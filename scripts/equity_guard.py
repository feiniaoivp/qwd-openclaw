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

H 组 单票资金桶一致性（2026-10-20 新增，portfolio_core 只 print 自检的门禁化版本）：
  H1  cash 桶脱钩：对每个**空仓**（position=False）票桶，
      |(cash − INITIAL_CAPITAL) − total_pl| > 50 元 -> 违规（HIGH）
      背景：f71ed07 restore bug 曾致 601066/000987 cash 虚增 27,566.54，
      F1-F4 全部查不出单票桶脱钩（总额口径正常）。持仓票跳过
      （cash 含未结算买入成本，口径不同，见 ledger_cash_repair.py 同款口径）。
      口径与 scripts/ledger_cash_repair.py::find_desync 保持一致。

用法:
  python3 scripts/equity_guard.py            # 检查
  python3 scripts/equity_guard.py --verbose  # 打印每个检查点的判定
  python3 scripts/equity_guard.py --date 2026-09-25  # 指定"今天"（测试用）
  python3 scripts/equity_guard.py --state <path>     # 指定 state 文件（测试注入用）
退出码: 0=全部通过, 1=存在 HIGH 违规（WARN 不阻断）
"""

import csv
import json
import os
import sys
import time
from datetime import datetime, date

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

EQUITY_FILE = os.path.join(WORKSPACE, "data", "portfolio_equity.csv")
REFERENCE_TOTAL_CAPITAL = 2_700_000
JUMP_THRESHOLD = 0.50  # 环比跳变 50%
CASH_PL_TOLERANCE = 50.0  # 与 ledger_cash_repair.TOLERANCE / portfolio_core 自检同口径


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


def check_h_cash_pl(state_path: str, verbose: bool):
    """H1 单票 cash 桶脱钩检查（空仓票 |(cash-本金)-total_pl| <= 容忍）。

    返回 (high_issues, warn_issues)。state 缺失 -> 跳过（非违规）；
    JSON 解析失败先等 5s 重试一次（可能撞 cron 写入窗口），仍失败 -> WARN。
    """
    high, warn = [], []
    if not os.path.exists(state_path):
        if verbose:
            print(f"   [H1] state 不存在（{state_path}），跳过（非违规）")
        return high, warn

    try:
        from analysis.portfolio_core import INITIAL_CAPITAL  # SSOT，失败不静默
    except Exception as e:
        warn.append(f"H1 无法导入 portfolio_core.INITIAL_CAPITAL（检查项未执行）: {e}")
        return high, warn

    state = None
    for attempt in (1, 2):
        try:
            with open(state_path) as f:
                state = json.load(f)
            break
        except (json.JSONDecodeError, OSError) as e:
            if attempt == 1:
                if verbose:
                    print(f"   [H1] state 读取失败（{e}），5s 后重试（可能撞写入窗口）")
                time.sleep(5)
            else:
                warn.append(f"H1 state 解析失败（重试后仍失败，检查项未执行）: {e}")
                return high, warn

    positions = state.get("positions", {})
    n_empty = n_held = 0
    for sym, p in positions.items():
        if not isinstance(p, dict):
            continue
        if p.get("position"):  # 持仓票 cash 含未结算买入成本，口径不同，跳过
            n_held += 1
            continue
        n_empty += 1
        cash = float(p.get("cash", 0) or 0)
        tpl = float(p.get("total_pl", 0) or 0)
        diff = (cash - INITIAL_CAPITAL) - tpl
        if abs(diff) > CASH_PL_TOLERANCE:
            high.append(
                f"H1 单票 cash 桶脱钩: {sym} {p.get('name', '')} "
                f"cash={cash:,.2f} total_pl={tpl:+,.2f} "
                f"(cash-本金)-total_pl={diff:+,.2f} 超容忍 {CASH_PL_TOLERANCE:.0f} 元; "
                f"修复参考 scripts/ledger_cash_repair.py"
            )
    if verbose:
        print(f"   [H1] 票桶 {len(positions)}: 空仓 {n_empty} 检查 / 持仓 {n_held} 跳过 / "
              f"违反 {len(high)}（本金 {INITIAL_CAPITAL:,}/票, 容忍 {CASH_PL_TOLERANCE:.0f} 元）")
    return high, warn


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="指定「今天」日期 YYYY-MM-DD（默认取系统当天）")
    ap.add_argument("--verbose", action="store_true", help="打印每个检查点判定")
    ap.add_argument("--state", help="指定 portfolio_sim_state.json 路径（测试注入用，默认生产文件）")
    args = ap.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    rows = load_rows(EQUITY_FILE)

    if not rows:
        if args.verbose:
            print("ℹ️  equity.csv 不存在或无数据，跳过（非违规）")
        # 不 return：H 组基于 state 文件，与 equity.csv 独立

    high_issues = []
    warn_issues = []

    # H1 单票 cash 桶脱钩（2026-10-20，f71ed07 restore bug 事故的门禁化）
    from analysis.portfolio_core import STATE_FILE as DEFAULT_STATE_FILE
    h_high, h_warn = check_h_cash_pl(args.state or DEFAULT_STATE_FILE, args.verbose)
    high_issues.extend(h_high)
    warn_issues.extend(h_warn)

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
        print(f"✅ equity_guard: 通过（{len(rows)} 行，无未来日期/断崖/口径漂移/cash桶脱钩）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
