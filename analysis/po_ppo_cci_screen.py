#!/usr/bin/env python3
"""关注池 PPO/CCI 量化筛选（a-share-analyst 技能 Workflow C）

数据源: baostock 前复权日K（近110交易日），本地CSV已滞后(10-08)故不使用。
输出: 每只股票 PPO/Signal/Histogram、CCI(20)、金叉/死叉、超买超卖分层。
"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd

from analysis.fib_extension_scan import WATCHLIST

CACHE = "/tmp/ppo_cci_cache_2026-10-13.csv"


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def fetch_all():
    if os.path.exists(CACHE):
        return pd.read_csv(CACHE)
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        print(f"baostock login failed: {lg.error_msg}", file=sys.stderr)
        sys.exit(1)
    frames = []
    for sym in WATCHLIST:
        rs = bs.query_history_k_data_plus(
            f"sh.{sym}" if sym.startswith(("6", "9")) else f"sz.{sym}",
            "date,close,high,low",
            start_date="2026-04-01", end_date="2026-10-13",
            frequency="d", adjustflag="2",  # 前复权
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        if rows:
            df = pd.DataFrame(rows, columns=["date", "close", "high", "low"])
            df["sym"] = sym
            frames.append(df)
    bs.logout()
    out = pd.concat(frames, ignore_index=True)
    out.to_csv(CACHE, index=False)
    return out


def compute(g):
    g = g.tail(110).reset_index(drop=True)
    close = g["close"].astype(float)
    high = g["high"].astype(float)
    low = g["low"].astype(float)
    if len(g) < 40 or str(g["date"].iloc[-1])[:10] < "2026-10-13":
        return {"date": str(g["date"].iloc[-1])[:10], "stale": True}
    ema12, ema26 = ema(close, 12), ema(close, 26)
    ppo = (ema12 - ema26) / ema26 * 100
    sig = ema(ppo, 9)
    hist = ppo - sig
    tp = (high + low + close) / 3
    sma = tp.rolling(20).mean()
    md = (tp - sma).abs().rolling(20).mean()
    cci = (tp - sma) / (0.015 * md)
    if ppo.iloc[-1] > sig.iloc[-1] and ppo.iloc[-2] <= sig.iloc[-2]:
        cross = "金叉"
    elif ppo.iloc[-1] < sig.iloc[-1] and ppo.iloc[-2] >= sig.iloc[-2]:
        cross = "死叉"
    elif ppo.iloc[-1] > sig.iloc[-1]:
        cross = "多"
    else:
        cross = "空"
    return {
        "date": str(g["date"].iloc[-1])[:10], "close": round(float(close.iloc[-1]), 2),
        "cci": round(float(cci.iloc[-1]), 1), "ppo": round(float(ppo.iloc[-1]), 2),
        "hist": round(float(hist.iloc[-1]), 3),
        "hist_d": round(float(hist.iloc[-1] - hist.iloc[-2]), 3),
        "cross": cross, "stale": False,
    }


def main():
    raw = fetch_all()
    results = []
    for sym, name in WATCHLIST.items():
        g = raw[raw["sym"] == sym]
        if g.empty:
            results.append({"name": name, "sym": sym, "stale": True, "date": "NODATA"})
            continue
        r = compute(g)
        r.update({"name": name, "sym": sym})
        results.append(r)

    ok = [r for r in results if not r["stale"]]
    bad = [r for r in results if r["stale"]]
    ok.sort(key=lambda x: -x["cci"])
    print(f"=== PPO/CCI 筛选 | 数据日 2026-10-13 | 覆盖 {len(ok)}/{len(WATCHLIST)} ===\n")
    print(f"{'股票':<10}{'收盘':>8}{'CCI20':>8}{'PPO':>8}{'柱':>8}{'柱动':>7}{'叉':>5}  标签")
    for r in ok:
        tags = []
        if r["cci"] > 100: tags.append("超买⚠回调")
        elif r["cci"] > 50: tags.append("偏强")
        if r["cci"] < -100: tags.append("超卖(观察反弹)")
        elif r["cci"] < -50: tags.append("偏弱")
        if r["cross"] == "金叉": tags.append("PPO金叉✓")
        if r["cross"] == "死叉": tags.append("PPO死叉✗")
        if r["hist_d"] > 0: tags.append("柱增")
        else: tags.append("柱缩")
        print(f"{r['name']:<10}{r['close']:>8}{r['cci']:>8}{r['ppo']:>8}{r['hist']:>8}{r['hist_d']:>7}{r['cross']:>5}  {' '.join(tags)}")
    if bad:
        print(f"\n⚠️ 无数据/滞后({len(bad)}): " + ", ".join(f"{r['name']}({r['date']})" for r in bad))


if __name__ == "__main__":
    main()
