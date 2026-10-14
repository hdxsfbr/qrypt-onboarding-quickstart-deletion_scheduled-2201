#!/usr/bin/env python3
"""Show the latest GitLab pipeline for a branch and its jobs (uses the glab CLI's login).

Usage:
  scripts/ci_status.py [--ref BRANCH] [--wait] [--log JOB_NAME]
  --wait polls every 20 s until the pipeline finishes (max 20 min).
  --log prints the last 60 lines of the named job's log (for failed jobs).
"""
import argparse
import json
import os
import subprocess
import sys
import time

PROJECT = "andrebatista%2Fqrypt-onboarding-quickstart"
ENV = {**os.environ, "GITLAB_HOST": "labs.gauntletai.com"}
DONE = {"success", "failed", "canceled", "skipped"}


def api(path):
    out = subprocess.run(["glab", "api", f"projects/{PROJECT}/{path}"], env=ENV, stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, check=True).stdout
    return out if path.endswith("/trace") else json.loads(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", default=subprocess.run(["git", "branch", "--show-current"], capture_output=True,
                                                    text=True).stdout.strip())
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--log")
    a = ap.parse_args()
    deadline = time.time() + 1200
    while True:
        pipes = api(f"pipelines?ref={a.ref}&per_page=1")
        if not pipes:
            sys.exit(f"no pipeline for {a.ref}")
        p = pipes[0]
        if not a.wait or p["status"] in DONE or time.time() > deadline:
            break
        time.sleep(20)
    jobs = api(f"pipelines/{p['id']}/jobs?per_page=100")
    print(f"pipeline {p['id']} on {a.ref} @ {p['sha'][:8]}: {p['status']}  {p['web_url']}")
    for j in sorted(jobs, key=lambda j: (j["stage"], j["name"])):
        dur = f"{j['duration']:.0f}s" if j.get("duration") else ""
        print(f"  {j['stage']:8} {j['name']:28} {j['status']:9} {dur}")
    if a.log:
        job = next((j for j in jobs if j["name"] == a.log), None)
        if job:
            print("\n".join(api(f"jobs/{job['id']}/trace").splitlines()[-60:]))
    return 0 if p["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
