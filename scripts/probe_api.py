#!/usr/bin/env python3
"""Probe Qrypt's entropy APIs and record observed behavior.

Never prints the API key or entropy bytes: successful responses are reduced to
their shape (field names, element counts, decoded lengths). Every probe that can
return entropy is appended to a usage ledger so live consumption stays auditable.

Usage:
  scripts/probe_api.py --list
  scripts/probe_api.py --only eus_ok,aws_ok_32
  scripts/probe_api.py --key-file ~/.config/agentforge/qrypt_api_key --only all
"""
import argparse
import base64
import csv
import datetime as dt
import json
import os
import pathlib
import re
import secrets
import string
import sys
import time
import urllib.error
import urllib.request

EUS = "https://api-eus.qrypt.com"
AWS = "https://eaas-aws.qrypt.com"
SHOW_SKELETON = False
LEDGER = pathlib.Path(__file__).resolve().parent.parent / "docs" / "evidence" / "usage.csv"


def fake_key():
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(40))


def bearer(k):
    return {"Authorization": f"Bearer {k}"}


def xkey(k):
    return {"X-API-Key": k}


# name: (method, url, auth(key)->headers or None, json body or None, max bytes of entropy it can cost)
PROBES = {
    "eus_ok":            ("GET",  f"{EUS}/api/v1/quantum-entropy?size=1", bearer, None, 1024),
    "eus_bad_key":       ("GET",  f"{EUS}/api/v1/quantum-entropy?size=1", lambda k: bearer(fake_key()), None, 0),
    "eus_no_auth":       ("GET",  f"{EUS}/api/v1/quantum-entropy?size=1", None, None, 0),
    "eus_size0":         ("GET",  f"{EUS}/api/v1/quantum-entropy?size=0", bearer, None, 0),
    "eus_xapikey":       ("GET",  f"{EUS}/api/v1/quantum-entropy?size=1", xkey, None, 1024),
    "eus_post_aws_path": ("POST", f"{EUS}/api/v1/entropy", xkey, {"block_size": 32, "block_count": 1}, 32),
    "aws_ok_32":         ("POST", f"{AWS}/api/v1/entropy", xkey, {"block_size": 32, "block_count": 1}, 32),
    "aws_ok_16x2":       ("POST", f"{AWS}/api/v1/entropy", xkey, {"block_size": 16, "block_count": 2}, 32),
    "aws_ok_1kib":       ("POST", f"{AWS}/api/v1/entropy", xkey, {"block_size": 1024, "block_count": 1}, 1024),
    "aws_bad_key":       ("POST", f"{AWS}/api/v1/entropy", lambda k: xkey(fake_key()), {"block_size": 32, "block_count": 1}, 0),
    "aws_bearer":        ("POST", f"{AWS}/api/v1/entropy", bearer, {"block_size": 32, "block_count": 1}, 32),
    "aws_bs1025":        ("POST", f"{AWS}/api/v1/entropy", xkey, {"block_size": 1025, "block_count": 1}, 0),
    "aws_capabilities":  ("GET",  f"{AWS}/api/v1/capabilities", xkey, None, 0),
    "aws_get_eus_path":  ("GET",  f"{AWS}/api/v1/quantum-entropy?size=1", bearer, None, 1024),
    "aws_get_eus_path_x":("GET",  f"{AWS}/api/v1/quantum-entropy?size=1", xkey, None, 1024),
}


def shape(raw):
    try:
        data = json.loads(raw)
    except ValueError:
        return f"non-JSON ({len(raw)} bytes)"
    if not isinstance(data, dict):
        return f"JSON {type(data).__name__}"
    parts = []
    for k, v in data.items():
        if isinstance(v, list):
            lens = []
            for s in v:
                try:
                    lens.append(len(base64.b64decode(s, validate=True)))
                except (ValueError, TypeError):
                    lens.append("?")
            parts.append(f"{k}: list[{len(v)}] decoded bytes={lens}")
        else:
            parts.append(f"{k}: {type(v).__name__}={v!r}")
    return "; ".join(parts)


def skeleton(raw):
    """Raw body with every base64 string replaced by a length marker, layout untouched."""
    def mask(m):
        try:
            return f'"<b64 {len(base64.b64decode(m.group(1), validate=True))} B>"'
        except ValueError:
            return m.group(0)
    return re.sub(r'"([A-Za-z0-9+/]{16,}={0,2})"', mask, raw)


def decoded_total(raw):
    try:
        data = json.loads(raw)
        lists = [v for v in data.values() if isinstance(v, list)]
        return sum(len(base64.b64decode(s)) for lst in lists for s in lst)
    except (ValueError, TypeError, AttributeError):
        return 0


def run(name, key, user_agent=None):
    method, url, auth, body, _ = PROBES[name]
    headers = {"Accept": "application/json"}
    if user_agent is not None:
        headers["User-Agent"] = user_agent
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if auth:
        headers.update(auth(key))
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            status, raw = r.status, r.read().decode("utf-8", "replace")
            ctype = r.headers.get("content-type", "")
            retry = r.headers.get("retry-after")
            if SHOW_SKELETON:
                hdrs = {k.lower(): v for k, v in r.headers.items()
                        if k.lower() in ("content-type", "content-length", "cache-control", "content-encoding")}
                print(f"  [{name}] headers: {hdrs}\n  [{name}] body: {skeleton(raw)}")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode("utf-8", "replace")
        ctype = e.headers.get("content-type", "")
        retry = e.headers.get("retry-after")
    except urllib.error.URLError as e:
        return name, "ERR", 0.0, "", f"network error: {e.reason}", 0
    elapsed = time.monotonic() - t0
    if 200 <= status < 300 and name.endswith("capabilities"):
        detail, used = raw[:600].replace("\n", " "), 0
    elif 200 <= status < 300:
        detail, used = shape(raw), decoded_total(raw)
    else:
        # API Gateway echoes a SHA-256 of the Authorization header; treat it as secret-derived.
        scrubbed = re.sub(r"(Base64\): ')[^']*", r"\1<REDACTED>", raw.replace(key, "<KEY>"))
        detail, used = scrubbed[:200].replace("\n", " "), 0
    if retry:
        detail += f" [Retry-After: {retry}]"
    return name, status, elapsed, ctype, detail, used


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key-file", help="file holding the raw key (default: $QRYPT_API_KEY)")
    ap.add_argument("--only", default="", help="comma-separated probe names, or 'all'")
    ap.add_argument("--list", action="store_true", help="list probes and their max entropy cost")
    ap.add_argument("--no-ledger", action="store_true", help="do not append to the usage ledger")
    # Cloudflare in front of both APIs blocks urllib's default "Python-urllib/x.y" with error 1010.
    ap.add_argument("--user-agent", default="qrypt-probe/1.0", help="User-Agent to send (default: %(default)s)")
    ap.add_argument("--skeleton", action="store_true",
                    help="for 2xx responses, print the raw body with base64 values masked, plus key headers")
    a = ap.parse_args()
    global SHOW_SKELETON
    SHOW_SKELETON = a.skeleton

    if a.list or not a.only:
        for n, p in PROBES.items():
            print(f"{n:20} {p[0]:4} {p[1]:60} max cost {p[4]} B")
        return 0
    names = list(PROBES) if a.only == "all" else [n.strip() for n in a.only.split(",")]
    unknown = [n for n in names if n not in PROBES]
    if unknown:
        sys.exit(f"unknown probe(s): {', '.join(unknown)}")
    key = (pathlib.Path(a.key_file).expanduser().read_text().strip() if a.key_file
           else os.environ.get("QRYPT_API_KEY", "").strip())
    if not key:
        sys.exit("No key: pass --key-file or set QRYPT_API_KEY.")
    budget = sum(PROBES[n][4] for n in names)
    print(f"max possible entropy cost of this run: {budget} B\n")

    rows = []
    for n in names:
        name, status, elapsed, ctype, detail, used = run(n, key, a.user_agent)
        print(f"{name:20} HTTP {status}  {elapsed:5.2f}s  {ctype:18}  {detail}")
        rows.append([dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), name, status, used])
        time.sleep(0.5)  # stay far below 30 requests / 10 s
    total = sum(r[3] for r in rows)
    print(f"\nentropy received this run: {total} B")
    if not a.no_ledger:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        new = not LEDGER.exists()
        with LEDGER.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["utc_time", "probe", "status", "entropy_bytes"])
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
