#!/bin/sh
# Build the site and deploy it to a Cloudflare Pages preview URL for the current git branch
# (https://<branch>.qrypt-quickstart.pages.dev). Production only changes through CI on main.
#
#   scripts/deploy_preview.sh [--no-build]
#
# Reads the Cloudflare token and account id from ~/.config/agentforge/ (never printed).
set -eu
cd "$(dirname "$0")/.."
branch=$(git branch --show-current)
[ "$branch" = main ] && { echo "refusing to deploy a preview from main" >&2; exit 2; }
[ "${1:-}" = --no-build ] || python3 scripts/build_site.py </dev/null >/dev/null

CLOUDFLARE_API_TOKEN=$(tr -d '\n\r ' < "$HOME/.config/agentforge/cloudflare_api_token")
CLOUDFLARE_ACCOUNT_ID=$(tr -d '\n\r ' < "$HOME/.config/agentforge/cloudflare_account_id")
export CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID WRANGLER_SEND_METRICS=false

# Deploy from a temp dir: wrangler writes a .wrangler/ cache next to its working directory.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cp -r site/public "$tmp/public"
sha=$(git rev-parse HEAD)
(cd "$tmp" && npx -y wrangler@4.144.0 pages deploy public --project-name qrypt-quickstart \
  --branch "$branch" --commit-hash "$sha" --commit-dirty=true </dev/null 2>&1) \
  | grep -E 'Success|complete|alias|ERROR' || true
echo "preview: https://$(echo "$branch" | tr '/_.' '---').qrypt-quickstart.pages.dev/quickstart/"
