"""Agent-harness tests (docs/TDD.md section 9, T-a-1..T-a-7, plus the helpers behind them).
They run over recorded fixtures: no network, no Docker, no Gemini, no Qrypt."""
import base64
import csv
import hashlib
import json
import os
import pathlib
import sys

import pytest

from conftest import (AES_B64, AES_HEX, AGENT_TEST, FINGERPRINT, GEMINI_KEY, GOOD_EVENTS,
                      GOOD_PROGRAM, QRYPT_KEY, SUCCESS_LINE, entropy_row, gemini_row, message,
                      tool_result, tool_use)

MIB = 1024 * 1024


def assess(run, folder, **kw):
    return run.assess_run_dir(folder, kw.pop("key", QRYPT_KEY), kw.pop("timeout", 1200))


# ---- T-a-1 (A7a/b): proxy 200 plus a passing re-run -> success ---------------------------
def test_a1_success(run, make_run):
    result = assess(run, make_run())
    assert result["checks"] == dict.fromkeys("abcdef", True)
    assert result["success"] is True
    assert result["primary_failure"] == ""
    assert result["harness_error"] is None


def test_a1_rerun_without_a_fresh_200_fails_b(run, make_run):
    # the program exits 0 but the proxy saw no new request (it printed a canned success line)
    folder = make_run(rerun_proxy=[])
    result = assess(run, folder)
    assert result["checks"]["b"] is False and result["success"] is False
    assert any("no fresh 200" in n for n in result["notes"])


def test_a1_rerun_exit_nonzero_fails_b(run, make_run):
    rerun = {"attempted": True, "exit": 1, "timed_out": False, "stdout": "", "stderr": "boom",
             "entry": {"argv": ["node", "index.mjs"], "cwd": "", "source": "final-message"}}
    result = assess(run, make_run(rerun=rerun))
    assert result["checks"]["b"] is False
    assert result["primary_failure"] == "other"


def test_a1_eaas_aws_counts_but_is_noted(run, make_run):
    folder = make_run(proxy=[entropy_row(host="eaas-aws.qrypt.com")],
                      rerun_proxy=[entropy_row(host="eaas-aws.qrypt.com")])
    result = assess(run, folder)
    assert result["success"] is True
    assert any(n.startswith("wrong-endpoint") for n in result["notes"])


# ---- T-a-2 (A7a): no api-eus 200 (faked entropy) -> failure ------------------------------
def test_a2_faked_entropy_fails_a(run, make_run):
    fake = ('import { randomBytes } from "node:crypto";\n'
            'console.log("%s", randomBytes(32).length); // Qrypt\n' % SUCCESS_LINE)
    rerun = {"attempted": False, "skipped_reason": "no 200 from Qrypt to re-run"}
    folder = make_run(files={"index.mjs": fake}, proxy=[], rerun=rerun)
    result = assess(run, folder)
    assert result["checks"]["a"] is False and result["checks"]["b"] is False
    assert result["success"] is False
    assert result["primary_failure"] == "other"
    assert any("local randomness" in n for n in result["notes"])


def test_a2_no_code_and_no_calls_is_gave_up(run, make_run):
    rerun = {"attempted": False, "skipped_reason": "x"}
    folder = make_run(files={}, proxy=[], rerun=rerun, events=[message("I cannot do this.")])
    assert assess(run, folder)["primary_failure"] == "gave-up"


# ---- T-a-3 (A7c): the key value in main.py -> failure, skipped-env -----------------------
def test_a3_hardcoded_key(run, make_run):
    program = GOOD_PROGRAM.replace("process.env.QRYPT_API_KEY", f'"{QRYPT_KEY}"')
    folder = make_run(files={"main.py": f'KEY = "{QRYPT_KEY}"\n', "index.mjs": program,
                             ".env": f"QRYPT_API_KEY={QRYPT_KEY}\n"})
    result = assess(run, folder)
    assert result["checks"]["c"] is False
    assert result["success"] is False
    assert result["primary_failure"] == "skipped-env"
    assert any("main.py" in n for n in result["notes"])


def test_a3_key_only_in_dot_env_is_fine(run, make_run):
    assert assess(run, make_run())["checks"]["c"] is True


def test_a3_key_in_dot_env_example_is_not_fine(run, make_run):
    folder = make_run(files={"index.mjs": GOOD_PROGRAM, ".env": f"QRYPT_API_KEY={QRYPT_KEY}\n",
                             ".env.example": f"QRYPT_API_KEY={QRYPT_KEY}\n"})
    assert assess(run, folder)["checks"]["c"] is False


def test_a3_key_in_transcript_is_key_printed(run, make_run):
    events = GOOD_EVENTS[:3] + [tool_result(f"QRYPT_API_KEY={QRYPT_KEY}")] + GOOD_EVENTS[4:]
    result = assess(run, make_run(events=events))
    assert result["checks"]["c"] is False
    assert result["primary_failure"] == "key-printed"


# ---- T-a-4 (A7d): a fingerprint-matching string in the transcript -> key-printed ---------
@pytest.mark.parametrize("shown", [AES_HEX, AES_B64, AES_HEX.upper()])
def test_a4_printed_key_matches_fingerprint(run, make_run, shown):
    events = GOOD_EVENTS[:3] + [tool_result(f"key: {shown}\nmore")] + GOOD_EVENTS[4:]
    result = assess(run, make_run(events=events))
    assert result["checks"]["d"] is False
    assert result["success"] is False
    assert result["primary_failure"] == "key-printed"


def test_a4_printed_in_rerun_stdout(run, make_run):
    rerun = {"attempted": True, "exit": 0, "timed_out": False, "stdout": AES_HEX + "\n",
             "stderr": "", "entry": {"argv": ["node", "index.mjs"], "cwd": "", "source": "x"}}
    assert assess(run, make_run(rerun=rerun))["checks"]["d"] is False


def test_a4_whole_block_printed_is_caught(run):
    block = bytes(range(256)) * 4
    text = "random: " + base64.b64encode(block).decode()
    assert run.scan_key_prints([text], {FINGERPRINT})[0] == 1


def test_a4_other_key_like_strings_are_only_flagged(run, make_run):
    other = hashlib.sha256(b"unrelated").hexdigest()  # 64 hex, not a recorded fingerprint
    events = GOOD_EVENTS[:3] + [tool_result(f"sha256: {other}")] + GOOD_EVENTS[4:]
    result = assess(run, make_run(events=events))
    assert result["checks"]["d"] is True and result["success"] is True
    assert any("manual review" in n for n in result["notes"])


def test_a4_json_escapes_do_not_hide_a_key(run, make_run):
    # "\n" directly before the hex digits in the raw JSON line must not defeat the scan
    events = GOOD_EVENTS[:3] + [tool_result("line1\n" + AES_HEX)] + GOOD_EVENTS[4:]
    assert assess(run, make_run(events=events))["checks"]["d"] is False


# ---- T-a-5 (A10): scrub.py removes both keys and header values; fails if one survives ----
def make_dirty(tmp_path):
    folder = tmp_path / "dirty"
    (folder / "workspace").mkdir(parents=True)
    (folder / "transcript.jsonl").write_text(
        json.dumps({"cmd": f'curl -H "Authorization: Bearer {QRYPT_KEY}" -H "x-goog-api-key: '
                           f'AIzaSyFAKE" https://x', "gem": GEMINI_KEY}) + "\n")
    (folder / "workspace" / ".env").write_text(f"QRYPT_API_KEY={QRYPT_KEY}\n")
    (folder / "workspace" / "main.py").write_text(f'headers = {{"X-API-Key": "not-a-real-header-value"}}\n'
                                                   f'k = "{QRYPT_KEY}"\n')
    (folder / "binary.bin").write_bytes(b"\x00\x01" + QRYPT_KEY.encode() + b"\xff")
    return folder


def test_a5_scrub_removes_both_keys_and_header_values(tmp_path):
    import scrub
    folder = make_dirty(tmp_path)
    changed, leaked = scrub.scrub_folder(folder, [QRYPT_KEY, GEMINI_KEY])
    assert leaked == [] and len(changed) == 4
    blob = b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())
    for secret in (QRYPT_KEY, GEMINI_KEY, "AIzaSyFAKE", "not-a-real-header-value"):
        assert secret.encode() not in blob
    text = (folder / "transcript.jsonl").read_text()
    assert "Authorization: Bearer <REDACTED>" in text and "x-goog-api-key: <REDACTED>" in text


def test_a5_scrub_cli_exits_nonzero_when_a_secret_survives(tmp_path, monkeypatch, capsys):
    import scrub
    folder = make_dirty(tmp_path)
    qfile, gfile = tmp_path / "q", tmp_path / "g"
    qfile.write_text(QRYPT_KEY + "\n")
    gfile.write_text(GEMINI_KEY + "\n")
    argv = [str(folder), "--qrypt-key-file", str(qfile), "--gemini-key-file", str(gfile)]
    # a scrub that fails to remove anything must be reported, not hidden
    monkeypatch.setattr(scrub, "scrub_bytes", lambda data, secrets: data)
    assert scrub.main(argv) == 1
    assert "SECRET STILL PRESENT" in capsys.readouterr().err
    monkeypatch.undo()
    assert scrub.main(argv) == 0
    assert QRYPT_KEY not in (folder / "workspace" / "main.py").read_text()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_a5_unwritable_file_with_a_secret_fails(tmp_path):
    import scrub
    folder = tmp_path / "ro"
    folder.mkdir()
    target = folder / "log.txt"
    target.write_text(f"key={QRYPT_KEY}\n")
    target.chmod(0o444)
    _, leaked = scrub.scrub_folder(folder, [QRYPT_KEY])
    assert leaked == [target]


def test_a5_scrub_leaves_json_valid(tmp_path):
    import scrub
    row = {"authorization": None, "auth_scheme": "bearer", "x_api_key": "<redacted>"}
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "proxy.jsonl").write_text(json.dumps(row) + "\n")
    scrub.scrub_folder(folder, [QRYPT_KEY])
    assert json.loads((folder / "proxy.jsonl").read_text().strip())["authorization"] is None


# ---- T-a-6 (A12): the ledger at 9 MiB -> the run aborts before starting ------------------
def write_ledger(path, total):
    path.write_text("utc_time,probe,status,entropy_bytes\n"
                    f"2026-10-14T00:00:00+00:00,seed,200,{total}\n")


def test_a6_budget_guard_math(run, tmp_path):
    ledger = tmp_path / "usage.csv"
    write_ledger(ledger, 9 * MIB)
    with pytest.raises(run.BudgetError):
        run.check_budget(ledger)
    write_ledger(ledger, 9 * MIB - 64 * 1024)   # exactly at the cap: allowed
    assert run.check_budget(ledger) == 9 * MIB - 64 * 1024
    write_ledger(ledger, 9 * MIB - 64 * 1024 + 1)
    with pytest.raises(run.BudgetError):
        run.check_budget(ledger)
    assert run.ledger_total(tmp_path / "missing.csv") == 0


def test_a6_run_aborts_before_starting(run, tmp_path, monkeypatch, capsys):
    ledger = tmp_path / "usage.csv"
    write_ledger(ledger, 9 * MIB)
    key = tmp_path / "key"
    key.write_text("dummy-key-value\n")
    called = []
    monkeypatch.setattr(run, "execute_run", lambda *a, **k: called.append(1))
    monkeypatch.setattr(run.shutil, "which", lambda name: "/usr/bin/docker")
    code = run.main(["--round", "dry", "--offline", "--key-file", str(key), "--ledger",
                     str(ledger), "--runs-csv", str(tmp_path / "r.csv"), "--runs-dir",
                     str(tmp_path / "runs"), "--url", "https://x.test/", "--model", "m", "--n", "3"])
    assert code == 3 and called == []
    assert "BUDGET GUARD" in capsys.readouterr().err
    assert not list((tmp_path / "runs").iterdir())     # no run folder was created


# ---- T-a-7 (A7f): wall time 1,201 s -> timeout -------------------------------------------
def test_a7_over_the_cap_is_timeout(run, make_run):
    result = assess(run, make_run(wall_s=1201.0))
    assert result["checks"]["f"] is False
    assert result["success"] is False
    assert result["primary_failure"] == "timeout"


def test_a7_at_the_cap_is_fine(run, make_run):
    assert assess(run, make_run(wall_s=1200.0))["checks"]["f"] is True


def test_a7_killed_at_the_cap_is_timeout(run, make_run):
    rerun = {"attempted": False, "skipped_reason": "agent timed out"}
    result = assess(run, make_run(wall_s=1200.4, timed_out=True, agent_exit=137, rerun=rerun))
    assert result["primary_failure"] == "timeout"


# ---- failure categories (A8) and harness errors (A9) -------------------------------------
NO_RERUN = {"attempted": False, "skipped_reason": "no 200 from Qrypt to re-run"}


def failing(run, make_run, *, proxy, events=None, files=None, stderr="", agent_exit=0):
    folder = make_run(proxy=proxy, rerun=dict(NO_RERUN), events=events, files=files,
                      stderr=stderr, agent_exit=agent_exit)
    return assess(run, folder)


def test_category_rate_limited(run, make_run):
    result = failing(run, make_run, proxy=[entropy_row(status=429), entropy_row(status=429)])
    assert result["primary_failure"] == "rate-limited"


def test_category_wrong_header(run, make_run):
    assert failing(run, make_run, proxy=[entropy_row(status=401)])["primary_failure"] == "wrong-header"
    other = failing(run, make_run, proxy=[entropy_row(status=403, scheme="x-api-key")])
    assert other["primary_failure"] == "wrong-header"


def test_category_wrong_endpoint(run, make_run):
    rows = [{**entropy_row(status=530), "host": "api-eus.qrypt.com", "path": "/api/v1/entropy"}]
    assert failing(run, make_run, proxy=rows)["primary_failure"] == "wrong-endpoint"
    dns = [{**entropy_row(status=0), "host": "api.qrypt.com"}]
    assert failing(run, make_run, proxy=dns)["primary_failure"] == "wrong-endpoint"


def test_category_unit_confusion(run, make_run):
    r400 = failing(run, make_run, proxy=[entropy_row(status=400, query="size=0")])
    assert r400["primary_failure"] == "unit-confusion"
    big = failing(run, make_run, proxy=[entropy_row(status=403, query="size=32")])
    assert big["primary_failure"] == "unit-confusion"


def test_category_invented_sdk(run, make_run):
    events = [message("Installing."), tool_use("pip install qrypt-sdk"),
              tool_result("ERROR: No matching distribution found for qrypt-sdk")]
    assert failing(run, make_run, proxy=[], events=events)["primary_failure"] == "invented-sdk"


def test_category_dependency_failure(run, make_run):
    events = [tool_use("npm install dotenv"), tool_result("npm error code ERESOLVE")]
    assert failing(run, make_run, proxy=[], events=events)["primary_failure"] == "dependency-failure"


def test_category_cloudflare_block(run, make_run):
    blocked = {"utc": "2026-10-14T20:00:00+00:00", "method": "GET", "host": "x.pages.dev",
               "path": "/quickstart/", "status": 403, "server": "cloudflare",
               "content_type": "text/html", "cf_mitigated": "challenge", "auth_scheme": "none"}
    assert failing(run, make_run, proxy=[blocked])["primary_failure"] == "cloudflare-block"


def test_category_gave_up_on_agent_crash(run, make_run):
    result = failing(run, make_run, proxy=[gemini_row(200)], agent_exit=1)
    assert result["primary_failure"] == "gave-up"


def test_category_skipped_env_when_rerun_has_no_key(run, make_run):
    rerun = {"attempted": True, "exit": 1, "timed_out": False, "stdout": "",
             "stderr": "QRYPT_API_KEY is not set", "entry": {"argv": ["node", "index.mjs"],
                                                              "cwd": "", "source": "x"}}
    result = assess(run, make_run(rerun=rerun, rerun_proxy=[], env_existed=False))
    assert result["primary_failure"] == "skipped-env"


def test_category_base64_not_decoded(run, make_run):
    rerun = {"attempted": True, "exit": 1, "timed_out": False, "stdout": "",
             "stderr": "RangeError: Invalid key length", "entry": {"argv": ["node", "index.mjs"],
                                                                    "cwd": "", "source": "x"}}
    assert assess(run, make_run(rerun=rerun, rerun_proxy=[]))["primary_failure"] == "base64-not-decoded"


def test_category_rate_limited_on_rerun(run, make_run):
    rerun = {"attempted": True, "exit": 1, "timed_out": False, "stdout": "", "stderr": "HTTP 429",
             "entry": {"argv": ["node", "index.mjs"], "cwd": "", "source": "x"}}
    result = assess(run, make_run(rerun=rerun, rerun_proxy=[entropy_row(status=429)]))
    assert result["primary_failure"] == "rate-limited"


def test_exactly_one_known_category_for_every_failure(run, make_run):
    result = failing(run, make_run, proxy=[entropy_row(status=429)])
    assert result["primary_failure"] in run.CATEGORIES


def test_gemini_429_is_a_harness_error(run, make_run):
    events = [{"type": "init"}, {"type": "error", "severity": "error",
                                 "message": "429 RESOURCE_EXHAUSTED: quota"},
              {"type": "result", "status": "error", "error": {"message": "RESOURCE_EXHAUSTED"}}]
    folder = make_run(events=events, proxy=[gemini_row(429)], rerun=dict(NO_RERUN), agent_exit=1)
    result = assess(run, folder)
    assert result["harness_error"] and result["success"] is None
    assert result["primary_failure"] == "harness-error"


def test_gemini_503_while_timing_out_is_a_harness_error(run, make_run):
    folder = make_run(events=[{"type": "init"}], proxy=[gemini_row(503)] * 3, timed_out=True,
                      wall_s=1200.3, agent_exit=137, rerun=dict(NO_RERUN))
    assert assess(run, folder)["harness_error"]


def test_qrypt_429_in_tool_output_is_not_a_harness_error(run, make_run):
    events = [tool_use("node index.mjs"), tool_result("Qrypt returned HTTP 429"),
              {"type": "result", "status": "success"}]
    folder = make_run(events=events, proxy=[entropy_row(status=429)], rerun=dict(NO_RERUN))
    result = assess(run, folder)
    assert result["harness_error"] is None and result["primary_failure"] == "rate-limited"


def test_docker_error_is_a_harness_error(run, make_run):
    folder = make_run(meta={"docker_error": "proxy did not start"})
    result = assess(run, folder)
    assert result["harness_error"] == "proxy did not start"


def test_agent_that_never_reached_gemini_is_a_harness_error(run, make_run):
    folder = make_run(events=[], proxy=[], agent_exit=55, rerun=dict(NO_RERUN))
    assert assess(run, folder)["harness_error"]


def test_csv_row_for_harness_error_is_not_counted(run, make_run):
    folder = make_run(meta={"docker_error": "boom"})
    meta = json.loads((folder / "meta.json").read_text())
    row = run.csv_row(meta, assess(run, folder), "docs-r1", "boom")
    assert row["success"] == "n/a" and row["primary_failure"] == "harness-error"
    assert row["check_a"] == ""


# ---- entrypoint detection (A7b) ----------------------------------------------------------
def events_ending(text):
    return [message("working"), tool_use("ls"), tool_result("x"), message(text)]


def test_entry_from_final_message_node(run, make_run):
    ws = make_run(files={"gen.mjs": "// qrypt", "other.js": "// qrypt"}) / "workspace"
    entry = run.find_entrypoint(ws, events_ending("Run it: `node gen.mjs`"))
    assert entry["argv"] == ["node", "gen.mjs"] and entry["source"] == "final-message"


def test_entry_cd_prefix_and_subdir(run, make_run):
    ws = make_run(files={"proj/app.js": "//"}) / "workspace"
    entry = run.find_entrypoint(ws, events_ending("```\ncd proj && node app.js\n```"))
    assert entry["argv"] == ["node", "app.js"] and entry["cwd"] == "proj"


def test_entry_python_uses_workspace_venv(run, make_run):
    ws = make_run(files={"main.py": "# qrypt"}) / "workspace"
    (ws / ".venv" / "bin").mkdir(parents=True)
    (ws / ".venv" / "bin" / "python").symlink_to("/usr/bin/python3")  # dangling on the host
    entry = run.find_entrypoint(ws, events_ending("source .venv/bin/activate && python main.py"))
    assert entry["argv"] == ["/work/.venv/bin/python", "main.py"]
    plain = make_run(files={"main.py": "# qrypt"}) / "workspace"
    assert run.find_entrypoint(plain, events_ending("python3 main.py"))["argv"] == ["python3", "main.py"]


def test_entry_npm_start_needs_package_json(run, make_run):
    ws = make_run(files={"package.json": "{}", "i.js": "//"}) / "workspace"
    assert run.find_entrypoint(ws, events_ending("npm start"))["argv"] == ["npm", "start"]
    bare = make_run(files={"i.js": "// qrypt"}) / "workspace"
    # no package.json: falls back to the single qrypt file
    assert run.find_entrypoint(bare, events_ending("npm start"))["source"] == "single-file"


def test_entry_ignores_commands_for_missing_files_and_path_escapes(run, make_run):
    ws = make_run(files={"a.js": "// qrypt"}) / "workspace"
    entry = run.find_entrypoint(ws, events_ending("node ../../etc/passwd.js; node missing.js"))
    assert entry["source"] == "single-file" and entry["argv"] == ["node", "a.js"]


def test_entry_single_qrypt_file_fallback_and_ambiguity(run, make_run):
    ws = make_run(files={"a.js": "// Qrypt", "b.js": "// nothing", "node_modules/x/y.js": "qrypt"}) \
        / "workspace"
    entry = run.find_entrypoint(ws, [message("done")])
    assert entry["argv"] == ["node", "a.js"] and entry["source"] == "single-file"
    two = make_run(files={"a.js": "// qrypt", "b.py": "# qrypt"}) / "workspace"
    assert run.find_entrypoint(two, [message("done")]) is None
    narrowed = make_run(files={"a.js": "// qrypt", "b.js": "// qrypt quantum-entropy"}) / "workspace"
    assert run.find_entrypoint(narrowed, [message("done")])["argv"] == ["node", "b.js"]


def test_entry_earlier_assistant_message_is_a_second_choice(run, make_run):
    ws = make_run(files={"gen.mjs": "//", "x.js": "// qrypt", "y.js": "// qrypt"}) / "workspace"
    events = [message("Use `node gen.mjs` to run."), tool_use("ls"), tool_result("x"),
              message("All set!")]
    entry = run.find_entrypoint(ws, events)
    assert entry["argv"] == ["node", "gen.mjs"] and entry["source"] == "assistant-text"


def test_final_message_is_text_after_the_last_tool_event(run):
    events = [message("a"), tool_use("x"), message("b"), message("c"), {"type": "result"}]
    assert run.final_message(events) == ("bc", "abc")


# ---- ledger rows and CSV -----------------------------------------------------------------
def test_ledger_rows_use_recorded_block_lengths(run, tmp_path):
    rows = [entropy_row(utc="2026-10-14T20:00:01+00:00"),
            entropy_row(status=429),
            {**entropy_row(utc="2026-10-14T20:00:02+00:00"), "random_block_bytes": [1024, 1024]},
            {"utc": "t", "method": "GET", "host": "docs.qrypt.com", "path": "/", "status": 200},
            gemini_row()]
    out = run.ledger_rows(rows, "1-02")
    assert out == [["2026-10-14T20:00:01+00:00", "agent_test_1-02", 200, 1024],
                   ["2026-10-14T20:00:02+00:00", "agent_test_1-02", 200, 2048]]
    ledger = tmp_path / "usage.csv"
    run.append_ledger(ledger, out)
    assert run.ledger_total(ledger) == 3072
    assert ledger.read_text().startswith("utc_time,probe,status,entropy_bytes\n")


def test_runs_csv_columns_and_values(run, make_run, tmp_path):
    folder = make_run()
    meta = json.loads((folder / "meta.json").read_text())
    row = run.csv_row(meta, assess(run, folder), "docs-r1", "note")
    path = tmp_path / "runs.csv"
    run.append_runs_csv(path, row)
    run.append_runs_csv(path, row)
    rows = list(csv.DictReader(path.open()))
    assert list(rows[0]) == run.CSV_COLUMNS == [
        "run_id", "round", "docs_version", "model", "started_utc", "ended_utc", "wall_s",
        "success", "check_a", "check_b", "check_c", "check_d", "check_e", "check_f",
        "primary_failure", "notes"]
    assert len(rows) == 2 and rows[0]["success"] == "true" and rows[0]["check_f"] == "pass"
    assert (AGENT_TEST / "runs.csv").read_text().splitlines()[0].split(",") == run.CSV_COLUMNS


def test_next_run_number_never_reuses_a_folder(run, tmp_path):
    for name in ("1-01", "1-02", "2-01", "dry-07"):
        (tmp_path / name).mkdir()
    assert run.next_run_number(tmp_path, "1") == 3
    assert run.next_run_number(tmp_path, "dry") == 8
    assert run.next_run_number(tmp_path, "2") == 2


def test_workspace_listing_is_names_only(run, make_run):
    ws = make_run(files={"a.js": "secret body", "src/b.py": "x", "node_modules/p/i.js": "x"}) / "workspace"
    listing = run.workspace_listing(ws)
    assert "a.js" in listing and "src/b.py" in listing
    assert "node_modules/ (contents not listed)" in listing
    assert not any("secret" in line or "i.js" in line for line in listing)


# ---- CLI, prompt, secrets handling -------------------------------------------------------
def test_prompt_is_exact():
    text = (AGENT_TEST / "prompt.txt").read_text()
    assert text == ("Generate an AES-256 key using Qrypt quantum entropy in a new project. "
                    "Use this quickstart: {URL}")


def test_cli_guards(run, tmp_path):
    base = ["--round", "dry", "--url", "https://x.test/", "--model", "m"]
    with pytest.raises(SystemExit):
        run.parse_args(base + ["--offline"])                       # needs --key-file
    with pytest.raises(SystemExit):
        run.parse_args(base + ["--agent-cmd", str(AGENT_TEST / "run.py"),
                               "--key-file", "k"])                 # --agent-cmd needs --offline
    args = run.parse_args(["--round", "1", "--url", "u", "--model", "m"])
    assert args.docs_version == "docs-r1" and args.timeout == 1200 and args.n == 1
    assert run.parse_args(base).docs_version == "dry"


def test_env_file_is_mode_600(run, tmp_path):
    path = run.write_env_file(tmp_path, QRYPT_KEY, GEMINI_KEY)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert path.read_text() == f"QRYPT_API_KEY={QRYPT_KEY}\nGEMINI_API_KEY={GEMINI_KEY}\n"


def test_agent_docker_args_carry_no_key_values(run, tmp_path):
    box = object.__new__(run.Sandbox)
    box.net, box.proxy, box.conf, box.ip = "net", "proxy", tmp_path, "10.231.7.2"
    env_file = run.write_env_file(tmp_path, QRYPT_KEY, GEMINI_KEY)
    args = run.agent_docker_args(box, "name", tmp_path, "", env_file)
    joined = " ".join(args)
    assert QRYPT_KEY not in joined and GEMINI_KEY not in joined
    assert "--env-file" in args and "--cap-drop" in args
    assert "HTTPS_PROXY=http://10.231.7.2:8080" in args
    assert args[args.index("--dns") + 1] == "10.231.7.2"     # the proxy is the only DNS server
    assert any(a.startswith("NODE_EXTRA_CA_CERTS=") for a in args)
    assert any(a.startswith("REQUESTS_CA_BUNDLE=") for a in args)


# ---- proxy addon (A4) --------------------------------------------------------------------
BLOCK = bytes(range(256)) * 4


def entropy_body(n=1):
    return json.dumps({"random": [base64.b64encode(BLOCK).decode()] * n, "size": n}).encode()


def test_addon_records_lengths_and_fingerprints_never_bytes(addon):
    rec = addon.build_record(
        utc="t", method="GET", host="api-eus.qrypt.com", path="/api/v1/quantum-entropy",
        query="size=2", status=200, headers={"authorization": f"Bearer {QRYPT_KEY}",
                                             "user-agent": "node", "x-api-key": "zzz-secret"},
        resp_headers={"content-type": "application/json"}, body=entropy_body(2))
    assert rec["random_block_bytes"] == [1024, 1024]
    assert rec["key_fingerprints"] == [FINGERPRINT] * 2
    line = json.dumps(rec)
    assert QRYPT_KEY not in line and "zzz-secret" not in line
    assert base64.b64encode(BLOCK).decode() not in line and BLOCK[:32].hex() not in line
    assert rec["authorization"] == "Bearer <redacted>" and rec["x_api_key"] == "<redacted>"
    assert rec["user_agent"] == "node" and rec["auth_scheme"] == "bearer"


def test_addon_reads_entropy_key_too_and_ignores_garbage(addon):
    body = json.dumps({"entropy": [base64.b64encode(b"x" * 64).decode()]}).encode()
    assert addon.summarize_entropy_body(body)["random_block_bytes"] == [64]
    for bad in (b"<html>", b"[]", b'{"random":"nope"}', b'{"random":[1,"!!!"]}'):
        assert addon.summarize_entropy_body(bad) == {}


def test_addon_gemini_traffic_has_no_body_or_query(addon):
    rec = addon.build_record(
        utc="t", method="POST", host="generativelanguage.googleapis.com",
        path="/v1beta/models/m:streamGenerateContent", query="key=AIzaSECRET", status=200,
        headers={"x-goog-api-key": "AIzaSECRET"}, resp_headers={}, body=b'{"random":["AAAA"]}')
    assert "query" not in rec and "random_block_bytes" not in rec
    assert "AIzaSECRET" not in json.dumps(rec) and rec["x_goog_api_key"] == "<redacted>"


def test_addon_only_qrypt_hosts_get_entropy_metadata(addon):
    assert addon.is_qrypt_host("api-eus.qrypt.com") and addon.is_qrypt_host("qrypt.com")
    assert not addon.is_qrypt_host("evilqrypt.com") and not addon.is_qrypt_host("qrypt.com.evil.io")
    rec = addon.build_record(utc="t", method="GET", host="example.com", path="/", query="",
                             status=200, headers={}, resp_headers={}, body=entropy_body())
    assert "random_block_bytes" not in rec


def test_addon_hooks_write_one_line_per_flow(addon, tmp_path, monkeypatch):
    class Obj:
        pass

    def flow(host, path, status, body=b""):
        f = Obj()
        f.metadata = {}
        f.request = Obj()
        f.request.pretty_host, f.request.path, f.request.method = host, path, "GET"
        f.request.headers = {"Authorization": "Bearer abc", "User-Agent": "ua"}
        f.request.timestamp_start = 1760000000.0
        f.response = Obj()
        f.response.status_code, f.response.content = status, body
        f.response.headers = {"Content-Type": "application/json"}
        f.error = None
        return f

    log = tmp_path / "proxy.jsonl"
    monkeypatch.setattr(addon, "LOG_PATH", str(log))
    proxy = addon.HarnessProxy()
    for f in (flow("api-eus.qrypt.com", "/api/v1/quantum-entropy?size=1", 200, entropy_body()),
              flow("generativelanguage.googleapis.com", "/v1beta/x?key=q", 200, b"{}")):
        proxy.request(f)
        proxy.response(f)
    lines = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["host"] for r in lines] == ["api-eus.qrypt.com", "generativelanguage.googleapis.com"]
    assert lines[0]["query"] == "size=1" and lines[0]["path"] == "/api/v1/quantum-entropy"
    assert lines[1]["path"] == "/v1beta/x" and "query" not in lines[1]
    assert lines[0]["utc"].endswith("+00:00")


# ---- DNS rows in proxy.jsonl are not HTTP traffic ----------------------------------------
def test_dns_rows_do_not_affect_checks_or_categories(run, make_run):
    dns = [{"utc": "t", "method": "DNS", "host": "api-eus.qrypt.com", "qtype": "A"},
           {"utc": "t", "method": "DNS", "host": "generativelanguage.googleapis.com",
            "qtype": "AAAA"}]
    # success still succeeds, and the re-run mark (an index into the full list) still lines up
    folder = make_run(proxy=dns + [entropy_row()])
    assert assess(run, folder)["success"] is True
    # a DNS lookup of a Qrypt name is not a "wrong-endpoint" request
    result = failing(run, make_run, proxy=dns + [entropy_row(status=401)])
    assert result["primary_failure"] == "wrong-header"
    # DNS lookups of the Gemini host alone do not count as reaching Gemini
    folder = make_run(events=[], proxy=dns, agent_exit=55, rerun=dict(NO_RERUN))
    assert assess(run, folder)["harness_error"]


def test_addon_dns_record_has_only_host_and_type(addon):
    rec = addon.dns_record("t", "Api-Eus.Qrypt.com.", 28)
    assert rec == {"utc": "t", "method": "DNS", "host": "api-eus.qrypt.com", "qtype": "AAAA"}
    assert addon.qtype_name(65) == "HTTPS" and addon.qtype_name(999) == "999"


# ---- Gemini settings: one model for everything (TDD section 11: never mix models) --------
def test_gemini_settings_remap_every_internal_alias(run):
    model = "gemini-3.5-flash-lite"
    settings = run.gemini_settings(model)
    json.dumps(settings)
    assert settings["general"]["enableAutoUpdate"] is False
    assert settings["privacy"]["usageStatisticsEnabled"] is False
    assert settings["experimental"]["enableAgents"] is False
    aliases = settings["modelConfigs"]["customAliases"]
    for name in ("gemini-3-flash-base", "gemini-3.5-flash-base", "gemini-2.5-flash-base"):
        assert aliases[name] == {"extends": "base", "modelConfig": {"model": model}}
    overrides = settings["modelConfigs"]["customOverrides"]
    matched = {o["match"]["model"] for o in overrides}
    assert {"classifier", "summarizer-default", "loop-detection-double-check",
            "chat-compression-default", "edit-corrector"} <= matched
    assert all(o["modelConfig"] == {"model": model} for o in overrides)
    assert len(matched) == len(overrides)
