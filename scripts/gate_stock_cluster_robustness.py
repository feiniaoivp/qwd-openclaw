#!/usr/bin/env python3
"""门控 55 线的个股聚类稳健性（2026-10-18 深挖）。

背景
----
55 线标定（全市场情绪口径，-2.17% 显著）的低分样本存在**个股重复**：
2025 年低分组 14 条里安达维尔占 8 条。bootstrap 按「交易日块」重采样，
没有处理「同一只票多个信号日」的个体自相关 → 显著性可能被少数票的路径主导。

本脚本做三个口径的稳健性检验（生产口径 up/(up+down) + active 中性 0.5）：
  1. **LOSO（逐只剔除）**：每次剔除一只票重算 55 线差异，
     看结论是否被某一只票翻转（若剔除安达维尔后差异消失 → 显著性是假象）
  2. **个股聚类 bootstrap**：以「票」为单位重采样（保留每票全部信号），
     保持票内相关结构，得到保守的显著性
  3. **纯票级均值对比**：每票取其低分信号均值 vs 其他信号均值，
     按票配对，做配对检验

铁律: 只读。
"""
import os
import sys
import json
import random
import statistics as st
from collections import defaultdict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd

from analysis.net_guard import install_default_timeout
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)

install_default_timeout(15)

CACHE = os.path.join(WORKSPACE, "data", "research_price_cache")
FUND = os.path.join(WORKSPACE, "data", "research_fund_history.json")
BREADTH = os.path.join(WORKSPACE, "data", "market_breadth_history.json")
EVAL = os.path.join(WORKSPACE, "analysis", "research", "dual_signal_eval_days.json")
HOLD = 5
LINE = 55.0


def load_prices():
    out = {}
    for fn in os.listdir(CACHE):
        if not fn.endswith(".pkl") or fn.startswith("chip"):
            continue
        sym = fn[:-4]
        df = pd.read_pickle(os.path.join(CACHE, fn))
        if "date" not in df.columns or "close" not in df.columns:
            continue
        df = df.copy()
        df["_d"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df = df[df["_d"] != "1970-01-01"].drop_duplicates("_d").sort_values("_d")
        out[sym] = df.set_index("_d")["close"].astype(float)
    return out


def _norm01(v, lo, hi):
    if v is None or hi <= lo:
        return None
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def fund_score(r):
    s_roe = _norm01(r.get("roe"), 0, 0.25)
    s_yoy = _norm01(r.get("yoy_pni"), -100, 100)
    s_npm = _norm01(r.get("np_margin"), 0, 30)
    parts = [p for p in (s_roe, s_yoy, s_npm) if p is not None]
    return sum(parts) / len(parts) * 100 if parts else None


def build_recs():
    fund_hist = json.load(open(FUND))
    fund_hist = {s: sorted(v, key=lambda x: x["pub"]) for s, v in fund_hist.items()}
    breadth = json.load(open(BREADTH))
    prices = load_prices()
    IDX = {sym: {str(d)[:10]: k for k, d in enumerate(ser.index)} for sym, ser in prices.items()}
    bench = defaultdict(list)
    for sym, ser in prices.items():
        a = ser.values
        ds = [str(x)[:10] for x in ser.index]
        for i in range(len(a) - HOLD):
            if a[i] > 0:
                bench[ds[i]].append((a[i + HOLD] / a[i] - 1) * 100)
    bench5 = {d: sum(v) / len(v) for d, v in bench.items() if v}
    rows = json.load(open(EVAL))
    sigs = [r for r in rows if any("买入" in str(a) for a in (r.get("actions") or []))]
    recs = []
    for s in sigs:
        sym = str(s.get("symbol") or "")
        d = str(s.get("date") or "")[:10]
        ser = prices.get(sym)
        if ser is None or sym not in IDX or d not in IDX[sym]:
            continue
        i = IDX[sym][d]
        if i + HOLD >= len(ser.values) or d not in bench5:
            continue
        fr = None
        for r in fund_hist.get(sym, []):
            if r["pub"] <= d:
                fr = r
            else:
                break
        fund = fund_score(fr) if fr else None
        b = breadth.get(d)
        if not b:
            continue
        ud, dd = b["up"], b["down"]
        if ud + dd <= 0:
            continue
        sent = (ud / (ud + dd)) * 70 + 15.0        # 生产口径 + active 中性 0.5
        score = 40.5 + (0.30 * fund if fund is not None else 0) + 0.25 * sent
        ex5 = (ser.values[i + HOLD] / ser.values[i] - 1) * 100 - bench5[d]
        recs.append({"sym": sym, "name": s.get("name"), "date": d,
                     "score": score, "ex5": ex5})
    return recs


def diff_of(recs):
    blk = [r["ex5"] for r in recs if r["score"] < LINE]
    pas = [r["ex5"] for r in recs if r["score"] >= LINE]
    if not blk or not pas:
        return None, len(blk)
    return st.mean(blk) - st.mean(pas), len(blk)


def main():
    recs = build_recs()
    base_diff, n_blk = diff_of(recs)
    print(f"总样本 {len(recs)} / 低分 {n_blk} / 基准差异 {base_diff:+.2f}%")

    # ── 1. LOSO 逐只剔除 ──
    print("\n=== 1. LOSO（逐只剔除后重算 55 线差异）===")
    syms = sorted({r["sym"] for r in recs})
    loso = []
    for sym in syms:
        sub = [r for r in recs if r["sym"] != sym]
        d, nb = diff_of(sub)
        name = next((r["name"] for r in recs if r["sym"] == sym), sym)
        n_low_sym = sum(1 for r in recs if r["sym"] == sym and r["score"] < LINE)
        if d is not None:
            loso.append((d, sym, name, n_low_sym))
    loso.sort()
    print(f"  剔除后差异范围: [{loso[0][0]:+.2f}%, {loso[-1][0]:+.2f}%]")
    print("  剔除后最弱的 5 个（差异最小）:")
    for d, sym, name, nlow in loso[:5]:
        print(f"    剔除 {name}({sym}) [该票低分{nlow}条] → 差异 {d:+.2f}%")
    neg = sum(1 for d, *_ in loso if d < 0)
    print(f"  35 只中剔除后差异仍为负的: {neg}/{len(loso)}")

    # ── 2. 个股聚类 bootstrap（以票为单位重采样，保留票内全部信号）──
    print("\n=== 2. 个股聚类 bootstrap（保守显著性）===")
    by_sym = defaultdict(list)
    for r in recs:
        by_sym[r["sym"]].append(r)
    syms = list(by_sym)
    rng = random.Random(42)
    diffs = []
    for _ in range(5000):
        sample = [rng.choice(syms) for _ in syms]
        sub = [r for s in sample for r in by_sym[s]]
        d, _ = diff_of(sub)
        if d is not None:
            diffs.append(d)
    diffs.sort()
    lo, hi = diffs[int(len(diffs) * 0.025)], diffs[int(len(diffs) * 0.975)]
    p_le0 = sum(1 for x in diffs if x <= 0) / len(diffs)
    sig = "显著" if (lo > 0 or hi < 0) else "不显著"
    print(f"  聚类 bootstrap: 差异 {base_diff:+.2f}%  CI95 [{lo:+.2f}, {hi:+.2f}]  {sig}（P(diff≤0)={p_le0:.3f}）")

    # ── 3. 票级配对（每票低分均值 vs 高分均值，配对差）──
    print("\n=== 3. 票级配对（同一票的低分信号 vs 高分信号）===")
    pairs = []
    for sym, rs in by_sym.items():
        lo5 = [r["ex5"] for r in rs if r["score"] < LINE]
        hi5 = [r["ex5"] for r in rs if r["score"] >= LINE]
        if lo5 and hi5:
            pairs.append((sym, st.mean(lo5) - st.mean(hi5), len(lo5), len(hi5)))
    print(f"  有配对的票: {len(pairs)} 只（两档都有信号的票才计入）")
    for sym, dp, nl, nh in sorted(pairs, key=lambda x: x[1]):
        name = next(r["name"] for r in recs if r["sym"] == sym)
        print(f"    {name}({sym}): 低分{nl}条-高分{nh}条 → 低-高 {dp:+.2f}%")
    if pairs:
        dp_list = [p[1] for p in pairs]
        print(f"  配对差均值 {st.mean(dp_list):+.2f}%  中位 {st.median(dp_list):+.2f}%  "
              f"负差票数 {sum(1 for x in dp_list if x < 0)}/{len(dp_list)}")

    # 结论落盘
    out = {
        "date": __import__("datetime").date.today().isoformat(),
        "line": LINE, "n_signals": len(recs), "n_low": n_blk,
        "base_diff": round(base_diff, 3),
        "loso": {"range": [round(loso[0][0], 3), round(loso[-1][0], 3)],
                 "negative_after_drop": f"{neg}/{len(loso)}",
                 "weakest5": [{"drop": f"{n}({s})", "low_n": nl, "diff": round(d, 3)}
                              for d, s, n, nl in loso[:5]]},
        "cluster_bootstrap": {"ci95": [round(lo, 3), round(hi, 3)],
                              "significant": sig == "显著", "p_le0": round(p_le0, 3)},
        "paired": {"n_pairs": len(pairs),
                   "mean": round(st.mean([p[1] for p in pairs]), 3) if pairs else None,
                   "negative": sum(1 for p in pairs if p[1] < 0)} if pairs else None,
    }
    path = os.path.join(WORKSPACE, "analysis", "research",
                        f"{out['date']}_gate_stock_cluster_robustness.json")
    _bak(path)
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n📄 落盘: {os.path.relpath(path, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
