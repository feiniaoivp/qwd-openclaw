#!/usr/bin/env python3
"""
打分行工厂（2026-11-10 晚，补 104 万行未落盘的失误）
====================================================
全历史逐日全池打分 → 落盘 pickle，此后所有判据实验（top-K 敏感性/
相对阈值/trend_down 双峰/分数漂移）秒级复用，不再重跑打分。

口径: 3d = 当前生产（chip 不参与打分，chip_enabled_regimes=[]）。
列: date, regime, sym, score, bb_pos, ret1, ret5
运行: nohup python3 analysis/universe_score_rows.py > logs/score_rows.log 2>&1 &
"""
#!/usr/bin/env python3
import os, sys, time, shutil
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, WORKSPACE + "/analysis", WORKSPACE + "/scripts"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import numpy as np
import pandas as pd

_t0 = time.time()
def log(m): print(f"[{time.time()-_t0:6.1f}s] {m}", flush=True)

OUT_PKL = os.path.join(WORKSPACE, "data", "universe", "score_rows_3d.pkl")
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
    from analysis.signal_scoring import score_stock, HARD_GATES

    data = load_lake_data(max_days=5000)
    chip = pd.read_pickle(CHIP_SERIES_PKL)
    panels = factor_panel(data)
    close_pivot = pd.DataFrame({s: p["close"] for s, p in panels.items()})
    ret1 = close_pivot.shift(-1) / close_pivot - 1.0
    ret5 = close_pivot.shift(-5) / close_pivot - 1.0

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
    dates = [d for d in all_dates[65:] if d in lbl.index]
    log(f"回放 {len(dates)} 日: {dates[0].date()} ~ {dates[-1].date()}")

    rows = []
    for ti, ts in enumerate(dates):
        lblv = lbl.get(ts, "sideways")
        pool_r1 = ret1.loc[ts].dropna()
        pool_r5 = ret5.loc[ts].dropna()
        pool_mean_r5 = float(pool_r5.mean()) if len(pool_r5) else np.nan
        day_scores = []
        for sym, p in panels.items():
            if ts not in p.index:
                continue
            snap = snap_at(panels, chip, sym, ts)
            if snap is None:
                continue
            total, _, _ = score_stock(snap)   # 3d 口径 = 当前生产
            r1 = ret1.at[ts, sym] if (sym in ret1.columns and pd.notna(ret1.at[ts, sym])) else np.nan
            r5 = ret5.at[ts, sym] if (sym in ret5.columns and pd.notna(ret5.at[ts, sym])) else np.nan
            bb = snap.get("bb_pos")
            day_scores.append((str(ts)[:10], lblv, sym, total,
                               float(bb) if bb is not None else np.nan, r1, r5, pool_mean_r5))
        rows += day_scores
        if (ti + 1) % 100 == 0:
            log(f"打分进度 {ti+1}/{len(dates)}")

    df = pd.DataFrame(rows, columns=["date", "regime", "sym", "score", "bb_pos", "ret1", "ret5", "pool_r5"])
    df["ex5"] = df["ret5"] - df["pool_r5"]
    _bak(OUT_PKL)
    pd.to_pickle(df, OUT_PKL)
    log(f"✅ {len(df)} 行 → {OUT_PKL}")


if __name__ == "__main__":
    main()
