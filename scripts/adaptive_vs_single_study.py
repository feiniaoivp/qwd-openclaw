#!/usr/bin/env python3
"""自适应分配 vs 单一策略对照研究（2026-10-17，用户议程第 3 项）。

问题: DSR 审计仅 6/33 显著——自适应策略映射是否优于「全池单策略」？
方法: 完全复用生产回测实现（backtest_strategies.ALL_STRATEGIES + run_simulation），
每股 7 候选策略（含买入持有基准）一次跑全，比较:
  1. 当前映射策略在 7 候选中的收益排名（随机基线 = 平均 4.0）
  2. 组合级等权收益/夏普: 映射组合 vs 各单一策略组合
  3. 配对胜率: 映射 vs 基准C(EMA金叉, 历史最优基准)
  4. DSR 显著子集的映射质量
注意: map 中 ema_adaptive_vol(3只) 不在周回测候选集——本身即为口径 finding。
产出: analysis/research/2026-10-17_adaptive_vs_single.json
"""
import os
import sys
import json
import time

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import logging
logging.getLogger().setLevel(logging.ERROR)

from analysis.backtest_strategies import (
    STOCKS, ALL_STRATEGIES, fetch_data, run_simulation,
    ensure_login, START_DATE, END_DATE,
)
from analysis.bs_session import logout as bs_logout

# 回测中文名 → map 英文键（手工核对，勿凭记忆）
CN2EN = {
    "布林带+ATR": "bollinger",
    "KDJ+CCI": "kdj_cci",
    "EMA+OBV": "ema_obv",
    "EMA12/26金叉(基准C)": "ema_cross",
    "纯MACD(基准B)": "macd",
    "牛市趋势跟踪": "bull_trend",
    "波动自适应EMA": "ema_adaptive_vol",
    "买入并持有(个股)": "buy_and_hold",
}
SKIP = {"沪深300基准", "中证500基准"}
CANDIDATES = [cn for cn, _ in ALL_STRATEGIES if cn not in SKIP]  # 7 个


def main():
    t0 = time.time()
    amap = json.load(open(os.path.join(WORKSPACE, "data/adaptive_strategy_map.json")))

    if not ensure_login():
        print("⚠️ baostock 登录失败，尝试降级链继续")

    matrix = {}   # symbol -> {cn_name: metrics}
    skipped = []
    for idx, (symbol, name) in enumerate(STOCKS):
        df = fetch_data(symbol, name)
        if df is None or len(df) < 100:
            skipped.append(f"{name}(数据不足)")
            continue
        row = {}
        for cn, sfunc in ALL_STRATEGIES:
            if cn in SKIP:
                continue
            try:
                actions = sfunc(df)
                mtc = run_simulation(df, actions)
                if "error" in mtc:
                    continue
                row[cn] = {
                    "total_return": mtc.get("total_return_pct"),
                    "sharpe": mtc.get("sharpe_ratio"),
                    "max_dd": mtc.get("max_drawdown_pct"),
                    "trades": mtc.get("total_trades"),
                }
            except Exception as e:
                print(f"  ⚠️ {name}/{cn}: {e}", flush=True)
        if len(row) >= 3:
            matrix[symbol] = {"name": name, "strategies": row}
        else:
            skipped.append(f"{name}(策略评估不足)")
        print(f"[study] {idx+1}/{len(STOCKS)} {name} 完成 ({time.time()-t0:.0f}s)", flush=True)

    try:
        bs_logout()
    except Exception:
        pass

    # 采集后立即落盘（防分析段异常丢数据；2026-10-17 双研究教训）
    raw_path = os.path.join(WORKSPACE, "analysis/research/2026-10-17_adaptive_vs_single.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump({"date": "2026-10-17", "window": f"{START_DATE}~{END_DATE}",
                   "candidates": CANDIDATES, "skipped": skipped, "matrix": matrix},
                  f, ensure_ascii=False, indent=1)
    print(f"[study] 原始矩阵已写 {raw_path}", flush=True)

    # ── 分析 ──
    mapped_rank, mapped_is_top, paired_vs_c = [], 0, 0
    paired_vs_c_n = 0
    port = {cn: [] for cn in CANDIDATES}
    port_mapped = []
    unmapped_strats = []
    for sym, item in matrix.items():
        strat_map = item["strategies"]
        rets = {cn: strat_map[cn]["total_return"] for cn in CANDIDATES
                if cn in strat_map and strat_map[cn].get("total_return") is not None}
        if len(rets) < 3:
            continue
        ranked = sorted(rets.items(), key=lambda x: (x[1] is not None, x[1] or -1e9), reverse=True)
        rank_of = {cn: i + 1 for i, (cn, _) in enumerate(ranked)}
        mp_key = amap.get(sym)
        mp_cn = next((cn for cn, en in CN2EN.items() if en == mp_key), None)
        if mp_cn is None:
            unmapped_strats.append(f"{item['name']}({sym})={mp_key}")
            continue
        if mp_cn not in rets:
            unmapped_strats.append(f"{item['name']}({sym})={mp_key}(候选集无此策略)")
            continue
        mapped_rank.append(rank_of[mp_cn])
        if rank_of[mp_cn] == 1:
            mapped_is_top += 1
        port_mapped.append(rets[mp_cn])
        for cn in CANDIDATES:
            if cn in rets:
                port[cn].append(rets[cn])
        if "EMA12/26金叉(基准C)" in rets:
            paired_vs_c_n += 1
            if rets[mp_cn] > rets["EMA12/26金叉(基准C)"]:
                paired_vs_c += 1

    n_mapped = len(mapped_rank)
    out = {
        "date": "2026-10-17",
        "window": f"{START_DATE}~{END_DATE}",
        "candidates": CANDIDATES,
        "stocks_evaluated": len(matrix),
        "skipped": skipped,
        "unmapped_note": unmapped_strats,
        "mapped_avg_rank": round(sum(mapped_rank) / n_mapped, 2) if n_mapped else None,
        "random_baseline_rank": round((len(CANDIDATES) + 1) / 2, 2),
        "mapped_top1_pct": round(mapped_is_top / n_mapped, 3) if n_mapped else None,
        "mapped_vs_benchC_wins": f"{paired_vs_c}/{paired_vs_c_n}",
        "portfolio": {
            "mapped_mean_return": round(sum(port_mapped) / len(port_mapped), 2) if port_mapped else None,
            "fixed_mean_return": {cn: round(sum(v) / len(v), 2) for cn, v in port.items() if v},
        },
        "matrix": matrix,
    }
    path = os.path.join(WORKSPACE, "analysis/research/2026-10-17_adaptive_vs_single.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"\n[study] 评估 {len(matrix)} 只 | 跳过 {skipped} | 映射候选外: {unmapped_strats}")
    print(f"[study] 映射策略平均排名: {out['mapped_avg_rank']} (随机基线 {out['random_baseline_rank']})")
    print(f"[study] 映射=#1 占比: {out['mapped_top1_pct']}")
    print(f"[study] 映射 vs 基准C 配对胜率: {out['mapped_vs_benchC_wins']}")
    print(f"[study] 组合等权收益: 映射 {out['portfolio']['mapped_mean_return']}% vs "
          + ", ".join(f"{cn} {v}%" for cn, v in out['portfolio']['fixed_mean_return'].items()))
    print(f"[study] 已写 {path} | 耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
