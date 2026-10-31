#!/usr/bin/env python3
"""fundamentals 维瘦身阈值平移模拟（2026-10-20，用户拍板立项）。

背景
----
两代证据同向指认 fundamentals 维是最弱环节：
* 维度级 LODO：Δ(full4 − drop_fund) = +0.102%，CI [−0.153, +0.352] 不显著
  （对照 tech +0.183✅ / vp +0.157✅ / chip +0.145✅）
* 组件级：7 个 pe/pb/roe 阈值组件 6 个无证据 + 1 个反证（f7 roe<5，2024 驱动）

本模拟回答：若从总分中移除 fundamentals 维，BUY/SELL 名单怎么变、质量怎么变？
**只做决策支持，不改生产**；落地与否由用户按模拟 + 后续每周 0v 审计数据拍板。

口径
----
* 4 维近似（tech/vp/fundamentals/chip；fund_flow 无历史对两臂对称）——
  **生产 5 维口径下的精确阈值行为无法历史复现**，方向性参考
* total = 有效维均值 ×5（生产口径）；BUY≥35 / SELL≤20
* ex5 winsorize ±15% = 5 日收盘 − 同日全池均值
* 臂：R0 现行（含 fund）/ R1 移除 fund（3 维）
* 附加：信号条件视角（1,062 买入信号行内的动作翻转）+ rank 级 top-K（与 LODO 互证）
"""
import os
import sys
import json
from collections import defaultdict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.net_guard import install_default_timeout
install_default_timeout(15)

import numpy as np
import pandas as pd

import chip_increment_test as cit
import low_open_sweetspot_test as los
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


EVAL = os.path.join(WORKSPACE, "analysis", "research", "dual_signal_eval_days.json")
OUT_JSON = os.path.join(WORKSPACE, "analysis", "research", "2026-10-20_fund_slim_sim.json")
OUT_MD = os.path.join(WORKSPACE, "analysis", "research", "2026-10-20_fund_slim_sim.md")
HOLD = 5
WINSOR = 0.15
BUY_LINE, SELL_LINE = 35.0, 20.0


def main():
    prices = cit.load_prices_full()

    bench = defaultdict(list)
    for sym, ser in prices.items():
        a = ser["close"].values
        ds = list(ser.index)
        for i in range(len(a) - HOLD):
            if a[i] > 0:
                bench[ds[i]].append((a[i + HOLD] / a[i] - 1) * 100)
    bench5 = {d: sum(v) / len(v) for d, v in bench.items() if v}

    cache = os.path.join(WORKSPACE, "data", "research_price_cache")
    chipm = {}
    for fn in os.listdir(cache):
        if fn.startswith("chipmetrics_") and fn.endswith(".pkl"):
            sym = fn[len("chipmetrics_"):-4]
            df = pd.read_pickle(os.path.join(cache, fn))
            chipm[sym] = dict(zip(df["date"].astype(str).str[:10], df["cost_bias"]))

    fund5_asof = cit.build_fund_asof(json.load(open(cit.FUND)), json.load(open(cit.PEPB)))

    rows = json.load(open(EVAL))
    by_sym = defaultdict(list)
    for r in rows:
        by_sym[str(r.get("symbol") or "")].append(r)

    recs = []
    for sym, srows in by_sym.items():
        ser = prices.get(sym)
        if ser is None:
            continue
        close = ser["close"]
        chg_s = close.pct_change() * 100
        dif = cit.ema(close, 12) - cit.ema(close, 26)
        dea = cit.ema(dif, 9)
        tech_s = pd.Series(5.0, index=close.index)
        tech_s[dif > dea] = 7.0
        tech_s[dif <= dea] = 3.0
        r14 = cit.rsi_wilder(close)
        tech_s[r14 > 70] = tech_s[r14 > 70] - 2.0
        tech_s = tech_s.clip(0, 10)
        turn = ser["turn"] if "turn" in ser else pd.Series(np.nan, index=close.index)
        fwd = (close.shift(-HOLD) / close - 1) * 100
        ex5_s = fwd - pd.Series(bench5).reindex(close.index)
        for r in srows:
            d = str(r.get("date") or "")[:10]
            if d not in ser.index:
                continue
            i = ser.index.get_loc(d)
            ex5 = ex5_s.iloc[i]
            if not np.isfinite(ex5):
                continue
            t = float(turn.iloc[i]) if pd.notna(turn.iloc[i]) else None
            chg = float(chg_s.iloc[i]) if i > 0 and pd.notna(chg_s.iloc[i]) else None
            vp = None
            if chg is not None:
                vp = 5.0
                if chg > 5:
                    vp -= 1
                elif abs(chg) > 2:
                    vp += 1
                elif abs(chg) < 0.5:
                    vp -= 1
                if t is not None:
                    if t > 10:
                        vp -= 2          # v4 反向（10-20 已落地生产）
                    elif t > 5:
                        vp += 1
                    elif t < 1:
                        vp -= 1
                vp = max(0.0, min(10.0, vp))
            cb = chipm.get(sym, {}).get(d)
            recs.append({
                "sym": sym, "date": d,
                "tech": float(tech_s.iloc[i]), "vp": vp,
                "fund": fund5_asof(sym, d), "chip": cit.chip5_from_cb(cb),
                "ex5": float(max(min(ex5, WINSOR * 100), -WINSOR * 100)),
                "is_buy": any("买入" in str(a) for a in (r.get("actions") or [])),
            })
    print(f"面板 {len(recs)} 行（fund 缺失 {sum(1 for x in recs if x['fund'] is None)}）")

    def totals(rec, with_fund):
        dims = [rec["tech"], rec["vp"]]
        if with_fund and rec["fund"] is not None:
            dims.append(rec["fund"])
        if rec["chip"] is not None:
            dims.append(rec["chip"])
        dims = [x for x in dims if x is not None]
        if not dims:
            return None
        return sum(dims) / len(dims) * 5.0

    def act(x):
        return "BUY" if x >= BUY_LINE else ("SELL" if x <= SELL_LINE else "HOLD")

    ex5_map = {(r["date"], r["sym"]): r["ex5"] for r in recs}
    out = {"date": "2026-10-20", "n_panel": len(recs), "winsor": WINSOR * 100,
           "note": "4维近似; R0=tech/vp/fund/chip, R1=移除fund; ex5 winsor±15%",
           "variants": {}, "rank_level": {}, "signal_conditional": {}}

    sets = {}
    for name, wf in (("R0_现行含fund", True), ("R1_移除fund", False)):
        buys, sells = [], []
        for rec in recs:
            t = totals(rec, wf)
            if t is None:
                continue
            a = act(t)
            if a == "BUY":
                buys.append((rec["date"], rec["sym"], rec["ex5"]))
            elif a == "SELL":
                sells.append((rec["date"], rec["sym"], rec["ex5"]))
        sets[name] = (set((d, s) for d, s, _ in buys), set((d, s) for d, s, _ in sells))
        out["variants"][name] = {
            "n_buy": len(buys), "buy_rate": round(len(buys) / len(recs), 4),
            "n_sell": len(sells), "sell_rate": round(len(sells) / len(recs), 4),
            "buy_ex5": round(sum(x[2] for x in buys) / len(buys), 4) if buys else None,
            "sell_ex5": round(sum(x[2] for x in sells) / len(sells), 4) if sells else None,
        }
        v = out["variants"][name]
        print(f"[{name}] BUY {v['n_buy']} ({v['buy_rate']:.1%}) ex5={v['buy_ex5']:+.3f} | "
              f"SELL {v['n_sell']} ({v['sell_rate']:.1%}) ex5={v['sell_ex5']:+.3f}")

    # 名单增减（R1 vs R0）
    for side, idx in (("BUY", 0), ("SELL", 1)):
        gained = sets["R1_移除fund"][idx] - sets["R0_现行含fund"][idx]
        lost = sets["R0_现行含fund"][idx] - sets["R1_移除fund"][idx]
        gx = [ex5_map[k] for k in gained if k in ex5_map]
        lx = [ex5_map[k] for k in lost if k in ex5_map]
        d_ci = los.boot_diff(gx, lx) if (gx and lx) else None
        key = f"{side.lower()}_gained_lost"
        out["variants"]["R1_移除fund"][key] = {
            "gained_n": len(gained), "gained_ex5": round(sum(gx) / len(gx), 4) if gx else None,
            "lost_n": len(lost), "lost_ex5": round(sum(lx) / len(lx), 4) if lx else None,
            "ci95": [round(c, 4) for c in d_ci] if d_ci else None,
        }
        print(f"  {side} 名单: +{len(gained)}(ex5 {out['variants']['R1_移除fund'][key]['gained_ex5']}) "
              f"/ -{len(lost)}(ex5 {out['variants']['R1_移除fund'][key]['lost_ex5']}) CI={d_ci}")

    # rank 级（与 LODO drop_fund 互证）
    by_date = defaultdict(list)
    for r in recs:
        by_date[r["date"]].append(r)
    for K in (5, 10):
        d0, d1 = {}, {}
        for d, lst in by_date.items():
            c0, c1 = [], []
            for rec in lst:
                t0, t1 = totals(rec, True), totals(rec, False)
                if t0 is not None:
                    c0.append((t0, rec["ex5"]))
                if t1 is not None:
                    c1.append((t1, rec["ex5"]))
            if len(c0) >= K + 3:
                c0.sort(key=lambda x: -x[0])
                d0[d] = sum(e for _s, e in c0[:K]) / K
            if len(c1) >= K + 3:
                c1.sort(key=lambda x: -x[0])
                d1[d] = sum(e for _s, e in c1[:K]) / K
        common = sorted(set(d0) & set(d1))
        diffs = [d0[x] - d1[x] for x in common]
        ci = cit.boot_ci_paired(diffs) if len(diffs) >= 5 else None
        out["rank_level"][f"top{K}"] = {
            "n_dates": len(common),
            "mean_delta": round(sum(diffs) / len(diffs), 4) if diffs else None,
            "ci95": ci,
        }
        print(f"[rank top{K}] Δ(R0−R1)={out['rank_level'][f'top{K}']['mean_delta']:+.3f} CI={ci}")

    # 信号条件：买入信号行内动作翻转
    flips = defaultdict(int)
    for rec in recs:
        if not rec["is_buy"]:
            continue
        t0, t1 = totals(rec, True), totals(rec, False)
        if t0 is None or t1 is None:
            continue
        a0, a1 = act(t0), act(t1)
        if a0 != a1:
            flips[f"{a0}→{a1}"] += 1
    out["signal_conditional"] = dict(flips)
    print(f"[信号条件] 买入信号行动作翻转: {dict(flips)}")

    _bak(OUT_JSON)
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"📄 落盘: {os.path.relpath(OUT_JSON, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
