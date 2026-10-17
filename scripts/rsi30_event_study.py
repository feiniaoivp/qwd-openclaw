#!/usr/bin/env python3
"""RSI<30 左侧假设事件研究（2026-10-17，为可逆声明提供数据）。

问题: 五维技术维旧口径给 RSI<30 加 2 分（超卖是买点）。该假设是否有超额？
方法: 全池(analysis.service.WATCHLIST) 2023-01-01 起日线 → RSI(14, ewm com=13，与
trader_stock_picks 同式) → RSI<30 收盘事件 → 前瞻 +1/+3/+5/+10 交易日收益 →
对照同窗口无条件基线（全体 stock-day）。输出均值/中位/胜率/n + 逐票样本。
统计纪律: 09-15/09-19 教训——报 n 与基线对照，小样本不下强结论。
产出: analysis/research/2026-10-17_rsi30_event_study.json
"""
import os
import sys
import json
import logging

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.getLogger().setLevel(logging.ERROR)

import pandas as pd

from analysis.service import WATCHLIST, get_daily_hist

START = "20230101"
HORIZONS = (1, 3, 5, 10)
RSI_TH = 30.0


def rsi_series(close: pd.Series) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.ewm(com=13, adjust=False).mean()
    ma_down = down.ewm(com=13, adjust=False).mean()
    return 100 - (100 / (1 + ma_up / ma_down))


def fwd_stats(rets):
    if not rets:
        return None
    s = pd.Series(rets).dropna()
    if s.empty:
        return None
    return {
        "n": int(len(s)),
        "mean": round(float(s.mean()), 4),
        "median": round(float(s.median()), 4),
        "win_rate": round(float((s > 0).mean()), 4),
    }


def main():
    wl = list(WATCHLIST.items()) if isinstance(WATCHLIST, dict) else list(WATCHLIST)
    events = []          # 每事件: {symbol, date, rsi, fwd: {h: ret}}
    baseline = {h: [] for h in HORIZONS}   # 全体 stock-day 前瞻收益
    skipped = []

    for sym, name in wl:
        df = get_daily_hist(sym, name, start_date=START)
        if df is None or len(df) < 60:
            skipped.append(f"{name}({sym})")
            continue
        close = df["close"].astype(float).reset_index(drop=True)
        if "date" in df.columns:
            dates = df["date"].astype(str).reset_index(drop=True)
        elif "day" in df.columns:
            dates = df["day"].astype(str).reset_index(drop=True)
        else:
            dates = pd.Series([f"row{i}" for i in range(len(df))])
        rsi = rsi_series(close)
        n = len(close)
        # 无条件基线（同一窗口、含warmup后全部天）
        for h in HORIZONS:
            if n > h:
                r = (close.shift(-h) / close - 1).iloc[h:-(h) if h else None] if False else (close.shift(-h) / close - 1).iloc[:-h]
                baseline[h].extend([x for x in r.dropna().tolist()])

        ev_idx = [i for i in range(15, n) if pd.notna(rsi.iloc[i]) and rsi.iloc[i] < RSI_TH]
        # 事件去重：5 日内连续超卖只记首次（避免同一波下跌重复计数）
        last = -10
        for i in ev_idx:
            if i - last < 5:
                continue
            last = i
            fwd = {}
            for h in HORIZONS:
                if i + h < n:
                    fwd[str(h)] = round(float(close.iloc[i + h] / close.iloc[i] - 1), 4)
            events.append({"symbol": sym, "name": name,
                           "date": str(dates.iloc[i]),
                           "rsi": round(float(rsi.iloc[i]), 1), "fwd": fwd})

    result = {
        "date": "2026-10-17",
        "window": f"{START}~now",
        "pool_size": len(wl),
        "skipped": skipped,
        "event_count": len(events),
        "event_fwd": {h: fwd_stats([e["fwd"][str(h)] for e in events if str(h) in e["fwd"]]) for h in HORIZONS},
        "baseline_fwd": {h: fwd_stats(baseline[h]) for h in HORIZONS},
        "events": events,
    }
    # 超额 = 事件均值 - 基线均值
    result["excess"] = {}
    for h in HORIZONS:
        ev, ba = result["event_fwd"][h], result["baseline_fwd"][h]
        result["excess"][h] = (round(ev["mean"] - ba["mean"], 4)
                               if ev and ba else None)

    out = os.path.join(WORKSPACE, "analysis/research/2026-10-17_rsi30_event_study.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print(f"[study] 跳过 {len(skipped)} 只: {skipped}")
    print(f"[study] RSI<30 事件数: {len(events)}（5日去重后）")
    for h in HORIZONS:
        ev, ba, ex = result["event_fwd"][h], result["baseline_fwd"][h], result["excess"][h]
        if ev and ba:
            print(f"[study] +{h}d: 事件均值 {ev['mean']:+.2%} (n={ev['n']}, 胜率 {ev['win_rate']:.0%}) "
                  f"| 基线 {ba['mean']:+.2%} | 超额 {ex:+.2%}")
    print(f"[study] 已写 {out}")


if __name__ == "__main__":
    main()
