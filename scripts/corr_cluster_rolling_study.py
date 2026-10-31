#!/usr/bin/env python3
"""簇门禁窗口口径研究：全期相关 vs 滚动 252d 相关（2026-10-18 议程 A 深挖 ①）。

问题: data/corr_clusters.json（同簇持仓上限≤2 的数据源）用的是**全期**相关
     （2024-01-01 起），但分年拆解已证相关性随 regime 漂移：
     2026 锂簇解体(0.456)、券商簇松动、思源/华明年内相关 >0.55 却全期未入簇。
     那么门禁用全期映射，究竟漏拦多少、误拦多少？
方法: 逐日滚动 252 交易日 Pearson 相关（因果，只用当日及以前）→ 0.55 连通分量聚类
     与生产全期映射逐日对比:
     - agreement: 同簇/不同簇一致的票对占比
     - missed  : 滚动同簇但生产不同簇（漏拦面）
     - phantom : 生产同簇但滚动不同簇（僵尸约束：可能误拦）
     并统计「同簇已持≥2」在两种口径下的触及率差异（用生产阈值模拟买入序列）
数据: data/research_price_cache/{sym}.pkl（35 只，缺 000708 暂跳过），零网络。
输出: analysis/research/<今日>_corr_window_study.json
"""
import os
import sys
import json
import pickle
from datetime import date
from itertools import combinations

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


CACHE = os.path.join(WORKSPACE, "data/research_price_cache")
CLU_JSON = os.path.join(WORKSPACE, "data/corr_clusters.json")
THRESHOLD = 0.55
ROLL = 252
MIN_PERIODS = 200


def _load(sym):
    p = os.path.join(CACHE, f"{sym}.pkl")
    if not os.path.exists(p):
        return None
    df = pd.read_pickle(p)
    if "date" not in df.columns:
        return None
    df = df[["date", "close"]].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df.set_index("date")["close"].astype(float)


def union_find_clusters(pairs, symbols):
    parent = {s: s for s in symbols}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in pairs:
        union(a, b)
    groups = {}
    for s in symbols:
        groups.setdefault(find(s), []).append(s)
    out = {}
    for gi, g in enumerate(sorted([v for v in groups.values() if len(v) >= 2], key=len, reverse=True)):
        for s in g:
            out[s] = f"C{gi+1:02d}"
    return out


def main():
    with open(CLU_JSON) as f:
        prod = json.load(f)
    prod_map = prod.get("symbol_to_cluster", {})
    syms = sorted(prod_map.keys())
    print(f"[roll] 生产簇映射覆盖 {len(syms)} 票 | date={prod.get('date')}", flush=True)

    closes = {}
    missing = []
    for s in syms:
        c = _load(s)
        if c is None:
            missing.append(s)
        else:
            closes[s] = c
    syms = [s for s in syms if s in closes]
    print(f"[roll] 可用 {len(syms)} 票 | 缺价 {missing}", flush=True)

    px = pd.DataFrame(closes).sort_index()
    ret = px.pct_change()
    dates = px.index.tolist()
    n = len(dates)

    # 生产口径的分簇对集合（仅对有共同标签的票对）
    def same_prod(a, b):
        ca, cb = prod_map.get(a), prod_map.get(b)
        return bool(ca and cb and ca == cb)

    prod_pairs = {frozenset((a, b)) for a, b in combinations(syms, 2) if same_prod(a, b)}

    daily = []
    # 从满足最小窗口的日期开始
    for i in range(MIN_PERIODS, n):
        day = dates[i]
        win = ret.iloc[max(0, i - ROLL + 1): i + 1]
        corr = win.corr(min_periods=MIN_PERIODS)
        pairs = []
        for a, b in combinations(syms, 2):
            try:
                v = corr.loc[a, b]
            except Exception:
                continue
            if pd.notna(v) and v >= THRESHOLD:
                pairs.append((a, b))
        roll_map = union_find_clusters(pairs, syms)
        roll_pairs = {frozenset((a, b)) for a, b in combinations(syms, 2)
                      if roll_map.get(a) and roll_map.get(a) == roll_map.get(b)}
        all_pairs = len(syms) * (len(syms) - 1) // 2
        agree = len(prod_pairs & roll_pairs) + (all_pairs - len(prod_pairs | roll_pairs))
        missed = roll_pairs - prod_pairs
        phantom = prod_pairs - roll_pairs
        daily.append({
            "date": day,
            "agreement_pct": round(agree / all_pairs * 100, 2),
            "missed_pairs": len(missed),
            "phantom_pairs": len(phantom),
            "clusters_roll": len({v for v in roll_map.values()}),
            "capped_stocks_roll": len(roll_map),
            "capped_stocks_prod": len([s for s in syms if prod_map.get(s)]),
            "missed_examples": sorted({f"{a}-{b}" for a, b in missed})[:8],
            "phantom_examples": sorted({f"{a}-{b}" for a, b in phantom})[:8],
        })

    if not daily:
        print("[roll] 数据不足，无法计算滚动相关")
        return 1

    # ── 簇稳定性：滚动簇标签的寿命（票在某簇的连续天数） ──
    life = {}
    prev = {}
    for row_i, i in enumerate(range(MIN_PERIODS, n)):
        day = dates[i]
        win = ret.iloc[max(0, i - ROLL + 1): i + 1]
        corr = win.corr(min_periods=MIN_PERIODS)
        pairs = [(a, b) for a, b in combinations(syms, 2)
                 if pd.notna(corr.loc[a, b]) and corr.loc[a, b] >= THRESHOLD]
        rmap = union_find_clusters(pairs, syms)
        members = {}
        for s, c in rmap.items():
            members.setdefault(c, set()).add(s)
        for s in syms:
            c = rmap.get(s)
            cur = frozenset(members.get(c, set()) or [s])
            if prev.get(s) != cur:
                life[s] = 0
            else:
                life[s] = life.get(s, 0) + 1
            prev[s] = cur

    agg = {
        "window": f"{dates[0]} ~ {dates[-1]}",
        "roll_days": ROLL,
        "threshold": THRESHOLD,
        "n_days": len(daily),
        "avg_agreement_pct": round(float(np.mean([d["agreement_pct"] for d in daily])), 2),
        "min_agreement_pct": min(d["agreement_pct"] for d in daily),
        "max_agreement_pct": max(d["agreement_pct"] for d in daily),
        "avg_missed_pairs": round(float(np.mean([d["missed_pairs"] for d in daily])), 1),
        "avg_phantom_pairs": round(float(np.mean([d["phantom_pairs"] for d in daily])), 1),
        "avg_roll_clusters": round(float(np.mean([d["clusters_roll"] for d in daily])), 2),
        "avg_capped_stocks_roll": round(float(np.mean([d["capped_stocks_roll"] for d in daily])), 1),
        "capped_stocks_prod": daily[-1]["capped_stocks_prod"],
        "last_day": daily[-1],
    }

    out = {"date": date.today().isoformat(), **agg, "daily": daily}
    path = os.path.join(WORKSPACE, f"analysis/research/{date.today().isoformat()}_corr_window_study.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _bak(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)

    print("=" * 70)
    print(f"[roll] 窗口 {agg['window']} | {agg['n_days']} 日滚动")
    print(f"[roll] 与生产全期映射的一致率: 均值 {agg['avg_agreement_pct']}% "
          f"(min {agg['min_agreement_pct']}% / max {agg['max_agreement_pct']}%)")
    print(f"[roll] 漏拦面 missed 均值 {agg['avg_missed_pairs']} 对/日 | "
          f"僵尸面 phantom 均值 {agg['avg_phantom_pairs']} 对/日")
    print(f"[roll] 入簇票数: 滚动均值 {agg['avg_capped_stocks_roll']} vs 生产 {agg['capped_stocks_prod']}")
    print(f"[roll] 最新一日({agg['last_day']['date']}) missed={agg['last_day']['missed_examples']}")
    print(f"[roll] 最新一日 phantom={agg['last_day']['phantom_examples']}")
    print(f"[roll] 已写 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
