#!/usr/bin/env python3
"""超跌反弹（Crash-Recovery）历史审计 v2
背景：2026-10-30 会话在 close_scan_v2 声称实现了「Crash 检测 + 反弹潜力 +5」，但
      历史审计因 baostock 故障未跑成（幻影实现，未落码）。本脚本补完该审计，
      用数据裁决该启发式是否值得真正落码。
数据：DataRouter.get_daily（baostock qfq 降级链 + 本地缓存），2023-01-01 起。
口径：
  事件（严格）= 3 日跌幅 < -10% 且 volatility_norm = |drop| / (ATR14/close) > 3
  事件（宽松）= 3 日跌幅 < -5%  且 volatility_norm > 2
  fwd_ret_h = 事件日收盘 -> T+h 收盘（h=1/5/10）
  超额 = fwd_ret - 同期全样本无条件均值（同票池、同 horizon，统一基准）
  分臂：vol_spike = 事件窗(3日)均量 / 前14日均量；high >= 中位数，low < 中位数
铁律：主结果先落盘；JSON 键一律 str；小样本必须带 n 与置信区间，不做单票结论。
输出：analysis/research/crash_recovery_audit.json + .md
"""
import os
import sys
import json
import logging

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd
import pandas_ta as ta

from analysis.fib_extension_scan import WATCHLIST
from analysis.data_layer.router import get_router

OUT_JSON = os.path.join(WORKSPACE, "analysis/research/crash_recovery_audit.json")
OUT_MD = os.path.join(WORKSPACE, "analysis/research/crash_recovery_audit.md")
HORIZONS = [1, 5, 10]

def winsor_mean_ci(x, lo=0.05, hi=0.95, n_boot=2000, seed=42):
    """去极值均值 + bootstrap 95% CI。返回 (mean, ci_lo, ci_hi, n)"""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n == 0:
        return (None, None, None, 0)
    if n == 1:
        return (float(x[0]), None, None, 1)
    q_lo, q_hi = np.quantile(x, lo), np.quantile(x, hi)
    w = np.clip(x, q_lo, q_hi)
    rng = np.random.default_rng(seed)
    boots = np.array([rng.choice(w, size=len(w), replace=True).mean() for _ in range(n_boot)])
    return (float(w.mean()), float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975)), n)

def find_events(df: pd.DataFrame, drop_thr: float, norm_thr: float) -> pd.DataFrame:
    df = df.copy()
    if len(df) < 60:
        return pd.DataFrame()
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    rows = []
    for i in range(14, len(df) - max(HORIZONS)):
        c0, c1 = df["close"].iloc[i - 3], df["close"].iloc[i - 1]
        atr = df["atr"].iloc[i - 1]
        if pd.isna(c0) or pd.isna(c1) or pd.isna(atr) or atr <= 0 or c1 <= 0:
            continue
        drop = c1 / c0 - 1.0
        if drop >= -drop_thr:
            continue
        vol_norm = abs(drop) / (atr / c1)
        if vol_norm < norm_thr:
            continue
        base_vol = df["volume"].iloc[i - 17:i - 3].mean()
        win_vol = df["volume"].iloc[i - 3:i].mean()
        vol_spike = float(win_vol / base_vol) if base_vol and base_vol > 0 else 1.0
        ev = {"date": str(df["date"].iloc[i - 1])[:10], "drop": round(drop, 4),
              "vol_norm": round(vol_norm, 2), "vol_spike": round(vol_spike, 2)}
        ok = True
        for h in HORIZONS:
            try:
                ev[f"fwd{h}"] = float(df["close"].iloc[i - 1 + h] / c1 - 1.0)
            except Exception:
                ok = False
                break
        if ok:
            rows.append(ev)
    return pd.DataFrame(rows)

def split_episodes(events):
    """同票事件自然日间隔 <=15 天合并为同一 episode（前向窗口重叠 = 伪重复）。"""
    import datetime as _dt
    from collections import defaultdict
    by_sym = defaultdict(list)
    for e in events:
        by_sym[e["symbol"]].append(e)
    eps = []
    for s, lst in by_sym.items():
        lst.sort(key=lambda x: x["date"])
        cur = [lst[0]]
        for e in lst[1:]:
            d0 = _dt.date.fromisoformat(cur[-1]["date"])
            d1 = _dt.date.fromisoformat(e["date"])
            if (d1 - d0).days <= 15:
                cur.append(e)
            else:
                eps.append(cur)
                cur = [e]
        eps.append(cur)
    return eps

def main():
    router = get_router()
    events_strict, events_relax = [], []
    all_days_fwd = {h: [] for h in HORIZONS}  # 无条件基准
    fail = []
    for sym, name in WATCHLIST.items():
        try:
            df = router.get_daily(sym, name, start_date="20230101", adjust="qfq")
            if df is None or len(df) < 60:
                fail.append(f"{sym}:{name}:数据不足")
                continue
            df["date"] = df["date"].astype(str)
            e_s = find_events(df, 0.10, 3.0)
            e_r = find_events(df, 0.05, 2.0)
            for r in e_s.itertuples(index=False):
                events_strict.append({"symbol": sym, "name": name, **r._asdict()})
            for r in e_r.itertuples(index=False):
                events_relax.append({"symbol": sym, "name": name, **r._asdict()})
            # 无条件基准：全样本逐日 T+h 前瞻收益（与事件窗同源，避免口径劈叉）
            closes = df["close"].values
            for h in HORIZONS:
                fwd = closes[h:] / closes[:-h] - 1.0
                all_days_fwd[h].extend(fwd.tolist())
            print(f"  ✅ {name}({sym}): strict={len(e_s)} relax={len(e_r)} rows={len(df)}")
        except Exception as e:
            logging.warning("审计 %s 失败: %s", sym, e)
            fail.append(f"{sym}:{name}:{e}")

    result = {"meta": {"generated": pd.Timestamp.now().isoformat(),
                       "universe": len(WATCHLIST), "fail": fail,
                       "definition": {"strict": "3日跌>10% 且 vol_norm>3",
                                       "relax": "3日跌>5% 且 vol_norm>2"}},
              "baseline": {}, "strict": {}, "relax": {}, "detail_strict": events_strict}
    # 基准（先落盘主结构）
    for h in HORIZONS:
        m, lo, hi, n = winsor_mean_ci(all_days_fwd[h])
        result["baseline"][str(h)] = {"mean": m, "ci": [lo, hi], "n": n}

    def arm_stats(events, label):
        out = {"n": len(events), "by_horizon": {}, "by_vol_spike": {}, "by_drop_bucket": {}}
        df = pd.DataFrame(events)
        if df.empty:
            return out
        med_spike = df["vol_spike"].median()
        for h in HORIZONS:
            col = f"fwd{h}"
            base = result["baseline"][str(h)]["mean"]
            m, lo, hi, n = winsor_mean_ci(df[col])
            out["by_horizon"][str(h)] = {"mean": m, "ci": [lo, hi], "n": n,
                                          "excess_vs_baseline": None if m is None or base is None else round(m - base, 5)}
            for arm, sub in [("high_spike", df[df["vol_spike"] >= med_spike]),
                             ("low_spike", df[df["vol_spike"] < med_spike])]:
                m2, lo2, hi2, n2 = winsor_mean_ci(sub[col])
                out["by_vol_spike"].setdefault(str(h), {})[arm] = {
                    "mean": m2, "ci": [lo2, hi2], "n": n2,
                    "excess_vs_baseline": None if m2 is None or base is None else round(m2 - base, 5)}
            for bkt, sub in [("mild(-5~-8%)", df[df["drop"] > -0.08]),
                             ("severe(<=-8%)", df[df["drop"] <= -0.08])]:
                m3, lo3, hi3, n3 = winsor_mean_ci(sub[col])
                out["by_drop_bucket"].setdefault(str(h), {})[bkt] = {
                    "mean": m3, "ci": [lo3, hi3], "n": n3,
                    "excess_vs_baseline": None if m3 is None or base is None else round(m3 - base, 5)}
        out["median_vol_spike"] = round(float(med_spike), 2)
        return out

    result["strict"] = arm_stats(events_strict, "strict")
    result["relax"] = arm_stats(events_relax, "relax")
    result["detail_relax"] = events_relax

    # episode 级去重分析（事件窗重叠 = 伪重复，铁律：离群归因先行）
    for arm in ("strict", "relax"):
        ev = events_strict if arm == "strict" else events_relax
        eps = split_episodes(ev)
        ep_rows = []
        for ep in eps:
            head = ep[0]
            ep_rows.append({"symbol": head["symbol"], "name": head["name"],
                            "first_date": head["date"], "n_events": len(ep),
                            **{f"fwd{h}": head.get(f"fwd{h}") for h in HORIZONS}})
        result[f"{arm}_episodes"] = ep_rows
        result[f"{arm}_episode_stats"] = {}
        for h in HORIZONS:
            vals = np.array([r[f"fwd{h}"] for r in ep_rows if r[f"fwd{h}"] is not None], dtype=float)
            if len(vals):
                result[f"{arm}_episode_stats"][str(h)] = {
                    "mean": round(float(vals.mean()), 5), "median": round(float(np.median(vals)), 5),
                    "win_rate": round(float((vals > 0).mean()), 3), "n": int(len(vals))}

    with open(OUT_JSON, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    # MD 摘要（读 JSON，不重算）
    lines = ["# 超跌反弹（Crash-Recovery）历史审计", "",
             f"> 生成: {result['meta']['generated']} | 池: {result['meta']['universe']} 只 | 失败: {len(fail)}",
             f"> 严格口径事件: **{result['strict']['n']}** | 宽松口径事件: **{result['relax']['n']}**", ""]
    bl = result["baseline"]
    lines.append("## 无条件基准（同池同窗）")
    lines.append("| horizon | 均值 | 95%CI | n |")
    lines.append("|---|---|---|---|")
    for h in HORIZONS:
        b = bl[str(h)]
        ci = "—" if b["ci"][0] is None else f"[{b['ci'][0]*100:.2f}%, {b['ci'][1]*100:.2f}%]"
        lines.append(f"| T+{h} | {b['mean']*100:.2f}% | {ci} | {b['n']} |")
    for arm in ("strict", "relax"):
        a = result[arm]
        lines += ["", f"## {arm} 口径（n={a['n']}）", ""]
        if a["n"] == 0:
            lines.append("无事件。")
            continue
        lines.append("| horizon | 均值 | 95%CI | n | 超额(vs基准) |")
        lines.append("|---|---|---|---|---|")
        for h in HORIZONS:
            r = a["by_horizon"][str(h)]
            ci = "—" if r["ci"][0] is None else f"[{r['ci'][0]*100:.2f}%, {r['ci'][1]*100:.2f}%]"
            ex = "—" if r["excess_vs_baseline"] is None else f"{r['excess_vs_baseline']*100:+.2f}%"
            lines.append(f"| T+{h} | {r['mean']*100:.2f}% | {ci} | {r['n']} | {ex} |")
        lines += ["", "### 放量/缩量分臂（vol_spike 中位数 %.2f 切分）" % a.get("median_vol_spike", 0)]
        lines.append("| horizon | 高放量均值(n) | 低放量均值(n) |")
        lines.append("|---|---|---|")
        for h in HORIZONS:
            hi_a = a["by_vol_spike"][str(h)]["high_spike"]
            lo_a = a["by_vol_spike"][str(h)]["low_spike"]
            lines.append(f"| T+{h} | {hi_a['mean']*100 if hi_a['mean'] is not None else '—':.2f}% (n={hi_a['n']}) | "
                         f"{lo_a['mean']*100 if lo_a['mean'] is not None else '—':.2f}% (n={lo_a['n']}) |")
        # episode 级去重（核心：伪重复检验）
        eps = result.get(f"{arm}_episodes", [])
        est = result.get(f"{arm}_episode_stats", {})
        if eps:
            from collections import Counter
            date_cnt = Counter(e["first_date"] for e in eps)
            top_dates = date_cnt.most_common(3)
            lines += ["", "### Episode 级去重（同票 15 天内合并）", "",
                      f"- events {a['n']} → **episodes {len(eps)}**；首日聚集: " +
                      ", ".join(f"{d}×{c}" for d, c in top_dates)]
            lines += ["", "| horizon | ep 均值 | ep 中位 | 胜率 | n_ep |", "|---|---|---|---|---|"]
            for h in HORIZONS:
                s = est.get(str(h), {})
                if s:
                    lines.append(f"| T+{h} | {s['mean']*100:+.2f}% | {s['median']*100:+.2f}% | {s['win_rate']*100:.0f}% | {s['n']} |")
            # 宏观日聚集度守卫（伪重复风险自动提示）
            top_date, top_n = top_dates[0]
            conc = top_n / len(eps)
            if conc >= 0.3:
                lines += ["", f"⚠️ **宏观日聚集警报**：最大单日 episode 占比 {conc*100:.0f}%（{top_date}×{top_n}）——"
                          "该口径的超额收益可能主要由单次全市场事件驱动，"
                          "独立宏观 episode 数过少，不足以支持个股级信号上线。"]
    with open(OUT_MD, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nDONE strict={result['strict']['n']} relax={result['relax']['n']} fail={len(fail)}")
    print(f"JSON -> {OUT_JSON}\nMD -> {OUT_MD}")

if __name__ == "__main__":
    main()
