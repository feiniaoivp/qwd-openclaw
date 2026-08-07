#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
参数网格搜索引擎 (2026-08-04)
=============================
超越"策略级选择"——升级到"参数级调优"：
对每只股票，在 5 个策略各自的参数网格空间里搜索，
用跨周期多窗口稳定性评分选出最优的 (策略, 参数组合)，
写回 data/adaptive_params.json，供 adaptive_dual.py 每日用最优参数跑信号。

评分公式 (沿用 validate_strategies.py 的稳定性思路):
  raw = 平均夏普*60 + 平均收益*0.6 - 回撤惩罚(超-30%部分)
  score = raw * 样本量分档权重
  样本量权重: >=10笔 1.0 / 6-9笔 0.75 / 3-5笔 0.4 / <3笔 0.15

护栏 (写回时):
  - 得分 >= 25 且 夏普 >= 阈值(低笔数需更严) 才覆盖参数
  - 低样本(<6笔) 要求 夏普>=0.4; >=6笔 要求 夏普>=0.25
  - 防止参数过拟合到最近窗口

运行: python3 analysis/param_tune.py [--stocks 代码,代码] [--write] [--fast]
  --write  写回 data/adaptive_params.json (否则只预览)
  --fast   快速模式: 参数网格缩减(相邻窗口只取3个代表参数), 通常5分钟内

多窗口:
  W1 全量6.5年 / W2 近3年 / W3 近1.5年 / W4 近1年
"""

import os
import sys
import json
import datetime
import time
import numpy as np
import pandas as pd
import baostock as bs

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

from backtest_strategies import (
    INITIAL_CAPITAL, COMMISSION, SLIPPAGE,
    run_simulation, STOCKS,
)

PARAMS_FILE = os.path.join(WORKSPACE, "data", "adaptive_params.json")
STRATEGY_MAP_FILE = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")

# ════════════════════════════════════════
# 参数网格定义
# ════════════════════════════════════════
# 每个策略: 参数名 -> 候选值列表。组合数 = 各参数候选数之积
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

# 快速模式: 每个策略只取中间值+两端(3个代表参数), 大幅减少组合
FAST_GRIDS = {
    "bollinger": {
        "length": [20],
        "std": [2.0, 2.5],
        "atr_mult": [1.5, 2.0],
    },
    "kdj_cci": {"length": [9], "rsi_threshold": [40, 50]},
    "ema_obv": {"length": [20]},
    "ema_cross": {"fast": [10, 12], "slow": [26]},
    "macd": {"fast": [12], "slow": [26], "signal": [9, 12]},
}

STRATEGY_NAMES = {
    "bollinger": "布林带+ATR",
    "kdj_cci": "KDJ+CCI",
    "ema_obv": "EMA+OBV",
    "ema_cross": "EMA12/26金叉",
    "macd": "纯MACD",
}

# 当前默认参数 (作为基准, 确保新参数不会比默认差太多才采纳)
DEFAULT_PARAMS = {
    "bollinger": {"length": 20, "std": 2.0, "atr_mult": 1.5},
    "kdj_cci": {"length": 9, "rsi_threshold": 40},
    "ema_obv": {"length": 20},
    "ema_cross": {"fast": 12, "slow": 26},
    "macd": {"fast": 12, "slow": 26, "signal": 9},
}


# ════════════════════════════════════════
# 参数化策略函数 (接受 params dict)
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
    # pandas_ta 列名: MACD_{f}_{s}_{sig}=DIF线, MACDs_{...}=信号线, MACDh_{...}=柱
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
    """生成网格所有参数组合"""
    keys = list(grid.keys())
    import itertools
    for vals in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, vals))


# ════════════════════════════════════════
# 数据获取 (长历史 + 多窗口)
# ════════════════════════════════════════

WIN_START = "2020-01-01"
WINDOWS = [("W1_全量", 0), ("W2_近3年", 3), ("W3_近1.5年", 1.5), ("W4_近1年", 1)]


def baostock_code(symbol):
    return ("sh." if symbol.startswith("6") or symbol.startswith("9") else "sz.") + symbol


def fetch_long(symbol, start, end, max_retry=3, login=None):
    """拉长历史日线, 复用同一 baostock 会话"""
    code = baostock_code(symbol)
    for attempt in range(max_retry):
        try:
            rs = bs.query_history_k_data_plus(
                code, "date,open,high,low,close,volume,amount",
                start_date=start, end_date=end,
                frequency="d", adjustflag="2")
            rows = []
            while (rs.error_code == "0") and rs.next():
                rows.append(rs.get_row_data())
            if len(rows) >= 100:
                df = pd.DataFrame(rows, columns=[
                    "date", "open", "high", "low", "close", "volume", "amount"])
                for c in ["open", "high", "low", "close", "volume", "amount"]:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                df["date"] = pd.to_datetime(df["date"])
                df = df.dropna(subset=["close"]).reset_index(drop=True)
                return df
        except Exception:
            time.sleep(2)
    return None


def slice_window(df, years_back):
    if years_back == 0:
        return df.copy()
    end_date = df["date"].max()
    start_date = end_date - pd.Timedelta(days=int(years_back * 365))
    return df[df["date"] >= start_date].copy()


def score_strategy_params(wdf, func, params):
    """对单窗口单参数组合跑回测, 返回指标"""
    try:
        actions = func(wdf, params)
        m = run_simulation(wdf, actions)
        m["signals"] = len([a for a in actions if a["type"] == "BUY"])
        m["trades"] = m.get("total_trades", 0)
        return m
    except Exception:
        return {"error": "策略报错"}


def aggregate_score(windows_metrics):
    """跨4窗口聚合稳定性评分"""
    returns = []; sharpes = []; dds = []; trades_list = []; cnt = 0
    for wname, m in windows_metrics.items():
        if "error" in m or "sharpe_ratio" not in m:
            continue
        returns.append(m.get("total_return_pct", 0))
        sharpes.append(m.get("sharpe_ratio", 0))
        dds.append(m.get("max_drawdown_pct", 0))
        trades_list.append(m.get("trades", 0))
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


# ════════════════════════════════════════
# 主流程
# ════════════════════════════════════════

def main():
    write = "--write" in sys.argv
    fast = "--fast" in sys.argv
    stock_arg = None
    if "--stocks" in sys.argv:
        i = sys.argv.index("--stocks")
        stock_arg = sys.argv[i+1].split(",")

    grids = FAST_GRIDS if fast else PARAM_GRIDS
    mode_label = "快速" if fast else "全量"
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    end = datetime.datetime.now().strftime("%Y-%m-%d")

    stocks = [s for s in STOCKS if stock_arg is None or s[0] in stock_arg]

    print(f"🔧 参数网格搜索开始 ({mode_label}模式) | {WIN_START} ~ {end} | {len(stocks)} 只")
    print(f"   参数组合数: " + ", ".join(f"{STRATEGY_NAMES[k]}={sum(1 for _ in iter_param_combos(g))}" for k, g in grids.items()))
    print("=" * 70)

    login = bs.login()
    if login.error_code != "0":
        print("❌ baostock 登录失败:", login.error_msg); sys.exit(1)

    results = {}
    t0 = time.time()
    for sidx, (symbol, name) in enumerate(stocks):
        df = fetch_long(symbol, WIN_START, end)
        if df is None or len(df) < 150:
            print(f"\n[{sidx+1}/{len(stocks)}] {name}: ❌ 数据不足")
            results[symbol] = {"error": "数据不足"}
            continue

        stock_best = None  # (score, strategy, params, agg_metrics)
        stock_detail = {}

        for sname, func in PARAM_FUNCS.items():
            g = grids[sname]
            n_combos = sum(1 for _ in iter_param_combos(g))
            combo_best = None  # (score, params, agg)
            for params in iter_param_combos(g):
                wmetrics = {}
                for wname, years_back in WINDOWS:
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

            # 还要算默认参数作为基准
            default_params = DEFAULT_PARAMS[sname]
            wmetrics = {}
            for wname, years_back in WINDOWS:
                wdf = slice_window(df, years_back)
                m = score_strategy_params(wdf, func, default_params)
                wmetrics[wname] = m
            def_agg = aggregate_score(wmetrics)
            if combo_best is not None:
                stock_detail[sname] = {
                    "best_strategy_key": sname,
                    "best_params": combo_best[1],
                    "best_score": combo_best[0],
                    "best_agg": combo_best[2],
                    "avg_return": combo_best[2]["avg_return"],
                    "avg_sharpe": combo_best[2]["avg_sharpe"],
                    "default_score": def_agg["score"] if def_agg else None,
                    "default_agg": def_agg,
                }
                if stock_best is None or combo_best[0] > stock_best[0]:
                    stock_best = (combo_best[0], sname, combo_best[1], combo_best[2])

        if stock_best is None:
            results[symbol] = {"error": "无可评分策略", "name": name}
            continue

        score, sname, params, agg = stock_best
        # 与当前 adaptive_strategy_map 对比, 报告策略变化
        results[symbol] = {
            "name": name,
            "strategy": sname,
            "params": params,
            "score": score,
            "avg_return": agg["avg_return"],
            "avg_sharpe": agg["avg_sharpe"],
            "avg_dd": agg["avg_dd"],
            "avg_trades": agg["avg_trades"],
            "details": stock_detail,
            "changed_improvement": {},
        }
        # 对比该股当前策略 (从 strategy_map 读)
        map_v = None
        if os.path.exists(STRATEGY_MAP_FILE):
            try:
                with open(STRATEGY_MAP_FILE) as f:
                    map_v = json.load(f).get(symbol)
            except Exception:
                map_v = None
        print(f"\n[{sidx+1}/{len(stocks)}] {name}({symbol}) "
              f"{time.time()-t0:.0f}s | 最优 {STRATEGY_NAMES[sname]} {params} "
              f"得分{score} 收益{agg['avg_return']}% 夏普{agg['avg_sharpe']} 回撤{agg['avg_dd']}% 交易{agg['avg_trades']}笔")
        if map_v:
            arrow = "✅" if map_v == sname else "🔄"
            print(f"      当前策略: {map_v} {arrow}")

    bs.logout()

    # ── 保存 & 写回 ──
    os.makedirs(os.path.dirname(PARAMS_FILE), exist_ok=True)
    with open(PARAMS_FILE, "w", encoding="utf-8") as f:
        json.dump({"date": today, "mode": mode_label, "stocks": results},
                  f, ensure_ascii=False, indent=2)
    print(f"\n✅ 参数搜索结果保存: {PARAMS_FILE}")

    # 写回 adaptive_strategy_map.json (策略) + adaptive_params.json (参数)
    if write:
        changes_s = 0
        changes_p = 0
        if os.path.exists(STRATEGY_MAP_FILE):
            try:
                with open(STRATEGY_MAP_FILE, "r", encoding="utf-8") as f:
                    cur_map = json.load(f)
            except Exception:
                cur_map = {}
            for symbol, info in results.items():
                if "error" in info or "strategy" not in info:
                    continue
                new_s = info["strategy"]
                old_s = cur_map.get(symbol)
                # 策略变更需过置信度护栏 (得分>=25 且 夏普>=阈值)
                sharpe = info.get("avg_sharpe", -999)
                trades = info.get("avg_trades", 999)
                thr = 0.4 if trades < 6 else 0.25
                if old_s != new_s and info["score"] >= 25 and sharpe >= thr:
                    print(f"  📝 策略变更 {old_s} -> {new_s} {info['name']}({symbol}) score={info['score']}")
                    cur_map[symbol] = new_s
                    changes_s += 1
            with open(STRATEGY_MAP_FILE, "w", encoding="utf-8") as f:
                json.dump(cur_map, f, ensure_ascii=False, indent=2)
            print(f"✅ 策略映射写回 {STRATEGY_MAP_FILE} (变更 {changes_s} 处)")
        else:
            with open(STRATEGY_MAP_FILE, "w", encoding="utf-8") as f:
                json.dump({s: i.get("strategy", "ema_cross") for s, i in results.items() if "error" not in i},
                          f, ensure_ascii=False, indent=2)

        # adaptive_params.json 已含全部参数, 无需额外写回 (上面已保存全量)
    else:
        print("\n(未写回 adaptive_strategy_map.json，加 --write 才会覆盖策略映射)")

    # stdout JSON 供 agent 解析
    print("\n=====PARAM_TUNE_RESULT=====")
    print(json.dumps({
        "date": today, "mode": mode_label, "stock_count": len(results),
        "best_strategies": {s: {"strategy": i.get("strategy"), "params": i.get("params"),
                                "score": i.get("score"), "avg_sharpe": i.get("avg_sharpe"),
                                "avg_return": i.get("avg_return")}
                            for s, i in results.items() if "error" not in i},
    }, ensure_ascii=False, indent=2))
    print("=====PARAM_TUNE_END=====")


if __name__ == "__main__":
    main()
