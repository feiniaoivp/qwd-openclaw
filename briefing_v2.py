#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pandas>=2.0.0",
#     "requests>=2.31.0",
# ]
# ///
"""
Stock Briefing v2 - Uses Tencent/Sina API for quotes + akshare for news
"""
import urllib.request
import json
import time
import sys
import re
from datetime import datetime

# === Stock list (merged, deduped) ===
watchlist_stocks = [
    ("600143", "金发科技"), ("002838", "道恩股份"), ("603010", "万盛股份"),
    ("600309", "万华化学"), ("002648", "卫星石化"), ("600346", "恒力石化"),
    ("002493", "荣盛石化"), ("600176", "中国巨石"), ("600183", "生益科技"),
    ("002916", "深南电路"), ("002463", "沪士电子"),
]
ttm_stocks = [
    ("600160", "巨化股份"), ("300827", "上能电气"), ("000708", "中信特钢"),
    ("300748", "金力永磁"), ("002413", "雷科防务"), ("601061", "中信金属"),
    ("000157", "中联重科"), ("603308", "应流股份"), ("601865", "福莱特"),
    ("600660", "福耀玻璃"), ("002318", "久立特材"), ("002335", "科华数据"),
    ("605566", "福莱蒽特"), ("601995", "中金公司"), ("600030", "中信证券"),
    ("601066", "中信建投"), ("000987", "越秀资本"), ("601100", "恒立液压"),
    ("600570", "恒生电子"), ("300124", "汇川技术"), ("300719", "安达维尔"),
    ("002466", "天齐锂业"), ("300014", "亿纬锂能"), ("600036", "招商银行"),
    ("300285", "国瓷材料"), ("688981", "中芯国际"), ("600584", "长电科技"),
    ("002156", "通富微电"), ("603601", "再升科技"), ("605123", "派克新材"),
    ("002149", "西部材料"), ("003009", "中天火箭"), ("603678", "火炬电子"),
    ("300337", "银邦股份"), ("603596", "伯特利"), ("688433", "华曙高科"),
    ("300762", "上海瀚讯"), ("600343", "航天动力"),
]

seen = set()
all_stocks = []
for code, name in watchlist_stocks + ttm_stocks:
    if code not in seen:
        seen.add(code)
        all_stocks.append((code, name))

print(f"📋 共计 {len(all_stocks)} 只关注股票", file=sys.stderr)

def get_stock_prefix(code):
    """Map stock code to Tencent/Sina prefix"""
    if code.startswith('6') or code.startswith('9'):
        return 'sh'
    elif code.startswith('0') or code.startswith('3'):
        return 'sz'
    elif code.startswith('4'):
        return 'bj'
    return 'sh'

def fetch_tencent_quote(code):
    """Fetch quote from Tencent API (qt.gtimg.cn)"""
    prefix = get_stock_prefix(code)
    url = f'https://qt.gtimg.cn/q={prefix}{code}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req, timeout=10)
    data = resp.read().decode('gbk')
    return data

def parse_tencent_quote(raw):
    """Parse Tencent quote string"""
    # v_sh600036="1~XD招商银~600036~36.88~36.55~36.61~793772~394956~398816~36.88~969~...
    m = re.search(r'~([^~]+)~(\d{6})~([\d.]+)~([\d.]+)~([\d.]+)~', raw)
    if not m:
        return None
    
    # Split by ~
    parts = raw.split('~')
    if len(parts) < 35:
        return None
    
    info = {
        'name': parts[1],
        'code': parts[2],
        'current': float(parts[3]) if parts[3] else 0,
        'prev_close': float(parts[4]) if parts[4] else 0,
        'open': float(parts[5]) if parts[5] else 0,
        'volume': float(parts[6]) if parts[6] else 0,
        'high': float(parts[33]) if parts[33] else 0,
        'low': float(parts[34]) if parts[34] else 0,
    }
    
    # Calculate change
    if info['prev_close'] > 0 and info['current'] > 0:
        info['change_amount'] = round(info['current'] - info['prev_close'], 2)
        info['change_pct'] = round(info['change_amount'] / info['prev_close'] * 100, 2)
    else:
        info['change_amount'] = 0
        info['change_pct'] = 0
    
    return info

# === Step 1: Get quotes ===
print("🔌 获取实时行情 (腾讯接口)...", file=sys.stderr)
quotes = {}
batch_codes = []
for code, name in all_stocks:
    prefix = get_stock_prefix(code)
    batch_codes.append(f'{prefix}{code}')

# Tencent supports batch query
try:
    batch_url = f'https://qt.gtimg.cn/q={",".join(batch_codes)}'
    req = urllib.request.Request(batch_url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req, timeout=15)
    data = resp.read().decode('gbk')
    
    lines = data.strip().split('\n')
    for line in lines:
        info = parse_tencent_quote(line)
        if info:
            quotes[info['code']] = info
    
    print(f"  ✅ 获取到 {len(quotes)} 只行情数据", file=sys.stderr)
except Exception as e:
    print(f"  ⚠️ 批量获取失败: {e}", file=sys.stderr)
    # Fallback to individual queries
    for code, name in all_stocks:
        try:
            raw = fetch_tencent_quote(code)
            info = parse_tencent_quote(raw)
            if info:
                quotes[code] = info
            time.sleep(0.05)
        except Exception as e2:
            print(f"  ⚠️ {code} {name}: {e2}", file=sys.stderr)

# === Step 2: Get news using akshare ===
print("📰 获取个股最新公告/新闻...", file=sys.stderr)
try:
    import akshare as ak
    has_akshare = True
except ImportError:
    has_akshare = False
    print("  ⚠️ akshare 未安装，跳过新闻", file=sys.stderr)

news_all = {}
if has_akshare:
    for i, (code, name) in enumerate(all_stocks):
        try:
            news_df = ak.stock_news_em(symbol=code)
            if news_df is not None and len(news_df) > 0:
                items = []
                for _, row in news_df.head(3).iterrows():
                    items.append({
                        'title': row.get('新闻标题', ''),
                        'datetime': str(row.get('发布时间', '')),
                    })
                news_all[code] = items
            time.sleep(0.2)
        except Exception:
            news_all[code] = []
        if (i+1) % 10 == 0:
            print(f"  ... {i+1}/{len(all_stocks)}", file=sys.stderr)

# === Step 3: Output as JSON ===
output = {
    'date': datetime.now().strftime('%Y-%m-%d'),
    'time': datetime.now().strftime('%H:%M'),
    'stocks': []
}

for code, name in all_stocks:
    q = quotes.get(code, {})
    entry = {
        'code': code,
        'name': name,
        'current': q.get('current'),
        'change_pct': q.get('change_pct'),
        'change_amount': q.get('change_amount'),
        'open': q.get('open'),
        'high': q.get('high'),
        'low': q.get('low'),
        'prev_close': q.get('prev_close'),
        'volume': q.get('volume'),
        'news': news_all.get(code, []),
    }
    output['stocks'].append(entry)

print(json.dumps(output, ensure_ascii=False))
