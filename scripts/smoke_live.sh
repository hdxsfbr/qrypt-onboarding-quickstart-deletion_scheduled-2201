#!/bin/sh
# End-to-end smoke test against the REAL deployed site and the LIVE Qrypt API: runs the
# Quickstart page's documented commands exactly as published, once per language, in a fresh
# folder. Uses 1 KiB of entropy per language (logged to docs/evidence/usage.csv).
#
#   scripts/smoke_live.sh [SITE_BASE_URL] [--lang node|python]
#   SITE_BASE_URL defaults to https://qrypt-quickstart.pages.dev/ (use a preview URL before merging)
#
# The only deviation from the page: after the documented `echo ... > .env`, the placeholder is
# replaced with the key from ~/.config/agentforge/qrypt_api_key (as the page tells a reader to).
# The key is never printed.
set -eu
cd "$(dirname "$0")/.."
repo=$PWD
base=https://qrypt-quickstart.pages.dev/
langs="Node.js Python"
for arg in "$@"; do
  case "$arg" in
    --lang=node|node) langs="Node.js" ;;
    --lang=python|python) langs="Python" ;;
    --lang) ;;
    http*) base=$arg ;;
  esac
done
case "$base" in */) ;; *) base="$base/" ;; esac
key_file="$HOME/.config/agentforge/qrypt_api_key"
success="✔ Generated a 256-bit AES key from Qrypt quantum entropy (AES-GCM round trip OK)."

status=0
for lang in $langs; do
  work=$(mktemp -d)
  script="$work/steps.sh"
  # Documented blocks in page order: shared ones plus this language's tab.
  python3 scripts/extract_commands.py site/content/quickstart/_index.md --kind sh \
    | python3 -c '
import json, sys
lang, base = sys.argv[1], sys.argv[2]
for b in json.load(sys.stdin):
    if b.get("tab") in (None, lang):
        code = b["code"].replace("https://qrypt-quickstart.pages.dev/", base)
        print(code)
        if "paste-your-key-here" in code:
            print("sed -i \"s|paste-your-key-here|$(tr -d \"\\n\\r \" < \"$QS_KEY_FILE\")|\" .env")
' "$lang" "$base" > "$script"
  printf '%-8s ' "$lang"
  if out=$(cd "$work" && QS_KEY_FILE="$key_file" env -u QRYPT_API_KEY -u QRYPT_API_URL \
             sh -e "$script" 2>&1 </dev/null); then :; fi
  last=$(printf '%s\n' "$out" | tail -1)
  if [ "$last" = "$success" ]; then
    echo "ok   $last"
    printf '%s,smoke_live_%s,200,1024\n' "$(date -u +%FT%T+00:00)" "$lang" >> "$repo/docs/evidence/usage.csv"
  else
    echo "FAIL"; printf '%s\n' "$out" | tail -8 | sed 's/^/    /'; status=1
  fi
  rm -rf "$work"
  [ "$lang" = Node.js ] && [ "$langs" != Node.js ] && sleep 15  # live rate limit (POV E16)
done
exit $status
