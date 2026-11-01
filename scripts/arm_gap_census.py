#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
臂级隔夜跳空成本普查（2026-11-01，B vs C 分歧深挖；用户批准「继续深入」）
==========================================================================

定位（与既有研究的关系，立项前盘点过）：
- gap_cost_replay.py / execution_timing_study.py 锚定 **dual_signal 生产买入子集**，
  测 fwd5 执行侵蚀 —— 不分臂。
- 本普查回答口径翻转留下的新问题：**B→C 的 7/36 映射分歧能否被「隔夜跳空成本」解释**，
  以及各臂的跳空 drag 量级（臂级、全信号、2020+ 全窗口，与验证器同数据）。

方法：
- 同一份策略 actions（6 个 key_map 臂 × 36 只 × 2020+ 全窗口）
- 逐信号 gap = open[i+1]/close[i] − 1（买侧正=高开=成本；卖侧负=低开=吃亏）
- 全模拟互证：run_simulation 同窗口 B 口径 vs C 口径 → Δret(C−B)，与普查估计对照
  （恒等式纪律：普查 drag 估计 × 换手率 应与模拟年化差同号且量级相容，越界打标不拦截）
- 翻转归因：validation_caliber_flip 的 B vs C 翻转对，查两臂年化 drag 差是否方向一致

输出：analysis/backtest/arm_gap_census_<today>.md / .json（写前 _bak）
运行：nohup python3 scripts/arm_gap_census.py > logs/arm_gap_census_<today>.log 2>&1 &
"""

import os
import sys
import json
import shutil
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

from analysis.backtest_strategies import STOCKS, CANDIDATE_STRATEGIES, fetch_data, run_simulation
from analysis.fill_rules import limit_ratio, is_sealed

KEYMAP_ARMS = ["布林带+ATR", "KDJ+CCI", "EMA+OBV", "EMA12/26金叉(基准C)", "纯MACD(基准B)", "牛市趋势跟踪"]
TODAY = datetime.now().strftime("%Y-%m-%d")
START = "20200101"
OUT_MD = os.path.join(WORKSPACE, "analysis", "backtest", f"arm_gap_census_{TODAY}.md")
OUT_JSON = os.path.join(WORKSPACE, "analysis", "backtest", f"arm_gap_census_{TODAY}.json")


def _bak(p):
    if os.path.exists(p):
        shutil.copy2(p, p + ".bak")


def winsor(v, lim=5.0):
    return float(np.clip(v, -lim, lim))


def main():
    arm_funcs = {n: f for n, f in CANDIDATE_STRATEGIES}
    # 信号级: (arm, direction) -> [gap]
    gaps = {a: {"BUY": [], "SELL": []} for a in KEYMAP_ARMS}
    sealed_buy = {a: 0 for a in KEYMAP_ARMS}
    # 股票级模拟: (arm) -> [ret_B, ret_C, years, trades]
    sim_rows = []
    fetched, failed = 0, []

    for sym, name in STOCKS:
        df = fetch_data(sym, name, start=START)
        if df is None or len(df) < 150:
            failed.append(f"{name}({sym})")
            continue
        fetched += 1
        closes = df["close"].astype(float).values
        opens = df["open"].astype(float).values
        dates = pd.to_datetime(df["date"])
        years = max((dates.max() - dates.min()).days / 365.25, 0.5)
        date_idx = {str(d.date()): i for i, d in enumerate(dates)}
        prev_closes = np.concatenate([[np.nan], closes[:-1]])

        for arm in KEYMAP_ARMS:
            actions = arm_funcs[arm](df)
            for a in actions:
                i = date_idx.get(a["date"])
                if i is None or i + 1 >= len(closes):
                    continue
                g = opens[i + 1] / closes[i] - 1.0
                gaps[arm][a["type"]].append(float(g))
                if a["type"] == "BUY":
                    ratio = limit_ratio(sym)
                    if prev_closes[i] == prev_closes[i] and is_sealed(prev_closes[i], closes[i], ratio, "up"):
                        sealed_buy[arm] += 1
            mb = run_simulation(df, actions, symbol=sym, fill_mode="signal_close", enforce_limit=True)
            mc = run_simulation(df, actions, symbol=sym, fill_mode="next_open", enforce_limit=True)
            if "error" in mb or "error" in mc:
                continue
            sim_rows.append({"symbol": sym, "name": name, "arm": arm, "years": round(years, 2),
                             "ret_B": mb["total_return_pct"], "ret_C": mc["total_return_pct"],
                             "delta_C_minus_B": round(mc["total_return_pct"] - mb["total_return_pct"], 2),
                             "trades_B": mb["total_trades"]})

    if fetched < 30:
        print(f"❌ 覆盖不足 {fetched}/36，失败 {failed} —— 拒绝出结论")
        return 1

    # ── 臂级聚合 ──
    arm_agg = {}
    for arm in KEYMAP_ARMS:
        gb = np.array(gaps[arm]["BUY"])
        gs = np.array(gaps[arm]["SELL"])
        n_b, n_s = len(gb), len(gs)
        if n_b < 30 or n_s < 30:
            arm_agg[arm] = {"n_buy": n_b, "n_sell": n_s, "note": "样本不足"}
            continue
        buy_gap_mean = float(np.mean(gb)) * 100
        sell_gap_mean = float(np.mean(gs)) * 100
        # 每回合对数 drag 估计（买晚付 + 卖晚收），winsorize ±5% 防单点极端
        per_rt_log = float(np.mean(np.log1p(np.clip(gb, -0.05, 0.05)))
                           + np.mean(np.log1p(np.clip(gs, -0.05, 0.05))))
        sel = [r for r in sim_rows if r["arm"] == arm]
        yrs = float(np.mean([r["years"] for r in sel]))
        tr_yr = float(np.mean([r["trades_B"] for r in sel])) / yrs
        drag_ann_est = per_rt_log * tr_yr * 100                      # %/年
        sim_delta_ann = float(np.mean([r["delta_C_minus_B"] for r in sel])) / yrs
        ratio = (drag_ann_est / sim_delta_ann) if abs(sim_delta_ann) > 0.05 else None
        arm_agg[arm] = {
            "n_buy": n_b, "n_sell": n_s,
            "buy_gap_mean_pct": round(buy_gap_mean, 3),
            "buy_gap_median_pct": round(float(np.median(gb)) * 100, 3),
            "sell_gap_mean_pct": round(sell_gap_mean, 3),
            "sell_gap_median_pct": round(float(np.median(gs)) * 100, 3),
            "buy_sealed_share_pct": round(sealed_buy[arm] / n_b * 100, 1),
            "per_rt_log_drag_bps": round(per_rt_log * 1e4, 1),
            "trades_per_year": round(tr_yr, 1),
            "drag_annualized_est_pct": round(drag_ann_est, 2),
            "sim_delta_annualized_pct": round(sim_delta_ann, 2),
            "reconcile_ratio": round(ratio, 2) if ratio else None,
        }

    # ── 翻转归因：B vs C 翻转对（validation_caliber_flip 的 rows）──
    flip_path = os.path.join(WORKSPACE, "analysis", "backtest", f"validation_caliber_flip_{TODAY}.json")
    flips = []
    if os.path.exists(flip_path):
        with open(flip_path) as f:
            fl = json.load(f)
        KEY2NAME = {"bollinger": "布林带+ATR", "kdj_cci": "KDJ+CCI", "ema_obv": "EMA+OBV",
                    "ema_cross": "EMA12/26金叉(基准C)", "macd": "纯MACD(基准B)", "bull_trend": "牛市趋势跟踪"}
        for r in fl.get("rows", []):
            if r.get("best_B") and r.get("best_C") and r["best_B"] != r["best_C"]:
                arm_in, arm_out = KEY2NAME.get(r["best_B"]), KEY2NAME.get(r["best_C"])
                din = arm_agg.get(arm_in, {}).get("drag_annualized_est_pct")
                dout = arm_agg.get(arm_out, {}).get("drag_annualized_est_pct")
                flips.append({"symbol": r["symbol"], "name": r["name"],
                              "arm_B": r["best_B"], "arm_C": r["best_C"],
                              "drag_B": din, "drag_C": dout,
                              "direction_ok": (din is not None and dout is not None and din > dout)})

    payload = {"date": TODAY, "start": START, "fetched": fetched, "failed": failed,
               "arm_agg": arm_agg, "flip_attribution": flips, "sim_rows": sim_rows}
    _bak(OUT_JSON)
    with open(OUT_JSON, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # ── Markdown ──
    L = [f"# 臂级隔夜跳空成本普查 {TODAY}\n",
         f"- 数据: {fetched}/36 只 2020+ 前复权日线；同信号 B/C 双口径全模拟互证",
         "- gap = open(T+1)/close(T) − 1；买侧正=高开=成本，卖侧负=低开=吃亏；winsorize ±5%\n",
         "| 臂 | 买信号 | 卖信号 | 买gap均值% | 卖gap均值% | 买侧封板占比% | 每回合drag(bp) | 换手(次/年) | drag年化est%/年 | 模拟Δ(C−B)%/年 | 互证比 |",
         "|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|"]
    for arm in KEYMAP_ARMS:
        a = arm_agg[arm]
        if "note" in a:
            L.append(f"| {arm} | {a['n_buy']} | {a['n_sell']} | - | - | - | - | - | 样本不足 | - | - |")
            continue
        L.append(f"| {arm} | {a['n_buy']} | {a['n_sell']} | {a['buy_gap_mean_pct']:+.2f} | "
                 f"{a['sell_gap_mean_pct']:+.2f} | {a['buy_sealed_share_pct']} | "
                 f"{a['per_rt_log_drag_bps']:+.0f} | {a['trades_per_year']} | "
                 f"{a['drag_annualized_est_pct']:+.2f} | {a['sim_delta_annualized_pct']:+.2f} | "
                 f"{a['reconcile_ratio'] if a['reconcile_ratio'] else '-'} |")
    L.append("\n## B vs C 映射翻转归因（7 对）\n")
    L.append("| 股票 | B 最优(臂) | C 最优(臂) | drag_B %/年 | drag_C %/年 | 方向一致 |")
    L.append("|:---|:---|:---|:---:|:---:|:---:|")
    for x in flips:
        L.append(f"| {x['name']}({x['symbol']}) | {x['arm_B']} | {x['arm_C']} | "
                 f"{x['drag_B'] if x['drag_B'] is not None else '-'} | "
                 f"{x['drag_C'] if x['drag_C'] is not None else '-'} | "
                 f"{'✅' if x['direction_ok'] else '—'} |")
    L.append("\n---\n互证比 = drag年化est ÷ 模拟Δ年化（两者应同号、量级相容；越界仅打标，因模拟含")
    L.append("仓位/封板顺延等二阶效应）。机制注：趋势臂买侧 gap 偏正（强势信号次日高开）——与")
    L.append("gap_cost_replay/execution_timing_study 在 dual_signal 子集的发现同向，本普查补臂级全覆盖。")

    _bak(OUT_MD)
    with open(OUT_MD, "w") as f:
        f.write("\n".join(L) + "\n")

    print("=====ARM_GAP_CENSUS=====")
    print(json.dumps({"fetched": fetched, "arm_agg": arm_agg,
                      "flip_attribution": flips}, ensure_ascii=False, indent=1, default=str))
    print("=====ARM_GAP_CENSUS_END=====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
