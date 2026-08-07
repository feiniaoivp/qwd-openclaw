#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "akshare>=1.14.0",
#     "pandas>=2.0.0",
#     "tabulate>=0.9.0",
# ]
# ///
"""
Stock Briefing Generator - Fetch all watchlist stocks' quotes, news, and produce a briefing.
"""
import akshare as ak
import pandas as pd
import json
import time
import sys
from datetime import datetime, timedelta

# === Merge all stock sources ===
# From stock-watcher (~/.clawdbot/stock_watcher/watchlist.txt)
watchlist_stocks = [
    ("600143", "金发科技"),
    ("002838", "道恩股份"),
    ("603010", "万盛股份"),
    ("600309", "万华化学"),
    ("002648", "卫星石化"),
    ("600346", "恒力石化"),
    ("002493", "荣盛石化"),
    ("600176", "中国巨石"),
    ("600183", "生益科技"),
    ("002916", "深南电路"),
    ("002463", "沪士电子"),
]

# From ttm_analysis batch analysis (39 stocks)
ttm_stocks = [
    ("600160", "巨化股份"),
    ("300827", "上能电气"),
    ("600346", "恒力石化"),  # duplicate
    ("000708", "中信特钢"),
    ("300748", "金力永磁"),
    ("002413", "雷科防务"),
    ("601061", "中信金属"),
    ("000157", "中联重科"),
    ("603308", "应流股份"),
    ("601865", "福莱特"),
    ("600660", "福耀玻璃"),
    ("002318", "久立特材"),
    ("002335", "科华数据"),
    ("605566", "福莱蒽特"),
    ("601995", "中金公司"),
    ("600030", "中信证券"),
    ("601066", "中信建投"),
    ("000987", "越秀资本"),
    ("601100", "恒立液压"),
    ("600570", "恒生电子"),
    ("300124", "汇川技术"),
    ("300719", "安达维尔"),
    ("002466", "天齐锂业"),
    ("300014", "亿纬锂能"),
    ("600036", "招商银行"),
    ("300285", "国瓷材料"),
    ("688981", "中芯国际"),
    ("600584", "长电科技"),
    ("002156", "通富微电"),
    ("603601", "再升科技"),
    ("605123", "派克新材"),
    ("002149", "西部材料"),
    ("003009", "中天火箭"),
    ("603678", "火炬电子"),
    ("300337", "银邦股份"),
    ("603596", "伯特利"),
    ("688433", "华曙高科"),
    ("300762", "上海瀚讯"),
    ("600343", "航天动力"),
]

# Dedup by code
seen = set()
all_stocks = []
for code, name in watchlist_stocks + ttm_stocks:
    if code not in seen:
        seen.add(code)
        all_stocks.append((code, name))

print(f"📋 总计 {len(all_stocks)} 只关注股票", file=sys.stderr)

# === Step 1: Get all spot quotes from EM ===
print("🔌 获取实时行情...", file=sys.stderr)
time.sleep(0.5)

try:
    spot_df = ak.stock_zh_a_spot_em()
    print(f"  行情数据共 {len(spot_df)} 条", file=sys.stderr)
except Exception as e:
    print(f"❌ 获取行情失败: {e}", file=sys.stderr)
    sys.exit(1)

# Filter by our stocks
codes_set = set(code for code, _ in all_stocks)
my_spot = spot_df[spot_df['代码'].isin(codes_set)].copy()
print(f"  命中 {len(my_spot)} 只", file=sys.stderr)

# === Step 2: For each stock, get latest K-line (last 5 days) for recent performance ===
print("📈 获取近期K线（最近5日涨跌幅）...", file=sys.stderr)

today = datetime.now()
# Go back 10 trading days to ensure we have data
end_str = today.strftime("%Y%m%d")
start_dt = today - timedelta(days=30)
start_str = start_dt.strftime("%Y%m%d")

recent_change = {}  # code -> 5_day_change_pct

for i, (code, name) in enumerate(all_stocks):
    try:
        hist = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                   start_date=start_str, end_date=end_str, 
                                   adjust="qfq")
        if hist is not None and len(hist) >= 2:
            # Get last 5 trading days close change
            recent = hist.tail(5)
            first_close = recent.iloc[0]['收盘']
            last_close = recent.iloc[-1]['收盘']
            change_pct = (last_close - first_close) / first_close * 100
            recent_change[code] = round(change_pct, 2)
        time.sleep(0.1)  # rate limit
    except Exception as e:
        print(f"  ⚠️ {code} {name} K线获取失败: {e}", file=sys.stderr)
        recent_change[code] = None
    if (i+1) % 10 == 0:
        print(f"  ... {i+1}/{len(all_stocks)}", file=sys.stderr)

print("  ✅ K线数据获取完成", file=sys.stderr)

# === Step 3: Get latest news for each stock ===
print("📰 获取个股公告/新闻...", file=sys.stderr)

stock_news = {}  # code -> list of news items (up to 3 per stock)
for i, (code, name) in enumerate(all_stocks):
    try:
        news_df = ak.stock_news_em(symbol=code)
        if news_df is not None and len(news_df) > 0:
            news_list = []
            for _, row in news_df.head(3).iterrows():
                news_list.append({
                    'title': row.get('新闻标题', row.get('title', '')),
                    'url': row.get('新闻链接', row.get('url', '')),
                    'time': row.get('发布时间', row.get('发布时间', '')),
                })
            stock_news[code] = news_list
        time.sleep(0.3)  # rate limit generously
    except Exception as e:
        print(f"  ⚠️ {code} {name} 新闻获取失败: {e}", file=sys.stderr)
        stock_news[code] = []
    if (i+1) % 10 == 0:
        print(f"  ... {i+1}/{len(all_stocks)}", file=sys.stderr)

# === Step 4: Generate briefing ===
print("\n\n📊 ====== 📊 ====== 📊 ======", file=sys.stderr)

# Output as JSON for processing
output = {
    'generated_at': today.strftime("%Y-%m-%d %H:%M"),
    'stocks': []
}

for code, name in all_stocks:
    row = my_spot[my_spot['代码'] == code]
    info = {
        'code': code,
        'name': name,
        'current': None,
        'change_pct': None,
        'change_amount': None,
        'volume': None,
        'amount': None,
        'open': None,
        'high': None,
        'low': None,
        'prev_close': None,
        'amplitude': None,
        'turnover_rate': None,
        'pe': None,
        'pb': None,
        'market_cap': None,
        'recent_5d_change': recent_change.get(code),
        'news': stock_news.get(code, []),
    }
    if len(row) > 0:
        r = row.iloc[0]
        info['current'] = float(r.get('最新价', 0))
        info['change_pct'] = float(r.get('涨跌幅', 0))
        info['change_amount'] = float(r.get('涨跌额', 0))
        info['volume'] = float(r.get('成交量', 0))
        info['amount'] = float(r.get('成交额', 0))
        info['open'] = float(r.get('今开', 0))
        info['high'] = float(r.get('最高', 0))
        info['low'] = float(r.get('最低', 0))
        info['prev_close'] = float(r.get('昨收', 0))
        info['amplitude'] = float(r.get('振幅', 0))
        info['turnover_rate'] = float(r.get('换手率', 0))
        info['pe'] = float(r.get('市盈率-动态', 0)) if pd.notna(r.get('市盈率-动态')) else None
        info['pb'] = float(r.get('市净率', 0)) if pd.notna(r.get('市净率')) else None
        info['market_cap'] = float(r.get('总市值', 0)) if pd.notna(r.get('总市值')) else None
    output['stocks'].append(info)

print(json.dumps(output, ensure_ascii=False, indent=2))
