"""scripts/bench_live.py: how much entropy a run counts for (F1), the report text (R2), and
that the ledger is written per run. Everything runs against the local mock: no live call, and
the real ledger and report are never touched.
"""
import csv
import importlib.util
import sys

import pytest

from conftest import REPO, TEST_KEY

SPEC = importlib.util.spec_from_file_location("bench_live", REPO / "scripts" / "bench_live.py")
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)

INTERPRETER = {"node": "node", "python": sys.executable}
BLOCK = bench.ENTROPY_BYTES


@pytest.mark.mock
@pytest.mark.parametrize("stderr, expected", [
    ("", BLOCK),                                              # no failure line at all
    ("Traceback (most recent call last):\n  KeyError: 'random'\n", BLOCK),   # 200, bad body
    ("✘ AES-GCM round trip failed.\n  Fix: https://x/#round-trip\n", BLOCK),  # 200, then failed
    ("✘ QRYPT_API_KEY is not set. Add it.\n", BLOCK),         # conservative: not an HTTP/network line
    ("✘ Qrypt returned HTTP 429: rate limit reached; wait about 15 seconds, then run once.\n", 0),
    ("✘ Qrypt returned HTTP 503: try again shortly.\n  Fix: x\n", 0),
    ("✘ Could not reach Qrypt (ECONNREFUSED).\n  Fix: x\n", 0),
    ("noise\n✘ Qrypt returned HTTP 403: key rejected.\n", 0),
])
def test_f1_entropy_consumed_rule(stderr, expected):
    """F1: a run counts 1,024 B unless it printed an HTTP-error or network-error line."""
    assert bench.entropy_consumed(stderr) == expected


def test_f1_a_timeout_counts_as_consumed():
    """No output at all before the kill still counts: the request may have been answered."""
    assert bench.entropy_consumed("") == BLOCK


SCENARIOS = [  # (scenario, ok, status, consumed bytes)
    ("ok", True, 200, BLOCK),
    ("badjson", False, 0, BLOCK),       # HTTP 200, then the snippet crashed: entropy was received
    ("forbidden", False, 403, 0),
    ("ratelimit", False, 429, 0),
    ("error500", False, 500, 0),
    ("unavailable", False, 503, 0),
    ("teapot", False, 418, 0),
    ("redirect", False, 302, 0),
    ("unauthorized", False, 401, 0),
]


@pytest.mark.parametrize("scenario, ok, status, consumed", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_f1_one_run_against_the_mock(lang, mock, scenario, ok, status, consumed):
    """F1 (--dry-run path): the computed value comes from the snippet's own output."""
    seconds, got_ok, got_status, detail, got = bench.one_run(
        lang, INTERPRETER[lang], TEST_KEY, mock.url(scenario), TEST_KEY)
    assert (got_ok, got_status, got) == (ok, status, consumed), detail
    (req,) = mock.requests()
    assert req["status"] == (200 if scenario in ("ok", "badjson") else status)


def test_f1_unreachable_host_consumes_nothing(lang, closed_port):
    url = f"http://127.0.0.1:{closed_port}/s/ok/api/v1/quantum-entropy?size=1"
    _, ok, status, _, consumed = bench.one_run(lang, INTERPRETER[lang], TEST_KEY, url, TEST_KEY)
    assert (ok, status, consumed) == (False, 0, 0)


@pytest.fixture
def one_language(lang, monkeypatch):
    """Restrict the benchmark to the language under test; the other interpreter argument is
    only asked for its version."""
    monkeypatch.setattr(bench, "LANGS", {lang: bench.LANGS[lang]})
    return ["--node", INTERPRETER["node"] if lang == "node" else sys.executable,
            "--python", sys.executable]


def test_r2_dry_run_writes_no_ledger_and_states_the_real_spacing(lang, one_language, tmp_path,
                                                                 capsys):
    """R2 + F1: --dry-run never writes the ledger, and the report says the --sleep spacing."""
    ledger, report = tmp_path / "usage.csv", tmp_path / "benchmarks.md"
    code = bench.main(["--dry-run", "--runs", "2", "--sleep", "0.25", "--ledger", str(ledger),
                       "--out", str(report), *one_language])
    assert code == 0, capsys.readouterr()
    assert not ledger.exists()
    text = report.read_text(encoding="utf-8")
    assert "2 runs per language, 0.25 s apart." in text and "1 s apart" not in text
    assert "Entropy used" not in text  # dry runs claim nothing about the live budget


@pytest.mark.parametrize("sleep, words", [("15", "15 s apart"), ("1", "1 s apart"),
                                          ("0.5", "0.5 s apart")])
def test_r2_render_uses_the_sleep_argument(sleep, words):
    results = {"python": [(0.5, True, 200, "", BLOCK)]}
    text = bench.render(results, 1, {"python": "3.13"}, "2026-10-14", False, float(sleep))
    assert f"1 run per language, {words}." in text
    assert "Entropy used: 1 KiB" in text


def test_r2_render_counts_entropy_from_the_recorded_bytes():
    """A run that failed after the 200 still counts in the report's entropy line."""
    results = {"python": [(0.5, True, 200, "", BLOCK), (0.4, False, 0, "boom", BLOCK),
                          (0.3, False, 403, "x", 0)]}
    text = bench.render(results, 3, {"python": "3.13"}, "2026-10-14", False, 15)
    assert "Entropy used: 2 KiB" in text


@pytest.mark.mock
def test_f1_ledger_rows_are_written_per_run_as_they_happen(tmp_path, monkeypatch):
    """The ledger row for a run is on disk before the next run starts: a crash after two runs
    leaves two rows. No live call: one_run is replaced."""
    calls = []

    def fake_one_run(*_):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("crash in the third run")
        return (0.5, len(calls) == 1, 200 if len(calls) == 1 else 0, "", BLOCK)

    monkeypatch.setattr(bench, "one_run", fake_one_run)
    monkeypatch.setattr(bench, "LANGS", {"python": bench.LANGS["python"]})
    monkeypatch.setattr(bench.time, "sleep", lambda _: None)
    key_file, ledger = tmp_path / "key", tmp_path / "usage.csv"
    key_file.write_text("not-a-real-key\n")
    with pytest.raises(RuntimeError):
        bench.main(["--key-file", str(key_file), "--ledger", str(ledger), "--runs", "5",
                    "--out", str(tmp_path / "out.md"), "--node", sys.executable,
                    "--python", sys.executable])
    rows = list(csv.DictReader(ledger.open(newline="")))
    assert [(r["probe"], r["status"], r["entropy_bytes"]) for r in rows] == \
        [("bench_live_python", "200", "1024"), ("bench_live_python", "0", "1024")]
