"""mitmproxy addon for the agent-readiness harness (docs/TDD.md section 6, rule A4).

Writes one JSON line per flow to $QH_PROXY_LOG (default /out/proxy.jsonl):

  utc, method, host, path, status, user_agent, authorization, x_api_key, auth_scheme,
  server, cf_mitigated, content_type            every flow
  query                                         *.qrypt.com only (never the Gemini host)
  random_block_bytes, key_fingerprints          *.qrypt.com 200 responses with random[]/entropy[]
  error                                         flows that failed before a response

DNS queries (the proxy is the agent's DNS server) are logged as {utc, method: "DNS", host,
qtype}: the name asked for and the query type, nothing else.

Secrets: Authorization and X-API-Key (and x-goog-api-key) are reduced to `<redacted>` (the
scheme word is kept: `Bearer <redacted>`). Entropy bytes are NEVER written: for each block in
`random[]` or `entropy[]` only its decoded length and the SHA-256 of its first 32 bytes are
kept. Gemini API traffic (generativelanguage.googleapis.com) is logged as host, path and
status only; no request or response body is read for any host except *.qrypt.com responses.

Offline mode (smoke tests only): set QH_OFFLINE_MOCK to the path of mock/server.py. DNS answers
are then synthetic (A queries get one fixed public address, other types an empty answer), so
no name is resolved outside the machine. The addon serves that mock on 127.0.0.1:$QH_OFFLINE_PORT inside the proxy container, forwards every
*.qrypt.com request to it (path prefixed with /s/ok) while still logging the ORIGINAL host and
path, and answers every other host with a 502, so nothing leaves the machine.

Only the standard library is imported at module level, so the pure functions can be tested
without mitmproxy installed.
"""
import base64
import binascii
import datetime as dt
import hashlib
import importlib.util
import json
import os
import threading

LOG_PATH = os.environ.get("QH_PROXY_LOG", "/out/proxy.jsonl")
OFFLINE_MOCK = os.environ.get("QH_OFFLINE_MOCK", "")
OFFLINE_PORT = int(os.environ.get("QH_OFFLINE_PORT", "18080"))
REDACTED = "<redacted>"
SCHEMES = {"bearer": "Bearer", "basic": "Basic", "token": "Token", "apikey": "ApiKey"}
# Response headers worth keeping for failure triage; none of them can hold a secret.
KEEP_HEADERS = {"server": "server", "cf-mitigated": "cf_mitigated", "content-type": "content_type"}


def is_qrypt_host(host):
    host = (host or "").lower().rstrip(".")
    return host == "qrypt.com" or host.endswith(".qrypt.com")


def redact_authorization(value):
    """`Bearer abc` -> `Bearer <redacted>`; unknown scheme or no scheme -> `<redacted>`."""
    if not value:
        return None
    first = value.strip().split(None, 1)[0].lower()
    return f"{SCHEMES[first]} {REDACTED}" if first in SCHEMES else REDACTED


def auth_scheme(headers):
    """How the request authenticated: bearer, basic, x-api-key, x-goog-api-key, other or none."""
    auth = headers.get("authorization")
    if auth:
        first = auth.strip().split(None, 1)[0].lower()
        return first if first in SCHEMES else "other"
    for name in ("x-api-key", "x-goog-api-key"):
        if headers.get(name):
            return name
    return "none"


def summarize_entropy_body(body):
    """From a JSON response body (bytes) return {} or the metadata of its random[]/entropy[]
    blocks: decoded length of each block and SHA-256 hex of each block's first 32 bytes.
    The decoded bytes are not kept anywhere beyond this function."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    lengths, prints = [], []
    for name in ("random", "entropy"):
        items = data.get(name)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, str):
                continue
            try:
                raw = base64.b64decode(item, validate=True)
            except (binascii.Error, ValueError):
                continue
            lengths.append(len(raw))
            prints.append(hashlib.sha256(raw[:32]).hexdigest())
    if not lengths:
        return {}
    return {"random_block_bytes": lengths, "key_fingerprints": prints}


QTYPES = {1: "A", 2: "NS", 5: "CNAME", 12: "PTR", 15: "MX", 16: "TXT", 28: "AAAA", 33: "SRV",
          64: "SVCB", 65: "HTTPS"}
OFFLINE_ADDRESS = "93.184.216.34"  # a public (non-private) address; nothing ever connects to it


def qtype_name(code):
    return QTYPES.get(int(code), str(int(code)))


def dns_record(utc, name, qtype):
    return {"utc": utc, "method": "DNS", "host": name.rstrip(".").lower(), "qtype": qtype_name(qtype)}


def build_record(*, utc, method, host, path, query, status, headers, resp_headers,
                 body=None, error=None):
    """The proxy.jsonl record for one flow. `headers`/`resp_headers` are lower-case dicts."""
    qrypt = is_qrypt_host(host)
    rec = {"utc": utc, "method": method, "host": host, "path": path, "status": status,
           "user_agent": headers.get("user-agent"),
           "authorization": redact_authorization(headers.get("authorization")),
           "x_api_key": REDACTED if headers.get("x-api-key") else None,
           "auth_scheme": auth_scheme(headers)}
    if headers.get("x-goog-api-key"):
        rec["x_goog_api_key"] = REDACTED
    for src, dst in KEEP_HEADERS.items():
        if resp_headers.get(src):
            rec[dst] = resp_headers[src][:120]
    if qrypt:
        rec["query"] = query
        if status == 200 and body is not None:
            rec.update(summarize_entropy_body(body))
    if error:
        rec["error"] = str(error)[:200]
    return rec


def iso(ts=None):
    when = dt.datetime.fromtimestamp(ts, dt.timezone.utc) if ts else dt.datetime.now(dt.timezone.utc)
    return when.isoformat(timespec="seconds")


class Recorder:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def write(self, record):
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=True) + "\n"
        with self.lock, open(self.path, "a", encoding="ascii") as fh:
            fh.write(line)
            fh.flush()


def start_mock(path, port):
    """Serve mock/server.py's Handler on 127.0.0.1:port in a daemon thread."""
    spec = importlib.util.spec_from_file_location("qh_mock_server", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = module.ThreadingHTTPServer(("127.0.0.1", port), module.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class HarnessProxy:
    def __init__(self):
        self.recorder = Recorder(LOG_PATH)
        self.offline = bool(OFFLINE_MOCK)
        if self.offline:
            start_mock(OFFLINE_MOCK, OFFLINE_PORT)

    # mitmproxy hooks -------------------------------------------------------------------
    def request(self, flow):
        flow.metadata["qh_path"] = flow.request.path.split("?", 1)[0]
        flow.metadata["qh_query"] = flow.request.path.partition("?")[2]
        flow.metadata["qh_host"] = flow.request.pretty_host
        if not self.offline:
            return
        if is_qrypt_host(flow.request.pretty_host):
            flow.request.scheme = "http"
            flow.request.host = "127.0.0.1"
            flow.request.port = OFFLINE_PORT
            flow.request.path = "/s/ok" + flow.request.path
        else:
            from mitmproxy import http
            flow.response = http.Response.make(
                502, b"offline harness: egress blocked", {"Content-Type": "text/plain"})

    def dns_request(self, flow):
        names = []
        for question in flow.request.questions:
            self.recorder.write(dns_record(iso(), question.name, question.type))
            names.append((question.name, int(question.type)))
        if self.offline:
            import ipaddress
            from mitmproxy import dns
            answers = [dns.ResourceRecord.A(name, ipaddress.IPv4Address(OFFLINE_ADDRESS))
                       for name, qtype in names if qtype == 1]
            flow.response = flow.request.succeed(answers)

    def responseheaders(self, flow):
        # Never buffer (or read) bodies of anything but Qrypt responses; lets Gemini SSE stream.
        if not is_qrypt_host(flow.metadata.get("qh_host", flow.request.pretty_host)):
            flow.response.stream = True

    def response(self, flow):
        host = flow.metadata.get("qh_host", flow.request.pretty_host)
        body = flow.response.content if is_qrypt_host(host) else None
        self.log(flow, host, flow.response.status_code, body)

    def error(self, flow):
        host = flow.metadata.get("qh_host", flow.request.pretty_host)
        self.log(flow, host, 0, None, error=flow.error.msg if flow.error else "error")

    # -----------------------------------------------------------------------------------
    def log(self, flow, host, status, body, error=None):
        headers = {k.lower(): v for k, v in flow.request.headers.items()}
        resp = {}
        if flow.response is not None:
            resp = {k.lower(): v for k, v in flow.response.headers.items()}
        path = flow.metadata.get("qh_path", flow.request.path.split("?", 1)[0])
        query = flow.metadata.get("qh_query", "")
        self.recorder.write(build_record(
            utc=iso(flow.request.timestamp_start), method=flow.request.method, host=host,
            path=path, query=query, status=status, headers=headers, resp_headers=resp,
            body=body, error=error))


addons = [HarnessProxy()] if os.environ.get("QH_ADDON_NO_INIT") != "1" else []
