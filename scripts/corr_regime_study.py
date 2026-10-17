#!/usr/bin/env python3
"""相关性的 regime 依赖检验（2026-10-17，A3）。

背景: 10-17 相关性研究给出全期平均两两相关 0.292、有效分散度 3.2/36，并落地
      「同簇持仓上限≤2」。悬而未决: 该相关结构是否跨行情稳定？若震荡市相关性
      系统性抬升（或簇解体），固定阈值 0.55 / 上限 2 的约束力会失真。
方法: 与 correlation_study 同源数据（baostock 日线 2024 起）→ 分年相关矩阵 +
      120 交易日滚动平均两两相关 → 三个口径对照:
        1) 分年 avg pairwise corr（趋势年 vs 震荡年）
        2) 滚动 120d avg corr 时间序列（极值、最新值）
        3) 簇稳定性: 全期簇(>0.55)内股票对在各年的相关是否仍 >0.55（簇是否只属于某年）
产出: analysis/research/2026-10-17_corr_regime_study.json
（不动 data/corr_clusters.json —— 本脚本只读检验，不改生产映射）
"""
import os
import sys
import json
import logging
import time

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.getLogger().setLevel(logging.ERROR)

import pandas as pd
import numpy as np

from analysis.backtest_strategies import STOCKS, fetch_data, START_DATE
from analysis.bs_session import logout as bs_logout

ROLL = 120  # 滚动窗口（交易日）


def avg_pairwise(corr: pd.DataFrame) -> float:
    iu = np.triu_indices_from(corr.values, k=1)
    return float(np.mean(corr.values[iu]))


def main():
    t0 = time.time()
    closes = {}
    skipped = []
    for sym, name in STOCKS:
        df = fetch_data(sym, name, start=START_DATE)
        if df is None or len(df) < 100:
            skipped.append(name)
            continue
        closes[sym] = df.set_index("date")["close"].astype(float)
        if len(closes) % 12 == 0:
            print(f"[corr-regime] {len(closes)}/{len(STOCKS)} ({time.time()-t0:.0f}s)", flush=True)
    try:
        bs_logout()
    except Exception:
        pass

    px = pd.DataFrame(closes).sort_index()
    rets = px.pct_change().dropna(how="all")
    keep = [c for c in rets.columns if rets[c].notna().mean() >= 0.8]
    rets = rets[keep]
    rets.index = pd.to_datetime(rets.index)

    # 1) 分年相关
    by_year = {}
    for y in sorted({int(v.year) for v in rets.index}):
        sub = rets[rets.index.year == y].dropna(how="all").dropna(axis=1, how="any")
        if len(sub) < 60 or sub.shape[1] < 5:
            continue
        c = sub.corr()
        by_year[str(y)] = {
            "days": int(len(sub)),
            "stocks": int(sub.shape[1]),
            "avg_pairwise_corr": round(avg_pairwise(c), 3),
        }

    # 2) 滚动 120d 平均两两相关（每月末采样，控制体量）
    rolling = []
    full_corr = rets.dropna(axis=1, how="any").corr()  # 占位防空
    idx = rets.index
    for end in range(ROLL, len(idx) + 1, 21):  # 每 ~21 交易日
        win = rets.iloc[end - ROLL:end].dropna(axis=1, how="any")
        if win.shape[1] < 10:
            continue
        rolling.append({
            "date": str(idx[end - 1].date()),
            "avg_pairwise_corr": round(avg_pairwise(win.corr()), 3),
            "stocks": int(win.shape[1]),
        })

    # 3) 全期簇稳定性: 读生产簇映射，逐簇逐对分年相关
    clu_path = os.path.join(WORKSPACE, "data/corr_clusters.json")
    cluster_stability = None
    if os.path.exists(clu_path):
        with open(clu_path, encoding="utf-8") as f:
            clu = json.load(f)
        cluster_stability = {}
        year_corr = {}
        for y in sorted({int(v.year) for v in rets.index}):
            sub = rets[rets.index.year == y].dropna(how="all").dropna(axis=1, how="any")
            if len(sub) >= 60:
                year_corr[str(y)] = sub.corr()
        for cid, syms in clu.get("clusters", {}).items():
            syms = [s for s in syms if s in keep]
            if len(syms) < 2:
                continue
            pairs = [(a, b) for i, a in enumerate(syms) for b in syms[i + 1:]]
            per_year = {}
            for y, yc in year_corr.items():
                vals = []
                for a, b in pairs:
                    if a in yc.columns and b in yc.columns:
                        vals.append(float(yc.loc[a, b]))
                if vals:
                    per_year[y] = {
                        "avg": round(float(np.mean(vals)), 3),
                        "frac_above_0.55": round(float(np.mean([v > 0.55 for v in vals])), 2),
                    }
            cluster_stability[cid] = {"size": len(syms), "per_year": per_year}

    # 主结果先落盘再打印（10-17 规约）
    result = {
        "date": "2026-10-17",
        "window": START_DATE,
        "roll_window": ROLL,
        "stocks": len(keep),
        "skipped": skipped,
        "by_year": by_year,
        "rolling": rolling,
        "cluster_stability": cluster_stability,
    }
    out = os.path.join(WORKSPACE, "analysis/research/2026-10-17_corr_regime_study.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print("[corr-regime] 分年平均两两相关:")
    for y, v in by_year.items():
        print(f"  {y}: {v['avg_pairwise_corr']:+.3f} (days={v['days']}, stocks={v['stocks']})")
    if rolling:
        vals = [r["avg_pairwise_corr"] for r in rolling]
        print(f"[corr-regime] 滚动{ROLL}d: min {min(vals):+.3f} | max {max(vals):+.3f} "
              f"| 最新 {rolling[-1]['date']} {rolling[-1]['avg_pairwise_corr']:+.3f}")
    if cluster_stability:
        print("[corr-regime] 簇稳定性（年内均相关 / 0.55 以上配对占比）:")
        for cid, v in sorted(cluster_stability.items()):
            ys = " | ".join(f"{y}:{d['avg']:+.2f}/{d['frac_above_0.55']:.0%}"
                            for y, d in sorted(v["per_year"].items()))
            print(f"  {cid}(n={v['size']}): {ys}")
    print(f"[corr-regime] 已写 {out} ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
