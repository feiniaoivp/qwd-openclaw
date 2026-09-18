#!/usr/bin/env python3
"""
ATR 动态仓位/止损 专项回测
============================
对比：
  1. 固定仓位（每只10万，回测基准）
  2. ATR波动率仓位（单笔风险1%总资金，止损2×ATR，最大15%单股上限）

验证：ATR仓位是否在控制回撤的同时保住收益
"""

import os, sys, json, warnings, time
from datetime import datetime
import pandas as pd
import pandas_ta as ta
import numpy as np

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

warnings.filterwarnings("ignore")

OUTPUT_DIR = os.path.join(WORKSPACE, "analysis", "backtest")
os.makedirs(OUTPUT_DIR, exist_ok=True)

START_DATE = os.environ.get("BT_START", "20240101")
END_DATE = os.environ.get("BT_END", "20260726")
INITIAL_CAPITAL = 100_000  # 单策略初始资金（对标现有回测口径）
TOTAL_CAPITAL = 3_000_000  # 组合总资金（用于ATR仓位计算）
COMMISSION = 0.0003
SLIPPAGE = 0.001

# 27只关注股（同backlog_backtest）
STOCKS = [
    ("600030","中信证券"), ("601066","中信建投"), ("600036","招商银行"),
    ("601995","中金公司"), ("000987","越秀资本"),
    ("600584","长电科技"), ("688981","中芯国际"), ("002156","通富微电"), ("002413","雷科防务"),
    ("300014","亿纬锂能"), ("002466","天齐锂业"),
    ("300285","国瓷材料"), ("603308","应流股份"), ("300124","汇川技术"), ("601100","恒立液压"),
    ("002318","久立特材"), ("300719","安达维尔"), ("002335","科华数据"), ("300748","金力永磁"),
    ("600160","巨化股份"), ("600346","恒力石化"),
    ("000708","中信特钢"),
    ("600660","福耀玻璃"), ("600570","恒生电子"), ("605566","福莱蒽特"), ("000157","中联重科"), ("601061","中信金属"),
]

# 导入数据路由
from analysis.data_layer.router import get_router

# 导入ATR仓位计算器
from analysis.position_sizer import PositionSizer

# 导入五策略
from analysis.backtest_strategies import (
    strategy_bollinger_atr, strategy_kdj_cci, strategy_ema_obv,
    strategy_ema_cross, strategy_macd, strategy_bull_trend_follow,
    run_simulation,
)

STRATEGIES = {
    "bollinger_atr": strategy_bollinger_atr,
    "kdj_cci": strategy_kdj_cci,
    "ema_obv": strategy_ema_obv,
    "ema_cross": strategy_ema_cross,
    "macd": strategy_macd,
    "bull_trend": strategy_bull_trend_follow,
}


def run_simulation_atr_position(df, actions, atr_series, total_capital=TOTAL_CAPITAL):
    """
    ATR波动率仓位模拟器
    单笔风险 = total_capital × 1%
    止损距离 = 2 × ATR
    股数 = 单笔风险金额 / 止损距离
    最大不超过 total_capital × 15%
    """
    capital = float(INITIAL_CAPITAL)  # 单策略账户资金
    shares = 0
    entry_price = 0
    stop_loss_price = 0
    trades = []
    equity_curve = []
    action_map = {a["date"]: a for a in actions}

    for i in range(len(df)):
        row = df.iloc[i]
        dt = str(row["date"].date())
        price = float(row["close"])
        atr_val = float(atr_series.iloc[i]) if i < len(atr_series) else 0

        if dt in action_map:
            act = action_map[dt]
            if act["type"] == "BUY" and shares == 0:
                # ATR仓位计算
                if atr_val > 0:
                    stop_distance = 2.0 * atr_val
                    risk_amount = total_capital * 0.01  # 单笔风险1%总资金
                    shares = int(risk_amount / stop_distance / 100) * 100
                    max_shares = int(total_capital * 0.15 / price / 100) * 100
                    shares = min(shares, max_shares)
                    stop_loss_price = price - stop_distance
                else:
                    shares = int(capital / (price * (1 + SLIPPAGE)) / 100) * 100
                    stop_loss_price = 0
                
                if shares >= 100:
                    cost = shares * price * (1 + SLIPPAGE)
                    fee2 = cost * COMMISSION
                    capital -= (cost + fee2)
                    entry_price = price
                    trades.append({"date": dt, "type": "BUY", "price": price,
                                   "shares": shares, "reason": act["reason"],
                                   "stop_loss": round(stop_loss_price, 2) if stop_loss_price > 0 else None})

            elif act["type"] == "SELL" and shares > 0:
                sell_value = shares * price * (1 - SLIPPAGE)
                fee = sell_value * COMMISSION
                net = sell_value - fee
                pnl = net - shares * entry_price * (1 + SLIPPAGE)
                pnl_pct = round((pnl / (shares * entry_price)) * 100, 2)
                trades.append({"date": dt, "type": "SELL", "price": price,
                               "shares": shares, "pnl": round(pnl, 2),
                               "pnl_pct": pnl_pct, "reason": act["reason"]})
                capital += net
                shares = 0
                entry_price = 0
                stop_loss_price = 0

        # 盘中止损检查（次日开盘止损，简化为收盘价触发）
        if shares > 0 and stop_loss_price > 0 and price <= stop_loss_price:
            sell_value = shares * stop_loss_price * (1 - SLIPPAGE)
            fee = sell_value * COMMISSION
            net = sell_value - fee
            pnl = net - shares * entry_price * (1 + SLIPPAGE)
            pnl_pct = round((pnl / (shares * entry_price)) * 100, 2)
            trades.append({"date": dt, "type": "SELL(ATR止损)", "price": stop_loss_price,
                           "shares": shares, "pnl": round(pnl, 2),
                           "pnl_pct": pnl_pct, "reason": f"ATR止损触发 止损价¥{stop_loss_price:.2f}"})
            capital += net
            shares = 0
            entry_price = 0
            stop_loss_price = 0

        equity_curve.append({"date": dt, "equity": capital + shares * price, "position": shares > 0})

    final_price = float(df.iloc[-1]["close"])
    if shares > 0:
        sell_value = shares * final_price * (1 - SLIPPAGE)
        fee = sell_value * COMMISSION
        net = sell_value - fee
        pnl = net - shares * entry_price * (1 + SLIPPAGE)
        pnl_pct = round((pnl / (shares * entry_price)) * 100, 2)
        trades.append({"date": str(df.iloc[-1]["date"].date()), "type": "SELL(平)",
                       "price": final_price, "shares": shares, "pnl": round(pnl, 2),
                       "pnl_pct": pnl_pct, "reason": "期末平仓"})
        capital += net
        shares = 0

    eq = pd.DataFrame(equity_curve)["equity"].values
    if len(eq) < 2:
        return {"error": "数据不足"}

    total_return = ((eq[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    years = len(eq) / 245
    ann = ((1 + total_return / 100) ** (1 / years) - 1) * 100 if years > 0 else 0
    daily = np.diff(eq) / eq[:-1]
    vol = np.std(daily, ddof=1) * np.sqrt(245) * 100
    sharpe = ((ann / 100) - 0.02) / (vol / 100) if vol > 0 else 0
    peak = np.maximum.accumulate(eq)
    mdd = ((eq - peak) / peak * 100).min()

    closed = [t for t in trades if t["type"].startswith("SELL")]
    wins = [t for t in closed if t.get("pnl", 0) > 0]
    win_rate = len(wins) / len(closed) * 100 if closed else 0
    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
    losses = [t for t in closed if t.get("pnl", 0) <= 0]
    avg_loss = abs(np.mean([t["pnl"] for t in losses])) if losses else 1
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else 0

    return {
        "total_return_pct": round(total_return, 2),
        "annualized_return_pct": round(ann, 2),
        "annualized_volatility_pct": round(vol, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(mdd, 2),
        "win_rate_pct": round(win_rate, 1),
        "profit_loss_ratio": round(pl_ratio, 2),
        "total_trades": len(closed),
        "final_equity": round(eq[-1], 2),
        "avg_shares_per_trade": round(np.mean([t["shares"] for t in closed if "shares" in t]), 0) if closed else 0,
    }


def fetch_data(symbol, name, start=START_DATE, end=END_DATE):
    router = get_router()
    try:
        df = router.get_daily(symbol, name, start_date=start)
        if df is not None and len(df) >= 100:
            return df
    except Exception as e:
        print(f"  ⚠️ {name}({symbol}) DataRouter获取失败: {e}")
    return None


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"🚀 ATR仓位/止损专项回测 | {START_DATE} ~ {END_DATE} | {len(STOCKS)} 只")
    print("=" * 70)

    results = []
    for idx, (symbol, name) in enumerate(STOCKS):
        print(f"[{idx+1}/{len(STOCKS)}] {name}({symbol}) 获取数据...")
        df = fetch_data(symbol, name)
        if df is None or len(df) < 100:
            print("  ❌ 数据不足，跳过")
            results.append({"symbol": symbol, "name": name, "error": "数据不足"})
            continue

        df = df[df["date"] <= pd.to_datetime(END_DATE)].reset_index(drop=True)
        
        # 预计算ATR
        df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)

        stock_result = {"symbol": symbol, "name": name, "strategies": {}}
        
        for sname, sfunc in STRATEGIES.items():
            try:
                actions = sfunc(df)
                # 固定仓位回测（基准）
                metrics_fixed = run_simulation(df, actions)
                # ATR动态仓位回测
                metrics_atr = run_simulation_atr_position(df, actions, df["ATR"])
                
                stock_result["strategies"][sname] = {
                    "fixed_position": metrics_fixed,
                    "atr_position": metrics_atr,
                }
                print(f"  {sname}: 固定={metrics_fixed.get('total_return_pct', 'err'):.1f}% | ATR={metrics_atr.get('total_return_pct', 'err'):.1f}%")
            except Exception as e:
                stock_result["strategies"][sname] = {"error": str(e)}
                print(f"  {sname}: 错误 - {e}")
        
        results.append(stock_result)

    # ── 聚合对比 ──
    print("\n" + "=" * 70)
    print("📊 聚合对比：固定仓位 vs ATR动态仓位")
    
    for sname in STRATEGIES:
        fixed_rets, atr_rets = [], []
        fixed_sharpes, atr_sharpes = [], []
        fixed_mdds, atr_mdds = [], []
        fixed_trades, atr_trades = [], []
        fixed_wr, atr_wr = [], []
        
        for r in results:
            if "error" in r:
                continue
            sd = r["strategies"].get(sname, {})
            if "error" in sd:
                continue
            f = sd.get("fixed_position", {})
            a = sd.get("atr_position", {})
            for key, vals_fixed, vals_atr in [
                ("total_return_pct", fixed_rets, atr_rets),
                ("sharpe_ratio", fixed_sharpes, atr_sharpes),
                ("max_drawdown_pct", fixed_mdds, atr_mdds),
                ("total_trades", fixed_trades, atr_trades),
                ("win_rate_pct", fixed_wr, atr_wr),
            ]:
                if key in f and f[key] is not None:
                    vals_fixed.append(f[key])
                if key in a and a[key] is not None:
                    vals_atr.append(a[key])
        
        if not fixed_rets or not atr_rets:
            continue
            
        print(f"\n--- {sname} ---")
        print(f"  平均收益:  固定={np.mean(fixed_rets):+.2f}%  ATR={np.mean(atr_rets):+.2f}%  差异={np.mean(atr_rets)-np.mean(fixed_rets):+.2f}%")
        print(f"  夏普比率:  固定={np.mean(fixed_sharpes):.3f}  ATR={np.mean(atr_sharpes):.3f}  差异={np.mean(atr_sharpes)-np.mean(fixed_sharpes):+.3f}")
        print(f"  最大回撤:  固定={np.mean(fixed_mdds):.2f}%  ATR={np.mean(atr_mdds):.2f}%  差异={np.mean(atr_mdds)-np.mean(fixed_mdds):+.2f}%")
        print(f"  平均交易:  固定={np.mean(fixed_trades):.1f}  ATR={np.mean(atr_trades):.1f}")
        print(f"  胜率:      固定={np.mean(fixed_wr):.1f}%  ATR={np.mean(atr_wr):.1f}%")

    # 逐股胜负统计
    for sname in STRATEGIES:
        win_atr = win_fixed = 0
        for r in results:
            if "error" in r: continue
            sd = r["strategies"].get(sname, {})
            if "error" in sd: continue
            f = sd.get("fixed_position", {})
            a = sd.get("atr_position", {})
            if "total_return_pct" in f and "total_return_pct" in a:
                if a["total_return_pct"] > f["total_return_pct"]:
                    win_atr += 1
                else:
                    win_fixed += 1
        print(f"  {sname}: ATR胜 {win_atr} 只 vs 固定胜 {win_fixed} 只")

    # 保存结果
    payload = {
        "date": today,
        "window": {"start": START_DATE, "end": END_DATE},
        "note": "固定仓位=每只10万初始资金 | ATR仓位=单笔风险1%总资金(300万) 止损2×ATR 单股上限15%",
        "results": results,
    }
    
    json_path = os.path.join(OUTPUT_DIR, f"atr_position_backtest_{today}.json")
    with open(json_path, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✅ 结果已保存: {json_path}")


if __name__ == "__main__":
    main()