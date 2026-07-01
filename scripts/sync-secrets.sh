#!/usr/bin/env bash
# One-time (re)sync of GitHub repo secrets from your local .env.
#
# Prereq:  gh auth login       (or set GH_TOKEN)
# Run:     bash scripts/sync-secrets.sh
#
# Safe to re-run anytime you change a URL in .env — it just overwrites.
# Secret names are set to match the env vars config.py reads, 1:1.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "Setting secrets from .env ..."
# Every KEY=value line in .env (skips blanks/comments) becomes a repo secret.
while IFS='=' read -r key value; do
  [[ -z "$key" || "$key" == \#* ]] && continue
  printf '%s' "$value" | gh secret set "$key"
  echo "  set $key"
done < <(grep -E '^[A-Z_]+=' .env)

echo
echo "Removing old, misnamed secrets (safe if they don't exist) ..."
for old in SL_TEAMS_CSV SL_MATCHES_CSV SL_GOALS_CSV \
           NDL_TEAMS_CSV NDL_MATCHES_CSV \
           WP_TEAMS_CSV WP_MATCHES_CSV; do
  gh secret delete "$old" 2>/dev/null && echo "  deleted $old" || true
done

echo
echo "Done. Current secrets:"
gh secret list
