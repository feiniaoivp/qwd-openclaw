#!/usr/bin/env python3
"""
外部建议三项判据实验（2026-11-10 晚，用户转发量化评审后要求评估）
================================================================
建议1 sideways 绝对阈值 → 判据: Top-1 分数分组 × top15 超额（有区分度才有效）
建议2 chip→仓位系数   → 判据: winner_pct/cost_bias × 未来 MAE/MFE（阻力假设）
建议3 regime 迟滞     → 判据: regime 日间闪烁率（连续段长度分布）

建议1 需打分（sideways 752 日，nohup 单独跑 regime_advice_test.py）;
本脚本先出建议2/3（无打分依赖，分钟级）。
"""
#!/usr/bin/env python3
import os, sys, json, time, shutil
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, WORKSPACE + "/analysis", WORKSPACE + "/scripts"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import numpy as np
import pandas as pd

_t0 = time.time()
def log(m): print(f"[{time.time()-_t0:6.1f}s] {m}", flush=True)

OUT = os.path.join(WORKSPACE, "data", "universe", "advice_experiments.json")

def _bak(p):
    try:
        if os.path.exists(p): shutil.copy2(p, p + ".bak")
    except Exception as e:
        print(f"⚠️ bak: {e}", file=sys.stderr)

def main():
    logging_on = None
    from analysis.universe_threshold_backtest import load_lake_data, CHIP_SERIES_PKL, regime_series
    data = load_lake_data(max_days=5000)
    chip = pd.read_pickle(CHIP_SERIES_PKL)
    chip_lookup = {}
    for sym, m in chip.items():
        for row in m.itertuples(index=False):
            chip_lookup[(sym, str(row.date)[:10])] = (row.winner_pct, row.cost_bias)

    # ── 建议3: regime 闪烁率 ──
    boost_s = regime_series(data)
    closes = pd.DataFrame({s: d["close"] for s, d in data.items()}).dropna(how="all")
    ew = closes.pct_change().mean(axis=1).add(1).cumprod()
    idx = ew.index
    lbl = pd.Series("sideways", index=idx)
    ret = ew.pct_change()
    ma5, ma20, ma60 = ew.rolling(5).mean(), ew.rolling(20).mean(), ew.rolling(60).mean()
    vol20, vol60 = ret.rolling(20).std() * np.sqrt(252), ret.rolling(60).std() * np.sqrt(252)
    sp5_20, sp20_60 = (ma5 - ma20) / ma20, (ma20 - ma60) / ma60
    lbl[vol20 > vol60 * 1.5] = "high_vol"
    lbl[(sp5_20 > 0.01) & (sp20_60 > 0) & ~(vol20 > vol60 * 1.5)] = "trend_up"
    lbl[(sp5_20 < -0.01) & (sp20_60 < 0) & ~(vol20 > vol60 * 1.5)] = "trend_down"
    seq = lbl.loc[idx].astype(str)
    changes = (seq != seq.shift()).sum() - 1
    # 连续段长度
    runs, cur = [], 1
    vals = seq.tolist()
    for i in range(1, len(vals)):
        if vals[i] == vals[i-1]: cur += 1
        else: runs.append(cur); cur = 1
    runs.append(cur)
    runs = np.array(runs)
    short12 = float((runs[1:-1] <= 2).mean()) if len(runs) > 3 else None  # 中间段 ≤2 日占比
    # trend_up↔sideways 直接互跳次数
    tu_sw = int(((seq == "trend_up") & (seq.shift() == "sideways") | (seq == "sideways") & (seq.shift() == "trend_up")).sum())
    regime_flash = {
        "n_days": int(len(seq)), "transitions": int(changes),
        "transitions_per_year": round(float(changes) / (len(seq) / 244), 1),
        "run_len_median": float(np.median(runs)),
        "short_run_le2_pct_mid": round(short12, 3) if short12 is not None else None,
        "trend_up_sideways_jumps": tu_sw,
        "note": "闪烁率低(段长中位数大/短段占比低) → 迟滞收益小",
    }
    log(f"建议3: {regime_flash}")

    # ── 建议2: chip 阻力假设 × 未来 MAE/MFE ──
    # MAE5 = min(low[T+1..T+5])/close[T]-1；MFE5 = max(high[T+1..T+5])/close[T]-1
    low_p = pd.DataFrame({s: d["low"] for s, d in data.items()})
    high_p = pd.DataFrame({s: d["high"] for s, d in data.items()})
    close_p = pd.DataFrame({s: d["close"] for s, d in data.items()})
    fwd_low_min = low_p.shift(-1).rolling(5, min_periods=3).min().shift(-4)
    fwd_high_max = high_p.shift(-1).rolling(5, min_periods=3).max().shift(-4)
    mae5 = fwd_low_min / close_p - 1.0
    mfe5 = fwd_high_max / close_p - 1.0
    rows = []
    for (sym, ds), (wp, cb) in chip_lookup.items():
        ts = pd.Timestamp(ds)
        if ts not in mae5.index or sym not in mae5.columns:
            continue
        m_, f_ = mae5.at[ts, sym], mfe5.at[ts, sym]
        if pd.isna(m_) or pd.isna(f_):
            continue
        rows.append((ds, sym, wp, cb, m_, f_))
    df = pd.DataFrame(rows, columns=["date", "sym", "wp", "cb", "mae5", "mfe5"])
    log(f"chip×MAE 样本 {len(df)}")
    out2 = {}
    for col, name in (("wp", "winner_pct"), ("cb", "cost_bias")):
        try:
            df[f"q_{col}"] = pd.qcut(df[col], 5, labels=False, duplicates="drop")
            g = df.groupby(f"q_{col}").agg(n=("mae5", "size"), m_mean=(col, "mean"),
                                           mae5=("mae5", "mean"), mfe5=("mfe5", "mean"))
            out2[name] = {str(k): {"n": int(v["n"]), "metric_mean": round(float(v["m_mean"]), 4),
                                    "MAE5_bp": round(float(v["mae5"]) * 1e4, 1), "MFE5_bp": round(float(v["mfe5"]) * 1e4, 1)}
                          for k, v in g.iterrows()}
        except Exception as e:
            log(f"⚠️ {name} 分位失败: {e}")
    from scipy.stats import spearmanr
    sp_mae = spearmanr(df["wp"], df["mae5"], nan_policy="omit")[0]
    sp_mfe = spearmanr(df["wp"], df["mfe5"], nan_policy="omit")[0]
    chip阻力 = {"quintiles": out2, "spearman_wp_mae5": round(float(sp_mae), 4), "spearman_wp_mfe5": round(float(sp_mfe), 4),
             "n": len(df),
             "note": "假设: winner_pct 低=上方套牢盘重=阻力大 → MAE 更深/MFE 受限。若五分位 MAE/MFE 无单调 → 仓位系数无据"}
    log(f"建议2: spearman(wp,MAE5)={sp_mae:.4f}, (wp,MFE5)={sp_mfe:.4f}")

    out = {"built_at": pd.Timestamp.now().isoformat(),
           "advice3_regime_flash": regime_flash,
           "advice2_chip_as_sizer": chip阻力,
           "advice1": "见 advice1_sideways_floor.json（nohup 单独跑）"}
    _bak(OUT)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log(f"✅ → {OUT}")


if __name__ == "__main__":
    main()
