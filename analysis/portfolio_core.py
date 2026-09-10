#!/usr/bin/env python3
"""
Portfolio Core - 可复用的组合核心逻辑
========================================
供 portfolio_sim.py（模拟盘）、实盘执行器、回测引擎共同调用，
保证「模拟/实盘/回测」行为 100% 一致。

核心能力：
1. 策略切换强平保护
2. 重复 BUY 信号钝化
3. 030 风控信号覆盖（致命/高风险）
4. ATR 止损价计算
5. 成交流水记录（含斐波那契止盈目标）
6. 哑铃策略仓位分类（核心/卫星）
7. 030 仓位管理集成（买入乘数、单股上限、总仓位上限）
"""

import os
import json
import warnings
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

import pandas as pd
import pandas_ta as ta
import numpy as np

warnings.filterwarnings("ignore")

WORKSPACE = "/Users/duguke/.openclaw/workspace"
STATE_FILE = os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")
TRADES_FILE = os.path.join(WORKSPACE, "data", "portfolio_sim_trades.json")
STRATEGY_MAP_FILE = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")
EQUITY_FILE = os.path.join(WORKSPACE, "data", "portfolio_equity.csv")

# ═══════════════════════════════════════════
# 常量与配置
# ═══════════════════════════════════════════
COMMISSION = 0.0003
SLIPPAGE = 0.001
INITIAL_CAPITAL = 100_000
ATR_STOP_MULT = 2.0

STRATEGY_LABELS = {
    "bollinger": "📊 布林带+ATR",
    "kdj_cci": "🎯 KDJ+RSI(均值回归)",
    "ema_obv": "📈 EMA+OBV",
    "ema_cross": "💹 EMA12/26",
    "macd": "📉 纯MACD",
    "bull_trend": "🐂 牛市趋势跟踪",
}

# ── 39只关注股（同步 memory/watchlist.md + backtest_strategies.py + 电力设备出海观察池）──
STOCKS = [
    # 证券/金融 (5)
    ("600030","中信证券"), ("601066","中信建投"), ("600036","招商银行"),
    ("601995","中金公司"), ("000987","越秀资本"),
    # 半导体/TMT (4)
    ("600584","长电科技"), ("688981","中芯国际"), ("002156","通富微电"), ("002413","雷科防务"),
    # 新能源/储能 (2)
    ("300014","亿纬锂能"), ("002466","天齐锂业"),
    # 高端制造/材料 (8)
    ("300285","国瓷材料"), ("603308","应流股份"), ("300124","汇川技术"), ("601100","恒立液压"),
    ("002318","久立特材"), ("300719","安达维尔"), ("002335","科华数据"), ("300748","金力永磁"),
    # 化工 (2)
    ("600160","巨化股份"), ("600346","恒力石化"),
    # 钢铁/特钢 (1)
    ("000708","中信特钢"),
    # 消费/其他 (5)
    ("600660","福耀玻璃"), ("600570","恒生电子"), ("605566","福莱蒽特"), ("000157","中联重科"), ("601061","中信金属"),
    # 电网装备/特高压 (8) —— 2026-08-29 新增
    ("600089","特变电工"), ("600406","国电南瑞"), ("000400","许继电气"), ("601179","中国西电"),
    ("002028","思源电气"), ("002270","华明装备"), ("002130","沃尔核材"), ("600312","平高电气"),
    # 电力设备/特高压出海 (8) —— 2026-09-07 新增观察池优先 (与上同组但标记独立方向)
    # 已包含在电网装备中，此处不重复，方向在 classify_bucket/STOCK_DIRECTION_MAP 区分
]

# 030 模块（可选导入）
try:
    from analysis.fatal_risk_detector import FatalRiskDetector
    from analysis.position_sizer import PositionSizer, load_market_context as load_position_context
    from analysis.signal_arbitrator import SignalArbitrator, RawSignal, SignalAction
    from analysis.moat_factor import calc_moat_score, is_core_moat_stock
    HAS_030_MODULES = True
except ImportError:
    HAS_030_MODULES = False

import urllib.request


def fetch_today_realtime(symbols):
    """新浪实时行情批量获取"""
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
            marker = f'hq_str_{prefix}{s}="'
            start = text.find(marker)
            if start < 0:
                continue
            start += len(marker)
            end = text.find("\"", start)
            if end < 0:
                continue
            fields = text[start:end].split(",")
            if len(fields) < 9:
                continue
            try:
                cur = float(fields[3])
                oh = float(fields[1])
                hi = float(fields[4]) if fields[4] else cur
                lo = float(fields[5]) if fields[5] else cur
                vol = int(fields[8]) if fields[8] else 0
                result[s] = {"date": datetime.now().strftime("%Y-%m-%d"),
                             "open": oh, "close": cur, "high": hi, "low": lo, "volume": vol}
            except (ValueError, IndexError):
                pass
        return result
    except Exception:
        return {}
    HAS_030_MODULES = False


# ═══════════════════════════════════════════
# 状态管理
# ═══════════════════════════════════════════
def load_state() -> Dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
        return state
    return {"last_signal_date": None, "positions": {}}


def save_state(state: Dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)


def load_strategy_map() -> Dict[str, str]:
    if os.path.exists(STRATEGY_MAP_FILE):
        with open(STRATEGY_MAP_FILE) as f:
            return json.load(f)
    return {}


def append_trade(trade: Dict):
    """追加成交流水"""
    log = []
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE) as f:
                log = json.load(f)
        except Exception:
            log = []
    log.append(trade)
    os.makedirs(os.path.dirname(TRADES_FILE), exist_ok=True)
    with open(TRADES_FILE, "w") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def append_equity_snapshot(date: str, portfolio_summary: Dict, core_value: float, sat_value: float, cash_total: float):
    """记录每日组合权益快照
    CSV 列: date, total_value, total_return_pct, core_value, satellite_value, cash_total, positions_held, total_initial
    """
    import csv
    file_exists = os.path.exists(EQUITY_FILE)
    
    row = {
        "date": date,
        "total_value": portfolio_summary.get("total_value", 0),
        "total_return_pct": portfolio_summary.get("total_return_pct", 0),
        "core_value": core_value,
        "satellite_value": sat_value,
        "cash_total": cash_total,
        "positions_held": portfolio_summary.get("positions_held", 0),
        "total_initial": portfolio_summary.get("total_initial", 0),
    }
    
    with open(EQUITY_FILE, "a", newline='') as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def init_position(symbol: str, name: str, strategy: str) -> Dict:
    """初始化单只股票持仓结构"""
    return {
        "name": name,
        "strategy": strategy,
        "position": False,
        "entry_price": 0.0,
        "entry_date": None,
        "shares": 0,
        "cash": INITIAL_CAPITAL,
        "total_pl": 0.0,
        "trade_count": 0,
        "_symbol": symbol,
        "_buy_streak": 0,
        "_last_action": None,
        "_last_action_date": None,
        "_strategy_switch_pending": False,
        "_old_strategy": None,
        "_new_strategy": None,
        "fib_targets": {},
        "bucket": "unknown",
        "atr_params": {"stop_mult": ATR_STOP_MULT, "risk_per_trade": 0.01, "max_position_pct": 0.15},
    }


def sync_strategy_map(state: Dict, strategy_map: Dict, default_strategy: str = "ema_cross"):
    """同步策略映射到持仓，检测切换 → 标记强平"""
    for sym, pos in state["positions"].items():
        new_strat = strategy_map.get(sym, default_strategy)
        old_strat = pos.get("strategy", new_strat)
        pos["strategy"] = new_strat
        if pos.get("position") and old_strat != new_strat:
            pos["_strategy_switch_pending"] = True
            pos["_old_strategy"] = old_strat
            pos["_new_strategy"] = new_strat
            print(f"⚠️ 策略切换检测: {sym} {old_strat} -> {new_strat}，持仓中，标记强制平仓")


# ═══════════════════════════════════════════
# 斐波那契扩展止盈计算
# ═══════════════════════════════════════════
def compute_fib_targets(df: pd.DataFrame, lookback: int = 150) -> Dict:
    """计算斐波那契扩展位止盈目标 (1.0/1.272/1.618/2.618)
    基于最近上升波段: swing_low -> swing_high -> 回调低点
    返回: {ratio: price, ...} 或 {}"""
    if df is None or len(df) < 30:
        return {}
    d = df.tail(lookback).reset_index(drop=True)
    closes = d["close"].values
    highs = d["high"].values
    lows = d["low"].values
    n = len(d)
    cur = float(closes[-1])

    def is_low(idx):
        lo, lg = max(0, idx - 4), min(n, idx + 5)
        return lows[idx] == min(lows[lo:lg]) and lows[idx] <= closes[idx]

    def is_high(idx):
        lo, lg = max(0, idx - 4), min(n, idx + 5)
        return highs[idx] == max(highs[lo:lg])

    # 从最近往回找显著 swing 高点
    candidate_hi = None
    for i in range(n - 2, max(0, n - 90) - 1, -1):
        if is_high(i) and highs[i] > cur:
            candidate_hi = i
            break
    if candidate_hi is None:
        for i in range(n - 2, max(0, n - 90) - 1, -1):
            if is_high(i):
                candidate_hi = i
                break
    if candidate_hi is None:
        return {}
    swing_high = float(highs[candidate_hi])

    # 从该高点往回找波段起点低点
    swing_low_i, swing_low = None, None
    for i in range(candidate_hi - 1, max(0, candidate_hi - 70) - 1, -1):
        if is_low(i):
            swing_low_i, swing_low = i, lows[i]
            break
    if swing_low_i is None:
        swing_low_i, swing_low = 0, float(min(lows[:candidate_hi]))

    run_pct = swing_high / swing_low - 1 if swing_low > 0 else 0
    if run_pct < 0.05:  # 波段涨幅<5% 不算有效
        return {}

    # 当前回调低点（swing_high 之后的盘中最低）
    after = lows[candidate_hi:]
    pullback_low = float(min(after))

    # 有效性护栏：当前价不能深度跌破波段起点（>5%）
    if cur < swing_low * 0.95:
        return {}

    # 计算扩展位
    base = pullback_low
    amp = swing_high - swing_low
    targets = {}
    for ratio in (1.0, 1.272, 1.618, 2.618):
        targets[str(ratio)] = round(base + ratio * amp, 2)
    return targets


def _compute_atr_stop(price: float, atr_val: float) -> float:
    return price - ATR_STOP_MULT * atr_val if atr_val > 0 else 0.0


def signal_bollinger_atr(df: pd.DataFrame) -> Dict:
    bb = ta.bbands(df["close"], length=20, std=2)
    bbu = bb[[c for c in bb.columns if "BBU" in c.upper()][0]]
    bbm = bb[[c for c in bb.columns if "BBM" in c.upper()][0]]
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    atr_val = float(atr.iloc[-1])
    action, reason = "持有", "无信号"
    if float(prev["close"]) <= float(bbu.iloc[-2]) and float(row["close"]) > float(bbu.iloc[-1]):
        action, reason = "买入", f"布林上轨突破(¥{float(bbu.iloc[-1]):.2f}) ATR={atr_val:.2f} 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif float(prev["close"]) >= float(bbm.iloc[-2]) and float(row["close"]) < float(bbm.iloc[-1]):
        action, reason = "卖出", f"跌破中轨(¥{float(bbm.iloc[-1]):.2f})"
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"上轨{float(bbu.iloc[-1]):.2f} 中轨{float(bbm.iloc[-1]):.2f} ATR{atr_val:.2f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def signal_kdj_cci(df: pd.DataFrame) -> Dict:
    cci = ta.cci(df["high"], df["low"], df["close"], length=14)
    cci_pct = cci.rolling(60).apply(lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-10) * 100)
    kdj = ta.kdj(df["high"], df["low"], df["close"], length=9, signal=3)
    k, d, j = kdj["K_9_3"], kdj["D_9_3"], kdj["J_9_3"]
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    cpi = float(cci_pct.iloc[-1]) if pd.notna(cci_pct.iloc[-1]) else 50
    j_cross_up = float(j.iloc[-2]) <= float(k.iloc[-2]) and float(j.iloc[-1]) > float(k.iloc[-1])
    j_cross_down = float(j.iloc[-2]) >= float(k.iloc[-2]) and float(j.iloc[-1]) < float(k.iloc[-1])
    action, reason = "持有", "无信号"
    if cpi < 20 and j_cross_up:
        action, reason = "买入", f"CCI低位({cpi:.0f}%)+KDJ金叉 K={float(k.iloc[-1]):.1f} 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif cpi > 80 or j_cross_down:
        reasons = []
        if cpi > 80: reasons.append(f"CCI高位({cpi:.0f}%)")
        if j_cross_down: reasons.append("KDJ死叉")
        action, reason = "卖出", " | ".join(reasons)
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"CCI_pct={cpi:.0f}% K={float(k.iloc[-1]):.1f} J={float(j.iloc[-1]):.1f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def signal_ema_obv(df: pd.DataFrame) -> Dict:
    ema20 = ta.ema(df["close"], length=20)
    obv = ta.obv(df["close"], df["volume"])
    obv_ema20 = ta.ema(obv, length=20)
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    e20 = float(ema20.iloc[-1])
    obv_now, obv_ema = float(obv.iloc[-1]), float(obv_ema20.iloc[-1])
    obv_cross_up = float(obv.iloc[-2]) <= float(obv_ema20.iloc[-2]) and obv_now > obv_ema
    obv_cross_down = float(obv.iloc[-2]) >= float(obv_ema20.iloc[-2]) and obv_now < obv_ema
    action, reason = "持有", "无信号"
    if price > e20 and obv_cross_up:
        action, reason = "买入", f"EMA20上方(¥{e20:.2f}) + OBV上穿均线(量能放大) 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif price < e20 or obv_cross_down:
        reasons = []
        if price < e20: reasons.append(f"跌破EMA20(¥{e20:.2f})")
        if obv_cross_down: reasons.append("OBV下穿均线(资金撤离)")
        action, reason = "卖出", " | ".join(reasons)
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"EMA20={e20:.2f} OBV/EMA={obv_now/obv_ema:.2f}x" if obv_ema > 0 else f"EMA20={e20:.2f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def signal_ema_cross(df: pd.DataFrame) -> Dict:
    ema12 = ta.ema(df["close"], length=12)
    ema26 = ta.ema(df["close"], length=26)
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    e12, e26 = float(ema12.iloc[-1]), float(ema26.iloc[-1])
    pe12, pe26 = float(ema12.iloc[-2]), float(ema26.iloc[-2])
    action, reason = "持有", "无信号"
    if pe12 <= pe26 and e12 > e26:
        action, reason = "买入", f"EMA12({e12:.2f})金叉EMA26({e26:.2f}) 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif pe12 >= pe26 and e12 < e26:
        action, reason = "卖出", f"EMA12({e12:.2f})死叉EMA26({e26:.2f})"
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"EMA12={e12:.2f} EMA26={e26:.2f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def signal_macd(df: pd.DataFrame) -> Dict:
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    macd_line = macd_df["MACD_12_26_9"]
    signal_line = macd_df["MACDs_12_26_9"]
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    m, s = float(macd_line.iloc[-1]), float(signal_line.iloc[-1])
    pm, ps = float(macd_line.iloc[-2]), float(signal_line.iloc[-2])
    action, reason = "持有", "无信号"
    if pm <= ps and m > s:
        action, reason = "买入", f"MACD金叉({m:.4f}/{s:.4f}) 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif pm >= ps and m < s:
        action, reason = "卖出", f"MACD死叉({m:.4f}/{s:.4f})"
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"MACD={m-s:.4f} Signal={s:.4f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def signal_bull_trend(df: pd.DataFrame) -> Dict:
    ema10 = ta.ema(df["close"], length=10)
    ema20 = ta.ema(df["close"], length=20)
    ema60 = ta.ema(df["close"], length=60)
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    e10, e20, e60 = float(ema10.iloc[-1]), float(ema20.iloc[-1]), float(ema60.iloc[-1])
    pe10, pe20, pe60 = float(ema10.iloc[-2]), float(ema20.iloc[-2]), float(ema60.iloc[-2])
    action, reason = "持有", "无信号"
    # 牛市趋势跟踪：EMA20 > EMA60 且价格 > EMA20 为多头排列
    if pe20 <= pe60 and e20 > e60 and price > e20:
        action, reason = "买入", f"牛市趋势确立: EMA20({e20:.2f})金叉EMA60({e60:.2f}) 价格在EMA20上方 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif pe20 >= pe60 and e20 < e60:
        action, reason = "卖出", f"趋势破坏: EMA20({e20:.2f})死叉EMA60({e60:.2f})"
    elif price < e60:
        action, reason = "卖出", f"价格跌破EMA60({e60:.2f}) 长期趋势支撑失效"
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"EMA10={e10:.2f} EMA20={e20:.2f} EMA60={e60:.2f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


SIGNAL_FUNCS = {
    "bollinger": signal_bollinger_atr,
    "kdj_cci": signal_kdj_cci,
    "ema_obv": signal_ema_obv,
    "ema_cross": signal_ema_cross,
    "macd": signal_macd,
    "bull_trend": signal_bull_trend,
}


# ═══════════════════════════════════════════
# 030 风控预检（单例模式，避免重复登录）
# ═══════════════════════════════════════════
class RiskGuard:
    _instance = None
    _fatal_risk = {"fatal_triggered": False, "high_risk_triggered": False}
    _position_plan = {"buy_signal_multiplier": 1.0, "single_stock_max_pct": 0.05,
                      "total_limit_pct": 0.5, "total_limit_amount": 1_500_000,
                      "direction_allocation": {}}
    _market_context = {"health_score": 5, "market_stage": "震荡筑底", "emotion_cycle": "修复"}

    @classmethod
    def refresh(cls):
        if not HAS_030_MODULES:
            return
        try:
            fatal_detector = FatalRiskDetector()
            cls._fatal_risk = fatal_detector.check()
            pos_context = load_position_context()
            sizer = PositionSizer(total_capital=3_000_000)
            pos_plan_obj = sizer.calculate(
                market_stage=pos_context["market_stage"],
                emotion_cycle=pos_context["emotion_cycle"],
                health_score=pos_context["health_score"],
                fatal_risk=cls._fatal_risk,
                held_positions=pos_context.get("held_positions"),
                watchlist=list(pos_context.get("watchlist", []))
            )
            cls._position_plan = {
                "buy_signal_multiplier": pos_plan_obj.buy_signal_multiplier,
                "single_stock_max_pct": pos_plan_obj.single_stock_max_pct,
                "single_stock_max_amount": pos_plan_obj.single_stock_max_amount,
                "total_limit_pct": pos_plan_obj.total_limit_pct,
                "total_limit_amount": pos_plan_obj.total_limit_amount,
                "direction_allocation": pos_plan_obj.direction_allocation,
                "risk_warnings": pos_plan_obj.risk_warnings,
            }
            cls._market_context = pos_context
        except Exception as e:
            print(f"⚠️ 030风控刷新失败: {e}")

    @classmethod
    def get_fatal_risk(cls) -> Dict:
        return cls._fatal_risk

    @classmethod
    def get_position_plan(cls) -> Dict:
        return cls._position_plan

    @classmethod
    def get_market_context(cls) -> Dict:
        return cls._market_context

    @classmethod
    def override_signal(cls, signal: Dict, pos: Dict) -> Dict:
        """根据风控状态强制覆盖信号"""
        fatal = cls._fatal_risk
        if fatal.get("fatal_triggered", False):
            if pos.get("position"):
                signal["action"] = "卖出"
                signal["reason"] = "☠️ 030致命风险触发：强制清仓"
            else:
                signal["action"] = "持有"
                signal["reason"] = "☠️ 030致命风险触发：禁止买入"
        elif fatal.get("high_risk_triggered", False):
            if pos.get("position"):
                signal["action"] = "卖出"
                signal["reason"] = "🟡 030高风险防御：核心中军≤-3%，减仓止损"
            else:
                signal["action"] = "持有"
                signal["reason"] = "🟡 030高风险防御：不开新仓"
        return signal

    @classmethod
    def check_strategy_switch(cls, signal: Dict, pos: Dict) -> Dict:
        """策略切换强平"""
        if pos.get("_strategy_switch_pending") and pos.get("position"):
            signal["action"] = "卖出"
            old_s = pos.get("_old_strategy", "unknown")
            new_s = pos.get("_new_strategy", "unknown")
            signal["reason"] = f"🔄 策略切换强制平仓: {old_s} -> {new_s}"
            pos.pop("_strategy_switch_pending", None)
            pos.pop("_old_strategy", None)
            pos.pop("_new_strategy", None)
        return signal

    @classmethod
    def apply_buy_streak_dampening(cls, signal: Dict, pos: Dict) -> Tuple[Dict, bool]:
        """
        重复 BUY 钝化（增强版）：
        1. 连续买入信号天数限制（默认最多连续2天执行，第3天起钝化）
        2. ATR波动率动态调整：高波动股票钝化更强
        3. 连续买入后若价格下跌，自动重置钝化计数（防止追高）
        """
        if signal["action"] == "买入":
            streak = pos.get("_buy_streak", 0) + 1
            pos["_buy_streak"] = streak
            
            # 获取ATR波动率用于动态钝化
            atr_val = signal.get("atr", 0)
            price = signal.get("price", 0)
            atr_pct = (atr_val / price * 100) if price > 0 else 0
            
            # 波动率调整钝化阈值
            # 高波动(ATR%>3): 仅允许连续1天买入
            # 中波动(ATR% 1.5-3): 允许连续2天
            # 低波动(ATR%<1.5): 允许连续3天
            if atr_pct > 3.0:
                max_streak = 1
            elif atr_pct > 1.5:
                max_streak = 2
            else:
                max_streak = 3
            
            # 检查是否需要钝化
            if streak > max_streak:
                # 记录钝化原因到信号
                signal["reason"] = f"{signal.get('reason', '')} | 钝化: 连续{streak}天买入信号(波动率ATR%={atr_pct:.1f}%, 限制{max_streak}天)"
                return signal, False  # 钝化：不执行
            
            # 连续买入后若价格跌破入场价，重置钝化计数（防追高保护）
            entry_price = pos.get("entry_price", 0)
            if entry_price > 0 and price < entry_price * 0.98:  # 跌破入场价2%
                pos["_buy_streak"] = 0  # 重置，允许新一轮买入
        else:
            pos["_buy_streak"] = 0
        return signal, True

    @classmethod
    def check_dedup(cls, signal: Dict, pos: Dict, dt: str) -> bool:
        """去重：同一日同方向不重复执行"""
        last_action = pos.get("_last_action")
        last_date = pos.get("_last_action_date")
        if last_action == signal["action"] and last_date == dt:
            return False
        return True


# ═══════════════════════════════════════════
# 仓位分类（哑铃策略）
# ═══════════════════════════════════════════
def classify_bucket(symbol: str) -> Tuple[str, float, float]:
    """返回 (bucket, max_single_pct, max_total_pct)"""
    # 电力设备/特高压出海板块 - 独立方向，核心仓优先
    POWER_OVERSEAS = {
        "600089", "600406", "000400", "601179",
        "600312", "002028", "002270", "002130"
    }
    if symbol in POWER_OVERSEAS:
        return "core", 0.15, 0.80
    if HAS_030_MODULES:
        try:
            moat_score, _ = calc_moat_score(symbol)
            is_core = is_core_moat_stock(symbol)
            if is_core:
                return "core", 0.15, 0.80
        except Exception:
            pass
    return "satellite", 0.05, 0.20


# ═══════════════════════════════════════════
# 交易执行器
# ═══════════════════════════════════════════
def execute_trade(pos: Dict, signal: Dict, dt: str, strategy: str,
                  position_plan: Dict) -> Tuple[Optional[str], Optional[str]]:
    """
    执行买卖，返回 (action_type, message)
    action_type: "BUY" | "SELL" | None
    """
    price = signal["price"]
    action = signal["action"]
    bucket = pos.get("bucket", "satellite")
    max_single_pct = 0.15 if bucket == "core" else 0.05
    single_cap = INITIAL_CAPITAL * max_single_pct

    # 030 仓位限制：动态调整可用资金
    if action == "买入" and not pos["position"]:
        max_amount = position_plan.get("single_stock_max_amount", 100_000) * position_plan.get("buy_signal_multiplier", 1.0)
        available_cash = min(pos["cash"], max_amount, single_cap)
    else:
        available_cash = pos["cash"]

    if action == "买入" and not pos["position"]:
        # 三因素共振门控（可选）
        try:
            from analysis.three_factor_helper import ResonanceGate
            ok, info = ResonanceGate().check_buy(str(pos["_symbol"]))
            if not ok:
                return "HOLD", f"⛔ **{pos['name']}({dt}) 买入被三因素门控拦截**\n{info.get('reason','')}\n技术信号: {signal['reason']}"
        except Exception:
            pass

        fee = available_cash * COMMISSION
        cash_after_fee = available_cash - fee
        max_shares = int(single_cap / (price * (1 + SLIPPAGE)) / 100) * 100
        shares = int(min(cash_after_fee, single_cap) / (price * (1 + SLIPPAGE)) / 100) * 100

        if shares >= 100:
            cost = shares * price * (1 + SLIPPAGE)
            fee2 = cost * COMMISSION
            pos["cash"] -= (cost + fee2)
            pos["shares"] = shares
            pos["position"] = True
            pos["entry_price"] = price
            pos["entry_date"] = dt
            pos["trade_count"] += 1
            pos["bucket"] = bucket
            pos["_last_action"] = action
            pos["_last_action_date"] = dt
            pos["_buy_streak"] = 1
            pos["fib_targets"] = signal.get("fib_tp", {}).get("targets", {})
            append_trade({"date": dt, "symbol": pos["_symbol"], "name": pos["name"],
                          "action": "BUY", "price": price, "shares": shares,
                          "pnl": 0, "pnl_pct": 0.0, "strategy": strategy, "bucket": bucket})
            bucket_emoji = "🏰" if bucket == "core" else "🛰️"
            fib_str = ""
            if pos["fib_targets"]:
                fib_str = f" | 止盈目标: " + " / ".join([f"{k}¥{v:.2f}" for k,v in pos["fib_targets"].items()])
            return "BUY", f"{bucket_emoji} **{pos['name']}({dt}) 买入**\n价格 ¥{price:.2f} | {shares}股({bucket}) | 理由: {signal['reason']}{fib_str}"

    elif action == "卖出" and pos["position"]:
        sell_value = pos["shares"] * price * (1 - SLIPPAGE)
        fee = sell_value * COMMISSION
        net = sell_value - fee
        cost_basis = pos["shares"] * pos["entry_price"] * (1 + SLIPPAGE)
        pnl = net - cost_basis
        pos["cash"] += net
        pos["total_pl"] += pnl
        pos["shares"] = 0
        pos["position"] = False
        pos["trade_count"] += 1
        pos["_buy_streak"] = 0
        pos["_last_action"] = action
        pos["_last_action_date"] = dt
        append_trade({"date": dt, "symbol": pos["_symbol"], "name": pos["name"],
                      "action": "SELL", "price": price, "shares": 0,
                      "pnl": pnl, "pnl_pct": round((pnl / cost_basis) * 100, 2) if cost_basis else 0.0,
                      "strategy": strategy})
        pnl_pct = round((pnl / cost_basis) * 100, 2) if cost_basis else 0
        emoji = "🟢" if pnl > 0 else "🔴"
        return "SELL", f"{emoji} **{pos['name']}({dt}) 卖出**\n价格 ¥{price:.2f} | 盈亏 {pnl_pct:+.2f}% | 理由: {signal['reason']}"

    return None, None


def _check_barbell_constraints(pos: Dict, signal: Dict, position_plan: Dict,
                                state: Dict) -> Tuple[bool, Optional[str]]:
    """
    哑铃策略硬约束检查（买入前强制预检）
    返回: (是否通过, 拦截原因)
    """
    if signal["action"] != "买入" or pos["position"]:
        return True, None  # 卖出或已持仓不检查
    
    bucket = pos.get("bucket", "satellite")
    max_single_pct = 0.15 if bucket == "core" else 0.05
    single_cap = INITIAL_CAPITAL * max_single_pct
    
    # 1. 030 仓位限制：单股上限
    max_amount = position_plan.get("single_stock_max_amount", 100_000) * position_plan.get("buy_signal_multiplier", 1.0)
    if max_amount > single_cap:
        max_amount = single_cap
    
    # 2. 核心/卫星仓位总上限检查
    total_initial = INITIAL_CAPITAL * 35  # 35只股票基准
    core_limit = total_initial * 0.80  # 核心仓最多80%
    sat_limit = total_initial * 0.20   # 卫星仓最多20%
    
    # 计算当前核心/卫星仓位价值
    positions = state.get("positions", {})
    core_value = 0
    sat_value = 0
    for p in positions.values():
        if p.get("position"):
            b = p.get("bucket", "satellite")
            val = p.get("cash", 0) + p.get("shares", 0) * p.get("entry_price", 0) * (1 + 0.001)  # 近似市值
            if b == "core":
                core_value += val
            else:
                sat_value += val
    
    # 估算买入后价值
    est_buy_value = min(pos["cash"], max_amount)
    
    if bucket == "core":
        if core_value + est_buy_value > core_limit:
            return False, f"🏰 核心仓位上限拦截: 当前¥{core_value:,.0f} + 估算买入¥{est_buy_value:,.0f} > 上限¥{core_limit:,.0f}"
    else:
        if sat_value + est_buy_value > sat_limit:
            return False, f"🛰️ 卫星仓位上限拦截: 当前¥{sat_value:,.0f} + 估算买入¥{est_buy_value:,.0f} > 上限¥{sat_limit:,.0f}"
    
    # 3. 方向分配上限（单日单方向不超过总仓位上限60%）
    direction = pos.get("direction", "其他")
    direction_alloc = position_plan.get("direction_allocation", {})
    dir_limit = direction_alloc.get(direction, 0)
    if dir_limit > 0:
        # 估算该方向已占用
        used_dir = 0
        for p in positions.values():
            if p.get("position") and p.get("direction", "其他") == direction:
                used_dir += p.get("cash", 0) + p.get("shares", 0) * p.get("entry_price", 0) * (1 + 0.001)
        if used_dir + est_buy_value > dir_limit:
            return False, f"🎯 方向{dir_limit:,.0f}上限拦截: {direction}已用¥{used_dir:,.0f} + 估算买入¥{est_buy_value:,.0f} > 上限"
    
    return True, None

def calc_position_value(pos: Dict, price: float) -> float:
    if pos.get("position"):
        return pos["cash"] + pos["shares"] * price
    return pos["cash"]


# ══════════════════════════════════════════
# 主扫描流程（供模拟盘/实盘调用）
# ═══════════════════════════════════════════
def run_portfolio_scan(
    stocks: List[Tuple[str, str]],
    fetch_data_func,  # 签名: fetch_data(symbol) -> DataFrame
    today_str: str = None,
    state: Dict = None,
    strategy_map: Dict = None,
    default_strategy: str = "ema_cross",
) -> Dict:
    """
    统一的组合扫描入口。
    返回: {portfolio_summary, details, messages, state}
    """
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")

    if state is None:
        state = load_state()
    if strategy_map is None:
        strategy_map = load_strategy_map()

    # 刷新 030 风控
    RiskGuard.refresh()
    fatal_risk = RiskGuard.get_fatal_risk()
    position_plan = RiskGuard.get_position_plan()

    # 同步策略映射
    sync_strategy_map(state, strategy_map, default_strategy)

    print(f"📡 组合扫描开始 ({today_str})...")
    all_msgs = []
    trade_count = 0
    errors = []
    results = []

    for sym, name in stocks:
        pos = state["positions"].get(sym)
        if pos is None:
            pos = init_position(sym, name, strategy_map.get(sym, default_strategy))
            state["positions"][sym] = pos
        pos["_symbol"] = sym
        strat = pos["strategy"]
        slabel = STRATEGY_LABELS.get(strat, strat)

        df = fetch_data_func(sym)
        if df is None or len(df) < 60:
            errors.append(f"{name}({sym}) 数据获取失败")
            results.append({"symbol": sym, "name": name, "error": "数据获取失败",
                            "position": pos["position"], "strategy": slabel})
            continue

        sig_func = SIGNAL_FUNCS.get(strat, signal_ema_cross)
        try:
            signal = sig_func(df)
        except Exception as e:
            errors.append(f"{name}({sym}) 信号计算失败: {e}")
            results.append({"symbol": sym, "name": name, "error": str(e),
                            "position": pos["position"], "strategy": slabel})
            continue

        # 030 风控覆盖
        signal = RiskGuard.override_signal(signal, pos)
        # 策略切换强平
        signal = RiskGuard.check_strategy_switch(signal, pos)
        # 重复 BUY 钝化
        signal, should_exec = RiskGuard.apply_buy_streak_dampening(signal, pos)
        # 去重
        data_date = df["date"].iloc[-1].strftime("%Y-%m-%d")
        if not RiskGuard.check_dedup(signal, pos, data_date):
            should_exec = False

        # 仅新交易日执行
        if should_exec and state.get("last_signal_date") is not None and data_date != state.get("last_signal_date"):
            action, msg = execute_trade(pos, signal, data_date, strategy=strat, position_plan=position_plan, state=state)
            if action:
                trade_count += 1
                all_msgs.append(msg)

        # 计算持仓价值
        pos_val = calc_position_value(pos, signal["price"])
        pos_pl = pos["total_pl"]
        if pos.get("position"):
            pos_pl += (pos["shares"] * signal["price"]) - (pos["shares"] * pos["entry_price"] * (1 + SLIPPAGE))
        pos_return = round((pos_val / INITIAL_CAPITAL - 1) * 100, 2)
        pos_pl_round = round(pos_pl, 2)

        bucket = pos.get("bucket", "unknown")
        if bucket == "unknown":
            bucket, _, _ = classify_bucket(sym)
            pos["bucket"] = bucket

        results.append({
            "symbol": sym, "name": name,
            "strategy": slabel,
            "price": signal["price"],
            "position": pos["position"],
            "entry_price": pos["entry_price"] if pos["position"] else 0,
            "entry_date": pos["entry_date"],
            "shares": pos["shares"],
            "cash": round(pos["cash"], 2),
            "value": round(pos_val, 2),
            "unrealized_pl": round(pos_pl_round, 2),
            "return_pct": pos_return,
            "trade_count": pos["trade_count"],
            "signal": signal["action"],
            "reason": signal["reason"],
            "fib_targets": pos.get("fib_targets", {}),
            "atr_stop": signal.get("atr_stop"),
            "fatal_risk_override": fatal_risk.get("fatal_triggered", False) or fatal_risk.get("high_risk_triggered", False),
            "position_mult": position_plan.get("buy_signal_multiplier", 1.0),
            "max_buy_amount": round(position_plan.get("single_stock_max_amount", 100_000) * position_plan.get("buy_signal_multiplier", 1.0), 2),
            "bucket": bucket,
        })

    state["last_signal_date"] = today_str
    save_state(state)

    # 汇总
    total_value = sum(r["value"] for r in results if "value" in r)
    total_initial = INITIAL_CAPITAL * len(stocks)
    total_return = round((total_value - total_initial) / total_initial * 100, 2)
    pos_count = sum(1 for r in results if r.get("position"))
    buy_qty = sum(1 for r in results if r.get("signal") == "买入")
    sell_qty = sum(1 for r in results if r.get("signal") == "卖出")

    core_positions = [r for r in results if r.get("position") and r.get("bucket") == "core"]
    sat_positions = [r for r in results if r.get("position") and r.get("bucket") == "satellite"]
    core_value = sum(r["value"] for r in core_positions)
    sat_value = sum(r["value"] for r in sat_positions)
    core_limit = total_initial * 0.8
    sat_limit = total_initial * 0.2

    portfolio = {
        "total_value": round(total_value, 2),
        "total_initial": total_initial,
        "total_return_pct": total_return,
        "positions_held": pos_count,
        "buy_signals_today": buy_qty,
        "sell_signals_today": sell_qty,
        "trades_executed": trade_count,
        "core_positions": len(core_positions),
        "satellite_positions": len(sat_positions),
        "core_value": round(core_value, 2),
        "satellite_value": round(sat_value, 2),
        "core_limit_pct": round(core_value / total_initial * 100, 2) if total_initial > 0 else 0,
        "sat_limit_pct": round(sat_value / total_initial * 100, 2) if total_initial > 0 else 0,
        "fatal_risk_triggered": fatal_risk.get("fatal_triggered", False),
        "high_risk_triggered": fatal_risk.get("high_risk_triggered", False),
        "position_plan": position_plan,
    }

    # 记录每日权益快照（移到循环外，仅记录一次）
    cash_total = sum(r.get("cash", 0) for r in results if "cash" in r)
    try:
        append_equity_snapshot(today_str, portfolio, core_value, sat_value, cash_total)
    except Exception as e:
        print(f"⚠️ 权益快照记录失败: {e}")

    return {
        "date": today_str,
        "stock_count": len(stocks),
        "portfolio": portfolio,
        "errors": errors,
        "details": results,
        "messages": all_msgs,
        "state": state,
    }


# ═══════════════════════════════════════════
# 人类可读报告生成
# ═══════════════════════════════════════════
def generate_human_report(scan_result: Dict) -> str:
    today_str = scan_result["date"]
    portfolio = scan_result["portfolio"]
    results = scan_result["details"]
    all_msgs = scan_result["messages"]
    errors = scan_result["errors"]
    fatal_risk = {"fatal_triggered": portfolio["fatal_risk_triggered"],
                  "high_risk_triggered": portfolio["high_risk_triggered"]}
    position_plan = portfolio["position_plan"]

    lines = []
    lines.append(f"💼 **全组合日报** ({today_str})")
    lines.append(f"组合总资产: ¥{portfolio['total_value']:,.2f} ({portfolio['total_return_pct']:+.2f}%) | 持仓 {portfolio['positions_held']}/{scan_result['stock_count']} 只")
    lines.append(f"今日信号: 🟢买入 {portfolio['buy_signals_today']} | 🔴卖出 {portfolio['sell_signals_today']} | 实际成交 {portfolio['trades_executed']} 笔")

    if fatal_risk.get("fatal_triggered", False):
        lines.append("☠️ **030致命风险触发**: 强制清仓，禁止买入")
    elif fatal_risk.get("high_risk_triggered", False):
        lines.append("🟡 **030高风险防御**: 核心中军≤-3%，减仓止损，不开新仓")
    else:
        lines.append(f"📐 **030仓位计划**: 总上限{position_plan.get('total_limit_pct', 0):.1%}(¥{position_plan.get('total_limit_amount', 0):,.0f}) | 单股上限{position_plan.get('single_stock_max_pct', 0):.1%}(¥{position_plan.get('single_stock_max_amount', 0):,.0f}) | 买入乘数{position_plan.get('buy_signal_multiplier', 1.0):.1f}x")
        lines.append(f"🏰 **哑铃仓位**: 核心仓{portfolio['core_positions']}只(¥{portfolio['core_value']:,.0f}/{portfolio['core_positions']*100000*0.8:,.0f}) | 卫星仓{portfolio['satellite_positions']}只(¥{portfolio['satellite_value']:,.0f}/{portfolio['satellite_positions']*100000*0.2:,.0f})")
    lines.append("")

    if all_msgs:
        lines.append("📝 **今日成交**")
        lines.extend(all_msgs)
        lines.append("")

    lines.append("📈 **持仓明细**(按收益率排序)")
    held = sorted([r for r in results if r.get("position")], key=lambda x: -x.get("return_pct", 0))
    if held:
        lines.append("| 股票 | 策略 | 现价 | 成本 | 持仓股 | 收益率 | 止损价 | 仓位类型 |")
        lines.append("|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|")
        for r in held:
            bucket = r.get("bucket", "未知")
            bucket_emoji = "🏰" if bucket == "core" else "🛰️" if bucket == "satellite" else "❓"
            atr_stop = r.get("atr_stop", 0)
            stop_str = f"¥{atr_stop:.2f}" if atr_stop and atr_stop > 0 else "—"
            lines.append(f"| {r['name']}({r['symbol']}) | {r['strategy']} | ¥{r['price']:.2f} | ¥{r['entry_price']:.2f} | {r['shares']} | {r['return_pct']:+.2f}% | {stop_str} | {bucket_emoji} |")
        lines.append("")

    lines.append("🎯 **今日信号**")
    buys = [r for r in results if r.get("signal") == "买入"]
    sells = [r for r in results if r.get("signal") == "卖出"]
    if buys:
        lines.append("🟢 买入信号:")
        for r in buys:
            max_amt = r.get("max_buy_amount", 0)
            lines.append(f"  • {r['name']}({r['symbol']}) [{r['strategy']}] {r['reason']} | 限额¥{max_amt:,.0f}")
    if sells:
        lines.append("🔴 卖出信号:")
        for r in sells:
            fatal_override = " ☠️" if r.get("fatal_risk_override", False) else ""
            lines.append(f"  • {r['name']}({r['symbol']}) [{r['strategy']}] {r['reason']}{fatal_override}")
    if not buys and not sells:
        lines.append("  今日无买卖信号，全部持有/观望")

    if errors:
        lines.append("")
        lines.append("⚠️ 数据异常:")
        for e in errors:
            lines.append(f"  • {e}")

    return "\n".join(lines)


if __name__ == "__main__":
    # 简单自测
    from analysis.portfolio_sim import fetch_data, STOCKS
    result = run_portfolio_scan(STOCKS[:3], fetch_data)
    print(generate_human_report(result))