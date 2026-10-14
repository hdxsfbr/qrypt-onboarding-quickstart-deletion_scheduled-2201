#!/usr/bin/env python3
"""Run Lighthouse (mobile preset) N times against one URL and summarize.

Reports LCP / FCP / TBT / CLS, performance and accessibility scores, and transfer
size, with p50 and p95 across runs. Each run's JSON report is kept in --out so the
numbers can be committed as evidence.

Usage:
  scripts/lighthouse_runs.py URL [--runs 10] [--out docs/evidence/lighthouse/<label>]
"""
import argparse
import json
import math
import pathlib
import statistics
import subprocess
import sys

LIGHTHOUSE = "lighthouse@12.8.2"


def pct(values, p):
    s = sorted(values)
    return s[min(len(s) - 1, math.ceil(p / 100 * len(s)) - 1)]


def run_once(url, out_json, log):
    cmd = ["npx", "-y", LIGHTHOUSE, url,
           "--only-categories=performance,accessibility",
           "--output=json", f"--output-path={out_json}",
           "--chrome-flags=--headless=new --no-sandbox", "--quiet"]
    with open(log, "a") as lf:
        rc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf, timeout=240).returncode
    if rc != 0:
        return None
    r = json.loads(pathlib.Path(out_json).read_text())
    a = r["audits"]
    return {
        "lcp_ms": a["largest-contentful-paint"]["numericValue"],
        "fcp_ms": a["first-contentful-paint"]["numericValue"],
        "tbt_ms": a["total-blocking-time"]["numericValue"],
        "cls": a["cumulative-layout-shift"]["numericValue"],
        "perf": round(r["categories"]["performance"]["score"] * 100),
        "a11y": round(r["categories"]["accessibility"]["score"] * 100),
        "bytes": a["total-byte-weight"]["numericValue"],
        "requests": len(a["network-requests"]["details"]["items"]),
        "a11y_fails": sorted(k for k, v in a.items()
                             if v.get("scoreDisplayMode") == "binary" and v.get("score") == 0
                             and k in {ref["id"] for ref in r["categories"]["accessibility"]["auditRefs"]}),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--out", required=True, help="directory for per-run JSON reports and summary")
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    log = out / "lighthouse.log"

    results = []
    for i in range(1, a.runs + 1):
        res = run_once(a.url, out / f"run-{i:02d}.json", log)
        if res is None:
            print(f"run {i}: lighthouse failed (see {log})", flush=True)
            continue
        results.append(res)
        print(f"run {i}: LCP {res['lcp_ms']:.0f} ms  FCP {res['fcp_ms']:.0f} ms  TBT {res['tbt_ms']:.0f} ms  "
              f"perf {res['perf']}  a11y {res['a11y']}  {res['bytes']/1024:.0f} KiB / {res['requests']} req", flush=True)
    if not results:
        return 1

    summary = {"url": a.url, "runs": len(results), "lighthouse": LIGHTHOUSE}
    for k in ("lcp_ms", "fcp_ms", "tbt_ms", "cls", "perf", "a11y", "bytes"):
        vals = [r[k] for r in results]
        summary[k] = {"p50": statistics.median(vals), "p95": pct(vals, 95), "min": min(vals), "max": max(vals)}
    summary["a11y_fails"] = sorted({f for r in results for f in r["a11y_fails"]})
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nLCP p50 {summary['lcp_ms']['p50']:.0f} ms, p95 {summary['lcp_ms']['p95']:.0f} ms | "
          f"a11y min {summary['a11y']['min']} | perf p50 {summary['perf']['p50']} | "
          f"weight p50 {summary['bytes']['p50']/1024:.0f} KiB")
    if summary["a11y_fails"]:
        print("failing a11y audits:", ", ".join(summary["a11y_fails"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
