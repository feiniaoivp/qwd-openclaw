#!/usr/bin/env python3
"""生产五维评分器组件级证据审计（2026-10-20，v2 肥尾修正）。

问题
----
五维总分 = 若干加/减分组件之和，但其中一部分从未单独验证过：
* vp 维：|chg|∈(2,5] +1（中幅带「未单独证伪」）；平盘 |chg|<0.5 −1；
  turnover 三档（>10 +2 / 5~10 +1 / <1 −1）
* fund 维：pe<15 +2 / pe<0 −2 / pe>50 −2；pb<3 +1.5 / pb>10 −1.5；roe>15 +1.5 / roe<5 −1
* tech 维：dif>dea ±2（MACD 骨干，由五策略回测间接覆盖）；rsi>70 −2

方法（与 chip_increment_test 同源）
----
* 面板 = dual_signal_eval_days 全部票日；ex5 = 5日收盘收益 − 同日全池均值
* **v1 教训**：无条件全面板存在真实暴涨日（924 行情 5 日 +61~94%），
  原始均值差分被极端日支配（首版 fired 组 ex5 均值 +30%、CI 宽 ±90pp 不可读）
  → **ex5 winsorize 至 ±15%**（肥尾事件研究标准做法），同时保留原始均值对照
* 组件 fired vs unfired 两样本差分，bootstrap CI（seed=42, n=3000）
* 判定：CI 不含 0 且方向与声明一致 → 确认；不含 0 方向相反 → 反证；含 0 → 无证据
* 对「无证据/反证」组件追加**信号条件复核**（买入信号行内再验）
* 附分年 delta（稳健性）

注意：组件非独立（chg/turn 同日相关、pe/pb/roe 横截面相关）——差分是边际信息量
不是因果贡献；本审计只产出证据与建议，**不改生产代码**，降级需用户拍板。
"""
import os
import sys
import json
from bisect import bisect_right
from collections import defaultdict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.net_guard import install_default_timeout
install_default_timeout(15)

import numpy as np
import pandas as pd

import chip_increment_test as cit          # load_prices_full / ema / rsi_wilder
import low_open_sweetspot_test as los      # boot_diff（两样本差分 CI）
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


EVAL = os.path.join(WORKSPACE, "analysis", "research", "dual_signal_eval_days.json")
OUT_JSON = os.path.join(WORKSPACE, "analysis", "research", "2026-10-20_score_component_audit.json")
OUT_MD = os.path.join(WORKSPACE, "analysis", "research", "2026-10-20_score_component_audit.md")
HOLD = 5
WINSOR = 0.15   # ±15%（面板 |ex5|>15% 占比 <1%，全部为真实暴涨/暴跌日）


def main():
    prices = cit.load_prices_full()

    # bench5（同日全池均值，与 chip_increment_test 同式）
    bench = defaultdict(list)
    for sym, ser in prices.items():
        a = ser["close"].values
        ds = list(ser.index)
        for i in range(len(a) - HOLD):
            if a[i] > 0:
                bench[ds[i]].append((a[i + HOLD] / a[i] - 1) * 100)
    bench5 = {d: sum(v) / len(v) for d, v in bench.items() if v}

    # fund 原始值 as-of（与 build_fund_asof 同源同阈值）
    fund_hist = {s: sorted(v, key=lambda x: x["pub"]) for s, v in json.load(open(cit.FUND)).items()}
    pepb_idx = {}
    for s, lst in json.load(open(cit.PEPB)).items():
        lst2 = sorted(lst, key=lambda x: x[0])
        pepb_idx[s] = ([x[0] for x in lst2], lst2)

    def raw_fund(sym, d):
        fk = fund_hist.get(sym)
        fr = None
        if fk:
            kk = [x["pub"] for x in fk]
            j = bisect_right(kk, d) - 1
            if j >= 0:
                fr = fk[j]
        pe = pb = None
        e = pepb_idx.get(sym)
        if e:
            dts, lst2 = e
            j = bisect_right(dts, d) - 1
            if j >= 0:
                pe, pb = lst2[j][1], lst2[j][2]
        roe = fr["roe"] * 25.0 if (fr and fr.get("roe") is not None) else None
        return pe, pb, roe

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
        n = len(close)
        chg_s = close.pct_change() * 100
        dif = cit.ema(close, 12) - cit.ema(close, 26)
        dea = cit.ema(dif, 9)
        r14 = cit.rsi_wilder(close)
        fwd = (close.shift(-HOLD) / close - 1) * 100
        ex5_s = fwd - pd.Series(bench5).reindex(close.index)
        for r in srows:
            d = str(r.get("date") or "")[:10]
            if d not in ser.index:
                continue
            i = ser.index.get_loc(d)
            ex5 = ex5_s.iloc[i]
            if ex5 is None or not np.isfinite(ex5):
                continue
            pe, pb, roe = raw_fund(sym, d)
            recs.append({
                "sym": sym, "date": d, "year": d[:4],
                "ex5_raw": float(ex5),
                "ex5": float(max(min(ex5, WINSOR * 100), -WINSOR * 100)),
                "chg": float(chg_s.iloc[i]) if i > 0 and pd.notna(chg_s.iloc[i]) else None,
                "turn": float(ser["turn"].iloc[i]) if "turn" in ser and pd.notna(ser["turn"].iloc[i]) else None,
                "macd_up": (bool(dif.iloc[i] > dea.iloc[i])
                            if pd.notna(dif.iloc[i]) and pd.notna(dea.iloc[i]) else None),
                "rsi": float(r14.iloc[i]) if pd.notna(r14.iloc[i]) else None,
                "pe": pe, "pb": pb, "roe": roe,
                "is_buy": any("买入" in str(a) for a in (r.get("actions") or [])),
            })
    n_outlier = sum(1 for x in recs if abs(x["ex5_raw"]) > WINSOR * 100)
    print(f"面板 {len(recs)} 行（fund 缺失 {sum(1 for x in recs if x['pe'] is None or x['roe'] is None)}；"
          f"winsor 截断 {n_outlier} 行 = {n_outlier/len(recs):.1%}）")

    comps = [
        ("v1_追高削分 chg>+5→-1",   lambda x: x["chg"] is not None and x["chg"] > 5,   -1, "chg"),
        ("v2a_深跌带 chg<=-2→+1",   lambda x: x["chg"] is not None and x["chg"] <= -2, +1, "chg"),
        ("v2b_中涨带 2<chg<=5→+1",  lambda x: x["chg"] is not None and 2 < x["chg"] <= 5, +1, "chg"),
        ("v3_平盘罚 |chg|<0.5→-1",  lambda x: x["chg"] is not None and abs(x["chg"]) < 0.5, -1, "chg"),
        ("v4_高换手 turn>10→+2",    lambda x: x["turn"] is not None and x["turn"] > 10, +1, "turn"),
        ("v5_中换手 5<turn<=10→+1", lambda x: x["turn"] is not None and 5 < x["turn"] <= 10, +1, "turn"),
        ("v6_低换手 turn<1→-1",     lambda x: x["turn"] is not None and x["turn"] < 1, -1, "turn"),
        ("f1_pe低 0<pe<15→+2",      lambda x: x["pe"] is not None and 0 < x["pe"] < 15, +1, "fund"),
        ("f2_pe负 pe<0→-2",         lambda x: x["pe"] is not None and x["pe"] < 0, -1, "fund"),
        ("f3_pe高 pe>50→-2",        lambda x: x["pe"] is not None and x["pe"] > 50, -1, "fund"),
        ("f4_pb低 0<pb<3→+1.5",     lambda x: x["pb"] is not None and 0 < x["pb"] < 3, +1, "fund"),
        ("f5_pb高 pb>10→-1.5",      lambda x: x["pb"] is not None and x["pb"] > 10, -1, "fund"),
        ("f6_roe高 >15→+1.5",       lambda x: x["roe"] is not None and x["roe"] > 15, +1, "fund"),
        ("f7_roe低 <5→-1",          lambda x: x["roe"] is not None and x["roe"] < 5, -1, "fund"),
        ("t1_macd多 dif>dea→+2",    lambda x: x["macd_up"] is True, +1, "tech"),
        ("t2_rsi超买 >70→-2",       lambda x: x["rsi"] is not None and x["rsi"] > 70, -1, "tech"),
    ]

    def universe(x, kind):
        if kind == "chg":
            return x["chg"] is not None
        if kind == "turn":
            return x["turn"] is not None
        if kind == "fund":
            return x["pe"] is not None and x["roe"] is not None
        if kind == "tech":
            return x["macd_up"] is not None or x["rsi"] is not None
        return True

    def evaluate(sub, cond, kind):
        fired = [x for x in sub if cond(x)]
        unfired = [x for x in sub if not cond(x) and universe(x, kind)]
        xf = [x["ex5"] for x in fired]
        xu = [x["ex5"] for x in unfired]
        mf = sum(xf) / len(xf) if xf else None
        mu = sum(xu) / len(xu) if xu else None
        delta = (mf - mu) if (mf is not None and mu is not None) else None
        ci = los.boot_diff(xf, xu)
        return len(fired), len(unfired), mf, mu, delta, ci

    def verdict_of(delta, ci, expected):
        if delta is None or ci is None:
            return "n/a(样本不足)"
        if (ci[0] > 0 and expected > 0) or (ci[1] < 0 and expected < 0):
            return "✅ 确认"
        if (ci[0] > 0 and expected < 0) or (ci[1] < 0 and expected > 0):
            return "❌ 反证"
        return "➖ 无证据"

    out = {"date": "2026-10-20", "n_panel": len(recs), "winsor_pct": WINSOR * 100,
           "n_winsorized": n_outlier,
           "note": "ex5=5日收盘−同日全池均值，winsorize±15%；delta=fired−unfired(winsor口径)；"
                   "expected>0=组件声明该桶更优；组件非独立，差分=边际信息量",
           "components": {}, "signal_conditional": {}}

    print(f"\n{'组件':<26} {'n_f':>6} {'ex5F':>7} {'ex5U':>7} {'Δ(wins)':>8} {'CI95':>20} 判定")
    for name, cond, expected, kind in comps:
        nf, nu, mf, mu, delta, ci = evaluate(recs, cond, kind)
        verdict = verdict_of(delta, ci, expected)
        # 分年
        yr = {}
        for y in ("2024", "2025", "2026"):
            sub = [x for x in recs if x["year"] == y]
            _, _, _, _, dy, ciy = evaluate(sub, cond, kind)
            yr[y] = {"delta": round(dy, 4) if dy is not None else None,
                     "ci95": [round(c, 4) for c in ciy] if ciy else None}
        out["components"][name] = {
            "n_fired": nf, "n_unfired": nu,
            "ex5_fired": round(mf, 4) if mf is not None else None,
            "ex5_unfired": round(mu, 4) if mu is not None else None,
            "delta": round(delta, 4) if delta is not None else None,
            "ci95": [round(c, 4) for c in ci] if ci else None,
            "expected_sign": expected, "verdict": verdict, "by_year": yr,
        }
        ci_s = "n/a" if not ci else f"[{ci[0]:+.2f},{ci[1]:+.2f}]"
        print(f"{name:<26} {nf:>6} {mf:>+6.2f} {mu:>+6.2f} {delta:>+8.2f} {ci_s:>20} {verdict}")

    weak = [n for n, v in out["components"].items() if v["verdict"].startswith(("➖", "❌"))]
    buys = [x for x in recs if x["is_buy"]]
    print(f"\n=== 信号条件复核（仅买入信号行 n={len(buys)}，winsor 同口径）===")
    for name in weak:
        comp = next(c for c in comps if c[0] == name)
        cond, expected, kind = comp[1], comp[2], comp[3]
        nf, nu, mf, mu, delta, ci = evaluate(buys, cond, kind)
        out["signal_conditional"][name] = {
            "n_fired": nf, "n_unfired": nu,
            "delta": round(delta, 4) if delta is not None else None,
            "ci95": [round(c, 4) for c in ci] if ci else None,
        }
        ci_s = "n/a" if not ci else f"[{ci[0]:+.2f},{ci[1]:+.2f}]"
        print(f"  {name:<26} fired n={nf:>4} Δ={delta if delta is not None else 0:>+6.2f}% {ci_s}")

    _bak(OUT_JSON)
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n📄 落盘: {os.path.relpath(OUT_JSON, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
