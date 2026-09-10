#!/usr/bin/env python3
"""
市场状态/均线参数自适应检测器 (P1)
=====================================
核心逻辑：
- ADX > 25 + DI+ > DI- → 强趋势(多头) → 缩短快线(EMA 8/21, 9/18)
- ADX > 25 + DI- > DI+ → 强趋势(空头) → 缩短快线(EMA 8/21, 9/18) + 偏空参数
- ADX < 20 → 震荡/无趋势 → 拉长快线(EMA 12/26, 20/50) 或直接弃做
- CHOP > 61.8 → 剧烈震荡 → 统一标记 chop_zone=True，仲裁器直接 HOLD
- 两均线夹缝：|EMA_fast - EMA_slow| / ATR < 0.3 → chop_zone=True

输出：
{
    "regime": "strong_trend_up" | "strong_trend_down" | "weak_trend" | "chop",
    "adx": float,
    "di_plus": float,
    "di_minus": float,
    "chop": float,
    "ema_gap_atr": float,
    "chop_zone": bool,
    "suggested_params": {
        "ema_cross": {"fast": 8, "slow": 21},
        "bollinger": {"length": 15, "std": 2.0},
        "ema_obv": {"length": 15},
        "kdj_cci": {"length": 7, "rsi_threshold": 50},
        "macd": {"fast": 8, "slow": 21, "signal": 5}
    },
    "avoid_trade": bool
}
"""

import pandas as pd
import pandas_ta as ta
import numpy as np
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict


@dataclass
class RegimeResult:
    regime: str                    # strong_trend_up / strong_trend_down / weak_trend / chop
    adx: float
    di_plus: float
    di_minus: float
    chop: float
    ema_gap_atr: float
    chop_zone: bool
    suggested_params: Dict[str, Dict]
    avoid_trade: bool
    reason: str


def compute_chop(df: pd.DataFrame, length: int = 14) -> float:
    """Choppiness Index: 100 * log10( sum(ATR) / (high_max - low_min) ) / log10(length)
    > 61.8 = 震荡, < 38.2 = 趋势"""
    if len(df) < length + 1:
        return 50.0
    atr = ta.atr(df["high"], df["low"], df["close"], length=1)
    atr_sum = atr.tail(length).sum()
    high_max = df["high"].tail(length).max()
    low_min = df["low"].tail(length).min()
    if high_max == low_min:
        return 50.0
    chop = 100 * np.log10(atr_sum / (high_max - low_min)) / np.log10(length)
    return float(np.clip(chop, 0, 100))


def detect_regime(df: pd.DataFrame,
                  adx_len: int = 14,
                  chop_len: int = 14,
                  atr_len: int = 14) -> RegimeResult:
    """
    检测当前市场状态并给出自适应参数建议
    """
    if df is None or len(df) < max(adx_len, chop_len) + 20:
        return RegimeResult(
            regime="unknown",
            adx=0.0, di_plus=0.0, di_minus=0.0,
            chop=50.0, ema_gap_atr=0.0, chop_zone=False,
            suggested_params={}, avoid_trade=False,
            reason="数据不足"
        )

    # ADX + DI
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=adx_len)
    adx_col = [c for c in adx_df.columns if "ADX_" in c.upper()][0]
    di_plus_col = [c for c in adx_df.columns if "DMP_" in c.upper()][0]
    di_minus_col = [c for c in adx_df.columns if "DMN_" in c.upper()][0]

    adx = float(adx_df[adx_col].iloc[-1]) if pd.notna(adx_df[adx_col].iloc[-1]) else 0.0
    di_plus = float(adx_df[di_plus_col].iloc[-1]) if pd.notna(adx_df[di_plus_col].iloc[-1]) else 0.0
    di_minus = float(adx_df[di_minus_col].iloc[-1]) if pd.notna(adx_df[di_minus_col].iloc[-1]) else 0.0

    # CHOP
    chop = compute_chop(df, chop_len)

    # EMA gap / ATR (夹缝检测)
    ema12 = ta.ema(df["close"], length=12)
    ema26 = ta.ema(df["close"], length=26)
    atr = ta.atr(df["high"], df["low"], df["close"], length=atr_len)

    ema_gap = abs(float(ema12.iloc[-1]) - float(ema26.iloc[-1]))
    atr_val = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else 1e-6
    ema_gap_atr = ema_gap / atr_val

    # 判定 regime
    regime = "weak_trend"
    reason_parts = []

    # 优先：CHOP 高 → 震荡
    if chop > 61.8:
        regime = "chop"
        reason_parts.append(f"CHOP={chop:.1f}>61.8 剧烈震荡")
    # 次优：ADX 强趋势
    elif adx > 25:
        if di_plus > di_minus:
            regime = "strong_trend_up"
            reason_parts.append(f"ADX={adx:.1f}>25, DI+({di_plus:.1f})>DI-({di_minus:.1f}) 强多头趋势")
        else:
            regime = "strong_trend_down"
            reason_parts.append(f"ADX={adx:.1f}>25, DI-({di_minus:.1f})>DI+({di_plus:.1f}) 强空头趋势")
    # 次优：ADX 弱 + 非高CHOP → 弱趋势/模糊
    elif adx < 20:
        regime = "weak_trend"
        reason_parts.append(f"ADX={adx:.1f}<20 无明确趋势")
    else:
        regime = "weak_trend"
        reason_parts.append(f"ADX={adx:.1f} 中性过渡")

    # 夹缝检测：EMA12/26 差值 < 0.3 ATR
    chop_zone = ema_gap_atr < 0.3
    if chop_zone:
        reason_parts.append(f"EMA12/26夹缝({ema_gap_atr:.2f}ATR)")

    # 避免交易条件
    avoid_trade = (regime == "chop") or chop_zone

    # 根据 regime 给出参数建议
    if regime == "strong_trend_up":
        params = {
            "ema_cross": {"fast": 8, "slow": 21},
            "bollinger": {"length": 15, "std": 2.0},
            "ema_obv": {"length": 15},
            "kdj_cci": {"length": 7, "rsi_threshold": 45},
            "macd": {"fast": 8, "slow": 21, "signal": 5},
        }
    elif regime == "strong_trend_down":
        params = {
            "ema_cross": {"fast": 8, "slow": 21},
            "bollinger": {"length": 15, "std": 2.0},
            "ema_obv": {"length": 15},
            "kdj_cci": {"length": 7, "rsi_threshold": 55},  # 空头偏高阈值
            "macd": {"fast": 8, "slow": 21, "signal": 5},
        }
    elif regime == "chop":
        params = {
            "ema_cross": {"fast": 20, "slow": 50},  # 拉长周期，减少假信号
            "bollinger": {"length": 30, "std": 2.5},
            "ema_obv": {"length": 30},
            "kdj_cci": {"length": 14, "rsi_threshold": 50},
            "macd": {"fast": 12, "slow": 26, "signal": 9},
        }
    else:  # weak_trend / unknown
        params = {
            "ema_cross": {"fast": 12, "slow": 26},
            "bollinger": {"length": 20, "std": 2.0},
            "ema_obv": {"length": 20},
            "kdj_cci": {"length": 9, "rsi_threshold": 40},
            "macd": {"fast": 12, "slow": 26, "signal": 9},
        }

    return RegimeResult(
        regime=regime,
        adx=adx, di_plus=di_plus, di_minus=di_minus,
        chop=chop, ema_gap_atr=ema_gap_atr, chop_zone=chop_zone,
        suggested_params=params, avoid_trade=avoid_trade,
        reason="; ".join(reason_parts)
    )


def get_adaptive_params(df: pd.DataFrame, strategy_key: str) -> Dict:
    """
    便捷函数：直接返回某策略的自适应参数
    """
    result = detect_regime(df)
    return result.suggested_params.get(strategy_key, {})


# 兼容 adaptive_dual 的接口：返回 param_template + regime_fn
def make_param_resolver(df: pd.DataFrame):
    """
    返回 (static_params_dict, regime_fn)
    regime_fn() -> 当前市场状态下的参数字典
    供 adaptive_dual.load_dual_strategy_map 使用
    """
    result = detect_regime(df)

    def regime_fn():
        return detect_regime(df).suggested_params

    # 静态默认参数（作为模板）
    static = {
        "ema_cross": {"fast": 12, "slow": 26},
        "bollinger": {"length": 20, "std": 2.0, "atr_mult": 1.5},
        "ema_obv": {"length": 20},
        "kdj_cci": {"length": 9, "rsi_threshold": 40},
        "macd": {"fast": 12, "slow": 26, "signal": 9},
    }

    return static, regime_fn, result


if __name__ == "__main__":
    # 简单自测
    import baostock as bs
    import datetime

    lg = bs.login()
    rs = bs.query_history_k_data_plus(
        "sh.600584", "date,open,high,low,close,volume",
        start_date="2025-01-01", end_date=datetime.date.today().strftime("%Y-%m-%d"),
        frequency="d", adjustflag="2")
    data = []
    while rs.next():
        data.append(rs.get_row_data())
    bs.logout()

    df = pd.DataFrame(data, columns=["date","open","high","low","close","volume"])
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True).dropna()

    res = detect_regime(df)
    print(f"Regime: {res.regime}")
    print(f"ADX: {res.adx:.1f}, DI+: {res.di_plus:.1f}, DI-: {res.di_minus:.1f}")
    print(f"CHOP: {res.chop:.1f}")
    print(f"EMA_gap/ATR: {res.ema_gap_atr:.3f}, chop_zone: {res.chop_zone}")
    print(f"Avoid trade: {res.avoid_trade}")
    print(f"Reason: {res.reason}")
    print("Suggested params:")
    for k, v in res.suggested_params.items():
        print(f"  {k}: {v}")