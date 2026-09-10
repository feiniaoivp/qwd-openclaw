#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
参数网格搜索引擎 - 重构版 (2026-09-09)
========================================
核心改进:
1. 统一使用 DataRouter (新浪日K主源 + baostock备用 + 缓存)
2. 复用 bs_session 单例会话
3. 增量调优模式 (--incremental): 仅重新调优近期表现恶化/策略变更的股票
4. 快速模式 (--fast): 缩减参数网格 + 单窗口预筛选 + 并行加速
5. 解耦评分逻辑: 复用 validate_strategies.py 的 aggregate_score

运行: python3 analysis/param_tune.py [--stocks 代码,代码] [--write] [--fast] [--incremental]
  --write         写回 data/adaptive_params.json + adaptive_strategy_map.json
  --fast          快速模式: 单窗口(W3近1.5年)预筛选 + 缩减网格, ~5分钟/35只
  --incremental   增量模式: 仅重新调优需更新的股票 (需 --write 配合)
  --top-k N       仅输出每只股票 Top N 个参数组合 (默认 1)
"""

import os
import sys
import json
import time
import datetime
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ProcessPoolExecutor, as_completed

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

from backtest_strategies import (
    INITIAL_CAPITAL, COMMISSION, SLIPPAGE,
    run_simulation, STOCKS,
)
from analysis.data_layer import get_router
from analysis.bs_session import ensure_login

PARAMS_FILE = os.path.join(WORKSPACE, "data", "adaptive_params.json")
STRATEGY_MAP_FILE = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")
CACHE_DIR = os.path.join(WORKSPACE, "data", "param_tune_cache")

os.makedirs(CACHE_DIR, exist_ok=True)

# ════════════════════════════════════════
# 参数网格定义
# ════════════════════════════════════════
PARAM_GRIDS = {
    "bollinger": {
        "length": [15, 20, 25],
        "std": [1.5, 2.0, 2.5],
        "atr_mult": [1.0, 1.5, 2.0],
    },
    "kdj_cci": {
        "length": [7, 9, 12],
        "rsi_threshold": [30, 40, 50],
    },
    "ema_obv": {
        "length": [15, 20, 25],
    },
    "ema_cross": {
        "fast": [8, 10, 12, 15],
        "slow": [21, 26, 30],
    },
    "macd": {
        "fast": [10, 12, 15],
        "slow": [22, 26, 30],
        "signal": [7, 9, 12],
    },
}

# 快速模式: 每个策略仅保留核心参数 (组合数从 81/9/3/12/27 降至 6/2/1/4/4)
FAST_GRIDS = {
    "bollinger": {"length": [20], "std": [2.0, 2.5], "atr_mult": [1.5, 2.0]},      # 4 组合
    "kdj_cci": {"length": [9], "rsi_threshold": [40, 50]},                         # 2 组合
    "ema_obv": {"length": [20]},                                                    # 1 组合
    "ema_cross": {"fast": [10, 12], "slow": [26]},                                 # 2 组合
    "macd": {"fast": [12], "slow": [26], "signal": [9, 12]},                       # 2 组合
}

STRATEGY_NAMES = {
    "bollinger": "布林带+ATR",
    "kdj_cci": "KDJ+RSI",
    "ema_obv": "EMA+OBV",
    "ema_cross": "EMA12/26金叉",
    "macd": "纯MACD",
}

DEFAULT_PARAMS = {
    "bollinger": {"length": 20, "std": 2.0, "atr_mult": 1.5},
    "kdj_cci": {"length": 9, "rsi_threshold": 40},
    "ema_obv": {"length": 20},
    "ema_cross": {"fast": 12, "slow": 26},
    "macd": {"fast": 12, "slow": 26, "signal": 9},
}

# 多窗口定义
WIN_START = "2020-01-01"
WINDOWS = [
    ("W1_全量", 0),
    ("W2_近3年", 3),
    ("W3_近1.5年", 1.5),
    ("W4_近1年", 1),
]
FAST_WINDOW = ("W3_近1.5年", 1.5)  # 快速模式仅用 W3


# ════════════════════════════════════════
# 参数化策略函数 (向后兼容 backtest_strategies.py)
# ════════════════════════════════════════

def p_bollinger_atr(df, p):
    import pandas_ta as ta
    df = df.copy()
    bb = ta.bbands(df["close"], length=p["length"], std=p["std"])
    bbu = bb[[c for c in bb.columns if "BBU" in c.upper()][0]]
    bbm = bb[[c for c in bb.columns if "BBM" in c.upper()][0]]
    df["BBU"] = bbu; df["BBM"] = bbm
    df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    actions = []; position = False; entry = 0
    for i in range(1, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date()); price = float(row["close"])
        if position:
            stop = entry - p["atr_mult"] * float(row["ATR"])
            if price < stop:
                actions.append({"date": dt, "type": "SELL", "price": price, "reason": "ATR止损"}); position = False; entry = 0
                continue
        if not position:
            if float(prev["close"]) <= float(prev["BBU"]) and float(row["close"]) > float(row["BBU"]):
                actions.append({"date": dt, "type": "BUY", "price": price, "reason": "布林上轨突破"}); position = True; entry = price
        else:
            if float(prev["close"]) >= float(prev["BBM"]) and float(row["close"]) < float(row["BBM"]):
                actions.append({"date": dt, "type": "SELL", "price": price, "reason": "跌破中轨"}); position = False; entry = 0
    return actions

def p_kdj_cci(df, p):
    import pandas_ta as ta
    df = df.copy()
    kdj = ta.kdj(df["high"], df["low"], df["close"], length=p["length"], signal=3)
    k_col = [c for c in kdj.columns if "K_" in c.upper()][0]
    d_col = [c for c in kdj.columns if "D_" in c.upper()][0]
    j_col = [c for c in kdj.columns if "J_" in c.upper()][0]
    df["K"] = kdj[k_col]; df["D"] = kdj[d_col]; df["J"] = kdj[j_col]
    df["RSI"] = ta.rsi(df["close"], length=14)
    actions = []; position = False
    for i in range(30, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date()); price = float(row["close"])
        j_cross_up = float(prev["J"]) <= float(prev["K"]) and float(row["J"]) > float(row["K"])
        j_cross_down = float(prev["J"]) >= float(prev["K"]) and float(row["J"]) < float(row["K"])
        if not position:
            if j_cross_up and float(row["RSI"]) < p["rsi_threshold"]:
                actions.append({"date": dt, "type": "BUY", "price": price, "reason": "KDJ金叉+RSI超卖"}); position = True
        else:
            if j_cross_down:
                actions.append({"date": dt, "type": "SELL", "price": price, "reason": "KDJ死叉"}); position = False
    return actions

def p_ema_obv(df, p):
    import pandas_ta as ta
    df = df.copy()
    df["EMA20"] = ta.ema(df["close"], length=p["length"])
    df["OBV"] = ta.obv(df["close"], df["volume"])
    df["OBV_EMA20"] = ta.ema(df["OBV"], length=p["length"])
    actions = []; position = False
    for i in range(1, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date()); price = float(row["close"])
        obv_cross_up = float(prev["OBV"]) <= float(prev["OBV_EMA20"]) and float(row["OBV"]) > float(row["OBV_EMA20"])
        obv_cross_down = float(prev["OBV"]) >= float(prev["OBV_EMA20"]) and float(row["OBV"]) < float(row["OBV_EMA20"])
        if not position:
            if price > float(row["EMA20"]) and obv_cross_up:
                actions.append({"date": dt, "type": "BUY", "price": price, "reason": "EMA上方+OBV上穿"}); position = True
        else:
            if price < float(row["EMA20"]) or obv_cross_down:
                actions.append({"date": dt, "type": "SELL", "price": price, "reason": "跌破EMA或OBV下穿"}); position = False
    return actions

def p_ema_cross(df, p):
    import pandas_ta as ta
    df = df.copy()
    df["F"] = ta.ema(df["close"], length=p["fast"])
    df["S"] = ta.ema(df["close"], length=p["slow"])
    actions = []; position = False
    for i in range(1, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date()); price = float(row["close"])
        cross_up = float(prev["F"]) <= float(prev["S"]) and float(row["F"]) > float(row["S"])
        cross_down = float(prev["F"]) >= float(prev["S"]) and float(row["F"]) < float(row["S"])
        if not position and cross_up:
            actions.append({"date": dt, "type": "BUY", "price": price, "reason": "快线上穿慢线"}); position = True
        elif position and cross_down:
            actions.append({"date": dt, "type": "SELL", "price": price, "reason": "快线下穿慢线"}); position = False
    return actions

def p_macd(df, p):
    import pandas_ta as ta
    df = df.copy()
    m = ta.macd(df["close"], fast=p["fast"], slow=p["slow"], signal=p["signal"])
    dif_col = [c for c in m.columns if c.upper().startswith("MACD_")][0]
    sig_col = [c for c in m.columns if "MACDS_" in c.upper()][0]
    df["M"] = m[dif_col]
    df["S"] = m[sig_col]
    actions = []; position = False
    for i in range(1, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date()); price = float(row["close"])
        cross_up = float(prev["M"]) <= float(prev["S"]) and float(row["M"]) > float(row["S"])
        cross_down = float(prev["M"]) >= float(prev["S"]) and float(row["M"]) < float(row["S"])
        if not position and cross_up:
            actions.append({"date": dt, "type": "BUY", "price": price, "reason": "MACD金叉"}); position = True
        elif position and cross_down:
            actions.append({"date": dt, "type": "SELL", "price": price, "reason": "MACD死叉"}); position = False
    return actions

PARAM_FUNCS = {
    "bollinger": p_bollinger_atr,
    "kdj_cci": p_kdj_cci,
    "ema_obv": p_ema_obv,
    "ema_cross": p_ema_cross,
    "macd": p_macd,
}

def iter_param_combos(grid):
    keys = list(grid.keys())
    import itertools
    for vals in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, vals))

def slice_window(df, years_back):
    if years_back == 0:
        return df.copy()
    end_date = df["date"].max()
    start_date = end_date - pd.Timedelta(days=int(years_back * 365))
    return df[df["date"] >= start_date].copy()


# ════════════════════════════════════════
# 评分复用 (与 validate_strategies.py 保持一致)
# ════════════════════════════════════════

def aggregate_score(windows_metrics) -> Optional[Dict]:
    """跨窗口聚合稳定性评分 (复用 validate_strategies.py 逻辑)"""
    returns = []; sharpes = []; dds = []; trades_list = []; cnt = 0
    for wname, m in windows_metrics.items():
        if "error" in m or "sharpe_ratio" not in m:
            continue
        returns.append(m.get("total_return_pct", 0))
        sharpes.append(m.get("sharpe_ratio", 0))
        dds.append(m.get("max_drawdown_pct", 0))
        trades_list.append(m.get("trades", m.get("total_trades", 0)))
        cnt += 1
    if cnt == 0:
        return None
    avg_trades = np.mean(trades_list)
    if avg_trades >= 10:
        sample_weight = 1.0
    elif avg_trades >= 6:
        sample_weight = 0.75
    elif avg_trades >= 3:
        sample_weight = 0.4
    else:
        sample_weight = 0.15
    avg_ret = np.mean(returns)
    avg_sharpe = np.mean(sharpes)
    avg_dd = np.mean(dds)
    dd_penalty = max(0, (abs(avg_dd) - 30)) * 0.5
    raw = avg_sharpe * 60 + avg_ret * 0.6 - dd_penalty
    score = raw * sample_weight
    return {
        "score": round(score, 2),
        "avg_return": round(avg_ret, 2),
        "avg_sharpe": round(avg_sharpe, 3),
        "avg_dd": round(avg_dd, 2),
        "avg_trades": round(avg_trades, 1),
        "sample_weight": sample_weight,
        "windows": cnt,
    }

def confidence_guard(score, sharpe, trades):
    """统一置信度护栏 (复用 validate_strategies.py)"""
    if score < 25:
        return False, f"得分{score}<25"
    thr = 0.4 if trades < 6 else 0.25
    if sharpe < thr:
        return False, f"夏普{sharpe}<{thr}(低笔数{int(trades)}笔需更严)"
    return True, ""


# ════════════════════════════════════════
# 单股票调优任务 (可并行)
# ════════════════════════════════════════

def _fetch_daily_worker(args):
    """子进程用的数据获取函数 (模块级，可被 pickle)"""
    sym, name, start_date = args
    sys.path.insert(0, WORKSPACE)
    from analysis.data_layer import get_router
    router = get_router()
    df = router.get_daily(sym, name, start_date=start_date)
    return (sym, name, df)

def _tune_worker(args):
    """子进程用的参数搜索函数 (模块级，可被 pickle)"""
    sym, name, df, grids, fast_mode, top_k = args
    if df is None or len(df) < 150:
        return sym, {"error": "数据不足", "name": name}
    return sym, tune_single_stock(sym, name, df, grids, fast_mode, top_k)

def tune_single_stock(symbol: str, name: str, df: pd.DataFrame, 
                      grids: Dict, fast_mode: bool, top_k: int) -> Dict:
    """单只股票的参数搜索，返回最优 Top-K 组合"""
    stock_detail = {}
    stock_best = []  # List of (score, sname, params, agg)
    
    for sname, func in PARAM_FUNCS.items():
        g = grids[sname]
        
        # ── 预筛选：先跑默认参数，若在快速模式窗口得分<15则跳过该策略 ──
        if fast_mode:
            default_params = DEFAULT_PARAMS[sname]
            wdf = slice_window(df, FAST_WINDOW[1])
            m = score_strategy_params(wdf, func, default_params)
            # 预筛选阈值降低到 -0.5，避免弱市全剪枝；若仍为 error 则跳过
            if "error" in m or m.get("sharpe_ratio", -999) < -0.5:
                stock_detail[sname] = {"pruned": True, "default_score": m.get("total_return_pct", -999), "default_sharpe": m.get("sharpe_ratio", -999)}
                continue
        
        # 网格搜索
        combo_best = None
        for params in iter_param_combos(g):
            wmetrics = {}
            windows_to_run = [FAST_WINDOW] if fast_mode else WINDOWS
            for wname, years_back in windows_to_run:
                wdf = slice_window(df, years_back)
                m = score_strategy_params(wdf, func, params)
                if "error" in m:
                    wmetrics[wname] = {"error": True}
                else:
                    wmetrics[wname] = m
            agg = aggregate_score(wmetrics)
            if agg is None:
                continue
            if combo_best is None or agg["score"] > combo_best[0]:
                combo_best = (agg["score"], params, agg)
        
        # 兜底：用默认参数
        if combo_best is None:
            default_params = DEFAULT_PARAMS[sname]
            wmetrics = {}
            windows_to_run = [FAST_WINDOW] if fast_mode else WINDOWS
            for wname, years_back in windows_to_run:
                wdf = slice_window(df, years_back)
                m = score_strategy_params(wdf, func, default_params)
                wmetrics[wname] = m
            def_agg = aggregate_score(wmetrics)
            if def_agg:
                combo_best = (def_agg["score"], default_params, def_agg)
                stock_detail[sname] = {"best_params": default_params, "best_score": def_agg["score"], "best_agg": def_agg, "fallback": True}
        else:
            stock_detail[sname] = {
                "best_params": combo_best[1],
                "best_score": combo_best[0],
                "best_agg": combo_best[2],
                "avg_return": combo_best[2]["avg_return"],
                "avg_sharpe": combo_best[2]["avg_sharpe"],
            }
        
        if combo_best:
            stock_best.append((combo_best[0], sname, combo_best[1], combo_best[2]))
    
    # 排序取 Top-K
    stock_best.sort(key=lambda x: x[0], reverse=True)
    top_results = stock_best[:top_k]
    
    # 构造返回
    if not top_results:
        return {"error": "无可评分策略", "name": name}
    
    best_score, best_sname, best_params, best_agg = top_results[0]
    return {
        "name": name,
        "strategy": best_sname,
        "params": best_params,
        "score": best_score,
        "avg_return": best_agg["avg_return"],
        "avg_sharpe": best_agg["avg_sharpe"],
        "avg_dd": best_agg["avg_dd"],
        "avg_trades": best_agg["avg_trades"],
        "details": stock_detail,
        "top_k": [
            {"strategy": s, "params": p, "score": sc, "avg_return": a["avg_return"], "avg_sharpe": a["avg_sharpe"]}
            for sc, s, p, a in top_results
        ],
    }

def score_strategy_params(wdf, func, params):
    try:
        actions = func(wdf, params)
        m = run_simulation(wdf, actions)
        m["signals"] = len([a for a in actions if a["type"] == "BUY"])
        m["trades"] = m.get("total_trades", 0)
        return m
    except Exception:
        return {"error": "策略报错"}


# ════════════════════════════════════════
# 主流程
# ════════════════════════════════════════

def load_incremental_candidates() -> List[str]:
    """增量模式：识别需重新调优的股票
    标准: 
    1. adaptive_strategy_map.json 中策略已变更
    2. param_evaluate.py 近期报告有"建议回滚/恶化"
    3. 最近 10 个交易日模拟盘累计收益 < -5%
    """
    candidates = set()
    
    # 1. 策略变更
    if os.path.exists(STRATEGY_MAP_FILE):
        try:
            with open(STRATEGY_MAP_FILE) as f:
                cur_map = json.load(f)
            # 读取上一版本 (param_tune_cache 里的 prev 备份)
            prev_map_file = os.path.join(CACHE_DIR, "adaptive_strategy_map_prev.json")
            if os.path.exists(prev_map_file):
                with open(prev_map_file) as f:
                    prev_map = json.load(f)
                for sym, new_s in cur_map.items():
                    old_s = prev_map.get(sym)
                    if old_s != new_s:
                        candidates.add(sym)
        except Exception:
            pass
    
    # 2. param_evaluate 恶化
    eval_dir = os.path.join(WORKSPACE, "analysis", "daily")
    for f in sorted(Path(eval_dir).glob("*_param_eval.md"), reverse=True)[:3]:
        try:
            content = f.read_text()
            if "建议回滚" in content or "恶化" in content:
                # 解析股票代码
                import re
                for match in re.finditer(r"(\d{6})", content):
                    candidates.add(match.group(1))
        except Exception:
            pass
    
    # 3. 模拟盘近期大幅回撤
    state_file = os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                state = json.load(f)
            for sym, pos in state.get("positions", {}).items():
                if pos.get("position") and pos.get("total_pl", 0) < -5000:  # 单票亏损 > 5000
                    candidates.add(sym)
        except Exception:
            pass
    
    # 只返回在 STOCKS 中的
    stock_codes = {s for s, _ in STOCKS}
    return [s for s in candidates if s in stock_codes]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="参数网格搜索")
    parser.add_argument("--stocks", help="指定股票代码，逗号分隔")
    parser.add_argument("--write", action="store_true", help="写回 adaptive_params.json + adaptive_strategy_map.json")
    parser.add_argument("--fast", action="store_true", help="快速模式 (单窗口预筛选 + 缩减网格)")
    parser.add_argument("--incremental", action="store_true", help="增量模式 (仅重新调优需更新的股票)")
    parser.add_argument("--top-k", type=int, default=1, help="每只股票输出 Top K 组合")
    parser.add_argument("--workers", type=int, default=4, help="并行工作进程数")
    args = parser.parse_args()
    
    fast_mode = args.fast
    incremental = args.incremental
    write = args.write
    top_k = args.top_k
    workers = min(args.workers, os.cpu_count() or 4)
    
    grids = FAST_GRIDS if fast_mode else PARAM_GRIDS
    mode_label = "快速" if fast_mode else "全量"
    if incremental:
        mode_label += "+增量"
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    end_date = datetime.datetime.now().strftime("%Y-%m-%d")
    
    # 确定股票范围
    if args.stocks:
        stock_codes = args.stocks.split(",")
        stocks = [s for s in STOCKS if s[0] in stock_codes]
    elif incremental:
        inc_candidates = load_incremental_candidates()
        if not inc_candidates:
            print("🔍 增量模式：无需更新的股票")
            return
        stocks = [s for s in STOCKS if s[0] in inc_candidates]
        print(f"🔍 增量模式：检测到 {len(stocks)} 只需更新股票: {inc_candidates}")
    else:
        stocks = STOCKS
    
    print(f"🔧 参数网格搜索开始 ({mode_label}模式) | {WIN_START} ~ {end_date} | {len(stocks)} 只")
    print(f"   并行度: {workers} | Top-K: {top_k}")
    print(f"   参数组合/策略: " + ", ".join(f"{STRATEGY_NAMES[k]}={sum(1 for _ in iter_param_combos(g))}" for k, g in grids.items()))
    print("=" * 70)
    
    # 预加载数据 (复用 DataRouter，自动缓存)
    router = get_router()
    router.preload_index_benchmarks(WIN_START, end_date)
    
    # 并行获取历史数据
    print(f"\n📥 预加载历史数据 ({len(stocks)} 只)...")
    data_cache = {}
    
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch_daily_worker, (sym, name, WIN_START)): sym for sym, name in stocks}
        for fut in as_completed(futures):
            sym, name, df = fut.result()
            if df is not None and len(df) >= 150:
                data_cache[sym] = df
                print(f"  ✅ {name}({sym}): {len(df)} 条日线")
            else:
                print(f"  ❌ {name}({sym}): 数据不足")
    
    # 并行参数搜索
    print(f"\n🔍 并行参数搜索 (workers={workers})...")
    results = {}
    
    tune_items = [(sym, name, data_cache[sym], grids, fast_mode, top_k) for sym, name in stocks if sym in data_cache]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_tune_worker, item): item[0] for item in tune_items}
        for fut in as_completed(futures):
            sym, result = fut.result()
            results[sym] = result
            if "error" in result:
                print(f"  ❌ {result['name']}({sym}): {result['error']}")
            else:
                print(f"  ✅ {result['name']}({sym}): 最优 {STRATEGY_NAMES.get(result['strategy'], result['strategy'])} {result['params']} 得分{result['score']} 夏普{result['avg_sharpe']} 收益{result['avg_return']}%")
    
    # 保存完整结果
    os.makedirs(os.path.dirname(PARAMS_FILE), exist_ok=True)
    output = {"date": today, "mode": mode_label, "fast": fast_mode, "incremental": incremental, "stocks": results}
    with open(PARAMS_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 参数搜索结果保存: {PARAMS_FILE}")
    
    # 写回映射文件
    if write:
        # 备份当前映射
        if os.path.exists(STRATEGY_MAP_FILE):
            import shutil
            shutil.copy2(STRATEGY_MAP_FILE, os.path.join(CACHE_DIR, "adaptive_strategy_map_prev.json"))
        
        # 策略映射
        if os.path.exists(STRATEGY_MAP_FILE):
            with open(STRATEGY_MAP_FILE) as f:
                cur_map = json.load(f)
        else:
            cur_map = {}
        
        changes_s = 0
        changes_p = 0
        for symbol, info in results.items():
            if "error" in info or "strategy" not in info:
                continue
            new_s = info["strategy"]
            old_s = cur_map.get(symbol)
            # 置信度护栏
            ok, reason = confidence_guard(info["score"], info["avg_sharpe"], info["avg_trades"])
            if old_s != new_s and ok:
                print(f"  📝 策略变更 {old_s} -> {new_s} {info['name']}({symbol}) score={info['score']} ({reason})")
                cur_map[symbol] = new_s
                changes_s += 1
            elif old_s != new_s and not ok:
                print(f"  ⏸️ 策略 {new_s} 未过护栏 ({reason})，保留 {old_s}")
        
        with open(STRATEGY_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(cur_map, f, ensure_ascii=False, indent=2)
        print(f"✅ 策略映射写回 {STRATEGY_MAP_FILE} (变更 {changes_s} 处)")
    
    # stdout JSON 供 agent 解析
    print("\n=====PARAM_TUNE_RESULT=====")
    print(json.dumps({
        "date": today, "mode": mode_label, "fast": fast_mode, "incremental": incremental,
        "stock_count": len(results),
        "best_strategies": {s: {"strategy": i.get("strategy"), "params": i.get("params"),
                                "score": i.get("score"), "avg_sharpe": i.get("avg_sharpe"),
                                "avg_return": i.get("avg_return")}
                            for s, i in results.items() if "error" not in i},
    }, ensure_ascii=False, indent=2))
    print("=====PARAM_TUNE_END=====")


if __name__ == "__main__":
    main()