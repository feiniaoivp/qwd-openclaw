import sys
sys.path.append('/Users/duguke/.openclaw/workspace/plugins/skills')
import akshare as ak
import pandas as pd
from quant_strategy import MultiDimensionStrategy
import time

stocks = [
    ('603601', '再升科技'),
    ('605123', '派克新材'),
    ('002149', '西部材料'),
    ('003009', '中天火箭'),
    ('603678', '火炬电子'),
    ('300337', '银邦股份'),
    ('603596', '伯特利'),
    ('688433', '华曙高科'),
    ('300762', '上海瀚讯'),
    ('600343', '航天动力'),
    ('688066', '*ST 航图')
]

results = []
for code, name in stocks:
    for attempt in range(2):
        try:
            print(f'Fetching {name} ({code}) attempt {attempt+1}...')
            df = ak.stock_zh_a_hist(symbol=code, period='daily', start_date='20250701', end_date='20260630', adjust='qfq')
            if df is None or df.empty:
                print(f'  No data')
                time.sleep(1)
                continue
            df = df.rename(columns={'收盘':'close','日期':'date'})
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date')
            if len(df) < 30:
                print(f'  Insufficient data: {len(df)} rows')
                break
            strategy = MultiDimensionStrategy()
            res = strategy.generate_signals(df.copy())
            if res is None or res.empty:
                print(f'  No signals')
                break
            signal_counts = res['Signal'].value_counts()
            buy = signal_counts.get(1,0)
            sell = signal_counts.get(-1,0)
            hold = signal_counts.get(0,0)
            recent_signal = res['Signal'].iloc[-1]
            recent_price = res['close'].iloc[-1]
            results.append({
                'code':code,
                'name':name,
                'buy':buy,
                'sell':sell,
                'hold':hold,
                'total':len(res),
                'recent_signal':recent_signal,
                'recent_price':recent_price
            })
            print(f'  Buy:{buy}, Sell:{sell}, Hold:{hold}, Recent signal:{recent_signal}, Price:{recent_price}')
            break
        except Exception as e:
            print(f'  Error: {e}')
            time.sleep(2)
    else:
        print(f'  Failed after 2 attempts')

print('\n=== Summary for these 11 stocks ===')
print('Code\tName\tBuy\tSell\tHold\tTotal\tRecent Signal\tRecent Price')
for r in results:
    print(f"{r['code']}\t{r['name']}\t{r['buy']}\t{r['sell']}\t{r['hold']}\t{r['total']}\t{r['recent_signal']}\t{r['recent_price']}")