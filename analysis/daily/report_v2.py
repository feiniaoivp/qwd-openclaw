#!/usr/bin/env python3
"""
A股复盘报告 v2 - 使用新浪财经直接API
Last trading day: 2026-07-24 (Friday)
Report date: 2026-07-26 (Sunday review)
"""
import urllib.request
import urllib.parse
import json
import re
import time
from datetime import datetime, timedelta

TODAY = datetime.now()
DATE_STR = TODAY.strftime("%Y-%m-%d")
LAST_TRADE = "2026-07-24"  # Last Friday

STOCKS = [
    ("002318", "久立特材"), ("300014", "亿纬锂能"), ("601066", "中信建投"),
    ("600030", "中信证券"), ("300124", "汇川技术"), ("601995", "中金公司"),
    ("600584", "长电科技"), ("002156", "通富微电"), ("002466", "天齐锂业"),
    ("600036", "招商银行"), ("600570", "恒生电子"), ("605566", "福莱蒽特"),
    ("000987", "越秀资本"), ("603308", "应流股份"), ("300285", "国瓷材料"),
    ("002413", "雷科防务"), ("688981", "中芯国际"), ("601865", "福莱特"),
    ("000157", "中联重科"), ("300719", "安达维尔"), ("601061", "中信金属"),
    ("600660", "福耀玻璃"), ("900925", "机电B股"), ("002335", "科华数据"),
    ("601100", "恒立液压")
]

def sina_quote(code_list):
    """批量获取新浪行情"""
    codes = []
    for c in code_list:
        prefix = 'sh' if c.startswith('6') or c.startswith('9') else 'sz'
        codes.append(f"{prefix}{c}")
    
    url = f"http://hq.sinajs.cn/list={','.join(codes)}"
    req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn'})
    resp = urllib.request.urlopen(req, timeout=15)
    data = resp.read().decode('gbk')
    
    results = {}
    for line in data.strip().split('\n'):
        if '=' not in line:
            continue
        match = re.search(r'hq_str_(\w+)="(.+)"', line)
        if not match:
            continue
        symbol = match.group(1)
        fields = match.group(2).split(',')
        code = symbol[2:]  # strip sh/sz prefix
        results[code] = {
            'name': fields[0],
            'open': float(fields[1]) if fields[1] else 0,
            'yclose': float(fields[2]) if fields[2] else 0,
            'price': float(fields[3]) if fields[3] else 0,
            'high': float(fields[4]) if fields[4] else 0,
            'low': float(fields[5]) if fields[5] else 0,
            'volume': int(fields[8]) if fields[8] else 0,
            'amount': float(fields[9]) if fields[9] else 0,
        }
        # 计算涨跌幅
        if results[code]['yclose'] > 0:
            results[code]['change_pct'] = (results[code]['price'] - results[code]['yclose']) / results[code]['yclose'] * 100
        else:
            results[code]['change_pct'] = 0
    
    return results

def get_index_kline(index_code, name):
    """获取指数K线数据"""
    try:
        url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={index_code}&scale=240&ma=no&datalen=120"
        req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn'})
        resp = urllib.request.urlopen(req, timeout=10)
        data = resp.read().decode('utf-8')
        data = json.loads(data)
        
        closes = [float(d['close']) for d in data]
        volumes = [float(d.get('volume', 0)) for d in data]
        
        if not closes:
            return None
        
        def ma(data, n):
            if len(data) < n:
                return data[-1] if data else 0
            return sum(data[-n:]) / n
        
        return {
            'closes': closes,
            'volumes': volumes,
            'latest': closes[-1],
            'ma5': ma(closes, 5),
            'ma10': ma(closes, 10),
            'ma20': ma(closes, 20),
            'ma60': ma(closes, 60),
            'change_pct': (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 else 0
        }
    except Exception as e:
        print(f"  Error getting {name} kline: {e}")
        return None

def get_stock_kline(code):
    """获取个股K线"""
    try:
        prefix = 'sh' if code.startswith('6') or code.startswith('9') else 'sz'
        url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen=120"
        req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn'})
        resp = urllib.request.urlopen(req, timeout=10)
        data = resp.read().decode('utf-8')
        raw = json.loads(data)
        
        closes = [float(d['close']) for d in raw]
        highs = [float(d['high']) for d in raw]
        lows = [float(d['low']) for d in raw]
        volumes = [float(d.get('volume', 0)) for d in raw]
        
        if not closes:
            return None
        
        def ma(data, n):
            if len(data) < n:
                return data[-1] if data else 0
            return sum(data[-n:]) / n
        
        # MACD
        def ema(data, n):
            k = 2 / (n + 1)
            result = [data[0]]
            for i in range(1, len(data)):
                result.append(data[i] * k + result[-1] * (1 - k))
            return result
        
        ema12 = ema(closes, 12)
        ema26 = ema(closes, 26)
        dif = [ema12[i] - ema26[i] for i in range(len(ema12))]
        dea = ema([dif[i] if i < len(dif) else 0 for i in range(len(dif))], 9)
        macd = [2 * (dif[i] - dea[i]) for i in range(len(dif))]
        
        # RSI
        def rsi(data, n=14):
            if len(data) < n + 1:
                return 50
            gains, losses = 0, 0
            for i in range(len(data)-n, len(data)):
                diff = data[i] - data[i-1]
                if diff > 0:
                    gains += diff
                else:
                    losses += abs(diff)
            avg_gain = gains / n
            avg_loss = losses / n
            if avg_loss == 0:
                return 100
            rs = avg_gain / avg_loss
            return 100 - (100 / (1 + rs))
        
        # 布林带
        boll_ma = ma(closes, 20)
        if len(closes) >= 20:
            boll_std = (sum((c - boll_ma)**2 for c in closes[-20:]) / 20) ** 0.5
        else:
            boll_std = 0
        boll_upper = boll_ma + 2 * boll_std
        boll_lower = boll_ma - 2 * boll_std
        
        latest_close = closes[-1]
        
        # 成交量比 (相对5日均量)
        avg_vol_5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 1
        vol_ratio = volumes[-1] / avg_vol_5 if avg_vol_5 > 0 else 1
        
        # 支撑压力位 (20日高低)
        support_20 = min(lows[-20:]) if len(lows) >= 20 else min(lows)
        resistance_20 = max(highs[-20:]) if len(highs) >= 20 else max(highs)
        
        return {
            'closes': closes,
            'volumes': volumes,
            'latest': latest_close,
            'ma5': ma(closes, 5),
            'ma10': ma(closes, 10),
            'ma20': ma(closes, 20),
            'ma60': ma(closes, 60),
            'macd_dif': dif[-1] if dif else 0,
            'macd_dea': dea[-1] if dea else 0,
            'macd': macd[-1] if macd else 0,
            'macd_signal': '金叉' if len(dif) > 1 and dif[-1] > dea[-1] and dif[-2] <= dea[-2] else
                          '死叉' if len(dif) > 1 and dif[-1] < dea[-1] and dif[-2] >= dea[-2] else
                          '多方' if dif[-1] > dea[-1] else '空方',
            'rsi': float(rsi(closes)),
            'boll_upper': boll_upper,
            'boll_mid': boll_ma,
            'boll_lower': boll_lower,
            'boll_pos': '超涨' if latest_close > boll_upper else
                       '超跌' if latest_close < boll_lower else
                       '上轨-中轨' if latest_close > boll_ma else '中轨-下轨',
            'vol_ratio': float(vol_ratio),
            'support': support_20,
            'resistance': resistance_20,
        }
    except Exception as e:
        print(f"  Error getting {code} kline: {e}")
        return None

def analyze_trend(kline, spot):
    """分析个股趋势"""
    if not kline:
        return {'signal': '数据不足', 'advice': '暂不操作', 'risk': '数据不足'}
    
    latest = kline['latest']
    ma5, ma10, ma20, ma60 = kline['ma5'], kline['ma10'], kline['ma20'], kline['ma60']
    rsi_val = kline['rsi']
    macd_sig = kline['macd_signal']
    boll_pos = kline['boll_pos']
    vol_ratio = kline['vol_ratio']
    support = kline['support']
    resistance = kline['resistance']
    change_pct = spot.get('change_pct', 0)
    
    # MA排列判断
    ma_list = [ma5, ma10, ma20, ma60]
    valid_ma = [m for m in ma_list if m and m > 0]
    
    bull_points = 0
    bear_points = 0
    reasons = []
    
    # 均线排列
    if len(valid_ma) >= 3:
        sorted_desc = all(valid_ma[i] >= valid_ma[i+1] for i in range(len(valid_ma)-1))
        sorted_asc = all(valid_ma[i] <= valid_ma[i+1] for i in range(len(valid_ma)-1))
        if sorted_desc:
            bull_points += 2
            reasons.append('均线多头排列')
        elif sorted_asc:
            bear_points += 2
            reasons.append('均线空头排列')
        else:
            reasons.append('均线交叉')
    
    # 价格相对MA位置
    above_count = sum(1 for m in valid_ma if latest > m)
    if above_count >= 3:
        bull_points += 1
        reasons.append(f'收于{above_count}/{len(valid_ma)}均线上')
    elif above_count <= 1:
        bear_points += 1
        reasons.append(f'仅收于{above_count}/{len(valid_ma)}均线上')
    
    # MACD
    if macd_sig == '金叉':
        bull_points += 2
        reasons.append('MACD金叉')
    elif macd_sig == '死叉':
        bear_points += 2
        reasons.append('MACD死叉')
    elif macd_sig == '多方':
        bull_points += 1
    elif macd_sig == '空方':
        bear_points += 1
    
    # RSI
    if rsi_val > 70:
        bear_points += 1
        reasons.append(f'RSI{rsi_val:.0f}超买')
    elif rsi_val < 30:
        bull_points += 1
        reasons.append(f'RSI{rsi_val:.0f}超卖')
    elif rsi_val > 50:
        bull_points += 1
        reasons.append(f'RSI{rsi_val:.0f}偏强')
    else:
        bear_points += 1
        reasons.append(f'RSI{rsi_val:.0f}偏弱')
    
    # 布林带
    if boll_pos == '超涨':
        bear_points += 1
        reasons.append('触及布林上轨')
    elif boll_pos == '超跌':
        bull_points += 1
        reasons.append('触及布林下轨')
    
    # 成交量
    if vol_ratio > 1.5:
        if change_pct > 0:
            bull_points += 1
            reasons.append('放量上涨')
        else:
            bear_points += 1
            reasons.append('放量下跌')
    elif vol_ratio < 0.7:
        if change_pct < 0:
            reasons.append('缩量调整')
        else:
            reasons.append('缩量盘整')
    
    score = bull_points - bear_points
    
    if score >= 3:
        trend = '多头'
        signal = '买入关注'
        advice = f"技术面偏多，可逢低关注，支撑{support:.2f}附近"
        risk = f"压力位{resistance:.2f}附近需放量突破"
    elif score <= -3:
        trend = '空头'
        signal = '减仓/回避'
        advice = f"技术面偏空，反弹至压力位{resistance:.2f}附近减仓"
        risk = f"若跌破{support:.2f}进一步看跌"
    elif score >= 0:
        trend = '偏多震荡'
        signal = '持有观察'
        advice = f"多方占优，持有看突破{resistance:.2f}"
        risk = f"下方支撑{support:.2f}"
    else:
        trend = '偏空震荡'
        signal = '谨慎持有'
        advice = f"空方占优，注意支撑{support:.2f}得失"
        risk = f"压力位{resistance:.2f}"
    
    return {
        'signal': signal,
        'trend': trend,
        'score': score,
        'reason': '; '.join(reasons),
        'advice': advice,
        'risk': risk,
        'support': support,
        'resistance': resistance,
        'ma_arrangement': reasons[0] if reasons else '数据不足',
    }

# ============ 新闻 ============
def get_stock_news(code, name):
    """获取个股新闻"""
    try:
        url = f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol={code}.phtml"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
            'Referer': 'https://finance.sina.com.cn'
        })
        resp = urllib.request.urlopen(req, timeout=10)
        html = resp.read().decode('gbk', errors='ignore')
        
        # 提取新闻标题
        titles = re.findall(r'<a[^>]*target="_blank"[^>]*>(.*?)</a>', html)
        news_list = [t.strip() for t in titles if len(t.strip()) > 5][:5]
        return news_list
    except Exception as e:
        return []

# ============ 主程序 ============
def main():
    print("=" * 50)
    print(f"A股每日复盘报告生成 - {DATE_STR}")
    print(f"最后交易日: {LAST_TRADE} (周五)")
    print("=" * 50)
    
    report = []
    report.append(f"# 📊 A股每日复盘报告 - {LAST_TRADE}（后市复盘）")
    report.append(f"\n> **报告日期**: {DATE_STR}（周日） | **最后一个交易日**: {LAST_TRADE}（周五）")
    report.append(f"> ⚠️ 本报告基于技术面分析，仅供参考，不构成投资建议")
    report.append(f"> 数据来源: 新浪财经\n")
    report.append("---\n")
    
    # 1. 大盘指数
    print("\n📊 获取大盘指数数据...")
    indices = {
        "上证指数": "sh000001",
        "深证成指": "sz399001", 
        "创业板指": "sz399006",
        "科创50": "sh000688"
    }
    
    # 获取指数实时行情
    all_quotes = sina_quote(['000001', '399001', '399006', '000688'])
    
    report.append("## 一、大盘指数走势\n")
    report.append("| 指数 | 最新价 | 涨跌幅 | MA5 | MA10 | MA20 | MA60 |")
    report.append("|------|--------|--------|-----|------|------|------|")
    
    index_analysis = {}
    
    for name, idx_code in indices.items():
        # 提取实际code
        idx_num = idx_code[2:]
        spot = all_quotes.get(idx_num, {})
        price = spot.get('price', '-')
        cp = spot.get('change_pct', '-')
        
        kline = get_index_kline(idx_code, name)
        if kline:
            cp_str = f"{kline['change_pct']:+.2f}%" 
            ma5s = f"{kline['ma5']:.1f}"
            ma10s = f"{kline['ma10']:.1f}"
            ma20s = f"{kline['ma20']:.1f}"
            ma60s = f"{kline['ma60']:.1f}" if kline.get('ma60') else "-"
            report.append(f"| {name} | {kline['latest']:.2f} | {cp_str} | {ma5s} | {ma10s} | {ma20s} | {ma60s} |")
            
            # 分析
            index_analysis[name] = kline
        else:
            if isinstance(price, (int, float)):
                cp_str = f"{cp:+.2f}%" if cp else "-"
                report.append(f"| {name} | {price} | {cp_str} | - | - | - | - |")
            else:
                report.append(f"| {name} | - | - | - | - | - | - |")
    
    report.append("")
    
    # 指数趋势点评
    report.append("**指数趋势点评：**\n")
    for name, k in index_analysis.items():
        if not k:
            continue
        latest = k['latest']
        ma20_val = k['ma20']
        ma5_val = k['ma5']
        change = k['change_pct']
        
        direction = "📈 上涨" if change > 0 else "📉 下跌"
        trend = "中期偏多 ✅" if latest > ma20_val else "中期偏弱 ⚠️"
        short_trend = "短期强势" if latest > ma5_val else "短期承压"
        
        report.append(f"- **{name}**: {direction} {abs(change):.2f}%，收于 {latest:.2f}")
        report.append(f"  - MA20={ma20_val:.2f}，{trend}")
        report.append(f"  - MA5={ma5_val:.2f}，{short_trend}")
        report.append("")
    
    # 综合大盘判断
    sh = index_analysis.get("上证指数", {})
    if sh:
        if sh['latest'] > sh['ma20']:
            report.append("> **大盘综合判断**: 🔵 中期多头趋势，指数位于MA20上方，整体环境偏暖。\n")
        else:
            report.append("> **大盘综合判断**: 🔴 中期空头趋势，指数位于MA20下方，注意控制仓位。\n")
    
    report.append("---\n")
    
    # 2. 板块轮动
    print("📈 获取板块数据...")
    report.append("## 二、板块轮动\n")
    
    # 尝试从新浪获取板块数据
    try:
        sector_url = "http://vip.stock.finance.sina.com.cn/q/go.php/vIndustryRank/kind/szhy/p/1/sort/changepercent/asc/0/num/20/"
        req = urllib.request.Request(sector_url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'
        })
        resp = urllib.request.urlopen(req, timeout=10)
        html = resp.read().decode('gbk', errors='ignore')
        
        # 提取板块表格
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
        sectors = []
        for row in rows[1:11]:  # skip header
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) >= 6:
                name = re.sub(r'<[^>]+>', '', cells[1]).strip()
                change = re.sub(r'<[^>]+>', '', cells[2]).strip()
                sectors.append((name, change))
        
        if sectors:
            report.append("| 排名 | 板块 | 涨跌幅 |")
            report.append("|------|------|--------|")
            for i, (sname, sch) in enumerate(sectors, 1):
                report.append(f"| {i} | {sname} | {sch} |")
            report.append("")
        else:
            report.append("> 板块数据获取中...\n")
    except Exception as e:
        print(f"  板块数据: {e}")
        report.append("> 板块数据获取失败\n")
    
    report.append("---\n")
    
    # 3. 个股分析
    print("📉 获取25只关注股数据...")
    report.append("## 三、个股分析\n")
    
    # 批量获取行情
    codes_only = [c for c, n in STOCKS]
    print(f"  批量获取{len(codes_only)}只股票行情...")
    quotes = sina_quote(codes_only)
    print(f"  获取到 {len(quotes)} 只行情")
    
    all_analysis = []
    
    for idx, (code, name) in enumerate(STOCKS, 1):
        print(f"  [{idx}/25] {name}({code})...", end=" ")
        
        spot = quotes.get(code, {})
        kline = get_stock_kline(code)
        news = get_stock_news(code, name)
        
        analysis = analyze_trend(kline, spot)
        
        price = spot.get('price', '-')
        change_pct = spot.get('change_pct', 0)
        
        if isinstance(price, (int, float)):
            price_str = f"{price:.2f}"
        else:
            price_str = str(price)
        
        change_str = f"{change_pct:+.2f}%" if isinstance(change_pct, (int, float)) else "-"
        
        tech = kline or {}
        
        # 信号emoji
        signal = analysis['signal']
        if signal == '买入关注':
            emoji = "🟢"
        elif '减仓' in signal or '回避' in signal:
            emoji = "🔴"
        elif '持有' in signal:
            emoji = "🟡"
        else:
            emoji = "⚪"
        
        rsi_str = f"{tech.get('rsi', 0):.1f}" if tech.get('rsi') else "-"
        vol_str = f"{tech.get('vol_ratio', 0):.2f}" if tech.get('vol_ratio') else "-"
        
        report.append(f"### {idx}. {emoji} {name}（{code}）")
        report.append(f"- **收盘价**: {price_str}　**涨跌**: {change_str}")
        report.append(f"- **均线**: MA5={tech.get('ma5', '-'):.2f} | MA10={tech.get('ma10', '-'):.2f} | MA20={tech.get('ma20', '-'):.2f} | MA60={tech.get('ma60', '-'):.2f}")
        report.append(f"- **技术指标**: MACD={tech.get('macd_signal', '-')} | RSI={rsi_str} | 量比={vol_str}")
        report.append(f"- **布林带**: {tech.get('boll_pos', '-')}")
        report.append(f"- **趋势判断**: {analysis['trend']}（评分: {analysis['score']}）")
        report.append(f"- **关键位**: 支撑={analysis.get('support', '-'):.2f} | 压力={analysis.get('resistance', '-'):.2f}")
        report.append(f"- **操作建议**: {analysis['advice']} 🎯")
        report.append(f"- **风险提示**: ⚠️ {analysis['risk']}")
        if news:
            report.append(f"- **📰 新闻**: {' | '.join(news[:3])}")
        report.append("")
        
        all_analysis.append({
            'code': code,
            'name': name,
            'price': price_str,
            'change': change_str,
            'change_pct': change_pct,
            'trend': analysis['trend'],
            'signal': signal,
            'score': analysis['score'],
            'advice': analysis['advice'],
            'risk': analysis['risk'],
            'support': analysis.get('support', 0),
            'resistance': analysis.get('resistance', 0),
        })
        
        print(f"{signal} | {change_str}")
        
        time.sleep(0.3)  # Rate limiting
    
    # 分类汇总
    report.append("---\n")
    report.append("## 四、明日关注清单 & 预案\n")
    
    # 关注买入
    buy_list = [s for s in all_analysis if s['signal'] == '买入关注']
    if buy_list:
        report.append("### 🟢 重点买入关注\n")
        report.append("| 代码 | 名称 | 收盘价 | 趋势 | 评分 | 操作建议 |")
        report.append("|------|------|--------|------|------|----------|")
        for s in sorted(buy_list, key=lambda x: x['score'], reverse=True):
            report.append(f"| {s['code']} | {s['name']} | {s['price']} | {s['trend']} | {s['score']} | {s['advice']} |")
        report.append("")
    
    # 减仓回避
    sell_list = [s for s in all_analysis if '回避' in s['signal'] or '减仓' in s['signal']]
    if sell_list:
        report.append("### 🔴 注意风险（建议减仓/回避）\n")
        report.append("| 代码 | 名称 | 收盘价 | 趋势 | 评分 | 风险提示 |")
        report.append("|------|------|--------|------|------|----------|")
        for s in sorted(sell_list, key=lambda x: x['score']):
            report.append(f"| {s['code']} | {s['name']} | {s['price']} | {s['trend']} | {s['score']} | {s['risk']} |")
        report.append("")
    
    # 持有观察
    hold_list = [s for s in all_analysis if s['signal'] in ['持有观察', '谨慎持有']] 
    if hold_list:
        report.append("### 🟡 持有/观望\n")
        report.append("| 代码 | 名称 | 收盘价 | 趋势 | 支撑 | 压力 |")
        report.append("|------|------|--------|------|------|------|")
        for s in hold_list:
            report.append(f"| {s['code']} | {s['name']} | {s['price']} | {s['trend']} | {s['support']:.2f} | {s['resistance']:.2f} |")
        report.append("")
    
    # 统计
    bullish = sum(1 for s in all_analysis if s['score'] >= 2)
    bearish = sum(1 for s in all_analysis if s['score'] <= -2)
    neutral = 25 - bullish - bearish
    
    report.append("### 📊 信号统计\n")
    report.append(f"- 🟢 偏多信号: **{bullish}** 只")
    report.append(f"- 🔴 偏空信号: **{bearish}** 只")
    report.append(f"- 🟡 中性信号: **{neutral}** 只")
    report.append("")
    
    # 策略
    report.append("### 📋 明日策略\n")
    sh_idx = index_analysis.get("上证指数", {})
    if sh_idx and sh_idx['latest'] > sh_idx['ma20']:
        report.append("- **大盘环境**: 中期偏多，仓位可维持 5-7 成")
    else:
        report.append("- **大盘环境**: 中期偏弱，建议仓位 3-5 成")
    report.append("- 关注量能变化：放量突破压力位可加仓")
    report.append("- 板块轮动加快，避免追高已大涨板块")
    report.append("- 个股严格止损，跌破支撑位及时离场")
    report.append("")
    
    report.append("---")
    report.append(f"\n*报告生成: {TODAY.strftime('%Y-%m-%d %H:%M')} | 数据源: 新浪财经 | 仅供参考，不构成投资建议*\n")
    
    # 保存
    output_path = f"/Users/duguke/.openclaw/workspace/analysis/daily/{DATE_STR}.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report))
    print(f"\n✅ 报告已保存: {output_path}")
    
    # JSON摘要
    summary = {
        "date": DATE_STR,
        "last_trade": LAST_TRADE,
        "index": {name: {"close": k['latest'], "change_pct": k['change_pct']} for name, k in index_analysis.items() if k},
        "stocks_summary": {
            "bullish": bullish,
            "bearish": bearish,
            "neutral": neutral,
        },
        "watch_buy": [{"code": s['code'], "name": s['name'], "price": s['price']} for s in buy_list],
        "watch_sell": [{"code": s['code'], "name": s['name'], "price": s['price']} for s in sell_list],
        "all_stocks": all_analysis,
    }
    
    json_path = f"/Users/duguke/.openclaw/workspace/analysis/daily/{DATE_STR}_summary.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"✅ JSON摘要已保存: {json_path}")
    
    return output_path, json_path, summary

if __name__ == "__main__":
    main()
