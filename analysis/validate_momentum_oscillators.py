#!/usr/bin/env python3
"""
回测验证 ⑩⑪：动量-震荡确认指标白名单扩充
==========================================
对比四种策略在 30 只池 × 4 周期的边际增量：

A. 基准：现有布林带+ATR（上轨突破入场 + ATR止损）
B. 涡流VI+布林挤压（挤压前置 + 突破确认 + VI方向过滤）
C. RVI+布林挤压（挤压前置 + 突破确认 + RVI金叉过滤）
D. 纯价格结构+量能（挤压前置 + 突破确认 + 量能放大过滤，无振荡指标）

周期窗口：
- W1: 全量（2020-01-01 ~ 2026-09-10）
- W2: 近3年（2023-09-10 ~ 2026-09-10）
- W3: 近1.5年（2025-03-10 ~ 2026-09-10）
- W4: 近1年（2025-09-10 ~ 2026-09-10）

稳定性评分 = 夏普×60 + 年化收益×0.6 - 样本量罚分 - 最大回撤罚分
置信度护栏：仅当 得分>=25 且 夏普>=0.25 才建议采纳
"""

import os, sys, json, warnings, traceback, time
from datetime import datetime, timedelta
import pandas as pd
import pandas_ta as ta
import numpy as np

# Baostock session
WORKSPACE = "/Users/duguke/.openclaw/workspace"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)
from analysis.bs_session import ensure_login, logout as bs_logout, query_history_k_data_plus_retry

warnings.filterwarnings("ignore")

OUTPUT_DIR = os.path.join(WORKSPACE, "analysis", "backtest", "momentum_oscillator_validation")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── 30只关注股（同步 memory/watchlist.md + 电力设备）──
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
    # 电网装备/特高压 (8)
    ("600089","特变电工"), ("600406","国电南瑞"), ("000400","许继电气"), ("601179","中国西电"),
    ("002028","思源电气"), ("002270","华明装备"), ("002130","沃尔核材"), ("600312","平高电气"),
]

# ── 回测参数 ──
WINDOWS = {
    "W1_全量": "20200101",
    "W2_近3年": "20230910",
    "W3_近1.5年": "20250310",
    "W4_近1年": "20250910",
}
END_DATE = datetime.now().strftime("%Y%m%d")
INITIAL_CAPITAL = 100_000
COMMISSION = 0.0003
SLIPPAGE = 0.001

# ════════════════════════════════════════
# 指标计算工具
# ════════════════════════════════════════

def add_indicators(df):
    """为 DataFrame 添加所有需要的指标"""
    df = df.copy()
    
    # 布林带
    bb = ta.bbands(df["close"], length=20, std=2)
    # 动态列名匹配
    bbu_col = [c for c in bb.columns if "BBU" in c.upper()][0]
    bbm_col = [c for c in bb.columns if "BBM" in c.upper()][0]
    bbl_col = [c for c in bb.columns if "BBL" in c.upper()][0]
    bbw_col = [c for c in bb.columns if "BBB" in c.upper()][0]
    bbp_col = [c for c in bb.columns if "BBP" in c.upper()][0]
    df["BBU"] = bb[bbu_col]
    df["BBM"] = bb[bbm_col]
    df["BBL"] = bb[bbl_col]
    df["BBW"] = bb[bbw_col]  # Bandwidth
    df["BBP"] = bb[bbp_col]  # Percent B
    
    # ATR
    df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    
    # 涡流指标 VI
    vi = ta.vortex(df["high"], df["low"], df["close"], length=14)
    df["VI_P"] = vi["VTXP_14"]
    df["VI_M"] = vi["VTXM_14"]
    
    # RVI (Relative Vigor Index)
    df["RVI"] = ta.rvi(df["close"], length=14)
    df["RVI_SIG"] = ta.sma(df["RVI"], length=4)  # 4-period signal line
    
    # 成交量
    df["VOL_SMA20"] = ta.sma(df["volume"], length=20)
    df["VOL_RATIO"] = df["volume"] / df["VOL_SMA20"]
    
    # 布林带挤压：带宽在 120日 (约6个月) 新低附近
    df["BBW_LOW_120"] = df["BBW"].rolling(120).min()
    df["SQUEEZE_CURRENT"] = df["BBW"] <= df["BBW_LOW_120"] * 1.05  # 当前处于挤压
    # 关键修正：突破发生在挤压结束后，用"过去10日曾有挤压"作为前置条件
    df["SQUEEZE_RECENT"] = df["SQUEEZE_CURRENT"].rolling(10).max() == 1
    
    return df


# ════════════════════════════════════════
# 四个策略实现
# ════════════════════════════════════════

def strategy_A_bollinger_atr(df):
    """
    A. 基准：现有布林带+ATR
    买入：收盘价上穿布林带上轨
    卖出：收盘价下穿布林带中轨 或 ATR止损
    """
    df = add_indicators(df)
    actions = []
    position = False
    entry_price = 0
    atr_mult = 1.5

    for i in range(30, len(df)):  # 需要足够历史计算指标
        row = df.iloc[i]
        prev = df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])

        # ATR止损检查
        if position:
            stop_loss = entry_price - atr_mult * float(row["ATR"])
            if price < stop_loss:
                actions.append({"date": dt, "type": "SELL", "price": price,
                                "reason": f"ATR止损: 入场¥{entry_price:.2f} 止损¥{stop_loss:.2f}"})
                position = False
                entry_price = 0
                continue

        # 买入：突破上轨
        if not position:
            if float(prev["close"]) <= float(prev["BBU"]) and float(row["close"]) > float(row["BBU"]):
                actions.append({"date": dt, "type": "BUY", "price": price,
                                "reason": f"布林带上轨突破(上轨¥{float(row['BBU']):.2f})"})
                position = True
                entry_price = price

        # 卖出：跌破中轨
        else:
            if float(prev["close"]) >= float(prev["BBM"]) and float(row["close"]) < float(row["BBM"]):
                actions.append({"date": dt, "type": "SELL", "price": price,
                                "reason": f"跌破布林带中轨(中轨¥{float(row['BBM']):.2f})"})
                position = False
                entry_price = 0

    return actions


def strategy_B_vi_squeeze(df):
    """
    B. 涡流VI + 布林挤压（修正版）
    买入条件（三条同满足）：
    1. 近期曾有布林带挤压 (SQUEEZE_RECENT=True，过去10日内曾挤压)
    2. 实体收于上轨外 (close > BBU)
    3. VI+ > VI- 且间距扩大 (VI_P > VI_M 且 VI_P - VI_M > 前一日)
    卖出条件：
    - VI+ 跌破 VI- (反向交叉)
    - 或价格回到中轨另一侧 (close < BBM 做多时)
    - 止损：前一局部低点 或 中轨
    """
    df = add_indicators(df)
    actions = []
    position = False
    entry_price = 0
    entry_low = 0  # 入场前的局部低点
    
    # 预计算局部低点/高点 (简化：用前5日最低/最高)
    df["LOCAL_LOW_5"] = df["low"].rolling(5).min().shift(1)
    df["LOCAL_HIGH_5"] = df["high"].rolling(5).max().shift(1)

    for i in range(130, len(df)):  # 需要120日挤压计算 + 指标预热
        row = df.iloc[i]
        prev = df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])
        close = float(row["close"])
        open_ = float(row["open"])
        bb_up = float(row["BBU"])
        bb_mid = float(row["BBM"])
        vi_p = float(row["VI_P"])
        vi_m = float(row["VI_M"])
        squeeze = bool(row["SQUEEZE_RECENT"])  # 修正：用近期挤压
        local_low = float(row["LOCAL_LOW_5"]) if not pd.isna(row["LOCAL_LOW_5"]) else 0
        local_high = float(row["LOCAL_HIGH_5"]) if not pd.isna(row["LOCAL_HIGH_5"]) else 0
        prev_vi_p = float(prev["VI_P"])
        prev_vi_m = float(prev["VI_M"])

        # VI 间距扩大
        vi_gap_expanding = (vi_p - vi_m) > (prev_vi_p - prev_vi_m)
        vi_bullish = vi_p > vi_m
        vi_bearish = vi_m > vi_p
        vi_cross_down = prev_vi_p > prev_vi_m and vi_p < vi_m  # 多头转空头
        vi_cross_up = prev_vi_m > prev_vi_p and vi_m < vi_p    # 空头转多头

        # 实体收于轨外
        bullish_candle = close > open_ and close > bb_up
        bearish_candle = close < open_ and close < float(row["BBL"])

        if not position:
            # 做多条件
            if squeeze and bullish_candle and vi_bullish and vi_gap_expanding:
                actions.append({"date": dt, "type": "BUY", "price": price,
                                "reason": f"挤压突破+VI确认: 近期挤压={squeeze}, 收上轨外={bullish_candle}, VI+>{vi_p:.1f}>VI-{vi_m:.1f}, 间距扩大={vi_gap_expanding}"})
                position = True
                entry_price = price
                entry_low = local_low if local_low > 0 else price * 0.98  # 兜底
            # 做空条件 (可选，当前仅做多)
            
        else:
            # 止损：前一局部低点 或 中轨
            stop_loss = max(local_low, bb_mid) if local_low > 0 else bb_mid
            
            # 离场条件
            exit_reason = None
            if vi_cross_down:
                exit_reason = f"VI反向交叉: VI+({vi_p:.1f})跌破VI-({vi_m:.1f})"
            elif price < bb_mid:
                exit_reason = f"回到中轨下方: 价格¥{price:.2f} < 中轨¥{bb_mid:.2f}"
            elif price < stop_loss:
                exit_reason = f"结构止损: 价格¥{price:.2f} < 止损¥{stop_loss:.2f}(局部低{local_low:.2f}/中轨{bb_mid:.2f})"
            
            if exit_reason:
                actions.append({"date": dt, "type": "SELL", "price": price, "reason": exit_reason})
                position = False
                entry_price = 0

    return actions


def strategy_C_rvi_squeeze(df):
    """
    C. RVI + 布林挤压（修正版）
    买入条件：
    1. 近期曾有布林带挤压 (SQUEEZE_RECENT=True)
    2. 实体收于上轨外
    3. RVI 金叉 (RVI 上穿 RVI_SIG) 且 RVI > 50
    卖出条件：
    - RVI 死叉
    - 价格回到中轨下方
    - 结构止损
    """
    df = add_indicators(df)
    actions = []
    position = False
    entry_price = 0
    
    df["LOCAL_LOW_5"] = df["low"].rolling(5).min().shift(1)
    df["LOCAL_HIGH_5"] = df["high"].rolling(5).max().shift(1)

    for i in range(130, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])
        close = float(row["close"])
        open_ = float(row["open"])
        bb_up = float(row["BBU"])
        bb_mid = float(row["BBM"])
        rvi = float(row["RVI"])
        rvi_sig = float(row["RVI_SIG"])
        squeeze = bool(row["SQUEEZE_RECENT"])  # 修正：用近期挤压
        local_low = float(row["LOCAL_LOW_5"]) if not pd.isna(row["LOCAL_LOW_5"]) else 0
        prev_rvi = float(prev["RVI"])
        prev_rvi_sig = float(prev["RVI_SIG"])

        # RVI 金叉/死叉
        rvi_cross_up = prev_rvi <= prev_rvi_sig and rvi > rvi_sig
        rvi_cross_down = prev_rvi >= prev_rvi_sig and rvi < rvi_sig
        rvi_bullish = rvi > 50

        bullish_candle = close > open_ and close > bb_up

        if not position:
            if squeeze and bullish_candle and rvi_cross_up and rvi_bullish:
                actions.append({"date": dt, "type": "BUY", "price": price,
                                "reason": f"挤压突破+RVI金叉: 近期挤压={squeeze}, RVI({rvi:.1f})上穿信号({rvi_sig:.1f}), RVI>50={rvi_bullish}"})
                position = True
                entry_price = price
        else:
            stop_loss = max(local_low, bb_mid) if local_low > 0 else bb_mid
            exit_reason = None
            if rvi_cross_down:
                exit_reason = f"RVI死叉: RVI({rvi:.1f})跌破信号({rvi_sig:.1f})"
            elif price < bb_mid:
                exit_reason = f"回到中轨下方"
            elif price < stop_loss:
                exit_reason = f"结构止损¥{stop_loss:.2f}"
            
            if exit_reason:
                actions.append({"date": dt, "type": "SELL", "price": price, "reason": exit_reason})
                position = False
                entry_price = 0

    return actions


def strategy_D_price_volume(df):
    """
    D. 纯价格结构 + 量能（白名单简化版，修正版）
    无任何振荡指标，仅用：
    1. 近期曾有布林带挤压 (SQUEEZE_RECENT)
    2. 实体收于轨外
    3. 成交量放大 (VOL_RATIO > 1.5)
    卖出：
    - 价格回到中轨下方
    - 结构止损 (前局部低点/中轨)
    - 量能萎缩确认 (可选)
    """
    df = add_indicators(df)
    actions = []
    position = False
    entry_price = 0
    
    df["LOCAL_LOW_5"] = df["low"].rolling(5).min().shift(1)

    for i in range(130, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])
        close = float(row["close"])
        open_ = float(row["open"])
        bb_up = float(row["BBU"])
        bb_mid = float(row["BBM"])
        squeeze = bool(row["SQUEEZE_RECENT"])  # 修正：用近期挤压
        vol_ratio = float(row["VOL_RATIO"])
        local_low = float(row["LOCAL_LOW_5"]) if not pd.isna(row["LOCAL_LOW_5"]) else 0

        bullish_candle = close > open_ and close > bb_up
        vol_surge = vol_ratio > 1.5

        if not position:
            if squeeze and bullish_candle and vol_surge:
                actions.append({"date": dt, "type": "BUY", "price": price,
                                "reason": f"挤压突破+量能: 近期挤压={squeeze}, 收上轨外, 量比={vol_ratio:.2f}"})
                position = True
                entry_price = price
        else:
            stop_loss = max(local_low, bb_mid) if local_low > 0 else bb_mid
            exit_reason = None
            if price < bb_mid:
                exit_reason = f"回到中轨下方"
            elif price < stop_loss:
                exit_reason = f"结构止损¥{stop_loss:.2f}"
            
            if exit_reason:
                actions.append({"date": dt, "type": "SELL", "price": price, "reason": exit_reason})
                position = False
                entry_price = 0

    return actions


# ════════════════════════════════════════
# 统一回测模拟器
# ════════════════════════════════════════

def run_simulation(df, actions):
    """根据交易信号模拟真实交易"""
    capital = float(INITIAL_CAPITAL)
    shares = 0
    entry_price = 0
    trades = []
    equity_curve = []

    action_map = {}
    for a in actions:
        action_map[a["date"]] = a

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

    # 期末平仓
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

    # 绩效指标
    eq_df = pd.DataFrame(equity_curve)
    eq_series = eq_df["equity"].values

    if len(eq_series) < 2:
        return {"error": "数据不足"}

    total_return = ((eq_series[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    days = len(eq_series)
    years = days / 245
    annualized_return = ((1 + total_return / 100) ** (1 / years) - 1) * 100 if years > 0 else 0
    daily_returns = np.diff(eq_series) / eq_series[:-1]
    annualized_vol = np.std(daily_returns, ddof=1) * np.sqrt(245) * 100
    risk_free = 0.02
    sharpe = ((annualized_return / 100) - risk_free) / (annualized_vol / 100) if annualized_vol > 0 else 0
    peak = np.maximum.accumulate(eq_series)
    drawdown = (eq_series - peak) / peak * 100
    max_drawdown = drawdown.min()

    closed_trades = [t for t in trades if t["type"].startswith("SELL")]
    wins = [t for t in closed_trades if t.get("pnl", 0) > 0]
    win_rate = len(wins) / len(closed_trades) * 100 if closed_trades else 0
    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
    losses = [t for t in closed_trades if t.get("pnl", 0) <= 0]
    avg_loss = abs(np.mean([t["pnl"] for t in losses])) if losses else 1
    profit_ratio = avg_win / avg_loss if avg_loss > 0 else 0

    streak = 0
    max_streak = 0
    for t in trades:
        if t["type"].startswith("SELL"):
            if t.get("pnl", 0) <= 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0

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


def fetch_data(symbol, name, start, end, max_retry=3):
    """获取前复权日线数据"""
    if symbol.startswith("6"):
        bs_code = f"sh.{symbol}"
    elif symbol.startswith("9"):
        bs_code = f"sh.{symbol}"
    else:
        bs_code = f"sz.{symbol}"

    start_ymd = start[0:4] + "-" + start[4:6] + "-" + start[6:8]
    end_ymd = end[0:4] + "-" + end[4:6] + "-" + end[6:8]

    for attempt in range(max_retry):
        try:
            rs = query_history_k_data_plus_retry(
                bs_code,
                "date,open,close,high,low,volume",
                start_date=start_ymd, end_date=end_ymd,
                frequency="d", adjustflag="2",
                max_retries=1, wait_seconds=1.0
            )
            if rs is None:
                raise ValueError("查询失败")
            data = []
            max_rows = 5000
            while rs.next() and len(data) < max_rows:
                data.append(rs.get_row_data())
            if len(data) < 100:
                raise ValueError(f"数据不足: {len(data)}条")

            df = pd.DataFrame(data, columns=["date", "open", "close", "high", "low", "volume"])
            for col in ["open", "close", "high", "low", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            df = df.dropna()
            return df

        except Exception as e:
            time.sleep(2)

    return None


# ════════════════════════════════════════
# 主流程
# ════════════════════════════════════════

STRATEGIES = [
    ("A_布林带+ATR_基准", strategy_A_bollinger_atr),
    ("B_VI+挤压", strategy_B_vi_squeeze),
    ("C_RVI+挤压", strategy_C_rvi_squeeze),
    ("D_价格结构+量能", strategy_D_price_volume),
]

WINDOW_NAMES = list(WINDOWS.keys())

def main():
    today = datetime.now().strftime("%Y-%m-%d")
    
    if not ensure_login():
        print("❌ baostock登录失败")
        return

    print(f"🚀 动量-震荡指标验证回测 | {today} | {len(STOCKS)}只股票 × {len(WINDOWS)}窗口 × {len(STRATEGIES)}策略")
    print("=" * 80)

    # 结果结构: results[window][strategy][symbol] = metrics
    all_results = {wn: {sn: {} for sn, _ in STRATEGIES} for wn in WINDOW_NAMES}

    for wname, wstart in WINDOWS.items():
        print(f"\n📊 窗口: {wname} ({wstart} ~ {END_DATE})")
        for symbol, name in STOCKS:
            print(f"  [{symbol}] {name}...", end=" ", flush=True)
            df = fetch_data(symbol, name, wstart, END_DATE)
            if df is None or len(df) < 150:  # 需要足够数据计算120日挤压
                print(" 数据不足")
                for sn, _ in STRATEGIES:
                    all_results[wname][sn][symbol] = {"error": "数据不足"}
                continue

            for sname, sfunc in STRATEGIES:
                try:
                    actions = sfunc(df)
                    metrics = run_simulation(df, actions)
                    all_results[wname][sname][symbol] = metrics
                except Exception as e:
                    all_results[wname][sname][symbol] = {"error": str(e)}
            print(" ✅")

    bs_logout()

    # ── 统计汇总 ──
    print("\n" + "=" * 80)
    print("📈 汇总统计：各窗口 × 各策略平均指标")
    print("=" * 80)

    summary_lines = []
    summary_lines.append(f"# 动量-震荡指标白名单验证回测报告 {today}\n")
    summary_lines.append(f"股票池: {len(STOCKS)}只 | 窗口: {len(WINDOWS)} | 策略: {len(STRATEGIES)}\n")

    for wname in WINDOW_NAMES:
        summary_lines.append(f"\n## 窗口: {wname}\n")
        header = "| 指标 | A_布林带+ATR | B_VI+挤压 | C_RVI+挤压 | D_价格结构+量能 |"
        sep = "|" + "|".join([":-----"] * 5) + "|"
        summary_lines.append(header)
        summary_lines.append(sep)

        fields = ["total_return_pct", "annualized_return_pct", "annualized_volatility_pct",
                  "sharpe_ratio", "max_drawdown_pct", "win_rate_pct",
                  "profit_loss_ratio", "total_trades"]
        field_labels = ["平均收益%", "年化收益%", "年化波动%",
                        "夏普比率", "最大回撤%", "胜率%",
                        "盈亏比", "总交易次数"]

        for flabel, fname in zip(field_labels, fields):
            row = f"| {flabel} "
            for sname, _ in STRATEGIES:
                vals = []
                for symbol, _ in STOCKS:
                    sd = all_results[wname][sname].get(symbol, {})
                    if "error" not in sd:
                        val = sd.get(fname)
                        if val is not None:
                            vals.append(val)
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
            summary_lines.append(row)

    # ── 稳定性评分 & 推荐 ──
    summary_lines.append("\n\n## 稳定性评分 & 跨窗口一致性\n")
    summary_lines.append("评分公式：稳定性得分 = 夏普均值×60 + 年化收益均值×0.6 - 样本量罚分 - 回撤罚分\n")
    summary_lines.append("置信度护栏：得分≥25 且 夏普≥0.25 才建议采纳\n")

    for sname, _ in STRATEGIES:
        # 收集该策略在所有窗口的表现
        sharpe_vals = []
        ret_vals = []
        dd_vals = []
        trade_counts = []
        
        for wname in WINDOW_NAMES:
            for symbol, _ in STOCKS:
                sd = all_results[wname][sname].get(symbol, {})
                if "error" not in sd:
                    sharpe_vals.append(sd.get("sharpe_ratio", 0))
                    ret_vals.append(sd.get("annualized_return_pct", 0))
                    dd_vals.append(abs(sd.get("max_drawdown_pct", 0)))
                    trade_counts.append(sd.get("total_trades", 0))
        
        if sharpe_vals:
            avg_sharpe = np.mean(sharpe_vals)
            avg_ret = np.mean(ret_vals)
            avg_dd = np.mean(dd_vals)
            avg_trades = np.mean(trade_counts)
            
            # 样本量权重
            if avg_trades >= 10:
                w = 1.0
            elif avg_trades >= 6:
                w = 0.75
            elif avg_trades >= 3:
                w = 0.4
            else:
                w = 0.15
            
            # 评分
            score = (avg_sharpe * 60 + avg_ret * 0.6 - avg_dd * 0.2) * w
            sharpe_ok = avg_sharpe >= 0.25
            score_ok = score >= 25
            
            summary_lines.append(f"\n### {sname}")
            summary_lines.append(f"- 平均夏普: {avg_sharpe:.3f} | 平均年化收益: {avg_ret:.2f}% | 平均最大回撤: {avg_dd:.2f}% | 平均交易数: {avg_trades:.1f}")
            summary_lines.append(f"- 样本量权重: {w} | 稳定性得分: {score:.2f} | 夏普达标: {'✅' if sharpe_ok else '❌'} | 得分达标: {'✅' if score_ok else '❌'}")
            summary_lines.append(f"- **建议**: {'✅ 采纳' if (score_ok and sharpe_ok) else '❌ 不采纳/需优化'}")

    # ── 逐股 Top 表现 ──
    summary_lines.append("\n\n## 各策略收益 Top 5 (各窗口)\n")
    for wname in WINDOW_NAMES:
        summary_lines.append(f"\n### 窗口: {wname}\n")
        for sname, _ in STRATEGIES:
            ranked = []
            for symbol, name in STOCKS:
                sd = all_results[wname][sname].get(symbol, {})
                if "total_return_pct" in sd:
                    ranked.append((sd["total_return_pct"], name, symbol, sd))
            ranked.sort(key=lambda x: x[0], reverse=True)
            
            summary_lines.append(f"\n#### {sname}\n")
            summary_lines.append("| 排名 | 股票 | 收益% | 夏普 | 最大回撤% | 胜率% | 交易次数 |")
            summary_lines.append("|:---:|:---:|:----:|:----:|:--------:|:----:|:--------:|")
            for rank, (ret, name, sym, sd) in enumerate(ranked[:5], 1):
                sharpe = sd.get("sharpe_ratio", "-")
                mdd = sd.get("max_drawdown_pct", "-")
                wr = sd.get("win_rate_pct", "-")
                trades = sd.get("total_trades", "-")
                summary_lines.append(f"| {rank} | {name}({sym}) | {ret:+.2f}% | {sharpe} | {mdd}% | {wr}% | {trades} |")

    # 保存报告
    report_path = os.path.join(OUTPUT_DIR, f"validation_report_{today}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines))
    print(f"\n📄 报告已保存: {report_path}")

    # 保存原始数据 JSON
    json_path = os.path.join(OUTPUT_DIR, f"validation_raw_{today}.json")
    # 转换为可序列化格式
    serializable = {}
    for wname in WINDOW_NAMES:
        serializable[wname] = {}
        for sname, _ in STRATEGIES:
            serializable[wname][sname] = {}
            for symbol, _ in STOCKS:
                serializable[wname][sname][symbol] = all_results[wname][sname].get(symbol, {})
    
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    print(f"📄 原始数据已保存: {json_path}")

    # ── 关键结论输出 ──
    print("\n" + "=" * 80)
    print("🎯 关键结论：哪个确认指标值得加入白名单？")
    print("=" * 80)
    
    for sname, _ in STRATEGIES:
        sharpe_vals = []
        ret_vals = []
        for wname in WINDOW_NAMES:
            for symbol, _ in STOCKS:
                sd = all_results[wname][sname].get(symbol, {})
                if "error" not in sd:
                    sharpe_vals.append(sd.get("sharpe_ratio", 0))
                    ret_vals.append(sd.get("annualized_return_pct", 0))
        
        if sharpe_vals:
            avg_sharpe = np.mean(sharpe_vals)
            avg_ret = np.mean(ret_vals)
            print(f"  {sname}: 平均夏普={avg_sharpe:.3f}, 平均年化收益={avg_ret:.2f}%")

    # 相对基准 A 的边际增量
    print("\n📊 相对基准 A (布林带+ATR) 的边际增量：")
    baseline_sharpe = []
    baseline_ret = []
    for wname in WINDOW_NAMES:
        for symbol, _ in STOCKS:
            sd = all_results[wname]["A_布林带+ATR_基准"].get(symbol, {})
            if "error" not in sd:
                baseline_sharpe.append(sd.get("sharpe_ratio", 0))
                baseline_ret.append(sd.get("annualized_return_pct", 0))
    
    base_sharpe = np.mean(baseline_sharpe) if baseline_sharpe else 0
    base_ret = np.mean(baseline_ret) if baseline_ret else 0
    print(f"  基准 A: 夏普={base_sharpe:.3f}, 年化收益={base_ret:.2f}%")
    
    for sname, _ in STRATEGIES[1:]:  # B, C, D
        sharpe_vals = []
        ret_vals = []
        for wname in WINDOW_NAMES:
            for symbol, _ in STOCKS:
                sd = all_results[wname][sname].get(symbol, {})
                if "error" not in sd:
                    sharpe_vals.append(sd.get("sharpe_ratio", 0))
                    ret_vals.append(sd.get("annualized_return_pct", 0))
        
        if sharpe_vals:
            avg_sharpe = np.mean(sharpe_vals)
            avg_ret = np.mean(ret_vals)
            delta_sharpe = avg_sharpe - base_sharpe
            delta_ret = avg_ret - base_ret
            print(f"  {sname}: Δ夏普={delta_sharpe:+.3f}, Δ年化收益={delta_ret:+.2f}% {'🟢' if delta_sharpe>0.05 else '🔴' if delta_sharpe<-0.05 else '🟡'}")


if __name__ == "__main__":
    main()