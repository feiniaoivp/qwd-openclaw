#!/usr/bin/env python3
"""双轨止损回测：同一入场信号集下对比 无止损/纯固定/纯自适应/双轨 四种离场口径
================================================================================
背景（2026-10-15 20:58 止损校验复盘）：通用固定止损 28 只 100% 破位 vs 自适应
ATR/EMA 止损 15 只 0% 破位 —— 口径差异已量化，本脚本在回测层评估「双轨止损」
对胜率/盈亏比/回撤的影响（用户 2026-10-16 05:02 指令立项）。

口径（复用存量定义，不造第二套）：
- 入场/信号卖出：直接复用 analysis/backtest_strategies.py 的策略 action 流，
  每只股票用 data/adaptive_strategy_map.json（SSOT 36 只口径）分配的策略
- 固定轨 fixed     : entry*(1-FIXED_PCT) 静态（默认 8%）
- 自适应轨 adaptive: close[t-1] - ATR_MULT*ATR14[t-1] 逐日重算
                     （== adaptive_trader.calc_atr_stops 盘中口径 mult=2.0，非棘轮）
- 双轨 dual        : max(fixed, adaptive) —— 固定轨兜尾部风险，自适应轨随价上移
- 触发顺序：先止损后信号卖出（盘中触发语义）；成交按当日收盘（与框架一致）
- 成本：COMMISSION=0.0003 / SLIPPAGE=0.001 / 本金 10 万（与 backtest_strategies 相同）

已知口径限制（显式声明，不静默）：
1) bollinger 策略自带 ATR 止损 action，各臂同等享有，不影响臂间对比
2) bull_trend 部分止盈在本模拟器按全平处理（仅 1 只，各臂同质）
3) ema_adaptive_vol（3 只）无回测级信号流实现，跳过并显式标注
4) 日收盘成交假设：未模拟盘中触发价与收盘价之差
"""

import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
# 必须同时插入：工作区根 + analysis/（TOOLS.md 强制样板）
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import argparse
import json
import logging
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import pandas_ta as ta

warnings.filterwarnings("ignore")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dual_stop")

# 存量复用：SSOT 关注池 + 策略 action 流 + 成本口径
from analysis.fib_extension_scan import WATCHLIST  # noqa: E402
from analysis.backtest_strategies import (  # noqa: E402
    fetch_data,
    strategy_bollinger_atr,
    strategy_ema_cross,
    strategy_ema_obv,
    strategy_kdj_cci,
    strategy_macd,
    strategy_bull_trend_follow,
    COMMISSION,
    SLIPPAGE,
    INITIAL_CAPITAL,
)
from analysis.bs_session import ensure_login  # noqa: E402

STRATEGY_FNS = {
    "macd": strategy_macd,
    "ema_cross": strategy_ema_cross,
    "ema_obv": strategy_ema_obv,
    "bollinger": strategy_bollinger_atr,
    "kdj_cci": strategy_kdj_cci,
    "bull_trend": strategy_bull_trend_follow,
}

MODES = ["none", "fixed", "adaptive", "dual"]
MODE_LABEL = {
    "none": "无止损(原信号)",
    "fixed": "纯固定(entry-8%)",
    "adaptive": "纯自适应(2xATR14)",
    "dual": "双轨(max固定,自适应)",
}

OUTPUT_DIR = os.path.join(WORKSPACE, "analysis", "backtest")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 止损参数（对齐存量口径；可用 CLI 覆盖）
ATR_MULT = 2.0        # == adaptive_trader.calc_atr_stops 默认
FIXED_PCT = 0.08      # 固定轨：进场价 -8%
FIXED_LOW_N = 10      # fixed_mode=prior_low 时：入场前 N 日最低价
W4_TAIL_BARS = 330    # 近 1 年窗：约 245 交易日 + 60 bar 策略预热 + 余量
MIN_BARS = 160        # 低于此样本量的股票跳过（预热不足）


def load_strategy_map() -> dict:
    """读 SSOT 策略分配，兼容 str/dict 两种历史结构"""
    path = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    m = {}
    for k, v in raw.items():
        if isinstance(v, str):
            m[k] = v
        elif isinstance(v, dict):
            s = v.get("strategy") or v.get("name")
            if s:
                m[k] = s
    return m


def simulate(df: pd.DataFrame, actions: list, mode: str,
             fixed_pct: float = FIXED_PCT, fixed_mode: str = "entry_pct",
             atr_mult: float = ATR_MULT):
    """同一信号流 + 可切换止损层的逐日模拟。

    返回 (trades, max_dd)：trades 每条含 entry/exit 日期价格、净收益率、
    离场原因（signal / stop:fixed / stop:adaptive / 期末平仓）。
    """
    df = df.reset_index(drop=True)
    atr = ta.atr(df["high"], df["low"], df["close"], length=14).values
    close = df["close"].astype(float).values
    low = df["low"].astype(float).values
    dates = [d.strftime("%Y-%m-%d") for d in df["date"]]
    act_by_date = {}
    for a in actions:
        act_by_date.setdefault(a["date"], a)

    capital = float(INITIAL_CAPITAL)
    shares = 0
    entry_price = 0.0
    entry_dt = None
    fixed_stop = None
    peak = capital
    max_dd = 0.0
    trades = []

    def do_sell(i: int, reason: str):
        nonlocal capital, shares, entry_price, entry_dt, fixed_stop
        price = float(close[i])
        unit_out = price * (1 - SLIPPAGE) * (1 - COMMISSION)
        capital += shares * unit_out
        unit_in = entry_price * (1 + SLIPPAGE) * (1 + COMMISSION)
        ret_pct = (unit_out / unit_in - 1) * 100 if unit_in > 0 else 0.0
        trades.append({
            "entry_date": entry_dt, "exit_date": dates[i],
            "entry": round(entry_price, 3), "exit": round(price, 3),
            "ret_pct": round(ret_pct, 3), "reason": reason,
        })
        shares = 0
        entry_price = 0.0
        entry_dt = None
        fixed_stop = None

    for i in range(60, len(df)):
        price = float(close[i])
        dt = dates[i]
        act = act_by_date.get(dt)

        # 1) 止损层（先于信号卖出 —— 盘中触发语义）
        if shares > 0 and mode != "none":
            prev_atr = atr[i - 1]
            ady = None
            if not np.isnan(prev_atr):
                ady = float(close[i - 1]) - atr_mult * float(prev_atr)
            if mode == "fixed":
                eff = fixed_stop
                track = "fixed"
            elif mode == "adaptive":
                eff = ady
                track = "adaptive"
            else:  # dual
                if ady is not None and fixed_stop is not None:
                    eff = max(fixed_stop, ady)
                    track = "adaptive" if ady >= fixed_stop else "fixed"
                else:
                    eff = fixed_stop
                    track = "fixed"
            if eff is not None and price < eff:
                do_sell(i, f"stop:{track}")

        # 2) 信号卖出
        if shares > 0 and act is not None and act["type"] == "SELL":
            do_sell(i, "signal")

        # 3) 信号买入
        if shares == 0 and act is not None and act["type"] == "BUY":
            unit_in = price * (1 + SLIPPAGE) * (1 + COMMISSION)
            n = int(capital / unit_in / 100) * 100
            if n >= 100:
                shares = n
                capital -= n * unit_in
                entry_price = price
                entry_dt = dt
                if fixed_mode == "prior_low" and i >= FIXED_LOW_N:
                    fixed_stop = float(np.min(low[i - FIXED_LOW_N:i]))
                else:
                    fixed_stop = price * (1 - fixed_pct)

        # 4) 权益曲线与回撤（持仓按裸收盘估值）
        eq = capital + shares * price
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # 期末未平仓按最后收盘强制了结（各臂同质，计入统计）
    if shares > 0:
        do_sell(len(df) - 1, "期末平仓")

    return trades, round(max_dd * 100, 2)


def agg_stats(trades: list, dds: list) -> dict:
    rets = [t["ret_pct"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    stop_fixed = sum(1 for t in trades if t["reason"] == "stop:fixed")
    stop_ady = sum(1 for t in trades if t["reason"] == "stop:adaptive")
    return {
        "n_trades": len(trades),
        "win_rate": round(len(wins) / len(rets) * 100, 2) if rets else None,
        "avg_ret": round(float(np.mean(rets)), 3) if rets else None,
        "median_ret": round(float(np.median(rets)), 3) if rets else None,
        "pl_ratio": round(float(np.mean(wins) / abs(np.mean(losses))), 3)
                    if wins and losses else None,
        "avg_max_dd": round(float(np.mean(dds)), 2) if dds else None,
        "worst_max_dd": round(float(np.max(dds)), 2) if dds else None,
        "stop_fixed_n": stop_fixed,
        "stop_adaptive_n": stop_ady,
        "signal_exit_n": sum(1 for t in trades if t["reason"] == "signal"),
        "eod_exit_n": sum(1 for t in trades if t["reason"] == "期末平仓"),
    }


def bootstrap_diff(pairs: list, n_boot: int = 2000, seed: int = 42):
    """配对自助法：pairs = [(mode_a_val, mode_b_val), ...]（逐股票配对）
    返回 (mean_diff, ci_lo, ci_hi, n_pairs)；样本不足返回 None。"""
    arr = np.array([a - b for a, b in pairs if a is not None and b is not None],
                   dtype=float)
    if len(arr) < 5:
        return None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(arr), size=(n_boot, len(arr)))
    boots = arr[idx].mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return round(float(arr.mean()), 3), round(float(lo), 3), round(float(hi), 3), len(arr)


def fmt_ci(ci) -> str:
    if ci is None:
        return "样本不足(n<5)"
    m, lo, hi, n = ci
    sig = "显著" if lo > 0 or hi < 0 else "不显著(含0)"
    return f"{m:+.2f}pct [95%CI {lo:+.2f}, {hi:+.2f}] n={n} {sig}"


def main():
    ap = argparse.ArgumentParser(description="双轨止损 vs 固定/自适应 止损回测")
    ap.add_argument("--fixed-pct", type=float, default=FIXED_PCT)
    ap.add_argument("--atr-mult", type=float, default=ATR_MULT)
    ap.add_argument("--fixed-mode", choices=["entry_pct", "prior_low"], default="entry_pct",
                    help="固定轨口径：entry_pct=进场价-X%%；prior_low=入场前N日最低价")
    ap.add_argument("--start", default="20240101")
    ap.add_argument("--max-stocks", type=int, default=0, help="调试用：限制股票数")
    args = ap.parse_args()

    smap = load_strategy_map()
    codes = list(WATCHLIST.keys())
    if args.max_stocks:
        codes = codes[: args.max_stocks]

    if not ensure_login():
        log.warning("baostock 登录失败，走 DataRouter 降级链继续")

    # results[window][mode] = {"trades": [...], "dds": [...], "per_stock": {...}}
    results = {"W1": {m: {"trades": [], "dds": [], "per_stock": {}} for m in MODES},
               "W4": {m: {"trades": [], "dds": [], "per_stock": {}} for m in MODES}}
    skipped = []   # (code, name, reason)
    fetch_fail = []

    for idx, code in enumerate(codes, 1):
        name = WATCHLIST[code]
        sname = smap.get(code)
        if sname is None:
            skipped.append((code, name, "策略映射缺失"))
            continue
        if sname not in STRATEGY_FNS:
            skipped.append((code, name, f"策略 {sname} 无回测级信号流(跳过,不静默替换)"))
            continue

        df = fetch_data(code, name, start=args.start)
        if df is None or len(df) < MIN_BARS:
            fetch_fail.append((code, name, f"数据不足({0 if df is None else len(df)}条)"))
            log.warning("数据不足跳过: %s(%s)", name, code)
            continue

        for wkey, wdf in [("W1", df), ("W4", df.tail(W4_TAIL_BARS))]:
            if len(wdf) < MIN_BARS:
                skipped.append((code, name, f"{wkey} 窗口样本不足({len(wdf)})"))
                continue
            actions = STRATEGY_FNS[sname](wdf)
            for mode in MODES:
                trades, mdd = simulate(wdf, actions, mode, fixed_pct=args.fixed_pct,
                                       fixed_mode=args.fixed_mode, atr_mult=args.atr_mult)
                for t in trades:
                    t["stock"] = code
                    t["stock_name"] = name
                slot = results[wkey][mode]
                slot["trades"].extend(trades)
                slot["dds"].append(mdd)
                slot["per_stock"][code] = {
                    "name": name, "strategy": sname, "n_trades": len(trades),
                    "win_rate": (round(sum(1 for t in trades if t["ret_pct"] > 0)
                                       / len(trades) * 100, 1) if trades else None),
                    "mean_ret": (round(float(np.mean([t["ret_pct"] for t in trades])), 2)
                                 if trades else None),
                    "max_dd": mdd,
                }
        log.info("[%d/%d] %s(%s) %s 完成", idx, len(codes), name, code, sname)

    # ---- 汇总 + 配对自助检验 ----
    ts = datetime.now().strftime("%Y-%m-%d")
    summary = {"generated_at": datetime.now().isoformat(timespec="seconds"),
               "params": {"fixed_pct": args.fixed_pct, "atr_mult": args.atr_mult,
                          "start": args.start, "w4_tail_bars": W4_TAIL_BARS,
                          "commission": COMMISSION, "slippage": SLIPPAGE,
                          "initial_capital": INITIAL_CAPITAL},
               "coverage": {"watchlist": len(codes), "skipped": skipped,
                            "fetch_fail": fetch_fail},
               "windows": {}}

    lines = []
    lines.append(f"# 双轨止损回测报告 {ts}")
    lines.append("")
    lines.append("## 方法（口径复用存量，不造第二套）")
    lines.append(f"- 入场/信号卖出：backtest_strategies 策略 action 流 + adaptive_strategy_map 分配（SSOT {len(codes)} 只）")
    lines.append(f"- 固定轨 = entry*(1-{args.fixed_pct:.0%}) 静态；自适应轨 = close[t-1] - {args.atr_mult}xATR14[t-1] 逐日重算（== calc_atr_stops 盘中口径）；双轨 = max(固定, 自适应)")
    lines.append("- 先止损后信号卖出；成交按当日收盘；成本与框架一致（佣金0.03%+滑点0.1%+本金10万）")
    lines.append("- W1 = 全窗口；W4 = 近1年（df 尾部切片，策略在切片内重新预热）")
    lines.append("- 覆盖声明见文末（跳过/缺数据均显式标注，无静默替换）")
    lines.append("")

    for wkey in ("W1", "W4"):
        lines.append(f"## {wkey} 窗口汇总")
        lines.append("")
        lines.append("| 口径 | 交易数 | 胜率 | 平均收益/笔 | 中位数 | 盈亏比 | 平均最大回撤 | 最差回撤 | 止损触发(固定/自适应) |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        wres = results[wkey]
        agg = {}
        for mode in MODES:
            st = agg_stats(wres[mode]["trades"], wres[mode]["dds"])
            agg[mode] = st
            lines.append(
                f"| {MODE_LABEL[mode]} | {st['n_trades']} | "
                f"{st['win_rate'] if st['win_rate'] is not None else '-'}% | "
                f"{st['avg_ret'] if st['avg_ret'] is not None else '-'}% | "
                f"{st['median_ret'] if st['median_ret'] is not None else '-'}% | "
                f"{st['pl_ratio'] if st['pl_ratio'] is not None else '-'} | "
                f"{st['avg_max_dd'] if st['avg_max_dd'] is not None else '-'}% | "
                f"{st['worst_max_dd'] if st['worst_max_dd'] is not None else '-'}% | "
                f"{st['stop_fixed_n']} / {st['stop_adaptive_n']} |")
        lines.append("")
        lines.append("### 配对自助检验（逐股票配对，2000 次重采样）")
        lines.append("")
        lines.append("| 对比 | 平均收益差 | 胜率差 |")
        lines.append("|---|---|---|")
        per = wres[MODES[0]]["per_stock"]
        code_list = sorted(per.keys())
        for a, b in [("dual", "fixed"), ("dual", "adaptive"),
                     ("adaptive", "fixed"), ("fixed", "none"),
                     ("adaptive", "none"), ("dual", "none")]:
            ret_pairs = [(wres[a]["per_stock"][c]["mean_ret"],
                          wres[b]["per_stock"][c]["mean_ret"]) for c in code_list]
            wr_pairs = [(wres[a]["per_stock"][c]["win_rate"],
                         wres[b]["per_stock"][c]["win_rate"]) for c in code_list]
            lines.append(f"| {MODE_LABEL[a]} − {MODE_LABEL[b]} | "
                         f"{fmt_ci(bootstrap_diff(ret_pairs))} | "
                         f"{fmt_ci(bootstrap_diff(wr_pairs))} |")
        lines.append("")

        # 逐股明细（W1 全窗口）：均值/胜率/笔数 + 止损触发次数
        if wkey == "W1":
            lines.append("### 逐股明细（W1，均值收益/笔% / 胜率% / 笔数(止损触发)）")
            lines.append("")
            lines.append("| 股票 | 策略 | "
                         + " | ".join(f"{MODE_LABEL[m]} 均值/胜率/笔数(止损)" for m in MODES)
                         + " |")
            lines.append("|---|---|---|---|---|---|")
            for c in code_list:
                row = [per[c]["name"], per[c]["strategy"]]
                for mode in MODES:
                    ps = wres[mode]["per_stock"][c]
                    stock_trades = [t for t in wres[mode]["trades"] if t.get("stock") == c]
                    stop_n = sum(1 for t in stock_trades
                                 if str(t["reason"]).startswith("stop:"))
                    mr = f"{ps['mean_ret']}" if ps["mean_ret"] is not None else "-"
                    wr = f"{ps['win_rate']}" if ps["win_rate"] is not None else "-"
                    suffix = f"({stop_n})" if mode != "none" else ""
                    row.append(f"{mr} / {wr} / {ps['n_trades']}{suffix}")
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")

        summary["windows"][wkey] = {
            "aggregate": agg,
            "per_stock": {m: wres[m]["per_stock"] for m in MODES},
            "trades": {m: wres[m]["trades"] for m in MODES},
            "dds": {m: wres[m]["dds"] for m in MODES},
        }

    lines.append("## 覆盖声明")
    lines.append(f"- SSOT 关注池 {len(codes)} 只；跳过 {len(skipped)} 只、取数失败 {len(fetch_fail)} 只（明细如下，无静默替换）")
    for c, n, r in skipped:
        lines.append(f"  - 跳过 {n}({c})：{r}")
    for c, n, r in fetch_fail:
        lines.append(f"  - 取数失败 {n}({c})：{r}")
    lines.append("- 已知口径限制：bollinger 自带 ATR 止损 action（各臂同享）；"
                 "bull_trend 部分止盈按全平；日收盘成交假设（详见脚本 docstring）")
    lines.append("- 判定说明：四臂共享同一入场信号集，属离场规则受控 A/B；"
                 "差异显著性以逐股票配对自助 95%CI 为准（上表），"
                 "不涉及『命中率 vs 随机基准』型判定，故不适用 backtest_guard 随机对照口径。")

    report_path = os.path.join(OUTPUT_DIR, f"dual_stop_{ts}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    json_path = os.path.join(OUTPUT_DIR, f"dual_stop_{ts}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, default=str)

    print("=" * 60)
    print(f"报告: {report_path}")
    print(f"JSON: {json_path}")
    for wkey in ("W1", "W4"):
        st = agg_stats(results[wkey]["dual"]["trades"], results[wkey]["dual"]["dds"])
        print(f"[{wkey}] 双轨: n={st['n_trades']} 胜率={st['win_rate']}% "
              f"平均={st['avg_ret']}% 盈亏比={st['pl_ratio']}")


if __name__ == "__main__":
    main()
