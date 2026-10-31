#!/usr/bin/env python3
"""五维评分「留一维」审计 + 组合 vs 单维/随机基线（2026-10-20）。

背景
----
chip 维已做增量检验（+0.147%/日 top5，安慰剂=0），但 tech/vp/fund 三维从未做过
「去掉它总分是否变差」的对称检验；组合总分是否胜过最好的单维、胜过随机，
也从未验证。本脚本用与 chip_increment_test 完全同源的 rank 级 top-K 机制一次收口。

口径
----
* 4 维近似（tech/vp/fund/chip，fund_flow 无历史对各臂对称）
* 臂：full4 / drop_X ×4 / single_X ×4 / random（每日随机 K 只，seed 固定）
* ex5 winsorize ±15%（肥尾教训）= 5 日收盘 − 同日全池均值
* 每日 top-K(5/10) 均值 ex5；配对按日差分 + bootstrap CI（seed=42）
* 判定：Δ(full4−drop_X) 显著为正 = 该维有增量；full4−best_single 显著为正 = 组合有超加性
* 附：full4 top-5 日度换手率（排名可用性诊断）
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

import numpy as np
import pandas as pd

import chip_increment_test as cit
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


EVAL = os.path.join(WORKSPACE, "analysis", "research", "dual_signal_eval_days.json")
OUT_JSON = os.path.join(WORKSPACE, "analysis", "research", "2026-10-20_dim_leave_out_audit.json")
OUT_MD = os.path.join(WORKSPACE, "analysis", "research", "2026-10-20_dim_leave_out_audit.md")
HOLD = 5
WINSOR = 0.15
K_LIST = (5, 10)
BOOT_SEED = 42


def main():
    prices = cit.load_prices_full()

    bench = defaultdict(list)
    for sym, ser in prices.items():
        a = ser["close"].values
        ds = list(ser.index)
        for i in range(len(a) - HOLD):
            if a[i] > 0:
                bench[ds[i]].append((a[i + HOLD] / a[i] - 1) * 100)
    bench5 = {d: sum(v) / len(v) for d, v in bench.items() if v}

    cache = os.path.join(WORKSPACE, "data", "research_price_cache")
    chipm = {}
    for fn in os.listdir(cache):
        if fn.startswith("chipmetrics_") and fn.endswith(".pkl"):
            sym = fn[len("chipmetrics_"):-4]
            df = pd.read_pickle(os.path.join(cache, fn))
            chipm[sym] = dict(zip(df["date"].astype(str).str[:10], df["cost_bias"]))

    fund5_asof = cit.build_fund_asof(json.load(open(cit.FUND)), json.load(open(cit.PEPB)))

    rows = json.load(open(EVAL))
    by_sym = defaultdict(list)
    for r in rows:
        by_sym[str(r.get("symbol") or "")].append(r)

    # 按股预计算 Series，逐行组装四维值 + ex5(winsor)
    recs = []
    for sym, srows in by_sym.items():
        ser = prices.get(sym)
        if ser is None:
            continue
        close = ser["close"]
        dif = cit.ema(close, 12) - cit.ema(close, 26)
        dea = cit.ema(dif, 9)
        tech_s = pd.Series(5.0, index=close.index)
        tech_s[dif > dea] = 7.0
        tech_s[dif <= dea] = 3.0
        r14 = cit.rsi_wilder(close)
        tech_s[r14 > 70] = tech_s[r14 > 70] - 2.0
        tech_s = tech_s.clip(0, 10)
        chg_s = close.pct_change() * 100
        turn = ser["turn"] if "turn" in ser else pd.Series(np.nan, index=close.index)
        fwd = (close.shift(-HOLD) / close - 1) * 100
        ex5_s = fwd - pd.Series(bench5).reindex(close.index)
        for r in srows:
            d = str(r.get("date") or "")[:10]
            if d not in ser.index:
                continue
            i = ser.index.get_loc(d)
            ex5 = ex5_s.iloc[i]
            if not np.isfinite(ex5):
                continue
            t = float(turn.iloc[i]) if pd.notna(turn.iloc[i]) else None
            chg = float(chg_s.iloc[i]) if i > 0 and pd.notna(chg_s.iloc[i]) else None
            vp = None
            if chg is not None:
                vp = 5.0
                if chg > 5:
                    vp -= 1
                elif abs(chg) > 2:
                    vp += 1
                elif abs(chg) < 0.5:
                    vp -= 1
                if t is not None:
                    if t > 10:
                        vp += 2
                    elif t > 5:
                        vp += 1
                    elif t < 1:
                        vp -= 1
                vp = max(0.0, min(10.0, vp))
            cb = chipm.get(sym, {}).get(d)
            recs.append({
                "sym": sym, "date": d,
                "tech": float(tech_s.iloc[i]), "vp": vp,
                "fund": fund5_asof(sym, d), "chip": cit.chip5_from_cb(cb),
                "ex5": float(max(min(ex5, WINSOR * 100), -WINSOR * 100)),
            })
    print(f"面板 {len(recs)} 行")

    dates = sorted({r["date"] for r in recs})
    by_date = defaultdict(list)
    for r in recs:
        by_date[r["date"]].append(r)

    DIMS = ("tech", "vp", "fund", "chip")

    def arm_total(rec, dims):
        vals = [rec[d] for d in dims if rec.get(d) is not None]
        if not vals:
            return None
        return sum(vals) / len(vals) * 5.0

    def topk_mean_arm(dims, K):
        """每日 top-K 均值 ex5 序列（按日期）。"""
        out = {}
        for d, lst in by_date.items():
            cand = []
            for rec in lst:
                t = arm_total(rec, dims)
                if t is not None:
                    cand.append((t, rec["ex5"]))
            if len(cand) < K + 3:
                continue
            cand.sort(key=lambda x: -x[0])
            out[d] = sum(e for _s, e in cand[:K]) / K
        return out

    rng = np.random.default_rng(BOOT_SEED)
    def topk_mean_random(K):
        out = {}
        for d, lst in by_date.items():
            elig = [(rec["ex5"]) for rec in lst]
            if len(elig) < K + 3:
                continue
            idx = rng.choice(len(elig), size=K, replace=False)
            out[d] = sum(elig[i] for i in idx) / K
        return out

    arms = {"full4": list(DIMS)}
    for d in DIMS:
        arms[f"drop_{d}"] = [x for x in DIMS if x != d]
    for d in DIMS:
        arms[f"single_{d}"] = [d]
    series = {}
    for name, dims in arms.items():
        series[name] = {K: topk_mean_arm(dims, K) for K in K_LIST}
    series["random"] = {K: topk_mean_random(K) for K in K_LIST}

    def paired_delta(a, b, K):
        """a−b 按日配对差分（两臂都有值的日期）。"""
        da, db = series[a][K], series[b][K]
        common = sorted(set(da) & set(db))
        diffs = [da[d] - db[d] for d in common]
        if len(diffs) < 5:
            return {"n_dates": len(common), "mean": None, "ci95": None}
        m = sum(diffs) / len(diffs)
        ci = cit.boot_ci_paired(diffs)
        return {"n_dates": len(common), "mean": round(m, 4),
                "ci95": ci, "better_pct": round(sum(1 for x in diffs if x > 0) / len(diffs), 3)}

    out = {"date": "2026-10-20", "n_panel": len(recs), "winsor": WINSOR * 100,
           "note": "4维近似；topK均值ex5(winsor±15%)；Δ=配对日度差分；drop_X增量=full4−drop_X",
           "levels": {}, "deltas": {}, "churn": {}}

    print(f"\n=== 各臂绝对水平（top-K 日均 ex5，全期均值）===")
    for name in list(arms) + ["random"]:
        row = {}
        for K in K_LIST:
            s = series[name][K]
            m = sum(s.values()) / len(s) if s else None
            row[f"top{K}"] = round(m, 4) if m is not None else None
        out["levels"][name] = row
        print(f"  {name:<12} top5={row['top5']:+.3f}%  top10={row['top10']:+.3f}%  (n_dates={len(series[name][K_LIST[0]])})")

    print(f"\n=== 留一维增量（Δ = full4 − drop_X，正值=该维有增量）===")
    for d in DIMS:
        for K in K_LIST:
            delta = paired_delta("full4", f"drop_{d}", K)
            out["deltas"][f"incr_{d}_top{K}"] = delta
            ci = delta.get("ci95")
            ci_s = "n/a" if not ci else f"[{ci[0]:+.3f},{ci[1]:+.3f}]"
            print(f"  {d:<5} top{K}: Δ={delta.get('mean',0):+.3f}% {ci_s} 更优占比={delta.get('better_pct')}")

    print(f"\n=== 组合 vs 单维/随机（Δ = full4 − X，top5）===")
    for name in ["random"] + [f"single_{d}" for d in DIMS]:
        delta = paired_delta("full4", name, 5)
        out["deltas"][f"full4_minus_{name}_top5"] = delta
        ci = delta.get("ci95")
        ci_s = "n/a" if not ci else f"[{ci[0]:+.3f},{ci[1]:+.3f}]"
        print(f"  full4 − {name:<10}: Δ={delta.get('mean',0):+.3f}% {ci_s} 更优占比={delta.get('better_pct')}")

    # top-5 日度换手（full4）
    ch = []
    prev = None
    for d in dates:
        lst = by_date[d]
        cand = []
        for rec in lst:
            t = arm_total(rec, DIMS)
            if t is not None:
                cand.append((t, rec["sym"]))
        if len(cand) < 8:
            continue
        cand.sort(key=lambda x: -x[0])
        cur = {s for _t, s in cand[:5]}
        if prev is not None and len(cur & prev) > 0:
            ch.append(1 - len(cur & prev) / 5)
        prev = cur
    churn = sum(ch) / len(ch) if ch else None
    out["churn"] = {"top5_daily_turnover": round(churn, 3) if churn is not None else None,
                    "n_transitions": len(ch)}
    print(f"\nfull4 top-5 日度换手率: {churn:.0%}（{len(ch)} 个交易日过渡）")

    _bak(OUT_JSON)
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"📄 落盘: {os.path.relpath(OUT_JSON, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
