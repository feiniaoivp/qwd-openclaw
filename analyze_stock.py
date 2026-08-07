import sys
sys.path.append('/Users/duguke/.openclaw/workspace/plugins/skills')

try:
    import akshare as ak
    import pandas as pd
    import numpy as np
    from quant_strategy import MultiDimensionStrategy
    
    # Get historical data
    df = ak.stock_zh_a_hist(symbol="002149", period="daily", start_date="20260501", end_date="20260630", adjust="qfq")
    
    # Convert to DataFrame and rename columns
    df = pd.DataFrame(df)
    df.rename(columns={'收盘': 'close', '日期': 'date'}, inplace=True)
    
    # Print columns for debugging
    print("DataFrame columns:", df.columns.tolist())
    
    # Instantiate strategy
    strategy = MultiDimensionStrategy()
    
    # Generate signals
    result = strategy.generate_signals(df)
    
    # Print last 5 rows with required columns
    print("\nLast 5 rows:")
    print(result[['date', 'close', 'MACD', 'Signal_Line', 'RSI', 'Signal']].tail())
    
    # Signal statistics
    signal_counts = result['Signal'].value_counts()
    print("\nSignal counts:")
    print(f"Buy signals (1): {signal_counts.get(1, 0)}")
    print(f"Sell signals (-1): {signal_counts.get(-1, 0)}")
    print(f"Hold/Watch (0): {signal_counts.get(0, 0)}")
    
    # Save to CSV
    output_path = '/Users/duguke/.openclaw/workspace/analysis_result_002149.csv'
    result.to_csv(output_path, index=False)
    print(f"\nFull results saved to: {output_path}")
    
except Exception as e:
    print(f"Error: {str(e)}")
    import traceback
    traceback.print_exc()