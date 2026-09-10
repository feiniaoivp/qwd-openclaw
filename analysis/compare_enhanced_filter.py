#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强指标 → 趋势信号过滤器 对比回测
==================================
不改变趋势跟踪策略(MACD/EMA金叉)的原始信号方向，只在信号发生时用「增强指标」
做二次确认过滤：不合格信号被跳过。对比 原始 vs 增强过滤 的绩效差异，判断过滤器
能否在保持收益的同时提升夏普/降回撤（提"可参考性"）。

过滤器（BUY时确认，满足其一）：
  F1 布林挤压后向上突破（波动收缩→爆发）
  F2 突破20日高点 且 放量（动量确认）
  F3 价格处于近60日斐波那契 0.382~0.618 区间企稳回升（回撤支撑买点）
SELL：跌破 2×ATR 止损 或 跌破布林下轨 → 提前离场（止盈/止损，减少回撤）

输出：analysis/backtest/YYYY-MM-DD_增强过滤器对比.md
用法：python3 analysis/compare_enhanced_filter.py
"""
import os, sys
from datetime import datetime

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import analysis.backtest_strategies as bt
try:
    import pandas_ta as ta
except Exception:
    ta = None


def _add_enhanced_cols(df):
    """附加增强指标列：ATR/布林带宽/20日高低点/斐波那契。返回copy。"""
    df = df.copy()
    df["ATR14"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    bb = ta.bbands(df["close"], length=20, std=2.0)
    df["BBU"] = bb[[c for c in bb.columns if "BBU" in c.upper()][0]]
    df["BBL"] = bb[[c for c in bb.columns if "BBL" in c.upper()][0]]
    df["BBM"] = bb[[c for c in bb.columns if "BBM" in c.upper()][0]]
    df["BW"] = (df["BBU"] - df["BBL"]) / df["BBM"]
    df["BW_mean"] = df["BW"].rolling(50).mean()
    # 20日高低点
    df["HI20"] = df["high"].rolling(20).max()
    df["LO20"] = df["low"].rolling(20).min()
    return df


def _fib_levels(df, i, look=60):
    """近look日斐波那契回撤位。返回 (retr_ratio 0~1, fib382, fib618)。0=顶,1=底。"""
    lo = max(0, i - look + 1)
    fhi = float(df["high"].iloc[lo:i + 1].max())
    flo = float(df["low"].iloc[lo:i + 1].min())
    rng = fhi - flo
    if rng <= 0:
        return 0.5, float(df["close"].iloc[i]), float(df["close"].iloc[i])
    retr = (fhi - float(df["close"].iloc[i])) / rng
    fib382 = fhi - rng * 0.382
    fib618 = fhi - rng * 0.618
    return retr, fib382, fib618


def _buy_confirmed(df, i, price):
    """增强确认：F1 挤压突破 / F2 突破高点放量 / F3 斐波那契支撑企稳。"""
    row = df.iloc[i]; prev = df.iloc[i - 1]
    # F1: 布林带宽挤压后向上突破
    bw_now = float(row["BW"]); bw_mean = float(row["BW_mean"])
    squeeze = pd.notna(bw_mean) and bw_now < bw_mean * 0.92
    break_up = float(prev["close"]) <= float(prev["BBU"]) and price > float(row["BBU"])
    if squeeze and break_up:
        return True, f"F1挤压突破(带宽{bw_now:.3f}<均值)"
    # F2: 突破20日高点且放量
    hi20 = float(row["HI20"])
    vol_ok = float(row["volume"]) > float(df["volume"].iloc[max(0, i - 5):i].mean())
    if price > hi20 and vol_ok:
        return True, f"F2突破20日高{hi20:.2f}放量"
    # F3: 价格在斐波那契0.382~0.618区间企稳回升(价格高于前一日)
    retr, fib382, fib618 = _fib_levels(df, i)
    prev_price = float(df["close"].iloc[i - 1])
    if fib618 <= price <= fib382 and price > prev_price:
        return True, f"F3斐波那契支撑({0.382:.0%}~{0.618:.0%})企稳"
    return False, ""


def _sell_confirmed(df, i, price, entry_price):
    """SELL处理：跌破 2×ATR 止损. 返回(是否应立即离场, 原因)。"""
    row = df.iloc[i]
    atr_v = float(row["ATR14"]) if pd.notna(row["ATR14"]) else price * 0.03
    stop = price - 2 * atr_v
    break_down = float(df["close"].iloc[i - 1]) >= float(row["BBL"]) and price < float(row["BBL"])
    if price < min(stop, entry_price):
        return True, f"跌破ATR止损({min(stop, entry_price):.2f})"
    if break_down:
        return True, f"跌破布林下轨({float(row['BBL']):.2f})"
    return False, ""


def strategy_macd_filtered(df):
    """纯MACD + 增强过滤：BUY需确认, SELL可ATR/布林提前离场。"""
    df = _add_enhanced_cols(df)
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    df["MACD"] = macd["MACD_12_26_9"]; df["MACDs"] = macd["MACDs_12_26_9"]
    actions = []; position = False; entry = 0.0
    for i in range(1, len(df)):
        row = df.iloc[i]; prev = df.iloc[i - 1]
        dt = str(row["date"].date()); price = float(row["close"])
        m, s = float(row["MACD"]), float(row["MACDs"])
        pm, ps = float(prev["MACD"]), float(prev["MACDs"])
        if not position and pm <= ps and m > s:
            ok, why = _buy_confirmed(df, i, price)
            if ok:
                actions.append({"date": dt, "type": "BUY", "price": price, "reason": f"MACD金叉+{why}"})
                position = True; entry = price
        elif position:
            s_ok, s_reason = _sell_confirmed(df, i, price, entry)
            cross_down = pm >= ps and m < s
            if s_ok:
                actions.append({"date": dt, "type": "SELL", "price": price, "reason": s_reason})
                position = False; entry = 0.0
            elif cross_down:
                actions.append({"date": dt, "type": "SELL", "price": price, "reason": "MACD死叉"})
                position = False; entry = 0.0
    return actions


def strategy_ema_filtered(df):
    """EMA12/26 + 增强过滤。"""
    df = _add_enhanced_cols(df)
    df["EMA12"] = ta.ema(df["close"], length=12); df["EMA26"] = ta.ema(df["close"], length=26)
    actions = []; position = False; entry = 0.0
    for i in range(1, len(df)):
        row = df.iloc[i]; prev = df.iloc[i - 1]
        dt = str(row["date"].date()); price = float(row["close"])
        e12, e26 = float(row["EMA12"]), float(row["EMA26"])
        pe12, pe26 = float(prev["EMA12"]), float(prev["EMA26"])
        cross_up = pe12 <= pe26 and e12 > e26
        cross_down = pe12 >= pe26 and e12 < e26
        if not position and cross_up:
            ok, why = _buy_confirmed(df, i, price)
            if ok:
                actions.append({"date": dt, "type": "BUY", "price": price, "reason": f"EMA金叉+{why}"})
                position = True; entry = price
        elif position:
            s_ok, s_reason = _sell_confirmed(df, i, price, entry)
            if s_ok:
                actions.append({"date": dt, "type": "SELL", "price": price, "reason": s_reason})
                position = False; entry = 0.0
            elif cross_down:
                actions.append({"date": dt, "type": "SELL", "price": price, "reason": "EMA死叉"})
                position = False; entry = 0.0
    return actions


def main():
    stocks = bt.STOCKS
    strategies = [
        ("纯MACD(原始)", bt.strategy_macd),
        ("纯MACD+增强过滤", strategy_macd_filtered),
        ("EMA12/26(原始)", bt.strategy_ema_cross),
        ("EMA12/26+增强过滤", strategy_ema_filtered),
    ]
    agg = {s[0]: {"tr": [], "sharpe": [], "mdd": [], "wr": [], "n": []} for s in strategies}
    print(f"🔬 增强过滤器对比回测 {datetime.now():%Y-%m-%d}  窗口{bt.START_DATE}~{bt.END_DATE}")
    print(f"   覆盖 {len(stocks)} 只\n")
    for sym, name in stocks:
        df = bt.fetch_data(sym, name)
        if df is None:
            continue
        for sname, sfunc in strategies:
            try:
                acts = sfunc(df)
                p = bt.run_simulation(df, acts)
                agg[sname]["tr"].append(p["total_return_pct"])
                agg[sname]["sharpe"].append(p["sharpe_ratio"])
                agg[sname]["mdd"].append(p["max_drawdown_pct"])
                agg[sname]["wr"].append(p["win_rate_pct"])
                agg[sname]["n"].append(p["total_trades"])
            except Exception as e:
                print(f"  ⚠️ {name} {sname}: {str(e)[:40]}")

    print(f"\n{'策略':<20}{'平均收益%':>10}{'平均夏普':>10}{'平均回撤%':>10}{'平均胜率%':>10}{'总交易':>8}")
    for sname, _ in strategies:
        tr = np.nanmean(agg[sname]["tr"]) if agg[sname]["tr"] else 0
        sh = np.nanmean(agg[sname]["sharpe"]) if agg[sname]["sharpe"] else 0
        mdd = np.nanmean(agg[sname]["mdd"]) if agg[sname]["mdd"] else 0
        wr = np.nanmean(agg[sname]["wr"]) if agg[sname]["wr"] else 0
        n = int(np.sum(agg[sname]["n"]))
        print(f"{sname:<20}{tr:>10.1f}{sh:>10.3f}{mdd:>10.1f}{wr:>10.1f}{n:>8}")

    out = os.path.join(WORKSPACE, "analysis", "backtest", f"{datetime.now():%Y-%m-%d}_增强过滤器对比.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# 增强过滤器对比 {datetime.now():%Y-%m-%d}\n\n窗口{bt.START_DATE}~{bt.END_DATE} | {len(stocks)}只\n\n")
        f.write("| 策略 | 平均收益% | 平均夏普 | 平均回撤% | 平均胜率% | 总交易 |\n|:--|--:|--:|--:|--:|--:|\n")
        for sname, _ in strategies:
            tr = np.nanmean(agg[sname]["tr"]) if agg[sname]["tr"] else 0
            sh = np.nanmean(agg[sname]["sharpe"]) if agg[sname]["sharpe"] else 0
            mdd = np.nanmean(agg[sname]["mdd"]) if agg[sname]["mdd"] else 0
            wr = np.nanmean(agg[sname]["wr"]) if agg[sname]["wr"] else 0
            n = int(np.sum(agg[sname]["n"]))
            f.write(f"| {sname} | {tr:.1f} | {sh:.3f} | {mdd:.1f} | {wr:.1f} | {n} |\n")
    print(f"\n📄 报告: {out}")


if __name__ == "__main__":
    main()
