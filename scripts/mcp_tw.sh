#!/usr/bin/env bash
# Stateless TradingWizard MCP tool call via streamable-http. Each POST is independent.
# Usage: mcp_tw.sh <method> <json-args-or-empty>
set -euo pipefail
EP="https://www.tradingwizard.ai/api/mcp"
METHOD="$1"
ARGS="${2:-}"
[ -z "$ARGS" ] && ARGS="{}"
# Validate ARGS is JSON
echo "$ARGS" | python3 -c "import json,sys; json.loads(sys.stdin.read())" >/dev/null 2>&1 || { echo "INVALID_ARGS: $ARGS" >&2; exit 2; }
curl -s -w "\n__HTTP_STATUS__:%{http_code}\n" -X POST "$EP" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"$METHOD\",\"arguments\":$ARGS}}" \
  --max-time 90
