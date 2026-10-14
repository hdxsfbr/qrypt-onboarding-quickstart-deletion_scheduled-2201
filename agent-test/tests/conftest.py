"""Shared fixtures for the agent-harness tests: loads run.py, scrub.py and the proxy addon, and
builds recorded run folders (transcript, proxy.jsonl, workspace, meta.json) in a temp dir.
No network, no Docker."""
import base64
import hashlib
import importlib.util
import json
import os
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
AGENT_TEST = HERE.parent
os.environ["QH_ADDON_NO_INIT"] = "1"  # importing the addon must not open a log or start a mock
sys.path.insert(0, str(AGENT_TEST))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUN = _load("agent_run", AGENT_TEST / "run.py")
ADDON = _load("harness_addon", AGENT_TEST / "proxy" / "addon.py")

QRYPT_KEY = "-".join(["qk", "test", "0123456789", "abcdefABCDEF"])  # a made-up value
GEMINI_KEY = "-".join(["gk", "test", "ZYXWVUTSRQPONMLKJIHGFEDCBA"])  # a made-up value
AES_KEY = bytes(range(32))                      # first 32 bytes of the mock's 1 KiB block
AES_HEX = AES_KEY.hex()
AES_B64 = base64.b64encode(AES_KEY).decode()
FINGERPRINT = hashlib.sha256(AES_KEY).hexdigest()
SUCCESS_LINE = "Generated a 256-bit AES key from Qrypt entropy (round trip OK)."

GOOD_PROGRAM = """\
import { createCipheriv } from "node:crypto";
process.loadEnvFile();
const token = process.env.QRYPT_API_KEY;
const res = await fetch("https://api-eus.qrypt.com/api/v1/quantum-entropy?size=1",
  { headers: { Authorization: `Bearer ${token}` } });
const key = Buffer.from((await res.json()).random[0], "base64").subarray(0, 32);
console.log("%s");
""" % SUCCESS_LINE


@pytest.fixture(scope="session")
def run():
    return RUN


@pytest.fixture(scope="session")
def addon():
    return ADDON


def entropy_row(*, host="api-eus.qrypt.com", status=200, utc="2026-10-14T20:00:00+00:00",
                query="size=1", scheme="bearer", fingerprint=FINGERPRINT, **extra):
    row = {"utc": utc, "method": "GET", "host": host, "path": "/api/v1/quantum-entropy",
           "query": query, "status": status, "user_agent": "node",
           "authorization": "Bearer <redacted>" if scheme == "bearer" else None,
           "x_api_key": None, "auth_scheme": scheme}
    if status == 200:
        row["random_block_bytes"] = [1024]
        row["key_fingerprints"] = [fingerprint]
    row.update(extra)
    return row


def gemini_row(status=200):
    return {"utc": "2026-10-14T20:00:00+00:00", "method": "POST",
            "host": "generativelanguage.googleapis.com",
            "path": "/v1beta/models/m:streamGenerateContent", "status": status,
            "user_agent": "GeminiCLI", "authorization": None, "x_api_key": None,
            "auth_scheme": "x-goog-api-key"}


def message(text):
    return {"type": "message", "role": "assistant", "content": text, "delta": True}


def tool_use(command):
    return {"type": "tool_use", "tool_name": "run_shell_command", "tool_id": "1",
            "parameters": {"command": command}}


def tool_result(output):
    return {"type": "tool_result", "tool_id": "1", "status": "success", "output": output}


GOOD_EVENTS = [{"type": "init"}, message("Writing the program."), tool_use("node index.mjs"),
               tool_result(SUCCESS_LINE), message("Done. Run it with `node index.mjs`."),
               {"type": "result", "status": "success"}]

GOOD_RERUN = {"attempted": True, "exit": 0, "timed_out": False, "stdout": SUCCESS_LINE + "\n",
              "stderr": "", "mark": 1, "entry": {"argv": ["node", "index.mjs"], "cwd": "",
                                                  "source": "final-message"}}


@pytest.fixture
def make_run(tmp_path):
    """Build a recorded run folder and return its path. Defaults describe a clean success;
    override any part."""
    counter = {"n": 0}

    def build(*, files=None, events=None, proxy=None, rerun=None, wall_s=42.0, timed_out=False,
              agent_exit=0, env_existed=True, stderr="", rerun_proxy=None, meta=None):
        counter["n"] += 1
        folder = tmp_path / f"run-{counter['n']}"
        (folder / "workspace").mkdir(parents=True)
        files = {"index.mjs": GOOD_PROGRAM, ".env": f"QRYPT_API_KEY={QRYPT_KEY}\n"} \
            if files is None else files
        for name, body in files.items():
            target = folder / "workspace" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        events = GOOD_EVENTS if events is None else events
        (folder / "transcript.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in events))
        (folder / "agent.stderr.log").write_text(stderr)
        rows = [entropy_row()] if proxy is None else proxy
        rerun = dict(GOOD_RERUN) if rerun is None else rerun
        if rerun.get("attempted"):
            rerun["mark"] = len(rows)
            rows = rows + ([entropy_row(utc="2026-10-14T20:00:20+00:00")]
                           if rerun_proxy is None else rerun_proxy)
        (folder / "proxy.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        info = {"run_id": "1-01", "round": "1", "model": "m", "wall_s": wall_s,
                "timed_out": timed_out, "agent_exit": agent_exit, "env_existed": env_existed,
                "rerun": rerun, "docker_error": None, "agent_cmd": False,
                "started_utc": "2026-10-14T20:00:00+00:00", "ended_utc": "2026-10-14T20:01:00+00:00"}
        info.update(meta or {})
        (folder / "meta.json").write_text(json.dumps(info))
        return folder

    return build
