import sys
sys.path.append('/Users/duguke/.openclaw/workspace/plugins/skills')

try:
    import akshare as ak
    import pandas as pd
    import numpy as np
    from quant_strategy import MultiDimensionStrategy

    # Step 4: Get historical data
    stock_code = "603601"
    start_date = "20260501"
    end_date = "20260630"
    df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
    
    # Step 5: Rename '收盘' to 'close'
    df.rename(columns={'收盘': 'close'}, inplace=True)
    
    # Step 6: Instantiate strategy
    strategy = MultiDimensionStrategy()
    
    # Step 7: Generate signals
    result_df = strategy.generate_signals(df)
    
    # Step 8: Print last 5 rows with specific columns
    print("Last 5 rows:")
    print(result_df[['日期', 'close', 'MACD', 'Signal_Line', 'RSI', 'Signal']].tail())
    
    # Step 9: Signal statistics
    signal_counts = result_df['Signal'].value_counts()
    print("\nSignal counts:")
    print(f"Buy signals (1): {signal_counts.get(1, 0)}")
    print(f"Sell signals (-1): {signal_counts.get(-1, 0)}")
    print(f"Hold signals (0): {signal_counts.get(0, 0)}")
    
    # Step 10: Save to CSV
    output_path = '/Users/duguke/.openclaw/workspace/analysis_result_603601.csv'
    result_df.to_csv(output_path, index=False)
    print(f"\nResult saved to {output_path}")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()