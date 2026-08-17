#!/usr/bin/env python3
"""
��后全���� v3.0 — 30只关注股信号 + 中联重科双策略持��
======================================================
重构依据: astock-unified-financial ��能文档 (第10/11/12/13/14/15章)
��心改进:
  1. 统一��心工具��数 (safe_float, parse_amount, parse_percent, convert_share, convert_sina_percent)
  2. ��重试+降级的通用数据获取器 (fetch_with_fallback + bs_query_with_retry + ak_safe_call)
  3. ��存��存类 (FinancialDataFetcher ��式) 复用 Baostock 登录/查��
  4. 新�� hq.sinajs.cn + 新��日K jsonp 双接口显式封装 (Referer、超时、重试)
  5. pandas_ta 列名动态匹配，规��版本差��
  6. 统一日志/错误处理/数据日期对��
  7. 移除��编码、重复代码、��余��辑

数据源路由 (MEMORY.md 2026-08-02 权威版):
  实时行情: 新�� hq.sinajs.cn (��定) → pytdx → akshare
  K线历史:  新��日K jsonp (��定, 0.1s/只) → pytdx → akshare东财(单次不sleep)
  ���务/新闻: akshare (同花顺/新��/巨��)

运行: python3 analysis/close_scan_v2.py
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

# 可选依��
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

# ============================================================================
# 配置常量
# ============================================================================

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
STATE_FILE = os.path.join(WORKSPACE, "data", "zhonglian_state.json")
CACHE_DIR = os.path.join(WORKSPACE, "data_cache")

CAPITAL = 100_000
COMMISSION = 0.0003
SLIPPAGE = 0.001

# 关注股权威清单 (MEMORY.md 2026-08-06: 30只，��除 900925 机电B股)
WATCHLIST = [
    ("600030", "中信证��"), ("601066", "中信建投"), ("600036", "��商银行"),
    ("601995", "中金公司"), ("000987", "越秀资本"),
    ("600584", "长电科技"), ("688981", "中��国际"), ("002156", "通富微电"),
    ("002413", "雷科防务"),
    ("300014", "亿����能"), ("002466", "天����业"), ("601865", "福莱特"),
    ("300285", "国��材料"), ("603308", "应流股份"), ("300124", "��川技术"),
    ("601100", "��立��压"), ("002318", "久立特材"), ("300719", "安达维尔"),
    ("002335", "科华数据"),
    ("600660", "福������"), ("600570", "��生电子"), ("605566", "福莱��特"),
    ("000157", "中联重科"), ("601061", "中信金属"),
    # 2026-07-29 新增
    ("600160", "巨化股份"),
    ("600346", "��力石化"), ("000708", "中信特��"), ("300748", "金力永��"),
    # 2026-08-06 替换: ��上能电气，加打印机国产替代��头
    ("002180", "��图科技"), ("300847", "中船汉光"),
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

# ============================================================================
# 第10章：��心工具��数
# ============================================================================

NO_DATA = "无"

def safe_float(val: Any, default: Any = NO_DATA) -> Any:
    """安全转浮点，处理 None/空��/NaN/False/超限值"""
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
    """解��带单位的金��字符��，统一转为亿元
    '1483.91亿' -> 1483.91
    '4.82万亿'  -> 48200
    '3391.23万' -> 0.339123
    纯数字(元)  -> 原值 (调用者需自行 ��1亿)
    无法解��    -> None
    """
    if s is None or s == "" or s == "False":
        return None
    s = str(s).strip()
    m = re.match(r"^([-\d.]+)\s*万亿$", s)
    if m:
        return float(m.group(1)) * 10000
    m = re.match(r"^([-\d.]+)\s*亿$", s)
    if m:
        return float(m.group(1))
    m = re.match(r"^([-\d.]+)\s*万$", s)
    if m:
        return float(m.group(1)) / 10000
    try:
        return float(s)  # 纯数字，单位需调用者判断
    except (ValueError, TypeError):
        return None


def parse_percent(s: Any) -> Optional[float]:
    """解��百分比字符��
    '6.22%' -> 6.22
    纯数字  -> 原值 (可能是小数也可能是百分比)
    """
    if s is None or s == "" or s == "False":
        return None
    s = str(s).strip()
    m = re.match(r"^([-\d.]+)\s*%$", s)
    if m:
        return float(m.group(1))
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def convert_share(val: Any) -> Any:
    """Baostock totalShare/liqaShare 自动单位����与转换 (→亿股)"""
    f = safe_float(val)
    if f == NO_DATA:
        return NO_DATA
    if f < 10000 and f > 0:
        return f / 10000  # 万股 -> 亿股
    return f / 100000000  # ��� -> 亿股


def convert_sina_percent(val: Any) -> Any:
    """新��(%)列: 判断是小数还是百分比，统一转为百分比"""
    f = safe_float(val)
    if f == NO_DATA:
        return NO_DATA
    if abs(f) < 2:  # 小数形式如 0.92
        return f * 100
    return f  # ��是百分比如 92.15


# ============================================================================
# 第13章：错误处理与重试 / 第14章：数据源降级策略
# ============================================================================

def bs_query_with_retry(fn: Callable, max_retries: int = 3, wait_seconds: float = 2.0):
    """带重试的 baostock ����"""
    for attempt in range(max_retries):
        try:
            rs = fn()
            if rs.error_code == "0":
                return rs
            log.warning(f"  [BS] ����错误: {rs.error_msg}, 重试 {attempt+1}/{max_retries}")
        except (socket.timeout, TimeoutError, OSError) as e:
            log.warning(f"  [BS] 网����常: {e}, 重试 {attempt+1}/{max_retries}")
        time.sleep(wait_seconds)
    return None


def ak_safe_call(fn: Callable, *args, default=None, **kwargs):
    """安全调用 akshare ��口"""
    try:
        result = fn(*args, **kwargs)
        if result is None or (hasattr(result, "empty") and result.empty):
            return default
        return result
    except Exception as e:
        log.warning(f"  [AK] ��口��常: {e}")
        return default


def fetch_with_fallback(primary_fn: Callable, fallback_fn1: Optional[Callable] = None,
                        fallback_fn2: Optional[Callable] = None) -> Any:
    """通用降级获取: 主数据源 -> 备用1 -> 备用2 -> NO_DATA"""
    result = primary_fn()
    if result is not None and result != NO_DATA:
        return result
    if fallback_fn1:
        result = fallback_fn1()
        if result is not None and result != NO_DATA:
            return result
    if fallback_fn2:
        result = fallback_fn2()
        if result is not None and result != NO_DATA:
            return result
    return NO_DATA


# ============================================================================
# 第12/15.1章：FinancialDataFetcher 类 - ��存��存 + Baostock 登录管理
# ============================================================================

class FinancialDataFetcher:
    """财务/行情数据获取器 - ��存��存 + 双源模式 + 登录复用"""

    def __init__(self):
        self._cache: dict[str, Any] = {}
        self._bs_logged_in = False
        self._last_login_check = 0.0

    # ---- Baostock 登录管理 ----
    def _ensure_bs_login(self) -> bool:
        """确保已登录 (带简单的会话保活��查)"""
        if self._bs_logged_in:
            # 简单保活：每 30 分钟重新登录一次
            if time.time() - self._last_login_check < 1800:
                return True
        try:
            import baostock as bs
            lg = bs.login()
            if lg.error_code != "0":
                log.error(f"[BS] 登录失败: {lg.error_msg}")
                return False
            self._bs_logged_in = True
            self._last_login_check = time.time()
            return True
        except Exception as e:
            log.error(f"[BS] 登录��常: {e}")
            return False

    def bs_logout(self):
        if self._bs_logged_in:
            try:
                import baostock as bs
                bs.logout()
            except Exception:
                pass
            self._bs_logged_in = False

    # ---- 通用��存查�� ----
    def _cache_get(self, key: str) -> Optional[Any]:
        return self._cache.get(key)

    def _cache_set(self, key: str, value: Any):
        self._cache[key] = value

    # ---- 新��实时行情 (hq.sinajs.cn) ----
    def fetch_sina_spot(self, symbols: list[str]) -> dict[str, dict]:
        """批量拉取新��实时行情，返回 {纯代码: {...}}"""
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
                if '="' not in line:
                    continue
                key = line.split("hq_str_")[1].split("=")[0]
                val = line.split('"')[1].split(",")
                if len(val) < 10:
                    continue
                symbol = key[2:]  # ���� sh/sz 前��
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

    # ---- 新��历史日K (jsonp) ----
    def fetch_sina_daily(self, symbol: str, n: int = 300) -> Optional[pd.DataFrame]:
        """新��日K jsonp ��口 (��定, ~0.1s/只)。返回原始不复权日K。"""
        import urllib.request
        cache_key = f"sina_daily_{symbol}_{n}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        pref = "sh" if symbol[0] in "69" else "sz"
        url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService."
               f"getKLineData?symbol={pref}{symbol}&scale=240&ma=no&datalen={n}")
        req = urllib.request.Request(
            url,
            headers={"Referer": "https://finance.sina.com.cn",
                     "User-Agent": "Mozilla/5.0"}
        )
        try:
            raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        except Exception as e:
            log.warning(f"[SINA-DAILY] {symbol} 请求失败: {e}")
            return None

        m = re.search(r"=\s*\((\[.*\])\)\s*;", raw, re.S)
        if not m:
            i, j = raw.find("["), raw.rfind("]")
            if i < 0 or j <= i:
                return None
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

    # ---- pytdx ��史日K (备用) ----
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
            df.columns = ["date", "open", "close", "high", "low", "volume"]
            df = df.sort_values("date").reset_index(drop=True).dropna()
            return df
        except Exception:
            return None
        finally:
            try:
                api.disconnect()
            except Exception:
                pass

    # ---- akshare ��史日K (最后��底，单次不 sleep) ----
    def fetch_akshare_daily(self, symbol: str, start_date: str = "20250101") -> Optional[pd.DataFrame]:
        if not HAS_AKSHARE:
            return None
        try:
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                    start_date=start_date, adjust="qfq")
            if df is None or df.empty:
                return None
            df = df[["日期", "开��", "收��", "最高", "最低", "成交量"]]
            df.columns = ["date", "open", "close", "high", "low", "volume"]
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df
        except Exception:
            return None

    # ---- 统一历史日K ���取 (降级��: 新��日K -> pytdx -> akshare) ----
    def get_daily_hist(self, symbol: str, name: str,
                       start_date: str = "20250101") -> Optional[pd.DataFrame]:
        """按技能文档推��降级��获取历史日线"""
        # Level 1: 新��日K (��定, ��选)
        try:
            df = self.fetch_sina_daily(symbol)
            if df is not None and len(df) >= 2:
                if start_date:
                    cut = pd.to_datetime(start_date)
                    df = df[df["date"] >= cut]
                if len(df) >= 2:
                    log.info(f"  �� {name}({symbol}): {len(df)} 条日线 [新��日K]")
                    return df.reset_index(drop=True)
        except Exception as e:
            log.warning(f"  ������ {name} 新��日K失败: {e}")

        # Level 2: pytdx
        try:
            df = self.fetch_pytdx_daily(symbol)
            if df is not None and len(df) >= 2:
                if start_date:
                    cut = pd.to_datetime(start_date)
                    df = df[df["date"] >= cut]
                if len(df) >= 2:
                    log.info(f"  �� {name}({symbol}): {len(df)} 条日线 [pytdx]")
                    return df.reset_index(drop=True)
        except Exception as e:
            log.warning(f"  ������ {name} pytdx历史失败: {e}")

        # Level 3: akshare (单次，失败不 sleep)
        try:
            df = self.fetch_akshare_daily(symbol, start_date)
            if df is not None and len(df) >= 2:
                log.info(f"  �� {name}({symbol}): {len(df)} 条日线 [akshare]")
                return df.reset_index(drop=True)
        except Exception as e:
            log.warning(f"  ������ {name} akshare历史失败: {e}")

        return None


# ============================================================================
# 单例获取器 (全局复用登录/��存)
# ============================================================================

_fetcher_instance: Optional[FinancialDataFetcher] = None

def get_fetcher() -> FinancialDataFetcher:
    global _fetcher_instance
    if _fetcher_instance is None:
        _fetcher_instance = FinancialDataFetcher()
    return _fetcher_instance


# ============================================================================
# 信号计算相关
# ============================================================================

def _pick_col(df: pd.DataFrame, *candidates: str) -> Optional[str]:
    """动态匹配 pandas_ta 列名，规��版本差��"""
    for c in candidates:
        if c in df.columns:
            return c
    # ����匹配��底
    for c in candidates:
        for col in df.columns:
            if c.lower() in col.lower():
                return col
    return None


def calc_full_signal(df: pd.DataFrame) -> dict:
    """完整技术指标信号 (需历史日线数据 >= 60根)"""
    if not HAS_PANDAS_TA:
        return {"error": "pandas_ta 未安装"}

    close = df["close"]

    # MACD
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    macd_col = _pick_col(macd_df, "MACD_12_26_9", "MACD")
    signal_col = _pick_col(macd_df, "MACDs_12_26_9", "MACD_SIGNAL")
    if not macd_col or not signal_col:
        return {"error": "MACD 列名匹配失败"}

    macd_line = macd_df[macd_col]
    signal_line = macd_df[signal_col]

    # EMA
    ema12 = ta.ema(close, length=12)
    ema26 = ta.ema(close, length=26)

    # RSI
    rsi14 = ta.rsi(close, length=14)

    vol_ma20 = df["volume"].rolling(20).mean()

    cur = df.iloc[-1]
    close_now = float(cur["close"])
    close_prev = float(df.iloc[-2]["close"])
    change_pct = round((close_now / close_prev - 1) * 100, 2)

    macd_now = float(macd_line.iloc[-1])
    macd_prev = float(macd_line.iloc[-2])
    sig_now = float(signal_line.iloc[-1])
    sig_prev = float(signal_line.iloc[-2])

    rsi = float(rsi14.iloc[-1]) if pd.notna(rsi14.iloc[-1]) else 50.0
    ema12_v = float(ema12.iloc[-1]) if pd.notna(ema12.iloc[-1]) else close_now
    ema26_v = float(ema26.iloc[-1]) if pd.notna(ema26.iloc[-1]) else close_now
    vol_ratio = float(cur["volume"] / vol_ma20.iloc[-1]) if vol_ma20.iloc[-1] > 0 else 1.0

    macd_bull = macd_now > sig_now
    macd_cross_up = (macd_prev <= sig_prev) and (macd_now > sig_now)
    macd_cross_down = (macd_prev >= sig_prev) and (macd_now < sig_now)
    ema_bull = ema12_v > ema26_v

    score = 0
    reasons = []
    risks = []

    if macd_cross_up and rsi < 55:
        score += 2
        reasons.append(f"MACD金��+RSI{rsi:.0f}<55")
    elif macd_bull:
        score += 1
        reasons.append("MACD多头")
    if ema_bull:
        score += 1
        reasons.append("EMA多头排列")
    if rsi < 30:
        score += 1
        reasons.append(f"RSI超��({rsi:.0f})")
    elif rsi > 70:
        score -= 1
        risks.append(f"RSI超��({rsi:.0f})")
    if vol_ratio > 0.6 and close_now > close_prev:
        score += 1
        reasons.append("放量上��")
    if macd_cross_down:
        score -= 2
        risks.append("MACD死��")
    if not ema_bull:
        score -= 1
        risks.append("EMA空头排列")

    if score >= 3:
        level = "���� ��烈��入"
    elif score >= 1:
        level = "���� 关注"
    elif score <= -2:
        level = "���� ��烈��出"
    elif score <= -1:
        level = "���� �����"
    else:
        level = "��� 中性"

    data_date = cur["date"]
    if hasattr(data_date, "date"):
        data_date = str(data_date.date())
    else:
        data_date = str(data_date)

    return {
        "price": close_now,
        "change_pct": change_pct,
        "volume": int(cur["volume"]),
        "vol_ratio": round(vol_ratio, 2),
        "indicators": {
            "MACD": round(macd_now - sig_now, 4),
            "MACD_bull": macd_bull,
            "MACD_cross_up": macd_cross_up,
            "MACD_cross_down": macd_cross_down,
            "EMA12": round(ema12_v, 2),
            "EMA26": round(ema26_v, 2),
            "EMA_bull": ema_bull,
            "RSI14": round(rsi, 1),
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

    if chg > 3:
        score += 2; reasons.append(f"��幅{chg:.1f}%")
    elif chg > 1:
        score += 1; reasons.append(f"微��{chg:.1f}%")
    elif chg < -5:
        score -= 2; risks.append(f"��幅{chg:.1f}%")
    elif chg < -2:
        score -= 1; risks.append(f"下��{chg:.1f}%")

    if score >= 2:
        level = "���� ��烈��入"
    elif score >= 1:
        level = "���� 关注"
    elif score == 0:
        level = "��� 中性"
    elif score == -1:
        level = "���� �����"
    else:
        level = "���� ��烈��出"

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
# 中联重科双策略
# ============================================================================

def analyze_zhonglian(df: pd.DataFrame) -> dict:
    """中联重科双策略分��"""
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

    dif_direction = "����金��" if dif.iloc[-1] > dea.iloc[-1] else "����死��"
    ema_direction = "����EMA12>26" if ema12.iloc[-1] > ema26.iloc[-1] else "����EMA12<26"

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
                          "desc": f"MACD{'金��' if macd_bull else '状态'} + RSI{round(rsi_now,1)}"},
            "strategy2": {"name": "��合最��(EMA+MACD+RSI)", "buy": bool(sig2_buy), "sell": bool(sig2_sell),
                          "desc": f"MACD金��:{macd_bull} EMA12>26:{ema12.iloc[-1] > ema26.iloc[-1]} RSI<60:{rsi_now < 60}"}
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
        "strategy2": {"name": "��合最��(EMA+MACD+RSI)", "position": False, "entry_price": 0, "entry_date": None,
                      "capital": CAPITAL, "shares": 0},
    }


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)


def execute_trade(state: dict, signals: dict) -> tuple[list, list, dict, float, float]:
    """双策略交易��行"""
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
                msgs.append(f"���� {s_name} ��入 ��{price} x {shares}股")
        elif sig["sell"] and s["position"]:
            sell_value = s["shares"] * price * (1 - SLIPPAGE)
            fee = sell_value * COMMISSION
            net = sell_value - fee
            pnl = round(net - s["shares"] * s["entry_price"] * (1 + SLIPPAGE), 2)
            s["capital"] += net
            s["shares"] = 0
            s["position"] = False
            emoji = "����" if pnl > 0 else "����"
            actions.append((key, "SELL"))
            msgs.append(f"{emoji} {s_name} ��出 ��{price} ������{pnl}")

    total_val = (state["strategy1"]["capital"] + (state["strategy1"]["shares"] * price if state["strategy1"]["position"] else 0) +
                 state["strategy2"]["capital"] + (state["strategy2"]["shares"] * price if state["strategy2"]["position"] else 0))
    total_return = round((total_val - CAPITAL * 2) / (CAPITAL * 2) * 100, 2)
    return actions, msgs, state, round(total_val, 2), total_return


# ============================================================================
# 交易日判断
# ============================================================================

def is_trading_day() -> bool:
    """��查今天是否是A股交易日 (新��日历接口)"""
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
        log.warning(f"交易日历接口��常: {e}，默认放行。")
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

    # 非交易日标记，但不��截 (周末也能��，取最近交易日数据)
    if not is_trading_day():
        output["is_trading_day"] = False

    # ── Step 1: ���速 spot ���� (新�� hq.sinajs.cn) ──
    log.info("���� 新�� hq.sinajs.cn 行情����...")
    sina_symbols = [("sh" if c[0] in "69" else "sz") + c for c, _ in WATCHLIST]
    hq = fetcher.fetch_sina_spot(sina_symbols)
    data_date = ""
    spot_results = []

    for code, name in WATCHLIST:
        r = hq.get(code)
        if r is None or r["last"] == 0:
            spot_results.append({"symbol": code, "name": name, "error": "无行情"})
            continue
        if not data_date:
            data_date = r["date"]
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

    # ── Step 2: ��试获取历史数据做完整信号 ──
    test_df = fetcher.get_daily_hist("000157", "中联重科", start_date="20260725")
    use_full = test_df is not None and len(test_df) >= 2

    if use_full:
        log.info("���� ��史接口可用，进行完整技术指标����...")
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
                    sig = calc_full_signal(df)
                    sig["symbol"] = sym
                    sig["name"] = item["name"]
                    output["watchlist_scan"].append(sig)
                    lv = sig["signal"]["level"]
                    if "强烈��入" in lv: output["summary"]["strong_buy"] += 1
                    elif "关注" in lv: output["summary"]["watch"] += 1
                    elif "中性" in lv: output["summary"]["neutral"] += 1
                    elif "����" in lv: output["summary"]["caution"] += 1
                    elif "强烈��出" in lv: output["summary"]["strong_sell"] += 1
                except Exception as e:
                    output["summary"]["fail_count"] += 1
                    output["watchlist_scan"].append({**item, "error": str(e)})
            else:
                # ��史数据不足则用 spot 信号
                sig = calc_spot_signal(item)
                sig["symbol"] = sym
                sig["name"] = item["name"]
                sig["data_source"] = "spot"
                output["watchlist_scan"].append(sig)
                lv = sig["signal"]["level"]
                if "强烈��入" in lv: output["summary"]["strong_buy"] += 1
                elif "关注" in lv: output["summary"]["watch"] += 1
                elif "中性" in lv: output["summary"]["neutral"] += 1
                elif "����" in lv: output["summary"]["caution"] += 1
                elif "强烈��出" in lv: output["summary"]["strong_sell"] += 1
    else:
        log.info("���� ��史接口不可用，仅用 spot ��据做快速����")
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
            if "强烈��入" in lv: output["summary"]["strong_buy"] += 1
            elif "关注" in lv: output["summary"]["watch"] += 1
            elif "中性" in lv: output["summary"]["neutral"] += 1
            elif "����" in lv: output["summary"]["caution"] += 1
            elif "强烈��出" in lv: output["summary"]["strong_sell"] += 1

    # ── ��场概�� ──
    up = sum(1 for s in spot_results if "error" not in s and s["change_pct"] > 0)
    down = sum(1 for s in spot_results if "error" not in s and s["change_pct"] < 0)
    total_amt = round(sum(s.get("amount", 0) for s in spot_results if "error" not in s) / 1e8, 2)
    output["market_overview"] = {"up": up, "down": down, "total": len(spot_results),
                                  "total_amount_billion": total_amt}

    # ── 中联重科双策略 ──
    log.info("���� 分��中联重科双策略...")
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
            log.error(f"中联重科分��失败: {e}")
            output["zhonglian"] = {"error": str(e)}

    # 退出前登出 baostock
    fetcher.bs_logout()

    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()