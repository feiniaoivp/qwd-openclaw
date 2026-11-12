#!/bin/bash
# Secret 迁移 v2：修复 v1 的排空不足（state db busy）
# 差异: 停服后轮询等待至无 openclaw 进程持有 state 库; apply 重试 3 次
set -uo pipefail

# launchd 环境无用户 PATH: openclaw shim 是 #!/usr/bin/env node
export PATH="/usr/local/bin:/usr/local/Cellar/node/26.3.1/bin:/usr/bin:/bin:/usr/sbin:/sbin"

WS="/Users/duguke/.openclaw/workspace"
PLAN="$WS/data/secrets/plan_batch1_20261112.json"
OC="/usr/local/bin/openclaw"
PLIST="$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"
LOG=/tmp/secrets_batch1_v2.log
exec >> "$LOG" 2>&1

echo "===== v2 start $(date '+%F %T') ====="

TS=$(date +%Y%m%d-%H%M%S)
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.bak-prebatch1v2-$TS
echo "[backup] openclaw.json.bak-prebatch1v2-$TS"

echo "[1] 停止 Gateway"
launchctl bootout gui/$(id -u)/ai.openclaw.gateway 2>&1 || echo "  (bootout 返回非零, 继续)"

echo "[2] 排空等待 (最长 90s)"
for i in $(seq 1 45); do
  P=$(pgrep -f "openclaw/dist" 2>/dev/null | wc -l | tr -d ' ')
  H=$(lsof -nP /Users/duguke/.openclaw/state/openclaw.sqlite 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')
  echo "  t=${i}x2s openclaw进程=$P state库持有者=$H"
  if [ "$P" = "0" ] && [ "$H" = "0" ]; then echo "  ✓ 排空完成"; break; fi
  sleep 2
done
P=$(pgrep -f "openclaw/dist" 2>/dev/null | wc -l | tr -d ' ')
if [ "$P" != "0" ]; then
  echo "  ✗ 90s 后仍有 openclaw 进程, 放弃 (不硬杀)"; exit 1
fi

echo "[3] apply (重试 3)"
RC=1
for try in 1 2 3; do
  "$OC" secrets apply --from "$PLAN" 2>&1 | grep -v "DEP_PLUGIN_SDK" | tail -6
  RC=${PIPESTATUS[0]}
  echo "  try$try rc=$RC"
  [ "$RC" = "0" ] && break
  sleep 5
done
[ "$RC" != "0" ] && { echo "✗ apply 三次均失败, 恢复服务后退出"; launchctl bootstrap gui/$(id -u) "$PLIST" 2>&1 || true; exit 1; }

echo "[4] 校验配置"
"$OC" config validate 2>&1 | tail -2

echo "[5] 启动 Gateway"
launchctl bootstrap gui/$(id -u) "$PLIST" 2>&1 || echo "  (bootstrap 返回非零, 可能已在)"
sleep 20

echo "[6] 验证"
"$OC" gateway status 2>&1 | grep -E "Runtime|Connectivity|version" | head -4 || true
lsof -nP -iTCP:18789 -sTCP:LISTEN 2>/dev/null | tail -1 || true
echo "--- secrets audit ---"
"$OC" secrets audit 2>&1 | head -1
echo "===== v2 done $(date '+%F %T') ====="
