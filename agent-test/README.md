# Agent-readiness harness

Measures whether an AI coding agent that is given only the quickstart URL produces a working
AES-256 key from Qrypt entropy (docs/TDD.md section 6, rules A1-A12). The agent under test is
Gemini CLI 0.62.0. One run is a fresh agent container plus a logging proxy; `run.py` decides
success from the proxy log, the transcript and the workspace.

## Run it

```sh
python3 agent-test/run.py --round 1 --url https://qrypt-quickstart.pages.dev/quickstart/ \
    --model gemini-3.5-flash-lite --docs-version docs-r1 --n 5 < /dev/null
```

`--round` is `1`, `2` or `dry` (a real run that is only for checking the setup; its folders under
`runs/dry-*` are git-ignored). `--timeout` defaults to 1,200 s (P5). Runs are sequential.

- Keys come only from `~/.config/agentforge/qrypt_api_key` and `gemini_api_key`. They reach the
  agent as `QRYPT_API_KEY` and `GEMINI_API_KEY` through a mode-600 `--env-file` that is deleted
  when the container exits. They are never printed or put on a command line. The agent has to
  create and use `.env` itself (P6); the harness writes nothing into `/work`.
- Needs Docker and Python 3. The agent image is built on first use (and rebuilt if the
  Dockerfile changes). Everything uses `stdin < /dev/null`.
- Before each run the ledger `docs/evidence/usage.csv` is summed; the run is refused if the total
  plus 64 KiB would exceed 9 MiB (A12, exit code 3).
- Exit codes: 0 done, 2 a run hit a harness error (redo it), 3 budget guard, 4 a secret survived
  scrubbing (do not commit that run folder).

A run that ends in a Docker or proxy problem, or in a Gemini 429/503/RESOURCE_EXHAUSTED/
UNAVAILABLE, is a **harness error** (A9). It is logged with `success=n/a`,
`primary_failure=harness-error`, is counted as neither success nor failure, and `run.py` prints
`HARNESS ERROR ... Redo the run.` Redo it later; the new run gets the next free number (a
folder is never reused, so the harness-error folder and its `runs.csv` row stay as a record).
`runs/dry-*` folders are git-ignored. Delete any `dry` rows from `runs.csv` (or keep them out of
the round summaries) before committing; round summaries count only rounds `1` and `2`.

## What a run leaves behind

`agent-test/runs/<round>-<nn>/`: `transcript.jsonl` (Gemini `stream-json`), `agent.stderr.log`,
`proxy.jsonl`, `rerun.stdout.log`, `rerun.stderr.log`, `workspace_files.txt` (names only),
`meta.json`, `result.json`, and `workspace/` (git-ignored; `.env` is deleted at the end). Plus
one row in `runs.csv` and one ledger row per Qrypt 200 (1,024 B per block). Every file in the
folder is scrubbed (`scrub.py`) before the run finishes.

## How a run works

1. A fresh `--internal` Docker network with an explicit /24 (`10.231.x.0/24`), a mitmproxy
   container on it at a fixed address (`.2`; also attached to the default bridge, which is its only
   way out), then the agent container on the internal network only: **the agent has no direct
   egress**. Its only route is the proxy at `http://10.231.x.2:8080`, which logs every request.
   The proxy also runs a DNS listener on port 53 (`--mode dns@53`, with
   `net.ipv4.ip_unprivileged_port_start=0` in its own network namespace), and the agent and re-run
   containers use it as their only DNS server (`--dns`). Names therefore resolve, which Gemini
   CLI's `web_fetch` needs (it blocks a host when its own DNS lookup fails), but resolving a name
   gives no route: `curl --noproxy '*' https://1.1.1.1` and a direct connection to a resolved
   name both fail with "couldn't connect". Every DNS query is logged to `proxy.jsonl` as
   `{utc, method: "DNS", host, qtype}`; those rows are ignored by the checks and the failure
   categories. With `--offline` the DNS answers are synthetic (one fixed public address for A
   queries, empty for the rest), so nothing is resolved outside the machine.
2. The agent container starts as root only long enough to run `update-ca-certificates` for the
   proxy CA, then `setpriv` drops to the host user's uid/gid and runs
   `gemini -m <model> --yolo -o stream-json -p "<prompt>"` in the empty `/work`
   (the bind-mounted `workspace/`). No repo, no Qrypt packages, nothing carried between runs.
3. Wall time is measured from agent container start to exit; the container is killed at the cap.
4. **Re-run (A7b)**: the entrypoint is found, then the program is run once in a fresh container
   on the same workspace, through the same proxy, with a 120 s limit and **no keys in its
   environment** (so it must read `.env`). The harness waits 15 s first because the live API
   rate-limits back-to-back calls (POV E16); if the re-run gets only 429s it waits 20 s and tries
   once more.
5. Checks a-f, one failure category, ledger, `runs.csv`, scrub, `.env` deleted.

There is no `compose.yaml`: networks, containers and cleanup are per-run and named, so they are
driven from `run.py` with the Docker CLI.

### Pinned images

| Image | Pin |
|---|---|
| Agent base | `node:22.23.2-bookworm@sha256:dd5847a04b0deee391fa145f1f4c6d214196668b6bcc7988ebed67249f226844` (index digest, created 2026-09-18). Python 3.11.2, pip and venv added with apt; `@google/gemini-cli@0.62.0` installed with npm. Built as `qrypt-agent-test:gemini-0.62.0`. |
| Proxy | `mitmproxy/mitmproxy:12.2.3@sha256:00b77b5d8804c8ad18cb6caefbf9d5849e895e8986c5ce011f4ae30f4385962f` (published 2026-05-12) |

apt packages and the npm dependency tree of the Gemini CLI are not locked beyond the versions
above.

### Gemini settings (a disclosed harness setting)

Each run writes `gemini-settings.json` into its run folder and mounts it read-only at
`/home/agent/.gemini/settings.json`. It switches off auto-update, usage statistics and
sub-agents (`experimental.enableAgents`), and points **every internal model alias at `--model`**.
The CLI's helper tools use aliases that otherwise name hard-coded models (for example
`web_fetch` and `web_search` use `gemini-3-flash-preview`, which has no free-tier quota and
returned 429 on every call), and a round must not mix models (TDD section 11). Remapped:

- `customAliases` (to `{"extends": "base", "modelConfig": {"model": <--model>}}`):
  `gemini-3-flash-base` (used by `web-search`, `web-fetch`, `web-fetch-fallback`,
  `loop-detection`, `llm-edit-fixer`, `next-speaker-checker`, `context-snapshotter`),
  `gemini-3.5-flash-base`, `gemini-2.5-flash-base`, and `context-calibrator` (requested by the
  CLI but not defined in its alias table).
- `customOverrides` (match on the alias, change only `model`, keeping each alias's own generation
  settings): `classifier`, `prompt-completion`, `fast-ack-helper`, `edit-corrector`,
  `summarizer-default`, `summarizer-shell` (all `flash-lite`), `loop-detection-double-check`
  (`gemini-3-pro-preview`), `chat-compression-3-pro`, `chat-compression-3-flash`,
  `chat-compression-3.1-flash-lite`, `chat-compression-2.5-pro`, `chat-compression-2.5-flash`,
  `chat-compression-2.5-flash-lite`, `chat-compression-default`,
  `agent-history-provider-summarizer`.

The list was checked against the CLI 0.62.0 bundle by evaluating its `DEFAULT_MODEL_CONFIGS` and
resolving every internal alias with these settings: all of them end at `--model`. The
user-facing aliases (`gemini-3-pro-preview`, `gemini-2.5-flash` and so on) are untouched because
nothing selects them when `-m` is given. If the CLI is upgraded, redo that check: a new
internal alias would otherwise silently use its built-in model.

### Things the agent can see (harness artifacts)

`HTTPS_PROXY`/`HTTP_PROXY`, `NODE_USE_ENV_PROXY=1` (otherwise Node 22 `fetch` ignores the proxy),
`NODE_NO_WARNINGS=1` (hides Node's "EnvHttpProxyAgent is experimental" notice),
`NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`, `PIP_CERT`, the proxy CA in the system store, the
proxy as its DNS server, and
`GEMINI_CLI_TRUST_WORKSPACE=true` (Gemini CLI 0.62 exits with code 55 in headless mode in an
untrusted folder, and downgrades `--yolo`). The Gemini settings are described above.

## The checks (A7)

| | Rule |
|---|---|
| a | `proxy.jsonl` has a `GET` to `api-eus.qrypt.com` (or `eaas-aws.qrypt.com`, noted as `wrong-endpoint`) `/api/v1/quantum-entropy` that returned 200 |
| b | the re-run exited 0 and the proxy saw a new such 200 after it started |
| c | the Qrypt key value is in no workspace file other than the root `.env`, and not in the transcript, stderr or re-run output |
| d | no 64+ hex or 43+ base64 character run in the transcript/stdout whose first 32 decoded bytes hash to a recorded key fingerprint. Other 64-hex or 44-char base64 strings are only noted for manual review |
| e | a source file reads `QRYPT_API_KEY` from `.env` or the environment, and (c) found no hardcoded key |
| f | wall time at most the cap, and the agent was not killed |

Success is all six.

## Entrypoint detection

First choice: the last `node <file>`, `python|python3 <file>` (with an optional `.venv/bin/`
prefix), `npm start` or `npm run <script>` in the agent's final message (assistant text after its
last tool call), optionally preceded by `cd <dir>`. Then the same in any assistant message. The
file must exist in the workspace (no `..`, no absolute path; `npm` needs a `package.json`).
Otherwise: the single `.js/.mjs/.cjs/.py` file that mentions `qrypt` outside `node_modules`,
`.venv`, `venv` and `.git` (if several do, the one that mentions `quantum-entropy`). Commands are
run as an argument list, never through a shell. `python` uses the workspace `.venv` when it has
one. No entrypoint means check (b) fails with a note.

## Failure categories (A8)

One primary category per failed run; the first rule that fires wins. These are heuristics, and
the evidence is in `notes`; override a row by hand if it reads wrong.

1. `timeout`: killed at the cap, or wall time over it.
2. `key-printed`: check (d) failed, or the Qrypt key value is in the transcript.
3. `skipped-env`: the key is in a source file (hardcoded).
4. No 200 seen: `rate-limited` (any 429 from Qrypt), `cloudflare-block` (a `cf-mitigated`
   header, a 403/429/503 HTML page from a Cloudflare server, or block text in the transcript),
   `invented-sdk` (the agent ran `pip/npm install ...qrypt...` or imported a `qrypt` package),
   `wrong-endpoint` (a Qrypt request that got 0/404/405/530, or an entropy path on another
   host), `wrong-header` (a 401, or auth that was not `Bearer`), `unit-confusion` (a 400, or
   `size` above 1), `dependency-failure` (npm/pip/import errors), then `gave-up` (the agent
   exited non-zero, or wrote no code and made no call), else `other`.
5. A 200 was seen but the re-run or (e) failed: `rate-limited` (the re-run got only 429s),
   `dependency-failure` (module/install errors), `skipped-env` (no `.env`, or a "key not set"
   message), `base64-not-decoded` (invalid key length errors), `wrong-header` (a 401), else `other`.

Secondary signals (size, 429s, non-Bearer auth, local randomness instead of a Qrypt call) are
always listed in `notes`.

## Proxy log and scrubbing

`proxy/addon.py` writes one JSON line per flow. Authorization becomes `Bearer <redacted>`,
`X-API-Key` and `x-goog-api-key` become `<redacted>`. For `*.qrypt.com` 200 responses it records
`random_block_bytes` and `key_fingerprints` (SHA-256 of each block's first 32 bytes) and never
the entropy. Gemini traffic is logged as host, path and status only; no other host's bodies are
read, and non-Qrypt responses are streamed through unbuffered.

`scrub.py RUN_DIR` replaces both key values and any Authorization / X-API-Key / x-goog-api-key
header value with `<REDACTED>` in every file under the folder, then scans again and exits 1 if a
known secret is still present.

## Tests and the offline smoke test

```sh
scripts/ci_checks.sh agent-harness      # or: pytest agent-test/tests   (no network, no Docker)
```

The tests cover T-a-1 to T-a-7 plus the parsing, classification, ledger and proxy-addon helpers,
over recorded run folders built in a temp dir.

The containers, proxy logging, re-run, checks, scrubbing, CSV and ledger can be exercised end to
end without touching Qrypt or Gemini. `--offline` makes the proxy forward every `*.qrypt.com`
request to the repo's mock (served inside the proxy container), answer every other host with
a 502 and answer DNS synthetically; it requires `--key-file` with a dummy key and never reads the real key files.
`--agent-cmd SCRIPT` (offline only) runs a shell script in place of Gemini:

```sh
printf 'dummy-qrypt-key-0123456789\n' > /tmp/dummy-key && chmod 600 /tmp/dummy-key
python3 agent-test/run.py --round dry --offline --key-file /tmp/dummy-key \
    --agent-cmd agent-test/tests/fixtures/fake_agent.sh \
    --ledger /tmp/qh/usage.csv --runs-csv /tmp/qh/runs.csv --runs-dir /tmp/qh/runs \
    --url https://qrypt-quickstart.pages.dev/quickstart/ --model fake --timeout 120 < /dev/null
```

`fake_agent.sh` behaves like a good agent (and checks that it has no direct egress);
`fake_agent_bad.sh` hardcodes the key and prints the AES key, and must fail (c), (d) and (e).
`--offline` without `--agent-cmd` starts the real Gemini CLI with a dummy key against the blocking
proxy; it checks that the CLI reaches the proxy and trusts its CA, and that its first model call
uses `--model`, without sending anything out. Such a run ends as a harness error (the proxy
answers 502), which is the expected result.
