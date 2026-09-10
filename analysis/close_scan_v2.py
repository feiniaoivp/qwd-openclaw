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
from datetime import datetime
from typing import Any, Callable, Optional
from functools import lru_cache

import pandas as pd

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

# Baostock 单例会话
import sys
import os
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)
from analysis.bs_session import ensure_login, logout as bs_logout, query_history_k_data_plus_retry

# ============================================================================
# 配置常量
# ============================================================================

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
STATE_FILE = os.path.join(WORKSPACE, "data", "zhonglian_state.json")
CACHE_DIR = os.path.join(WORKSPACE, "data_cache")

CAPITAL = 100_000
COMMISSION = 0.0003
SLIPPAGE = 0.001

# 关注股权威清单 (MEMORY.md 2026-08-30: 27只 + 2026-09-04 新增电网装备/特高压 8只 = 35只)
WATCHLIST = [
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
# 错误处理与重试 / 数据源降级策略
# ============================================================================

def bs_query_with_retry(fn: Callable, max_retries: int = 3, wait_seconds: float = 2.0):
    for attempt in range(max_retries):
        try:
            rs = fn()
            if rs.error_code == "0": return rs
            log.warning(f"  [BS] 查询错误: {rs.error_msg}, 重试 {attempt+1}/{max_retries}")
        except (socket.timeout, TimeoutError, OSError) as e:
            log.warning(f"  [BS] 网络异常: {e}, 重试 {attempt+1}/{max_retries}")
        time.sleep(wait_seconds)
    return None

def ak_safe_call(fn: Callable, *args, default=None, **kwargs):
    try:
        result = fn(*args, **kwargs)
        if result is None or (hasattr(result, "empty") and result.empty):
            return default
        return result
    except Exception as e:
        log.warning(f"  [AK] 接口异常: {e}")
        return default

def fetch_with_fallback(primary_fn: Callable, fallback_fn1: Optional[Callable] = None,
                        fallback_fn2: Optional[Callable] = None) -> Any:
    result = primary_fn()
    if result is not None and result != NO_DATA: return result
    if fallback_fn1:
        result = fallback_fn1()
        if result is not None and result != NO_DATA: return result
    if fallback_fn2:
        result = fallback_fn2()
        if result is not None and result != NO_DATA: return result
    return NO_DATA

# ============================================================================
# FinancialDataFetcher 类 - 单例缓存 + Baostock 登录管理
# ============================================================================

class FinancialDataFetcher:
    """财务/行情数据获取器 - 单例缓存 + 双源模式 + 登录复用"""

    def __init__(self):
        self._cache: dict[str, Any] = {}
        self._bs_logged_in = False
        self._last_login_check = 0.0

    # ---- Baostock 登录管理 (委托给全局单例) ----
    def _ensure_bs_login(self) -> bool:
        return ensure_login()

    def bs_logout(self):
        bs_logout()

    # ---- 通用缓存查询 ----
    def _cache_get(self, key: str) -> Optional[Any]:
        return self._cache.get(key)

    def _cache_set(self, key: str, value: Any):
        self._cache[key] = value

    # ---- 新浪实时行情 (hq.sinajs.cn) ----
    def fetch_sina_spot(self, symbols: list[str]) -> dict[str, dict]:
        """批量拉取新浪实时行情，返回 {纯代码: {...}}"""
        import urllib.request
        out: dict[str, dict] = {}
        for i in range(0, len(symbols), 60):
            batch = symbols[i:i+60]
            url = "http://hq.sinajs.cn/list=" + ",".join(batch)
            req = urllib.request.Request(
                url,
                headers={"Referer": "https://finance.sina.com.cn",
                         "User-Agent": "Mozilla/5.0"}
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

    # ---- 新浪历史日K (jsonp) ----
    def fetch_sina_daily(self, symbol: str, n: int = 300) -> Optional[pd.DataFrame]:
        """新浪日K jsonp 接口 (稳定, ~0.1s/只)。返回原始不复权日K。"""
        import urllib.request
        cache_key = f"sina_daily_{symbol}_{n}"
        cached = self._cache_get(cache_key)
        if cached is not None: return cached

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
        if len(df) >= 2: self._cache_set(cache_key, df)
        return df

    # ---- pytdx 历史日K (备用) ----
    def fetch_pytdx_daily(self, symbol: str,
                          ip: str = "123.125.108.14", port: int = 7709) -> Optional[pd.DataFrame]:
        if not HAS_PYTDX: return None
        api = TdxHq_API()
        try:
            ok = api.connect(ip, port, time_out=5)
            if not ok: return None
            market = 0 if symbol[0] in "69" else 1
            bars = api.get_security_bars(9, market, symbol, 0, 300)
            if not bars: return None
            df = pd.DataFrame(bars)
            df["date"] = pd.to_datetime(df["datetime"], unit="s")
            df = df[["date", "open", "high", "low", "close", "vol"]]
            df.columns = ["date", "open", "high", "low", "close", "volume"]
            df = df.sort_values("date").reset_index(drop=True).dropna()
            return df
        except Exception:
            return None
        finally:
            try: api.disconnect()
            except Exception: pass

    # ---- akshare 历史日K (最后兜底，单次不 sleep) ----
    def fetch_akshare_daily(self, symbol: str, start_date: str = "20250101") -> Optional[pd.DataFrame]:
        if not HAS_AKSHARE: return None
        try:
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                    start_date=start_date, adjust="qfq")
            if df is None or df.empty: return None
            df = df[["日期", "开盘", "收盘", "最高", "最低", "成交量"]]
            df.columns = ["date", "open", "close", "high", "low", "volume"]
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df
        except Exception:
            return None

    # ---- baostock 历史日K (作为 pytdx 后的备选) ----
    def fetch_baostock_daily(self, symbol: str, start_date: str = "20250101") -> Optional[pd.DataFrame]:
        try:
            code = f"sh.{symbol}" if symbol.startswith("6") or symbol.startswith("9") else f"sz.{symbol}"
            start_ymd = f"{start_date[0:4]}-{start_date[4:6]}-{start_date[6:8]}"
            end_ymd = datetime.now().strftime("%Y-%m-%d")
            rs = query_history_k_data_plus_retry(
                code, "date,open,high,low,close,volume",
                start_date=start_ymd, end_date=end_ymd,
                frequency="d", adjustflag="2")
            if rs is None:
                return None
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                return None
            df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True).dropna()
            if len(df) >= 2:
                log.info(f"  ✅ {name}({symbol}): {len(df)} 条日线 [baostock]")
                return df
        except Exception as e:
            log.warning(f"  ⚠️ {name} baostock历史失败: {e}")
        return None

    # ---- 统一历史日K 获取 (降级链: 新浪日K -> pytdx -> baostock -> akshare) ----
    def get_daily_hist(self, symbol: str, name: str,
                       start_date: str = "20250101") -> Optional[pd.DataFrame]:
        """按技能文档推荐降级链获取历史日线"""
        # Level 1: 新浪日K (稳定, 首选)
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

        # Level 3: baostock (单例会话，重试机制)
        try:
            df = self.fetch_baostock_daily(symbol, start_date)
            if df is not None and len(df) >= 2:
                return df.reset_index(drop=True)
        except Exception as e:
            log.warning(f"  ⚠️ {name} baostock历史失败: {e}")

        # Level 4: akshare (单次，失败不 sleep)
        try:
            df = self.fetch_akshare_daily(symbol, start_date)
            if df is not None and len(df) >= 2:
                log.info(f"  ✅ {name}({symbol}): {len(df)} 条日线 [akshare]")
                return df.reset_index(drop=True)
        except Exception as e:
            log.warning(f"  ⚠️ {name} akshare历史失败: {e}")

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
        vol_profile = pd.cut(recent["close"], bins=bin_edges, labels=False)
        vol_by_bin = recent.groupby(vol_profile)["volume"].sum()
        if not vol_by_bin.empty:
            poc_bin = vol_by_bin.idxmax()
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

def calc_full_signal_whitelist(df: pd.DataFrame) -> dict:
    """完整技术指标信号 —— 仅白名单指标"""
    if len(df) < 60:
        return {"error": "历史数据不足 60 根"}
    
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
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    latest_date = str(latest["date"].date())

    close = df["close"]
    ef = close.ewm(span=12, adjust=False).mean()
    es = close.ewm(span=26, adjust=False).mean()
    dif = ef - es
    dea = dif.ewm(span=9, adjust=False).mean()

    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    macd_bull = (dif.iloc[-1] > dea.iloc[-1]) and (dif.iloc[-2] <= dea.iloc[-2])
    macd_bear = (dif.iloc[-1] < dea.iloc[-1]) and (dif.iloc[-2] >= dea.iloc[-2])
    rsi_low = rsi.iloc[-1] < 50

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    rsi_now = float(rsi.iloc[-1])

    sig1_buy = macd_bull and rsi_low
    sig1_sell = macd_bear

    sig2_buy = macd_bull and (ema12.iloc[-1] > ema26.iloc[-1]) and (rsi_now < 60)
    sig2_sell = macd_bear or (latest["close"] < ema26.iloc[-1])

    dif_direction = "多头金叉" if dif.iloc[-1] > dea.iloc[-1] else "多头死叉"
    ema_direction = "多头EMA12>26" if ema12.iloc[-1] > ema26.iloc[-1] else "多头EMA12<26"

    return {
        "date": latest_date,
        "price": round(float(latest["close"]), 2),
        "change_pct": round((float(latest["close"]) / float(prev["close"]) - 1) * 100, 2),
        "volume": int(latest["volume"]),
        "indicators": {
            "MACD_DIF": round(float(dif.iloc[-1]), 4),
            "MACD_DEA": round(float(dea.iloc[-1]), 4),
            "MACD_state": dif_direction,
            "EMA12": round(float(ema12.iloc[-1]), 2),
            "EMA26": round(float(ema26.iloc[-1]), 2),
            "EMA_state": ema_direction,
            "RSI14": round(rsi_now, 1),
        },
        "signals": {
            "strategy1": {"name": "MACD+RSI<50", "buy": bool(sig1_buy), "sell": bool(sig1_sell),
                          "rsi_filter": rsi_now < 50,
                          "desc": f"MACD{'金叉' if macd_bull else '状态'} + RSI{round(rsi_now,1)}"},
            "strategy2": {"name": "综合最优(EMA+MACD+RSI)", "buy": bool(sig2_buy), "sell": bool(sig2_sell),
                          "desc": f"MACD金叉:{macd_bull} EMA12>26:{ema12.iloc[-1] > ema26.iloc[-1]} RSI<60:{rsi_now < 60}"}
        }
    }

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
    cfg = load_strategy_alert_config()
    stocks_cfg = cfg.get("stocks", {})
    state = _load_intraday_state()
    fired = state.get("fired", {})
    alerts = []
    triggered = []

    for item in spot_results:
        if "error" in item: continue
        sym = item["symbol"]
        scfg = stocks_cfg.get(sym)
        if not scfg: continue
        price = float(item["price"])
        chg = float(item["change_pct"])
        levels = scfg.get("levels", {})
        action = scfg.get("action", "")
        type_ = scfg.get("type", "")

        bl = levels.get("break_below")
        if bl is not None and price > 0 and price <= bl:
            msg = f"跌破止损位{bl:.2f} (现{price:.2f}) 应变{action}"
            alert = _mk_alert("break_below", item, scfg, bl, msg, "HIGH")
            alerts.append(alert)
            if fired.get(f"break_below:{sym}") != price:
                triggered.append(alert)
                fired[f"break_below:{sym}"] = price

        ba = levels.get("break_above")
        if ba is not None and price > 0 and price >= ba:
            msg = f"升破关键位{ba:.2f} (现{price:.2f}) 应{action}"
            alert = _mk_alert("break_above", item, scfg, ba, msg, "MEDIUM")
            alerts.append(alert)
            if fired.get(f"break_above:{sym}") != price:
                triggered.append(alert)
                fired[f"break_above:{sym}"] = price

        blz = levels.get("buy_zone_low")
        if blz is not None and price > 0 and (bl is None or price > bl) and price <= blz:
            msg = f"进入买入参考区 (现{price:.2f} 接近{blz:.2f}) 可考虑低吸"
            alert = _mk_alert("buy_zone", item, scfg, blz, msg, "LOW")
            alerts.append(alert)
            if fired.get(f"buy_zone:{sym}") != price:
                triggered.append(alert)
                fired[f"buy_zone:{sym}"] = price

        dd = scfg.get("drop_pct")
        if dd is not None and chg <= dd:
            msg = f"日内跌超{abs(dd):.1f}% ({chg:.1f}%) 注意风控"
            alert = _mk_alert("drop_pct", item, scfg, dd, msg, "HIGH")
            alerts.append(alert)
            if fired.get(f"drop_pct:{sym}") != chg:
                triggered.append(alert)
                fired[f"drop_pct:{sym}"] = chg

        sg = scfg.get("surge_pct")
        if sg is not None and chg >= sg:
            msg = f"日内涨超{sg:.1f}% ({chg:.1f}%) 注意止盈/追高风险"
            alert = _mk_alert("surge_pct", item, scfg, sg, msg, "MEDIUM")
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
        if r is None or r["last"] == 0 or r["pre_close"] == 0: continue
        chg = (r["last"] / r["pre_close"] - 1) * 100
        spot_results.append({"symbol": code, "name": name, "price": round(r["last"], 2),
                             "change_pct": round(chg, 2)})
    alerts, triggered = evaluate_strategy_alerts(spot_results)
    out = {
        "mode": "intraday_alert",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scanned": len(spot_results),
        "total_alert_rules": len(alerts),
        "alerts": alerts,
        "triggered_this_run": triggered,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


# ============================================================================
# 交易日判断
# ============================================================================

def is_trading_day() -> bool:
    today = datetime.now()
    if today.weekday() >= 5:
        log.info("今天是周末，非交易日。")
        return False
    if not HAS_AKSHARE:
        log.warning("akshare 不可用，默认放行。")
        return True
    try:
        trade_cal = ak.tool_trade_date_hist_sina()
        today_str = today.strftime("%Y-%m-%d")
        if today_str in trade_cal["trade_date"].values:
            row = trade_cal[trade_cal["trade_date"] == today_str]
            if not row.empty and row.iloc[0]["is_open"] == 1:
                return True
            log.info(f"{today_str} 非交易日。")
            return False
        return True
    except Exception as e:
        log.warning(f"交易日历接口异常: {e}，默认放行。")
        return True


# ============================================================================
# 主入口
# ============================================================================

def main():
    today = datetime.now().strftime("%Y-%m-%d")
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
        if r is None or r["last"] == 0:
            spot_results.append({"symbol": code, "name": name, "error": "无行情"})
            continue
        if not data_date: data_date = r["date"]
        chg = (r["last"] / r["pre_close"] - 1) * 100 if r["pre_close"] else 0
        spot_results.append({
            "symbol": code, "name": name,
            "price": round(r["last"], 2), "change_pct": round(chg, 2),
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
        for item in spot_results:
            if "error" in item:
                output["watchlist_scan"].append(item)
                output["summary"]["fail_count"] += 1
                continue
            sym = item["symbol"]
            df = fetcher.get_daily_hist(sym, item["name"])
            if df is not None and len(df) >= 60:
                try:
                    sig = calc_full_signal_whitelist(df)
                    sig["symbol"] = sym
                    sig["name"] = item["name"]
                    output["watchlist_scan"].append(sig)
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

    bs_logout()
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    if "--intraday" in sys.argv:
        run_intraday_alert_mode()
    else:
        main()