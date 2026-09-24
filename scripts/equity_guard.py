#!/usr/bin/env python3
"""
权益快照账本门禁 (equity_guard)
================================
背景（2026-09-25）：
  模拟盘 `portfolio_equity.csv` 出现两类脏数据：
  1) 「未来日期」—— 存在晚于当天日期的行（09-25 当天出现 09-26 行）；
  2) 「断崖跳变」—— total_value 环比跳变 >50%（09-25 从 271 万崩到 31 万，
     根因是 run_portfolio_scan 在 state 重建中间态下算出的 cash_total 残缺）。

  本门禁把这两类异常固化为**可执行检查**，接入 cron 后不依赖人记得执行。

检查规则（相对 equity.csv 最新一行）：
  F1  未来日期：任意数据行 date 晚于「今天」 -> 违规（HIGH）
  F2  断崖跳变：与前一「正常」行相比，total_value 环比变化 >50% -> 违规（HIGH）
  F3  口径漂移：total_initial 偏离 REFERENCE（270 万）>5% -> 违规（HIGH）
  F4  日期单调性：数据行 date 未按时间递增（回退/乱序）-> 告警（WARN）

用法:
  python3 scripts/equity_guard.py            # 检查
  python3 scripts/equity_guard.py --verbose  # 打印每个检查点的判定
  python3 scripts/equity_guard.py --date 2026-09-25  # 指定"今天"（测试用）
退出码: 0=全部通过, 1=存在 HIGH 违规（WARN 不阻断）
"""

import csv
import os
import sys
from datetime import datetime, date

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)

EQUITY_FILE = os.path.join(WORKSPACE, "data", "portfolio_equity.csv")
REFERENCE_TOTAL_CAPITAL = 2_700_000
JUMP_THRESHOLD = 0.50  # 环比跳变 50%


def load_rows(path: str):
    """读取 equity.csv 数据行（跳过表头），返回 list[dict]。"""
    if not os.path.exists(path):
        return []
    with open(path, newline='') as f:
        reader = csv.DictReader(f)
        return [r for r in reader if r.get("date")]


def parse_date(s: str):
    try:
        return date.fromisoformat(s.strip())
    except (ValueError, AttributeError):
        return None


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="指定「今天」日期 YYYY-MM-DD（默认取系统当天）")
    ap.add_argument("--verbose", action="store_true", help="打印每个检查点判定")
    args = ap.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    rows = load_rows(EQUITY_FILE)

    if not rows:
        if args.verbose:
            print("ℹ️  equity.csv 不存在或无数据，跳过（非违规）")
        return 0

    high_issues = []
    warn_issues = []

    # F1 未来日期
    for r in rows:
        d = parse_date(r.get("date", ""))
        if d is None:
            high_issues.append(f"F1 非法日期: {r.get('date')!r}")
        elif d > today:
            high_issues.append(f"F1 未来日期: {r.get('date')} (晚于今天 {today})")

    # F4 日期单调性
    dates = [parse_date(r.get("date", "")) for r in rows if parse_date(r.get("date", ""))]
    sorted_dates = sorted(dates)
    if dates != sorted_dates:
        warn_issues.append(f"F4 日期未按时间递增: {[str(d) for d in dates]}")

    # F2 断崖跳变（逐行对比前一「正常」行）
    prev_tv = None
    for r in rows:
        try:
            tv = float(r.get("total_value", 0))
        except (ValueError, TypeError):
            continue
        if prev_tv is not None and prev_tv > 0:
            delta = abs(tv - prev_tv) / prev_tv
            if delta > JUMP_THRESHOLD:
                high_issues.append(
                    f"F2 断崖跳变: {r.get('date')} total_value={tv:,.0f} vs 前值 {prev_tv:,.0f} (环比 {delta*100:.1f}% > {JUMP_THRESHOLD*100:.0f}%)"
                )
        prev_tv = tv

    # F3 口径漂移
    for r in rows:
        try:
            ti = float(r.get("total_initial", 0))
        except (ValueError, TypeError):
            continue
        if ti > 0 and abs(ti - REFERENCE_TOTAL_CAPITAL) / REFERENCE_TOTAL_CAPITAL > 0.05:
            high_issues.append(
                f"F3 口径漂移: {r.get('date')} total_initial={ti:,.0f} vs REFERENCE={REFERENCE_TOTAL_CAPITAL:,.0f}"
            )

    # 输出
    if args.verbose:
        print(f"📋 equity_guard 检查 (today={today}, 数据行 {len(rows)})")
        for r in rows:
            print(f"   {r.get('date')}  total_value={r.get('total_value')}  total_initial={r.get('total_initial')}")

    if high_issues:
        print(f"🔴 equity_guard: {len(high_issues)} 项 HIGH 违规")
        for i in high_issues:
            print(f"   - {i}")
        return 1

    if warn_issues:
        print(f"🟡 equity_guard: {len(warn_issues)} 项告警（不阻断）")
        for w in warn_issues:
            print(f"   - {w}")
    else:
        print(f"✅ equity_guard: 通过（{len(rows)} 行，无未来日期/断崖/口径漂移）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
