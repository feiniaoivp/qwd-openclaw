#!/bin/sh
# ============================================================
# Analytics API → Telegram 定时推送触发器
# 供 openclaw cron --command 调用（isolated + 命令模式，API 自身推 Telegram）
#
# 用法: analytics_push.sh <dashboard|tickets|cases> [limit] [dry]
#   dashboard → /api/push/dashboard （早盘 09:30，失败归因仪表盘摘要）
#   tickets   → /api/push/tickets?limit=N （午盘 13:00，OPEN 工单待办）
#   cases     → /api/push/cases?limit=N （收盘 15:30，最近失败案例）
#   第 3 参传 "dry" 时附加 dry_run=true&force=true（只组稿落日志、不实发，用于测试）
#
# 设计要点：
#   - API 侧自带交易日门控（周末/节假日自动跳过，见 analysis/analytics_api.py _do_push）
#   - 失败重试一次（sleep 8 后再试，覆盖 launchd 重启窗口）
#   - 响应落盘 logs/analytics_push.log（不依赖 cron 捕获 stdout）
#   - 退出码 = 最后一次 curl 的退出码（真实反映成败，不吞错）
# ============================================================
KIND="${1:?用法: analytics_push.sh <dashboard|tickets|cases> [limit] [dry]}"
LIMIT="${2:-10}"
API="http://127.0.0.1:8081/api/push/${KIND}?limit=${LIMIT}"
LOG="/Users/duguke/.openclaw/workspace/logs/analytics_push.log"

if [ "$3" = "dry" ]; then
  API="${API}&dry_run=true&force=true"
fi

mkdir -p "$(dirname "$LOG")"
{
  echo "=== $(date '+%F %T') push:${KIND} limit:${LIMIT}${3:+ mode:$3} ==="
  curl -s --noproxy '*' --max-time 75 "$API" \
    || { sleep 8; curl -s --noproxy '*' --max-time 75 "$API"; }
  echo
} >> "$LOG" 2>&1
