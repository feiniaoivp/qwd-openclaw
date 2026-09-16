#!/usr/bin/env python3
"""
低置信度股票深度参数网格搜索
=================================
针对 validate_strategies.py 识别出的低置信度股票，
做更细粒度的参数网格搜索，寻找更稳健的参数组合。

低置信度名单 (2026-09-16 验证):
  券商周期: 中信建投(601066)、中金公司(601995)
  周期/工业: 中信特钢(000708)、中联重科(000157)、恒立液压(601100)
  科技成长: 恒生电子(600570)、科华数据(002335)、福耀玻璃(600660)
  电网装备: 特变电工(600089)、国电南瑞(600406)、许继电气(000400)、思源电气(002028)、平高电气(600312)
  其他: 汇川技术(300124)、安达维尔(300719)
"""

import os, sys, json, warnings, traceback
from datetime import datetime, timedelta
import pandas as pd
import pandas_ta as ta
import numpy as np
from itertools import product

WORKSPACE = "/Users/duguke/.openclaw/workspace"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

from analysis.data_layer.router import get_router
from analysis.backtest_guard import assert_beats_random

warnings.filterwarnings("ignore")

# ── 低置信度股票池 ──
LOW_CONFIDENCE = [
    ("601066", "中信建投", "券商周期"),
    ("601995", "中金公司", "券商周期"),
    ("000708", "中信特钢", "钢铁周期"),
    ("000157", "中联重科", "工程机械周期"),
    ("601100", "恒立液压", "高端液压周期"),
    ("600570", "恒生电子", "金融IT"),
    ("002335", "科华数据", "数据中心"),
    ("600660", "福耀玻璃", "汽车玻璃"),
    ("600089", "特变电工", "电网装备"),
    ("600406", "国电南瑞", "电网装备"),
    ("000400", "许继电气", "电网装备"),
    ("002028", "思源电气", "电网装备"),
    ("600312", "平高电气", "电网装备"),
    ("300124", "汇川技术", "工业自动化"),
    ("300719", "安达维尔", "连接器"),
]

START_DATE = "2020-01-01"
END_DATE = datetime.now().strftime("%Y-%m-%d")
INITIAL_CAPITAL = 100_000
COMMISSION = 0.0003
SLIPPAGE = 0.001

# ── 策略参数网格 ──
GRIDS = {
    "ema_cross": {
        "fast": [5, 8, 10, 12, 15],
        "slow": [21, 26, 30, 35, 40],
        "filter": lambda f, s: f < s,
    },
    "macd": {
        "fast": [8, 10, 12, 15],
        "slow": [21, 26, 30, 35],
        "signal": [7, 9, 11],
        "filter": lambda f, s, sig: f < s and sig < f,
    },
    "bollinger": {
        "length": [15, 20, 25, 30],
        "std": [1.5, 2.0, 2.5],
        "exit_on_mid": [True, False],
    },
    "ema_obv": {
        "ema_len": [15, 20, 25, 30],
        "obv_ma": [10, 20, 30],
    },
    "kdj_cci": {
        "kdj_len": [9, 12, 14],
        "kdj_signal": [3, 4, 5],
        "rsi_len": [10, 14, 20],
        "rsi_buy": [30, 35, 40],
    },
    "bull_trend": {
        "ema_short": [8, 10, 12],
        "ema_mid": [20, 25, 30],
        "ema_long": [50, 60, 80],
        "obv_ma": [10, 20, 30],
    },
}


def fetch_data(symbol, name, start, end):
    router = get_router()
    try:
        df = router.get_daily(symbol, name, start_date=start.replace("-", ""))
        if df is not None and len(df) >= 200:
            df = df[(df["date"] >= pd.to_datetime(start)) &
                    (df["date"] <= pd.to_datetime(end))].copy()
            return df
    except Exception as e:
        print(f"  {symbol} 获取失败: {e}")
    return None


def run_sim(df, actions):
    """统一模拟引擎"""
    cash = INITIAL_CAPITAL
    shares = 0
    trades = []
    equity_curve = []
    
    for i, row in df.iterrows():
        dt = row["date"]
        price = float(row["close"])
        
        # 处理当日信号
        day_actions = [a for a in actions if a["date"] == str(dt.date())]
        for act in day_actions:
            if act["type"] == "BUY" and shares == 0:
                buy_price = act["price"] * (1 + SLIPPAGE)
                max_shares = int(cash * 0.95 / buy_price / 100) * 100
                if max_shares >= 100:
                    shares = max_shares
                    cost = shares * buy_price * (1 + COMMISSION)
                    cash -= cost
                    trades.append({"date": dt, "type": "BUY", "price": buy_price, 
                                  "shares": shares, "cash": cash})
            elif act["type"] == "SELL" and shares > 0:
                sell_price = act["price"] * (1 - SLIPPAGE)
                revenue = shares * sell_price * (1 - COMMISSION)
                cash += revenue
                trades.append({"date": dt, "type": "SELL", "price": sell_price,
                              "shares": shares, "cash": cash})
                shares = 0
        
        equity = cash + shares * price
        equity_curve.append({"date": dt, "equity": equity})
    
    # 最终平仓
    if shares > 0:
        last_price = float(df.iloc[-1]["close"]) * (1 - SLIPPAGE)
        cash += shares * last_price * (1 - COMMISSION)
        trades.append({"date": df.iloc[-1]["date"], "type": "SELL", "price": last_price,
                      "shares": shares, "cash": cash})
        shares = 0
    
    total_return = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    
    # 计算夏普
    eq_df = pd.DataFrame(equity_curve)
    eq_df["ret"] = eq_df["equity"].pct_change()
    sharpe = 0
    if eq_df["ret"].std() > 0:
        sharpe = eq_df["ret"].mean() / eq_df["ret"].std() * np.sqrt(252)
    
    # 最大回撤
    peak = eq_df["equity"].expanding().max()
    dd = (eq_df["equity"] - peak) / peak * 100
    max_dd = dd.min()
    
    # 胜率
    buy_trades = [t for t in trades if t["type"] == "BUY"]
    sell_trades = [t for t in trades if t["type"] == "SELL"]
    wins = 0
    for i, sell in enumerate(sell_trades):
        if i < len(buy_trades):
            buy = buy_trades[i]
            if sell["price"] > buy["price"]:
                wins += 1
    win_rate = wins / len(sell_trades) * 100 if sell_trades else 0
    
    return {
        "total_return_pct": round(total_return, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd, 2),
        "total_trades": len(sell_trades),
        "win_rate_pct": round(win_rate, 1),
        "final_equity": round(cash, 2),
    }


# ── 策略信号生成器（参数化）──
def gen_ema_cross(df, fast, slow):
    df = df.copy()
    df["EMA_f"] = ta.ema(df["close"], length=fast)
    df["EMA_s"] = ta.ema(df["close"], length=slow)
    actions = []
    pos = False
    for i in range(1, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])
        if not pos and float(prev["EMA_f"]) <= float(prev["EMA_s"]) and float(row["EMA_f"]) > float(row["EMA_s"]):
            actions.append({"date": dt, "type": "BUY", "price": price, "reason": f"EMA{fast}/{slow}金叉"})
            pos = True
        elif pos and float(prev["EMA_f"]) >= float(prev["EMA_s"]) and float(row["EMA_f"]) < float(row["EMA_s"]):
            actions.append({"date": dt, "type": "SELL", "price": price, "reason": f"EMA{fast}/{slow}死叉"})
            pos = False
    return actions


def gen_macd(df, fast, slow, signal):
    df = df.copy()
    macd = ta.macd(df["close"], fast=fast, slow=slow, signal=signal)
    df["MACD"] = macd[f"MACD_{fast}_{slow}_{signal}"]
    df["SIGNAL"] = macd[f"MACDs_{fast}_{slow}_{signal}"]
    actions = []
    pos = False
    for i in range(1, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])
        if not pos and float(prev["MACD"]) <= float(prev["SIGNAL"]) and float(row["MACD"]) > float(row["SIGNAL"]):
            actions.append({"date": dt, "type": "BUY", "price": price, "reason": f"MACD({fast},{slow},{signal})金叉"})
            pos = True
        elif pos and float(prev["MACD"]) >= float(prev["SIGNAL"]) and float(row["MACD"]) < float(row["SIGNAL"]):
            actions.append({"date": dt, "type": "SELL", "price": price, "reason": f"MACD({fast},{slow},{signal})死叉"})
            pos = False
    return actions


def gen_bollinger(df, length, std, exit_on_mid):
    df = df.copy()
    bb = ta.bbands(df["close"], length=length, std=std)
    bbu = [c for c in bb.columns if "BBU" in c.upper()][0]
    bbm = [c for c in bb.columns if "BBM" in c.upper()][0]
    bbl = [c for c in bb.columns if "BBL" in c.upper()][0]
    df["BBU"] = bb[bbu]
    df["BBM"] = bb[bbm]
    df["BBL"] = bb[bbl]
    actions = []
    pos = False
    for i in range(1, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])
        if not pos and float(prev["close"]) <= float(prev["BBU"]) and float(row["close"]) > float(row["BBU"]):
            actions.append({"date": dt, "type": "BUY", "price": price, "reason": f"BB({length},{std})上轨突破"})
            pos = True
        elif pos:
            exit_cond = (float(prev["close"]) >= float(prev["BBM"]) and float(row["close"]) < float(row["BBM"])) if exit_on_mid \
                        else (float(prev["close"]) >= float(prev["BBL"]) and float(row["close"]) < float(row["BBL"]))
            if exit_cond:
                actions.append({"date": dt, "type": "SELL", "price": price, "reason": f"BB({length},{std}){'中轨' if exit_on_mid else '下轨'}跌破"})
                pos = False
    return actions


def gen_ema_obv(df, ema_len, obv_ma):
    df = df.copy()
    df["EMA"] = ta.ema(df["close"], length=ema_len)
    df["OBV"] = ta.obv(df["close"], df["volume"])
    df["OBV_MA"] = ta.sma(df["OBV"], length=obv_ma)
    actions = []
    pos = False
    for i in range(1, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])
        if not pos and float(prev["close"]) <= float(prev["EMA"]) and float(row["close"]) > float(row["EMA"]) \
           and float(row["OBV"]) > float(row["OBV_MA"]):
            actions.append({"date": dt, "type": "BUY", "price": price, "reason": f"EMA{ema_len}+OBV上穿"})
            pos = True
        elif pos and (float(row["close"]) < float(row["EMA"]) or float(row["OBV"]) < float(row["OBV_MA"])):
            actions.append({"date": dt, "type": "SELL", "price": price, "reason": f"跌破EMA/OBV"})
            pos = False
    return actions


def gen_kdj_cci(df, kdj_len, kdj_signal, rsi_len, rsi_buy):
    df = df.copy()
    kdj = ta.kdj(df["high"], df["low"], df["close"], length=kdj_len, signal=kdj_signal)
    df["K"] = kdj[f"K_{kdj_len}_{kdj_signal}"]
    df["D"] = kdj[f"D_{kdj_len}_{kdj_signal}"]
    df["J"] = kdj[f"J_{kdj_len}_{kdj_signal}"]
    df["RSI"] = ta.rsi(df["close"], length=rsi_len)
    actions = []
    pos = False
    for i in range(30, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])
        j, k = float(row["J"]), float(row["K"])
        pj, pk = float(prev["J"]), float(prev["K"])
        rsi = float(row["RSI"])
        j_cross_up = (pj <= pk) and (j > k)
        j_cross_down = (pj >= pk) and (j < k)
        if not pos and j_cross_up and rsi < rsi_buy:
            actions.append({"date": dt, "type": "BUY", "price": price, "reason": f"KDJ金叉+RSI<{rsi_buy}"})
            pos = True
        elif pos and j_cross_down:
            actions.append({"date": dt, "type": "SELL", "price": price, "reason": f"KDJ死叉"})
            pos = False
    return actions


def gen_bull_trend(df, ema_s, ema_m, ema_l, obv_ma):
    df = df.copy()
    df["EMA_s"] = ta.ema(df["close"], length=ema_s)
    df["EMA_m"] = ta.ema(df["close"], length=ema_m)
    df["EMA_l"] = ta.ema(df["close"], length=ema_l)
    df["OBV"] = ta.obv(df["close"], df["volume"])
    df["OBV_MA"] = ta.sma(df["OBV"], length=obv_ma)
    actions = []
    pos = False
    for i in range(1, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])
        trend_up = float(row["EMA_s"]) > float(row["EMA_m"]) > float(row["EMA_l"])
        price_above = float(row["close"]) > float(row["EMA_l"])
        obv_up = float(row["OBV"]) > float(row["OBV_MA"])
        if not pos and trend_up and price_above and obv_up:
            actions.append({"date": dt, "type": "BUY", "price": price, "reason": f"牛市趋势确立"})
            pos = True
        elif pos and (not trend_up or not price_above):
            actions.append({"date": dt, "type": "SELL", "price": price, "reason": f"趋势破坏"})
            pos = False
    return actions


STRATEGY_GENS = {
    "ema_cross": gen_ema_cross,
    "macd": gen_macd,
    "bollinger": gen_bollinger,
    "ema_obv": gen_ema_obv,
    "kdj_cci": gen_kdj_cci,
    "bull_trend": gen_bull_trend,
}


def grid_search_for_stock(symbol, name, category, df):
    """对单只股票做全策略网格搜索"""
    print(f"\n{'='*60}")
    print(f"🔍 {name}({symbol}) [{category}] - 网格搜索")
    print(f"{'='*60}")
    
    results = {}
    
    for strat_name, grid in GRIDS.items():
        print(f"\n  策略: {strat_name}")
        gen_func = STRATEGY_GENS[strat_name]
        
        # 构建参数组合
        param_names = list(grid.keys())
        param_values = [grid[k] for k in param_names if k != "filter"]
        filter_fn = grid.get("filter", lambda *args: True)
        
        best = {"score": -999, "params": None, "metrics": None}
        tested = 0
        
        for combo in product(*param_values):
            params = dict(zip(param_names, combo))
            if not filter_fn(*combo):
                continue
            
            try:
                actions = gen_func(df, **params)
                m = run_sim(df, actions)
                
                # 综合评分：夏普*50 + 收益*0.5 - 回撤惩罚*0.5
                dd_penalty = max(0, abs(m["max_drawdown_pct"]) - 25) * 0.5
                score = m["sharpe_ratio"] * 50 + m["total_return_pct"] * 0.5 - dd_penalty
                
                # 样本量修正
                if m["total_trades"] < 5:
                    score *= 0.3
                elif m["total_trades"] < 10:
                    score *= 0.6
                elif m["total_trades"] < 20:
                    score *= 0.85
                
                tested += 1
                if score > best["score"]:
                    best = {"score": round(score, 2), "params": params, "metrics": m}
            except Exception as e:
                pass
        
        if best["params"]:
            m = best["metrics"]
            # ── 随机基准对照 (2026-09-16 接线) ──
            # 此前 import 了 backtest_guard 却从未调用，导致「全样本挑最优」的高分参数
            # 无任何显著性检验。此处对胜率 vs 随机 50% 做 Wilson CI + p 值，
            # 并写入结果，供下游 (oos_validate_grid.py / 人工) 判定是否可信。
            guard = None
            n_tr = m["total_trades"]
            if n_tr > 0:
                try:
                    hits = int(round(m["win_rate_pct"] / 100.0 * n_tr))
                    guard = compare_to_random(hits, n_tr, random_hit_prob=0.5)
                except Exception:
                    guard = None
            best["guard"] = guard
            print(f"    ✅ 最优: {best['params']} | "
                  f"收益{m['total_return_pct']}% 夏普{m['sharpe_ratio']} "
                  f"回撤{m['max_drawdown_pct']}% 胜率{m['win_rate_pct']}% "
                  f"交易{m['total_trades']}笔 | 得分{best['score']}")
            results[strat_name] = best
        else:
            print(f"    ❌ 无有效参数组合")
    
    return results


def main():
    print("=" * 70)
    print("低置信度股票深度参数网格搜索")
    print(f"数据范围: {START_DATE} ~ {END_DATE}")
    print(f"目标股票: {len(LOW_CONFIDENCE)} 只")
    print("=" * 70)
    
    all_results = {}
    
    for symbol, name, category in LOW_CONFIDENCE:
        df = fetch_data(symbol, name, START_DATE, END_DATE)
        if df is None or len(df) < 200:
            print(f"❌ {name}({symbol}) 数据不足，跳过")
            all_results[symbol] = {"error": "数据不足"}
            continue
        
        print(f"\n✅ {name}({symbol}) 获取 {len(df)} 条日线")
        results = grid_search_for_stock(symbol, name, category, df)
        all_results[symbol] = {"name": name, "category": category, "results": results}
    
    # 汇总报告
    print("\n" + "=" * 70)
    print("📊 汇总：每只股票的最优策略+参数")
    print("=" * 70)
    
    summary = []
    for symbol, data in all_results.items():
        if "error" in data:
            continue
        name = data["name"]
        cat = data["category"]
        best_overall = {"score": -999, "strategy": None, "params": None, "metrics": None, "guard": None}
        for strat, res in data["results"].items():
            if res["score"] > best_overall["score"]:
                best_overall = {"score": res["score"], "strategy": strat,
                               "params": res["params"], "metrics": res["metrics"],
                               "guard": res.get("guard")}
        if best_overall["strategy"]:
            m = best_overall["metrics"]
            summary.append({
                "symbol": symbol, "name": name, "category": cat,
                "strategy": best_overall["strategy"],
                "params": best_overall["params"],
                "return": m["total_return_pct"], "sharpe": m["sharpe_ratio"],
                "dd": m["max_drawdown_pct"], "wr": m["win_rate_pct"],
                "trades": m["total_trades"], "score": best_overall["score"],
                "guard": best_overall.get("guard"),
            })
            print(f"  {name}({symbol}) [{cat}]: {best_overall['strategy']} {best_overall['params']} | "
                  f"收益{m['total_return_pct']}% 夏普{m['sharpe_ratio']} 回撤{m['max_drawdown_pct']}% "
                  f"胜率{m['win_rate_pct']}% 交易{m['total_trades']}笔 | 得分{best_overall['score']}")
    
    # 按得分排序
    summary.sort(key=lambda x: x["score"], reverse=True)
    
    # 保存
    out_file = os.path.join(WORKSPACE, "analysis", f"grid_search_{datetime.now().strftime('%Y%m%d')}.json")
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 详细结果已保存: {out_file}")
    
    # 生成建议更新的 adaptive_params.json 片段
    print("\n" + "=" * 70)
    print("💡 候选参数（得分>30 且 夏普>0.3 且 交易≥8笔）")
    print("⚠️ 这些是【全样本 IS 最优】，未经样本外验证，禁止直接写入 adaptive_params.json。")
    print("   下一步：必须跑 analysis/oos_validate_grid.py 做样本外 + 随机基准体检。")
    print("=" * 70)
    for s in summary:
        if s["score"] > 30 and s["sharpe"] > 0.3 and s["trades"] >= 8:
            g = s.get("guard") or {}
            gp = f" 随机基准p={g['p_value']:.3f} {'通过' if g.get('passed') else '未过'}" if g else ""
            print(f'  "{s["symbol"]}": {{"strategy": "{s["strategy"]}", "params": {json.dumps(s["params"])}}},'
                  f'   # IS得分{s["score"]}{gp}')


if __name__ == "__main__":
    main()