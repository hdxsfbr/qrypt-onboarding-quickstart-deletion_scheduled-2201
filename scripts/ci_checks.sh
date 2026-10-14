#!/bin/sh
# The single definition of this repo's CI checks. GitLab CI calls one subcommand per job; the
# pre-push hook (.githooks/pre-push) runs `local` so a push that would fail CI fails here first.
#
#   scripts/ci_checks.sh lint           LOC/width limits, syntax, pinned requirements
#   scripts/ci_checks.sh test-python    pytest -m "python or mock"   (needs pytest + deps installed)
#   scripts/ci_checks.sh test-node      pytest -m node               (needs pytest + node on PATH)
#   scripts/ci_checks.sh secrets        gitleaks over git history (binary, else the pinned image)
#   scripts/ci_checks.sh local          all of the above with the local runtimes, using .venv/
#   scripts/ci_checks.sh matrix         the CI test matrix in its exact Docker images, one at a time
#
# POSIX sh on purpose: CI images include slim Debian and Alpine (gitleaks), which lack bash.
set -eu
cd "$(dirname "$0")/.."

SNIPPETS="quickstart/node/quickstart.mjs quickstart/python/quickstart.py"
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
  secrets) secrets ;;
  matrix) matrix ;;
  local)
    start=$(date +%s)
    step lint lint
    step venv ensure_venv
    PYTEST=.venv/bin/pytest
    step test-python test_python
    step test-node test_node
    step secrets secrets
    echo "all CI checks passed locally in $(( $(date +%s) - start ))s"
    ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
