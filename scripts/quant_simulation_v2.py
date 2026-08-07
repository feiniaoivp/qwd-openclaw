#!/usr/bin/env python3
"""
A股量化模拟盘对比 v2 - 使用yfinance获取数据
25只关注股票，策略A: MA双均线，策略B: MACD+RSI
"""

import yfinance as yf
import pandas as pd
import numpy as np
import os
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# 1. 股票列表
# ============================================================
STOCKS_MAP = {
    "600030": ("600030.SS", "中信证券"),
    "601066": ("601066.SS", "中信建投"),
    "600036": ("600036.SS", "招商银行"),
    "601995": ("601995.SS", "中金公司"),
    "000987": ("000987.SZ", "越秀资本"),
    "600584": ("600584.SS", "长电科技"),
    "688981": ("688981.SS", "中芯国际"),
    "002156": ("002156.SZ", "通富微电"),
    "002413": ("002413.SZ", "雷科防务"),
    "300014": ("300014.SZ", "亿纬锂能"),
    "002466": ("002466.SZ", "天齐锂业"),
    "601865": ("601865.SS", "福莱特"),
    "300285": ("300285.SZ", "国瓷材料"),
    "603308": ("603308.SS", "应流股份"),
    "300124": ("300124.SZ", "汇川技术"),
    "601100": ("601100.SS", "恒立液压"),
    "002318": ("002318.SZ", "久立特材"),
    "300719": ("300719.SZ", "安达维尔"),
    "002335": ("002335.SZ", "科华数据"),
    "600660": ("600660.SS", "福耀玻璃"),
    "600570": ("600570.SS", "恒生电子"),
    "605566": ("605566.SS", "福莱蒽特"),
    "000157": ("000157.SZ", "中联重科"),
    "601061": ("601061.SS", "中信金属"),
    "900925": ("900925.SS", "机电B股"),
}

SYMBOLS = list(STOCKS_MAP.keys())
INITIAL_CAPITAL = 1_000_000
NUM_STOCKS = len(SYMBOLS)
CAPITAL_PER_STOCK = INITIAL_CAPITAL / NUM_STOCKS

OUTPUT_DIR = "/Users/duguke/.openclaw/workspace/analysis"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "quant_simulation_20260723.md")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
# 2. 获取数据
# ============================================================
def fetch_data(symbol):
    """使用yfinance获取最近60个交易日数据"""
    ticker_sym, name = STOCKS_MAP[symbol]
    try:
        t = yf.Ticker(ticker_sym)
        # 获取约3个月数据确保有60个交易日
        df = t.history(period="3mo")
        if df is None or df.empty:
            print(f"  {symbol} {name} -> 空数据")
            return None
        
        df = df.reset_index()
        df.columns = [c.strip() for c in df.columns]
        
        # 重命名列以匹配之前脚本
        df = df.rename(columns={
            'Date': '日期',
            'Open': '开盘',
            'High': '最高',
            'Low': '最低',
            'Close': '收盘',
            'Volume': '成交量'
        })
        
        # 最近65个交易日取最近60
        df = df.sort_values('日期').tail(65).reset_index(drop=True)
        
        # yfinance默认已做复权处理
        print(f"  {symbol} {name} -> {len(df)}行, {df['日期'].iloc[0].strftime('%Y-%m-%d')} ~ {df['日期'].iloc[-1].strftime('%Y-%m-%d')}")
        return df
        
    except Exception as e:
        print(f"  {symbol} {name} 获取失败: {e}")
        return None

print("=" * 60)
print("开始获取25只股票数据 (yfinance)")
print("=" * 60)

all_data = {}
for symbol in SYMBOLS:
    name = STOCKS_MAP[symbol][1]
    print(f"\n正在获取 {symbol} {name} ...")
    df = fetch_data(symbol)
    if df is not None:
        all_data[symbol] = df

print(f"\n成功获取 {len(all_data)} / {NUM_STOCKS} 只股票数据")
if len(all_data) < NUM_STOCKS:
    failed = [s for s in SYMBOLS if s not in all_data]
    print(f"失败: {[f'{s} {STOCKS_MAP[s][1]}' for s in failed]}")

# ============================================================
# 3. 计算技术指标
# ============================================================
def calc_indicators(df):
    """计算MA5, MA10, MACD, RSI"""
    df = df.copy().sort_values('日期').reset_index(drop=True)
    close = df['收盘'].astype(float)
    
    # MA
    df['MA5'] = close.rolling(5).mean()
    df['MA10'] = close.rolling(10).mean()
    
    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['DIF'] = ema12 - ema26
    df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
    df['MACD_BAR'] = 2 * (df['DIF'] - df['DEA'])
    
    # RSI(14)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df['RSI'] = 100 - (100 / (1 + rs))
    
    return df

# ============================================================
# 4. 模拟盘引擎
# ============================================================
def run_simulation(symbol, df, strategy='A'):
    df = calc_indicators(df).copy()
    
    # 从第15个索引开始（需要MA5/MA10和RSI14数据就绪）
    start_idx = 15
    if len(df) <= start_idx:
        return None
    
    df = df.iloc[start_idx:].reset_index(drop=True)
    
    records = []
    holding = False
    shares = 0
    cash = CAPITAL_PER_STOCK
    
    for i, row in df.iterrows():
        date = row['日期']
        close = float(row['收盘'])
        name = STOCKS_MAP[symbol][1]
        
        if strategy == 'A':
            ma5 = row['MA5']
            ma10 = row['MA10']
            
            if pd.isna(ma5) or pd.isna(ma10):
                mv = shares * close if holding else 0
                records.append({
                    '日期': date, '名称': name, '收盘价': round(close, 2),
                    '持仓': int(shares), '市值': round(mv, 2), '现金': round(cash, 2),
                    '总资产': round(mv + cash, 2), '操作': '初始化', '信号': 'N/A'
                })
                continue
            
            buy_signal = close > ma10
            sell_signal = close < ma5
            
            if not holding and buy_signal:
                qty = int(cash / (close * 100)) * 100
                if qty >= 100:
                    cost = qty * close
                    shares = qty
                    cash -= cost
                    holding = True
                    action = f"买入{qty//100}手"
                else:
                    action = "信号买入(不足1手)"
            elif holding and sell_signal:
                proceeds = shares * close
                cash += proceeds
                action = f"卖出{shares//100}手"
                shares = 0
                holding = False
            elif holding:
                action = "持有"
            else:
                action = "空仓"
            
            sig = f"MA5={ma5:.2f} MA10={ma10:.2f}"
        
        elif strategy == 'B':
            dif = row['DIF']
            dea = row['DEA']
            rsi = row['RSI']
            
            if pd.isna(dif) or pd.isna(dea) or pd.isna(rsi):
                mv = shares * close if holding else 0
                records.append({
                    '日期': date, '名称': name, '收盘价': round(close, 2),
                    '持仓': int(shares), '市值': round(mv, 2), '现金': round(cash, 2),
                    '总资产': round(mv + cash, 2), '操作': '初始化', '信号': 'N/A'
                })
                continue
            
            prev_dif = df.iloc[i-1]['DIF'] if i > 0 else dif
            prev_dea = df.iloc[i-1]['DEA'] if i > 0 else dea
            
            golden_cross = (prev_dif <= prev_dea) and (dif > dea)
            death_cross = (prev_dif >= prev_dea) and (dif < dea)
            
            buy_signal = golden_cross and (rsi < 70)
            sell_signal = death_cross
            
            if not holding and buy_signal:
                qty = int(cash / (close * 100)) * 100
                if qty >= 100:
                    cost = qty * close
                    shares = qty
                    cash -= cost
                    holding = True
                    action = f"买入{qty//100}手"
                else:
                    action = "金叉买入(不足1手)"
            elif holding and sell_signal:
                proceeds = shares * close
                cash += proceeds
                action = f"卖出{shares//100}手"
                shares = 0
                holding = False
            elif holding:
                action = "持有"
            else:
                action = "空仓"
            
            sig = f"DIF={dif:.2f} DEA={dea:.2f} RSI={rsi:.1f}"
        
        mv = shares * close if holding else 0
        records.append({
            '日期': date, '名称': name, '收盘价': round(close, 2),
            '持仓': int(shares), '市值': round(mv, 2), '现金': round(cash, 2),
            '总资产': round(mv + cash, 2), '操作': action, '信号': sig
        })
    
    return pd.DataFrame(records)


def calc_metrics(df_trades):
    if df_trades is None or len(df_trades) == 0:
        return {}
    
    df = df_trades.copy()
    initial = CAPITAL_PER_STOCK
    final = df['总资产'].iloc[-1]
    total_return = (final - initial) / initial * 100
    
    # 最大回撤
    peak = df['总资产'].cummax()
    drawdown = (df['总资产'] - peak) / peak * 100
    max_dd = drawdown.min()
    
    # 统计买卖
    buys, sells = [], []
    for i, row in df.iterrows():
        op = str(row['操作'])
        if '买入' in op:
            buys.append((i, float(row['收盘价'])))
        elif '卖出' in op:
            sells.append((i, float(row['收盘价'])))
    
    wins, trades = 0, 0
    for b_idx, bp in buys:
        ms = [(si, sp) for si, sp in sells if si > b_idx]
        if ms:
            _, sp = ms[0]
            trades += 1
            if sp > bp:
                wins += 1
    
    win_rate = wins / trades * 100 if trades > 0 else 0
    
    # 每日收益率
    daily_ret = df['总资产'].pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    
    return {
        '初始资金': initial,
        '最终资产': final,
        '总收益率%': round(total_return, 2),
        '最大回撤%': round(max_dd, 2),
        '胜率%': round(win_rate, 2),
        '交易次数': len(buys),
        '夏普比率': round(sharpe, 2),
        '最终持仓': int(df['持仓'].iloc[-1]),
    }


# ============================================================
# 5. 运行所有模拟
# ============================================================
print("\n" + "=" * 60)
print("开始运行模拟盘...")
print("=" * 60)

all_results = {}

for symbol, df in all_data.items():
    name = STOCKS_MAP[symbol][1]
    print(f"\n--- {symbol} {name} ---")
    
    result_a = run_simulation(symbol, df, 'A')
    result_b = run_simulation(symbol, df, 'B')
    
    metrics_a = calc_metrics(result_a) if result_a is not None else {}
    metrics_b = calc_metrics(result_b) if result_b is not None else {}
    
    all_results[symbol] = {
        'name': name,
        'df_a': result_a,
        'df_b': result_b,
        'metrics_a': metrics_a,
        'metrics_b': metrics_b,
    }
    
    if metrics_a:
        print(f"  策略A: 收益{metrics_a['总收益率%']:+.2f}%, 回撤{metrics_a['最大回撤%']:.2f}%, 胜率{metrics_a['胜率%']:.1f}%, 交易{metrics_a['交易次数']}次")
    if metrics_b:
        print(f"  策略B: 收益{metrics_b['总收益率%']:+.2f}%, 回撤{metrics_b['最大回撤%']:.2f}%, 胜率{metrics_b['胜率%']:.1f}%, 交易{metrics_b['交易次数']}次")

# ============================================================
# 6. 汇总计算
# ============================================================
print("\n" + "=" * 60)
print("汇总计算...")
print("=" * 60)

# 策略A: 所有股票每日总资产
strategy_a_daily = {}
strategy_b_daily = {}

for symbol, res in all_results.items():
    df_a = res['df_a']
    df_b = res['df_b']
    
    if df_a is not None:
        for _, row in df_a.iterrows():
            d = pd.Timestamp(row['日期'])
            v = float(row['总资产'])
            strategy_a_daily[d] = strategy_a_daily.get(d, 0) + v
    
    if df_b is not None:
        for _, row in df_b.iterrows():
            d = pd.Timestamp(row['日期'])
            v = float(row['总资产'])
            strategy_b_daily[d] = strategy_b_daily.get(d, 0) + v

# 补上未成功获取的股票按初始资金算
missing_cash = (NUM_STOCKS - len(all_data)) * CAPITAL_PER_STOCK

# 排序日期
dates_a = sorted(strategy_a_daily.keys())
dates_b = sorted(strategy_b_daily.keys())

# 计算总资产 + 补缺
for d in dates_a:
    strategy_a_daily[d] += missing_cash / max(len(dates_a), 1)
for d in dates_b:
    strategy_b_daily[d] += missing_cash / max(len(dates_b), 1)

# 最终总资产
total_a_final = strategy_a_daily[dates_a[-1]] if dates_a else INITIAL_CAPITAL
total_b_final = strategy_b_daily[dates_b[-1]] if dates_b else INITIAL_CAPITAL

total_a_return = (total_a_final / INITIAL_CAPITAL - 1) * 100
total_b_return = (total_b_final / INITIAL_CAPITAL - 1) * 100

# 组合最大回撤
def calc_portfolio_dd(daily_dict, dates):
    peak = 0
    max_dd = 0
    for d in dates:
        v = daily_dict[d]
        if v > peak:
            peak = v
        dd = (v - peak) / peak * 100
        if dd < max_dd:
            max_dd = dd
    return max_dd

a_max_dd = calc_portfolio_dd(strategy_a_daily, dates_a)
b_max_dd = calc_portfolio_dd(strategy_b_daily, dates_b)

# 汇总交易统计
total_a_trades = sum(r['metrics_a'].get('交易次数', 0) for r in all_results.values() if r['metrics_a'])
total_b_trades = sum(r['metrics_b'].get('交易次数', 0) for r in all_results.values() if r['metrics_b'])
total_a_wins = sum(r['metrics_a'].get('胜率%', 0) / 100 * r['metrics_a'].get('交易次数', 0) for r in all_results.values() if r['metrics_a'])
total_b_wins = sum(r['metrics_b'].get('胜率%', 0) / 100 * r['metrics_b'].get('交易次数', 0) for r in all_results.values() if r['metrics_b'])
a_win_rate = total_a_wins / total_a_trades * 100 if total_a_trades > 0 else 0
b_win_rate = total_b_wins / total_b_trades * 100 if total_b_trades > 0 else 0

# ============================================================
# 7. 生成报告
# ============================================================
print("\n写入报告...")

lines = []
lines.append("# A股量化模拟盘对比报告")
lines.append("")
lines.append(f"**生成时间：** 2026-07-23 21:57 (北京时间)")
lines.append(f"**数据源：** Yahoo Finance (yfinance)")
lines.append(f"**总资金：** 100万元（每只股票约{int(CAPITAL_PER_STOCK)}元等分）")
lines.append(f"**关注股票：** 共{NUM_STOCKS}只，成功获取{len(all_data)}只数据")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 策略说明")
lines.append("")
lines.append("### 策略A：MA双均线交叉（趋势跟踪）")
lines.append("- **买入条件：** 收盘价 > MA10（10日均线）")
lines.append("- **卖出条件：** 收盘价 < MA5（5日均线）")
lines.append("- **逻辑：** 趋势跟踪，收盘价站上10日线说明趋势走好，破5日线则离场")
lines.append("")
lines.append("### 策略B：MACD金叉 + RSI动量过滤")
lines.append("- **买入条件：** MACD金叉（DIF上穿DEA）且 RSI(14) < 70")
lines.append("- **卖出条件：** MACD死叉（DIF下穿DEA）")
lines.append("- **逻辑：** 利用MACD趋势信号，RSI过滤超买区追高风险")
lines.append("")
lines.append("---")
lines.append("")

# --- 一、整体对比总表 ---
lines.append("## 一、整体对比总表")
lines.append("")
lines.append("| 指标 | 策略A (MA双均线) | 策略B (MACD+RSI) |")
lines.append("|------|:------:|:------:|")
lines.append(f"| **初始资金** | 1,000,000 | 1,000,000 |")
lines.append(f"| **最终总资产** | {total_a_final:,.2f} | {total_b_final:,.2f} |")
lines.append(f"| **总收益率** | **{total_a_return:+.2f}%** | **{total_b_return:+.2f}%** |")
lines.append(f"| **组合最大回撤** | {a_max_dd:.2f}% | {b_max_dd:.2f}% |")
lines.append(f"| **累计交易次数** | {total_a_trades} | {total_b_trades} |")
lines.append(f"| **综合胜率** | {a_win_rate:.1f}% | {b_win_rate:.1f}% |")

# 活跃交易股票数
active_a = sum(1 for r in all_results.values() if r['metrics_a'] and r['metrics_a'].get('交易次数', 0) > 0)
active_b = sum(1 for r in all_results.values() if r['metrics_b'] and r['metrics_b'].get('交易次数', 0) > 0)
lines.append(f"| **有交易股票数** | {active_a} | {active_b} |")
lines.append("")

# 策略优劣
winner = "策略A (MA双均线)" if total_a_return > total_b_return else "策略B (MACD+RSI)"
if abs(total_a_return - total_b_return) < 0.5:
    winner = "两者接近"

lines.append(f"**优胜策略：** {winner}")
lines.append("")

# --- 二、各股票策略对比 ---
lines.append("---")
lines.append("## 二、各股票策略对比明细")
lines.append("")
lines.append("| 股票 | 名称 | 策略A收益率 | 策略A回撤 | 策略A胜率 | 策略A交易 | 策略B收益率 | 策略B回撤 | 策略B胜率 | 策略B交易 | 优劣 |")
lines.append("|------|------|:----------:|:---------:|:---------:|:---------:|:----------:|:---------:|:---------:|:---------:|:----:|")

a_wins = b_wins = ties = 0

for symbol in SYMBOLS:
    if symbol not in all_results:
        lines.append(f"| {symbol} | {STOCKS_MAP[symbol][1]} | 数据缺失 | - | - | - | 数据缺失 | - | - | - | - |")
        continue
    
    res = all_results[symbol]
    m_a = res['metrics_a']
    m_b = res['metrics_b']
    
    a_ret = f"{m_a.get('总收益率%', 0):+.1f}%" if m_a else "N/A"
    a_dd = f"{m_a.get('最大回撤%', 0):.1f}%" if m_a else "-"
    a_wr = f"{m_a.get('胜率%', 0):.1f}%" if m_a else "-"
    a_tc = m_a.get('交易次数', 0) if m_a else 0
    
    b_ret = f"{m_b.get('总收益率%', 0):+.1f}%" if m_b else "N/A"
    b_dd = f"{m_b.get('最大回撤%', 0):.1f}%" if m_b else "-"
    b_wr = f"{m_b.get('胜率%', 0):.1f}%" if m_b else "-"
    b_tc = m_b.get('交易次数', 0) if m_b else 0
    
    a_val = m_a.get('总收益率%', -999) if m_a else -999
    b_val = m_b.get('总收益率%', -999) if m_b else -999
    
    if a_val > b_val + 0.5:
        judge = "✅ A"
        a_wins += 1
    elif b_val > a_val + 0.5:
        judge = "✅ B"
        b_wins += 1
    else:
        judge = "≈"
        ties += 1
    
    lines.append(f"| {symbol} | {res['name']} | {a_ret} | {a_dd} | {a_wr} | {a_tc} | {b_ret} | {b_dd} | {b_wr} | {b_tc} | {judge} |")

lines.append("")
lines.append("**优劣统计：** 策略A更优 {a_wins} 只，策略B更优 {b_wins} 只，接近 {ties} 只".format(a_wins=a_wins, b_wins=b_wins, ties=ties))
lines.append("")

# --- 三、策略A每日明细 ---
lines.append("---")
lines.append("## 三、策略A - 每日持仓明细")
lines.append("")

if dates_a:
    lines.append("### 策略A - 每日组合总资产走势")
    lines.append("")
    lines.append("| 日期 | 组合总资产 | 日收益率 | 累计收益率 |")
    lines.append("|------|:----------:|:--------:|:----------:|")
    
    prev = None
    for d in dates_a:
        v = strategy_a_daily[d]
        if prev:
            dr = (v - prev) / prev * 100
        else:
            dr = None
        cr = (v / INITIAL_CAPITAL - 1) * 100
        dr_str = f"{dr:+.2f}%" if dr is not None else "-"
        lines.append(f"| {d.strftime('%Y-%m-%d')} | {v:,.2f} | {dr_str} | {cr:+.2f}% |")
        prev = v
    
    lines.append("")
    
    lines.append("### 策略A - 每日各股票明细")
    lines.append("")
    
    for d in dates_a:
        d_str = d.strftime('%Y-%m-%d')
        lines.append(f"#### {d_str}")
        lines.append("")
        lines.append("| 股票 | 名称 | 收盘价 | 持仓(股) | 市值 | 现金 | 操作 |")
        lines.append("|------|------|:------:|:--------:|:----:|:----:|:----:|")
        
        day_total = 0
        for symbol in SYMBOLS:
            if symbol not in all_results:
                continue
            res = all_results[symbol]
            df_a = res['df_a']
            if df_a is None:
                continue
            day_row = df_a[df_a['日期'] == d]
            if len(day_row) == 0:
                continue
            row = day_row.iloc[0]
            day_total += float(row['总资产'])
            lines.append(f"| {symbol} | {res['name']} | {row['收盘价']} | {int(row['持仓'])} | {row['市值']:,.2f} | {row['现金']:,.2f} | {row['操作']} |")
        
        lines.append(f"| **合计** | | | | **{day_total:,.2f}** | **{day_total:,.2f}** | |")
        lines.append("")

# --- 四、策略B每日明细 ---
lines.append("---")
lines.append("## 四、策略B - 每日持仓明细")
lines.append("")

if dates_b:
    lines.append("### 策略B - 每日组合总资产走势")
    lines.append("")
    lines.append("| 日期 | 组合总资产 | 日收益率 | 累计收益率 |")
    lines.append("|------|:----------:|:--------:|:----------:|")
    
    prev = None
    for d in dates_b:
        v = strategy_b_daily[d]
        if prev:
            dr = (v - prev) / prev * 100
        else:
            dr = None
        cr = (v / INITIAL_CAPITAL - 1) * 100
        dr_str = f"{dr:+.2f}%" if dr is not None else "-"
        lines.append(f"| {d.strftime('%Y-%m-%d')} | {v:,.2f} | {dr_str} | {cr:+.2f}% |")
        prev = v
    
    lines.append("")
    
    lines.append("### 策略B - 每日各股票明细")
    lines.append("")
    
    for d in dates_b:
        d_str = d.strftime('%Y-%m-%d')
        lines.append(f"#### {d_str}")
        lines.append("")
        lines.append("| 股票 | 名称 | 收盘价 | 持仓(股) | 市值 | 现金 | 操作 |")
        lines.append("|------|------|:------:|:--------:|:----:|:----:|:----:|")
        
        day_total = 0
        for symbol in SYMBOLS:
            if symbol not in all_results:
                continue
            res = all_results[symbol]
            df_b = res['df_b']
            if df_b is None:
                continue
            day_row = df_b[df_b['日期'] == d]
            if len(day_row) == 0:
                continue
            row = day_row.iloc[0]
            day_total += float(row['总资产'])
            lines.append(f"| {symbol} | {res['name']} | {row['收盘价']} | {int(row['持仓'])} | {row['市值']:,.2f} | {row['现金']:,.2f} | {row['操作']} |")
        
        lines.append(f"| **合计** | | | | **{day_total:,.2f}** | **{day_total:,.2f}** | |")
        lines.append("")

# --- 五、对比总结 ---
lines.append("---")
lines.append("## 五、对比总结")
lines.append("")

lines.append(f"### 最终结果")
lines.append("")
lines.append(f"- **策略A (MA双均线)：** 总收益率 **{total_a_return:+.2f}%**，最大回撤 **{a_max_dd:.2f}%**，累计交易 {total_a_trades} 次，综合胜率 {a_win_rate:.1f}%")
lines.append(f"- **策略B (MACD+RSI)：** 总收益率 **{total_b_return:+.2f}%**，最大回撤 **{b_max_dd:.2f}%**，累计交易 {total_b_trades} 次，综合胜率 {b_win_rate:.1f}%")
lines.append(f"- **优胜策略：** {winner}")
lines.append("")

lines.append("### 分析要点")
lines.append("")
lines.append("1. **MA双均线策略** 是典型的趋势跟踪策略，在单边上涨/下跌行情中表现较好，持仓时间较长，交易频率低。")
lines.append("2. **MACD+RSI策略** 结合了趋势信号（MACD金叉/死叉）和动量过滤（RSI < 70避免追高），信号更谨慎。")
lines.append("3. 交易次数差异：MA策略通常信号频率较低，持仓周期更长；MACD+RSI策略由于双条件过滤，信号可能更少。")
lines.append("4. 风险提示：本报告为历史回测（~60个交易日），样本量有限，不代表未来收益。实际交易需考虑手续费、滑点等影响。")
lines.append("")

lines.append("### 各股票优劣统计")
lines.append("")
lines.append(f"- 策略A更优：{a_wins} 只股票")
lines.append(f"- 策略B更优：{b_wins} 只股票")
lines.append(f"- 接近/无显著差异：{ties} 只股票")
lines.append("")

# 写入文件
content = "\n".join(lines)
with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\n{'=' * 60}")
print(f"报告已写入: {OUTPUT_FILE}")
print(f"{'=' * 60}")
print(f"\n最终摘要:")
print(f"  收益: 策略A {total_a_return:+.2f}% vs 策略B {total_b_return:+.2f}%")
print(f"  回撤: 策略A {a_max_dd:.2f}% vs 策略B {b_max_dd:.2f}%")
print(f"  交易: 策略A {total_a_trades}次 vs 策略B {total_b_trades}次")
print(f"  优胜: {winner}")
print(f"  股票覆盖率: {len(all_data)}/{NUM_STOCKS}")
