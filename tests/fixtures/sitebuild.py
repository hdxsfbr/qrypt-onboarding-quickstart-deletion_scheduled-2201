"""Shared by tests/test_site.py and tests/test_docs_commands.py: build the site once per pytest
session and serve it over local HTTP.

The build is `python3 scripts/build_site.py --out <tmp>`. Set SITE_PUBLIC to an existing build
to skip it (and SITE_UPSTREAM_PUBLIC for the --upstream-only build used by T-site-3). Both
helpers memoize, so the two test modules share one build. Failures call pytest.fail with the
tail of the build log: a missing or broken build is a failure, never a skip.
"""
import atexit
import functools
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO / "scripts" / "build_site.py"
BUILD_TIMEOUT_S = 900


def _build(out, *extra):
    if not BUILD_SCRIPT.is_file():
        pytest.fail(f"{BUILD_SCRIPT} does not exist yet (agent D's deliverable); cannot build")
    log = out.with_name(out.name + ".build.log")
    cmd = [sys.executable, str(BUILD_SCRIPT), "--out", str(out), *extra]
    with open(log, "w") as fh:
        try:
            done = subprocess.run(cmd, cwd=REPO, stdin=subprocess.DEVNULL, stdout=fh,
                                  stderr=subprocess.STDOUT, timeout=BUILD_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            pytest.fail(f"{' '.join(cmd)} timed out after {BUILD_TIMEOUT_S} s; see {log}")
    if done.returncode != 0:
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-30:])
        pytest.fail(f"{' '.join(cmd)} exited {done.returncode}; log {log}:\n{tail}")
    if not (out / "index.html").is_file():
        pytest.fail(f"build succeeded but {out}/index.html is missing")
    return out


def _once(fn):
    """Memoize fn, failures included: a broken build fails every test that needs it at once,
    instead of being retried (and re-timed-out) per test."""
    box = []

    @functools.wraps(fn)
    def wrapper():
        if not box:
            try:
                box.append((fn(), None))
            except pytest.fail.Exception as exc:
                box.append((None, str(exc)))
        value, message = box[0]
        if message is not None:
            pytest.fail(message, pytrace=False)
        return value

    return wrapper


def _existing(var):
    value = os.environ.get(var)
    if not value:
        return None
    path = Path(value).resolve()
    if not path.is_dir():
        pytest.fail(f"{var}={value} is not a directory")
    return path


def _scratch(prefix):
    path = Path(tempfile.mkdtemp(prefix=prefix))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path / "public"


@_once
def public():
    """The full site build (or $SITE_PUBLIC)."""
    return _existing("SITE_PUBLIC") or _build(_scratch("qs-site-"))


@_once
def upstream_public():
    """The unmodified upstream shell, built with --upstream-only (or $SITE_UPSTREAM_PUBLIC)."""
    return _existing("SITE_UPSTREAM_PUBLIC") or _build(_scratch("qs-upstream-"), "--upstream-only")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(directory):
    """Serve a directory over HTTP on a free local port; returns 'http://127.0.0.1:<port>/'."""
    handler = functools.partial(_Quiet, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    atexit.register(httpd.shutdown)
    return f"http://127.0.0.1:{httpd.server_address[1]}/"


@_once
def server_url():
    """The full site build, served (one server per session)."""
    return serve(public())
