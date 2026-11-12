#!/usr/bin/env python3
"""
Top-K 扩缩受控审计（2026-11-12，/signal_evidence_audit）
==================================================================
假设: trend_up regime_top_k 30(现产) vs 15/20/25 —— K 扩缩能否改善候选池超额?
数据: data/universe/score_rows_3d.pkl (1.04M 行, 3d 生产口径)
语义: 与生产 signal_scoring.py 一致(regime K / sideways 双条件地板 /
      bb_pos 带硬门槛[缺失放行=fail-open] / score>=45), 逐日同池, 唯一差异=trend_up K
判据: 预注册见 analysis/topk_expansion_audit_20261112_report.md (跑前写定)
断言: I1 非trend_up日四臂全等 / I2 n_gated<=15 日全等 / I3 收缩嵌套; 违反 exit 1
产出: data/universe/topk_audit_20261112.json (写前 .bak)
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
OUT = os.path.join(WORKSPACE, "data", "universe", "topk_audit_20261112.json")

MIN_SCORE = 45.0          # 生产 CFG.l3.min_score (2026-11-12 实读)
BB_LO, BB_HI = 0.15, 0.92  # 生产 l3.hard_gates
FLOOR_TOP1, FLOOR_TOP5 = 90.0, 80.0  # sideways 双条件地板
REGIME_K_BASE = {"trend_up": 30, "sideways": 15, "trend_down": 15, "high_vol": 5}
ARMS = {"A_K30": 30, "B15": 15, "B20": 20, "B25": 25}
N_BOOT = 2000
BP = 1e4


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def day_excess(scores, bb, ex5, k):
    """生产语义单日选择 -> (日超额, n_selected, n_gated, 选中ex5数组)"""
    order = np.argsort(-scores, kind="stable")
    s, b, e = scores[order], bb[order], ex5[order]
    gated = (b >= BB_LO) & (b <= BB_HI)
    gated |= np.isnan(b)          # fail-open: bb 缺失放行(与生产一致)
    gated &= s >= MIN_SCORE
    if not gated.any():
        return np.nan, 0, 0, np.array([])
    idx = np.where(gated)[0][:k]
    sel_ex = e[idx]
    return float(np.nanmean(sel_ex)) if np.any(~np.isnan(sel_ex)) else np.nan, \
        len(idx), int(gated.sum()), sel_ex


def trimmed_excess(sel_ex, n_trim=2):
    """去头稳健: 剔除选中集内 ex5 最优 n_trim 只(≥3 只才做)"""
    if len(sel_ex) < n_trim + 1:
        return np.nan
    v = sel_ex[~np.isnan(sel_ex)]
    if len(v) < n_trim + 1:
        return np.nan
    v = np.sort(v)[::-1][n_trim:]
    return float(v.mean())


def main():
    df = pd.read_pickle(PKL)
    log(f"行 {len(df)}, {df['date'].min()} -> {df['date'].max()}")
    log(f"bb_pos 非空覆盖 {df['bb_pos'].notna().mean():.1%} (门槛保真度参考)")

    # 逐日同池构造四臂
    rows = []
    for d, g in df.groupby("date"):
        reg = g["regime"].iloc[0] if g["regime"].notna().any() else None
        scores = g["score"].to_numpy(dtype=float)
        bb = g["bb_pos"].to_numpy(dtype=float)
        ex5 = g["ex5"].to_numpy(dtype=float)
        rec = {"date": d, "regime": reg}
        sels = {}
        sel_ex5s = {}
        syms = g["sym"].to_numpy()
        for arm, k in ARMS.items():
            if reg is None:
                rec[arm], rec[f"n_{arm}"] = np.nan, 0
                sels[arm] = np.array([])
                continue
            kk = k if reg == "trend_up" else REGIME_K_BASE.get(reg, 15)
            if reg == "sideways":  # 双条件地板 -> 候选归零
                srt = np.sort(scores[~np.isnan(scores)])[::-1]
                top1 = srt[0] if len(srt) else np.nan
                top5m = float(np.nanmean(srt[:5])) if len(srt) >= 5 else np.nan
                if (not np.isnan(top1) and top1 < FLOOR_TOP1) or \
                   (not np.isnan(top5m) and top5m < FLOOR_TOP5):
                    rec[arm], rec[f"n_{arm}"] = np.nan, 0
                    sels[arm] = np.array([])
                    continue
            order = np.argsort(-scores, kind="stable")
            gated = ((bb[order] >= BB_LO) & (bb[order] <= BB_HI)) | np.isnan(bb[order])
            gated &= scores[order] >= MIN_SCORE
            idx = np.where(gated)[0][:kk]
            rec[arm] = float(np.nanmean(ex5[order][idx])) if len(idx) and np.any(~np.isnan(ex5[order][idx])) else np.nan
            rec[f"n_{arm}"] = len(idx)
            rec[f"ngated_{arm}"] = int(gated.sum())
            sels[arm] = syms[order][idx]
            sel_ex5s[arm] = ex5[order][idx]
        for _a in ARMS:
            sels.setdefault(_a, np.array([]))
            sel_ex5s.setdefault(_a, np.array([]))
        rec["_trim"] = {arm: trimmed_excess(sel_ex5s[arm]) for arm in ARMS}
        rec["_sels"] = sels
        rows.append(rec)
    D = pd.DataFrame(rows)
    log(f"逐日构造完成 {len(D)} 日")

    # ===== 恒等式断言 =====
    non_tu = D[D["regime"] != "trend_up"]
    i1 = non_tu[["A_K30", "B15", "B20", "B25"]].fillna(-999).values
    assert np.allclose(i1[:, 0], i1[:, 1]) and np.allclose(i1[:, 1], i1[:, 2]) \
        and np.allclose(i1[:, 2], i1[:, 3]), "I1 violated: 非 trend_up 日四臂不一致"
    tu = D[D["regime"] == "trend_up"].copy()
    small = tu[(tu["ngated_A_K30"].fillna(0) <= 15)]
    s4 = small[["A_K30", "B15", "B20", "B25"]].fillna(-999).values
    assert np.allclose(s4[:, 0], s4[:, 1]) and np.allclose(s4[:, 1], s4[:, 2]) \
        and np.allclose(s4[:, 2], s4[:, 3]), "I2 violated: n_gated<=15 日不一致"
    nest = tu.dropna(subset=["A_K30", "B15"]).copy()
    # I3(修正版): 选中集前缀嵌套 —— 同池同序下 B15 的选择必须是 A 的前 15 名子集
    bad_prefix = 0
    for _, r in nest.iterrows():
        sa, sb = r["_sels"]["A_K30"], r["_sels"]["B15"]
        if len(sb) and (len(sb) > len(sa) or not np.array_equal(sa[: len(sb)], sb)):
            bad_prefix += 1
    assert bad_prefix == 0, f"I3 violated: {bad_prefix} 日 B15 非A前缀"
    log(f"恒等式 I1/I2/I3 通过 (非tu日 {len(non_tu)}, tu小池日 {len(small)}, tu有效日 {len(nest)})")

    # ===== 主判据: trend_up 配对差 =====
    act = tu.dropna(subset=["A_K30", "B15"]).copy()
    act["year"] = act["date"].str[:4]
    d_bp = (act["B15"] - act["A_K30"]) * BP
    d20 = (act["B20"] - act["A_K30"]) * BP
    d25 = (act["B25"] - act["A_K30"]) * BP
    rng = np.random.default_rng(20261112)
    dv = d_bp.dropna().to_numpy()
    boots = np.array([dv[rng.integers(0, len(dv), len(dv))].mean() for _ in range(N_BOOT)])
    ci = np.percentile(boots, [2.5, 97.5]) * 1.0

    # 去头稳健
    t = act.dropna(subset=["A_K30", "B15"])
    trim_pairs = [(r["_trim"]["B15"], r["_trim"]["A_K30"]) for _, r in t.iterrows()]
    tp = [(a, b) for a, b in trim_pairs if not (np.isnan(a) or np.isnan(b))]
    d_trim_bp = float(np.mean([a - b for a, b in tp])) * BP if tp else np.nan

    # 分年
    by_year = {}
    for y, gy in act.groupby("year"):
        dy = (gy["B15"] - gy["A_K30"]) * BP
        by_year[y] = {"delta_bp": float(dy.mean()), "n_days": len(gy),
                      "a_mean_bp": float((gy["A_K30"] * BP).mean()),
                      "b15_mean_bp": float((gy["B15"] * BP).mean())}

    res = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "score_rows_3d.pkl", "n_boot": N_BOOT, "seed": 20261112,
        "min_score": MIN_SCORE, "bb_gate": [BB_LO, BB_HI],
        "identity": {"I1_non_tu_days": len(non_tu), "I2_small_days": len(small),
                     "I3_tu_active_days": len(nest), "all_passed": True},
        "primary": {
            "n_days": int(d_bp.notna().sum()),
            "A_mean_bp": float((act["A_K30"] * BP).mean()),
            "B15_mean_bp": float((act["B15"] * BP).mean()),
            "delta_bp": float(dv.mean()), "ci95": [float(ci[0]), float(ci[1])],
            "delta_trim_bp": d_trim_bp, "n_trim_days": len(tp),
            "delta20_bp": float(d20.mean()), "delta25_bp": float(d25.mean()),
            "n_bind_gt15": int((act["ngated_A_K30"].fillna(0) > 15).sum()),
            "n_bind_gt25": int((act["ngated_A_K30"].fillna(0) > 25).sum()),
        },
        "by_year": by_year,
        "portfolio_all_regimes": {
            arm: {"mean_bp": float((D[arm] * BP).mean()), "n_active": int(D[arm].notna().sum())}
            for arm in ARMS},
    }

    if os.path.exists(OUT):
        shutil.copy2(OUT, OUT + ".bak")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=float)
    log(f"产出 -> {OUT}")

    p = res["primary"]
    print("\n===== 主判据 (trend_up, bp) =====")
    print(f"  A(K30) mean={p['A_mean_bp']:+.1f}  B15 mean={p['B15_mean_bp']:+.1f}")
    print(f"  Δ(B15-A)={p['delta_bp']:+.1f}  CI95=[{p['ci95'][0]:+.1f},{p['ci95'][1]:+.1f}]  n={p['n_days']}")
    print(f"  去头Δ={p['delta_trim_bp']:+.1f} (n={p['n_trim_days']})  Δ20={p['delta20_bp']:+.1f}  Δ25={p['delta25_bp']:+.1f}")
    print(f"  K差异绑定日: n_gated>15 {p['n_bind_gt15']}/{p['n_days']}, >25 {p['n_bind_gt25']}/{p['n_days']}")
    print("  分年 Δ(B15-A):", {y: f"{v['delta_bp']:+.1f}bp/{v['n_days']}d" for y, v in by_year.items()})


if __name__ == "__main__":
    main()
