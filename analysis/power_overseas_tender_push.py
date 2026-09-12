#!/usr/bin/env python3
"""
电网装备/特高压出海 - 真实事件/公告监控推送 (Command 模式)
============================================================
替代原伪造的 tender_email_parser 路径。

数据源: 东方财富公告 API (直连逐股) -> power_overseas_events.EventCollector
       分类: ORDER_WIN(中标/合同) / EARNINGS_BEAT(业绩) / INVESTMENT / POLICY 等

⚠️ 实测该 API ~31s/请求, 8只约 4-5min, 故 timeout 设 600s。

用法:
  python3 analysis/power_overseas_tender_push.py [--days 3] [--no-push]
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

WORKSPACE = "/Users/duguke/.openclaw/workspace"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)
if f"{WORKSPACE}/analysis" not in sys.path:
    sys.path.insert(0, f"{WORKSPACE}/analysis")

logging.disable(logging.WARNING)

TYPE_LABEL = {
    "ORDER_WIN": "🏆 中标/合同",
    "EARNINGS_BEAT": "📈 业绩",
    "INVESTMENT": "🏗️ 投资/产能",
    "POLICY": "📜 政策",
    "PARTNERSHIP": "🤝 合作",
    "FINANCING": "💰 融资",
    "OTHER": "📄 其他",
}
# 高价值 = 真正值得推送的事件类型
HIGH_VALUE = {"ORDER_WIN", "EARNINGS_BEAT", "INVESTMENT", "POLICY", "PARTNERSHIP"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--all", action="store_true", help="推送全部事件(默认仅高价值类型)")
    args = ap.parse_args()

    import power_overseas_events as e

    try:
        events = e.collect_events(args.days)
    except Exception as ex:
        msg = f"❌ 事件采集失败: {type(ex).__name__}: {ex}"
        print(msg)
        if not args.no_push:
            from send_telegram import send_with_retry
            send_with_retry(msg, parse_mode="")
        return 1

    # 转 dict
    rows = []
    for x in events:
        d = x if isinstance(x, dict) else x.__dict__
        rows.append(d)

    if not rows:
        print(f"[INFO] 近{args.days}天无事件")
        return 0

    if not args.all:
        rows = [r for r in rows if r.get("event_type") in HIGH_VALUE]

    if not rows:
        print(f"[INFO] 近{args.days}天无高价值事件(仅常规公告)")
        return 0

    # 按重要性+日期排序
    rows.sort(key=lambda r: (r.get("importance", 1), r.get("pub_date", "")), reverse=True)

    lines = [f"⚡ 电网装备出海 · 事件/公告监控", f"📅 近{args.days}天 | 源: 东财公告(真实)"]
    lines.append("")
    for r in rows[:15]:
        label = TYPE_LABEL.get(r.get("event_type", ""), r.get("event_type", ""))
        imp = "★" * int(r.get("importance", 1) or 1)
        lines.append(f"{label} {imp}")
        lines.append(f"  {r.get('name','')}({r.get('symbol','')}) {r.get('pub_date','')}")
        lines.append(f"  {str(r.get('title',''))[:70]}")
    lines.append("")
    lines.append(f"共 {len(rows)} 条 | {datetime.now().strftime('%m-%d %H:%M')}")

    text = "\n".join(lines)
    print(text)

    if not args.no_push:
        from send_telegram import send_with_retry
        send_with_retry(text, parse_mode="")
        print("\n[PUSHED]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
