# Project spec: Qrypt developer quickstart

Status: **approved defaults, ready for the technical design (TDD)**. 2026-10-14.
Inputs: the Gauntlet brief (portal v1 plus attachment `pm_developer_quickstart_onboarding.md`) and [POV.md](POV.md), which holds the evidence and reasoning behind every decision below.

This is our internal build spec. The graded `PRD.md` (Qrypt's onboarding PRD) is one of the deliverables it specifies.

---

## 1. Goal and success

Get a new developer, or their AI coding agent, from "never heard of Qrypt" to a working AES-256 key generated from Qrypt quantum entropy. Prove it with data. Pass the 100-point rubric (70 or more); aim for 90 or more.

| Rubric area | Points | What earns "excellent" here |
|---|---|---|
| Quickstart works, site usable | 25 | Two languages, CI-tested against a mock that copies **live** API behavior, troubleshooting built from observed errors |
| PRD quality | 25 | A funnel with an event spec an engineer can implement without asking questions, plus explicit non-goals and a tradeoff |
| Agent-readiness | 20 | At least 4 of 5 after revision, both rounds logged, failure taxonomy mapped to doc fixes |
| Backlog | 10 | 10 items, RICE-scored, each tied to a logged finding |
| Accuracy and safety | 10 | Claims-to-source table (including live observations), honest security framing, safe key handling |
| Docs, AI disclosure, demo | 10 | Grader quick-start under 5 minutes, AI_USAGE.md, demo of 5 minutes or less |

## 2. Decisions

| # | Decision | Default chosen | Can change if |
|---|---|---|---|
| D1 | API | **`GET https://api-eus.qrypt.com/api/v1/quantum-entropy?size=1`** with `Accept: application/json` and `Authorization: Bearer`. This is what the brief specifies, and it works with our key. | Sean says `eaas-aws` is canonical. That swap touches 3 lines per snippet, the mock server and the docs. |
| D2 | Second API (`eaas-aws`) | Not used in the snippets. Covered in the PRD and backlog as a finding, plus one short site note: "The portal showed you different sample code?" | Same as D1. |
| D3 | Languages | **Node.js ≥ 20.12** with no dependencies (built-in `fetch`, `crypto`, `process.loadEnvFile`). **Python ≥ 3.10** with `requests`, `cryptography` and `python-dotenv` pinned in `requirements.txt`. | n/a |
| D4 | Python HTTP client | `requests`. Cloudflare blocks the stdlib `urllib` User-Agent (POV E13), and `requests` is what developers and agents reach for. | n/a |
| D5 | Key material | Decode `random[0]` and use **bytes 0–31** as the AES-256 key. Discard the remaining 992 bytes and never reuse them. Cite NIST SP 800-133r2 for using RBG output directly. | Sean recommends a KDF. That costs 1–3 lines. |
| D6 | AES-GCM nonce | 12 bytes from the OS CSPRNG, not Qrypt bytes | n/a |
| D7 | Attribution | Each snippet sends `User-Agent: qrypt-quickstart-<lang>/1.0`. This makes "first call from the quickstart" measurable on Qrypt's side with no extra telemetry. | It doesn't fit in 25 lines. Then drop it and record it as a backlog item. |
| D8 | Testability | Optional `QRYPT_API_URL` environment variable (the full URL), defaulting to the brief's URL. It is not mentioned in the quickstart path, only in the README's testing section. Spike B showed the full URL saves a line compared with a base URL. | n/a |
| D9 | Single source of truth | The site embeds the snippet files at build time. CI tests the same files. No code is copied into pages. | n/a |
| D10 | Lines-of-code (LOC) rule | Count non-blank lines that are not comments and not imports (`require`/`import`/`from … import`). `scripts/loc.py` enforces this in CI and fails above 25. | n/a |
| D11 | Hosting and CI | **GitLab only** (labs.gauntletai.com, this repo), with no GitHub. CI is GitLab CI on Andre's project runner `agentforge-ci-runner` (id 222, shared with Cerbo; attached 2026-10-14). Shared runners are off. The static host is open (TDD H1). | n/a |
| D12 | Site stack | **A fork of Qrypt's real docs site** (`QryptInc/QryptInc.github.io`: Hugo 0.154.5, `hugo-theme-learn`, a custom "mine" variant, licensed CC BY-NC-SA 4.0) pinned at `a88e74cf9c`, with a new top-level **Quickstart** section. Only our section needs to work. The look is identical, and the result is a merge-ready branch Qrypt could adopt. To keep it from being mistaken for official docs: remove `static/CNAME` (`docs.qrypt.com`), add `noindex`, add an attribution and license notice, and link to the official docs. **Shell only.** All our changes are scoped to the Quickstart section, with its own lean templates. Their pages stay byte-identical except for the new menu entry, and we don't fix or change anything of theirs. Spike A confirmed this approach: LCP 1.50 s, accessibility 95, performance 100 ([SPIKE_RESULTS.md](SPIKE_RESULTS.md)). | n/a. Confirmed by spike. |
| D13 | Performance metric | **LCP < 1.5 s at p95 over 10 Lighthouse mobile runs**, plus accessibility ≥ 95. Reports are committed. | n/a |
| D14 | Agent under test | **Gemini CLI** by default (it has a free tier). The brief requires *an* agent "with a free path", not Gemini specifically, with the version and model pinned in `AI_USAGE.md`. Claude Code builds the project but is not the test subject. | Andre picks another agent with a free path. |
| D15 | Live budget | 10 MB hard cap. Every live call goes into `docs/evidence/usage.csv`. Projected total is about 150 KiB. | n/a |
| D16 | OS coverage | Linux and macOS shells as primary. PowerShell variants only for the `.env` and run commands. CI covers Linux. | n/a |

## 3. Deliverables and acceptance criteria

### 3.1 Quickstart snippets (`quickstart/node/`, `quickstart/python/`)

Behavior. Each snippet:
1. Loads `.env`. If `QRYPT_API_KEY` is missing or blank, it says how to set it and links to troubleshooting, which explains where to get a key. It exits with **code 1** and makes **no API call**.
2. Sends **one** request for `size=1`. It never loops or retries automatically.
3. On 200, it decodes `random[0]`, takes 32 bytes, encrypts a fixed sample string with AES-256-GCM, decrypts it, and checks the result matches.
4. Prints exactly one success line, for example `✔ Generated a 256-bit AES key from Qrypt quantum entropy and verified an AES-GCM round trip (key not shown).` The success line must contain the stable substring `Generated a 256-bit AES key`, which CI and the agent harness match on. The key is never printed, logged or written to disk.
5. On failure, prints one actionable line and exits with **code 1**. Messages are keyed to *live* behavior (POV, "Live API observations"):

| Status | Snippet message (gist) |
|---|---|
| 400 | The request was malformed. `size` must be 1–512 (KiB). Print the API's `error` text. |
| 401 | The Authorization header was missing or malformed. Check that `QRYPT_API_KEY` has no quotes or spaces. |
| 403 | Key rejected, **or** the monthly quota has been used up. Explain how to tell which, and link to troubleshooting. Covers Cloudflare 403s too. |
| 429 | Over 30 requests in 10 seconds. Wait about 10 seconds. If `Retry-After` is present, show it. |
| 500 / 503 | Qrypt is temporarily unavailable or short on entropy. Try again shortly. |
| other | Print the status code and the troubleshooting URL. |
| network / TLS | Name the likely cause (proxy, TLS inspection, offline) and link to troubleshooting. |

Acceptance:
- [ ] Each snippet is 25 lines or fewer by `scripts/loc.py`.
- [ ] CI runs both snippets against the mock server on every push for these cases: success, missing key, 400, 401, 403, 429, 500, 503, malformed JSON, and connection refused. The brief requires success, 401, 429 and 503. The rest are ours.
- [ ] Every CI case checks the exit code and the message. The success case also checks that no 32-byte value from the mock's entropy appears in output, in hex or base64.
- [ ] Live runtime is under 3 s end to end, 5 runs per language. Results go in `docs/evidence/benchmarks.md`.

### 3.2 Mock server (`mock/`)

- [ ] Copies live behavior, not the docs: 403 `{"error":"Forbidden or entropy quota reached"}` for a wrong key, 401 for a missing or malformed header, 400 with the live body text.
- [ ] Bodies for 429, 500 and 503 are labeled *illustrative* in code and in `CLAIMS.md`, because we haven't observed them live.
- [ ] A scenario is selected per request (for example by a header or a path prefix) so one server process serves the whole CI matrix.

### 3.3 Quickstart site (`site/`)

- [ ] **One path:** get a key, set up `.env`, run, see the success line. Language tabs (an accessible tabs pattern), copy buttons with `aria-label`s, and a time target stated as "about 5 minutes once you have a key", linked to the measurement behind it.
- [ ] **"What this does and doesn't give you" box:** the key material travels over TLS, so for no-key-in-transit use cases, point to the Qrypt SDK. No superlatives and no "quantum-safe key" claims.
- [ ] **"The portal showed you different sample code?" note** that maps `eaas-aws` onto `api-eus`.
- [ ] **Troubleshooting page**, written as symptom → likely cause → fix. It covers:
  - every status code: 400, 401, 403 (both causes), 429 vs 403, 500, 503
  - Cloudflare 1010 (Python `urllib`) and a 530 on a mistyped path
  - common mistakes: size is in KiB, base64 must be decoded, don't loop, don't reuse entropy bytes, don't print or log or commit keys
  - environment issues: wrong runtime version, missing `.env`, proxy and TLS inspection
- [ ] **`llms.txt`** with a plain-Markdown copy of the quickstart, linked from the site. This is a brief stretch goal, but it's cheap and directly helps agent success.
- [ ] Works keyboard-only, readable at 200% zoom, Lighthouse accessibility ≥ 95, LCP < 1.5 s at p95. Reports in `docs/evidence/lighthouse/`.
- [ ] Every documented command runs as written. CI extracts the shell blocks and runs them against the mock.

### 3.4 Agent-readiness test (`agent-test/`, `AGENT_READINESS.md`)

- Harness: a fresh container per run from a pinned image, an empty working directory, no repo access, and no memory carried between runs. The token is provided as a container environment variable. The agent still has to create `.env` and handle it itself. Egress goes through a logging proxy that records every call to `api-eus` with the `Authorization` header redacted. A transcript scrubber runs before anything is committed.
- Prompt (fixed, as the brief specifies): `Generate an AES-256 key using Qrypt quantum entropy in a new project`, plus the quickstart URL.
- **A run counts as a success only if all of these hold:**
  - it runs with one command
  - the proxy shows a real `api-eus` call returning 200
  - the output contains the success line, or an equivalent check that the key works
  - the key is never printed or written
  - the token comes from `.env` and is not hardcoded
  - it finishes within 20 minutes
- Round 1 runs on the first published docs, as they are, with no sandbagging. Then we revise the docs and run round 2. If round 1 is already 5/5, we report that honestly and add a harder variant to surface real failure modes.
- [ ] `agent-test/runs.csv` with run id, round, start and end time, wall-clock time, success, failure category and notes. Both rounds included.
- [ ] Failure taxonomy, with each failure category mapped to the doc change that addressed it.
- [ ] At least 4 of 5 successes in round 2, with a median time of 10 minutes or less for successful runs.

### 3.5 `PRD.md` (graded onboarding PRD, readable in 10 minutes)

- [ ] Problem, personas with jobs-to-be-done (application developer, AI agent, internal stakeholders), scope, out-of-scope, risks, and the explicit tradeoff (no full SDK this quarter, and why).
- [ ] The funnel as an **event spec**. Each stage has an event name, owning system, trigger, properties, join key, target and measurement method:
  - landing → account created → key created → first successful call → first key → return within 7 days
  - "First key" uses a proxy definition: the first 200 response on a quickstart `User-Agent`.
  - "Return" means a successful call on a different UTC day, 1–7 days after the first call.
  - Join keys are account or key IDs inside Qrypt's systems. Site analytics, if any, are cookieless and aggregate-only.
- [ ] One activation definition used consistently, which fixes the brief's mismatch between "signup" and "token created".
- [ ] The business case includes its assumptions and says plainly that support deflection is about $1.8k/year and immaterial, and that activation is the lever.
- [ ] Findings: the two APIs, the error codes that don't match the docs, the Cloudflare `urllib` block, and the dead `/register` link. Each finding links to its evidence.

### 3.6 `BACKLOG.md` plus `backlog.csv`

- [ ] 10 items, each with a RICE score (reach, impact, confidence, effort, score), the evidence behind it (a run id, probe output or claim row), and a sequence.

### 3.7 `CLAIMS.md` (claims-to-source table)

- [ ] Covers every factual claim about Qrypt on the site and in the PRD. Columns: claim, source (a docs URL pinned to repo commit `a88e74cf9c`, the portal with a date, or a live probe with date and script), AI-drafted (yes/no), corrected (yes/no, plus what changed).
- [ ] The count of corrected AI-drafted claims is reported in `AI_USAGE.md`.

### 3.8 Repository and submission

- [ ] `README.md` with a grader quick-start (set a key, run each snippet, see the success line in under 5 minutes), how to build the site, and how to reproduce the agent test.
- [ ] `.env.example` committed, and `.env` in `.gitignore`. Secret scanning runs in CI and as a pre-commit hook.
- [ ] `AI_USAGE.md` listing the tools used and for what, the exact agent-test prompt, the agent's version and model, and the count of corrected claims.
- [ ] A one-line description of 120 characters or fewer.
- [ ] A demo video of 5 minutes or less: a fresh quickstart run, the 401 or 403 troubleshooting path, the PRD funnel, and one agent run (sped up if needed). No token appears on screen.

## 4. Out of scope

A full SDK in any language, the `eaas-aws` API in the snippets, a third language, real analytics wiring (we specify the events but don't instrument Qrypt's portal), and load or rate-limit testing against production.

## 5. Non-blocking questions for Qrypt (via Byron)

These have defaults (above). Answers refine the build but don't gate it: which API is canonical (D1), whether an expired token returns 401 or 403, the format of 429 and 503 responses and `Retry-After`, raw bytes vs KDF (D5), `/trial` vs `/register`, and the key's quota. The full text is in [drafts/email-sean.md](drafts/email-sean.md).

## 6. Still Andre's call

These all have defaults, so none of them blocks work: the deadline (we assume the 2-day timebox), the static host (TDD H1), the agent under test (D14), and whether to share the dual-API finding publicly in #help.
