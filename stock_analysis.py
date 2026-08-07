import akshare as ak
import pandas as pd
import numpy as np
stock_code = '605566'
stock_name = '福莱蒽特'
start_date = '20250629'
end_date = '20260629'
try:
    df = ak.stock_zh_a_hist(symbol=stock_code, period='daily', start_date=start_date, end_date=end_date, adjust='qfq')
    df['日期'] = pd.to_datetime(df['日期'])
    df = df.sort_values('日期')
    if df.empty:
        print(f'{stock_name} ({stock_code}) 未找到数据。')
    else:
        start_close = df.iloc[0]['收盘']
        end_close = df.iloc[-1]['收盘']
        total_return = (end_close - start_close) / start_close * 100
        df['return'] = df['收盘'].pct_change()
        volatility = df['return'].std() * np.sqrt(252) * 100
        avg_volume = df['成交量'].mean()
        x = np.arange(len(df))
        y = df['收盘'].values
        coeff = np.polyfit(x, y, 1)
        trend_slope = coeff[0]
        print(f'{stock_name} ({stock_code}) 一年趋势分析（{start_date[:4]}-{start_date[4:6]}-{start_date[6:]} 至 {end_date[:4]}-{end_date[4:6]}-{end_date[6:]}，前复权）')
        print(f'起始收盘价: {start_close:.2f} 元')
        print(f'结束收盘价: {end_close:.2f} 元')
        print(f'一年总涨幅: {total_return:.2f}%')
        print(f'年化波动率: {volatility:.2f}%')
        print(f'平均日成交量: {avg_volume:,.0f} 手')
        print(f'趋势斜率: {trend_slope:.4f} 元/日')
except Exception as e:
    print(f'Error processing {stock_name} ({stock_code}): {e}')