#!/bin/bash
# 自动 Wiki 编译 + 检查 Cron 脚本
# 建议加入 crontab: 0 4 * * * /path/to/wiki_cron_setup.sh >> /tmp/wiki_cron.log 2>&1

WIKI_DIR="/Users/duguke/.openclaw/wiki/main"
LOG_FILE="/tmp/wiki_cron_$(date +%Y%m%d).log"

echo "=== Wiki 自动维护开始 $(date) ===" >> "$LOG_FILE"

cd "$WIKI_DIR" || { echo "无法进入 Wiki 目录" >> "$LOG_FILE"; exit 1; }

# 1. 全量编译 (重建索引/摘要/agent-digest.json)
echo "[$(date)] 执行 wiki compile..." >> "$LOG_FILE"
if openclaw wiki compile >> "$LOG_FILE" 2>&1; then
    echo "[$(date)] wiki compile 成功" >> "$LOG_FILE"
else
    echo "[$(date)] wiki compile 失败" >> "$LOG_FILE"
fi

# 2. Lint 检查 (矛盾/溯源缺口/开放问题)
echo "[$(date)] 执行 wiki lint..." >> "$LOG_FILE"
if openclaw wiki lint >> "$LOG_FILE" 2>&1; then
    echo "[$(date)] wiki lint 成功 (0 issues)" >> "$LOG_FILE"
else
    echo "[$(date)] wiki lint 发现问题，详见报告" >> "$LOG_FILE"
fi

# 3. 状态检查
echo "[$(date)] 执行 wiki status..." >> "$LOG_FILE"
openclaw wiki status >> "$LOG_FILE" 2>&1

# 4. 桥接同步 (拉取最新 memory-core 公共产物)
echo "[$(date)] 执行 wiki bridge import..." >> "$LOG_FILE"
if openclaw wiki bridge import >> "$LOG_FILE" 2>&1; then
    echo "[$(date)] wiki bridge import 成功" >> "$LOG_FILE"
else
    echo "[$(date)] wiki bridge import 失败或无新内容" >> "$LOG_FILE"
fi

echo "=== Wiki 自动维护结束 $(date) ===" >> "$LOG_FILE"