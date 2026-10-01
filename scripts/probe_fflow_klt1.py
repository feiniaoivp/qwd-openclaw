#!/usr/bin/env python3
"""快速探针: push2 klt=1 实时资金流快照 — 单只响应结构确认（只读,不改生产代码）"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
from curl_cffi import requests as cr

URL = ("https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
       "?lmt=0&klt=1&secid=1.600030"
       "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
       "&ut=b2884a393a59ad64002292a3e90d46a5")
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
}

def fetch(impersonate, timeout=10):
    r = cr.get(URL, headers=HEADERS, impersonate=impersonate, timeout=timeout)
    print(f"[{impersonate}] HTTP {r.status_code}, bytes={len(r.content)}")
    return r

r = None
for fp in ("chrome110", "edge99", "chrome110"):
    try:
        r = fetch(fp)
        break
    except Exception as e:
        print(f"[{fp}] FAIL: {type(e).__name__}: {str(e)[:120]}")
if r is None:
    sys.exit(1)
d = r.json()
data = d.get("data") or {}
print(f"top-level keys: {sorted(d.keys())}")
print(f"data keys: {sorted(data.keys())}")
print(f"code={data.get('code')} name={data.get('name')} rc={d.get('rc')}")
klines = data.get("klines") or []
print(f"klines count: {len(klines)}")
if klines:
    print(f"first: {klines[0]}")
    print(f"last:  {klines[-1]}")
    p = klines[-1].split(",")
    print(f"last fields ({len(p)}): date={p[0]} main={p[1]} small={p[2]} medium={p[3]} large={p[4]} xlarge={p[5]} pct={p[6] if len(p)>6 else '-'}")
# 其他字段抽样
for k in ("tradePeriods", "preKlines", "prePrice", "trends"):
    if k in data:
        v = data[k]
        print(f"{k}: {str(v)[:200]}")
