# Live benchmark: running the quickstart

Date (UTC): 2026-10-14. Node.js v24.14.0, Python 3.13.2.

Each run is `quickstart.mjs / quickstart.py` against the live `api-eus` endpoint with `size=1`, started from a fresh folder holding only the script and a `.env`. The time is wall-clock, from process start to exit, in seconds, including interpreter start-up. 5 runs per language, 1 s apart. This measures the run step only; the five-minute figure on the page covers the whole path.

| Run | Node.js (s) | Python (s) |
|---|---|---|
| 1 | 0.46 | 0.64 |
| 2 | 0.49 | 0.50 |
| 3 | 0.47 | 0.57 |
| 4 | 0.48 | 0.56 |
| 5 | 0.58 | 0.44 |
| **p50** | **0.48** | **0.56** |
| **max** | **0.58** | **0.64** |

Target: every run under 3 s. Result: met (slowest run 0.64 s).

Entropy used: 10 KiB, recorded in `docs/evidence/usage.csv`.
