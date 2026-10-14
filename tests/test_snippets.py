"""Contract tests for the quickstart snippets (docs/TDD.md section 2 rules S1-S13 and the
message contract; test list T-s-1..21), run for Node and Python against mock/server.py.

Snippets come from SNIPPETS_DIR (default quickstart/). The only network traffic is the local
mock, one refused loopback port (T-s-13) and one `.invalid` DNS lookup (T-s-14).
"""
import base64
import hashlib
import json
import re
import subprocess

import pytest

from conftest import TEST_KEY, read_help, snippet_path

SUCCESS = "✔ Generated a 256-bit AES key from Qrypt quantum entropy (AES-GCM round trip OK).\n"
ENV_OK = f"QRYPT_API_KEY={TEST_KEY}\n"
USER_AGENT = {"node": "qrypt-quickstart-node/1.0", "python": "qrypt-quickstart-python/1.0"}

# The message contract (TDD section 2), without the leading "✘ " and the trailing period.
NO_KEY = "QRYPT_API_KEY is not set. Add your key from portal.qrypt.com to .env."
H401 = "key expired or malformed; create a new one at portal.qrypt.com"
H403 = "key rejected, or monthly entropy quota used up"
H429 = "rate limit reached; wait about 15 seconds, then run once"
H5XX = "try again shortly"
HOTHER = "unexpected response"


def http_msg(status, hint):
    return f"Qrypt returned HTTP {status}: {hint}."


def assert_failure(run, lang, message, anchor, *, regex=False):
    """Exit 1, nothing on stdout, and stderr is exactly the two-line contract (S10):
    `✘ <message>` then `  Fix: <HELP>#<anchor>`. No traceback can hide in an exact match."""
    help_url = read_help(lang)
    assert run.code == 1, run.stderr
    assert run.stdout == ""
    first, sep, rest = run.stderr.partition("\n")
    fix = f"  Fix: {help_url}#{anchor}\n"
    assert rest == fix, f"stderr was {run.stderr!r}"
    assert first.startswith("✘ ")
    if regex:
        assert re.fullmatch(message, first[2:]), f"message was {first!r}"
    else:
        assert first[2:] == message, f"message was {first!r}"
    assert "Traceback" not in run.stderr


# --- success path ----------------------------------------------------------------------


def test_t_s_1_ok_prints_exactly_the_success_line(lang, mock, run_snippet):
    """T-s-1 (S9, S7): exit 0, stdout is exactly the success line, stderr is empty."""
    run = run_snippet(lang, ENV_OK, url=mock.url("ok"))
    assert (run.code, run.stdout, run.stderr) == (0, SUCCESS, "")


def test_t_s_2_ok_sends_one_well_formed_request(lang, mock, run_snippet):
    """T-s-2 (S1, S2): exactly one request: GET, size=1, Accept, Bearer token, User-Agent."""
    run = run_snippet(lang, ENV_OK, url=mock.url("ok"))
    assert run.code == 0, run.stderr
    (req,) = mock.requests()
    assert req["method"] == "GET"
    assert req["path"] == "/s/ok/api/v1/quantum-entropy"
    assert req["query"] == "size=1"
    assert req["status"] == 200
    assert req["headers"]["accept"] == "application/json"
    assert req["headers"]["authorization"] == "Bearer <redacted>"
    assert req["bearer_sha256"] == hashlib.sha256(TEST_KEY.encode()).hexdigest()
    assert req["headers"]["user-agent"] == USER_AGENT[lang]


def test_t_s_3_key_bytes_never_leave_the_process(lang, mock, run_snippet):
    """T-s-3 (S8): neither the hex nor the base64 of the key bytes (bytes(range(32)), the first
    32 bytes of the mock block), nor the API key, reaches stdout or stderr, and the working
    directory gains no files."""
    key = bytes(range(32))
    forbidden = [key.hex(), key.hex().upper(), base64.b64encode(key).decode(),
                 base64.urlsafe_b64encode(key).decode(), base64.b64encode(key).decode().rstrip("="),
                 TEST_KEY]
    assert key == (bytes(range(256)) * 4)[:32]  # these are the bytes the mock serves
    run = run_snippet(lang, ENV_OK, url=mock.url("ok"))
    assert run.code == 0, run.stderr
    for needle in forbidden:
        assert needle not in run.stdout and needle not in run.stderr, needle
    names = sorted(p.name for p in run.cwd.iterdir())
    script = "quickstart.mjs" if lang == "node" else "quickstart.py"
    assert names == sorted([".env", script]), names


# --- missing key (S3) ------------------------------------------------------------------


def test_t_s_4_no_env_file(lang, mock, run_snippet):
    """T-s-4 (S3): no .env file: the no-key message, no request."""
    run = run_snippet(lang, None, url=mock.url("ok"))
    assert_failure(run, lang, NO_KEY, "no-key")
    assert mock.requests() == []


def test_t_s_4_a_key_in_the_parent_environment_is_not_used(lang, mock, run_snippet, monkeypatch):
    """T-s-4 isolation: the helper strips QRYPT_* from the environment, so a developer's real
    key can never reach a snippet."""
    monkeypatch.setenv("QRYPT_API_KEY", "leaked-from-the-parent-environment")
    run = run_snippet(lang, None, url=mock.url("ok"))
    assert_failure(run, lang, NO_KEY, "no-key")
    assert mock.requests() == []


@pytest.mark.parametrize("content", ["QRYPT_API_KEY=\n", 'QRYPT_API_KEY="   "\n', "\n"],
                         ids=["empty", "spaces-only", "empty-file"])
def test_t_s_5_empty_key(lang, mock, run_snippet, content):
    """T-s-5 (S3): an empty (or blank) key behaves like a missing one."""
    run = run_snippet(lang, content, url=mock.url("ok"))
    assert_failure(run, lang, NO_KEY, "no-key")
    assert mock.requests() == []


def test_t_s_6_quoted_key_with_spaces_is_trimmed(lang, mock, run_snippet):
    """T-s-6 (S3): QRYPT_API_KEY="  <key>  " succeeds and the mock sees the trimmed token."""
    run = run_snippet(lang, f'QRYPT_API_KEY="  {TEST_KEY}  "\n', url=mock.url("ok"))
    assert (run.code, run.stdout, run.stderr) == (0, SUCCESS, "")
    (req,) = mock.requests()
    assert req["bearer_sha256"] == hashlib.sha256(TEST_KEY.encode()).hexdigest()


# --- HTTP failures (S5) ----------------------------------------------------------------

HTTP_CASES = [  # (test id, scenario, status, hint)
    ("T-s-7-forbidden", "forbidden", 403, H403),
    ("T-s-8-ratelimit", "ratelimit", 429, H429),
    ("T-s-9-unavailable", "unavailable", 503, H5XX),
    ("T-s-10-error500", "error500", 500, H5XX),
    ("T-s-11-teapot", "teapot", 418, HOTHER),
    ("T-s-12-unauthorized", "unauthorized", 401, H401),
]
HTTP_FAILURES = [pytest.param(s, c, h, id=i) for i, s, c, h in HTTP_CASES]


@pytest.mark.parametrize("scenario, status, hint", HTTP_FAILURES)
def test_t_s_7_to_12_http_failure(lang, mock, run_snippet, scenario, status, hint):
    """T-s-7..12 (S5, S1): a non-2xx status gives the exact message for its code, the matching
    #anchor, and exactly one request (no retry, which matters most for 429)."""
    run = run_snippet(lang, ENV_OK, url=mock.url(scenario))
    assert_failure(run, lang, http_msg(status, hint), str(status))
    (req,) = mock.requests()
    assert req["status"] == status


def test_t_s_18_redirect_is_not_followed(lang, mock, run_snippet):
    """T-s-18 (S1): a 302 whose Location is the working `ok` URL is a failure, not a success,
    and the redirect is not followed: the mock sees exactly one request."""
    run = run_snippet(lang, ENV_OK, url=mock.url("redirect"))
    assert_failure(run, lang, http_msg(302, HOTHER), "302")
    (req,) = mock.requests()
    assert (req["path"], req["status"]) == ("/s/redirect/api/v1/quantum-entropy", 302)


# --- transport failures (S6) -----------------------------------------------------------


def test_t_s_13_refused_connection(lang, mock, run_snippet, closed_port):
    """T-s-13 (S6): a closed port gives the network message and no traceback. The detail is the
    error code (Node) or the exception class (Python)."""
    url = f"http://127.0.0.1:{closed_port}/s/ok/api/v1/quantum-entropy?size=1"
    run = run_snippet(lang, ENV_OK, url=url)
    detail = {"node": "ECONNREFUSED", "python": "ConnectionError"}[lang]
    assert_failure(run, lang, f"Could not reach Qrypt ({detail}).", "network")
    assert mock.requests() == []


def test_t_s_14_unresolvable_host(lang, run_snippet):
    """T-s-14 (S6): a host that cannot resolve (.invalid, RFC 6761) gives the network message.
    The detail depends on the resolver (Node: ENOTFOUND or EAI_AGAIN), so it is matched
    loosely; Python reports the exception class."""
    url = "http://qrypt-quickstart-test.invalid/api/v1/quantum-entropy?size=1"
    run = run_snippet(lang, ENV_OK, url=url)
    detail = r"(ENOTFOUND|EAI_AGAIN)" if lang == "node" else "ConnectionError"
    assert_failure(run, lang, rf"Could not reach Qrypt \({detail}\)\.", "network", regex=True)


# --- runtime check (S4) ----------------------------------------------------------------


@pytest.mark.node
def test_t_s_15_node_without_loadenvfile(mock, run_snippet):
    """T-s-15 (S4, Node only): with process.loadEnvFile deleted by a --import stub, the snippet
    fails with the runtime message before doing anything else (no request)."""
    stub = "data:text/javascript,delete process.loadEnvFile"
    found = subprocess.run(["node", "--version"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=30, check=True).stdout.strip()
    run = run_snippet("node", ENV_OK, url=mock.url("ok"), node_args=["--import", stub])
    assert_failure(run, "node", f"Node.js 20.12+ required (found {found}).", "runtime")
    assert mock.requests() == []


# --- malformed 200 (P1) ----------------------------------------------------------------


def test_t_s_16_badjson_exits_non_zero(lang, mock, run_snippet):
    """T-s-16 (P1): a 200 with a body that is not the expected JSON exits non-zero and never
    claims success. The stack trace is accepted, and documented on the troubleshooting page."""
    run = run_snippet(lang, ENV_OK, url=mock.url("badjson"))
    assert run.code != 0
    assert "Generated a 256-bit AES key" not in run.stdout
    assert len(mock.requests()) == 1


# --- failure format (S10, S11) ---------------------------------------------------------


def test_t_s_17_help_constant(lang):
    """T-s-17 (S11): HELP is an https URL ending in /quickstart/troubleshooting/, and both
    snippets use the same one."""
    help_url = read_help(lang)
    assert help_url.startswith("https://")
    assert help_url.endswith("/quickstart/troubleshooting/")
    assert help_url == read_help("python" if lang == "node" else "node")


def test_t_s_17_every_failure_ends_with_the_fix_line(lang, mock, run_snippet, closed_port):
    """T-s-17 (S10): every failure's stderr ends with `  Fix: <HELP>#<anchor>`."""
    help_url = read_help(lang)
    closed = f"http://127.0.0.1:{closed_port}/s/ok/api/v1/quantum-entropy?size=1"
    cases = [(None, mock.url("ok"), "no-key"), (ENV_OK, closed, "network")]
    cases += [(ENV_OK, mock.url(s), str(code)) for _, s, code, _ in HTTP_CASES]
    for env_file, url, anchor in cases:
        run = run_snippet(lang, env_file, url=url)
        assert run.code == 1, (anchor, run.stderr)
        lines = run.stderr.rstrip("\n").split("\n")
        assert len(lines) == 2, (anchor, run.stderr)
        assert lines[0].startswith("✘ ")
        assert lines[-1] == f"  Fix: {help_url}#{anchor}", (anchor, run.stderr)


# --- the crypto contract (S7) and the default URL (S2) ---------------------------------

MESSAGE = b"Hello from Qrypt quantum entropy"
SHA256 = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731
DEFAULT_URL = "https://api-eus.qrypt.com/api/v1/quantum-entropy?size=1"

# Instrumentation, loaded before the snippet. It records only fingerprints (SHA-256 of the key
# and of the plaintext, the algorithm, the IV length) to the JSON file named by FP_RECORD, and
# with FP_MODE=mismatch makes decryption return a different plaintext.
PY_HOOK = """
import hashlib, json, os
import cryptography.hazmat.primitives.ciphers.aead as aead

REAL, REC, rec = aead.AESGCM, os.environ["FP_RECORD"], {}
sha = lambda data: hashlib.sha256(bytes(data)).hexdigest()


def save():
    with open(REC, "w") as out:
        json.dump(rec, out)


class AESGCM:
    def __init__(self, key):
        rec.update(algorithm=f"aes-{len(key) * 8}-gcm", key_sha256=sha(key))
        self.real = REAL(key)
        save()

    def encrypt(self, nonce, data, aad):
        rec.update(iv_length=len(nonce), plaintext_sha256=sha(data))
        save()
        return self.real.encrypt(nonce, data, aad)

    def decrypt(self, nonce, data, aad):
        out = self.real.decrypt(nonce, data, aad)
        return out + b"!" if os.environ.get("FP_MODE") == "mismatch" else out


aead.AESGCM = AESGCM
"""

NODE_HOOK = """
import crypto from "node:crypto";
import { writeFileSync } from "node:fs";
import { syncBuiltinESMExports } from "node:module";

const rec = {}, real = { c: crypto.createCipheriv, d: crypto.createDecipheriv };
const sha = (data) => crypto.createHash("sha256").update(data).digest("hex");
const save = () => writeFileSync(process.env.FP_RECORD, JSON.stringify(rec));

crypto.createCipheriv = (algorithm, key, iv, ...rest) => {
  Object.assign(rec, { algorithm, key_sha256: sha(key), iv_length: iv.length });
  const cipher = real.c(algorithm, key, iv, ...rest), update = cipher.update.bind(cipher);
  cipher.update = (data, ...args) => {
    rec.plaintext_sha256 = sha(typeof data === "string" ? Buffer.from(data, args[0]) : data);
    save();
    return update(data, ...args);
  };
  return cipher;
};
crypto.createDecipheriv = (...args) => {
  const decipher = real.d(...args), update = decipher.update.bind(decipher);
  decipher.update = (...a) => {
    const out = update(...a);
    return process.env.FP_MODE === "mismatch" ? Buffer.concat([out, Buffer.from("!")]) : out;
  };
  return decipher;
};
syncBuiltinESMExports();
"""


def run_instrumented(lang, mock, run_snippet, tmp_path, mode=""):
    """Run the snippet against the `ok` scenario with the crypto instrumentation loaded.
    Returns (run, record), where record is the dict of fingerprints (empty if none written)."""
    hook = tmp_path / ("sitecustomize.py" if lang == "python" else "hook.mjs")
    hook.write_text(PY_HOOK if lang == "python" else NODE_HOOK, encoding="utf-8")
    record = tmp_path / "fingerprints.json"
    env = {"FP_RECORD": str(record), "FP_MODE": mode}
    if lang == "python":
        env["PYTHONPATH"] = str(tmp_path)
        run = run_snippet(lang, ENV_OK, env=env, url=mock.url("ok"))
    else:
        run = run_snippet(lang, ENV_OK, env=env, url=mock.url("ok"),
                          node_args=["--import", hook.as_uri()])
    text = record.read_text(encoding="utf-8") if record.exists() else "{}"
    key = bytes(range(32))
    assert key.hex() not in text and key.hex() not in run.stdout + run.stderr  # fingerprints only
    return run, json.loads(text)


def test_t_s_19_key_algorithm_iv_and_plaintext(lang, mock, run_snippet, tmp_path):
    """T-s-19 (S7): the AES key is bytes [0, 32) of the block (the mock serves bytes 0..255
    repeated), the algorithm is AES-256-GCM, the IV or nonce is 12 bytes, and the plaintext is
    the fixed message. Only SHA-256 fingerprints are recorded, never key bytes."""
    run, rec = run_instrumented(lang, mock, run_snippet, tmp_path)
    assert (run.code, run.stdout, run.stderr) == (0, SUCCESS, "")
    assert rec == {"algorithm": "aes-256-gcm", "key_sha256": SHA256(bytes(range(32))),
                   "iv_length": 12, "plaintext_sha256": SHA256(MESSAGE)}


def test_t_s_20_a_decryption_mismatch_fails_the_round_trip(lang, mock, run_snippet, tmp_path):
    """T-s-20 (S7): when decryption returns a different plaintext, the snippet exits 1 with the
    round-trip message instead of claiming success."""
    run, rec = run_instrumented(lang, mock, run_snippet, tmp_path, mode="mismatch")
    assert rec["plaintext_sha256"] == SHA256(MESSAGE)  # the instrumentation was active
    assert_failure(run, lang, "AES-GCM round trip failed.", "round-trip")


def test_t_s_21_default_url_literal(lang):
    """T-s-21 (S2): with no QRYPT_API_URL the snippet calls the production URL. This is a static
    check (no live call): the one api-eus literal is the fallback of QRYPT_API_URL."""
    text = snippet_path(lang).read_text(encoding="utf-8")
    assert text.count("api-eus.qrypt.com") == 1
    assert re.search(rf'QRYPT_API_URL[^\n]*"{re.escape(DEFAULT_URL)}"', text), text
