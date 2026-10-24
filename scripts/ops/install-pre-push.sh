#!/bin/sh
# Install scripts/ops/pre-push into this clone's .git/hooks (macOS / Linux).
#   sh scripts/ops/install-pre-push.sh
# Then self-tests it: a non-fast-forward push to main must be refused.
set -e
root=$(git rev-parse --show-toplevel)
hooks=$(git rev-parse --git-path hooks)
cp "$root/scripts/ops/pre-push" "$hooks/pre-push"
chmod +x "$hooks/pre-push"
Z=0000000000000000000000000000000000000000
head=$(git rev-parse HEAD)
parent=$(git rev-parse HEAD~1 2>/dev/null || echo "$Z")
if printf 'refs/heads/main %s refs/heads/main %s\n' "$parent" "$head" | "$hooks/pre-push" origin x >/dev/null 2>&1; then
  echo "pre-push installed, but the self-test did NOT refuse a rewrite of main" >&2; exit 1
fi
echo "pre-push installed at $hooks/pre-push; self-test: a rewrite of main is refused."
