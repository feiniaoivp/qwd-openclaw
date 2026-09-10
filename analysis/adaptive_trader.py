#!/usr/bin/env python3
"""
自适应策略分配器 v2.0 — 基于指标白名单重构
======================================================
核心原则（来自 wiki/sources/technical-analysis-core.md）：
  • 仅保留三大原子指标：支撑压力、成交量、均线
  • 核心辅助：MACD(零轴分多空)、ATR(仅风控)、斐波那契(共振时)、EMA8/21/50组合
  • 移除：RSI/KDJ/CCI、布林带、MACD柱状图斜率、其他震荡/波动类
  • 五大策略映射到白名单指标组合：
    - bollinger: 支撑压力 + 成交量 + EMA20/中轨 + ATR止损
    - kdj_cci: 均线趋势 + 成交量 + MACD零轴/背离 + ATR止损
    - ema_obv: 成交量OBV + EMA20/50 + MACD动能 + ATR止损
    - ema_cross: EMA12/26/50组合 + 成交量 + MACD零轴 + ATR止损
    - macd: MACD零轴/金叉/背离 + EMA趋势 + 成交量 + ATR止损

数据源路由（MEMORY.md 2026-08-02 权威版）:
  实时行情: 新浪 hq.sinajs.cn (稳定) → pytdx → akshare
  K线历史:  新浪日K jsonp (稳定, 0.1s/只) → pytdx → akshare东财(单次不sleep)
  财务/新闻: akshare (同花顺/新浪/巨潮)

运行: python3 analysis/adaptive_trader.py
输出: JSON(stdout) + 可读简报(stderr) + 日报保存
"""

from __future__ import annotations

import os
import sys
import json
import time
import warnings
import urllib.request
import re
from datetime import datetime
from typing import Optional

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

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
    import baostock as bs
    HAS_BAOSTOCK = True
except Exception:
    HAS_BAOSTOCK = False

try:
    from pytdx.hq import TdxHq_API
    HAS_PYTDX = True
except Exception:
    HAS_PYTDX = False

# ============================================================================
# 配置常量
# ============================================================================

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
OUTPUT_DIR = os.path.join(WORKSPACE, "analysis", "daily")
STRATEGY_MAP_FILE = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")
ADAPTIVE_PARAMS_FILE = os.path.join(WORKSPACE, "data", "adaptive_params.json")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 27只关注股 (MEMORY.md 2026-08-30)
STOCKS = [
    ("600030", "中信证券"), ("601066", "中信建投"), ("600036", "招商银行"),
    ("601995", "中金公司"), ("000987", "越秀资本"),
    ("600584", "长电科技"), ("688981", "中芯国际"), ("002156", "通富微电"),
    ("002413", "雷科防务"),
    ("300014", "亿纬锂能"), ("002466", "天齐锂业"),
    ("300285", "国瓷材料"), ("603308", "应流股份"), ("300124", "汇川技术"),
    ("601100", "恒立液压"), ("002318", "久立特材"), ("300719", "安达维尔"),
    ("002335", "科华数据"),
    ("600660", "福耀玻璃"), ("600570", "恒生电子"), ("605566", "福莱蒽特"),
    ("000157", "中联重科"), ("601061", "中信金属"),
    # 2026-07-29 新增
    ("600160", "巨化股份"),
    ("600346", "恒力石化"), ("000708", "中信特钢"), ("300748", "金力永磁"),
]

# 从 adaptive_strategy_map.json 读取策略映射
def load_strategy_map() -> dict:
    if os.path.exists(STRATEGY_MAP_FILE):
        try:
            with open(STRATEGY_MAP_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    # 兜底硬编码 (与 adaptive_strategy_map.json 保持一致)
    return {
        "600030": "macd", "601066": "macd", "600036": "kdj_cci", "601995": "kdj_cci", "000987": "macd",
        "600584": "macd", "688981": "kdj_cci", "002156": "ema_obv", "002413": "ema_cross",
        "300014": "bollinger", "002466": "ema_obv",
        "300285": "macd", "603308": "ema_cross", "300124": "macd",
        "601100": "ema_cross", "002318": "bollinger", "300719": "kdj_cci",
        "002335": "ema_obv",
        "600660": "macd", "600570": "bollinger", "605566": "macd",
        "000157": "ema_cross", "601061": "ema_obv",
        "600160": "ema_cross", "600346": "macd", "000708": "ema_cross", "300748": "kdj_cci",
    }

BEST_STRATEGY_MAP = load_strategy_map()

STRATEGY_LABELS = {
    "bollinger": "📊 布林带+ATR",
    "kdj_cci": "🎯 KDJ+RSI(均值回归)",
    "ema_obv": "📈 EMA+OBV",
    "ema_cross": "💹 EMA12/26",
    "macd": "📉 纯MACD",
    "bull_trend": "🐂 牛市趋势跟踪",
}

DEFAULT_STRATEGY = "ema_cross"

COMMISSION = 0.0003
SLIPPAGE = 0.001
INITIAL_CAPITAL = 100_000

# ============================================================================
# 日志配置
# ============================================================================

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ============================================================================
# 统一数据获取器 (复用 close_scan_v2.py 的 FinancialDataFetcher)
# ============================================================================

class FinancialDataFetcher:
    """财务/行情数据获取器 - 单例缓存 + 多数据源降级"""

    def __init__(self):
        self._cache: dict[str, Any] = {}
        self._bs_logged_in = False
        self._last_login_check = 0.0

    def _ensure_bs_login(self) -> bool:
        if self._bs_logged_in and time.time() - self._last_login_check < 1800:
            return True
        if not HAS_BAOSTOCK:
            return False
        try:
            lg = bs.login()
            if lg.error_code != "0":
                log.error(f"[BS] 登录失败: {lg.error_msg}")
                return False
            self._bs_logged_in = True
            self._last_login_check = time.time()
            return True
        except Exception as e:
            log.error(f"[BS] 登录异常: {e}")
            return False

    def bs_logout(self):
        if self._bs_logged_in:
            try:
                if HAS_BAOSTOCK:
                    bs.logout()
            except Exception:
                pass
            self._bs_logged_in = False

    def _cache_get(self, key: str) -> Optional[Any]:
        return self._cache.get(key)

    def _cache_set(self, key: str, value: Any):
        self._cache[key] = value

    # 新浪实时行情
    def fetch_sina_spot(self, symbols: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(symbols), 60):
            batch = symbols[i:i+60]
            url = "http://hq.sinajs.cn/list=" + ",".join(batch)
            req = urllib.request.Request(
                url,
                headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
            )
            try:
                raw = urllib.request.urlopen(req, timeout=12).read().decode("gbk")
            except Exception as e:
                log.warning(f"[SINA-SPOT] 批次请求失败: {e}")
                continue
            for line in raw.splitlines():
                if '="' not in line: continue
                key = line.split("hq_str_")[1].split("=")[0]
                val = line.split('"')[1].split(",")
                if len(val) < 10: continue
                symbol = key[2:]
                def f(x):
                    try: return float(x)
                    except: return 0.0
                out[symbol] = {
                    "name": val[0],
                    "open": f(val[1]), "pre_close": f(val[2]), "last": f(val[3]),
                    "high": f(val[4]), "low": f(val[5]),
                    "volume": int(float(val[8])), "amount": f(val[9]),
                    "date": val[30] if len(val) > 30 else "",
                    "time": val[31] if len(val) > 31 else "",
                }
        return out

    # 新浪历史日K (jsonp)
    def fetch_sina_daily(self, symbol: str, n: int = 300) -> Optional[pd.DataFrame]:
        cache_key = f"sina_daily_{symbol}_{n}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        pref = "sh" if symbol[0] in "69" else "sz"
        url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService."
               f"getKLineData?symbol={pref}{symbol}&scale=240&ma=no&datalen={n}")
        req = urllib.request.Request(
            url,
            headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
        )
        try:
            raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        except Exception as e:
            log.warning(f"[SINA-DAILY] {symbol} 请求失败: {e}")
            return None
        m = re.search(r"=\s*\((\[.*\])\)\s*;", raw, re.S)
        if not m:
            i, j = raw.find("["), raw.rfind("]")
            if i < 0 or j <= i: return None
            arr = json.loads(raw[i:j+1])
        else:
            arr = json.loads(m.group(1))
        df = pd.DataFrame(arr)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["date"] = pd.to_datetime(df["day"])
        df = df[["date", "open", "high", "low", "close", "volume"]].dropna()
        if len(df) >= 2:
            self._cache_set(cache_key, df)
        return df

    # pytdx 历史日K (备用)
    def fetch_pytdx_daily(self, symbol: str,
                          ip: str = "123.125.108.14", port: int = 7709) -> Optional[pd.DataFrame]:
        if not HAS_PYTDX:
            return None
        api = TdxHq_API()
        try:
            ok = api.connect(ip, port, time_out=5)
            if not ok:
                return None
            market = 0 if symbol[0] in "69" else 1
            bars = api.get_security_bars(9, market, symbol, 0, 300)
            if not bars:
                return None
            df = pd.DataFrame(bars)
            df["date"] = pd.to_datetime(df["datetime"], unit="s")
            df = df[["date", "open", "high", "low", "close", "vol"]]
            df.columns = ["date", "open", "high", "low", "close", "volume"]
            df = df.sort_values("date").reset_index(drop=True).dropna()
            return df
        except Exception:
            return None
        finally:
            try:
                api.disconnect()
            except Exception:
                pass

    # akshare 历史日K (最后兜底)
    def fetch_akshare_daily(self, symbol: str, start_date: str = "20250101") -> Optional[pd.DataFrame]:
        if not HAS_AKSHARE:
            return None
        try:
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                    start_date=start_date, adjust="qfq")
            if df is None or df.empty:
                return None
            df = df[["日期", "开盘", "收盘", "最高", "最低", "成交量"]]
            df.columns = ["date", "open", "close", "high", "low", "volume"]
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df
        except Exception:
            return None

    # 统一历史日K 获取 (降级链: 新浪日K -> pytdx -> akshare)
    def get_daily_hist(self, symbol: str, name: str,
                       start_date: str = "20250101") -> Optional[pd.DataFrame]:
        # Level 1: 新浪日K
        try:
            df = self.fetch_sina_daily(symbol)
            if df is not None and len(df) >= 2:
                if start_date:
                    cut = pd.to_datetime(start_date)
                    df = df[df["date"] >= cut]
                if len(df) >= 2:
                    log.info(f"  ✅ {name}({symbol}): {len(df)} 条日线 [新浪日K]")
                    return df.reset_index(drop=True)
        except Exception as e:
            log.warning(f"  ⚠️ {name} 新浪日K失败: {e}")
        # Level 2: pytdx
        try:
            df = self.fetch_pytdx_daily(symbol)
            if df is not None and len(df) >= 2:
                if start_date:
                    cut = pd.to_datetime(start_date)
                    df = df[df["date"] >= cut]
                if len(df) >= 2:
                    log.info(f"  ✅ {name}({symbol}): {len(df)} 条日线 [pytdx]")
                    return df.reset_index(drop=True)
        except Exception as e:
            log.warning(f"  ⚠️ {name} pytdx历史失败: {e}")
        # Level 3: akshare
        try:
            df = self.fetch_akshare_daily(symbol, start_date)
            if df is not None and len(df) >= 2:
                log.info(f"  ✅ {name}({symbol}): {len(df)} 条日线 [akshare]")
                return df.reset_index(drop=True)
        except Exception as e:
            log.warning(f"  ⚠️ {name} akshare历史失败: {e}")
        return None


_fetcher_instance: Optional[FinancialDataFetcher] = None

def get_fetcher() -> FinancialDataFetcher:
    global _fetcher_instance
    if _fetcher_instance is None:
        _fetcher_instance = FinancialDataFetcher()
    return _fetcher_instance


# ============================================================================
# 白名单指标计算函数 (复用 close_scan_v2.py 的核心逻辑)
# ============================================================================

def _pick_col(df: pd.DataFrame, *candidates: str) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    for c in candidates:
        for col in df.columns:
            if c.lower() in col.lower():
                return col
    return None

def calc_support_resistance(df: pd.DataFrame, lookback: int = 60) -> dict:
    if len(df) < lookback:
        lookback = len(df)
    recent = df.tail(lookback)
    high = recent["high"].max()
    low = recent["low"].min()
    price_range = high - low
    if price_range > 0:
        bins = 20
        bin_edges = [low + i * price_range / bins for i in range(bins + 1)]
        vol_profile = pd.cut(recent["close"], bins=bin_edges, labels=False)
        vol_by_bin = recent.groupby(vol_profile)["volume"].sum()
        if not vol_by_bin.empty:
            poc_bin = vol_by_bin.idxmax()
            poc_price = (bin_edges[poc_bin] + bin_edges[poc_bin + 1]) / 2
        else:
            poc_price = (high + low) / 2
    else:
        poc_price = high
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
    vol = df["volume"]
    close = df["close"]
    vol_ma20 = vol.rolling(20).mean()
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
        "divergence_bear": (not price_up) and vol_up,
        "divergence_bull": price_up and vol_shrink,
    }

def calc_ema_trend(df: pd.DataFrame) -> dict:
    close = df["close"]
    if HAS_PANDAS_TA:
        ema20 = ta.ema(close, length=20)
        ema50 = ta.ema(close, length=50)
        ema200 = ta.ema(close, length=200)
    else:
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()
    cur_price = float(close.iloc[-1])
    e20 = float(ema20.iloc[-1]) if pd.notna(ema20.iloc[-1]) else cur_price
    e50 = float(ema50.iloc[-1]) if pd.notna(ema50.iloc[-1]) else cur_price
    e200 = float(ema200.iloc[-1]) if pd.notna(ema200.iloc[-1]) else cur_price
    bull_arrange = e20 > e50 > e200
    bear_arrange = e20 < e50 < e200
    above_20 = cur_price > e20
    above_50 = cur_price > e50
    above_200 = cur_price > e200
    e20_slope = float(ema20.iloc[-1] - ema20.iloc[-6]) if len(ema20) >= 6 else 0
    e50_slope = float(ema50.iloc[-1] - ema50.iloc[-6]) if len(ema50) >= 6 else 0
    return {
        "EMA20": round(e20, 2), "EMA50": round(e50, 2), "EMA200": round(e200, 2),
        "price": round(cur_price, 2),
        "above_EMA20": above_20, "above_EMA50": above_50, "above_EMA200": above_200,
        "bull_arrange": bull_arrange, "bear_arrange": bear_arrange,
        "EMA20_slope": round(e20_slope, 4), "EMA50_slope": round(e50_slope, 4),
        "trend": "BULL" if bull_arrange else ("BEAR" if bear_arrange else "NEUTRAL"),
    }

def calc_macd_signal(df: pd.DataFrame) -> dict:
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
    above_zero = macd_now > 0 and sig_now > 0
    below_zero = macd_now < 0 and sig_now < 0
    cross_up = (macd_prev <= sig_prev) and (macd_now > sig_now)
    cross_down = (macd_prev >= sig_prev) and (macd_now < sig_now)
    hist_expand = hist_now > hist_prev
    hist_shrink = hist_now < hist_prev
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
        "MACD": round(macd_now, 4), "SIGNAL": round(sig_now, 4), "HIST": round(hist_now, 4),
        "above_zero": above_zero, "below_zero": below_zero,
        "cross_up": cross_up, "cross_down": cross_down,
        "hist_expand": hist_expand, "hist_shrink": hist_shrink,
        "bear_divergence": bear_div, "bull_divergence": bull_div,
        "zone": "BULL" if above_zero else ("BEAR" if below_zero else "NEUTRAL"),
    }

def calc_atr_stops(df: pd.DataFrame, mult: float = 2.0) -> dict:
    if not HAS_PANDAS_TA:
        return {"error": "pandas_ta 未安装"}
    atr_series = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else 0
    cur_price = float(df["close"].iloc[-1])
    stop_long = round(cur_price - mult * atr, 2)
    stop_short = round(cur_price + mult * atr, 2)
    risk_per_trade = 0.01
    shares_per_100k = int(100000 * risk_per_trade / (mult * atr) / 100) * 100 if atr > 0 else 0
    return {
        "ATR14": round(atr, 2),
        "stop_long": stop_long,
        "stop_short": stop_short,
        "risk_per_share": round(mult * atr, 2),
        "suggested_shares_per_100k": shares_per_100k,
    }

def calc_fibonacci_confluence(df: pd.DataFrame, sr: dict) -> dict:
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
    confluence = []
    for name, level in levels.items():
        if abs(level - sr.get("resistance_strong", 0)) / cur_price < 0.02:
            confluence.append(f"{name}@{level}≈强压力")
        if abs(level - sr.get("support_strong", 0)) / cur_price < 0.02:
            confluence.append(f"{name}@{level}≈强支撑")
    return {"levels": levels, "confluence": confluence}

def calc_ema_combo_8_21_50(df: pd.DataFrame) -> dict:
    close = df["close"]
    if HAS_PANDAS_TA:
        ema8 = ta.ema(close, length=8)
        ema21 = ta.ema(close, length=21)
        ema50 = ta.ema(close, length=50)
    else:
        ema8 = close.ewm(span=8, adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
    e8 = float(ema8.iloc[-1]) if pd.notna(ema8.iloc[-1]) else 0
    e21 = float(ema21.iloc[-1]) if pd.notna(ema21.iloc[-1]) else 0
    e50 = float(ema50.iloc[-1]) if pd.notna(ema50.iloc[-1]) else 0
    cur_price = float(close.iloc[-1])
    bull = cur_price > e50 and e8 > e21 > e50
    bear = cur_price < e50 and e8 < e21 < e50
    pullback_buy_zone = bull and (cur_price <= e8 * 1.02) and (cur_price >= e21 * 0.98)
    return {
        "EMA8": round(e8, 2), "EMA21": round(e21, 2), "EMA50": round(e50, 2),
        "bull_setup": bull, "bear_setup": bear, "pullback_buy_zone": pullback_buy_zone,
    }


# ============================================================================
# 五大策略信号生成器 —— 仅使用白名单指标
# ============================================================================

def signal_bull_trend(df: pd.DataFrame, symbol: Optional[str] = None) -> dict:
    """
    策略：牛市趋势跟踪 → 白名单映射：EMA20/60多头排列 + EMA10支撑 + OBV健康 + ATR止损
    买入：EMA20 > EMA60 且 价格回调至EMA20附近(±1%) + OBV > OBV_EMA20
    卖出：跌破EMA60 或 EMA20死叉EMA60 或 跌破EMA10
    """
    ema_trend = calc_ema_trend(df)
    vol = calc_volume_analysis(df)
    atr = calc_atr_stops(df)
    fib = calc_fibonacci_confluence(df, calc_support_resistance(df))
    
    # 计算 EMA10 和 OBV
    if 'EMA10' not in df.columns:
        import pandas_ta as ta
        df = df.copy()
        df['EMA10'] = ta.ema(df['close'], length=10)
        df['OBV'] = ta.obv(df['close'], df['volume'])
        df['OBV_EMA20'] = ta.ema(df['OBV'], length=20)
    
    cur_price = float(df['close'].iloc[-1])
    prev_close = float(df['close'].iloc[-2])
    change_pct = round((cur_price / prev_close - 1) * 100, 2)
    
    ema10 = float(df['EMA10'].iloc[-1])
    ema20 = ema_trend['EMA20']
    ema60 = ema_trend['EMA60']
    obv = float(df['OBV'].iloc[-1])
    obv_ema = float(df['OBV_EMA20'].iloc[-1])
    
    # 趋势判断
    bull_trend = ema20 > ema60 and cur_price > ema20
    trend_break = ema20 < ema60 or cur_price < ema60
    near_ema20 = abs(cur_price - ema20) / ema20 < 0.01  # ±1%
    obv_ok = obv > obv_ema  # 资金未流出
    
    action, reason = "持有", "无信号"
    
    if bull_trend and near_ema20 and obv_ok:
        action = "🟢买入"
        reason = f"牛市回调买入: EMA20({ema20:.2f})>EMA60({ema60:.2f}) 回调EMA20附近 OBV健康"
    elif trend_break:
        action = "🔴卖出"
        reason = f"趋势破坏: EMA20({ema20:.2f}){'<' if ema20<ema60 else '>'}EMA60({ema60:.2f}) 价格{'<' if cur_price<ema60 else '>'}EMA60"
    elif cur_price < ema10:
        action = "🔴卖出"
        reason = f"跌破EMA10({ema10:.2f}) 短期支撑失效"
    
    atr_stop = atr.get('stop_long')
    
    fib_tp = {}
    if action == "🟢买入" and fib.get('confluence'):
        sr = calc_support_resistance(df)
        wave_high = sr['resistance_strong']
        wave_low = sr['support_strong']
        wave_range = wave_high - wave_low
        if wave_range > 0:
            targets = {}
            for level in [1.0, 1.272, 1.618, 2.618]:
                targets[f'fib_{level}'] = round(wave_high + wave_range * (level - 1.0), 2)
            fib_tp = {'entry': round(cur_price, 2), 'targets': targets, 'alloc': [0.3, 0.4, 0.2, 0.1]}
    
    return {
        'action': action, 'reason': reason, 'price': round(cur_price, 2),
        'change_pct': change_pct,
        'key_indicators': f'EMA10{ema10:.2f} EMA20{ema20:.2f} EMA60{ema60:.2f} OBV{obv:.0f}',
        'atr': atr.get('ATR14'), 'atr_stop': atr_stop, 'fib_tp': fib_tp,
        'indicators': {'ema_trend': ema_trend, 'vol': vol, 'atr': atr, 'fib': fib},
    }


def signal_bollinger_atr(df: pd.DataFrame, symbol: Optional[str] = None) -> dict:
    """
    策略：布林带+ATR → 白名单映射：支撑压力(上轨/中轨) + 成交量 + EMA20(中轨) + ATR止损
    买入：价格突破上轨(强压力位) + 放量 + EMA20上行
    卖出：跌破中轨(EMA20) + 缩量 或 ATR止损触发
    """
    sr = calc_support_resistance(df)
    vol = calc_volume_analysis(df)
    ema_trend = calc_ema_trend(df)
    atr = calc_atr_stops(df)
    fib = calc_fibonacci_confluence(df, sr)
    
    cur_price = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])
    change_pct = round((cur_price / prev_close - 1) * 100, 2)
    
    # 布林带上轨 ≈ 强压力位，中轨 ≈ EMA20
    resistance = sr["resistance_strong"]
    middle = ema_trend["EMA20"]
    
    action, reason = "持有", "无信号"
    
    # 突破强压力位 + 放量 + 趋势向上
    breakout_up = cur_price > resistance and vol["volume_expand"] and ema_trend["EMA20_slope"] > 0
    # 跌破中轨/EMA20
    breakdown = cur_price < middle
    
    if breakout_up:
        action = "🟢买入"
        reason = f"突破强压力位¥{resistance:.2f} + 放量(ratio={vol['vol_ratio']:.1f}) + EMA20上行"
    elif breakdown:
        action = "🔴卖出"
        reason = f"跌破EMA20/中轨¥{middle:.2f}"
    
    # ATR止损
    atr_stop = atr.get("stop_long")
    
    # 斐波那契扩展止盈
    fib_tp = {}
    if action == "🟢买入" and fib.get("confluence"):
        wave_high = sr["resistance_strong"]
        wave_low = sr["support_strong"]
        wave_range = wave_high - wave_low
        if wave_range > 0:
            targets = {}
            for level in [1.0, 1.272, 1.618, 2.618]:
                targets[f"fib_{level}"] = round(wave_high + wave_range * (level - 1.0), 2)
            fib_tp = {"entry": round(cur_price, 2), "targets": targets, "alloc": [0.3, 0.4, 0.2, 0.1]}
    
    return {
        "action": action, "reason": reason, "price": round(cur_price, 2),
        "change_pct": change_pct,
        "key_indicators": f"强压力{resistance:.2f} EMA20{middle:.2f} ATR{atr.get('ATR14',0):.2f}",
        "atr": atr.get("ATR14"), "atr_stop": atr_stop, "fib_tp": fib_tp,
        "indicators": {"sr": sr, "vol": vol, "ema": ema_trend, "atr": atr, "fib": fib},
    }

def signal_kdj_cci(df: pd.DataFrame, symbol: Optional[str] = None) -> dict:
    """
    策略：KDJ+CCI(均值回归) → 白名单映射：均线趋势 + 成交量 + MACD零轴/背离 + ATR止损
    买入：大趋势向上(EMA200上) + MACD底背离/零轴上金叉 + 缩量回调
    卖出：MACD顶背离/死叉 + 放量滞涨 或 ATR止损触发
    """
    ema_trend = calc_ema_trend(df)
    vol = calc_volume_analysis(df)
    macd = calc_macd_signal(df)
    atr = calc_atr_stops(df)
    fib = calc_fibonacci_confluence(df, calc_support_resistance(df))
    
    cur_price = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])
    change_pct = round((cur_price / prev_close - 1) * 100, 2)
    
    action, reason = "持有", "无信号"
    
    # 大趋势过滤：必须在EMA200上方
    trend_ok = ema_trend["above_EMA200"]
    
    # 超卖反弹条件：MACD零轴上金叉 或 底背离
    macd_bull = macd.get("cross_up") or macd.get("bull_divergence")
    # 超买卖出条件：MACD死叉 或 顶背离
    macd_bear = macd.get("cross_down") or macd.get("bear_divergence")
    
    if trend_ok and macd_bull and vol["volume_shrink"] and not vol["price_up"]:
        action = "🟢买入"
        reason = f"大趋势向上 + MACD{'金叉' if macd.get('cross_up') else '底背离'} + 缩量回调"
    elif macd_bear and (vol["volume_expand"] or not vol["price_up"]):
        action = "🔴卖出"
        reason = f"MACD{'死叉' if macd.get('cross_down') else '顶背离'} + {'放量' if vol['volume_expand'] else '量价背离'}"
    
    atr_stop = atr.get("stop_long")
    
    fib_tp = {}
    if action == "🟢买入" and fib.get("confluence"):
        sr = calc_support_resistance(df)
        wave_high = sr["resistance_strong"]
        wave_low = sr["support_strong"]
        wave_range = wave_high - wave_low
        if wave_range > 0:
            targets = {}
            for level in [1.0, 1.272, 1.618, 2.618]:
                targets[f"fib_{level}"] = round(wave_high + wave_range * (level - 1.0), 2)
            fib_tp = {"entry": round(cur_price, 2), "targets": targets, "alloc": [0.3, 0.4, 0.2, 0.1]}
    
    return {
        "action": action, "reason": reason, "price": round(cur_price, 2),
        "change_pct": change_pct,
        "key_indicators": f"EMA200{ema_trend['EMA200']:.2f} MACD区{macd.get('zone','-')} ATR{atr.get('ATR14',0):.2f}",
        "atr": atr.get("ATR14"), "atr_stop": atr_stop, "fib_tp": fib_tp,
        "indicators": {"ema": ema_trend, "vol": vol, "macd": macd, "atr": atr, "fib": fib},
    }

def signal_ema_obv(df: pd.DataFrame, symbol: Optional[str] = None) -> dict:
    """
    策略：EMA+OBV(量价共振) → 白名单映射：成交量OBV + EMA20/50 + MACD动能 + ATR止损
    买入：价格>EMA50 + OBV上穿均线 + MACD零轴上
    卖出：跌破EMA50 或 OBV下穿 + MACD死叉
    """
    ema_trend = calc_ema_trend(df)
    vol = calc_volume_analysis(df)
    macd = calc_macd_signal(df)
    atr = calc_atr_stops(df)
    fib = calc_fibonacci_confluence(df, calc_support_resistance(df))
    
    # OBV 计算
    if HAS_PANDAS_TA:
        obv = ta.obv(df["close"], df["volume"])
        obv_ema20 = ta.ema(obv, length=20)
    else:
        obv = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
        obv_ema20 = obv.ewm(span=20, adjust=False).mean()
    
    obv_now = float(obv.iloc[-1])
    obv_ema = float(obv_ema20.iloc[-1]) if pd.notna(obv_ema20.iloc[-1]) else obv_now
    obv_cross_up = float(obv.iloc[-2]) <= float(obv_ema20.iloc[-2]) and obv_now > obv_ema
    obv_cross_down = float(obv.iloc[-2]) >= float(obv_ema20.iloc[-2]) and obv_now < obv_ema
    
    cur_price = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])
    change_pct = round((cur_price / prev_close - 1) * 100, 2)
    
    action, reason = "持有", "无信号"
    
    # 共振买入：站上EMA50 + OBV上穿 + MACD多头区
    if cur_price > ema_trend["EMA50"] and obv_cross_up and macd.get("above_zero"):
        action = "🟢买入"
        reason = f"站上EMA50¥{ema_trend['EMA50']:.2f} + OBV上穿均线 + MACD零轴上"
    # 共振卖出：跌破EMA50 或 OBV下穿 + MACD死叉
    elif cur_price < ema_trend["EMA50"] or (obv_cross_down and macd.get("cross_down")):
        action = "🔴卖出"
        reasons = []
        if cur_price < ema_trend["EMA50"]:
            reasons.append(f"跌破EMA50¥{ema_trend['EMA50']:.2f}")
        if obv_cross_down:
            reasons.append("OBV下穿均线(资金撤离)")
        if macd.get("cross_down"):
            reasons.append("MACD死叉")
        reason = " | ".join(reasons)
    
    atr_stop = atr.get("stop_long")
    
    fib_tp = {}
    if action == "🟢买入" and fib.get("confluence"):
        sr = calc_support_resistance(df)
        wave_high = sr["resistance_strong"]
        wave_low = sr["support_strong"]
        wave_range = wave_high - wave_low
        if wave_range > 0:
            targets = {}
            for level in [1.0, 1.272, 1.618, 2.618]:
                targets[f"fib_{level}"] = round(wave_high + wave_range * (level - 1.0), 2)
            fib_tp = {"entry": round(cur_price, 2), "targets": targets, "alloc": [0.3, 0.4, 0.2, 0.1]}
    
    return {
        "action": action, "reason": reason, "price": round(cur_price, 2),
        "change_pct": change_pct,
        "key_indicators": f"EMA50{ema_trend['EMA50']:.2f} OBV/EMA{obv_now/obv_ema:.2f}x MACD{macd.get('zone','-')}",
        "atr": atr.get("ATR14"), "atr_stop": atr_stop, "fib_tp": fib_tp,
        "indicators": {"ema": ema_trend, "vol": vol, "macd": macd, "atr": atr, "fib": fib, "obv": obv_now, "obv_ema": obv_ema},
    }

def signal_ema_cross(df: pd.DataFrame, symbol: Optional[str] = None) -> dict:
    """
    策略：EMA12/26金叉(基准C) → 白名单映射：EMA8/21/50组合 + 成交量 + MACD零轴 + ATR止损
    买入：EMA8>EMA21>EMA50 + 回踩EMA8/21买入区 + 放量 + MACD零轴上
    卖出：跌破EMA21 或 EMA8死叉EMA21 + MACD死叉
    """
    ema_combo = calc_ema_combo_8_21_50(df)
    vol = calc_volume_analysis(df)
    macd = calc_macd_signal(df)
    atr = calc_atr_stops(df)
    fib = calc_fibonacci_confluence(df, calc_support_resistance(df))
    
    cur_price = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])
    change_pct = round((cur_price / prev_close - 1) * 100, 2)
    
    action, reason = "持有", "无信号"
    
    # EMA8/21/50 多头排列 + 回踩买入区
    if ema_combo["pullback_buy_zone"] and vol["volume_expand"] and macd.get("above_zero"):
        action = "🟢买入"
        reason = f"EMA8/21/50多头回踩买入区 + 放量 + MACD零轴上"
    # 死叉或跌破EMA21
    elif cur_price < ema_combo["EMA21"] or (ema_combo["EMA8"] < ema_combo["EMA21"] and macd.get("cross_down")):
        action = "🔴卖出"
        reasons = []
        if cur_price < ema_combo["EMA21"]:
            reasons.append(f"跌破EMA21¥{ema_combo['EMA21']:.2f}")
        if ema_combo["EMA8"] < ema_combo["EMA21"]:
            reasons.append("EMA8死叉EMA21")
        if macd.get("cross_down"):
            reasons.append("MACD死叉")
        reason = " | ".join(reasons)
    
    atr_stop = atr.get("stop_long")
    
    fib_tp = {}
    if action == "🟢买入" and fib.get("confluence"):
        sr = calc_support_resistance(df)
        wave_high = sr["resistance_strong"]
        wave_low = sr["support_strong"]
        wave_range = wave_high - wave_low
        if wave_range > 0:
            targets = {}
            for level in [1.0, 1.272, 1.618, 2.618]:
                targets[f"fib_{level}"] = round(wave_high + wave_range * (level - 1.0), 2)
            fib_tp = {"entry": round(cur_price, 2), "targets": targets, "alloc": [0.3, 0.4, 0.2, 0.1]}
    
    return {
        "action": action, "reason": reason, "price": round(cur_price, 2),
        "change_pct": change_pct,
        "key_indicators": f"EMA8{ema_combo['EMA8']:.2f} EMA21{ema_combo['EMA21']:.2f} EMA50{ema_combo['EMA50']:.2f}",
        "atr": atr.get("ATR14"), "atr_stop": atr_stop, "fib_tp": fib_tp,
        "indicators": {"ema_combo": ema_combo, "vol": vol, "macd": macd, "atr": atr, "fib": fib},
    }

def signal_macd(df: pd.DataFrame, symbol: Optional[str] = None) -> dict:
    """
    策略：纯MACD(基准B) → 白名单映射：MACD零轴/金叉/背离 + EMA趋势 + 成交量 + ATR止损
    买入：MACD零轴上金叉 + 底背离 + EMA趋势向上 + 放量
    卖出：MACD零轴下死叉 + 顶背离 或 跌破EMA50
    """
    macd = calc_macd_signal(df)
    ema_trend = calc_ema_trend(df)
    vol = calc_volume_analysis(df)
    atr = calc_atr_stops(df)
    fib = calc_fibonacci_confluence(df, calc_support_resistance(df))
    
    cur_price = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])
    change_pct = round((cur_price / prev_close - 1) * 100, 2)
    
    action, reason = "持有", "无信号"
    
    # 零轴上金叉 + 趋势向上 + 放量
    macd_bull = macd.get("cross_up") and macd.get("above_zero") and ema_trend["bull_arrange"] and vol["volume_expand"]
    # 零轴下死叉 或 顶背离
    macd_bear = macd.get("cross_down") or (macd.get("bear_divergence") and not macd.get("above_zero"))
    
    if macd_bull:
        action = "🟢买入"
        reason = f"MACD零轴上金叉 + 多头排列 + 放量"
    elif macd_bear:
        action = "🔴卖出"
        reasons = []
        if macd.get("cross_down"):
            reasons.append("MACD死叉")
        if macd.get("bear_divergence"):
            reasons.append("MACD顶背离")
        if not ema_trend["above_EMA50"]:
            reasons.append("跌破EMA50")
        reason = " | ".join(reasons)
    
    atr_stop = atr.get("stop_long")
    
    fib_tp = {}
    if action == "🟢买入" and fib.get("confluence"):
        sr = calc_support_resistance(df)
        wave_high = sr["resistance_strong"]
        wave_low = sr["support_strong"]
        wave_range = wave_high - wave_low
        if wave_range > 0:
            targets = {}
            for level in [1.0, 1.272, 1.618, 2.618]:
                targets[f"fib_{level}"] = round(wave_high + wave_range * (level - 1.0), 2)
            fib_tp = {"entry": round(cur_price, 2), "targets": targets, "alloc": [0.3, 0.4, 0.2, 0.1]}
    
    return {
        "action": action, "reason": reason, "price": round(cur_price, 2),
        "change_pct": change_pct,
        "key_indicators": f"MACD{macd.get('MACD',0):.4f} SIG{macd.get('SIGNAL',0):.4f} EMA50{ema_trend['EMA50']:.2f}",
        "atr": atr.get("ATR14"), "atr_stop": atr_stop, "fib_tp": fib_tp,
        "indicators": {"macd": macd, "ema": ema_trend, "vol": vol, "atr": atr, "fib": fib},
    }


SIGNAL_FUNCS = {
    "bollinger": signal_bollinger_atr,
    "kdj_cci": signal_kdj_cci,
    "ema_obv": signal_ema_obv,
    "ema_cross": signal_ema_cross,
    "macd": signal_macd,
    "bull_trend": signal_bull_trend,
}


# ============================================================================
# 主扫描逻辑
# ============================================================================

def scan_all() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    results = []
    buy_count = 0
    sell_count = 0
    error_count = 0
    strategy_actions = {k: {"total": 0, "buy": 0, "sell": 0} for k in STRATEGY_LABELS}
    
    fetcher = get_fetcher()
    
    for symbol, name in STOCKS:
        sname = BEST_STRATEGY_MAP.get(symbol, DEFAULT_STRATEGY)
        slabel = STRATEGY_LABELS[sname]
        
        df = fetcher.get_daily_hist(symbol, name, start_date="20250101")
        if df is None or len(df) < 60:
            results.append({
                "symbol": symbol, "name": name,
                "error": "数据获取失败或不足60根",
                "strategy": slabel,
                "signal": None,
            })
            error_count += 1
            continue
        
        sig_func = SIGNAL_FUNCS[sname]
        try:
            signal = sig_func(df, symbol)
        except Exception as e:
            log.error(f"{name}({symbol}) 信号计算异常: {e}")
            results.append({
                "symbol": symbol, "name": name,
                "error": str(e),
                "strategy": slabel,
                "signal": None,
            })
            error_count += 1
            continue
        
        result = {
            "symbol": symbol, "name": name,
            "strategy": slabel,
            "price": signal["price"],
            "change_pct": signal["change_pct"],
            "action": signal["action"],
            "reason": signal["reason"],
            "key_indicators": signal.get("key_indicators", ""),
            "atr": signal.get("atr"),
            "atr_stop": signal.get("atr_stop"),
            "fib_tp": signal.get("fib_tp", {}),
        }
        results.append(result)
        
        strategy_actions[sname]["total"] += 1
        if "买入" in signal["action"]:
            strategy_actions[sname]["buy"] += 1
            buy_count += 1
        elif "卖出" in signal["action"]:
            strategy_actions[sname]["sell"] += 1
            sell_count += 1
    
    fetcher.bs_logout()
    
    return {
        "date": today,
        "stock_count": len(STOCKS),
        "summary": {
            "buy": buy_count,
            "sell": sell_count,
            "hold": len(STOCKS) - buy_count - sell_count - error_count,
            "error": error_count,
        },
        "strategy_breakdown": strategy_actions,
        "details": results,
    }


def format_brief(output: dict) -> str:
    lines = []
    s = output["summary"]
    lines.append(f"📊 **自适应策略扫描** ({output['date']})")
    lines.append(f"覆盖 {output['stock_count']} 只股票 | "
                 f"🟢买入 {s['buy']} | 🔴卖出 {s['sell']} | ⚪持有 {s['hold']} | ❌失败 {s['error']}")
    lines.append("")
    
    # 买入信号
    buys = [r for r in output["details"] if r.get("action") and "买入" in r["action"]]
    if buys:
        lines.append("🟢 **买入信号**")
        lines.append("| 股票 | 策略 | 现价 | 涨跌 | 理由 | ATR止损 | 斐波止盈(1.0/1.272/1.618/2.618) |")
        lines.append("|:---|:---|:---:|:---:|:---|:---:|:---|")
        for r in buys:
            atr_stop = r.get('atr_stop')
            fib_tp = r.get('fib_tp', {})
            atr_str = f"¥{atr_stop:.2f}" if atr_stop else "−"
            if fib_tp and "targets" in fib_tp:
                targets = fib_tp["targets"]
                fib_str = " | ".join([f"{k}:¥{v:.2f}" for k, v in targets.items()])
            else:
                fib_str = "−"
            lines.append(f"| {r['name']}({r['symbol']}) | {r['strategy']} | ¥{r['price']:.2f} | {r['change_pct']:+.2f}% | {r['reason']} | {atr_str} | {fib_str} |")
        lines.append("")
    
    # 卖出信号
    sells = [r for r in output["details"] if r.get("action") and "卖出" in r["action"]]
    if sells:
        lines.append("🔴 **卖出信号**")
        lines.append("| 股票 | 策略 | 现价 | 涨跌 | 理由 | ATR止损 |")
        lines.append("|:---|:---|:---:|:---:|:---|:---:|")
        for r in sells:
            atr_stop = r.get('atr_stop')
            atr_str = f"¥{atr_stop:.2f}" if atr_stop else "−"
            lines.append(f"| {r['name']}({r['symbol']}) | {r['strategy']} | ¥{r['price']:.2f} | {r['change_pct']:+.2f}% | {r['reason']} | {atr_str} |")
        lines.append("")
    
    # 策略分布
    lines.append("📈 **策略分配概览**")
    for sk, sv in output["strategy_breakdown"].items():
        if sv["total"] > 0:
            pct = sv["buy"] / sv["total"] * 100 if sv["total"] > 0 else 0
            lines.append(f"  {STRATEGY_LABELS[sk]}: {sv['total']}只 (买入{sv['buy']}/卖出{sv['sell']} 比例{pct:.0f}%)")
    lines.append("")
    
    return "\n".join(lines)


def main():
    # ① 保存/加载策略映射
    os.makedirs(os.path.dirname(STRATEGY_MAP_FILE), exist_ok=True)
    with open(STRATEGY_MAP_FILE, "w") as f:
        json.dump(BEST_STRATEGY_MAP, f, ensure_ascii=False, indent=2)
    print(f"📁 策略映射已保存: {STRATEGY_MAP_FILE}")
    
    # ② 执行全扫描
    print("📡 开始自适应策略扫描...")
    output = scan_all()
    
    # ③ 输出JSON（供agent解析）
    print("\n=====ADAPTIVE_SCAN_RESULT=====")
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    print("=====ADAPTIVE_SCAN_END=====")
    
    # ④ 输出可读简报
    print("\n" + format_brief(output))
    
    # ⑤ 保存日报
    today = datetime.now().strftime("%Y-%m-%d")
    report_path = os.path.join(OUTPUT_DIR, f"{today}_adaptive.md")
    with open(report_path, "w") as f:
        f.write(f"# 自适应策略日报 {today}\n\n")
        f.write(format_brief(output))
    print(f"📄 日报已保存: {report_path}")


if __name__ == "__main__":
    main()