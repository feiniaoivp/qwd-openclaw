#!/usr/bin/env python3
"""2026-11-07 补推：11-06~11-07 Telegram TLS 潮汐期间推送失败的任务产物。

背景
----
11-06(周五) 起 api.telegram.org TLS 握手全灭（直连+7890 代理+固定 IP 三路 curl 35），
9 个 cron 报 error；11-07 早间 blogwatcher/weekly-review 同签名再挂 2 个。
2026-11-07 21:56 实测：7890 代理存活，经代理访问 Telegram HTTP 302（通）。
本脚本只补推送已落盘产物，不重算（市场已收盘，重算无意义且有非交易日护栏）。

补推清单（7 项）
----------------
1. 11-06 收盘全池扫描   analysis/daily/2026-11-06_close_scan.md
2. 11-06 盘后新闻日报   data/news/daily_2026-11-06.md（超长自动分段）
3. 11-06 模拟盘周报     analysis/daily/2026-11-06_portfolio_weekly.md
4. 11-06 每日量化回测   analysis/backtest/2026-11-06.md（仅整体概况节）
5. 11-07 周六交易复盘   memory/2026-11-07-weekly-review.md
6. 11-07 blogwatcher 摘要  logs/blogwatcher_scan.log 最后一个 2026-11-07 摘要块
7. 11-07 每周策略回测简报  analysis/daily/2026-11-07_brief.md

不补推（有意跳过）：盘中时效类 4 job（auction-signal-push / market-breadth-open-scan /
intraday-alert-watch ×2）——盘中信号已过时，补推误导；原始数据快照已在
data/auction/2026-11-06_9_25.json 留痕。

产出
----
logs/backfill_push_20261107.json 逐条落盘（含每条 msg 结果），防止最终回复失败丢记录。
"""
import os
import sys
import json
import time
import shutil

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from send_telegram import send_message  # noqa: E402  (自带 直连→7890→固定IP 三层 fallback)

WS = WORKSPACE
RESULT_PATH = os.path.join(WS, "logs", "backfill_push_20261107.json")


def read_text(rel):
    with open(os.path.join(WS, rel), encoding="utf-8", errors="replace") as f:
        return f.read()


def quant_section():
    """整体概况节 + 完整报告指引（忠实于 quant_push 的摘要式推送）。"""
    md = read_text("analysis/backtest/2026-11-06.md")
    cut = md.find("## 各策略收益Top5股票")
    body = md[:cut] if cut > 0 else md[:3800]
    return body.rstrip() + "\n\n(完整报告: analysis/backtest/2026-11-06.md)"


def blogwatcher_digest():
    """从扫描日志提取今日摘要块（cron 06:51 推送失败，扫描/落盘已完成）。"""
    txt = read_text("logs/blogwatcher_scan.log")
    marker = "📰 blogwatcher 每日扫描 2026-11-07"
    idx = txt.rfind(marker)
    if idx < 0:
        return None
    end_marker = "条见 blogwatcher articles"
    end = txt.find(end_marker, idx)
    if end < 0:
        return txt[idx:idx + 3800]
    return txt[idx:end + len(end_marker)]


def build_items():
    items = []
    items.append(("【补推 11-06】A股收盘全池扫描（原 18:30 推送遇 TLS 潮汐失败）",
                  read_text("analysis/daily/2026-11-06_close_scan.md")))
    items.append(("【补推 11-06】盘后新闻日报（原 19:06 推送失败）",
                  read_text("data/news/daily_2026-11-06.md")))
    items.append(("【补推 11-06】模拟盘周报 11-02~11-06（原 19:30 推送失败）",
                  read_text("analysis/daily/2026-11-06_portfolio_weekly.md")))
    items.append(("【补推 11-06】每日量化回测（原 20:30 推送失败）",
                  quant_section()))
    items.append(("【补推 11-07】周六交易复盘（原 06:00 推送失败）",
                  read_text("memory/2026-11-07-weekly-review.md")))
    dg = blogwatcher_digest()
    if dg:
        items.append(("（原 06:51 推送失败）" , dg))
    else:
        print("[warn] blogwatcher 11-07 摘要块未在日志中找到，跳过该项")
    items.append(("【补推 11-07】每周A股策略回测更新简报（weekly_full_pipeline 06:51 产物，晚间简报推送当时 Fake OK 未达）",
                  read_text("analysis/daily/2026-11-07_brief.md")))
    return items


def main():
    items = build_items()
    results = []
    for header, text in items:
        payload = header.rstrip("）") + "）\n" + text if not header.startswith("【补推") else header + "\n" + text
        try:
            r = send_message(payload)
        except Exception as e:  # noqa: BLE001 —— 补推循环必须逐条可见，禁止静默
            r = {"ok": False, "error": repr(e)}
        head = text.strip().splitlines()[0][:42] if text.strip() else "(empty)"
        results.append({"header": header[:60], "first_line": head, "ok": bool(r.get("ok")), "result": r})
        print(("OK  " if r.get("ok") else "FAIL") + " | " + header[:50], flush=True)
        time.sleep(1.5)
    if os.path.exists(RESULT_PATH):
        shutil.copy2(RESULT_PATH, RESULT_PATH + ".bak")  # 写前备份（backup_guard 铁律）
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump({"ran_at": time.strftime("%F %T"), "results": results}, f, ensure_ascii=False, indent=2)
    fails = [r for r in results if not r["ok"]]
    print(f"\n补推完成: {len(results) - len(fails)}/{len(results)} 成功，明细: {RESULT_PATH}", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
