#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复脚本：用新浪行情接口获取今日实时价格，更新分析摘要
分析日期: 2026-07-14
"""

import sys
import os
import pandas as pd
import numpy as np
import requests
import re
import time

sys.path.append('/Users/duguke/.openclaw/workspace/plugins/skills')
from quant_strategy import MultiDimensionStrategy


def get_sina_quotes(symbols):
    """通过新浪接口批量获取实时行情"""
    # 新浪接口格式：sh600036, sz002318, ...
    codes = []
    for s in symbols:
        if s.startswith('6') or s.startswith('9'):
            codes.append(f"sh{s}")
        elif s.startswith('0') or s.startswith('3'):
            codes.append(f"sz{s}")
        elif s.startswith('9'):
            codes.append(f"sh{s}")
        else:
            codes.append(f"sz{s}")
    
    # 新浪一次最多查几十个没问题，按20个一批
    batch_size = 20
    result = {}
    
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        url = "https://hq.sinajs.cn/list=" + ",".join(batch)
        headers = {'Referer': 'https://finance.sina.com.cn'}
        
        try:
            r = requests.get(url, headers=headers, timeout=15)
            r.encoding = 'gbk'
            text = r.text
            
            # 解析每个股票
            for line in text.strip().split('\n'):
                if not line.strip():
                    continue
                # var hq_str_sh600036="招商银行,37.100,..."
                match = re.search(r'hq_str_(\w+)="(.+?)"', line)
                if not match:
                    continue
                
                code_key = match.group(1)  # sh600036
                data = match.group(2).split(',')
                
                if len(data) >= 3:
                    raw_code = code_key[2:]  # 去掉 sh/sz 前缀
                    name = data[0]
                    open_p = float(data[1]) if data[1] else 0
                    prev_close = float(data[2]) if data[2] else 0
                    current = float(data[3]) if data[3] else 0
                    high = float(data[4]) if data[4] else 0
                    low = float(data[5]) if data[5] else 0
                    
                    change_pct = ((current - prev_close) / prev_close * 100) if prev_close else 0
                    change_amt = current - prev_close
                    
                    result[raw_code] = {
                        'price': current,
                        '涨跌幅': round(change_pct, 2),
                        '涨跌额': round(change_amt, 2),
                        '今开': open_p,
                        '昨收': prev_close,
                        '最高': high,
                        '最低': low,
                        '名称': name
                    }
            print(f"  批次 {i//batch_size+1}: 获取 {len(batch)} 只成功")
        except Exception as e:
            print(f"  批次 {i//batch_size+1} 失败: {e}")
        
        time.sleep(0.5)
    
    return result


def analyze_csv_file(csv_path, stock_name, stock_code, today_prices=None):
    """分析CSV + 用今日价格覆盖"""
    try:
        df = pd.read_csv(csv_path)
        
        if 'close' not in df.columns:
            if '收盘' in df.columns:
                df.rename(columns={'收盘': 'close'}, inplace=True)
            else:
                print(f"错误: {csv_path} 中没有收盘价列")
                return None
        
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
        elif '日期' in df.columns:
            df['日期'] = pd.to_datetime(df['日期'])
            df.rename(columns={'日期': 'date'}, inplace=True)
        
        if len(df) < 30:
            print(f"数据不足: 只有 {len(df)} 条记录")
            return None
        
        df_reset = df.reset_index(drop=True)
        
        strategy = MultiDimensionStrategy()
        result_df = strategy.generate_signals(df_reset.copy())
        if result_df is None or result_df.empty:
            print("策略未生成结果")
            return None
        
        if 'Signal' in result_df.columns:
            signal_counts = result_df['Signal'].value_counts()
            buy = signal_counts.get(1, 0)
            sell = signal_counts.get(-1, 0)
            hold = signal_counts.get(0, 0)
        else:
            buy = sell = hold = 0
        
        recent_signal = result_df['Signal'].iloc[-1] if 'Signal' in result_df.columns else 0
        
        # 用今日实时价格替换
        if today_prices and stock_code in today_prices and today_prices[stock_code]['price'] is not None:
            tp = today_prices[stock_code]
            recent_price = round(tp['price'], 2)
            pct = tp.get('涨跌幅', 0)
            print(f"  ✅ {stock_name}({stock_code}): 信号={recent_signal}, 今日价={recent_price} ({pct:+.2f}%)")
        else:
            recent_price = round(result_df['close'].iloc[-1], 2) if 'close' in result_df.columns else 0
            print(f"  ⚠️ {stock_name}({stock_code}): 无今日价，使用K线最后价={recent_price}")
        
        result = {
            'code': stock_code,
            'name': stock_name,
            'buy_signals': buy,
            'sell_signals': sell,
            'hold_signals': hold,
            'total_days': len(result_df),
            'recent_signal': recent_signal,
            'recent_price': recent_price,
            '涨跌幅': tp.get('涨跌幅', None) if today_prices and stock_code in today_prices else None
        }
        
        # 保存详细结果
        if 'date' not in result_df.columns and 'date' in df.columns:
            result_df.insert(0, 'date', df['date'].values)
        output_file = f"/Users/duguke/.openclaw/workspace/analysis_result_{stock_code}.csv"
        result_df.to_csv(output_file, index=False)
        
        return result
    except Exception as e:
        print(f"分析 {stock_name} 时出错: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    workspace = "/Users/duguke/.openclaw/workspace"
    
    # 获取所有股票CSV文件
    ignore_patterns = ['analysis_image', 'combined_analysis_summary', 'stock_advanced_metrics', 'stock_analysis_41', 'strategy_test']
    csv_files = [f for f in os.listdir(workspace)
                 if f.endswith('.csv') and '_' in f
                 and not f.startswith('analysis_result_')
                 and not any(p in f for p in ignore_patterns)]
    
    print(f"找到 {len(csv_files)} 只股票的CSV文件")
    
    # 提取所有股票代码
    stock_symbols = []
    stock_map = {}
    for csv_file in csv_files:
        base = csv_file[:-4]
        parts = base.split('_', 1)
        if len(parts) != 2:
            continue
        code, name = parts
        if code == '900925':
            # B股特殊处理
            stock_symbols.append(code)
        else:
            stock_symbols.append(code)
        stock_map[code] = {'name': name, 'file': csv_file}
    
    print(f"\n正在获取 {len(stock_symbols)} 只股票的今日实时行情...")
    today_prices = get_sina_quotes(stock_symbols)
    success_count = sum(1 for v in today_prices.values() if v['price'] > 0)
    print(f"成功获取 {success_count}/{len(stock_symbols)} 只")
    
    print(f"\n正在逐一分析并更新价格...")
    results = []
    for csv_file in csv_files:
        base = csv_file[:-4]
        parts = base.split('_', 1)
        if len(parts) != 2:
            print(f"跳过文件 {csv_file}: 文件名格式不正确")
            continue
        code, name = parts
        csv_path = os.path.join(workspace, csv_file)
        print(f"正在分析 {name} ({code})...")
        result = analyze_csv_file(csv_path, name, code, today_prices)
        if result:
            results.append(result)
    
    # 输出汇总
    print("\n" + "="*100)
    print("分析完成摘要（已更新今日价格）")
    print("="*100)
    print(f"成功分析: {len(results)} / {len(csv_files)} 只股票\n")
    
    if results:
        # 显示表格
        print(f"{'代码':<8} {'名称':<10} {'买入':<6} {'卖出':<6} {'持有':<6} {'天数':<6} {'信号':<6} {'最新价':<10} {'涨跌幅':<10}")
        print("-"*70)
        for r in results:
            pct_str = f"{r['涨跌幅']:+.2f}%" if r['涨跌幅'] is not None else "-"
            signal_map = {1: '买入↗', -1: '卖出↘', 0: '持有→'}
            sig_str = signal_map.get(r['recent_signal'], str(r['recent_signal']))
            print(f"{r['code']:<8} {r['name']:<10} {r['buy_signals']:<6} {r['sell_signals']:<6} {r['hold_signals']:<6} {r['total_days']:<6} {sig_str:<6} {r['recent_price']:<10} {pct_str:<10}")
        
        # 保存更新后的摘要
        summary_path = os.path.join(workspace, "analysis_summary_all.md")
        today_str = pd.Timestamp.now().strftime('%Y-%m-%d')
        now_str = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
        
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(f"# 全A股股票多维度策略分析摘要 ({today_str})\n\n")
            f.write(f"分析时间: {now_str}\n")
            f.write(f"✅ 最近价格为今日({today_str})实时行情（新浪接口）\n\n")
            f.write(f"成功分析: {len(results)} / {len(csv_files)} 只股票\n\n")
            f.write("## 分析结果\n\n")
            f.write("| 股票代码 | 股票名称 | 买入信号 | 卖出信号 | 持有信号 | 总天数 | 最近信号 | 最新价 | 今日涨跌幅 |\n")
            f.write("|----------|----------|----------|----------|----------|--------|----------|--------|------------|\n")
            for r in results:
                pct_str = f"{r['涨跌幅']:+.2f}%" if r['涨跌幅'] is not None else "-"
                signal_map = {1: '买入', -1: '卖出', 0: '持有'}
                sig_str = signal_map.get(r['recent_signal'], str(r['recent_signal']))
                f.write(f"| {r['code']} | {r['name']} | {r['buy_signals']} | {r['sell_signals']} | {r['hold_signals']} | {r['total_days']} | {sig_str} | {r['recent_price']} | {pct_str} |\n")
        
        print(f"\n✅ 摘要已保存到: {summary_path}")


if __name__ == "__main__":
    main()
