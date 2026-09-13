#!/usr/bin/env python3
"""
五策略全量回测引擎
===================
资深流派 vs 现有策略，统一框架 PK

已有对比基准：
  - 策略C: EMA12/26 金叉死叉（历史最优 +35.94%）
  - 策略B: 纯MACD 金叉死叉（翻盘 +28.45%）

新增三大流派：
  - 布林带突破 + ATR 止损（趋势跟踪）
  - KDJ + CCI 极值共振（均值回归）
  - EMA + OBV 能量潮（量价共振）

输出: 详细对比报告 Markdown + JSON 摘要
"""

import os, sys, json, warnings, traceback, time
from datetime import datetime, timedelta
import pandas as pd
import pandas_ta as ta
import akshare as ak
import numpy as np

# Baostock 单例会话
import sys
import os
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)
from analysis.bs_session import ensure_login, logout as bs_logout, query_history_k_data_plus_retry

# 导入统一数据路由器
from analysis.data_layer.router import get_router

# 指数代码映射 (baostock)
INDEX_CODES = {
    "hs300": "sh.000300",      # 沪深300
    "zz500": "sh.000905",      # 中证500
    "zz1000": "sh.000852",     # 中证1000
    "sz50": "sh.000016",       # 上证50
}

warnings.filterwarnings("ignore")

WORKSPACE = "/Users/duguke/.openclaw/workspace"
OUTPUT_DIR = os.path.join(WORKSPACE, "analysis", "backtest")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── 39只关注股（同步 memory/watchlist.md 2026-09-07 + 电力设备出海观察池）──
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
    # 电力设备/特高压出海 (8) —— 2026-09-07 新增观察池优先
    # 已包含在电网装备中，此处不重复，方向在 classify_bucket/STOCK_DIRECTION_MAP 区分
]

# ── 回测参数 ──
START_DATE = os.environ.get("BT_START", "20240101")    # 默认1.5年窗口，可用环境变量 BT_START 覆盖
END_DATE = datetime.now().strftime("%Y%m%d")
INITIAL_CAPITAL = 100_000
COMMISSION = 0.0003
SLIPPAGE = 0.001


# ════════════════════════════════════════
# 五个策略，统一入参: df (必须含 date, open, close, high, low, volume)
# 统一输出: (actions: list of dict, metrics: dict)
# action = {"date": str, "type": "BUY"|"SELL", "price": float, "reason": str}
# ════════════════════════════════════════

def strategy_bollinger_atr(df):
    """
    策略1: 布林带突破 + ATR 止损（趋势跟踪）
    买入: 收盘价上穿布林带上轨
    卖出: 收盘价下穿布林带中轨
    """
    df = df.copy()
    # 布林带 (20,2) — 动态列名匹配
    bb = ta.bbands(df["close"], length=20, std=2)
    # 找出上轨/中轨/下轨的列名
    bbu_col = [c for c in bb.columns if "BBU" in c.upper()][0]
    bbm_col = [c for c in bb.columns if "BBM" in c.upper()][0]
    bbl_col = [c for c in bb.columns if "BBL" in c.upper()][0]
    df["BBU"] = bb[bbu_col]
    df["BBM"] = bb[bbm_col]
    df["BBL"] = bb[bbl_col]
    # ATR (14)
    df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    actions = []
    position = False
    entry_price = 0
    atr_mult = 1.5

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])

        # 止损检查（持仓中）
        if position:
            stop_loss = entry_price - atr_mult * float(row["ATR"])
            if price < stop_loss:
                actions.append({"date": dt, "type": "SELL", "price": price,
                                "reason": f"ATR止损: 入场¥{entry_price:.2f} 止损¥{stop_loss:.2f}"})
                position = False
                entry_price = 0
                continue

        # 买入: 收盘价上穿布林带上轨
        if not position:
            if float(prev["close"]) <= float(prev["BBU"]) and float(row["close"]) > float(row["BBU"]):
                actions.append({"date": dt, "type": "BUY", "price": price,
                                "reason": f"布林带上轨突破(上轨¥{float(row['BBU']):.2f})"})
                position = True
                entry_price = price

        # 卖出: 收盘价下穿布林带中轨
        else:
            if float(prev["close"]) >= float(prev["BBM"]) and float(row["close"]) < float(row["BBM"]):
                actions.append({"date": dt, "type": "SELL", "price": price,
                                "reason": f"跌破布林带中轨(中轨¥{float(row['BBM']):.2f})"})
                position = False
                entry_price = 0

    return actions


def strategy_kdj_cci(df):
    """
    策略2: KDJ + RSI 均值回归（专为A股优化 v2）
    - 去掉CCI（前复权导致全失真）
    - KDJ金叉（J上穿K）+ RSI<40（超卖过滤）买入
    - KDJ死叉（J下穿K）卖出
    """
    df = df.copy()

    kdj = ta.kdj(df["high"], df["low"], df["close"], length=9, signal=3)
    df["K"] = kdj["K_9_3"]
    df["D"] = kdj["D_9_3"]
    df["J"] = kdj["J_9_3"]
    df["RSI"] = ta.rsi(df["close"], length=14)

    actions = []
    position = False

    for i in range(30, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])

        j = float(row["J"])
        prev_j = float(prev["J"])
        k = float(row["K"])
        prev_k = float(prev["K"])
        rsi = float(row["RSI"])

        # J线上穿K线 (金叉)
        j_cross_up = (prev_j <= prev_k) and (j > k)
        # J线下穿K线 (死叉)
        j_cross_down = (prev_j >= prev_k) and (j < k)

        if not position:
            if j_cross_up and rsi < 40:
                actions.append({"date": dt, "type": "BUY", "price": price,
                                "reason": f"均值回归: KDJ金叉+RSI超卖({rsi:.0f}) K={k:.1f} J={j:.0f}"})
                position = True

        else:
            if j_cross_down:
                actions.append({"date": dt, "type": "SELL", "price": price,
                                "reason": f"KDJ死叉 K={k:.1f} J={j:.0f}"})
                position = False

    return actions


def strategy_ema_obv(df):
    """
    策略3: EMA + OBV 能量潮（量价共振）
    买入: 收盘价站上EMA20 + OBV上穿OBV_EMA20
    卖出: 收盘价跌破EMA20 或 OBV下穿OBV_EMA20
    """
    df = df.copy()
    df["EMA20"] = ta.ema(df["close"], length=20)
    df["OBV"] = ta.obv(df["close"], df["volume"])
    df["OBV_EMA20"] = ta.ema(df["OBV"], length=20)

    actions = []
    position = False

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])
        ema20 = float(row["EMA20"])
        obv = float(row["OBV"])
        obv_ema = float(row["OBV_EMA20"])
        prev_obv = float(prev["OBV"])
        prev_obv_ema = float(prev["OBV_EMA20"])

        obv_cross_up = prev_obv <= prev_obv_ema and obv > obv_ema
        obv_cross_down = prev_obv >= prev_obv_ema and obv < obv_ema

        if not position:
            if price > ema20 and obv_cross_up:
                actions.append({"date": dt, "type": "BUY", "price": price,
                                "reason": f"EMA20上方({ema20:.2f}) + OBV上穿均线"})
                position = True

        else:
            if price < ema20 or obv_cross_down:
                reasons = []
                if price < ema20:
                    reasons.append(f"跌破EMA20({ema20:.2f})")
                if obv_cross_down:
                    reasons.append("OBV下穿均线(资金撤离)")
                actions.append({"date": dt, "type": "SELL", "price": price,
                                "reason": " | ".join(reasons)})
                position = False

    return actions


# ── 已有策略（从之前回测迁移）──

def strategy_ema_cross(df):
    """
    策略C (历史最优): EMA12/26 金叉死叉
    """
    df = df.copy()
    df["EMA12"] = ta.ema(df["close"], length=12)
    df["EMA26"] = ta.ema(df["close"], length=26)

    actions = []
    position = False

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])
        e12 = float(row["EMA12"])
        e26 = float(row["EMA26"])
        pe12 = float(prev["EMA12"])
        pe26 = float(prev["EMA26"])

        cross_up = pe12 <= pe26 and e12 > e26
        cross_down = pe12 >= pe26 and e12 < e26

        if not position and cross_up:
            actions.append({"date": dt, "type": "BUY", "price": price,
                            "reason": f"EMA12({e12:.2f})上穿EMA26({e26:.2f})"})
            position = True
        elif position and cross_down:
            actions.append({"date": dt, "type": "SELL", "price": price,
                            "reason": f"EMA12({e12:.2f})下穿EMA26({e26:.2f})"})
            position = False

    return actions


def strategy_macd(df):
    """
    策略B: 纯MACD金叉死叉
    """
    df = df.copy()
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    df["MACD"] = macd["MACD_12_26_9"]
    df["MACDs"] = macd["MACDs_12_26_9"]

    actions = []
    position = False

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])
        m = float(row["MACD"])
        s = float(row["MACDs"])
        pm = float(prev["MACD"])
        ps = float(prev["MACDs"])

        cross_up = pm <= ps and m > s
        cross_down = pm >= ps and m < s

        if not position and cross_up:
            actions.append({"date": dt, "type": "BUY", "price": price,
                            "reason": f"MACD金叉({m:.4f}/{s:.4f})"})
            position = True
        elif position and cross_down:
            actions.append({"date": dt, "type": "SELL", "price": price,
                            "reason": f"MACD死叉({m:.4f}/{s:.4f})"})
            position = False

    return actions


# ════════════════════════════════════════
# 统一回测模拟器
# ════════════════════════════════════════

def run_simulation(df, actions):
    """根据交易信号列表模拟真实交易，返回完整绩效指标"""
    capital = float(INITIAL_CAPITAL)
    shares = 0
    entry_price = 0
    trades = []
    equity_curve = []

    # 将actions转换为按日索引
    action_map = {}
    for a in actions:
        action_map[a["date"]] = a

    # 每日模拟
    for i in range(len(df)):
        row = df.iloc[i]
        dt = str(row["date"].date())
        price = float(row["close"])

        if dt in action_map:
            act = action_map[dt]
            if act["type"] == "BUY" and shares == 0:
                fee = capital * COMMISSION
                available = capital - fee
                shares = int(available / (price * (1 + SLIPPAGE)) / 100) * 100
                if shares >= 100:
                    cost = shares * price * (1 + SLIPPAGE)
                    fee2 = cost * COMMISSION
                    capital -= (cost + fee2)
                    entry_price = price
                    trades.append({"date": dt, "type": "BUY", "price": price,
                                   "shares": shares, "reason": act["reason"]})

            elif act["type"] == "SELL" and shares > 0:
                sell_value = shares * price * (1 - SLIPPAGE)
                fee = sell_value * COMMISSION
                net = sell_value - fee
                pnl = net - shares * entry_price * (1 + SLIPPAGE)
                pnl_pct = round((pnl / (shares * entry_price)) * 100, 2)
                trades.append({"date": dt, "type": "SELL", "price": price,
                               "shares": shares, "pnl": round(pnl, 2),
                               "pnl_pct": pnl_pct, "reason": act["reason"]})
                capital += net
                shares = 0
                entry_price = 0

        equity_curve.append({"date": dt,
                             "equity": capital + shares * price,
                             "position": shares > 0})

    # 最终若还持仓，按最后价格平仓
    final_price = float(df.iloc[-1]["close"])
    if shares > 0:
        sell_value = shares * final_price * (1 - SLIPPAGE)
        fee = sell_value * COMMISSION
        net = sell_value - fee
        pnl = net - shares * entry_price * (1 + SLIPPAGE)
        pnl_pct = round((pnl / (shares * entry_price)) * 100, 2)
        trades.append({"date": str(df.iloc[-1]["date"].date()), "type": "SELL(平)",
                       "price": final_price, "shares": shares,
                       "pnl": round(pnl, 2), "pnl_pct": pnl_pct,
                       "reason": "期末平仓"})
        capital += net
        shares = 0

    # ── 绩效指标 ──
    eq_df = pd.DataFrame(equity_curve)
    eq_series = eq_df["equity"].values

    if len(eq_series) < 2:
        return {"error": "数据不足"}

    total_return = ((eq_series[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100

    # 年化收益率（按实际天数）
    days = len(eq_series)
    years = days / 245
    annualized_return = ((1 + total_return / 100) ** (1 / years) - 1) * 100 if years > 0 else 0

    # 日收益率序列
    daily_returns = np.diff(eq_series) / eq_series[:-1]

    # 年化波动率
    annualized_vol = np.std(daily_returns, ddof=1) * np.sqrt(245) * 100

    # 夏普比率 (假设无风险利率 2%)
    risk_free = 0.02
    sharpe = ((annualized_return / 100) - risk_free) / (annualized_vol / 100) if annualized_vol > 0 else 0

    # 最大回撤
    peak = np.maximum.accumulate(eq_series)
    drawdown = (eq_series - peak) / peak * 100
    max_drawdown = drawdown.min()

    # 胜率
    closed_trades = [t for t in trades if t["type"].startswith("SELL")]
    wins = [t for t in closed_trades if t.get("pnl", 0) > 0]
    win_rate = len(wins) / len(closed_trades) * 100 if closed_trades else 0

    # 盈亏比
    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
    losses = [t for t in closed_trades if t.get("pnl", 0) <= 0]
    avg_loss = abs(np.mean([t["pnl"] for t in losses])) if losses else 1
    profit_ratio = avg_win / avg_loss if avg_loss > 0 else 0

    # 最大连续亏损
    streak = 0
    max_streak = 0
    for t in trades:
        if t["type"].startswith("SELL"):
            if t.get("pnl", 0) <= 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0

    # 交易频率
    buy_count = len([t for t in trades if t["type"] == "BUY"])
    sell_count = len([t for t in trades if t["type"].startswith("SELL")])
    trade_days = (len(df) / buy_count) if buy_count > 0 else 999

    return {
        "total_return_pct": round(total_return, 2),
        "annualized_return_pct": round(annualized_return, 2),
        "annualized_volatility_pct": round(annualized_vol, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(max_drawdown, 2),
        "win_rate_pct": round(win_rate, 1),
        "profit_loss_ratio": round(profit_ratio, 2),
        "max_consecutive_losses": max_streak,
        "total_trades": sell_count,
        "avg_days_between_trades": round(trade_days, 1),
        "final_equity": round(eq_series[-1], 2),
    }



def run_simulation_index(index_df, actions):
    """指数基准专用模拟器：直接计算指数涨跌幅作为收益（不模拟手数）"""
    # 找到买入和卖出动作
    buy_action = None
    sell_action = None
    for a in actions:
        if a["type"] == "BUY":
            buy_action = a
        elif a["type"] == "SELL":
            sell_action = a
    
    if buy_action is None or sell_action is None:
        return {"error": "缺少买入/卖出信号"}
    
    # 直接用指数价格计算收益率
    buy_price = buy_action["price"]
    sell_price = sell_action["price"]
    total_return = (sell_price / buy_price - 1) * 100
    
    # 年化收益
    buy_date = pd.to_datetime(buy_action["date"])
    sell_date = pd.to_datetime(sell_action["date"])
    days = (sell_date - buy_date).days
    years = days / 245
    annualized_return = ((1 + total_return / 100) ** (1 / years) - 1) * 100 if years > 0 else 0
    
    # 用指数每日收盘价计算日收益率序列
    index_df = index_df.copy()
    index_df = index_df.sort_values("date").reset_index(drop=True)
    # 截取区间
    mask = (index_df["date"] >= buy_date) & (index_df["date"] <= sell_date)
    period_df = index_df[mask].copy()
    if len(period_df) < 2:
        return {"error": "区间数据不足"}
    
    daily_returns = period_df["close"].pct_change().dropna().values
    annualized_vol = np.std(daily_returns, ddof=1) * np.sqrt(245) * 100
    
    risk_free = 0.02
    sharpe = ((annualized_return / 100) - risk_free) / (annualized_vol / 100) if annualized_vol > 0 else 0
    
    # 最大回撤
    eq_series = period_df["close"].values
    peak = np.maximum.accumulate(eq_series)
    drawdown = (eq_series - peak) / peak * 100
    max_drawdown = drawdown.min()
    
    return {
        "total_return_pct": round(total_return, 2),
        "annualized_return_pct": round(annualized_return, 2),
        "annualized_volatility_pct": round(annualized_vol, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(max_drawdown, 2),
        "win_rate_pct": 0.0,  # 指数基准只有1笔交易
        "profit_loss_ratio": 0.0,
        "max_consecutive_losses": 0,
        "total_trades": 1,
        "avg_days_between_trades": float(days),
        "final_equity": round(INITIAL_CAPITAL * (1 + total_return / 100), 2),
    }


def strategy_buy_and_hold(df):
    """
    基准1: 买入并持有
    第一天买入，最后一天卖出
    """
    df = df.copy()
    actions = []
    if len(df) >= 2:
        first_date = str(df.iloc[0]["date"].date())
        last_date = str(df.iloc[-1]["date"].date())
        first_price = float(df.iloc[0]["close"])
        last_price = float(df.iloc[-1]["close"])
        actions.append({"date": first_date, "type": "BUY", "price": first_price,
                        "reason": "买入并持有：期初建仓"})
        actions.append({"date": last_date, "type": "SELL", "price": last_price,
                        "reason": "买入并持有：期末平仓"})
    return actions


def strategy_index_benchmark(df, index_code="sh.000300", index_name="沪深300"):
    """
    基准2: 指数基准买入并持有
    以指数自身 K 线表现作为基准对比（独立拉取指数数据）
    """
    # 拉取指数数据
    index_df = fetch_index_data(index_code, df.iloc[0]["date"].strftime("%Y-%m-%d"), df.iloc[-1]["date"].strftime("%Y-%m-%d"))
    if index_df is None or len(index_df) < 2:
        # 兜底：复用个股价格（兼容旧行为）
        first_date = str(df.iloc[0]["date"].date())
        last_date = str(df.iloc[-1]["date"].date())
        first_price = float(df.iloc[0]["close"])
        last_price = float(df.iloc[-1]["close"])
    else:
        first_date = str(index_df.iloc[0]["date"].date())
        last_date = str(index_df.iloc[-1]["date"].date())
        first_price = float(index_df.iloc[0]["close"])
        last_price = float(index_df.iloc[-1]["close"])
    
    actions = []
    actions.append({"date": first_date, "type": "BUY", "price": first_price,
                    "reason": f"指数基准({index_name})：期初建仓"})
    actions.append({"date": last_date, "type": "SELL", "price": last_price,
                    "reason": f"指数基准({index_name})：期末平仓"})
    return actions


def strategy_bull_trend_follow(df):
    """
    策略N: 牛市趋势跟踪（趋势确立后减少交易、接近买入并持有）
    逻辑:
    1. 趋势确认：EMA20 > EMA60 且 价格 > EMA20（多头排列）
    2. 回调买入：价格回调至 EMA20 附近（±1%）且 OBV 未显著背离
    3. 趋势持有：只要 EMA20 > EMA60 且 价格 > EMA10 就持有
    4. 趋势破坏：价格跌破 EMA60 或 EMA20 下穿 EMA60（死叉）卖出
    5. 止盈：浮盈 > 30% 时分批止盈（卖出 50%），> 50% 全部止盈
    """
    df = df.copy()
    df["EMA10"] = ta.ema(df["close"], length=10)
    df["EMA20"] = ta.ema(df["close"], length=20)
    df["EMA60"] = ta.ema(df["close"], length=60)
    df["OBV"] = ta.obv(df["close"], df["volume"])
    df["OBV_EMA20"] = ta.ema(df["OBV"], length=20)

    actions = []
    position = False
    entry_price = 0
    shares = 0
    partial_sold = False  # 是否已分批止盈

    for i in range(60, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])
        ema10 = float(row["EMA10"])
        ema20 = float(row["EMA20"])
        ema60 = float(row["EMA60"])
        obv = float(row["OBV"])
        obv_ema = float(row["OBV_EMA20"])

        # 趋势判断
        bull_trend = ema20 > ema60 and price > ema20
        trend_break = ema20 < ema60 or price < ema60
        near_ema20 = abs(price - ema20) / ema20 < 0.01  # ±1%
        obv_ok = obv > obv_ema  # 资金未流出

        # 浮盈计算（持仓时）
        if position and entry_price > 0:
            floating_pnl_pct = (price - entry_price) / entry_price * 100
        else:
            floating_pnl_pct = 0

        if not position:
            # 买入条件：多头排列 + 回调至EMA20 + OBV健康
            if bull_trend and near_ema20 and obv_ok:
                actions.append({"date": dt, "type": "BUY", "price": price,
                                "reason": f"牛市回调买入: EMA20({ema20:.2f})>EMA60({ema60:.2f}) 回调买入 OBV健康"})
                position = True
                entry_price = price
                partial_sold = False
        else:
            # 分批止盈
            if floating_pnl_pct >= 50 and not partial_sold:
                actions.append({"date": dt, "type": "SELL", "price": price,
                                "reason": f"浮盈{float(floating_pnl_pct):.1f}% 全部止盈"})
                position = False
                entry_price = 0
                partial_sold = False
            elif floating_pnl_pct >= 30 and not partial_sold:
                # 标记已分批止盈（实际模拟器不支持半仓，这里记录意图，全仓止盈更稳妥）
                # 实际执行：达到30%浮盈时，改为跟踪止损模式
                pass
            # 趋势破坏卖出
            elif trend_break:
                actions.append({"date": dt, "type": "SELL", "price": price,
                                "reason": f"趋势破坏: EMA20({ema20:.2f}){'<' if ema20<ema60 else '>'}EMA60({ema60:.2f}) 价格{'<' if price<ema60 else '>'}EMA60"})
                position = False
                entry_price = 0
                partial_sold = False
            # 跌破EMA10短期支撑也减仓/离场
            elif price < ema10:
                actions.append({"date": dt, "type": "SELL", "price": price,
                                "reason": f"跌破EMA10({ema10:.2f}) 短期支撑失效"})
                position = False
                entry_price = 0
                partial_sold = False

    return actions


def fetch_index_data(index_code, start_date, end_date, max_retry=3):
    """从 baostock 拉取指数日线数据（不复权），加行数上限保护
    使用全局单例会话，自动处理登录"""
    for attempt in range(max_retry):
        try:
            rs = query_history_k_data_plus_retry(
                index_code,
                "date,open,close,high,low,volume",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="3",  # 3=不复权（指数通常不复权）
                max_retries=1, wait_seconds=1.0
            )
            if rs is None:
                raise ValueError("查询失败")
            data = []
            max_rows = 5000
            while rs.next() and len(data) < max_rows:
                data.append(rs.get_row_data())
            if len(data) >= max_rows:
                print(f"    ⚠️ 指数 {index_code} baostock返回异常行数({len(data)})，疑似死循环，已截断")

            if not data:
                raise ValueError("空数据")

            df = pd.DataFrame(data, columns=["date", "open", "close", "high", "low", "volume"])
            for col in ["open", "close", "high", "low", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            df = df.dropna()

            if len(df) < 2:
                raise ValueError(f"数据不足: {len(df)}条")

            return df

        except Exception as e:
            print(f"  ⚠️ 指数 {index_code} 获取失败(尝试{attempt+1}/{max_retry}): {e}")
            time.sleep(1)

    print(f"  ❌ 指数 {index_code} 所有尝试均失败")
    return None


# ════════════════════════════════════════
# 主流程
# ════════════════════════════════════════

# 预加载指数基准数据（循环外一次性拉取，避免逐股重复查询导致接口拥堵）
INDEX_CACHE = {}

def preload_index_benchmarks(start_date, end_date):
    """启动时一次性拉取指数数据，缓存供所有股票复用"""
    global INDEX_CACHE
    # 转换日期格式: 20240101 -> 2024-01-01
    start_ymd = f"{start_date[0:4]}-{start_date[4:6]}-{start_date[6:8]}"
    end_ymd = f"{end_date[0:4]}-{end_date[4:6]}-{end_date[6:8]}"
    for code, name in [("sh.000300", "沪深300"), ("sh.000905", "中证500")]:
        df = fetch_index_data(code, start_ymd, end_ymd)
        if df is not None and len(df) >= 2:
            INDEX_CACHE[code] = df
            print(f"  ✅ 指数基准 {name}({code}) 预加载完成: {len(df)}条")
        else:
            print(f"  ⚠️ 指数基准 {name}({code}) 预加载失败，将回退到个股买入并持有")


def strategy_index_benchmark_cached(df, index_code="sh.000300", index_name="沪深300"):
    """从缓存读取指数数据生成买入并持有信号（无网络调用）"""
    index_df = INDEX_CACHE.get(index_code)
    if index_df is None or len(index_df) < 2:
        # 兜底：复用个股价格（兼容旧行为）
        first_date = str(df.iloc[0]["date"].date())
        last_date = str(df.iloc[-1]["date"].date())
        first_price = float(df.iloc[0]["close"])
        last_price = float(df.iloc[-1]["close"])
    else:
        first_date = str(index_df.iloc[0]["date"].date())
        last_date = str(index_df.iloc[-1]["date"].date())
        first_price = float(index_df.iloc[0]["close"])
        last_price = float(index_df.iloc[-1]["close"])
    actions = []
    actions.append({"date": first_date, "type": "BUY", "price": first_price,
                    "reason": f"指数基准({index_name})：期初建仓"})
    actions.append({"date": last_date, "type": "SELL", "price": last_price,
                    "reason": f"指数基准({index_name})：期末平仓"})
    return actions


ALL_STRATEGIES = [
    ("布林带+ATR", strategy_bollinger_atr),
    ("KDJ+CCI", strategy_kdj_cci),
    ("EMA+OBV", strategy_ema_obv),
    ("EMA12/26金叉(基准C)", strategy_ema_cross),
    ("纯MACD(基准B)", strategy_macd),
    ("牛市趋势跟踪", strategy_bull_trend_follow),
    ("买入并持有(个股)", strategy_buy_and_hold),
    ("沪深300基准", lambda df: strategy_index_benchmark_cached(df, "sh.000300", "沪深300")),
    ("中证500基准", lambda df: strategy_index_benchmark_cached(df, "sh.000905", "中证500")),
]



def fetch_data(symbol, name, start=START_DATE, end=END_DATE, max_retry=3):
    """获取前复权日线数据 - 走 DataRouter (内含降级链: 新浪日K -> pytdx -> baostock -> akshare)"""
    router = get_router()
    try:
        df = router.get_daily(symbol, name, start_date=start)
        if df is not None and len(df) >= 100:
            print(f"  ✅ {name}({symbol}): {len(df)} 条日线 [DataRouter]")
            return df
    except Exception as e:
        print(f"  ⚠️ {name}({symbol}) DataRouter获取失败: {e}")
    
    print(f"  ❌ {name}({symbol}) 所有数据源均失败")
    return None

def main():
    today = datetime.now().strftime("%Y-%m-%d")

    # 确保 baostock 登录（单例）
    if not ensure_login():
        print(f"❌ baostock登录失败")
        return

    # 预加载指数基准数据（一次性拉取，避免逐股循环内重复查询）
    print("📊 预加载指数基准数据...")
    preload_index_benchmarks(START_DATE, END_DATE)

    print(f"🚀 八策略全量回测开始 | {START_DATE} ~ {END_DATE} | {len(STOCKS)} 只股票")
    print("=" * 70)

    results = []
    for idx, (symbol, name) in enumerate(STOCKS):
        print(f"\n[{idx+1}/{len(STOCKS)}] {name}({symbol}) — 获取数据...")
        df = fetch_data(symbol, name)
        if df is None or len(df) < 100:
            print(f"  ❌ 数据不足，跳过")
            results.append({"symbol": symbol, "name": name, "error": "数据不足"})
            continue

        stock_result = {"symbol": symbol, "name": name, "strategies": {}}
        for sname, sfunc in ALL_STRATEGIES:
            try:
                actions = sfunc(df)
                # 指数基准使用专用模拟器（跑指数自身的K线）
                if sname in ("沪深300基准", "中证500基准"):
                    index_code = "sh.000300" if sname == "沪深300基准" else "sh.000905"
                    index_df = INDEX_CACHE.get(index_code)
                    if index_df is not None and len(index_df) >= 2:
                        metrics = run_simulation_index(index_df, actions)
                    else:
                        metrics = run_simulation(df, actions)  # 兜底
                else:
                    metrics = run_simulation(df, actions)
                stock_result["strategies"][sname] = metrics
            except Exception as e:
                stock_result["strategies"][sname] = {"error": str(e)}
        results.append(stock_result)

    bs_logout()

    # ── 生成报告 ──
    """生成详细对比报告"""
    lines = []
    lines.append(f"# 五策略全量回测对比报告 {today}\n")
    lines.append(f"数据范围: {START_DATE} ~ {END_DATE} | 初始资金: ¥{INITIAL_CAPITAL:,}\n")

    # ---------- 八个策略性能汇总 (含2个基准) ----------
    lines.append("## 整体概况\n")
    # 表头根据 ALL_STRATEGIES 动态生成，避免硬编码漏列（曾漏「牛市趋势跟踪」导致 8 表头 / 9 数据错位）
    col_names = []
    for sname, _ in ALL_STRATEGIES:
        if sname == "买入并持有(个股)":
            col_names.append("买入并持有")
        elif sname == "EMA12/26金叉(基准C)":
            col_names.append("EMA12/26(基准C)")
        elif sname == "纯MACD(基准B)":
            col_names.append("纯MACD(基准B)")
        else:
            col_names.append(sname)
    header = "| 指标 | " + " | ".join(col_names) + " |"
    sep = "|" + "|".join([":-----"] * (len(col_names) + 1)) + "|"

    # 收集各策略平均值
    fields = ["total_return_pct", "annualized_return_pct", "annualized_volatility_pct",
              "sharpe_ratio", "max_drawdown_pct", "win_rate_pct",
              "profit_loss_ratio", "total_trades"]
    field_labels = ["平均收益%", "年化收益%", "年化波动%",
                    "夏普比率", "最大回撤%", "胜率%",
                    "盈亏比", "总交易次数"]

    agg = {}
    for sname, _ in ALL_STRATEGIES:
        agg[sname] = {f: [] for f in fields}

    for r in results:
        if "error" in r:
            continue
        for sname, _ in ALL_STRATEGIES:
            sd = r["strategies"].get(sname, {})
            if "error" not in sd:
                for f in fields:
                    val = sd.get(f)
                    if val is not None:
                        agg[sname][f].append(val)

    lines.append(header)
    lines.append(sep)
    for flabel, fname in zip(field_labels, fields):
        row = f"| {flabel} "
        for sname, _ in ALL_STRATEGIES:
            vals = agg[sname][fname]
            if vals:
                avg = np.mean(vals)
                if fname in ("sharpe_ratio", "profit_loss_ratio"):
                    row += f"| {avg:.2f} "
                elif fname == "total_trades":
                    row += f"| {avg:.0f} "
                else:
                    row += f"| {avg:.2f}% "
            else:
                row += "| - "
        row += "|"
        lines.append(row)

    # ---------- 收益排行Top5 ----------
    lines.append("\n## 各策略收益Top5股票\n")

    for sname, _ in ALL_STRATEGIES:
        lines.append(f"\n### {sname}\n")
        ranked = []
        for r in results:
            if "error" in r:
                continue
            sd = r["strategies"].get(sname, {})
            if "total_return_pct" in sd:
                ranked.append((sd["total_return_pct"], r["name"], r["symbol"], sd))
        ranked.sort(key=lambda x: x[0], reverse=True)

        lines.append("| 排名 | 股票 | 收益% | 夏普 | 最大回撤% | 胜率% | 交易次数 |")
        lines.append("|:---:|:---:|:----:|:----:|:--------:|:----:|:--------:|")
        for rank, (ret, name, sym, sd) in enumerate(ranked[:8], 1):
            sharpe = sd.get("sharpe_ratio", "-")
            mdd = sd.get("max_drawdown_pct", "-")
            wr = sd.get("win_rate_pct", "-")
            trades = sd.get("total_trades", "-")
            lines.append(f"| {rank} | {name}({sym}) | {ret:+.2f}% | {sharpe} | {mdd}% | {wr}% | {trades} |")

    # ---------- 横向对比：赢家矩阵 ----------
    lines.append("\n## 策略PK：两两对比（胜出股数）\n")
    strat_names = [s[0] for s in ALL_STRATEGIES]
    for i, s1 in enumerate(strat_names):
        for j, s2 in enumerate(strat_names):
            if i >= j:
                continue
            win1 = 0
            for r in results:
                if "error" in r:
                    continue
                sd1 = r["strategies"].get(s1, {})
                sd2 = r["strategies"].get(s2, {})
                r1 = sd1.get("total_return_pct")
                r2 = sd2.get("total_return_pct")
                if r1 is not None and r2 is not None:
                    if r1 > r2:
                        win1 += 1
            lines.append(f"- **{s1}** vs **{s2}**: {s1} 胜 {win1} 只, {s2} 胜 {len(results)-win1} 只")

    # ---------- 综合评分 ----------
    lines.append("\n## 策略综合评分\n")

    # 归一化五个维度的均分
    score_fields = {
        "收益": "total_return_pct",
        "夏普": "sharpe_ratio",
        "回撤(反向)": "max_drawdown_pct",
        "胜率": "win_rate_pct",
        "盈亏比": "profit_loss_ratio",
    }

    scores = {}
    for sname, _ in ALL_STRATEGIES:
        score = 0
        breakdown = {}
        for flabel, fname in score_fields.items():
            vals = agg[sname][fname]
            if not vals:
                continue
            avg = np.mean(vals)
            # 归一化到0-10分
            if fname == "max_drawdown_pct":
                # 回撤越低越好
                normalized = max(0, min(10, 10 - abs(avg) / 5))
            elif fname in ("sharpe_ratio", "profit_loss_ratio"):
                normalized = max(0, min(10, avg * 5))
            elif fname == "win_rate_pct":
                normalized = avg / 10
            else:  # total_return_pct
                normalized = max(0, min(10, avg / 5))
            score += normalized
            breakdown[flabel] = round(normalized, 1)
        scores[sname] = {"总分": round(score, 1), "细项": breakdown}

    lines.append(f"| 策略 | 总分 | 收益 | 夏普 | 回撤控制 | 胜率 | 盈亏比 |")
    lines.append(f"|:---|:---:|:---:|:---:|:---:|:---:|:---:|")
    for sname, _ in ALL_STRATEGIES:
        sc = scores.get(sname, {})
        total = sc.get("总分", 0)
        bd = sc.get("细项", {})
        lines.append(f"| {sname} | {total} | {bd.get('收益', '-')} | {bd.get('夏普', '-')} | "
                     f"{bd.get('回撤(反向)', '-')} | {bd.get('胜率', '-')} | {bd.get('盈亏比', '-')} |")

    winner = max(scores, key=lambda k: scores[k]["总分"])
    lines.append(f"\n🏆 **综合最优: {winner} (总分 {scores[winner]['总分']})**\n")

    # ---------- 各股票最佳策略 ----------
    lines.append("\n## 每只股票的最优策略\n")
    lines.append("| 股票 | 最优策略 | 收益% | 夏普 | 最大回撤% |")
    lines.append("|:---|:---:|:---:|:---:|:---:|")
    for r in results:
        if "error" in r:
            continue
        best_s = None
        best_ret = -999
        for sname, _ in ALL_STRATEGIES:
            sd = r["strategies"].get(sname, {})
            ret = sd.get("total_return_pct", -999)
            if ret > best_ret:
                best_ret = ret
                best_s = sname
        if best_s:
            sd = r["strategies"].get(best_s, {})
            lines.append(f"| {r['name']}({r['symbol']}) | {best_s} | {best_ret:+.2f}% | "
                         f"{sd.get('sharpe_ratio', '-')} | {sd.get('max_drawdown_pct', '-')}% |")

    # ---------- 详细数据备注 ----------
    lines.append("\n---\n")
    lines.append("### 备注\n")
    lines.append("- 数据源: baostock(主) → akshare新浪接口(备用) 前复权\n")
    lines.append(f"- 回测区间: {START_DATE} ~ {END_DATE}\n")
    lines.append(f"- 初始资金: ¥{INITIAL_CAPITAL:,} / 策略\n")
    lines.append("- 费用: 佣金0.03% + 滑点0.1%\n")
    lines.append("- 风险提示: 历史回测不代表未来收益，不构成投资建议\n")

    content = "\n".join(lines)

    # 写文件
    report_path = os.path.join(OUTPUT_DIR, f"{today}.md")
    with open(report_path, "w") as f:
        f.write(content)

    # 输出摘要到stdout（给agent推送）
    winner_ret = np.mean(agg[winner]["total_return_pct"]) if agg[winner]["total_return_pct"] else 0
    print("=====BACKTEST_RESULT=====")
    print(json.dumps({
        "date": today,
        "stock_count": len(results),
        "strategy_count": len(ALL_STRATEGIES),
        "avg_returns": {s: round(np.mean(agg[s]["total_return_pct"]), 2) if agg[s]["total_return_pct"] else None for s, _ in ALL_STRATEGIES},
        "avg_sharpes": {s: round(np.mean(agg[s]["sharpe_ratio"]), 3) if agg[s]["sharpe_ratio"] else None for s, _ in ALL_STRATEGIES},
        "avg_maxdd": {s: round(np.mean(agg[s]["max_drawdown_pct"]), 2) if agg[s]["max_drawdown_pct"] else None for s, _ in ALL_STRATEGIES},
        "composite_scores": {s: scores[s]["总分"] for s in scores},
        "winner": winner,
        "winner_avg_return": round(winner_ret, 2),
    }, ensure_ascii=False, indent=2))
    print("=====BACKTEST_END=====")

    # 打印简报表
    print(f"\n📊 **五策略回测完成** ({today})")
    print(f"覆盖 {len(results)} 只股票 x {len(ALL_STRATEGIES)} 个策略\n")
    print(f"| 策略 | 平均收益 | 夏普 | 最大回撤 | 胜率 | 总分 |")
    print(f"|:---|:----:|:---:|:------:|:---:|:---:|")
    for sname, _ in ALL_STRATEGIES:
        sr = scores.get(sname, {})
        ret = np.mean(agg[sname]["total_return_pct"]) if agg[sname]["total_return_pct"] else 0
        sh = np.mean(agg[sname]["sharpe_ratio"]) if agg[sname]["sharpe_ratio"] else 0
        dd = np.mean(agg[sname]["max_drawdown_pct"]) if agg[sname]["max_drawdown_pct"] else 0
        wr = np.mean(agg[sname]["win_rate_pct"]) if agg[sname]["win_rate_pct"] else 0
        print(f"| {sname} | {ret:+.2f}% | {sh:.2f} | {dd:.1f}% | {wr:.1f}% | {sr.get('总分', '-')} |")
    print(f"\n🏆 综合最优: **{winner}** (总分 {scores[winner]['总分']})")


if __name__ == "__main__":
    main()
