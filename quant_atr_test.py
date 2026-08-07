#!/usr/bin/env python3
"""
📊 ATR止损对比测试 — 有止损 vs 无止损
用最低价判定止损触发，动态追踪止损线
"""
import os, sys, json, time, warnings, copy
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

WORKSPACE = "/Users/duguke/.openclaw/workspace"
FETCH_DELAY = 0.2

START_CAPITAL = 1_000_000
COMMISSION = 0.0003
SLIPPAGE = 0.001
DATA_START = "2025-07-01"
TRADING_START = "2025-07-01"

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

# ---------- 技术指标 ----------
def calc_ma(series, window):
    return series.rolling(window=window).mean()

def calc_ema(series, window):
    return series.ewm(span=window, adjust=False).mean()

def calc_macd(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    return dif, dea

def calc_atr(high, low, close, window=14):
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=window).mean()

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
        time.sleep(FETCH_DELAY)
        return df
    except Exception as e:
        return None

# ---------- 策略信号 ----------
def strategy_ema_cross(df):
    """EMA12/26 金叉死叉"""
    df = df.copy()
    df["EMA12"] = calc_ema(df["close"], 12)
    df["EMA26"] = calc_ema(df["close"], 26)
    df["signal"] = 0
    df.loc[(df["EMA12"] > df["EMA26"]) & (df["EMA12"].shift(1) <= df["EMA26"].shift(1)), "signal"] = 1
    df.loc[(df["EMA12"] < df["EMA26"]) & (df["EMA12"].shift(1) >= df["EMA26"].shift(1)), "signal"] = -1
    return df

def strategy_macd_only(df):
    """纯MACD金叉死叉"""
    df = df.copy()
    dif, dea = calc_macd(df["close"])
    df["MACD_DIF"] = dif
    df["MACD_DEA"] = dea
    df["signal"] = 0
    df.loc[(df["MACD_DIF"] > df["MACD_DEA"]) & (df["MACD_DIF"].shift(1) <= df["MACD_DEA"].shift(1)), "signal"] = 1
    df.loc[(df["MACD_DIF"] < df["MACD_DEA"]) & (df["MACD_DIF"].shift(1) >= df["MACD_DEA"].shift(1)), "signal"] = -1
    return df

# ---------- 回测（含ATR止损）----------
def backtest_stock(df_strat, code, name, strategy_name, use_atr_stop=False, atr_multiplier=2.0):
    df = df_strat.copy()
    df = df[df["date"] >= TRADING_START].reset_index(drop=True)
    if len(df) < 30:
        return None

    # 计算ATR
    if use_atr_stop:
        df["ATR14"] = calc_atr(df["high"], df["low"], df["close"], 14)

    capital_per_stock = START_CAPITAL / 25
    capital = capital_per_stock
    shares = 0
    trades = []
    in_position = False
    entry_price = 0
    current_stop = 0
    max_value = capital
    min_value = capital

    for i, row in df.iterrows():
        price = row["close"]
        sig = row["signal"]

        # ATR止损触发（盘中最低价触及动态止损线）
        if use_atr_stop and in_position and current_stop > 0:
            if row["low"] < current_stop:
                # 按止损价卖出（止损价低于收盘价）
                stop_price = current_stop
                sell_value = shares * stop_price * (1 - SLIPPAGE)
                fee = sell_value * COMMISSION
                capital += (sell_value - fee)
                trades.append({"date": row["date"], "type": "SELL(ATR止损)", 
                               "price": round(stop_price, 2), "shares": shares, 
                               "value": round(sell_value - fee, 2)})
                shares = 0
                in_position = False
                current_stop = 0
                total_value = capital
                max_value = max(max_value, total_value)
                min_value = min(min_value, total_value)
                continue

        if sig == 1 and not in_position:
            fee_pre = capital * COMMISSION
            available = capital - fee_pre
            shares_buy = int(available / (price * (1 + SLIPPAGE)) / 100) * 100
            if shares_buy >= 100:
                actual_cost = shares_buy * price * (1 + SLIPPAGE)
                actual_fee = actual_cost * COMMISSION
                capital -= (actual_cost + actual_fee)
                shares = shares_buy
                in_position = True
                entry_price = price
                # 入场时设置初始止损
                if use_atr_stop and i > 0 and not pd.isna(df.iloc[i].get("ATR14", 0)):
                    current_stop = price - atr_multiplier * df.iloc[i]["ATR14"]
                trades.append({"date": row["date"], "type": "BUY", "price": price, "shares": shares_buy})

        elif sig == -1 and in_position:
            sell_value = shares * price * (1 - SLIPPAGE)
            fee = sell_value * COMMISSION
            capital += (sell_value - fee)
            trades.append({"date": row["date"], "type": "SELL", "price": price, "shares": shares,
                           "value": round(sell_value - fee, 2)})
            shares = 0
            in_position = False
            current_stop = 0

        # 动态上移止损线（追踪止盈）
        if use_atr_stop and in_position and not pd.isna(row.get("ATR14", 0)):
            new_stop = price - atr_multiplier * row["ATR14"]
            current_stop = max(current_stop, new_stop)

        total_value = capital + (shares * price) if in_position else capital
        max_value = max(max_value, total_value)
        min_value = min(min_value, total_value)

    # 期末平仓
    if in_position and len(df) > 0:
        price = df.iloc[-1]["close"]
        sell_value = shares * price * (1 - SLIPPAGE)
        fee = sell_value * COMMISSION
        capital += (sell_value - fee)
        shares = 0

    final_value = capital
    total_return = (final_value / capital_per_stock - 1) * 100
    max_drawdown = max(0, (max_value - min_value) / max_value * 100) if max_value > 0 else 0

    return {
        "code": code, "name": name, "strategy": strategy_name,
        "final": round(final_value, 2),
        "return_pct": round(total_return, 2),
        "max_dd": round(max_drawdown, 2),
        "n_trades": len([t for t in trades if t["type"] == "BUY"]),
        "atr_stops": len([t for t in trades if "ATR止损" in t["type"]]),
    }

# ============ 主流程 ============
def main():
    os.makedirs(os.path.join(WORKSPACE, "analysis"), exist_ok=True)

    print("📡 获取数据中...")
    all_data = {}
    for code, name in WATCHLIST:
        print(f"  {name}({code})...")
        df = fetch_data(code, name)
        if df is not None:
            all_data[code] = df

    print(f"\n✅ 成功获取 {len(all_data)}/{len(WATCHLIST)}")

    # 多版本：2种策略 × 3种止损（无/2x/1.5x）
    configs = [
        ("C-EMA", "EMA12/26 无止损", strategy_ema_cross, False, 0),
        ("C-EMA+ATR2", "EMA12/26 + 2xATR追踪止损", strategy_ema_cross, True, 2.0),
        ("C-EMA+ATR1.5", "EMA12/26 + 1.5xATR追踪止损", strategy_ema_cross, True, 1.5),
        ("B-MACD", "纯MACD 无止损", strategy_macd_only, False, 0),
        ("B-MACD+ATR2", "纯MACD + 2xATR追踪止损", strategy_macd_only, True, 2.0),
        ("B-MACD+ATR1.5", "纯MACD + 1.5xATR追踪止损", strategy_macd_only, True, 1.5),
    ]

    results = {}
    for short, name, sfunc, use_atr, atr_mult in configs:
        print(f"\n🔬 {short}: {name}")
        stock_results = []
        for code, sname in WATCHLIST:
            if code not in all_data:
                continue
            df = sfunc(all_data[code])
            res = backtest_stock(df, code, sname, name, use_atr, atr_mult)
            if res:
                stock_results.append(res)

        total_final = sum(r["final"] for r in stock_results)
        avg_return = np.mean([r["return_pct"] for r in stock_results])
        avg_maxdd = np.mean([r["max_dd"] for r in stock_results])
        total_trades = sum(r["n_trades"] for r in stock_results)
        total_atr_stops = sum(r["atr_stops"] for r in stock_results)
        pos_count = sum(1 for r in stock_results if r["return_pct"] > 0)

        results[short] = {
            "name": name, "final": round(total_final, 2),
            "return_pct": round((total_final / START_CAPITAL - 1) * 100, 2),
            "avg_return": round(avg_return, 2),
            "avg_maxdd": round(avg_maxdd, 2),
            "trades": total_trades, "atr_stops": total_atr_stops,
            "pos_count": pos_count, "total_count": len(stock_results),
            "stocks": stock_results,
        }
        print(f"  → 总资产: {results[short]['final']:,.0f} 收益率: {results[short]['return_pct']:+.2f}% "
              f"平均回撤: {avg_maxdd:.2f}% ATR止损: {total_atr_stops}次")

    # 对比
    print(f"\n{'='*60}")
    print("📊 ATR追踪止损效果对比")
    print(f"{'='*60}")
    for base, atr_v in [("C-EMA", "C-EMA+ATR2"), ("C-EMA", "C-EMA+ATR1.5"), 
                        ("B-MACD", "B-MACD+ATR2"), ("B-MACD", "B-MACD+ATR1.5")]:
        if base in results and atr_v in results:
            r_no = results[base]
            r_yes = results[atr_v]
            dd_red = r_no["avg_maxdd"] - r_yes["avg_maxdd"]
            ret_d = r_yes["return_pct"] - r_no["return_pct"]
            print(f"\n{results[base]['name']} → {results[atr_v]['name']}:")
            print(f"  收益率: {r_no['return_pct']:+.2f}% → {r_yes['return_pct']:+.2f}% ({ret_d:+.2f}%)")
            print(f"  平均回撤: {r_no['avg_maxdd']:.2f}% → {r_yes['avg_maxdd']:.2f}% (降{dd_red:.2f}%)")
            print(f"  ATR止损触发: {r_yes['atr_stops']}次")

    # 写入报告
    lines = []
    lines.append(f"# 📊 ATR追踪止损对比测试报告")
    lines.append(f"")
    lines.append(f"**生成时间：** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**数据区间：** {DATA_START} ~ {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"**止损规则：** 持仓期间盘中最低价触及 `close - N×ATR(14)` 时强制平仓，止损线随股价上涨动态上移")
    lines.append(f"")
    lines.append(f"## 一、六版本整体对比")
    lines.append(f"")
    lines.append("| 版本 | 策略 | 总收益率 | 平均回撤 | 交易次数 | ATR止损 | 正收益/总 |")
    lines.append("|------|------|:------:|:------:|:------:|:------:|:------:|")
    for short in ["C-EMA", "C-EMA+ATR2", "C-EMA+ATR1.5", "B-MACD", "B-MACD+ATR2", "B-MACD+ATR1.5"]:
        r = results[short]
        lines.append(f"| {short} | {r['name']} | **{r['return_pct']:+.2f}%** | {r['avg_maxdd']:.2f}% | {r['trades']} | {r['atr_stops']} | {r['pos_count']}/{r['total_count']} |")
    lines.append(f"")

    # 各股票明细
    lines.append(f"## 二、各股票明细")
    lines.append(f"")
    for sector, codes in [
        ("证券/金融", ["600030","601066","600036","601995","000987"]),
        ("半导体/TMT", ["600584","688981","002156","002413"]),
        ("新能源/锂电", ["300014","002466","601865"]),
        ("高端制造/材料", ["300285","603308","300124","601100","002318","300719","002335"]),
        ("消费/其他", ["600660","600570","605566","000157","601061","900925"]),
    ]:
        lines.append(f"### {sector}")
        lines.append(f"")
        h = "| 代码 | 名称 | C-EMA(%) | 回撤(%) | +ATR2(%) | 回撤(%) | +ATR1.5(%) | 回撤(%) | MACD(%) | 回撤(%) | +ATR2(%) | 回撤(%) | +ATR1.5(%) | 回撤(%) |"
        s = "|------|------|:------:|:------:|:------:|:------:|:------:|:------:|:------:|:------:|:------:|:------:|:------:|:------:|"
        lines.append(h)
        lines.append(s)
        for code in codes:
            name = ""
            for stk in results["C-EMA"]["stocks"]:
                if stk["code"] == code:
                    name = stk["name"]
                    break
            row = f"| {code} | {name} |"
            for short in ["C-EMA", "C-EMA+ATR2", "C-EMA+ATR1.5", "B-MACD", "B-MACD+ATR2", "B-MACD+ATR1.5"]:
                if short in results:
                    for stk in results[short]["stocks"]:
                        if stk["code"] == code:
                            rp = stk["return_pct"]
                            dd = stk["max_dd"]
                            emoji = " 🟢" if rp > 0 else " 🔴" if rp < -10 else ""
                            row += f" {rp:+.2f}{emoji} | {dd:.2f} |"
                            break
            lines.append(row)
        lines.append(f"")

    # 结论
    lines.append(f"## 三、结论")
    lines.append(f"")
    ranked = sorted(results.items(), key=lambda x: x[1]["return_pct"], reverse=True)
    lines.append(f"**收益率排名：**")
    for i, (k, r) in enumerate(ranked):
        tag = "👑" if i == 0 else ""
        lines.append(f"{i+1}. **{k} ({r['name']})**: {r['return_pct']:+.2f}% {tag}")
    lines.append(f"")

    # ATR止损判断
    for base, atr_v in [("C-EMA", "C-EMA+ATR2"), ("C-EMA", "C-EMA+ATR1.5"),
                        ("B-MACD", "B-MACD+ATR2"), ("B-MACD", "B-MACD+ATR1.5")]:
        if base in results and atr_v in results:
            r_no = results[base]
            r_yes = results[atr_v]
            dd_red = r_no["avg_maxdd"] - r_yes["avg_maxdd"]
            ret_d = r_yes["return_pct"] - r_no["return_pct"]
            if dd_red > 2 and ret_d > -2:
                lines.append(f"- ✅ **{atr_v}** 有效：回撤降低{dd_red:.1f}%，收益仅微降{abs(ret_d):.1f}%")
            elif dd_red > 0 and ret_d > 0:
                lines.append(f"- ✅ **{atr_v}** 同时降低回撤和提高收益！")
            elif ret_d < -5:
                lines.append(f"- ⚠️ **{atr_v}** 收益损失太大（{ret_d:+.1f}%），建议放宽参数")
            else:
                lines.append(f"- 🔄 **{atr_v}** 效果一般：回撤{d:+.1f}% vs 收益{ret_d:+.1f}%")
    
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"*自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    out_path = os.path.join(WORKSPACE, "analysis", "quant_atr_stop_test.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n✅ 报告已保存: {out_path}")
    
    print("\n=====BRIEF_BEGIN=====")
    print("📊 **ATR追踪止损对比测试**")
    print()
    for short in ["C-EMA", "C-EMA+ATR2", "C-EMA+ATR1.5", "B-MACD", "B-MACD+ATR2", "B-MACD+ATR1.5"]:
        r = results[short]
        print(f"{short} ({r['name']}): {r['return_pct']:+.2f}% | 回撤 {r['avg_maxdd']:.2f}% | ATR止损{r['atr_stops']}次")
    print()
    for base, atr_v in [("C-EMA", "C-EMA+ATR2"), ("C-EMA", "C-EMA+ATR1.5"),
                        ("B-MACD", "B-MACD+ATR2"), ("B-MACD", "B-MACD+ATR1.5")]:
        if base in results and atr_v in results:
            r_no = results[base]
            r_yes = results[atr_v]
            dd_red = r_no["avg_maxdd"] - r_yes["avg_maxdd"]
            ret_d = r_yes["return_pct"] - r_no["return_pct"]
            print(f"{base} → {atr_v}: 收益{ret_d:+.2f}% 回撤↓{dd_red:.1f}%")
    print("=====BRIEF_END=====")

if __name__ == "__main__":
    main()
