#!/usr/bin/env python3
"""
Trader stock picks module: simplified implementation of the top trader's stock selection logic.
Provides scoring and recommendation for watchlist stocks based on five dimensions:
chip structure, volume-price, fund flow, fundamentals, technical.
Falls back to default values when data unavailable.
"""

import os, json, time, math
from typing import List, Dict, Any
import pandas as pd

try:
    import akshare as ak
    _HAS_AKSHARE = True
except Exception:
    _HAS_AKSHARE = False

from analysis.service import get_daily_hist, get_spot_scan, is_trading_day

# Helper to safely fetch akshare data (fail fast)
# 2026-08 实测：本机 akshare 财务/资金流等东财接口被网络拦截，重试+sleep 只会白白拖慢
# (29只×多次重试→超 18s)。改为单次尝试、失败即返回 None，上层已做好优雅降级。
def _akshare_call(func, *args, **kwargs):
    if not _HAS_AKSHARE:
        return None
    try:
        return func(*args, **kwargs)
    except Exception:
        return None

def get_fundamentals(symbol: str) -> Dict[str, float]:
    """Fetch PE, PB, etc. Returns dict with keys pe, pb, roe, etc."""
    if not _HAS_AKSHARE:
        return {}
    # Try multiple akshare fundamentals interfaces
    # 1. stock_financial_analysis_indicator
    df = _akshare_call(ak.stock_financial_analysis_indicator, symbol=symbol)
    if df is not None and not df.empty:
        # Get latest row
        latest = df.iloc[-1]
        return {
            "pe": float(latest.get("市盈率", 0)) if latest.get("市盈率") else 0,
            "pb": float(latest.get("市净率", 0)) if latest.get("市净率") else 0,
            "roe": float(latest.get("净资产收益率", 0)) if latest.get("净资产收益率") else 0,
            "profit": float(latest.get("销售净利率", 0)) if latest.get("销售净利率") else 0,
        }
    # 2. stock_a_indicator_lg may not exist in some versions; try but ignore errors
    try:
        df = _akshare_call(ak.stock_a_indicator_lg, symbol=symbol)
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            return {
                "pe": float(latest.get("市盈率", 0)) if latest.get("市盈率") else 0,
                "pb": float(latest.get("市净率", 0)) if latest.get("市净率") else 0,
                "roe": float(latest.get("净资产收益率", 0)) if latest.get("净资产收益率") else 0,
            }
    except AttributeError:
        pass
    return {}

def get_fund_flow(symbol: str) -> Dict[str, float]:
    """Fetch main force net inflow, etc."""
    if not _HAS_AKSHARE:
        return {}
    # stock_individual_fund_flow
    df = _akshare_call(ak.stock_individual_fund_flow, symbol=symbol)
    if df is not None and not df.empty:
        latest = df.iloc[-1]
        return {
            "main_net_in": float(latest.get("主力净流入-净额", 0)) if latest.get("主力净流入-净额") else 0,
            "main_net_in_rate": float(latest.get("主力净流入-净占比", 0)) if latest.get("主力净流入-净占比") else 0,
            "super_net_in": float(latest.get("超大单净流入-净额", 0)) if latest.get("超大单净流入-净额") else 0,
            "big_net_in": float(latest.get("大单净流入-净额", 0)) if latest.get("大单净流入-净额") else 0,
            "medium_net_in": float(latest.get("中单净流入-净额", 0)) if latest.get("中单净流入-净额") else 0,
            "small_net_in": float(latest.get("小单净流入-净额", 0)) if latest.get("小单净流入-净额") else 0,
        }
    return {}

def get_technical_from_hist(symbol: str, name: str) -> Dict[str, Any]:
    """Compute technical indicators from historical data."""
    df = get_daily_hist(symbol, name, start_date="20260101")
    if df is None or len(df) < 30:
        return {}
    # Use existing calc_full_signal if available
    from analysis.service import calc_full_signal
    try:
        sig = calc_full_signal(df)
        # Expect keys: signal (dict with level, score, reasons, risks)
        return sig.get("signal", {})
    except Exception:
        # fallback to simple MACD/RSI
        close = df["close"]
        # MACD
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9).mean()
        macd = dif - dea
        # RSI
        delta = close.diff()
        up = delta.clip(lower=0)
        down = -delta.clip(upper=0)
        ma_up = up.ewm(com=13, adjust=False).mean()
        ma_down = down.ewm(com=13, adjust=False).mean()
        rs = ma_up / ma_down
        rsi = 100 - (100 / (1 + rs))
        latest = {
            "macd": macd.iloc[-1],
            "dif": dif.iloc[-1],
            "dea": dea.iloc[-1],
            "rsi": rsi.iloc[-1],
        }
        return latest

def score_chip_structure(symbol: str) -> float:
    """Chip structure score (0-10). Default medium."""
    # In reality we would look at institutional holder %, top 10 changes, etc.
    # Lacking data, return a neutral score.
    return 5.0

def score_volume_price(symbol: str, name: str, spot_item: dict) -> float:
    """Volume-price score based on turnover rate and price change."""
    score = 5.0  # base
    # Price change magnitude (absolute)
    change_pct = abs(spot_item.get("change_pct", 0))
    if change_pct > 5:
        score += 2
    elif change_pct > 2:
        score += 1
    if change_pct < 0.5:
        score -= 1
    # Turnover rate (if available)
    turnover = spot_item.get("turnover_rate")
    if turnover is not None:
        if turnover > 10:
            score += 2
        elif turnover > 5:
            score += 1
        elif turnover < 1:
            score -= 1
    # Clamp
    return max(0, min(10, score))

def score_fund_flow(symbol: str, name: str) -> float:
    """Fund flow score based on net main force inflow."""
    data = get_fund_flow(symbol)
    if not data:
        return 5.0
    main_net_in = data.get("main_net_in") or 0
    # Normalize roughly: assume +/- 1e8 is strong
    # Convert to 0-10 scale
    # Simple: if main_net_in > 0 => bullish, else bearish
    if main_net_in > 0:
        score = 5 + min(5, main_net_in / 20000000)  # 每 2000万 加 1 分，上限 10
    else:
        score = 5 - min(5, abs(main_net_in) / 20000000)
    return max(0, min(10, score))

def score_fundamentals(symbol: str, name: str) -> float:
    """Fundamentals score based on PE, PB, ROE."""
    data = get_fundamentals(symbol)
    if not data:
        return 5.0
    pe = data.get("pe") or 0
    pb = data.get("pb") or 0
    roe = data.get("roe") or 0   # 修复：原代码 key 写成 "roe, 0" 导致恒为 None 且比较崩溃
    score = 5.0
    # PE lower better (but not too low)
    if 0 < pe < 15:
        score += 2
    elif pe < 0:
        score -= 2  # loss
    elif pe > 50:
        score -= 2
    # PB lower better
    if 0 < pb < 3:
        score += 1.5
    elif pb > 10:
        score -= 1.5
    # ROE higher better
    if roe > 15:
        score += 1.5
    elif roe < 5:
        score -= 1
    return max(0, min(10, score))

def score_technical(symbol: str, name: str) -> float:
    """Technical score based on MACD, RSI, etc from historical signal."""
    tech = get_technical_from_hist(symbol, name)
    if not tech:
        return 5.0
    score = 5.0
    # MACD: dif > dea => bullish
    dif = tech.get("dif") or 0
    dea = tech.get("dea") or 0
    if dif > dea:
        score += 2
    else:
        score -= 2
    # RSI: 30-70 neutral, <30 oversold => bullish, >70 overbought => bearish
    rsi = tech.get("rsi") or 50
    if rsi < 30:
        score += 2
    elif rsi > 70:
        score -= 2
    # MACD histogram rising?
    macd = tech.get("macd", 0)
    # Not stored, compute from dif/dea if available
    # We'll approximate: if dif > 0 and dea > 0 and dif > dea? Already captured.
    return max(0, min(10, score))

def get_trader_picks(watchlist: List[tuple]) -> List[Dict[str, Any]]:
    """
    Return list of stock picks sorted by total score.
    Each item: {
        "symbol": str,
        "name": str,
        "total_score": float,
        "chip": float,
        "volume_price": float,
        "fund_flow": float,
        "fundamentals": float,
        "technical": float,
        "stage": str,  # e.g., "吸筹", "洗盘末期", "拉升初期"
        "catalyst": str,
        "advice": str  # BUY/SELL/HOLD with reason
    }
    """
    results = []
    for symbol, name in watchlist:
        chip = score_chip_structure(symbol)
        vol_price = score_volume_price(symbol, name, {})  # we'll update spot later
        fund_flow = score_fund_flow(symbol, name)
        fundamentals = score_fundamentals(symbol, name)
        technical = score_technical(symbol, name)
        # 数据缺失时可能得 None，统一回退 0 防止 NoneType 崩溃
        chip = chip or 0.0
        vol_price = vol_price or 0.0
        fund_flow = fund_flow or 0.0
        fundamentals = fundamentals or 0.0
        technical = technical or 0.0
        total = chip + vol_price + fund_flow + fundamentals + technical
        # Determine stage based on scores (simplified)
        stage = "中性"
        if technical > 6 and fund_flow > 6:
            stage = "拉升初期"
        elif technical < 4 and fund_flow < 4:
            stage = "洗盘末期"
        elif chip > 7 and vol_price > 6:
            stage = "吸筹"
        else:
            stage = "观察"
        # Catalyst placeholder
        catalyst = "待定"
        # Advice based on total score
        if total >= 35:
            action = "BUY"
            reason = "综合评分较高，具备启动条件"
        elif total <= 20:
            action = "SELL"
            reason = "综合评分偏低，注意风险"
        else:
            action = "HOLD"
            reason = "综合评分中性，建议观望"
        results.append({
            "symbol": symbol,
            "name": name,
            "total_score": round(total, 2),
            "chip": round(chip, 2),
            "volume_price": round(vol_price, 2),
            "fund_flow": round(fund_flow, 2),
            "fundamentals": round(fundamentals, 2),
            "technical": round(technical, 2),
            "stage": stage,
            "catalyst": catalyst,
            "advice": f"{action}: {reason}",
        })
    # Sort by total_score descending
    results.sort(key=lambda x: x["total_score"], reverse=True)
    # Update volume_price with actual spot data for better accuracy
    spot_data, _ = get_spot_scan()
    spot_map = {item["symbol"]: item for item in spot_data if "symbol" in item}
    for res in results:
        sym = res["symbol"]
        if sym in spot_map:
            spot_item = spot_map[sym]
            res["volume_price"] = round(score_volume_price(sym, res["name"], spot_item), 2)
            # Recalculate total
            res["total_score"] = round(
                res["chip"] + res["volume_price"] + res["fund_flow"] +
                res["fundamentals"] + res["technical"], 2
            )
    # Re-sort after update
    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results

if __name__ == "__main__":
    # Example usage with a small watchlist
    from analysis.service import WATCHLIST
    picks = get_trader_picks(WATCHLIST[:5])  # test with first 5
    for p in picks:
        print(p)