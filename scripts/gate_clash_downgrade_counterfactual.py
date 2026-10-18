#!/usr/bin/env python3
"""对撞信号门控降档反事实（2026-10-18 深挖收口）。

背景
----
gate_regime_execution_deepdive 发现：放行信号中「分歧对撞」（两轨矛盾）是唯一负超额组
（理想臂 −0.55%，n=35），而门控对全部信号 tech 恒 90。本脚本做**反事实**：
仅对对撞信号把 tech 从 90 降到 T'，其余级别不动，扫描 T' ∈ {90, 80, 75, 60, 45}：

  * 每个 T' 下：对撞被拦 / 仍放行的数量与双臂超额（理想 close(T) / 现实 open(T+1)）
  * 判定「降档方向正确」：新拦截组超额 < 仍放行组超额
  * 稳健性：分年 + 对撞信号逐条剔除（LOSO）

⚠️ 只做反事实，不改生产。生产接线需另行拍板（三个调用方需按 cls 传 tech_score）。
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
install_default_timeout(15)

CACHE = os.path.join(WORKSPACE, "data", "research_price_cache")
FUND = os.path.join(WORKSPACE, "data", "research_fund_history.json")
BREADTH = os.path.join(WORKSPACE, "data", "market_breadth_history.json")
EVAL = os.path.join(WORKSPACE, "analysis", "research", "dual_signal_eval_days.json")
OUT_DIR = os.path.join(WORKSPACE, "analysis", "research")
HOLD = 5
LINE = 55.0
W_T, W_F, W_S = 0.45, 0.30, 0.25


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

    clash = []
    for s in sigs:
        if s.get("cls") != "分歧对撞":
            continue
        sym = str(s.get("symbol") or "")
        d = str(s.get("date") or "")[:10]
        ser = prices.get(sym)
        if ser is None or sym not in IDX or d not in IDX[sym]:
            continue
        i = IDX[sym][d]
        c = ser["close"].values
        o = ser["open"].values
        n = len(c)
        if i + 1 + HOLD >= n or c[i] <= 0 or o[i + 1] <= 0 or d not in bI or d not in bR:
            continue
        fr = None
        for r in fund_hist.get(sym, []):
            if r["pub"] <= d:
                fr = r
            else:
                break
        fund = fund_score(fr) if fr else None
        b = breadth.get(d)
        if not b or fund is None:
            continue
        ud, dd = b["up"], b["down"]
        if ud + dd <= 0:
            continue
        sent = (ud / (ud + dd)) * 70 + 15.0
        clash.append({
            "sym": sym, "name": s.get("name"), "date": d, "year": d[:4],
            "fund": fund, "sent": sent,
            "exI": (c[i + HOLD] / c[i] - 1) * 100 - bI[d],
            "exR": (c[i + 1 + HOLD] / o[i + 1] - 1) * 100 - bR[d],
        })

    print(f"对撞买入信号（可评估）: {len(clash)} 条")
    by_year = defaultdict(int)
    for r in clash:
        by_year[r["year"]] += 1
    print(f"  分年: {dict(sorted(by_year.items()))}")
    print(f"  全组: 理想臂 {st.mean([r['exI'] for r in clash]):+.2f}% / "
          f"现实臂 {st.mean([r['exR'] for r in clash]):+.2f}%")

    # tech 常数扫描（仅对撞信号）
    print(f"\n=== 对撞信号 tech 降档扫描（其余级别不动；买入线 {LINE}）===")
    print(f"{'tech':>5} {'拦截':>4} {'放行':>4} {'拦截exI':>9} {'放行exI':>9} "
          f"{'拦截exR':>9} {'放行exR':>9} {'exR差(拦-放)':>12}")
    sweep = {}
    for tech in (90, 80, 75, 60, 45):
        blk = [r for r in clash if W_T * tech + W_F * r["fund"] + W_S * r["sent"] < LINE]
        pas = [r for r in clash if W_T * tech + W_F * r["fund"] + W_S * r["sent"] >= LINE]
        if not blk or not pas:
            print(f"{tech:>5} {len(blk):>4} {len(pas):>4}   （单侧为空）")
            continue
        bI_, pI_ = st.mean([r["exI"] for r in blk]), st.mean([r["exI"] for r in pas])
        bR_, pR_ = st.mean([r["exR"] for r in blk]), st.mean([r["exR"] for r in pas])
        print(f"{tech:>5} {len(blk):>4} {len(pas):>4} {bI_:>+8.2f}% {pI_:>+8.2f}% "
              f"{bR_:>+8.2f}% {pR_:>+8.2f}% {bR_-pR_:>+10.2f}%")
        sweep[tech] = {"blocked_n": len(blk), "passed_n": len(pas),
                       "blocked_exI": round(bI_, 3), "passed_exI": round(pI_, 3),
                       "blocked_exR": round(bR_, 3), "passed_exR": round(pR_, 3)}

    # 全池对照：对撞降档不影响其他级别（按构造），但确认全池通过率变化
    all_signals = []
    for s in sigs:
        sym = str(s.get("symbol") or "")
        d = str(s.get("date") or "")[:10]
        ser = prices.get(sym)
        if ser is None or sym not in IDX or d not in IDX[sym] or d not in bR:
            continue
        i = IDX[sym][d]
        if i + 1 + HOLD >= len(ser) :
            continue
        fr = None
        for r in fund_hist.get(sym, []):
            if r["pub"] <= d:
                fr = r
            else:
                break
        fund = fund_score(fr) if fr else None
        b = breadth.get(d)
        if not b or fund is None:
            continue
        ud, dd = b["up"], b["down"]
        if ud + dd <= 0:
            continue
        sent = (ud / (ud + dd)) * 70 + 15.0
        all_signals.append({"cls": s.get("cls"), "fund": fund, "sent": sent,
                            "year": d[:4],
                            "exR": (ser["close"].values[i + 1 + HOLD] / ser["open"].values[i + 1] - 1) * 100 - bR[d]})
    # tech=75 情景下全池拦截率变化
    for tech in (90, 75):
        def blocked(r):
            t = tech if r["cls"] == "分歧对撞" else 90.0
            return W_T * t + W_F * r["fund"] + W_S * r["sent"] < LINE
        nb = sum(1 for r in all_signals if blocked(r))
        newly = [r for r in all_signals if r["cls"] == "分歧对撞" and blocked(r)]
        print(f"\n全池拦截率（tech对撞={tech}）: {nb}/{len(all_signals)} = {nb/len(all_signals):.1%}")
        if tech == 75:
            print(f"  其中对撞新拦截 {len(newly)} 条，其现实臂超额 "
                  f"{st.mean([r['exR'] for r in newly]):+.2f}%" if newly else "  （无新拦截）")

    # 稳健性：tech=75 下「新拦截 vs 仍放行」的 exR 差，LOSO
    if 75 in sweep and sweep[75]["blocked_n"] > 0:
        tech = 75
        blk = [r for r in clash if W_T * tech + W_F * r["fund"] + W_S * r["sent"] < LINE]
        pas = [r for r in clash if W_T * tech + W_F * r["fund"] + W_S * r["sent"] >= LINE]
        print(f"\n=== tech=75 稳健性 ===")
        yrs = defaultdict(lambda: ([], []))
        for r in clash:
            sc = W_T * tech + W_F * r["fund"] + W_S * r["sent"]
            (yrs[r["year"]][0] if sc < LINE else yrs[r["year"]][1]).append(r["exR"])
        for y in sorted(yrs):
            lb, pp = yrs[y]
            if lb and pp:
                print(f"  {y}: 拦截{len(lb)}均{st.mean(lb):+.2f}% | 放行{len(pp)}均{st.mean(pp):+.2f}%")
            else:
                print(f"  {y}: 拦截{len(lb)} 放行{len(pp)}（单侧空）")
        # LOSO（对撞信号少，逐条剔除看方向是否翻转）
        rng = random.Random(42)
        diffs_all = []
        for drop in range(len(clash)):
            sub = clash[:drop] + clash[drop + 1:]
            b2 = [r for r in sub if W_T * tech + W_F * r["fund"] + W_S * r["sent"] < LINE]
            p2 = [r for r in sub if W_T * tech + W_F * r["fund"] + W_S * r["sent"] >= LINE]
            if b2 and p2:
                diffs_all.append(st.mean([r["exR"] for r in b2]) - st.mean([r["exR"] for r in p2]))
        neg = sum(1 for x in diffs_all if x < 0)
        print(f"  LOSO: 剔除任一信号后「拦截-放行」exR 差为负的比例 {neg}/{len(diffs_all)}"
              f"（范围 [{min(diffs_all):+.2f}%, {max(diffs_all):+.2f}%]）")

    # 落盘
    from datetime import date
    out = {"date": date.today().isoformat(), "n_clash": len(clash),
           "clash_exI": round(st.mean([r["exI"] for r in clash]), 3),
           "clash_exR": round(st.mean([r["exR"] for r in clash]), 3),
           "sweep": sweep,
           "note": "反事实，未改生产；接线需三调用方按 cls 传 tech_score"}
    path = os.path.join(OUT_DIR, f"{out['date']}_gate_clash_downgrade_counterfactual.json")
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n📄 落盘: {os.path.relpath(path, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
