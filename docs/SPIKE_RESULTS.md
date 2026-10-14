# Spike results (TDD step 0)

2026-10-14. Both spikes answer "yes". Neither changes the architecture in [PROJECT_SPEC.md](PROJECT_SPEC.md). The details below go into the TDD.

## Spike A: can a fork of Qrypt's docs be our quickstart site and still meet the targets?

**Yes.** The fork serves only as a visual shell. All our changes stay inside the Quickstart section, and Qrypt's own pages are left untouched.

Setup: `QryptInc/QryptInc.github.io` at `a88e74cf9c`, theme submodule `e817f53`, Hugo 0.154.5 (official release, checksum verified), built with `hugo --minify`. It was served locally with gzip (`serve@14.2.4`) and measured with `scripts/lighthouse_runs.py` (Lighthouse 12.8.2, mobile preset). Summaries are in `docs/evidence/lighthouse/spikes/`.

| Variant | LCP p50 | a11y | Perf | Weight / requests | Notes |
|---|---|---|---|---|---|
| Live docs.qrypt.com/sdk/quickstarts/ (3 runs) | 2,811 ms (p95 4,169) | 92 | 91 | 603 KiB / 32 | The look as Qrypt ships it |
| V1: fork with our page, theme templates as-is | 4,601 ms | 85 | 71 | 609 KiB / 32 | 23 render-blocking files. Contrast and link-name failures. |
| **V2: our section on lean templates** | **1,503 ms** | **95** | **100** | **116 KiB / 6** | Inlined CSS, no theme JS, Chroma `github` style, labeled links |
| Diagnostic: V2 without web fonts (1 run) | 1,203 ms | 95 | 100 | 60 KiB / 2 | The 4 IBM Plex weights cost about 300 ms |

How the lean section works, with all of it scoped to `content/quickstart/` and `layouts/quickstart/`:
- **Templates:** `layouts/quickstart/{list,single}.html` call `qs-header` and `qs-footer` partials in place of the theme's.
- **CSS:** the theme's CSS (nucleus, tags, theme, hugo-theme, theme-mine) is concatenated with `readFile` into one inline `<style>`, so nothing blocks rendering.
- **Removed:** jQuery, Font Awesome, highlight.js, featherlight, perfect-scrollbar, modernizr and Mermaid.
- **Code highlighting:** a section-only render hook, `layouts/quickstart/_markup/render-codeblock.html`, uses `transform.Highlight … "style=github"`.
- **Menu and labels:** `qs-menu.html` is a copy of their menu with an accessible name on the logo link. The menu toggle uses an inline SVG with an `aria-label`.
- **Verified:** a diff of their pages against an unmodified upstream build shows the only change is the new "Quickstart" menu entry, plus cache-busting timestamps.

Remaining work for the TDD (no risk to feasibility):
1. **LCP margin:** load 2 preloaded IBM Plex weights instead of 4, or subset them, to land comfortably under 1.5 s. Then confirm on the deployed GitHub Pages URL, not only localhost.
2. **Tabs:** write our own accessible tabs component. The theme's tabs have no ARIA roles, and their hidden panels inherit light-on-light code colors (contrast 1.03:1), which is the last accessibility failure. We also need copy buttons and a vanilla JS menu toggle for mobile.
3. **Fork hygiene:** delete `static/CNAME` (`docs.qrypt.com`), add `noindex`, attribution, and the CC BY-NC-SA 4.0 notice.
4. **Hugo warnings:** the build warns "Raw HTML omitted" on two of their pages. That's their content, so leave it alone.

## Spike B: do both snippets fit in 25 lines with full error handling?

**Yes**, and with every line under 100 characters. Drafts are in `spikes/snippets/`, counted by `scripts/loc.py`.

| Snippet | Lines (brief's rule) | Live run | Error cases verified against the spike mock |
|---|---|---|---|
| `quickstart.mjs` (Node 24, no packages) | 24 / 25 | ✔ 0.61 s | no .env, empty key, 403, 429, 500, 503, network down |
| `quickstart.py` (Python 3.13, requests + cryptography + python-dotenv) | 25 / 25 | ✔ 0.53 s | same |

No run printed the key in hex or base64 (checked against a fixed mock block). Live usage was 2 KiB, logged.

How the drafts fit:
- **Error messages:** each one names the problem and links to a troubleshooting anchor (`…/troubleshooting/#403`). The full explanation lives on the troubleshooting page.
- **Hints:** only 401, 403 and 429 have their own hint. Every 5xx gets "try again shortly".
- **URL override:** the variable is `QRYPT_API_URL` (the full URL), not `QRYPT_API_BASE`, which saves a line in both languages. Spec D8 changes accordingly.
- **Exit codes:** every failure exits with 1, which saves the separate exit code 2 from the spec. The brief only requires a non-zero exit.

Trade-offs to record in the TDD:
- **Malformed 200 responses produce a stack trace** in both languages. Handling them costs 2–3 lines, which would put Python over 25. This is acceptable because the brief requires clean failures for a missing token, 401, 429, 500 and 503, all of which are handled.
- **Network error detail in Node** prints `TypeError`, not the underlying code (`e.cause?.code` was undefined on Node 24 for a refused connection). Fix: read `e.cause?.code ?? e.cause?.message`.
- **`.env` location:** Python's `load_dotenv()` searches from the *script's* directory upward, while Node's `loadEnvFile()` reads the *current working* directory. The docs must say "run it from the folder that holds `.env`".
- **Wording:** the two snippets word the generic fallback differently ("unexpected" vs "unexpected response"). Make them match.
