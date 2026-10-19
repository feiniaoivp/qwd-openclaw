#!/usr/bin/env python3
"""低开甜点档 horizon 稳健性检验（2026-10-20）。

背景
----
2026-10-20_low_open_sweetspot 的甜点结论全部基于 h=5（entry T+1 open，持有 5 日）。
本脚本问：[−2%,−1%) 甜点在 h=1（当日短线）与 h=10（双周）下是否还成立？
若只在 h=5 成立，则甜点可能是窗口特异；若 h=1/10 同向，则稳健性加分。

口径（继承 gap_tier_execution_test 机制，horizons 参数化）
----
* 分歧买信号（n=984）；gap = open(T+1)/close(T)−1
* ex_oc(h) = close(T+1+h)/open(T+1) − 1 − 同年全样本同口径均值
* winsorize ±15%（肥尾教训同前）
* 分桶同 gap_tier；重点档 [−2,−1)
"""
import os
import sys
import json
from collections import defaultdict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.net_guard import install_default_timeout
install_default_timeout(15)

import pandas as pd

import gap_tier_execution_test as gt

OUT_JSON = os.path.join(WORKSPACE, "analysis", "research", "2026-10-20_sweetspot_horizon_check.json")
OUT_MD = os.path.join(WORKSPACE, "analysis", "research", "2026-10-20_sweetspot_horizon_check.md")
HORIZONS = (1, 5, 10)
WINSOR = 0.15
BUCKETS = [
    ("[-2,-1)甜点", -0.02, -0.01),
    ("gap<-1%全档", None, -0.01),
    ("[-1,0)", -0.01, 0.0),
    ("[0,+1)", 0.0, 0.01),
    ("[+1,+3)", 0.01, 0.03),
    ("gap>+3%", 0.03, None),
]


def main():
    with open(gt.EVAL_JSON) as f:
        rows = json.load(f)
    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append(r)

    # recs：每票日 gap + 各 horizon 的 open-entry 前瞻
    recs = []
    for sym, srows in sorted(by_sym.items()):
        p = os.path.join(gt.PRICE_CACHE_DIR, f"{sym}.pkl")
        if not os.path.exists(p):
            continue
        df = pd.read_pickle(p)
        closes = df["close"].astype(float).tolist()
        opens = df["open"].astype(float).tolist()
        dates = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in df["date"]]
        pos = {d: k for k, d in enumerate(dates)}
        for r in srows:
            k = pos.get(r["date"])
            if k is None or k + 1 >= len(closes):
                continue
            gap = opens[k + 1] / closes[k] - 1
            fwd = {}
            for h in HORIZONS:
                # 口径对齐原研究：fwd5_oc = close[k+5]/open[k+1]（entry 日当天收盘算 h=1）
                fwd[h] = closes[k + h] / opens[k + 1] - 1 if k + h < len(closes) else None
            recs.append({"symbol": sym, "date": r["date"], "year": r["date"][:4],
                         "cls": r["cls"], "gap": gap, "fwd": fwd})

    # 同年全样本同口径基线（每 horizon）
    base = {h: {} for h in HORIZONS}
    for h in HORIZONS:
        acc = defaultdict(list)
        for r in recs:
            if r["fwd"][h] is not None:
                acc[r["year"]].append(r["fwd"][h])
        base[h] = {y: sum(v) / len(v) for y, v in acc.items() if v}

    buys = [r for r in recs if r["cls"] in gt.BUY_CLASSES and r["cls"] == "分歧买"]

    def tier_ex(sub, h, winsorized=True):
        xs = []
        for r in sub:
            v = r["fwd"][h]
            if v is None:
                continue
            ex = v - base[h][r["year"]]
            if winsorized:
                ex = max(min(ex, WINSOR), -WINSOR)
            xs.append(ex * 100)
        if not xs:
            return {"n": 0}
        return {"n": len(xs), "ex_oc_mean": round(sum(xs) / len(xs), 3),
                "ci95": gt.boot_ci(xs)}

    out = {"date": "2026-10-20", "n_recs": len(recs), "n_buy": len(buys),
           "winsor": WINSOR * 100,
           "note": "ex_oc(h)=T+1 open→T+1+h close − 同年全样本同口径均值；winsor±15%",
           "tiers": {}}

    print(f"分歧买 n={len(buys)}（winsor ±{WINSOR:.0%}）")
    print(f"{'桶':<14} " + " ".join(f"h={h}: n/ex/CI" for h in HORIZONS))
    for label, lo, hi in BUCKETS:
        sub = [r for r in buys if (lo is None or r["gap"] >= lo) and (hi is None or r["gap"] < hi)]
        entry = {}
        parts = []
        for h in HORIZONS:
            s = tier_ex(sub, h)
            entry[f"h{h}"] = s
            ci = s.get("ci95")
            ci_s = "n/a" if not ci else f"[{ci[0]:+.2f},{ci[1]:+.2f}]"
            parts.append(f"{s.get('n',0)}/{s.get('ex_oc_mean','—'):+.2f}{ci_s}")
        out["tiers"][label] = entry
        print(f"{label:<14} " + " | ".join(parts))

    with open(OUT_JSON, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"📄 落盘: {os.path.relpath(OUT_JSON, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
