#!/usr/bin/env python3
"""vp 维 |change_pct|>5 加分假设事件研究（2026-10-17，A1 收尾）。

背景: trader_stock_picks.score_volume_price 给 |change_pct|>5 加 2 分、>2 加 1 分
      （方向无关，涨跌都算「量价活跃」）。该假设 10-17 下午因零样本挂起，本脚本用
      历史日线补验: 大波动日之后是否有正的前瞻超额（动量自增强）？还是纯噪声？
方法: 全池(analysis.service.WATCHLIST) 2023-01-01 起日线 → |chg|>5% 收盘事件
      （5 日去重，同 rsi30_event_study）→ 前瞻 +1/+3/+5/+10 收益 vs 同窗口无条件基线
      + 同年基线（10-17 方法论教训: 全期基线被池子漂移/行情年污染，分年超额必须同年算）。
产出: analysis/research/2026-10-17_vp_change_event_study.json
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
TH = 5.0        # |change_pct| > 5
DEDUP = 5       # 事件 5 日去重


def fwd_stats(rets):
    if not rets:
        return None
    s = pd.Series(rets, dtype=float).dropna()
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
    events = []
    baseline = {h: [] for h in HORIZONS}          # 全期无条件
    baseline_year = {h: {} for h in HORIZONS}     # 同年基线 year -> h -> [rets]
    skipped = []

    for sym, name in wl:
        df = get_daily_hist(sym, name, start_date=START)
        if df is None or len(df) < 60:
            skipped.append(f"{name}({sym})")
            continue
        close = df["close"].astype(float).reset_index(drop=True)
        if "date" in df.columns:
            dates = pd.to_datetime(df["date"].astype(str)).reset_index(drop=True)
        elif "day" in df.columns:
            dates = pd.to_datetime(df["day"].astype(str)).reset_index(drop=True)
        else:
            dates = pd.Series([pd.NaT] * len(df))
        chg = close.pct_change() * 100.0
        n = len(close)

        # 无条件基线（全窗口 + 分年）
        for h in HORIZONS:
            r = (close.shift(-h) / close - 1).iloc[:-h]
            yrs = dates.dt.year
            for i, v in r.items():
                if pd.isna(v):
                    continue
                baseline[h].append(float(v))
                y = int(yrs.iloc[i])
                baseline_year[h].setdefault(y, []).append(float(v))

        # 事件: |chg|>5，5 日去重
        last = -10
        for i in range(15, n):
            c = chg.iloc[i]
            if pd.isna(c) or abs(c) <= TH:
                continue
            if i - last < DEDUP:
                continue
            last = i
            fwd = {}
            for h in HORIZONS:
                if i + h < n:
                    fwd[str(h)] = round(float(close.iloc[i + h] / close.iloc[i] - 1), 4)
            events.append({
                "symbol": sym, "name": name,
                "date": str(dates.iloc[i].date()) if pd.notna(dates.iloc[i]) else f"row{i}",
                "year": int(dates.iloc[i].year) if pd.notna(dates.iloc[i]) else None,
                "chg_pct": round(float(c), 2),
                "fwd": fwd,
            })

    result = {
        "date": "2026-10-17",
        "window": f"{START}~now",
        "rule": f"abs(change_pct)>{TH:.0f} at close, {DEDUP}d dedup",
        "pool_size": len(wl),
        "skipped": skipped,
        "event_count": len(events),
        "event_fwd": {str(h): fwd_stats([e["fwd"][str(h)] for e in events if str(h) in e["fwd"]])
                      for h in HORIZONS},
        "baseline_fwd": {str(h): fwd_stats(baseline[h]) for h in HORIZONS},
        "excess_full": {},   # 全期超额（已知会被行情年污染，仅对照）
        "by_year": {},       # 分年: 事件超额 vs 同年基线（本研究的裁决口径）
        "events": events,
    }

    for h in HORIZONS:
        ev, ba = result["event_fwd"][str(h)], result["baseline_fwd"][str(h)]
        result["excess_full"][str(h)] = (
            round(ev["mean"] - ba["mean"], 4) if ev and ba else None)

    years = sorted({e["year"] for e in events if e["year"]})
    for y in years:
        entry = {}
        for h in HORIZONS:
            ev_rets = [e["fwd"][str(h)] for e in events
                       if e["year"] == y and str(h) in e["fwd"]]
            ev = fwd_stats(ev_rets)
            ba = fwd_stats(baseline_year[h].get(y, []))
            entry[str(h)] = {
                "event": ev,
                "baseline_same_year": ba,
                "excess": (round(ev["mean"] - ba["mean"], 4)
                           if ev and ba and ev["n"] >= 5 and ba["n"] >= 30 else None),
            }
        result["by_year"][str(y)] = entry

    out = os.path.join(WORKSPACE, "analysis/research/2026-10-17_vp_change_event_study.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    # 控制台摘要（主结果先落盘再打印 —— 10-17 规约）
    print(f"[study] 跳过 {len(skipped)} 只 | 事件数 {len(events)}（{DEDUP}d 去重）")
    for h in HORIZONS:
        ev, ba, ex = (result["event_fwd"][str(h)], result["baseline_fwd"][str(h)],
                      result["excess_full"][str(h)])
        if ev and ba:
            print(f"[study] +{h}d 全期: 事件 {ev['mean']:+.2%} (n={ev['n']}, 胜率 {ev['win_rate']:.0%}) "
                  f"| 基线 {ba['mean']:+.2%} | 超额 {ex:+.2%}")
    print("[study] 分年超额（同年基线，裁决口径）:")
    for y in years:
        parts = []
        for h in HORIZONS:
            ex = result["by_year"][str(y)][str(h)]["excess"]
            nn = result["by_year"][str(y)][str(h)]["event"]["n"]
            parts.append(f"+{h}d {ex:+.2%}(n={nn})" if ex is not None else f"+{h}d n/a")
        print(f"  {y}: " + "  ".join(parts))
    print(f"[study] 已写 {out}")


if __name__ == "__main__":
    main()
