# Qrypt Developer Quickstart: POV, brief review, and open questions

Status: pre-PRD review, 2026-10-13. Purpose: settle every open question before we write the build PRD and the technical design (TDD).

**Decisions now live in [PROJECT_SPEC.md](PROJECT_SPEC.md) (2026-10-14).** This file keeps the evidence and reasoning behind them. No question here blocks the build.

Sources reviewed:

- Gauntlet brief, portal version 1 (last updated 2026-10-12), and its attachment `pm_developer_quickstart_onboarding.md`. The attachment is more detailed and is treated as canonical where the two differ.
- Qrypt company profile on the Gauntlet partner page.
- Qrypt official docs at https://docs.qrypt.com/eaas/, read from the source repo `QryptInc/QryptInc.github.io` at commit `a88e74cf9c` (2026-01-15, "Major docs clean-up"). This is the commit our claims-to-source table should pin.
- Live checks of `portal.qrypt.com` on 2026-10-13. No form was submitted.

---

## 1. Point of view

### What this project actually is

This is a **Product Manager** assessment wearing an engineering costume. The rubric is worth 100 points, and 70 passes:

| Criterion | Points |
|---|---|
| Quickstart works and site is usable | 25 |
| PRD quality (funnel, metrics, scope, tradeoffs) | 25 |
| Agent-readiness (at least 4 of 5, failure taxonomy, doc fixes) | 20 |
| Backlog (evidence-backed, scored, sequenced) | 10 |
| Accuracy and safety of content | 10 |
| Docs, AI disclosure, demo | 10 |

Code is about 25 points, and the code itself is small: two scripts of 25 lines or fewer, a mock server, CI, and a static site. The other 75 points go to judgment: a funnel an engineer can instrument without asking questions, honest measurement, evidence-backed prioritization, and content that is accurate and safe. We should plan our time to match. Engineering is the cheap part. The writing, the agent experiments, and the evidence trail are the expensive part.

### What Qrypt is selling, and why that matters for the copy

Qrypt's core pitch, from the company profile, is "no key in transit". Endpoints independently derive identical symmetric keys from quantum entropy, so nothing needs to be intercepted, which defends against "harvest now, decrypt later" (HNDL). That is their distributed key generation SDK (C++ only, documented under `/sdk`).

**The quickstart we are asked to build does the opposite.** It downloads entropy over HTTPS and uses those bytes directly as the key. In this flow the key material *is* in transit. It is protected only by the TLS session, which today is usually classical key exchange, so it is HNDL-exposed. Qrypt also sees the bytes.

None of this disqualifies the quickstart. It is a legitimate first-run experience for Entropy-as-a-Service. But it creates real accuracy and safety risk (10 points, plus credibility with a security-literate CTO):

- The site must not imply that this flow gives you a "quantum-safe" or "quantum-secure" key, or that there is no key in transit.
- The site must not imply that OS randomness (`/dev/urandom`, `crypto.randomBytes`) is broken. Qrypt's FAQ wording ("completely random and unpredictable according to the laws of physics") is a physics claim we can cite but should not amplify.
- **Recommended framing:** "Your first quantum-sourced AES-256 key in under N minutes." Add a short, honest "What this does and doesn't give you" box that points to the Qrypt SDK for no-key-in-transit use cases. That box is also a natural funnel hand-off to Qrypt's higher-value product, which is a PM point in our favor.

I think this is the single most differentiating insight we can show. Most submissions will gloss over it.

### Where the business case is strong and where it is weak

- **Activation (25% to 50%) is the real lever.** At 400 signups a quarter, that is 100 more activated developers each quarter.
- **Support deflection is worth about $1,800 a year.** The arithmetic checks out (15 × 4 × 0.5 h × $60), but it is immaterial. The PRD should say plainly that deflection is a side benefit, not the justification. Showing we noticed that is a judgment signal.
- **Time-to-first-key appears three times with three different populations and clocks:** humans under 10 minutes from landing, agents at a median of 10 minutes or less for successful runs, and graders under 5 minutes. The PRD needs one canonical definition per population.

---

## 2. What the brief gets wrong (verified)

| # | Brief says | Reality (evidence) | Impact / fix |
|---|---|---|---|
| E1 | Get a token at `portal.qrypt.com/register` | **Returns 404** (checked live 2026-10-13). The login page links "Create trial account" to `portal.qrypt.com/trial` (full name and work email). | The "get a token" step is broken as written. The site must use `/trial`. Confirm with Qrypt that a trial account includes an **Entropy** token. |
| E2 | Implies self-registration is the normal path | Official docs say "contact us to obtain one" in two places and never mention self-signup. | The docs and the portal disagree. Our claims table must cite the portal (screenshot, date) for `/trial`. This is a backlog item for Qrypt. |
| E3 | Portal summary says "**derives** a 32-byte AES-256 key". The attachment says "**takes** 32 bytes". | These are different operations: raw slice versus KDF. Qrypt's own docs ("Key Entropy Size") budget **40 bytes of entropy per 32-byte key** for their SDK, which suggests they condition entropy before using it as a key. | Decision needed (Q4). |
| E4 | Headers: `Accept: application/json` plus `Authorization` | The docs' request table lists both, but **the docs' own curl and Python examples omit `Accept`**. The JS example has a syntax bug (`{ kib_entropy }`), and the JSON response example is missing its outer braces. | We always send both headers. This is evidence for the backlog ("official examples are inconsistent and agents copy them") and a likely source of agent failures. |
| E5 | "Rate limit: more than 30 requests per 10 seconds" | Correct. The docs specify per access token. The docs do **not** document `Retry-After`, error bodies, or error content-type. | The mock server's error responses are guesses. Decision needed (Q8). |
| E6 | 403 is the "allotment for the current period" | The FAQ makes it specific: a **monthly** quota that resets on the day of the month the account was opened. No numbers are published. | Troubleshooting can say "monthly, resets on your account anniversary day". Trial quota size is unknown, which matters for budget (Q1). |
| E7 | "No official SDK in most languages" | True. There is a C++ SDK, but it does distributed key generation, not entropy fetch. | The PRD tradeoff ("no full SDK this quarter") should name this explicitly. Choosing C++ as a quickstart language would be confusing next to the SDK. |
| E8 | Activation = "signup → first successful API call" | The funnel stages are landing → token created → first call → first key → return within 7 days. "Signup" is not a stage, and the activation denominator changes between sections. | The PRD must pick one denominator. Proposal: account created (trial submitted) is stage 1b, and activation = first successful call ÷ accounts created. |
| E9 | Site load "< 1.5 s p95 on mobile, Lighthouse mobile, 10 runs" | Lighthouse has no "load p95". It reports FCP, LCP, TBT, CLS and Speed Index under simulated throttling. | Proposal: **LCP < 1.5 s at p95 over 10 runs** (with 10 runs, p95 is effectively the worst run). This rules out heavy JS doc frameworks. |
| E10 | Portal section headings | The portal's "OFF-LIMITS TECH" section actually holds the edge cases, and "OTHER REQUIREMENTS" holds the API reference. | Cosmetic, but the attachment is the better source for the requirements audit. |
| E12 | 401 = "token invalid or expired"; 403 = allotment reached (brief **and** official docs) | **Live API disagrees** (2026-10-13). A wrong but well-formed token returns **403** `{"error":"Forbidden or entropy quota reached"}`. **401** only means a missing or malformed header: `{"error":"Missing or malformed Authorization header (expected 'Bearer <token>')"}`, which also covers an empty `QRYPT_API_KEY`. Expired-token behavior is unverified. | This is the most important doc fix. Troubleshooting must be organized around what the developer *sees*. A 403 means "wrong token **or** quota used up", and the page needs a way to tell them apart (did it ever work? check the portal). The mock server must copy the real behavior, not the docs. Ask Sean whether expired tokens return 401 or 403. |
| E15 | n/a (docs site quality) | The published build of docs.qrypt.com (repo `docs/`, Hugo 0.154.5) includes `/livereload.js?…port=1313`. It was built with `hugo server` (dev mode) and committed. Every page also loads 11 CSS and 11 JS files, including the full Mermaid bundle. | Backlog item: rebuild with `hugo --minify` and trim assets. It also constrains our site if we fork the theme (spec D12). |
| E11 | Submission: "GitHub repository" | Our repo is on GitLab (`labs.gauntletai.com`). | Decision needed (Q11). |

---

### Live API observations (2026-10-13)

Live usage so far: 1 KiB, from one successful call. Error probes return before any entropy is served.

| Request | Status | Body / notes |
|---|---|---|
| Valid Entropy-as-a-Service token, `size=1` | 200 in 1.04 s | `{"random":[1 string of 1,368 chars],"size":1}`. Standard padded base64 that decodes to 1,024 bytes. `cache-control: no-store`. |
| Wrong 40-character token | 403 | `{"error":"Forbidden or entropy quota reached"}` |
| `Bearer ` with an empty token, or no `Authorization` header | 401 | `{"error":"Missing or malformed Authorization header (expected 'Bearer <token>')"}` |
| `size=0` or `size=513` | 400 | `{"error":"'size' must be an integer between 1 and 512"}` |
| All responses | n/a | `content-type: application/json`, `server: cloudflare`. Errors are JSON with a single `error` string, so snippets can print it safely. Because the API sits behind Cloudflare, the proxy, TLS and bot-block cases on the troubleshooting page may see Cloudflare HTML error pages rather than JSON. |

Not observed, and we won't deliberately trigger: 429, 500, 503, quota-exhausted 403, expired token, and `Retry-After`.

### A second, undocumented entropy API (found 2026-10-13)

**Source: the Qrypt portal itself.** The portal shows this as sample code immediately after an API key is created. So at the "token created" stage of the funnel, the portal teaches one API while the docs and the brief teach another. This is the strongest first-run finding so far. Evidence still to capture: a screenshot of the post-creation sample with the key hidden, saved to `docs/evidence/`.

The endpoint accepts the **same key** as `api-eus`, but neither the public docs nor the brief mention it.

| | Documented API (brief) | Second API |
|---|---|---|
| Request | `GET https://api-eus.qrypt.com/api/v1/quantum-entropy?size={KiB}` | `POST https://eaas-aws.qrypt.com/api/v1/entropy` with JSON `{"block_size": bytes, "block_count": n}` |
| Auth | `Authorization: Bearer <key>` | `X-API-Key: <key>` |
| Units | KiB only, 1–512. The minimum is 1,024 bytes even when you need 32. | `block_size` 1–1024 **bytes**, `block_count` 1–512. You can ask for exactly 32 bytes. |
| Response | `{"random": [base64 of 1,024 bytes …], "size": n}` | `{"entropy": [base64 of block_size bytes …], "extensions": []}` |
| Bad or missing key | 403 JSON `error` (bad key); 401 (missing header) | 403 `{"message":"Forbidden"}` for both. This looks like an AWS API Gateway default. |
| Bad parameters | 400 JSON `{"error": …}` | **422 plain text**, e.g. `Parameter 'block_size' has value 0 which is not in the valid range between 1 and 1024` |
| Latency (n = 1 each) | 1.04 s for 1 KiB | 0.49 s for 32 B |

**Update 2026-10-14: the request format *is* documented, under a different product.** The `eaas-aws` API is the **On-Prem Appliance's "Entropy API Schema" v1.0.0**. Its OpenAPI spec sits at the bottom of `docs.qrypt.com/eaas/appliance/` (repo `content/eaas/appliance/_index.md`). The spec defines:
- `POST /api/v1/entropy` with `block_size` 1–1024 and `block_count` 1–512 (default 1), returning `{entropy: [base64], extensions: []}`.
- `GET /api/v1/capabilities`.

Live, `eaas-aws` serves that capabilities endpoint and reports limits that exactly match the spec: `{"entropy":{"min_block_size":1,"max_block_size":1024,"min_block_count":1,"max_block_count":512,"entropy_types":[],"extensions":[]},"healthtest":{}}`. So it is the appliance API running as a cloud service.

What the spec does **not** cover for the cloud deployment:
- the hostname
- `X-API-Key` auth (the appliance has no auth; it is a LAN device)
- every error except 503: we observed 403, 422 and 429 live
- the rate limit

The only source for the hostname and auth is the portal's post-creation sample. Nothing in the docs connects the appliance spec to a cloud endpoint, and a developer reading the "Quantum Entropy" cloud page would never find it.

This shifts the Q17 calculus somewhat. `eaas-aws` gives one API shape for both cloud and on-prem, byte-level sizing, and a discovery endpoint, which reads like the direction Qrypt is heading. It is also now *partly* citable. The default stays `api-eus` until Sean answers, because the brief names it explicitly.

**Are they the same service behind two doors? No.** These results come from `scripts/probe_api.py --only all`, run 2026-10-14.

- **Neither host accepts the other's request.**
  - `api-eus` with `X-API-Key` returns 401 (missing Authorization).
  - `api-eus` at `/api/v1/entropy` returns **530**, Cloudflare error 1033 ("Cloudflare Tunnel error").
  - `eaas-aws` with a Bearer token returns 403. Its GET on the old path returns AWS API Gateway errors ("Missing Authentication Token" / "Invalid key=value pair … in Authorization header").
- **They run on different stacks.** `api-eus` is a Cloudflare Tunnel to an origin service. `eaas-aws` is AWS API Gateway, fronted by Cloudflare. This is inferred from the error signatures. Together with the company profile listing AWS, it supports the "migration in progress" reading, but that is unconfirmed.
- **Successful responses map cleanly.** `GET ?size=n` corresponds exactly to `POST {"block_size":1024,"block_count":n}`: `random` → `entropy`, `size` → `len(entropy)`, and both use standard padded base64. `extensions` has always been empty, and its purpose is unknown.
- **Success payloads, exact comparison** (2026-10-14, `--skeleton`, base64 masked):

  ```
  api-eus  size=1                      {"random":["<b64 1024 B>"],"size":1}             content-length 1392, cache-control: no-store
  eaas-aws block_size=1024, count=1    {"entropy":["<b64 1024 B>"],"extensions":[]}     content-length 1401, no cache-control
  eaas-aws block_size=16,   count=2    {"entropy":["<b64 16 B>","<b64 16 B>"],"extensions":[]}
  ```

  Both are compact JSON with no hidden fields (the content lengths add up exactly) and the same `application/json` type. The element encoding is identical: standard padded base64, 1,368 characters per KiB. The differences are the wrapper field names, the lack of a `size` field on `eaas-aws`, element size (always 1,024 bytes vs `block_size`), and the missing `cache-control: no-store` on `eaas-aws`. That last one is minor, but it is a hygiene item for a service that returns key material.
- **`eaas-aws` has its own, tighter rate limit.** It returned 429 `{"message":"Too Many Requests"}` with no `Retry-After` after about 3 requests in 2 seconds, well under the documented 30 per 10 s (which is documented only for `api-eus`). The exact limit is unknown, and we won't probe for it. Add it to the questions for Sean.
- **Errors don't map at all.**

  | Case | `api-eus` | `eaas-aws` |
  |---|---|---|
  | Missing auth | 401 | 403 |
  | Wrong key | 403 | 403 |
  | Bad size | 400 JSON | 422 plain text labeled `text/html` |
  | Error field | `error` | `message` |
  | Unknown path | 530 Cloudflare | 403 "Missing Authentication Token" |

  Code written for one API's errors would mis-diagnose the other's.

**E13: Cloudflare blocks Python's standard-library HTTP client.** Any request with urllib's default `User-Agent: Python-urllib/3.x` gets **403, Cloudflare error 1010** ("Access denied", banned client signature) on both hosts, before auth is checked. `requests`, `httpx`, `aiohttp`, Node `fetch`, curl, Go and custom User-Agents all pass. Consequences:
- A stdlib-only Python snippet fails unless it sets a User-Agent.
- An agent writing "no-dependency Python" will hit a 403 that looks exactly like a bad key.
- It goes on the troubleshooting page and into the agent failure taxonomy.
- It is a strong backlog item for Qrypt: whitelist the API paths, or return a distinguishable error.

**E16: the live rate limit is far tighter than documented (2026-10-14).** The docs say 30 requests per 10 seconds per token. During the live benchmark, `api-eus` returned **429** on the *second* successful-path request, about 1 second after the first (Node run, then Python run). `eaas-aws` behaved the same way earlier (429 after about 3 requests in 2 seconds). With 15 seconds between requests, 10 of 10 runs succeeded. There's no `Retry-After` header. The exact limit is unknown, and we won't probe for it. Consequences:
- The snippets' 429 hint must not assert "30 requests in 10 seconds".
- The troubleshooting page should say "wait about 15 seconds".
- The agent-test harness must expect quick retries to hit 429.
- It's a strong backlog item: document the real limit, or send `Retry-After`.

**E14: a typo in the path on `api-eus` returns 530 (Cloudflare Tunnel error 1033), not 404.** Confusing for developers. It goes on the troubleshooting page.

Live usage so far: 3,328 B, logged in `docs/evidence/usage.csv`. That includes Andre's portal-sample curl, estimated at 160 B.

**Q17 [you + Sean]: Which API does the quickstart teach?**
*Recommendation (pending Sean):* build on the **documented `api-eus` API**. The brief specifies it explicitly, the graders will check it, and it is the only one with public docs to cite. The agent-readiness test isn't affected, because the agent sees only our quickstart. Human developers will see both, though, so the site needs a short "The portal showed you different sample code?" note that maps one API onto the other. Use the second API as **evidence in the PRD and backlog** instead. Three sources (brief/docs, portal, and whatever produced this curl) now point developers at two different APIs with different auth, units and error formats. That is a first-run problem in its own right, and the byte-sized API removes the brief's whole "KiB vs bytes" failure class. Ask Sean which API is canonical going forward. If he says `eaas-aws`, we switch before writing the snippets. The switch is cheap now and expensive later.

## 3. Open questions (each with a proposed default)

Questions marked **[you]** are for you. **[Sean]** means the question should go to Qrypt's CTO (sean@qrypt.com). If we can't wait for an answer, we proceed on the default and log it as a disclosed assumption.

### Access and budget

**Q1: resolved 2026-10-13.** We have an Entropy-as-a-Service key from a contracted portal account, stored outside the repo. It is verified working. The quota is still unknown.
*(Original question)* **Do we have a token, and which kind?** The options are a self-serve trial via `/trial` or an assessment token from the program contact. The trial quota is unknown. It could be below the 10 MB assessment budget, and a 403 midway through agent runs would wreck the test.
*Default:* request an assessment token. If it doesn't arrive before day 1 ends, use a trial. Track usage in a committed ledger (KiB per activity).

**Q2 [you]: How does the agent get the token without it showing up in logs?** Every agent-readiness run needs a live token in a fresh environment, but logs and recordings must never show it.
*Default:* inject `QRYPT_API_KEY` through the container environment, not through the prompt. Tell the agent "a `.env` with QRYPT_API_KEY already exists" or have the harness write `.env`, and run a scrubber over transcripts before committing them. Open sub-question: does the harness providing `.env` count as fair, given that "skipped .env" is a failure category? Proposal: the harness provides the *value* as an environment variable, and the agent must still create `.env` and its handling itself. That keeps the failure category testable.

**Q3 [you]: Can a separate token be used for the agent runs?** An agent can loop on calls and burn quota.
*Default:* yes if Qrypt will issue one. Either way, add a harness-level rate limit by running agents behind a recording proxy (see Q9).

### Snippet behavior

**Q4 [Sean, default needed now]: Slice or derive?** The attachment says take 32 bytes. Qrypt's own docs budget 40 bytes per AES-256 key.
*Default:* follow the attachment literally (use the first 32 bytes of the first block and discard the rest; never reuse them). Cite NIST SP 800-133r2, which allows direct use of RBG output as a symmetric key. Ask Sean whether EaaS output is already conditioned. If he prefers HKDF, it costs about one line in Node (`crypto.hkdfSync`) and about three in Python.

**Q5 [you]: Where does the AES-GCM nonce come from?** The brief doesn't say. A 12-byte nonce is needed.
*Default:* use the OS CSPRNG (`crypto.randomBytes(12)` / `os.urandom(12)`), not more Qrypt bytes. This keeps "one entropy block → one key" clean and matches the brief's "don't reuse entropy bytes for two purposes" spirit.

**Q6 [you]: How do we count "≤ 25 LOC (excl. comments and imports)"?** Are blank lines excluded? Does the `.env` loader count as an import?
*Default:* count non-blank, non-comment, non-import lines with a committed script (`scripts/loc.sh`) that CI enforces. Report the count on the site.

**Q7 [you]: How does CI point the snippet at the mock server?** A snippet that hardcodes the URL can't be tested. Adding `QRYPT_API_BASE` adds a knob the reader doesn't need.
*Default:* add an optional `QRYPT_API_BASE` environment variable that defaults to `https://api-eus.qrypt.com`. It is not mentioned in the quickstart path and is documented only in the README's testing section. The site renders the **same files** CI tests, so there's a single source of truth with no copy drift.

**Q8 [Sean]: What do real error responses look like?** We need the body format, whether `Retry-After` is sent on 429 and 503, and whether a 400 costs quota. We have since observed 400, 401 and 403 live (see "Live API observations"). We should **not** deliberately trigger 429, 403 or 503 against production.
*Default:* snippets branch on status code only, never on body. Mock bodies are labeled "illustrative" in tests. If `Retry-After` is present, the snippet prints it. Otherwise it prints "wait about 10 seconds".

### The agent-readiness test

**Q9 [you]: Which agent?** The brief says "any with a free path" so graders can reproduce it. Claude Code needs a paid plan, so it may not qualify as the test subject (we can still build with it). Candidates with free tiers include Gemini CLI and GitHub Copilot's free tier, among others.
*Default:* one agent, pinned version and model, recorded in `AI_USAGE.md`. Run it in a throwaway container (`docker run --rm` from a pinned image) with network access, an empty working directory, no repo access, and no memory between runs.

**Q10 [you]: What counts as success, and what is a fair round 1?**
*Proposed success criteria (all must hold):* the program runs with one command, makes a real call to `api-eus` (confirmed at the proxy), and decodes base64. It uses 32 bytes, encrypts and decrypts with AES-GCM, prints success, never prints or writes the key, reads the token from `.env` (not hardcoded), and finishes within 20 minutes of wall-clock time.
*Round 1 integrity:* round 1 must run against the genuine first published version of the docs, not a deliberately weakened one. If round 1 is already 5/5, that's fine. We report it and run a harder variant (a different language, or the Markdown-only path) to find real failure modes for the taxonomy and backlog. We don't sandbag.

### Funnel and measurement (the 25-point PRD)

**Q11 [you]: GitHub or GitLab?** The submission says GitHub, and this repo is GitLab.
*Default:* develop here and push a mirror to GitHub. Run CI on **GitHub Actions** and host on **GitHub Pages**, since "site builds from the repo on push" is simplest when they share a platform.

**Q12 (PRD design, no answer needed, flagged for agreement): Most funnel stages are invisible to us.** Our static site can see landing and in-page actions. Token creation lives in Qrypt's portal. First call and return within 7 days live in Qrypt's API gateway logs. **First key is invisible to everyone**, because key derivation happens client-side and the server can't tell "fetched entropy" from "made a key".
*Proposal for the PRD:*
- Write each stage as an event spec: name, owner system, trigger, properties, and join key.
- Use the account or token ID as the join key inside Qrypt's own systems, which is not a personal identifier exposed to us.
- Define "first key" by proxy as the first 200 response from a request carrying a quickstart `User-Agent` (for example `qrypt-quickstart-python/1.0`). This is honest attribution with no extra telemetry call.
- Define "return" as a successful call on a different UTC day, 1 to 7 days after the first call.
- Keep site analytics, if we add any, cookieless and aggregate-only (landing, tab switch, copy click, troubleshooting view) and disclose them on the site.

**Q13 [you]: Is any human testing possible?** The brief wants a stated time target. Without humans, our evidence is agent runs plus our own timed run.
*Default:* time three cold runs ourselves (fresh container, screen-recorded with the token hidden), plus the agent data. State the target as "about 5 minutes once you have a token" with the measurement linked. Token creation time is excluded and stated separately, because we can't control Qrypt's trial approval.

### Scope and logistics

**Q14 [you]: Deadline?** The brief says a 2-day timebox. When is submission due?

**Q15 [you]: Stretch goals?** *Default:* do `llms.txt` (cheap, and it directly raises agent success) and the competitive teardown (pure PM signal; drand (League of Entropy) or random.org are candidates). Skip a third language and real analytics unless we're ahead.

**Q16 [you]: Which operating systems does "every documented command runs as written" cover?**
*Default:* macOS/Linux shells as primary, plus a Windows PowerShell variant for the `.env` and run commands only. CI covers Linux.

---

## 4. Recommended technical shape (input to the TDD, not decided yet)

| Area | Recommendation | Why |
|---|---|---|
| Language 1 | **Node.js 20.12+** | Zero dependencies: built-in `fetch`, `crypto` AES-GCM, and `process.loadEnvFile()` for `.env`. Fits in 25 lines comfortably. (Local machine has v24.) |
| Language 2 | **Python 3.10+** | The language most developers and agents reach for. The cost is that AES-GCM isn't in the stdlib, so it needs `cryptography` (and `requests` or `urllib`, plus a few lines to parse `.env`). That install step is real first-run friction, so we document it and treat it as a finding. |
| Not chosen | Go | Stdlib-only, but its error handling makes 25 lines very tight. A good third-language stretch. |
| Mock server | One small Node or Python server with scenario switches (200, 400, 401, 403, 429, 500, 503, malformed JSON, timeout) | Shared by both snippets' CI. |
| Site | Plain HTML or a minimal static generator (Astro or 11ty) with no client framework, system fonts, and copy buttons as small vanilla JS | Needed for LCP under 1.5 s on Lighthouse mobile and accessibility of 95 or higher. Docusaurus or Next would fight both targets. |
| Agent harness | Container plus a logging HTTP proxy that redacts the `Authorization` header and records every call to `api-eus` | Gives objective success evidence, the quota ledger, and token-safe logs in one place. |
| Records | CSV for the agent log and RICE backlog. A claims table with an `ai_drafted` column and a `corrected` column from day one. | The brief asks how many AI-drafted claims were corrected. That can only be answered if we track it as we go, not reconstruct it at the end. |

---

## 5. Risks

1. **Trial quota or token availability blocks live work.** Mitigation: Q1, mock-first development, and a usage ledger.
2. **The agent test produces no failures, or only fails for reasons unrelated to the docs.** Mitigation: the Q10 success criteria, and recording the failure reason in agent-neutral categories.
3. **Messaging overclaims quantum safety** (see section 1). Mitigation: claims table review as a merge gate for any site copy.
4. **Mock-server fidelity.** Mitigation: Q8, plus disclosing that 403, 429 and 503 behavior was not verified live.
5. **Token leakage in recordings or logs.** Mitigation: proxy redaction, a pre-commit secret scan (for example gitleaks), and recording from an environment where the token is only in `.env`.
6. **Portal and docs drift during the project.** `/register` is already dead. Mitigation: date-stamp every live observation in the claims table.

---

## 6. Two different PRDs

The brief's deliverable `PRD.md` is an **onboarding PRD for Qrypt's product**: personas, funnel, metrics, non-goals. It is graded at 25 points. What we need before the TDD is a **build spec for our project**: deliverables, acceptance tests, decisions on Q1–Q16. Proposal:

- `docs/PROJECT_SPEC.md` is our internal build PRD. It comes next, once the questions above are answered or defaulted.
- `PRD.md` is the graded deliverable, written as part of the build, informed by the agent findings.

## 7. Next steps

1. You answer the **[you]** questions, or accept the defaults.
2. We email Sean Neal Q4 and Q8 (with E1 and E2 as an FYI). I can draft that email.
3. I write `docs/PROJECT_SPEC.md` with final decisions and acceptance criteria per deliverable, then the TDD.
