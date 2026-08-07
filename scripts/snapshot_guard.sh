#!/usr/bin/env bash
# snapshot_guard.sh — 重大变更前自动创建 Time Machine 本地快照 + Git tag
# 用法: snapshot_guard.sh "<变更描述>" [--dry-run]
# 依赖: macOS tmutil, git, op (1Password CLI)

set -euo pipefail

DESC="${1:-"未描述的变更"}"
DRY_RUN="${2:-}"
WORKSPACE="/Users/duguke/.openclaw/workspace"
RETENTION_DAYS=30
MAX_SNAPSHOTS=30

cd "$WORKSPACE"

# 1. Git 状态检查
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "⚠️  工作区有未提交变更，建议先 commit 或 stash"
  [[ -z "$DRY_RUN" ]] && exit 1
fi

# 2. 创建 Git tag（语义化：guard/YYYYMMDD-HHMMSS-short-desc）
TAG="guard/$(date +%Y%m%d-%H%M%S)-$(echo "$DESC" | tr ' ' '-' | cut -c1-30)"
if [[ -z "$DRY_RUN" ]]; then
  git tag -a "$TAG" -m "Guard snapshot: $DESC"
  echo "✅ Git tag created: $TAG"
else
  echo "[dry-run] Would create tag: $TAG"
fi

# 3. 创建 Time Machine 本地快照
SNAP_LABEL="openclaw-guard-$(date +%Y%m%d-%H%M%S)"
if [[ -z "$DRY_RUN" ]]; then
  tmutil localsnapshot /
  echo "✅ Time Machine local snapshot created: $SNAP_LABEL"
else
  echo "[dry-run] Would run: tmutil localsnapshot /"
fi

# 4. 清理旧快照（保留最近 MAX_SNAPSHOTS 个 openclaw-guard-* 快照）
if [[ -z "$DRY_RUN" ]]; then
  tmutil listlocalsnapshots / | grep 'openclaw-guard-' | sort -r | tail -n +$((MAX_SNAPSHOTS+1)) | while read -r snap; do
    tmutil deletelocalsnapshots "$(echo "$snap" | awk '{print $NF}')"
    echo "🗑️  Deleted old snapshot: $snap"
  done
  # 同步清理对应 Git tag
  git tag -l 'guard/*' | sort -r | tail -n +$((MAX_SNAPSHOTS+1)) | xargs -r git tag -d
  echo "✅ Retention policy applied (keep last $MAX_SNAPSHOTS)"
fi

echo "🛡️  Guard snapshot complete: $DESC"