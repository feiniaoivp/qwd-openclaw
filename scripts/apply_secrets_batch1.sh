#!/bin/bash
# ---------------------------------------------------------------------------
# OpenClaw Secret 迁移 · 第一批（4 个密钥）
#
# 生成时间：2026-11-12 03:53
# 前置：4 个密钥已存入 team secret store（OPENCLAW_* 系列）
# 目标：把 openclaw.json 里的明文替换为 store SecretRef
#
# ⚠️ 必须在【独立终端】执行（脚本会停 Gateway，会话内跑会杀死自己）
#
# 用法：
#   bash scripts/apply_secrets_batch1.sh --dry-run   # 只演练，不改任何东西
#   bash scripts/apply_secrets_batch1.sh             # 正式执行
# ---------------------------------------------------------------------------
set -uo pipefail

WS="/Users/duguke/.openclaw/workspace"
PLAN="$WS/data/secrets/plan_batch1_20261112.json"
OC="/usr/local/bin/openclaw"
PLIST="$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

echo "=============================================="
echo " OpenClaw Secret 迁移 · 第一批"
echo " DRY_RUN=$DRY"
echo "=============================================="

# ---- 前置检查 ----
echo ""
echo "[0/5] 前置检查"
[ -f "$PLAN" ] || { echo "  ✗ plan 文件缺失: $PLAN"; exit 1; }
echo "  ✓ plan: $PLAN"
[ -x "$OC" ] || { echo "  ✗ 找不到 $OC"; exit 1; }
echo "  ✓ CLI: $($OC --version 2>&1 | head -1)"

TS=$(date +%Y%m%d-%H%M%S)
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.bak-prebatch1-$TS
echo "  ✓ 配置已备份: ~/.openclaw/openclaw.json.bak-prebatch1-$TS"

# ---- 1. 停服务 ----
echo ""
echo "[1/5] 停止 Gateway"
if [ $DRY -eq 1 ]; then
  echo "  (dry-run) launchctl bootout gui/$(id -u)/ai.openclaw.gateway"
else
  launchctl bootout gui/$(id -u)/ai.openclaw.gateway 2>&1 || echo "  (可能已停止)"
  sleep 4
  if pgrep -f "openclaw/dist/index.js gateway" >/dev/null 2>&1; then
    echo "  ⚠️ 仍有 gateway 进程，等待..."
    sleep 5
  fi
  echo "  ✓ 已停止"
fi

# ---- 2. 应用 plan ----
echo ""
echo "[2/5] 应用 secret plan"
if [ $DRY -eq 1 ]; then
  echo "  (dry-run) $OC secrets apply --from $PLAN --dry-run"
  "$OC" secrets apply --from "$PLAN" --dry-run 2>&1 | tail -5
  echo ""
  echo "dry-run 结束，未改动任何文件。"
  exit 0
else
  "$OC" secrets apply --from "$PLAN" 2>&1 | grep -v "DEP_PLUGIN_SDK\|trace-deprecation" | tail -10
  RC=${PIPESTATUS[0]}
  echo "  apply 退出码: $RC"
fi

# ---- 3. 校验配置 ----
echo ""
echo "[3/5] 校验配置"
"$OC" config validate 2>&1 | tail -5

# ---- 4. 启动 ----
echo ""
echo "[4/5] 启动 Gateway"
launchctl bootstrap gui/$(id -u) "$PLIST" 2>&1 || echo "  (可能已加载)"
sleep 18
echo "  ✓ 已提交"

# ---- 5. 验证 ----
echo ""
echo "[5/5] 验证"
"$OC" gateway status 2>&1 | grep -E "CLI version|Gateway version|Runtime|Connectivity" || true
echo ""
echo "--- secrets audit ---"
"$OC" secrets audit --check 2>&1 | head -2

echo ""
echo "=============================================="
echo " 完成。预期 plaintext 从 17 降到 13"
echo "=============================================="
echo ""
echo "如需回滚："
echo "  launchctl bootout gui/\$(id -u)/ai.openclaw.gateway"
echo "  cp ~/.openclaw/openclaw.json.bak-prebatch1-$TS ~/.openclaw/openclaw.json"
echo "  launchctl bootstrap gui/\$(id -u) $PLIST"
