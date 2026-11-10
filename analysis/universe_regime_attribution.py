#!/usr/bin/env python3
"""
regime 分段归因（2026-11-10 用户批准三项建议落实，Step 1 数据层）
==================================================================
问题（来自 min_score=45 回测的三项待拍板）:
  ① chip 维保留与否 → 按 regime 分解 chip 在场/缺席的 h5 超额差
  ② TOP_K×regime 联动 → 各 regime 下 top15 超额 + 分数-收益价差
  ③ 均值回归段反向性缓解 → 各 regime 下 D9-D0 价差方向

口径:
  - 样本: 数据湖 800 只全历史（2021-01 起，~1215 交易日）
  - 复用生产 score_stock（两模式: 4d chip在场 / 3d chip缺席）
  - regime: 阈值法逐日标签（trend_up/sideways/trend_down/high_vol）
  - ret_h = close[T+5]/close[T]-1；超额 = 组内均值 - 全池同日均值
  - CI = ±1.96×se（日度超额均值的标准误）

产出: data/universe/regime_attribution.json（主结果先落盘）
运行: nohup python3 analysis/universe_regime_attribution.py > logs/regime_attribution.log 2>&1 &
"""
#!/usr/bin/env python3
import os
import sys
import json
import time
import shutil
import logging

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis"), os.path.join(WORKSPACE, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

OUT_JSON = os.path.join(WORKSPACE, "data", "universe", "regime_attribution.json")
CHIP_SERIES_PKL = os.path.join(WORKSPACE, "data", "universe", "chip_backtest_series.pkl")
TOP_K = 15

_t0 = time.time()
def log(msg):
    print(f"[{time.time()-_t0:7.1f}s] {msg}", flush=True)


def _bak(path):
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
    except Exception as e:
        print(f"⚠️ 备份失败(不阻断): {path}: {e}", file=sys.stderr)


def main():
    logging.disable(logging.CRITICAL)
    from analysis.universe_threshold_backtest import load_lake_data, factor_panel, snap_at
    from analysis.signal_scoring import score_stock

    log("加载数据湖…")
    data = load_lake_data(max_days=5000)   # 全历史
    log(f"样本 {len(data)} 只")
    chip = pd.read_pickle(CHIP_SERIES_PKL)  # {sym: df(date,winner_pct,cost_bias,conc90)}
    chip_lookup = {}
    for sym, m in chip.items():
        for row in m.itertuples(index=False):
            chip_lookup[(sym, str(row.date)[:10])] = (row.winner_pct, row.cost_bias, row.conc90)
    log(f"chip 长表 {len(chip_lookup)} 键")

    panels = factor_panel(data)
    close_pivot = pd.DataFrame({s: p["close"] for s, p in panels.items()})
    ret5 = close_pivot.shift(-5) / close_pivot - 1.0

    # 逐日 regime 标签（与 regime_series 同阈值，输出标签而非 boost）
    closes = close_pivot.dropna(how="all")
    ew = closes.pct_change().mean(axis=1).add(1).cumprod()
    ret = ew.pct_change()
    ma5, ma20, ma60 = ew.rolling(5).mean(), ew.rolling(20).mean(), ew.rolling(60).mean()
    sp5_20, sp20_60 = (ma5 - ma20) / ma20, (ma20 - ma60) / ma60
    vol20, vol60 = ret.rolling(20).std() * np.sqrt(252), ret.rolling(60).std() * np.sqrt(252)
    regime_lbl = pd.Series("sideways", index=ew.index)
    regime_lbl[vol20 > vol60 * 1.5] = "high_vol"
    regime_lbl[(sp5_20 > 0.01) & (sp20_60 > 0) & ~(vol20 > vol60 * 1.5)] = "trend_up"
    regime_lbl[(sp5_20 < -0.01) & (sp20_60 < 0) & ~(vol20 > vol60 * 1.5)] = "trend_down"

    all_dates = sorted(set().union(*[set(p.index) for p in panels.values()]))
    dates = [d for d in all_dates if d in regime_lbl.index]
    dates = [d for d in dates if str(d)[:10] in set(k[1] for k in list(chip_lookup)[:0]) or True]
    # warmup 60 日 + vol60 需要 60 日 → 从第 65 个交易日起
    dates = dates[65:]
    log(f"回放 {len(dates)} 日: {dates[0].date()} ~ {dates[-1].date()}")

    # 逐日打分（4d/3d），记录 (date, regime, mode, sym, score, ret5)
    rows = []
    for ti, ts in enumerate(dates):
        lbl = regime_lbl.get(ts, "sideways")
        for sym, p in panels.items():
            if ts not in p.index:
                continue
            snap = snap_at(panels, chip, sym, ts)
            if snap is None:
                continue
            r5 = ret5.at[ts, sym] if (sym in ret5.columns and pd.notna(ret5.at[ts, sym])) else None
            sc4, _, _ = score_stock(snap)
            snap3 = {k: v for k, v in snap.items() if k not in ("winner_pct", "cost_bias", "conc90")}
            sc3, _, _ = score_stock(snap3)
            rows.append((str(ts)[:10], lbl, sym, sc4, sc3, r5))
        if (ti + 1) % 100 == 0:
            log(f"打分进度 {ti+1}/{len(dates)}")
    df = pd.DataFrame(rows, columns=["date", "regime", "sym", "score4", "score3", "ret5"])
    log(f"打分行 {len(df)}")

    # 全池同日 ret5 均值
    pool = df.groupby("date")["ret5"].mean().rename("pool_r5")
    df = df.join(pool, on="date")
    df["ex5"] = df["ret5"] - df["pool_r5"]

    def seg_stats(g, sc_col):
        """g: 单 regime×mode 行；返回 top15 超额 / low20 超额 / D9-D0 / spearman"""
        from scipy.stats import spearmanr
        out = {}
        # 每日 top15 / low20
        tops, lows = [], []
        for dte, gd in g.groupby("date"):
            gd2 = gd.dropna(subset=["ret5"])
            if len(gd2) < 30:
                continue
            srt = gd2.sort_values(sc_col, ascending=False)
            tops.append(srt.head(TOP_K)["ex5"].mean())
            low_thr = gd2[sc_col].quantile(0.20)
            lows.append(gd2[gd2[sc_col] <= low_thr]["ex5"].mean())
        tops, lows = np.array(tops, dtype=float), np.array(lows, dtype=float)
        tops, lows = tops[~np.isnan(tops)], lows[~np.isnan(lows)]
        if len(tops):
            out["top15_excess_bp"] = round(float(tops.mean()) * 1e4, 2)
            out["top15_excess_ci95"] = [round(float(tops.mean() - 1.96 * tops.std(ddof=1) / np.sqrt(len(tops))) * 1e4, 2),
                                         round(float(tops.mean() + 1.96 * tops.std(ddof=1) / np.sqrt(len(tops))) * 1e4, 2)] if len(tops) > 2 else None
            out["top15_days"] = int(len(tops))
        if len(lows):
            out["low20_excess_bp"] = round(float(lows.mean()) * 1e4, 2)
        # 全池十分位 D9-D0 价差（按分数聚合后再分位，避免日内噪声）
        try:
            q = g.dropna(subset=["ret5"]).copy()
            q["dec"] = pd.qcut(q[sc_col], 10, labels=False, duplicates="drop")
            dm = q.groupby("dec")["ex5"].mean()
            if len(dm) >= 8:
                out["D9_D0_bp"] = round(float(dm.loc[dm.index.max()] - dm.loc[dm.index.min()]) * 1e4, 2)
        except Exception:
            pass
        # spearman（池内）
        sp = spearmanr(g[sc_col], g["ret5"], nan_policy="omit")[0] if len(g.dropna(subset=["ret5"])) > 50 else None
        if sp is not None and np.isfinite(sp):
            out["spearman"] = round(float(sp), 4)
        return out

    results = {"built_at": pd.Timestamp.now().isoformat(),
               "sample": {"n_stocks": len(data), "n_days": len(dates),
                          "start": str(dates[0].date()), "end": str(dates[-1].date()),
                          "top_k": TOP_K, "h": 5}}
    # 1) regime × mode 矩阵
    mat = {}
    for regime in ("trend_up", "sideways", "trend_down", "high_vol"):
        gr = df[df["regime"] == regime]
        if len(gr) < 500:
            continue
        mat[regime] = {"n_days": int(gr["date"].nunique()),
                       "4d": seg_stats(gr, "score4"), "3d": seg_stats(gr, "score3")}
    results["regime_matrix"] = mat

    # 2) 年份矩阵
    df["year"] = df["date"].str[:4]
    yr = {}
    for y, gy in df.groupby("year"):
        yr[y] = {"n_days": int(gy["date"].nunique()),
                 "4d": seg_stats(gy, "score4"), "3d": seg_stats(gy, "score3")}
    results["year_matrix"] = yr

    # 3) 结论字段
    concl = {}
    for regime, m in mat.items():
        d4, d3 = m["4d"], m["3d"]
        concl[regime] = {
            "top15_excess_4d_bp": d4.get("top15_excess_bp"),
            "top15_excess_3d_bp": d3.get("top15_excess_bp"),
            "chip_marginal_bp": (round(d4["top15_excess_bp"] - d3["top15_excess_bp"], 2)
                                  if d4.get("top15_excess_bp") is not None and d3.get("top15_excess_bp") is not None else None),
            "D9_D0_4d_bp": d4.get("D9_D0_bp"),
        }
    results["conclusion_per_regime"] = concl

    # 主结果先落盘（铁律）
    _bak(OUT_JSON)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log(f"✅ 落盘 → {OUT_JSON}")

    print("\n=====REGIME ATTRIBUTION (h5, bp)=====")
    print(f"{'regime':>10} {'days':>5} | {'top15_4d':>9} {'top15_3d':>9} {'chip边际':>8} | {'D9-D0':>8} {'sp4':>7}")
    for regime, c in concl.items():
        m = mat[regime]
        sp4 = m["4d"].get("spearman")
        print(f"{regime:>10} {m['n_days']:>5} | {c['top15_excess_4d_bp']:>9} {c['top15_excess_3d_bp']:>9} "
              f"{c['chip_marginal_bp']:>8} | {c['D9_D0_4d_bp']:>8} {sp4 if sp4 is not None else 0:>7.4f}")
    print("\n=====YEARLY=====")
    for y, m in yr.items():
        print(f"{y}: top15_4d={m['4d'].get('top15_excess_bp')}bp D9-D0={m['4d'].get('D9_D0_bp')}bp days={m['n_days']}")
    print("=====END=====")


if __name__ == "__main__":
    main()
