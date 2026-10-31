#!/usr/bin/env python3
"""门控 × regime / 执行口径 / 信号级 tech 变量化 深挖（2026-10-18）。

四个问题（共享同一份信号重建，生产口径 + active 中性）：
  A. **门控价值是否依赖市场状态**：55 线拦截差异在 trend vs chop（regime_er/regime_adx）
     分层下是否都成立 → 决定「什么时候该信门控」
  B. **实战执行口径下的拦截收益**：标定用 close(T) 理想入场；生产次日开盘执行。
     若被拦信号高开更多（被追逐），实战拦截收益 > 标定值。用统一基准
     open(T+1)→close(T+1+HOLD) 重算两臂（与 gap_cost_replay 同口径）
  C. **信号级 tech 变量化预研**：当前门控 tech 恒 90；若通过信号中
     「分歧对撞」系统性差于「双买」，则 tech 维有变量化空间
  D. **0~+1% 高开档（负期望档）是否已被门控覆盖**：该档信号的门控分分布；
     若集中在低分已被拦 → 无需执行折扣规则；若高分放行 → 执行折扣有独立价值
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
        if "date" not in df.columns or "close" not in df.columns or "open" not in df.columns:
            continue
        df = df.copy()
        df["_d"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df = df[df["_d"] != "1970-01-01"].drop_duplicates("_d").sort_values("_d")
        out[sym] = df.set_index("_d")[["open", "close"]].astype(float)
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


def main():
    fund_hist = {s: sorted(v, key=lambda x: x["pub"])
                 for s, v in json.load(open(FUND)).items()}
    breadth = json.load(open(BREADTH))
    prices = load_prices()
    IDX = {sym: {str(d)[:10]: k for k, d in enumerate(ser.index)} for sym, ser in prices.items()}

    # 基准：理想臂 close(T)→close(T+H)；现实臂 open(T+1)→close(T+1+H)（同一基准，gap 重放口径）
    benchI, benchR = defaultdict(list), defaultdict(list)
    for sym, ser in prices.items():
        c = ser["close"].values
        o = ser["open"].values
        ds = list(ser.index)
        for i in range(len(c)):
            if c[i] > 0 and i + HOLD < len(c):
                benchI[ds[i]].append((c[i + HOLD] / c[i] - 1) * 100)
            if i + 1 + HOLD < len(c) and o[i + 1] > 0 and c[i] > 0:
                benchR[ds[i]].append((c[i + 1 + HOLD] / o[i + 1] - 1) * 100)
    bI = {d: sum(v) / len(v) for d, v in benchI.items() if v}
    bR = {d: sum(v) / len(v) for d, v in benchR.items() if v}

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
        c = ser["close"].values
        o = ser["open"].values
        n = len(c)
        if i + 1 + HOLD >= n or c[i] <= 0 or o[i + 1] <= 0:
            continue
        if d not in bI or d not in bR:
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
        sent = (ud / (ud + dd)) * 70 + 15.0
        score = 40.5 + (0.30 * fund if fund is not None else 0) + 0.25 * sent
        gap = (o[i + 1] / c[i] - 1) * 100
        recs.append({
            "sym": sym, "name": s.get("name"), "date": d, "score": score,
            "exI": (c[i + HOLD] / c[i] - 1) * 100 - bI[d],          # 理想臂超额
            "exR": (c[i + 1 + HOLD] / o[i + 1] - 1) * 100 - bR[d],  # 现实臂超额（生产可执行）
            "gap": gap,
            "cls": s.get("cls"), "regime_er": s.get("regime_er"),
            "regime_adx": s.get("regime_adx"), "p1_zone": s.get("p1_zone"),
        })
    print(f"样本 {len(recs)}（生产口径+active中性，理想/现实双臂）")
    low = [r for r in recs if r["score"] < LINE]
    pas = [r for r in recs if r["score"] >= LINE]
    print(f"低分 {len(low)} / 放行 {len(pas)}")

    # ── A. regime 条件化 ──
    print("\n=== A. 门控 55 线拦截差异 × 市场状态 ===")
    for field in ("regime_er", "regime_adx"):
        print(f"  [{field}]")
        for val in sorted({str(r[field]) for r in recs}):
            sub = [r for r in recs if str(r[field]) == val]
            lb = [r["exI"] for r in sub if r["score"] < LINE]
            pp = [r["exI"] for r in sub if r["score"] >= LINE]
            if lb and pp:
                print(f"    {val:>6}: n={len(sub):>4} 拦截{len(lb):>2}均{st.mean(lb):+.2f}% | "
                      f"放行{len(pp):>3}均{st.mean(pp):+.2f}% | 差{st.mean(lb)-st.mean(pp):+.2f}%")
            else:
                print(f"    {val:>6}: n={len(sub):>4} 拦截{len(lb)} 放行{len(pp)}（样本不足）")

    # ── B. 实战执行口径：被拦信号的 gap 分布 + 现实臂差异 ──
    print("\n=== B. 执行口径：被拦信号是否高开更多（被追逐）===")
    g_low = st.mean([r["gap"] for r in low]) if low else 0
    g_pas = st.mean([r["gap"] for r in pas]) if pas else 0
    print(f"  T+1 跳空均值: 被拦 {g_low:+.3f}% vs 放行 {g_pas:+.3f}%")
    exR_low = st.mean([r["exR"] for r in low]) if low else 0
    exR_pas = st.mean([r["exR"] for r in pas]) if pas else 0
    print(f"  理想臂拦截差异: {st.mean([r['exI'] for r in low]) - st.mean([r['exI'] for r in pas]):+.2f}%")
    print(f"  现实臂拦截差异: {exR_low - exR_pas:+.2f}%  （被拦 {exR_low:+.2f}% vs 放行 {exR_pas:+.2f}%）")

    # ── C. 信号级 tech 变量化预研（放行信号按 cls 的质量差异）──
    print("\n=== C. 放行信号按信号级别（tech 变量化空间）===")
    for cls in ("双买", "分歧买", "分歧对撞"):
        sub = [r for r in pas if r["cls"] == cls]
        if sub:
            print(f"  放行&{cls}: n={len(sub):>3} 理想臂超额 {st.mean([r['exI'] for r in sub]):+.2f}%  "
                  f"现实臂 {st.mean([r['exR'] for r in sub]):+.2f}%")
    # 被拦信号的 cls 分布（门控是否已在拦某种级别）
    lc = defaultdict(int)
    for r in low:
        lc[r["cls"]] += 1
    print(f"  被拦信号的级别分布: {dict(lc)}")

    # ── D. 0~+1% 高开负期望档是否已被门控覆盖 ──
    print("\n=== D. 0~+1% 高开档 × 门控分数 ===")
    bucket = [r for r in recs if 0 <= r["gap"] < 1]
    if bucket:
        b_low = [r for r in bucket if r["score"] < LINE]
        b_pas = [r for r in bucket if r["score"] >= LINE]
        print(f"  该档信号 {len(bucket)} 条: 被拦 {len(b_low)} / 放行 {len(b_pas)}")
        if b_pas:
            print(f"  放行部分现实臂超额 {st.mean([r['exR'] for r in b_pas]):+.2f}%"
                  f"（若仍为负 → 执行折扣有独立价值；若转正 → 门控已覆盖）")
        if b_low:
            print(f"  被拦部分现实臂超额 {st.mean([r['exR'] for r in b_low]):+.2f}%")

    # 落盘
    from datetime import date
    out = {"date": date.today().isoformat(), "n": len(recs), "n_low": len(low),
           "line": LINE,
           "gap_blocked_vs_passed": [round(g_low, 3), round(g_pas, 3)],
           "exR_intercept_diff": round(exR_low - exR_pas, 3),
           "exI_intercept_diff": round(
               st.mean([r["exI"] for r in low]) - st.mean([r["exI"] for r in pas]), 3)}
    path = os.path.join(WORKSPACE, "analysis", "research",
                        f"{out['date']}_gate_regime_execution_deepdive.json")
    _bak(path)
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n📄 落盘: {os.path.relpath(path, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
