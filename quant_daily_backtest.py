#!/usr/bin/env python3
"""
📊 量化模拟盘每日自动更新脚本（收盘后运行）
- 从新浪财经获取最新日K线数据
- 更新两个策略的模拟盘持仓
- 计算净值曲线、收益率、最大回撤等
- 生成对比报告并保存

定时：工作日 15:45（收盘后15分钟）
"""
import os, sys, json, time, warnings
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

WORKSPACE = "/Users/duguke/.openclaw/workspace"
FETCH_DELAY = 0.3

# ======== 配置 ========
START_CAPITAL = 1_000_000
COMMISSION = 0.0003
SLIPPAGE = 0.001
DATA_START = "2025-07-01"
TRADING_START = "2026-01-01"

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

STRATEGY_A_NAME = "EMA12/26金叉死叉"
STRATEGY_B_NAME = "纯MACD金叉死叉"

# ---------- 技术指标 ----------
def calc_ma(series, window):
    return series.rolling(window=window).mean()

def calc_macd(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    return dif, dea

def calc_ema(series, window):
    return series.ewm(span=window, adjust=False).mean()

# ---------- 数据获取 ----------
def fetch_data(code, name):
    try:
        if code.startswith('6') or code.startswith('9'):
            symbol = f'sh{code}'
        else:
            symbol = f'sz{code}'
        df = ak.stock_zh_a_daily(symbol=symbol,
            start_date=DATA_START.replace("-",""),
            end_date=datetime.now().strftime("%Y%m%d"),
            adjust="qfq")
        if df is None or df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        df["code"] = code
        df["name"] = name
        return df
    except Exception as e:
        return None

# ---------- 策略信号 ----------
def strategy_a_ema_cross(df):
    """策略C升级版：EMA12/26 金叉死叉（替代原MA5/20）"""
    df = df.copy()
    df["EMA12"] = calc_ema(df["close"], 12)
    df["EMA26"] = calc_ema(df["close"], 26)
    df["signal"] = 0
    df.loc[(df["EMA12"] > df["EMA26"]) & (df["EMA12"].shift(1) <= df["EMA26"].shift(1)), "signal"] = 1
    df.loc[(df["EMA12"] < df["EMA26"]) & (df["EMA12"].shift(1) >= df["EMA26"].shift(1)), "signal"] = -1
    return df

def strategy_b_macd_only(df):
    """策略B升级版：纯MACD金叉死叉（去掉RSI过滤）"""
    df = df.copy()
    dif, dea = calc_macd(df["close"])
    df["MACD_DIF"] = dif
    df["MACD_DEA"] = dea
    df["signal"] = 0
    df.loc[(df["MACD_DIF"] > df["MACD_DEA"]) & (df["MACD_DIF"].shift(1) <= df["MACD_DEA"].shift(1)), "signal"] = 1
    df.loc[(df["MACD_DIF"] < df["MACD_DEA"]) & (df["MACD_DIF"].shift(1) >= df["MACD_DEA"].shift(1)), "signal"] = -1
    return df

# ---------- 单只股票回测 ----------
def backtest_stock(df_strat, code, name, strategy_name):
    df = df_strat.copy()
    df = df[df["date"] >= TRADING_START].reset_index(drop=True)
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
        volume = row["volume"] if "volume" in row else 0

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
                trades.append({"date": dt, "type": "BUY", "price": price, "shares": shares_buy,
                               "value": round(actual_cost + actual_fee, 2)})
        elif sig == -1 and in_position:
            sell_value = shares * price * (1 - SLIPPAGE)
            fee = sell_value * COMMISSION
            capital += (sell_value - fee)
            pnl = sell_value - fee - (shares * entry_price * (1 + SLIPPAGE) +
                                      (trades[-1].get("fee",0) if trades else 0))
            trades.append({"date": dt, "type": "SELL", "price": price, "shares": shares,
                           "value": round(sell_value - fee, 2),
                           "pnl": round(pnl, 2),
                           "pnl_pct": round((pnl / (shares * entry_price)) * 100, 2) if shares else 0})
            shares = 0
            in_position = False

        total_value = capital + (shares * price) if in_position else capital
        daily_values.append({"date": dt, "total_value": total_value, "close": price})

    # 期末平仓
    if in_position and len(df) > 0:
        last_row = df.iloc[-1]
        price = last_row["close"]
        sell_value = shares * price * (1 - SLIPPAGE)
        fee = sell_value * COMMISSION
        capital += (sell_value - fee)
        trades.append({"date": last_row["date"], "type": "SELL(期末)", "price": price, "shares": shares,
                       "value": round(sell_value - fee, 2)})
        shares = 0

    final_value = capital
    total_return = (final_value - START_CAPITAL) / START_CAPITAL * 100
    win_trades = [t for t in trades if t.get("pnl", 0) > 0]
    loss_trades = [t for t in trades if t.get("pnl", 0) < 0]
    total_trades = len([t for t in trades if t["type"].startswith("SELL")])
    win_rate = (len(win_trades) / total_trades * 100) if total_trades > 0 else 0

    daily_df = pd.DataFrame(daily_values)
    if len(daily_df) > 0:
        daily_df["peak"] = daily_df["total_value"].cummax()
        daily_df["drawdown"] = (daily_df["total_value"] - daily_df["peak"]) / daily_df["peak"] * 100
        max_dd = daily_df["drawdown"].min()
    else:
        max_dd = 0

    trading_days = len(df)
    years = trading_days / 252
    annual_return = ((final_value / START_CAPITAL) ** (1 / years) - 1) * 100 if years > 0 and final_value > 0 else 0

    # 当日涨跌（最新一个交易日 vs 上一个）
    latest_value = daily_values[-1]["total_value"] if daily_values else 0
    if len(daily_values) >= 2:
        prev_value = daily_values[-2]["total_value"]
        day_change_pct = (latest_value - prev_value) / prev_value * 100
    else:
        day_change_pct = 0

    # 最新持仓状态
    holding = daily_values[-1]["close"] if in_position else 0
    holding_shares = shares if in_position else 0

    return {
        "code": code, "name": name, "strategy": strategy_name,
        "total_return_pct": round(total_return, 2),
        "annual_return_pct": round(annual_return, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "total_trades": total_trades,
        "win_trades": len(win_trades),
        "loss_trades": len(loss_trades),
        "win_rate_pct": round(win_rate, 2),
        "final_capital": round(final_value, 2),
        "profit_loss": round(final_value - START_CAPITAL, 2),
        "day_change_pct": round(day_change_pct, 2),
        "in_position": in_position,
        "holding_price": round(holding, 2) if in_position else 0,
        "holding_shares": holding_shares,
        "latest_price": round(daily_values[-1]["close"], 2) if daily_values else 0,
        "trades": trades,
        "daily_values": [{"date": str(d["date"].date()), "v": round(d["total_value"], 2)}
                         for d in daily_values[::5]],  # 每隔5天采样
    }


def main():
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"⏱️ 运行时间: {today_str}")
    print(f"{'='*60}")
    print(f"📊 量化模拟盘每日自动更新")
    print(f"{'='*60}")
    
    results_a = []
    results_b = []
    errors = []

    for idx, (code, name) in enumerate(WATCHLIST, 1):
        print(f"[{idx}/25] {name}({code})...", end=" ")
        sys.stdout.flush()
        time.sleep(FETCH_DELAY)

        df = fetch_data(code, name)
        if df is None or len(df) < 60:
            print("❌")
            errors.append(name)
            continue

        print(f"{len(df)}条", end=" ")
        df_a = strategy_a_ema_cross(df)
        r_a = backtest_stock(df_a, code, name, STRATEGY_A_NAME)
        if r_a:
            results_a.append(r_a)

        df_b = strategy_b_macd_only(df)
        r_b = backtest_stock(df_b, code, name, STRATEGY_B_NAME)
        if r_b:
            results_b.append(r_b)

        print("✅")

    # ======== 计算汇总 ========
    def aggregate(rs):
        if not rs:
            return {}
        return {
            "avg_return": round(np.mean([r["total_return_pct"] for r in rs]), 2),
            "avg_annual": round(np.mean([r["annual_return_pct"] for r in rs]), 2),
            "avg_max_dd": round(np.mean([r["max_drawdown_pct"] for r in rs]), 2),
            "avg_win_rate": round(np.mean([r["win_rate_pct"] for r in rs]), 2),
            "positive_count": sum(1 for r in rs if r["total_return_pct"] > 0),
            "stock_count": len(rs),
        }

    agg_a = aggregate(results_a)
    agg_b = aggregate(results_b)

    # 当日变动统计
    day_changes_a = [r["day_change_pct"] for r in results_a if r["day_change_pct"] != 0]
    day_changes_b = [r["day_change_pct"] for r in results_b if r["day_change_pct"] != 0]
    day_avg_a = round(np.mean(day_changes_a), 2) if day_changes_a else 0
    day_avg_b = round(np.mean(day_changes_b), 2) if day_changes_b else 0

    # A vs B 对比计数
    a_wins = sum(1 for ra, rb in zip(results_a, results_b)
                 if ra["total_return_pct"] > rb["total_return_pct"])
    b_wins = sum(1 for ra, rb in zip(results_a, results_b)
                 if rb["total_return_pct"] > ra["total_return_pct"])

    # ======== 输出报告 ========
    print(f"\n{'='*60}")
    print(f"📋 每日模拟盘简报")
    print(f"{'='*60}")
    print(f"📅 日期: {today_str}")
    print()
    print(f"**策略A ({STRATEGY_A_NAME})**")
    print(f"  - 整体平均收益: {agg_a.get('avg_return',0):.2f}%")
    print(f"  - 今日平均变动: {day_avg_a:+.2f}%")
    print(f"  - 正收益股票: {agg_a.get('positive_count',0)}/{agg_a.get('stock_count',0)}")
    print(f"  - 平均最大回撤: {agg_a.get('avg_max_dd',0):.2f}%")
    print()
    print(f"**策略B ({STRATEGY_B_NAME})**")
    print(f"  - 整体平均收益: {agg_b.get('avg_return',0):.2f}%")
    print(f"  - 今日平均变动: {day_avg_b:+.2f}%")
    print(f"  - 正收益股票: {agg_b.get('positive_count',0)}/{agg_b.get('stock_count',0)}")
    print(f"  - 平均最大回撤: {agg_b.get('avg_max_dd',0):.2f}%")
    print()
    print(f"**直接对比: C胜 {a_wins} / B胜 {b_wins}**")
    print()

    # ======== 收益排行 ========
    print(f"**策略C (EMA12/26) 收益Top5**")
    top_a = sorted(results_a, key=lambda r: r["total_return_pct"], reverse=True)[:5]
    for r in top_a:
        pos = "📗" if r["in_position"] else "📕空仓"
        print(f"  {r['name']}  {r['total_return_pct']:+.2f}%  (今日{r['day_change_pct']:+.2f}%)  {pos}  ¥{r['latest_price']}")
    print()
    print(f"**策略B (纯MACD) 收益Top5**")
    top_b = sorted(results_b, key=lambda r: r["total_return_pct"], reverse=True)[:5]
    for r in top_b:
        pos = "📗" if r["in_position"] else "📕空仓"
        print(f"  {r['name']}  {r['total_return_pct']:+.2f}%  (今日{r['day_change_pct']:+.2f}%)  {pos}  ¥{r['latest_price']}")
    print()

    # ======== 今日信号 ========
    print(f"**⚡ 今日交易信号**")
    # 找出今日产生信号的股票
    today_signals_a = []
    today_signals_b = []
    for r in results_a:
        if r["trades"] and len(r["trades"]) >= 2:
            last_trade = r["trades"][-1]
            if last_trade.get("date") and str(last_trade["date"].date()) == today_str:
                today_signals_a.append((r["name"], last_trade["type"]))
        # 检查是否只有一笔买入且是今天的
        if not today_signals_a or today_signals_a[-1][0] != r["name"]:
            if r["trades"] and len(r["trades"]) == 1 and r["trades"][0]["type"] == "BUY":
                if str(r["trades"][0]["date"].date()) == today_str:
                    today_signals_a.append((r["name"], "BUY"))
    
    for r in results_b:
        if r["trades"] and len(r["trades"]) >= 2:
            last_trade = r["trades"][-1]
            if last_trade.get("date") and str(last_trade["date"].date()) == today_str:
                today_signals_b.append((r["name"], last_trade["type"]))
        if not today_signals_b or today_signals_b[-1][0] != r["name"]:
            if r["trades"] and len(r["trades"]) == 1 and r["trades"][0]["type"] == "BUY":
                if str(r["trades"][0]["date"].date()) == today_str:
                    today_signals_b.append((r["name"], "BUY"))
    
    # 策略信号已由对应函数覆盖

    # ======== 输出文本报告 ========
    output_lines = []
    output_lines.append(f"# 量化模拟盘日报 {today_str}")
    output_lines.append("")
    output_lines.append(f"## 整体概况")
    output_lines.append("")
    output_lines.append(f"| 指标 | 策略C (EMA12/26) | 策略B (纯MACD) |")
    output_lines.append(f"|:-----|:--------------:|:----------------:|")
    output_lines.append(f"| 累计平均收益 | {agg_a.get('avg_return',0):.2f}% | {agg_b.get('avg_return',0):.2f}% |")
    output_lines.append(f"| 今日平均变动 | {day_avg_a:+.2f}% | {day_avg_b:+.2f}% |")
    output_lines.append(f"| 正收益股票 | {agg_a.get('positive_count',0)}/{agg_a.get('stock_count',0)} | {agg_b.get('positive_count',0)}/{agg_b.get('stock_count',0)} |")
    output_lines.append(f"| 平均最大回撤 | {agg_a.get('avg_max_dd',0):.2f}% | {agg_b.get('avg_max_dd',0):.2f}% |")
    output_lines.append(f"| 策略C vs B 直接对抗 | **C胜 {a_wins}** | B胜 {b_wins} |")
    output_lines.append("")
    output_lines.append(f"## 收益排行 Top5")
    output_lines.append("")
    output_lines.append(f"### 策略C (EMA12/26)")
    for i, r in enumerate(top_a, 1):
        pos = "持仓" if r["in_position"] else "空仓"
        output_lines.append(f"{i}. **{r['name']}** {r['total_return_pct']:+.2f}% 今日{r['day_change_pct']:+.2f}% [{pos}] ¥{r['latest_price']}")
    output_lines.append("")
    output_lines.append(f"### 策略B (纯MACD)")
    for i, r in enumerate(top_b, 1):
        pos = "持仓" if r["in_position"] else "空仓"
        output_lines.append(f"{i}. **{r['name']}** {r['total_return_pct']:+.2f}% 今日{r['day_change_pct']:+.2f}% [{pos}] ¥{r['latest_price']}")
    output_lines.append("")
    output_lines.append(f"## 持仓状态")
    output_lines.append("")
    output_lines.append("| 股票 | 策略C | 策略B |")
    output_lines.append("|:----|:----:|:----:|")
    for ra in results_a:
        rb = next((r for r in results_b if r["code"] == ra["code"]), None)
        a_pos = "📗" if ra["in_position"] else "📕"
        b_pos = "📗" if rb and rb["in_position"] else "📕"
        output_lines.append(f"| {ra['name']} | {a_pos} | {b_pos} |")
    output_lines.append("")
    output_lines.append(f"## 今日交易信号")
    if today_signals_a or today_signals_b:
        output_lines.append("")
        if today_signals_a:
            output_lines.append(f"### 策略C (EMA12/26)")
            for n, t in today_signals_a:
                icon = "🟢买入" if t == "BUY" or t.startswith("BUY") else "🔴卖出"
                output_lines.append(f"- {n}: {icon}")
        if today_signals_b:
            output_lines.append(f"### 策略B (纯MACD)")
            for n, t in today_signals_b:
                icon = "🟢买入" if t == "BUY" or t.startswith("BUY") else "🔴卖出"
                output_lines.append(f"- {n}: {icon}")
    else:
        output_lines.append("今日无交易信号")
    output_lines.append("")
    output_lines.append("---")
    output_lines.append("")
    output_lines.append(f"> **风险提示**：以上为模拟盘回测结果，基于历史数据，不构成投资建议。")

    report_text = "\n".join(output_lines)

    # ======== 保存结果 ========
    state_file = os.path.join(WORKSPACE, "quant_backtest_state.json")
    state = {
        "last_update": today_str,
        "summary": {
            "strategy_a": {**agg_a, "day_change": day_avg_a},
            "strategy_b": {**agg_b, "day_change": day_avg_b},
            "a_wins": a_wins,
            "b_wins": b_wins,
        },
        "top5_a": [{"name": r["name"], "return_pct": r["total_return_pct"],
                     "in_position": r["in_position"], "latest_price": r["latest_price"]}
                   for r in top_a],
        "top5_b": [{"name": r["name"], "return_pct": r["total_return_pct"],
                     "in_position": r["in_position"], "latest_price": r["latest_price"]}
                   for r in top_b],
        "details_a": results_a,
        "details_b": results_b,
        "errors": errors,
    }
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)

    # 保存日报
    report_dir = os.path.join(WORKSPACE, "analysis", "backtest")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"{today_str}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"\n📁 状态已保存: {state_file}")
    print(f"📁 日报已保存: {report_path}")
    print()
    # 输出简报（供agent推送用）
    print("=====DAILY_BRIEF_BEGIN=====")
    print(f"📊 **量化模拟盘日报 {today_str}**")
    print()
    print(f"策略C(EMA12/26) {agg_a.get('avg_return',0):.2f}% | 策略B(纯MACD) {agg_b.get('avg_return',0):.2f}%")
    print(f"今日变动: C {day_avg_a:+.2f}% | B {day_avg_b:+.2f}%")
    print(f"C胜 {a_wins} : B胜 {b_wins}")
    print()
    if today_signals_a or today_signals_b:
        print("📢 **今日信号**:")
        for n, t in today_signals_a:
            icon = "🟢买入" if t == "BUY" else "🔴卖出"
            print(f"C-{n}: {icon}")
        for n, t in today_signals_b:
            icon = "🟢买入" if t == "BUY" else "🔴卖出"
            print(f"B-{n}: {icon}")
    print()
    top3_a_str = ' '.join([f'{r["name"]}{r["total_return_pct"]:+.1f}%' for r in top_a[:3]])
    top3_b_str = ' '.join([f'{r["name"]}{r["total_return_pct"]:+.1f}%' for r in top_b[:3]])
    print(f"🏆 策略C(EMA) Top3: {top3_a_str}")
    print(f"🏆 策略B(MACD) Top3: {top3_b_str}")
    print("=====DAILY_BRIEF_END=====")

if __name__ == "__main__":
    main()
