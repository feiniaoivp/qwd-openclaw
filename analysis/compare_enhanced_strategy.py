#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
综合增强策略 vs 现有5策略 —— 对比回测
=====================================
新增策略 `enhanced`：融合新指标
  - 波动控制：ATR 止损距离(2×/3×ATR)、布林带宽(挤压→突破)
  - 支撑阻力：近期20日高低点、斐波那契回撤(0.382/0.5/0.618)
  - 趋势过滤：EMA12/26 + MACD 方向
买入：趋势向上 且 (布林挤压后突破 或 突破20日高点放量)
卖出：跌破 2×ATR 止损 或 跌破布林下轨

数据源：复用 backtest_strategies.fetch_data（baostock→akshare新浪），前复权。
对比窗口：默认近1.5年(20240101~今)，可用 BT_START 覆盖。
输出：analysis/backtest/YYYY-MM-DD_增强策略对比.md

用法：
  BT_START=20240101 python3 analysis/compare_enhanced_strategy.py
"""
import os, sys, time
from datetime import datetime

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 复用现有回测框架的数据抓取与模拟器
import analysis.backtest_strategies as bt

try:
    import pandas_ta as ta
except Exception:
    ta = None


# ════════════════ 综合增强策略回测版 ════════════════
def strategy_enhanced(df):
    """综合增强：趋势 + 波动控制(ATR/布林带宽) + 支撑阻力(高低点/斐波那契)。"""
    df = df.copy()
    if ta is None:
        return []
    close = df["close"]; high = df["high"]; low = df["low"]
    # 趋势指标
    df["EMA12"] = ta.ema(close, length=12)
    df["EMA26"] = ta.ema(close, length=26)
    macd = ta.macd(close, fast=12, slow=26, signal=9)
    df["MACD"] = macd["MACD_12_26_9"]
    df["MACDs"] = macd["MACDs_12_26_9"]
    # 波动控制
    df["ATR14"] = ta.atr(high, low, close, length=14)
    bb = ta.bbands(close, length=20, std=2.0)
    df["BBU"] = bb[[c for c in bb.columns if "BBU" in c.upper()][0]]
    df["BBL"] = bb[[c for c in bb.columns if "BBL" in c.upper()][0]]
    df["BBM"] = bb[[c for c in bb.columns if "BBM" in c.upper()][0]]
    df["BW"] = (df["BBU"] - df["BBL"]) / df["BBM"]
    df["BW_mean"] = df["BW"].rolling(50).mean()

    actions = []
    position = False
    for i in range(40, len(df)):
        row = df.iloc[i]; prev = df.iloc[i - 1]
        dt = str(row["date"].date()); price = float(row["close"])
        ema12, ema26 = float(row["EMA12"]), float(row["EMA26"])
        m, s = float(row["MACD"]), float(row["MACDs"])
        trend_up = ema12 > ema26 and m > s
        # 布林带宽：当前 < 均值的92% → 挤压
        bw_now = float(row["BW"]); bw_mean = float(row["BW_mean"])
        squeeze = (pd.notna(bw_mean) and bw_now < bw_mean * 0.92)
        # 布林突破（用前日 vs 今日上/下轨）
        break_up = (float(prev["close"]) <= float(prev["BBU"])) and (price > float(row["BBU"]))
        break_down = (float(prev["close"]) >= float(prev["BBL"])) and (price < float(row["BBL"]))
        # ATR 止损
        atr_v = float(row["ATR14"]) if pd.notna(row["ATR14"]) else price * 0.03
        stop_2atr = price - 2 * atr_v
        # 近期20日高低点
        look = slice(i - 19, i + 1)
        hi20 = float(high.iloc[look].max()); lo20 = float(low.iloc[look].min())
        # 斐波那契(近60日)
        fl = slice(max(0, i - 59), i + 1)
        fhi, flo = float(high.iloc[fl].max()), float(low.iloc[fl].min())
        rng = fhi - flo
        fib618 = fhi - rng * 0.618 if rng > 0 else price
        vol_ok = float(row["volume"]) > float(df["volume"].iloc[max(0, i - 5):i].mean())

        if not position and trend_up:
            if squeeze and break_up:
                actions.append({"date": dt, "type": "BUY", "price": price,
                                "reason": f"布林挤压后向上突破({price:.2f})"})
                position = True
            elif price > hi20 and vol_ok:
                actions.append({"date": dt, "type": "BUY", "price": price,
                                "reason": f"突破20日高点{hi20:.2f}放量"})
                position = True
            elif break_up and not squeeze:
                actions.append({"date": dt, "type": "BUY", "price": price,
                                "reason": f"布林上轨突破({price:.2f})"})
                position = True
        elif position:
            # 止损/破位卖出
            sell_flag = False; sr = ""
            if price < min(stop_2atr, fib618):
                sell_flag = True; sr = f"跌破ATR止损/斐波那契({min(stop_2atr, fib618):.2f})"
            elif break_down or price < lo20:
                sell_flag = True; sr = f"破位({lo20:.2f})"
            elif not trend_up and (s < m):  # 趋势转弱且 MACD 转空
                sell_flag = True; sr = "趋势转弱"
            if sell_flag:
                actions.append({"date": dt, "type": "SELL", "price": price, "reason": sr})
                position = False

    return actions


# ════════════════ 汇总分析 ════════════════
def analyze():
    bt_stocks = bt.STOCKS
    all_strats = bt.ALL_STRATEGIES + [("综合增强(ATR+布林+斐波那契)", strategy_enhanced)]
    stats = {s[0]: {"total_return_pct": [], "sharpe_ratio": [], "max_drawdown_pct": [],
                    "win_rate_pct": [], "total_trades": []} for s in all_strats}

    print(f"📊 增强策略对比回测 {datetime.now():%Y-%m-%d}  窗口 {bt.START_DATE}~{bt.END_DATE}")
    print(f"   覆盖 {len(bt_stocks)} 只股票 × {len(all_strats)} 个策略\n")

    for sym, name in bt_stocks:
        df = bt.fetch_data(sym, name)
        if df is None:
            print(f"  ⚠️ {name}: 数据缺失跳过")
            continue
        for sname, sfunc in all_strats:
            try:
                actions = sfunc(df)
                perf = bt.run_simulation(df, actions)
                stats[sname]["total_return_pct"].append(perf["total_return_pct"])
                stats[sname]["sharpe_ratio"].append(perf["sharpe_ratio"])
                stats[sname]["max_drawdown_pct"].append(perf["max_drawdown_pct"])
                stats[sname]["win_rate_pct"].append(perf["win_rate_pct"])
                stats[sname]["total_trades"].append(perf["total_trades"])
            except Exception as e:
                print(f"  ⚠️ {name} {sname} 回测异常: {str(e)[:50]}")
                stats[sname]["total_return_pct"].append(None)

    # 平均汇总
    print("\n=== 全池平均绩效（越高越好：收益/夏普/胜率/盈亏比） ===")
    rows = []
    for sname, _ in all_strats:
        r = {k: np.nanmean(stats[sname][k]) if stats[sname][k] else None for k in stats[sname]}
        rows.append((sname, r))
    # 按平均收益降序
    rows.sort(key=lambda x: -(x[1]["total_return_pct"] or -999))
    print(f"{'策略':<26}{'平均收益%':>10}{'平均夏普':>10}{'平均回撤%':>10}{'平均胜率%':>10}{'总交易次数':>10}")
    for sname, r in rows:
        navg = int(np.nansum(stats[sname]["total_trades"])) if stats[sname]["total_trades"] else 0
        print(f"{sname:<26}{r['total_return_pct'] if r['total_return_pct'] is not None else 0:>10.1f}"
              f"{r['sharpe_ratio'] if r['sharpe_ratio'] is not None else 0:>10.3f}"
              f"{r['max_drawdown_pct'] if r['max_drawdown_pct'] is not None else 0:>10.1f}"
              f"{r['win_rate_pct'] if r['win_rate_pct'] is not None else 0:>10.1f}{navg:>10}")

    # 生成 Markdown
    out = os.path.join(WORKSPACE, "analysis", "backtest", f"{datetime.now():%Y-%m-%d}_增强策略对比.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# 增强策略对比回测 {datetime.now():%Y-%m-%d}\n\n")
        f.write(f"窗口 {bt.START_DATE}~{bt.END_DATE} | 覆盖 {len(bt_stocks)} 只\n\n")
        f.write("| 策略 | 平均收益% | 平均夏普 | 平均回撤% | 平均胜率% | 总交易 |\n")
        f.write("|:--|--:|--:|--:|--:|--:|\n")
        for sname, r in rows:
            navg = int(np.nansum(stats[sname]["total_trades"])) if stats[sname]["total_trades"] else 0
            f.write(f"| {sname} | {r['total_return_pct'] or 0:.1f} | {r['sharpe_ratio'] or 0:.3f} | "
                    f"{r['max_drawdown_pct'] or 0:.1f} | {r['win_rate_pct'] or 0:.1f} | {navg} |\n")
    print(f"\n📄 报告: {out}")


if __name__ == "__main__":
    analyze()
