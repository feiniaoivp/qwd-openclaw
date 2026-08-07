#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import pandas as pd
import numpy as np

# Add path to quant_strategy
sys.path.append('/Users/duguke/.openclaw/workspace/plugins/skills')
from quant_strategy import MultiDimensionStrategy

def analyze_csv_file(csv_path, stock_name, stock_code):
    print(f"\n{'='*60}")
    print(f"分析 {stock_name} ({stock_code})")
    print(f"{'='*60}")
    try:
        # Load CSV
        df = pd.read_csv(csv_path)
        # Ensure we have a close column
        if 'close' not in df.columns:
            # Try to rename 收盘 to close if present
            if '收盘' in df.columns:
                df.rename(columns={'收盘': 'close'}, inplace=True)
            else:
                print(f"错误: {csv_path} 中没有收盘价列")
                return None
        # Ensure date column exists and set as index if needed (we will reset later for strategy)
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
        elif '日期' in df.columns:
            df['日期'] = pd.to_datetime(df['日期'])
            df.rename(columns={'日期': 'date'}, inplace=True)
        else:
            # If no date column, create a dummy date index? but we need date for nothing.
            # We'll just keep as is.
            pass
        
        # Ensure we have enough data
        if len(df) < 30:
            print(f"数据不足: 只有 {len(df)} 条记录")
            return None
        
        # Reset index to have integer positions for strategy
        df_reset = df.reset_index(drop=True)
        
        # Initialize strategy
        strategy = MultiDimensionStrategy()
        # Generate signals
        result_df = strategy.generate_signals(df_reset.copy())
        if result_df is None or result_df.empty:
            print("策略未生成结果")
            return None
        
        # Show last 5 rows with key columns
        display_cols = ['close', 'MA_Fast', 'MA_Slow', 'MACD', 'Signal_Line', 'RSI', 'Signal']
        available_cols = [col for col in display_cols if col in result_df.columns]
        print(f"\n最近5天数据:")
        print(result_df.tail(5)[available_cols])
        
        # Signal statistics
        if 'Signal' in result_df.columns:
            signal_counts = result_df['Signal'].value_counts()
            buy = signal_counts.get(1, 0)
            sell = signal_counts.get(-1, 0)
            hold = signal_counts.get(0, 0)
            print(f"\n信号统计:")
            print(f"  买入信号 (1): {buy}")
            print(f"  卖出信号 (-1): {sell}")
            print(f"  持有/观望 (0): {hold}")
        
        # Save result (ensure date column exists)
        # If date column not in result_df but in df, add it
        if 'date' not in result_df.columns and 'date' in df.columns:
            result_df.insert(0, 'date', df['date'].values)
        output_file = f"/Users/duguke/.openclaw/workspace/analysis_result_{stock_code}.csv"
        result_df.to_csv(output_file, index=False)
        print(f"完整结果已保存到: {output_file}")
        
        return result_df
    except Exception as e:
        print(f"分析 {stock_name} 时出错: {e}")
        import traceback
        traceback.print_exc()
        return None

def main():
    stocks = [
        ("002156_通富微电.csv", "通富微电", "002156"),
        ("900925_机电B股.csv", "机电B股", "900925"),
        ("601865_福莱特.csv", "福莱特", "601865")
    ]
    
    for csv_file, name, code in stocks:
        csv_path = os.path.join("/Users/duguke/.openclaw/workspace", csv_file)
        if os.path.exists(csv_path):
            analyze_csv_file(csv_path, name, code)
        else:
            print(f"文件不存在: {csv_path}")

if __name__ == "__main__":
    main()