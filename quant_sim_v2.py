#!/usr/bin/env python3
"""
📊 量化模拟盘对比 V2 — 四策略 × 整年回测
策略A: MA金叉死叉 (原版, 作为baseline)
策略B: 纯MACD金叉死叉 (去掉RSI限制)
策略C: EMA12/26金叉死叉 (EMA替换MA)
策略D: KDJ金叉死叉 (新增指标)
"""
import os, sys, json, time, warnings
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
DATA_START_DATE = "2025-07-01"
TRADING_START_DATE = "2025-07-01"

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

OUTPUT_FILE = os.path.join(WORKSPACE, "analysis", "quant_simulation_v2.md")

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

def calc_kdj(high, low, close, n=9, k_smooth=3, d_smooth=3):
    low_n = low.rolling(window=n).min()
    high_n = high.rolling(window=n).max()
    rsv = (close - low_n) / (high_n - low_n) * 100
    k = rsv.ewm(span=k_smooth, adjust=False).mean()
    d = k.ewm(span=d_smooth, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j

# ---------- 数据获取 ----------
def fetch_data(code, name):
    try:
        if code.startswith('9'):  # B股
            if code.startswith('900'):
                symbol = f'sh{code}'
            else:
                symbol = f'sz{code}'
        elif code.startswith('6') or code.startswith('9'):
            symbol = f'sh{code}'
        else:
            symbol = f'sz{code}'
            
        start_dt = DATA_START_DATE.replace("-", "")
        df = ak.stock_zh_a_daily(symbol=symbol,
            start_date=start_dt,
            end_date=datetime.now().strftime("%Y%m%d"),
            adjust="qfq")
        if df is None or df.empty:
            print(f"  ⚠️ {name}({code}) 无数据")
            return None
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        df["code"] = code
        df["name"] = name
        time.sleep(FETCH_DELAY)
        return df
    except Exception as e:
        print(f"  ❌ {name}({code}) 获取失败: {e}")
        return None

# ---------- 策略信号 ----------
def strategy_a_ma5_20(df):
    """策略A: MA5/20 金叉死叉 (原版baseline)"""
    df = df.copy()
    df["MA5"] = calc_ma(df["close"], 5)
    df["MA20"] = calc_ma(df["close"], 20)
    df["signal"] = 0
    df.loc[(df["MA5"] > df["MA20"]) & (df["MA5"].shift(1) <= df["MA20"].shift(1)), "signal"] = 1
    df.loc[(df["MA5"] < df["MA20"]) & (df["MA5"].shift(1) >= df["MA20"].shift(1)), "signal"] = -1
    return df

def strategy_b_macd_only(df):
    """策略B: 纯MACD金叉死叉 (无RSI过滤)"""
    df = df.copy()
    dif, dea = calc_macd(df["close"])
    df["MACD_DIF"] = dif
    df["MACD_DEA"] = dea
    df["signal"] = 0
    df.loc[(df["MACD_DIF"] > df["MACD_DEA"]) & (df["MACD_DIF"].shift(1) <= df["MACD_DEA"].shift(1)), "signal"] = 1
    df.loc[(df["MACD_DIF"] < df["MACD_DEA"]) & (df["MACD_DIF"].shift(1) >= df["MACD_DEA"].shift(1)), "signal"] = -1
    return df

def strategy_c_ema_cross(df):
    """策略C: EMA12/26 金叉死叉"""
    df = df.copy()
    df["EMA12"] = calc_ema(df["close"], 12)
    df["EMA26"] = calc_ema(df["close"], 26)
    df["signal"] = 0
    df.loc[(df["EMA12"] > df["EMA26"]) & (df["EMA12"].shift(1) <= df["EMA26"].shift(1)), "signal"] = 1
    df.loc[(df["EMA12"] < df["EMA26"]) & (df["EMA12"].shift(1) >= df["EMA26"].shift(1)), "signal"] = -1
    return df

def strategy_d_kdj_cross(df):
    """策略D: KDJ金叉 + J<100过滤超买"""
    df = df.copy()
    k, d, j = calc_kdj(df["high"], df["low"], df["close"])
    df["K"] = k
    df["D"] = d
    df["J"] = j
    df["signal"] = 0
    buy_cond = (df["K"] > df["D"]) & (df["K"].shift(1) <= df["D"].shift(1)) & (df["J"] < 100)
    sell_cond = (df["K"] < df["D"]) & (df["K"].shift(1) >= df["D"].shift(1))
    df.loc[buy_cond, "signal"] = 1
    df.loc[sell_cond, "signal"] = -1
    return df

# ---------- 回测 ----------
def backtest_stock(df_strat, strategy_name, label):
    df = df_strat.copy()
    df = df[df["date"] >= TRADING_START_DATE].reset_index(drop=True)
    if len(df) < 30:
        return None

    capital_per_stock = START_CAPITAL / 25
    capital = capital_per_stock
    shares = 0
    trades = []
    in_position = False
    entry_price = 0
    max_value = capital
    min_value = capital
    
    for i, row in df.iterrows():
        price = row["close"]
        sig = row["signal"]

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
                
        elif sig == -1 and in_position:
            sell_value = shares * price * (1 - SLIPPAGE)
            fee = sell_value * COMMISSION
            capital += (sell_value - fee)
            shares = 0
            in_position = False

        total_value = capital + (shares * price) if in_position else capital
        max_value = max(max_value, total_value)
        min_value = min(min_value, total_value)

    # 最终
    final_value = capital + (shares * df.iloc[-1]["close"]) if in_position else capital
    total_return = (final_value / capital_per_stock - 1) * 100
    max_drawdown = max(0, (max_value - min_value) / max_value * 100) if max_value > 0 else 0
    return {
        "final": final_value,
        "return_pct": round(total_return, 2),
        "max_dd": round(max_drawdown, 2),
        "n_trades": len([t for t in trades if t.get("type") == "BUY"]),
    }

# ============ 主流程 ============
def main():
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    
    # 获取所有股票数据
    print("📡 获取数据中...")
    all_data = {}
    for code, name in WATCHLIST:
        print(f"  {name}({code})...")
        df = fetch_data(code, name)
        if df is not None:
            all_data[code] = df
    
    print(f"\n✅ 成功获取 {len(all_data)}/{len(WATCHLIST)} 只股票数据")
    
    strategies = [
        ("策略A", "MA5/20金叉死叉", strategy_a_ma5_20),
        ("策略B", "纯MACD金叉死叉(无RSI)", strategy_b_macd_only),
        ("策略C", "EMA12/26金叉死叉", strategy_c_ema_cross),
        ("策略D", "KDJ金叉(J<100)", strategy_d_kdj_cross),
    ]
    
    results = {}
    for sid, sname, sfunc in strategies:
        print(f"\n🔬 运行 {sid}: {sname}")
        stock_results = []
        for code, name in WATCHLIST:
            if code not in all_data:
                continue
            df = sfunc(all_data[code])
            res = backtest_stock(df, sid, f"{name}({code})")
            if res:
                stock_results.append({
                    "code": code, "name": name, **res
                })
        
        total_final = sum(r["final"] for r in stock_results)
        avg_return = np.mean([r["return_pct"] for r in stock_results])
        avg_maxdd = np.mean([r["max_dd"] for r in stock_results])
        total_trades = sum(r["n_trades"] for r in stock_results)
        
        results[sid] = {
            "name": sname,
            "total_final": round(total_final, 2),
            "total_return_pct": round((total_final / START_CAPITAL - 1) * 100, 2),
            "avg_return": round(avg_return, 2),
            "avg_maxdd": round(avg_maxdd, 2),
            "total_trades": total_trades,
            "stocks": stock_results,
        }
        print(f"  → 总资产: {results[sid]['total_final']:.2f}, "
              f"收益率: {results[sid]['total_return_pct']:+.2f}%, "
              f"平均回撤: {results[sid]['avg_maxdd']:.2f}%, "
              f"交易: {total_trades}次")
    
    # ============ 写入报告 ============
    lines = []
    lines.append(f"# 📊 四策略量化模拟盘对比 (V2)")
    lines.append(f"")
    lines.append(f"**生成时间：** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**数据区间：** {DATA_START_DATE} ~ {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"**总资金：** {START_CAPITAL/10000:.0f}万元（每只约4万元等分）")
    lines.append(f"**关注股票：** 共{len(WATCHLIST)}只")
    lines.append(f"")
    
    # --- 整体对比 ---
    lines.append(f"---")
    lines.append(f"## 一、四策略整体对比")
    lines.append(f"")
    lines.append(f"| 指标 | 策略A (MA5/20) | 策略B (纯MACD) | 策略C (EMA12/26) | 策略D (KDJ) |")
    lines.append(f"|------|:------:|:------:|:------:|:------:|")
    
    best_return = max((results[s]["total_return_pct"], s) for s in results)[1]
    best_dd = max((results[s]["avg_maxdd"], s) for s in results)[1]
    best_trades = max((results[s]["total_trades"], s) for s in results)[1]
    
    for s in ["策略A", "策略B", "策略C", "策略D"]:
        r = results[s]
        r_label = "👑" if s == best_return else ""
        lines.append(f"| **{s}: {r['name']}** | **{r['total_return_pct']:+.2f}%** {r_label}| **{r['avg_maxdd']:.2f}%** | **{r['total_trades']}次** | |")
    
    # 修正表格格式
    lines = []
    lines.append(f"# 📊 四策略量化模拟盘对比 (V2)")
    lines.append(f"")
    lines.append(f"**生成时间：** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**数据区间：** {DATA_START_DATE} ~ {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"**总资金：** {START_CAPITAL/10000:.0f}万元（每只约4万元等分）")
    lines.append(f"**关注股票：** 共{len(WATCHLIST)}只")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"## 一、四策略整体对比")
    lines.append(f"")
    
    # 表格
    header = "| 策略 | 最终总资产 | 总收益率 | 平均回撤 | 总交易次数 | 优胜股票数 |"
    sep = "|------|:------:|:------:|:------:|:------:|:------:|"
    lines.append(header)
    lines.append(sep)
    
    for s_name, s_label in [("策略A", "MA5/20"), ("策略B", "纯MACD"), ("策略C", "EMA12/26"), ("策略D", "KDJ")]:
        r = results[s_name]
        w = []  # wins
        lines.append(f"| **{s_name} ({s_label})** | {r['total_final']:,.0f} | **{r['total_return_pct']:+.2f}%** | {r['avg_maxdd']:.2f}% | {r['total_trades']}次 | |")
    
    lines.append(f"")
    # 两两对比统计 (简化版)
    all_stock_codes = [s["code"] for s in list(results.values())[0]["stocks"]]
    win_counts = {s: 0 for s in ["策略A", "策略B", "策略C", "策略D"]}
    for code in all_stock_codes:
        returns = {}
        for s in ["策略A", "策略B", "策略C", "策略D"]:
            for stk in results[s]["stocks"]:
                if stk["code"] == code:
                    returns[s] = stk["return_pct"]
                    break
        if len(returns) == 4:
            best = max(returns, key=returns.get)
            win_counts[best] += 1
    
    lines.append(f"### 优胜分布（各策略跑赢其他三只的股票数）")
    lines.append(f"")
    for s in ["策略A", "策略B", "策略C", "策略D"]:
        lines.append(f"- **{s} ({results[s]['name']})**: {win_counts[s]} 只股票最优")
    lines.append(f"")
    
    # --- 各策略详情 ---
    lines.append(f"---")
    lines.append(f"## 二、各策略各股票收益率明细")
    lines.append(f"")
    
    # 按板块分组
    sectors = {
        "证券/金融": ["600030", "601066", "600036", "601995", "000987"],
        "半导体/TMT": ["600584", "688981", "002156", "002413"],
        "新能源/锂电": ["300014", "002466", "601865"],
        "高端制造/材料": ["300285", "603308", "300124", "601100", "002318", "300719", "002335"],
        "消费/其他": ["600660", "600570", "605566", "000157", "601061", "900925"],
    }
    
    for sector, codes in sectors.items():
        lines.append(f"### {sector}")
        lines.append(f"")
        h = "| 代码 | 名称 |"
        s = "|------|------|"
        for sn in ["策略A", "策略B", "策略C", "策略D"]:
            h += f" {sn}(%) | 回撤(%) |"
            s += f" :------:|:------:|"
        lines.append(h)
        lines.append(s)
        
        for code in codes:
            name = ""
            row = f"| {code} |"
            for stk in results["策略A"]["stocks"]:
                if stk["code"] == code:
                    name = stk["name"]
                    break
            row += f" {name} |"
            for s_name in ["策略A", "策略B", "策略C", "策略D"]:
                for stk in results[s_name]["stocks"]:
                    if stk["code"] == code:
                        rp = stk["return_pct"]
                        dd = stk["max_dd"]
                        emoji = " 🟢" if rp > 0 else " 🔴" if rp < -10 else ""
                        row += f" {rp:+.2f}{emoji} | {dd:.2f} |"
                        break
            lines.append(row)
        lines.append(f"")
    
    # --- 最优最差 ---
    lines.append(f"---")
    lines.append(f"## 三、明星股 & 问题股")
    lines.append(f"")
    
    for s_name in ["策略A", "策略B", "策略C", "策略D"]:
        r = results[s_name]
        sorted_stocks = sorted(r["stocks"], key=lambda x: x["return_pct"], reverse=True)
        top3 = sorted_stocks[:3]
        bot3 = sorted_stocks[-3:]
        lines.append(f"### {s_name} ({r['name']})")
        lines.append(f"")
        lines.append(f"**🏆 Top 3:**")
        for stk in top3:
            lines.append(f"- {stk['name']}({stk['code']}): **{stk['return_pct']:+.2f}%**, 回撤{stk['max_dd']:.2f}%")
        lines.append(f"")
        lines.append(f"**💩 Bottom 3:**")
        for stk in bot3:
            lines.append(f"- {stk['name']}({stk['code']}): **{stk['return_pct']:+.2f}%**, 回撤{stk['max_dd']:.2f}%")
        lines.append(f"")
    
    # --- 结论 ---
    lines.append(f"---")
    lines.append(f"## 四、结论与建议")
    lines.append(f"")
    
    # 找出综合最优的两个策略
    sorted_by_return = sorted(results.items(), key=lambda x: x[1]["total_return_pct"], reverse=True)
    lines.append(f"**收益率排名：**")
    for i, (s, r) in enumerate(sorted_by_return):
        lines.append(f"{i+1}. {s} ({r['name']}): {r['total_return_pct']:+.2f}%")
    lines.append(f"")
    
    best_two = sorted_by_return[:2]
    lines.append(f"**推荐保留的两个策略：**")
    for s, r in best_two:
        lines.append(f"- ✅ **{s} ({r['name']})**: 总收益率 {r['total_return_pct']:+.2f}%, 平均回撤 {r['avg_maxdd']:.2f}%")
    lines.append(f"")
    
    lines.append(f"---")
    lines.append(f"*自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    
    content = "\n".join(lines)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    
    print(f"\n✅ 报告已保存: {OUTPUT_FILE}")
    
    best_label = f"{best_two[0][0]} ({best_two[0][1]['total_return_pct']:+.2f}%)"
    second_label = f"{best_two[1][0]} ({best_two[1][1]['total_return_pct']:+.2f}%)"
    return f"🏆 推荐保留: {best_label} 和 {second_label}"

if __name__ == "__main__":
    summary = main()
    if summary:
        print(f"\n{summary}")
