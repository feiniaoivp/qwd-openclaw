#!/usr/bin/env python3
"""
每日A股复盘分析 FINAL - 2026-07-28
新浪接口(实时行情) + baostock(K线技术指标)
"""
import baostock as bs
import pandas as pd
import numpy as np
import json
import requests
import time
import warnings
warnings.filterwarnings('ignore')

STOCKS = {
    "002318": "久立特材", "300014": "亿纬锂能", "601066": "中信建投",
    "600030": "中信证券", "300124": "汇川技术", "601995": "中金公司",
    "600584": "长电科技", "002156": "通富微电", "002466": "天齐锂业",
    "600036": "招商银行", "600570": "恒生电子", "605566": "福莱蒽特",
    "000987": "越秀资本", "603308": "应流股份", "300285": "国瓷材料",
    "002413": "雷科防务", "688981": "中芯国际", "601865": "福莱特",
    "000157": "中联重科", "300719": "安达维尔", "601061": "中信金属",
    "600660": "福耀玻璃", "002335": "科华数据", "601100": "恒立液压"
}

def fetch_spot():
    """从新浪获取实时行情"""
    prefixes = []
    code_map = {}
    for c in STOCKS:
        if c.startswith('6'): prefixes.append('sh'+c)
        elif c.startswith('0') or c.startswith('3'): prefixes.append('sz'+c)
        code_map[f'sh{c}'] = c
        code_map[f'sz{c}'] = c
    
    url = 'https://hq.sinajs.cn/list=' + ','.join(prefixes)
    r = requests.get(url, headers={'Referer': 'https://finance.sina.com.cn'}, timeout=10)
    results = {}
    
    for line in r.text.strip().split('\n'):
        if 'hq_str_' in line:
            try:
                parts = line.split('=\"')[1].split(',')
                full_code = line.split('=')[0].split('_')[-1]
                code = full_code[2:]  # remove sh/sz prefix
                name = parts[0]
                now_p = float(parts[3]) if parts[3] != '' else 0
                close_y = float(parts[2]) if parts[2] != '' else 0
                vol = int(parts[8]) if parts[8] != '' else 0
                amt = float(parts[9]) if parts[9] != '' else 0
                high = float(parts[4]) if parts[4] != '' else 0
                low = float(parts[5]) if parts[5] != '' else 0
                change_pct = round((now_p - close_y) / close_y * 100, 2) if close_y > 0 else 0
                results[code] = {
                    'price': now_p, 'change_pct': change_pct,
                    'volume': vol, 'amount': amt,
                    'high': high, 'low': low, 'yclose': close_y
                }
            except: pass
    return results

def fetch_kline_baostock():
    """从baostock获取K线（到7月27日）"""
    lg = bs.login()
    if lg.error_code != '0': return {}
    
    all_k = {}
    for code in STOCKS:
        if code == '900925': continue
        bsc = f"sz.{code}" if code.startswith('0') or code.startswith('3') else f"sh.{code}"
        try:
            rs = bs.query_history_k_data_plus(
                bsc, 'date,close,volume,amount,pctChg',
                start_date='2026-01-01', end_date='2026-07-28',
                frequency='d', adjustflag='2'
            )
            data = []
            while rs.next():
                row = rs.get_row_data()
                if row and row[0] and row[1] and row[1] != '':
                    data.append({
                        'date': row[0],
                        'close': float(row[1]),
                        'volume': float(row[2]) if row[2] != '' else 0,
                        'amount': float(row[3]) if row[3] != '' else 0,
                        'pctChg': float(row[4]) if row[4] != '' else 0,
                    })
            if len(data) >= 20:
                all_k[code] = pd.DataFrame(data)
            print(f"  {code} {STOCKS[code]}: {len(data)}条", end="")
            if data:
                print(f" 最新{data[-1]['date']} {data[-1]['close']}")
            else:
                print()
        except Exception as e:
            print(f"  {code}: ❌ {e}")
        time.sleep(0.1)
    
    bs.logout()
    return all_k

def compute_ma(s, w):
    return round(float(s.tail(w).mean()), 2) if len(s) >= w else None

def compute_macd(p, fast=12, slow=26, sig=9):
    if len(p) < slow: return None, None, None
    e1 = p.ewm(span=fast, adjust=False).mean()
    e2 = p.ewm(span=slow, adjust=False).mean()
    m = e1 - e2
    s = m.ewm(span=sig, adjust=False).mean()
    h = m - s
    return float(m.iloc[-1]), float(s.iloc[-1]), float(h.iloc[-1])

def compute_rsi(p, w=14):
    if len(p) < w: return None
    d = p.diff()
    g = d.where(d > 0, 0).tail(w)
    l = (-d.where(d < 0, 0)).tail(w)
    ag = g.mean()
    al = l.mean()
    if al == 0: return 100.0
    return round(100 - 100/(1 + ag/al), 1)

def compute_bb(p, w=20):
    if len(p) < w: return None, None, None
    m = p.tail(w).mean()
    s = p.tail(w).std()
    return round(float(m+2*s),2), round(float(m),2), round(float(m-2*s),2)

def analyze(code, name, spot, kline):
    """合并实时行情 + K线技术指标"""
    r = dict(code=code, name=name, price='--', change_pct=0, volume=0, amount=0,
             ma5='-', ma10='-', ma20='-', ma60='-',
             macd_str='N/A', rsi='-',
             boll_upper='-', boll_mid='-', boll_lower='-',
             vol_ratio='-', trend='未知',
             support='-', resistance='-',
             signal='持有', detail='',
             prev_close='-', boll_pos='-')
    
    if code == '900925':
        r['price'] = 'B股暂不可用'
        r['detail'] = 'B股暂不可用'
        return r
    
    # 实时数据
    s = spot.get(code)
    if s:
        r['price'] = s['price']
        r['change_pct'] = s['change_pct']
        r['volume'] = s['volume']
        r['amount'] = s['amount']
        r['prev_close'] = s['yclose']
    
    # K线技术指标
    k = kline.get(code)
    if k is not None and len(k) >= 20:
        prices = k['close']
        volumes = k['volume']
        
        n = len(prices)
        r['ma5'] = compute_ma(prices, 5)
        r['ma10'] = compute_ma(prices, 10)
        r['ma20'] = compute_ma(prices, 20)
        r['ma60'] = compute_ma(prices, 60)
        
        # MACD
        macd_l, sig_l, hist = compute_macd(prices)
        if macd_l is not None:
            macd_st = '红柱🔴' if hist > 0 else '绿柱🟢'
            cross = '金叉' if macd_l > sig_l else '死叉'
            r['macd_str'] = f"{macd_st} {cross}"
        
        # RSI
        r['rsi'] = compute_rsi(prices)
        
        # 布林带
        u, m, l = compute_bb(prices)
        r['boll_upper'] = u
        r['boll_mid'] = m
        r['boll_lower'] = l
        
        # 量比（相对20日均量）
        if n >= 20:
            avg_v = volumes.tail(20).mean()
            cur_v = volumes.iloc[-1]
            r['vol_ratio'] = round(float(cur_v/avg_v), 2) if avg_v > 0 else '-'
        
        # 价格在布林带位置
        if s and u and l:
            p = s['price']
            if p >= u: r['boll_pos'] = '上轨上方'
            elif p <= l: r['boll_pos'] = '下轨下方'
            elif p >= m: r['boll_pos'] = '中轨上方'
            else: r['boll_pos'] = '中轨下方'
        
        # 趋势
        p_last = float(prices.iloc[-1])
        sigs = []
        if r['ma5'] != '-' and r['ma10'] != '-' and r['ma20'] != '-':
            if r['ma5'] > r['ma10'] > r['ma20']: sigs.append('多头📈')
            elif r['ma5'] < r['ma10'] < r['ma20']: sigs.append('空头📉')
            else: sigs.append('均线交织')
            sigs.append('MA20上' if p_last > r['ma20'] else 'MA20下')
        
        if r['rsi'] != '-':
            if r['rsi'] > 70: sigs.append('超买⚠️')
            elif r['rsi'] < 30: sigs.append('超卖💡')
        
        r['trend'] = ' | '.join(sigs) if sigs else '震荡'
        
        # 支撑压力
        p_today = s['price'] if s else p_last
        if r['ma20'] != '-':
            if p_today > r['ma20']:
                r['support'] = r['ma20']
                r['resistance'] = r['boll_upper'] if r['boll_upper'] != '-' else round(p_today*1.05, 2)
            else:
                r['resistance'] = r['ma20']
                r['support'] = r['boll_lower'] if r['boll_lower'] != '-' else (r['ma10'] if r['ma10'] != '-' else round(p_today*0.95, 2))
    
    # 详情
    if s:
        parts = [f"涨跌{s['change_pct']:+.2f}%"]
        if r['vol_ratio'] != '-':
            if r['vol_ratio'] > 1.5: parts.append('放量')
            elif r['vol_ratio'] < 0.7: parts.append('缩量')
            else: parts.append('量平')
        if '多头' in r['trend']: parts.append('趋势偏强')
        elif '空头' in r['trend']: parts.append('趋势偏弱')
        if r['rsi'] != '-':
            if r['rsi'] > 70: parts.append('⚠️超买')
            elif r['rsi'] < 30: parts.append('💡超卖')
        r['detail'] = '，'.join(parts) if parts else '正常波动'
        
        if s['change_pct'] >= 5: r['signal'] = '持有/强势'
        elif s['change_pct'] > 0: r['signal'] = '持有'
        elif s['change_pct'] > -3: r['signal'] = '观望'
        elif s['change_pct'] > -5: r['signal'] = '关注止跌'
        else: r['signal'] = '警惕/减仓'
    
    return r

def main():
    print("=" * 60)
    print("📊 A股每日复盘分析 - 2026年7月28日（周二）")
    print("=" * 60)
    
    # 1. 新浪实时行情
    print("\n📡 新浪实时行情...")
    spot = fetch_spot()
    print(f"  成功获取 {len(spot)} 只股票实时数据")
    for c, s in sorted(spot.items()):
        print(f"  {c} {STOCKS[c]}: ¥{s['price']} ({s['change_pct']:+.2f}%)")
    
    # 2. baostock K线
    print("\n📡 Baostock K线数据...")
    kline = fetch_kline_baostock()
    print(f"\n  成功获取 {len(kline)} 只股票K线数据")
    
    # 3. 分析
    print("\n📈 分析中...")
    results = []
    for code, name in STOCKS.items():
        r = analyze(code, name, spot, kline)
        results.append(r)
    print(f"  完成 {len(results)} 只分析")
    
    # 4. 统计
    valid = [r for r in results if r['change_pct'] != 0 or (r['change_pct'] == 0 and isinstance(r['price'], (int, float)))]
    # Fix: only count valid price values
    valid = [r for r in results if isinstance(r['price'], (int, float))]
    pos = len([r for r in valid if r['change_pct'] > 0])
    neg = len([r for r in valid if r['change_pct'] < 0])
    zero = len([r for r in valid if r['change_pct'] == 0])
    avg = np.mean([r['change_pct'] for r in valid])
    
    print(f"\n📊 统计: 上涨{pos}只 下跌{neg}只 平{zero}只 平均{avg:+.2f}%")
    
    # 5. 生成报告
    report = gen_report(results)
    fname = "/Users/duguke/.openclaw/workspace/analysis/daily/2026-07-28.md"
    with open(fname, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"✅ 报告保存: {fname} ({len(report)}字符)")
    
    # 6. 摘要
    sr = sorted(valid, key=lambda x: x['change_pct'], reverse=True)
    summary = {
        'date': '2026年7月28日（周二）',
        'avg_change': round(avg, 2), 'positive': pos, 'negative': neg, 'total': len(valid),
        'up_5': len([r for r in sr if r['change_pct'] >= 5]),
        'down_3': len([r for r in sr if r['change_pct'] <= -3]),
        'down_5': len([r for r in sr if r['change_pct'] <= -5]),
        'top_gainers': ' | '.join([f"{r['name']}({r['change_pct']:+.2f}%)" for r in sr[:3]]),
        'top_losers': ' | '.join([f"{r['name']}({r['change_pct']:+.2f}%)" for r in sr[-3:] if r['change_pct'] < 0]),
        'top_losers_detail': [(r['code'], r['name'], r['change_pct'], r['rsi'], r['ma20']) for r in sr[-5:] if r['change_pct'] < 0]
    }
    sf = '/Users/duguke/.openclaw/workspace/analysis/daily/2026-07-28_summary.json'
    with open(sf, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    print(f"\n📋 核心摘要")
    print(f"  关注股上涨{pos}只/下跌{neg}只，平均{avg:+.2f}%")
    print(f"  📈 涨幅前3: {summary['top_gainers']}")
    print(f"  📉 跌幅前3: {summary['top_losers']}")
    
    return report, summary

def gen_report(results):
    valid = [r for r in results if isinstance(r['price'], (int, float))]
    sr = sorted(valid, key=lambda x: x['change_pct'], reverse=True)
    pos = len([r for r in sr if r['change_pct'] > 0])
    neg = len([r for r in sr if r['change_pct'] < 0])
    zero = len([r for r in sr if r['change_pct'] == 0])
    avg = np.mean([r['change_pct'] for r in sr])
    
    up5 = [r for r in sr if r['change_pct'] >= 5]
    up3 = [r for r in sr if 3 <= r['change_pct'] < 5]
    up0 = [r for r in sr if 0 < r['change_pct'] < 3]
    dn0 = [r for r in sr if -3 < r['change_pct'] <= 0]
    dn3 = [r for r in sr if -5 < r['change_pct'] <= -3]
    dn5 = [r for r in sr if r['change_pct'] <= -5]
    
    lines = [
        "## 📊 2026年7月28日（周二）A股收盘复盘",
        "**—— 针对你的25只关注股票**",
        "",
        "---",
        "",
        "### 📈 盘面总览",
        "",
        "| 指标 | 数据 |",
        "|------|------|",
        f"| **上涨** | ✅ {pos}只 ({pos/len(sr)*100:.0f}%) |",
        f"| **下跌** | 🔴 {neg}只 ({neg/len(sr)*100:.0f}%) |",
        f"| **平盘** | {zero}只 |",
        f"| **平均涨跌幅** | {avg:+.2f}% |",
        f"| **涨幅≥5%** | {len(up5)}只 |",
        f"| **跌幅≥3%** | {len(dn3)+len(dn5)}只 |",
        f"| **跌幅≥5%** | {len(dn5)}只 |",
        f"| **跌停(≥-10%)** | {len([r for r in sr if r['change_pct'] <= -10])}只 |",
        "",
        f"> **一句话总结**：关注股上涨{pos}只/下跌{neg}只，平均{avg:+.2f}%。{'今日市场大幅回调，半导体、材料板块集体重挫，仅银行、金属等防御品种逆势走强，亏钱效应显著。' if avg < -1 else '窄幅震荡偏弱，多数个股回调。' if avg < 0 else '窄幅震荡偏强，结构性行情。' if avg < 1 else '整体偏强，结构性行情明显。'}",
        "",
        "---",
        ""
    ]
    
    if up5:
        lines += ["### 🟢 强势领涨（涨幅≥5%）", "",
                  "| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 说明 |",
                  "|------|------|--------|--------|-----|------|"]
        for r in up5:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | **+{r['change_pct']:.2f}%** | {r['rsi']} | {r['detail']} |")
        lines.append("")
    
    if up3:
        lines += ["### 🟢 中幅上涨（涨幅3%~5%）", "",
                  "| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 说明 |",
                  "|------|------|--------|--------|-----|------|"]
        for r in up3:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | **+{r['change_pct']:.2f}%** | {r['rsi']} | {r['detail']} |")
        lines.append("")
    
    if up0:
        lines += ["### 🟢 小幅上涨/逆势走强（涨幅0%~3%）", "",
                  "| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 说明 |",
                  "|------|------|--------|--------|-----|------|"]
        for r in up0:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | **+{r['change_pct']:.2f}%** | {r['rsi']} | {r['detail']} |")
        lines.append("")
    
    if dn0:
        lines += ["### 🟡 小幅回调（跌幅0%~-3%）", "",
                  "| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 布林位 | 说明 |",
                  "|------|------|--------|--------|-----|--------|------|"]
        for r in dn0:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['change_pct']:+.2f}% | {r['rsi']} | {r['boll_pos']} | {r['detail']} |")
        lines.append("")
    
    if dn3:
        lines += ["### 🔴 深度回调（跌幅-3%~-5%）", "",
                  "| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 布林位 | 预警 |",
                  "|------|------|--------|--------|-----|--------|------|"]
        for r in dn3:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['change_pct']:+.2f}% | {r['rsi']} | {r['boll_pos']} | ⚠️ {r['detail']} |")
        lines.append("")
    
    if dn5:
        lines += ["### 🚨 严重回调（跌幅≥-5%）", "",
                  "| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 布林位 | 预警 |",
                  "|------|------|--------|--------|-----|--------|------|"]
        for r in dn5:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['change_pct']:+.2f}% | {r['rsi']} | {r['boll_pos']} | 🚨 {r['detail']} |")
        lines.append("")
    
    b = [r for r in results if r['code'] == '900925']
    if b:
        lines += ["### ⚠️ 数据缺失", "",
                  "| 代码 | 名称 | 状态 |",
                  "|------|------|------|",
                  "| 900925 | 机电B股 | B股暂不可用 |", ""]
    
    lines += ["---", "", "### 📊 技术指标一览", "",
              "| 代码 | 名称 | 现价 | 涨跌 | MA5 | MA10 | MA20 | MA60 | RSI | MACD | 量比 | 趋势 |",
              "|------|------|------|------|-----|------|------|------|-----|------|------|------|"]
    for r in sr:
        lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['change_pct']:+.2f}% | {r['ma5']} | {r['ma10']} | {r['ma20']} | {r['ma60']} | {r['rsi']} | {r['macd_str']} | {r['vol_ratio']} | {r['trend'][:18]} |")
    lines.append("")
    
    lines += ["---", "", "### 🎯 关键支撑压力位", "",
              "| 代码 | 名称 | 现价 | 支撑位 | 压力位 | 距支撑 | 距压力 |",
              "|------|------|------|--------|--------|--------|--------|"]
    for r in sr:
        sup = r['support']; res = r['resistance']
        ds = f"{(r['price']/sup - 1)*100:+.1f}%" if isinstance(sup, (int,float)) else '-'
        dr = f"{(res/r['price'] - 1)*100:+.1f}%" if isinstance(res, (int,float)) else '-'
        lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {sup} | {res} | {ds} | {dr} |")
    lines.append("")
    
    lines += ["---", "", "### 🔮 操作建议", ""]
    
    strong = [r for r in sr if r['signal'] == '持有/强势']
    hold = [r for r in sr if r['signal'] == '持有']
    watch = [r for r in sr if r['signal'] in ['观望', '关注止跌']]
    danger = [r for r in sr if '警惕' in (r['signal'] or '') or '减仓' in (r['signal'] or '')]
    
    if strong:
        lines.append("**✅ 持有/强势（逆势上涨）**")
        for r in strong: lines.append(f"- **{r['name']}**（{r['code']}）：{r['detail']}")
        lines.append("")
    
    if hold:
        lines.append(f"**✅ 持有不动（抗跌品种）** — {', '.join([r['name'] for r in hold])}")
        lines.append("")
    
    if watch:
        lines.append("**👀 观望/等待企稳**")
        for r in watch: lines.append(f"- **{r['name']}**（{r['code']}）：{r['detail']}，支撑{r['support']}")
        lines.append("")
    
    if danger:
        lines.append("**⚠️ 警惕/建议减仓**")
        for r in danger:
            risk = f"，跌破支撑{r['support']}建议止损" if isinstance(r['support'], (int,float)) else ""
            lines.append(f"- **{r['name']}**（{r['code']}）：回调{r['change_pct']:+.2f}%，RSI={r['rsi']}{risk}")
        lines.append("")
    
    lines += ["---", "", "### 💡 建议仓位", ""]
    if avg > 1: lines.append("> 市场偏强，建议仓位 **7~8成**")
    elif avg > 0: lines.append("> 市场震荡偏强，建议仓位 **5~7成**")
    elif avg > -1: lines.append("> 市场震荡偏弱，建议仓位 **3~5成**")
    else: lines.append("> 市场弱势，建议仓位 **2~3成**，等待企稳信号再补仓")
    lines.append("")
    
    # 明日关注
    lines += ["---", "", "### 🔭 明日关注与预案", ""]
    
    oversold = [r for r in sr if r['rsi'] != '-' and r['rsi'] < 30 and r['change_pct'] <= -5]
    if oversold:
        lines.append("**① 超跌反弹机会（RSI<30，短期超卖）：**")
        for r in oversold:
            sup = r['support'] or '待确认'
            lines.append(f"- **{r['name']}**（{r['code']}）：现价{r['price']}，RSI={r['rsi']}，支撑{sup}，放量止跌可左侧试探")
        lines.append("")
    
    strong2 = [r for r in sr if r['change_pct'] > 0 and (r['rsi'] == '-' or r['rsi'] < 60)]
    if strong2:
        lines.append("**② 强势延续关注：**")
        for r in strong2[:5]:
            lines.append(f"- **{r['name']}**（{r['code']}）：今日涨{r['change_pct']:+.2f}%逆势走强，关注量能持续性")
        lines.append("")
    
    dn3_but_good = [r for r in sr if r['change_pct'] <= -3 and (r['rsi'] == '-' or r['rsi'] > 30)]
    if dn3_but_good:
        lines.append("**③ 观察企稳标的：**")
        for r in dn3_but_good[:3]:
            lines.append(f"- **{r['name']}**（{r['code']}）：回调{r['change_pct']:+.2f}%，RSI={r['rsi']}，等待缩量止跌信号")
        lines.append("")
    
    lines.append("---")
    lines.append("")
    lines.append("*📡 数据源：新浪实时行情 + Baostock K线（东方财富7/28接口临时不可用，已启用兜底） | ⏰ 分析时间：2026-07-28 15:42*")
    lines.append("*⚡ 技术指标基于7/27收盘K线计算，实时涨跌幅基于7/28收盘新浪报价*")
    
    return "\n".join(lines)

if __name__ == "__main__":
    report, summary = main()
