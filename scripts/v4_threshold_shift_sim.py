#!/usr/bin/env python3
"""v4（高换手 turn>10→+2）变更的阈值命中率平移模拟（2026-10-20）。

背景
----
2026-10-20_score_component_audit：v4 高换手 +2 是唯一三年全负的稳健反证
（分年 −0.33/−0.45/−0.93pp）。但总分阈值 35/20 是在现有组件结构下校准的——
单独动组件会平移全部分数分布。本模拟回答：如果改 v4，BUY/SELL 名单怎么变，
变好的还是变坏的？（**只做决策支持，不改生产**）

口径
----
* 维度 = 4 维近似（tech/vp/fund + chip，同 chip_increment_test；fund_flow 无历史对
  各变体对称）；total = 有效维均值 ×5（生产口径）
* vp 生产公式 base 5 + chg/turn 调整 clamp 0-10；变体只动 turn>10 档：
  R0 现行(+2) / R1 归零(0) / R2 反向(−2)
* ex5 winsorize ±15%（肥尾教训），= 5日收盘 − 同日全池均值
* 关键指标：变体相对 R0 的 BUY 名单增减行的 ex5（进场质量变化）+ 配对日度差分 CI
* 数据：dual_signal_eval_days 全面板（20,893 行）+ 买入信号行（1,062）双视角
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
OUT_JSON = os.path.join(WORKSPACE, "analysis", "research", "2026-10-20_v4_threshold_shift_sim.json")
OUT_MD = os.path.join(WORKSPACE, "analysis", "research", "2026-10-20_v4_threshold_shift_sim.md")
HOLD = 5
WINSOR = 0.15
BUY_LINE, SELL_LINE = 35.0, 20.0
TURN_V4 = {"R0_现行+2": 2.0, "R1_归零0": 0.0, "R2_反向-2": -2.0}


def vp_score(chg, turn, v4):
    """生产 vp 公式，v4 参数化（仅 turn>10 档分值可变）。"""
    if chg is None:
        return None
    s = 5.0
    if chg > 5:
        s -= 1
    elif abs(chg) > 2:
        s += 1
    elif abs(chg) < 0.5:
        s -= 1
    if turn is not None:
        if turn > 10:
            s += v4
        elif turn > 5:
            s += 1.0
        elif turn < 1:
            s -= 1.0
    return max(0.0, min(10.0, s))


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

    # chipm as-of
    chipm = {}
    cache = os.path.join(WORKSPACE, "data", "research_price_cache")
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
        fwd = (close.shift(-HOLD) / close - 1) * 100
        ex5_s = fwd - pd.Series(bench5).reindex(close.index)
        turn = ser["turn"] if "turn" in ser else pd.Series(np.nan, index=close.index)
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
            cb = chipm.get(sym, {}).get(d)
            chip = cit.chip5_from_cb(cb)
            recs.append({
                "sym": sym, "date": d, "ex5": float(max(min(ex5, WINSOR * 100), -WINSOR * 100)),
                "tech": float(tech_s.iloc[i]),
                "chg": chg, "turn": t,
                "fund": fund5_asof(sym, d), "chip": chip,
                "is_buy": any("买入" in str(a) for a in (r.get("actions") or [])),
            })
    print(f"面板 {len(recs)} 行")

    def totals(rec, v4):
        vp = vp_score(rec["chg"], rec["turn"], v4)
        dims = [x for x in (rec["tech"], vp, rec["fund"]) if x is not None]
        if rec["chip"] is not None:
            dims.append(rec["chip"])
        if not dims:
            return None
        return sum(dims) / len(dims) * 5.0

    def act(x):
        return "BUY" if x >= BUY_LINE else ("SELL" if x <= SELL_LINE else "HOLD")

    out = {"date": "2026-10-20", "n_panel": len(recs), "winsor": WINSOR * 100,
           "note": "4维近似口径；只动 turn>10 档；ex5 winsor±15%",
           "variants": {}}

    base_sets = None
    for vname, v4 in TURN_V4.items():
        buys, sells = [], []
        for rec in recs:
            t = totals(rec, v4)
            if t is None:
                continue
            a = act(t)
            if a == "BUY":
                buys.append((rec["date"], rec["sym"], rec["ex5"]))
            elif a == "SELL":
                sells.append((rec["date"], rec["sym"], rec["ex5"]))
        out["variants"][vname] = {
            "n_buy": len(buys), "n_sell": len(sells),
            "buy_rate": round(len(buys) / len(recs), 4),
            "sell_rate": round(len(sells) / len(recs), 4),
            "buy_ex5_mean": round(sum(x[2] for x in buys) / len(buys), 4) if buys else None,
            "sell_ex5_mean": round(sum(x[2] for x in sells) / len(sells), 4) if sells else None,
        }
        if vname == "R0_现行+2":
            base_sets = (set((d, s) for d, s, _ in buys), set((d, s) for d, s, _ in sells))
        print(f"[{vname}] BUY {len(buys)} ({len(buys)/len(recs):.1%}) ex5={out['variants'][vname]['buy_ex5_mean']:+.3f} | "
              f"SELL {len(sells)} ex5={out['variants'][vname]['sell_ex5_mean']:+.3f}")

    # 名单增减行的质量（R1/R2 vs R0）
    for vname, v4 in TURN_V4.items():
        if vname == "R0_现行+2":
            continue
        buys_v = set()
        for rec in recs:
            t = totals(rec, v4)
            if t is not None and act(t) == "BUY":
                buys_v.add((rec["date"], rec["sym"]))
        gained = buys_v - base_sets[0]
        lost = base_sets[0] - buys_v
        ex5_map = {(r["date"], r["sym"]): r["ex5"] for r in recs}
        gx = [ex5_map[k] for k in gained if k in ex5_map]
        lx = [ex5_map[k] for k in lost if k in ex5_map]
        d_ci = los.boot_diff(gx, lx) if (gx and lx) else None
        out["variants"][vname]["buy_gained"] = {
            "n": len(gained),
            "ex5_mean": round(sum(gx) / len(gx), 4) if gx else None,
            "buy_lost": {"n": len(lost), "ex5_mean": round(sum(lx) / len(lx), 4) if lx else None},
            "delta_gained_minus_lost": (round(sum(gx) / len(gx) - sum(lx) / len(lx), 4) if gx and lx else None),
            "ci95": [round(c, 4) for c in d_ci] if d_ci else None,
        }
        print(f"[{vname}] BUY 名单 +{len(gained)} / -{len(lost)} | "
              f"新增行 ex5={out['variants'][vname]['buy_gained']['ex5_mean']} "
              f"移除行 ex5={out['variants'][vname]['buy_gained']['buy_lost']['ex5_mean']} "
              f"Δ={out['variants'][vname]['buy_gained']['delta_gained_minus_lost']} CI={d_ci}")

        # SELL 侧增减（对称分析）
        sells_v = set()
        for rec in recs:
            t = totals(rec, v4)
            if t is not None and act(t) == "SELL":
                sells_v.add((rec["date"], rec["sym"]))
        sg = sells_v - base_sets[1]
        sl = base_sets[1] - sells_v
        sgx = [ex5_map[k] for k in sg if k in ex5_map]
        slx = [ex5_map[k] for k in sl if k in ex5_map]
        s_ci = los.boot_diff(sgx, slx) if (sgx and slx) else None
        out["variants"][vname]["sell_gained"] = {
            "n": len(sg),
            "ex5_mean": round(sum(sgx) / len(sgx), 4) if sgx else None,
            "sell_lost": {"n": len(sl), "ex5_mean": round(sum(slx) / len(slx), 4) if slx else None},
            "note": "新增 SELL 行 ex5 越负，旗标边际区分度越好（负值=正确识别弱势）",
            "ci95": [round(c, 4) for c in s_ci] if s_ci else None,
        }
        print(f"[{vname}] SELL 名单 +{len(sg)} / -{len(sl)} | 新增行 ex5={out['variants'][vname]['sell_gained']['ex5_mean']} CI={s_ci}")

    _bak(OUT_JSON)
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"📄 落盘: {os.path.relpath(OUT_JSON, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
