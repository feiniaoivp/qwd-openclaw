#!/usr/bin/env python3
"""
量化交易模拟盘对比回测
策略A：双均线金叉死叉（MA5 / MA20）
策略B：MACD + RSI 融合策略
对比维度：总收益率、胜率、最大回撤、夏普比率、交易次数
"""
import os, sys, json, warnings, time
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

# 全局延迟控制，避免触发限流
FETCH_DELAY = 0.5

# ======== 配置 ========
START_CAPITAL = 1_000_000  # 初始资金 100万
COMMISSION = 0.0003       # 佣金万分之三
SLIPPAGE = 0.001          # 滑点 0.1%
BACKTEST_START = "2026-01-01"  # 回测起始（模拟盘从这天开始交易）
DATA_START = "2025-07-01"      # 数据拉取起始（需要预热均线/MACD）
DATA_END = "2026-07-22"        # 数据截止

# 25只关注股票
WATCHLIST = [
    ("600030", "中信证券"), ("601066", "中信建投"), ("600036", "招商银行"),
    ("601995", "中金公司"), ("000987", "越秀资本"), ("600584", "长电科技"),
    ("688981", "中芯国际"), ("002156", "通富微电"), ("002413", "雷科防务"),
    ("300014", "亿纬锂能"), ("002466", "天齐锂业"), ("601865", "福莱特"),
    ("300285", "国瓷材料"), ("603308", "应流股份"), ("300124", "汇川技术"),
    ("601100", "恒立液压"), ("002318", "久立特材"), ("300719", "安达维尔"),
    ("002335", "科华数据"), ("600660", "福耀玻璃"), ("600570", "恒生电子"),
    ("605566", "福莱蒽特"), ("000157", "中联重科"), ("601061", "中信金属"),
    ("900925", "机电B股"),
]

def calc_ma(series, window):
    return series.rolling(window=window).mean()

def calc_macd(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_bar = 2 * (dif - dea)
    return dif, dea, macd_bar

def calc_rsi(close, window=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=window, min_periods=window).mean()
    avg_loss = loss.rolling(window=window, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calc_atr(high, low, close, window=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(window=window).mean()
    return atr

def fetch_data(code, name):
    """获取复权日K数据（使用新浪接口）"""
    try:
        # 确定交易所前缀
        if code.startswith('6'):
            symbol = f'sh{code}'
        elif code.startswith('9'):
            symbol = f'sh{code}'
        elif code.startswith('0') or code.startswith('3'):
            symbol = f'sz{code}'
        else:
            symbol = f'sz{code}'
        
        df = ak.stock_zh_a_daily(
            symbol=symbol,
            start_date=DATA_START.replace("-", ""),
            end_date=DATA_END.replace("-", ""),
            adjust="qfq"
        )
        if df is None or df.empty:
            return None
        df = df.rename(columns={
            "date": "date", "open": "open", "close": "close",
            "high": "high", "low": "low", "volume": "volume",
            "amount": "amount",
        })
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        df["code"] = code
        df["name"] = name
        return df
    except Exception as e:
        print(f"  ⚠️ {name}({code}) 数据获取失败: {e}")
        return None

def strategy_a_ma_cross(df):
    """策略A：MA5金叉MA20买入，死叉卖出"""
    df = df.copy()
    df["MA5"] = calc_ma(df["close"], 5)
    df["MA20"] = calc_ma(df["close"], 20)
    df["signal"] = 0
    # 金叉信号
    df.loc[(df["MA5"] > df["MA20"]) & (df["MA5"].shift(1) <= df["MA20"].shift(1)), "signal"] = 1
    # 死叉信号
    df.loc[(df["MA5"] < df["MA20"]) & (df["MA5"].shift(1) >= df["MA20"].shift(1)), "signal"] = -1
    return df

def strategy_b_macd_rsi(df):
    """策略B：MACD金叉 + RSI(14)在40-70之间做多；死叉或RSI>80平仓"""
    df = df.copy()
    dif, dea, macd_bar = calc_macd(df["close"])
    df["MACD_DIF"] = dif
    df["MACD_DEA"] = dea
    df["MACD_BAR"] = macd_bar
    df["RSI14"] = calc_rsi(df["close"], 14)
    df["signal"] = 0
    
    # MACD金叉（DIF上穿DEA）且 RSI在40-70之间 → 买入
    buy_cond = (
        (df["MACD_DIF"] > df["MACD_DEA"]) &
        (df["MACD_DIF"].shift(1) <= df["MACD_DEA"].shift(1)) &
        (df["RSI14"] >= 40) & (df["RSI14"] <= 70)
    )
    # MACD死叉（DIF下穿DEA）或 RSI > 80 → 卖出
    sell_cond = (
        ((df["MACD_DIF"] < df["MACD_DEA"]) & (df["MACD_DIF"].shift(1) >= df["MACD_DEA"].shift(1))) |
        (df["RSI14"] > 80)
    )
    df.loc[buy_cond, "signal"] = 1
    df.loc[sell_cond, "signal"] = -1
    return df

def backtest_single(df_strat, code, name, strategy_name):
    """
    对单只股票执行回测
    规则：每次信号触发，全仓买入/卖出。
    如果持仓中再出买入信号，忽略（已在仓）。
    空仓中再出卖出信号，忽略。
    """
    df = df_strat.copy()
    df = df[df["date"] >= BACKTEST_START].reset_index(drop=True)
    if len(df) < 20:
        return None
    
    capital = START_CAPITAL
    shares = 0
    trades = []
    in_position = False
    entry_price = 0
    entry_date = None
    
    daily_values = []
    
    for i, row in df.iterrows():
        price = row["close"]
        dt = row["date"]
        sig = row["signal"]
        
        # 买入
        if sig == 1 and not in_position:
            cost = capital
            fee = cost * COMMISSION
            available = cost - fee
            shares_buy = int(available / (price * (1 + SLIPPAGE)) / 100) * 100
            if shares_buy >= 100:
                actual_cost = shares_buy * price * (1 + SLIPPAGE)
                actual_fee = actual_cost * COMMISSION
                capital -= (actual_cost + actual_fee)
                shares = shares_buy
                in_position = True
                entry_price = price
                entry_date = dt
                trades.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "type": "BUY",
                    "price": round(price, 2),
                    "shares": shares_buy,
                    "value": round(actual_cost + actual_fee, 2)
                })
        # 卖出
        elif sig == -1 and in_position:
            sell_value = shares * price * (1 - SLIPPAGE)
            fee = sell_value * COMMISSION
            capital += (sell_value - fee)
            pnl = (sell_value - fee) - (shares * entry_price * (1 + SLIPPAGE) + trades[-1].get("fee", 0) if trades else 0)
            trades.append({
                "date": dt.strftime("%Y-%m-%d"),
                "type": "SELL",
                "price": round(price, 2),
                "shares": shares,
                "value": round(sell_value - fee, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round((pnl / (shares * entry_price)) * 100, 2) if shares else 0
            })
            shares = 0
            in_position = False
        
        # 记录每日资产总值
        total_value = capital + (shares * price) if shares > 0 else capital
        daily_values.append({
            "date": dt,
            "total_value": total_value
        })
    
    # 期末平仓
    if in_position and len(df) > 0:
        last_row = df.iloc[-1]
        price = last_row["close"]
        sell_value = shares * price * (1 - SLIPPAGE)
        fee = sell_value * COMMISSION
        capital += (sell_value - fee)
        trades.append({
            "date": last_row["date"].strftime("%Y-%m-%d"),
            "type": "SELL(期末)",
            "price": round(price, 2),
            "shares": shares,
            "value": round(sell_value - fee, 2),
            "pnl": round((sell_value - fee) - (shares * entry_price * (1 + SLIPPAGE)), 2) if entry_price else 0,
        })
        shares = 0
    
    final_value = capital
    
    # 计算指标
    total_return = (final_value - START_CAPITAL) / START_CAPITAL * 100
    win_trades = [t for t in trades if t.get("pnl", 0) > 0]
    loss_trades = [t for t in trades if t.get("pnl", 0) < 0]
    total_trades = len([t for t in trades if t["type"].startswith("SELL")])
    win_rate = (len(win_trades) / total_trades * 100) if total_trades > 0 else 0
    
    # 最大回撤
    daily_df = pd.DataFrame(daily_values)
    if len(daily_df) > 0:
        daily_df["peak"] = daily_df["total_value"].cummax()
        daily_df["drawdown"] = (daily_df["total_value"] - daily_df["peak"]) / daily_df["peak"] * 100
        max_dd = daily_df["drawdown"].min()
    else:
        max_dd = 0
    
    # 年化收益率
    trading_days = len(df)
    years = trading_days / 252
    if years > 0 and final_value > 0 and START_CAPITAL > 0:
        annual_return = ((final_value / START_CAPITAL) ** (1 / years) - 1) * 100
    else:
        annual_return = 0
    
    # 夏普比率（假设无风险利率 2.5%）
    if len(daily_df) > 1:
        daily_df["daily_return"] = daily_df["total_value"].pct_change()
        excess_returns = daily_df["daily_return"].dropna() - (0.025 / 252)
        if excess_returns.std() > 0:
            sharpe = np.sqrt(252) * excess_returns.mean() / excess_returns.std()
        else:
            sharpe = 0
    else:
        sharpe = 0
    
    # 平均每笔盈亏
    avg_win = np.mean([t.get("pnl_pct", 0) for t in win_trades]) if win_trades else 0
    avg_loss = np.mean([t.get("pnl_pct", 0) for t in loss_trades]) if loss_trades else 0
    
    return {
        "code": code,
        "name": name,
        "strategy": strategy_name,
        "total_return_pct": round(total_return, 2),
        "annual_return_pct": round(annual_return, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 3),
        "total_trades": total_trades,
        "win_trades": len(win_trades),
        "loss_trades": len(loss_trades),
        "win_rate_pct": round(win_rate, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "final_capital": round(final_value, 2),
        "profit_loss": round(final_value - START_CAPITAL, 2),
    }

def main():
    print(f"🚀 量化模拟盘对比回测")
    print(f"{'='*60}")
    print(f"初始资金: ¥{START_CAPITAL:,}")
    print(f"回测区间: {BACKTEST_START} ~ {DATA_END}")
    print(f"股票数量: {len(WATCHLIST)} 只")
    print(f"策略A: 双均线金叉死叉 (MA5/MA20)")
    print(f"策略B: MACD金叉+RSI (MACD+RSI14)")
    print(f"{'='*60}\n")
    
    results_a = []
    results_b = []
    errors = []
    
    for idx, (code, name) in enumerate(WATCHLIST, 1):
        print(f"[{idx}/{len(WATCHLIST)}] 正在获取 {name}({code})...", end=" ")
        sys.stdout.flush()
        
        time.sleep(FETCH_DELAY)
        df = fetch_data(code, name)
        if df is None or len(df) < 60:
            print(f"❌ 数据不足")
            errors.append(f"{name}({code}): 数据不足")
            continue
        
        print(f"共{len(df)}条日K", end=" ")
        
        # 策略A
        df_a = strategy_a_ma_cross(df)
        r_a = backtest_single(df_a, code, name, "MA金叉死叉")
        if r_a:
            results_a.append(r_a)
        
        # 策略B
        df_b = strategy_b_macd_rsi(df)
        r_b = backtest_single(df_b, code, name, "MACD+RSI")
        if r_b:
            results_b.append(r_b)
        
        print("✅")
    
    # 汇总结果
    print(f"\n{'='*60}")
    print(f"📊 汇总对比")
    print(f"{'='*60}\n")
    
    # 整体统计
    def aggregate(rs):
        if not rs:
            return {}
        avg_return = np.mean([r["total_return_pct"] for r in rs])
        avg_annual = np.mean([r["annual_return_pct"] for r in rs])
        avg_max_dd = np.mean([r["max_drawdown_pct"] for r in rs])
        avg_sharpe = np.mean([r["sharpe_ratio"] for r in rs])
        avg_win_rate = np.mean([r["win_rate_pct"] for r in rs])
        total_trades = sum(r["total_trades"] for r in rs)
        
        # 正收益股票数量
        positive = sum(1 for r in rs if r["total_return_pct"] > 0)
        negative = sum(1 for r in rs if r["total_return_pct"] <= 0)
        
        # 跑赢基准（基准=沪深300同期约-5%估算）
        beat_index = sum(1 for r in rs if r["total_return_pct"] > -5)
        
        return {
            "avg_return": round(avg_return, 2),
            "avg_annual": round(avg_annual, 2),
            "avg_max_dd": round(avg_max_dd, 2),
            "avg_sharpe": round(avg_sharpe, 3),
            "avg_win_rate": round(avg_win_rate, 2),
            "total_trades": total_trades,
            "positive_count": positive,
            "negative_count": negative,
            "beat_index_count": beat_index,
            "stock_count": len(rs)
        }
    
    agg_a = aggregate(results_a)
    agg_b = aggregate(results_b)
    
    # 对比表
    print(f"### 整体指标对比\n")
    print(f"| 指标 | 策略A (MA金叉死叉) | 策略B (MACD+RSI) | 优劣 |")
    print(f"|:-----|:-----------------:|:---------------:|:----:|")
    
    def cmp(val_a, val_b, higher_is_better=True):
        if higher_is_better:
            return "✅ 胜" if val_a > val_b else ("✅ 胜" if val_b > val_a else "— 平")
        else:
            return "✅ 胜" if val_a < val_b else ("✅ 胜" if val_b < val_a else "— 平")
    
    print(f"| 平均收益率 | {agg_a['avg_return']:.2f}% | {agg_b['avg_return']:.2f}% | {cmp(agg_a['avg_return'], agg_b['avg_return'], True)} |")
    print(f"| 平均年化收益 | {agg_a['avg_annual']:.2f}% | {agg_b['avg_annual']:.2f}% | {cmp(agg_a['avg_annual'], agg_b['avg_annual'], True)} |")
    print(f"| 平均最大回撤 | {agg_a['avg_max_dd']:.2f}% | {agg_b['avg_max_dd']:.2f}% | {cmp(agg_a['avg_max_dd'], agg_b['avg_max_dd'], False)} |")
    print(f"| 平均夏普比率 | {agg_a['avg_sharpe']:.3f} | {agg_b['avg_sharpe']:.3f} | {cmp(agg_a['avg_sharpe'], agg_b['avg_sharpe'], True)} |")
    print(f"| 平均胜率 | {agg_a['avg_win_rate']:.2f}% | {agg_b['avg_win_rate']:.2f}% | {cmp(agg_a['avg_win_rate'], agg_b['avg_win_rate'], True)} |")
    print(f"| 正收益股票数 | {agg_a['positive_count']}/{agg_a['stock_count']} | {agg_b['positive_count']}/{agg_b['stock_count']} | — |")
    print(f"| 总交易次数 | {agg_a['total_trades']} | {agg_b['total_trades']} | — |")
    print()
    
    # 个股对比
    print(f"### 个股逐只对比\n")
    print(f"| 股票 | 策略A收益率 | 策略B收益率 | 策略A最大回撤 | 策略B最大回撤 | 策略A夏普 | 策略B夏普 | 较优 |")
    print(f"|:----|:----------:|:----------:|:------------:|:------------:|:--------:|:--------:|:---:|")
    
    # 合并结果
    all_codes = set(r["code"] for r in results_a) | set(r["code"] for r in results_b)
    merged = {}
    for r in results_a:
        merged[r["code"]] = {"name": r["name"], "a": r, "b": None}
    for r in results_b:
        if r["code"] in merged:
            merged[r["code"]]["b"] = r
        else:
            merged[r["code"]] = {"name": r["name"], "a": None, "b": r}
    
    a_wins = 0
    b_wins = 0
    tie = 0
    
    for code in sorted(all_codes):
        m = merged[code]
        r_a = m.get("a")
        r_b = m.get("b")
        a_ret = r_a["total_return_pct"] if r_a else "—"
        b_ret = r_b["total_return_pct"] if r_b else "—"
        a_dd = f"{r_a['max_drawdown_pct']:.2f}%" if r_a else "—"
        b_dd = f"{r_b['max_drawdown_pct']:.2f}%" if r_b else "—"
        a_sr = f"{r_a['sharpe_ratio']:.3f}" if r_a else "—"
        b_sr = f"{r_b['sharpe_ratio']:.3f}" if r_b else "—"
        
        if r_a and r_b:
            if r_a["total_return_pct"] > r_b["total_return_pct"]:
                best = "📈 A"
                a_wins += 1
            elif r_b["total_return_pct"] > r_a["total_return_pct"]:
                best = "📈 B"
                b_wins += 1
            else:
                best = "—"
                tie += 1
        elif r_a:
            best = "仅A"
        else:
            best = "仅B"
        
        a_ret_str = f"{a_ret:.2f}%" if isinstance(a_ret, float) else a_ret
        b_ret_str = f"{b_ret:.2f}%" if isinstance(b_ret, float) else b_ret
        
        print(f"| {m['name']} | {a_ret_str} | {b_ret_str} | {a_dd} | {b_dd} | {a_sr} | {b_sr} | {best} |")
    
    print()
    print(f"**回合结果：A胜 {a_wins} / B胜 {b_wins} / 平 {tie}**")
    print()
    
    # 收益排名
    print(f"### 策略A 收益Top10\n")
    top_a = sorted(results_a, key=lambda r: r["total_return_pct"], reverse=True)[:10]
    print(f"| 排名 | 股票 | 收益率 | 年化收益 | 最大回撤 | 夏普 | 胜率 | 交易次数 |")
    print(f"|:---:|:----|:------:|:--------:|:--------:|:----:|:----:|:--------:|")
    for i, r in enumerate(top_a, 1):
        print(f"| {i} | {r['name']} | {r['total_return_pct']:.2f}% | {r['annual_return_pct']:.2f}% | {r['max_drawdown_pct']:.2f}% | {r['sharpe_ratio']:.3f} | {r['win_rate_pct']:.1f}% | {r['total_trades']} |")
    
    print()
    print(f"### 策略B 收益Top10\n")
    top_b = sorted(results_b, key=lambda r: r["total_return_pct"], reverse=True)[:10]
    print(f"| 排名 | 股票 | 收益率 | 年化收益 | 最大回撤 | 夏普 | 胜率 | 交易次数 |")
    print(f"|:---:|:----|:------:|:--------:|:--------:|:----:|:----:|:--------:|")
    for i, r in enumerate(top_b, 1):
        print(f"| {i} | {r['name']} | {r['total_return_pct']:.2f}% | {r['annual_return_pct']:.2f}% | {r['max_drawdown_pct']:.2f}% | {r['sharpe_ratio']:.3f} | {r['win_rate_pct']:.1f}% | {r['total_trades']} |")
    
    print()
    # 最差股票
    print(f"### 亏损最严重的股票\n")
    worst_all = sorted(results_a + results_b, key=lambda r: r["total_return_pct"])[:10]
    print(f"| 策略 | 股票 | 收益率 | 最大回撤 |")
    print(f"|:----|:----|:------:|:--------:|")
    for r in worst_all:
        print(f"| {r['strategy']} | {r['name']} | {r['total_return_pct']:.2f}% | {r['max_drawdown_pct']:.2f}% |")
    
    print()
    if errors:
        print(f"⚠️ 数据获取失败: {len(errors)} 只")
        for e in errors:
            print(f"  - {e}")
    
    # 保存详细结果
    output = {
        "backtest_config": {
            "start_capital": START_CAPITAL,
            "period": f"{BACKTEST_START} ~ {DATA_END}",
            "stocks_count": len(WATCHLIST),
        },
        "summary": {
            "strategy_a": agg_a,
            "strategy_b": agg_b,
            "a_wins": a_wins,
            "b_wins": b_wins,
            "tie": tie
        },
        "details_a": results_a,
        "details_b": results_b,
        "errors": errors
    }
    
    output_path = "/Users/duguke/.openclaw/workspace/backtest_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 详细结果已保存至: {output_path}")
    print(f"{'='*60}")
    print(f"🏁 回测完成！")
    
    # 最终结论
    print(f"\n### 📝 结论")
    print(f"- 策略A(MA金叉死叉) 在 {agg_a['positive_count']}/{agg_a['stock_count']} 只股票上实现正收益")
    print(f"- 策略B(MACD+RSI) 在 {agg_b['positive_count']}/{agg_b['stock_count']} 只股票上实现正收益")
    print(f"- 平均收益率: A={agg_a['avg_return']:.2f}% vs B={agg_b['avg_return']:.2f}%")
    print(f"- 直接对比: A胜 {a_wins} 次 / B胜 {b_wins} 次")
    print(f"- 夏普比率: A={agg_a['avg_sharpe']:.3f} vs B={agg_b['avg_sharpe']:.3f} (越高越好)")

if __name__ == "__main__":
    main()
