#!/usr/bin/env python3
"""Gap 成本重放研究：开盘跳空对买入执行成本的侵蚀（2026-10-18）。

问题
----
信号在**收盘**生成、次日开盘执行。回测/事件研究里通常用 T 日收盘价买入，
这在实盘不可实现（收盘后已无法成交）。真实的执行价是 **T+1 开盘**，
而隔夜跳空（gap）会系统性改变入场成本——尤其对「强势追入」类信号，
跳空高开就是隐形成本。

本脚本做三件事
--------------
1. **gap 分布**: 全池 T+1 开盘 / T 收盘 - 1 的分布（按信号方向分层）
2. **成本侵蚀**: 同一批买入信号，用「T 收盘价」vs「T+1 开盘价」两种口径
   各算 5 日收益，差额 = gap 带来的执行成本（正=高开吃掉收益，负=低开白送）
3. **净收益存活检验**: 扣 gap 后 5 日超额是否仍显著（bootstrap 95%CI）

口径
----
* 信号: analysis/research/dual_signal_eval_days.json（生产同源买入口径）
* 价格: data/research_price_cache/{sym}.pkl（前复权 open/close，只读）
* 收益: 两种入场价 → 都到 T+1+HOLD 日收盘卖出
    A 理想（回测口径）: close(T) → close(T+1+HOLD)
    B 现实（可执行）  : open(T+1) → close(T+1+HOLD)
  差额 = B − A
* 基准: 同口径全池等权（每臂各自减自己的基准），避免基准口径混淆
* 铁律: 只读，绝不写 data/portfolio_*.json / portfolio_equity.csv

用法
----
  python3 scripts/gap_cost_replay.py
  python3 scripts/gap_cost_replay.py --quick
"""
import os
import sys
import json
import random
import statistics as st
from collections import defaultdict
from datetime import date

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd

from analysis.net_guard import install_default_timeout

install_default_timeout(15)

CACHE = os.path.join(WORKSPACE, "data", "research_price_cache")
OUT_DIR = os.path.join(WORKSPACE, "analysis", "research")
HOLD = 5
TODAY = date.today().isoformat()
QUICK = "--quick" in sys.argv


def load_prices():
    out = {}
    if not os.path.isdir(CACHE):
        return out
    for fn in os.listdir(CACHE):
        if not fn.endswith(".pkl") or fn.startswith(("chip_", "chipmetrics_")):
            continue
        sym = fn[:-4]
        try:
            df = pd.read_pickle(os.path.join(CACHE, fn))
            if "date" not in df.columns or "open" not in df.columns:
                continue
            df = df.copy()
            df["_d"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            df = df[df["_d"] != "1970-01-01"].drop_duplicates("_d").sort_values("_d")
            out[sym] = df.set_index("_d")[["open", "close"]].astype(float)
        except Exception as e:
            print(f"⚠️ {fn} 读取失败: {e}")
    return out


def load_signals():
    path = os.path.join(OUT_DIR, "dual_signal_eval_days.json")
    with open(path) as f:
        rows = json.load(f)
    sig = []
    for r in rows:
        if not any("买入" in str(a) for a in (r.get("actions") or [])):
            continue
        sym = str(r.get("symbol") or "")
        d = str(r.get("date") or "")[:10]
        if sym and d:
            sig.append({"sym": sym, "date": d, "cls": r.get("cls")})
    sig.sort(key=lambda x: x["date"])
    if QUICK:
        step = max(1, len(sig) // 300)
        sig = sig[::step][:300]
    return sig


def boot_ci(vals, n=4000, seed=42):
    if len(vals) < 20:
        return None
    rng = random.Random(seed)
    a = list(vals)
    ms = []
    for _ in range(n):
        ms.append(sum(rng.choice(a) for _ in a) / len(a))
    ms.sort()
    return ms[int(n * 0.025)], ms[int(n * 0.975)]


def main():
    prices = load_prices()
    sigs = load_signals()
    print(f"价格缓存 {len(prices)} 只 / 买入信号 {len(sigs)} 条")
    if not prices or not sigs:
        print("❌ 输入不足")
        return 1

    IDX = {s: {d: k for k, d in enumerate(df.index)} for s, df in prices.items()}

    # ── 1. gap 分布（全池，全交易日）────────────────────────────
    gaps = []
    for sym, df in prices.items():
        o = df["open"].values
        c = df["close"].values
        for i in range(1, len(o)):
            if c[i - 1] > 0:
                gaps.append((o[i] / c[i - 1] - 1) * 100)
    if gaps:
        gaps.sort()
        print(f"\n全池隔夜跳空 (n={len(gaps)}): 均值 {st.mean(gaps):+.3f}% "
              f"中位 {st.median(gaps):+.3f}% | 高开占比 {sum(1 for g in gaps if g > 0)/len(gaps):.1%}")

    # ── 2/3. 两臂收益对比 ────────────────────────────────────
    # 基准：同口径全池等权（每臂独立）
    benchA = defaultdict(list)   # close(T) → close(T+HOLD)
    benchB = defaultdict(list)   # close(T) → open(T+1)
    for sym, df in prices.items():
        c = df["close"].values
        o = df["open"].values
        ds = list(df.index)
        for i in range(len(c)):
            if i + 1 < len(c) and c[i] > 0:
                benchB[ds[i]].append((o[i + 1] / c[i] - 1) * 100)
            if i + 1 + HOLD < len(c) and c[i] > 0:
                benchA[ds[i]].append((c[i + 1 + HOLD] / c[i] - 1) * 100)
    bA = {d: sum(v) / len(v) for d, v in benchA.items() if v}
    bB = {d: sum(v) / len(v) for d, v in benchB.items() if v}

    # 🔴 2026-10-18 口径修正：两臂**必须用同一个基准**，否则超额差里混入基准口径差。
    # 首版用 benchA=close(T)→close(T+HOLD) 与 benchB=close(T)→open(T+1) 当各自基准，
    # 两者的持有期完全不同（6 天 vs 隔夜），基准本身差 0.92pp → 出现
    # 「成本为负却现实臂超额更高」的矛盾结论。改为统一用**同期市场等权收益**：
    #   bench = open(T+1) → close(T+1+HOLD)（与两臂完全相同的持有区间）
    # 这样 exB − exA 恒等于 cost，内部一致。
    benchC = defaultdict(list)
    for sym, df in prices.items():
        c = df["close"].values
        o = df["open"].values
        ds = list(df.index)
        for i in range(len(c) - 1 - HOLD):
            if o[i + 1] > 0:
                benchC[ds[i]].append((c[i + 1 + HOLD] / o[i + 1] - 1) * 100)
    bC = {d: sum(v) / len(v) for d, v in benchC.items() if v}

    rows = []
    for s in sigs:
        df = prices.get(s["sym"])
        im = IDX.get(s["sym"])
        if df is None or im is None or s["date"] not in im:
            continue
        d = s["date"]
        i = im[d]
        c = df["close"].values
        o = df["open"].values
        n = len(c)
        if i + 1 + HOLD >= n or c[i] <= 0 or o[i + 1] <= 0:
            continue
        if d not in bC:
            continue
        retA = (c[i + 1 + HOLD] / c[i] - 1) * 100          # 理想：T日收盘买入
        retB = (c[i + 1 + HOLD] / o[i + 1] - 1) * 100      # 现实：T+1开盘买入
        gap = (o[i + 1] / c[i] - 1) * 100
        rows.append({"sym": s["sym"], "date": d, "cls": s.get("cls", ""),
                     "gap": round(gap, 3),
                     "retA": round(retA, 3), "retB": round(retB, 3),
                     "cost": round(retB - retA, 3),
                     "exA": round(retA - bC[d], 3), "exB": round(retB - bC[d], 3)})

    if not rows:
        print("❌ 无可评估样本")
        return 1

    med = st.median([r["retA"] for r in rows])
    if abs(med) > 20:
        print(f"❌ 收益量纲异常(中位 {med:+.1f}%)，终止")
        return 2

    print(f"\n可评估样本 {len(rows)} 条 ({rows[0]['date']} ~ {rows[-1]['date']})")

    costs = [r["cost"] for r in rows]
    ci = boot_ci(costs)
    print("\n── Gap 执行成本（现实 − 理想，负=实盘比回测差）──")
    print(f"均值 {st.mean(costs):+.3f}%  中位 {st.median(costs):+.3f}%  "
          f"CI95 [{ci[0]:+.3f},{ci[1]:+.3f}]" if ci else "")
    if ci and ci[1] < 0:
        print("→ 成本显著为负：回测口径系统性高估，实盘执行吃亏")
    elif ci and ci[0] > 0:
        print("→ 成本显著为正：跳空反而有利于入场（低开居多）")
    else:
        print("→ CI 含 0：gap 成本在本样本无一致方向（个股差异互相抵消）")

    print("\n── 两臂 5 日超额 ──")
    for lab, key in (("理想(收盘价买)", "exA"), ("现实(次日开盘买)", "exB")):
        v = [r[key] for r in rows]
        c = boot_ci(v)
        ci_s = f"CI95[{c[0]:+.2f},{c[1]:+.2f}]" if c else "n/a"
        sig_s = "显著" if (c and (c[0] > 0 or c[1] < 0)) else "不显著"
        print(f"{lab:>16}: 均值 {st.mean(v):+.2f}%  中位 {st.median(v):+.2f}%  {ci_s} {sig_s}")

    # 按 gap 方向分层：高开 vs 低开，看成本侵蚀集中在哪
    print("\n── 按 T+1 跳空方向分层 ──")
    print(f"{'分层':>10} {'n':>5} {'gap均值':>9} {'成本':>8} {'exB':>8}")
    buckets = {"低开<-1%": lambda g: g < -1, "-1%~0": lambda g: -1 <= g < 0,
               "0~+1%": lambda g: 0 <= g < 1, "高开≥+1%": lambda g: g >= 1}
    layer = {}
    for lab, fn in buckets.items():
        sub = [r for r in rows if fn(r["gap"])]
        if not sub:
            print(f"{lab:>10} {0:>5}   —")
            continue
        layer[lab] = {"n": len(sub),
                      "gap_mean": round(st.mean([r["gap"] for r in sub]), 3),
                      "cost_mean": round(st.mean([r["cost"] for r in sub]), 3),
                      "exB_mean": round(st.mean([r["exB"] for r in sub]), 3)}
        print(f"{lab:>10} {len(sub):>5} {layer[lab]['gap_mean']:>+8.2f}% "
              f"{layer[lab]['cost_mean']:>+7.2f}% {layer[lab]['exB_mean']:>+7.2f}%")

    # 结论建议
    # 内部一致性自检：exB − exA 必须恒等于 cost（同基准后成立），否则口径又分叉了
    drift = max(abs((r["exB"] - r["exA"]) - r["cost"]) for r in rows)
    print(f"\n口径自检: max|(exB-exA)-cost| = {drift:.6f}pp (应≈0)")
    if drift > 0.005:  # round(3) 四处累积误差上限 0.004pp
        print("⚠️ 口径不一致！基准未统一，结论不可用")
        return 3
    mean_cost = st.mean(costs)
    out = {
        "date": TODAY, "hold_days": HOLD, "n_signals": len(rows),
        "n_price_syms": len(prices), "quick": QUICK,
        "all_day_gap": {"n": len(gaps), "mean": round(st.mean(gaps), 4),
                        "median": round(st.median(gaps), 4)} if gaps else None,
        "cost": {"mean": round(mean_cost, 4), "median": round(st.median(costs), 4),
                 "ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None},
        "arm_ideal": {"mean": round(st.mean([r["exA"] for r in rows]), 4),
                      "median": round(st.median([r["exA"] for r in rows]), 4)},
        "arm_real": {"mean": round(st.mean([r["exB"] for r in rows]), 4),
                     "median": round(st.median([r["exB"] for r in rows]), 4)},
        "by_gap_bucket": layer,
        "caveat": ("理想臂 = T日收盘价买入（回测常用但实盘不可执行）；现实臂 = T+1 开盘买入。"
                   "两臂卖出同为 T+1+HOLD 收盘，故差额纯粹反映入场 gap 成本。"
                   "未计手续费/印花税/滑点，故真实成本只会更高（本结论偏乐观）。"),
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{TODAY}_gap_cost_replay.json")
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n📄 落盘: {os.path.relpath(path, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
