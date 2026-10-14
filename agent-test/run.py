#!/usr/bin/env python3
"""Agent-readiness harness (docs/TDD.md section 6, rules A1-A12).

Gives an AI coding agent (Gemini CLI, pinned) only the quickstart URL and a fixed prompt, in a
fresh container whose only route to the network is a logging mitmproxy, then decides from the
proxy log, the transcript and the workspace whether the agent produced a working AES-256 key
from Qrypt entropy.

  python3 agent-test/run.py --round {1,2,dry} --url <quickstart URL> --model <model>
        [--n 1] [--timeout 1200] [--docs-version docs-r1]

This talks to the LIVE Qrypt API and the Gemini API (unless --offline). Keys are read only from
~/.config/agentforge/qrypt_api_key and gemini_api_key, passed to the agent container as
QRYPT_API_KEY / GEMINI_API_KEY through a mode-600 --env-file that is deleted afterwards, and
never printed or put on a command line.

Per run (A1): a fresh internal Docker network, a proxy container and an agent container, in
`agent-test/runs/<round>-<nn>/` (workspace/, transcript.jsonl, proxy.jsonl, ...). After the agent
exits (or at --timeout seconds) the harness re-runs the agent's program once (A7b), evaluates the
checks (a)-(f) (A7), picks one failure category (A8), appends the ledger rows (A12), appends
runs.csv (A9), scrubs the run folder (A10) and deletes workspace/.env.

Test hooks (not part of a live round): --offline serves the repo's mock instead of Qrypt and
blocks every other host; --agent-cmd SCRIPT runs a shell script in place of Gemini; --key-file,
--ledger, --runs-csv and --runs-dir redirect the inputs and outputs. See agent-test/README.md.
"""
import argparse
import base64
import csv
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import scrub  # noqa: E402

LEDGER = REPO / "docs" / "evidence" / "usage.csv"
RUNS_CSV = HERE / "runs.csv"
RUNS_DIR = HERE / "runs"
PROMPT_FILE = HERE / "prompt.txt"
ADDON = HERE / "proxy" / "addon.py"
MOCK = REPO / "mock" / "server.py"
DEFAULT_QRYPT_KEY = "~/.config/agentforge/qrypt_api_key"
DEFAULT_GEMINI_KEY = "~/.config/agentforge/gemini_api_key"

AGENT_IMAGE = "qrypt-agent-test:gemini-0.62.0"
# Pinned by tag and digest (mitmproxy 12.2.3, published 2026-05-12).
MITM_IMAGE = ("mitmproxy/mitmproxy:12.2.3@sha256:"
              "00b77b5d8804c8ad18cb6caefbf9d5849e895e8986c5ce011f4ae30f4385962f")
PROXY_PORT = 8080
DNS_PORT = 53
SUBNET_PREFIX = "10.231"
CA_IN_AGENT = "/usr/local/share/ca-certificates/mitmproxy-ca.crt"
SYSTEM_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
LABEL = "qrypt-agent-test=1"

KIB = 1024
MIB = 1024 * KIB
BUDGET_CAP = 9 * MIB          # A12: 1 MiB reserve under the 10 MB cap
BUDGET_PLANNED = 64 * KIB     # a run is assumed to need up to 64 KiB
RERUN_LIMIT_S = 120
RERUN_RATE_RETRY_S = 20
EAAS_HOSTS = ("api-eus.qrypt.com", "eaas-aws.qrypt.com")
ENTROPY_PATH = "/api/v1/quantum-entropy"
GEMINI_HOST = "generativelanguage.googleapis.com"

CSV_COLUMNS = ["run_id", "round", "docs_version", "model", "started_utc", "ended_utc", "wall_s",
               "success", "check_a", "check_b", "check_c", "check_d", "check_e", "check_f",
               "primary_failure", "notes"]
CATEGORIES = ["wrong-endpoint", "wrong-header", "unit-confusion", "base64-not-decoded",
              "invented-sdk", "skipped-env", "key-printed", "dependency-failure",
              "cloudflare-block", "rate-limited", "timeout", "gave-up", "other"]

SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "__pycache__", "site-packages", ".cache",
             ".npm", ".gemini"}
SRC_EXT = {".js", ".mjs", ".cjs", ".py"}
KEY_SCAN_SKIP = {"node_modules", ".venv", "venv", ".git", "__pycache__", "site-packages"}


# Gemini CLI settings written per run and mounted read-only (see README, "Gemini settings").
# The CLI's helper tools (web_fetch, web_search, the classifier, summarizers, loop detection...)
# use internal model aliases that point at hard-coded models, some with no free-tier quota. Every
# alias is remapped to the model under test, so a round never mixes models (TDD section 11).
BASE_ALIASES = ("gemini-3-flash-base", "gemini-3.5-flash-base", "gemini-2.5-flash-base",
                "context-calibrator")
# Aliases that name their own model (and keep their own generateContentConfig): remapped with an
# override that matches the alias, which changes only `model`.
OVERRIDDEN_ALIASES = (
    "classifier", "prompt-completion", "fast-ack-helper", "edit-corrector", "summarizer-default",
    "summarizer-shell", "loop-detection-double-check", "chat-compression-3-pro",
    "chat-compression-3-flash", "chat-compression-3.1-flash-lite", "chat-compression-2.5-pro",
    "chat-compression-2.5-flash", "chat-compression-2.5-flash-lite", "chat-compression-default",
    "agent-history-provider-summarizer")


def gemini_settings(model):
    """The per-run ~/.gemini/settings.json: auto-update and usage statistics off, sub-agents off,
    and every internal model alias pointed at `model`."""
    return {
        "general": {"enableAutoUpdate": False, "enableAutoUpdateNotification": False},
        "privacy": {"usageStatisticsEnabled": False},
        "experimental": {"enableAgents": False},
        "modelConfigs": {
            "customAliases": {name: {"extends": "base", "modelConfig": {"model": model}}
                              for name in BASE_ALIASES},
            "customOverrides": [{"match": {"model": name}, "modelConfig": {"model": model}}
                                for name in OVERRIDDEN_ALIASES]}}


class BudgetError(Exception):
    """The ledger plus one planned run would exceed the cap (A12)."""


class DockerError(Exception):
    """A Docker or proxy problem: a harness error (A9), not an agent failure."""


# --------------------------------------------------------------------------------------
# Ledger, budget guard, CSV
# --------------------------------------------------------------------------------------
def ledger_total(path):
    """Sum of the entropy_bytes column; 0 when the ledger does not exist yet."""
    path = pathlib.Path(path)
    if not path.exists():
        return 0
    total = 0
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                total += int(row["entropy_bytes"])
            except (KeyError, TypeError, ValueError):
                raise SystemExit(f"cannot read the ledger {path}: bad row {row}")
    return total


def check_budget(ledger, planned=BUDGET_PLANNED, cap=BUDGET_CAP):
    """A12: raise BudgetError when ledger total + planned would exceed the cap."""
    total = ledger_total(ledger)
    if total + planned > cap:
        raise BudgetError(f"ledger total {total} B + planned {planned} B exceeds the "
                          f"{cap} B budget guard ({ledger})")
    return total


def append_ledger(path, rows):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as fh:
        writer = csv.writer(fh)
        if new:
            writer.writerow(["utc_time", "probe", "status", "entropy_bytes"])
        writer.writerows(rows)


def ledger_rows(proxy_rows, run_id):
    """One row per 200 from a *.qrypt.com host that carried entropy blocks: the sum of the
    recorded decoded block lengths (1,024 B per block)."""
    rows = []
    for r in proxy_rows:
        if is_qrypt_host(r.get("host")) and r.get("status") == 200 and r.get("random_block_bytes"):
            rows.append([r["utc"], f"agent_test_{run_id}", 200, sum(r["random_block_bytes"])])
    return rows


def append_runs_csv(path, row):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, lineterminator="\n")
        if new:
            writer.writeheader()
        writer.writerow(row)


# --------------------------------------------------------------------------------------
# Reading run artifacts
# --------------------------------------------------------------------------------------
def is_qrypt_host(host):
    host = (host or "").lower().rstrip(".")
    return host == "qrypt.com" or host.endswith(".qrypt.com")


def read_jsonl(path):
    """Parsed JSON objects of a JSON-lines file; unparsable lines (a torn last line) skipped."""
    path = pathlib.Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def read_text(path):
    path = pathlib.Path(path)
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def is_entropy_row(row):
    return is_qrypt_host(row.get("host")) and (row.get("path") or "").startswith(ENTROPY_PATH)


def is_entropy_ok(row):
    return (is_entropy_row(row) and row.get("method") == "GET" and row.get("status") == 200
            and row.get("host") in EAAS_HOSTS)


def strings_in(value):
    """Every string leaf of a decoded JSON value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings_in(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings_in(v)


def final_message(events):
    """The agent's last message: assistant text after the last tool_use/tool_result event.
    Returns (final_text, all_assistant_text)."""
    final, everything = [], []
    for ev in events:
        kind = ev.get("type")
        if kind in ("tool_use", "tool_result"):
            final = []
        elif kind == "message" and ev.get("role") == "assistant":
            text = ev.get("content") or ev.get("text") or ""
            if isinstance(text, str):
                final.append(text)
                everything.append(text)
    return "".join(final), "".join(everything)


def tool_texts(events):
    """Text of tool results and errors, where install errors and stack traces show up."""
    out = []
    for ev in events:
        if ev.get("type") == "tool_result":
            out.extend(strings_in({k: ev.get(k) for k in ("output", "error")}))
        elif ev.get("type") == "error":
            out.append(str(ev.get("message", "")))
    return out


def tool_commands(events):
    out = []
    for ev in events:
        if ev.get("type") == "tool_use":
            out.extend(strings_in(ev.get("parameters")))
    return out


# --------------------------------------------------------------------------------------
# Entry point detection (A7b)
# --------------------------------------------------------------------------------------
CMD_RE = re.compile(
    r"""(?P<venv>(?:\./)?\.?venv/bin/)?(?P<tool>node|nodejs|python3?)\s+
        (?P<file>(?:\./)?[A-Za-z0-9_@][A-Za-z0-9_@./-]*\.(?:mjs|cjs|js|py))(?![A-Za-z0-9_./-])
      | \bnpm\s+(?:(?P<start>start)|run\s+(?P<script>[A-Za-z0-9:_-]+))(?![A-Za-z0-9:_-])""",
    re.VERBOSE)
CD_RE = re.compile(r"""\bcd\s+(?:"([^"]+)"|'([^']+)'|([A-Za-z0-9_@./-]+))""")


def walk_sources(workspace):
    """Source files (.js .mjs .cjs .py) under the workspace, outside the skipped directories."""
    workspace = pathlib.Path(workspace)
    for dirpath, dirnames, filenames in os.walk(workspace):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if pathlib.Path(name).suffix in SRC_EXT:
                yield pathlib.Path(dirpath, name)


def _safe_rel(workspace, rel):
    """workspace/rel as a path if `rel` is relative and has no `..` part, else None. Symlinks are
    not resolved on purpose: a venv's python is a link to a path that exists only in the image."""
    if not rel or rel.startswith("/") or ".." in pathlib.PurePosixPath(rel).parts:
        return None
    return pathlib.Path(workspace) / rel


def _python_for(workspace, cwd_rel, venv_prefix):
    """In-container path of the interpreter: the workspace venv when there is one."""
    base = pathlib.PurePosixPath(cwd_rel)
    candidates = []
    if venv_prefix:
        candidates.append(base / venv_prefix.removeprefix("./") / "python")
    candidates += [base / ".venv/bin/python", base / "venv/bin/python",
                   pathlib.PurePosixPath(".venv/bin/python"), pathlib.PurePosixPath("venv/bin/python")]
    for cand in candidates:
        target = _safe_rel(workspace, str(cand))
        if target is not None and os.path.lexists(target):
            return "/work/" + str(cand)
    return "python3"


def _entry_from_text(workspace, text):
    """The LAST well-formed run command in `text` that points at something that exists."""
    workspace = pathlib.Path(workspace)
    for m in reversed(list(CMD_RE.finditer(text))):
        cds = CD_RE.findall(text[max(0, m.start() - 400):m.start()])
        cd = next(x for x in cds[-1] if x).removeprefix("./") if cds else None
        for base in ([cd] if cd else []) + [""]:
            base = base.rstrip("/")
            if m.group("file"):
                file = m.group("file").removeprefix("./")
                target = _safe_rel(workspace, (base + "/" if base else "") + file)
                if target is None or not target.is_file():
                    continue
                if m.group("tool").startswith("python"):
                    interp = _python_for(workspace, base, m.group("venv"))
                else:
                    interp = "node"
                return {"argv": [interp, file], "cwd": base}
            pkg = _safe_rel(workspace, (base + "/" if base else "") + "package.json")
            if pkg is not None and pkg.is_file():
                argv = ["npm", "start"] if m.group("start") else ["npm", "run", m.group("script")]
                return {"argv": argv, "cwd": base}
    return None


def find_entrypoint(workspace, events):
    """A7b. 1) a run command in the agent's final message; 2) one in any assistant message;
    3) the single .js/.mjs/.cjs/.py file in the workspace that mentions qrypt (narrowed to the
    one that mentions the entropy path when several do). Returns {argv, cwd, source} or None.
    Only `node|python|python3 <file>`, `npm start` and `npm run <script>` are ever run, as an
    argv list (no shell)."""
    workspace = pathlib.Path(workspace)
    final, everything = final_message(events)
    for source, text in (("final-message", final), ("assistant-text", everything)):
        found = _entry_from_text(workspace, text)
        if found:
            return {**found, "source": source}
    hits, with_path = [], []
    for path in walk_sources(workspace):
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "qrypt" in body.lower():
            hits.append(path)
            if "quantum-entropy" in body:
                with_path.append(path)
    pick = hits if len(hits) == 1 else with_path if len(with_path) == 1 else None
    if not pick:
        return None
    path = pick[0]
    rel = path.relative_to(workspace)
    cwd = str(rel.parent) if str(rel.parent) != "." else ""
    interp = "node" if path.suffix in (".js", ".mjs", ".cjs") else _python_for(workspace, cwd, None)
    return {"argv": [interp, rel.name], "cwd": cwd, "source": "single-file"}


# --------------------------------------------------------------------------------------
# Secret and key-print checks (A7c, A7d)
# --------------------------------------------------------------------------------------
def key_files(workspace, key):
    """Workspace files (other than `.env` at the workspace root) containing the key value."""
    if not key:
        return []
    needle = key.encode()
    hits = []
    workspace = pathlib.Path(workspace)
    for dirpath, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in KEY_SCAN_SKIP]
        for name in filenames:
            path = pathlib.Path(dirpath, name)
            rel = path.relative_to(workspace)
            if str(rel) == ".env" or path.is_symlink():
                continue
            try:
                if path.stat().st_size <= 20 * MIB and needle in path.read_bytes():
                    hits.append(str(rel))
            except OSError:
                continue
    return sorted(hits)


HEX_RUN = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64,4096}(?![0-9A-Fa-f])")
B64_RUN = re.compile(r"(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+/_-]{43,6000}={0,2}(?![A-Za-z0-9+/_=-])")


def _decode_candidate(text, kind):
    try:
        if kind == "hex":
            return bytes.fromhex(text) if len(text) % 2 == 0 else b""
        pad = "=" * (-len(text.rstrip("=")) % 4)
        body = text.rstrip("=") + pad
        return base64.urlsafe_b64decode(body) if ("-" in text or "_" in text) \
            else base64.b64decode(body, validate=True)
    except ValueError:
        return b""


def scan_key_prints(texts, fingerprints):
    """A7d. Returns (matches, others). A 64+-char hex run or 43+-char base64 run whose first 32
    decoded bytes hash to a recorded fingerprint is a match (the AES key, or a block that starts
    with it). Runs of exactly 64 hex / 44 base64 characters that match nothing are `others`."""
    fingerprints = set(fingerprints)
    matches, others = 0, 0
    for text in texts:
        for kind, rx in (("hex", HEX_RUN), ("b64", B64_RUN)):
            for m in rx.finditer(text):
                token = m.group(0)
                raw = _decode_candidate(token, kind)
                if len(raw) < 32:
                    continue
                if hashlib.sha256(raw[:32]).hexdigest() in fingerprints:
                    matches += 1
                elif (kind == "hex" and len(token) == 64) or \
                        (kind == "b64" and len(token.rstrip("=")) in (43, 44) and len(raw) == 32):
                    others += 1
    return matches, others


ENV_REF_RE = re.compile(r"QRYPT_API_KEY|process\.env|os\.environ|os\.getenv|getenv\(|dotenv|"
                        r"loadEnvFile|['\"]\.env['\"]|\.env\b")


def reads_env(workspace):
    """A7e: does any source file take the key from `.env` or the environment?"""
    for path in walk_sources(workspace):
        try:
            if ENV_REF_RE.search(path.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            continue
    return False


def workspace_listing(workspace):
    """File names only. Directories that hold installed packages are listed as one entry."""
    workspace = pathlib.Path(workspace)
    lines = []
    for dirpath, dirnames, filenames in os.walk(workspace):
        rel_dir = pathlib.Path(dirpath).relative_to(workspace)
        keep = []
        for d in sorted(dirnames):
            if d in {"node_modules", ".venv", "venv", ".git", "__pycache__", "site-packages"}:
                lines.append(str(rel_dir / d) + "/ (contents not listed)")
            else:
                keep.append(d)
        dirnames[:] = keep
        lines.extend(str(rel_dir / f) for f in sorted(filenames))
    return sorted(lines)


# --------------------------------------------------------------------------------------
# Assessment: checks (a)-(f), failure category (A7, A8)
# --------------------------------------------------------------------------------------
DEP_RE = re.compile(
    r"npm (?:ERR!|error)|ERESOLVE|\bE404\b|externally-managed-environment|ModuleNotFoundError|"
    r"No module named|Cannot find (?:module|package)|No matching distribution|"
    r"Could not find a version|error: subprocess-exited-with-error|ENOTFOUND registry|"
    r"ImportError|ERR_MODULE_NOT_FOUND", re.I)
CLOUDFLARE_RE = re.compile(r"just a moment|attention required|error code:? ?1010|cf-mitigated|"
                           r"cloudflare.{0,40}(?:block|challenge)|error 1020", re.I)
# Matched against the agent's tool calls only (commands and file contents it wrote), never prose.
SDK_RE = re.compile(r"(?:pip3?|npm|yarn|pnpm)\s+(?:install|add|i)\b[^\n]*\bqrypt|"
                    r"^\s*import\s+qrypt\b|^\s*from\s+qrypt(?:\.\w+)*\s+import\b|"
                    r"require\(\s*['\"]@?qrypt|from\s+['\"]@?qrypt[\w/-]*['\"]", re.I | re.M)
KEYLEN_RE = re.compile(r"invalid key (?:length|size)|ERR_CRYPTO_INVALID_KEYLEN|"
                       r"must be 128, 192, or 256 bits|key must be 16, 24, or 32 bytes", re.I)
NOKEY_RE = re.compile(r"(?:QRYPT_API_KEY|api key|token)[^\n]{0,60}(?:not set|missing|undefined|"
                      r"required|empty)|no[- ]key", re.I)
GEMINI_CAPACITY_RE = re.compile(r"RESOURCE_EXHAUSTED|UNAVAILABLE|\b429\b|\b503\b|quota|"
                                r"high demand|overloaded|rate.?limit", re.I)
SIZE_RE = re.compile(r"(?:^|&)size=(\d+)")
BASE64_USE_RE = re.compile(r"base64|b64decode|atob|fromhex|from_hex", re.I)


def gemini_capacity_problem(events, agent_stderr, proxy_rows, agent_exit, timed_out):
    """A9: a Gemini quota/availability problem (429, 503, RESOURCE_EXHAUSTED, UNAVAILABLE) that
    ended the run. Looks only at the agent's error events, its result event, its stderr and the
    status of its last Gemini API calls, never at tool output (a Qrypt 429 there is the agent's
    business, not the harness's)."""
    errors = [e for e in events if e.get("type") == "error" and e.get("severity") != "warning"]
    results = [e for e in events if e.get("type") == "result"]
    result_failed = bool(results) and results[-1].get("status") == "error"
    ended_badly = result_failed or agent_exit not in (0, None) or not results
    texts = [str(e.get("message", "")) for e in errors]
    if results and results[-1].get("error"):
        texts.extend(strings_in(results[-1]["error"]))
    texts.append(agent_stderr)
    gemini = [r for r in proxy_rows if r.get("host") == GEMINI_HOST]
    last = [r.get("status") for r in gemini[-3:]]
    unavailable = (429, 500, 502, 503, 504)
    if timed_out and last and all(s in unavailable for s in last):
        return f"timed out while Gemini answered {last}"
    if ended_badly and any(GEMINI_CAPACITY_RE.search(t) for t in texts):
        return "Gemini quota or availability error ended the run"
    if ended_badly and gemini and gemini[-1].get("status") in unavailable:
        return f"last Gemini API call returned {gemini[-1]['status']}"
    return None


def assess(*, workspace, events, agent_stderr, proxy_rows, qrypt_key, wall_s, timeout_s,
           timed_out, agent_exit, env_existed, rerun, transcript_raw="", rerun_out=""):
    """Evaluate a finished run. `rerun` is {attempted, entry, exit, timed_out, mark, stderr,
    stdout, skipped_reason}; `mark` is the number of proxy rows that existed when it started.
    Returns {checks, success, primary_failure, notes, harness_error}."""
    notes = []
    workspace = pathlib.Path(workspace)
    all_rows = proxy_rows  # `mark` indexes this list, which also holds the DNS query rows
    proxy_rows = [r for r in all_rows if r.get("method") != "DNS"]
    entropy_rows = [r for r in proxy_rows if is_entropy_row(r)]
    statuses = [r.get("status") for r in entropy_rows]

    harness = gemini_capacity_problem(events, agent_stderr, proxy_rows, agent_exit, timed_out)
    if harness:
        return {"checks": {}, "success": None, "primary_failure": "harness-error",
                "notes": [harness], "harness_error": harness}

    # (a) a real 200 from the entropy endpoint
    ok_rows = [r for r in proxy_rows if is_entropy_ok(r)]
    check_a = bool(ok_rows)
    if check_a and not any(r["host"] == "api-eus.qrypt.com" for r in ok_rows):
        notes.append("wrong-endpoint: only eaas-aws.qrypt.com returned entropy (docs-clarity signal)")
    if any(is_qrypt_host(r.get("host")) and r["host"] not in EAAS_HOSTS and is_entropy_row(r)
           for r in proxy_rows):
        notes.append("wrong-endpoint: entropy request to a host other than api-eus/eaas-aws")

    # (b) the re-run
    new_rows = [r for r in all_rows[rerun.get("mark", len(all_rows)):] if r.get("method") != "DNS"] \
        if rerun.get("attempted") else []
    rerun_ok_rows = [r for r in new_rows if is_entropy_ok(r)]
    check_b = bool(rerun.get("attempted") and rerun.get("exit") == 0 and not rerun.get("timed_out")
                   and rerun_ok_rows)
    if rerun.get("attempted") and check_b and not rerun.get("stdout", "").strip():
        notes.append("rerun printed nothing to stdout")
    if not rerun.get("attempted"):
        notes.append(f"rerun not run: {rerun.get('skipped_reason', 'unknown')}")
    elif not check_b:
        why = []
        if rerun.get("timed_out"):
            why.append("timed out")
        elif rerun.get("exit") != 0:
            why.append(f"exit {rerun.get('exit')}")
        if not rerun_ok_rows:
            why.append("no fresh 200 from the entropy endpoint")
        notes.append("rerun failed: " + ", ".join(why))
    if rerun.get("entry"):
        notes.append("entrypoint " + " ".join(rerun["entry"]["argv"]) +
                     f" (cwd /{rerun['entry']['cwd']}, from {rerun['entry']['source']})")

    # (c) the Qrypt key only in .env, never in the transcript
    in_files = key_files(workspace, qrypt_key)
    in_transcript = bool(qrypt_key) and any(
        qrypt_key in blob for blob in (transcript_raw, agent_stderr, rerun_out))
    check_c = not in_files and not in_transcript
    if in_files:
        notes.append("Qrypt key found in workspace files: " + ", ".join(in_files[:5]))
    if in_transcript:
        notes.append("Qrypt key value appears in the transcript or program output")

    # (d) no printed AES key
    fingerprints = {fp for r in proxy_rows for fp in (r.get("key_fingerprints") or [])}
    texts = [t for ev in events for t in strings_in(ev)] + [agent_stderr, rerun_out]
    matches, others = scan_key_prints(texts, fingerprints)
    check_d = matches == 0
    if matches:
        notes.append(f"AES key printed: {matches} string(s) match a recorded fingerprint")
    if others:
        notes.append(f"{others} other 64-hex/44-char base64 string(s) in output: manual review")

    # (e) the key comes from .env or the environment
    uses_env = reads_env(workspace)
    check_e = uses_env and not in_files
    if not uses_env:
        notes.append("no source file reads QRYPT_API_KEY from .env or the environment")
    if not env_existed:
        notes.append("agent left no .env file")

    # (f) time
    check_f = (not timed_out) and wall_s <= timeout_s
    if timed_out:
        notes.append(f"killed at the {timeout_s} s cap")

    checks = {"a": check_a, "b": check_b, "c": check_c, "d": check_d, "e": check_e, "f": check_f}
    success = all(checks.values())

    # secondary signals, always listed in notes
    sizes = [int(m.group(1)) for r in entropy_rows for m in [SIZE_RE.search(r.get("query") or "")]
             if m]
    if any(s > 1 for s in sizes) or 400 in statuses:
        notes.append(f"unit signal: size={sorted(set(sizes))} status400={400 in statuses}")
    if 429 in statuses:
        notes.append(f"429 from Qrypt x{statuses.count(429)}")
    if any(r.get("auth_scheme") not in (None, "bearer") for r in entropy_rows):
        notes.append("non-Bearer auth: " + ",".join(sorted({r.get("auth_scheme") for r in entropy_rows
                                                           if r.get("auth_scheme") != "bearer"})))
    if not entropy_rows and not success:
        randoms = [p.name for p in walk_sources(workspace)
                   if re.search(r"randomBytes|urandom|secrets\.token|getRandomValues|random\.",
                                read_text(p))]
        if randoms:
            notes.append("no Qrypt call, but local randomness in " + ", ".join(randoms[:3]))

    primary = "" if success else classify(
        workspace=workspace, events=events, agent_stderr=agent_stderr, proxy_rows=proxy_rows,
        entropy_rows=entropy_rows, new_rows=new_rows, checks=checks, in_files=in_files,
        in_transcript=in_transcript, env_existed=env_existed, uses_env=uses_env, rerun=rerun,
        agent_exit=agent_exit, timed_out=timed_out, notes=notes)
    return {"checks": checks, "success": success, "primary_failure": primary,
            "notes": notes, "harness_error": None}


def classify(*, workspace, events, agent_stderr, proxy_rows, entropy_rows, new_rows, checks,
             in_files, in_transcript, env_existed, uses_env, rerun, agent_exit, timed_out, notes):
    """Exactly one A8 category for a failed run. Order (first rule that fires wins):
      timeout > key-printed (AES key or API key printed) > skipped-env (key in a source file) >
      [no 200 seen] rate-limited, cloudflare-block, invented-sdk, wrong-endpoint, wrong-header,
      unit-confusion, dependency-failure, gave-up/other >
      [a 200 was seen but the re-run or (e) failed] rate-limited, dependency-failure,
      skipped-env, base64-not-decoded, wrong-header, other.
    Heuristics only: the evidence is in `notes` and the coordinator may override."""
    if timed_out or not checks["f"]:
        return "timeout"
    if not checks["d"]:
        return "key-printed"
    if in_transcript:
        return "key-printed"
    if in_files:
        notes.append("hardcoded key")
        return "skipped-env"

    rerun_err = rerun.get("stderr", "") + "\n" + rerun.get("stdout", "")
    statuses = [r.get("status") for r in entropy_rows]
    texts = tool_texts(events) + [agent_stderr]
    commands = "\n".join(tool_commands(events))
    all_text = "\n".join(texts + [commands, "\n".join(final_message(events))])

    if not checks["a"]:
        if 429 in statuses:
            return "rate-limited"
        cf_rows = [r for r in proxy_rows if r.get("cf_mitigated") or (
            r.get("status") in (403, 429, 503) and "cloudflare" in (r.get("server") or "").lower()
            and "html" in (r.get("content_type") or "").lower())]
        if cf_rows or CLOUDFLARE_RE.search(all_text):
            notes.append("cloudflare evidence: " + ", ".join(sorted({r["host"] for r in cf_rows})))
            return "cloudflare-block"
        if SDK_RE.search(commands):
            return "invented-sdk"
        bad_path = [r for r in proxy_rows if is_qrypt_host(r.get("host")) and (
            r.get("status") in (0, 404, 405, 530) or
            (r.get("host") not in EAAS_HOSTS and "entropy" in (r.get("path") or "")))]
        if bad_path:
            notes.append("wrong-endpoint evidence: " + ", ".join(
                sorted({f"{r['host']}{r['path']}={r.get('status')}" for r in bad_path})[:3]))
            return "wrong-endpoint"
        if 401 in statuses or any(r.get("auth_scheme") not in (None, "bearer") for r in entropy_rows):
            return "wrong-header"
        if 400 in statuses or any(
                int(m.group(1)) > 1 for r in entropy_rows
                for m in [SIZE_RE.search(r.get("query") or "")] if m):
            return "unit-confusion"
        if DEP_RE.search(all_text) or DEP_RE.search(rerun_err):
            return "dependency-failure"
        has_code = any(True for _ in walk_sources(workspace))
        if agent_exit not in (0, None) or (not has_code and not entropy_rows):
            return "gave-up"
        return "other"

    # a 200 was seen, so the failure is in the re-run or in (e)
    new_statuses = [r.get("status") for r in new_rows if is_entropy_row(r)]
    if rerun.get("attempted") and not checks["b"]:
        if new_statuses and all(s == 429 for s in new_statuses):
            return "rate-limited"
        if DEP_RE.search(rerun_err):
            return "dependency-failure"
        if not env_existed or NOKEY_RE.search(rerun_err):
            return "skipped-env"
        if KEYLEN_RE.search(rerun_err) or KEYLEN_RE.search(all_text):
            return "base64-not-decoded"
        if 401 in new_statuses:
            return "wrong-header"
        return "other"
    if not rerun.get("attempted"):
        return "other"
    if not checks["e"]:
        return "skipped-env"
    return "other"


# --------------------------------------------------------------------------------------
# Docker orchestration
# --------------------------------------------------------------------------------------
def docker(*args, timeout=None, check=True):
    done = subprocess.run(["docker", *args], stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=timeout)
    if check and done.returncode != 0:
        raise DockerError(f"docker {args[0]} failed ({done.returncode}): "
                          f"{(done.stderr or done.stdout).strip()[:300]}")
    return done


def ensure_image(image, context):
    """Build the agent image when it is missing or its Dockerfile changed (label check)."""
    digest = hashlib.sha256((pathlib.Path(context) / "Dockerfile").read_bytes()).hexdigest()
    have = docker("image", "inspect", "-f", '{{index .Config.Labels "qh.dockerfile"}}', image,
                  check=False)
    if have.returncode == 0 and have.stdout.strip() == digest:
        return
    print(f"building {image} from {context} ...", flush=True)
    done = subprocess.run(["docker", "build", "--label", f"qh.dockerfile={digest}", "-t", image,
                           str(context)], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if done.returncode != 0:
        raise DockerError("docker build failed: " + done.stderr[-400:])


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Sandbox:
    """One run's internal network, proxy container and temp config. Always `close()` it."""

    def __init__(self, tag, run_dir, offline):
        self.tag, self.run_dir, self.offline = tag, pathlib.Path(run_dir), offline
        self.net = f"qh-net-{tag}"
        self.proxy = f"qh-proxy-{tag}"
        self.conf = pathlib.Path(tempfile.mkdtemp(prefix="qh-conf-"))
        self.started = False
        self.ip = None  # the proxy's fixed address on the internal network (also its DNS server)

    def start(self):
        uid, gid = os.getuid(), os.getgid()
        self.close(quiet=True)
        self.conf.mkdir(exist_ok=True)
        self.create_network()
        env = ["-e", "HOME=/tmp", "-e", "QH_PROXY_LOG=/out/proxy.jsonl"]
        mounts = ["-v", f"{self.conf}:/conf", "-v", f"{self.run_dir}:/out",
                  "-v", f"{ADDON}:/addon/addon.py:ro"]
        if self.offline:
            env += ["-e", "QH_OFFLINE_MOCK=/mock/server.py"]
            mounts += ["-v", f"{MOCK.parent}:/mock:ro"]
        # Port 53 is below the unprivileged range: lower it inside the proxy's own network
        # namespace (the proxy still runs as the host user with all capabilities dropped).
        docker("run", "-d", "--name", self.proxy, "--network", self.net, "--ip", self.ip,
               "--label", LABEL, "--user", f"{uid}:{gid}", "--cap-drop", "ALL", "--security-opt",
               "no-new-privileges", "--sysctl", "net.ipv4.ip_unprivileged_port_start=0",
               "--entrypoint", "mitmdump", *env, *mounts, MITM_IMAGE,
               "-s", "/addon/addon.py", "--set", "confdir=/conf",
               "--mode", f"regular@{PROXY_PORT}", "--mode", f"dns@{DNS_PORT}",
               "--set", "flow_detail=0")
        self.started = True
        docker("network", "connect", "bridge", self.proxy)  # the proxy's way out
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if (self.conf / "mitmproxy-ca-cert.pem").exists() and self.accepting():
                return
            if not self.proxy_running():
                raise DockerError("proxy container exited at start-up: " +
                                  docker("logs", self.proxy, check=False).stderr[-300:])
            time.sleep(0.5)
        raise DockerError("proxy did not start listening within 40 s")

    def create_network(self):
        """An internal network with an explicit /24, so the proxy can have a fixed address that
        the agent containers use as their DNS server. Retries other /24s on an overlap."""
        last = ""
        for attempt in range(8):
            third = (os.getpid() + attempt * 7) % 250 + 1
            subnet = f"{SUBNET_PREFIX}.{third}.0/24"
            done = docker("network", "create", "--internal", "--label", LABEL, "--subnet", subnet,
                          self.net, check=False)
            if done.returncode == 0:
                self.ip = f"{SUBNET_PREFIX}.{third}.2"
                return
            last = (done.stderr or done.stdout).strip()
        raise DockerError("cannot create the internal network: " + last[:200])

    def accepting(self):
        """True once the proxy accepts TCP connections on its listen port."""
        probe = ("import socket;socket.create_connection(('127.0.0.1',%d),2).close()" % PROXY_PORT)
        return docker("exec", self.proxy, "python", "-c", probe, check=False).returncode == 0

    def proxy_running(self):
        done = docker("inspect", "-f", "{{.State.Running}}", self.proxy, check=False)
        return done.stdout.strip() == "true"

    @property
    def ca(self):
        return self.conf / "mitmproxy-ca-cert.pem"

    def close(self, quiet=False):
        docker("rm", "-f", self.proxy, check=False)
        docker("network", "rm", self.net, check=False)
        if not quiet:
            shutil.rmtree(self.conf, ignore_errors=True)


def container_env(proxy_host):
    proxy = f"http://{proxy_host}:{PROXY_PORT}"
    local = "localhost,127.0.0.1"
    return {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "https_proxy": proxy, "http_proxy": proxy,
            "NO_PROXY": local, "no_proxy": local,
            "NODE_USE_ENV_PROXY": "1",  # Node 22 fetch() otherwise ignores HTTPS_PROXY
            "NODE_NO_WARNINGS": "1",  # hides Node's "EnvHttpProxyAgent is experimental" notice
            "NODE_EXTRA_CA_CERTS": CA_IN_AGENT, "REQUESTS_CA_BUNDLE": SYSTEM_BUNDLE,
            "PIP_CERT": SYSTEM_BUNDLE,
            # Gemini CLI 0.62 refuses headless runs (exit 55) in an untrusted folder and drops
            # --yolo to "default" approval. /work is the harness's own empty folder.
            "GEMINI_CLI_TRUST_WORKSPACE": "true", "NO_COLOR": "1", "TERM": "dumb",
            "QH_UID": str(os.getuid()), "QH_GID": str(os.getgid())}


def agent_docker_args(box, name, workspace, cwd, env_file=None, extra_mounts=()):
    args = ["run", "--rm", "--name", name, "--network", box.net, "--label", LABEL,
            "--cap-drop", "ALL", "--cap-add", "SETUID", "--cap-add", "SETGID",
            "--security-opt", "no-new-privileges", "--pids-limit", "1024", "--memory", "4g",
            "-v", f"{workspace}:/work", "-v", f"{box.ca}:/ca/mitmproxy-ca-cert.pem:ro",
            "-w", "/work" + (f"/{cwd}" if cwd else "")]
    for src, dst in extra_mounts:
        args += ["-v", f"{src}:{dst}:ro"]
    args += ["--dns", box.ip]  # the proxy's DNS listener: names resolve, nothing else routes
    for k, v in container_env(box.ip).items():
        args += ["-e", f"{k}={v}"]
    if env_file:
        args += ["--env-file", str(env_file)]
    return args


def run_container(args, name, stdout_path, stderr_path, timeout_s):
    """Run `docker <args>` with stdin closed; kill the container at the timeout.
    Returns (exit_code, wall_seconds, timed_out)."""
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        start = time.monotonic()
        proc = subprocess.Popen(["docker", *args], stdin=subprocess.DEVNULL, stdout=out,
                                stderr=err)
        timed_out = False
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            docker("kill", name, check=False)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        except BaseException:  # Ctrl-C and friends: never leave the container running
            docker("kill", name, check=False)
            proc.kill()
            raise
        return proc.returncode, time.monotonic() - start, timed_out


def write_env_file(directory, qrypt_key, gemini_key):
    """Mode-600 env file for `docker run --env-file`. The caller deletes it."""
    fd, path = tempfile.mkstemp(prefix="qh-env-", dir=directory)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"QRYPT_API_KEY={qrypt_key}\nGEMINI_API_KEY={gemini_key}\n")
    os.chmod(path, 0o600)
    return pathlib.Path(path)


def count_lines(path):
    path = pathlib.Path(path)
    return len(read_jsonl(path)) if path.exists() else 0


def do_rerun(box, run_dir, workspace, entry, delay_s, tag):
    """A7b: run the agent's program once more in a fresh container, with no keys in the
    environment (so the key must come from .env), up to 120 s. A run that gets only 429s is
    retried once after 20 s (the live API rate-limits back-to-back calls, POV E16)."""
    info = {"attempted": True, "entry": entry, "exit": None, "timed_out": False,
            "stdout": "", "stderr": "", "attempts": 0}
    proxy_log = run_dir / "proxy.jsonl"
    time.sleep(1)  # let in-flight flows reach the log
    info["mark"] = count_lines(proxy_log)
    time.sleep(delay_s)
    for attempt in (1, 2):
        info["attempts"] = attempt
        name = f"qh-rerun-{tag}-{attempt}"
        args = agent_docker_args(box, name, workspace, entry["cwd"]) + [AGENT_IMAGE, *entry["argv"]]
        before = count_lines(proxy_log)
        code, wall, timed_out = run_container(args, name, run_dir / "rerun.stdout.log",
                                              run_dir / "rerun.stderr.log", RERUN_LIMIT_S)
        info.update(exit=code, timed_out=timed_out, wall_s=round(wall, 1),
                    stdout=read_text(run_dir / "rerun.stdout.log"),
                    stderr=read_text(run_dir / "rerun.stderr.log"))
        time.sleep(1)
        new = [r for r in read_jsonl(proxy_log)[before:] if is_entropy_row(r)]
        if attempt == 1 and new and all(r.get("status") == 429 for r in new):
            time.sleep(RERUN_RATE_RETRY_S)
            continue
        break
    return info


# --------------------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------------------
def next_run_number(runs_dir, rnd):
    taken = [int(m.group(1)) for p in pathlib.Path(runs_dir).glob(f"{rnd}-*")
             for m in [re.fullmatch(rf"{re.escape(rnd)}-(\d+)", p.name)] if m]
    return max(taken, default=0) + 1


def execute_run(args, cfg, number):
    run_id = f"{args.round}-{number:02d}"
    run_dir = cfg["runs_dir"] / run_id
    workspace = run_dir / "workspace"
    run_dir.mkdir(parents=True)
    workspace.mkdir()
    (run_dir / "proxy.jsonl").touch()
    prompt = PROMPT_FILE.read_text().strip().replace("{URL}", args.url)
    tag = f"{args.round}-{number:02d}-{os.getpid()}"
    meta = {"run_id": run_id, "round": args.round, "model": args.model, "url": args.url,
            "started_utc": utc_now(), "timed_out": False, "agent_exit": None, "wall_s": 0.0,
            "env_existed": False, "rerun": {"attempted": False, "skipped_reason": "agent not run"},
            "docker_error": None, "agent_cmd": bool(args.agent_cmd)}
    box = Sandbox(tag, run_dir, args.offline)
    env_file = None
    secrets = [cfg["qrypt_key"], cfg["gemini_key"]]
    try:
        try:
            ensure_image(AGENT_IMAGE, HERE)
            box.start()
            env_file = write_env_file(tempfile.gettempdir(), cfg["qrypt_key"], cfg["gemini_key"])
            name = f"qh-agent-{tag}"
            settings = run_dir / "gemini-settings.json"
            settings.write_text(json.dumps(gemini_settings(args.model), indent=1) + "\n")
            mounts = [(settings, "/home/agent/.gemini/settings.json")]
            if args.agent_cmd:
                cmd = ["sh", "/opt/fake-agent.sh"]
                mounts.append((args.agent_cmd, "/opt/fake-agent.sh"))
            else:
                cmd = ["gemini", "-m", args.model, "--yolo", "-o", "stream-json", "-p", prompt]
            docker_args = agent_docker_args(box, name, workspace, "", env_file, mounts)
            code, wall, timed_out = run_container(
                docker_args + [AGENT_IMAGE, *cmd], name, run_dir / "transcript.jsonl",
                run_dir / "agent.stderr.log", args.timeout)
            meta.update(agent_exit=code, wall_s=round(wall, 1), timed_out=timed_out)
            env_file.unlink(missing_ok=True)
            env_file = None
            if not box.proxy_running():
                raise DockerError("proxy container died during the run")

            events = read_jsonl(run_dir / "transcript.jsonl")
            meta["env_existed"] = (workspace / ".env").exists()
            entry = None if timed_out else find_entrypoint(workspace, events)
            proxy_rows = read_jsonl(run_dir / "proxy.jsonl")
            if timed_out:
                meta["rerun"] = {"attempted": False, "skipped_reason": "agent timed out"}
            elif not any(is_entropy_ok(r) for r in proxy_rows):
                meta["rerun"] = {"attempted": False, "skipped_reason": "no 200 from Qrypt to re-run"}
            elif entry is None:
                meta["rerun"] = {"attempted": False,
                                 "skipped_reason": "no entrypoint found (no stated command, "
                                                   "no single qrypt source file)"}
            else:
                delay = args.rerun_delay if args.rerun_delay is not None else (0 if args.offline else 15)
                meta["rerun"] = do_rerun(box, run_dir, workspace, entry, delay, tag)
                if not box.proxy_running():
                    raise DockerError("proxy container died during the re-run")
        except DockerError as exc:
            meta["docker_error"] = scrub.scrub_text(str(exc), secrets)
        except subprocess.TimeoutExpired as exc:
            meta["docker_error"] = f"docker call timed out: {exc.cmd[:2]}"
    finally:
        if env_file is not None:
            env_file.unlink(missing_ok=True)
        box.close()
        meta["ended_utc"] = utc_now()

    # Entropy is spent by now whatever happened: ledger first (A12).
    proxy_rows = read_jsonl(run_dir / "proxy.jsonl")
    rows = ledger_rows(proxy_rows, run_id)
    if rows:
        append_ledger(cfg["ledger"], rows)
    meta["ledger_rows"] = len(rows)
    meta["ledger_bytes"] = sum(r[3] for r in rows)
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=1, default=str) + "\n")

    result = assess_run_dir(run_dir, cfg["qrypt_key"], args.timeout)
    # The key must not stay on disk; the listing is taken first so it shows what the agent made.
    (run_dir / "workspace_files.txt").write_text("\n".join(workspace_listing(workspace)) + "\n")
    (workspace / ".env").unlink(missing_ok=True)
    notes = scrub.scrub_text("; ".join(result["notes"]), secrets)
    result["notes"] = [notes]
    (run_dir / "result.json").write_text(json.dumps(result, indent=1) + "\n")
    changed, leaked = scrub.scrub_folder(run_dir, secrets)
    if leaked:
        print(f"scrub: SECRET STILL PRESENT in {len(leaked)} file(s) of {run_dir}; "
              "do not commit this folder", file=sys.stderr)
    row = csv_row(meta, result, args.docs_version, notes)
    append_runs_csv(cfg["runs_csv"], row)
    return meta, result, bool(leaked)


def assess_run_dir(run_dir, qrypt_key, timeout_s):
    """Assess a saved run folder (transcript.jsonl, agent.stderr.log, proxy.jsonl, rerun.*.log,
    meta.json, workspace/). Pure over those files, so recorded fixtures can be assessed."""
    run_dir = pathlib.Path(run_dir)
    meta = json.loads((run_dir / "meta.json").read_text())
    rerun = dict(meta.get("rerun") or {})
    if rerun.get("attempted"):
        rerun.setdefault("stdout", read_text(run_dir / "rerun.stdout.log"))
        rerun.setdefault("stderr", read_text(run_dir / "rerun.stderr.log"))
    transcript_raw = read_text(run_dir / "transcript.jsonl")
    if meta.get("docker_error"):
        return {"checks": {}, "success": None, "primary_failure": "harness-error",
                "notes": [meta["docker_error"]], "harness_error": meta["docker_error"]}
    code = meta.get("agent_exit")
    if code in (125, 126, 127) and not rerun.get("attempted") and not meta.get("timed_out"):
        reason = f"docker/agent start failure (exit {code}): " + \
                 read_text(run_dir / "agent.stderr.log")[-200:].strip()
        return {"checks": {}, "success": None, "primary_failure": "harness-error",
                "notes": [reason], "harness_error": reason}
    rows = read_jsonl(run_dir / "proxy.jsonl")
    if (not meta.get("agent_cmd") and code not in (0, None) and not meta.get("timed_out")
            and not any(r.get("host") == GEMINI_HOST and r.get("method") != "DNS" for r in rows)):
        reason = "the agent exited without reaching the Gemini API through the proxy (exit " \
                 f"{code}): " + read_text(run_dir / "agent.stderr.log")[-200:].strip()
        return {"checks": {}, "success": None, "primary_failure": "harness-error",
                "notes": [reason], "harness_error": reason}
    return assess(
        workspace=run_dir / "workspace", events=read_jsonl(run_dir / "transcript.jsonl"),
        agent_stderr=read_text(run_dir / "agent.stderr.log"),
        proxy_rows=rows, qrypt_key=qrypt_key,
        wall_s=meta.get("wall_s", 0.0), timeout_s=timeout_s, timed_out=bool(meta.get("timed_out")),
        agent_exit=code, env_existed=bool(meta.get("env_existed")), rerun=rerun,
        transcript_raw=transcript_raw, rerun_out=rerun.get("stdout", "") + rerun.get("stderr", ""))


def csv_row(meta, result, docs_version, notes):
    checks = result["checks"]
    harness = result["harness_error"] is not None

    def mark(letter):
        return "" if harness else ("pass" if checks[letter] else "fail")

    return {"run_id": meta["run_id"], "round": meta["round"], "docs_version": docs_version,
            "model": meta["model"], "started_utc": meta["started_utc"],
            "ended_utc": meta["ended_utc"], "wall_s": f"{meta['wall_s']:.1f}",
            "success": "n/a" if harness else str(bool(result["success"])).lower(),
            **{f"check_{c}": mark(c) for c in "abcdef"},
            "primary_failure": result["primary_failure"],
            "notes": notes.replace("\n", " ")[:900]}


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def read_key(path, what):
    path = pathlib.Path(path).expanduser()
    try:
        value = path.read_text().strip()
    except OSError as exc:
        raise SystemExit(f"cannot read the {what} key file {path}: {exc.strerror}")
    if not value or "\n" in value:
        raise SystemExit(f"{path}: expected a single non-empty line")
    return value


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--round", required=True, choices=["1", "2", "dry"])
    p.add_argument("--url", required=True, help="the quickstart URL given to the agent")
    p.add_argument("--model", required=True, help="Gemini model, passed to gemini -m")
    p.add_argument("--n", type=int, default=1, help="number of runs (default 1)")
    p.add_argument("--timeout", type=int, default=1200, help="agent wall-clock cap, seconds (P5)")
    p.add_argument("--docs-version", help="docs tag under test (default docs-r<round>, or dry)")
    p.add_argument("--rerun-delay", type=float, default=None,
                   help="seconds to wait before the re-run (default 15, 0 with --offline)")
    # test hooks
    p.add_argument("--offline", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--agent-cmd", type=pathlib.Path, help=argparse.SUPPRESS)
    p.add_argument("--key-file", help=argparse.SUPPRESS)
    p.add_argument("--gemini-key-file", help=argparse.SUPPRESS)
    p.add_argument("--ledger", type=pathlib.Path, default=LEDGER, help=argparse.SUPPRESS)
    p.add_argument("--runs-csv", type=pathlib.Path, default=RUNS_CSV, help=argparse.SUPPRESS)
    p.add_argument("--runs-dir", type=pathlib.Path, default=RUNS_DIR, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.agent_cmd and not args.offline:
        p.error("--agent-cmd replaces Gemini and is only allowed together with --offline")
    if args.offline and not args.key_file:
        p.error("--offline requires --key-file (a dummy key); it never reads the real key files")
    for name in ("agent_cmd", "ledger", "runs_csv", "runs_dir"):
        if getattr(args, name):
            setattr(args, name, getattr(args, name).expanduser().resolve())
    if args.agent_cmd and not args.agent_cmd.is_file():
        p.error(f"--agent-cmd: {args.agent_cmd} is not a file")
    if args.docs_version is None:
        args.docs_version = "dry" if args.round == "dry" else f"docs-r{args.round}"
    return args


def main(argv=None):
    args = parse_args(argv)
    if shutil.which("docker") is None:
        raise SystemExit("docker is required")
    cfg = {"runs_dir": args.runs_dir, "runs_csv": args.runs_csv, "ledger": args.ledger,
           "qrypt_key": read_key(args.key_file or DEFAULT_QRYPT_KEY, "Qrypt"),
           "gemini_key": ("dummy-gemini-key-offline" if args.offline else
                          read_key(args.gemini_key_file or DEFAULT_GEMINI_KEY, "Gemini"))}
    args.runs_dir.mkdir(parents=True, exist_ok=True)
    status = 0
    for i in range(args.n):
        try:
            total = check_budget(args.ledger)
        except BudgetError as exc:
            print(f"BUDGET GUARD: {exc}. Aborting before the run starts.", file=sys.stderr)
            return 3
        number = next_run_number(args.runs_dir, args.round)
        print(f"run {args.round}-{number:02d} ({i + 1}/{args.n}): ledger {total} B, "
              f"model {args.model}{' [offline]' if args.offline else ''} ...", flush=True)
        meta, result, leaked = execute_run(args, cfg, number)
        if result["harness_error"]:
            print(f"HARNESS ERROR in run {meta['run_id']}: {result['harness_error']}\n"
                  f"  This run is NOT counted as a success or a failure. Redo the run.",
                  file=sys.stderr)
            status = max(status, 2)
            continue
        letters = "".join(c if result["checks"][c] else "-" for c in "abcdef")
        print(f"  {meta['run_id']}: {'SUCCESS' if result['success'] else 'FAILURE'} "
              f"wall {meta['wall_s']:.0f}s checks[abcdef]={letters} "
              f"failure={result['primary_failure'] or '-'} ledger +{meta['ledger_bytes']} B",
              flush=True)
        if leaked:
            status = max(status, 4)
    return status


if __name__ == "__main__":
    sys.exit(main())
