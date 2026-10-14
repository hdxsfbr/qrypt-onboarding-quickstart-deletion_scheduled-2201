#!/usr/bin/env python3
"""Throwaway spike mock: scenario is the first path segment, e.g. /ok/api/v1/quantum-entropy.

Bodies copy the live API where observed (401, 403, 400); others are illustrative.
The real mock is designed in the TDD.
"""
import base64
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BLOCK = bytes(range(256)) * 4  # deterministic 1 KiB so tests can check the key never leaks
SCENARIOS = {
    "ok": (200, {"random": [base64.b64encode(BLOCK).decode()], "size": 1}),
    "401": (401, {"error": "Missing or malformed Authorization header (expected 'Bearer <token>')"}),
    "403": (403, {"error": "Forbidden or entropy quota reached"}),
    "429": (429, {"error": "Too many requests"}),
    "500": (500, {"error": "Internal server error"}),
    "503": (503, {"error": "Not enough entropy supply"}),
    "badjson": (200, None),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        scenario = self.path.strip("/").split("/")[0]
        auth = self.headers.get("Authorization", "")
        if scenario == "ok" and not auth.startswith("Bearer ") or len(auth) <= len("Bearer "):
            scenario = "401"
        status, body = SCENARIOS.get(scenario, (404, {"error": "unknown scenario"}))
        raw = b"<html>not json</html>" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
