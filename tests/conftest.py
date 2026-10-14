"""Shared fixtures: the mock server, and a helper that runs a snippet in a clean sandbox.

Snippets under test come from SNIPPETS_DIR (default: <repo>/quickstart). The directory holds
node/quickstart.mjs and python/quickstart.py; a flat layout (quickstart.mjs and quickstart.py
side by side, as in spikes/snippets) is accepted too.
"""
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MOCK_SERVER = REPO / "mock" / "server.py"
TIMEOUT_S = 30
READY_TIMEOUT_S = 15

TEST_KEY = "test-key-not-real"  # CI rule C3: tests never see a real key
SNIPPET_FILES = {"node": ("node", "quickstart.mjs"), "python": ("python", "quickstart.py")}


def pytest_generate_tests(metafunc):
    """Every test that takes a `lang` argument runs once per language, marked accordingly."""
    if "lang" in metafunc.fixturenames:
        metafunc.parametrize("lang", [pytest.param("node", marks=pytest.mark.node),
                                      pytest.param("python", marks=pytest.mark.python)])


# --- snippet location -------------------------------------------------------------------


def snippets_dir():
    return Path(os.environ.get("SNIPPETS_DIR") or REPO / "quickstart").resolve()


def snippet_path(lang):
    subdir, name = SNIPPET_FILES[lang]
    root = snippets_dir()
    for candidate in (root / subdir / name, root / name):
        if candidate.is_file():
            return candidate
    pytest.fail(f"{lang} snippet not found: looked for {root / subdir / name} and {root / name}")


def read_help(lang):
    """The HELP constant of one snippet file."""
    text = snippet_path(lang).read_text(encoding="utf-8")
    found = re.search(r'^\s*(?:const\s+)?HELP\s*=\s*"([^"]*)"', text, re.MULTILINE)
    assert found, f"no HELP constant in {snippet_path(lang)}"
    return found.group(1)


# --- mock server ------------------------------------------------------------------------


class Mock:
    """Handle on the running mock server process."""

    def __init__(self, proc, port, ready_line, stderr_path):
        self.proc, self.port, self.ready_line = proc, port, ready_line
        self.stderr_path = stderr_path

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def url(self, scenario="ok", size=1):
        """Full snippet URL (the QRYPT_API_URL override) for a scenario."""
        return f"{self.base}/s/{scenario}/api/v1/quantum-entropy?size={size}"

    def request(self, method, path, headers=None):
        """Raw HTTP request to the mock. Returns (status, headers dict, body text)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, headers=headers or {})
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", "replace")
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, body
        finally:
            conn.close()

    def requests(self):
        """The request log (M5)."""
        status, _, body = self.request("GET", "/__requests")
        assert status == 200
        return json.loads(body)

    def clear(self):
        status, _, _ = self.request("DELETE", "/__requests")
        assert status == 200

    def alive(self):
        return self.proc.poll() is None


def _read_ready(proc):
    """Read the first stdout line without blocking forever."""
    box = []
    reader = threading.Thread(target=lambda: box.append(proc.stdout.readline()), daemon=True)
    reader.start()
    reader.join(READY_TIMEOUT_S)
    return box[0].strip() if box else ""


@pytest.fixture(scope="session")
def mock(tmp_path_factory):
    """One mock server for the whole session, on a free port (port argument 0)."""
    stderr_path = tmp_path_factory.mktemp("mock") / "stderr.log"
    with open(stderr_path, "w") as err:
        proc = subprocess.Popen([sys.executable, str(MOCK_SERVER), "0"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=err, text=True)
    line = _read_ready(proc)
    match = re.fullmatch(r"READY (\d+)", line)
    if not match:
        proc.kill()
        pytest.fail(f"mock server did not print 'READY <port>' (got {line!r}); "
                    f"stderr: {stderr_path.read_text()}")
    handle = Mock(proc, int(match.group(1)), line, stderr_path)
    try:
        yield handle
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
        proc.stdout.close()


@pytest.fixture(autouse=True)
def mock_guard(mock):
    """Start every test with an empty request log; fail the test if the server has died."""
    assert mock.alive(), f"mock server not running before test: {mock.stderr_path.read_text()}"
    mock.clear()
    yield
    assert mock.alive(), f"mock server died during test: {mock.stderr_path.read_text()}"


# --- running a snippet ------------------------------------------------------------------


@dataclass
class Run:
    code: int
    stdout: str
    stderr: str
    cwd: Path


def sanitized_env(extra=None):
    """The parent environment minus every QRYPT_* variable (so a developer's real key can
    never reach a snippet), minus proxy and Node option variables that would redirect or
    alter requests, plus only what the test asks for."""
    drop = ("QRYPT_", "NODE_OPTIONS")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(drop) and not k.upper().endswith("_PROXY")}
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra or {})
    return env


def _ancestor_dotenv(path):
    """python-dotenv searches upward from the script's folder; report a stray .env above it."""
    for parent in path.parents:
        if (parent / ".env").exists():
            return parent / ".env"
    return None


@pytest.fixture
def run_snippet(tmp_path_factory):
    """run_snippet(lang, env_file=None, env=None, url=None, node_args=()) -> Run

    Copies the snippet into a fresh temp folder, writes `.env` there when `env_file` is a
    string, runs it from that folder with a sanitized environment (plus `env`, plus
    QRYPT_API_URL=`url`), with a 30 s timeout. The folder is returned in `Run.cwd` so
    tests can inspect it.
    """

    def run(lang, env_file=None, env=None, url=None, node_args=()):
        src = snippet_path(lang)
        cwd = tmp_path_factory.mktemp(f"snip-{lang}")
        script = cwd / src.name
        shutil.copyfile(src, script)
        if env_file is not None:
            (cwd / ".env").write_text(env_file, encoding="utf-8")
        elif lang == "python":
            stray = _ancestor_dotenv(cwd)
            if stray:
                pytest.fail(f"{stray} exists above the temp folder and python-dotenv would "
                            "load it; move it away so the test is isolated")
        extra = dict(env or {})
        if url is not None:
            extra["QRYPT_API_URL"] = url
        if lang == "node":
            if not shutil.which("node"):
                pytest.fail("node is not on PATH (deselect with -m 'not node')")
            cmd = ["node", *node_args, script.name]
        else:
            cmd = [sys.executable, script.name]
        try:
            done = subprocess.run(cmd, cwd=cwd, env=sanitized_env(extra), stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=TIMEOUT_S, encoding="utf-8")
        except subprocess.TimeoutExpired as exc:
            pytest.fail(f"{' '.join(cmd)} timed out after {TIMEOUT_S} s; "
                        f"stdout={exc.stdout!r} stderr={exc.stderr!r}")
        return Run(done.returncode, done.stdout, done.stderr, cwd)

    return run


@pytest.fixture
def closed_port():
    """A loopback port nothing is listening on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
