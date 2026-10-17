#!/usr/bin/env python3
"""执行时点研究（B3）：信号日收盘确认 vs 次日开盘执行的差距。

问题: 所有事件研究都是 close-to-close 口径（信号在 T 日收盘确认、按 T 收盘价计收益），
      实际执行只能在 T+1 开盘（竞价）。这个口径到底虚多少？
      - 买侧信号: 次日开盘高开 → 买入成本更高，超额被 gap 吃掉多少？
      - 卖侧信号: 次日开盘低开 → 卖出更吃亏，等待成本多少？

方法:
- 信号日清单复用 analysis/research/dual_signal_eval_days.json
- 价格走本地缓存 data/research_price_cache/{sym}.pkl（momentum_decay_study 落盘，含 open/close，零网络）
- 每个信号日 T（位置 k）:
    gap        = open[k+1]/close[k] - 1        （隔夜跳空 = 等到次日开盘的价格变化）
    fwd5_cc    = close[k+5]/close[k] - 1        （收盘入场口径，既有研究）
    fwd5_oc    = close[k+5]/open[k+1] - 1       （次日开盘入场口径，实际可得）
    drag5      = fwd5_cc - fwd5_oc               （买侧: 正=晚买更贵; 卖侧看 gap 本身）
- 超额同年去均值 + bootstrap 95%CI（iid）
输出: analysis/research/execution_timing_study.json + stdout 标记块
"""
import os
import sys
import json
import time
import logging
import pandas as pd

try:
    import numpy as np
except Exception:
    np = None
    print("[warn] numpy 不可用，CI 将跳过", flush=True)

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("exec_timing")

EVAL_JSON = os.path.join(WORKSPACE, "analysis/research/dual_signal_eval_days.json")
PRICE_CACHE_DIR = os.path.join(WORKSPACE, "data/research_price_cache")
OUT_JSON = os.path.join(WORKSPACE, "analysis/research/execution_timing_study.json")
CLASSES = ("分歧买", "双买", "分歧卖", "双卖")
H2 = 5  # 主口径 5 日


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def win_rate(xs):
    xs = [x for x in xs if x is not None]
    return sum(1 for x in xs if x > 0) / len(xs) if xs else None


def boot_ci(xs, n=3000):
    if np is None or len(xs) < 20:
        return None
    a = np.asarray(xs, dtype=float)
    rng = np.random.default_rng(42)
    idx = rng.integers(0, len(a), size=(n, len(a)))
    ms = a[idx].mean(axis=1)
    return [float(np.percentile(ms, 2.5)), float(np.percentile(ms, 97.5))]


def main():
    t0 = time.time()
    with open(EVAL_JSON) as f:
        rows = json.load(f)
    by_sym = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)

    recs = []  # {symbol,date,cls,year,gap,fwd5_cc,fwd5_oc}
    miss_cache = []
    for sym, srows in sorted(by_sym.items()):
        p = os.path.join(PRICE_CACHE_DIR, f"{sym}.pkl")
        if not os.path.exists(p):
            miss_cache.append(sym)
            continue
        df = pd.read_pickle(p)
        closes = df["close"].astype(float).tolist()
        opens = df["open"].astype(float).tolist()
        dates = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in df["date"]]
        pos = {d: k for k, d in enumerate(dates)}
        for r in srows:
            k = pos.get(r["date"])
            if k is None or k + 1 >= len(closes):
                continue
            gap = opens[k + 1] / closes[k] - 1
            fwd5_cc = fwd5_oc = None
            if k + H2 < len(closes):
                fwd5_cc = closes[k + H2] / closes[k] - 1
                fwd5_oc = closes[k + H2] / opens[k + 1] - 1
            recs.append({"symbol": sym, "date": r["date"], "cls": r["cls"],
                         "year": r["date"][:4], "gap": gap,
                         "fwd5_cc": fwd5_cc, "fwd5_oc": fwd5_oc})
    if miss_cache:
        log.warning("缺价格缓存 %d 只: %s", len(miss_cache), miss_cache)
    log.info("recs=%d (%.1fs)", len(recs), time.time() - t0)

    # 同年基线（全部 recs 含双持有）
    base_gap, base_cc, base_oc = {}, {}, {}
    for r in recs:
        y = r["year"]
        base_gap.setdefault(y, []).append(r["gap"])
        if r["fwd5_cc"] is not None:
            base_cc.setdefault(y, []).append(r["fwd5_cc"])
            base_oc.setdefault(y, []).append(r["fwd5_oc"])
    bm_gap = {y: sum(v) / len(v) for y, v in base_gap.items()}
    bm_cc = {y: sum(v) / len(v) for y, v in base_cc.items()}
    bm_oc = {y: sum(v) / len(v) for y, v in base_oc.items()}

    out = {"h": H2, "miss_cache": miss_cache,
           "baseline": {"gap": bm_gap, "fwd5_cc": bm_cc, "fwd5_oc": bm_oc}, "classes": {}}
    for cls in CLASSES:
        sub = [r for r in recs if r["cls"] == cls]
        info = {"n": len(sub), "gap": {}, "fwd5": {}}
        # gap: 全体 + 分年（gap 无需同年基线对照，直接看分布 vs 全样本基线均值）
        gaps_all = [r["gap"] for r in sub]
        info["gap"]["all"] = {
            "mean": mean(gaps_all), "win": win_rate(gaps_all), "n": len(gaps_all),
            "ci": boot_ci(gaps_all),
            "neg_pct": sum(1 for g in gaps_all if g < 0) / len(gaps_all) if gaps_all else None,
        }
        for y in sorted({r["year"] for r in sub}):
            gy = [r["gap"] for r in sub if r["year"] == y]
            info["gap"][y] = {"mean": mean(gy), "n": len(gy), "ci": boot_ci(gy)}
        # 持有收益双口径 + 超额
        xs_cc, xs_oc, xs_drag = [], [], []
        for r in sub:
            if r["fwd5_cc"] is None:
                continue
            xs_cc.append(r["fwd5_cc"] - bm_cc[r["year"]])
            xs_oc.append(r["fwd5_oc"] - bm_oc[r["year"]])
            xs_drag.append(r["fwd5_cc"] - r["fwd5_oc"])
        info["fwd5"] = {
            "excess_cc": mean(xs_cc), "excess_cc_ci": boot_ci(xs_cc), "n": len(xs_cc),
            "excess_oc": mean(xs_oc), "excess_oc_ci": boot_ci(xs_oc),
            "drag_mean": mean(xs_drag),
            "survival_pct": (mean(xs_oc) / mean(xs_cc)) if mean(xs_cc) and mean(xs_oc) is not None else None,
        }
        out["classes"][cls] = info

    with open(OUT_JSON, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info("已落盘 %s (%.0fs)", OUT_JSON, time.time() - t0)

    print("\n=====EXEC_TIMING=====")
    bg = mean([g for v in base_gap.values() for g in v])
    print(f"[全样本无条件 gap 基线] mean={bg:+.3%} (n={sum(len(v) for v in base_gap.values())})")
    for cls in CLASSES:
        info = out["classes"][cls]
        g = info["gap"]["all"]
        ci = g["ci"]
        ci_s = f" CI[{ci[0]:+.2%},{ci[1]:+.2%}]" if ci else ""
        f5 = info["fwd5"]
        cci = f5["excess_cc_ci"]; oci = f5["excess_oc_ci"]
        print(f"\n[{cls}] n={info['n']}")
        print(f"  次日开盘 gap: mean={g['mean']:+.2%} 低开占比={g['neg_pct']:.0%}{ci_s}")
        print(f"  5日超额 收盘口径={f5['excess_cc']:+.2%} CI[{cci[0]:+.2%},{cci[1]:+.2%}]"
              f" → 开盘口径={f5['excess_oc']:+.2%} CI[{oci[0]:+.2%},{oci[1]:+.2%}]"
              f" | 存活率={f5['survival_pct']:.0%}" if f5["excess_cc"] is not None else "  无fwd5")
        for y, gy in info["gap"].items():
            if y == "all":
                continue
            ciy = gy["ci"]
            ciy_s = f" CI[{ciy[0]:+.2%},{ciy[1]:+.2%}]" if ciy else ""
            print(f"    {y}: gap={gy['mean']:+.2%} (n={gy['n']}){ciy_s}")
    print("=====EXEC_TIMING_END=====")


if __name__ == "__main__":
    main()
