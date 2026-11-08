#!/usr/bin/env python3
"""
因子计算引擎 — 向量化批量计算、缓存、增量更新
==================================================
核心能力：
  1. 从 factor_registry 读取因子定义
  2. 批量计算多只股票 × 多个因子（面板数据结构）
  3. 缓存机制：内存 LRU + 磁盘持久化（Parquet/Feather）
  4. 增量更新：只算最新 N 行，拼接历史
  5. NaN 容错：统一 pd.to_numeric(coerce) + 前向填充/插值策略
  6. 计算图依赖解析：自动按依赖顺序拓扑排序

数据结构约定：
  - 输入：Dict[str, pd.DataFrame]  key=symbol, value=K线DataFrame(index=date, columns=OHLCV+基本面)
  - 输出：pd.DataFrame (MultiIndex: symbol × date, columns=因子名)
  - 缓存：data/factor_cache/{factor_name}.parquet (分区按 symbol)
"""

import os
import shutil
import sys
import json
import pickle
import hashlib
import inspect
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Set, Tuple
from functools import lru_cache
from collections import defaultdict
from pathlib import Path

import pandas as pd
import numpy as np

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
CACHE_DIR = os.path.join(WORKSPACE, "data", "factor_cache")
REGISTRY_PATH = os.path.join(WORKSPACE, "data", "factor_registry.json")

os.makedirs(CACHE_DIR, exist_ok=True)

# 确保 analysis 在 path
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

from analysis.factor_registry import get_registry, FactorMeta


# ══════════════════════════════════════════════════════════════
# 计算函数注册表（纯函数，无副作用，可序列化）
# ══════════════════════════════════════════════════════════════

def _ensure_numeric(series: pd.Series) -> pd.Series:
    """统一数值容错：空串/None → NaN → 前向填充"""
    return pd.to_numeric(series, errors="coerce").ffill()


def _sma(series: pd.Series, period: int) -> pd.Series:
    return _ensure_numeric(series).rolling(period, min_periods=1).mean()


def _ema(series: pd.Series, span: int) -> pd.Series:
    return _ensure_numeric(series).ewm(span=span, adjust=False, min_periods=1).mean()


def _std(series: pd.Series, period: int) -> pd.Series:
    return _ensure_numeric(series).rolling(period, min_periods=1).std()


def _zscore(series: pd.Series, period: int) -> pd.Series:
    s = _ensure_numeric(series)
    mean = s.rolling(period, min_periods=1).mean()
    std = s.rolling(period, min_periods=1).std()
    return (s - mean) / std.replace(0, np.nan)


# ── 单因子计算函数（按 factor_registry.compute_fn 名称对应）──

def ema(df: pd.DataFrame, **params) -> pd.Series:
    span = params.get("span", 12)
    return _ema(df["close"], span)


def ema_cross(df: pd.DataFrame, **params) -> pd.Series:
    fast = params.get("fast", 12)
    slow = params.get("slow", 26)
    ema_f = _ema(df["close"], fast)
    ema_s = _ema(df["close"], slow)
    cross = pd.Series(0, index=df.index, dtype=int)
    cross[(ema_f > ema_s) & (ema_f.shift(1) <= ema_s.shift(1))] = 1   # 金叉
    cross[(ema_f < ema_s) & (ema_f.shift(1) >= ema_s.shift(1))] = -1  # 死叉
    return cross


def macd(df: pd.DataFrame, **params) -> pd.Series:
    fast = params.get("fast", 12)
    slow = params.get("slow", 26)
    signal = params.get("signal", 9)
    ema_f = _ema(df["close"], fast)
    ema_s = _ema(df["close"], slow)
    macd_line = ema_f - ema_s
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=1).mean()
    return macd_line - signal_line  # 柱状图


def macd_signal(df: pd.DataFrame, **params) -> pd.Series:
    fast = params.get("fast", 12)
    slow = params.get("slow", 26)
    signal = params.get("signal", 9)
    ema_f = _ema(df["close"], fast)
    ema_s = _ema(df["close"], slow)
    macd_line = ema_f - ema_s
    return macd_line.ewm(span=signal, adjust=False, min_periods=1).mean()


def rsi(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 14)
    close = _ensure_numeric(df["close"])
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period, min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(period, min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def kdj(df: pd.DataFrame, **params) -> pd.Series:
    n = params.get("n", 9)
    m1 = params.get("m1", 3)
    m2 = params.get("m2", 3)
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    lowest = low.rolling(n, min_periods=1).min()
    highest = high.rolling(n, min_periods=1).max()
    rsv = (close - lowest) / (highest - lowest).replace(0, np.nan) * 100
    k = rsv.ewm(com=m1 - 1, adjust=False, min_periods=1).mean()
    return k


def kdj_d(df: pd.DataFrame, **params) -> pd.Series:
    n = params.get("n", 9)
    m1 = params.get("m1", 3)
    m2 = params.get("m2", 3)
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    lowest = low.rolling(n, min_periods=1).min()
    highest = high.rolling(n, min_periods=1).max()
    rsv = (close - lowest) / (highest - lowest).replace(0, np.nan) * 100
    k = rsv.ewm(com=m1 - 1, adjust=False, min_periods=1).mean()
    d = k.ewm(com=m2 - 1, adjust=False, min_periods=1).mean()
    return d


def cci(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 20)
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    tp = (high + low + close) / 3
    ma = tp.rolling(period, min_periods=1).mean()
    md = (tp - ma).abs().rolling(period, min_periods=1).mean()
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def roc(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 10)
    close = _ensure_numeric(df["close"])
    return close.pct_change(period) * 100


def momentum(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 20)
    close = _ensure_numeric(df["close"])
    return close - close.shift(period)


def adx(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 14)
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    
    # True Range
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    
    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    
    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    
    # +DM: up_move > down_move and up_move > 0
    plus_mask = (up_move > down_move) & (up_move > 0)
    plus_dm[plus_mask] = up_move[plus_mask]
    
    # -DM: down_move > up_move and down_move > 0
    minus_mask = (down_move > up_move) & (down_move > 0)
    minus_dm[minus_mask] = down_move[minus_mask]
    
    # Wilder's Smoothing (alpha = 1/period)
    alpha = 1.0 / period
    
    tr_smooth = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    plus_dm_smooth = plus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    minus_dm_smooth = minus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    
    # Directional Indicators
    plus_di = 100 * plus_dm_smooth / tr_smooth.replace(0, np.nan)
    minus_di = 100 * minus_dm_smooth / tr_smooth.replace(0, np.nan)
    
    # DX
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    
    # ADX = smoothed DX
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    
    return adx


def atr(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 14)
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def bb_width(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 20)
    std_mult = params.get("std", 2)
    close = _ensure_numeric(df["close"])
    ma = close.rolling(period, min_periods=1).mean()
    std = close.rolling(period, min_periods=1).std()
    upper = ma + std_mult * std
    lower = ma - std_mult * std
    return (upper - lower) / ma.replace(0, np.nan)


def bb_position(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 20)
    std_mult = params.get("std", 2)
    close = _ensure_numeric(df["close"])
    ma = close.rolling(period, min_periods=1).mean()
    std = close.rolling(period, min_periods=1).std()
    upper = ma + std_mult * std
    lower = ma - std_mult * std
    return (close - lower) / (upper - lower).replace(0, np.nan)


def historical_vol(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 20)
    close = _ensure_numeric(df["close"])
    ret = close.pct_change()
    return ret.rolling(period, min_periods=1).std() * np.sqrt(252)


def zscore(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 20)
    col = params.get("column", "volume")
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index)
    return _zscore(df[col], period)


def volume_ratio(df: pd.DataFrame, **params) -> pd.Series:
    short = params.get("short", 5)
    long = params.get("long", 20)
    vol = _ensure_numeric(df["volume"])
    vol_s = vol.rolling(short, min_periods=1).mean()
    vol_l = vol.rolling(long, min_periods=1).mean()
    return vol_s / vol_l.replace(0, np.nan)


def obv(df: pd.DataFrame, **params) -> pd.Series:
    close = _ensure_numeric(df["close"])
    vol = _ensure_numeric(df["volume"])
    direction = np.sign(close.diff().fillna(0))
    return (direction * vol).cumsum()


def vwap_deviation(df: pd.DataFrame, **params) -> pd.Series:
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    vol = _ensure_numeric(df["volume"])
    tp = (high + low + close) / 3
    vwap = (tp * vol).cumsum() / vol.cumsum().replace(0, np.nan)
    return (close - vwap) / vwap.replace(0, np.nan)


def mfi(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 14)
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    vol = _ensure_numeric(df["volume"])
    tp = (high + low + close) / 3
    mf = tp * vol
    pos_mf = mf.where(tp > tp.shift(1), 0).rolling(period, min_periods=1).sum()
    neg_mf = mf.where(tp < tp.shift(1), 0).rolling(period, min_periods=1).sum()
    mfr = pos_mf / neg_mf.replace(0, np.nan)
    return 100 - 100 / (1 + mfr)


def pass_through(df: pd.DataFrame, **params) -> pd.Series:
    """基本面因子：直接透传字段（需数据源已包含）"""
    col = params.get("column", "")
    if col and col in df.columns:
        return _ensure_numeric(df[col])
    return pd.Series(np.nan, index=df.index)


def amplitude(df: pd.DataFrame, **params) -> pd.Series:
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    open_ = _ensure_numeric(df["open"])
    return (high - low) / open_.replace(0, np.nan)


def gap_pct(df: pd.DataFrame, **params) -> pd.Series:
    open_ = _ensure_numeric(df["open"])
    close = _ensure_numeric(df["close"])
    return (open_ - close.shift(1)) / close.shift(1).replace(0, np.nan)


def close_to_high(df: pd.DataFrame, **params) -> pd.Series:
    high = _ensure_numeric(df["high"])
    close = _ensure_numeric(df["close"])
    return (high - close) / (high - _ensure_numeric(df["low"])).replace(0, np.nan)


def williams_r(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 14)
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    highest = high.rolling(period, min_periods=1).max()
    lowest = low.rolling(period, min_periods=1).min()
    return -100 * (highest - close) / (highest - lowest).replace(0, np.nan)


def stoch_rsi(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 14)
    smooth_k = params.get("smooth_k", 3)
    smooth_d = params.get("smooth_d", 3)
    close = _ensure_numeric(df["close"])
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period, min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(period, min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi_min = rsi.rolling(period, min_periods=1).min()
    rsi_max = rsi.rolling(period, min_periods=1).max()
    stoch = (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan) * 100
    k = stoch.rolling(smooth_k, min_periods=1).mean()
    return k


def chaikin_money_flow(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 20)
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    vol = _ensure_numeric(df["volume"])
    mfm = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)
    mfv = mfm * vol
    cmf = mfv.rolling(period, min_periods=1).sum() / vol.rolling(period, min_periods=1).sum().replace(0, np.nan)
    return cmf


def force_index(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 13)
    close = _ensure_numeric(df["close"])
    vol = _ensure_numeric(df["volume"])
    fi = close.diff() * vol
    return fi.ewm(span=period, adjust=False, min_periods=1).mean()


def keltner_channel(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 20)
    mult = params.get("mult", 2)
    which = params.get("which", "upper")
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    ema = close.ewm(span=period, adjust=False, min_periods=1).mean()
    atr_val = (high - low).rolling(period, min_periods=1).mean()  # 简化版 ATR
    upper = ema + mult * atr_val
    lower = ema - mult * atr_val
    return upper if which == "upper" else lower


def donchian_channel(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 20)
    which = params.get("which", "upper")
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    upper = high.rolling(period, min_periods=1).max()
    lower = low.rolling(period, min_periods=1).min()
    return upper if which == "upper" else lower


def supertrend(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 10)
    mult = params.get("mult", 3)
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    hl2 = (high + low) / 2
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr_val = tr.rolling(period, min_periods=1).mean()
    upper_band = hl2 + mult * atr_val
    lower_band = hl2 - mult * atr_val
    trend = pd.Series(1, index=df.index)
    for i in range(1, len(df)):
        if close.iloc[i] > upper_band.iloc[i-1]:
            trend.iloc[i] = 1
        elif close.iloc[i] < lower_band.iloc[i-1]:
            trend.iloc[i] = -1
        else:
            trend.iloc[i] = trend.iloc[i-1]
    return trend


def ichimoku(df: pd.DataFrame, **params) -> pd.Series:
    which = params.get("which", "tenkan")
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    if which == "tenkan":
        period = 9
        high_max = high.rolling(period, min_periods=1).max()
        low_min = low.rolling(period, min_periods=1).min()
        return (high_max + low_min) / 2
    if which == "kijun":
        period = 26
        high_max = high.rolling(period, min_periods=1).max()
        low_min = low.rolling(period, min_periods=1).min()
        return (high_max + low_min) / 2
    if which == "span_a":
        # Senkou Span A = (先行十日均线 + 先行廿六日均线) / 2（不位移；位移是绘图约定）
        tenkan = ichimoku(df, which="tenkan")
        kijun = ichimoku(df, which="kijun")
        return (tenkan + kijun) / 2
    # span_b
    period = 52
    high_max = high.rolling(period, min_periods=1).max()
    low_min = low.rolling(period, min_periods=1).min()
    return (high_max + low_min) / 2


def elder_ray(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 13)
    which = params.get("which", "bull")
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    ema = close.ewm(span=period, adjust=False, min_periods=1).mean()
    if which == "bull":
        return high - ema
    else:
        return low - ema


def vwap(df: pd.DataFrame, **params) -> pd.Series:
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    vol = _ensure_numeric(df["volume"])
    tp = (high + low + close) / 3
    return (tp * vol).cumsum() / vol.cumsum().replace(0, np.nan)


def pivot_point(df: pd.DataFrame, **params) -> pd.Series:
    which = params.get("which", "pp")
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    pp = (high + low + close) / 3
    if which == "pp":
        return pp
    elif which == "r1":
        return 2 * pp - low
    elif which == "s1":
        return 2 * pp - high
    elif which == "r2":
        return pp + (high - low)
    elif which == "s2":
        return pp - (high - low)
    return pp

def amplitude(df: pd.DataFrame, **params) -> pd.Series:
    high = _ensure_numeric(df["high"])
    low = _ensure_numeric(df["low"])
    open_ = _ensure_numeric(df["open"])
    return (high - low) / open_.replace(0, np.nan)


def upper_shadow(df: pd.DataFrame, **params) -> pd.Series:
    high = _ensure_numeric(df["high"])
    close = _ensure_numeric(df["close"])
    open_ = _ensure_numeric(df["open"])
    body = (close - open_).abs()
    return (high - close.where(close > open_, open_)) / body.replace(0, np.nan)


# ═══════════════════════════════════════════
# 估值因子计算函数（2026-11-09 新增）
# ═══════════════════════════════════════════

def valuation_pe_ttm(df: pd.DataFrame, **params) -> pd.Series:
    """PE_TTM = close / eps_basic (eps > 0)"""
    close = _ensure_numeric(df["close"])
    eps = _ensure_numeric(df.get("eps_basic", pd.Series(np.nan, index=df.index)))
    result = np.where(eps > 0, close / eps, np.nan)
    return pd.Series(result, index=df.index, name="PE_TTM")


def valuation_pb_mrq(df: pd.DataFrame, **params) -> pd.Series:
    """PB_MRQ = close / book_value_per_share (bv > 0)"""
    close = _ensure_numeric(df["close"])
    bv = _ensure_numeric(df.get("book_value_per_share", pd.Series(np.nan, index=df.index)))
    result = np.where(bv > 0, close / bv, np.nan)
    return pd.Series(result, index=df.index, name="PB_MRQ")


def valuation_ps_ttm(df: pd.DataFrame, **params) -> pd.Series:
    """PS_TTM = close / revenue_per_share (rev > 0)"""
    close = _ensure_numeric(df["close"])
    rev = _ensure_numeric(df.get("revenue_per_share", pd.Series(np.nan, index=df.index)))
    result = np.where(rev > 0, close / rev, np.nan)
    return pd.Series(result, index=df.index, name="PS_TTM")


def valuation_pcf_ttm(df: pd.DataFrame, **params) -> pd.Series:
    """PCF_TTM = close / operating_cash_flow_per_share (ocf > 0)"""
    close = _ensure_numeric(df["close"])
    ocf = _ensure_numeric(df.get("operating_cash_flow_per_share", pd.Series(np.nan, index=df.index)))
    result = np.where(ocf > 0, close / ocf, np.nan)
    return pd.Series(result, index=df.index, name="PCF_TTM")


def lower_shadow(df: pd.DataFrame, **params) -> pd.Series:
    low = _ensure_numeric(df["low"])
    close = _ensure_numeric(df["close"])
    open_ = _ensure_numeric(df["open"])
    body = (close - open_).abs()
    return (close.where(close < open_, open_) - low) / body.replace(0, np.nan)


def sma(df: pd.DataFrame, **params) -> pd.Series:
    period = params.get("period", 20)
    col = params.get("column", "close")
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index)
    return _sma(df[col], period)


# ═══════════════════════════════════════════════════════════════
# 计算函数注册表（字符串名 → 函数）
# ═══════════════════════════════════════════════════════════════

COMPUTE_FNS = {
    "ema": ema,
    "ema_cross": ema_cross,
    "macd": macd,
    "macd_signal": macd_signal,
    "rsi": rsi,
    "kdj": kdj,
    "kdj_d": kdj_d,
    "cci": cci,
    "roc": roc,
    "momentum": momentum,
    "adx": adx,
    "atr": atr,
    "bb_width": bb_width,
    "bb_position": bb_position,
    "historical_vol": historical_vol,
    "zscore": zscore,
    "volume_ratio": volume_ratio,
    "obv": obv,
    "vwap_deviation": vwap_deviation,
    "mfi": mfi,
    "pass_through": pass_through,
    "amplitude": amplitude,
    "gap_pct": gap_pct,
    "close_to_high": close_to_high,
    "upper_shadow": upper_shadow,
    "lower_shadow": lower_shadow,
    "sma": sma,
    # 新增高级技术因子
    "williams_r": williams_r,
    "stoch_rsi": stoch_rsi,
    "chaikin_money_flow": chaikin_money_flow,
    "force_index": force_index,
    "keltner_channel": keltner_channel,
    "donchian_channel": donchian_channel,
    "supertrend": supertrend,
    "ichimoku": ichimoku,
    "elder_ray": elder_ray,
    "vwap": vwap,
    "pivot_point": pivot_point,
    "pass_through": pass_through,
    # 估值因子
    "valuation_pe_ttm": valuation_pe_ttm,
    "valuation_pb_mrq": valuation_pb_mrq,
    "valuation_ps_ttm": valuation_ps_ttm,
    "valuation_pcf_ttm": valuation_pcf_ttm,
    "pass_through": pass_through,
}


# ═══════════════════════════════════════════════════════════════
# 因子计算引擎主类
# ═══════════════════════════════════════════════════════════════

class FactorEngine:
    """向量化因子批量计算引擎"""

    def __init__(self, cache_dir: str = CACHE_DIR):
        self.cache_dir = cache_dir
        self.registry = get_registry()
        self._register_compute_fns()
        os.makedirs(cache_dir, exist_ok=True)

    def _register_compute_fns(self):
        """把计算函数注册到注册表"""
        for name, fn in COMPUTE_FNS.items():
            self.registry.register_compute_fn(name, fn)

    def _params_hash(self, params: Dict) -> str:
        return hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()

    def _code_hash(self, compute_fn_name: str) -> str:
        """计算函数源码指纹 —— 修复后旧缓存静默服役的根治（2026-11-09）。
        事故背景：ADX≡ATR 修复(11-08)后缓存键不含代码版本，若重算不发生会继续产坏值。"""
        fn = COMPUTE_FNS.get(compute_fn_name)
        try:
            src = inspect.getsource(fn) if fn else ""
        except Exception:
            src = ""
        return hashlib.md5(src.encode()).hexdigest()[:8]

    def _versions_path(self, factor_name: str) -> str:
        return os.path.join(self.cache_dir, f"{factor_name}.versions.json")

    def _load_versions(self, factor_name: str) -> Dict:
        """读取旁车版本表 {symbol: version}（parquet attrs 不落盘，故用 JSON 旁车）"""
        try:
            with open(self._versions_path(factor_name), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}  # 首跑正常态
        except Exception as e:
            print(f"⚠️ 版本表读取异常 {factor_name}: {e}（按无版本处理，全部重算，不影响正确性）")
            return {}

    def _save_version(self, factor_name: str, symbol: str, version: str):
        """写旁车版本表（写前 .bak，backup_guard 铁律）"""
        vp = self._versions_path(factor_name)
        try:
            versions = self._load_versions(factor_name)
            versions[symbol] = version
            if os.path.exists(vp):
                shutil.copy2(vp, vp + ".bak")
            tmp = vp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(versions, f, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, vp)
        except Exception as e:
            print(f"⚠️ 版本表写入失败 {factor_name}/{symbol}: {e}（下轮将重算该 symbol，不影响正确性）")

    def _data_version_hash(self, df: pd.DataFrame) -> str:
        """计算源数据版本哈希：基于日期范围、行数、列哈希"""
        if df.empty:
            return "empty"
        # 日期范围 + 行数 + 列名 + 首尾收盘价（敏感指纹）
        info = {
            "rows": len(df),
            "date_min": str(df.index.min()) if hasattr(df.index, 'min') else "",
            "date_max": str(df.index.max()) if hasattr(df.index, 'max') else "",
            "cols": list(df.columns),
            "first_close": float(df.iloc[0].get("close", 0)) if len(df) > 0 else 0,
            "last_close": float(df.iloc[-1].get("close", 0)) if len(df) > 0 else 0,
        }
        return hashlib.md5(json.dumps(info, sort_keys=True).encode()).hexdigest()[:12]

    def _cache_path(self, factor_name: str) -> str:
        return os.path.join(self.cache_dir, f"{factor_name}.parquet")

    # ── 单因子计算（单股票）──
    def compute_one(self, df: pd.DataFrame, factor: FactorMeta) -> pd.Series:
        """计算单只股票单个因子"""
        fn = COMPUTE_FNS.get(factor.compute_fn)
        if fn is None:
            raise ValueError(f"未找到计算函数: {factor.compute_fn}")
        try:
            result = fn(df, **factor.params)
            result.name = factor.name
            return result
        except Exception as e:
            print(f"⚠️ 因子 {factor.name} 计算失败 ({factor.compute_fn}): {e}")
            return pd.Series(np.nan, index=df.index, name=factor.name)

    # ── 批量计算（多股票 × 多因子）──
    def compute_panel(
        self,
        data: Dict[str, pd.DataFrame],
        factor_names: List[str],
        use_cache: bool = True,
        incremental: bool = False,
    ) -> pd.DataFrame:
        """
        面板计算：多股票 × 多因子
        :param data: {symbol: DataFrame(index=date, columns=OHLCV+...)}
        :param factor_names: 因子名列表
        :param use_cache: 是否使用磁盘缓存
        :param incremental: 增量模式（只算最新日期，拼接历史缓存）
        :return: DataFrame (MultiIndex: symbol × date, columns=factor_names)
        """
        factors = [self.registry.get(n) for n in factor_names]
        missing = [n for n, f in zip(factor_names, factors) if f is None]
        if missing:
            raise ValueError(f"因子不存在: {missing}")

        # 拓扑排序（按依赖顺序，简单版：按 lookback 升序）
        factors.sort(key=lambda f: f.lookback)

        all_results = {}

        for symbol, df in data.items():
            # 确保索引是日期
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)

            symbol_results = {}

            # 计算数据版本哈希（每股票一次）
            data_version = self._data_version_hash(df)
            
            for factor in factors:
                params_hash = self._params_hash(factor.params)
                # 版本三重指纹：数据 + 参数 + 计算函数源码（2026-11-09 修复：
                # 旧版 cache_key 算出后从未比对，改代码/换数据后旧缓存静默服役且新日期恒 NaN）
                version = f"{data_version}_{params_hash[:8]}_{self._code_hash(factor.compute_fn)}"
                cache_path = self._cache_path(factor.name)
                stored_versions = self._load_versions(factor.name) if (use_cache and not incremental) else {}

                if use_cache and not incremental and os.path.exists(cache_path) \
                        and stored_versions.get(symbol) == version:
                    # 读取缓存 —— 版本逐 symbol 比对，不匹配落到重算路径
                    try:
                        cached = pd.read_parquet(cache_path)
                        if symbol in cached.columns.get_level_values(0):
                            series = cached[symbol][factor.name]
                            series = series.reindex(df.index)
                            symbol_results[factor.name] = series
                            continue
                    except Exception:
                        pass

                # 计算
                series = self.compute_one(df, factor)
                symbol_results[factor.name] = series

                # 写入缓存（增量追加）+ 版本表
                if use_cache:
                    self._save_to_cache(factor.name, symbol, series)
                    self._save_version(factor.name, symbol, version)

            all_results[symbol] = pd.DataFrame(symbol_results, index=df.index)

        # 合并为面板
        panel = pd.concat(all_results, axis=0)
        panel.index.names = ["symbol", "date"]
        panel = panel[factor_names]  # 保证列顺序
        return panel

    def _save_to_cache(self, factor_name: str, symbol: str, series: pd.Series):
        """保存到 Parquet 缓存（分区按 symbol）"""
        cache_path = self._cache_path(factor_name)
        try:
            if os.path.exists(cache_path):
                existing = pd.read_parquet(cache_path)
            else:
                existing = pd.DataFrame()

            # 转为 DataFrame 格式 - 确保索引正确
            new_df = series.to_frame(name=factor_name)
            new_df.index.name = "date"
            new_df = new_df.reset_index()  # 将 date 索引转为列
            new_df["symbol"] = symbol
            new_df = new_df.set_index(["symbol", "date"])

            # 合并去重
            combined = pd.concat([existing, new_df])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined = combined.sort_index()

            combined.to_parquet(cache_path, compression="snappy")
        except Exception as e:
            print(f"⚠️ 缓存写入失败 {factor_name}: {e}")

    # ── 增量更新 ──
    def update_latest(
        self,
        data: Dict[str, pd.DataFrame],
        factor_names: List[str],
        n_days: int = 5,
    ) -> pd.DataFrame:
        """只计算最新 N 天，自动拼接历史缓存"""
        # 截取最新 N 天
        latest_data = {}
        for symbol, df in data.items():
            latest_data[symbol] = df.tail(n_days)
        return self.compute_panel(latest_data, factor_names, use_cache=True, incremental=True)

    # ── 缓存管理 ──
    def clear_cache(self, factor_name: Optional[str] = None):
        if factor_name:
            path = self._cache_path(factor_name)
            if os.path.exists(path):
                os.remove(path)
                print(f"✅ 清除缓存: {factor_name}")
        else:
            for f in os.listdir(self.cache_dir):
                if f.endswith(".parquet"):
                    os.remove(os.path.join(self.cache_dir, f))
            print("✅ 清除所有缓存")

    def cache_info(self) -> pd.DataFrame:
        """返回缓存统计"""
        rows = []
        for f in os.listdir(self.cache_dir):
            if f.endswith(".parquet"):
                path = os.path.join(self.cache_dir, f)
                try:
                    df = pd.read_parquet(path)
                    rows.append({
                        "factor": f.replace(".parquet", ""),
                        "symbols": df.index.get_level_values("symbol").nunique() if "symbol" in df.index.names else "N/A",
                        "rows": len(df),
                        "size_mb": os.path.getsize(path) / 1024 / 1024,
                        "date_range": f"{df.index.get_level_values('date').min()} ~ {df.index.get_level_values('date').max()}" if "date" in df.index.names else "N/A",
                    })
                except Exception:
                    pass
        return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════
# 全局引擎实例
# ═══════════════════════════════════════════════════════════════

_engine: Optional[FactorEngine] = None

def get_engine() -> FactorEngine:
    global _engine
    if _engine is None:
        _engine = FactorEngine()
    return _engine


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-info", action="store_true", help="显示缓存统计")
    ap.add_argument("--clear-cache", help="清除指定因子缓存")
    ap.add_argument("--clear-all", action="store_true", help="清除所有缓存")
    ap.add_argument("--test", action="store_true", help="跑通测试（用模拟数据）")
    args = ap.parse_args()

    engine = get_engine()

    if args.cache_info:
        df = engine.cache_info()
        print(df.to_string(index=False))

    if args.clear_cache:
        engine.clear_cache(args.clear_cache)

    if args.clear_all:
        engine.clear_cache()

    if args.test:
        # 生成模拟数据测试
        print("🧪 生成模拟数据测试...")
        np.random.seed(42)
        dates = pd.date_range("2025-01-01", periods=200, freq="D")
        symbols = ["000001", "000002", "600000", "600036"]

        data = {}
        for sym in symbols:
            base = 10 + np.random.rand() * 50
            close = base + np.cumsum(np.random.randn(200) * 0.5)
            high = close + np.abs(np.random.randn(200) * 0.3)
            low = close - np.abs(np.random.randn(200) * 0.3)
            open_ = close + np.random.randn(200) * 0.2
            volume = np.abs(np.random.randn(200) * 1e6 + 5e6)

            df = pd.DataFrame({
                "open": open_, "high": high, "low": low, "close": close, "volume": volume
            }, index=dates)
            data[sym] = df

        # 计算核心因子
        test_factors = ["EMA_12", "EMA_26", "RSI_14", "MACD_hist", "ATR_14", "BB_pos_20", "volume_zscore_20"]
        panel = engine.compute_panel(data, test_factors)

        print(f"✅ 面板形状: {panel.shape}")
        print(f"   索引: {panel.index.names}")
        print(f"   列: {list(panel.columns)}")
        print(f"   样本:\n{panel.head(10)}")

        # 测试增量更新
        print("\n🔄 测试增量更新...")
        new_dates = pd.date_range("2025-07-20", periods=10, freq="D")
        new_data = {}
        for sym in symbols:
            base = float(data[sym]["close"].iloc[-1])
            close = base + np.cumsum(np.random.randn(10) * 0.5)
            high = close + np.abs(np.random.randn(10) * 0.3)
            low = close - np.abs(np.random.randn(10) * 0.3)
            open_ = close + np.random.randn(10) * 0.2
            volume = np.abs(np.random.randn(10) * 1e6 + 5e6)
            df = pd.DataFrame({
                "open": open_, "high": high, "low": low, "close": close, "volume": volume
            }, index=new_dates)
            new_data[sym] = df

        inc_panel = engine.update_latest(new_data, test_factors, n_days=10)
        print(f"✅ 增量面板形状: {inc_panel.shape}")


if __name__ == "__main__":
    main()
