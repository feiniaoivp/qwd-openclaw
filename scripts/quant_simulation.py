#!/usr/bin/env python3
"""
A股量化模拟盘对比 - 25只关注股票
策略A: MA双均线交叉 (收盘价>MA10买入, 收盘价<MA5卖出)
策略B: MACD金叉+RSI动量过滤 (金叉且RSI<70买入, 死叉卖出)
"""

import akshare as ak
import pandas as pd
import numpy as np
import os
import time
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# 1. 股票列表
# ============================================================
STOCKS = [
    "600030", "601066", "600036", "601995", "000987",  # 金融券商
    "600584", "688981", "002156", "002413",              # 半导体
    "300014", "002466", "601865",                        # 新能源
    "300285", "603308", "300124", "601100", "002318", "300719", "002335",  # 高端制造
    "600660", "600570", "605566", "000157", "601061",    # 其他
    "900925"                                             # B股
]

STOCK_NAMES = {
    "600030": "中信证券", "601066": "中信建投", "600036": "招商银行",
    "601995": "中金公司", "000987": "越秀资本",
    "600584": "长电科技", "688981": "中芯国际", "002156": "通富微电",
    "002413": "雷科防务",
    "300014": "亿纬锂能", "002466": "天齐锂业", "601865": "福莱特",
    "300285": "国瓷材料", "603308": "应流股份", "300124": "汇川技术",
    "601100": "恒立液压", "002318": "久立特材", "300719": "安达维尔",
    "002335": "科华数据",
    "600660": "福耀玻璃", "600570": "恒生电子", "605566": "福莱蒽特",
    "000157": "中联重科", "601061": "中信金属",
    "900925": "机电B股"
}

INITIAL_CAPITAL = 1_000_000  # 总资金100万
NUM_STOCKS = len(STOCKS)
CAPITAL_PER_STOCK = INITIAL_CAPITAL / NUM_STOCKS  # 每只约4万

OUTPUT_DIR = "/Users/duguke/.openclaw/workspace/analysis"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "quant_simulation_20260723.md")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
# 2. 获取数据
# ============================================================
def fetch_stock_data(symbol, retries=3):
    """获取股票最近60个交易日日K线数据（前复权）"""
    for attempt in range(retries):
        try:
            if symbol == "900925":
                # B股使用stock_zh_b_hist
                df = ak.stock_zh_b_hist(symbol=symbol, period="daily", adjust="qfq")
            else:
                # A股使用stock_zh_a_hist
                df = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
            
            if df is None or df.empty:
                print(f"  {symbol} ({STOCK_NAMES[symbol]}) -> 空数据, 跳过")
                return None
            
            # 标准化列名
            df.columns = [c.strip() for c in df.columns]
            
            # 获取最近60个交易日
            df = df.sort_values('日期').tail(65).reset_index(drop=True)
            
            print(f"  {symbol} ({STOCK_NAMES[symbol]}) -> {len(df)}行, 日期: {df['日期'].iloc[0]} ~ {df['日期'].iloc[-1]}")
            return df
            
        except Exception as e:
            print(f"  {symbol} 尝试 {attempt+1}/{retries} 失败: {e}")
            if attempt < retries - 1:
                time.sleep(2)
            else:
                print(f"  {symbol} -> 获取失败, 跳过")
                return None

print("="*60)
print("开始获取25只股票数据...")
print("="*60)

all_data = {}
for symbol in STOCKS:
    name = STOCK_NAMES.get(symbol, symbol)
    print(f"\n正在获取 {symbol} {name} ...")
    df = fetch_stock_data(symbol)
    if df is not None:
        all_data[symbol] = df

print(f"\n成功获取 {len(all_data)} 只股票数据")
print(f"跳过: {[s for s in STOCKS if s not in all_data]}")

# ============================================================
# 3. 计算技术指标
# ============================================================
def calc_indicators(df):
    """计算MA5, MA10, MACD, RSI"""
    df = df.copy().sort_values('日期').reset_index(drop=True)
    
    close = df['收盘'].astype(float)
    
    # MA5, MA10
    df['MA5'] = close.rolling(5).mean()
    df['MA10'] = close.rolling(10).mean()
    
    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['DIF'] = ema12 - ema26
    df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
    df['MACD'] = 2 * (df['DIF'] - df['DEA'])
    
    # MACD金叉/死叉信号
    df['MACD_CROSS'] = 0
    df.loc[df['DIF'] > df['DEA'], 'MACD_CROSS'] = 1
    
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
    """
    模拟单只股票交易
    策略A: 收盘价>MA10买入, 收盘价<MA5卖出
    策略B: MACD金叉且RSI<70买入, MACD死叉卖出
    返回: 每日持仓明细DataFrame
    """
    df = calc_indicators(df).copy()
    
    # 从第15个交易日开始(需要10+5 MA数据, 以及RSI14)
    start_idx = 15
    if len(df) <= start_idx:
        return None
    
    df = df.iloc[start_idx:].reset_index(drop=True)
    
    records = []
    holding = False
    shares = 0
    cash = CAPITAL_PER_STOCK
    buy_price = 0
    
    for i, row in df.iterrows():
        date = row['日期']
        close = float(row['收盘'])
        
        if strategy == 'A':
            # 策略A: MA双均线交叉
            ma5 = row['MA5']
            ma10 = row['MA10']
            
            if pd.isna(ma5) or pd.isna(ma10):
                market_value = shares * close if holding else 0
                total_value = market_value + cash
                records.append({
                    '日期': date, '收盘价': close, '持仓': int(shares),
                    '市值': round(market_value, 2),
                    '现金': round(cash, 2), '总资产': round(total_value, 2),
                    '操作': '无', '信号': 'N/A'
                })
                continue
            
            buy_signal = close > ma10
            sell_signal = close < ma5
            
            if not holding and buy_signal:
                # 买入
                buy_qty = int(cash / (close * 100)) * 100  # 整手
                if buy_qty >= 100:
                    cost = buy_qty * close
                    shares = buy_qty
                    cash -= cost
                    buy_price = close
                    holding = True
                    action = f"买入{buy_qty//100}手"
                else:
                    action = f"信号买入(资金不足)"
            elif holding and sell_signal:
                # 卖出
                proceeds = shares * close
                cash += proceeds
                action = f"卖出{shares//100}手"
                shares = 0
                holding = False
                buy_price = 0
            elif holding:
                action = "持有"
            else:
                action = "空仓"
        
        elif strategy == 'B':
            # 策略B: MACD金叉+RSI过滤
            dif = row['DIF']
            dea = row['DEA']
            rsi = row['RSI']
            
            if pd.isna(dif) or pd.isna(dea) or pd.isna(rsi):
                market_value = shares * close if holding else 0
                total_value = market_value + cash
                records.append({
                    '日期': date, '收盘价': close, '持仓': int(shares),
                    '市值': round(market_value, 2),
                    '现金': round(cash, 2), '总资产': round(total_value, 2),
                    '操作': '无', '信号': 'N/A'
                })
                continue
            
            # 金叉: 前一日DIF<=DEA 且 今日DIF>DEA
            prev_dif = df.iloc[i-1]['DIF'] if i > 0 else dif
            prev_dea = df.iloc[i-1]['DEA'] if i > 0 else dea
            
            golden_cross = (prev_dif <= prev_dea) and (dif > dea)
            death_cross = (prev_dif >= prev_dea) and (dif < dea)
            
            buy_signal = golden_cross and (rsi < 70)
            sell_signal = death_cross
            
            if not holding and buy_signal:
                buy_qty = int(cash / (close * 100)) * 100
                if buy_qty >= 100:
                    cost = buy_qty * close
                    shares = buy_qty
                    cash -= cost
                    buy_price = close
                    holding = True
                    action = f"买入{buy_qty//100}手"
                else:
                    action = f"金叉买入(资金不足)"
            elif holding and sell_signal:
                proceeds = shares * close
                cash += proceeds
                action = f"卖出{shares//100}手"
                shares = 0
                holding = False
                buy_price = 0
            elif holding:
                # 持有中检查RSI是否超买(>70) - 可增加止盈预警但不强制卖出
                action = "持有"
            else:
                action = "空仓"
        
        market_value = shares * close if holding else 0
        total_value = market_value + cash
        
        # 信号显示
        if strategy == 'A':
            sig = f"MA5={ma5:.2f} MA10={ma10:.2f}"
        else:
            sig = f"DIF={dif:.2f} DEA={dea:.2f} RSI={rsi:.1f}"
        
        records.append({
            '日期': date, '收盘价': round(close, 2), '持仓': int(shares),
            '市值': round(market_value, 2),
            '现金': round(cash, 2), '总资产': round(total_value, 2),
            '操作': action, '信号': sig
        })
    
    return pd.DataFrame(records)


def calc_metrics(df_trades):
    """计算交易指标"""
    if df_trades is None or len(df_trades) == 0:
        return {}
    
    df = df_trades.copy()
    
    # 最终收益
    initial = CAPITAL_PER_STOCK
    final = df['总资产'].iloc[-1]
    total_return = (final - initial) / initial * 100
    
    # 最大回撤
    peak = df['总资产'].cummax()
    drawdown = (df['总资产'] - peak) / peak * 100
    max_dd = drawdown.min()
    
    # 胜率（从操作中提取买卖）
    buys = []
    sells = []
    for i, row in df.iterrows():
        op = str(row['操作'])
        if '买入' in op:
            buys.append((i, row['收盘价']))
        elif '卖出' in op:
            sells.append((i, row['收盘价']))
    
    # 匹配买卖对
    wins = 0
    trades = 0
    for b_idx, b_price in buys:
        matching_sells = [(si, sp) for si, sp in sells if si > b_idx]
        if matching_sells:
            _, s_price = matching_sells[0]
            trades += 1
            if s_price > b_price:
                wins += 1
    
    win_rate = wins / trades * 100 if trades > 0 else 0
    
    # 交易次数
    trade_count = len(buys)
    
    # 日收益率
    daily_returns = df['总资产'].pct_change().dropna()
    sharpe_approx = daily_returns.mean() / daily_returns.std() * np.sqrt(252) if daily_returns.std() > 0 else 0
    
    return {
        '初始资金': initial,
        '最终资产': final,
        '总收益率%': round(total_return, 2),
        '最大回撤%': round(max_dd, 2),
        '胜率%': round(win_rate, 2),
        '交易次数': trade_count,
        '夏普比率(近似)': round(sharpe_approx, 2),
        '最终持仓': int(df['持仓'].iloc[-1]),
    }


# ============================================================
# 5. 运行所有模拟
# ============================================================
print("\n" + "="*60)
print("开始运行模拟盘...")
print("="*60)

all_results = {}

for symbol, df in all_data.items():
    name = STOCK_NAMES[symbol]
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
        print(f"  策略A: 收益率{metrics_a.get('总收益率%', 0)}%, 回撤{metrics_a.get('最大回撤%', 0)}%, 胜率{metrics_a.get('胜率%', 0)}%, 交易{metrics_a.get('交易次数', 0)}次")
    if metrics_b:
        print(f"  策略B: 收益率{metrics_b.get('总收益率%', 0)}%, 回撤{metrics_b.get('最大回撤%', 0)}%, 胜率{metrics_b.get('胜率%', 0)}%, 交易{metrics_b.get('交易次数', 0)}次")

# ============================================================
# 6. 汇总对比
# ============================================================
print("\n" + "="*60)
print("生成汇总数据...")
print("="*60)

# 合并所有股票每日总资产
common_dates = None
for symbol, res in all_results.items():
    df_a = res['df_a']
    if df_a is not None:
        dates = set(df_a['日期'].tolist())
        if common_dates is None:
            common_dates = dates
        else:
            common_dates = common_dates & dates
    df_b = res['df_b']
    if df_b is not None:
        dates = set(df_b['日期'].tolist())
        if common_dates is None:
            common_dates = dates
        else:
            common_dates = common_dates & dates

if common_dates:
    common_dates = sorted(common_dates)

# ============================================================
# 7. 生成报告
# ============================================================
print("\n" + "="*60)
print("写入报告...")
print("="*60)

lines = []
lines.append("# A股量化模拟盘对比报告")
lines.append("")
lines.append(f"**生成时间：** 2026-07-23 21:57")
lines.append(f"**总资金：** 100万元（每只股票约{int(CAPITAL_PER_STOCK)}元等分）")
lines.append(f"**关注股票：** {len(STOCKS)}只（成功获取{len(all_data)}只数据）")
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

# --- 总对比表 ---
lines.append("---")
lines.append("## 一、整体对比总表")
lines.append("")
lines.append("| 指标 | 策略A (MA双均线) | 策略B (MACD+RSI) |")
lines.append("|------|:------:|:------:|")

# 计算整体汇总
total_a_final = 0
total_b_final = 0
total_a_dd_list = []
total_b_dd_list = []
total_a_trades = 0
total_b_trades = 0
total_a_wins = 0
total_b_wins = 0
total_a_trades_count = 0
total_b_trades_count = 0
strategy_a_daily_total = {}
strategy_b_daily_total = {}

for symbol, res in all_results.items():
    df_a = res['df_a']
    df_b = res['df_b']
    m_a = res['metrics_a']
    m_b = res['metrics_b']
    
    if m_a:
        total_a_final += m_a.get('最终资产', 0)
        total_a_dd_list.append(m_a.get('最大回撤%', 0) or 0)
        total_a_trades += m_a.get('交易次数', 0)
        total_a_trades_count += m_a.get('交易次数', 0)
        # 计算胜率加权
        if m_a.get('交易次数', 0) > 0:
            total_a_wins += m_a.get('胜率%', 0) / 100 * m_a.get('交易次数', 0)
    
    if m_b:
        total_b_final += m_b.get('最终资产', 0)
        total_b_dd_list.append(m_b.get('最大回撤%', 0) or 0)
        total_b_trades += m_b.get('交易次数', 0)
        total_b_trades_count += m_b.get('交易次数', 0)
        if m_b.get('交易次数', 0) > 0:
            total_b_wins += m_b.get('胜率%', 0) / 100 * m_b.get('交易次数', 0)
    
    # 构建组合每日总资产
    if df_a is not None:
        for _, row in df_a.iterrows():
            d = row['日期']
            v = float(row['总资产'])
            strategy_a_daily_total[d] = strategy_a_daily_total.get(d, 0) + v
    
    if df_b is not None:
        for _, row in df_b.iterrows():
            d = row['日期']
            v = float(row['总资产'])
            strategy_b_daily_total[d] = strategy_b_daily_total.get(d, 0) + v

# 补上现金部分（未成功获取数据的股票按初始资金算）
missing_cash_a = (NUM_STOCKS - len(all_data)) * CAPITAL_PER_STOCK
total_a_final += missing_cash_a
total_b_final += missing_cash_a

total_a_return = (total_a_final / INITIAL_CAPITAL - 1) * 100
total_b_return = (total_b_final / INITIAL_CAPITAL - 1) * 100

# 组合最大回撤（策略A）
if strategy_a_daily_total:
    sorted_dates_a = sorted(strategy_a_daily_total.keys())
    a_peak = 0
    a_max_dd_val = 0
    for d in sorted_dates_a:
        v = strategy_a_daily_total[d]
        if v > a_peak:
            a_peak = v
        dd = (v - a_peak) / a_peak * 100
        if dd < a_max_dd_val:
            a_max_dd_val = dd
else:
    a_max_dd_val = 0

if strategy_b_daily_total:
    sorted_dates_b = sorted(strategy_b_daily_total.keys())
    b_peak = 0
    b_max_dd_val = 0
    for d in sorted_dates_b:
        v = strategy_b_daily_total[d]
        if v > b_peak:
            b_peak = v
        dd = (v - b_peak) / b_peak * 100
        if dd < b_max_dd_val:
            b_max_dd_val = dd
else:
    b_max_dd_val = 0

# 组合胜率
a_win_rate = total_a_wins / total_a_trades * 100 if total_a_trades > 0 else 0
b_win_rate = total_b_wins / total_b_trades * 100 if total_b_trades > 0 else 0

lines.append(f"| 初始资金 | 1,000,000 | 1,000,000 |")
lines.append(f"| 最终总资产 | {total_a_final:,.2f} | {total_b_final:,.2f} |")
lines.append(f"| 总收益率 | {total_a_return:.2f}% | {total_b_return:.2f}% |")
lines.append(f"| 组合最大回撤 | {a_max_dd_val:.2f}% | {b_max_dd_val:.2f}% |")
lines.append(f"| 累计交易次数 | {total_a_trades} | {total_b_trades} |")
lines.append(f"| 综合胜率 | {a_win_rate:.1f}% | {b_win_rate:.1f}% |")
lines.append("")

# --- 各股票详细对比 ---
lines.append("---")
lines.append("## 二、各股票策略对比")
lines.append("")
lines.append("| 股票 | 名称 | 策略A收益率 | 策略A回撤 | 策略A胜率 | 策略A交易 | 策略B收益率 | 策略B回撤 | 策略B胜率 | 策略B交易 | 优劣 |")
lines.append("|------|------|:----------:|:---------:|:---------:|:---------:|:----------:|:---------:|:---------:|:---------:|:----:|")

for symbol in STOCKS:
    if symbol not in all_results:
        lines.append(f"| {symbol} | {STOCK_NAMES[symbol]} | 数据缺失 | - | - | - | 数据缺失 | - | - | - | - |")
        continue
    
    res = all_results[symbol]
    m_a = res['metrics_a']
    m_b = res['metrics_b']
    
    a_ret = f"{m_a.get('总收益率%', 0):.1f}%" if m_a else "N/A"
    a_dd = f"{m_a.get('最大回撤%', 0):.1f}%" if m_a else "-"
    a_wr = f"{m_a.get('胜率%', 0):.1f}%" if m_a else "-"
    a_tc = m_a.get('交易次数', 0) if m_a else 0
    
    b_ret = f"{m_b.get('总收益率%', 0):.1f}%" if m_b else "N/A"
    b_dd = f"{m_b.get('最大回撤%', 0):.1f}%" if m_b else "-"
    b_wr = f"{m_b.get('胜率%', 0):.1f}%" if m_b else "-"
    b_tc = m_b.get('交易次数', 0) if m_b else 0
    
    # 优劣判断
    a_ret_val = m_a.get('总收益率%', -999) if m_a else -999
    b_ret_val = m_b.get('总收益率%', -999) if m_b else -999
    if a_ret_val > b_ret_val + 0.5:
        judge = "✅ 策略A"
    elif b_ret_val > a_ret_val + 0.5:
        judge = "✅ 策略B"
    else:
        judge = "≈ 接近"
    
    lines.append(f"| {symbol} | {res['name']} | {a_ret} | {a_dd} | {a_wr} | {a_tc} | {b_ret} | {b_dd} | {b_wr} | {b_tc} | {judge} |")

lines.append("")

# --- 策略A每日明细 ---
lines.append("---")
lines.append("## 三、策略A - 每日持仓明细 (MA双均线)")
lines.append("")
lines.append("> 每日展示各股票持仓股数和当日总资产")
lines.append("")

# 按日期分组输出
if strategy_a_daily_total:
    all_a_dates = sorted(strategy_a_daily_total.keys())
    
    lines.append("### 每日总资产走势")
    lines.append("")
    lines.append("| 日期 | 组合总资产 | 日收益率 |")
    lines.append("|------|:----------:|:--------:|")
    
    prev_total = None
    for d in all_a_dates:
        v = strategy_a_daily_total[d]
        if prev_total:
            daily_ret = (v - prev_total) / prev_total * 100
            lines.append(f"| {d} | {v:,.2f} | {daily_ret:+.2f}% |")
        else:
            lines.append(f"| {d} | {v:,.2f} | - |")
        prev_total = v
    
    lines.append("")
    
    # 按日期列出每只股票持仓
    lines.append("### 每日各股票持仓明细")
    lines.append("")
    
    for d in all_a_dates:
        lines.append(f"#### {d}")
        lines.append("")
        lines.append("| 股票 | 名称 | 收盘价 | 持仓(股) | 市值 | 操作 |")
        lines.append("|------|------|:------:|:--------:|:----:|:----:|")
        
        day_total = 0
        for symbol in STOCKS:
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
            lines.append(f"| {symbol} | {res['name']} | {row['收盘价']} | {int(row['持仓'])} | {row['市值']:,.2f} | {row['操作']} |")
        
        lines.append(f"| **合计** | | | | **{day_total:,.2f}** | |")
        lines.append("")

# --- 策略B每日明细 ---
lines.append("---")
lines.append("## 四、策略B - 每日持仓明细 (MACD+RSI)")
lines.append("")
lines.append("> 每日展示各股票持仓股数和当日总资产")
lines.append("")

if strategy_b_daily_total:
    all_b_dates = sorted(strategy_b_daily_total.keys())
    
    lines.append("### 每日总资产走势")
    lines.append("")
    lines.append("| 日期 | 组合总资产 | 日收益率 |")
    lines.append("|------|:----------:|:--------:|")
    
    prev_total = None
    for d in all_b_dates:
        v = strategy_b_daily_total[d]
        if prev_total:
            daily_ret = (v - prev_total) / prev_total * 100
            lines.append(f"| {d} | {v:,.2f} | {daily_ret:+.2f}% |")
        else:
            lines.append(f"| {d} | {v:,.2f} | - |")
        prev_total = v
    
    lines.append("")
    
    # 按日期列出每只股票持仓
    lines.append("### 每日各股票持仓明细")
    lines.append("")
    
    for d in all_b_dates:
        lines.append(f"#### {d}")
        lines.append("")
        lines.append("| 股票 | 名称 | 收盘价 | 持仓(股) | 市值 | 操作 |")
        lines.append("|------|------|:------:|:--------:|:----:|:----:|")
        
        day_total = 0
        for symbol in STOCKS:
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
            lines.append(f"| {symbol} | {res['name']} | {row['收盘价']} | {int(row['持仓'])} | {row['市值']:,.2f} | {row['操作']} |")
        
        lines.append(f"| **合计** | | | | **{day_total:,.2f}** | |")
        lines.append("")

# --- 对比总结 ---
lines.append("---")
lines.append("## 五、对比总结")
lines.append("")

winner = "策略A (MA双均线)" if total_a_return > total_b_return else "策略B (MACD+RSI)"
lines.append(f"### 最终结果")
lines.append("")
lines.append(f"- **策略A (MA双均线)：** 总收益率 {total_a_return:.2f}%，最大回撤 {a_max_dd_val:.2f}%，交易次数 {total_a_trades} 次，综合胜率 {a_win_rate:.1f}%")
lines.append(f"- **策略B (MACD+RSI)：** 总收益率 {total_b_return:.2f}%，最大回撤 {b_max_dd_val:.2f}%，交易次数 {total_b_trades} 次，综合胜率 {b_win_rate:.1f}%")
lines.append(f"- **优胜策略：** {winner}")
lines.append("")

lines.append("### 分析要点")
lines.append("")
lines.append("1. **MA双均线策略** 优点在于简单直观，趋势行情中表现较好；缺点是在震荡市中容易反复被止损。")
lines.append("2. **MACD+RSI策略** 增加RSI过滤避免在超买区追高，信号更谨慎，交易次数通常少于MA策略。")
lines.append("3. 在震荡行情中，MACD+RSI策略由于信号频率低，摩擦成本小，可能优于MA策略。")
lines.append("4. 趋势行情中，MA策略反应更快，能抓住早期趋势启动。")
lines.append("5. **风险提示：** 本报告为历史回测，不代表未来收益。实际交易需考虑滑点、手续费等影响。")
lines.append("")

lines.append("### 各股票优劣统计")
lines.append("")
a_wins = sum(1 for s in STOCKS if s in all_results and all_results[s]['metrics_a'] and all_results[s]['metrics_b'] and all_results[s]['metrics_a'].get('总收益率%', -999) > all_results[s]['metrics_b'].get('总收益率%', -999) + 0.5)
b_wins = sum(1 for s in STOCKS if s in all_results and all_results[s]['metrics_a'] and all_results[s]['metrics_b'] and all_results[s]['metrics_b'].get('总收益率%', -999) > all_results[s]['metrics_a'].get('总收益率%', -999) + 0.5)
ties = sum(1 for s in STOCKS if s in all_results and all_results[s]['metrics_a'] and all_results[s]['metrics_b'] and abs(all_results[s]['metrics_a'].get('总收益率%', 0) - all_results[s]['metrics_b'].get('总收益率%', 0)) <= 0.5)

lines.append(f"- 策略A更优：{a_wins} 只股票")
lines.append(f"- 策略B更优：{b_wins} 只股票")
lines.append(f"- 接近/无显著差异：{ties} 只股票")
lines.append("")

# 写入文件
content = "\n".join(lines)
with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\n{'='*60}")
print(f"报告已写入: {OUTPUT_FILE}")
print(f"{'='*60}")
print(f"\n摘要:")
print(f"  策略A最终总资产: {total_a_final:,.2f} (收益率 {total_a_return:.2f}%)")
print(f"  策略B最终总资产: {total_b_final:,.2f} (收益率 {total_b_return:.2f}%)")
print(f"  优胜策略: {winner}")
print(f"  成功获取数据: {len(all_data)}/{NUM_STOCKS} 只股票")
