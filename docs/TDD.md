# Technical design: Qrypt developer quickstart

Status: **draft for Andre's sign-off**, 2026-10-14.
Inputs: [PROJECT_SPEC.md](PROJECT_SPEC.md) (what and why), [POV.md](POV.md) (evidence), [SPIKE_RESULTS.md](SPIKE_RESULTS.md) (feasibility).
Format: each component follows the design-first template (purpose, inputs, units, rules, edge cases, time, failure handling, outputs, tests, out of scope). Rules are numbered so tests and reviews can cite them, for example "S3" or "M2".

---

## 0. Decisions that need your sign-off

Everything else in this document follows from the spec or the spikes. These six are product calls:

| # | Decision | Proposed | Why |
|---|---|---|---|
| P1 | Malformed 200 response (not JSON, or no `random`) | **Allow a stack trace.** No handling in the snippets. Covered on the troubleshooting page. | Handling it costs 2–3 lines and pushes Python over 25. The brief requires clean failures only for a missing token, 401, 429, 500 and 503. |
| P2 | Site analytics (stretch goal) | **None on the site.** The PRD specifies the funnel events for Qrypt's systems instead. | Cookieless analytics adds a disclosure, a dependency and Lighthouse cost for little evidence in a 2-day window. The PRD's event spec still earns the "measurable funnel" points. |
| P3 | Activation window | **First successful call within 7 days of account creation**, cohorted by the UTC week the account was created | The brief says "signup → first successful call" without a window. A metric with no window never closes. |
| P4 | "Return within 7 days" | **A successful call on a later UTC calendar day, no more than 168 h after the first successful call** | Rules out a same-session retry counting as a "return". The boundaries are tested in §8 (R1–R4). |
| P5 | Agent run time cap | **20 minutes of wall-clock time**. A run that hits the cap counts as a failure, category `timeout`. | Keeps the test bounded. The brief's median target is 10 minutes. |
| H1 | Where the site is hosted | **Decided 2026-10-14: Cloudflare Pages**, project `qrypt-quickstart`, at https://qrypt-quickstart.pages.dev/. GitLab Pages is disabled on labs.gauntletai.com instance-wide (no Deploy > Pages menu item, `/pages` returns 404, no feature toggle). CI deploys with `wrangler pages deploy`, using masked, protected variables `CLOUDFLARE_API_TOKEN` (account token, Pages Read+Edit only, 90 days) and `CLOUDFLARE_ACCOUNT_ID`. | Free static host with a CDN, built from the repo on push |
| P6 | How the agent gets the key | **The harness writes nothing.** The key is passed as a container environment variable (`QRYPT_API_KEY`), and the agent must create and use `.env` itself. | Keeps "skipped .env" a real, testable failure mode. The prompt stays exactly as the brief specifies. |

Inputs only you can provide (none blocks wave 1):
- **Hosting choice (H1).** It sets the site URL baked into the snippets.
- ~~Runner~~ Done 2026-10-14: `agentforge-ci-runner` (id 222) is attached to this project and still locked.
- **The agent under test** (D14). Gemini CLI is the default, not a requirement.

Create any agent API key yourself and store it under `~/.config/agentforge/`. I won't handle the value.

---

### Decision log (after sign-off)

- 2026-10-14. **Wave 1 review leftovers accepted as is** (Andre). Python treats only HTTP 200 as success (S1). T-s-19 doesn't prove nonce freshness or OS origin. T-s-21 checks the default URL statically rather than intercepting a request.
- 2026-10-14. **Workflow.** `main` is protected: no direct pushes, merges by maintainers only, and only when the pipeline succeeds and all discussions are resolved. Each wave lives on a `wave-N` branch and reaches `main` through a merge request, after Codex pass 2 and Andre's go-ahead. Wave 1 reached `main` by direct pushes before this rule existed.

---

## 1. Architecture

```
            ┌──────────────────────── repo (labs.gauntletai.com GitLab) ──────── ───────────────────────┐
            │                                                                                            │
 quickstart/node/quickstart.mjs ─┐                                                                       │
 quickstart/python/quickstart.py ├──► tests/ (pytest) ──► mock/server.py      CI: GitLab CI (runner 222)  │
 quickstart/python/requirements  ┘          ▲                                  ├ lint + LOC                │
            │                               │                                  ├ snippet matrix vs mock    │
            │ copied at build               │                                  ├ documented-commands test  │
            ▼                               │                                  ├ site build + shell diff   │
 site/ (Hugo project) ── mounts ──► site/upstream (git submodule: QryptInc.github.io @ a88e74cf9c)       │
   ├ content/quickstart/   (ours)                                              └ secrets scan → deploy     │
   ├ layouts/quickstart/, layouts/partials/qs-*  (ours)                              │                    │
   └ static/robots.txt, static/quickstart/  (ours)                                   ▼                    │
                                                                   Cloudflare Pages: https://qrypt-quickstart.pages.dev/  
 agent-test/ (Docker harness + mitmproxy) ──► Gemini CLI ──► deployed site + api-eus.qrypt.com (live)     │
 scripts/ (probe_api, loc, lighthouse_runs, bench_live, extract_commands, build_site) ──► docs/evidence/  │
            └────────────────────────────────────────────────────────────────────────────────────────────┘
```

Repository layout:

```
.env.example                      QRYPT_API_KEY=
.gitignore                        .env, site/public/, site/assets/snippets/, agent-test/runs/*/workspace/
.gitlab-ci.yml                    lint, test, docs-commands, site, secrets, deploy (main only)
quickstart/node/quickstart.mjs
quickstart/python/quickstart.py
quickstart/python/requirements.txt  (exact pins)
mock/server.py                    stdlib-only mock of api-eus
tests/test_snippets.py            contract tests (pytest), both languages × all scenarios
tests/test_docs_commands.py       runs every shell block marked `test` in our pages
tests/test_site.py                built-site checks (anchors, snippet bytes, shell diff, HELP URL)
site/hugo.toml                    their config + our mounts and params
site/upstream/                    submodule, pinned, never edited
site/content/quickstart/          _index.md (quickstart), troubleshooting.md
site/layouts/…                    section-scoped templates and partials (qs-*), shortcodes (qs-*)
site/static/robots.txt
agent-test/                       Dockerfile, compose.yaml, proxy addon, run.py, scrub.py, prompt.txt
agent-test/runs.csv               one row per run, both rounds
scripts/                          committed tools (see §7)
docs/evidence/                    usage.csv, benchmarks.md, lighthouse/, probes/
PRD.md  AGENT_READINESS.md  BACKLOG.md  backlog.csv  CLAIMS.md  AI_USAGE.md  README.md
```

---

## 2. Quickstart snippets (`quickstart/`)

**Purpose:** take a developer from a key in `.env` to a verified AES-256 key in one command. The snippet files are the single source for CI, the site and downloads.

**Inputs:**
- `QRYPT_API_KEY` comes from `.env`. Node reads it from the current working directory with `process.loadEnvFile()`. Python's `load_dotenv()` searches from the script's directory upward. Either way, the docs say: run it from the folder that holds `.env`.
- `QRYPT_API_URL` is an optional full-URL override, used only by tests.
- The HTTP response comes from one GET.

**Units:**
- `size` is a whole number of KiB, fixed at `1`, so the request returns 1,024 bytes.
- The key is 32 bytes (256 bits): bytes `[0, 32)` of the decoded `random[0]`.
- The AES-GCM nonce is 12 bytes from the OS CSPRNG. The tag is 16 bytes, the library default.

**Rules:**
- S1. Exactly one HTTP request per run. No retries, no loops, and no redirects followed: Node uses `redirect: "manual"`, Python `allow_redirects=False`. A 3xx is a failure ("unexpected response"). Python treats only status 200 as success, because `r.ok` would accept 3xx.
- S2. The request is `GET {QRYPT_API_URL or https://api-eus.qrypt.com/api/v1/quantum-entropy?size=1}` with headers `Accept: application/json`, `Authorization: Bearer <key>`, and `User-Agent: qrypt-quickstart-node/1.0` or `qrypt-quickstart-python/1.0`.
- S3. The key value has surrounding whitespace trimmed. An empty or missing key fails before any request (message `no-key`).
- S4. Node only: if `process.loadEnvFile` is missing, fail with `runtime` before anything else.
- S5. A non-2xx status fails with `http`. The hint is chosen in this order: the exact code in {401, 403, 429}, then `>= 500` gives "try again shortly", otherwise "unexpected response".
- S6. A transport error (DNS failure, refused connection, TLS failure, timeout) fails with `network`. The detail is the error code if present (`e.cause?.code ?? e.cause?.message ?? e.name` in Node, `type(e).__name__` in Python). Python uses a 15 s timeout. Node uses the default fetch timeout and the docs say so.
- S7. On 2xx: decode `random[0]` (standard base64), take the first 32 bytes, encrypt the fixed message `Hello from Qrypt quantum entropy` with AES-256-GCM, decrypt it, and require byte equality. A mismatch fails with `round-trip`.
- S8. The key bytes, any encoding of them, and the API key never reach stdout, stderr, logs or disk.
- S9. Success prints exactly one line to stdout and exits with 0: `✔ Generated a 256-bit AES key from Qrypt quantum entropy (AES-GCM round trip OK).`
- S10. Every failure prints to stderr and exits with 1. The format is two lines: `✘ <message>` then `  Fix: <HELP>#<anchor>`.
- S11. `HELP` = `<site base URL>quickstart/troubleshooting/`. It is a constant in each file and must equal what the site actually serves (tested in T-site-4).
- S12. 25 lines or fewer under `scripts/loc.py` and 100 characters or fewer per line. CI fails otherwise.
- S13. Python dependencies are exactly `requests`, `cryptography` and `python-dotenv`, pinned in `requirements.txt`. Node has no dependencies.

Message contract (stderr). The tests assert these exact strings:

| Anchor | Message |
|---|---|
| `no-key` | `QRYPT_API_KEY is not set. Add QRYPT_API_KEY=<your key> to .env.` |
| `runtime` | `Node.js 20.12+ required (found <process.version>).` |
| `network` | `Could not reach Qrypt (<detail>).` |
| `<status>` | `Qrypt returned HTTP <status>: <hint>.` The hints are: 401 `Authorization header missing or malformed`, 403 `key rejected, or monthly entropy quota used up`, 429 `over 30 requests in 10 seconds; wait, then run once`, 5xx `try again shortly`, otherwise `unexpected response`. |
| `round-trip` | `AES-GCM round trip failed.` |

**Edge cases:**
- **Looks wrong but is fine:**
  - The remaining 992 bytes are discarded. That's intentional; the docs explain why we don't reuse them.
  - A quoted key in `.env` works, because both dotenv parsers strip the quotes.
  - A 403 from Cloudflare (HTML body) gets the same 403 message. The troubleshooting page explains both causes.
- **Looks fine but is wrong:**
  - `size` interpreted as bytes.
  - Decoding with base64url instead of standard base64.
  - Taking `random` as the bytes themselves.
  - Reusing entropy bytes for the nonce or for a second key.
  - The standard-library `urllib` user agent (Cloudflare 1010). Not reachable with our clients, but covered in the docs.
- **Accepted gap (P1):** a malformed 2xx body produces a stack trace.

**Time:** n/a for logic. Benchmark: under 3 s end to end against the live API, 5 runs per language. Spike B measured 0.53–0.61 s.

**Failure handling:** every failure is terminal for the run and exits with 1. Nothing is retried (S1).

**Outputs:** the stdout success line, or the two stderr lines. Nothing is written to disk.

**Out of scope:** retries and backoff, the `eaas-aws` API, key storage, more than one key per run, a CLI with flags, and a third language.

---

## 3. Mock server (`mock/server.py`)

**Purpose:** reproduce `api-eus`'s *observed* behavior so CI can test every snippet path without a token.

**Inputs:** an HTTP request. The scenario is selected by path prefix: `/s/<scenario>/api/v1/quantum-entropy`.

**Units:** the same as the live API. One block is 1,024 bytes. The test block is the fixed `bytes(range(256)) * 4`.

**Rules:**
- M1. Stdlib only (`http.server`, `ThreadingHTTPServer`). It binds to `127.0.0.1` on a port passed as an argument, or 0 for any free port, and prints `READY <port>` once listening.
- M2. Auth is checked first, as live: a missing header, or one not starting with `Bearer ` followed by at least one character, returns **401** `{"error":"Missing or malformed Authorization header (expected 'Bearer <token>')"}`.
- M3. A `size` that isn't an integer in 1..512 returns **400** `{"error":"'size' must be an integer between 1 and 512"}`, the live text.
- M4. Scenarios and their responses:
  - `ok` returns 200 `{"random":[<b64 of test block> × size],"size":size}`.
  - `forbidden` returns 403 `{"error":"Forbidden or entropy quota reached"}`, the live text.
  - `unauthorized` returns 401 with the live M2 body even when the header is well formed. The snippets always send a well-formed header, so this is the only way CI reaches their 401 path.
  - `ratelimit` returns 429 with an illustrative body and **no** `Retry-After`. This hasn't been observed live.
  - `error500` and `unavailable` return 500 and 503 with illustrative bodies.
  - `teapot` returns 418, to exercise the "unexpected response" path.
  - `redirect` returns 302 with a `Location` pointing at the `ok` scenario, to prove S1 (no redirect followed).
  - `badjson` returns 200 with an HTML body (for P1 documentation tests only).
- M5. Every request is recorded: method, path, query, headers with `Authorization` replaced by `Bearer <redacted>` or `<missing>`, and the status code. `GET /__requests` returns the records. `DELETE /__requests` clears them.
- M6. Bodies marked illustrative carry a code comment `# illustrative: not observed live`. CLAIMS.md lists them.

**Edge cases:** a valid key on an unknown scenario returns 404 `{"error":"unknown scenario"}`, so test typos fail loudly.

**Failure handling:** a server crash fails the test session, through a fixture that checks the process is alive after each test.

**Outputs:** HTTP responses and the request log.

**Tests:** T-mock-1 to T-mock-4 in §8.

**Out of scope:** real rate limiting, timing simulation, `eaas-aws`.

---

## 4. Quickstart site (`site/`)

**Purpose:** a Quickstart section that sits inside a visually faithful copy of docs.qrypt.com, deployed to the static host (H1). Qrypt's pages are only the shell.

**Inputs:**
- **The upstream shell:** submodule `site/upstream` = `QryptInc/QryptInc.github.io` at `a88e74cf9c`, which brings its own theme submodule `e817f53`.
- **Our overlay:** files under `site/`.
- **Snippet files:** copied from `quickstart/` into `site/assets/snippets/` (gitignored) by `scripts/build_site.py` before Hugo runs.

**Units:** time in ms. LCP target < 1,500 ms at p95 over 10 Lighthouse mobile runs against the **deployed** URL, with an internal goal of p95 ≤ 1,400 ms. Weight in KiB.

**Rules:**
- W1. **Shell-only.**
  - Nothing under `site/upstream/` is edited, ever.
  - Our files never shadow an upstream file path, except the one deliberate addition of the `quickstart` section's own templates.
  - The upstream config is copied into `site/hugo.toml`, with additions marked in a comment block.
- W2. **Mounts.** `site/hugo.toml` mounts `upstream/content`, `upstream/static` (excluding `CNAME`), `upstream/layouts` and `upstream/themes` alongside our own `content`, `static`, `layouts` and `assets`. If Hugo's union mounts misbehave, the fallback is for `build_site.py` to assemble a build directory (upstream copy, then our overlay, then delete `CNAME`). The first task in wave 2 decides which, and both produce the same output.
- W3. **Lean section templates.** Built from the Spike A design:
  - `layouts/quickstart/{list,single}.html` use the `qs-header`, `qs-menu` and `qs-footer` partials.
  - All CSS is inlined in one `<style>`.
  - No theme JS. Ours is a single inline script under 2 KB: tabs, copy buttons, menu toggle.
  - A section-only code-block render hook with `style=github`.
- W4. **Fonts.** Preload the IBM Plex Sans woff2 weights the page uses, with `font-display: swap`. If deployed LCP p95 is over 1,400 ms, cut to 400 and 600 (`font-synthesis: none`) and re-measure.
- W5. **Pages:**
  - `/quickstart/` is the one path: key → `.env` → get the file → install (Python) → run → expected output → next steps.
  - `/quickstart/troubleshooting/` uses symptom → cause → fix.
  - `/llms.txt` and `/quickstart/index.md` are a plain-Markdown output format of the same content.
- W6. **Snippets on the page come only from the copied snippet files**, through the `qs-snippet` shortcode (`resources.Get`, then `transform.Highlight`). No code is pasted into Markdown. The same files are published at `/quickstart/files/quickstart.mjs` and `/quickstart/files/quickstart.py`, with `requirements.txt` and `.env.example`, for the documented `curl -O` commands.
- W7. **Tabs.**
  - WAI-ARIA tabs pattern: `role=tablist/tab/tabpanel`, `aria-selected`, `aria-controls`, roving `tabindex`, and Left/Right/Home/End keys.
  - Without JS, both panels render stacked, each with a heading (progressive enhancement).
  - The chosen tab persists in `localStorage`, wrapped in try/catch.
  - Hidden panels use `hidden`, not color tricks. This fixes Spike A's 1.03:1 contrast failure.
- W8. **Copy buttons** are real `<button>`s with `aria-label="Copy <language> code"`. They copy the raw source (`navigator.clipboard.writeText`) and announce "Copied" in an `aria-live=polite` region. Without the Clipboard API, they select the code instead.
- W9. **Code blocks** wrap long lines at 200% zoom with `white-space: pre-wrap` inside our section only. The highlight contrast must be ≥ 4.5:1 for every token color, measured.
- W10. **The time target** is stated once, near the top: "About 5 minutes once you have a key." It links to the measurement in `docs/evidence/benchmarks.md`.
- W11. **Required content blocks:**
  - "What this does and doesn't give you": the entropy travels over TLS; for keys that never cross the network, see the Qrypt SDK.
  - "The portal showed you different sample code?": maps `eaas-aws` onto `api-eus`.
  - The key-handling warnings: don't print, log or commit keys, and don't reuse entropy bytes.
  - No superlatives.
- W12. **Troubleshooting anchors** exist for every snippet anchor: `no-key`, `runtime`, `network`, `400`, `401`, `403`, `429`, `500`, `503`, `round-trip`, and an `other` index. There are also sections for:
  - Cloudflare 1010 (Python `urllib`)
  - a 530 on a mistyped path
  - KiB vs bytes
  - base64 decoding
  - looping and rate limits
  - a missing `.env`, or running from the wrong folder
  - wrong runtime version
  - corporate proxy and TLS inspection, with fixes per language: `NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`, `HTTPS_PROXY`
  - a malformed response (P1)
- W13. **Not passing as official:**
  - `robots.txt` with `Disallow: /`, and `<meta name="robots" content="noindex">` on our pages.
  - A footer line on our pages: "Unofficial community quickstart. Docs shell © Qrypt, CC BY-NC-SA 4.0. Official docs: docs.qrypt.com". This is end-user information, not developer commentary.
- W14. **Documented commands** are fenced with `{test="true"}`. `tests/test_docs_commands.py` runs them in order in a temp directory, with the site URL pointed at the local build and `QRYPT_API_URL` at the mock.
- W15. **Accessibility:** keyboard-only operation, visible focus, readable at 200% zoom, Lighthouse accessibility ≥ 95. The report is committed.

**Edge cases:**
- **Looks wrong but is fine:**
  - Their pages contain superlatives and use highlight.js. Those are their content (W1). CLAIMS.md covers only our section.
  - Their pages fail Lighthouse. Not our scope.
- **Looks fine but is wrong:**
  - A Quickstart that works locally but whose `HELP` URL points at a different base path. Tested in T-site-4.
  - `CNAME` leaking into the deploy. Tested in T-site-2.

**Time:** n/a.

**Failure handling:** any failing site test blocks deploy. Deploy runs only on `main`, after every job passes.

**Outputs:** `site/public/` deployed to the static host (H1), plus Lighthouse JSON for 10 runs in `docs/evidence/lighthouse/deployed/`.

**Out of scope:** search, dark mode, analytics (P2), editing or restyling their pages, and other languages for the docs.

---

### 4.1 Wave 2 interface contract (between the site agents)

**Build entry point:** `scripts/build_site.py [--out site/public] [--base-url URL] [--upstream-only]`.
- **Hugo:** downloads Hugo 0.154.5 (linux-amd64, SHA-256 checked against the release checksums file) into `.tools/hugo-0.154.5/` unless it's already there. Override with `HUGO=/path/to/hugo`.
- **Snippet copies:** copies `quickstart/node/quickstart.mjs`, `quickstart/python/quickstart.py`, `quickstart/python/requirements.txt` and `.env.example` into both `site/assets/snippets/` (for rendering) and `site/static/quickstart/files/` (for download). Both locations are generated and gitignored.
- **Build:** `hugo --minify`. The default base URL is `https://qrypt-quickstart.pages.dev/`.
- **`--upstream-only`:** builds the unmodified upstream shell (for T-site-3).
- Exits non-zero on any Hugo `ERROR`.

**Shortcodes** (signatures are fixed; the content agent uses only these):

| Shortcode | Renders |
|---|---|
| `{{< qs-snippet lang="node" >}}` / `lang="python"` | The full snippet file from `assets/snippets/`, highlighted (S/W6), with a copy button and a download link to `/quickstart/files/<file>` |
| `{{< qs-tabs id="<group>" >}} … {{< /qs-tabs >}}` containing `{{% qs-tab name="Node.js" %}} … {{% /qs-tab %}}` and `{{% qs-tab name="Python" %}} … {{% /qs-tab %}}` | WAI-ARIA tabs (W7). The tab content is Markdown. All tab groups on a page share the selected language (one `localStorage` key, `qs-lang`). |
| `{{% qs-note kind="info|warning" title="…" %}} … {{% /qs-note %}}` | A callout with a heading, no icon fonts |

Every fenced code block in our section gets a copy button from the section's render hook (W8). Fences may carry `{test="true"}`, which is ignored by rendering and read by `tests/test_docs_commands.py`.

**Pages and anchors** (the content agent owns the text, the template agent owns the chrome):
- `site/content/quickstart/_index.md`: the one path (W5, W10, W11).
- `site/content/quickstart/troubleshooting.md`: every anchor in W12. Anchors are explicit heading IDs, for example `## HTTP 403 {#403}`.
- `/llms.txt` and `/quickstart/index.md`: plain-text and Markdown output formats rendered from both pages, with snippets inlined verbatim.

**Documented commands** (W14):
- Shell blocks marked `{test="true"}` run under `sh` in a clean temp dir. Before running, the test rewrites `https://qrypt-quickstart.pages.dev/` to the local build's URL and sets `QRYPT_API_URL` to the mock.
- PowerShell variants (D16) are limited to creating `.env` and running the snippet. They are marked `{test="pwsh"}` and are run by `scripts/ci_checks.sh docs-pwsh` in the pinned `mcr.microsoft.com/powershell` image, outside the default hook, checking only the `.env` they produce.

**CI:**
- A new `site` job (stage `test`) runs `scripts/ci_checks.sh site`, which builds the site, then runs `pytest -m site` and `pytest -m docs`.
- The `deploy` job uses the `site` job's `site/public` artifact instead of the placeholder.
- `scripts/ci_checks.sh local` includes `site`.

**Claims tracking (E2):** every factual statement about Qrypt in our pages is recorded as it's written, in `docs/claims_wave2.csv` (`page, claim, source_type, source, ai_drafted, corrected`). Allowed sources:
- docs.qrypt.com at commit `a88e74cf9c`
- the portal, with an observation date
- live probes recorded in `docs/POV.md` and `docs/evidence/`

## 5. CI and deploy (`.gitlab-ci.yml`)

**Purpose:** prove on every push that the snippets, docs and site meet the contract, then deploy from `main`.

**Rules:**
- C1. Jobs:
  - **`lint`:** `scripts/loc.py --max 25`, a line-length check (100 or fewer), `node --check`, `python -m py_compile`.
  - **`snippets`:** matrix of Node {20.12.x, 22.x, 24.x} × Python {3.10, 3.13}, running `pytest tests/test_snippets.py`.
  - **`docs-commands`:** runs `tests/test_docs_commands.py`.
  - **`site`:** Hugo 0.154.5 (download plus SHA-256 check, the same as the spike), `scripts/build_site.py`, then `pytest tests/test_site.py`, then upload the Pages artifact.
  - **`secrets`:** gitleaks, pinned version.
  - **`deploy`:** only on `main`, needs all the jobs above, publishes `site/public/` to the host chosen in H1. Options:
    - GitLab Pages `pages` job, if Pages can be made public.
    - `railway up` with a masked `RAILWAY_TOKEN`, the same pattern as Cerbo.
    - Cloudflare Pages direct upload with a masked token.

    The job is skipped, not failed, when its token is absent.
- C2. The submodule is checked out with `submodules: recursive`. Its commit is asserted to equal `a88e74cf9c` (T-site-1).
- C3. CI never sees a real Qrypt key. Tests use `QRYPT_API_KEY=test-key-not-real`.
- C4. Job images are pinned by exact tag (for example `python:3.13-slim`, `node:22-bookworm`). Hugo and gitleaks are pinned by exact version and checksum. Jobs run on the project runner (Docker executor). The Python × Node matrix uses `parallel:matrix`.

**Failure handling:** any job failure blocks deploy. There are no allowed failures.

**Out of scope:** Lighthouse in CI (too flaky under shared runners; run with `scripts/lighthouse_runs.py` against the deployed URL and commit the results) and live-API tests in CI.

---

## 6. Agent-readiness harness (`agent-test/`)

**Purpose:** measure, reproducibly, whether an AI coding agent given only our quickstart URL produces a working AES-256 key from Qrypt entropy, and log why it fails when it doesn't.

**Inputs:**
- The fixed prompt, with the deployed URL appended.
- `QRYPT_API_KEY` and `GEMINI_API_KEY`, as container environment variables (P6). They are read from Andre's local key files at launch and never written to disk in the repo.
- A pinned agent: Gemini CLI at an exact version and model, recorded in `AI_USAGE.md`.

**Units:** wall-clock seconds, UTC ISO-8601 timestamps, entropy in bytes for the usage ledger.

**Rules:**
- A1. **One run** is a fresh pair of containers: `agent` and `proxy`. The run directory `agent-test/runs/<round>-<n>/` is created empty. The agent's working directory is an empty, run-specific `workspace/`. There is no repo access and no state carried between runs: a new container from a pinned image digest every time.
- A2. **The agent image** is Debian bookworm with Node 22, Python 3.11 plus pip and venv, git, curl and the Gemini CLI (pinned). Nothing Qrypt-specific is preinstalled.
- A3. **The proxy** is mitmproxy (pinned image). The agent container trusts its CA through the system store, `NODE_EXTRA_CA_CERTS` and `REQUESTS_CA_BUNDLE`. These variables are visible to the agent and noted as a harness artifact.
- A4. **The proxy addon** writes `proxy.jsonl`: time, method, host, path, status, request `User-Agent`, `Authorization` reduced to `Bearer <redacted>`. Only for `*.qrypt.com` responses does it also record the decoded length of each `random[]` block and a SHA-256 of each block's first 32 bytes, never the bytes themselves.
- A5. **The prompt** is exactly: `Generate an AES-256 key using Qrypt quantum entropy in a new project. Use this quickstart: <URL>`. The brief's sentence, plus the URL, which the brief allows.
- A6. **Invocation:** `gemini -p "<prompt>" --yolo`, or whatever the pinned version's non-interactive auto-approve flag is (verified in wave 3), run from the workspace. The run ends when the agent process exits or at 1,200 s (P5).
- A7. **Success requires all of these.** Each is evaluated by `run.py` and recorded as its own column:
  - (a) `proxy.jsonl` has ≥ 1 `GET api-eus.qrypt.com/api/v1/quantum-entropy` returning 200.
  - (b) After the agent exits, the harness runs the agent's program once more inside the same container, using the command the agent reported or the single obvious entrypoint. That must exit with 0 and print a line asserting a working key, and the proxy must see a fresh 200.
  - (c) The `QRYPT_API_KEY` value appears in no workspace file except `.env`, and nowhere in the transcript.
  - (d) No 64-hex-character or 44-character base64 string in stdout or the transcript matches the SHA-256 fingerprints from A4. Any other such string is flagged for manual review.
  - (e) The workspace reads the key from `.env` or the environment. It isn't hardcoded; see (c).
  - (f) The total time is within 1,200 s.
- A8. **Failure categories** (exactly one primary per failed run): `wrong-endpoint`, `wrong-header`, `unit-confusion`, `base64-not-decoded`, `invented-sdk`, `skipped-env`, `key-printed`, `dependency-failure`, `cloudflare-block`, `rate-limited`, `timeout`, `gave-up`, `other`. Secondary categories go in `notes`.
- A9. **Logged per run:** `agent-test/runs.csv` (`run_id, round, docs_version, started_utc, ended_utc, wall_s, success, a..f checks, primary_failure, notes`), the scrubbed transcript, `proxy.jsonl` and a workspace listing (file names only).
- A10. **Scrubbing:** `scrub.py` replaces both key values, and any `Authorization` header value, with `<REDACTED>` in every committed file, and fails if a known secret is still present. gitleaks runs in CI as a backstop.
- A11. **Rounds:**
  - Round 1 runs on the deployed docs at git tag `docs-r1`, as published with no pre-testing edits.
  - Then come doc revisions, each mapped to failure categories in `AGENT_READINESS.md`.
  - Round 2 runs at `docs-r2`. Five runs per round.
  - If round 1 scores 5/5, add a variant round using the `/llms.txt` URL only, so failure modes still surface.
- A12. **Budget guard:** before each run, the harness checks `docs/evidence/usage.csv` and aborts if the projected total would exceed 9 MiB, which leaves a 1 MiB reserve under the 10 MB cap. Every 200 response seen by the proxy is appended to the ledger.

**Edge cases:**
- **Looks wrong but is fine:**
  - The agent fetches several KiB while debugging. That counts toward the budget, not toward failure.
  - The agent uses `eaas-aws` successfully. That's a success for (b), but the primary category is recorded as `wrong-endpoint` in notes, as a doc-clarity signal.
- **Looks fine but is wrong:**
  - The agent prints "success" without ever calling Qrypt (it fakes entropy with `crypto.randomBytes`). Caught by (a) and (b).
  - The agent hardcodes the key it saw in the environment. Caught by (c).

**Time:** wall-clock time from the start of the agent container to the agent's exit. The median for successful runs is computed per round.

**Failure handling:**
- A harness crash, such as a proxy or Docker error, is recorded as `harness-error`, not counted, and the run is redone.
- An agent crash counts as `gave-up`.
- A Gemini quota error is a harness error. The run is redone later, with a note.

**Outputs:** `runs.csv`, per-run folders, and the round summary in `AGENT_READINESS.md` (success rate, median time, taxonomy table, doc change for each category).

**Out of scope:** more than one agent, statistical significance claims (n = 5 is reported as is), and testing the agent's general coding quality.

---

## 7. Evidence and scripts

| Script | Purpose | Live API? |
|---|---|---|
| `scripts/probe_api.py` | Record observed API behavior (exists) | yes, ledgered |
| `scripts/loc.py` | LOC rule S12 (exists) | no |
| `scripts/lighthouse_runs.py` | 10-run Lighthouse with p50 and p95 (exists) | no |
| `scripts/bench_live.py` | 5 timed live runs per language, written to `docs/evidence/benchmarks.md` | yes, about 10 KiB |
| `scripts/build_site.py` | Copy the snippets, then run Hugo build per W2 | no |
| `scripts/extract_commands.py` | List the `{test="true"}` blocks, used by the docs test | no |

**Ledger rule (E1):** every live call made by a script or the harness appends `utc_time, source, status, entropy_bytes` to `docs/evidence/usage.csv`. The budget is 10,240 KiB (10,485,760 bytes). Projected total: about 150 KiB.

**Claims rule (E2):** `CLAIMS.md` has one row per factual statement about Qrypt in our section, `PRD.md` and the README. The columns are: claim, source type (docs at commit, portal on a date, live probe on a date, or our measurement), link, AI-drafted (y/n), corrected (y/n plus what changed). Text drafted by an AI is marked as such the moment it's written.

---

## 8. Funnel metric definitions (used verbatim by PRD.md)

All times are UTC. "Successful call" means an HTTP 200 from the entropy API for any key belonging to the account.

| Stage | Event | Owner system | Rule |
|---|---|---|---|
| 1 | `quickstart_viewed` | Qrypt web analytics (future) | A page view of `/quickstart/`, aggregate count only |
| 2 | `account_created` | Portal | Trial or contract account record created; t = creation time |
| 3 | `key_created` | Portal | API key created with product = Entropy-as-a-Service |
| 4 | `first_successful_call` | API gateway logs | The earliest successful call, t₀ |
| 5 | `first_quickstart_key` (proxy for "first key") | API gateway logs | The earliest successful call whose `User-Agent` starts with `qrypt-quickstart-` |
| 6 | `returned_7d` | API gateway logs | There is a successful call at t with `date(t) > date(t₀)` **and** `t ≤ t₀ + 168 h` (P4) |

Activation (P3) is the count of accounts with `first_successful_call ≤ account_created + 168 h`, divided by accounts created, cohorted by the ISO week of `account_created`.

---

## 9. Test list

Each test names the rule it covers.

**Snippets** (`tests/test_snippets.py`, both languages unless noted):
- T-s-1 (S9, S7): `ok`. Exit 0, stdout is exactly the success line, stderr is empty.
- T-s-2 (S1, S2): `ok`. The mock recorded exactly 1 request, with `Accept: application/json`, a Bearer token, the right User-Agent and `size=1`.
- T-s-3 (S8): `ok`. Neither the hex nor the base64 of `bytes(range(32))` appears in stdout or stderr, and no new files exist in the working directory.
- T-s-4 (S3): no `.env` file. Exit 1, the `no-key` message, 0 requests.
- T-s-5 (S3): `QRYPT_API_KEY=` (empty). Same as T-s-4.
- T-s-6 (S3): `QRYPT_API_KEY="  test-key  "` (quotes and spaces). Succeeds, and the mock sees the trimmed token.
- T-s-7 (S5): `forbidden`. Exit 1, the exact 403 message, `#403`, 1 request.
- T-s-8 (S5, S1): `ratelimit`. Exit 1, the exact 429 message, exactly 1 request (no retry).
- T-s-9 (S5): `unavailable`. Exit 1, "try again shortly", `#503`.
- T-s-10 (S5): `error500`. Exit 1, "try again shortly", `#500`.
- T-s-11 (S5): `teapot`. Exit 1, "unexpected response", `#418`.
- T-s-12 (S5): `unauthorized`. Exit 1, the exact 401 message, `#401`, 1 request. The brief requires a 401 case in CI.
- T-s-13 (S6): a refused connection (a closed port). Exit 1, `network` message, no traceback.
- T-s-14 (S6): an unresolvable host (`.invalid`). Exit 1, `network` message.
- T-s-15 (S4, Node only): run with `process.loadEnvFile` deleted through `--import` of a stub. Exit 1, `runtime` message.
- T-s-16 (P1): `badjson`. A non-zero exit is asserted, and the stack trace is accepted and documented.
- T-s-18 (S1): `redirect`. Exit 1, `Qrypt returned HTTP 302: unexpected response.`, exactly 1 request.
- T-s-19 (S7): the crypto calls are instrumented (a Python `sitecustomize` wrapper around `AESGCM`, a Node `--import` preload around `createCipheriv`/`createDecipheriv` plus `syncBuiltinESMExports`). The test asserts the AES key's SHA-256 equals that of mock bytes `[0, 32)`, the algorithm is AES-256-GCM, the IV or nonce is 12 bytes, and the plaintext is the fixed message. Only fingerprints are recorded, never key bytes.
- T-s-20 (S7): the instrumentation forces a decrypted-plaintext mismatch. Exit 1, `AES-GCM round trip failed.`, `#round-trip`.
- T-s-21 (S2): with no `QRYPT_API_URL`, the default URL literal in each snippet is exactly `https://api-eus.qrypt.com/api/v1/quantum-entropy?size=1` (a static check, so no live call).
- T-s-17 (S10, S11): every failure message ends with `Fix: <HELP>#<anchor>`, and `HELP` matches the site base URL in `site/hugo.toml`.

**Lint** (`lint` job):
- T-l-1 (S12): `loc.py` reports 25 or fewer for both files.
- T-l-2 (S12): every line is 100 characters or fewer.
- T-l-3 (S13): `requirements.txt` holds exactly 3 packages, each pinned with `==`.

**Mock** (`tests/test_mock.py`):
- T-mock-1 (M2): a missing auth header gives 401, with the exact live body.
- T-mock-2 (M3): `size=0` and `size=513` give 400, with the exact live body.
- T-mock-3 (M5): the request log redacts `Authorization`, and the raw token string is absent from the log.
- T-mock-4 (edge case): an unknown scenario gives 404.

**Site** (`tests/test_site.py`, run against the built `public/`):
- T-site-1 (C2): the submodule commit equals `a88e74cf9c`.
- T-site-2 (W1, W13): there is no `CNAME` in `public/`, `robots.txt` disallows `/`, and our pages carry `noindex` and the footer line.
- T-site-3 (W1): every non-quickstart HTML page equals the upstream-only build after normalizing cache-busting timestamps and the single added menu `<li>`.
- T-site-4 (S11, W12): every anchor used by either snippet exists on `/quickstart/troubleshooting/`.
- T-site-5 (W6): the served files under `/quickstart/files/` are byte-identical to `quickstart/`, and the highlighted page code matches the file contents with tags stripped.
- T-site-6 (W7): the tabs markup has the required roles and attributes. The no-JS render contains both panels.
- T-site-7 (W8): each copy button has an `aria-label`, and a live region exists.
- T-site-8 (W9): every inline token color in our code blocks has contrast ≥ 4.5:1 against the block background (computed).
- T-site-9 (W11): the required content blocks are present. A banned-words check finds none of: unbreakable, perfectly secure, quantum-proof, uncrackable, military-grade, quantum-safe key.
- T-site-10 (W5): `/llms.txt` exists, is `text/plain`, and contains both snippets verbatim.

**Documented commands:**
- T-d-1 (W14): every `{test="true"}` block runs in order in a clean temp directory, for both the Node path and the Python path (venv plus `pip install -r requirements.txt` from the local build), and ends with the success line from the mock.

**Funnel definitions** (no code: these are worked examples printed in PRD.md, so an engineer can check an implementation against them):
- R1 (P4): a call on the same UTC date as t₀, at 23:59:59 → not returned.
- R2 (P4): a call at 00:00:00 on the next UTC date → returned.
- R3 (P4): a call at exactly t₀ + 168 h on a later date → returned.
- R4 (P4): a call at t₀ + 168 h + 1 s → not returned.
- R5 (P3): a first call at exactly `account_created + 168 h` → activated. At + 1 s → not activated.
- R6 (stage 5): a 200 with User-Agent `python-requests/2.32` → not `first_quickstart_key`. A 200 with `qrypt-quickstart-node/1.0` → it is.

**Agent harness** (`agent-test/tests/`, with no live calls, using recorded fixtures):
- T-a-1 (A7a/b): a fixture run with a proxy 200 and a passing re-run → success.
- T-a-2 (A7a): a fixture with no `api-eus` 200 (faked entropy) → failure, `other` or as categorized.
- T-a-3 (A7c): the key value in `main.py` → failure, `skipped-env` (hardcoded).
- T-a-4 (A7d): a transcript containing the 64-hex fingerprint match → failure, `key-printed`.
- T-a-5 (A10): `scrub.py` removes the key values. The check fails if one survives.
- T-a-6 (A12): the ledger at 9 MiB → the run aborts before starting.
- T-a-7 (A7f): wall time 1,201 s → `timeout`.

**Manual, recorded as evidence:**
- **Lighthouse:** 10 mobile runs on the deployed URL. LCP p95 < 1,500 ms, accessibility ≥ 95 (W4, W15).
- **Live benchmark:** 5 runs × 2 languages, each under 3 s.
- **Keyboard and zoom:** a keyboard-only walkthrough and a 200% zoom check. Screenshots saved, token never on screen.

---

## 10. Build plan (waves, for the wave-loop skill)

Each wave uses disjoint file scopes, runs parallel agents, then build and test, then one independent review, then a fix pass, then a commit.

| Wave | Agents (file scope) | Exit criterion |
|---|---|---|
| **1. Core** | **A:** `quickstart/` and `.env.example`, from the spike drafts plus the S6 Node detail fix and wording alignment. **B:** `mock/`, `tests/test_snippets.py`, `tests/test_mock.py`. **C:** `.gitlab-ci.yml` (lint, snippets and secrets jobs), `.gitignore`, `README.md` skeleton. | The lint, mock and snippet tests are green locally and in a GitLab pipeline on runner 222. |
| **2. Site** | **D:** `site/hugo.toml`, the submodule, `scripts/build_site.py`, the lean `layouts/` (decides W2 mounts vs. copy first). **E:** `site/content/quickstart/*`, the `qs-snippet`, tabs and copy shortcodes and JS, `llms.txt` output. **F:** `tests/test_site.py`, `tests/test_docs_commands.py`, `scripts/extract_commands.py`, the deploy job. | Deployed URL live. All site and docs tests green. Lighthouse 10-run evidence committed. Tag `docs-r1`. |
| **3. Agent test** | **G:** `agent-test/` harness plus its tests. Then rounds run sequentially (not parallel, to protect the rate limit and budget). Doc revisions happen in `site/content/quickstart/` only. | Both rounds logged, round 2 ≥ 4/5, `AGENT_READINESS.md` written. Tag `docs-r2`. |
| **4. Write-ups** | **H:** `PRD.md` (uses §8 verbatim). **I:** `BACKLOG.md` and `backlog.csv`. **J:** `CLAIMS.md`, `AI_USAGE.md`, the final `README.md`. | Then the `requirements-audit` skill, `submission-checklist`, and `demo-recording`. |

The live benchmark (`bench_live.py`) runs at the end of wave 2. Lighthouse runs after every deploy that changes our templates.

---

## 11. Risks and fallbacks

| Risk | Signal | Fallback |
|---|---|---|
| Hugo union mounts don't compose the shell cleanly | Wave 2 task D's first build | Build-directory assembly in `build_site.py` (W2) |
| Deployed LCP is over 1.5 s (the host's TTFB differs from localhost) | First deployed Lighthouse run | Two font weights (W4), then trimming unused inlined CSS rules |
| Gemini CLI non-interactive or approval flags differ from the plan | Wave 3 setup | Use the pinned version's documented headless mode. If none works, switch to another agent with a free path, record it in `AI_USAGE.md` and the spec (D14) |
| mitmproxy breaks the agent's TLS in ways a real developer wouldn't hit | The `harness-error` rate in a dry run | CONNECT-only logging (host and bytes) plus the A7(b) re-run as the sole success proof |
| Gemini free-tier capacity. On 2026-10-14 the key was valid, `*-flash` models returned 503 (high demand), `gemini-pro-latest` returned 429 (no free quota), and `gemini-3.5-flash-lite` / `gemini-flash-lite-latest` answered. The 2.5 models are closed to new keys. | A dry run in wave 3 | Pin the model with `-m`. Prefer `gemini-3.5-flash` when it's available, otherwise `gemini-3.5-flash-lite`, and record which in `AI_USAGE.md`. A 503 or 429 mid-run is a `harness-error`, and the run is redone later (§6). Never mix models within a round. |
| Round 1 already scores 5/5 | Round 1 results | The `llms.txt`-only variant round (A11) |
| Quota or 403 midway through | Ledger and A12 | Pause. Ask Byron or Qrypt for more quota. Never switch to another person's key. |
| Qrypt answers via Byron change D1 or D5 | Slack | Swapping APIs touches 3 lines per snippet plus the mock and docs. A KDF costs 1–3 lines. Both are re-tested by the same suite. |
