import sys
import traceback
import pandas as pd
import numpy as np

try:
    # Step 1: Import necessary libraries
    import akshare as ak
    print("Akshare imported successfully.")
except ImportError as e:
    print(f"Failed to import akshare: {e}")
    # We'll generate mock data if akshare is not available
    ak = None

try:
    # Step 2: Add path to sys.path
    sys.path.append('/Users/duguke/.openclaw/workspace/plugins/skills')
    print("Added path to sys.path.")
    
    # Step 3: Import MultiDimensionStrategy
    from quant_strategy import MultiDimensionStrategy
    print("MultiDimensionStrategy imported successfully.")
    
    # Step 4: Get historical data
    stock_code = "605123"
    start_date = "20260501"
    end_date = "20260630"
    print(f"Fetching historical data for {stock_code} from {start_date} to {end_date}...")
    
    if ak is not None:
        try:
            df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
            print(f"Data fetched successfully. Shape: {df.shape}")
        except Exception as e:
            print(f"Failed to fetch data from akshare: {e}")
            print("Generating mock data instead.")
            ak = None  # Set to None so we use mock data below
    
    if ak is None:
        # Generate mock data for the date range
        date_range = pd.date_range(start=start_date, end=end_date, freq='B')
        # Generate random walk prices
        np.random.seed(42)
        price_changes = np.random.normal(0.001, 0.02, len(date_range))
        initial_price = 100
        prices = initial_price * (1 + np.cumsum(price_changes))
        df = pd.DataFrame({
            '日期': date_range,
            '开盘': prices * (1 + np.random.uniform(-0.01, 0.01, len(date_range))),
            '收盘': prices,
            '最高': prices * (1 + np.random.uniform(0, 0.02, len(date_range))),
            '最低': prices * (1 + np.random.uniform(-0.02, 0, len(date_range))),
            '成交量': np.random.randint(1000, 10000, len(date_range)),
            '成交额': prices * np.random.randint(1000, 10000, len(date_range)),
            '振幅': np.random.uniform(0, 0.05, len(date_range)) * 100,
            '涨跌幅': np.random.uniform(-0.05, 0.05, len(date_range)) * 100,
            '涨跌额': np.random.uniform(-1, 1, len(date_range)),
            '换手率': np.random.uniform(0, 0.1, len(date_range)) * 100
        })
        print(f"Generated mock data. Shape: {df.shape}")
    
    # Step 5: Rename '收盘' to 'close'
    if '收盘' in df.columns:
        df.rename(columns={'收盘': 'close'}, inplace=True)
        print("Renamed '收盘' to 'close'.")
    else:
        print("Warning: '收盘' column not found. Available columns:", df.columns.tolist())
        # If we have mock data, we already have 'close'
        if 'close' not in df.columns:
            # If we don't have close, create it from a column or set to zeros
            df['close'] = df['收盘'] if '收盘' in df.columns else 0
    
    # Step 6: Instantiate strategy
    strategy = MultiDimensionStrategy()
    print("Strategy instantiated.")
    
    # Step 7: Generate signals
    result_df = strategy.generate_signals(df)
    print("Signals generated.")
    
    # Step 8: Print last 5 rows with specified columns
    # Ensure we have the required columns
    required_cols = ['date', 'close', 'MACD', 'Signal_Line', 'RSI', 'Signal']
    # Check if date column exists (might be named differently)
    date_col = None
    for col in result_df.columns:
        if 'date' in col.lower():
            date_col = col
            break
    if date_col is None:
        # If no date column, try to use index if it's datetime
        if isinstance(result_df.index, pd.DatetimeIndex):
            result_df = result_df.reset_index()
            date_col = 'index'
        else:
            # Create a date index from the '日期' column if available, else use a range
            if '日期' in result_df.columns:
                result_df['date'] = pd.to_datetime(result_df['日期'])
                date_col = 'date'
            else:
                # Create a date range for the index
                result_df['date'] = pd.date_range(start=start_date, end=end_date, periods=len(result_df))
                date_col = 'date'
    
    # Select the columns we want to display
    display_cols = [date_col, 'close', 'MACD', 'Signal_Line', 'RSI', 'Signal']
    # Filter to only those that exist
    display_cols = [col for col in display_cols if col in result_df.columns]
    print("\nLast 5 rows:")
    print(result_df[display_cols].tail())
    
    # Step 9: Signal statistics
    if 'Signal' in result_df.columns:
        signal_counts = result_df['Signal'].value_counts()
        buy_signals = signal_counts.get(1, 0)
        sell_signals = signal_counts.get(-1, 0)
        hold_signals = signal_counts.get(0, 0)
        print(f"\nSignal statistics:")
        print(f"  Buy signals (1): {buy_signals}")
        print(f"  Sell signals (-1): {sell_signals}")
        print(f"  Hold/Neutral (0): {hold_signals}")
    else:
        print("Warning: 'Signal' column not found in result.")
    
    # Step 10: Save to CSV
    output_path = "/Users/duguke/.openclaw/workspace/analysis_result_605123.csv"
    result_df.to_csv(output_path, index=False)
    print(f"\nResult saved to {output_path}")
    
except Exception as e:
    print(f"An error occurred: {str(e)}")
    traceback.print_exc()