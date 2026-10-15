#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三轨止损变体回测 (2026-10-16 立项, 用户结论入档 memory/2026-10-16.md)
====================================================================
背景: 10-15 盘后止损有效性校验发现 —— 双策略通用固定止损 28 只 100% 破位
(止损位=前期高点/固定位, 偏保守) vs 自适应 ATR/EMA 动态止损 15 只 0% 破位
(贴近现价, 抗噪强)。用户要求回测评估「双轨止损」: 进场初期固定止损控尾部
风险, 持有期切换 ATR 自适应位抗噪锁利。

方法: 信号集完全相同(每票用 adaptive_strategy_map 分配的最优策略), 唯一变量
是止损口径 → 组内配对对照(自控), 四组:
  off      无额外止损 (仅策略自身买卖信号, 基线)
  fixed    纯固定: entry × (1-5%) 静态 (next_day_simulator cost×0.95 口径)
  adaptive 纯自适应: close - 2×ATR14 逐日追踪 (adaptive_dual._compute_atr_stop 口径)
  dual     双轨: 持有 <5 个交易日用固定止损, 之后切 max(entry×0.95 尾部风险底,
           前日close-2×ATR14 浮动位) —— 固定 floor 托底, ATR 分量随价移动

指标: 胜率 / 盈亏比 / 最大回撤 / 总收益 / 交易次数(换手) / 止损触发占比,
逐票配对比较 (dual vs fixed / dual vs adaptive 胜负计数)。
模拟执行一律复用生产 run_simulation (教训: 回测禁止本地复刻生产函数)。

运行: python3 analysis/backtest_stop_variants.py [--stocks 代码,代码]
输出: analysis/backtest/2026-10-16_stop_variants.{md,json}
"""

import os
import sys
import json
import datetime

# 强制 sys.path 样板 (TOOLS.md): 脚本模式下同时插入工作区根 + analysis/
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd
import pandas_ta as ta

# 复用生产回测引擎 (禁止本地复刻)
from analysis.backtest_strategies import (
    fetch_data, run_simulation, CANDIDATE_STRATEGIES,
    strategy_bollinger_atr, strategy_kdj_cci, strategy_ema_obv,
    strategy_ema_cross, strategy_macd, strategy_bull_trend_follow,
)

OUTPUT_DIR = os.path.join(WORKSPACE, "analysis", "backtest")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── 止损参数 (口径注释即出处) ──
FIXED_PCT = 0.05     # 固定止损 = entry×0.95 (next_day_simulator._build_stop_loss_map 口径)
ATR_MULT = 2.0       # 自适应 = price - 2×ATR14 (adaptive_dual._compute_atr_stop 口径)
DUAL_DAYS = 5        # 双轨切换点: 持有前 5 个交易日用固定, 之后切 ATR 追踪(只升不降)

KEY_TO_FUNC = {
    "bollinger": strategy_bollinger_atr,
    "kdj_cci": strategy_kdj_cci,
    "ema_obv": strategy_ema_obv,
    "ema_cross": strategy_ema_cross,
    "macd": strategy_macd,
    "bull_trend": strategy_bull_trend_follow,
}
MODES = ["off", "fixed", "adaptive", "dual"]


def load_assigned_strategies() -> dict:
    """读 SSOT 策略映射 (key 集合 == WATCHLIST, strategy_map_guard 门禁保障)"""
    path = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for sym, val in raw.items():
        key = val if isinstance(val, str) else (val.get("strategy") or val.get("name") or "")
        out[sym] = KEY_TO_FUNC.get(key, strategy_ema_cross)  # 未知 key -> 验证脚本同款回退
    return out


def inject_stop_sells(df, actions, mode):
    """在策略信号时间线上注入止损 SELL (组内配对: 信号集不变, 只加止损出口)

    返回 (new_actions, stop_exit_count)。止损日若策略已有同日动作则不注入
    (策略退出优先, 避免 action_map 同日键冲突)。
    """
    if mode == "off" or not actions:
        return list(actions), 0

    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    action_by_date = {a["date"]: a for a in actions}

    injected = []
    pos = None  # {"entry_idx", "entry_price", "entry_date"}
    stop_exits = 0

    for i in range(len(df)):
        row = df.iloc[i]
        dt = str(row["date"].date())
        price = float(row["close"])
        act = action_by_date.get(dt)

        if act and act["type"] == "BUY" and pos is None:
            pos = {"entry_idx": i, "entry_price": price, "entry_date": dt}
            continue
        if act and act["type"].startswith("SELL"):
            pos = None
            continue
        if pos is None or i <= pos["entry_idx"]:
            continue
        if act is not None:
            continue  # 当日已有策略动作(防御)

        # 计算止损位 —— 🔴 必须用【前一根 bar】的价位派生, 与当根 close 比较:
        # 若用当根 close 自身派生 (price - 2*ATR) 再与当根 close 比,
        # `price < price - 2*ATR` 永假 → 止损永不触发 (2026-10-16 v1 实测自纠)。
        fixed_stop = pos["entry_price"] * (1 - FIXED_PCT)
        atr_prev = float(atr.iloc[i - 1]) if not pd.isna(atr.iloc[i - 1]) else 0.0
        prev_close = float(df.iloc[i - 1]["close"])
        if mode == "fixed":
            stop = fixed_stop
        elif mode == "adaptive":
            if atr_prev <= 0:
                continue
            stop = prev_close - ATR_MULT * atr_prev  # 逐日浮动, 随价格双向移动(抗噪)
        else:  # dual
            days_held = i - pos["entry_idx"]
            if days_held < DUAL_DAYS or atr_prev <= 0:
                stop = fixed_stop
            else:
                stop = max(fixed_stop, prev_close - ATR_MULT * atr_prev)
                # 固定 floor 控尾部风险, ATR 分量随价上移锁利

        if price < stop:
            injected.append({
                "date": dt, "type": "SELL", "price": price,
                "reason": (f"{mode}止损: 入场¥{pos['entry_price']:.2f} "
                           f"止损¥{stop:.2f} (第{i - pos['entry_idx']}日)"),
            })
            stop_exits += 1
            pos = None

    if not injected:
        return list(actions), 0
    merged = list(actions) + injected
    merged.sort(key=lambda a: (a["date"], 0 if a["type"] == "BUY" else 1))
    return merged, stop_exits


def run_one(df, sfunc, mode):
    base_actions = sfunc(df)
    actions, stop_exits = inject_stop_sells(df, base_actions, mode)
    m = run_simulation(df, actions)
    if "error" in m:
        return m
    m["signals"] = len([a for a in base_actions if a["type"] == "BUY"])
    m["stop_exits"] = stop_exits
    return m


def main(stocks=None):
    from analysis.fib_extension_scan import WATCHLIST  # SSOT 36 只
    if stocks is None:
        stocks = list(WATCHLIST.keys())

    assigned = load_assigned_strategies()
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"🔍 三轨止损变体回测 | {len(stocks)} 只 x {len(MODES)} 组 | "
          f"fixed={FIXED_PCT:.0%} / adaptive=-{ATR_MULT}xATR / dual切换={DUAL_DAYS}日")

    results = {}
    for sym in stocks:
        name = WATCHLIST.get(sym, "未知")
        sfunc = assigned.get(sym, strategy_ema_cross)
        sname = next((n for n, f in CANDIDATE_STRATEGIES if f is sfunc), "ema_cross")
        df = fetch_data(sym, name)
        if df is None:
            results[sym] = {"name": name, "strategy": sname, "error": "数据不足"}
            print(f"  ❌ {name}({sym}) 数据不足, 跳过")
            continue
        row_out = {"name": name, "strategy": sname, "modes": {}}
        for mode in MODES:
            try:
                row_out["modes"][mode] = run_one(df, sfunc, mode)
            except Exception as e:
                row_out["modes"][mode] = {"error": str(e)}
        results[sym] = row_out
        parts = []
        for mode in MODES:
            m = row_out["modes"][mode]
            parts.append(f"{mode}={m.get('total_return_pct', 'ERR')}%")
        print(f"  ✅ {name}({sym}) [{sname}] " + " | ".join(parts))

    # ── 聚合 ──
    ok = {s: r for s, r in results.items() if "error" not in r}
    agg = {}
    for mode in MODES:
        rows = [r["modes"][mode] for r in ok.values()
                if "error" not in r["modes"].get(mode, {"error": 1})]
        if not rows:
            agg[mode] = {"n": 0}
            continue
        agg[mode] = {
            "n": len(rows),
            "avg_return_pct": round(float(np.mean([m.get("total_return_pct", 0) for m in rows])), 2),
            "median_return_pct": round(float(np.median([m.get("total_return_pct", 0) for m in rows])), 2),
            "avg_win_rate_pct": round(float(np.mean([m.get("win_rate_pct", 0) for m in rows])), 1),
            "avg_profit_loss_ratio": round(float(np.mean([m.get("profit_loss_ratio", 0) for m in rows])), 2),
            "avg_max_drawdown_pct": round(float(np.mean([m.get("max_drawdown_pct", 0) for m in rows])), 2),
            "avg_trades": round(float(np.mean([m.get("total_trades", 0) for m in rows])), 1),
            "stop_exits_total": int(sum(m.get("stop_exits", 0) for m in rows)),
            "stocks_better_than_off": int(sum(
                1 for s, r in ok.items()
                if "error" not in r["modes"].get("off", {"error": 1})
                and r["modes"][mode].get("total_return_pct", -1e9)
                > r["modes"]["off"].get("total_return_pct", -1e9))),
            "stocks_worse_than_off": int(sum(
                1 for s, r in ok.items()
                if "error" not in r["modes"].get("off", {"error": 1})
                and r["modes"][mode].get("total_return_pct", 1e9)
                < r["modes"]["off"].get("total_return_pct", 1e9))),
        }

    # 配对胜负 (总收益逐票)
    def paired(a, b):
        win = loss = tie = 0
        for r in ok.values():
            ma, mb = r["modes"].get(a, {}), r["modes"].get(b, {})
            if "error" in ma or "error" in mb:
                continue
            da = ma.get("total_return_pct", 0) - mb.get("total_return_pct", 0)
            if da > 0.01:
                win += 1
            elif da < -0.01:
                loss += 1
            else:
                tie += 1
        return {"win": win, "loss": loss, "tie": tie}

    pairs = {
        "dual_vs_fixed": paired("dual", "fixed"),
        "dual_vs_adaptive": paired("dual", "adaptive"),
        "fixed_vs_off": paired("fixed", "off"),
        "adaptive_vs_off": paired("adaptive", "off"),
    }

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = {
        "generated_at": stamp,
        "params": {"fixed_pct": FIXED_PCT, "atr_mult": ATR_MULT, "dual_days": DUAL_DAYS},
        "n_stocks": len(ok),
        "aggregate": agg,
        "paired_returns": pairs,
        "per_stock": results,
    }
    json_path = os.path.join(OUTPUT_DIR, f"{today}_stop_variants.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)

    # ── Markdown 报告 ──
    L = [f"# 三轨止损变体回测 {today}", "",
         f"生成: {stamp} | {len(ok)}/{len(stocks)} 只有效 (SSOT {len(WATCHLIST)} 只口径) | "
         f"信号集同组配对(每票 adaptive_strategy_map 分配策略), 唯一变量=止损口径。",
         f"参数: 固定=-{FIXED_PCT:.0%}(entry×0.95) / 自适应=close-2×ATR14 / "
         f"双轨=前{DUAL_DAYS}日固定后切 max(entry固定底, 前日close-2×ATR浮动)。", "",
         "## 聚合对比", "",
         "| 组 | n | 平均收益% | 中位收益% | 胜率% | 盈亏比 | 最大回撤% | 交易数 | 止损出场 | 优于/劣于无止损(票) |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for mode in MODES:
        a = agg.get(mode, {})
        L.append(f"| {mode} | {a.get('n', 0)} | {a.get('avg_return_pct', '-')} | "
                 f"{a.get('median_return_pct', '-')} | {a.get('avg_win_rate_pct', '-')} | "
                 f"{a.get('avg_profit_loss_ratio', '-')} | {a.get('avg_max_drawdown_pct', '-')} | "
                 f"{a.get('avg_trades', '-')} | {a.get('stop_exits_total', '-')} | "
                 f"{a.get('stocks_better_than_off', '-')}/{a.get('stocks_worse_than_off', '-')} |")
    L += ["", "## 逐票配对胜负 (总收益, win/loss/tie)", "",
          "| 对照 | win | loss | tie |", "|---|---|---|---|"]
    for k, v in pairs.items():
        L.append(f"| {k} | {v['win']} | {v['loss']} | {v['tie']} |")
    L += ["", "## 逐票明细 (总收益%)", "",
          "| 股票 | 策略 | off | fixed | adaptive | dual |", "|---|---|---|---|---|---|"]
    for sym in stocks:
        r = results.get(sym)
        if not r or "error" in r:
            L.append(f"| {r.get('name', sym) if r else sym} | - | ❌数据不足 | - | - | - |")
            continue
        cells = []
        for mode in MODES:
            m = r["modes"].get(mode, {})
            cells.append("ERR" if "error" in m else f"{m.get('total_return_pct', 0)}")
        L.append(f"| {r['name']}({sym}) | {r['strategy']} | " + " | ".join(cells) + " |")
    L += ["", "## 结论口径", "",
          "- 组内配对: 同一信号集, 止损是唯一变量 → 差异直接归因止损口径 (无随机基准需求, 自控对照)。",
          "- fixed 100% 破位 vs adaptive 0% 破位 (10-15 实盘校验) 的口径差异, 在本回测体现为 stop_exits 差异。",
          "- 结果并入 DSR/护栏评估口径时, 双轨是否采纳以 paired 胜负 + 聚合回撤共同判定。", ""]
    md_path = os.path.join(OUTPUT_DIR, f"{today}_stop_variants.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("\n" + "=" * 70)
    for mode in MODES:
        a = agg.get(mode, {})
        print(f"{mode:9s} 收益均值 {a.get('avg_return_pct', '-')}% | 中位 "
              f"{a.get('median_return_pct', '-')}% | 胜率 {a.get('avg_win_rate_pct', '-')}% | "
              f"回撤 {a.get('avg_max_drawdown_pct', '-')}% | 止损出场 {a.get('stop_exits_total', '-')}")
    print(f"配对: dual vs fixed {pairs['dual_vs_fixed']} | dual vs adaptive {pairs['dual_vs_adaptive']}")
    print(f"\n📄 {md_path}\n📄 {json_path}")
    return out


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--stocks", help="股票代码逗号分隔 (默认 SSOT 36 只)")
    args = parser.parse_args()
    main(stocks=args.stocks.split(",") if args.stocks else None)
