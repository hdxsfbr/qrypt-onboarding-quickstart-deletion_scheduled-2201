"""T-d-1 (W14): the commands documented in site/content/quickstart/_index.md actually work.

Every `{test="true"}` shell block runs, in page order, in one `sh -e` session inside a clean temp
directory, once for the Node path (every block except those in the Python tab) and once for the
Python path (every block except those in the Node tab). Before running, the built site is served
over local HTTP and `https://qrypt-quickstart.pages.dev/` in the commands is rewritten to that
URL; QRYPT_API_URL points the snippet at the session mock's `ok` scenario. The last command must
print the exact success line and the mock must have seen exactly one request.

The Python path does a real `pip install` into a venv, so it needs a reachable package index
(PIP_INDEX_URL and PIP_CACHE_DIR pass through). Windows blocks (`{test="pwsh"}`) are not run
here: `scripts/ci_checks.sh docs-pwsh` runs them.

Also unit tests for scripts/extract_commands.py against tests/fixtures/qs_page.md.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import sitebuild  # noqa: E402
from conftest import _ancestor_dotenv, sanitized_env  # noqa: E402

pytestmark = pytest.mark.docs

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import extract_commands  # noqa: E402

PAGE = REPO / "site" / "content" / "quickstart" / "_index.md"
TROUBLESHOOTING = REPO / "site" / "content" / "quickstart" / "troubleshooting.md"
FIXTURE_PAGE = Path(__file__).parent / "fixtures" / "qs_page.md"
LIVE_BASE = "https://qrypt-quickstart.pages.dev/"
SUCCESS = "✔ Generated a 256-bit AES key from Qrypt quantum entropy (AES-GCM round trip OK)."
USER_AGENT = {"node": "qrypt-quickstart-node/1.0", "python": "qrypt-quickstart-python/1.0"}
RUN_TIMEOUT_S = 900  # the Python path installs packages from the network
MARK = "::qs-block::"


# --- selecting the blocks of one language path -------------------------------------------


def blocks_for_path(blocks, path):
    """Blocks outside any tab, plus those in this path's tab. A tab that is neither Node.js
    nor Python is an error: the path would be ambiguous."""
    other = "python" if path == "node" else "node"
    chosen = []
    for block in blocks:
        tab = (block["tab"] or "").strip().lower()
        if not tab:
            chosen.append(block)
        elif tab.startswith(("node", "python")):
            if not tab.startswith(other):
                chosen.append(block)
        else:
            pytest.fail(f"{block['file']}:{block['line']}: block sits in tab {block['tab']!r}; "
                        "expected a Node.js or Python tab")
    return chosen


def session_script(blocks):
    """One sh script: a marker line before each block, then the block, so the output of each
    block can be told apart while they still share one shell session."""
    return "".join(f"echo '{MARK}{n}'\n{b['code']}" for n, b in enumerate(blocks))


def clean_env(extra):
    """conftest's sanitizer (no QRYPT_*, proxies or NODE_OPTIONS), also without the test
    runner's own Python environment, so the documented commands start from a bare shell."""
    env = sanitized_env(extra)
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(name, None)
    venv = env.pop("VIRTUAL_ENV", None)
    if venv:
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                      if p != os.path.join(venv, "bin"))
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env


# --- T-d-1 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["node", "python"])
def test_t_d_1_documented_commands_run(path, mock, tmp_path_factory):
    """T-d-1 (W14): the documented {test="true"} commands, in order, print the success line."""
    if not PAGE.is_file():
        pytest.fail(f"{PAGE.relative_to(REPO)} does not exist yet (agent E's deliverable): "
                    "there are no documented commands to run")
    blocks = extract_commands.extract_files([PAGE], "sh")
    if not blocks:
        pytest.fail(f'{PAGE.relative_to(REPO)} has no {{test="true"}} shell blocks')
    chosen = blocks_for_path(blocks, path)
    assert chosen, f"no blocks left for the {path} path"
    for block in chosen:
        assert "qrypt.com" not in block["code"], \
            f"line {block['line']}: a tested command must not reach a live Qrypt host"
    runtime = "node" if path == "node" else "python3"
    if not shutil.which(runtime):
        pytest.fail(f"{runtime} is not on PATH")

    script = session_script(chosen).replace(LIVE_BASE, sitebuild.server_url())
    work = tmp_path_factory.mktemp(f"docs-{path}")
    if _ancestor_dotenv(work):
        pytest.fail(f"{_ancestor_dotenv(work)} exists above the temp folder and python-dotenv "
                    "would load it; move it away so the test is isolated")
    script_file = tmp_path_factory.mktemp(f"docs-{path}-script") / "session.sh"
    script_file.write_text(script, encoding="utf-8")

    env = clean_env({"QRYPT_API_URL": mock.url("ok"), "HOME": str(work)}
                    | {k: v for k, v in os.environ.items() if k in ("PIP_INDEX_URL", "PIP_CACHE_DIR")})
    try:
        done = subprocess.run(["sh", "-e", str(script_file)], cwd=work, env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                              timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"the {path} path timed out after {RUN_TIMEOUT_S} s; "
                    f"stdout tail: {(exc.stdout or '')[-500:]!r}")

    out_blocks = done.stdout.split(MARK)
    where = out_blocks[-1].split("\n", 1)[0] if len(out_blocks) > 1 else "?"
    if done.returncode != 0:
        line = chosen[int(where)]["line"] if where.isdigit() else "?"
        pytest.fail(f"{path} path: the session exited {done.returncode} in the block at page "
                    f"line {line}.\nstdout tail:\n{done.stdout[-1500:]}\n"
                    f"stderr tail:\n{done.stderr[-1500:]}", pytrace=False)
    lines = [ln for ln in out_blocks[-1].splitlines()[1:] if ln.strip()]
    if not lines or lines[-1] != SUCCESS:
        pytest.fail(f"{path} path: the final command printed {lines[-3:]!r}, expected "
                    f"exactly the success line {SUCCESS!r}", pytrace=False)
    assert done.stdout.count(SUCCESS) == 1, "the success line must be printed exactly once"
    (request,) = mock.requests()  # exactly one request reached the mock
    assert request["path"] == "/s/ok/api/v1/quantum-entropy" and request["status"] == 200
    assert request["headers"]["user-agent"] == USER_AGENT[path]
    assert request["headers"]["authorization"] == "Bearer <redacted>"


# --- every shell-like fence is annotated (F6) -----------------------------------------------

SHELL_LANGS = {"sh", "bash", "shell", "powershell"}
TEST_VALUES = {"true", "pwsh", "no"}


def shell_fence_problems(text):
    """One message per shell-like fence (sh, bash, shell, powershell) whose `test` attribute is
    missing or not one of true, pwsh, no. Fences inside another fence are content."""
    problems, fence = [], None
    for number, line in enumerate(text.splitlines(), 1):
        if fence:
            stripped = line.strip()
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= fence[1]:
                fence = None
            continue
        opened = extract_commands.FENCE_OPEN.match(line)
        if opened and not (opened.group(2)[0] == "`" and "`" in opened.group(3)):
            fence = (opened.group(2)[0], len(opened.group(2)))
            lang, attrs = extract_commands.parse_info(opened.group(3))
            if lang.lower() in SHELL_LANGS and attrs.get("test") not in TEST_VALUES:
                problems.append(f"line {number}: ```{lang} fence needs test=\"true\", "
                                f"test=\"pwsh\" or test=\"no\" (found {attrs.get('test')!r})")
    return problems


@pytest.mark.parametrize("page", [PAGE, TROUBLESHOOTING], ids=lambda p: p.stem)
def test_t_d_2_every_shell_fence_declares_whether_it_is_tested(page):
    """T-d-2 (W14, F6): no shell-like fence on either quickstart page is silently untested.
    Placeholder commands (proxy, CA, diagnostics) say so with test="no"."""
    assert shell_fence_problems(page.read_text(encoding="utf-8")) == []


def test_t_d_2_the_check_catches_unannotated_shell_fences():
    """Negative fixtures: a missing attribute, a wrong value and every shell-like language are
    reported; text, other languages, nested fences and the three valid values are not."""
    page = "\n".join([
        "```sh", "a", "```",                          # 1: missing
        "```bash {linenos=false}", "b", "```",       # 4: some attribute, but not test
        '```shell {test="maybe"}', "c", "```",       # 7: wrong value
        "```powershell", "d", "```",                 # 10: missing
        '```sh {test="true"}', "ok", "```",
        '```powershell {test="pwsh"}', "ok", "```",
        '```sh {test="no"}', "ok", "```",
        "```text", "not a shell", "```",
        "````md", "```sh", "inside another fence", "```", "````",
    ])
    assert [p.split(":")[0] for p in shell_fence_problems(page)] == \
        ["line 1", "line 4", "line 7", "line 10"]


# --- extract_commands.py -----------------------------------------------------------------


@pytest.fixture(scope="module")
def fixture_blocks():
    return extract_commands.extract_files([FIXTURE_PAGE])


def test_extract_finds_only_marked_blocks_in_page_order(fixture_blocks):
    assert [b["kind"] for b in fixture_blocks] == ["sh"] * 9 + ["pwsh"]
    assert [b["index"] for b in fixture_blocks] == list(range(10))
    assert [b["line"] for b in fixture_blocks] == sorted(b["line"] for b in fixture_blocks)
    assert all("not tested" not in b["code"] for b in fixture_blocks)


def test_extract_reports_the_enclosing_tab_and_group(fixture_blocks):
    assert [(b["tab"], b["group"]) for b in fixture_blocks] == [
        (None, None), ("Node.js", "get"), ("Python", "get"), ("Python", "get"), (None, None),
        (None, None), (None, None), ("Node.js", "run"), ("Python", "run"), (None, None)]


def test_extract_handles_fence_variants(fixture_blocks):
    by_line = {b["line"]: b for b in fixture_blocks}
    assert by_line[26]["lang"] == "bash"  # extra attributes after test="true"
    assert by_line[37]["code"] == 'echo "tilde fence, single quotes"\n'
    assert by_line[41]["code"] == "indented by two spaces\n  keeps relative indent\n"
    assert by_line[46]["code"] == "```inner fence is content\n```\n"  # four-backtick fence
    assert fixture_blocks[0]["code"] == \
        'mkdir -p demo && cd demo\necho "QRYPT_API_KEY=paste-your-key-here" > .env\n'


def test_extract_kind_filter_and_cli(capsys):
    assert [b["lang"] for b in extract_commands.extract_files([FIXTURE_PAGE], "pwsh")] == \
        ["powershell"]
    done = subprocess.run([sys.executable, str(REPO / "scripts" / "extract_commands.py"),
                           str(FIXTURE_PAGE), "--kind", "sh"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    data = json.loads(done.stdout)
    assert len(data) == 9 and data[0]["file"] == str(FIXTURE_PAGE)
    assert set(data[0]) == {"file", "index", "kind", "lang", "line", "tab", "group", "code"}


def test_extract_cli_errors_cleanly(tmp_path):
    script = str(REPO / "scripts" / "extract_commands.py")
    missing = subprocess.run([sys.executable, script, str(tmp_path / "nope.md")],
                             capture_output=True, text=True)
    assert missing.returncode == 1 and "Traceback" not in missing.stderr
    broken = tmp_path / "broken.md"
    broken.write_text('```sh {test="true"}\necho never closed\n')
    done = subprocess.run([sys.executable, script, str(broken)], capture_output=True, text=True)
    assert done.returncode == 1 and "unterminated" in done.stderr


def test_extract_ignores_fences_that_merely_mention_test(tmp_path):
    page = tmp_path / "p.md"
    page.write_text('```sh {test="false"}\nx\n```\n\n```sh\ny # {test="true"}\n```\n')
    assert extract_commands.extract_files([page]) == []


def test_blocks_for_path_splits_on_the_tab(fixture_blocks):
    node = blocks_for_path(fixture_blocks, "node")
    python = blocks_for_path(fixture_blocks, "python")
    assert {b["tab"] for b in node} == {None, "Node.js"}
    assert {b["tab"] for b in python} == {None, "Python"}
    assert len(node) + len(python) == len(fixture_blocks) + sum(b["tab"] is None
                                                                for b in fixture_blocks)
    with pytest.raises(pytest.fail.Exception, match="expected a Node.js or Python tab"):
        blocks_for_path([{"tab": "Ruby", "file": "x", "line": 1}], "node")


def test_session_script_marks_each_block(fixture_blocks):
    script = session_script(fixture_blocks[:2])
    assert script.startswith(f"echo '{MARK}0'\nmkdir -p demo") and f"echo '{MARK}1'" in script
    assert re.findall(re.escape(MARK) + r"\d+", script) == [f"{MARK}0", f"{MARK}1"]
