#!/usr/bin/env python3
"""
电网装备/特高压出海板块专项回测
======================================================
目标：验证「核心产品+出海资质」双验证标的在该板块的历史策略有效性
对比三类策略：
  1. 趋势跟踪: EMA20/50/200多头排列 + MACD零轴上 + 回踩EMA20买入
  2. 事件驱动: 重大订单公告/中标/海外收入超预期 事件窗口交易
  3. 价值回归: 低估值(PE/PB历史分位) + 高股息 + 催化剂临近 买入

数据源：新浪日K (jsonp) → baostock 前复权兜底
回测区间：2022-01-01 至 2026-09-03 (约 4.5 年，覆盖特高压建设周期+出海周期)
输出：每只标的各策略收益/夏普/最大回撤/胜率/盈亏比 + 板块加权汇总
"""

from __future__ import annotations

import os
import sys
import json
import re
import time
import math
import logging
import argparse
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache

import pandas as pd
import numpy as np

try:
    import pandas_ta as ta
    HAS_PANDAS_TA = True
except Exception:
    HAS_PANDAS_TA = False

# 导入真实事件数据源
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))
try:
    from power_overseas_events import get_events_for_backtest
    HAS_REAL_EVENTS = True
except Exception as e:
    log.warning(f"真实事件数据源不可用: {e}")
    HAS_REAL_EVENTS = Falsee

try:
    import baostock as bs
    HAS_BAOSTOCK = True
except Exception:
    HAS_BAOSTOCK = False

# ============================================================================
# 配置
# ============================================================================

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
CONFIG_FILE = os.path.join(WORKSPACE, "data", "power_overseas_config.json")
RESULT_DIR = os.path.join(WORKSPACE, "analysis", "backtest", "power_overseas")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ============================================================================
# 数据获取
# ============================================================================

class DataFetcher:
    def __init__(self):
        self._bs_logged_in = False
        self._cache = {}
    
    def _ensure_bs_login(self):
        if self._bs_logged_in:
            return True
        try:
            lg = bs.login()
            if lg.error_code == "0":
                self._bs_logged_in = True
                return True
            log.error(f"Baostock登录失败: {lg.error_msg}")
        except Exception as e:
            log.error(f"Baostock登录异常: {e}")
        return False
    
    def fetch_sina_daily(self, symbol: str, n: int = 1500) -> Optional[pd.DataFrame]:
        """新浪日K jsonp (不复权)"""
        import urllib.request
        cache_key = f"sina_{symbol}_{n}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        pref = "sh" if symbol[0] in "69" else "sz"
        url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService."
               f"getKLineData?symbol={pref}{symbol}&scale=240&ma=no&datalen={n}")
        req = urllib.request.Request(
            url,
            headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
        )
        try:
            raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
        except Exception as e:
            log.warning(f"[SINA] {symbol} 失败: {e}")
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
        df = df.sort_values("date").reset_index(drop=True)
        if len(df) >= 2:
            self._cache[cache_key] = df
        return df
    
    def fetch_bs_daily_qfq(self, symbol: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """Baostock 前复权日K"""
        if not self._ensure_bs_login():
            return None
        try:
            rs = bs.query_history_k_data_plus(
                symbol, "date,code,open,high,low,close,volume,amount",
                start_date=start, end_date=end,
                frequency="d", adjustflag="2"  # 2=前复权
            )
            if rs.error_code != "0":
                return None
            data = []
            while rs.next():
                data.append(rs.get_row_data())
            if not data:
                return None
            df = pd.DataFrame(data, columns=rs.fields)
            for c in ["open", "high", "low", "close", "volume", "amount"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df
        except Exception as e:
            log.warning(f"[BS] {symbol} 失败: {e}")
            return None
    
    def get_daily(self, symbol: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """统一获取：优先新浪(不复权) -> Baostock前复权兜底"""
        # 先试新浪(不复权，但历史长、稳定)
        df = self.fetch_sina_daily(symbol, n=1500)
        if df is not None and len(df) >= 200:
            # 截取日期范围
            df = df[(df["date"] >= start) & (df["date"] <= end)]
            if len(df) >= 60:
                return df.reset_index(drop=True)
        
        # 兜底Baostock前复权
        df = self.fetch_bs_daily_qfq(symbol, start, end)
        if df is not None and len(df) >= 60:
            return df.reset_index(drop=True)
        
        return None

_fetcher = DataFetcher()

# ============================================================================
# 策略实现
# ============================================================================

def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算所有需要的技术指标"""
    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]
    
    # EMA系统
    df["ema20"] = ta.ema(close, length=20) if HAS_PANDAS_TA else close.ewm(span=20, adjust=False).mean()
    df["ema50"] = ta.ema(close, length=50) if HAS_PANDAS_TA else close.ewm(span=50, adjust=False).mean()
    df["ema200"] = ta.ema(close, length=200) if HAS_PANDAS_TA else close.ewm(span=200, adjust=False).mean()
    
    # MACD
    if HAS_PANDAS_TA:
        macd_df = ta.macd(close, fast=12, slow=26, signal=9)
        df["macd"] = macd_df.get("MACD_12_26_9", macd_df.get("MACD"))
        df["macd_signal"] = macd_df.get("MACDs_12_26_9", macd_df.get("MACD_SIGNAL"))
        df["macd_hist"] = macd_df.get("MACDh_12_26_9", macd_df.get("MACD_HIST"))
    else:
        ef = close.ewm(span=12, adjust=False).mean()
        es = close.ewm(span=26, adjust=False).mean()
        df["macd"] = ef - es
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]
    
    # ATR
    if HAS_PANDAS_TA:
        df["atr14"] = ta.atr(high, low, close, length=14)
    else:
        tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()
    
    # 成交量均线
    df["vol_ma20"] = vol.rolling(20).mean()
    df["vol_ratio"] = vol / df["vol_ma20"]
    
    # 价格相对位置
    df["dist_ema20"] = (close - df["ema20"]) / df["ema20"]
    df["dist_ema50"] = (close - df["ema50"]) / df["ema50"]
    
    # 趋势状态
    df["bull_arrange"] = (df["ema20"] > df["ema50"]) & (df["ema50"] > df["ema200"])
    df["bear_arrange"] = (df["ema20"] < df["ema50"]) & (df["ema50"] < df["ema200"])
    df["macd_above_zero"] = (df["macd"] > 0) & (df["macd_signal"] > 0)
    df["macd_cross_up"] = (df["macd"].shift(1) <= df["macd_signal"].shift(1)) & (df["macd"] > df["macd_signal"])
    df["macd_cross_down"] = (df["macd"].shift(1) >= df["macd_signal"].shift(1)) & (df["macd"] < df["macd_signal"])
    
    # 估值代理：用PB/PE历史分位需要基本面数据，这里用"距年线距离"代理
    df["dist_ema200"] = (close - df["ema200"]) / df["ema200"]
    df["dist_ema200_rank"] = df["dist_ema200"].rolling(250).rank(pct=True)
    
    return df

# -------- 策略1：趋势跟踪 --------
def strategy_trend_following(df: pd.DataFrame, params: dict = None) -> pd.DataFrame:
    """
    趋势跟踪策略：
    - 买入：多头排列 + MACD零轴上 + 回踩EMA20附近(距离<2%) + 量比>0.8
    - 卖出：跌破EMA50 或 MACD死叉 或 跌破ATR止损
    """
    p = params or {"ema20_dist": 0.02, "vol_ratio_min": 0.8, "atr_mult": 2.0}
    df = df.copy()
    df["signal"] = 0
    position = 0
    entry_price = 0
    stop_price = 0
    
    for i in range(1, len(df)):
        cur = df.iloc[i]
        prev = df.iloc[i-1]
        
        # 买入条件
        if position == 0:
            buy_cond = (
                cur["bull_arrange"] and
                cur["macd_above_zero"] and
                cur["dist_ema20"] >= -p["ema20_dist"] and  # 回踩EMA20附近
                cur["dist_ema20"] <= 0.03 and  # 不追高
                cur["vol_ratio"] >= p["vol_ratio_min"]
            )
            if buy_cond:
                position = 1
                entry_price = cur["close"]
                stop_price = entry_price - p["atr_mult"] * cur["atr14"]
                df.loc[df.index[i], "signal"] = 1
        
        # 卖出条件
        elif position == 1:
            sell_cond = (
                cur["close"] < cur["ema50"] or  # 跌破半年线
                cur["macd_cross_down"] or  # MACD死叉
                cur["close"] < stop_price  # ATR止损
            )
            if sell_cond:
                position = 0
                df.loc[df.index[i], "signal"] = -1
                stop_price = 0
            else:
                # 移动止损
                new_stop = cur["close"] - p["atr_mult"] * cur["atr14"]
                if new_stop > stop_price:
                    stop_price = new_stop
    
    return df

# -------- 策略2：事件驱动 --------
def strategy_event_driven(df: pd.DataFrame, events: List[dict], params: dict = None) -> pd.DataFrame:
    """
    事件驱动策略：
    - 事件类型：中标公告、海外收入超预期、重大合同签署、特高压核准利好
    - 买入：事件公告日 T+1 开盘 (或 T 日收盘若已知)
    - 持有：5-10 个交易日 (参数化)
    - 止损：-5% 硬止损
    """
    p = params or {"hold_days": 7, "stop_loss": -0.05}
    df = df.copy()
    df["signal"] = 0
    
    # 将事件日期转为交易日索引
    event_dates = [pd.to_datetime(e["date"]) for e in events if "date" in e]
    event_indices = []
    for ed in event_dates:
        idx = df[df["date"] >= ed].index
        if len(idx) > 0:
            event_indices.append(idx[0])  # 事件日或后一交易日
    
    for ei in event_indices:
        if ei + 1 >= len(df):
            continue
        # T+1 买入
        buy_idx = ei + 1
        df.loc[df.index[buy_idx], "signal"] = 1
        entry_price = df.iloc[buy_idx]["close"]
        # 持有期卖出
        sell_idx = min(buy_idx + p["hold_days"], len(df) - 1)
        # 检查止损
        for j in range(buy_idx + 1, sell_idx + 1):
            if (df.iloc[j]["close"] - entry_price) / entry_price <= p["stop_loss"]:
                sell_idx = j
                break
        df.loc[df.index[sell_idx], "signal"] = -1
    
    return df

# -------- 策略3：价值回归 --------
def strategy_value_reversion(df: pd.DataFrame, params: dict = None) -> pd.DataFrame:
    """
    价值回归策略：
    - 买入：距年线距离历史分位 < 20% (严重低估) + MACD底背离或RSI超卖 + 成交量萎缩
    - 卖出：距年线回归至分位 > 60% 或 MACD金叉后获利了结 或 止损-8%
    """
    p = params or {"dist_rank_buy": 0.2, "dist_rank_sell": 0.6, "stop_loss": -0.08}
    df = df.copy()
    df["signal"] = 0
    position = 0
    entry_price = 0
    
    for i in range(1, len(df)):
        cur = df.iloc[i]
        prev = df.iloc[i-1]
        
        if position == 0:
            # 简易底背离：价格创新低但MACD未创新低
            lookback = 20
            if i >= lookback:
                price_low = df["close"].iloc[i-lookback:i].min()
                macd_low = df["macd"].iloc[i-lookback:i].min()
                bull_div = (cur["close"] <= price_low * 1.01) and (cur["macd"] > macd_low * 1.05)
            else:
                bull_div = False
            
            buy_cond = (
                cur["dist_ema200_rank"] < p["dist_rank_buy"] and
                (bull_div or cur.get("rsi14", 50) < 30) and  # 需要RSI指标
                cur["vol_ratio"] < 0.8  # 缩量
            )
            if buy_cond:
                position = 1
                entry_price = cur["close"]
                df.loc[df.index[i], "signal"] = 1
        
        elif position == 1:
            sell_cond = (
                cur["dist_ema200_rank"] > p["dist_rank_sell"] or
                (cur["macd_cross_up"] and (cur["close"] - entry_price) / entry_price > 0.15) or
                (cur["close"] - entry_price) / entry_price <= p["stop_loss"]
            )
            if sell_cond:
                position = 0
                df.loc[df.index[i], "signal"] = -1
    
    return df

# ============================================================================
# 回测引擎
# ============================================================================

def run_backtest(df: pd.DataFrame, initial_capital: float = 100000,
                 commission: float = 0.0003, slippage: float = 0.001) -> dict:
    """运行回测，返回绩效指标"""
    df = df.copy()
    capital = initial_capital
    position = 0
    shares = 0
    entry_price = 0
    trades = []
    equity_curve = []
    
    for i in range(len(df)):
        cur = df.iloc[i]
        price = cur["close"]
        signal = cur.get("signal", 0)
        
        # 买入
        if signal == 1 and position == 0:
            cost = price * (1 + slippage)
            fee = capital * commission
            available = capital - fee
            shares = int(available / cost / 100) * 100
            if shares >= 100:
                total_cost = shares * cost + shares * cost * commission
                capital -= total_cost
                position = 1
                entry_price = price
                trades.append({"date": cur["date"], "type": "BUY", "price": price, "shares": shares})
        
        # 卖出
        elif signal == -1 and position == 1:
            proceeds = shares * price * (1 - slippage)
            fee = proceeds * commission
            net = proceeds - fee
            pnl = net - shares * entry_price * (1 + slippage) * (1 + commission)
            capital += net
            trades.append({"date": cur["date"], "type": "SELL", "price": price, "shares": shares, "pnl": round(pnl, 2)})
            position = 0
            shares = 0
            entry_price = 0
        
        # 权益曲线
        mkt_val = shares * price if position else 0
        equity_curve.append(capital + mkt_val)
    
    df["equity"] = equity_curve
    
    # 计算指标
    if len(trades) == 0:
        return {
            "total_return": 0, "annual_return": 0, "sharpe": 0,
            "max_drawdown": 0, "win_rate": 0, "profit_factor": 0,
            "num_trades": 0, "trades": [], "equity_curve": equity_curve
        }
    
    equity = pd.Series(equity_curve)
    total_return = (equity.iloc[-1] - initial_capital) / initial_capital
    days = (df["date"].iloc[-1] - df["date"].iloc[0]).days
    annual_return = (1 + total_return) ** (365 / max(1, days)) - 1 if days > 0 else 0
    
    # 夏普比率 (日度)
    daily_ret = equity.pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * math.sqrt(250) if daily_ret.std() > 0 else 0
    
    # 最大回撤
    peak = equity.expanding().max()
    drawdown = (equity - peak) / peak
    max_drawdown = drawdown.min()
    
    # 胜率/盈亏比
    sell_trades = [t for t in trades if t["type"] == "SELL"]
    wins = [t for t in sell_trades if t.get("pnl", 0) > 0]
    losses = [t for t in sell_trades if t.get("pnl", 0) <= 0]
    win_rate = len(wins) / len(sell_trades) if sell_trades else 0
    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
    avg_loss = abs(np.mean([t["pnl"] for t in losses])) if losses else 1
    profit_factor = avg_win / avg_loss if avg_loss > 0 else 0
    
    return {
        "total_return": round(total_return * 100, 2),
        "annual_return": round(annual_return * 100, 2),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(max_drawdown * 100, 2),
        "win_rate": round(win_rate * 100, 1),
        "profit_factor": round(profit_factor, 2),
        "num_trades": len(sell_trades),
        "trades": trades,
        "equity_curve": equity_curve
    }

def backtest_stock(symbol: str, name: str, config: dict,
                   start: str = "2022-01-01", end: str = "2026-09-03") -> dict:
    """单只股票三策略回测"""
    log.info(f"回测 {name}({symbol}) {start} ~ {end}")
    
    df = _fetcher.get_daily(symbol, start, end)
    if df is None or len(df) < 200:
        return {"symbol": symbol, "name": name, "error": "数据不足"}
    
    df = calc_indicators(df)
    
    results = {}
    
    # 策略1：趋势跟踪
    df1 = strategy_trend_following(df)
    results["trend_following"] = run_backtest(df1)
    results["trend_following"]["strategy"] = "trend_following"
    
    # 策略2：事件驱动 (使用真实公告事件数据)
    if HAS_REAL_EVENTS:
        real_events = get_events_for_backtest(symbol, start, end)
        log.info(f"  {name}({symbol}) 加载真实事件: {len(real_events)} 条")
    else:
        # 兜底：使用配置中的催化剂日历生成模拟事件
        real_events = []
        cal = config.get("catalyst_calendar", {})
        q_map = {"2022Q1": "2022-03-31", "2022Q2": "2022-06-30", "2022Q3": "2022-09-30", "2022Q4": "2022-12-31",
                 "2023Q1": "2023-03-31", "2023Q2": "2023-06-30", "2023Q3": "2023-09-30", "2023Q4": "2023-12-31",
                 "2024Q1": "2024-03-31", "2024Q2": "2024-06-30", "2024Q3": "2024-09-30", "2024Q4": "2024-12-31",
                 "2025Q1": "2025-03-31", "2025Q2": "2025-06-30", "2025Q3": "2025-09-30", "2025Q4": "2025-12-31",
                 "2026Q1": "2026-03-31", "2026Q2": "2026-06-30", "2026Q3": "2026-09-30", "2026Q4": "2026-12-31"}
        for quarter, evs in cal.items():
            for ev in evs:
                if quarter in q_map:
                    real_events.append({"date": q_map[quarter], "event": ev})
        log.info(f"  {name}({symbol}) 使用模拟事件: {len(real_events)} 条")
    
    df2 = strategy_event_driven(df, real_events)
    results["event_driven"] = run_backtest(df2)
    results["event_driven"]["strategy"] = "event_driven"
    
    # 策略3：价值回归
    df3 = strategy_value_reversion(df)
    results["value_reversion"] = run_backtest(df3)
    results["value_reversion"]["strategy"] = "value_reversion"
    
    # 买入持有基准
    bh_return = (df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0] * 100
    results["buy_hold"] = {"total_return": round(bh_return, 2), "strategy": "buy_hold"}
    
    return {"symbol": symbol, "name": name, **results}

# ============================================================================
# 主流程
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="电网装备出海板块专项回测")
    parser.add_argument("--start", default="2022-01-01", help="回测开始日期")
    parser.add_argument("--end", default="2026-09-03", help="回测结束日期")
    parser.add_argument("--symbols", nargs="+", help="指定股票代码(默认配置文件全量)")
    args = parser.parse_args()
    
    os.makedirs(RESULT_DIR, exist_ok=True)
    
    with open(CONFIG_FILE, encoding="utf-8") as f:
        config = json.load(f)
    
    stocks = config["stocks"]
    if args.symbols:
        stocks = {k: v for k, v in stocks.items() if k in args.symbols}
    
    all_results = {}
    summary_rows = []
    
    for code, cfg in stocks.items():
        res = backtest_stock(code, cfg["name"], cfg, args.start, args.end)
        all_results[code] = res
        
        if "error" in res:
            continue
        
        for strat_name in ["trend_following", "event_driven", "value_reversion", "buy_hold"]:
            if strat_name in res:
                r = res[strat_name]
                summary_rows.append({
                    "symbol": code, "name": cfg["name"],
                    "strategy": strat_name,
                    "total_return": r.get("total_return", 0),
                    "annual_return": r.get("annual_return", 0),
                    "sharpe": r.get("sharpe", 0),
                    "max_drawdown": r.get("max_drawdown", 0),
                    "win_rate": r.get("win_rate", 0),
                    "profit_factor": r.get("profit_factor", 0),
                    "num_trades": r.get("num_trades", 0)
                })
    
    # 汇总表
    summary_df = pd.DataFrame(summary_rows)
    if not summary_df.empty:
        # 板块加权平均 (按海外营收占比加权)
        weights = {code: cfg["revenue_overseas_pct"] for code, cfg in stocks.items()}
        summary_df["weight"] = summary_df["symbol"].map(weights).fillna(0.2)
        
        # 保存详细结果
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        detail_file = os.path.join(RESULT_DIR, f"backtest_detail_{timestamp}.json")
        with open(detail_file, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
        
        # 保存汇总表
        summary_file = os.path.join(RESULT_DIR, f"backtest_summary_{timestamp}.csv")
        summary_df.to_csv(summary_file, index=False, encoding="utf-8-sig")
        
        # 打印汇总
        print(f"\n{'='*80}")
        print(f"📊 电网装备/特高压出海板块回测汇总 ({args.start} ~ {args.end})")
        print(f"{'='*80}\n")
        
        for strat in ["trend_following", "event_driven", "value_reversion", "buy_hold"]:
            sub = summary_df[summary_df["strategy"] == strat]
            if sub.empty: continue
            w_avg_ret = np.average(sub["total_return"], weights=sub["weight"])
            w_avg_sharpe = np.average(sub["sharpe"], weights=sub["weight"])
            w_avg_mdd = np.average(sub["max_drawdown"], weights=sub["weight"])
            w_avg_wr = np.average(sub["win_rate"], weights=sub["weight"])
            print(f"【{strat.upper()}】")
            print(f"  加权总收益: {w_avg_ret:.2f}%  加权年化: {np.average(sub['annual_return'], weights=sub['weight']):.2f}%")
            print(f"  加权夏普: {w_avg_sharpe:.3f}  加权最大回撤: {w_avg_mdd:.2f}%  加权胜率: {w_avg_wr:.1f}%")
            print(f"  平均盈亏比: {np.average(sub['profit_factor'], weights=sub['weight']):.2f}  平均交易次数: {sub['num_trades'].mean():.0f}")
            print()
        
        # 最优策略映射
        print("【各标的最优策略】")
        for code in stocks.keys():
            sub = summary_df[summary_df["symbol"] == code]
            if sub.empty: continue
            best = sub.loc[sub["total_return"].idxmax()]
            print(f"  {code} {stocks[code]['name']}: {best['strategy']} (收益{best['total_return']:.1f}% 夏普{best['sharpe']:.2f})")
        
        print(f"\n✅ 详细结果: {detail_file}")
        print(f"✅ 汇总表: {summary_file}")

if __name__ == "__main__":
    main()