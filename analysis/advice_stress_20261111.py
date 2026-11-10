#!/usr/bin/env python3
"""
外部评审压力测试两判据（2026-11-11）
==================================================================
风险 A（地板硬截断陡峭度）: sideways 段 Top-1 ∈ [85,90) 的被咔嚓日，
  其 top15 反事实超额是多少？若显著为负 → 地板 90 砍得对；若 ≈0/正 → 偏陡。
  附: [88,90) 细分带 + 分布扁平度（top1-中位差）分组对照。
风险 B（trend_up K=30 尾部噪声）: 第 16-30 名 vs 1-15 名的胜率/偏度/
  头部依赖（去当日最优 2 只后的均值）/ 质量门槛（score>60）效果。
数据源: data/universe/score_rows_3d.pkl（打分行工厂，104 万行，3d=生产口径）
产出: data/universe/stress_test_20261111.json（写前 .bak）
=================================================================="""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis"), os.path.join(WORKSPACE, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
import shutil
import time
from collections import defaultdict

import numpy as np
import pandas as pd

_t0 = time.time()


def log(m):
    print(f"[{time.time()-_t0:6.1f}s] {m}", flush=True)


OUT = os.path.join(WORKSPACE, "data", "universe", "stress_test_20261111.json")
PKL = os.path.join(WORKSPACE, "data", "universe", "score_rows_3d.pkl")
BP = 1e4  # ex5 小数 → bp


def _bak(p):
    try:
        if os.path.exists(p):
            shutil.copy2(p, p + ".bak")
    except Exception as e:
        print(f"⚠️ bak 失败: {e}", file=sys.stderr)


def _stats(v):
    """ex5 序列(bp 口径)统计; 空 → None"""
    v = np.asarray(v, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) == 0:
        return None
    return {
        "n": int(len(v)),
        "mean_bp": float(np.mean(v) * BP),
        "median_bp": float(np.median(v) * BP),
        "win_rate": float((v > 0).mean()),
        "p10_bp": float(np.percentile(v, 10) * BP),
        "p90_bp": float(np.percentile(v, 90) * BP),
        "skew": float(pd.Series(v).skew()) if len(v) > 2 else None,
    }


def main():
    df = pd.read_pickle(PKL)
    log(f"打分行 {len(df)} 行, 日期 {df['date'].min()} → {df['date'].max()}")
    res = {"built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "source": "score_rows_3d.pkl (3d 生产口径)",
           "risk_a": {}, "risk_b": {}}

    # ============ 风险 A: sideways 地板陡峭度 ============
    sw = df[df["regime"] == "sideways"]
    days = {}
    for d, g in sw.groupby("date"):
        g = g.sort_values("score", ascending=False)
        days[d] = {"top1": float(g["score"].iloc[0]),
                   "scores": g["score"].to_numpy(),
                   "ex5": g["ex5"].to_numpy()}
    log(f"sideways 日数 {len(days)}")

    def band_stats(lo, hi, k=15):
        """[lo,hi) 地板带: 反事实 topK 超额（地板未生效时本来会买什么）"""
        sel = [v for v in days.values() if lo <= v["top1"] < hi] if hi is not None \
            else [v for v in days.values() if v["top1"] >= lo]
        ex = [np.nanmean(v["ex5"][:k]) for v in sel]  # 前 K 名等权（NaN 容错）
        med_gap = [float(v["top1"] - np.median(v["scores"])) for v in sel]
        return {"n_days": len(sel), "topk_ex5": _stats(ex), "top1_med_gap": _stats(med_gap)}

    for name, lo, hi in [(">=90(地板放行)", 90.0, None), ("[88,90)", 88.0, 90.0),
                         ("[85,90)", 85.0, 90.0), ("[80,85)", 80.0, 85.0),
                         ("<80", None, 80.0)]:
        if lo is None:
            sel = [v for v in days.values() if v["top1"] < hi]
            ex = [np.nanmean(v["ex5"][:15]) for v in sel]
            res["risk_a"][name] = {"n_days": len(sel), "topk_ex5": _stats(ex)}
        else:
            res["risk_a"][name] = band_stats(lo, hi)
    log("风险A 分带完成: " + ", ".join(f"{k}: {v['n_days']}日" for k, v in res["risk_a"].items()))

    # 扁平度对照: [85,90) 带内按 top1-中位差 二分
    band = [v for v in days.values() if 85.0 <= v["top1"] < 90.0]
    if band:
        gaps = np.array([v["top1"] - np.median(v["scores"]) for v in band])
        thr = float(np.median(gaps))
        for tag, mask in [("扁平(差<中位)", gaps < thr), ("陡峭(差>=中位)", gaps >= thr)]:
            ex = [np.nanmean(v["ex5"][:15]) for v, m in zip(band, mask) if m]
            res["risk_a"].setdefault("flatness_split_85_90", {})[tag] = {
                "n_days": int(mask.sum()), "gap_threshold": thr, "topk_ex5": _stats(ex)}

    # ============ 风险 B: trend_up K=30 尾部 ============
    tu = df[df["regime"] == "trend_up"]
    b15, b1630 = [], []
    b1630_trim2, b1630_gate60, b1630_nogate = [], [], []
    score_ge60_frac = []
    for d, g in tu.groupby("date"):
        g = g.sort_values("score", ascending=False)
        if len(g) < 30:
            continue
        ex = g["ex5"].to_numpy()
        sc = g["score"].to_numpy()
        b15.append(np.nanmean(ex[:15]))
        b1630.append(np.nanmean(ex[15:30]))
        tail_ex, tail_sc = ex[15:30], sc[15:30]
        # 去当日尾部最优 2 只（NaN 排到最后）
        order = np.argsort(~np.isnan(tail_ex))  # stable; NaN→False 在前
        order = np.argsort(np.where(np.isnan(tail_ex), -np.inf, tail_ex))[::-1]
        b1630_trim2.append(np.nanmean(np.delete(tail_ex, order[:2])))
        # 质量门槛: 尾部 15 只中 score>60 子集
        gate = tail_ex[tail_sc > 60]
        score_ge60_frac.append(float((tail_sc > 60).mean()))
        if len(gate):
            b1630_gate60.append(np.nanmean(gate))
        # K30 无门槛对照 = tail 全体
        b1630_nogate.append(np.nanmean(tail_ex))
    log(f"trend_up 可用日(≥30只) {len(b15)}")

    # 覆盖率是 0-1 比例，不做 bp 换算
    _cov = np.asarray(score_ge60_frac, dtype=float)
    res["risk_b"] = {
        "rank_1_15": _stats(b15),
        "rank_16_30": _stats(b1630),
        "rank_16_30_trim_top2_per_day": _stats(b1630_trim2),
        "rank_16_30_score_gt60": _stats(b1630_gate60) if b1630_gate60 else None,
        "score_gt60_coverage": {"mean": float(_cov.mean()), "median": float(np.median(_cov))} if len(_cov) else None,
        "verdict_inputs": {
            "k15_mean_bp": float(np.nanmean(b15) * BP),
            "k30_mean_bp": float(np.nanmean(b15 + b1630) * BP) if b1630 else None,
            "k30_gate60_mean_bp": float(np.nanmean(b1630_gate60) * BP) if b1630_gate60 else None,
        },
    }

    # 分年稳定性（尾部 16-30）
    year_map = defaultdict(list)
    for d, g in tu.groupby("date"):
        if len(g) < 30:
            continue
        g = g.sort_values("score", ascending=False)
        year_map[d[:4]].append(np.nanmean(g["ex5"].to_numpy()[15:30]))
    res["risk_b"]["rank_16_30_by_year"] = {
        y: {"mean_bp": float(np.nanmean(v) * BP), "n_days": len(v)}
        for y, v in sorted(year_map.items())}

    _bak(OUT)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=float)
    log(f"产出 → {OUT}")

    # 摘要打印
    print("\n===== 风险 A: sideways 地板陡峭度（top15 反事实, bp）=====")
    for k, v in res["risk_a"].items():
        if isinstance(v, dict) and "topk_ex5" in v and v["topk_ex5"]:
            s = v["topk_ex5"]
            print(f"  {k:18s} {v['n_days']:5d}日  mean={s['mean_bp']:+7.1f}  median={s['median_bp']:+7.1f}  胜率={s['win_rate']:.0%}")
    print("===== 风险 B: trend_up K=30 尾部（bp）=====")
    for k in ("rank_1_15", "rank_16_30", "rank_16_30_trim_top2_per_day", "rank_16_30_score_gt60"):
        s = res["risk_b"].get(k)
        if s:
            print(f"  {k:30s} n={s['n']:6d}  mean={s['mean_bp']:+7.1f}  median={s['median_bp']:+7.1f}  胜率={s['win_rate']:.0%}  skew={s['skew']:+.2f}")
    print("  尾部 score>60 覆盖率:", res["risk_b"]["score_gt60_coverage"])
    print("  分年(16-30):", {y: f"{v['mean_bp']:+.1f}bp/{v['n_days']}d" for y, v in res["risk_b"]["rank_16_30_by_year"].items()})


if __name__ == "__main__":
    main()
