#!/usr/bin/env python3
"""
中联重科(000157) 周期股专项优化模拟盘
- 3年长周期回测 (2023-07 ~ 2026-07)
- 8策略对比：EMA/MACD + 周期股专用改良
- 详细持仓记录、交易信号、净值曲线
"""
import os, sys, json, time, warnings
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime

warnings.filterwarnings("ignore")

WORKSPACE = "/Users/duguke/.openclaw/workspace"

# ======== 配置 ========
CODE = "000157"
NAME = "中联重科"
START_CAPITAL = 100_000  # 单股10万模拟资金
COMMISSION = 0.0003
SLIPPAGE = 0.001

# 数据获取 - 3年长周期
def fetch_data():
    try:
        df = ak.stock_zh_a_daily(symbol='sz000157',
            start_date='20230701',
            end_date=datetime.now().strftime('%Y%m%d'),
            adjust='qfq')
        if df is None or df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        df["code"] = CODE
        df["name"] = NAME
        return df
    except Exception as e:
        print(f"❌ 数据获取失败: {e}")
        return None

# ---------- 技术指标 ----------
def calc_ema(series, w): return series.ewm(span=w, adjust=False).mean()
def calc_sma(series, w): return series.rolling(w).mean()
def calc_macd(close, f=12, s=26, sig=9):
    ef = close.ewm(span=f, adjust=False).mean()
    es = close.ewm(span=s, adjust=False).mean()
    dif = ef - es
    dea = dif.ewm(span=sig, adjust=False).mean()
    return dif, dea

# ---------- 8种策略信号 ----------
def stratey_c_ema(df):
    """#1 基准策略C: EMA12/26金叉死叉"""
    df = df.copy()
    df["EMA12"] = calc_ema(df["close"], 12)
    df["EMA26"] = calc_ema(df["close"], 26)
    df["sig"] = 0
    df.loc[(df["EMA12"] > df["EMA26"]) & (df["EMA12"].shift(1) <= df["EMA26"].shift(1)), "sig"] = 1
    df.loc[(df["EMA12"] < df["EMA26"]) & (df["EMA12"].shift(1) >= df["EMA26"].shift(1)), "sig"] = -1
    return df, "EMA12/26 (基准C)"

def strategy_b_macd(df):
    """#2 基准策略B: 纯MACD金叉死叉"""
    df = df.copy()
    dif, dea = calc_macd(df["close"])
    df["sig"] = 0
    df.loc[(dif > dea) & (dif.shift(1) <= dea.shift(1)), "sig"] = 1
    df.loc[(dif < dea) & (dif.shift(1) >= dea.shift(1)), "sig"] = -1
    return df, "纯MACD (基准B)"

def strategy_bollinger(df):
    """#3 布林带均值回归: 跌破下轨买, 突破上轨卖"""
    df = df.copy()
    df["SMA20"] = calc_sma(df["close"], 20)
    df["std20"] = df["close"].rolling(20).std()
    df["upper"] = df["SMA20"] + 2 * df["std20"]
    df["lower"] = df["SMA20"] - 2 * df["std20"]
    df["sig"] = 0
    # 收盘价跌破下轨买入（回归预期）
    df.loc[(df["close"] < df["lower"]) & (df["close"].shift(1) >= df["lower"].shift(1)), "sig"] = 1
    # 收盘价突破上轨卖出（回归预期）
    df.loc[(df["close"] > df["upper"]) & (df["close"].shift(1) <= df["upper"].shift(1)), "sig"] = -1
    return df, "布林带均值回归"

def strategy_double_ma(df):
    """#4 周期股专用: MA60/120 长周期双均线"""
    df = df.copy()
    df["MA60"] = calc_sma(df["close"], 60)
    df["MA120"] = calc_sma(df["close"], 120)
    df["sig"] = 0
    df.loc[(df["MA60"] > df["MA120"]) & (df["MA60"].shift(1) <= df["MA120"].shift(1)), "sig"] = 1
    df.loc[(df["MA60"] < df["MA120"]) & (df["MA60"].shift(1) >= df["MA120"].shift(1)), "sig"] = -1
    return df, "MA60/120长周期"

def strategy_ema_vol(df):
    """#5 EMA12/26 + 成交量确认: 金叉必须有放量"""
    df = df.copy()
    df["EMA12"] = calc_ema(df["close"], 12)
    df["EMA26"] = calc_ema(df["close"], 26)
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["sig"] = 0
    # 金叉 + 成交量 > MA20
    df.loc[(df["EMA12"] > df["EMA26"]) & (df["EMA12"].shift(1) <= df["EMA26"].shift(1))
           & (df["volume"] > df["vol_ma20"]), "sig"] = 1
    df.loc[(df["EMA12"] < df["EMA26"]) & (df["EMA12"].shift(1) >= df["EMA26"].shift(1)), "sig"] = -1
    return df, "EMA+成交量确认"

def strategy_macd_rsi(df):
    """#6 MACD+RSI底部过滤: 只在RSI<50时金叉买入（避免高位追涨）"""
    df = df.copy()
    dif, dea = calc_macd(df["close"])
    # RSI
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))
    # 金叉 + RSI < 50 (底部/中位区域)
    df["sig"] = 0
    df.loc[(dif > dea) & (dif.shift(1) <= dea.shift(1)) & (df["RSI"] < 50), "sig"] = 1
    df.loc[(dif < dea) & (dif.shift(1) >= dea.shift(1)), "sig"] = -1
    return df, "MACD+RSI<50"

def strategy_wr(df):
    """#7 WR威廉指标: WR>80超卖买, WR<20超买卖"""
    df = df.copy()
    period = 14
    high14 = df["high"].rolling(period).max()
    low14 = df["low"].rolling(period).min()
    df["WR"] = (high14 - df["close"]) / (high14 - low14) * -100
    df["sig"] = 0
    # WR从>80区域向下突破80（超卖区回升）买入
    df.loc[(df["WR"] <= -80) & (df["WR"].shift(1) > -80), "sig"] = 1
    # WR从<20区域向上突破20（超买区回落）卖出
    df.loc[(df["WR"] >= -20) & (df["WR"].shift(1) < -20), "sig"] = -1
    return df, "威廉WR超买超卖"

def strategy_combined_best(df):
    """#8 综合最优: MACD金叉+EMA12>26（确认趋势）+RSI<60（避免极端高位）
       卖出: MACD死叉 || 跌破EMA26"""
    df = df.copy()
    dif, dea = calc_macd(df["close"])
    df["EMA12"] = calc_ema(df["close"], 12)
    df["EMA26"] = calc_ema(df["close"], 26)
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))
    df["sig"] = 0
    # 买入: MACD金叉 + EMA12 > EMA26 + RSI < 60
    buy_cond = (dif > dea) & (dif.shift(1) <= dea.shift(1)) & (df["EMA12"] > df["EMA26"]) & (df["RSI"] < 60)
    df.loc[buy_cond, "sig"] = 1
    # 卖出: MACD死叉 || 跌破EMA26
    sell_cond1 = (dif < dea) & (dif.shift(1) >= dea.shift(1))
    sell_cond2 = df["close"] < df["EMA26"]
    df.loc[sell_cond1 | sell_cond2, "sig"] = -1
    return df, "综合最优(EMA+MACD+RSI)"

# ---------- 回测核心 ----------
def backtest(df_strat, strategy_name):
    df = df_strat.copy()
    if len(df) < 60:
        return None
    
    capital = START_CAPITAL
    shares = 0
    trades = []
    in_position = False
    entry_price = 0
    entry_date = None
    daily_values = []
    buy_signals = 0
    sell_signals = 0
    
    for i, row in df.iterrows():
        price = row["close"]
        dt = row["date"]
        sig = row["sig"]
        
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
                buy_signals += 1
                trades.append({"date": str(dt.date()), "type": "BUY", "price": round(price, 2),
                               "shares": shares_buy, "value": round(actual_cost + actual_fee, 2)})
        
        # 卖出
        elif sig == -1 and in_position:
            sell_value = shares * price * (1 - SLIPPAGE)
            fee = sell_value * COMMISSION
            net_sell = sell_value - fee
            cost_basis = shares * entry_price * (1 + SLIPPAGE) + (trades[-1]["value"] - shares * entry_price * (1 + SLIPPAGE) if trades else 0)
            pnl_value = net_sell - shares * entry_price * (1 + SLIPPAGE)
            pnl_pct = (pnl_value / (shares * entry_price)) * 100
            capital += net_sell
            sell_signals += 1
            trades.append({"date": str(dt.date()), "type": "SELL", "price": round(price, 2),
                           "shares": shares, "value": round(net_sell, 2),
                           "pnl": round(pnl_value, 2), "pnl_pct": round(pnl_pct, 2),
                           "hold_days": (dt - entry_date).days if entry_date else 0})
            shares = 0
            in_position = False
        
        total_value = capital + (shares * price) if in_position else capital
        daily_values.append({"date": str(dt.date()), "v": round(total_value, 2), "close": round(price, 2)})
    
    # 期末平仓
    if in_position and len(df) > 0:
        last_row = df.iloc[-1]
        price = last_row["close"]
        sell_value = shares * price * (1 - SLIPPAGE)
        fee = sell_value * COMMISSION
        capital += (sell_value - fee)
        trades.append({"date": str(last_row["date"].date()), "type": "SELL(期末)", "price": round(price, 2),
                       "shares": shares, "value": round(sell_value - fee, 2)})
        shares = 0
    
    final_value = capital
    total_return = (final_value - START_CAPITAL) / START_CAPITAL * 100
    
    # 统计
    sell_trades = [t for t in trades if t["type"] == "SELL"]
    win_trades = [t for t in sell_trades if t.get("pnl", 0) > 0]
    loss_trades = [t for t in sell_trades if t.get("pnl", 0) < 0]
    total_sells = len(sell_trades)
    win_rate = (len(win_trades) / total_sells * 100) if total_sells > 0 else 0
    
    # 回撤
    daily_df = pd.DataFrame(daily_values)
    daily_df["peak"] = daily_df["v"].cummax()
    daily_df["dd"] = (daily_df["v"] - daily_df["peak"]) / daily_df["peak"] * 100
    max_dd = daily_df["dd"].min()
    
    trading_days = len(df)
    years = trading_days / 252
    annual_return = ((final_value / START_CAPITAL) ** (1 / years) - 1) * 100 if years > 0 and final_value > 0 else 0
    
    # 夏普比（简化）
    daily_returns = daily_df["v"].pct_change().dropna()
    sharpe = (daily_returns.mean() / daily_returns.std() * 252**0.5) if daily_returns.std() > 0 else 0
    
    # 盈亏比
    avg_win = np.mean([t["pnl"] for t in win_trades]) if win_trades else 0
    avg_loss = abs(np.mean([t["pnl"] for t in loss_trades])) if loss_trades else 1
    profit_factor = avg_win / avg_loss if avg_loss > 0 else 0
    
    # 最大连续亏损
    loss_streak = 0
    max_loss_streak = 0
    for t in sell_trades:
        if t.get("pnl", 0) < 0:
            loss_streak += 1
            max_loss_streak = max(max_loss_streak, loss_streak)
        else:
            loss_streak = 0
    
    # 平均持仓天数
    hold_days = [t.get("hold_days", 0) for t in sell_trades if "hold_days" in t]
    avg_hold = round(np.mean(hold_days), 1) if hold_days else 0
    
    # 胜率过滤后的收益（剔除手续费）
    gross_pnl = sum(t.get("pnl", 0) for t in sell_trades)
    
    return {
        "strategy": strategy_name,
        "total_return_pct": round(total_return, 2),
        "annual_return_pct": round(annual_return, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "total_signals": buy_signals + sell_signals,
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
        "total_trades": total_sells,
        "win_trades": len(win_trades),
        "loss_trades": len(loss_trades),
        "win_rate_pct": round(win_rate, 2),
        "sharpe_ratio": round(sharpe, 3),
        "profit_factor": round(profit_factor, 2),
        "avg_hold_days": avg_hold,
        "max_loss_streak": max_loss_streak,
        "final_capital": round(final_value, 2),
        "profit_loss": round(final_value - START_CAPITAL, 2),
        "trades_detail": trades,
        "daily_curve": daily_values,
    }


def main():
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"{'='*70}")
    print(f"  🏗️  中联重科(000157) 周期股专项优化模拟盘")
    print(f"  📅 {today_str}  |  资金: ¥{START_CAPITAL:,.0f}")
    print(f"{'='*70}")
    
    # 获取数据
    df = fetch_data()
    if df is None:
        print("❌ 数据获取失败")
        return
    
    print(f"\n📊 获取数据: {len(df)}条 ({df['date'].min().date()} ~ {df['date'].max().date()})")
    print(f"   最新价: ¥{df['close'].iloc[-1]:.2f}")
    print(f"   区间振幅: {(df['high'].max()-df['low'].min())/df['close'].iloc[0]*100:.1f}%")
    print()
    
    # 运行8个策略
    strategies = [
        stratey_c_ema,
        strategy_b_macd,
        strategy_bollinger,
        strategy_double_ma,
        strategy_ema_vol,
        strategy_macd_rsi,
        strategy_wr,
        strategy_combined_best,
    ]
    
    results = []
    for fn in strategies:
        try:
            df_sig, name = fn(df)
            r = backtest(df_sig, name)
            if r:
                results.append(r)
                emoji = "✅" if r["total_return_pct"] > 0 else "❌"
                print(f"  {emoji} {name:<30s} 收益:{r['total_return_pct']:>+8.2f}%  年化:{r['annual_return_pct']:>+6.2f}%  "
                      f"回撤:{r['max_drawdown_pct']:>6.2f}%  胜率:{r['win_rate_pct']:>5.1f}%  夏普:{r['sharpe_ratio']:>5.3f}")
        except Exception as e:
            print(f"  ⚠️ {fn.__name__} 失败: {e}")
    
    # 排序
    results.sort(key=lambda r: r["total_return_pct"], reverse=True)
    
    # ======== 详细报告 ========
    print(f"\n{'='*70}")
    print(f"  📋 完整报告")
    print(f"{'='*70}\n")
    
    print(f"## 🥇 收益排名")
    print(f"| 排名 | 策略 | 总收益 | 年化收益 | 最大回撤 | 夏普比 | 胜率 | 交易次数 |")
    print(f"|:---:|:----|:-----:|:-------:|:-------:|:-----:|:---:|:-------:|")
    for i, r in enumerate(results, 1):
        medal = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣"][min(i-1,7)]
        print(f"| {medal} | {r['strategy']:<28s} | {r['total_return_pct']:>+7.2f}% | {r['annual_return_pct']:>+6.2f}% | "
              f"{r['max_drawdown_pct']:>6.2f}% | {r['sharpe_ratio']:>5.3f} | {r['win_rate_pct']:>5.1f}% | {r['total_trades']:>3d}次 |")
    
    print(f"\n## 📊 详细指标对比")
    headers = ["策略", "总收益%", "年化%", "回撤%", "夏普", "胜率%", "交易", "盈亏比", "平均持仓", "最大连亏"]
    print(f"| {' | '.join(h.center(14) for h in headers)} |")
    print(f"|{'|'.join('-'*16 for _ in headers)}|")
    for r in results:
        prof = r.get("profit_factor", 0)
        ahd = r.get("avg_hold_days", 0)
        mls = r.get("max_loss_streak", 0)
        print(f"| {r['strategy']:>28s} | {r['total_return_pct']:>+7.2f}% | {r['annual_return_pct']:>+6.2f}% | "
              f"{r['max_drawdown_pct']:>7.2f}% | {r['sharpe_ratio']:>5.3f} | {r['win_rate_pct']:>5.1f}% | "
              f"{r['total_trades']:>3d} | {prof:>5.2f} | {ahd:>4.1f}天 | {mls:>3d}次 |")
    
    # ======== 最优策略持仓详情 ========
    best = results[0]
    print(f"\n## 🏆 最优策略: {best['strategy']}")
    print(f"\n### 总体")
    print(f"- 初始资金: ¥{START_CAPITAL:,.0f}")
    print(f"- 最终资产: ¥{best['final_capital']:,.2f}")
    print(f"- 总收益: {best['total_return_pct']:+.2f}% (¥{best['profit_loss']:+,.2f})")
    print(f"- 年化收益: {best['annual_return_pct']:+.2f}%")
    print(f"- 最大回撤: {best['max_drawdown_pct']:.2f}%")
    print(f"- 夏普比: {best['sharpe_ratio']}")
    print(f"- 盈亏比: {best.get('profit_factor', 0):.2f}")
    print(f"- 总信号: {best['total_signals']}次 (买入{best['buy_signals']}/卖出{best['sell_signals']})")
    print(f"- 胜率: {best['win_rate_pct']:.1f}% ({best['win_trades']}胜/{best['loss_trades']}负)")
    print(f"- 最大连亏: {best['max_loss_streak']}次")
    print(f"- 平均持仓: {best['avg_hold_days']}天")
    
    print(f"\n### 交易记录")
    print(f"| # | 日期 | 方向 | 价格 | 数量 | 金额 | 盈亏 | 盈亏% | 持仓天数 |")
    print(f"|---|------|:---:|:---:|:---:|-----:|:----:|:-----:|:-------:|")
    trade_num = 0
    for t in best["trades_detail"]:
        if t["type"] == "SELL":
            trade_num += 1
            pnl_str = f"¥{t.get('pnl',0):+,.0f}" if "pnl" in t else "-"
            pnl_pct_str = f"{t.get('pnl_pct',0):+.1f}%" if "pnl_pct" in t else "-"
            hold_str = f"{t.get('hold_days','-')}天" if "hold_days" in t else "-"
            print(f"| {trade_num:>2d} | {t['date']} | 🔴卖 | ¥{t['price']:>6.2f} | {t['shares']:>4d} | ¥{t['value']:>8,.0f} | {pnl_str:>8s} | {pnl_pct_str:>6s} | {hold_str:>5s} |")
    
    for t in best["trades_detail"]:
        if t["type"] == "BUY":
            print(f"|    | {t['date']} | 🟢买 | ¥{t['price']:>6.2f} | {t['shares']:>4d} | ¥{t['value']:>8,.0f} | - | - | - |")
    
    # ======== 第二优策略持仓 ========
    if len(results) >= 2:
        second = results[1]
        print(f"\n## 🥈 次优策略: {second['strategy']}")
        print(f"   总收益: {second['total_return_pct']:+.2f}% | 回撤: {second['max_drawdown_pct']:.2f}% | 夏普: {second['sharpe_ratio']}")
    
    # ======== 策略C和B详细战绩 ========
    print(f"\n## 🔍 基准策略详细回顾")
    for r in results:
        if "EMA12/26" in r["strategy"] or "基准B" in r["strategy"]:
            print(f"\n### {r['strategy']}")
            print(f"总收益: {r['total_return_pct']:+.2f}% | 年化: {r['annual_return_pct']:+.2f}% | 回撤: {r['max_drawdown_pct']:.2f}%")
            print(f"胜率: {r['win_rate_pct']:.1f}% | 交易: {r['total_trades']}次 | 夏普: {r['sharpe_ratio']}")
    
    # ======== 净值走势采样 ========
    best_curve = best["daily_curve"]
    sample_step = max(1, len(best_curve) // 50)
    print(f"\n## 📈 最优策略净值曲线(采样)")
    print(f"日期         净值      涨跌幅")
    for c in best_curve[::sample_step]:
        nav = c["v"] / START_CAPITAL
        print(f"{c['date']}  ¥{c['v']:>8,.0f}  {nav:>5.3f}")
    # 最后一点
    last_c = best_curve[-1]
    nav = last_c["v"] / START_CAPITAL
    print(f"{last_c['date']}  ¥{last_c['v']:>8,.0f}  {nav:>5.3f}")
    
    # ======== 保存结果 ========
    report = {
        "stock": {"code": CODE, "name": NAME},
        "date": today_str,
        "data_range": f"{df['date'].min().date()} ~ {df['date'].max().date()}",
        "data_count": len(df),
        "latest_price": round(df["close"].iloc[-1], 2),
        "capital": START_CAPITAL,
        "results": results,
    }
    
    report_path = os.path.join(WORKSPACE, "analysis", f"zhonglian_optimize_{today_str}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    
    # 文本报告
    md_lines = []
    md_lines.append(f"# 中联重科(000157) 周期股优化模拟盘报告")
    md_lines.append(f"")
    md_lines.append(f"**生成时间:** {today_str}")
    md_lines.append(f"**数据区间:** {df['date'].min().date()} ~ {df['date'].max().date()} (3年)")
    md_lines.append(f"**最新价:** ¥{df['close'].iloc[-1]:.2f}")
    md_lines.append(f"**模拟资金:** ¥{START_CAPITAL:,.0f}/股")
    md_lines.append(f"")
    md_lines.append(f"## 8策略收益排名")
    md_lines.append(f"")
    md_lines.append(f"| 排名 | 策略 | 总收益 | 年化 | 回撤 | 夏普 | 胜率 | 交易 | 盈亏比 |")
    md_lines.append(f"|:---:|:----|:-----:|:----:|:----:|:----:|:----:|:----:|:-----:|")
    for i, r in enumerate(results, 1):
        medal = ["🥇","🥈","🥉","4","5","6","7","8"][min(i-1,7)]
        md_lines.append(f"| {medal} | {r['strategy']} | {r['total_return_pct']:+.2f}% | {r['annual_return_pct']:+.2f}% | "
                      f"{r['max_drawdown_pct']:.2f}% | {r['sharpe_ratio']} | {r['win_rate_pct']:.1f}% | "
                      f"{r['total_trades']} | {r.get('profit_factor',0):.2f} |")
    md_lines.append("")
    md_lines.append(f"## 最优策略交易记录: {best['strategy']}")
    md_lines.append(f"")
    md_lines.append(f"- 总收益: {best['total_return_pct']:+.2f}%")
    md_lines.append(f"- 年化: {best['annual_return_pct']:+.2f}%")
    md_lines.append(f"- 最大回撤: {best['max_drawdown_pct']:.2f}%")
    md_lines.append(f"- 夏普比: {best['sharpe_ratio']}")
    md_lines.append(f"- 胜率: {best['win_rate_pct']:.1f}% ({best['win_trades']}/{best['total_trades']})")
    md_lines.append(f"- 盈亏比: {best.get('profit_factor',0):.2f}")
    md_lines.append(f"- 平均持仓: {best['avg_hold_days']}天")
    md_lines.append(f"- 最大连亏: {best['max_loss_streak']}次")
    md_lines.append(f"- 总信号: {best['total_signals']}次")
    md_lines.append(f"")
    md_lines.append(f"| # | 日期 | 方向 | 价格 | 盈亏 | 盈亏% | 持仓 |")
    md_lines.append(f"|---|------|:---:|:----:|:----:|:-----:|:----:|")
    tn = 0
    for t in best["trades_detail"]:
        if t["type"] == "SELL":
            tn += 1
            pnl = f"¥{t.get('pnl',0):+.0f}" if "pnl" in t else "-"
            pct = f"{t.get('pnl_pct',0):+.1f}%" if "pnl_pct" in t else "-"
            hold = f"{t.get('hold_days','-')}天" if "hold_days" in t else "-"
            md_lines.append(f"| {tn} | {t['date']} | 🔴卖 | ¥{t['price']} | {pnl} | {pct} | {hold} |")
    for t in best["trades_detail"]:
        if t["type"] == "BUY":
            md_lines.append(f"| | {t['date']} | 🟢买 | ¥{t['price']} | - | - | - |")
    md_lines.append("")
    
    # 基准对比
    base_c = None
    base_b = None
    for r in results:
        if "EMA12/26 (基准C)" in r["strategy"]:
            base_c = r
        if "纯MACD (基准B)" in r["strategy"]:
            base_b = r
    
    if base_c and base_b:
        md_lines.append(f"## 基准策略对比")
        md_lines.append(f"")
        md_lines.append(f"| 指标 | 策略C(基准) | 策略B(基准) | 最优({best['strategy']}) |")
        md_lines.append(f"|:----|:----------:|:----------:|:-------------------:|")
        md_lines.append(f"| 总收益 | {base_c['total_return_pct']:+.2f}% | {base_b['total_return_pct']:+.2f}% | **{best['total_return_pct']:+.2f}%** |")
        md_lines.append(f"| 回撤 | {base_c['max_drawdown_pct']:.2f}% | {base_b['max_drawdown_pct']:.2f}% | **{best['max_drawdown_pct']:.2f}%** |")
        md_lines.append(f"| 夏普 | {base_c['sharpe_ratio']} | {base_b['sharpe_ratio']} | **{best['sharpe_ratio']}** |")
        md_lines.append(f"| 胜率 | {base_c['win_rate_pct']:.1f}% | {base_b['win_rate_pct']:.1f}% | {best['win_rate_pct']:.1f}% |")
        md_lines.append(f"| 交易 | {base_c['total_trades']}次 | {base_b['total_trades']}次 | {best['total_trades']}次 |")
    
    md_lines.append("")
    md_lines.append(f"## 分析结论")
    md_lines.append(f"")
    
    # 自动分析
    if best["total_return_pct"] > max(base_c["total_return_pct"], base_b["total_return_pct"]):
        md_lines.append(f"✅ **{best['strategy']} 在周期股上显著优于基准策略**")
    
    if best["max_drawdown_pct"] < min(base_c["max_drawdown_pct"], base_b["max_drawdown_pct"]):
        md_lines.append(f"✅ **回撤控制优于基准：从{max(base_c['max_drawdown_pct'],base_b['max_drawdown_pct']):.1f}%降到{best['max_drawdown_pct']:.1f}%**")
    
    # 最佳适应周期
    # 查看最优策略的买入点 - 是否都在低位区域
    buy_prices = [t["price"] for t in best["trades_detail"] if t["type"] == "BUY"]
    all_prices = df["close"].values
    pct25 = np.percentile(all_prices, 25)
    pct75 = np.percentile(all_prices, 75)
    low_buys = sum(1 for p in buy_prices if p < pct25)
    md_lines.append(f"- 买入价位于25分位({pct25:.2f})以下: {low_buys}/{len(buy_prices)}次")
    md_lines.append(f"- 3年股价25分位: ¥{pct25:.2f}, 75分位: ¥{pct75:.2f}")
    md_lines.append(f"- 当前价: ¥{df['close'].iloc[-1]:.2f} (位于{int((df['close'].iloc[-1] < pct25)+(df['close'].iloc[-1] < pct75))}/3分位)")
    
    md_text = "\n".join(md_lines)
    md_report_path = os.path.join(WORKSPACE, "analysis", f"zhonglian_optimize_{today_str}.md")
    with open(md_report_path, "w", encoding="utf-8") as f:
        f.write(md_text)
    
    print(f"\n📁 完整报告已保存:")
    print(f"  JSON: {report_path}")
    print(f"  Markdown: {md_report_path}")
    
    # 输出交互简报
    print(f"\n{'='*70}")
    print(f"  📋 简报")
    print(f"{'='*70}")
    print(f"\n🏗️ **中联重科(000157) 周期股优化模拟盘**")
    print(f"📅 数据: 3年 | 💰 资金: ¥{START_CAPITAL:,.0f}")
    print(f"\n**8策略收益排名:**")
    for i, r in enumerate(results, 1):
        medal = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣"][min(i-1,7)]
        print(f"  {medal} {r['strategy']:<30s}  {r['total_return_pct']:>+7.2f}%  (回撤{r['max_drawdown_pct']:>5.1f}%  夏普{r['sharpe_ratio']})")
    print(f"\n**最优推荐:** {best['strategy']}")
    print(f"  收益{best['total_return_pct']:+.2f}% | 回撤{best['max_drawdown_pct']:.1f}% | 夏普{best['sharpe_ratio']}")
    print(f"  胜率{best['win_rate_pct']:.1f}% | 交易{best['total_trades']}次 | 盈亏比{best.get('profit_factor',0):.2f}")
    
    if base_c and base_b:
        print(f"\n**基准对比:**")
        base_ret = [base_c['total_return_pct'], base_b['total_return_pct']]
        improve = best['total_return_pct'] - max(base_ret)
        print(f"  策略C: {base_c['total_return_pct']:+.2f}% | 策略B: {base_b['total_return_pct']:+.2f}%")
        if improve > 0:
            print(f"  优化提升: {improve:+.2f}% 🎯")
    
    # 存储路径
    print(f"\n📁 完整报告: `analysis/zhonglian_optimize_{today_str}.md`")
    print(f"\n=====ZHONGLIAN_BRIEF_END=====")


if __name__ == "__main__":
    main()
