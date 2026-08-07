#!/usr/bin/env bash
# restore_from_snapshot.sh — 从 guard 快照（Git tag + TM 快照）一键回滚
# 用法: restore_from_snapshot.sh <guard-tag或快照名称>
# 依赖: git, tmutil, launchctl
#
# 示例:
#   ./scripts/restore_from_snapshot.sh guard/init-20260808-0530
#   ./scripts/restore_from_snapshot.sh guard/20260808-053000-示例变更

set -euo pipefail

TAG="${1:-}"
WORKSPACE="/Users/duguke/.openclaw/workspace"
PLIST="$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"

if [[ -z "$TAG" ]]; then
  echo "❌ 用法: restore_from_snapshot.sh <guard-tag>"
  echo ""
  echo "   可用的 guard tag:"
  git -C "$WORKSPACE" tag -l 'guard/*' | sort -r | head -20
  exit 1
fi

echo "⚠️  即将回滚至快照: $TAG"
echo "   工作区: $WORKSPACE"
echo ""
read -r -p "确认回滚？当前未提交变更将被覆盖 (yes/no): " CONFIRM
if [[ "$CONFIRM" != "yes" ]]; then
  echo "🚫 已取消"
  exit 1
fi

cd "$WORKSPACE"

# 1. 备份当前 Git 状态（防止误操作）
CUR_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'detached')"
echo "📦 当前分支: $CUR_BRANCH，正在创建救援分支 pre-rollback-$(date +%Y%m%d-%H%M%S)..."
git stash --include-untracked 2>/dev/null || true
git branch -f "pre-rollback-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true

# 2. Git 回滚到指定 tag
if ! git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "❌ Git tag 不存在: $TAG"
  echo "   可用 tag:"
  git tag -l 'guard/*'
  exit 1
fi
echo "🔄 Git checkout $TAG ..."
git checkout "$TAG" -- . 2>&1 | tail -5

# 3. 尝试 Time Machine 快照恢复（若对应快照存在）
SNAP_NAME="com.apple.TimeMachine.$(echo "$TAG" | tr '/' '-')"
if tmutil listlocalsnapshots / | grep -q "$SNAP_NAME"; then
  echo "📀 正在恢复 Time Machine 快照: $SNAP_NAME ..."
  tmutil restorelocalsnapshot "$SNAP_NAME" || echo "⚠️  TM 快照恢复失败（不影响 git 回滚）"
else
  echo "ℹ️  未找到对应 TM 快照 $SNAP_NAME，跳过（Git 回滚已生效）"
fi

# 4. 重启 Gateway 服务
if [[ -f "$PLIST" ]]; then
  echo "🔄 重启 OpenClaw Gateway 服务..."
  launchctl unload "$PLIST" 2>/dev/null || true
  sleep 2
  launchctl load "$PLIST" 2>/dev/null || true
  echo "✅ Gateway 已重启"
else
  echo "⚠️  未找到 plist: $PLIST，请手动重启服务"
fi

echo ""
echo "✅ 已回滚到 $TAG"
echo "   如需要放弃回滚: git checkout <pre-rollback 分支> -- ."
