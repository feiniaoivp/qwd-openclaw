#!/usr/bin/env python3
"""One-off: 2026-10-08 trader picks with repaired technical dim (真实 RSI/MACD),
fund_flow explicitly excluded (moneyflow_em 双路失败), chip explicitly defaulted."""
import os, sys, json

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd
from analysis.service import WATCHLIST, get_daily_hist, get_spot_scan
from analysis.trader_stock_picks import score_fundamentals, score_volume_price


def technical_real(symbol: str, name: str):
    """真实 MACD/RSI 技术分（0-10），口径 = trader_stock_picks.score_technical 的 fallback 公式。"""
    df = get_daily_hist(symbol, name, start_date="20260101")
    if df is None or len(df) < 30:
        return None, None, None
    close = df["close"].astype(float)
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    dif = (ema12 - ema26)
    dea = dif.ewm(span=9).mean()
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.ewm(com=13, adjust=False).mean()
    ma_down = down.ewm(com=13, adjust=False).mean()
    rsi = (100 - (100 / (1 + ma_up / ma_down))).iloc[-1]
    score = 5.0
    if dif.iloc[-1] > dea.iloc[-1]:
        score += 2
    else:
        score -= 2
    if rsi < 30:
        score += 2
    elif rsi > 70:
        score -= 2
    return max(0, min(10, score)), round(float(dif.iloc[-1]), 3), round(float(rsi), 1)


if __name__ == "__main__":
    wl = list(WATCHLIST)
    if isinstance(wl, dict):
        wl = list(wl.items())
    spot_data, _ = get_spot_scan()
    spot_map = {it["symbol"]: it for it in spot_data if "symbol" in it}
    rows = []
    for symbol, name in wl:
        vp = score_volume_price(symbol, name, spot_map.get(symbol, {}))
        fund = score_fundamentals(symbol, name)
        tech, dif, rsi = technical_real(symbol, name)
        spot = spot_map.get(symbol, {})
        rows.append({
            "symbol": symbol, "name": name,
            "price": spot.get("price"), "change_pct": spot.get("change_pct"),
            "volume_price": vp, "fundamentals": fund,
            "technical": tech, "dif": dif, "rsi": rsi,
            # chip=5.0(设计默认无数据) fund_flow=None(双路失败) 均不计入
            "core_score": round((vp or 0) + (fund or 0) + (tech or 0), 2),
        })
    rows.sort(key=lambda r: r["core_score"], reverse=True)
    print("=====REPAIR_PICKS_BEGIN=====")
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    print("=====REPAIR_PICKS_END=====")
