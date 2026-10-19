#!/usr/bin/env python3
"""chip 第 5 维增量检验：加入 chip 后，五维评分的排序/动作是否真的变好。

背景
----
chip 维（cost_bias 绝对阈值加分）已接进生产五维评分（trader_stock_picks），
但标定只验证了**单维预测力**（cost_bias<-0.20 → 5日超额 +2.38%），
从未验证**增量价值**：加入 chip 后，总评分对后续收益的区分度是否改善？

口径（全部生产同源，防前视）
---------------------------
* 信号: dual_signal_eval_days.json 买入信号（信号级检验）+ 全面板（rank 级检验）
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

2026-10-20 加固（补齐证据标准缺口）
-----------------------------------
1. 动作翻转检验加 bootstrap CI（上调组−下调组均值差）
2. 翻转按 2024/2025/2026 分年（稳健性：是否单年驱动）
3. Spearman 3维 vs 4维 Δ 值 bootstrap CI
4. **rank 级每日 top-K 检验**（生产真实用法=每日全池排序取 top）：
   全面板逐日按 3维/4维总分各取 top-K(5/10)，配对比较次日入场 5 日超额，
   按日期 bootstrap CI + 分年 —— 直接回答「每日推荐榜变好了没有」
"""
import os
import sys
import json
import statistics as st
from bisect import bisect_right
from collections import defaultdict, Counter

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
BOOT_N = 3000
BOOT_SEED = 42


def boot_ci_paired(ds, n=BOOT_N, seed=BOOT_SEED):
    """配对样本均值差的 bootstrap CI（按日期整段重采样）。"""
    if len(ds) < 5:
        return None
    a = np.asarray(ds, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(n, len(a)))
    ms = a[idx].mean(axis=1)
    return [round(float(np.percentile(ms, 2.5)), 4), round(float(np.percentile(ms, 97.5)), 4)]


def boot_ci_two(xs, ys, n=BOOT_N, seed=BOOT_SEED):
    """两组独立重采样的均值差 CI（上调组−下调组）。"""
    if len(xs) < 5 or len(ys) < 5:
        return None
    a = np.asarray(xs, dtype=float)
    b = np.asarray(ys, dtype=float)
    rng = np.random.default_rng(seed)
    ia = rng.integers(0, len(a), size=(n, len(a)))
    ib = rng.integers(0, len(b), size=(n, len(b)))
    d = a[ia].mean(axis=1) - b[ib].mean(axis=1)
    return [round(float(np.percentile(d, 2.5)), 4), round(float(np.percentile(d, 97.5)), 4)]


def chip5_from_cb(cb):
    """生产 chip 分数映射（单一出口，主循环与面板共用）。"""
    if cb is None:
        return None
    if cb < -0.20:
        return 8.5
    if cb < -0.15:
        return 7.5
    return 5.0


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

    # ── as-of 索引（bisect，防 21k 行 × 长列表线性扫描）──
    fund_keys = {s: [r["pub"] for r in lst] for s, lst in fund_hist.items()}
    pepb_idx = {}
    for s, lst in pepb.items():
        lst2 = sorted(lst, key=lambda x: x[0])
        pepb_idx[s] = ([x[0] for x in lst2], lst2)

    def fund5_asof(sym, d):
        """生产基本面公式 as-of 版。缺任一字段 → None（缺失不填充）。"""
        fk = fund_keys.get(sym)
        fr = None
        if fk:
            i = bisect_right(fk, d) - 1
            if i >= 0:
                fr = fund_hist[sym][i]
        pe = pb = None
        e = pepb_idx.get(sym)
        if e:
            dts, lst2 = e
            i = bisect_right(dts, d) - 1
            if i >= 0:
                pe, pb = lst2[i][1], lst2[i][2]
        roe_pct = fr["roe"] * 25.0 if (fr and fr.get("roe") is not None) else None  # roeAvg×100
        if pe is None or pb is None or roe_pct is None:
            return None
        f = 5.0
        if 0 < pe < 15:
            f += 2
        elif pe < 0:
            f -= 2
        elif pe > 50:
            f -= 2
        if 0 < pb < 3:
            f += 1.5
        elif pb > 10:
            f -= 1.5
        if roe_pct > 15:
            f += 1.5
        elif roe_pct < 5:
            f -= 1.0
        return max(0.0, min(10.0, f))

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
        fund5 = fund5_asof(sym, d)
        if fund5 is None:
            miss["fund"] += 1
        cb = chipm.get(sym, {}).get(d)
        chip = chip5_from_cb(cb)
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

    rho = {}
    for with_chip in (False, True):
        key = "4d" if with_chip else "3d"
        scored = [(totals(r, with_chip), r["ex5"]) for r in recs]
        scored = [(s, e) for s, e in scored if s is not None]
        ss = [s for s, _ in scored]
        ee = [e for _, e in scored]
        rho[key] = float(pd.Series(ss).corr(pd.Series(ee), method="spearman"))
        # 十分位
        df = pd.DataFrame({"s": ss, "e": ee})
        df["q"] = pd.qcut(df["s"], 10, labels=False, duplicates="drop")
        g = df.groupby("q")["e"].mean()
        spread = g.iloc[-1] - g.iloc[0]
        print(f"\n[{key}] n={len(scored)} Spearman={rho[key]:+.3f} 十分位首尾差={spread:+.2f}%")
        print("  分位均值:", " ".join(f"{v:+.2f}" for v in g))

    # Spearman Δ bootstrap CI（重采样信号级样本）
    rng = np.random.default_rng(BOOT_SEED)
    d_rho = []
    arr = [(totals(r, False), totals(r, True), r["ex5"]) for r in recs]
    arr = [(a, b, e) for a, b, e in arr if a is not None and b is not None]
    aa = np.array([[a, b, e] for a, b, e in arr], dtype=float)
    for _ in range(1000):
        idx = rng.integers(0, len(aa), size=len(aa))
        sub = aa[idx]
        r3 = float(pd.Series(sub[:, 0]).corr(pd.Series(sub[:, 2]), method="spearman"))
        r4 = float(pd.Series(sub[:, 1]).corr(pd.Series(sub[:, 2]), method="spearman"))
        d_rho.append(r4 - r3)
    d_rho_ci = [round(float(np.percentile(d_rho, 2.5)), 4), round(float(np.percentile(d_rho, 97.5)), 4)]
    print(f"\nSpearman Δ(4d−3d) = {rho['4d'] - rho['3d']:+.4f}  bootstrap95CI {d_rho_ci}")

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
    up_ex = [r['ex5'] for r, _a3, _a4 in ups]
    dn_ex = [r['ex5'] for r, _a3, _a4 in downs]
    if ups:
        print(f"  上调组 ex5 均值 {st.mean(up_ex):+.2f}%")
    if downs:
        print(f"  下调组 ex5 均值 {st.mean(dn_ex):+.2f}%")
    flip_diff_ci = None
    if ups and downs:
        d = st.mean(up_ex) - st.mean(dn_ex)
        flip_diff_ci = boot_ci_two(up_ex, dn_ex)
        print(f"  增量判定: 上调-下调 = {d:+.2f}%  bootstrap95CI {flip_diff_ci} （正=chip 翻转方向正确）")
    # 翻转分年
    flip_by_year = {}
    for tag, grp in (("up", ups), ("down", downs)):
        for y in ("2024", "2025", "2026"):
            sub = [r['ex5'] for r, _a, _b in grp if r['date'][:4] == y]
            if sub:
                flip_by_year.setdefault(y, {})[tag] = {"n": len(sub), "ex5_mean": round(st.mean(sub), 3)}

    # ═══ rank 级每日 top-K 检验（生产用法：每日全池排序取 top）═══
    print("\n=== rank 级每日 top-K 检验（全面板，3维 vs 4维）===")
    tech_cache, vp_cache, ex5_cache = {}, {}, {}
    bench_ser = pd.Series(bench5).sort_index()
    for sym, ser in prices.items():
        close = ser["close"]
        tech_cache[sym] = score_technical_hist(close)
        vp_cache[sym] = score_vp_hist(close, ser["turn"] if "turn" in ser else None)
        fwd = (close.shift(-HOLD) / close - 1) * 100
        ex5_cache[sym] = fwd - bench_ser.reindex(close.index)

    panels = defaultdict(list)   # date -> [(s3, s4, s3p, ex5), ...]（s3p=安慰剂臂：恒定5.0第4维）
    n_panel = 0
    for r in rows:
        sym = str(r.get("symbol") or "")
        d = str(r.get("date") or "")[:10]
        if sym not in prices or d not in bench5:
            continue
        ex5 = ex5_cache[sym].get(d)
        if ex5 is None or not np.isfinite(ex5):
            continue
        t = tech_cache[sym].get(d)
        t = None if (t is None or not np.isfinite(t)) else float(t)
        v = vp_cache[sym].get(d)
        v = None if (v is None or not np.isfinite(v)) else float(v)
        f5 = fund5_asof(sym, d)
        cb = chipm.get(sym, {}).get(d)
        c5 = chip5_from_cb(cb)
        dims3 = [x for x in (t, v, f5) if x is not None]
        s3 = sum(dims3) / len(dims3) * 5.0 if dims3 else None
        dims4 = dims3 + ([c5] if c5 is not None else [])
        s4 = sum(dims4) / len(dims4) * 5.0 if dims4 else None
        # 安慰剂臂：同维数但第4维恒为 5.0（无信号）——分离「加维机械效应」与「chip 真信号」
        s3p = sum(dims3 + [5.0]) / (len(dims3) + 1) * 5.0 if dims3 else None
        if s3 is None and s4 is None:
            continue
        panels[d].append((s3, s4, s3p, float(ex5)))
        n_panel += 1
    print(f"  面板 {n_panel} 行 / {len(panels)} 日（均值 {n_panel/max(len(panels),1):.1f} 只/日）")

    def topk_mean(lst, key_idx, K):
        cand = sorted([(x[key_idx], x[3]) for x in lst if x[key_idx] is not None], key=lambda x: -x[0])
        if len(cand) < K + 3:
            return None
        return sum(e for _s, e in cand[:K]) / K

    rank_out = {}
    for K in (5, 10):
        d_ship, d_sig, d_mech = [], [], []      # 4d−3d / 4d−安慰剂 / 安慰剂−3d
        by_year = defaultdict(list)
        better = 0
        used = 0
        changed = 0
        for d, lst in panels.items():
            m3 = topk_mean(lst, 0, K)
            m4 = topk_mean(lst, 1, K)
            mp = topk_mean(lst, 2, K)
            if m3 is None or m4 is None or mp is None:
                continue
            a3 = {x[0] for x in sorted(lst, key=lambda x: -(x[0] if x[0] is not None else -99))[:K] if x[0] is not None}
            a4 = {x[1] for x in sorted(lst, key=lambda x: -(x[1] if x[1] is not None else -99))[:K] if x[1] is not None}
            d_ship.append(m4 - m3)
            d_sig.append(m4 - mp)
            d_mech.append(mp - m3)
            by_year[d[:4]].append(m4 - m3)
            better += (m4 >= m3)
            changed += (a3 != a4)
            used += 1
        ci = boot_ci_paired(d_ship)
        ci_sig = boot_ci_paired(d_sig)
        ci_mech = boot_ci_paired(d_mech)
        mean_d = st.mean(d_ship) if d_ship else None
        mean_s = st.mean(d_sig) if d_sig else None
        mean_m = st.mean(d_mech) if d_mech else None
        yr = {y: {"n": len(v), "mean_diff": round(st.mean(v), 4)} for y, v in sorted(by_year.items())}
        rank_out[f"top{K}"] = {
            "K": K, "dates_used": used, "set_changed_pct": round(changed / used, 3) if used else None,
            "better_pct": round(better / used, 3) if used else None,
            "shipped_diff_pct": round(mean_d, 4) if mean_d is not None else None,
            "shipped_ci95": ci,
            "signal_vs_placebo_pct": round(mean_s, 4) if mean_s is not None else None,
            "signal_ci95": ci_sig,
            "mechanical_vs_3d_pct": round(mean_m, 4) if mean_m is not None else None,
            "mechanical_ci95": ci_mech,
            "by_year": yr,
        }
        if mean_d is not None:
            print(f"  [top{K}] {used}日 榜单变更{changed/used:.0%} 4d更优占比{better/used:.0%}")
            print(f"    出厂效应(4d−3d)      {mean_d:+.3f}% CI95 {ci}")
            print(f"    真信号(4d−安慰剂)    {mean_s:+.3f}% CI95 {ci_sig}")
            print(f"    机械效应(安慰剂−3d)  {mean_m:+.3f}% CI95 {ci_mech}")

    # 落盘
    from datetime import date
    out = {"date": date.today().isoformat(),
           "n_signals": len(recs), "n_panel_rows": n_panel, "n_panel_dates": len(panels),
           "miss": dict(miss),
           "note": "4维近似口径（fund_flow 无历史，缺席对两臂对称）；"
                   "2026-10-20 加固：bootstrap CI + 分年 + SpearmanΔCI + rank级topK"
                   "（含安慰剂对照：恒定5.0第4维，分离机械效应与chip真信号）",
           "spearman": {"3d": round(rho["3d"], 4), "4d": round(rho["4d"], 4),
                        "delta_ci95": d_rho_ci, "n_boot": 1000},
           "flip": {"up_n": len(ups), "down_n": len(downs), "same_n": len(same),
                    "up_ex5_mean": round(st.mean(up_ex), 3) if up_ex else None,
                    "down_ex5_mean": round(st.mean(dn_ex), 3) if dn_ex else None,
                    "diff_ci95": flip_diff_ci, "by_year": flip_by_year},
           "rank": rank_out,
           # 兼容旧字段
           "flip_up_n": len(ups), "flip_down_n": len(downs)}
    if up_ex:
        out["flip_up_ex5"] = round(st.mean(up_ex), 3)
    if dn_ex:
        out["flip_down_ex5"] = round(st.mean(dn_ex), 3)
    path = os.path.join(OUT_DIR, f"{out['date']}_chip_increment_test.json")
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n📄 落盘: {os.path.relpath(path, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
