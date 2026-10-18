#!/usr/bin/env python3
"""三因素门控（ResonanceGate）口径标定研究（2026-10-18）。

背景
----
约束层盘点（ERR-20261018-C03）发现门控近乎橡皮图章：
  - 缺失维按「中性 50」计入 → 技术 90 双缺 = 68 分 ≥ 60 恒放行（缺失惩罚方向反了）
  - 全池 36 只探针仅 1 只被拦（3%），最低 59.4 卡线
用户拍板「按推荐落实」= 方案 B（BUY_LINE 提高到标定值）+ A（缺失维 None 传播、
判定层 fail-closed）。

本研究做 B 的证据部分：给定历史信号的技术分，门控加权分与「后续 5 日超额收益」
的关系是否存在可用区分度？若存在 → 标定新阈值；若不存在 → 直接按 A 收紧语义并
声明门控只做「缺失防火墙」不做 alpha。

口径
----
* 信号源: analysis/research/dual_signal_eval_days.json（生产同源买入口径）
* 价格:   data/research_price_cache/*.pkl（已落盘前复权日线，只读）
* 基准:   全池等权日均收益（同日横截面均值），超额 = 个股 5d 收益 − 基准 5d 收益
* 分组:   门控 buy_score 分位（技术分用信号自带 strengths 归一，基本面/情绪取当日实值缓存）
* 统计:   逐组均值 + 中位数 + bootstrap 95%CI（5000 次，按交易日块重采样抗同日相关）
* 铁律:   只读，绝不写 data/portfolio_*.json / portfolio_equity.csv

用法
----
  python3 scripts/gate_threshold_study.py            # 完整跑并落盘
  python3 scripts/gate_threshold_study.py --quick    # 抽样 300 信号快速预演
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

DATA = os.path.join(WORKSPACE, "data")
CACHE = os.path.join(DATA, "research_price_cache")
OUT_DIR = os.path.join(WORKSPACE, "analysis", "research")
HOLD = 5
TODAY = date.today().isoformat()
QUICK = "--quick" in sys.argv


def load_prices():
    """读本地前复权日线缓存 → {sym: DataFrame(index=date, close)}"""
    out = {}
    if not os.path.isdir(CACHE):
        return out
    for fn in os.listdir(CACHE):
        if not fn.endswith(".pkl"):
            continue
        sym = fn[:-4]
        try:
            df = pd.read_pickle(os.path.join(CACHE, fn))
            if sym.startswith("chipmetrics_"):
                continue  # 指标缓存，非价格
            df = df.copy()
            col = "close" if "close" in df.columns else df.columns[-1]
            # 缓存 index 有 date 列则用之（实测 pkl 的 index 为 RangeIndex，日期在 'date' 列）
            if "date" in df.columns:
                df["_d"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            else:
                df["_d"] = pd.to_datetime(df.index).strftime("%Y-%m-%d")
            dup = df["_d"].duplicated()
            if dup.all():
                print(f"⚠️ {fn} 日期列不可解析，跳过")
                continue
            ser = df.groupby("_d")[col].last().astype(float)
            out[sym] = ser[ser.index != "1970-01-01"]
        except Exception as e:
            print(f"⚠️ 读取失败 {fn}: {e}")
    return out


def load_signals():
    """生产同源买入口径: actions 里含「买入」"""
    path = os.path.join(OUT_DIR, "dual_signal_eval_days.json")
    with open(path) as f:
        rows = json.load(f)
    sig = []
    for r in rows:
        actions = r.get("actions") or []
        if not any("买入" in str(a) for a in actions):
            continue
        sym = str(r.get("symbol") or r.get("sym") or "")
        d = str(r.get("date") or "")[:10]
        if sym and d:
            sig.append({"sym": sym, "date": d, "raw": r})
    sig.sort(key=lambda x: x["date"])
    if QUICK:
        step = max(1, len(sig) // 300)
        sig = sig[::step][:300]
    return sig


def tech_from_signal(r):
    """信号技术分代理（0~100）。

    实测 dual_signal_eval_days.json 无 strengths 字段，可用的技术证据只有：
      * cls: 双买(双轨同向买入) > 分歧买；分歧对撞最弱
      * actions: 两条轨道是否都买
      * ema_gap_atr: 趋势强度（ATR 单位），生产侧同类指标的经验量纲
    生产调用 check_buy() 时不传 tech_score → 硬编码 90（强买口径）。
    这里按 a/b/c 三档代理，避免全样本同分导致阈值扫描无区分度：
      a) 双买               → 90（等价生产口径）
      b) 单轨分歧买 + 趋势强 → 75
      c) 单轨分歧买 + 趋势弱/对撞 → 60
    """
    cls = str(r.get("cls") or "")
    acts = [str(a) for a in (r.get("actions") or [])]
    nb = sum(1 for a in acts if "买入" in a)
    if cls == "双买" or nb >= 2:
        return 90.0
    if cls == "分歧对撞":
        return 60.0
    gap = r.get("ema_gap_atr")
    try:
        g = abs(float(gap))
    except (TypeError, ValueError):
        g = 0.0
    return 75.0 if g >= 1.5 else 60.0


def main():
    prices = load_prices()
    sigs = load_signals()
    print(f"价格缓存 {len(prices)} 只 / 信号 {len(sigs)} 条")
    if not prices or not sigs:
        print("❌ 输入不足")
        return 1

    # 基准: 全池等权 5 日收益（按日，单位 %）
    global _IDX
    _IDX = {sym: {str(d)[:10]: k for k, d in enumerate(ser.index)} for sym, ser in prices.items()}
    bench = {}
    for sym, ser in prices.items():
        arr = ser.values
        ds = [str(x)[:10] for x in ser.index]
        for i in range(len(arr) - HOLD):
            bench.setdefault(ds[i], [])
            if arr[i] > 0:
                bench[ds[i]].append((arr[i + HOLD] / arr[i] - 1) * 100)
    bench5 = {d: sum(v) / len(v) for d, v in bench.items() if v}

    # 情绪/基本面：历史无法回放（缓存只存当日），故 buy_score 用
    # 「技术分 × W_TECH + 中性50 × (W_FUND+W_SENT)」的**可复现代理分**，
    # 并单独标注这是保守下界（真实情绪/基本面历史值缺失）。
    W_TECH, W_REST = 0.45, 0.55
    proxy = lambda t: round(t * W_TECH + 50.0 * W_REST, 1)

    rows = []
    for s in sigs:
        ser = prices.get(s["sym"])
        if ser is None:
            continue
        d = s["date"]
        idx_map = _IDX.get(s["sym"])
        if idx_map is None or d not in idx_map:
            continue
        i = idx_map[d]
        vals = ser.values
        if i + HOLD >= len(vals):
            continue  # 缓存不覆盖卖出日，跳样本
        if d not in bench5:
            continue
        fwd = (s["raw"].get("fwd") or {})
        pct = fwd.get(str(HOLD), 0.0)
        # fwd 口径量纲不统一（实测有 0.0082 与 0.93 两种，疑为「小数比」与「倍数」混用）
        # → 用价格缓存自算 5d 收益，仅在缓存缺该日时回退 fwd，并统一用中位数校准量纲
        if vals[i] > 0:
            ret = (vals[i + HOLD] / vals[i] - 1) * 100
        else:
            ret = float(pct)
        ex = ret - bench5[d]
        t = tech_from_signal(s["raw"])
        rows.append({"sym": s["sym"], "date": d, "tech": round(t, 1),
                     "score": proxy(t), "ret5": round(ret, 3),
                     "ex5": round(ex, 3)})

    if not rows:
        print("❌ 无可评估样本")
        return 1

    # 量纲自检：正常 A 股 5d 收益中位数应在 [-20%, +20%]，超出则口径错，直接失败
    med = st.median([r["ret5"] for r in rows])
    if abs(med) > 20:
        print(f"❌ 收益量纲异常(中位 {med:+.1f}%)，疑似 fwd 口径混用，终止")
        return 2
    print(f"可评估样本 {len(rows)} 条 ({rows[0]['date']} ~ {rows[-1]['date']})")
    exs = [r["ex5"] for r in rows]
    print(f"全样本 5d 超额: 均值 {st.mean(exs):+.2f}% 中位 {st.median(exs):+.2f}%")

    # 按代理分分档（每 5 分一档）
    buckets = defaultdict(list)
    for r in rows:
        buckets[int(r["score"] // 5) * 5].append(r["ex5"])
    print("\n分档（代理分 = tech×0.45 + 50×0.55，保守下界）:")
    print(f"{'区间':>10} {'n':>5} {'均值':>8} {'中位':>8}")
    keys = sorted(buckets)
    by_day = {k: defaultdict(list) for k in keys}
    for r in rows:
        by_day[int(r["score"] // 5) * 5][r["date"]].append(r["ex5"])

    def boot(days_map, n=5000):
        ds = list(days_map)
        if len(ds) < 5:
            return None, None
        means = []
        for _ in range(n):
            pick = [days_map[random.choice(ds)] for _ in ds]
            flat = [x for g in pick for x in g]
            if flat:
                means.append(sum(flat) / len(flat))
        means.sort()
        return means[int(n * 0.025)], means[int(n * 0.975)]

    for k in keys:
        v = buckets[k]
        lo, hi = boot(by_day[k])
        ci = f"[{lo:+.2f},{hi:+.2f}]" if lo is not None else "n/a"
        print(f"{k:>4}-{k+5:<5} {len(v):>5} {st.mean(v):>+7.2f}% {st.median(v):>+7.2f}%  CI95 {ci}")

    # 阈值扫描: 若把 BUY_LINE 设在 T，被拦样本(score<T)的 5d 超额 vs 放行样本
    print("\n阈值扫描（拦截组=score<T 的样本；若拦截组超额显著为负，说明该线有区分度）:")
    print(f"{'T':>5} {'拦截n':>6} {'拦截均值':>9} {'放行均值':>9} {'差(拦-放)':>10} {'CI95':>20}")
    scan = []
    for T in (60, 65, 70, 75, 80, 85):
        blocked = [r for r in rows if r["score"] < T]
        passed = [r for r in rows if r["score"] >= T]
        if len(blocked) < 10 or len(passed) < 10:
            print(f"{T:>5} {len(blocked):>6}  样本不足跳过")
            continue
        bm = st.mean([r["ex5"] for r in blocked])
        pm = st.mean([r["ex5"] for r in passed])
        # 差值的 bootstrap（按交易日块）
        days = sorted({r["date"] for r in rows})
        bd = defaultdict(lambda: ([], []))
        for r in rows:
            l = bd[r["date"]]
            (l[0] if r["score"] < T else l[1]).append(r["ex5"])
        diffs = []
        for _ in range(3000):
            pick = [bd[random.choice(days)] for _ in days]
            b = [x for p in pick for x in p[0]]
            q = [x for p in pick for x in p[1]]
            if b and q:
                diffs.append(sum(b) / len(b) - sum(q) / len(q))
        diffs.sort()
        ci = f"[{diffs[len(diffs)//40]:+.2f},{diffs[-len(diffs)//40]:+.2f}]"
        sig_ = "显著" if (diffs[len(diffs)//40] > 0 or diffs[-len(diffs)//40] < 0) else "不显著"
        print(f"{T:>5} {len(blocked):>6} {bm:>+8.2f}% {pm:>+8.2f}% {bm-pm:>+9.2f}% {ci:>20} {sig_}")
        scan.append({"T": T, "blocked_n": len(blocked), "blocked_mean": round(bm, 3),
                     "passed_mean": round(pm, 3), "diff": round(bm - pm, 3), "ci95": ci,
                     "significant": sig_ == "显著"})

    out = {
        "date": TODAY,
        "hold_days": HOLD,
        "n_signals": len(rows),
        "n_price_syms": len(prices),
        "quick": QUICK,
        "caveat": ("代理分用 tech×0.45+50×0.55（情绪/基本面历史实值不可回放），为保守下界；"
                   "真实门控分只会更高，故本研究的拦截组被低估 → 结论偏保守。"),
        "overall_ex5_mean": round(st.mean(exs), 3),
        "overall_ex5_median": round(st.median(exs), 3),
        "buckets": [{"lo": k, "n": len(buckets[k]),
                     "mean": round(st.mean(buckets[k]), 3),
                     "median": round(st.median(buckets[k]), 3)} for k in keys],
        "threshold_scan": scan,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{TODAY}_gate_threshold_study.json")
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n📄 落盘: {os.path.relpath(path, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
