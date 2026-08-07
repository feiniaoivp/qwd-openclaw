import sys
import traceback

try:
    # Step 1: Import necessary libraries
    import akshare as ak
    import pandas as pd
    import numpy as np
    
    # Step 2: Add path to sys.path
    sys.path.append('/Users/duguke/.openclaw/workspace/plugins/skills')
    
    # Step 3: Import MultiDimensionStrategy
    from quant_strategy import MultiDimensionStrategy
    
    # Step 4: Select two stocks
    stocks = [
        ('603678', '火炬电子', '+5.42%'),
        ('002149', '西部材料', '-5.15%')
    ]
    
    # Fixed date range
    start_date = "20260501"
    end_date = "20260630"
    
    for stock_code, stock_name, change in stocks:
        print(f"\n=== 分析股票: {stock_name} ({stock_code}) ===")
        try:
            # Step 5: Get historical data
            df_hist = ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"
            )
            
            # Step 6: Convert to DataFrame and rename '收盘' to 'close'
            df = df = pd.DataFrame(df_hist)
            if '收盘' in df.columns:
                df = df.rename(columns={'收盘': 'close'})
            else:
                # If column name different, try to find close column
                # Assuming Chinese column names: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
                close_col = None
                for col in df.columns:
                    if '收' in col:
                        close_col = col
                        break
                if close_col:
                    df = df.rename(columns={close_col: 'close'})
                else:
                    raise ValueError("未找到收盘价列")
            
            # Keep default integer index for compatibility with strategy
            # Do not set date as index; strategy expects integer index
            pass
            
            # Step 7: Instantiate strategy
            strategy = MultiDimensionStrategy()
            
            # Step 8: Generate signals
            result_df = strategy.generate_signals(df)
            
            # Step 9: Print last 5 rows with selected columns
            display_cols = ['close', 'MACD', 'Signal_Line', 'RSI', 'Signal']
            # Ensure columns exist
            available_cols = [col for col in display_cols if col in result_df.columns]
            if not available_cols:
                available_cols = result_df.columns.tolist()
            print("最近5天数据:")
            print(result_df[available_cols].tail())
            
            # Step 10: Signal statistics
            if 'Signal' in result_df.columns:
                signal_counts = result_df['Signal'].value_counts()
                buy_signals = signal_counts.get(1, 0)
                sell_signals = signal_counts.get(-1, 0)
                hold_signals = signal_counts.get(0, 0)
                print(f"信号统计: 买入(1)={buy_signals}, 卖出(-1)={sell_signals}, 持有/观望(0)={hold_signals}")
            else:
                print("未找到Signal列")
            
            # Step 11: Save to CSV
            output_path = f'/Users/duguke/.openclaw/workspace/analysis_result_{stock_code}.csv'
            result_df.to_csv(output_path)
            print(f"结果已保存到: {output_path}")
            
        except Exception as e:
            print(f"处理股票 {stock_code} 时出错: {str(e)}")
            traceback.print_exc()
    
    # Step 12: Comparison (brief)
    print("\n=== 比较摘要 ===")
    print("请查看各股票的CSV文件以进行详细比较。")
    
except Exception as e:
    print(f"初始化过程中出错: {str(e)}")
    traceback.print_exc()