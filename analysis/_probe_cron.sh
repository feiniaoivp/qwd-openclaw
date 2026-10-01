#!/usr/bin/env bash
# Locate openclaw and show cron job / runs for the pre-market deep analysis job
set -e
OPENCLAW=""
for p in "$HOME/Library/pnpm/openclaw" "$HOME/.openclaw/bin/openclaw" "/usr/local/bin/openclaw" "$HOME/.local/bin/openclaw"; do
  if [ -f "$p" ] || command -v openclaw >/dev/null 2>&1; then
    OPENCLAW="$(command -v openclaw 2>/dev/null || echo "$p")"
    break
  fi
done
echo "OPENCLAW=$OPENCLAW"
# Prefer the pnpm store absolute path (version-locked)
PNPM_PATH="$HOME/Library/pnpm/store/v11/links/@/openclaw/2026.7.1-2/70f37540f83c40ebaf27fb4fba4200035ea3cde560aaee5f3dd525267857fa1f/node_modules/openclaw/dist/index.js"
if [ -f "$PNPM_PATH" ]; then
  OPENCLAW="$PNPM_PATH"
  echo "Using pnpm store path: $OPENCLAW"
fi
echo "=== node openclaw cron list (json tail) ==="
node "$OPENCLAW" cron list 2>&1 | tail -80