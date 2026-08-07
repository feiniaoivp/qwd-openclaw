#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘前9:00竞价/实时扫描 — 2026-08-07 复用 close_scan_v2.py 的 fetch_hq_dict"""
import sys, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from close_scan_v2 import fetch_hq_dict, WATCHLIST

codes = []
for code, _name in WATCHLIST:
    codes.append(("sh" if code[0] in "69" else "sz") + code)

hq = fetch_hq_dict(codes)

# 输出精简JSON供外部解析
rows = []
for code, name in WATCHLIST:
    r = hq.get(code)
    if not r or r["last"] == 0:
        rows.append({"code": code, "name": name, "error": "无行情/未成交"})
        continue
    pre = r["pre_close"]
    last = r["last"]
    openp = r["open"]
    chg_pct = (last / pre - 1) * 100 if pre else 0
    open_chg = (openp / pre - 1) * 100 if pre else 0
    amp = (r["high"] - r["low"]) / pre * 100 if pre else 0
    amt_yi = r["amount"] / 1e8
    rows.append({
        "code": code, "name": name, "price": round(last, 2),
        "chg_pct": round(chg_pct, 2), "open": round(openp, 2),
        "open_chg_pct": round(open_chg, 2), "amp_pct": round(amp, 2),
        "amount_yi": round(amt_yi, 3), "volume": r["volume"],
        "high": round(r["high"], 2), "low": round(r["low"], 2),
        "time": r["time"], "date": r["date"],
    })

print(json.dumps(rows, ensure_ascii=False, indent=2))
