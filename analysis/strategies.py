#!/usr/bin/env python3
"""
strategies — 共享策略库（2026-09-11 抽取）
====================================================
背景: 中联重科「综合最优(EMA+MACD+RSI)」策略曾在 3 个脚本中重复实现
      (service.py / close_scan_v2.py / zhonglian_monitor.py), 导致:
        ① 修改必须同步三处, 极易漂移;
        ② 2026-09-11 发现三处都带有同一「卖出信号卡死」bug
           (sig2_sell 用 `close < EMA26` 的【状态】而非【事件】,
            导致下跌趋势中 sell 恒为 True, 实测连续 26 个交易日不复位)。

本模块将中联重科双策略统一为单一实现, 三个调用方改为引用此模块,
从根本上消除漂移风险。

设计要点:
  - 【事件 vs 状态】卖出信号只在「新触发」当天为 True:
      · MACD 死叉 (事件): 当日发生交叉
      · 下穿 EMA26 (事件): 当日收盘跌破且前一日在均线上方
    持续处于 EMA26 下方时 sell=False, 由 below_ema26 状态字段表达"不宜买入"。
  - 返回结构保持向后兼容 (含 buy/sell/sell_event/below_ema26/desc)。
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

# 策略名称常量 (避免各处硬编码字符串漂移)
STRATEGY1_NAME = "MACD+RSI<50"
STRATEGY2_NAME = "综合最优(EMA+MACD+RSI)"

# 卖出事件类型
SELL_EVENT_MACD_DEAD_CROSS = "MACD死叉"
SELL_EVENT_BREAK_EMA26 = "下穿EMA26"


def compute_indicators(df: pd.DataFrame) -> dict:
    """计算中联重科策略所需全部指标 (MACD / EMA / RSI)。

    Args:
        df: 需包含 close 列, 且至少 2 行 (用于判断交叉事件)。

    Returns:
        dict, 含各指标序列的末值与交叉事件布尔标志。
    """
    if df is None or len(df) < 2:
        raise ValueError("compute_indicators: 数据不足 (需至少 2 行)")

    close = df["close"]
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    # ── MACD ──
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()

    # ── RSI(14) ──
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    rsi_now = float(rsi.iloc[-1])

    # ── 交叉事件 (事件语义: 仅当日发生交叉时为 True) ──
    macd_bull = (dif.iloc[-1] > dea.iloc[-1]) and (dif.iloc[-2] <= dea.iloc[-2])   # 金叉
    macd_bear = (dif.iloc[-1] < dea.iloc[-1]) and (dif.iloc[-2] >= dea.iloc[-2])   # 死叉

    # ── 下穿 EMA26 (事件语义) ──
    close_now = float(latest["close"])
    close_prev = float(prev["close"])
    ema26_now = float(ema26.iloc[-1])
    ema26_prev = float(ema26.iloc[-2])
    price_broke_ema26 = (close_now < ema26_now) and (close_prev >= ema26_prev)
    below_ema26 = close_now < ema26_now   # 状态字段 (不驱动 sell)

    return {
        "latest": latest,
        "prev": prev,
        "close_now": close_now,
        "close_prev": close_prev,
        "ema12_now": float(ema12.iloc[-1]),
        "ema26_now": ema26_now,
        "dif_now": float(dif.iloc[-1]),
        "dea_now": float(dea.iloc[-1]),
        "rsi_now": rsi_now,
        "macd_bull": bool(macd_bull),
        "macd_bear": bool(macd_bear),
        "price_broke_ema26": bool(price_broke_ema26),
        "below_ema26": bool(below_ema26),
    }


def zhonglian_strategy1(ind: dict) -> dict:
    """策略1: MACD+RSI<50 —— 金叉且 RSI<50 买入, 死叉卖出。"""
    buy = ind["macd_bull"] and (ind["rsi_now"] < 50)
    return {
        "name": STRATEGY1_NAME,
        "buy": bool(buy),
        "sell": bool(ind["macd_bear"]),
        "rsi_filter": ind["rsi_now"] < 50,
        "desc": f"MACD{'金叉' if ind['macd_bull'] else '状态'} + RSI{round(ind['rsi_now'], 1)}",
    }


def zhonglian_strategy2(ind: dict) -> dict:
    """策略2: 综合最优(EMA+MACD+RSI)。

    买入: MACD金叉 + EMA12>EMA26 + RSI<60
    卖出: MACD死叉(事件) 或 下穿EMA26(事件)
          —— 注意: 不能用 `close < EMA26` 的状态, 否则下跌趋势中信号卡死。
    """
    buy = ind["macd_bull"] and (ind["ema12_now"] > ind["ema26_now"]) and (ind["rsi_now"] < 60)

    if ind["macd_bear"]:
        sell_event: Optional[str] = SELL_EVENT_MACD_DEAD_CROSS
    elif ind["price_broke_ema26"]:
        sell_event = SELL_EVENT_BREAK_EMA26
    else:
        sell_event = None

    return {
        "name": STRATEGY2_NAME,
        "buy": bool(buy),
        "sell": sell_event is not None,
        "sell_event": sell_event,
        "below_ema26": ind["below_ema26"],
        "desc": (f"MACD金叉:{ind['macd_bull']} "
                 f"EMA12>26:{ind['ema12_now'] > ind['ema26_now']} "
                 f"RSI<60:{ind['rsi_now'] < 60}"),
    }


def analyze_zhonglian(df: pd.DataFrame, *, macd_state_style: str = "default") -> dict:
    """中联重科双策略完整分析 (统一入口)。

    Args:
        df: 含 date/close/volume 列的日线数据。
        macd_state_style: 指标状态文案风格。
            "default" -> "📗金叉"/"📕死叉"  (service.py / zhonglian_monitor.py)
            "plain"   -> "多头金叉"/"多头死叉" (close_scan_v2.py)

    Returns:
        与旧实现结构完全一致的分析结果 dict。
    """
    ind = compute_indicators(df)
    latest, prev = ind["latest"], ind["prev"]

    if macd_state_style == "plain":
        dif_dir = "多头金叉" if ind["dif_now"] > ind["dea_now"] else "多头死叉"
        ema_dir = "多头EMA12>26" if ind["ema12_now"] > ind["ema26_now"] else "多头EMA12<26"
    else:
        dif_dir = "📗金叉" if ind["dif_now"] > ind["dea_now"] else "📕死叉"
        ema_dir = "📗EMA12>26" if ind["ema12_now"] > ind["ema26_now"] else "📕EMA12<26"

    return {
        "date": str(latest["date"].date()),
        "price": round(ind["close_now"], 2),
        "change_pct": round((ind["close_now"] / ind["close_prev"] - 1) * 100, 2),
        "volume": int(latest["volume"]),
        "indicators": {
            "MACD_DIF": round(ind["dif_now"], 4),
            "MACD_DEA": round(ind["dea_now"], 4),
            "MACD_state": dif_dir,
            "EMA12": round(ind["ema12_now"], 2),
            "EMA26": round(ind["ema26_now"], 2),
            "EMA_state": ema_dir,
            "RSI14": round(ind["rsi_now"], 1),
        },
        "signals": {
            "strategy1": zhonglian_strategy1(ind),
            "strategy2": zhonglian_strategy2(ind),
        },
    }


if __name__ == "__main__":
    # 自检: 构造合成数据验证事件语义
    import numpy as np

    dates = pd.date_range("2025-01-01", periods=120, freq="B")
    # 先涨后跌, 制造死叉与下穿
    prices = list(np.linspace(10, 14, 60)) + list(np.linspace(14, 10, 60))
    df = pd.DataFrame({"date": dates, "close": prices, "volume": [1_000_000] * 120})
    r = analyze_zhonglian(df)
    print("合成数据结果:")
    print("  日期:", r["date"], "收盘:", r["price"])
    print("  策略2:", r["signals"]["strategy2"])
