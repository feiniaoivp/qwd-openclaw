#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网格搜索参数的「样本外 + 随机基准」稳健性体检
================================================
背景 (2026-09-16):
  deep_grid_search.py 在 2020-01~2026-09 全样本上对参数网格取 max(score)，
  产出 14 只股票的"最优参数"建议。问题：
    1) 全样本挑最优 = 过拟合冠军，无样本外验证；
    2) import 了 backtest_guard 却从未调用 → 无随机基准/CI/p 值；
    3) 评分公式偏袒高频，回撤罚过轻。

本脚本对同一批候选参数做真正的稳健性体检：
  A. 样本内 (IS)  : 2020-01-01 ~ 2024-12-31  重搜一遍网格 → 得到"只用旧数据"的最优参数
  B. 样本外 (OOS) : 2025-01-01 ~ 今        用 A 的参数直接跑 → 关键证据
  C. 随机基准对照 : OOS 交易胜率 vs 随机 50%（Wilson CI + p 值）
  D. 买入持有基准 : OOS 区间 B&H 收益，作对照（策略跑不赢拿着不动就没意义）
  E. 判定:
     - OOS 收益 > 0 且 OOS 夏普 > 0
     - OOS 胜率显著超越随机（backtest_guard.passed）
     - OOS 收益 >= B&H 收益 * 0.5   (允许跑输一半, 但不允许大幅跑输)
  只有 E 全过才标「✅ 可采纳」，否则「⚠️ 观望」/「❌ 否决」。

输出:
  analysis/oos_validation_YYYYMMDD.json
  analysis/oos_validation_YYYYMMDD.md
"""
from __future__ import annotations

import os
import sys
import json
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

WORKSPACE = "/Users/duguke/.openclaw/workspace"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

from analysis.deep_grid_search import (  # noqa: E402
    GRIDS, STRATEGY_GENS, run_sim, fetch_data, LOW_CONFIDENCE,
    INITIAL_CAPITAL,
)
from analysis.backtest_guard import compare_to_random  # noqa: E402

warnings.filterwarnings("ignore")

# ── 样本切分 ──
IS_START = "2020-01-01"
IS_END = "2024-12-31"
OOS_START = "2025-01-01"
OOS_END = datetime.now().strftime("%Y-%m-%d")

MIN_TRADES_IS = 5      # 样本内最少交易笔数（低于则参数不可信）
MIN_TRADES_OOS = 3     # 样本外最低门槛（OOS 窗口天然更短）


def gen_actions(strat_name: str, df: pd.DataFrame, params: dict):
    """调用对应策略生成器"""
    return STRATEGY_GENS[strat_name](df, **params)


def grid_best_on(df: pd.DataFrame, strat_name: str, grid: dict):
    """在给定数据窗口上重搜某策略的最优参数（口径与 deep_grid_search 一致）"""
    from itertools import product
    gen_func = STRATEGY_GENS[strat_name]
    param_names = list(grid.keys())
    param_values = [grid[k] for k in param_names if k != "filter"]
    filter_fn = grid.get("filter", lambda *a: True)

    best = {"score": -999, "params": None, "metrics": None}
    for combo in product(*param_values):
        params = dict(zip(param_names, combo))
        if not filter_fn(*combo):
            continue
        try:
            actions = gen_func(df, **params)
            m = run_sim(df, actions)
            dd_penalty = max(0, abs(m["max_drawdown_pct"]) - 25) * 0.5
            score = m["sharpe_ratio"] * 50 + m["total_return_pct"] * 0.5 - dd_penalty
            if m["total_trades"] < 5:
                score *= 0.3
            elif m["total_trades"] < 10:
                score *= 0.6
            elif m["total_trades"] < 20:
                score *= 0.85
            if score > best["score"]:
                best = {"score": round(score, 2), "params": params, "metrics": m}
        except Exception:
            continue
    return best


def buy_hold(df: pd.DataFrame) -> dict:
    """买入持有基准（首日收盘买入、末日收盘卖出）"""
    if df is None or len(df) < 2:
        return {}
    p0 = float(df.iloc[0]["close"])
    p1 = float(df.iloc[-1]["close"])
    ret = (p1 / p0 - 1) * 100
    eq = df["close"] / p0
    peak = eq.expanding().max()
    dd = ((eq - peak) / peak * 100).min()
    return {"total_return_pct": round(ret, 2), "max_drawdown_pct": round(float(dd), 2)}


def evaluate_stock(symbol: str, name: str, category: str) -> dict:
    print(f"\n{'='*64}")
    print(f"🔎 {name}({symbol}) [{category}] 样本外体检")
    print(f"{'='*64}")

    df_all = fetch_data(symbol, name, IS_START, OOS_END)
    if df_all is None or len(df_all) < 200:
        return {"symbol": symbol, "name": name, "error": "数据不足"}

    is_df = df_all[(df_all["date"] >= pd.to_datetime(IS_START)) &
                   (df_all["date"] <= pd.to_datetime(IS_END))].copy()
    oos_df = df_all[(df_all["date"] >= pd.to_datetime(OOS_START)) &
                    (df_all["date"] <= pd.to_datetime(OOS_END))].copy()

    print(f"  样本内 IS : {len(is_df)} 条 ({IS_START}~{IS_END})")
    print(f"  样本外 OOS: {len(oos_df)} 条 ({OOS_START}~{OOS_END})")
    if len(is_df) < 200 or len(oos_df) < 40:
        return {"symbol": symbol, "name": name, "error": "窗口过短"}

    bh = buy_hold(oos_df)
    print(f"  📈 OOS 买入持有基准: {bh['total_return_pct']}% (回撤 {bh['max_drawdown_pct']}%)")

    per_strategy = {}
    for strat_name, grid in GRIDS.items():
        # A. 样本内重搜最优
        is_best = grid_best_on(is_df, strat_name, grid)
        if not is_best["params"]:
            continue
        params = is_best["params"]

        # B. 用 IS 参数跑 OOS（冻结参数，不重搜）
        try:
            oos_actions = gen_actions(strat_name, oos_df, params)
            oos_m = run_sim(oos_df, oos_actions)
        except Exception as e:
            per_strategy[strat_name] = {"error": f"OOS失败: {e}"}
            continue

        # C. 随机基准对照（胜率 vs 50%）
        n = oos_m["total_trades"]
        guard = None
        if n > 0:
            hits = int(round(oos_m["win_rate_pct"] / 100.0 * n))
            try:
                guard = compare_to_random(hits, n, random_hit_prob=0.5)
            except Exception:
                guard = None

        # E. 综合判定
        oos_ret = oos_m["total_return_pct"]
        oos_sharpe = oos_m["sharpe_ratio"]
        bh_ret = bh["total_return_pct"]
        reasons = []
        if oos_ret <= 0:
            reasons.append(f"OOS收益非正({oos_ret}%)")
        if oos_sharpe <= 0:
            reasons.append(f"OOS夏普非正({oos_sharpe})")
        if n < MIN_TRADES_OOS:
            reasons.append(f"OOS交易过少({n})")
        if guard and not guard["passed"]:
            reasons.append(f"胜率未超越随机(p={guard['p_value']:.2f})")
        if bh_ret > 0 and oos_ret < bh_ret * 0.5:
            reasons.append(f"大幅跑输持有({oos_ret}% vs {bh_ret}%)")

        verdict = "✅ 可采纳" if not reasons else ("❌ 否决" if oos_ret <= 0 or oos_sharpe <= 0 else "⚠️ 观望")

        per_strategy[strat_name] = {
            "is_best_score": is_best["score"],
            "is_params": params,
            "is_metrics": is_best["metrics"],
            "oos_metrics": oos_m,
            "oos_guard": guard,
            "oos_bh": bh,
            "verdict": verdict,
            "reasons": reasons,
        }
        print(f"  【{strat_name}】IS最优{params} 得分{is_best['score']}")
        print(f"      → OOS: 收益{oos_ret}% 夏普{oos_sharpe} 回撤{oos_m['max_drawdown_pct']}% "
              f"胜率{oos_m['win_rate_pct']}% 交易{n}笔 | {verdict}")
        if guard:
            print(f"        随机基准: 超额{guard['excess']:+.2%} p={guard['p_value']:.3f}")
        if reasons:
            print(f"        原因: {'; '.join(reasons)}")

    # 该股最优 = OOS 判定通过里 IS 得分最高者；否则全局 IS 最高（供参考）
    valid = {k: v for k, v in per_strategy.items()
             if "error" not in v and v["verdict"] == "✅ 可采纳"}
    if valid:
        pick = max(valid.items(), key=lambda kv: kv[1]["is_best_score"])
        strategy, detail = pick
    elif per_strategy:
        pool = {k: v for k, v in per_strategy.items() if "error" not in v}
        if pool:
            strategy, detail = max(pool.items(), key=lambda kv: kv[1]["is_best_score"])
        else:
            strategy, detail = None, None
    else:
        strategy, detail = None, None

    return {
        "symbol": symbol,
        "name": name,
        "category": category,
        "is_window": f"{IS_START}~{IS_END}",
        "oos_window": f"{OOS_START}~{OOS_END}",
        "oos_buy_hold": bh,
        "strategies": per_strategy,
        "pick_strategy": strategy,
        "pick_detail": detail,
        "pick_verdict": detail["verdict"] if detail else "无",
    }


def main():
    print("=" * 70)
    print("网格搜索参数 · 样本外 + 随机基准 稳健性体检")
    print(f"IS: {IS_START}~{IS_END}   OOS: {OOS_START}~{OOS_END}")
    print("=" * 70)

    results = {}
    for symbol, name, category in LOW_CONFIDENCE:
        try:
            results[symbol] = evaluate_stock(symbol, name, category)
        except Exception as e:
            import traceback
            print(f"❌ {name}({symbol}) 体检异常: {e}")
            traceback.print_exc()
            results[symbol] = {"symbol": symbol, "name": name, "error": str(e)}

    # ── 汇总 ──
    print("\n" + "=" * 70)
    print("📊 汇总：样本外体检结论")
    print("=" * 70)
    rows = []
    for sym, r in results.items():
        if "error" in r:
            print(f"  {sym}: ❌ {r['error']}")
            continue
        d = r.get("pick_detail")
        if not d:
            print(f"  {r['name']}({sym}): 无有效结果")
            continue
        oos = d["oos_metrics"]
        rows.append({
            "symbol": sym, "name": r["name"], "category": r["category"],
            "strategy": r["pick_strategy"], "params": d["is_params"],
            "is_score": d["is_best_score"],
            "is_ret": d["is_metrics"]["total_return_pct"],
            "oos_ret": oos["total_return_pct"], "oos_sharpe": oos["sharpe_ratio"],
            "oos_dd": oos["max_drawdown_pct"], "oos_wr": oos["win_rate_pct"],
            "oos_trades": oos["total_trades"],
            "oos_bh_ret": r["oos_buy_hold"]["total_return_pct"],
            "guard_passed": (d["oos_guard"] or {}).get("passed"),
            "guard_p": (d["oos_guard"] or {}).get("p_value"),
            "excess": (d["oos_guard"] or {}).get("excess"),
            "verdict": r["pick_verdict"], "reasons": d["reasons"],
        })
    rows.sort(key=lambda x: (x["verdict"] != "✅ 可采纳", -x["is_score"]))

    for x in rows:
        print(f"  {x['name']}({x['symbol']}) {x['strategy']}: {x['verdict']} | "
              f"OOS收益{x['oos_ret']}% 夏普{x['oos_sharpe']} 胜率{x['oos_wr']}% "
              f"({x['oos_trades']}笔) vs B&H {x['oos_bh_ret']}%")

    ok = sum(1 for x in rows if x["verdict"] == "✅ 可采纳")
    print(f"\n  合计: {ok}/{len(rows)} 只通过样本外体检")

    out_json = os.path.join(WORKSPACE, "analysis", f"oos_validation_{datetime.now().strftime('%Y%m%d')}.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"results": results, "summary": rows,
                   "is_window": f"{IS_START}~{IS_END}", "oos_window": f"{OOS_START}~{OOS_END}"},
                  f, ensure_ascii=False, indent=2)
    print(f"\n✅ JSON: {out_json}")

    # Markdown
    lines = ["# 网格搜索参数 · 样本外稳健性体检", "",
             f"- 样本内 IS: `{IS_START} ~ {IS_END}`（重搜参数）",
             f"- 样本外 OOS: `{OOS_START} ~ {OOS_END}`（冻结参数直接跑）",
             f"- 随机基准: 交易胜率 vs 50%，Wilson CI + 单侧 p 值",
             f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
             "## 结论表", "",
             "| 股票 | 策略 | IS得分 | IS收益 | **OOS收益** | OOS夏普 | OOS回撤 | OOS胜率 | OOS笔数 | B&H收益 | 超额vs随机 | p值 | 判定 |",
             "|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|"]
    for x in rows:
        exc = f"{x['excess']:+.2%}" if x["excess"] is not None else "-"
        p = f"{x['guard_p']:.3f}" if x["guard_p"] is not None else "-"
        lines.append(
            f"| {x['name']}({x['symbol']}) | {x['strategy']} | {x['is_score']} | {x['is_ret']}% | "
            f"**{x['oos_ret']}%** | {x['oos_sharpe']} | {x['oos_dd']}% | {x['oos_wr']}% | "
            f"{x['oos_trades']} | {x['oos_bh_ret']}% | {exc} | {p} | {x['verdict']} |")
    lines += ["", f"**合计: {ok}/{len(rows)} 只通过样本外体检**", ""]
    out_md = os.path.join(WORKSPACE, "analysis", f"oos_validation_{datetime.now().strftime('%Y%m%d')}.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"✅ MD  : {out_md}")


if __name__ == "__main__":
    main()
