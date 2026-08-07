#!/bin/bash
# ============================================================
# 一键恢复 OpenClaw Gateway 到锁定版本 2026.7.1-2
# 用途：当 upgrade 或 daemon 重装覆盖了 plist（指向新版本）后，
#       用此脚本恢复回 2026.7.1-2 版本的启动配置。
#
# 使用：bash restore-openclaw-2026.7.1-2.sh
# ============================================================
set -euo pipefail

VERSION="2026.7.1-2"
PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/ai.openclaw.gateway.plist.v2026.7.1-2.bak"
PLIST_DST="$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"

echo "🔒 目标版本: $VERSION"
echo "  备份来源: $PLIST_SRC"
echo "  目标位置: $PLIST_DST"

# 0. 前置检查：确认锁定版本的 pnpm store 目录仍然存在
STORE_DIR="$HOME/Library/pnpm/store/v11/links/@/openclaw/$VERSION"
if [ ! -d "$STORE_DIR" ]; then
    echo "⚠️  警告：pnpm store 中未找到 $VERSION 目录:"
    echo "    $STORE_DIR"
    echo "    该版本可能已被 pnpm store prune 清理。"
    echo "    需要先重新安装该版本（npm i -g openclaw@$VERSION）才能恢复。"
    read -p "  是否继续？（当前 plist 可能指向不存在的版本）[y/N] " ans
    [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "已取消。"; exit 1; }
fi

# 1. 先停掉当前 gateway 服务
echo ""
echo "⏹  停止当前 gateway 服务..."
openclaw daemon stop 2>/dev/null || launchctl bootout gui/$(id -u)/ai.openclaw.gateway 2>/dev/null || true

# 2. 备份当前（可能是新版）的 plist，避免不可恢复
if [ -f "$PLIST_DST" ]; then
    TIMESTAMP=$(date +%Y%m%d-%H%M%S)
    cp "$PLIST_DST" "${PLIST_DST}.pre-restore-${TIMESTAMP}.bak"
    echo "  已备份当前 plist → ${PLIST_DST}.pre-restore-${TIMESTAMP}.bak"
fi

# 3. 用锁定版本替换 plist
echo ""
echo "📄 覆盖 plist 为 $VERSION 版本..."
cp "$PLIST_SRC" "$PLIST_DST"

# 4. 重新加载服务
echo ""
echo "▶️  重新加载并启动 gateway（$VERSION）..."
openclaw daemon install 2>/dev/null || true   # 确保注册进 launchd
openclaw daemon start 2>/dev/null || launchctl bootstrap gui/$(id -u) "$PLIST_DST" 2>/dev/null || true

echo ""
echo "✅ 恢复完成！验证："
openclaw gateway status
