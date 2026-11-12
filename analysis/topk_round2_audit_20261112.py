#!/usr/bin/env python3
"""
Top-K 第二轮深究审计（2026-11-12，第一轮后非单调信号追踪）
==================================================================
臂: A=K30恒定 / B20 / B25 / C=条件收缩(gap>12.78→K15, in-sample P75) /
    D=环形对照(阈值 2021-23 重拟合, 仅评估 2024-26)
判据: 预注册见 analysis/topk_expansion_audit_20261112_report.md 第二轮节
断言: 非 trend_up 日五臂全等; B20/B25 ⊆ A 前缀; C 非触发日 = A
产出: data/universe/topk_round2_audit_20261112.json (写前 .bak)
"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
import shutil
import time

import numpy as np
import pandas as pd

PKL = os.path.join(WORKSPACE, "data", "universe", "score_rows_3d.pkl")
OUT = os.path.join(WORKSPACE, "data", "universe", "topk_round2_audit_20261112.json")

MIN_SCORE = 45.0
BB_LO, BB_HI = 0.15, 0.92
FLOOR_TOP1, FLOOR_TOP5 = 90.0, 80.0
REGIME_K_BASE = {"trend_up": 30, "sideways": 15, "trend_down": 15, "high_vol": 5}
IS_THRESH = 12.78          # in-sample P75 (distribution_audit, trend_up 段)
N_BOOT = 2000
BP = 1e4
BANDS = [(1, 5), (6, 10), (11, 15), (16, 20), (21, 25), (26, 30)]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def sorted_pool(g):
    """全池按 score 降序（门槛前，生产 gap 口径）"""
    s = g["score"].to_numpy(dtype=float)
    order = np.argsort(-s, kind="stable")
    return s[order], g["bb_pos"].to_numpy(dtype=float)[order], \
        g["ex5"].to_numpy(dtype=float)[order]


def select_k(scores_srt, bb_srt, k):
    """生产选择: bb 带内(缺失放行) & score>=45, 前 k"""
    gated = ((bb_srt >= BB_LO) & (bb_srt <= BB_HI)) | np.isnan(bb_srt)
    gated &= scores_srt >= MIN_SCORE
    idx = np.where(gated)[0][:k]
    return idx


def trim_top2(v, n=2):
    vv = np.sort(v[~np.isnan(v)])[::-1]
    return float(vv[n:].mean()) if len(vv) > n else np.nan


def main():
    df = pd.read_pickle(PKL)
    log(f"行 {len(df)}")

    tu_days, other_days = [], []
    for d, g in df.groupby("date"):
        reg = g["regime"].iloc[0] if g["regime"].notna().any() else None
        if reg == "trend_up":
            tu_days.append((d, g))
        else:
            s_srt, b_srt, e_srt = sorted_pool(g)
            kk = REGIME_K_BASE.get(reg, 15)
            if reg == "sideways":
                nn = s_srt[~np.isnan(s_srt)]
                top1 = nn[0] if len(nn) else np.nan
                top5m = float(np.nanmean(nn[:5])) if len(nn) >= 5 else np.nan
                if (not np.isnan(top1) and top1 < FLOOR_TOP1) or \
                   (not np.isnan(top5m) and top5m < FLOOR_TOP5):
                    for arm in ("A", "B20", "B25", "C", "D"):
                        other_days.append({"date": d, "arm": arm, "ex": np.nan})
                    continue
            idx = select_k(s_srt, b_srt, kk)
            ex = float(np.nanmean(e_srt[idx])) if len(idx) and np.any(~np.isnan(e_srt[idx])) else np.nan
            for arm in ("A", "B20", "B25", "C", "D"):
                other_days.append({"date": d, "arm": arm, "ex": ex})
    log(f"trend_up 日 {len(tu_days)}, 其余日 {len(other_days)//5}")

    # ===== D 阈值: 2021-2023 trend_up gap P75 重拟合 =====
    gaps_2123 = []
    for d, g in tu_days:
        if d[:4] in ("2021", "2022", "2023"):
            s_srt, _, _ = sorted_pool(g)
            if len(s_srt) >= 30:
                gaps_2123.append(float(s_srt[0] - s_srt[29]))
    d_thresh = float(np.percentile(gaps_2123, 75)) if gaps_2123 else np.nan
    log(f"D 环形阈值 (2021-23 P75, n={len(gaps_2123)}): {d_thresh:.2f}")

    # ===== trend_up 日五臂 =====
    rows, bands_acc = [], {f"{lo}-{hi}": [] for lo, hi in BANDS}
    c_trig = 0
    for d, g in tu_days:
        s_srt, b_srt, e_srt = sorted_pool(g)
        if len(s_srt) < 30:
            continue
        gap = float(s_srt[0] - s_srt[29])
        rec = {"date": d, "year": d[:4], "gap": gap}

        idx_a = select_k(s_srt, b_srt, 30)
        ex_a = np.nanmean(e_srt[idx_a])
        rec["A"] = float(ex_a) if np.any(~np.isnan(e_srt[idx_a])) else np.nan
        rec["A_trim"] = trim_top2(e_srt[idx_a])

        for arm, k in (("B20", 20), ("B25", 25)):
            ix = select_k(s_srt, b_srt, k)
            ev = e_srt[ix]
            rec[arm] = float(np.nanmean(ev)) if np.any(~np.isnan(ev)) else np.nan
            rec[f"{arm}_trim"] = trim_top2(ev)

        # C: 条件收缩
        c_hit = gap > IS_THRESH
        if c_hit:
            c_trig += 1
            ix = select_k(s_srt, b_srt, 15)
            ev = e_srt[ix]
        else:
            ev = e_srt[idx_a]
        rec["C"] = float(np.nanmean(ev)) if np.any(~np.isnan(ev)) else np.nan
        rec["C_trim"] = trim_top2(ev)
        rec["C_hit"] = bool(c_hit)

        # D: 环形阈值, 仅 2024-26 生效; 其余日 = A
        if d[:4] in ("2024", "2025", "2026") and not np.isnan(d_thresh):
            if gap > d_thresh:
                ix = select_k(s_srt, b_srt, 15)
                ev = e_srt[ix]
            else:
                ev = e_srt[idx_a]
            rec["D"] = float(np.nanmean(ev)) if np.any(~np.isnan(ev)) else np.nan
            rec["D_trim"] = trim_top2(ev)
            rec["D_hit"] = bool(gap > d_thresh)
        else:
            rec["D"] = rec["A"]
            rec["D_trim"] = rec["A_trim"]
            rec["D_hit"] = None

        # 带分解 (口径 = A 选择集)
        for lo, hi in BANDS:
            seg = e_srt[idx_a][lo-1:hi]
            seg = seg[~np.isnan(seg)]
            if len(seg):
                bands_acc[f"{lo}-{hi}"].append(float(seg.mean()) * BP)

        rows.append(rec)
    TU = pd.DataFrame(rows)
    log(f"trend_up 有效日(≥30行) {len(TU)}, C 触发 {c_trig} ({c_trig/len(TU):.0%})")

    # ===== 恒等式 =====
    od = pd.DataFrame(other_days).pivot(index="date", columns="arm", values="ex")
    assert np.allclose(od["A"].fillna(-999), od["B20"].fillna(-999)) and \
        np.allclose(od["A"].fillna(-999), od["C"].fillna(-999)) and \
        np.allclose(od["A"].fillna(-999), od["D"].fillna(-999)), "非tu日五臂不一致"

    # ===== 配对统计 =====
    def pair(col, base="A", sub=None):
        t = TU if sub is None else TU[TU["year"].isin(sub)]
        dv = ((t[col] - t[base]) * BP).dropna().to_numpy()
        if not len(dv):
            return None
        rng = np.random.default_rng(20261112 + len(col))
        boots = np.array([dv[rng.integers(0, len(dv), len(dv))].mean() for _ in range(N_BOOT)])
        ci = np.percentile(boots, [2.5, 97.5])
        yrs = {}
        for y, gy in t.groupby("year"):
            dy = (gy[col] - gy[base]) * BP
            yrs[y] = float(dy.mean())
        return {"delta_bp": float(dv.mean()), "ci95": [float(ci[0]), float(ci[1])],
                "n": len(dv), "by_year": yrs,
                "pos_years": sum(1 for v in yrs.values() if v > 0), "n_years": len(yrs),
                "trim_delta_bp": float(((t[f"{col}_trim"] - t[f"{A_TRIM[base]}"]) * BP).mean())}

    A_TRIM = {"A": "A_trim", "B20": "B20_trim", "B25": "B25_trim", "C": "C_trim", "D": "D_trim"}
    res = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "score_rows_3d.pkl", "seed_offset": 20261112,
        "is_threshold": IS_THRESH, "d_threshold_2123_p75": d_thresh,
        "identity": {"non_tu_days": int(len(od)), "tu_days": len(TU), "all_passed": True},
        "C_trigger_rate": c_trig / len(TU),
        "D_trigger_rate_2426": float(TU.loc[TU["D_hit"].notna(), "D_hit"].mean()),
        "band_decomposition_bp": {k: float(np.mean(v)) for k, v in bands_acc.items()},
        "pairs": {
            "B20-A": pair("B20"), "B25-A": pair("B25"),
            "C-A": pair("C"),
            "D-A(24-26)": pair("D", sub=("2024", "2025", "2026")),
        },
    }

    if os.path.exists(OUT):
        shutil.copy2(OUT, OUT + ".bak")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=float)
    log(f"产出 -> {OUT}")

    print("\n===== 带分解 (A 选择集, bp) =====")
    for k, v in res["band_decomposition_bp"].items():
        print(f"  rank {k:6s} {v:+8.1f}")
    print("\n===== 配对判据 =====")
    for name, p in res["pairs"].items():
        if p:
            print(f"  {name:12s} Δ={p['delta_bp']:+7.1f}  CI[{p['ci95'][0]:+.1f},{p['ci95'][1]:+.1f}]"
                  f"  年同号 {p['pos_years']}/{p['n_years']}  去头Δ={p['trim_delta_bp']:+.1f}  n={p['n']}")
    print(f"\nC 触发率 {res['C_trigger_rate']:.0%} | D(24-26) 触发率 {res['D_trigger_rate_2426']:.0%}")


if __name__ == "__main__":
    main()
