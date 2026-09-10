#!/usr/bin/env bash
# ============================================================
# Obsidian Vault 单向同步脚本：仅同步 .md 文件，排除 .obsidian/ .trash/ 及图片/附件
# 目标：/Users/duguke/.openclaw/workspace/obsidian_vault
# 用法：bash scripts/sync_obsidian.sh [--dry-run]
# ============================================================

set -euo pipefail

# ---------- 配置区（按需修改） ----------
SRC="/Users/duguke/Documents/Obsidian Vault"
DEST="/Users/duguke/.openclaw/workspace/obsidian_vault"
# ----------------------------------------

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
  echo "🔍  DRY-RUN 模式：仅预览，不写入"
fi

# 颜色
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

log()  { echo -e "${GREEN}[SYNC]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERR ]${NC} $*"; }

# 检查源目录
if [[ ! -d "$SRC" ]]; then
  err "源目录不存在：$SRC"
  exit 1
fi

# 确保目标目录存在
mkdir -p "$DEST"

# rsync 过滤规则（按序匹配，首个匹配生效）：
#  1. 排除 .obsidian/ .trash/ 目录
#  2. 排除常见非 md 附件
#  3. 包含所有目录（用于遍历）
#  4. 包含所有 .md 文件
#  5. 排除其余一切
# --prune-empty-dirs  删除同步后空目录
RSYNC_OPTS=(
  -avh
  --delete
  --filter="- .obsidian/"
  --filter="- .trash/"
  --filter="- *~"
  --filter="- *.png"
  --filter="- *.jpg"
  --filter="- *.jpeg"
  --filter="- *.gif"
  --filter="- *.pdf"
  --filter="- *.base"
  --filter="- *.tmp"
  --filter="- *.bak"
  --filter="+ */"
  --filter="+ *.md"
  --filter="- *"
  --prune-empty-dirs
  --progress
)

if $DRY_RUN; then
  RSYNC_OPTS+=(--dry-run)
fi

log "开始同步：$SRC  →  $DEST"
log "仅同步 *.md，排除 .obsidian/ .trash/ 及图片/附件"

rsync "${RSYNC_OPTS[@]}" "$SRC/" "$DEST/"

EXIT_CODE=$?
if [[ $EXIT_CODE -eq 0 ]]; then
  log "同步完成 ✅"
  # 统计
  MD_COUNT=$(find "$DEST" -name "*.md" -type f | wc -l | tr -d ' ')
  TOTAL_SIZE=$(du -sh "$DEST" 2>/dev/null | cut -f1)
  log "目标 .md 文件数：$MD_COUNT"
  log "目标占用大小：$TOTAL_SIZE"
else
  err "rsync 退出码：$EXIT_CODE"
  exit $EXIT_CODE
fi