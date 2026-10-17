#!/usr/bin/env python3
"""持仓相关性/集中度研究（2026-10-17，议程 A）。

问题: 36 只关注池的名义分散 vs 真实分散（相关聚类）——仓位限额是否高估了分散度？
方法: 2024-01-01~now 日收益 Pearson 相关矩阵 → 平均两两相关、分层聚类(简化: 相关>阈值并查集)、
      有效 N = 1/Σ(1/N)²（等权假设）、板块内 vs 跨板块相关对比。
数据: baostock 日线（与回测同源）。
产出: analysis/research/2026-10-17_correlation_study.json
"""
import os
import sys
import json
import logging
import time
from collections import defaultdict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.getLogger().setLevel(logging.ERROR)

import pandas as pd
import numpy as np

from analysis.backtest_strategies import STOCKS, fetch_data, START_DATE
from analysis.bs_session import logout as bs_logout

SECTOR = {  # 来自 portfolio_sim_state.direction + 电网板块常识
    "000157": "工程机械", "000400": "电网", "000708": "特钢", "000987": "基建金融",
    "002028": "电网", "002130": "电力新材", "002156": "半导体封测", "002270": "电网",
    "002318": "特钢管材", "002335": "数据中心", "002413": "军工电子", "002466": "锂资源",
    "300014": "锂电池", "300124": "工业自动化", "300199": "医药", "300285": "特种陶瓷",
    "300719": "连接器", "300748": "稀土永磁", "600004": "机场", "600036": "银行",
    "600030": "券商", "600089": "电网", "600160": "氟化工", "600346": "炼化一体化",
    "600406": "电网", "600570": "金融IT", "600584": "半导体封测", "600660": "汽车玻璃",
    "601061": "有色贸易", "601066": "券商", "601100": "高端液压", "601766": "轨交",
    "601995": "券商", "603308": "特材管材", "605117": "电网", "688981": "半导体晶圆",
}


def cluster_corr(corr: pd.DataFrame, threshold=0.55):
    """简化层次聚类：平均相关>阈值的连通分量（并查集）。"""
    syms = list(corr.columns)
    parent = {s: s for s in syms}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            if corr.loc[a, b] > threshold:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
    groups = defaultdict(list)
    for s in syms:
        groups[find(s)].append(s)
    return list(groups.values())


def main():
    t0 = time.time()
    closes = {}
    skipped = []
    for idx, (sym, name) in enumerate(STOCKS):
        df = fetch_data(sym, name, start=START_DATE)
        if df is None or len(df) < 100:
            skipped.append(name)
            continue
        closes[sym] = df.set_index("date")["close"].astype(float)
        if (idx + 1) % 12 == 0:
            print(f"[corr] {idx+1}/{len(STOCKS)} ({time.time()-t0:.0f}s)", flush=True)
    try:
        bs_logout()
    except Exception:
        pass

    px = pd.DataFrame(closes).sort_index()
    rets = px.pct_change().dropna(how="all")
    # 仅保留覆盖≥80% 样本的票
    keep = [c for c in rets.columns if rets[c].notna().mean() >= 0.8]
    rets = rets[keep]
    corr = rets.corr()

    iu = np.triu_indices_from(corr.values, k=1)
    pairs = corr.values[iu]
    avg_pair = float(np.mean(pairs))
    med_pair = float(np.median(pairs))

    # 有效 N（等权）
    N = len(keep)
    eff_n = 1.0 / ((1.0 / N) ** 2 * N + (N * (N - 1)) * (1.0 / N) ** 2 * avg_pair) if avg_pair > 0 else float(N)
    # 更精确: eff_N = N / (1 + (N-1)*rho)
    eff_n = N / (1 + (N - 1) * avg_pair)

    groups = cluster_corr(corr, 0.55)
    sec = {s: SECTOR.get(s, "?") for s in keep}

    # 板块内 vs 跨板块
    within, across = [], []
    syms = list(corr.columns)
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            if sec[a] == "?" or sec[b] == "?":
                continue
            (within if sec[a] == sec[b] else across).append(corr.loc[a, b])

    top_pairs = []
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            top_pairs.append((round(float(corr.loc[a, b]), 3), a, b))
    top_pairs.sort(reverse=True)

    out = {
        "date": "2026-10-17", "window": START_DATE, "stocks": N,
        "avg_pairwise_corr": round(avg_pair, 3),
        "median_pairwise_corr": round(med_pair, 3),
        "effective_n": round(eff_n, 1),
        "nominal_n": N,
        "within_sector_corr": round(float(np.mean(within)), 3) if within else None,
        "across_sector_corr": round(float(np.mean(across)), 3) if across else None,
        "corr_clusters(>0.55)": [
            {"stocks": [{"symbol": s, "sector": sec.get(s, "?")} for s in g],
             "size": len(g)} for g in sorted(groups, key=len, reverse=True) if len(g) >= 2],
        "top10_pairs": top_pairs[:10],
        "skipped": skipped,
    }
    path = os.path.join(WORKSPACE, "analysis/research/2026-10-17_correlation_study.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)

    print(f"[corr] 覆盖 {N} 只 | 平均两两相关 {avg_pair:+.3f} | 有效N {eff_n:.1f} / 名义 {N}")
    print(f"[corr] 板块内 {out['within_sector_corr']} vs 跨板块 {out['across_sector_corr']}")
    print(f"[corr] 相关簇(>0.55): " + " | ".join(
        f"{[x['symbol'] for x in g['stocks']]}" for g in out['corr_clusters(>0.55)'][:6]))
    print(f"[corr] Top5 相关对: {top_pairs[:5]}")
    print(f"[corr] 已写 {path} | 耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
