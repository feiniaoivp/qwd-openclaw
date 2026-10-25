#!/usr/bin/env python3
"""关注池 PPO/CCI 量化筛选（a-share-analyst 技能 Workflow C）

数据源: baostock 前复权日K（近110交易日），收盘后跑=当日口径，盘前跑=上一交易日口径。
数据日期动态推导（2026-10-23 升级：去除 10-13 硬编码），经 analysis.service 交易日历 SSOT。
输出: 每只股票 PPO/Signal/Histogram、CCI(20)、金叉/死叉、超买超卖分层、振幅、量比(5日均量)。
"""
import json
import os
import sys
from datetime import date, timedelta

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd

from analysis.fib_extension_scan import WATCHLIST
from analysis.service import get_trading_days


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def detect_data_day():
    """数据驱动探测数据日：以 sh.600030 最新已收盘 bar 为准。
    盘前→昨日；收盘后→当日；探测失败回退交易日历(d<today)，失败可见告警。
    """
    import socket
    socket.setdefaulttimeout(30)
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code == "0":
            rs = bs.query_history_k_data_plus(
                "sh.600030", "date,close",
                start_date=(date.today() - timedelta(days=15)).isoformat(),
                end_date=date.today().isoformat(),
                frequency="d", adjustflag="2")
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            bs.logout()
            if rows:
                return str(rows[-1][0])[:10]
        print(f"probe 降级: login={lg.error_code} {lg.error_msg}", file=sys.stderr)
    except Exception as e:
        print(f"probe 降级(异常可见): {e}", file=sys.stderr)
    today = date.today().isoformat()
    days = [d for d in get_trading_days() if d < today]
    return days[-1] if days else None


def fetch_all(end_day):
    cache = f"/tmp/ppo_cci_cache_{end_day}.csv"
    if os.path.exists(cache):
        return pd.read_csv(cache, dtype={"sym": str})
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        print(f"baostock login failed: {lg.error_code} {lg.error_msg}", file=sys.stderr)
        sys.exit(1)
    start = (date.fromisoformat(end_day) - timedelta(days=300)).isoformat()
    frames = []
    try:
        for sym in WATCHLIST:
            code = f"sh.{sym}" if sym.startswith(("6", "9")) else f"sz.{sym}"
            rs = bs.query_history_k_data_plus(
                code,
                "date,close,high,low,volume",
                start_date=start, end_date=end_day,
                frequency="d", adjustflag="2",  # 前复权
            )
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if rows:
                df = pd.DataFrame(rows, columns=["date", "close", "high", "low", "volume"])
                df["sym"] = sym
                frames.append(df)
    finally:
        bs.logout()
    out = pd.concat(frames, ignore_index=True)
    out.to_csv(cache, index=False)
    return out


def compute(g, end_day):
    g = g.tail(110).reset_index(drop=True)
    close = pd.to_numeric(g["close"], errors="coerce")
    high = pd.to_numeric(g["high"], errors="coerce")
    low = pd.to_numeric(g["low"], errors="coerce")
    vol = pd.to_numeric(g["volume"], errors="coerce").replace(0, pd.NA)
    last_date = str(g["date"].iloc[-1])[:10]
    if len(g) < 40 or last_date < end_day or close.iloc[-1] is None or pd.isna(close.iloc[-1]):
        return {"date": last_date, "stale": True}
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
    prev_close = close.iloc[-2]
    amplitude = (high.iloc[-1] - low.iloc[-1]) / prev_close * 100 if prev_close else 0.0
    vol_avg5 = vol.iloc[-6:-1].mean()
    vol_ratio = float(vol.iloc[-1]) / float(vol_avg5) if vol_avg5 and pd.notna(vol.iloc[-1]) else 0.0
    return {
        "date": last_date, "close": round(float(close.iloc[-1]), 2),
        "cci": round(float(cci.iloc[-1]), 1), "ppo": round(float(ppo.iloc[-1]), 2),
        "hist": round(float(hist.iloc[-1]), 3),
        "hist_d": round(float(hist.iloc[-1] - hist.iloc[-2]), 3),
        "ampl": round(float(amplitude), 2),
        "vr": round(float(vol_ratio), 2),
        "cross": cross, "stale": False,
    }


def tags_for(r):
    """标签判定（表格/JSON 单一来源，避免双处口径漂移）"""
    tags = []
    if r["cci"] > 100: tags.append("超买⚠回调")
    elif r["cci"] > 50: tags.append("偏强")
    if r["cci"] < -100: tags.append("超卖(观察反弹)")
    elif r["cci"] < -50: tags.append("偏弱")
    if r["cross"] == "金叉": tags.append("PPO金叉✓")
    if r["cross"] == "死叉": tags.append("PPO死叉✗")
    if r["hist_d"] > 0: tags.append("柱增")
    else: tags.append("柱缩")
    if r["vr"] > 2 and r["ampl"] > 5: tags.append("🔴量价异动")
    return tags


def emit_json(end_day, ok, bad):
    """管线模式：标记包裹 JSON（premarket_pipeline 阶段约定），行内带 tags 免二次推导"""
    rows = [{
        "name": r["name"], "sym": r["sym"], "date": r["date"],
        "close": r["close"], "cci": r["cci"], "ppo": r["ppo"],
        "hist": r["hist"], "hist_d": r["hist_d"],
        "ampl": r["ampl"], "vr": r["vr"], "cross": r["cross"],
        "tags": tags_for(r),
    } for r in ok]
    payload = {
        "data_day": end_day,
        "coverage": {"ok": len(ok), "total": len(WATCHLIST)},
        "rows": rows,
        "bad": [{"name": r["name"], "sym": r["sym"], "date": r.get("date", "NODATA")} for r in bad],
    }
    print("=====PPO_CCI_RESULT=====")
    print(json.dumps(payload, ensure_ascii=False))
    print("=====PPO_CCI_END=====")


def main():
    emit_json_mode = "--json" in sys.argv[1:]
    end_day = detect_data_day()
    if not end_day:
        print("交易日历为空，无法确定数据日", file=sys.stderr)
        sys.exit(1)
    raw = fetch_all(end_day)
    results = []
    for sym, name in WATCHLIST.items():
        g = raw[raw["sym"] == sym]
        if g.empty:
            results.append({"name": name, "sym": sym, "stale": True, "date": "NODATA"})
            continue
        r = compute(g, end_day)
        r.update({"name": name, "sym": sym})
        results.append(r)

    ok = [r for r in results if not r["stale"]]
    bad = [r for r in results if r["stale"]]
    ok.sort(key=lambda x: -x["cci"])
    if emit_json_mode:
        emit_json(end_day, ok, bad)
        return
    print(f"=== PPO/CCI 筛选 | 数据日 {end_day} | 覆盖 {len(ok)}/{len(WATCHLIST)} ===\n")
    print(f"{'股票':<10}{'收盘':>8}{'CCI20':>8}{'PPO':>8}{'柱':>8}{'柱动':>7}{'振幅%':>7}{'量比':>6}{'叉':>5}  标签")
    for r in ok:
        tags = tags_for(r)
        print(f"{r['name']:<10}{r['close']:>8}{r['cci']:>8}{r['ppo']:>8}{r['hist']:>8}{r['hist_d']:>7}{r['ampl']:>7}{r['vr']:>6}{r['cross']:>5}  {' '.join(tags)}")
    if bad:
        print(f"\n⚠️ 无数据/滞后({len(bad)}): " + ", ".join(f"{r['name']}({r['date']})" for r in bad))


if __name__ == "__main__":
    main()
