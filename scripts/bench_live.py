#!/usr/bin/env python3
"""Time the quickstart snippets end to end against the LIVE Qrypt API (docs/TDD.md section 7).

Five runs per language (--runs). Each run copies the snippet into a fresh temp folder, writes
the key to a mode-600 `.env` there, runs it the way the docs do (`node quickstart.mjs`,
`python quickstart.py`), records the wall time from process start to exit, and deletes the
folder. The key is read from --key-file and never printed or put on a command line.

Outputs
  docs/evidence/benchmarks.md  per-run times, p50 and max, the date, Node and Python versions
  docs/evidence/usage.csv      one ledger row per run, appended as soon as the run ends
                               (1,024 bytes unless the snippet printed an HTTP-error or
                               network-error line, see entropy_consumed)

Safety: sleeps --sleep seconds between runs, aborts before starting if the ledger total plus
the planned runs would exceed --budget-mib (9 MiB, leaving 1 MiB under the 10 MB cap), and
stops at once on an HTTP 403 or 429.

--dry-run runs against the local mock instead (no key file read, no ledger, nothing written
unless --out is given), to check the script itself.

Usage:
  scripts/bench_live.py [--key-file ~/.config/agentforge/qrypt_api_key] [--runs 5]
  scripts/bench_live.py --dry-run
"""
import argparse
import csv
import datetime as dt
import os
import pathlib
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
LEDGER = REPO / "docs" / "evidence" / "usage.csv"
OUT = REPO / "docs" / "evidence" / "benchmarks.md"
DEFAULT_KEY_FILE = "~/.config/agentforge/qrypt_api_key"
SUCCESS = "✔ Generated a 256-bit AES key from Qrypt quantum entropy (AES-GCM round trip OK)."
ENTROPY_BYTES = 1024  # size=1 KiB per successful run
TARGET_S = 3.0
RUN_TIMEOUT_S = 60
MIB = 1024 * 1024
LANGS = {
    "node": ("Node.js", "quickstart/node/quickstart.mjs"),
    "python": ("Python", "quickstart/python/quickstart.py"),
}


def ledger_total(path):
    """Sum of the entropy_bytes column; 0 when the ledger does not exist yet."""
    if not path.exists():
        return 0
    with path.open(newline="") as fh:
        total = 0
        for row in csv.DictReader(fh):
            try:
                total += int(row["entropy_bytes"])
            except (KeyError, ValueError):
                raise SystemExit(f"cannot read the ledger {path}: bad row {row}")
        return total


def append_ledger(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as fh:
        writer = csv.writer(fh)
        if new:
            writer.writerow(["utc_time", "probe", "status", "entropy_bytes"])
        writer.writerows(rows)


def child_env(extra):
    """The parent environment without QRYPT_* (the key must come from `.env`, as for a
    developer) and NODE_OPTIONS. Proxy settings are kept: this runs on a real network."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("QRYPT_") and k != "NODE_OPTIONS"}
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


def write_env_file(folder, key):
    fd = os.open(folder / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"QRYPT_API_KEY={key}\n")


def version(cmd):
    done = subprocess.run([*cmd, "--version"], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=20)
    return (done.stdout or done.stderr).strip()


# The snippets' own failure lines (docs/TDD.md section 2). A run that printed one of these
# never received entropy: the server answered with an error, or no answer arrived.
NO_ENTROPY_LINE = re.compile(r"^✘ (Qrypt returned HTTP \d{3}:|Could not reach Qrypt \()",
                             re.MULTILINE)


def entropy_consumed(stderr):
    """Bytes of entropy a run counts against the budget. Any run that did not print an
    HTTP-error or network-error ✘ line before exiting counts as having received one 1,024-byte
    block, even if the snippet then failed (bad body, AES failure, timeout): the conservative
    reading for a budget guard."""
    return 0 if NO_ENTROPY_LINE.search(stderr or "") else ENTROPY_BYTES


def one_run(lang, interpreter, key, api_url, secret):
    """Returns (seconds, ok, http_status, detail, entropy_bytes). `secret` is scrubbed from any
    output."""
    source = REPO / LANGS[lang][1]
    folder = pathlib.Path(tempfile.mkdtemp(prefix=f"qs-bench-{lang}-"))
    try:
        script = folder / source.name
        shutil.copyfile(source, script)
        write_env_file(folder, key)
        extra = {"QRYPT_API_URL": api_url} if api_url else {}
        start = time.perf_counter()
        try:
            done = subprocess.run([interpreter, script.name], cwd=folder, env=child_env(extra),
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  encoding="utf-8", timeout=RUN_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return (time.perf_counter() - start, False, 0, f"timed out after {RUN_TIMEOUT_S} s",
                    entropy_consumed(""))
        seconds = time.perf_counter() - start
    finally:
        shutil.rmtree(folder, ignore_errors=True)  # removes the .env with it
    if done.returncode == 0 and done.stdout.strip() == SUCCESS:
        return seconds, True, 200, "", ENTROPY_BYTES
    detail = (done.stderr.strip().splitlines() or ["no output"])[0].replace(secret, "<redacted>")
    http = re.search(r"HTTP (\d{3})", detail)
    return (seconds, False, int(http.group(1)) if http else 0, detail[:160],
            entropy_consumed(done.stderr))


def start_mock():
    proc = subprocess.Popen([sys.executable, str(REPO / "mock" / "server.py"), "0"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
    match = re.fullmatch(r"READY (\d+)", proc.stdout.readline().strip())
    if not match:
        proc.kill()
        raise SystemExit("mock server did not start")
    return proc, f"http://127.0.0.1:{match.group(1)}/s/ok/api/v1/quantum-entropy?size=1"


def percentile_row(values):
    return (f"{statistics.median(values):.2f}", f"{max(values):.2f}") if values else ("n/a", "n/a")


def render(results, runs, versions, today, dry_run, sleep=1.0):
    """The benchmarks.md text. results: lang -> list of (seconds, ok, status, detail, bytes).
    `sleep` is the spacing between runs, in seconds, exactly as passed to --sleep."""
    langs = list(results)
    lines = ["# Live benchmark: running the quickstart", ""]
    if dry_run:
        lines += ["DRY RUN against the local mock, not the live API. These numbers are not "
                  "evidence.", ""]
    lines += [
        f"Date (UTC): {today}. {', '.join(f'{LANGS[l][0]} {versions[l]}' for l in langs)}.",
        "",
        f"Each run is `{' / '.join(os.path.basename(LANGS[l][1]) for l in langs)}` against "
        f"{'the mock' if dry_run else 'the live `api-eus` endpoint'} with `size=1`, started from a fresh folder holding only the "
        "script and a `.env`. The time is wall-clock, from process start to exit, in seconds, "
        f"including interpreter start-up. {runs} run{'s' if runs != 1 else ''} per language, "
        f"{sleep:g} s apart. This measures "
        "the run step only; the five-minute figure on the page covers the whole path.",
        "",
        "| Run | " + " | ".join(f"{LANGS[l][0]} (s)" for l in langs) + " |",
        "|---|" + "---|" * len(langs),
    ]
    for n in range(runs):
        cells = []
        for lang in langs:
            seconds, ok = results[lang][n][:2] if n < len(results[lang]) else (0, None)
            cells.append("not run" if ok is None else f"{seconds:.2f}" if ok else
                         f"failed ({seconds:.2f})")
        lines.append(f"| {n + 1} | " + " | ".join(cells) + " |")
    good = {l: [r[0] for r in results[l] if r[1]] for l in langs}
    p50 = {l: percentile_row(good[l]) for l in langs}
    lines.append("| **p50** | " + " | ".join(f"**{p50[l][0]}**" for l in langs) + " |")
    lines.append("| **max** | " + " | ".join(f"**{p50[l][1]}**" for l in langs) + " |")
    failed = sum(1 for l in langs for r in results[l] if not r[1])
    slowest = max((max(v) for v in good.values() if v), default=0)
    lines += ["", f"Target: every run under {TARGET_S:.0f} s. "
              + ("Result: not met, " + (f"{failed} failed run(s)." if failed else
                                        f"slowest run {slowest:.2f} s.")
                 if failed or slowest >= TARGET_S else f"Result: met (slowest run {slowest:.2f} s)."),
              ""]
    if not dry_run:
        used = sum(r[4] for l in langs for r in results[l])
        lines += [f"Entropy used: {used // 1024} KiB, "
                  "recorded in `docs/evidence/usage.csv`.", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--key-file", default=DEFAULT_KEY_FILE, help="file holding the raw API key")
    ap.add_argument("--runs", type=int, default=5, help="runs per language (default 5)")
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between runs (default 1)")
    ap.add_argument("--budget-mib", type=float, default=9.0, help="ledger ceiling (default 9)")
    ap.add_argument("--python", default=sys.executable,
                    help="interpreter with requests, cryptography and python-dotenv installed")
    ap.add_argument("--node", default="node", help="the node executable")
    ap.add_argument("--out", type=pathlib.Path, help=f"report path (default {OUT.relative_to(REPO)};"
                    " with --dry-run, none)")
    ap.add_argument("--ledger", type=pathlib.Path, default=LEDGER, help="usage ledger CSV")
    ap.add_argument("--dry-run", action="store_true", help="use the mock; no ledger, no key file")
    args = ap.parse_args(argv)

    planned = args.runs * len(LANGS) * ENTROPY_BYTES
    if not args.dry_run:
        used = ledger_total(args.ledger)
        if used + planned > args.budget_mib * MIB:
            print(f"abort: ledger holds {used} B and this run would add up to {planned} B, "
                  f"over the {args.budget_mib} MiB budget", file=sys.stderr)
            return 2

    interpreters = {"node": args.node, "python": args.python}
    for lang, exe in interpreters.items():
        if not shutil.which(exe):
            print(f"abort: {exe} not found on PATH", file=sys.stderr)
            return 2
    needs = subprocess.run([args.python, "-c", "import requests, cryptography, dotenv"],
                           capture_output=True, stdin=subprocess.DEVNULL)
    if needs.returncode != 0:
        print(f"abort: {args.python} lacks the quickstart's dependencies; run it with --python "
              "from a venv that has pip-installed quickstart/python/requirements.txt",
              file=sys.stderr)
        return 2
    versions = {"node": version([args.node]),
                "python": version([args.python]).replace("Python ", "")}

    mock = None
    if args.dry_run:
        key, (mock, api_url) = "dry-run-test-key-not-real", start_mock()
        print("dry run: using the local mock; nothing is written to the ledger")
    else:
        path = pathlib.Path(args.key_file).expanduser()
        try:
            key = path.read_text().strip()
        except OSError as exc:
            print(f"abort: cannot read the key file: {exc.strerror}", file=sys.stderr)
            return 2
        if not key:
            print("abort: the key file is empty", file=sys.stderr)
            return 2
        api_url = None  # the snippets' own default: the live endpoint

    results = {lang: [] for lang in LANGS}
    aborted = False
    try:
        for n in range(args.runs):
            for lang in LANGS:
                result = one_run(lang, interpreters[lang], key, api_url, key)
                seconds, ok, status, detail, consumed = result
                results[lang].append(result)
                if not args.dry_run:  # the ledger is written per run, before anything else
                    append_ledger(args.ledger, [[
                        dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                        f"bench_live_{lang}", status, consumed]])
                print(f"run {n + 1}/{args.runs} {lang:6} {'ok  ' if ok else 'FAIL'} "
                      f"{seconds:5.2f}s {consumed:4d} B {detail}")
                if not ok and status in (403, 429):
                    print(f"stopping: HTTP {status} (quota or rate limit); not retrying",
                          file=sys.stderr)
                    aborted = True
                    break
                if not (n == args.runs - 1 and lang == list(LANGS)[-1]):
                    time.sleep(args.sleep)
            if aborted:
                break
    finally:
        if mock:
            mock.terminate()

    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    report = render(results, args.runs, versions, today, args.dry_run, args.sleep)
    out = args.out or (None if args.dry_run else OUT)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"wrote {out}")
    else:
        print()
        print(report)
    return 0 if not aborted and all(r[1] for rs in results.values() for r in rs) else 1


if __name__ == "__main__":
    sys.exit(main())
