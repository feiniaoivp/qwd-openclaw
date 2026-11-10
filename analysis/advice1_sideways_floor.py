#!/usr/bin/env python3
"""
建议1 判据实验: sideways 段「绝对得分阈值」有效性（2026-11-10 晚）
==================================================================
外部建议: Top-1 Score < Threshold 时候选归零（避免震荡段强行买烂票）。
判据: sideways 段按每日 Top-1 分数分组 → 各组 top15 等权 h5 超额。
  - 若高 Top-1 组超额并不更好/更差 → 绝对阈值无区分度（排名截断已足够）
  - 若 Top-1 ≥ X 组转正 → 阈值=X 有据
产出: data/universe/advice1_sideways_floor.json
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

OUT = os.path.join(WORKSPACE, "data", "universe", "advice1_sideways_floor.json")
CHIP_SERIES_PKL = os.path.join(WORKSPACE, "data", "universe", "chip_backtest_series.pkl")

def _bak(p):
    try:
        if os.path.exists(p): shutil.copy2(p, p + ".bak")
    except Exception as e:
        print(f"⚠️ bak: {e}", file=sys.stderr)

def main():
    import logging
    logging.disable(logging.CRITICAL)
    from analysis.universe_threshold_backtest import load_lake_data, factor_panel, snap_at
    from analysis.signal_scoring import score_stock

    data = load_lake_data(max_days=5000)
    chip = pd.read_pickle(CHIP_SERIES_PKL)
    panels = factor_panel(data)
    close_pivot = pd.DataFrame({s: p["close"] for s, p in panels.items()})
    ret5 = close_pivot.shift(-5) / close_pivot - 1.0

    # sideways 日期（与归因脚本同阈值）
    ew = close_pivot.pct_change().mean(axis=1).add(1).cumprod()
    ret = ew.pct_change()
    ma5, ma20, ma60 = ew.rolling(5).mean(), ew.rolling(20).mean(), ew.rolling(60).mean()
    vol20, vol60 = ret.rolling(20).std() * np.sqrt(252), ret.rolling(60).std() * np.sqrt(252)
    sp5_20, sp20_60 = (ma5 - ma20) / ma20, (ma20 - ma60) / ma60
    lbl = pd.Series("sideways", index=ew.index)
    lbl[vol20 > vol60 * 1.5] = "high_vol"
    lbl[(sp5_20 > 0.01) & (sp20_60 > 0) & ~(vol20 > vol60 * 1.5)] = "trend_up"
    lbl[(sp5_20 < -0.01) & (sp20_60 < 0) & ~(vol20 > vol60 * 1.5)] = "trend_down"
    all_dates = sorted(set().union(*[set(p.index) for p in panels.values()]))
    dates = [d for d in all_dates[65:] if d in lbl.index and lbl.get(d) == "sideways"]
    log(f"sideways 段 {len(dates)} 日")

    rows = []
    for ti, ts in enumerate(dates):
        scored = []
        for sym, p in panels.items():
            if ts not in p.index: continue
            snap = snap_at(panels, chip, sym, ts)
            if snap is None: continue
            total, _, _ = score_stock(snap)
            scored.append((sym, total))
        pool_r5 = ret5.loc[ts].dropna()
        if not len(pool_r5) or not scored:
            continue
        srt = sorted(scored, key=lambda x: x[1], reverse=True)
        top1 = srt[0][1]
        top15 = [ret5.at[ts, s] for s, _ in srt[:15] if pd.notna(ret5.at[ts, s])]
        rows.append((str(ts)[:10], top1, float(np.mean(top15)) if top15 else np.nan,
                     float(pool_r5.mean())))
        if (ti + 1) % 100 == 0:
            log(f"进度 {ti+1}/{len(dates)}")

    df = pd.DataFrame(rows, columns=["date", "top1", "top15_r5", "pool_r5"])
    df["ex5"] = df["top15_r5"] - df["pool_r5"]
    df["band"] = pd.cut(df["top1"], [0, 70, 80, 90, 100], labels=["<70", "70-80", "80-90", ">=90"])
    g = df.groupby("band", observed=True).agg(days=("ex5", "size"), top1_mean=("top1", "mean"),
                                              top15_ex5_bp=("ex5", lambda x: x.mean() * 1e4),
                                              se=("ex5", lambda x: x.std(ddof=1) / np.sqrt(max(len(x), 1)) * 1e4))
    g["ci_lo"] = g["top15_ex5_bp"] - 1.96 * g["se"]
    g["ci_hi"] = g["top15_ex5_bp"] + 1.96 * g["se"]
    out = {"built_at": pd.Timestamp.now().isoformat(),
           "sideways_days": int(len(df)),
           "bands": {str(k): {"days": int(v["days"]), "top1_mean": round(float(v["top1_mean"]), 1),
                               "top15_ex5_bp": round(float(v["top15_ex5_bp"]), 2),
                               "ci95": [round(float(v["ci_lo"]), 2), round(float(v["ci_hi"]), 2)]}
                     for k, v in g.iterrows()},
           "note": "判据: 若各带超额无单调/高分带不转正 → Top-1 绝对阈值无据，维持 TOP_K 缩量"}
    _bak(OUT)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log(f"✅ → {OUT}")
    print(json.dumps(out["bands"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
