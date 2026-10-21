#!/usr/bin/env python3
"""三臂口径审计 · 分析层：读 JSON → 配对 bootstrap CI + argmax 翻转率 + map 迁移 → MD。"""
import os
import sys
import json

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np

IN_JSON = os.path.join(WORKSPACE, "analysis/research/2026-10-21_exec_caliber_data_audit.json")
OUT_MD = os.path.join(WORKSPACE, "analysis/research/2026-10-21_exec_caliber_data_audit.md")
MAP_JSON = os.path.join(WORKSPACE, "data/adaptive_strategy_map.json")

KEY2NAME = {
    "macd": "纯MACD(基准B)",
    "ema_cross": "EMA12/26金叉(基准C)",
    "ema_obv": "EMA+OBV",
    "bollinger": "布林带+ATR",
    "kdj_cci": "KDJ+CCI",
    "bull_trend": "牛市趋势跟踪",
    "ema_adaptive_vol": "波动自适应EMA",
}
CANDIDATES = list(KEY2NAME.values())  # 7 候选（排除参照），与 validate 口径一致
MIN_TRADES = 3


def boot_ci(deltas, n=3000, seed=7):
    rng = np.random.default_rng(seed)
    deltas = np.asarray(deltas, dtype=float)
    if len(deltas) < 5:
        return None
    means = [float(np.mean(rng.choice(deltas, size=len(deltas), replace=True))) for _ in range(n)]
    return [round(float(np.percentile(means, 2.5)) * 100, 3), round(float(np.percentile(means, 97.5)) * 100, 3)]


def fmt_ci(ci):
    return f"[{ci[0]:+.2f}, {ci[1]:+.2f}]" if ci else "n/a"


def main():
    d = json.load(open(IN_JSON))
    stocks = d["stocks"]
    syms = sorted(stocks.keys())
    strat_names = d["meta"]["strategies"]
    cands = [s for s in strat_names if s in CANDIDATES]

    def val(sym, arm, st, field):
        v = stocks[sym]["arms"][arm].get(st, {})
        return v.get(field) if isinstance(v, dict) else None

    # ── 1. 全池均值表 ──
    pool_rows = []
    for st in strat_names:
        row = {"strategy": st}
        for arm in ("A1", "A2", "A3"):
            sh = [val(s, arm, st, "sharpe_ratio") for s in syms]
            tr = [val(s, arm, st, "total_return_pct") for s in syms]
            sh = [x for x in sh if x is not None]
            tr = [x for x in tr if x is not None]
            row[f"{arm}_sharpe_mean"] = round(float(np.mean(sh)), 3) if sh else None
            row[f"{arm}_ret_mean"] = round(float(np.mean(tr)), 1) if tr else None
        pool_rows.append(row)

    # ── 2. 配对效应（全池 7 候选均值层面的 delta，按票配对） ──
    def paired(st, armB, armA, field):
        ds = []
        for s in syms:
            b, a = val(s, armB, st, field), val(s, armA, st, field)
            if b is not None and a is not None:
                ds.append(b - a)
        return ds

    effect_rows = []
    for st in cands:
        r = {"strategy": st}
        for label, B, A in (("复权效应(A2−A1)", "A2", "A1"), ("窗口效应(A3−A2)", "A3", "A2"), ("合计(A3−A1)", "A3", "A1")):
            ds_sh = paired(st, B, A, "sharpe_ratio")
            ds_tr = paired(st, B, A, "total_return_pct")
            r[f"{label}_sharpe"] = round(float(np.mean(ds_sh)), 3) if ds_sh else None
            r[f"{label}_sharpe_ci"] = boot_ci(ds_sh)
            r[f"{label}_ret"] = round(float(np.mean(ds_tr)), 1) if ds_tr else None
            r[f"{label}_ret_ci"] = boot_ci(ds_tr)
        effect_rows.append(r)

    # ── 3. argmax 翻转率（sharpe，total_trades>=MIN_TRADES） ──
    def argmax_map(arm):
        out = {}
        for s in syms:
            best, best_sh = None, -1e9
            for st in cands:
                sh, nt = val(s, arm, st, "sharpe_ratio"), val(s, arm, st, "total_trades")
                if sh is not None and nt is not None and nt >= MIN_TRADES and sh > best_sh:
                    best, best_sh = st, sh
            out[s] = best
        return out

    m1, m2, m3 = argmax_map("A1"), argmax_map("A2"), argmax_map("A3")
    flip_21 = [s for s in syms if m1[s] != m2[s]]
    flip_32 = [s for s in syms if m2[s] != m3[s]]
    flip_31 = [s for s in syms if m1[s] != m3[s]]

    # ── 4. 现行 map 的口径迁移 ──
    smap = json.load(open(MAP_JSON))
    map_rows = []
    for sym in syms:
        v = smap.get(sym)
        key = v["strategy"] if isinstance(v, dict) else v
        st = KEY2NAME.get(key)
        if st is None:
            continue
        sh1, sh3 = val(sym, "A1", st, "sharpe_ratio"), val(sym, "A3", st, "sharpe_ratio")
        best3 = m3.get(sym)
        map_rows.append({
            "sym": sym, "name": stocks[sym]["name"], "map_key": key, "strategy": st,
            "sharpe_A1": sh1, "sharpe_A3": sh3,
            "still_best_under_A3": (best3 == st),
        })
    still_best = sum(1 for r in map_rows if r["still_best_under_A3"])

    # ── 5. 跳变证据汇总 ──
    jump_rows = sorted(
        ({"sym": s, "name": stocks[s]["name"], **stocks[s]["gap_jumps"]}
         for s in syms if stocks[s].get("gap_jumps", {}).get("jump_days_gt1pp")),
        key=lambda r: -r["max_single_day_distortion_pp"])

    # ── 输出 MD ──
    L = []
    L.append("# 回测数据口径三臂对照审计（2026-10-21 回测线深究第一轮）\n")
    L.append("> 触发：2026-10-21 20:30 quant-backtest-daily provenance 坐实 —— 36 只中 **34 只**在 baostock 登录抖动时")
    L.append("> 经 DataRouter 降级到「新浪日K · 不复权 · 300 根」(≈15 个月窗口)，报告头仍声称 20240101~20261021；")
    L.append("> backtest_strategies.py 不消费 provenance，降级零标注。本审计用 canonical 同款模拟器（同日收盘成交、同成本），")
    L.append("> 唯一变量=数据口径，36 票 × 8 策略（7 候选 + 买入持有参照）× 3 臂。\n")
    L.append("| 臂 | 数据 | 隔离效应 |\n|---|---|---|")
    L.append("| A1 | 新浪 300 根不复权（复刻降级实况） | — |")
    L.append("| A2 | 与 A1 同日期窗口 qfq | **复权效应** = A2−A1 |")
    L.append("| A3 | 2024-01-01 起 qfq（671~676 根） | **窗口效应** = A3−A2 |\n")

    L.append("## 1. 全池均值（36 票）\n")
    L.append("| 策略 | A1 夏普 | A2 夏普 | A3 夏普 | A1 收益% | A3 收益% |")
    L.append("|---|---|---|---|---|---|")
    for r in pool_rows:
        L.append(f"| {r['strategy']} | {r['A1_sharpe_mean']} | {r['A2_sharpe_mean']} | {r['A3_sharpe_mean']} | {r['A1_ret_mean']} | {r['A3_ret_mean']} |")

    L.append("\n## 2. 配对效应（按票配对，bootstrap 95% CI）\n")
    L.append("| 策略 | 复权效应 夏普Δ | 95%CI | 窗口效应 夏普Δ | 95%CI | 合计 夏普Δ | 95%CI |")
    L.append("|---|---|---|---|---|---|---|")
    for r in effect_rows:
        L.append(f"| {r['strategy']} | {r['复权效应(A2−A1)_sharpe']:+.3f} | {fmt_ci(r['复权效应(A2−A1)_sharpe_ci'])} "
                 f"| {r['窗口效应(A3−A2)_sharpe']:+.3f} | {fmt_ci(r['窗口效应(A3−A2)_sharpe_ci'])} "
                 f"| {r['合计(A3−A1)_sharpe']:+.3f} | {fmt_ci(r['合计(A3−A1)_sharpe_ci'])} |")

    L.append("\n## 3. 逐票最优策略（argmax sharpe, n_trades≥3）翻转率\n")
    L.append(f"- A1→A2（复权）翻转：**{len(flip_21)}/{len(syms)}**")
    L.append(f"- A2→A3（窗口）翻转：**{len(flip_32)}/{len(syms)}**")
    L.append(f"- A1→A3（合计）翻转：**{len(flip_31)}/{len(syms)}**")
    L.append("- 合计翻转明细：")
    for s in flip_31:
        L.append(f"  - {stocks[s]['name']}({s}): {m1[s]} → {m3[s]}")

    L.append("\n## 4. 现行 strategy_map 在正确口径(A3)下的成色\n")
    L.append(f"- 36 个映射中 **{still_best}/{len(map_rows)}** 在 A3 下仍是该票最优（n_trades≥3）；{len(map_rows)-still_best} 个名次易主。")
    still = [r for r in map_rows if r["still_best_under_A3"]]
    lost = [r for r in map_rows if not r["still_best_under_A3"]]
    L.append("\n### 名次易主（现行 map 在 A3 下不再最优）\n")
    L.append("| 股票 | map 策略 | 夏普A1 | 夏普A3 | A3 下最优 |")
    L.append("|---|---|---|---|---|")
    for r in lost:
        b3 = m3.get(r["sym"])
        best_sh = val(r["sym"], "A3", b3, "sharpe_ratio") if b3 else None
        L.append(f"| {r['name']}({r['sym']}) | {r['strategy']} | {r['sharpe_A1']} | {r['sharpe_A3']} | {b3} ({best_sh}) |")

    L.append("\n## 5. 复权跳变直接证据（A1 vs A2 单日收益差 >1pp 的日子）\n")
    if jump_rows:
        L.append("| 股票 | 跳变日数 | 最大单日失真(pp) | 日期样本 |")
        L.append("|---|---|---|---|")
        for r in jump_rows[:10]:
            dates = ", ".join(f"{j['date']}({j['drag_pp']}pp)" for j in r.get("jump_dates", [])[:2])
            L.append(f"| {r['name']}({r['sym']}) | {r['jump_days_gt1pp']} | {r['max_single_day_distortion_pp']} | {dates} |")
    else:
        L.append("无（15 个月窗口内除权事件稀少——但这是**幸存窗口**的结论，见局限）")

    L.append("\n## 汇总与解读\n")
    L.append("见会话报告。关键数字以本文件表格为准。\n")
    L.append("## 局限\n")
    L.append("1. 模拟器=同日收盘成交（与 canonical 一致），执行口径（T+1 开盘）是另一条待审轴。")
    L.append("2. A1 的跳变证据只覆盖 15 个月窗口；2024-01~2025-07 的失真（更多除权日）未纳入 A1 对照，")
    L.append("   全窗口不复权口径的伤害**大于**本表 A2−A1 读数（方向性下界）。")
    L.append("3. 三臂共享同一交易日历；新浪若停牌日缺失，A1/A2 对齐行数已由 gap_jumps.aligned_rows 校验。")
    L.append("4. baostock 前复权截至 2026-10-21；未来除权会使 qfq 序列整体平移（回测内自洽）。")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"written: {OUT_MD} ({len('\n'.join(L))} chars)")

    # 关键数字 stdout 摘要（供会话引用）
    print("\n=== 关键摘要 ===")
    print(f"argmax 翻转: A1→A2={len(flip_21)} A2→A3={len(flip_32)} A1→A3={len(flip_31)} / {len(syms)}")
    print(f"map 仍最优: {still_best}/{len(map_rows)}")
    for r in pool_rows[:8]:
        print(f"{r['strategy']}: A1={r['A1_sharpe_mean']} A2={r['A2_sharpe_mean']} A3={r['A3_sharpe_mean']}")


if __name__ == "__main__":
    main()
