#!/usr/bin/env python3
"""Mock of the Qrypt api-eus entropy endpoint (docs/TDD.md section 3, rules M1-M6).

Usage: python3 mock/server.py [port]    (port 0 or omitted: pick a free port)

Prints `READY <port>` on stdout once it is listening on 127.0.0.1. Stdlib only.

The scenario is the path prefix: /s/<scenario>/api/v1/quantum-entropy?size=<KiB>
  ok            200, `size` copies of the fixed 1 KiB test block, base64 encoded
  unauthorized  401 even when the Authorization header is well formed
  forbidden     403 (live text)
  ratelimit     429, no Retry-After
  error500      500
  unavailable   503
  teapot        418
  badjson       200 with an HTML body

Order of checks (M2, M3, M4): Authorization header, then `size`, then scenario.

Request log (M5): GET /__requests returns every API request seen so far, DELETE
/__requests clears it. The log never holds the raw token: only `Bearer <redacted>`
and the SHA-256 hex digest of the token (`bearer_sha256`), so tests can check
what a snippet actually sent (for example, that it trimmed whitespace).

Bodies copied from the live API (docs/POV.md, "Live API observations", 2026-10-13):
401, 403, 400. Every other error body is marked `# illustrative: not observed live`.
"""
import base64
import hashlib
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

BLOCK = bytes(range(256)) * 4  # the fixed 1 KiB test block
BLOCK_B64 = base64.b64encode(BLOCK).decode("ascii")

# Observed live. Compact JSON, exactly as the API sends it.
BODY_401 = '{"error":"Missing or malformed Authorization header (expected \'Bearer <token>\')"}'
BODY_400 = '{"error":"\'size\' must be an integer between 1 and 512"}'
BODY_403 = '{"error":"Forbidden or entropy quota reached"}'

# scenario -> (status, content type, body). `ok` is built per request.
SCENARIOS = {
    "unauthorized": (401, "application/json", BODY_401),
    "forbidden": (403, "application/json", BODY_403),
    "ratelimit": (429, "application/json",
                  '{"error":"Too many requests"}'),  # illustrative: not observed live
    "error500": (500, "application/json",
                 '{"error":"Internal server error"}'),  # illustrative: not observed live
    "unavailable": (503, "application/json",
                    '{"error":"Service unavailable"}'),  # illustrative: not observed live
    "teapot": (418, "application/json",
               '{"error":"I\'m a teapot"}'),  # illustrative: not observed live
    "badjson": (200, "text/html",
                "<html><body>not json</body></html>"),  # illustrative: not observed live
}
KNOWN = {"ok", *SCENARIOS}

PATH_RE = re.compile(r"^/s/(?P<scenario>[^/]+)/api/v1/quantum-entropy$")
SIZE_RE = re.compile(r"^[0-9]+$")
ADMIN_PATH = "/__requests"

LOG = []
LOG_LOCK = threading.Lock()


def parse_size(query):
    """Return the integer `size` (1..512), or None when it is missing or invalid (M3)."""
    values = parse_qs(query, keep_blank_values=True).get("size", [])
    if len(values) != 1 or not SIZE_RE.match(values[0]):
        return None
    size = int(values[0])
    return size if 1 <= size <= 512 else None


def bearer_token(header):
    """Return the token after `Bearer ` (at least one character), else None (M2)."""
    if header is not None and header.startswith("Bearer ") and len(header) > len("Bearer "):
        return header[len("Bearer "):]
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "qrypt-mock/1"

    def log_message(self, *args):  # keep stderr quiet
        pass

    def reply(self, status, body, ctype="application/json", extra=None):
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)
        return status

    def admin(self):
        if self.command == "GET":
            with LOG_LOCK:
                body = json.dumps(LOG)
            self.reply(200, body)
        elif self.command == "DELETE":
            with LOG_LOCK:
                cleared = len(LOG)
                LOG.clear()
            self.reply(200, json.dumps({"cleared": cleared}))
        else:
            self.reply(405, '{"error":"method not allowed"}')

    def respond(self, split, token):
        """Decide the response: auth (M2), size (M3), then scenario (M4)."""
        if token is None:
            return 401, "application/json", BODY_401, None
        size = parse_size(split.query)
        if size is None:
            return 400, "application/json", BODY_400, None
        match = PATH_RE.match(split.path)
        if match is None:
            return 404, "application/json", '{"error":"unknown path"}', None
        scenario = match.group("scenario")
        if scenario not in KNOWN:
            return 404, "application/json", '{"error":"unknown scenario"}', None
        if scenario == "ok":
            body = json.dumps({"random": [BLOCK_B64] * size, "size": size},
                              separators=(",", ":"))
            return 200, "application/json", body, {"Cache-Control": "no-store"}
        status, ctype, body = SCENARIOS[scenario]
        return status, ctype, body, None

    def handle_api(self):
        split = urlsplit(self.path)
        auth = self.headers.get("Authorization")
        token = bearer_token(auth)
        status, ctype, body, extra = self.respond(split, token)

        headers = {name.lower(): value for name, value in self.headers.items()}
        if auth is None:
            headers["authorization"] = "<missing>"
        elif token is not None:
            headers["authorization"] = "Bearer <redacted>"
        else:
            headers["authorization"] = "<redacted>"
        digest = None
        if token is not None:
            digest = hashlib.sha256(token.encode("latin-1")).hexdigest()
        record = {"method": self.command, "path": split.path, "query": split.query,
                  "headers": headers, "status": status, "bearer_sha256": digest}
        with LOG_LOCK:  # record before replying, so a client that exits at once still sees it
            LOG.append(record)
        self.reply(status, body, ctype, extra)

    def dispatch(self):
        if urlsplit(self.path).path == ADMIN_PATH:
            self.admin()
        else:
            self.handle_api()

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = dispatch


def main(argv):
    port = int(argv[1]) if len(argv) > 1 else 0
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"READY {server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main(sys.argv)
