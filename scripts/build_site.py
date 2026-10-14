#!/usr/bin/env python3
"""Build the Quickstart site: Qrypt's docs shell (submodule) plus our Quickstart section.

  scripts/build_site.py [--out site/public] [--base-url URL] [--upstream-only]

1. Finds Hugo 0.154.5: $HUGO if set, else .tools/hugo-0.154.5/hugo, downloaded on first use
   (linux-amd64 release; the tarball's SHA-256 must match both the pin below and the release's
   own checksums file).
2. Copies the snippet files from quickstart/ into site/assets/snippets/ (rendered by the
   shortcodes) and site/static/quickstart/files/ (served for `curl -O`). Both are generated
   and gitignored.
3. Runs `hugo --minify`. Any Hugo ERROR line, or a non-zero exit, fails the build.

--upstream-only builds the unmodified shell from site/upstream (no snippets, no Quickstart
section). tests/test_site.py compares that output with the normal build.
"""
import argparse
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUGO_VERSION = "0.154.5"
HUGO_TARBALL = f"hugo_{HUGO_VERSION}_linux-amd64.tar.gz"
HUGO_SHA256 = "99b2f2f3db3e65bbbdb5840bc5158e271975b17d152904bb750ebae3c2a2aecc"
RELEASE = f"https://github.com/gohugoio/hugo/releases/download/v{HUGO_VERSION}"
DEFAULT_BASE_URL = "https://qrypt-quickstart.pages.dev/"

# source (relative to the repo root) -> file name under quickstart/files/ and assets/snippets/
SNIPPETS = {
    "quickstart/node/quickstart.mjs": "quickstart.mjs",
    "quickstart/python/quickstart.py": "quickstart.py",
    "quickstart/python/requirements.txt": "requirements.txt",
    ".env.example": ".env.example",
}
# Only these two are rendered into pages; the others are download-only.
RENDERED = ("quickstart.mjs", "quickstart.py")


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def find_hugo() -> pathlib.Path:
    if os.environ.get("HUGO"):
        return pathlib.Path(os.environ["HUGO"])
    hugo = ROOT / ".tools" / f"hugo-{HUGO_VERSION}" / "hugo"
    if hugo.exists():
        return hugo
    if sys.platform != "linux" or os.uname().machine not in ("x86_64", "amd64"):
        sys.exit(f"build_site: no automatic Hugo download for this platform; set HUGO=/path/to/hugo ({HUGO_VERSION})")
    print(f"build_site: downloading Hugo {HUGO_VERSION}", flush=True)
    tarball = fetch(f"{RELEASE}/{HUGO_TARBALL}")
    digest = hashlib.sha256(tarball).hexdigest()
    sums = fetch(f"{RELEASE}/hugo_{HUGO_VERSION}_checksums.txt").decode()
    listed = dict(reversed(line.split()) for line in sums.splitlines() if line.strip())
    if digest != HUGO_SHA256 or digest != listed.get(HUGO_TARBALL):
        sys.exit(f"build_site: Hugo checksum mismatch (got {digest}); refusing to use the download")
    dest = hugo.parent
    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest / HUGO_TARBALL
    tmp.write_bytes(tarball)
    with tarfile.open(tmp) as tf:
        member = tf.getmember("hugo")
        member.name = "hugo"
        tf.extract(member, dest, filter="data")
    tmp.unlink()
    hugo.chmod(0o755)
    return hugo


def copy_snippets(site: pathlib.Path) -> None:
    targets = {
        site / "static" / "quickstart" / "files": list(SNIPPETS.values()),
        site / "assets" / "snippets": list(RENDERED),
    }
    for folder in targets:
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
    for src, name in SNIPPETS.items():
        path = ROOT / src
        if not path.is_file():
            sys.exit(f"build_site: missing snippet source {src}")
        for folder, names in targets.items():
            if name in names:
                shutil.copyfile(path, folder / name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "site" / "public"), help="output directory (default site/public)")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--upstream-only", action="store_true", help="build the unmodified upstream shell only")
    args = ap.parse_args()

    hugo = find_hugo()
    out = pathlib.Path(args.out).resolve()
    if args.upstream_only:
        source = ROOT / "site" / "upstream"
    else:
        source = ROOT / "site"
        copy_snippets(source)
    if not (source / ("config.toml" if args.upstream_only else "hugo.toml")).is_file():
        sys.exit("build_site: Hugo config not found; run `git submodule update --init --recursive`")

    cmd = [str(hugo), "--minify", "--source", str(source), "--destination", str(out),
           "--baseURL", args.base_url, "--cleanDestinationDir", "--logLevel", "info"]
    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True)
    print(proc.stdout, end="")
    errors = [l for l in proc.stdout.splitlines() if re.match(r"^ERROR\b", l)]
    if proc.returncode != 0 or errors:
        print(f"build_site: Hugo failed (exit {proc.returncode}, {len(errors)} ERROR lines)", file=sys.stderr)
        return 1
    print(f"build_site: built {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
