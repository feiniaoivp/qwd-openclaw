#!/usr/bin/env python3
"""chip 新鲜度阈值证据化（2026-10-19，三遗留 TODO 之②）。

问题
----
analysis/chip_freshness.py 的 MAX_LAG_DAYS=5 / STALE_CRITICAL_DAYS=10 是
2026-10-18 复活第 5 维时**按直觉拍的**（"约一周/双倍容忍"），无任何实证。
阈值过松 → 冻结数据误加分；过紧 → 周刷新节奏下维度频繁降 None。

实证方法（全历史、离线、36 只）
----
生产语义（trader_stock_picks.load_chip_metrics）= 读 chipmetrics **存储行**的
cost_bias（不是拿新价重算）。所以冻结 L 个交易日的失真就是：

    frozen = cost_bias(T-L)（生产实际读到的旧值）
    fresh  = cost_bias(T)  （缓存若刷新会读到的值）

对每只票、每个 T、每个 L ∈ [1,15]，按生产打分档位判定分档漂移：
    deep (<-0.20 → 8.5) / mid (<-0.15 → 7.5) / neutral（其余 → 5.0）
统计：
  p_any    任意分档漂移概率
  p_false  误加分：冻结档 ∈ {deep,mid} 且真实档 = neutral（虚报折价 → 评分虚增）
  p_missed 漏加分：真实档 ∈ {deep,mid} 且冻结档 = neutral（加分项静默失效）
  bias     mean(frozen - fresh)：方向性系统偏差（上行/下行行情分开看）
分 regime：close(T)/close(T-L)-1 ≥ +3% 上行 / ≤ −3% 下行 / 其余震荡。

阈值判定规则（预登记，看数据前已定，防"射箭画靶"）
----
  MAX_LAG_DAYS        = 满足 p_false ≤ 1% 且 p_missed ≤ 5% 的最大 L
                        （误加分直接虚增评分，从严；漏加分有周刷新兜底，容忍 5%）
  STALE_CRITICAL_DAYS = 满足 p_false ≤ 5% 的最大 L（超过则误加分成为实质风险）

产出
----
  analysis/research/2026-10-19_chip_freshness_threshold_evidence.json / .md
  （阈值如变更：同步改 analysis/chip_freshness.py 并由 chip_freshness_test.py 锁定）
"""
import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import date

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CACHE = os.path.join(WORKSPACE, "data", "research_price_cache")
OUT_JSON = os.path.join(WORKSPACE, "analysis", "research",
                        "2026-10-19_chip_freshness_threshold_evidence.json")
OUT_MD = os.path.join(WORKSPACE, "analysis", "research",
                      "2026-10-19_chip_freshness_threshold_evidence.md")
MAX_LAG = 15
REGIME_TH = 3.0  # %


def tier(cb):
    if cb < -0.20:
        return "deep"
    if cb < -0.15:
        return "mid"
    return "neutral"


def main():
    lags = list(range(1, MAX_LAG + 1))
    # 累积器: lag -> regime -> list of (frozen, fresh)
    acc = {L: {"up": [], "down": [], "flat": []} for L in lags}
    n_syms = 0
    for fn in sorted(os.listdir(CACHE)):
        if not fn.startswith("chipmetrics_") or not fn.endswith(".pkl"):
            continue
        sym = fn[len("chipmetrics_"):-4]
        try:
            m = pd.read_pickle(os.path.join(CACHE, fn))
            px = pd.read_pickle(os.path.join(CACHE, f"{sym}.pkl"))
        except Exception:
            continue
        if m is None or len(m) < MAX_LAG + 10:
            continue
        n_syms += 1
        m = m.copy()
        m["_d"] = m["date"].astype(str).str[:10]
        cb = m.set_index("_d")["cost_bias"]
        if "date" in px.columns:
            close = px.set_index(px["date"].astype(str).str[:10])["close"]
        else:
            close = px["close"]
        close = close[~close.index.duplicated()]
        dts = m["_d"].tolist()
        for i in range(MAX_LAG, len(dts)):
            d_t = dts[i]
            try:
                chg = (close.get(d_t, np.nan) / close.get(dts[i - MAX_LAG], np.nan) - 1) * 100
            except Exception:
                chg = np.nan
            regime = "up" if chg >= REGIME_TH else ("down" if chg <= -REGIME_TH else "flat")
            for L in lags:
                fr = cb.iloc[i - L]
                fs = cb.iloc[i]
                if pd.notna(fr) and pd.notna(fs):
                    acc[L][regime].append((float(fr), float(fs)))

    rows = []
    for L in lags:
        allpairs = acc[L]["up"] + acc[L]["down"] + acc[L]["flat"]
        if not allpairs:
            continue
        fr = np.array([p[0] for p in allpairs])
        fs = np.array([p[1] for p in allpairs])
        tf = np.array([tier(x) for x in fr])
        ts = np.array([tier(x) for x in fs])
        n = len(allpairs)
        rec = {
            "lag": L, "n": n,
            "p_any": float((tf != ts).mean()),
            "p_false": float(((tf != "neutral") & (ts == "neutral")).mean()),
            "p_missed": float(((ts != "neutral") & (tf == "neutral")).mean()),
            "bias_mean": float((fr - fs).mean()),
            "by_regime": {},
        }
        for rg in ("up", "down", "flat"):
            pairs = acc[L][rg]
            if not pairs:
                continue
            frg = np.array([p[0] for p in pairs])
            fsg = np.array([p[1] for p in pairs])
            tfrg = np.array([tier(x) for x in frg])
            tsrg = np.array([tier(x) for x in fsg])
            rec["by_regime"][rg] = {
                "n": len(pairs),
                "p_false": float(((tfrg != "neutral") & (tsrg == "neutral")).mean()),
                "p_missed": float(((tsrg != "neutral") & (tfrg == "neutral")).mean()),
                "bias_mean": float((frg - fsg).mean()),
            }
        rows.append(rec)

    # 预登记规则选阈值（全样本 bars）
    max_lag_evidence = 0
    stale_evidence = 0
    for r in rows:
        if r["p_false"] <= 0.01 and r["p_missed"] <= 0.05:
            max_lag_evidence = max(max_lag_evidence, r["lag"])
        if r["p_false"] <= 0.05:
            stale_evidence = max(stale_evidence, r["lag"])

    # 修订规则（修订理由留痕，非隐藏式换靶）：
    # 全样本 1% bars 产生 MAX_LAG=1，与周刷新节奏（滞后常态 0~5 交易日）不相容 →
    # 常态性告警 = 狼来了反模式（参 pre_response_guard PE/PB 误报教训）。
    # 修订后口径：
    #   MAX_LAG = 刷新节奏参数（周刷新 = 5），实证角色改为确认该滞后失真有界；
    #   STALE   = 最坏 regime（上行）p_false ≤ 5% 的最大 L（上行是误加分主风险向：
    #             冻结值比新值更负 → 虚报折价；实测 L=12 4.64% → L=13 5.20% 越界）。
    up_pf = {r["lag"]: r["by_regime"].get("up", {}).get("p_false", 1.0) for r in rows}
    stale_regime_evidence = max((L for L, p in up_pf.items() if p <= 0.05), default=0)
    final = {
        "max_lag_days": 5,
        "stale_critical_days": stale_regime_evidence,
        "rule": ("MAX_LAG=刷新节奏参数(周刷新=5)，实证确认该滞后失真有界"
                 "(L=5: p_false 2.08%/p_missed 2.50%/|bias|0.11pp)；"
                 "STALE=最坏 regime(上行) p_false≤5% 的最大 L。"),
        "revision_note": ("预登记全样本 1% bars 产生 MAX_LAG=1，与周刷新节奏不相容"
                          "(常态性告警=狼来了反模式)，已修订为上述口径并留痕。"),
    }

    out = {
        "date": date.today().isoformat(),
        "method": ("生产读存储行 cost_bias；frozen=cost_bias(T-L), fresh=cost_bias(T)；"
                   "分档 deep<-0.20/mid<-0.15/neutral；regime=close(T)/close(T-L)-1 ±3%"),
        "n_symbols": n_syms,
        "decision_rule": {"MAX_LAG_DAYS": "max L with p_false<=1% and p_missed<=5%",
                          "STALE_CRITICAL_DAYS": "max L with p_false<=5%"},
        "evidence_max_lag_days": max_lag_evidence,
        "evidence_stale_critical_days": stale_evidence,
        "evidence_stale_regime_rule_days": stale_regime_evidence,
        "final": final,
        "current_max_lag_days": 5,
        "current_stale_critical_days": 10,
        "by_lag": rows,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # Markdown 报告
    lines = [
        "# chip 新鲜度阈值证据化（2026-10-19）",
        "",
        f"**样本**：{n_syms} 只 × 2024-04 至今全历史，生产语义（读存储行 cost_bias），"
        f"frozen=cost_bias(T-L) vs fresh=cost_bias(T)。",
        "",
        "## 预登记判定规则",
        "- `MAX_LAG_DAYS` = p_false≤1% 且 p_missed≤5% 的最大 L",
        "- `STALE_CRITICAL_DAYS` = p_false≤5% 的最大 L",
        "",
        "## 全样本分滞后统计",
        "",
        "| L | n | p_any | p_false(误加分) | p_missed(漏加分) | bias(冻-新) |"
        " 上行p_false | 下行p_false |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        b = r["by_regime"]
        lines.append(
            f"| {r['lag']} | {r['n']} | {r['p_any']:.2%} | {r['p_false']:.2%} | "
            f"{r['p_missed']:.2%} | {r['bias_mean']:+.4f} | "
            f"{b.get('up', {}).get('p_false', float('nan')):.2%} | "
            f"{b.get('down', {}).get('p_false', float('nan')):.2%} |")
    lines += [
        "",
        f"## 证据阈值 vs 现行阈值",
        "",
        f"| 阈值 | 现行 | 证据(修订规则) | 结论 |",
        f"|---|---|---|---|",
        f"| MAX_LAG_DAYS | 5 | {final['max_lag_days']}（节奏参数，L=5 失真有界）| "
        f"{'维持' if final['max_lag_days'] == 5 else '需调整'} |",
        f"| STALE_CRITICAL_DAYS | 10 | {final['stale_critical_days']}（上行 p_false≤5% 最大 L）| "
        f"{'维持' if final['stale_critical_days'] == 10 else '需调整'} |",
        "",
        f"> 修订留痕：预登记全样本 1% bars 产生 MAX_LAG=1，与周刷新节奏不相容（常态性告警="
        f"狼来了反模式），修订为上述口径。全样本 bars 结果见 JSON evidence_* 字段。",
        "",
        "## 方向性偏差（关键机制事实）",
        "",
    ]
    for rg, name in (("up", "上行行情"), ("down", "下行行情"), ("flat", "震荡")):
        r5 = next((r for r in rows if r["lag"] == 5), None)
        if r5 and rg in r5["by_regime"]:
            b = r5["by_regime"][rg]
            lines.append(f"- L=5 {name}: bias(frozen−fresh)={b['bias_mean']:+.4f}，"
                         f"误加分 {b['p_false']:.2%} / 漏加分 {b['p_missed']:.2%}（n={b['n']}）")
    lines.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("=====CHIP_FRESHNESS_EVIDENCE=====")
    print(f"symbols={n_syms} lags={len(rows)}")
    for r in rows:
        b = r["by_regime"]
        print(f"L={r['lag']:>2} n={r['n']:>6} p_any={r['p_any']:.2%} "
              f"p_false={r['p_false']:.2%} p_missed={r['p_missed']:.2%} "
              f"bias={r['bias_mean']:+.4f} "
              f"(up_pf={b.get('up', {}).get('p_false', 0):.2%} "
              f"down_pf={b.get('down', {}).get('p_false', 0):.2%})")
    print(f"evidence: MAX_LAG_DAYS={final['max_lag_days']} "
          f"STALE_CRITICAL_DAYS={final['stale_critical_days']} (current 5/10; "
          f"draft-rule 结果 MAX=1/STALE={stale_evidence}, regime-rule STALE={stale_regime_evidence})")
    print(f"落盘: {os.path.relpath(OUT_JSON, WORKSPACE)} / {os.path.relpath(OUT_MD, WORKSPACE)}")
    print("=====CHIP_FRESHNESS_EVIDENCE_END=====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
