#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import pandas as pd
import time
sys.path.append('/Users/duguke/.openclaw/workspace/plugins/skills')
from quant_strategy import MultiDimensionStrategy

try:
    import akshare as ak
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "akshare"])
    import akshare as ak

def analyze_stock(symbol, name):
    try:
        print(f'Fetching {name} ({symbol})...')
        # Use recent 1 year data
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily", 
                               start_date='20250701', end_date='20260630', adjust='qfq')
        if df is None or df.empty:
            print(f'  No data for {symbol}')
            return None
        df = df.rename(columns={'收盘': 'close', '日期': 'date'})
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        if len(df) < 30:
            print(f'  Insufficient data: {len(df)} rows')
            return None
        strategy = MultiDimensionStrategy()
        res = strategy.generate_signals(df.copy())
        if res is None or res.empty:
            print(f'  No signals generated')
            return None
        signal_counts = res['Signal'].value_counts()
        buy = signal_counts.get(1, 0)
        sell = signal_counts.get(-1, 0)
        hold = signal_counts.get(0, 0)
        recent_signal = res['Signal'].iloc[-1]
        recent_price = res['close'].iloc[-1]
        result = {
            'symbol': symbol,
            'name': name,
            'buy': buy,
            'sell': sell,
            'hold': hold,
            'total': len(res),
            'recent_signal': recent_signal,
            'recent_price': recent_price
        }
        print(f'  Buy:{buy}, Sell:{sell}, Hold:{hold}, Recent signal:{recent_signal}, Price:{recent_price:.2f}')
        # Save detailed CSV
        out_file = f'/Users/duguke/.openclaw/workspace/analysis_result_{symbol}.csv'
        res.to_csv(out_file, index=False)
        return result
    except Exception as e:
        print(f'  Error processing {symbol}: {e}')
        # try shorter range on failure
        try:
            print(f'  Retrying with shorter range...')
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily", 
                                   start_date='20260101', end_date='20260630', adjust='qfq')
            if df is None or df.empty:
                print(f'  Still no data')
                return None
            df = df.rename(columns={'收盘': 'close', '日期': 'date'})
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date')
            if len(df) < 20:
                print(f'  Still insufficient data')
                return None
            strategy = MultiDimensionStrategy()
            res = strategy.generate_signals(df.copy())
            if res is None or res.empty:
                print(f'  Still no signals')
                return None
            signal_counts = res['Signal'].value_counts()
            buy = signal_counts.get(1, 0)
            sell = signal_counts.get(-1, 0)
            hold = signal_counts.get(0, 0)
            recent_signal = res['Signal'].iloc[-1]
            recent_price = res['close'].iloc[-1]
            result = {
                'symbol': symbol,
                'name': name,
                'buy': buy,
                'sell': sell,
                'hold': hold,
                'total': len(res),
                'recent_signal': recent_signal,
                'recent_price': recent_price
            }
            print(f'  Retry -> Buy:{buy}, Sell:{sell}, Hold:{hold}, Recent signal:{recent_signal}, Price:{recent_price:.2f}')
            out_file = f'/Users/duguke/.openclaw/workspace/analysis_result_{symbol}.csv'
            res.to_csv(out_file, index=False)
            return result
        except Exception as e2:
            print(f'  Retry also failed: {e2}')
            return None

def main():
    remaining = [
        ('300337', '银邦股份'),
        ('603596', '伯特利'),
        ('688433', '华曙高科'),
        ('300762', '上海瀚讯'),
        ('600343', '航天动力'),
        ('688066', '*ST 航图')
    ]
    results = []
    for sym, name in remaining:
        res = analyze_stock(sym, name)
        if res:
            results.append(res)
        time.sleep(2)
    
    # Load existing summary if exists
    existing_summary = []
    summary_path = '/Users/duguke/.openclaw/workspace/analysis_summary_all.md'
    try:
        # parse the markdown table
        with open(summary_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        # find table lines
        in_table = False
        for line in lines:
            if line.startswith('|') and '股票代码' in line:
                in_table = True
                continue
            if in_table and line.startswith('|----------'):
                continue
            if in_table and line.startswith('|') and not line.startswith('|=='):
                parts = [p.strip() for p in line.strip().split('|')[1:-1]]
                if len(parts) >= 8:
                    code, name, buy, sell, hold, total, recent_signal, recent_price = parts[:8]
                    try:
                        existing_summary.append({
                            'symbol': code,
                            'name': name,
                            'buy': int(buy),
                            'sell': int(sell),
                            'hold': int(hold),
                            'total': int(total),
                            'recent_signal': int(recent_signal) if recent_signal.lstrip('-').isdigit() else recent_signal,
                            'recent_price': float(recent_price) if recent_price.replace('.','',1).isdigit() else 0.0
                        })
                    except:
                        pass
            if in_table and not line.startswith('|'):
                break
    except Exception as e:
        print(f'Could not parse existing summary: {e}')
    
    all_results = existing_summary + results
    
    # Print combined summary
    print('\n' + '='*80)
    print('Combined Analysis Summary (Existing + New)')
    print('='*80)
    print('Code\tName\tBuy\tSell\tHold\tTotal\tRecent Signal\tRecent Price')
    for r in all_results:
        print(f"{r['symbol']}\t{r['name']}\t{r['buy']}\t{r['sell']}\t{r['hold']}\t{r['total']}\t{r['recent_signal']}\t{r['recent_price']:.2f}")
    
    # Save to CSV
    df_all = pd.DataFrame(all_results)
    df_all.to_csv('/Users/duguke/.openclaw/workspace/combined_analysis_summary.csv', index=False, encoding='utf_8_sig')
    print(f'\nCombined summary saved to: /Users/duguke/.openclaw/workspace/combined_analysis_summary.csv')
    
    # Also produce a markdown report
    report_path = '/Users/duguke/.openclaw/workspace/analysis_report_combined.md'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('# 股票多维度策略分析汇总报告\n\n')
        f.write(f'生成时间: {pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")}\n\n')
        f.write('## 分析结果\n\n')
        f.write('| 股票代码 | 股票名称 | 买入信号 | 卖出信号 | 持有信号 | 总天数 | 最近信号 | 最近价格 |\n')
        f.write('|----------|----------|----------|----------|----------|--------|----------|----------|\n')
        for r in all_results:
            f.write(f"| {r['symbol']} | {r['name']} | {r['buy']} | {r['sell']} | {r['hold']} | {r['total']} | {r['recent_signal']} | {r['recent_price']:.2f} |\n")
        f.write('\n## 结论\n\n')
        # Add some observations
        total_stocks = len(all_results)
        avg_buy = sum(r['buy'] for r in all_results) / total_stocks if total_stocks else 0
        avg_sell = sum(r['sell'] for r in all_results) / total_stocks if total_stocks else 0
        avg_hold = sum(r['hold'] for r in all_results) / total_stocks if total_stocks else 0
        f.write(f'- 共分析 {total_stocks} 只股票\n')
        f.write(f'- 平均买入信号: {avg_buy:.1f}, 平均卖出信号: {avg_sell:.1f}, 平均持有信号: {avg_hold:.1f}\n')
        f.write('- 持有信号占比较高，表明策略多数时间保持观望或持仓。\n')
        # Identify stocks with recent buy/sell signals
        recent_buy = [r for r in all_results if r['recent_signal'] == 1]
        recent_sell = [r for r in all_results if r['recent_signal'] == -1]
        if recent_buy:
            f.write(f'- 最近有买入信号的股票: {", ".join([r["symbol"] for r in recent_buy])}\n')
        if recent_sell:
            f.write(f'- 最近有卖出信号的股票: {", ".join([r["symbol"] for r in recent_sell])}\n')
    print(f'Markdown report saved to: {report_path}')

if __name__ == '__main__':
    main()