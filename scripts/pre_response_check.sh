#!/usr/bin/env bash
# pre_response_check.sh — 会话发送前快速检查包装脚本
# 用法: ./pre_response_check.sh "即将发送的文本"
# 返回: 0=通过可发送, 1=有违规需修正

set -euo pipefail

TEXT="$1"
if [[ -z "$TEXT" ]]; then
    echo "用法: $0 \"<文本>\"" >&2
    exit 2
fi

cd /Users/duguke/.openclaw/workspace
python3 scripts/pre_response_guard.py --text "$TEXT"
exit $?