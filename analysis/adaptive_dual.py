#!/usr/bin/env python3
"""
自适应双策略分配器 — 每股跑最优的两个策略
=============================================
基于 validation_*.json 的多窗口稳健评分，为每只股票找出
**score 排名前二**的策略，分别跑出当日信号，输出对比。

数据源: baostock(前复权) + 新浪实时补丁（当日数据）+ push2 跨族兜底（2026-10-19）
"""


# ── sys.path 样板（TOOLS.md 强制；2026-09-19）──
# 脚本模式下 sys.path[0] 是脚本所在目录而非工作区根，
# 必须显式插入工作区根（供 `from analysis.xxx`）+ analysis/（供子包引用）。
import os as _os, sys as _sys
_WORKSPACE = _os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (_WORKSPACE, _os.path.join(_WORKSPACE, "analysis")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os, sys, json, time, warnings
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Dict, List
import pandas as pd
import pandas_ta as ta
import baostock as bs
import numpy as np
import urllib.request

# 网络兜底：进程级 socket 默认超时（防数据源挂起导致永久阻塞）
try:
    from analysis.net_guard import install_default_timeout as _install_net_timeout
    _install_net_timeout()
except ImportError:
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from analysis.net_guard import install_default_timeout as _install_net_timeout
    _install_net_timeout()

warnings.filterwarnings("ignore")

# 导入护城河因子
from analysis.moat_factor import calc_moat_score, is_core_moat_stock, get_moat_tags

# 导入政权检测器 (P1 自适应参数)
try:
    from analysis.regime_detector import detect_regime
    HAS_REGIME_DETECTOR = True
except ImportError as e:
    HAS_REGIME_DETECTOR = False
    print(f"⚠️ regime_detector 导入失败: {e}")

# ═══════════════════════════════════════════
# 030 核心模块导入
# ═══════════════════════════════════════════
try:
    from analysis.fatal_risk_detector import FatalRiskDetector
    from analysis.position_sizer import PositionSizer, load_market_context as load_position_context
    from analysis.signal_arbitrator import SignalArbitrator, RawSignal, SignalAction
    from analysis.next_day_simulator import NextDaySimulator
    HAS_030_MODULES = True
except ImportError as e:
    HAS_030_MODULES = False
    print(f"⚠️ 030模块导入失败: {e}")


# ════════════════════════════════════════
# 出货形态检测（方案B：接入 adaptive_dual）
# ════════════════════════════════════════

def detect_distribution_patterns(df: pd.DataFrame, lookback: int = 5) -> list:
    """
    检测最近 lookback 根日K线中的出货形态。
    返回: [{pattern, strength, desc, idx}, ...]
    strength: 1-5，越高越可靠
    """
    if df is None or len(df) < lookback + 10:
        return []
    
    recent = df.tail(lookback).copy().reset_index(drop=True)
    o, h, l, c, v = recent["open"], recent["high"], recent["low"], recent["close"], recent["volume"]
    prev_c = c.shift(1)
    
    patterns = []
    
    for i in range(len(recent)):
        # 当前根
        o_i, h_i, l_i, c_i, v_i = o.iloc[i], h.iloc[i], l.iloc[i], c.iloc[i], v.iloc[i]
        pc_i = prev_c.iloc[i] if i > 0 else c.iloc[i-1] if i > 0 else c_i
        body = abs(c_i - o_i)
        upper_shadow = h_i - max(o_i, c_i)
        lower_shadow = min(o_i, c_i) - l_i
        is_bull = c_i > o_i
        is_bear = c_i < o_i
        
        # 成交量均值（前20根）
        vol_ma20 = df["volume"].tail(20 + lookback - i).head(20).mean() if len(df) >= 20 else v_i
        
        # 1. 长上影线/射击之星：高位 + 上影线 >= 实体2倍 + 实体小
        if upper_shadow >= body * 2 and body > 0 and not is_bear:
            # 判断高位：收盘价接近近20日高点
            high_20 = df["high"].tail(20 + lookback - i).head(20).max()
            if c_i >= high_20 * 0.95:
                patterns.append({
                    "pattern": "长上影线/射击之星",
                    "strength": 5,
                    "desc": f"第{i+1}根K线上影线{upper_shadow/body:.1f}倍实体，高位{high_20:.2f}附近",
                    "idx": len(df) - lookback + i
                })
        
        # 2. 巨量长阴：高位 + 阴线 + 量 >= 5日均量2倍 + 实体 >= 3%
        if is_bear and body / o_i >= 0.03 and v_i >= vol_ma20 * 2:
            high_20 = df["high"].tail(20 + lookback - i).head(20).max()
            if c_i >= high_20 * 0.9:
                patterns.append({
                    "pattern": "巨量长阴",
                    "strength": 5,
                    "desc": f"量比{v_i/vol_ma20:.1f}x，跌幅{body/o_i*100:.1f}%，高位出货",
                    "idx": len(df) - lookback + i
                })
        
        # 3. 黄昏之星：3根K - 大阳 -> 星线 -> 大阴(回吞阳线50%+)
        if i >= 2:
            c1, o1 = c.iloc[i-2], o.iloc[i-2]  # 第一根
            c2, o2 = c.iloc[i-1], o.iloc[i-1]  # 第二根(星线)
            body1 = abs(c1 - o1)
            body2 = abs(c2 - o2)
            body3 = body
            if c1 > o1 and body1 / o1 >= 0.02:  # 第一根大阳
                if body2 / o2 < 0.01:  # 星线(十字/小实体)
                    if is_bear and c_i < (o1 + c1) / 2:  # 第三根大阴回吞50%+
                        patterns.append({
                            "pattern": "黄昏之星",
                            "strength": 4,
                            "desc": f"大阳->星线->大阴回吞{(o1+c1)/2 - c_i:.2f}，趋势反转确认",
                            "idx": len(df) - lookback + i
                        })
        
        # 4. 双顶/M头：简化版 - 两次冲高相近 ±1.5% + 颈线跌破
        # 这里只检测最近是否有双顶雏形（需要更长周期，略过实时检测）
        
        # 5. 高位吊颈线：高位 + 下影线 >= 实体2倍 + 上影线极短 + 实体小
        if lower_shadow >= body * 2 and upper_shadow < body * 0.5 and body > 0:
            high_20 = df["high"].tail(20 + lookback - i).head(20).max()
            if c_i >= high_20 * 0.95:
                patterns.append({
                    "pattern": "高位吊颈线",
                    "strength": 3,
                    "desc": f"下影线{lower_shadow/body:.1f}倍实体，高位见顶信号",
                    "idx": len(df) - lookback + i
                })
        
        # 6. 乌云盖顶：上升趋势 + 大阳线次日大阴低开 + 阴实体深入阳实体50%+
        if i >= 1:
            c1, o1 = c.iloc[i-1], o.iloc[i-1]
            if c1 > o1 and (c1 - o1) / o1 >= 0.015:  # 前一日大阳
                if is_bear and o_i > c1 and c_i < (o1 + c1) / 2:  # 今日大阴低开并回吞50%+
                    patterns.append({
                        "pattern": "乌云盖顶",
                        "strength": 4,
                        "desc": f"前阳后阴，回吞前阳实体{(o1+c1)/2 - c_i:.2f}",
                        "idx": len(df) - lookback + i
                    })
        
        # 7. 高位横盘震荡出货：需要更长周期，简化检测箱体上沿多次试探
        # 略过实时检测
        
        # 8. 多重顶/圆弧顶：略过
        
        # 9. 断头铡刀：一根大阴线跌破 MA5/MA10/MA20 多条均线 + 量增
        if is_bear and body / o_i >= 0.025 and v_i >= vol_ma20 * 1.5:
            ma5 = ta.sma(df["close"], length=5).iloc[-lookback + i] if len(df) >= 5 else None
            ma10 = ta.sma(df["close"], length=10).iloc[-lookback + i] if len(df) >= 10 else None
            ma20 = ta.sma(df["close"], length=20).iloc[-lookback + i] if len(df) >= 20 else None
            broken = 0
            if ma5 and c_i < ma5: broken += 1
            if ma10 and c_i < ma10: broken += 1
            if ma20 and c_i < ma20: broken += 1
            if broken >= 2:
                patterns.append({
                    "pattern": "断头铡刀",
                    "strength": 5,
                    "desc": f"大阴线跌破{broken}条均线(MA5/10/20)，量比{v_i/vol_ma20:.1f}x",
                    "idx": len(df) - lookback + i
                })
        
        # 10. 向上跳空缺口回补：高位向上跳空 + 3日内被阴线完全回补
        if i >= 1 and i < lookback - 1:
            gap_up = o_i - c.iloc[i-1]
            if gap_up > c.iloc[i-1] * 0.015:  # 向上跳空 > 1.5%
                # 检查后续是否被回补
                for j in range(i+1, min(i+4, len(recent))):
                    if c.iloc[j] <= c.iloc[i-1] and c.iloc[j] < o.iloc[j]:  # 阴线回补
                        patterns.append({
                            "pattern": "向上跳空缺口回补",
                            "strength": 4,
                            "desc": f"跳空{gap_up/prev_c.iloc[i]*100:.1f}%在第{j-i}日被阴线回补",
                            "idx": len(df) - lookback + j
                        })
                        break
    
    # 去重：同形态只保留强度最高的
    seen = {}
    for p in patterns:
        key = p["pattern"]
        if key not in seen or p["strength"] > seen[key]["strength"]:
            seen[key] = p
    
    return list(seen.values())

WORKSPACE = "/Users/duguke/.openclaw/workspace"
OUTPUT_DIR = os.path.join(WORKSPACE, "analysis", "daily")
def _resolve_latest_validation_file():
    """动态解析最新 validation_*.json（文件名含日期，字典序=时间序，取最大）。
    修复(2026-10-17): 曾硬编码 validation_2026-08-01.json（29 只），8 月后新增的
    电网 8 只+翰宇不在兜底名单 → param_signal_diff 旧参数侧报「一侧无策略」，
    连续 2 期（10-10/10-17）跳过 8 只。无任何 validation 文件时回退 08-01 旧文件。"""
    import glob as _glob
    _files = _glob.glob(os.path.join(WORKSPACE, "analysis", "validation_*.json"))
    if not _files:
        return os.path.join(WORKSPACE, "analysis", "validation_2026-08-01.json")
    return max(_files)


VALIDATION_FILE = _resolve_latest_validation_file()
PARAMS_FILE = os.path.join(WORKSPACE, "data", "adaptive_params.json")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_latest_validation():
    """合并所有 validation_*.json (日期新者优先)，返回 {symbol: info}。
    解决：新股补评分写到新 validation 文件后，能被 adaptive 双策略自动拾取，
    同时保留旧文件里已有股票的评分。批量文件缺失/损坏时回退到旧的 VALIDATION_FILE。"""
    import glob
    files = glob.glob(os.path.join(WORKSPACE, "analysis", "validation_*.json"))
    files = [f for f in files if os.path.abspath(f) != os.path.abspath(VALIDATION_FILE) or True]
    files.sort()  # 文件名含日期, 自然升序 → 新的在后
    merged = {}
    for f in files:
        try:
            with open(f) as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                merged.update(data)  # 新文件覆盖同 key
        except Exception:
            continue
    if not merged and os.path.exists(VALIDATION_FILE):
        with open(VALIDATION_FILE) as fh:
            merged = json.load(fh)
    return merged

# ── 39只关注股 (含8只电力设备/特高压出海) ──
# ── 关注池 SSOT（TOOLS.md 红线：涉及股票列表必须引用 WATCHLIST，禁止硬编码）──
# 2026-10-16 对齐：原硬编码 38 只与 WATCHLIST(36) 漂移
# （多出福莱特 601865/奔图 002180/中船汉光 300847，缺 10-12 入池的翰宇药业 300199）
from analysis.fib_extension_scan import WATCHLIST
STOCKS = [(code, name) for code, name in WATCHLIST.items()]

# ── validation中文策略名 → 信号函数key 映射 ──
VALIDATION_STRAT_MAP = {
    "布林带+ATR": "bollinger",
    "KDJ+CCI": "kdj_cci",
    "EMA+OBV": "ema_obv",
    "EMA12/26金叉(基准C)": "ema_cross",
    "纯MACD(基准B)": "macd",
}

STRATEGY_LABELS = {
    "bollinger": "📊 布林带+ATR",
    "kdj_cci": "🎯 KDJ+RSI",
    "ema_obv": "📈 EMA+OBV",
    "ema_cross": "💹 EMA12/26",
    "macd": "📉 纯MACD",
    "ema_adaptive_vol": "🌊 波动自适应EMA",
}

# 各策略默认参数 (param_tune 未覆盖时使用)
DEFAULT_PARAMS = {
    "bollinger": {"length": 20, "std": 2.0, "atr_mult": 1.5},
    "kdj_cci": {"length": 9, "rsi_threshold": 40},
    "ema_obv": {"length": 20},
    "ema_cross": {"fast": 12, "slow": 26},
    "macd": {"fast": 12, "slow": 26, "signal": 9},
    "ema_adaptive_vol": {"fast": 8, "slow": 21, "atr_lookback": 60, "high_pct": 70, "low_pct": 30},
}

DEFAULT_STRATEGY = "ema_cross"


# ════════════════════════════════════════
# 数据获取（复用 adaptive_trader 逻辑）
# ════════════════════════════════════════

def fetch_today_realtime(symbols):
    prefix_map = {s: ("sh" if s.startswith("6") else "sz") for s in symbols}
    codes = [f"{prefix_map[s]}{s}" for s in symbols]
    url = "https://hq.sinajs.cn/list=" + ",".join(codes)
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    result = {}
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        text = resp.read().decode("gbk")
        for s in symbols:
            prefix = prefix_map[s]
            start = text.find(f"hq_str_{prefix}{s}=\"")
            if start < 0:
                continue
            start += len(f"hq_str_{prefix}{s}=\"")
            end = text.find("\"", start)
            if end < 0:
                continue
            fields = text[start:end].split(",")
            if len(fields) < 4:
                continue
            try:
                cur = float(fields[3])
                oh = float(fields[1])
                hi = float(fields[4]) if fields[4] else cur
                lo = float(fields[5]) if fields[5] else cur
                vol = int(fields[8]) if fields[8] else 0
                # 🔴 2026-10-11 修复：bar 日期必须取行情快照自带的交易日(fields[30])，
                # 不能用日历日 today——盘前/周末快照是上一交易日行情，误标 today 会把
                # T-1 收盘注入成“今日 bar”（dual.md 现价=T-1、涨跌全 0.00% 的根因）。
                qdate = fields[30].strip()[:10].replace("/", "-") if len(fields) > 30 and fields[30] else ""
                result[s] = {"date": qdate,
                             "open": oh, "close": cur, "high": hi, "low": lo, "volume": vol}
            except (ValueError, IndexError):
                pass
        return result
    except Exception:
        return {}


def _fetch_sina_daily(symbol: str, n: int = 300):
    """新浪日K jsonp 接口(可靠)。baostock 整体失败时的历史兜底，能过 60 条门槛并算技术指标。
    只提供原始日K(非前复权)，指标用相对阈值兼容。返回 df[date,open,high,low,close,volume]。"""
    import re as _re
    pref = "sh" if symbol.startswith("6") else "sz"
    u = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService."
         f"getKLineData?symbol={pref}{symbol}&scale=240&ma=no&datalen={n}")
    req = urllib.request.Request(u, headers={"Referer": "https://finance.sina.com.cn",
                                             "User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
    m = _re.search(r'=\s*\((\[.*\])\)\s*;', raw, _re.S)
    if not m:
        i, j = raw.find("["), raw.rfind("]")
        if i < 0 or j <= i:
            return None
        import json as _json
        arr = _json.loads(raw[i:j+1])
    else:
        import json as _json
        arr = _json.loads(m.group(1))
    if not arr:
        return None
    df = pd.DataFrame(arr)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["day"])
    df = df[["date", "open", "high", "low", "close", "volume"]].dropna()
    return df.sort_values("date").reset_index(drop=True)


_EXPECTED_BAR_DATE_CACHE = {"value": None}


def _expected_bar_date() -> str:
    """当前时刻应已存在的最新日线 bar 日期（进程级缓存，批量扫描只算一次）。

    判据（2026-10-11 重写，消除 T-1 假 bar）：
      - 交易日判定走 analysis.service.is_trading_day（SSOT，含节假日），
        取代旧实现 weekday<5（节假日会误判，国庆也曾注入假 bar）；
      - 盘前(09:30 前) = 上一交易日 —— 此时绝不存在“今日 bar”；
      - 交易日 09:30 后（盘中/盘后）= 今日。
    """
    if _EXPECTED_BAR_DATE_CACHE["value"] is not None:
        return _EXPECTED_BAR_DATE_CACHE["value"]
    from analysis.service import is_trading_day
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    expected = None
    if is_trading_day(today_str) and now.time() >= dtime(9, 30):
        expected = today_str
    if expected is None:
        d = now.date() - timedelta(days=1)
        for _ in range(12):
            ds = d.strftime("%Y-%m-%d")
            if is_trading_day(ds):
                expected = ds
                break
            d -= timedelta(days=1)
    expected = expected or today_str
    _EXPECTED_BAR_DATE_CACHE["value"] = expected
    return expected


_PUSH2_UT = "b2884a393a59ad64002292a3e90d46a5"  # 与 moneyflow_em._UT 同源公共 token


def _fetch_push2_today_bar(symbol: str, expected: str):
    """push2 实时快照 → 当日 OHLCV bar（新浪族潮汐双失败时的跨族兜底，2026-10-19）。

    背景：18:30 补录窗口内新浪 jsonp 日K 与 hq.sinajs spot 可能同窗全挂（TLS 潮汐，
    2026-10-19 实测）；push2 基础行情路径不受 fflow 封锁影响（诊断
    analysis/research/2026-10-19_push2_self_diagnosis：stock/get 三 host 200@0.03s）。
    硬守卫：f86 快照时间戳日期 == expected（与 spot 补录同范式，绝不伪造日期）。
    单位：push2 f47=手 ×100 → 股（对齐 baostock/新浪日K 口径，保 OBV 一致）。
    """
    try:
        from curl_cffi import requests as _cffi
        secid = ("1." if symbol.startswith(("6", "9")) else "0.") + symbol
        url = (f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}"
               f"&fields=f43,f44,f45,f46,f47,f86&fltt=2&invt=2&ut={_PUSH2_UT}")
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                 "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                   "Referer": "https://data.eastmoney.com/"}
        resp = _cffi.get(url, headers=headers, impersonate="chrome", timeout=8)
        data = (resp.json() or {}).get("data") or {}
        f86 = data.get("f86")
        if not f86:
            return None
        bar_date = datetime.fromtimestamp(int(f86)).strftime("%Y-%m-%d")
        if bar_date != expected:
            return None
        vals = [data.get(k) for k in ("f46", "f44", "f45", "f43")]
        if any(v in (None, "-", "") for v in vals):
            return None
        o, h, l, c = (float(v) for v in vals)
        if c <= 0:
            return None
        raw_vol = data.get("f47")
        vol = float(raw_vol) * 100.0 if raw_vol not in (None, "-", "") else 0.0
        return {"date": pd.Timestamp(bar_date), "open": o, "high": h,
                "low": l, "close": c, "volume": vol}
    except Exception as e:  # noqa: BLE001 —— 兕底失败必须可见（下游 4) 滞后告警兜底）
        print(f"  ⚠️ {symbol} push2 兕底异常: {e}")
        return None


def _backfill_stale_bars(symbol: str, df, latest: str, expected: str):
    """滞后补录（对齐 portfolio_sim._backfill_today 已验证范式，2026-10-11）：

    1) 新浪日K按真实 bar 日期合并（可跨多日、去重），不再是“伪造今日日期的 spot 行”；
    2) 仍缺今日 bar 时才用新浪 spot 补，且仅当快照日期(fields[30])确为今日（盘中/盘后）；
    2b) 新浪族双失败时 push2 stock/get 跨族兜底（f86 日期守卫，2026-10-19）；
    3) 补完做与前收的跳变自检（>±10.5% 疑似除权/错位，可见告警）；
    4) 全部失败时可见告警，不静默。
    """
    # 1) 新浪日K补录（真实日期）
    try:
        sina = _fetch_sina_daily(symbol, n=300)
        if sina is not None and len(sina) >= 2:
            sina_latest = sina["date"].iloc[-1].strftime("%Y-%m-%d")
            if sina_latest > latest:
                new_rows = sina[sina["date"] > pd.Timestamp(latest)]
                df = pd.concat([df, new_rows], ignore_index=True)
                df = df.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
                latest = df["date"].iloc[-1].strftime("%Y-%m-%d")
                print(f"  ✅ {symbol} 滞后补录(新浪日K)：最新 {latest}（预期 {expected}）")
    except Exception as e:
        print(f"  ⚠️ {symbol} 新浪日K补录异常: {e}")

    # 2) spot 补今日 bar（仅当快照确为今日行情）
    if latest < expected:
        rt = fetch_today_realtime([symbol])
        r = (rt or {}).get(symbol)
        if r and r.get("date") == expected:
            new_row = {"date": pd.Timestamp(r["date"]), "open": r["open"], "close": r["close"],
                       "high": r["high"], "low": r["low"], "volume": r["volume"]}
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            df = df.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
            latest = r["date"]
            print(f"  ✅ {symbol} spot 补今日 bar：{r['date']} 收盘 ¥{r['close']}")

    # 2b) push2 跨族兜底（2026-10-19：新浪族 jsonp 日K + spot 同窗潮汐全挂时的第三源）
    if latest < expected:
        bar = _fetch_push2_today_bar(symbol, expected)
        if bar is not None:
            df = pd.concat([df, pd.DataFrame([bar])], ignore_index=True)
            df = df.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
            latest = bar["date"].strftime("%Y-%m-%d")
            print(f"  ✅ {symbol} push2 补今日 bar：{latest} 收盘 ¥{bar['close']}")

    # 3) 跳变自检（补录 bar vs 前收）
    try:
        if len(df) >= 2:
            prev_close = float(df["close"].iloc[-2])
            new_close = float(df["close"].iloc[-1])
            if prev_close > 0 and abs(new_close / prev_close - 1) > 0.105:
                print(f"  ⚠️ {symbol} 补录 bar 与前收跳变 {new_close / prev_close - 1:+.1%}"
                      f"（>±10.5%，疑似除权/数据错位），指标可能失真")
    except Exception as e:
        print(f"  ⚠️ {symbol} 跳变自检异常: {e}")

    # 4) 仍滞后：可见告警
    if latest < expected:
        print(f"  ⚠️ {symbol} 数据滞后：最新 {latest} < 预期 {expected}，补录失败 —— 信号基于过期数据")
    return df


def fetch_data(symbol, start="20250101", max_retry=3, realtime_fallback=True):
    """统一走 DataRouter 获取历史日线，自动带降级链。

    新鲜度策略（2026-10-11 重写）：
      - 判据用 _expected_bar_date()（SSOT 交易日历 + 时段），不再用“最新bar < 日历日”；
        盘前 06:00 预期=T-1，绝不注入假 bar（旧实现把 T-1 收盘伪装成今日 bar，
        dual.md「现价=10-08、涨跌全 0.00%、data_date 误标今日」即此根因）；
      - 真滞后时：新浪日K真实日期合并补录 → spot 仅补“确为今日”的 bar → 失败可见告警。
    """
    from analysis.data_layer.router import get_router
    router = get_router()
    expected = _expected_bar_date() if realtime_fallback else None

    # 主路径：DataRouter（内含 新浪日K/baostock/pytdx/akshare 降级，且有进程级缓存）
    try:
        df = router.get_daily(symbol, "", start_date=start)
        if df is not None and len(df) >= 60:
            latest = df["date"].iloc[-1].strftime("%Y-%m-%d")
            if realtime_fallback and expected and latest < expected:
                df = _backfill_stale_bars(symbol, df, latest, expected)
            return df
    except Exception as e:
        print(f"  [DataRouter] {symbol} 获取失败: {e}")

    return None


# ════════════════════════════════════════
# 五个策略的信号发生器
# ════════════════════════════════════════

def _compute_atr_stop(price: float, atr_val: float) -> float:
    return price - 2.0 * atr_val if atr_val > 0 else 0.0

# 斐波那契扩展位止盈目标：收敛为单一实现（见 fib_extension_scan.compute_fib_targets）
try:
    from analysis.fib_extension_scan import compute_fib_targets  # noqa: F401
except ImportError:
    def compute_fib_targets(df, lookback: int = 150) -> dict:  # type: ignore
        return {}


def signal_bollinger_atr(df, params=None):
    p = dict(DEFAULT_PARAMS["bollinger"]); p.update(params or {})
    bb = ta.bbands(df["close"], length=p["length"], std=p["std"])
    bbu = bb[[c for c in bb.columns if "BBU" in c.upper()][0]]
    bbm = bb[[c for c in bb.columns if "BBM" in c.upper()][0]]
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    if float(prev["close"]) <= float(bbu.iloc[-2]) and float(row["close"]) > float(bbu.iloc[-1]):
        action = "🟢买入"; reason = f"布林上轨突破(¥{float(bbu.iloc[-1]):.2f}) ATR={atr_val:.2f} 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif float(prev["close"]) >= float(bbm.iloc[-2]) and float(row["close"]) < float(bbm.iloc[-1]):
        action = "🔴卖出"; reason = f"跌破中轨(¥{float(bbm.iloc[-1]):.2f})"
    return {"action": action, "reason": reason, "price": price,
            "indicators": f"上轨{float(bbu.iloc[-1]):.2f} 中轨{float(bbm.iloc[-1]):.2f} ATR{atr_val:.2f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def signal_kdj_cci(df, params=None):
    p = dict(DEFAULT_PARAMS["kdj_cci"]); p.update(params or {})
    cci = ta.cci(df["high"], df["low"], df["close"], length=14)
    cci_pct = cci.rolling(60).apply(lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-10) * 100)
    kdj = ta.kdj(df["high"], df["low"], df["close"], length=p["length"], signal=3)
    k_col = [c for c in kdj.columns if "K_" in c.upper()][0]
    d_col = [c for c in kdj.columns if "D_" in c.upper()][0]
    j_col = [c for c in kdj.columns if "J_" in c.upper()][0]
    k, d, j = kdj[k_col], kdj[d_col], kdj[j_col]
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    cpi = float(cci_pct.iloc[-1]) if pd.notna(cci_pct.iloc[-1]) else 50
    j_cross_up = float(j.iloc[-2]) <= float(k.iloc[-2]) and float(j.iloc[-1]) > float(k.iloc[-1])
    j_cross_down = float(j.iloc[-2]) >= float(k.iloc[-2]) and float(j.iloc[-1]) < float(k.iloc[-1])
    # ── 2026-08-18 修复#1：KDJ 系弱策略二次确认护栏 ──
    # KDJ+CCI 五策略回测垫底(总分20.6, 负夏普)却高频使用。给信号加趋势二次确认：
    # 买信号需 EMA12>EMA26（趋势向上）确认；卖信号需 EMA12<EMA26（趋势向下）确认，
    # 否则降级为持有/观望，避免弱策略单信号频繁误触发。
    ema12 = ta.ema(df["close"], length=12)
    ema26 = ta.ema(df["close"], length=26)
    ema12_v = float(ema12.iloc[-1]) if pd.notna(ema12.iloc[-1]) else price
    ema26_v = float(ema26.iloc[-1]) if pd.notna(ema26.iloc[-1]) else price
    trend_up = ema12_v > ema26_v
    trend_down = ema12_v < ema26_v
    guard_ema = params is not None and params.get("guard", True)
    if cpi < 20 and j_cross_up:
        if guard_ema and not trend_up:
            action = "持有"; reason = f"KDJ金叉但EMAT趋势未确认(EMA12 {ema12_v:.2f}<EMA26 {ema26_v:.2f})，弱策略观望，需二次确认"
        else:
            action = "🟢买入"; reason = f"CCI低位({cpi:.0f}%)+KDJ金叉 K={float(k.iloc[-1]):.1f}{' (EMAT确认)' if guard_ema else ''} 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif cpi > 80 or j_cross_down:
        reasons = []
        if cpi > 80:
            reasons.append(f"CCI高位({cpi:.0f}%)")
        if j_cross_down:
            reasons.append("KDJ死叉")
        if guard_ema and not trend_down:
            # 反向背离护栏：卖出信号但趋势仍向上 → 仅降级观望，不卖（避免震荡中被洗出）
            action = "持有"; reason = " / ".join(reasons) + f" 但EMA仍向上(EMA12 {ema12_v:.2f}>EMA26 {ema26_v:.2f})，弱策略观望，需二次确认"
        else:
            action = "🔴卖出"; reason = " | ".join(reasons)
    return {"action": action, "reason": reason, "price": price,
            "indicators": f"CCI_pct={cpi:.0f}% K={float(k.iloc[-1]):.1f} J={float(j.iloc[-1]):.1f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def signal_ema_obv(df, params=None):
    p = dict(DEFAULT_PARAMS["ema_obv"]); p.update(params or {})
    ema20 = ta.ema(df["close"], length=p["length"])
    obv = ta.obv(df["close"], df["volume"])
    obv_ema20 = ta.ema(obv, length=p["length"])
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    e20 = float(ema20.iloc[-1])
    obv_now = float(obv.iloc[-1])
    obv_ema = float(obv_ema20.iloc[-1])
    obv_cross_up = float(obv.iloc[-2]) <= float(obv_ema20.iloc[-2]) and obv_now > obv_ema
    obv_cross_down = float(obv.iloc[-2]) >= float(obv_ema20.iloc[-2]) and obv_now < obv_ema
    if price > e20 and obv_cross_up:
        action = "🟢买入"; reason = f"EMA20上方(¥{e20:.2f}) + OBV上穿 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif price < e20 or obv_cross_down:
        reasons = []
        if price < e20:
            reasons.append(f"跌破EMA20(¥{e20:.2f})")
        if obv_cross_down:
            reasons.append("OBV下穿")
        action = "🔴卖出"; reason = " | ".join(reasons)
    return {"action": action, "reason": reason, "price": price,
            "indicators": f"EMA20={e20:.2f} OBV/EMA={obv_now/obv_ema:.2f}x" if obv_ema > 0 else f"EMA20={e20:.2f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def signal_ema_cross(df, params=None):
    p = dict(DEFAULT_PARAMS["ema_cross"]); p.update(params or {})
    ema12 = ta.ema(df["close"], length=p["fast"])
    ema26 = ta.ema(df["close"], length=p["slow"])
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    e12, e26 = float(ema12.iloc[-1]), float(ema26.iloc[-1])
    pe12, pe26 = float(ema12.iloc[-2]), float(ema26.iloc[-2])
    if pe12 <= pe26 and e12 > e26:
        action = "🟢买入"; reason = f"EMA{p['fast']}({e12:.2f})金叉EMA{p['slow']}({e26:.2f}) 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif pe12 >= pe26 and e12 < e26:
        action = "🔴卖出"; reason = f"EMA{p['fast']}({e12:.2f})死叉EMA{p['slow']}({e26:.2f})"
    return {"action": action, "reason": reason, "price": price,
            "indicators": f"EMA{p['fast']}={e12:.2f} EMA{p['slow']}={e26:.2f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def signal_macd(df, params=None):
    p = dict(DEFAULT_PARAMS["macd"]); p.update(params or {})
    macd_df = ta.macd(df["close"], fast=p["fast"], slow=p["slow"], signal=p["signal"])
    dif_col = [c for c in macd_df.columns if c.upper().startswith("MACD_")][0]
    sig_col = [c for c in macd_df.columns if "MACDS_" in c.upper()][0]
    macd_line = macd_df[dif_col]
    signal_line = macd_df[sig_col]
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    m = float(macd_line.iloc[-1]); s = float(signal_line.iloc[-1])
    pm = float(macd_line.iloc[-2]); ps = float(signal_line.iloc[-2])
    if pm <= ps and m > s:
        action = "🟢买入"; reason = f"MACD金叉({m:.4f}/{s:.4f}) 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif pm >= ps and m < s:
        action = "🔴卖出"; reason = f"MACD死叉({m:.4f}/{s:.4f})"
    return {"action": action, "reason": reason, "price": price,
            "indicators": f"MACD柱={m-s:.4f} Signal={s:.4f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def ema_adaptive_vol_actions(df, params=None):
    """波动自适应EMA 的逐日动作序列（生产实现，信号与回测共用）。

    返回 (actions, ctx)。2026-10-17 从 signal_ema_adaptive_vol 内联循环抽出：
    3/36 票映射该策略但周回测候选集缺失，无法被周六流水线再验证，本抽出为闭环第一步。
    """
    p = dict(DEFAULT_PARAMS.get("ema_adaptive_vol", {}))
    p.update(params or {})
    fast = p.get("fast", 8)
    slow = p.get("slow", 21)
    atr_lookback = p.get("atr_lookback", 60)
    high_pct = p.get("high_pct", 70)
    low_pct = p.get("low_pct", 30)

    df = df.copy()
    df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    df["ATR_pct"] = df["ATR"] / df["close"] * 100

    actions, position = [], False
    atr_percentile, cur_fast, cur_slow, atr_val = 50.0, 12, 26, 0.0

    for i in range(max(30, atr_lookback), len(df)):
        row = df.iloc[i]
        dt = str(row["date"].date())
        price = float(row["close"])

        # 计算ATR百分位（过去atr_lookback天）
        atr_pct_series = df["ATR_pct"].iloc[max(0,i-atr_lookback):i+1]
        atr_pct = float(row["ATR_pct"]) if pd.notna(row["ATR_pct"]) else atr_pct_series.median()
        atr_percentile = (atr_pct_series < atr_pct).mean() * 100

        # 根据波动率选择EMA周期
        if atr_percentile > high_pct:      # 高波动
            cur_fast, cur_slow = 20, 50
        elif atr_percentile < low_pct:     # 低波动
            cur_fast, cur_slow = fast, slow
        else:
            cur_fast, cur_slow = 12, 26

        # 计算当前EMA
        ema_fast = ta.ema(df["close"].iloc[:i+1], length=cur_fast).iloc[-1]
        ema_slow = ta.ema(df["close"].iloc[:i+1], length=cur_slow).iloc[-1]
        ema_fast_prev = ta.ema(df["close"].iloc[:i], length=cur_fast).iloc[-1]
        ema_slow_prev = ta.ema(df["close"].iloc[:i], length=cur_slow).iloc[-1]

        ef, es = float(ema_fast), float(ema_slow)
        pef, pes = float(ema_fast_prev), float(ema_slow_prev)

        cross_up = pef <= pes and ef > es
        cross_down = pef >= pes and ef < es

        atr_val = float(df["ATR"].iloc[i]) if pd.notna(df["ATR"].iloc[i]) else 0

        if not position and cross_up:
            action = "🟢买入"
            reason = f"波动自适应EMA{cur_fast}/{cur_slow}金叉 ATR%ile={atr_percentile:.0f}% 止损¥{_compute_atr_stop(price, atr_val):.2f}"
            actions.append({"date": dt, "type": "BUY", "price": price, "reason": reason})
            position = True
        elif position and cross_down:
            action = "🔴卖出"
            reason = f"波动自适应EMA{cur_fast}/{cur_slow}死叉 ATR%ile={atr_percentile:.0f}%"
            actions.append({"date": dt, "type": "SELL", "price": price, "reason": reason})
            position = False

    return actions, {"atr_percentile": atr_percentile, "cur_fast": cur_fast,
                     "cur_slow": cur_slow, "atr_val": atr_val,
                     "last_price": float(df["close"].iloc[-1])}


def signal_ema_adaptive_vol(df, params=None):
    """波动率自适应EMA：ATR percentile 决定周期
    - 高波动(ATR%ile>70): EMA20/50 (慢速滤噪)
    - 低波动(ATR%ile<30): EMA8/21 (快速捕捉)
    - 中性: EMA12/26 (标准)
    2026-10-17: 循环体抽出至 ema_adaptive_vol_actions（与回测共用生产实现）。
    """
    actions, ctx = ema_adaptive_vol_actions(df, params)

    last_action = "持有"
    last_reason = "无信号"
    if actions:
        last_action = actions[-1]["type"]
        last_reason = actions[-1]["reason"]

    price = ctx["last_price"]
    atr_val = ctx["atr_val"]

    return {"action": last_action, "reason": last_reason, "price": price,
            "indicators": f"ATR%ile={ctx['atr_percentile']:.0f}% EMA{ctx['cur_fast']}/{ctx['cur_slow']}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


SIGNAL_FUNCS = {
    "bollinger": signal_bollinger_atr,
    "kdj_cci": signal_kdj_cci,
    "ema_obv": signal_ema_obv,
    "ema_cross": signal_ema_cross,
    "macd": signal_macd,
    "ema_adaptive_vol": signal_ema_adaptive_vol,
}


def calc_change_pct(df):
    cur = float(df.iloc[-1]["close"])
    prev = float(df.iloc[-2]["close"])
    return round((cur / prev - 1) * 100, 2)


# ════════════════════════════════════════
# 主扫描
# ════════════════════════════════════════

def load_dual_strategy_map():
    """读取每股 score 前二的策略 + 最优参数
    优先从 param_tune 结果 (adaptive_params.json) 读取每股各策略的 best_score 与 best_params;
    若 param_tune 未覆盖该股, 回退到 validation json 的策略级评分(用默认参数)。
    """
    dual = {}

    # ① 优先: param_tune 参数级结果
    params_map = None
    if os.path.exists(PARAMS_FILE):
        try:
            with open(PARAMS_FILE) as f:
                params_map = json.load(f).get("stocks", {})
        except Exception:
            params_map = None
    if params_map:
        for symbol, info in params_map.items():
            details = info.get("details", {}) if isinstance(info, dict) else {}
            scored = []
            for strat_key, dinfo in details.items():
                if not isinstance(dinfo, dict):
                    continue
                bp = dinfo.get("best_params")
                bs = dinfo.get("best_score")
                skey = dinfo.get("best_strategy_key", strat_key)
                # strat_key 可能是中文名, 转成信号key
                key = VALIDATION_STRAT_MAP.get(strat_key) or strat_key
                if key in SIGNAL_FUNCS and bp is not None and bs is not None:
                    scored.append({"strategy": key, "score": bs,
                                   "params": bp,
                                   "avg_return": dinfo.get("avg_return"),
                                   "avg_sharpe": dinfo.get("avg_sharpe")})
            if len(scored) >= 2:
                scored.sort(key=lambda x: x["score"], reverse=True)
                dual[symbol] = scored[:2]

    # ② 回退/补齐: 对 param_tune 未覆盖或不足2个策略的股票用 validation(合并所有validation文件,新者优先)
    val = load_latest_validation()
    for symbol, info in val.items():
        if symbol in dual and len(dual[symbol]) >= 2:
            continue
        details = info.get("details", [])
        ranked = sorted(details, key=lambda x: x.get("score", 0), reverse=True)
        top2 = []
        for d in ranked[:2]:
            key = VALIDATION_STRAT_MAP.get(d.get("strategy"))
            if key:
                top2.append({"strategy": key,
                             "score": d.get("score"),
                             "params": None,  # 用默认参数
                             "avg_return": d.get("avg_return"),
                             "avg_sharpe": d.get("avg_sharpe")})
        if top2:
            dual[symbol] = top2

    # 兜底: 凡在 STOCKS 但在 dual_map 无覆盖的股票(如新增新股 validation 未生成评分),
    # 给默认双策略(ema_cross + macd, 默认参数), 避免"validation缺失"导致现价显示0
    for symbol, name in STOCKS:
        if symbol not in dual:
            dual[symbol] = [
                {"strategy": "ema_cross", "score": 0.0, "params": None,
                 "avg_return": None, "avg_sharpe": None},
                {"strategy": "macd", "score": 0.0, "params": None,
                 "avg_return": None, "avg_sharpe": None},
            ]
    return dual


def scan_all():
    today = datetime.now().strftime("%Y-%m-%d")
    
    # ═══════════════════════════════════════════
    # 030 预检：致命风险 + 仓位计划 + 市场上下文
    # ═══════════════════════════════════════════
    fatal_risk = {"fatal_triggered": False, "high_risk_triggered": False}
    position_plan = {"buy_signal_multiplier": 1.0, "single_stock_max_pct": 0.05,
                     "total_limit_pct": 0.5, "total_limit_amount": 1_500_000,
                     "direction_allocation": {}}
    market_context = {"health_score": 5, "market_stage": "震荡筑底", "emotion_cycle": "修复"}
    
    if HAS_030_MODULES:
        try:
            fatal_detector = FatalRiskDetector()
            fatal_risk = fatal_detector.check()
            
            pos_context = load_position_context()
            sizer = PositionSizer(total_capital=REFERENCE_TOTAL_CAPITAL)
            pos_plan_obj = sizer.calculate(
                market_stage=pos_context["market_stage"],
                emotion_cycle=pos_context["emotion_cycle"],
                health_score=pos_context["health_score"],
                fatal_risk=fatal_risk,
                held_positions=pos_context.get("held_positions"),
                watchlist=[s for s, _ in STOCKS]
            )
            position_plan = {
                "buy_signal_multiplier": pos_plan_obj.buy_signal_multiplier,
                "single_stock_max_pct": pos_plan_obj.single_stock_max_pct,
                "single_stock_max_amount": pos_plan_obj.single_stock_max_amount,
                "total_limit_pct": pos_plan_obj.total_limit_pct,
                "total_limit_amount": pos_plan_obj.total_limit_amount,
                "direction_allocation": pos_plan_obj.direction_allocation,
                "risk_warnings": pos_plan_obj.risk_warnings,
            }
            market_context = pos_context
        except Exception as e:
            print(f"⚠️ 030预检模块运行失败: {e}")
    
    dual_map = load_dual_strategy_map()
    results = []
    stats = {"buy": 0, "sell": 0, "hold": 0, "agree": 0, "disagree": 0, "error": 0}
    strat_usage = {k: 0 for k in STRATEGY_LABELS}
    _bar_dates = []  # 各股实际行情 bar 日期，用于报告数据日期标注

    for idx, (symbol, name) in enumerate(STOCKS):
        top2 = dual_map.get(symbol)
        if not top2:
            results.append({"symbol": symbol, "name": name,
                            "error": "validation缺失", "strategies": [],
                            "price": None, "change_pct": 0, "consensus": "-"})
            stats["error"] += 1
            continue

        df = fetch_data(symbol)
        if df is None or len(df) < 60:
            results.append({"symbol": symbol, "name": name,
                            "error": "数据获取失败", "strategies": [],
                            "price": None, "change_pct": 0, "consensus": "-"})
            stats["error"] += 1
            continue

        change_pct = calc_change_pct(df)
        cur_price = float(df.iloc[-1]["close"])
        # 2026-10-17: ER20 效率比——事件研究验证的趋势态标志（因果无前视；
        # ER>=0.25 事前阈值；逐票级而非池级，池广度已验证无分离力）
        try:
            _c = df["close"].astype(float)
            _diff = _c.diff().abs()
            _den = float(_diff.rolling(20).sum().iloc[-1])
            er20 = round(float(_c.diff(20).abs().iloc[-1] / _den), 4) if len(_c) > 20 and _den > 0 else None
        except Exception:
            er20 = None
        regime_er = (("趋势" if er20 >= 0.25 else "震荡") if er20 is not None else None)
        # 2026-10-17: 前置5日涨跌（高位双杀/深跌后双卖 标注依据；因果无前视。
        # 事件研究: 前置仍涨的双卖 -0.79% vs 深跌后双卖 +0.81%，语义相反）
        try:
            _c5 = df["close"].astype(float)
            pre_chg_5d = (round(float(_c5.iloc[-1] / _c5.iloc[-6] - 1) * 100, 2)
                          if len(_c5) >= 6 else None)
        except Exception:
            pre_chg_5d = None
        try:
            _bar_dates.append(df["date"].iloc[-1].strftime("%Y-%m-%d"))
        except Exception as _e:
            print(f"  ⚠️ {symbol} bar 日期提取失败: {_e}")
        
        # 🔄 P1: 检测市场政权并获取自适应参数
        regime_params = {}
        regime_info = {}
        if HAS_REGIME_DETECTOR:
            try:
                regime_res = detect_regime(df)
                regime_params = regime_res.suggested_params
                regime_info = {
                    "regime": regime_res.regime,
                    "adx": regime_res.adx,
                    "di_plus": regime_res.di_plus,
                    "di_minus": regime_res.di_minus,
                    "chop": regime_res.chop,
                    "ema_gap_atr": regime_res.ema_gap_atr,
                    "chop_zone": regime_res.chop_zone,
                    "avoid_trade": regime_res.avoid_trade,
                    "reason": regime_res.reason
                }
                if regime_res.chop_zone:
                    print(f"  ⚠️ {symbol} 处于均线夹缝震荡区(chop_zone)，建议回避")
                elif regime_res.avoid_trade:
                    print(f"  ⚠️ {symbol} 政权={regime_res.regime}(ADX={regime_res.adx:.1f}, CHOP={regime_res.chop:.1f})，建议回避交易")
            except Exception as e:
                print(f"  ⚠️ {symbol} 政权检测失败: {e}")
        
        # 🔍 检测出货形态
        dist_patterns = detect_distribution_patterns(df, lookback=5)
        max_dist_strength = max([p["strength"] for p in dist_patterns], default=0)
        
        # 🏰 护城河因子
        moat_score, moat_tags = calc_moat_score(symbol)
        is_core = is_core_moat_stock(symbol)
        
        sigs = []
        for entry in top2:
            sname = entry["strategy"]
            strat_usage[sname] = strat_usage.get(sname, 0) + 1
            # 合并参数：优先级 regime_params > entry.params > DEFAULT_PARAMS
            params = dict(DEFAULT_PARAMS.get(sname, {}))
            if entry.get("params"):
                params.update(entry["params"])
            if regime_params.get(sname):
                params.update(regime_params[sname])
            try:
                sig = SIGNAL_FUNCS[sname](df, params)
                sigs.append({
                    "strategy": sname,
                    "strategy_label": STRATEGY_LABELS[sname],
                    "validation_score": entry["score"],
                    "avg_return": entry.get("avg_return"),
                    "params": params,
                    "price": cur_price,
                    "action": sig["action"],
                    "reason": sig["reason"],
                    "indicators": sig["indicators"],
                })
            except Exception as e:
                sigs.append({"strategy": sname, "error": str(e), "action": "错误"})

        # 判断两策略是否一致
        act1 = sigs[0]["action"] if sigs else "持有"
        act2 = sigs[1]["action"] if len(sigs) > 1 else "持有"
        # 归一化动作类型
        def typ(a):
            if "买入" in a:
                return "buy"
            if "卖出" in a:
                return "sell"
            return "hold"
        t1, t2 = typ(act1), typ(act2)
        agree = (t1 == t2 and t1 != "hold") or (t1 == "hold" and t2 == "hold")
        if (t1 != "hold" and t2 == t1):
            if t1 == "buy":
                stats["buy"] += 1
            else:
                stats["sell"] += 1
            stats["agree"] += 1
        elif t1 != "hold" or t2 != "hold":
            stats["disagree"] += 1
        else:
            stats["hold"] += 1

        # 🔴 出货形态过滤：若强度>=4且策略给买入，标记警告
        dist_warning = ""
        buy_signals = sum(1 for s in sigs if "买入" in s.get("action", ""))
        if max_dist_strength >= 4 and buy_signals > 0:
            pattern_names = ", ".join([p["pattern"] for p in dist_patterns if p["strength"] >= 4])
            dist_warning = f" ⚠️出货形态:{pattern_names}"
        
        # 构建原始信号供裁决引擎使用
        raw_signals = []
        for sg in sigs:
            raw_signals.append(RawSignal(
                source="adaptive_dual",
                symbol=symbol,
                name=name,
                action=sg.get("action", ""),
                strength=0,  # 由仲裁器归一化
                confidence=min(sg.get("validation_score", 0) / 100.0, 1.0) if sg.get("validation_score", 0) > 0 else 0.5,
                reason=f"[{sg.get('strategy_label', '')}] {sg.get('reason', '')}",
                indicators=sg.get("indicators", ""),
                strategy=sg.get("strategy", ""),
                validation_score=sg.get("validation_score", 0),
                moat_score=moat_score,
                distribution_warning=dist_warning,
            ))
        
        # 030 裁决（如果模块可用）
        arbitration_result = None
        if HAS_030_MODULES:
            try:
                arbitrator = SignalArbitrator()
                # 获取持仓信息
                held_pos = {}
                portfolio_path = os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")
                if os.path.exists(portfolio_path):
                    with open(portfolio_path) as f:
                        portfolio = json.load(f)
                    held_pos = portfolio.get("positions", {}).get(symbol, {})
                
                arbitration_result = arbitrator.arbitrate(
                    raw_signals=raw_signals,
                    fatal_risk=fatal_risk,
                    position_plan=position_plan,
                    market_context=market_context,
                    held_position=held_pos
                )
            except Exception as e:
                print(f"⚠️ 裁决引擎失败 {symbol}: {e}")

        # ── 2026-08-19 三因素共振门控：买入类裁决需通过 情绪+基本面 两维 ──
        _gate_blocked = None  # 若为 str 则该标的买入被拦截
        if arbitration_result is not None:
            try:
                from analysis.three_factor_helper import ResonanceGate  # 惰性 import 防循环
                _act = arbitration_result.final_action.value
                if _act in ("小仓买入", "买入", "积极买入"):
                    _ok, _info = ResonanceGate().check_buy(str(symbol))
                    if not _ok:
                        _gate_blocked = _info.get("reason") or ""
                        # 降级裁决：买入 → 持有/观望, 清仓位
                        from analysis.signal_arbitrator import SignalAction
                        arbitration_result.final_action = SignalAction.HOLD
                        arbitration_result.position_mult = 0.0
                        # B阶段 2026-11-01 决策留痕：此前门控拦截只改 action 不改 reason/path，
                        # 仲裁 JSON 里 reason 仍是「买入」→ 下游审计看不见拦截原因（10-28 类盲区）
                        _gate_tag = f"[三因素门控拦截]{_gate_blocked}"
                        arbitration_result.reason = ((arbitration_result.reason or "") + "；" + _gate_tag).strip("；")
                        arbitration_result.arbitration_path.append("RESONANCE_GATE_BLOCK")
                        if arbitration_result.decision_trace is not None:
                            arbitration_result.decision_trace.append(
                                {"stage": "RESONANCE_GATE_BLOCK", "gate_reason": _gate_blocked,
                                 "overridden_from": _act})
            except Exception as _e:
                # B阶段 2026-11-01：门控异常原为静默 fail-open（_gate_blocked=None 无痕）。
                # 维持 fail-open 语义（门控故障不拦买），但必须留痕可见（10-18 铁律：fail-open/fail-closed 显式选择并留痕）
                _gate_blocked = None
                print(f"  ⚠️ 三因素门控异常fail-open(不拦买) {symbol}: {_e}")
                arbitration_result.arbitration_path.append("RESONANCE_GATE_FAIL_OPEN")
                if arbitration_result.decision_trace is not None:
                    arbitration_result.decision_trace.append(
                        {"stage": "RESONANCE_GATE_FAIL_OPEN", "error": str(_e), "fail_open": True})

        # ── 2026-10-17 Regime×030 联动：ER 震荡态买入乘数 ×0.5 ──
        # 依据: 21,068 日事件研究——震荡态买入期望≈趋势态0.38×（三年一致
        # +1.33/+1.15/+0.17 vs +4.14/+1.70/+2.45%），胜率不变但右尾更小左尾更肥。
        # 只降乘数不禁买（震荡态买入仍为正超额 +0.67% vs 同态基线）。
        if (regime_er == "震荡" and arbitration_result is not None
                and "买入" in str(arbitration_result.final_action.value)
                and arbitration_result.position_mult and arbitration_result.position_mult > 0):
            arbitration_result.position_mult = round(arbitration_result.position_mult * 0.5, 2)
            _reg_tag = "[ER震荡态]买入乘数×0.5(震荡态买入期望≈趋势态0.4×,事件研究2026-10-17)"
            arbitration_result.reason = ((arbitration_result.reason or "") + "；" + _reg_tag).strip("；")
            try:
                arbitration_result.arbitration_path.append(_reg_tag)
            except Exception:
                pass

        results.append({
            "symbol": symbol, "name": name,
            "price": cur_price,
            "change_pct": change_pct,
            "strategies": sigs,
            "consensus": "一致" if (t1 == t2) else "分歧",
            "distribution_patterns": dist_patterns,
            "dist_max_strength": max_dist_strength,
            "dist_warning": dist_warning,
            "moat_score": moat_score,
            "moat_tags": moat_tags,
            "is_core_moat": is_core,
            # P1 政权信息
            "regime": regime_info,
            # 2026-10-17: ER20 趋势/震荡标志（已验证分离买入信号价值）
            "er20": er20,
            "regime_er": regime_er,
            "pre_chg_5d": pre_chg_5d,
            # 030 新增字段
            "arbitration": {
                "final_action": arbitration_result.final_action.value if arbitration_result else "未裁决",
                "confidence": arbitration_result.confidence if arbitration_result else 0.0,
                "position_mult": arbitration_result.position_mult if arbitration_result else 0.0,
                "stop_loss": arbitration_result.stop_loss if arbitration_result else None,
                "support_price": arbitration_result.support_price if arbitration_result else None,
                "reason": arbitration_result.reason if arbitration_result else "",
                "arbitration_path": arbitration_result.arbitration_path if arbitration_result else [],
                "decision_trace": arbitration_result.decision_trace if arbitration_result else [],
                "resonance_gate": _gate_blocked,  # 若为 str 表示买入被三因素门控拦截的原因（B阶段同步进仲裁 JSON）
            } if arbitration_result else None,
            "position_advice": {
                "max_amount": round(position_plan.get("single_stock_max_amount", 0) * 
                                  (arbitration_result.position_mult if arbitration_result else 1.0), 2),
                "direction_alloc": position_plan.get("direction_allocation", {}).get(
                    "半导体封测" if symbol in ["600584", "002156"] else 
                    "半导体晶圆" if symbol == "688981" else
                    "军工电子" if symbol == "002413" else
                    "工业自动化" if symbol == "300124" else
                    "高端液压" if symbol == "601100" else
                    "锂电池" if symbol == "300014" else
                    "锂资源" if symbol == "002466" else
                    "光伏玻璃" if symbol == "601865" else
                    "特种陶瓷" if symbol == "300285" else
                    "特材管材" if symbol == "603308" else
                    "特钢管" if symbol == "002318" else
                    "氟化工" if symbol == "600160" else
                    "炼化一体化" if symbol == "600346" else
                    "特钢" if symbol == "000708" else
                    "稀土永磁" if symbol == "300748" else
                    "数据中心" if symbol == "002335" else
                    "连接器" if symbol == "300719" else
                    "券商" if symbol in ["600030", "601066", "601995"] else
                    "银行" if symbol == "600036" else
                    "基建金融" if symbol == "000987" else
                    "金融IT" if symbol == "600570" else
                    "消费电子" if symbol == "605566" else
                    "汽车玻璃" if symbol == "600660" else
                    "工程机械" if symbol == "000157" else
                    "有色贸易" if symbol == "601061" else
                    "打印机国产替代" if symbol == "002180" else
                    "军工光电" if symbol == "300847" else
                    "其他", 0),
                "buy_signal_multiplier": position_plan.get("buy_signal_multiplier", 1.0),
            },
        })

    # 数据日期 = 众数 bar 日期（多数股票所属的最新交易日）；无则回退报告日期
    data_date = today
    if _bar_dates:
        from collections import Counter
        data_date = Counter(_bar_dates).most_common(1)[0][0]
    return {"date": today, "data_date": data_date,
            "stock_count": len(STOCKS), "stats": stats,
            "dual_strategy_map": dual_map, "strategy_usage": strat_usage,
            "details": results}


def format_brief(output):
    lines = []
    s = output["stats"]
    lines.append(f"📊 **双策略最优扫描** ({output['date']})")
    lines.append(f"覆盖 {output['stock_count']} 只 | 每股跑score前2策略")
    lines.append(f"🟢双买 {s['buy']} | 🔴双卖 {s['sell']} | ⚪双持有 {s['hold']} | ⚡分歧 {s['disagree']} | ❌失败 {s['error']}")
    lines.append("")

    # 一致性买/卖
    for title, tgt in [("🟢 **双策略一致买入**", "buy"), ("🔴 **双策略一致卖出**", "sell")]:
        rows = []
        for r in output["details"]:
            if "strategies" not in r or len(r["strategies"]) < 2:
                continue
            acts = [x.get("action", "") for x in r["strategies"]]
            a1, a2 = acts[0], acts[1]
            if ("买入" in a1) == ("买入" in a2) and tgt == "buy" and "买入" in a1:
                rows.append(r)
            elif ("卖出" in a1) == ("卖出" in a2) and tgt == "sell" and "卖出" in a1:
                rows.append(r)
        if rows:
            lines.append(title)
            lines.append("| 股票 | 策略1 | 策略2 | 现价 | 涨跌 |")
            lines.append("|:---|:---|:---|:---:|:---:|")
            for r in rows:
                lbl1 = r["strategies"][0].get("strategy_label", "-").replace("📊 ","").replace("🎯 ","").replace("📈 ","").replace("💹 ","").replace("📉 ","")
                lbl2 = r["strategies"][1].get("strategy_label", "-").replace("📊 ","").replace("🎯 ","").replace("📈 ","").replace("💹 ","").replace("📉 ","")
                price = r.get("price", 0) or 0
                lines.append(f"| {r['name']}({r['symbol']}) | {lbl1} | {lbl2} | ¥{price:.2f} | {r['change_pct']:+.2f}% |")
            lines.append("")

    # 分歧股
    rows = []
    n_dis_sell = n_dis_buy = n_dis_clash = 0
    for r in output["details"]:
        if "strategies" not in r or len(r["strategies"]) < 2:
            continue
        acts = [("买入" in x.get("action","")) for x in r["strategies"]]
        acts2 = [("卖出" in x.get("action","")) for x in r["strategies"]]
        if sum(acts) == 1 or sum(acts2) == 1:
            rows.append(r)
            # 2026-10-17 细分计数：事件研究显示分歧卖为三年稳定负超额信号（-0.23~-0.34%/5d），
            # 双卖符号不稳，故单列分歧卖供简报引用
            if sum(acts) == 1 and sum(acts2) == 1:
                n_dis_clash += 1
            elif sum(acts2) == 1:
                n_dis_sell += 1
            else:
                n_dis_buy += 1
    if rows:
        sub = f"（卖 {n_dis_sell} / 买 {n_dis_buy}" + (f" / 对撞 {n_dis_clash}" if n_dis_clash else "") + "）"
        lines.append(f"⚡ **双策略分歧**{sub}")
        lines.append("| 股票 | 策略1 | 策略2 | 现价 |")
        lines.append("|:---|:---|:---|:---:|")
        for r in rows:
            lbl1 = r["strategies"][0].get("strategy_label", "-").replace("📊 ","").replace("🎯 ","").replace("📈 ","").replace("💹 ","").replace("📉 ","")
            lbl2 = r["strategies"][1].get("strategy_label", "-").replace("📊 ","").replace("🎯 ","").replace("📈 ","").replace("💹 ","").replace("📉 ","")
            a1 = r["strategies"][0].get("action","").split(" ")[0]
            a2 = r["strategies"][1].get("action","").split(" ")[0]
            price = r.get("price", 0) or 0
            lines.append(f"| {r['name']}({r['symbol']}) | {lbl1} {a1} | {lbl2} {a2} | ¥{price:.2f} |")
        lines.append("")

    # 📋 全标的双策略信号总榜（覆盖所有关注股）
    all_rows = []
    for r in output["details"]:
        if "strategies" not in r or len(r["strategies"]) < 2 or "error" in r:
            continue
        acts = ["买入" in x.get("action", "") for x in r["strategies"]]
        sells = ["卖出" in x.get("action", "") for x in r["strategies"]]
        buy_n, sell_n = sum(acts), sum(sells)
        if buy_n == 2:
            tag = "🟢双买"
        elif sell_n == 2:
            tag = "🔴双卖"
        elif buy_n == 1 or sell_n == 1:
            tag = "⚡分歧"
        else:
            tag = "⚪双持有"
        all_rows.append((tag, r))
    if all_rows:
        # 排序: 双买/双卖 在前, 分歧次之, 双持有最后
        order = {"🟢双买": 0, "🔴双卖": 1, "⚡分歧": 2, "⚪双持有": 3}
        all_rows.sort(key=lambda x: order.get(x[0], 9))
        lines.append("📋 **全标的双策略信号总榜**（覆盖 " + str(len(all_rows)) + " 只）")
        lines.append("| 股票 | 状态 | 策略1 | 策略2 | 现价 | 涨跌 | ER状态 | 前置5日 |")
        lines.append("|:---|:---:|:---|:---|:---:|:---:|:---:|:---:|")
        for tag, r in all_rows:
            lbl1 = r["strategies"][0].get("strategy_label", "-").replace("📊 ","").replace("🎯 ","").replace("📈 ","").replace("💹 ","").replace("📉 ","")
            lbl2 = r["strategies"][1].get("strategy_label", "-").replace("📊 ","").replace("🎯 ","").replace("📈 ","").replace("💹 ","").replace("📉 ","")
            a1 = r["strategies"][0].get("action", "").split(" ")[0]
            a2 = r["strategies"][1].get("action", "").split(" ")[0]
            price = r.get("price", 0) or 0
            cp = r.get("change_pct") or 0
            reg = r.get("regime_er") or "-"
            _pre = r.get("pre_chg_5d")
            pre_s = f"{_pre:+.2f}%" if _pre is not None else "—"
            lines.append(f"| {r['name']}({r['symbol']}) | {tag} | {lbl1} {a1} | {lbl2} {a2} | ¥{price:.2f} | {cp:+.2f}% | {reg} | {pre_s} |")
        lines.append("")

    # 策略使用分布
    lines.append("📈 **双策略使用分布**（每股用的前2策略累计）")
    for sk, sv in output["strategy_usage"].items():
        if sv > 0:
            lines.append(f"  {STRATEGY_LABELS[sk]}: {sv}次")
    lines.append("")

    # 🏰 护城河因子分布
    core_stocks = [r for r in output["details"] if r.get("is_core_moat")]
    sat_stocks = [r for r in output["details"] if not r.get("is_core_moat") and r.get("moat_score", 0) > 0]
    no_moat = [r for r in output["details"] if r.get("moat_score", 0) == 0]
    lines.append("🏰 **护城河因子分布**")
    lines.append(f"  核心仓(≥2.0): {len(core_stocks)} 只")
    lines.append(f"  卫星仓(>0<2.0): {len(sat_stocks)} 只")
    lines.append(f"  无护城河: {len(no_moat)} 只")
    if core_stocks:
        lines.append("  核心标的: " + ", ".join([f"{r['name']}({r['symbol']})[{r['moat_score']:.1f}]" for r in core_stocks[:10]]))
        if len(core_stocks) > 10:
            lines.append(f"    ... 及其他 {len(core_stocks)-10} 只")
    lines.append("")

    # ⚠️ 出货形态预警（强度>=4且有买入信号）
    dist_rows = []
    for r in output["details"]:
        if r.get("dist_warning"):
            dist_rows.append(r)
    if dist_rows:
        lines.append("⚠️ **出货形态预警**（买入信号被否决/降级）")
        lines.append("| 股票 | 形态 | 强度 | 现价 | 涨跌 |")
        lines.append("|:---|:---|:---:|:---:|:---:|")
        for r in dist_rows:
            patterns = ", ".join([p["pattern"] for p in r.get("distribution_patterns", []) if p["strength"] >= 4])
            price = r.get("price", 0) or 0
            lines.append(f"| {r['name']}({r['symbol']}) | {patterns} | {r['dist_max_strength']} | ¥{price:.2f} | {r['change_pct']:+.2f}% |")
        lines.append("")
    return "\n".join(lines)




def main():
    print("📡 开始双策略最优扫描（每股跑score前2策略）...")
    output = scan_all()

    # 保存裁决结果供后续模块使用
    today = datetime.now().strftime("%Y-%m-%d")
    arbitration_results = []
    for r in output["details"]:
        if r.get("arbitration"):
            arb = r["arbitration"]
            arbitration_results.append({
                "symbol": r["symbol"],
                "name": r["name"],
                "final_action": arb["final_action"],
                "confidence": arb["confidence"],
                "position_mult": arb["position_mult"],
                "stop_loss": arb["stop_loss"],
                "support_price": arb["support_price"],
                "reason": arb["reason"],
                "arbitration_path": arb["arbitration_path"],
                # B阶段 2026-11-01：门控拦截原因 + 决策轨迹原只留在 details 层，仲裁 JSON 丢失 → 补齐
                # （此前 execution_audit/next_day_sim 读到的 reason 在门控拦截时仍是「买入」）
                "resonance_gate": arb.get("resonance_gate"),
                "decision_trace": arb.get("decision_trace"),
            })
    
    # 保存裁决结果
    arb_path = os.path.join(WORKSPACE, "data", f"arbitration_{today}.json")
    import shutil
    if os.path.exists(arb_path):
        shutil.copy(arb_path, arb_path + '.bak')
    with open(arb_path, "w") as f:
        json.dump(arbitration_results, f, ensure_ascii=False, indent=2, default=str)
    
    # 生成隔日推演
    if HAS_030_MODULES and arbitration_results:
        try:
            # 加载持仓
            held_positions = {}
            portfolio_path = os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")
            if os.path.exists(portfolio_path):
                with open(portfolio_path) as f:
                    portfolio = json.load(f)
                held_positions = portfolio.get("positions", {})
            
            # 加载扫描结果
            scan_results = output
            
            # 加载市场上下文和仓位计划
            from analysis.position_sizer import load_market_context
            market_context = load_market_context()
            
            plan_path = os.path.join(WORKSPACE, "data", f"position_plan_{today}.json")
            position_plan = {}
            if os.path.exists(plan_path):
                with open(plan_path) as f:
                    position_plan = json.load(f)
            if not position_plan:
                # [2026-10-17] 修复 0% 口径分叉:写盘链路 09-08 起停更,旧逻辑文件缺失时
                # position_plan 停留 {} → next_day_sim 渲染全 0(与 portfolio_sim 25% 矛盾)。
                # 改为现算(与 portfolio_sim/portfolio_core 同源,SSOT=position_sizer.compute_plan_now)。
                try:
                    from analysis.position_sizer import compute_plan_now
                    position_plan = compute_plan_now(held_positions=held_positions)
                except Exception as _e:
                    print(f"⚠️ position_plan 现算失败,退静态兑底(50%): {_e}")
                    position_plan = {"buy_signal_multiplier": 1.0, "single_stock_max_pct": 0.05,
                                     "total_limit_pct": 0.5, "total_limit_amount": 1_500_000}
            
            simulator = NextDaySimulator()
            simulation = simulator.generate(
                market_context=market_context,
                position_plan=position_plan,
                arbitration_results=arbitration_results,
                held_positions=held_positions,
                scan_results=scan_results,
            )
            
            # 保存推演结果
            from analysis.next_day_simulator import save_simulation
            json_path, md_path = save_simulation(simulation)
            print(f"📄 隔日推演已保存: {md_path}")
        except Exception as e:
            print(f"⚠️ 隔日推演生成失败: {e}")
    
    print("\n=====DUAL_SCAN_RESULT=====")
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    print("=====DUAL_SCAN_END=====")

    print("\n" + format_brief(output))

    report_path = os.path.join(OUTPUT_DIR, f"{today}_dual.md")
    data_date = output.get("data_date", today)
    import shutil
    if os.path.exists(report_path):
        shutil.copy(report_path, report_path + '.bak')
    with open(report_path, "w") as f:
        f.write(f"# 双策略最优扫描日报 {today}\n\n")
        # 🔴 数据新鲜度标注（供 scripts/data_freshness_guard.py 门禁校验）
        f.write(f"数据日期: {data_date}\n\n")
        if data_date < today:
            f.write(f"> ⚠️ **数据滞后**：日K 最新 bar = {data_date}，"
                    f"以下「现价」为 {data_date} 收盘价，**非当日行情**。\n\n")
        f.write(format_brief(output))
        # 每只股票的详细信号
        f.write("\n---\n\n## 逐股双策略信号详情\n")
        for r in output["details"]:
            if "strategies" not in r:
                f.write(f"\n### {r['name']}({r['symbol']}) — {r.get('error','')}\n")
                continue
            f.write(f"\n### {r['name']}({r['symbol']}) 现价¥{(r.get('price') or 0):.2f} ({r.get('change_pct') or 0:+.2f}%) [{r['consensus']}]\n")
            # 2026-10-17: ER20 状态行 + 复活 P1 政权检测展示（原计算后即弃）
            _reg_parts = []
            if r.get("regime_er"):
                _reg_parts.append(f"状态={r['regime_er']}(ER20={r.get('er20')})" if r.get("er20") is not None else f"状态={r['regime_er']}")
            _rg = r.get("regime") or {}
            if _rg.get("regime"):
                _reg_parts.append(f"政权={_rg['regime']}(ADX {_rg.get('adx', 0):.1f}, CHOP {_rg.get('chop', 0):.1f})")
            if _rg.get("avoid_trade"):
                _reg_parts.append("⚠️建议回避")
            if _reg_parts:
                f.write("- **🌊 状态**: " + " | ".join(_reg_parts) + "\n")
            for sg in r["strategies"]:
                score = sg.get("validation_score", "?")
                f.write(f"- **{sg.get('strategy_label')}** (score={score}): {sg.get('action')} — {sg.get('reason')}\n")
            # 030 裁决结果
            if r.get("arbitration"):
                arb = r["arbitration"]
                f.write(f"\n**📋 030裁决**: {arb['final_action']} (置信度{arb['confidence']:.0%}, 仓位乘数{arb['position_mult']:.1f}x)\n")
                f.write(f"理由: {arb['reason']}\n")
                if arb["stop_loss"]:
                    f.write(f"止损: ¥{arb['stop_loss']} | 支撑: ¥{arb['support_price']}\n")
            # 仓位建议
            if r.get("position_advice"):
                pa = r["position_advice"]
                f.write(f"\n**💰 仓位建议**: 最大¥{pa['max_amount']:,.0f} | 方向额度¥{pa['direction_alloc']:,.0f} | 买入乘数{pa['buy_signal_multiplier']:.1f}x\n")
    print(f"📄 日报已保存: {report_path}")


if __name__ == "__main__":
    main()
