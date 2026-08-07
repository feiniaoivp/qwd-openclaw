#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os

# Step 1: Import necessary libraries
try:
    import akshare as ak
    import pandas as pd
    import numpy as np
except ImportError as e:
    print(f"导入库时出错: {e}")
    print("尝试安装缺失的库...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "akshare", "pandas", "numpy"])
    import akshare as ak
    import pandas as pd
    import numpy as np

# Step 2: Add path to sys.path
sys.path.append('/Users/duguke/.openclaw/workspace/plugins/skills')

# Step 3: Import MultiDimensionStrategy from quant_strategy
try:
    from quant_strategy import MultiDimensionStrategy
except ImportError as e:
    print(f"导入MultiDimensionStrategy时出错: {e}")
    sys.exit(1)

def fuzzy_match_stock(target_name, stock_df):
    """
    在股票列表中进行模糊搜索
    返回第一个匹配的股票代码和名称，如果没有找到则返回(None, None)
    """
    # 首先尝试精确匹配
    exact_match = stock_df[stock_df['name'] == target_name]
    if not exact_match.empty:
        row = exact_match.iloc[0]
        return row['code'], row['name']
    
    # 然后尝试包含匹配（目标名称是股票名称的一部分）
    partial_match = stock_df[stock_df['name'].str.contains(target_name, na=False)]
    if not partial_match.empty:
        row = partial_match.iloc[0]
        return row['code'], row['name']
    
    # 最后尝试股票名称包含目标名称（目标名称是股票名称的一部分）
    reverse_partial = stock_df[stock_df['name'].apply(lambda x: target_name in x if isinstance(x, str) else False)]
    if not reverse_partial.empty:
        row = reverse_partial.iloc[0]
        return row['code'], row['name']
    
    return None, None

def get_closest_match(target_name, stock_df):
    """
    使用简单的字符串相似度找到最相似的股票名称
    返回最相似的股票代码和名称
    """
    from difflib import SequenceMatcher
    
    def similar(a, b):
        return SequenceMatcher(None, a, b).ratio()
    
    best_score = 0
    best_code = None
    best_name = None
    
    for _, row in stock_df.iterrows():
        score = similar(target_name, row['name'])
        if score > best_score:
            best_score = score
            best_code = row['code']
            best_name = row['name']
    
    return best_code, best_name, best_score

def analyze_stock(stock_code, stock_name):
    """
    分析单只股票
    """
    try:
        print(f"\n正在分析 {stock_name} ({stock_code})...")
        
        # Step 6: 获取历史日线数据
        hist_data = ak.stock_zh_a_hist(
            symbol=stock_code,
            period="daily",
            start_date="20260501",
            end_date="20260630",
            adjust="qfq"
        )
        
        if hist_data is None or hist_data.empty:
            print(f"未能获取 {stock_name} ({stock_code}) 的历史数据")
            return None
        
        # Step 7: 将返回的数据转换为 pandas DataFrame。重命名 '收盘' 列为 'close'
        df = hist_data.copy()
        # 确保有收盘价列
        if '收盘' in df.columns:
            df.rename(columns={'收盘': 'close'}, inplace=True)
        else:
            print(f"警告: {stock_name} 的数据中没有找到'收盘'列")
            return None
        
        # 确保日期列存在并设置为索引（如果需要）
        if '日期' in df.columns:
            df['日期'] = pd.to_datetime(df['日期'])
            df.set_index('日期', inplace=True)
        
        # Step 8: 实例化 MultiDimensionStrategy 类（使用默认参数）
        strategy = MultiDimensionStrategy()
        
        # Step 9: 调用策略的 generate_signals 方法计算指标并生成交易信号
        result_df = strategy.generate_signals(df)
        
        if result_df is None or result_df.empty:
            print(f"策略对 {stock_name} 没有生成结果")
            return None
        
        # Step 10: 打印出结果 DataFrame 的最后 5 行
        print(f"\n{stock_name} ({stock_code}) 最近5天的数据:")
        # 选择需要显示的列
        display_cols = ['close', 'MACD', 'Signal_Line', 'RSI', 'Signal']
        # 确保这些列存在
        available_cols = [col for col in display_cols if col in result_df.columns]
        if available_cols:
            print(result_df.tail(5)[available_cols])
        else:
            print("结果DataFrame中没有预期的指标列")
            print(result_df.tail(5))
        
        # Step 11: 打印出整个期间的信号统计
        if 'Signal' in result_df.columns:
            signal_counts = result_df['Signal'].value_counts()
            buy_signals = signal_counts.get(1, 0)
            sell_signals = signal_counts.get(-1, 0)
            hold_signals = signal_counts.get(0, 0)
            print(f"\n{stock_name} 信号统计:")
            print(f"  买入信号 (1): {buy_signals}")
            print(f"  卖出信号 (-1): {sell_signals}")
            print(f"  持有/观望 (0): {hold_signals}")
        else:
            print(f"\n{stock_name} 没有找到Signal列")
        
        # Step 12: 将每只股票的完整结果保存到CSV文件
        output_file = f"/Users/duguke/.openclaw/workspace/analysis_result_{stock_code}.csv"
        result_df.to_csv(output_file)
        print(f"完整结果已保存到: {output_file}")
        
        return result_df
        
    except Exception as e:
        print(f"分析 {stock_name} ({stock_code}) 时出错: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

def main():
    # 目标股票名称列表
    target_stocks = ["富通微电", "机电B股", "福莱安特"]
    
    try:
        # Step 4: 使用 akshare.stock_info_a_code_name() 获取所有A股股票的代码和名称列表
        print("正在获取A股股票列表...")
        stock_info = ak.stock_info_a_code_name()
        print(f"成功获取 {len(stock_info)} 只股票的信息")
        
        # 确保列名正确
        if 'code' not in stock_info.columns or 'name' not in stock_info.columns:
            print("股票信息数据格式不符合预期")
            print("可用列:", stock_info.columns.tolist())
            return
        
        # 处理每只目标股票
        for target_name in target_stocks:
            print(f"\n{'='*50}")
            print(f"正在查找股票: {target_name}")
            print(f"{'='*50}")
            
            # Step 5: 为每只目标股票名称进行模糊搜索
            code, name = fuzzy_match_stock(target_name, stock_info)
            
            if code is not None and name is not None:
                print(f"找到匹配股票: {name} ({code})")
                # 分析这只股票
                analyze_stock(code, name)
            else:
                # Step 13: 如果未找到确切匹配，则打印出找到的最相似的股票名称和代码作为参考
                print(f"未找到确切匹配的股票: {target_name}")
                closest_code, closest_name, score = get_closest_match(target_name, stock_info)
                if closest_code is not None and closest_name is not None:
                    print(f"最相似的股票: {closest_name} ({closest_code}), 相似度: {score:.2f}")
                else:
                    print("无法找到相似的股票")
    
    except Exception as e:
        # Step 14: 如果任何步骤失败，请捕获异常并打印错误信息
        print(f"程序执行过程中出错: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()