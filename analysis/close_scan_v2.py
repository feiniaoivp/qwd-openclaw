#!/usr/bin/env python3
"""
收盘后全量扫描 v3.1 — 基于指标白名单重构
======================================================
核心原则（来自 wiki/sources/technical-analysis-core.md）：
  • 仅保留三大原子指标：支撑压力、成交量、均线
  • 核心辅助：MACD(零轴分多空)、ATR(仅风控)、斐波那契(共振时)、EMA8/21/50组合
  • 移除：RSI/KDJ/CCI、布林带、MACD柱状图斜率、其他震荡/波动类

数据源路由（MEMORY.md 2026-08-02 权威版）:
  实时行情: 新浪 hq.sinajs.cn (稳定) → pytdx → akshare
  K线历史:  新浪日K jsonp (稳定, 0.1s/只) → pytdx → akshare东财(单次不sleep)
  财务/新闻: akshare (同花顺/新浪/巨潮)

运行: python3 analysis/close_scan_v2.py [--intraday]
输出: JSON(stdout) + 可读简报(stderr)

已统一使用 analysis.data_layer.router.DataRouter (Phase 1 完成)
"""

from __future__ import annotations

import os
import sys
import json
import re
import time
import math
import logging
import socket
from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from functools import lru_cache

import pandas as pd

# 共享策略库 (2026-09-11): 中联重科双策略统一实现, 消除多脚本重复漂移
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from strategies import analyze_zhonglian as _analyze_zhonglian_shared
except ImportError:
    _analyze_zhonglian_shared = None  # 延迟到调用时报错

# 可选依赖
try:
    import pandas_ta as ta
    HAS_PANDAS_TA = True
except Exception:
    HAS_PANDAS_TA = False

try:
    import akshare as ak
    HAS_AKSHARE = True
except Exception:
    HAS_AKSHARE = False

try:
    from pytdx.hq import TdxHq_API
    HAS_PYTDX = True
except Exception:
    HAS_PYTDX = False

# 导入统一数据路由器
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)
from analysis.data_layer.router import get_router
from analysis.fib_extension_scan import compute_fib_time_targets
# 交易日判定收敛到 service 单一来源（2026-10-01 立项）：本文件不再自持实现
from analysis.service import is_trading_day
from analysis.data_layer.router import get_router

# ============================================================================
# 配置常量
# ============================================================================

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
STATE_FILE = os.path.join(WORKSPACE, "data", "zhonglian_state.json")
LIFECYCLE_FILE = os.path.join(WORKSPACE, "data", "signal_lifecycle.json")
NEWS_SENTIMENT_FILE = os.path.join(WORKSPACE, "data", "news", "sentiment_cache.json")

CAPITAL = 100_000
COMMISSION = 0.0003
SLIPPAGE = 0.001

# 关注股权威清单 —— 单一事实来源 (SSOT): analysis/fib_extension_scan.WATCHLIST
# 2026-10-12 起改为 SSOT 引用（此前本地 27 只与 SSOT 静默漂移：注释称 35 只但
# 电网出海 8 只从未加入）。intraday_alert / review_030_snapshot 经此处间接引用 SSOT。
# 格式: [(code, name), ...]，dict.items() 保序。
from analysis.fib_extension_scan import WATCHLIST as _SSOT_WATCHLIST

WATCHLIST = list(_SSOT_WATCHLIST.items())

# ============================================================================
# 日志配置
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

NO_DATA = "无"

def safe_float(val: Any, default: Any = NO_DATA) -> Any:
    if val is None or val == "" or val == "False" or val == "NaN":
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f) or abs(f) > 1e15:
            return default
        return f
    except (ValueError, TypeError):
        return default

def parse_amount(s: Any) -> Optional[float]:
    if s is None or s == "" or s == "False":
        return None
    s = str(s).strip()
    m = re.match(r"^([-\d.]+)\s*万亿$", s)
    if m: return float(m.group(1)) * 10000
    m = re.match(r"^([-\d.]+)\s*亿$", s)
    if m: return float(m.group(1))
    m = re.match(r"^([-\d.]+)\s*万$", s)
    if m: return float(m.group(1)) / 10000
    try: return float(s)
    except (ValueError, TypeError): return None

def parse_percent(s: Any) -> Optional[float]:
    if s is None or s == "" or s == "False": return None
    s = str(s).strip()
    m = re.match(r"^([-\d.]+)\s*%$", s)
    if m: return float(m.group(1))
    try: return float(s)
    except (ValueError, TypeError): return None

def convert_share(val: Any) -> Any:
    f = safe_float(val)
    if f == NO_DATA: return NO_DATA
    if f < 10000 and f > 0: return f / 10000
    return f / 100000000

def convert_sina_percent(val: Any) -> Any:
    f = safe_float(val)
    if f == NO_DATA: return NO_DATA
    if abs(f) < 2: return f * 100
    return f

# ============================================================================
# 统一数据获取器 - 使用 DataRouter (Phase 1)
# ============================================================================

_router_instance = None

def get_router_instance():
    global _router_instance
    if _router_instance is None:
        _router_instance = get_router()
    return _router_instance


def fetch_sina_spot(symbols: list[str]) -> dict[str, dict]:
    """批量拉取新浪实时行情 - 走 DataRouter"""
    router = get_router_instance()
    return router.get_spot(symbols)


def get_daily_hist(symbol: str, name: str,
                   start_date: str = "20250101") -> Optional[pd.DataFrame]:
    """统一历史日K获取 - 走 DataRouter (内含降级链: 新浪日K -> pytdx -> baostock -> akshare)"""
    router = get_router_instance()
    try:
        df = router.get_daily(symbol, name, start_date=start_date)
        if df is not None and len(df) >= 2:
            log.info(f"  ✅ {name}({symbol}): {len(df)} 条日线 [DataRouter]")
            return df
    except Exception as e:
        log.warning(f"  ⚠️ {name}({symbol}) DataRouter获取失败: {e}")
    return None


# ============================================================================
# 单例获取器 (全局复用登录/缓存)
# ============================================================================

_fetcher_instance: Optional[FinancialDataFetcher] = None

def get_fetcher() -> FinancialDataFetcher:
    global _fetcher_instance
    if _fetcher_instance is None:
        _fetcher_instance = FinancialDataFetcher()
    return _fetcher_instance


# ============================================================================
# 信号计算 — 仅白名单指标
# ============================================================================

# FinancialDataFetcher 类已废弃，保留接口兼容但内部委托 DataRouter
class FinancialDataFetcher:
    """兼容层：内部委托 DataRouter，保持原有接口不变"""

    def __init__(self):
        self._router = get_router_instance()

    def _ensure_bs_login(self) -> bool:
        return True  # DataRouter 内部管理

    def bs_logout(self):
        pass  # DataRouter 内部管理

    def _cache_get(self, key: str) -> Optional[Any]:
        return self._router.cache.get(key)

    def _cache_set(self, key: str, value: Any):
        self._router.cache.set(key, value)

    def fetch_sina_spot(self, symbols: list[str]) -> dict[str, dict]:
        return self._router.get_spot(symbols)

    def fetch_sina_daily(self, symbol: str, n: int = 300) -> Optional[pd.DataFrame]:
        # 委托 DataRouter (内含新浪日K作为首选)
        return self._router.sina_daily.get_daily(symbol, n=n)

    def fetch_pytdx_daily(self, symbol: str,
                          ip: str = "123.125.108.14", port: int = 7709) -> Optional[pd.DataFrame]:
        # DataRouter 内部已处理 pytdx 降级，不再单独暴露
        return None

    def fetch_akshare_daily(self, symbol: str, start_date: str = "20250101") -> Optional[pd.DataFrame]:
        # DataRouter 内部已处理 akshare 降级
        return None

    def fetch_baostock_daily(self, symbol: str, start_date: str = "20250101") -> Optional[pd.DataFrame]:
        return self._router.baostock.get_daily(symbol, start_date=start_date)

    def get_daily_hist(self, symbol: str, name: str,
                       start_date: str = "20250101") -> Optional[pd.DataFrame]:
        return get_daily_hist(symbol, name, start_date)


# ============================================================================
# 信号计算 — 仅白名单指标
# ============================================================================

def _pick_col(df: pd.DataFrame, *candidates: str) -> Optional[str]:
    """动态匹配 pandas_ta 列名，规避版本差异"""
    for c in candidates:
        if c in df.columns: return c
    for c in candidates:
        for col in df.columns:
            if c.lower() in col.lower(): return col
    return None

def calc_support_resistance(df: pd.DataFrame, lookback: int = 60) -> dict:
    """支撑压力位：成交密集区 + 前高低点 + 极性转换"""
    if len(df) < lookback: lookback = len(df)
    recent = df.tail(lookback)
    high = recent["high"].max()
    low = recent["low"].min()
    
    # 成交密集区 (简易版：价格分箱累积成交量)
    bins = 20
    price_range = high - low
    if price_range > 0:
        bin_edges = [low + i * price_range / bins for i in range(bins + 1)]
        # 2026-10-23 修复：pd.cut 默认左开区间 (low, ...]，当窗口内 min(close)==min(low)
        # （收盘恰在最低价边界）时该值被判 NaN → vol_profile 变 float64 →
        # idxmax() 返回 np.float64 → bin_edges[float] TypeError 崩溃
        # （越秀资本 10-22 晚/10-23 晨两次实证，error 行致下游 signal=null）。
        # include_lowest 让边界值归入 bin0（同时修止 poc 统计丢样本）；int() 兕底防御。
        vol_profile = pd.cut(recent["close"], bins=bin_edges, labels=False, include_lowest=True)
        vol_by_bin = recent.groupby(vol_profile)["volume"].sum()
        if not vol_by_bin.empty:
            poc_bin = int(vol_by_bin.idxmax())
            poc_price = (bin_edges[poc_bin] + bin_edges[poc_bin + 1]) / 2
        else:
            poc_price = (high + low) / 2
    else:
        poc_price = high
    
    # 近期高低点
    recent_high = recent["high"].rolling(5).max().iloc[-1]
    recent_low = recent["low"].rolling(5).min().iloc[-1]
    
    cur_price = float(df["close"].iloc[-1])
    
    return {
        "resistance_strong": round(float(high), 2),
        "resistance_recent": round(float(recent_high), 2),
        "poc": round(float(poc_price), 2),
        "support_recent": round(float(recent_low), 2),
        "support_strong": round(float(low), 2),
        "current_price": round(cur_price, 2),
        "dist_to_resist_pct": round((high - cur_price) / cur_price * 100, 2),
        "dist_to_support_pct": round((cur_price - low) / cur_price * 100, 2),
    }

def calc_volume_analysis(df: pd.DataFrame) -> dict:
    """量价分析：放量突破/缩量回调/量价背离"""
    vol = df["volume"]
    close = df["close"]
    vol_ma20 = vol.rolling(20).mean()
    vol_ma5 = vol.rolling(5).mean()
    
    cur_vol = float(vol.iloc[-1])
    cur_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    vol_ratio = cur_vol / float(vol_ma20.iloc[-1]) if float(vol_ma20.iloc[-1]) > 0 else 1.0
    
    price_up = cur_close > prev_close
    vol_up = vol_ratio > 1.2
    vol_shrink = vol_ratio < 0.6
    
    return {
        "volume": int(cur_vol),
        "vol_ma20": int(float(vol_ma20.iloc[-1])),
        "vol_ratio": round(vol_ratio, 2),
        "price_up": price_up,
        "volume_expand": vol_up,
        "volume_shrink": vol_shrink,
        "price_volume_align": price_up and vol_up,
        "divergence_bear": (not price_up) and vol_up,  # 价跌量增
        "divergence_bull": price_up and vol_shrink,    # 价涨量缩
    }

def calc_ema_trend(df: pd.DataFrame) -> dict:
    """多周期均线趋势状态 + 排列"""
    close = df["close"]
    ema20 = ta.ema(close, length=20) if HAS_PANDAS_TA else close.ewm(span=20, adjust=False).mean()
    ema50 = ta.ema(close, length=50) if HAS_PANDAS_TA else close.ewm(span=50, adjust=False).mean()
    ema200 = ta.ema(close, length=200) if HAS_PANDAS_TA else close.ewm(span=200, adjust=False).mean()
    
    cur_price = float(close.iloc[-1])
    e20 = float(ema20.iloc[-1]) if pd.notna(ema20.iloc[-1]) else cur_price
    e50 = float(ema50.iloc[-1]) if pd.notna(ema50.iloc[-1]) else cur_price
    e200 = float(ema200.iloc[-1]) if pd.notna(ema200.iloc[-1]) else cur_price
    
    # 均线排列
    bull_arrange = e20 > e50 > e200
    bear_arrange = e20 < e50 < e200
    
    # 价格相对位置
    above_20 = cur_price > e20
    above_50 = cur_price > e50
    above_200 = cur_price > e200
    
    # 斜率 (近5根)
    e20_slope = float(ema20.iloc[-1] - ema20.iloc[-6]) if len(ema20) >= 6 else 0
    e50_slope = float(ema50.iloc[-1] - ema50.iloc[-6]) if len(ema50) >= 6 else 0
    
    return {
        "EMA20": round(e20, 2),
        "EMA50": round(e50, 2),
        "EMA200": round(e200, 2),
        "price": round(cur_price, 2),
        "above_EMA20": above_20,
        "above_EMA50": above_50,
        "above_EMA200": above_200,
        "bull_arrange": bull_arrange,
        "bear_arrange": bear_arrange,
        "EMA20_slope": round(e20_slope, 4),
        "EMA50_slope": round(e50_slope, 4),
        "trend": "BULL" if bull_arrange else ("BEAR" if bear_arrange else "NEUTRAL"),
    }

def calc_macd_signal(df: pd.DataFrame) -> dict:
    """MACD：零轴分多空 + 金叉/死叉 + 背离"""
    if not HAS_PANDAS_TA:
        return {"error": "pandas_ta 未安装"}
    
    close = df["close"]
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    macd_col = _pick_col(macd_df, "MACD_12_26_9", "MACD")
    signal_col = _pick_col(macd_df, "MACDs_12_26_9", "MACD_SIGNAL")
    hist_col = _pick_col(macd_df, "MACDh_12_26_9", "MACD_HIST")
    if not macd_col or not signal_col:
        return {"error": "MACD 列名匹配失败"}
    
    macd_line = macd_df[macd_col]
    signal_line = macd_df[signal_col]
    hist = macd_df[hist_col] if hist_col else (macd_line - signal_line)
    
    macd_now = float(macd_line.iloc[-1])
    macd_prev = float(macd_line.iloc[-2])
    sig_now = float(signal_line.iloc[-1])
    sig_prev = float(signal_line.iloc[-2])
    hist_now = float(hist.iloc[-1])
    hist_prev = float(hist.iloc[-2])
    
    # 零轴位置
    above_zero = macd_now > 0 and sig_now > 0
    below_zero = macd_now < 0 and sig_now < 0
    
    # 金叉/死叉
    cross_up = (macd_prev <= sig_prev) and (macd_now > sig_now)
    cross_down = (macd_prev >= sig_prev) and (macd_now < sig_now)
    
    # 柱状图动能
    hist_expand = hist_now > hist_prev
    hist_shrink = hist_now < hist_prev
    
    # 背离检测 (简易：价格新高/新低 vs MACD未创新高/新低)
    lookback = min(20, len(df))
    price_high = df["high"].tail(lookback).max()
    price_low = df["low"].tail(lookback).min()
    macd_high = macd_line.tail(lookback).max()
    macd_low = macd_line.tail(lookback).min()
    
    cur_price = float(close.iloc[-1])
    cur_macd = macd_now
    
    bear_div = (cur_price >= price_high * 0.99) and (cur_macd < macd_high * 0.95)
    bull_div = (cur_price <= price_low * 1.01) and (cur_macd > macd_low * 1.05)
    
    return {
        "MACD": round(macd_now, 4),
        "SIGNAL": round(sig_now, 4),
        "HIST": round(hist_now, 4),
        "above_zero": above_zero,
        "below_zero": below_zero,
        "cross_up": cross_up,
        "cross_down": cross_down,
        "hist_expand": hist_expand,
        "hist_shrink": hist_shrink,
        "bear_divergence": bear_div,
        "bull_divergence": bull_div,
        "zone": "BULL" if above_zero else ("BEAR" if below_zero else "NEUTRAL"),
    }

def calc_atr_stops(df: pd.DataFrame, mult: float = 2.0) -> dict:
    """ATR止损位 + 动态仓位建议 (仅风控用)"""
    if not HAS_PANDAS_TA:
        return {"error": "pandas_ta 未安装"}
    
    atr_series = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else 0
    cur_price = float(df["close"].iloc[-1])
    
    stop_long = round(cur_price - mult * atr, 2)
    stop_short = round(cur_price + mult * atr, 2)
    
    # 仓位建议：单笔风险 1% 总资金
    risk_per_trade = 0.01
    shares_per_100k = int(100000 * risk_per_trade / (mult * atr) / 100) * 100 if atr > 0 else 0
    
    return {
        "ATR14": round(atr, 2),
        "stop_long": stop_long,
        "stop_short": stop_short,
        "risk_per_share": round(mult * atr, 2),
        "suggested_shares_per_100k": shares_per_100k,
        "vol_norm_factor": round(atr / cur_price, 4) if cur_price > 0 else 0,
    }

def calc_fibonacci_confluence(df: pd.DataFrame, sr: dict) -> dict:
    """斐波那契：仅当与支撑压力/EMA共振时输出"""
    high = float(df["high"].tail(60).max())
    low = float(df["low"].tail(60).min())
    diff = high - low
    
    levels = {
        "0.382": round(high - 0.382 * diff, 2),
        "0.500": round(high - 0.500 * diff, 2),
        "0.618": round(high - 0.618 * diff, 2),
        "1.000": round(low, 2),
        "1.272": round(low - 0.272 * diff, 2),
        "1.618": round(low - 0.618 * diff, 2),
    }
    
    cur_price = float(df["close"].iloc[-1])
    # 检查共振：斐波那契位是否靠近支撑压力位或EMA
    confluence = []
    for name, level in levels.items():
        if abs(level - sr.get("resistance_strong", 0)) / cur_price < 0.02:
            confluence.append(f"{name}@{level}≈强压力")
        if abs(level - sr.get("support_strong", 0)) / cur_price < 0.02:
            confluence.append(f"{name}@{level}≈强支撑")
        # EMA共振后续在综合评分时检查
    
    return {
        "levels": levels,
        "confluence": confluence,
    }

def calc_ema_combo_8_21_50(df: pd.DataFrame) -> dict:
    """EMA 8/21/50 组合策略"""
    close = df["close"]
    ema8 = ta.ema(close, length=8) if HAS_PANDAS_TA else close.ewm(span=8, adjust=False).mean()
    ema21 = ta.ema(close, length=21) if HAS_PANDAS_TA else close.ewm(span=21, adjust=False).mean()
    ema50 = ta.ema(close, length=50) if HAS_PANDAS_TA else close.ewm(span=50, adjust=False).mean()
    
    e8 = float(ema8.iloc[-1]) if pd.notna(ema8.iloc[-1]) else 0
    e21 = float(ema21.iloc[-1]) if pd.notna(ema21.iloc[-1]) else 0
    e50 = float(ema50.iloc[-1]) if pd.notna(ema50.iloc[-1]) else 0
    cur_price = float(close.iloc[-1])
    
    bull = cur_price > e50 and e8 > e21 > e50
    bear = cur_price < e50 and e8 < e21 < e50
    pullback_buy_zone = bull and (cur_price <= e8 * 1.02) and (cur_price >= e21 * 0.98)
    
    return {
        "EMA8": round(e8, 2),
        "EMA21": round(e21, 2),
        "EMA50": round(e50, 2),
        "bull_setup": bull,
        "bear_setup": bear,
        "pullback_buy_zone": pullback_buy_zone,
    }

def apply_shock_decay(score: float, daily_return: float, flow_shock: bool = False) -> float:
    """
    [2026-10-29] 权重剧减弹性防御机制
    防止在单日极端负面冲击时，因慢指标粘性导致信号钝化（接飞刀）。
    """
    decay = 1.0
    if daily_return <= -0.08:
        decay = 0.4  # 极端冲击: 显著禁入
    elif daily_return <= -0.05:
        decay = 0.6  # 严重冲击: 降级至观察
    elif daily_return <= -0.03:
        decay = 0.8  # 轻微冲击: 预警
    
    if flow_shock:
        decay *= 0.9  # 资金流出共振额外惩罚
    return score * decay

def apply_temporal_decay(symbol: str, score: float, level: str, current_date_str: str) -> tuple[float, str, list]:
    """
    [2026-10-30] 时间衰减机制 (Temporal Decay)
    记录 🟢 强烈买入 信号的 birth date，随时间推移阶梯式衰减其权重。
    """
    if level != "🟢 强烈买入":
        return score, level, []

    # 1. 加载/更新生命周期状态
    lifecycle = {}
    if os.path.exists(LIFECYCLE_FILE):
        try:
            with open(LIFECYCLE_FILE, "r") as f:
                lifecycle = json.load(f)
        except Exception as e:
            # 损坏即重建会重置全部 birth date（衰减跟踪静默清零），必须可见
            logging.warning("signal_lifecycle.json 读取失败，本轮按空状态重建: %s", e)

    # 如果是新信号，记录 birth date
    if symbol not in lifecycle:
        lifecycle[symbol] = {"birth_date": current_date_str, "initial_score": score}
    
    birth_date_str = lifecycle[symbol]["birth_date"]
    birth_date = datetime.strptime(birth_date_str, "%Y-%m-%d")
    cur_date = datetime.strptime(current_date_str, "%Y-%m-%d")
    
    # 计算交易日差值（简化为自然日）
    days_diff = (cur_date - birth_date).days
    
    # 2. 阶梯衰减曲线
    decay_factor = 1.0
    status = "Fresh"
    if 3 < days_diff <= 6:
        decay_factor, status = 0.7, "Stale"
    elif 6 < days_diff <= 10:
        decay_factor, status = 0.4, "Obsolete"
    elif days_diff > 10:
        decay_factor, status = 0.0, "Expired"

    new_score = score * decay_factor
    
    # 3. 重新映射等级
    if new_score >= 20: new_level = "🟢 强烈买入"
    elif new_score >= 10: new_level = "🟢 关注"
    elif new_score >= 0: new_level = "🟡 中性"
    else: new_level = "🔴 强烈卖出"

    # 持久化（写失败 = 衰减时钟静默停摆，必须可见）
    try:
        with open(LIFECYCLE_FILE, "w") as f:
            json.dump(lifecycle, f, indent=2)
    except Exception as e:
        logging.warning("signal_lifecycle.json 写入失败: %s", e)

    decay_msg = f"时间衰减({status}: {days_diff}d, x{decay_factor})" if decay_factor < 1.0 else ""
    return new_score, new_level, [decay_msg] if decay_msg else []

def get_news_sentiment(symbol: str, name: str, lookback_days: int = 5) -> dict:
    """
    获取个股新闻情绪评分
    
    返回: {
        "score": float,        # 情绪分 -2 到 +2
        "label": str,          # 标签
        "count": int,          # 新闻条数
        "positive": int,       # 利好条数
        "negative": int,       # 利空条数
        "details": list        # 明细
    }
    """
    try:
        router = get_router()
        df_news = router.get_stock_news(symbol)
        if df_news is None or df_news.empty:
            return {"score": 0, "label": "无新闻", "count": 0, "positive": 0, "negative": 0, "details": []}
        
        # 解析发布时间，只取近 N 天
        cutoff = datetime.now() - timedelta(days=lookback_days)
        
        positive_keywords = [
            r"回购", r"增持", r"预增", r"业绩.*增长", r"扭亏", r"重大合同", r"中标",
            r"战略合作", r"重组", r"分红", r"派息", r"业绩超预期", r"净利润.*增"
        ]
        negative_keywords = [
            r"减持", r"预亏", r"业绩.*下降", r"巨额亏损", r"立案", r"调查", r"违规",
            r"处罚", r"警示", r"风险提示", r"业绩暴雷", r"净利润.*亏"
        ]
        
        pos_patterns = [re.compile(kw, re.IGNORECASE) for kw in positive_keywords]
        neg_patterns = [re.compile(kw, re.IGNORECASE) for kw in negative_keywords]
        
        positive_count = 0
        negative_count = 0
        details = []
        
        for _, row in df_news.iterrows():
            title = str(row.get("新闻标题", ""))
            pub_time_str = str(row.get("发布时间", ""))
            
            # 解析时间
            pub_time = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    pub_time = datetime.strptime(pub_time_str[:len(fmt)], fmt)
                    break
                except ValueError:
                    continue
            
            if pub_time and pub_time < cutoff:
                continue
            
            # 关键词匹配
            is_positive = any(p.search(title) for p in pos_patterns)
            is_negative = any(p.search(title) for p in neg_patterns)
            
            if is_positive and not is_negative:
                positive_count += 1
                details.append({"title": title, "time": pub_time_str, "sentiment": "positive"})
            elif is_negative and not is_positive:
                negative_count += 1
                details.append({"title": title, "time": pub_time_str, "sentiment": "negative"})
            elif is_positive and is_negative:
                # 混合信号，按权重判断
                details.append({"title": title, "time": pub_time_str, "sentiment": "mixed"})
        
        total = positive_count + negative_count
        if total == 0:
            return {"score": 0, "label": "中性/无关键词", "count": len(df_news), "positive": 0, "negative": 0, "details": details}
        
        # 评分：-2 到 +2
        # 利好 +1~2，利空 -1~-2
        net = positive_count - negative_count
        if net >= 2:
            score = 2
        elif net >= 1:
            score = 1
        elif net <= -2:
            score = -2
        elif net <= -1:
            score = -1
        else:
            score = 0
        
        if score > 0:
            label = f"利好({positive_count}条)"
        elif score < 0:
            label = f"利空({negative_count}条)"
        else:
            label = "中性"
        
        return {
            "score": score,
            "label": label,
            "count": total,
            "positive": positive_count,
            "negative": negative_count,
            "details": details
        }
    except Exception as e:
        logging.warning(f"get_news_sentiment({symbol}) 失败: {e}")
        return {"score": 0, "label": "获取失败", "count": 0, "positive": 0, "negative": 0, "details": []}


def calc_full_signal_whitelist(df: pd.DataFrame) -> dict:
    """完整技术指标信号 —— 仅白名单指标 + 新闻情绪"""
    if len(df) < 60:
        return {"error": "历史数据不足 60 根"}
    
    # 从 DataFrame 属性或外部传入获取 symbol/name（close_scan_v2.py main() 中已知）
    # 这里通过全局变量或上下文获取，简化：假设调用者会在外部计算 news_sentiment 并注入
    # 实际集成见 main() 中的调用逻辑
    
    # 1. 三大原子指标
    sr = calc_support_resistance(df)
    vol = calc_volume_analysis(df)
    ema_trend = calc_ema_trend(df)
    
    # 2. 核心辅助
    macd = calc_macd_signal(df)
    atr = calc_atr_stops(df)
    fib = calc_fibonacci_confluence(df, sr)
    ema_combo = calc_ema_combo_8_21_50(df)
    
    cur_price = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])
    change_pct = round((cur_price / prev_close - 1) * 100, 2)
    
    # 3. 综合评分 (技术分析核心知识库 §5.3 权重)
    score = 0
    reasons = []
    risks = []
    
    # 趋势分 (40%)
    if ema_trend["bull_arrange"]: score += 8; reasons.append("多头排列")
    elif ema_trend["bear_arrange"]: score -= 8; risks.append("空头排列")
    if ema_trend["above_EMA200"]: score += 5; reasons.append("站上年线")
    else: score -= 5; risks.append("跌破年线")
    if ema_trend["EMA20_slope"] > 0: score += 3; reasons.append("EMA20上行")
    if ema_trend["above_EMA50"]: score += 4; reasons.append("站上半年线")
    
    # 动能分 (25%)
    if macd.get("above_zero"): score += 5; reasons.append("MACD零轴上")
    if macd.get("cross_up"): score += 8; reasons.append("MACD金叉")
    if macd.get("cross_down"): score -= 10; risks.append("MACD死叉")
    if macd.get("bull_divergence"): score += 5; reasons.append("MACD底背离")
    if macd.get("bear_divergence"): score -= 5; risks.append("MACD顶背离")
    
    # 位置分 (20%)
    dist_resist = sr.get("dist_to_resist_pct", 100)
    dist_support = sr.get("dist_to_support_pct", 100)
    if dist_support < 5: score += 6; reasons.append("临近强支撑")
    if dist_resist < 3: score -= 4; risks.append("临近强压力")
    if fib.get("confluence"): score += 4; reasons.append("斐波那契共振:" + ";".join(fib["confluence"]))
    if ema_combo.get("pullback_buy_zone"): score += 6; reasons.append("EMA8/21/50回踩买入区")
    
    # 风控分 (15%)
    if vol.get("price_volume_align"): score += 4; reasons.append("量价配合")
    if vol.get("volume_shrink") and not vol.get("price_up"): score += 2; reasons.append("缩量回调")
    if vol.get("divergence_bear"): score -= 3; risks.append("价跌量增背离")
    
    # ATR止损合理性
    atr_stop = atr.get("stop_long", 0)
    if atr_stop > 0 and atr_stop < cur_price:
        rr = (sr.get("resistance_strong", cur_price*1.1) - cur_price) / (cur_price - atr_stop)
        if rr >= 2: score += 3; reasons.append(f"盈亏比{rr:.1f}:1")
    
    # --- 新闻情绪因子 (2026-11-03 新增) ---
    # 从 indicators 注入的 news_sentiment 读取（由 main() 计算并注入）
    news_sentiment = df.attrs.get("news_sentiment") if hasattr(df, "attrs") else None
    if news_sentiment and isinstance(news_sentiment, dict):
        ns_score = news_sentiment.get("score", 0)
        if ns_score != 0:
            score += ns_score  # -2 到 +2 直接加到总分
            if ns_score > 0:
                reasons.append(f"新闻情绪利好({news_sentiment.get('positive',0)}条)")
            else:
                risks.append(f"新闻情绪利空({news_sentiment.get('negative',0)}条)")
    
    # --- Shock Filter 权重衰减 (2026-10-29) ---
    daily_ret = (cur_price / prev_close) - 1
    score = apply_shock_decay(score, daily_ret, flow_shock=False)
    if daily_ret <= -0.03:
        risks.append(f"Shock_Filter衰减(跌幅{change_pct}%)")
    
    # 等级判定
    if score >= 20: level = "🟢 强烈买入"
    elif score >= 10: level = "🟢 关注"
    elif score >= 0: level = "🟡 中性"
    elif score >= -10: level = "🟠 谨慎"
    else: level = "🔴 强烈卖出"
    
    data_date = df["date"].iloc[-1]
    if hasattr(data_date, "date"): data_date = str(data_date.date())
    else: data_date = str(data_date)
    
    return {
        "price": round(cur_price, 2),
        "change_pct": change_pct,
        "volume": int(df["volume"].iloc[-1]),
        "indicators": {
            "support_resistance": sr,
            "volume": vol,
            "ema_trend": ema_trend,
            "macd": macd,
            "atr": atr,
            "fibonacci": fib,
            "ema_combo": ema_combo,
        },
        "signal": {
            "level": level,
            "score": score,
            "reasons": "; ".join(reasons) if reasons else "无",
            "risks": "; ".join(risks) if risks else "无",
        },
        "data_date": data_date,
    }

def calc_spot_signal(spot: dict) -> dict:
    """基于 spot 实时数据快速判定信号方向 (无历史量对比)"""
    chg = spot.get("change_pct", 0)
    score = 0
    reasons = []
    risks = []
    
    if chg > 3: score += 2; reasons.append(f"涨幅{chg:.1f}%")
    elif chg > 1: score += 1; reasons.append(f"微涨{chg:.1f}%")
    elif chg < -5: score -= 2; risks.append(f"跌幅{chg:.1f}%")
    elif chg < -2: score -= 1; risks.append(f"下跌{chg:.1f}%")
    
    if score >= 2: level = "🟢 强烈买入"
    elif score >= 1: level = "🟢 关注"
    elif score == 0: level = "🟡 中性"
    elif score == -1: level = "🟠 谨慎"
    else: level = "🔴 强烈卖出"
    
    return {
        "price": spot["price"],
        "change_pct": spot["change_pct"],
        "volume": spot["volume"],
        "amount": spot["amount"],
        "signal": {
            "level": level,
            "score": score,
            "reasons": "; ".join(reasons) if reasons else "无",
            "risks": "; ".join(risks) if risks else "无",
        }
    }


# ============================================================================
# 中联重科双策略 (保留原逻辑)
# ============================================================================

def analyze_zhonglian(df: pd.DataFrame) -> dict:
    """中联重科双策略 (共享实现 -> analysis/strategies.py, 风格: plain)。

    2026-09-11: 原三处重复实现已统一, 消除漂移风险 (曾导致同一卖出信号卡死 bug 复发三次)。
    """
    return _analyze_zhonglian_shared(df, macd_state_style="plain")

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "last_signal_date": None,
        "strategy1": {"name": "MACD+RSI<50", "position": False, "entry_price": 0, "entry_date": None,
                      "capital": CAPITAL, "shares": 0},
        "strategy2": {"name": "综合最优(EMA+MACD+RSI)", "position": False, "entry_price": 0, "entry_date": None,
                      "capital": CAPITAL, "shares": 0},
    }

def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    import shutil
    if os.path.exists(STATE_FILE):
        shutil.copy(STATE_FILE, STATE_FILE + ".bak")
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)

def execute_trade(state: dict, signals: dict) -> tuple[list, list, dict, float, float]:
    price = signals["price"]
    dt = signals["date"]
    msgs = []
    actions = []

    for key, s_name in [("strategy1", "策略1"), ("strategy2", "策略2")]:
        s = state[key]
        sig = signals["signals"][key]
        if sig["buy"] and not s["position"]:
            fee = s["capital"] * COMMISSION
            available = s["capital"] - fee
            shares = int(available / (price * (1 + SLIPPAGE)) / 100) * 100
            if shares >= 100:
                cost = shares * price * (1 + SLIPPAGE)
                fee2 = cost * COMMISSION
                s["capital"] -= (cost + fee2)
                s["shares"] = shares
                s["position"] = True
                s["entry_price"] = price
                s["entry_date"] = dt
                actions.append((key, "BUY"))
                msgs.append(f"🟢 {s_name} 买入 ¥{price} x {shares}股")
        elif sig["sell"] and s["position"]:
            sell_value = s["shares"] * price * (1 - SLIPPAGE)
            fee = sell_value * COMMISSION
            net = sell_value - fee
            pnl = round(net - s["shares"] * s["entry_price"] * (1 + SLIPPAGE), 2)
            s["capital"] += net
            s["shares"] = 0
            s["position"] = False
            emoji = "🟢" if pnl > 0 else "🔴"
            actions.append((key, "SELL"))
            msgs.append(f"{emoji} {s_name} 卖出 ¥{price} 盈亏{pnl}")

    total_val = (state["strategy1"]["capital"] + (state["strategy1"]["shares"] * price if state["strategy1"]["position"] else 0) +
                 state["strategy2"]["capital"] + (state["strategy2"]["shares"] * price if state["strategy2"]["position"] else 0))
    total_return = round((total_val - CAPITAL * 2) / (CAPITAL * 2) * 100, 2)
    return actions, msgs, state, round(total_val, 2), total_return


# ============================================================================
# 盘中策略预警 (保留原逻辑)
# ============================================================================

STRATEGY_ALERT_CONFIG = os.path.join(WORKSPACE, "data", "intraday_alert_config.json")
INTRADAY_STATE_FILE = os.path.join(WORKSPACE, "data", "intraday_alert_state.json")

def load_strategy_alert_config() -> dict:
    if not os.path.exists(STRATEGY_ALERT_CONFIG): return {}
    try:
        with open(STRATEGY_ALERT_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"策略预警配置读取失败: {e}")
        return {}


# 盘中预警价位派生溯源（最近一次 evaluate_strategy_alerts 的口径来源，供输出层引用）
_LAST_LEVELS_PROVENANCE: dict = {}


def find_latest_close_scan_json() -> Optional[str]:
    """最新收盘全池扫描 json（analysis/daily/*_close_scan.json 按 mtime 取最新）。"""
    import glob
    cands = glob.glob(os.path.join(WORKSPACE, "analysis", "daily", "*_close_scan.json"))
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def derive_alert_levels(scan_path: Optional[str] = None) -> tuple[dict, dict]:
    """盘中预警价位自动派生（根除「静态配置 vs 移动市场」漂移，2026-10-16 用户批准）。

    映射（全部来自最新 close_scan 全池实算，随每日 18:30 扫描刷新）:
      break_below  = stop_long    (ATR 纪律止损)
      buy_zone_low = support      (近期支撑 → 低吸观察区上沿)
      break_above  = resistance   (近期阻力 → 突破确认线)
    drop_pct/surge_pct 为百分比规则不随价漂移，仍走静态配置。

    返回 (levels_by_code, provenance):
      levels_by_code: {code: {"break_below": v, "buy_zone_low": v, "break_above": v}}
      provenance: {source, data_date, scan_run_at, coverage, stale, warning}
    无可用 json 时返回 ({}, provenance[source=None])，调用方必须显式降级标注，
    禁止静默回退旧价位（对齐「数据降级必须显式标注」红线）。
    """
    prov = {"source": None, "data_date": None, "scan_run_at": None,
            "coverage": 0, "stale": False, "warning": None}
    path = scan_path or find_latest_close_scan_json()
    if not path or not os.path.exists(path):
        prov["warning"] = "未找到任何 *_close_scan.json，预警价位降级为静态配置（可能陈旧）"
        log.warning(prov["warning"])
        return {}, prov
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        prov["warning"] = f"close_scan json 解析失败: {e}；预警价位降级为静态配置"
        log.warning(prov["warning"])
        return {}, prov
    prov["source"] = os.path.relpath(path, WORKSPACE)
    prov["data_date"] = data.get("data_date")
    prov["scan_run_at"] = data.get("scan_run_at")
    # 陈旧判定：data_date 应等于最近一个已收盘交易日（is_trading_day SSOT）
    try:
        now = datetime.now()
        d = now.date()
        # 18:30 扫描约 19:00 前落地：此前 data_date=T-1 属正常，不应误报 stale
        if is_trading_day(d) and now.hour >= 19:
            expected = d
        else:
            expected = d - timedelta(days=1)
            for _ in range(10):
                if is_trading_day(expected):
                    break
                expected = expected - timedelta(days=1)
        if data.get("data_date") and str(data.get("data_date")) < str(expected):
            prov["stale"] = True
            prov["warning"] = (f"close_scan 数据日期 {data.get('data_date')} 早于最近已收盘交易日 {expected}，"
                               f"预警价位可能滞后；建议先跑 close_scan_v2 刷新")
            log.warning(prov["warning"])
    except Exception as e:
        log.warning(f"预警价位陈旧判定失败（不影响派生）: {e}")

    levels_by_code: dict = {}
    for s in data.get("stocks", []):
        code = str(s.get("symbol") or "")
        if not code:
            continue
        lv = {}
        stop = safe_float(s.get("stop_long"), default=None)
        sup = safe_float(s.get("support"), default=None)
        res = safe_float(s.get("resistance"), default=None)
        if stop and float(stop) > 0:
            lv["break_below"] = round(float(stop), 2)
        if sup and float(sup) > 0:
            lv["buy_zone_low"] = round(float(sup), 2)
        if res and float(res) > 0:
            lv["break_above"] = round(float(res), 2)
        if lv:
            levels_by_code[code] = lv
    prov["coverage"] = len(levels_by_code)
    if not levels_by_code:
        prov["warning"] = "close_scan json 无有效价位字段（stop_long/support/resistance 全缺），降级静态配置"
        log.warning(prov["warning"])
    return levels_by_code, prov

def _load_intraday_state() -> dict:
    if os.path.exists(INTRADAY_STATE_FILE):
        try:
            with open(INTRADAY_STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"fired": {}}
    return {"fired": {}}

def _save_intraday_state(state: dict):
    os.makedirs(os.path.dirname(INTRADAY_STATE_FILE), exist_ok=True)
    with open(INTRADAY_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

_ALERT_LABEL = {"break_below": "跌破止损", "break_above": "升破关键位", "buy_zone": "低吸区", 
                "drop_pct": "急跌预警", "surge_pct": "急涨预警"}

def _mk_alert(code: str, item: dict, scfg: dict, level, msg: str, severity: str) -> dict:
    return {"code": code, "symbol": item["symbol"], "name": scfg.get("name", item["symbol"]),
            "price": float(item["price"]), "level": level, "change_pct": float(item["change_pct"]),
            "msg": msg, "action": scfg.get("action", ""), "type": scfg.get("type", ""),
            "level_name": _ALERT_LABEL.get(code, code), "severity": severity}

def evaluate_strategy_alerts(spot_results: list[dict]) -> tuple[list[dict], list[dict]]:
    """盘中策略预警评估。价位自 2026-10-16 起从最新 close_scan json 自动派生
    （break_below=stop_long / buy_zone_low=support / break_above=resistance，
    随每日 18:30 扫描刷新）；json 缺失时显式降级静态配置并标注
    levels_src=static_config(降级)。静态 levels.lock=true 可豁免覆盖。"""
    cfg = load_strategy_alert_config()
    stocks_cfg = cfg.get("stocks", {})
    derived, prov = derive_alert_levels()
    global _LAST_LEVELS_PROVENANCE
    _LAST_LEVELS_PROVENANCE = prov
    state = _load_intraday_state()
    fired = state.get("fired", {})
    alerts = []
    triggered = []

    for item in spot_results:
        if "error" in item: continue
        sym = item["symbol"]
        raw_cfg = stocks_cfg.get(sym)
        dlv = derived.get(sym) or {}
        if not raw_cfg and not dlv:
            continue
        # 池内新成员（不在静态配置）自动覆盖：合成元数据 + 默认百分比监控
        scfg = dict(raw_cfg) if raw_cfg else {"name": item.get("name", sym),
                                              "action": "策略预警", "type": "scan_derived",
                                              "drop_pct": -5.0, "surge_pct": 5.0}
        # 17:55 撞车合并修复：lock 豁免时 levels_src 必须如实标 static_config(lock)，
        # 不得标 scan:*（实际用的是静态人工开关位）
        _locked = bool((raw_cfg or {}).get("levels", {}).get("lock"))
        if dlv and not _locked:
            scfg["levels"] = dict(dlv)  # 派生价位整组覆盖；drop_pct/surge_pct 在 cfg 顶层不受影响
        if dlv and not _locked:
            levels_src = f"scan:{prov['data_date']}"
        elif _locked:
            levels_src = "static_config(lock)"
        else:
            levels_src = "static_config(降级)"
        price = float(item["price"])
        chg = float(item["change_pct"])
        levels = scfg.get("levels", {})
        action = scfg.get("action", "")
        type_ = scfg.get("type", "")

        bl = levels.get("break_below")
        if bl is not None and price > 0 and price <= bl:
            msg = f"跌破止损位{bl:.2f} (现{price:.2f}) 应变{action}"
            alert = _mk_alert("break_below", item, scfg, bl, msg, "HIGH")
            alert["levels_src"] = levels_src
            alerts.append(alert)
            if fired.get(f"break_below:{sym}") != price:
                triggered.append(alert)
                fired[f"break_below:{sym}"] = price

        ba = levels.get("break_above")
        if ba is not None and price > 0 and price >= ba:
            msg = f"升破关键位{ba:.2f} (现{price:.2f}) 应{action}"
            alert = _mk_alert("break_above", item, scfg, ba, msg, "MEDIUM")
            alert["levels_src"] = levels_src
            alerts.append(alert)
            if fired.get(f"break_above:{sym}") != price:
                triggered.append(alert)
                fired[f"break_above:{sym}"] = price

        blz = levels.get("buy_zone_low")
        if blz is not None and price > 0 and (bl is None or price > bl) and price <= blz:
            msg = f"进入买入参考区 (现{price:.2f} 接近{blz:.2f}) 可考虑低吸"
            alert = _mk_alert("buy_zone", item, scfg, blz, msg, "LOW")
            alert["levels_src"] = levels_src
            alerts.append(alert)
            if fired.get(f"buy_zone:{sym}") != price:
                triggered.append(alert)
                fired[f"buy_zone:{sym}"] = price

        dd = scfg.get("drop_pct")
        if dd is not None and chg <= dd:
            msg = f"日内跌超{abs(dd):.1f}% ({chg:.1f}%) 注意风控"
            alert = _mk_alert("drop_pct", item, scfg, dd, msg, "HIGH")
            alert["levels_src"] = levels_src
            alerts.append(alert)
            if fired.get(f"drop_pct:{sym}") != chg:
                triggered.append(alert)
                fired[f"drop_pct:{sym}"] = chg

        sg = scfg.get("surge_pct")
        if sg is not None and chg >= sg:
            msg = f"日内涨超{sg:.1f}% ({chg:.1f}%) 注意止盈/追高风险"
            alert = _mk_alert("surge_pct", item, scfg, sg, msg, "MEDIUM")
            alert["levels_src"] = levels_src
            alerts.append(alert)
            if fired.get(f"surge_pct:{sym}") != chg:
                triggered.append(alert)
                fired[f"surge_pct:{sym}"] = chg

    state["fired"] = fired
    _save_intraday_state(state)
    return alerts, triggered

def run_intraday_alert_mode():
    fetcher = get_fetcher()
    sina_symbols = [("sh" if c[0] in "69" else "sz") + c for c, _ in WATCHLIST]
    hq = fetcher.fetch_sina_spot(sina_symbols)
    spot_results = []
    for code, name in WATCHLIST:
        r = hq.get(code)
        if r is None or r.get("close", 0) == 0 or r["pre_close"] == 0: continue
        chg = (r.get("close", 0) / r["pre_close"] - 1) * 100
        spot_results.append({"symbol": code, "name": name, "price": round(r.get("close", 0), 2),
                             "change_pct": round(chg, 2)})
    alerts, triggered = evaluate_strategy_alerts(spot_results)
    out = {
        "mode": "intraday_alert",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scanned": len(spot_results),
        "total_alert_rules": len(alerts),
        "alerts": alerts,
        "triggered_this_run": triggered,
        "levels_provenance": dict(_LAST_LEVELS_PROVENANCE),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


# ============================================================================
# 资金流采集（东财，curl_cffi Chrome TLS 指纹）
# ============================================================================

def collect_moneyflow(codes: list[str], name_map: dict[str, str], days: int = 3) -> dict:
    """
    采集个股 + 行业 + 北向资金流。

    设计原则：**失败不阻断主流程**，任一子项异常只记录到 error 字段。
    返回：{
      "stocks": {code: {main, xlarge, large, main_pct, cum3, name}},
      "industry_inflow": [...], "industry_outflow": [...],
      "market_net_total": float,
      "northbound": {...}, "errors": [...]
    }
    """
    out: dict[str, Any] = {"stocks": {}, "errors": []}
    try:
        from analysis.moneyflow_em import (
            stock_flow, industry_flow_rank, market_main_flow, northbound_summary,
        )
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"moneyflow_em import: {e}")
        return out

    # ── 1) 个股主力资金流 ──
    # 2026-10-09：先做一次 datacenter 批量预热（1 次请求覆盖整池）。push2 系 fflow
    # 路径被掐断时，moneyflow_em 会自动逐票回落到 datacenter；预热可把 N 次请求压成 1 次。
    #   注意：dc_refresh_batch 成功**不代表** push2 挂了（datacenter 平时也可用），
    #   故降级告警只看下面逐票结果的 degraded 标记，不看预热结果。
    try:
        from analysis.moneyflow_em import dc_refresh_batch
        dc_refresh_batch(list(codes))
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"moneyflow prewarm: {e}")

    for code in codes:
        try:
            d = stock_flow(code, lmt=days)
            if not d or not d.get("days"):
                # 三路（push2 实时 / daykline / datacenter）全空才到这里。既往仅标注
                # rc=100/data=null，现在也可能是网络全断，故如实带上排查方向。
                out["errors"].append(
                    f"{code}: 资金流三路全空(上游无数据 rc=100 或网络全断)")
                continue
            last = d["days"][-1]
            n_days = len(d["days"])
            cum = sum(x.get("main") or 0 for x in d["days"])
            out["stocks"][code] = {
                "name": name_map.get(code, d.get("name") or ""),
                "date": last["date"],
                "main": round((last.get("main") or 0) / 1e8, 4),
                "xlarge": round((last.get("xlarge") or 0) / 1e8, 4) if last.get("xlarge") is not None else None,
                "large": round((last.get("large") or 0) / 1e8, 4) if last.get("large") is not None else None,
                "main_pct": last.get("main_pct"),
                # ⚠️ cum 的天数**不固定**：健康态=days 天，datacenter 兜底态=仅 1 天。
                #    渲染层必须按 days_n 标注口径，禁止把 cum 一律当"3 日累计"。
                "cum": round(cum / 1e8, 4),
                "days_n": n_days,
                "degraded": bool(d.get("_degraded")),
            }
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"{code}: {e}")

    # 口径告警：只要有票走兜底，整块资金流就必须显式标半口径（禁止静默混口径）。
    # ⚠️ 走**独立字段**而非 errors[]：errors 会被渲染层当"未取到 N 只"播报，
    #    而兜底票**是有数据的**（只是口径窄），两者语义完全不同，混用会误报缺数。
    _degraded = [c for c, v in out["stocks"].items() if v.get("degraded")]
    if _degraded:
        out["degraded_note"] = (
            f"{len(_degraded)}/{len(codes)} 只走 datacenter 兜底"
            "（仅当日快照、无中/小单拆分、cum 为单日值）")
        time.sleep(0.2)  # 东财限流保护

    # ── 2) 行业板块（全市场口径首选）──
    try:
        mk = market_main_flow(6)
        out["industry_inflow"] = [
            {"name": r["name"], "main": round((r["main_net"] or 0) / 1e8, 2),
             "pct": r.get("main_pct")} for r in mk.get("inflow_top", [])[:10]
        ]
        out["industry_outflow"] = [
            {"name": r["name"], "main": round((r["main_net"] or 0) / 1e8, 2),
             "pct": r.get("main_pct")} for r in mk.get("outflow_top", [])
        ]
        out["market_net_total"] = round((mk.get("net_total") or 0) / 1e8, 2)
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"industry: {e}")

    # ── 3) 北向（注：净买入已停披，仅成交额）──
    try:
        nb = northbound_summary(3)
        out["northbound"] = {
            "available": nb.get("available", False),
            "net_flow_discontinued": nb.get("net_flow_discontinued", False),
            "note": nb.get("note", ""),
            "days": [{"date": r["date"], "deal_amt": r["deal_amt"]}
                     for r in nb.get("days", [])],
        }
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"northbound: {e}")

    # ── 4) 关注池资金流汇总（排序后取 TOP）──
    if out["stocks"]:
        ranked = sorted(out["stocks"].items(), key=lambda kv: -(kv[1].get("main") or 0))
        out["top_inflow"] = [{"code": k, **v} for k, v in ranked[:5]]
        out["top_outflow"] = [{"code": k, **v} for k, v in ranked[-5:]][::-1]

    return out


# ============================================================================
# 交易日判断
# ============================================================================
# （交易日判定已收敛到 analysis.service.is_trading_day 单一来源，2026-10-01 立项）

# ============================================================================
# 主入口
# ============================================================================

def main(target_date: str = None):
    today = target_date or datetime.now().strftime("%Y-%m-%d")
    fetcher = get_fetcher()

    output = {
        "date": today,
        "is_trading_day": True,
        "data_source": "spot_scan",
        "watchlist_scan": [],
        "zhonglian": None,
        "zhonglian_actions": [],
        "zhonglian_messages": [],
        "summary": {"strong_buy": 0, "watch": 0, "neutral": 0, "caution": 0, "strong_sell": 0, "fail_count": 0},
        "market_overview": {},
        "fib_time_windows": [],
    }

    if not is_trading_day():
        output["is_trading_day"] = False

    # ── Step 1: 极速 spot 扫描 (新浪 hq.sinajs.cn) ──
    log.info("⚡ 新浪 hq.sinajs.cn 行情扫描...")
    sina_symbols = [("sh" if c[0] in "69" else "sz") + c for c, _ in WATCHLIST]
    hq = fetcher.fetch_sina_spot(sina_symbols)
    data_date = ""
    spot_results = []

    for code, name in WATCHLIST:
        r = hq.get(code)
        if r is None or r.get("close", 0) == 0:
            spot_results.append({"symbol": code, "name": name, "error": "无行情"})
            continue
        if not data_date: data_date = r["date"]
        chg = (r.get("close", 0) / r["pre_close"] - 1) * 100 if r["pre_close"] else 0
        spot_results.append({
            "symbol": code, "name": name,
            "price": round(r.get("close", 0), 2), "change_pct": round(chg, 2),
            "open": round(r["open"], 2), "high": round(r["high"], 2),
            "low": round(r["low"], 2), "pre_close": round(r["pre_close"], 2),
            "volume": r["volume"], "amount": round(r["amount"], 2),
            "data_date": r["date"],
        })

    output["data_date"] = data_date or today

    # ── Step 2: 尝试获取历史数据做完整信号 ──
    test_df = fetcher.get_daily_hist("000157", "中联重科", start_date="20260725")
    use_full = test_df is not None and len(test_df) >= 2

    if use_full:
        log.info("✅ 历史接口可用，进行完整技术指标扫描...")
        output["data_source"] = "hist_scan"
        
        # 批量获取历史数据（使用 DataRouter 批量接口，单会话复用）
        symbols = [item["symbol"] for item in spot_results if "error" not in item]
        if symbols:
            log.info(f"📦 批量获取 {len(symbols)} 只历史数据...")
            daily_data = fetcher._router.get_daily_batch(symbols, start_date="20240101", adjust="qfq")
            log.info(f"  ✅ 批量获取完成: {len(daily_data)}/{len(symbols)} 只")
        else:
            daily_data = {}
        
        for item in spot_results:
            if "error" in item:
                output["watchlist_scan"].append(item)
                output["summary"]["fail_count"] += 1
                continue
            sym = item["symbol"]
            df = daily_data.get(sym)
            # ── 2026-10-13: spot vs K线 sanity 门禁 ──
            # 10-12 事故: 新浪日K对601061返回坏序列(末根15.82 vs 真实10.80, 偏离46%),
            # 坏K线直通 calc_full_signal_whitelist 进日报。规则: spot价 与 K线末根 偏离
            # >25%(超出全部板块涨跌停幅度, 必非真实行情) => 数据源损坏, 该票显式失败 +
            # 告警, 宁可缺数不可错数(与"数据降级必须显式标注"同哲学)。
            spot_price = item.get("price")
            hist_last = None
            if df is not None and len(df):
                try:
                    hist_last = float(df["close"].iloc[-1])
                except Exception as e:
                    log.warning(f"sanity: {sym} K线末根读取失败({e}), 跳过spot交叉校验")
                    hist_last = None
            if spot_price and hist_last:
                dev = abs(hist_last - spot_price) / spot_price
                if dev > 0.25:
                    msg = (f"sanity_check_failed: {sym} {item['name']} hist末根 {hist_last:.2f} "
                           f"vs spot {spot_price:.2f} 偏离 {dev*100:.1f}% > 25%, 数据源损坏, 拒绝本票")
                    log.warning(msg)
                    output.setdefault("sanity_warnings", []).append(msg)
                    output["summary"]["fail_count"] += 1
                    output["watchlist_scan"].append({**item, "error": msg})
                    continue
            if df is not None and len(df) >= 60:
                try:
                    # 计算新闻情绪因子
                    news_sentiment = get_news_sentiment(sym, item["name"], lookback_days=5)
                    # 将新闻情绪注入 DataFrame attrs，供 calc_full_signal_whitelist 读取
                    if not hasattr(df, "attrs"):
                        df.attrs = {}
                    df.attrs["news_sentiment"] = news_sentiment
                    
                    sig = calc_full_signal_whitelist(df)
                    
                    # [2026-10-30] 应用时间衰减 (Temporal Decay)
                    # 记录 🟢 强烈买入 信号的 birth date，随时间推移阶梯式衰减权重
                    final_score, final_level, decay_logs = apply_temporal_decay(
                        sym, sig["signal"]["score"], sig["signal"]["level"], today
                    )
                    sig["signal"]["score"], sig["signal"]["level"] = final_score, final_level
                    if decay_logs:
                        curr_risks = sig["signal"]["risks"]
                        risk_list = curr_risks.split("; ") if curr_risks != "无" else []
                        risk_list = risk_list + decay_logs
                        sig["signal"]["risks"] = "; ".join(risk_list[-5:])
                    
                    # 斐波那契时间线预警 (⚠️ 2026-09-15 经随机基准对照回测判定【与随机无差异/无效】，
                    # 仅供观察性展示，严禁进入决策链。详见 analysis/fib_time_random_benchmark_2026-09-15.md)
                    fib_time = compute_fib_time_targets(df)
                    # 字段层级修正：history 文档称在 signal 内新增 fib_time，
                    # 实际原代码挂在 watchlist_scan 条目顶层。此处两处都写，保证向后兼容。
                    sig["fib_time"] = fib_time
                    sig.setdefault("signal", {})["fib_time"] = fib_time
                    sig["symbol"] = sym
                    sig["name"] = item["name"]
                    output["watchlist_scan"].append(sig)
                    # 收集时间线汇聚窗口用于汇总输出
                    if fib_time and fib_time.get("next_window"):
                        nw = fib_time["next_window"]
                        output["fib_time_windows"].append({
                            "symbol": sym,
                            "name": item["name"],
                            "window_start": nw["window_start"],
                            "window_end": nw["window_end"],
                            "hits": nw["hits"],
                            "strength": nw["strength"],
                            "sources": nw["sources"][:4],  # 只取前4个源
                        })
                    lv = sig["signal"]["level"]
                    if "强烈买入" in lv: output["summary"]["strong_buy"] += 1
                    elif "关注" in lv: output["summary"]["watch"] += 1
                    elif "中性" in lv: output["summary"]["neutral"] += 1
                    elif "谨慎" in lv: output["summary"]["caution"] += 1
                    elif "强烈卖出" in lv: output["summary"]["strong_sell"] += 1
                except Exception as e:
                    output["summary"]["fail_count"] += 1
                    output["watchlist_scan"].append({**item, "error": str(e)})
            else:
                sig = calc_spot_signal(item)
                sig["symbol"] = sym
                sig["name"] = item["name"]
                sig["data_source"] = "spot"
                output["watchlist_scan"].append(sig)
                lv = sig["signal"]["level"]
                if "强烈买入" in lv: output["summary"]["strong_buy"] += 1
                elif "关注" in lv: output["summary"]["watch"] += 1
                elif "中性" in lv: output["summary"]["neutral"] += 1
                elif "谨慎" in lv: output["summary"]["caution"] += 1
                elif "强烈卖出" in lv: output["summary"]["strong_sell"] += 1
    else:
        log.info("⚠️ 历史接口不可用，仅用 spot 数据做快速扫描")
        for item in spot_results:
            if "error" in item:
                output["watchlist_scan"].append(item)
                output["summary"]["fail_count"] += 1
                continue
            sig = calc_spot_signal(item)
            sig["symbol"] = item["symbol"]
            sig["name"] = item["name"]
            sig["data_source"] = "spot"
            output["watchlist_scan"].append(sig)
            lv = sig["signal"]["level"]
            if "强烈买入" in lv: output["summary"]["strong_buy"] += 1
            elif "关注" in lv: output["summary"]["watch"] += 1
            elif "中性" in lv: output["summary"]["neutral"] += 1
            elif "谨慎" in lv: output["summary"]["caution"] += 1
            elif "强烈卖出" in lv: output["summary"]["strong_sell"] += 1

    # ── 市场概览 ──
    up = sum(1 for s in spot_results if "error" not in s and s["change_pct"] > 0)
    down = sum(1 for s in spot_results if "error" not in s and s["change_pct"] < 0)
    total_amt = round(sum(s.get("amount", 0) for s in spot_results if "error" not in s) / 1e8, 2)
    output["market_overview"] = {"up": up, "down": down, "total": len(spot_results),
                                  "total_amount_billion": total_amt}

    # ── 盘中策略预警 ──
    log.info("🔔 盘中策略预警对照...")
    try:
        strat_alerts, strat_triggered = evaluate_strategy_alerts(spot_results)
        output["strategy_alerts"] = strat_alerts
        output["strategy_alert_triggered"] = strat_triggered
    except Exception as e:
        log.warning(f"策略预警评估失败: {e}")
        output["strategy_alerts"] = []
        output["strategy_alert_triggered"] = []

    # ── 中联重科双策略 ──
    log.info("📊 分析中联重科双策略...")
    if test_df is not None and len(test_df) >= 2:
        try:
            zl_full = fetcher.get_daily_hist("000157", "中联重科", start_date="20240101")
            if zl_full is None or len(zl_full) < 120:
                zl_full = test_df
            zl_sig = analyze_zhonglian(zl_full)
            state = load_state()
            if zl_sig["date"] != state.get("last_signal_date") and zl_sig["date"] <= today:
                state["last_signal_date"] = zl_sig["date"]
                actions, msgs, state, total_val, total_ret = execute_trade(state, zl_sig)
                output["zhonglian_actions"] = actions
                output["zhonglian_messages"] = msgs
            else:
                s1 = state["strategy1"]
                s2 = state["strategy2"]
                total_val = s1["capital"] + (s1["shares"] * zl_sig["price"] if s1["position"] else 0) \
                          + s2["capital"] + (s2["shares"] * zl_sig["price"] if s2["position"] else 0)
                total_ret = round((total_val - CAPITAL * 2) / (CAPITAL * 2) * 100, 2)
            save_state(state)
            output["zhonglian"] = {**zl_sig, "portfolio": {
                "strategy1": {"position": state["strategy1"]["position"], "entry_price": state["strategy1"]["entry_price"],
                              "entry_date": state["strategy1"]["entry_date"], "capital": round(state["strategy1"]["capital"], 2),
                              "shares": state["strategy1"]["shares"]},
                "strategy2": {"position": state["strategy2"]["position"], "entry_price": state["strategy2"]["entry_price"],
                              "entry_date": state["strategy2"]["entry_date"], "capital": round(state["strategy2"]["capital"], 2),
                              "shares": state["strategy2"]["shares"]},
                "total_value": round(total_val, 2), "total_return_pct": total_ret,
            }}
        except Exception as e:
            log.error(f"中联重科分析失败: {e}")
            output["zhonglian"] = {"error": str(e)}

    # 排序时间线窗口：优先 hits（去重后信息量），其次日期近远。
    # ⚠️ 原按 (strength, window_start) 排序会把起点=基准日+1、宽度可达 15 自然日的
    # 窗口排最前 —— 展示的恰是最宽最无效的那批。现改为 hits 优先。
    output["fib_time_windows"].sort(key=lambda x: (-x["hits"], x["window_start"]))

    # ── 资金流（东财，失败不影响主流程）──
    log.info("💰 获取主力资金流...")
    try:
        output["moneyflow"] = collect_moneyflow(
            [c for c, _ in WATCHLIST],
            {c: n for c, n in WATCHLIST},
        )
    except Exception as e:
        log.warning(f"资金流获取失败: {e}")
        output["moneyflow"] = {"error": str(e)}

    # bs_logout() - DataRouter 内部管理会话，无需手动登出
    print("=====CLOSE_SCAN_RESULT=====")
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    print("=====CLOSE_SCAN_END=====")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="收盘后全量扫描")
    parser.add_argument("--intraday", action="store_true", help="盘中预警模式")
    parser.add_argument("--date", type=str, default=None, help="目标日期 YYYY-MM-DD（默认今天）")
    args = parser.parse_args()
    if args.intraday:
        run_intraday_alert_mode()
    else:
        main(args.date)
