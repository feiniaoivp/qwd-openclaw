#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
海龟策略臂：唐奇安通道突破（P2，2026-11-01，借鉴构建计划 turtle_trading）
==========================================================================

经典海龟要素与本仓框架的对照取舍：
- 入场: 收盘价突破前 N1=20 根 bar 的最高高点（唐奇安上轨，不含当根 —— shift(1) 防前视）
- 出场: 收盘价跌破前 N2=10 根 bar 的最低低点（唐奇安下轨，不含当根）
- ATR 单位仓位 / 金字塔加仓 / 2% 单位风险: **属组合层职责**（position_sizer /
  portfolio_risk 已覆盖），个股臂必须与既有 5 臂同为全进全出 —— 臂间对照
  只允许信号逻辑一个自由度（多臂减同一基准纪律，2026-10-18）。
- S2 系统(55/20) 留作参数变体，不在 v1。
- 与趋势族(EMA金叉/MACD)的差异化价值：突破型入场的收益结构独立于均线交叉，
  且本仓 07-26 起无纯突破臂。

自测（合成趋势段验证突破/退出方向 + 状态机 + 无前视）：
  python3 analysis/turtle_strategy.py    （退出码 0=过 / 1=违规）
"""

import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

N_ENTRY = int(os.environ.get("TURTLE_N_ENTRY", "20"))
N_EXIT = int(os.environ.get("TURTLE_N_EXIT", "10"))


def strategy_turtle_donchian(df, n_entry=N_ENTRY, n_exit=N_EXIT):
    """海龟突破臂：唐奇安 20/10，全进全出（仓位在组合层）。"""
    df = df.copy().reset_index(drop=True)
    # shift(1)：通道由「前 N 根」构成，不含当根 —— 突破判定无前视
    # 外部行情列统一 to_numeric（空串 coerce NaN，NaN 参与 rolling 自动跳过）
    hh = pd.to_numeric(df["high"], errors="coerce").rolling(n_entry).max().shift(1)
    ll = pd.to_numeric(df["low"], errors="coerce").rolling(n_exit).min().shift(1)

    actions = []
    position = False
    for i in range(n_entry, len(df)):
        row = df.iloc[i]
        hhv, llv = hh.iloc[i], ll.iloc[i]
        if pd.isna(hhv) or pd.isna(llv):
            continue
        dt = str(row["date"].date())
        price = float(row["close"])
        if not position and price > float(hhv):
            actions.append({"date": dt, "type": "BUY", "price": price,
                            "reason": f"海龟突破: close({price:.2f})>前{n_entry}日高点({float(hhv):.2f})"})
            position = True
        elif position and price < float(llv):
            actions.append({"date": dt, "type": "SELL", "price": price,
                            "reason": f"海龟退出: close({price:.2f})<前{n_exit}日低点({float(llv):.2f})"})
            position = False
    return actions


# ════════════════════════════════════════
# 自测
# ════════════════════════════════════════

def _make_synth_df(n=600, seed=11):
    """前段阴跌震荡 + 中段强趋势（触发突破）+ 尾段崩塌（触发退出）"""
    rng = np.random.default_rng(seed)
    rets = rng.normal(-0.0002, 0.010, n)
    rets[150:400] += 0.006   # 强趋势段
    rets[400:520] -= 0.008   # 崩塌段
    close = 30 * np.cumprod(1 + rets)
    rng2 = np.random.default_rng(seed + 1)
    high = close * (1 + np.abs(rng2.normal(0, 0.008, n)))
    low = close * (1 - np.abs(rng2.normal(0, 0.008, n)))
    openp = low + (high - low) * rng2.random(n)
    dates = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame({"date": dates, "open": openp, "close": close,
                         "high": high, "low": low,
                         "volume": rng2.integers(5e6, 5e7, n).astype(float)})


def _selftest() -> int:
    failures = []
    df = _make_synth_df()
    acts = strategy_turtle_donchian(df)

    # 1) 状态机：BUY/SELL 交替、价格与当日收盘一致
    close_map = {str(d.date()): float(c) for d, c in zip(df["date"], df["close"])}
    pos = False
    for a in acts:
        if abs(close_map[a["date"]] - a["price"]) > 1e-9:
            failures.append(f"价格与当日收盘不符: {a['date']}")
            break
        if a["type"] == "BUY" and pos:
            failures.append(f"持仓中重复 BUY: {a['date']}")
            break
        if a["type"] == "SELL" and not pos:
            failures.append(f"空仓中出现 SELL: {a['date']}")
            break
        pos = (a["type"] == "BUY")
    if pos:
        failures.append("序列结束时仍持仓（合成尾段崩塌应已触发退出）")

    # 2) 突破方向正确性：趋势注入段(150~400)内应发生入场，崩塌段(400~520)内应发生退出
    idx_map = {str(d.date()): i for i, d in enumerate(df["date"])}
    buys = [idx_map[a["date"]] for a in acts if a["type"] == "BUY"]
    sells = [idx_map[a["date"]] for a in acts if a["type"] == "SELL"]
    if not any(120 <= b <= 460 for b in buys):
        failures.append(f"趋势段(120~460)内未发生入场: buys={buys[:5]}")
    if sells and sells[-1] < 380:
        failures.append(f"末次退出过早: bar {sells[-1]}（崩塌段 400~520 应触发退出）")

    # 3) 无前视硬证明：截断前 300 根重跑，重叠动作必须一致
    cut = 300
    acts_cut = strategy_turtle_donchian(df.iloc[:cut].copy())
    cut_end = str(df.iloc[cut - 1]["date"].date())
    overlap = [a for a in acts if a["date"] <= cut_end]
    if overlap != acts_cut:
        failures.append("截断攻击失败: 重叠区间动作不一致（存在前视）")

    # 4) 退出参数改变 → 行为改变（参数确实生效，非摆设）
    acts_alt = strategy_turtle_donchian(df, n_entry=20, n_exit=55)
    if acts_alt == acts:
        failures.append("n_exit 参数无效果（退出通道疑似未生效）")

    if failures:
        print("❌ turtle_strategy 自测失败:")
        for x in failures:
            print("   -", x)
        return 1
    print(f"✅ turtle_strategy 自测通过: 状态机/方向正确性/截断攻击/参数生效 "
          f"共 4 组 | 合成数据动作 {len(acts)} 个 (BUY {len(buys)}/SELL {len(sells)})")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
