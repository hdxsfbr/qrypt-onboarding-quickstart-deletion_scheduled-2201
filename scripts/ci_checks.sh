#!/bin/sh
# The single definition of this repo's CI checks. GitLab CI calls one subcommand per job; the
# pre-push hook (.githooks/pre-push) runs `local` so a push that would fail CI fails here first.
#
#   scripts/ci_checks.sh lint           LOC/width limits, syntax, pinned requirements
#   scripts/ci_checks.sh test-python    pytest -m "python or mock"   (needs pytest + deps installed)
#   scripts/ci_checks.sh test-node      pytest -m node               (needs pytest + node on PATH)
#   scripts/ci_checks.sh secrets        gitleaks over git history (binary, else the pinned image)
#   scripts/ci_checks.sh site           build the site, then pytest -m "site or docs" against it
#                                       (needs python3, git, curl, node 20.12+, network for Hugo/pip)
#   scripts/ci_checks.sh docs-pwsh      run the {test="pwsh"} doc blocks in the pinned PowerShell
#                                       image and check the .env they write (needs docker)
#   scripts/ci_checks.sh agent-harness  pytest agent-test/tests (agent-readiness harness; no Docker,
#                                       no network, no live calls)
#   scripts/ci_checks.sh local          all of the above but docs-pwsh, with the local runtimes,
#                                       using .venv/
#   scripts/ci_checks.sh matrix         the CI test matrix in its exact Docker images, one at a time
#
# POSIX sh on purpose: CI images include slim Debian and Alpine (gitleaks), which lack bash.
set -eu
cd "$(dirname "$0")/.."

SNIPPETS="quickstart/node/quickstart.mjs quickstart/python/quickstart.py"
# MCR publishes no patch-level powershell tags after 7.2.2, so the newest 7.x series is pinned by
# digest as well (tag 7.5-debian-12, PowerShell 7.5.0, image created 2025-01-30).
PWSH_IMAGE="mcr.microsoft.com/powershell:7.5-debian-12@sha256:7ab5bd5ca6f95a3351fa0c6a1205237d57048c94542355aab55519a0861a9b25"
DOCS_PAGE="${DOCS_PAGE:-site/content/quickstart/_index.md}"
GITLEAKS_IMAGE="zricethezav/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"
PYTEST="${PYTEST:-pytest}"

lint() {
  python3 scripts/loc.py $SNIPPETS --max 25 --max-width 100
  node --check quickstart/node/quickstart.mjs
  python3 -m py_compile quickstart/python/quickstart.py
  # Exactly three requirements, each pinned with ==, ignoring blank and comment lines.
  awk '
    /^[[:space:]]*(#|$)/ { next }
    { n++ }
    $0 !~ /^[A-Za-z0-9._-]+==[^=[:space:]]+$/ { print "not pinned with ==: " $0; bad++ }
    END {
      if (n != 3) { print "expected 3 requirements, found " n; bad++ }
      exit bad ? 1 : 0
    }' quickstart/python/requirements.txt
}

test_python() { $PYTEST -q -m "python or mock"; }
agent_harness() { $PYTEST -q agent-test/tests; }
test_node() { $PYTEST -q -m node; }

secrets() {
  if command -v gitleaks >/dev/null 2>&1; then
    gitleaks git --redact --no-banner .
  elif command -v docker >/dev/null 2>&1; then
    docker run --rm -v "$PWD:/repo:ro" -w /repo "$GITLEAKS_IMAGE" \
      git --redact --no-banner /repo </dev/null
  else
    echo "secrets: neither gitleaks nor docker is available; install one (or push with --no-verify)" >&2
    return 1
  fi
}

# Builds site/public with Hugo, then runs the site and documented-command tests against exactly
# that build (SITE_PUBLIC), so the tested files are the files the deploy job publishes.
site() {
  python3 scripts/build_site.py
  SITE_PUBLIC="$PWD/site/public" $PYTEST -q -m "site or docs"
}

# Windows blocks: every {test="pwsh"} block must parse in PowerShell; blocks that run the
# snippet (node, python) cannot run in this image, so they are parse-checked only; all others
# run in order in one session. The check is on the .env they leave behind.
docs_pwsh() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "docs-pwsh: docker is required" >&2; return 1
  fi
  work=$(mktemp -d)
  mkdir "$work/blocks" "$work/run"
  python3 scripts/extract_commands.py "$DOCS_PAGE" --kind pwsh | python3 -c '
import json, sys
blocks = json.load(sys.stdin)
if not blocks:
    sys.exit("docs-pwsh: no {test=\"pwsh\"} blocks in " + sys.argv[1])
for b in blocks:
    with open("%s/blocks/%02d.ps1" % (sys.argv[2], b["index"]), "w", encoding="utf-8") as fh:
        fh.write(b["code"])
print("docs-pwsh: %d block(s)" % len(blocks))
' "$DOCS_PAGE" "$work"
  cat > "$work/runner.ps1" <<'PS1'
$ErrorActionPreference = 'Stop'
Set-Location /work/run
foreach ($file in Get-ChildItem /work/blocks -Filter *.ps1 | Sort-Object Name) {
  $text = Get-Content -Raw $file.FullName
  $errors = $null
  $null = [System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$null, [ref]$errors)
  if ($errors.Count -gt 0) { throw "$($file.Name): syntax error: $($errors[0].Message)" }
  if ($text -match '^\s*(node|python3?|py)\b') {
    Write-Host "parse only: $($file.Name)"
  } else {
    Write-Host "run: $($file.Name)"
    . $file.FullName
  }
}
$env_file = Join-Path (Get-Location) '.env'
if (-not (Test-Path $env_file)) { throw 'the blocks did not create .env' }
$bytes = [System.IO.File]::ReadAllBytes($env_file)
if ($bytes -contains 0) { throw '.env contains NUL bytes (UTF-16?)' }
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
  throw '.env starts with a UTF-8 BOM'
}
if (-not (@(Get-Content $env_file) -match '^QRYPT_API_KEY=.+')) {
  throw '.env has no QRYPT_API_KEY= line'
}
Write-Host '.env contains QRYPT_API_KEY='
PS1
  rc=0
  docker run --rm --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -e NO_COLOR=1 -e POWERSHELL_TELEMETRY_OPTOUT=1 -e POWERSHELL_UPDATECHECK=Off -v "$work:/work" \
    "$PWSH_IMAGE" pwsh -NoProfile -NonInteractive -File /work/runner.ps1 </dev/null || rc=$?
  rm -rf "$work"
  return $rc
}

ensure_venv() {
  if [ ! -x .venv/bin/pytest ] || [ requirements-dev.txt -nt .venv/.installed ] \
     || [ quickstart/python/requirements.txt -nt .venv/.installed ]; then
    python3 -m venv .venv
    .venv/bin/pip install -q -r requirements-dev.txt </dev/null
    touch .venv/.installed
  fi
  PYTEST=.venv/bin/pytest
}

# Reads the version lists from .gitlab-ci.yml so the local matrix cannot drift from CI.
ci_versions() { sed -n "s/.*- $1: \[\(.*\)\].*/\1/p" .gitlab-ci.yml | tr -d "' " | tr ',' ' '; }

matrix() {
  for v in $(ci_versions PYTHON_VERSION); do
    echo "== test-python $v"
    docker run --rm -v "$PWD:/src:ro" "python:$v-slim-bookworm" sh -c \
      'cp -r /src /w && cd /w && pip install -q -r requirements-dev.txt && scripts/ci_checks.sh test-python' </dev/null
  done
  for v in $(ci_versions NODE_VERSION); do
    echo "== test-node $v"
    docker run --rm -v "$PWD:/src:ro" "node:$v-bookworm" sh -c \
      'apt-get update -qq && apt-get install -y -qq --no-install-recommends python3-venv >/dev/null \
       && cp -r /src /w && cd /w && python3 -m venv /v && /v/bin/pip install -q -r requirements-dev.txt \
       && PYTEST=/v/bin/pytest scripts/ci_checks.sh test-node' </dev/null
  done
}

step() {
  name=$1; shift
  printf '%-12s ' "$name"
  log=$(mktemp)
  if "$@" >"$log" 2>&1; then echo "ok"; rm -f "$log"; else
    echo "FAILED"; tail -40 "$log"; rm -f "$log"; return 1; fi
}

case "${1:-}" in
  lint) lint ;;
  test-python) test_python ;;
  test-node) test_node ;;
  agent-harness) agent_harness ;;
  secrets) secrets ;;
  site) site ;;
  docs-pwsh) docs_pwsh ;;
  matrix) matrix ;;
  local)
    start=$(date +%s)
    step lint lint
    step venv ensure_venv
    PYTEST=.venv/bin/pytest
    step test-python test_python
    step test-node test_node
    step agent-harness agent_harness
    step site site
    step secrets secrets
    echo "all CI checks passed locally in $(( $(date +%s) - start ))s"
    ;;
  *) sed -n '2,20p' "$0"; exit 2 ;;
esac
