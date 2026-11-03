#!/usr/bin/env python3
"""RSSHub 健康检查：定时任务可调用，失败时 Telegram 报警。"""
import os
import sys
import subprocess
import requests

WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL")  # 可选：直接用 Bot API 更稳
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "626141741")
RSSHUB_BASE = "http://localhost:1200"

ROUTES = [
    ("财联社深度", "/cls/depth"),
    ("财新网", "/caixin/article"),
    ("金十数据快讯", "/jin10/important"),
    ("东财策略研报", "/eastmoney/report/strategyreport"),
    ("东财宏观研报", "/eastmoney/report/macresearch"),
    ("东财券商晨报", "/eastmoney/report/brokerreport"),
    ("东财行业研报", "/eastmoney/report/industry"),
]

def send_alert(text):
    if BOT_TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
        except Exception:
            pass
    # 也打印到 stdout 供 cron 捕获
    print(text)

def check_route(name, path):
    try:
        r = requests.get(f"{RSSHUB_BASE}{path}", timeout=10)
        if r.status_code != 200:
            return f"❌ {name}: HTTP {r.status_code}"
        if "<?xml" not in r.text[:200] and "<rss" not in r.text[:500]:
            return f"❌ {name}: 非 XML 响应 ({len(r.text)} bytes)"
        return f"✅ {name}"
    except Exception as e:
        return f"❌ {name}: {e}"

def main():
    results = []
    failed = 0
    for name, path in ROUTES:
        result = check_route(name, path)
        results.append(result)
        if result.startswith("❌"):
            failed += 1
    
    summary = f"RSSHub 健康检查 {'❌ 失败' if failed else '✅ 通过'} ({len(ROUTES)-failed}/{len(ROUTES)})\n" + "\n".join(results)
    print(summary)
    
    if failed:
        send_alert(f"🚨 <b>RSSHub 异常</b>\n{summary}")
        sys.exit(1)

if __name__ == "__main__":
    main()
