#!/usr/bin/env python3
"""信号 × 基本面叠加：读 close_scan_v2 当日 json，叠加 baostock 基本面维。

复用 SSOT：analysis/trader_stock_picks.get_fundamentals（peTTM/pbMRQ 日K扩展 + query_profit_data ROE/净利率）。
用法: python3 scripts/signal_fundamentals_overlay.py [YYYY-MM-DD]
输出: analysis/daily/<date>_signal_fundamentals.md（同时 stdout 打印表格文本）
"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
from datetime import date as _date

from analysis.trader_stock_picks import get_fundamentals

TIER_ORDER = ["强烈买入", "关注", "中性", "谨慎", "强烈卖出"]


def tier_of(level: str) -> str:
    for t in TIER_ORDER:
        if t in level:
            return t
    return "其他"


def fmt(v, nd=2, suffix=""):
    if v is None:
        return "—"
    try:
        return f"{float(v):.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else _date.today().isoformat()
    src = os.path.join(WORKSPACE, "analysis", "daily", f"{day}_close_scan.json")
    with open(src) as fh:
        d = json.load(fh)
    if d.get("data_date") != day:
        print(f"⚠️ json data_date={d.get('data_date')} != 请求日期 {day}", file=sys.stderr)

    rows = []
    for s in d["stocks"]:
        sig = s.get("signal") or {}
        f = get_fundamentals(s["symbol"])
        rows.append({
            "symbol": s["symbol"], "name": s["name"],
            "price": s.get("price"), "chg": s.get("change_pct"),
            "level": sig.get("level", "?"), "score": sig.get("score"),
            "reasons": (sig.get("reasons") or "").strip(),
            "pe": f.get("pe"), "pb": f.get("pb"),
            "roe": f.get("roe"), "npm": f.get("profit"),
        })
        print(f"  [{len(rows)}/{len(d['stocks'])}] {s['symbol']} {s['name']} "
              f"pe={fmt(f.get('pe'))} roe={fmt(f.get('roe'))}", file=sys.stderr)

    tier_rank = {t: i for i, t in enumerate(TIER_ORDER)}
    rows.sort(key=lambda r: (tier_rank.get(tier_of(r["level"]), 9),
                             -(r["score"] or 0), r["symbol"]))

    lines = [f"# 信号 × 基本面叠加 {day}", "",
             f"- 数据日期: {d.get('data_date')} | 信号源: close_scan_v2 "
             f"(analysis/daily/{day}_close_scan.json)",
             "- 基本面源: baostock peTTM/pbMRQ(日K扩展) + query_profit_data ROE/净利率(2026Q2, 缺失回退Q1) "
             "[复用 trader_stock_picks.get_fundamentals]",
             "", "## 全池 36 只（按信号档位排序）", "",
             "| 股票 | 收盘 | 涨跌% | 信号 | 分 | PE(TTM) | PB | ROE% | 净利率% |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['name']}({r['symbol']}) | {fmt(r['price'])} | {fmt(r['chg'])} "
            f"| {r['level']} | {r['score'] if r['score'] is not None else '—'} "
            f"| {fmt(r['pe'], 1)} | {fmt(r['pb'])} | {fmt(r['roe'], 1)} | {fmt(r['npm'], 1)} |")

    buys = [r for r in rows if tier_of(r["level"]) in ("强烈买入", "关注")]
    if buys:
        lines += ["", "## 买入档明细", ""]
        for r in buys:
            lines.append(
                f"### {r['name']}({r['symbol']}) {r['level']} 分{r['score']} "
                f"收盘 {fmt(r['price'])} ({fmt(r['chg'])}%)")
            lines.append(f"- 信号理由: {r['reasons'] or '—'}")
            lines.append(
                f"- 基本面: PE {fmt(r['pe'],1)} / PB {fmt(r['pb'])} / "
                f"ROE {fmt(r['roe'],1)}% / 净利率 {fmt(r['npm'],1)}%")
            lines.append("")

    out = os.path.join(WORKSPACE, "analysis", "daily", f"{day}_signal_fundamentals.md")
    with open(out, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nOK -> {out}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
