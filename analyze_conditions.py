import pandas as pd

# Load the saved CSV files
df_603678 = pd.read_csv('/Users/duguke/.openclaw/workspace/analysis_result_603678.csv')
df_002149 = pd.read_csv('/Users/duguke/.openclaw/workspace/analysis_result_002149.csv')

# Check if the DataFrames have a date column; if not, assume index is date
# The CSV might have an unnamed index column. Let's check the columns.
print("Columns for 603678:", df_603678.columns.tolist())
print("Columns for 002149:", df_002149.columns.tolist())

# If there's an unnamed index column, we can drop it or set it as index.
# But for simplicity, we'll just use the columns we need.

# Let's check the last few rows of each dataframe for indicators
print("\n=== 603678 最后5天 ===")
print(df_603678[['close', 'MA_Fast', 'MA_Slow', 'MACD', 'Signal_Line', 'RSI']].tail())

print("\n=== 002149 最后5天 ===")
print(df_002149[['close', 'MA_Fast', 'MA_Slow', 'MACD', 'Signal_Line', 'RSI']].tail())

# Now, let's check for buy and sell conditions across the entire dataset
# We'll define the conditions as per the strategy (using default parameters)
# We assume the strategy parameters: fast_ma=5, slow_ma=10, rsi_period=6, rsi_sell_thresh=70, stop_loss_pct=0.05

# We need to compute MA_Fast and MA_Slow if not already in the dataframe (they should be, from the strategy)
# But let's verify they exist.

# For 603678
df_603678['MA_Fast'] = df_603678['close'].rolling(window=5).mean()
df_603678['MA_Slow'] = df_603678['close'].rolling(window=10).mean()
# MACD and Signal_Line and RSI should already be there from the strategy, but we can recompute to be sure
# However, we trust the strategy's calculation.

# Let's check for golden cross and death cross conditions
# Golden cross: MA_Fast > MA_Slow and previous day MA_Fast <= MA_Slow
df_603678['golden_cross'] = (df_603678['MA_Fast'] > df_603678['MA_Slow']) & (df_603678['MA_Fast'].shift(1) <= df_603678['MA_Slow'].shift(1))
# Death cross: MA_Fast < MA_Slow and previous day MA_Fast >= MA_Slow
df_603678['death_cross'] = (df_603678['MA_Fast'] < df_603678['MA_Slow']) & (df_603678['MA_Fast'].shift(1) >= df_603678['MA_Slow'].shift(1))

# MACD > Signal_Line
df_603678['macd_above_signal'] = df_603678['MACD'] > df_603678['Signal_Line']
# RSI < 70 (buy condition) and RSI > 70 (sell condition)
df_603678['rsi_below_70'] = df_603678['RSI'] < 70
df_603678['rsi_above_70'] = df_603678['RSI'] > 70

# Now, let's see if any of these conditions are met
print("\n=== 603678 条件触发次数 ===")
print("Golden Cross 次数:", df_603678['golden_cross'].sum())
print("Death Cross 次数:", df_603678['death_cross'].sum())
print("MACD > Signal_Line 次数:", df_603678['macd_above_signal'].sum())
print("RSI < 70 次数:", df_603678['rsi_below_70'].sum())
print("RSI > 70 次数:", df_603678['rsi_above_70'].sum())

# For 002149
df_002149['MA_Fast'] = df_002149['close'].rolling(window=5).mean()
df_002149['MA_Slow'] = df_002149['close'].rolling(window=10).mean()
df_002149['golden_cross'] = (df_002149['MA_Fast'] > df_002149['MA_Slow']) & (df_002149['MA_Fast'].shift(1) <= df_002149['MA_Slow'].shift(1))
df_002149['death_cross'] = (df_002149['MA_Fast'] < df_002149['MA_Slow']) & (df_002149['MA_Fast'].shift(1) >= df_002149['MA_Slow'].shift(1))
df_002149['macd_above_signal'] = df_002149['MACD'] > df_002149['Signal_Line']
df_002149['rsi_below_70'] = df_002149['RSI'] < 70
df_002149['rsi_above_70'] = df_002149['RSI'] > 70

print("\n=== 002149 条件触发次数 ===")
print("Golden Cross 次数:", df_002149['golden_cross'].sum())
print("Death Cross 次数:", df_002149['death_cross'].sum())
print("MACD > Signal_Line 次数:", df_002149['macd_above_signal'].sum())
print("RSI < 70 次数:", df_002149['rsi_below_70'].sum())
print("RSI > 70 次数:", df_002149['rsi_above_70'].sum())

# Additionally, we can check the hard stop-loss condition, but that depends on buy price.
# Since we didn't have any buy signals, we can skip.

# Let's also output the last few rows of the conditions to see the indicator values
print("\n=== 603678 最后5天指标 ===")
print(df_603678[['date', 'close', 'MA_Fast', 'MA_Slow', 'MACD', 'Signal_Line', 'RSI']].tail() if 'date' in df_603678.columns else df_603678[['close', 'MA_Fast', 'MA_Slow', 'MACD', 'Signal_Line', 'RSI']].tail())

print("\n=== 002149 最后5天指标 ===")
print(df_002149[['date', 'close', 'MA_Fast', 'MA_Slow', 'MACD', 'Signal_Line', 'RSI']].tail() if 'date' in df_002149.columns else df_002149[['close', 'MA_Fast', 'MA_Slow', 'MACD', 'Signal_Line', 'RSI']].tail())