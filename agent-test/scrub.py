#!/usr/bin/env python3
"""Scrub secrets from a run folder (docs/TDD.md rule A10).

In every file under the folder (workspace included; symlinks are not followed):
  1. each known secret value (the Qrypt key and the Gemini key) becomes <REDACTED>;
  2. the value of any Authorization, X-API-Key or x-goog-api-key header (as `Name: value`,
     `"Name": "value"` or `Name=value`) becomes <REDACTED>; an auth scheme word is kept
     (`Authorization: Bearer <REDACTED>`).
Then the folder is scanned again. The exit status is 1 if a known secret value is still
present anywhere (the report names files, never values).

Usage:
  scrub.py RUN_DIR [--qrypt-key-file F] [--gemini-key-file F]
Key files default to ~/.config/agentforge/{qrypt,gemini}_api_key. A missing file is an error.
Secrets are only ever read from files, never taken from the command line.
"""
import argparse
import os
import pathlib
import re
import sys

REDACTED = "<REDACTED>"
MIN_SECRET_LEN = 4  # shorter values would garble ordinary text
MAX_FILE = 200 * 1024 * 1024
DEFAULT_QRYPT_KEY = "~/.config/agentforge/qrypt_api_key"
DEFAULT_GEMINI_KEY = "~/.config/agentforge/gemini_api_key"

# `Authorization: Bearer abc`, `"x-api-key": "abc"`, `Authorization=abc`, also inside JSON strings
# whose quotes are backslash-escaped. Group 1 = name + separator + optional quote, 2 = scheme.
HEADER_RE = re.compile(
    rb"""(?ix)
    ((?:authorization|x-api-key|x-goog-api-key)\\?["']?\s*[:=]\s*\\?["']?)
    ((?:bearer|basic|token|apikey)\s+)?
    (\$\{[^}\s"']*\}|[^\s"'\\,}<][^\s"'\\,}]*|<[^\s>]*>)
    """)


def scrub_bytes(data, secrets):
    """Returns the scrubbed bytes. `secrets` are non-empty byte strings (longest first)."""
    for secret in secrets:
        data = data.replace(secret, REDACTED.encode())

    def repl(match):
        value = match.group(3)
        if value.lower() in (REDACTED.lower().encode(), b"null", b"none", b"undefined"):
            return match.group(0)  # already redacted, or not a value at all
        return match.group(1) + (match.group(2) or b"") + REDACTED.encode()

    return HEADER_RE.sub(repl, data)


def scrub_text(text, secret_values):
    """str -> str version for notes and log lines."""
    return scrub_bytes(text.encode("utf-8", "replace"), prepare(secret_values)).decode(
        "utf-8", "replace")


def prepare(secret_values):
    """Normalized, deduplicated secrets as bytes, longest first. Too-short values are dropped."""
    out = {s.strip().encode() for s in secret_values if s and len(s.strip()) >= MIN_SECRET_LEN}
    return sorted(out, key=len, reverse=True)


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            path = pathlib.Path(dirpath, name)
            if not path.is_symlink() and path.is_file():
                yield path


def scrub_folder(folder, secret_values):
    """Scrub in place; returns (files_changed, files_still_holding_a_secret)."""
    secrets = prepare(secret_values)
    changed = []
    for path in iter_files(folder):
        if path.stat().st_size > MAX_FILE:
            continue
        mode = path.stat().st_mode
        data = path.read_bytes()
        clean = scrub_bytes(data, secrets)
        if clean != data:
            try:
                path.write_bytes(clean)
            except OSError:
                continue  # the second scan below reports a file that still holds a secret
            os.chmod(path, mode & 0o7777)
            changed.append(path)
    leaked = []
    for path in iter_files(folder):
        if path.stat().st_size <= MAX_FILE:
            data = path.read_bytes()
            if any(secret in data for secret in secrets):
                leaked.append(path)
    return changed, leaked


def read_secret(path):
    value = pathlib.Path(path).expanduser().read_text().strip()
    if not value:
        raise SystemExit(f"{path}: empty key file")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("run_dir", type=pathlib.Path)
    parser.add_argument("--qrypt-key-file", default=DEFAULT_QRYPT_KEY)
    parser.add_argument("--gemini-key-file", default=DEFAULT_GEMINI_KEY)
    args = parser.parse_args(argv)
    if not args.run_dir.is_dir():
        raise SystemExit(f"{args.run_dir}: not a directory")
    values = [read_secret(args.qrypt_key_file), read_secret(args.gemini_key_file)]
    changed, leaked = scrub_folder(args.run_dir, values)
    print(f"scrub: {len(changed)} file(s) changed under {args.run_dir}")
    if leaked:
        for path in leaked:
            print(f"scrub: SECRET STILL PRESENT in {path}", file=sys.stderr)
        return 1
    print("scrub: no known secret value remains")
    return 0


if __name__ == "__main__":
    sys.exit(main())
