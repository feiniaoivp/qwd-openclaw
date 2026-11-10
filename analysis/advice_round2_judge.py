#!/usr/bin/env python3
"""
评审第二轮两建议判据 + top-K 敏感性（2026-11-10 晚，读 score_rows_3d.pkl 秒级）
==============================================================================
建议A 相对分位数阈值 → 判据: z_top1=(top1-median)/std 分组 × top15 超额；
      与绝对阈值(≥90)选中日对比交集/差集
建议B trend_down 极端反转双峰 → 判据: top15 票 ex5 分布 GMM 1v2 分量 BIC + 直方图
附带: top-K 敏感性(5/10/15/30)×regime（周末任务提前）；分数漂移月度序列
产出: data/universe/advice_round2.json
"""
#!/usr/bin/env python3
import os, sys, json, shutil
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, WORKSPACE + "/analysis"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import numpy as np
import pandas as pd

PKL = os.path.join(WORKSPACE, "data", "universe", "score_rows_3d.pkl")
OUT = os.path.join(WORKSPACE, "data", "universe", "advice_round2.json")


def _bak(p):
    try:
        if os.path.exists(p): shutil.copy2(p, p + ".bak")
    except Exception as e:
        print(f"⚠️ bak: {e}", file=sys.stderr)


def topk_excess(df, k):
    """逐日 topK 等权 h5 超额（含 bb_pos 门槛 0.15-0.92，生产口径）。"""
    outs = []
    for dte, g in df.groupby("date"):
        g2 = g[(g["bb_pos"].isna()) | ((g["bb_pos"] >= 0.15) & (g["bb_pos"] <= 0.92))]
        g2 = g2.dropna(subset=["ret5"])
        if len(g2) < 30:
            continue
        srt = g2.sort_values("score", ascending=False).head(k)
        outs.append((dte, srt["ex5"].mean(), g2["regime"].iloc[0]))
    return pd.DataFrame(outs, columns=["date", "ex5", "regime"])


def ci(x):
    x = np.asarray(x, dtype=float); x = x[~np.isnan(x)]
    if len(x) < 3: return None
    return [round(float(x.mean() - 1.96 * x.std(ddof=1) / np.sqrt(len(x))) * 1e4, 2),
            round(float(x.mean() + 1.96 * x.std(ddof=1) / np.sqrt(len(x))) * 1e4, 2)]


def main():
    df = pd.read_pickle(PKL)
    df["date_ts"] = pd.to_datetime(df["date"])
    res = {"built_at": pd.Timestamp.now().isoformat(), "n_rows": int(len(df))}

    # ── 附带1: top-K 敏感性 × regime ──
    tk = {}
    for k in (5, 10, 15, 30):
        t = topk_excess(df, k)
        for regime, g in t.groupby("regime"):
            tk.setdefault(regime, {})[f"K{k}"] = {
                "days": int(len(g)), "ex5_bp": round(float(g["ex5"].mean()) * 1e4, 2),
                "ci95": ci(g["ex5"])}
    res["topk_sensitivity"] = tk

    # ── 建议A: 相对阈值 vs 绝对阈值（sideways 段）──
    sw = df[df["regime"] == "sideways"]
    day_stat = sw.groupby("date").agg(median=("score", "median"), std=("score", "std"),
                                      top1=("score", "max"), n=("score", "size"))
    top15_map = {}
    for dte, g in sw.groupby("date"):
        g2 = g[((g["bb_pos"].isna()) | ((g["bb_pos"] >= 0.15) & (g["bb_pos"] <= 0.92)))].dropna(subset=["ret5"])
        if len(g2) < 30: continue
        top15_map[dte] = float(g2.sort_values("score", ascending=False).head(15)["ex5"].mean())
    day_stat["top15_ex5"] = day_stat.index.map(top15_map)
    ds = day_stat.dropna(subset=["top15_ex5"]).copy()
    ds["z_top1"] = (ds["top1"] - ds["median"]) / ds["std"].replace(0, np.nan)
    # 三分位 z_top1 分组
    ds["z_band"] = pd.qcut(ds["z_top1"], 3, labels=["low", "mid", "high"])
    rel = {}
    for b, g in ds.groupby("z_band", observed=True):
        rel[str(b)] = {"days": int(len(g)), "z_mean": round(float(g["z_top1"].mean()), 3),
                       "top1_mean": round(float(g["top1"].mean()), 1),
                       "top15_ex5_bp": round(float(g["top15_ex5"].mean()) * 1e4, 2), "ci95": ci(g["top15_ex5"])}
    abs_sel = set(ds[ds["top1"] >= 90].index)
    z_hi = set(ds[ds["z_band"] == "high"].index)
    both = abs_sel & z_hi
    only_z = z_hi - abs_sel
    only_abs = abs_sel - z_hi
    cmp_ = {
        "abs90_days": len(abs_sel), "zhigh_days": len(z_hi),
        "overlap": len(both),
        "only_zhigh_days": len(only_z),
        "only_zhigh_ex5_bp": round(float(ds.loc[list(only_z), "top15_ex5"].mean()) * 1e4, 2) if only_z else None,
        "only_abs_days": len(only_abs),
        "only_abs_ex5_bp": round(float(ds.loc[list(only_abs), "top15_ex5"].mean()) * 1e4, 2) if only_abs else None,
    }
    # 分数漂移（月度）
    ds2 = ds.copy(); ds2["month"] = ds2.index.str[:7]
    drift = ds2.groupby("month").agg(median=("median", "mean"), std=("std", "mean"), top1=("top1", "mean"))
    drift_s = {m: {"pool_median": round(float(v["median"]), 1), "pool_std": round(float(v["std"]), 1),
                    "top1": round(float(v["top1"]), 1)} for m, v in drift.iterrows()}
    res["adviceA_relative_floor"] = {"z_top1_bands": rel, "abs_vs_rel": cmp_,
                                      "pool_score_drift_monthly": drift_s,
                                      "note": "判据: z_high 带超额若不优于 abs90 且 overlap 高 → 相对阈值无增量; only_zhigh 日超额好 → 相对阈值捕捉到绝对阈值漏掉的机会"}

    # ── 建议B: trend_down 双峰 ──
    td = df[(df["regime"] == "trend_down")].dropna(subset=["ex5"])
    top15_rows = []
    for dte, g in td.groupby("date"):
        g2 = g[((g["bb_pos"].isna()) | ((g["bb_pos"] >= 0.15) & (g["bb_pos"] <= 0.92)))]
        srt = g2.sort_values("score", ascending=False).head(15)
        top15_rows.append(srt)
    td15 = pd.concat(top15_rows)
    x = td15["ex5"].dropna().values
    bic = {}
    try:
        from sklearn.mixture import GaussianMixture
        X = x.reshape(-1, 1)
        for n in (1, 2, 3):
            gm = GaussianMixture(n_components=n, random_state=42, n_init=3).fit(X)
            bic[n] = round(float(gm.bic(X)), 1)
        best2 = GaussianMixture(n_components=2, random_state=42, n_init=3).fit(X)
        means = sorted(float(m[0]) for m in best2.means_)
        weights = [round(float(w), 3) for w in best2.weights_]
    except Exception as e:
        bic = {"error": str(e)}; means = []; weights = []
    from scipy.stats import skew, kurtosis
    res["adviceB_trenddown_bimodal"] = {
        "n": int(len(x)), "gmm_bic": bic, "gmm2_means_bp": [round(m * 1e4, 1) for m in means],
        "gmm2_weights": weights,
        "skew": round(float(skew(x)), 3), "kurtosis": round(float(kurtosis(x)), 3),
        "mean_bp": round(float(x.mean()) * 1e4, 2), "ci95": ci(x),
        "p10_p90_bp": [round(float(np.percentile(x, 10)) * 1e4, 1), round(float(np.percentile(x, 90)) * 1e4, 1)],
        "hist_bp": {f"{int(lo*1e4)}~{int(hi*1e4)}": int(((x >= lo) & (x < hi)).sum())
                     for lo, hi in zip(np.arange(-0.20, 0.20, 0.04), np.arange(-0.16, 0.24, 0.04))},
        "note": "判据: GMM BIC 2分量显著优于1分量 且 两均值符号相反且距离大 → 双峰(伏击区有据); 单峰/2分量同号 → 维持减仓"}

    _bak(OUT)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f"✅ → {OUT}")
    # 控制台摘要
    print("\n== top-K 敏感性 (h5 超额 bp) ==")
    print(f"{'regime':>10} {'K5':>8} {'K10':>8} {'K15':>8} {'K30':>8}")
    for regime, m in tk.items():
        print(f"{regime:>10} " + " ".join(f"{m.get(f'K{k}',{}).get('ex5_bp','-'):>8}" for k in (5, 10, 15, 30)))
    print("\n== 建议A: z_top1 三分位 ==")
    for b, m in rel.items():
        print(f"  {b}: days={m['days']} top15超额={m['top15_ex5_bp']}bp CI={m['ci95']}")
    print(f"  abs90 vs z_high: {cmp_}")
    print("\n== 建议B: trend_down top15 ex5 分布 ==")
    print(json.dumps(res["adviceB_trenddown_bimodal"], ensure_ascii=False)[:600])


if __name__ == "__main__":
    main()
