#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关注股「择时 vs 买入持有」全量验证（快速版）
==============================================
对完整关注池 (35 只) 回答：K线择时值不值得做？还是买入持有更好？

关键设计（修正 2026-09-16 卡死问题）：
  上一版对每只股在每个窗口都重跑全参数网格（~19k 次 run_sim，10+ 分钟不结束）。
  本版：
    1. 参数**只搜一次**（用全样本 IS 窗口），OOS 直接复用冻结参数 —— 不再重搜。
    2. 每只股完成即 flush 打印，支持进度观察。
    3. 输出分批落盘（每只完成即写 JSON），中断也不丢结果。

方法：
  - B&H 基准: 区间首日收盘买入、末日收盘卖出（计一次滑点+佣金）。
  - 择时: 5 策略 (ema_cross/macd/bollinger/ema_obv/kdj_cci)，参数取 IS 网格最优。
  - 对比: 全样本 & OOS 两个窗口的「择时收益 - B&H 收益」。
  - 分类:
      ✅ 适合择时 : OOS 跑赢 B&H 的策略占比 >= 60%
      ➖ 适合持有 : 占比 <= 40% 且 B&H 为正
      ❌ 都不行   : B&H 为负 且 择时占比 < 50%
      ⚪ 无优势   : 其余
"""
from __future__ import annotations

import os
import sys
import json
import socket
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

# 网络兜底：防止数据源挂起导致进程永久阻塞（见 2026-08-28 教训 network.hang）
socket.setdefaulttimeout(20)

WORKSPACE = "/Users/duguke/.openclaw/workspace"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

from analysis.deep_grid_search import GRIDS, STRATEGY_GENS, run_sim, fetch_data, INITIAL_CAPITAL  # noqa: E402
from analysis.fib_extension_scan import WATCHLIST  # noqa: E402

warnings.filterwarnings("ignore")

FULL_START = "2020-01-01"
FULL_END = datetime.now().strftime("%Y-%m-%d")
OOS_START = "2025-01-01"
OOS_END = FULL_END
SLIPPAGE, COMMISSION = 0.001, 0.0003


def log(msg: str):
    print(msg, flush=True)


def buy_hold_metrics(df: pd.DataFrame) -> dict:
    if df is None or len(df) < 2:
        return {}
    p0, p1 = float(df.iloc[0]["close"]), float(df.iloc[-1]["close"])
    buy_p, sell_p = p0 * (1 + SLIPPAGE), p1 * (1 - SLIPPAGE)
    shares = int(INITIAL_CAPITAL * 0.95 / buy_p / 100) * 100
    if shares < 100:
        return {"total_return_pct": 0.0, "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0}
    cash = INITIAL_CAPITAL - shares * buy_p * (1 + COMMISSION)
    cash += shares * sell_p * (1 - COMMISSION)
    ret = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    r = df["close"].pct_change().dropna()
    sharpe = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0
    nav = df["close"] / buy_p
    dd = float(((nav - nav.expanding().max()) / nav.expanding().max() * 100).min())
    return {"total_return_pct": round(ret, 2), "sharpe_ratio": round(sharpe, 3),
            "max_drawdown_pct": round(dd, 2)}


def best_params_is(is_df: pd.DataFrame, strat: str):
    """仅在 IS 窗口搜一次最优参数"""
    from itertools import product
    grid = GRIDS[strat]
    gen = STRATEGY_GENS[strat]
    names = list(grid.keys())
    values = [grid[k] for k in names if k != "filter"]
    filt = grid.get("filter", lambda *a: True)
    bs, bp = -1e9, None
    for combo in product(*values):
        params = dict(zip(names, combo))
        if not filt(*combo):
            continue
        try:
            m = run_sim(is_df, gen(is_df, **params))
            dd_pen = max(0, abs(m["max_drawdown_pct"]) - 25) * 0.5
            sc = m["sharpe_ratio"] * 50 + m["total_return_pct"] * 0.5 - dd_pen
            if m["total_trades"] < 5:
                sc *= 0.3
            elif m["total_trades"] < 10:
                sc *= 0.6
            elif m["total_trades"] < 20:
                sc *= 0.85
            if sc > bs:
                bs, bp = sc, params
        except Exception:
            continue
    return bp


def analyze_stock(symbol: str, name: str) -> dict:
    df = fetch_data(symbol, name, FULL_START, FULL_END)
    if df is None or len(df) < 200:
        return {"symbol": symbol, "name": name, "error": "数据不足"}
    is_df = df[df["date"] <= pd.to_datetime("2024-12-31")].copy()
    oos_df = df[df["date"] >= pd.to_datetime(OOS_START)].copy()

    bh_full = buy_hold_metrics(df)
    bh_oos = buy_hold_metrics(oos_df) if len(oos_df) >= 2 else {}

    per = {}
    for strat in GRIDS.keys():
        p = best_params_is(is_df, strat) if len(is_df) >= 60 else None
        if p is None:
            continue
        try:
            m_full = run_sim(df, STRATEGY_GENS[strat](df, **p))
        except Exception:
            continue
        m_oos = None
        if len(oos_df) >= 40:
            try:
                m_oos = run_sim(oos_df, STRATEGY_GENS[strat](oos_df, **p))
            except Exception:
                m_oos = None
        exc_full = m_full["total_return_pct"] - bh_full.get("total_return_pct", 0)
        exc_oos = (m_oos["total_return_pct"] - bh_oos.get("total_return_pct", 0)) if m_oos else None
        per[strat] = {
            "params": p, "full": m_full, "full_excess": round(exc_full, 2),
            "oos": m_oos, "oos_excess": round(exc_oos, 2) if exc_oos is not None else None,
            "beats_full": exc_full > 0,
            "beats_oos": (exc_oos > 0) if exc_oos is not None else None,
        }

    valid_oos = [v for v in per.values() if v["beats_oos"] is not None]
    beats_oos = [v for v in valid_oos if v["beats_oos"]]
    ratio_oos = len(beats_oos) / len(valid_oos) if valid_oos else 0
    ratio_full = (len([v for v in per.values() if v["beats_full"]]) / len(per)) if per else 0
    bh_oos_ret = bh_oos.get("total_return_pct", 0) if bh_oos else 0

    if bh_oos_ret <= 0 and ratio_oos < 0.5:
        verdict = "❌ 都不行"
    elif ratio_oos >= 0.6:
        verdict = "✅ 适合择时"
    elif ratio_oos <= 0.4 and bh_oos_ret > 0:
        verdict = "➖ 适合持有"
    else:
        verdict = "⚪ 无优势"

    return {
        "symbol": symbol, "name": name,
        "bh_full": bh_full, "bh_oos": bh_oos,
        "strategies": per,
        "n_beats_oos": len(beats_oos), "n_valid_oos": len(valid_oos),
        "ratio_oos": round(ratio_oos, 2), "ratio_full": round(ratio_full, 2),
        "verdict": verdict,
    }


def main():
    log("=" * 72)
    log("关注股「择时 vs 买入持有」全量验证（快速版）")
    log(f"全样本: {FULL_START}~{FULL_END}   OOS: {OOS_START}~{OOS_END}   标的: {len(WATCHLIST)} 只")
    log("=" * 72)

    out_json = os.path.join(WORKSPACE, "analysis", f"timing_vs_hold_{datetime.now().strftime('%Y%m%d')}.json")
    # 断点续跑：读取已有结果，已完成的股票跳过（避免重复计算与网络重拉）
    results = {}
    if os.path.exists(out_json):
        try:
            with open(out_json, encoding="utf-8") as f:
                results = json.load(f).get("results", {})
            log(f"↻ 断点续跑：已有 {len(results)} 只结果，将跳过")
        except Exception:
            results = {}
    t0 = datetime.now()
    for i, (symbol, name) in enumerate(WATCHLIST.items(), 1):
        if symbol in results and "error" not in results[symbol]:
            log(f"[{i:2}/{len(WATCHLIST)}] {name}({symbol}) ↷ 已有结果，跳过")
            continue
        try:
            r = analyze_stock(symbol, name)
        except Exception as e:
            r = {"symbol": symbol, "name": name, "error": str(e)}
        results[symbol] = r
        if "error" in r:
            log(f"[{i:2}/{len(WATCHLIST)}] {name}({symbol}) ❌ {r['error']}")
        else:
            log(f"[{i:2}/{len(WATCHLIST)}] {name}({symbol}) {r['verdict']} | "
                f"OOS B&H {r['bh_oos'].get('total_return_pct','-')}% | "
                f"择时跑赢 {r['n_beats_oos']}/{r['n_valid_oos']} ({r['ratio_oos']:.0%})")
        # 分批落盘
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump({"results": results, "generated_at": datetime.now().isoformat()},
                      f, ensure_ascii=False, indent=2)
    log(f"\n⏱ 耗时: {(datetime.now()-t0).total_seconds():.0f}s   ✅ JSON: {out_json}")

    # 汇总 + Markdown
    ok = [r for r in results.values() if "error" not in r]
    buckets = {}
    for r in ok:
        buckets.setdefault(r["verdict"], []).append(r)

    log("\n" + "=" * 72)
    log("📊 分类结论")
    log("=" * 72)
    for v in ["✅ 适合择时", "➖ 适合持有", "⚪ 无优势", "❌ 都不行"]:
        items = buckets.get(v, [])
        log(f"\n{v} ({len(items)} 只)")
        for r in sorted(items, key=lambda x: -(x["bh_oos"].get("total_return_pct", -999) or -999)):
            log(f"   {r['name']}({r['symbol']}): OOS B&H {r['bh_oos'].get('total_return_pct','-')}% | "
                f"择时跑赢 {r['ratio_oos']:.0%}")

    lines = ["# 关注股「择时 vs 买入持有」全量验证", "",
             f"- 标的池: **{len(WATCHLIST)} 只**",
             f"- 全样本: `{FULL_START} ~ {FULL_END}`",
             f"- 样本外 OOS: `{OOS_START} ~ {OOS_END}`",
             f"- 策略: ema_cross / macd / bollinger / ema_obv / kdj_cci（参数在 IS 上取网格最优，OOS 冻结复用）",
             f"- 生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    for v in ["✅ 适合择时", "➖ 适合持有", "⚪ 无优势", "❌ 都不行"]:
        items = buckets.get(v, [])
        lines.append(f"## {v} ({len(items)} 只)")
        if not items:
            lines.append("- （无）")
        for r in sorted(items, key=lambda x: -(x["bh_oos"].get("total_return_pct", -999) or -999)):
            lines.append(f"- **{r['name']}**({r['symbol']}) — OOS B&H {r['bh_oos'].get('total_return_pct','-')}%，"
                         f"择时跑赢 {r['n_beats_oos']}/{r['n_valid_oos']}")
        lines.append("")
    lines += ["## 明细", "",
              "| 股票 | OOS B&H | 择时跑赢率(OOS) | 最优择时OOS超额 | 全样本跑赢率 | 结论 |",
              "|:---|:---:|:---:|:---:|:---:|:---:|"]
    for r in sorted(ok, key=lambda x: -(x["bh_oos"].get("total_return_pct", -999) or -999)):
        excs = [(v["oos_excess"], k) for k, v in r["strategies"].items() if v["oos_excess"] is not None]
        exc_s = f"{max(excs)[0]:+.1f}pct ({max(excs)[1]})" if excs else "-"
        lines.append(f"| {r['name']}({r['symbol']}) | {r['bh_oos'].get('total_return_pct','-')}% | "
                     f"{r['ratio_oos']:.0%} | {exc_s} | {r['ratio_full']:.0%} | {r['verdict']} |")
    out_md = os.path.join(WORKSPACE, "analysis", f"timing_vs_hold_{datetime.now().strftime('%Y%m%d')}.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"✅ MD  : {out_md}")


if __name__ == "__main__":
    main()
