#!/usr/bin/env python3
"""同簇上限≤2 门禁:全期映射 vs 滚动252d 映射的反事实重放(2026-10-18 深挖①-B)。

为何不用 PortfolioExecutor.run_backtest:
  实测三臂(prod/roll/off)结果逐字节相同 → run_backtest 主循环不含簇拦截
  (cluster 逻辑只存在于 portfolio_core.run_portfolio_scan 日线扫描链路)。
  故改用离线重放:信号流 = dual_signal_eval_days.json(双策略口径,
  即生产信号融合的源头), 10 交易日持有模型, 逐日按时间顺序模拟建仓顺序,
  在买入点分别套 prod / roll 两种簇映射判定拦截, 用 fwd 前向收益评拦截质量。

判定:好门禁 = 拦掉的买入后续收益差(负得多)、放行的买入后续收益好。
输出: analysis/research/<今日>_cluster_cap_counterfactual.json
"""
import os
import sys
import json
from datetime import date
from itertools import combinations

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

CACHE = os.path.join(WORKSPACE, "data", "research_price_cache")
CLU_JSON = os.path.join(WORKSPACE, "data", "corr_clusters.json")
EVAL = os.path.join(WORKSPACE, "analysis", "research", "dual_signal_eval_days.json")
THRESHOLD = 0.55
ROLL = 252
MIN_PERIODS = 200
HOLD_DAYS = 10


def build_roll_maps(closes: pd.DataFrame):
    ret = closes.pct_change()
    syms = list(closes.columns)
    dates = closes.index
    maps = {}
    for i in range(MIN_PERIODS, len(dates)):
        win = ret.iloc[max(0, i - ROLL + 1): i + 1]
        corr = win.corr(min_periods=MIN_PERIODS)
        parent = {s: s for s in syms}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in combinations(syms, 2):
            v = corr.loc[a, b]
            if pd.notna(v) and v >= THRESHOLD:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
        groups = {}
        for s in syms:
            groups.setdefault(find(s), []).append(s)
        m = {}
        for gi, g in enumerate(sorted([v for v in groups.values() if len(v) >= 2], key=len, reverse=True)):
            for s in g:
                m[s] = f"C{gi+1:02d}"
        maps[dates[i]] = m
    return maps


def cluster_held(map_fn, sym, held, day_ix):
    """held: {sym: exit_ix}; 返回同簇已持只数。map_fn(day_ix)->{sym:cluster}"""
    m = map_fn(day_ix)
    c = m.get(sym)
    if not c:
        return 0, m
    return sum(1 for o in held if o != sym and m.get(o) == c), m


def main():
    prod = json.load(open(CLU_JSON))["symbol_to_cluster"]
    recs = json.load(open(EVAL))
    print(f"[cf] 信号流 {len(recs)} 条 | 生产簇 {len(prod)} 票", flush=True)

    # 收盘价对齐(用于滚动相关)
    closes = {}
    for s in sorted({r["symbol"] for r in recs}):
        p = os.path.join(CACHE, f"{s}.pkl")
        if not os.path.exists(p):
            continue
        df = pd.read_pickle(p)
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        closes[s] = df.set_index("date")["close"].astype(float)
    px = pd.DataFrame(closes).sort_index()
    all_days = list(px.index)
    day_ix = {d: i for i, d in enumerate(all_days)}
    print(f"[cf] 滚动映射计算 {px.shape} ...", flush=True)
    roll = build_roll_maps(px)
    print(f"[cf] 滚动映射 {len(roll)} 日", flush=True)

    # 买入候选: actions 含 买入
    buys = [r for r in recs if any("买入" in a for a in r.get("actions", []))]
    buys.sort(key=lambda r: (r["date"], r["symbol"]))
    print(f"[cf] 买入候选 {len(buys)} 笔", flush=True)

    def roll_map_at(i):
        # 取 ≤ 当日最近一个映射(缓存不足期退化为最早映射)
        d = all_days[min(i, len(all_days) - 1)]
        if d in roll:
            return roll[d]
        keys = sorted(roll.keys())
        return roll[keys[0]] if keys else {}

    prod_map_fn = lambda i: prod

    held_prod, held_roll = {}, {}
    stats = {k: [] for k in ("both_pass", "both_block", "prod_only_block", "roll_only_block")}
    for r in buys:
        sym, d = r["symbol"], r["date"]
        i = day_ix.get(d)
        if i is None:
            continue
        fwd5 = (r.get("fwd") or {}).get("5")
        fwd10 = (r.get("fwd") or {}).get("10")
        # 释放到期持仓
        for h in (held_prod, held_roll):
            for o in [o for o, e in h.items() if e <= i]:
                del h[o]
        hp, _ = cluster_held(prod_map_fn, sym, held_prod, i)
        hr, _ = cluster_held(roll_map_at, sym, held_roll, i)
        bp = hp >= 2
        br = hr >= 2
        key = ("prod_block" if bp else "pass") + "_" + ("roll_block" if br else "pass")
        key = {"pass_pass": "both_pass", "prod_block_roll_block": "both_block",
               "prod_block_pass": "prod_only_block", "pass_roll_block": "roll_only_block"}[key]
        stats[key].append({"symbol": sym, "name": r.get("name"), "date": d,
                           "fwd5": fwd5, "fwd10": fwd10, "held_prod": hp, "held_roll": hr})
        if not bp:
            held_prod[sym] = i + HOLD_DAYS
        if not br:
            held_roll[sym] = i + HOLD_DAYS

    out = {"date": date.today().isoformat(), "hold_days": HOLD_DAYS,
           "total_buys": len(buys), "arms": {}}
    for k, v in stats.items():
        f5 = [x["fwd5"] for x in v if x["fwd5"] is not None]
        f10 = [x["fwd10"] for x in v if x["fwd10"] is not None]
        out["arms"][k] = {
            "n": len(v),
            "pct_of_buys": round(len(v) / max(1, len(buys)) * 100, 2),
            "fwd5_mean_pct": round(float(np.mean(f5)) * 100, 3) if f5 else None,
            "fwd5_median_pct": round(float(np.median(f5)) * 100, 3) if f5 else None,
            "fwd10_mean_pct": round(float(np.mean(f10)) * 100, 3) if f10 else None,
            "examples": v[:10],
        }
        print(f"[cf] {k:18s} n={len(v):4d} ({out['arms'][k]['pct_of_buys']:5.2f}%) "
              f"fwd5均值 {out['arms'][k]['fwd5_mean_pct']}% fwd10 {out['arms'][k]['fwd10_mean_pct']}%", flush=True)

    # ── 逐笔配对 bootstrap: roll 比 prod 多拦(roll_only) vs 少拦(prod_only) 的前向差 ──
    # H1: 若滚动口径拦的笔后续更差,则滚动映射更有价值。
    ro = [x["fwd5"] for x in stats["roll_only_block"] if x["fwd5"] is not None]
    po = [x["fwd5"] for x in stats["prod_only_block"] if x["fwd5"] is not None]
    bp = [x["fwd5"] for x in stats["both_pass"] if x["fwd5"] is not None]
    # 全放行组合 (both_pass + prod_only 是 prod 实际放行集; both_pass + roll_only 是 roll 放行集)
    pass_prod = bp + po
    pass_roll = bp + ro
    rng = np.random.default_rng(42)

    def boot_diff(a, b, n=4000):
        a, b = np.array(a), np.array(b)
        if len(a) < 5 or len(b) < 5:
            return None
        d = [float(rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean()) for _ in range(n)]
        lo, hi = np.percentile(d, [2.5, 97.5])
        return {"mean_diff_pct": round(float(np.mean(a) - np.mean(b)) * 100, 3),
                "ci95": [round(float(lo) * 100, 3), round(float(hi) * 100, 3)],
                "p_gt0": round(float(np.mean(np.array(d) > 0)), 3)}

    out["bootstrap"] = {
        "roll_blocked_fwd5_vs_prod_passed_fwd5": boot_diff(ro, pass_prod),
        "roll_passed_fwd5_vs_prod_passed_fwd5": boot_diff(pass_roll, pass_prod),
        "prod_blocked_fwd5_vs_roll_passed_fwd5": boot_diff(po + [x for x in [y["fwd5"] for y in stats["both_block"]] if x is not None], pass_roll) if po else None,
    }

    path = os.path.join(WORKSPACE, f"analysis/research/{date.today().isoformat()}_cluster_cap_counterfactual.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[cf] 已写 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
