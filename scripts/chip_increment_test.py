#!/usr/bin/env python3
"""chip 第 5 维增量检验：加入 chip 后，五维评分的排序/动作是否真的变好（2026-10-18）。

背景
----
chip 维（cost_bias 绝对阈值加分）已接进生产五维评分（trader_stock_picks），
但标定只验证了**单维预测力**（cost_bias<-0.20 → 5日超额 +2.38%），
从未验证**增量价值**：加入 chip 后，总评分对后续收益的区分度是否改善？

口径（全部生产同源，防前视）
---------------------------
* 信号: dual_signal_eval_days.json 买入信号（1062 条）
* tech 维: 本地价格缓存重建 MACD(12,26,9) dif/dea + RSI14（Wilder），生产公式
   dif>dea +2 / else -2；rsi>70 -2；base 5，clamp 0-10
* vp 维: 生产公式（2026-10-18 版）: chg>+5 -1 / |chg|>2 +1 / |chg|<0.5 -1；
   turn>10 +2 / >5 +1 / <1 -1；base 5，clamp 0-10（turn 来自 chip_ 输入缓存）
* fundamentals 维: 生产公式 pe<15 +2 / pe<0 -2 / pe>50 -2；pb<3 +1.5 / pb>10 -1.5；
   roe>15 +1.5 / roe<5 -1（roe=单季 roeAvg×100，as-of 防前视）；base 5
* chip 维: 生产公式 cost_bias<-0.20 → 8.5 / <-0.15 → 7.5 / else 5.0（chipmetrics as-of）
* fund_flow 维: **跳过**（无历史资金流数据），声明为「4 维近似口径」——
   本检验回答的是「chip 对其余维有无增量」，flow 缺席对两臂对称，不影响增量判定
* 总分 = 有效维均值 ×5（生产口径），动作阈值 BUY>=35 / SELL<=20

检验
----
1. Spearman(score, ex5)：3维 vs 4维（信号级）
2. 十分位单调性：3维 vs 4维的首尾差
3. **动作翻转方向检验**（最决策相关）：加入 chip 后动作翻转的信号，
   上调组 vs 下调组的 ex5 差——chip 的增量 = 翻转方向是否正确
"""
import os
import sys
import json
import statistics as st
from collections import defaultdict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd
import numpy as np

from analysis.net_guard import install_default_timeout
install_default_timeout(15)

CACHE = os.path.join(WORKSPACE, "data", "research_price_cache")
CHIP_IN = os.path.join(CACHE)          # chip_{sym}.pkl（含 turn）
CHIPMETRICS = os.path.join(CACHE)      # chipmetrics_{sym}.pkl
FUND = os.path.join(WORKSPACE, "data", "research_fund_history.json")
PEPB = os.path.join(WORKSPACE, "data", "research_pepb_history.json")
EVAL = os.path.join(WORKSPACE, "analysis", "research", "dual_signal_eval_days.json")
OUT_DIR = os.path.join(WORKSPACE, "analysis", "research")
HOLD = 5
BUY_LINE, SELL_LINE = 35.0, 20.0        # 50 制


def load_prices_full():
    """{sym: DataFrame(date, open, close, turn)}"""
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
        cols = ["close"]
        if "open" in df.columns:
            cols.append("open")
        ser = df.set_index("_d")[cols].astype(float)
        # turn 从 chip_ 输入缓存取（baostock 换手率百分数）
        chip_in = os.path.join(CHIP_IN, f"chip_{sym}.pkl")
        if os.path.exists(chip_in):
            try:
                ci = pd.read_pickle(chip_in)
                ci["_d"] = ci["date"].astype(str).str[:10]
                t = ci.set_index("_d")["turn"].astype(float)
                ser = ser.join(t.rename("turn")).sort_index()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"turn 读取失败 {sym}: {e}")
                ser["turn"] = np.nan
        else:
            ser["turn"] = np.nan
        out[sym] = ser
    return out


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi_wilder(close, n=14):
    d = close.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    # Wilder 平滑
    au = up.ewm(alpha=1 / n, adjust=False).mean()
    ad = dn.ewm(alpha=1 / n, adjust=False).mean()
    rs = au / ad.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def score_technical_hist(close):
    """生产公式：dif>dea +2 / else -2；rsi>70 -2；base 5，clamp 0-10。返回 Series(date)。"""
    dif = ema(close, 12) - ema(close, 26)
    dea = ema(dif, 9)
    r14 = rsi_wilder(close)
    s = pd.Series(5.0, index=close.index)
    s[dif > dea] = 7.0
    s[dif <= dea] = 3.0
    s[r14 > 70] = s[r14 > 70] - 2.0
    return s.clip(0, 10)


def score_vp_hist(close, turn):
    """生产公式（2026-10-18 版）。返回 Series(date)。"""
    chg = close.pct_change() * 100
    s = pd.Series(5.0, index=close.index)
    s[chg > 5] = 4.0
    s[(chg <= 5) & (chg.abs() > 2)] = 6.0
    s[(chg.abs() <= 2) & (chg.abs() < 0.5)] = 4.0
    if turn is not None:
        t = turn
        s[t > 10] = s[t > 10] + 2.0
        s[(t > 5) & (t <= 10)] = s[(t > 5) & (t <= 10)] + 1.0
        s[t < 1] = s[t < 1] - 1.0
    return s.clip(0, 10)


def _norm01(v, lo, hi):
    if v is None or hi <= lo:
        return None
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def main():
    prices = load_prices_full()
    fund_hist = {s: sorted(v, key=lambda x: x["pub"])
                 for s, v in json.load(open(FUND)).items()}
    pepb = json.load(open(PEPB))
    chipm = {}
    for fn in os.listdir(CHIPMETRICS):
        if fn.startswith("chipmetrics_") and fn.endswith(".pkl"):
            sym = fn[len("chipmetrics_"):-4]
            try:
                df = pd.read_pickle(os.path.join(CHIPMETRICS, fn))
                chipm[sym] = dict(zip(df["date"].astype(str).str[:10], df["cost_bias"]))
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"chipmetrics 读取失败 {sym}: {e}")

    rows = json.load(open(EVAL))
    sigs = [r for r in rows if any("买入" in str(a) for a in (r.get("actions") or []))]

    bench = defaultdict(list)
    for sym, ser in prices.items():
        a = ser["close"].values
        ds = list(ser.index)
        for i in range(len(a) - HOLD):
            if a[i] > 0:
                bench[ds[i]].append((a[i + HOLD] / a[i] - 1) * 100)
    bench5 = {d: sum(v) / len(v) for d, v in bench.items() if v}

    recs = []
    miss = defaultdict(int)
    for s in sigs:
        sym = str(s.get("symbol") or "")
        d = str(s.get("date") or "")[:10]
        ser = prices.get(sym)
        if ser is None or d not in ser.index:
            continue
        i = ser.index.get_loc(d)
        if i + HOLD >= len(ser) or d not in bench5:
            continue
        ex5 = (ser["close"].values[i + HOLD] / ser["close"].values[i] - 1) * 100 - bench5[d]
        # tech
        tech = float(score_technical_hist(ser["close"]).iloc[i])
        # vp
        vp = float(score_vp_hist(ser["close"], ser["turn"] if "turn" in ser else None).iloc[i])
        # fundamentals（五维口径，as-of）
        fr = None
        for r in fund_hist.get(sym, []):
            if r["pub"] <= d:
                fr = r
            else:
                break
        pe = pb = None
        for dt, p, b in pepb.get(sym, []):
            if dt <= d:
                pe, pb = p, b
            else:
                break
        roe_pct = fr["roe"] * 25.0 if (fr and fr.get("roe") is not None) else None  # roeAvg×100
        if pe is None or pb is None or roe_pct is None:
            miss["fund"] += 1
            fund5 = None
        else:
            fund5 = 5.0
            if 0 < pe < 15:
                fund5 += 2
            elif pe < 0:
                fund5 -= 2
            elif pe > 50:
                fund5 -= 2
            if 0 < pb < 3:
                fund5 += 1.5
            elif pb > 10:
                fund5 -= 1.5
            if roe_pct > 15:
                fund5 += 1.5
            elif roe_pct < 5:
                fund5 -= 1.0
            fund5 = max(0.0, min(10.0, fund5))
        # chip（as-of）
        cb = chipm.get(sym, {}).get(d)
        chip = 8.5 if (cb is not None and cb < -0.20) else (7.5 if (cb is not None and cb < -0.15) else (5.0 if cb is not None else None))
        if chip is None:
            miss["chip"] += 1
        recs.append({"sym": sym, "date": d, "ex5": ex5, "tech": tech, "vp": vp,
                     "fund": fund5, "chip": chip})
    print(f"样本 {len(recs)}（缺失 fund:{miss['fund']} chip:{miss['chip']}）")

    # 两组分数
    def totals(r, with_chip):
        dims = [r["tech"], r["vp"], r["fund"]]
        dims = [x for x in dims if x is not None]
        if with_chip and r["chip"] is not None:
            dims.append(r["chip"])
        if not dims:
            return None
        return sum(dims) / len(dims) * 5.0

    for with_chip in (False, True):
        key = "4d" if with_chip else "3d"
        scored = [(totals(r, with_chip), r["ex5"]) for r in recs]
        scored = [(s, e) for s, e in scored if s is not None]
        ss = [s for s, _ in scored]
        ee = [e for _, e in scored]
        rho = pd.Series(ss).corr(pd.Series(ee), method="spearman")
        # 十分位
        df = pd.DataFrame({"s": ss, "e": ee})
        df["q"] = pd.qcut(df["s"], 10, labels=False, duplicates="drop")
        g = df.groupby("q")["e"].mean()
        spread = g.iloc[-1] - g.iloc[0]
        print(f"\n[{key}] n={len(scored)} Spearman={rho:+.3f} 十分位首尾差={spread:+.2f}%")
        print("  分位均值:", " ".join(f"{v:+.2f}" for v in g))

    # 动作翻转检验（BUY>=35 / SELL<=20，50 制）
    print("\n=== 动作翻转方向检验（3维 → 4维）===")
    ups, downs, same = [], [], []
    for r in recs:
        s3, s4 = totals(r, False), totals(r, True)
        if s3 is None or s4 is None:
            continue
        def act(x):
            return "BUY" if x >= BUY_LINE else ("SELL" if x <= SELL_LINE else "HOLD")
        a3, a4 = act(s3), act(s4)
        if a4 != a3:
            rank_up = ({"SELL": 0, "HOLD": 1, "BUY": 2}[a4] > {"SELL": 0, "HOLD": 1, "BUY": 2}[a3])
            (ups if rank_up else downs).append((r, a3, a4))
        else:
            same.append(r)
    print(f"  翻转 {len(ups)+len(downs)} 条（上调 {len(ups)} / 下调 {len(downs)}）/ 不变 {len(same)} 条")
    if ups:
        print(f"  上调组 ex5 均值 {st.mean([r['ex5'] for r, _a3, _a4 in ups]):+.2f}%")
    if downs:
        print(f"  下调组 ex5 均值 {st.mean([r['ex5'] for r, _a3, _a4 in downs]):+.2f}%")
    if ups and downs:
        d = st.mean([r['ex5'] for r, _a, _b in ups]) - st.mean([r['ex5'] for r, _a, _b in downs])
        print(f"  增量判定: 上调-下调 = {d:+.2f}% （正=chip 翻转方向正确，有增量）")

    # 落盘
    from datetime import date
    out = {"date": date.today().isoformat(), "n": len(recs), "miss": dict(miss),
           "note": "4维近似口径（fund_flow 无历史，缺席对两臂对称）",
           "flip_up_n": len(ups), "flip_down_n": len(downs)}
    flip_up_mean = st.mean([r['ex5'] for r, _a, _b in ups]) if ups else None
    flip_dn_mean = st.mean([r['ex5'] for r, _a, _b in downs]) if downs else None
    out["flip_up_ex5"] = round(flip_up_mean, 3) if flip_up_mean is not None else None
    out["flip_down_ex5"] = round(flip_dn_mean, 3) if flip_dn_mean is not None else None
    path = os.path.join(OUT_DIR, f"{out['date']}_chip_increment_test.json")
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n📄 落盘: {os.path.relpath(path, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
