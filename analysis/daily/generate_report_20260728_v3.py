#!/usr/bin/env python3
"""
每日A股复盘分析 v3 - 2026-07-28
使用 baostock 获取K线数据（东方财富接口不可用时的兜底方案）
"""
import baostock as bs
import pandas as pd
import numpy as np
import json
import warnings
import time

warnings.filterwarnings('ignore')

STOCKS = {
    "002318": "久立特材", "300014": "亿纬锂能", "601066": "中信建投",
    "600030": "中信证券", "300124": "汇川技术", "601995": "中金公司",
    "600584": "长电科技", "002156": "通富微电", "002466": "天齐锂业",
    "600036": "招商银行", "600570": "恒生电子", "605566": "福莱蒽特",
    "000987": "越秀资本", "603308": "应流股份", "300285": "国瓷材料",
    "002413": "雷科防务", "688981": "中芯国际", "601865": "福莱特",
    "000157": "中联重科", "300719": "安达维尔", "601061": "中信金属",
    "600660": "福耀玻璃", "900925": "机电B股", "002335": "科华数据",
    "601100": "恒立液压"
}

def bs_code(code):
    c = str(code).strip()
    if c.startswith('6') or c.startswith('9'):
        return f"sh.{c}"
    elif c.startswith('0') or c.startswith('3'):
        return f"sz.{c}"
    return c

def fetch_all_baostock():
    """通过baostock获取所有K线数据"""
    lg = bs.login()
    if lg.error_code != '0':
        print(f"baostock login failed: {lg.error_msg}")
        return {}
    
    all_kline = {}
    for code, name in STOCKS.items():
        if code == "900925":
            continue
        bsc = bs_code(code)
        print(f"  📥 {code} {name} ({bsc})...", end=" ", flush=True)
        try:
            rs = bs.query_history_k_data_plus(
                bsc, 'date,close,volume,amount,pctChg',
                start_date='2026-01-01', end_date='2026-07-28',
                frequency='d', adjustflag='2'
            )
            data = []
            while rs.next():
                row = rs.get_row_data()
                if row and row[0] and row[1]:
                    data.append({
                        '日期': row[0],
                        '收盘': float(row[1]) if row[1] != '' else None,
                        '成交量': float(row[2]) if row[2] != '' else 0,
                        '成交额': float(row[3]) if row[3] != '' else 0,
                        '涨跌幅': float(row[4]) if row[4] != '' else 0,
                    })
            if len(data) > 20:
                df = pd.DataFrame(data)
                df = df.dropna(subset=['收盘'])
                all_kline[code] = df
                print(f"✅ {len(df)}条, 最新{df.iloc[-1]['日期']} ¥{df.iloc[-1]['收盘']:.2f}")
            else:
                print(f"❌ 仅{len(data)}条")
        except Exception as e:
            print(f"❌ {e}")
        time.sleep(0.2)
    
    bs.logout()
    return all_kline

def compute_ma(series, w):
    return round(float(series.tail(w).mean()), 2) if len(series) >= w else None

def compute_macd(prices, fast=12, slow=26, s=9):
    if len(prices) < slow: return None, None, None
    e1 = prices.ewm(span=fast, adjust=False).mean()
    e2 = prices.ewm(span=slow, adjust=False).mean()
    m = e1 - e2
    sig = m.ewm(span=s, adjust=False).mean()
    h = m - sig
    return float(m.iloc[-1]), float(sig.iloc[-1]), float(h.iloc[-1])

def compute_rsi(prices, w=14):
    if len(prices) < w: return None
    d = prices.diff()
    g = d.where(d > 0, 0).tail(w)
    l = (-d.where(d < 0, 0)).tail(w)
    ag = g.mean()
    al = l.mean()
    if al == 0: return 100.0
    return round(100 - 100/(1 + ag/al), 1)

def compute_bb(prices, w=20):
    if len(prices) < w: return None, None, None
    m = prices.tail(w).mean()
    s = prices.tail(w).std()
    return round(float(m+2*s),2), round(float(m),2), round(float(m-2*s),2)

def analyze(code, name, kline):
    r = dict(code=code, name=name, price=None, change_pct=None, volume=None,
             ma5=None, ma10=None, ma20=None, ma60=None, macd_str='N/A',
             rsi=None, boll_upper=None, boll_mid=None, boll_lower=None,
             vol_ratio=None, trend='未知', support=None, resistance=None,
             signal='持有', detail='')
    
    if code == '900925':
        r['price'] = 'B股暂不可用'
        r['detail'] = 'B股数据暂不可用'
        return r
    if kline is None or len(kline) < 5:
        r['detail'] = '数据不足'
        return r
    
    p = kline['收盘'].astype(float)
    v = kline['成交量'].astype(float)
    chg = kline['涨跌幅'].astype(float)
    last = kline.iloc[-1]
    
    r['price'] = float(last['收盘'])
    r['change_pct'] = float(last['涨跌幅'])
    r['volume'] = int(float(last['成交量']))
    
    n = len(p)
    r['ma5'] = compute_ma(p, 5); r['ma10'] = compute_ma(p, 10)
    r['ma20'] = compute_ma(p, 20); r['ma60'] = compute_ma(p, 60)
    
    macd_l, sig_l, hist = compute_macd(p)
    if macd_l is not None:
        macd_st = '红柱🔴' if hist > 0 else '绿柱🟢'
        cross = '金叉' if macd_l > sig_l else '死叉'
        r['macd_str'] = f"{macd_st} {cross}"
    
    r['rsi'] = compute_rsi(p)
    u, m, l = compute_bb(p)
    r['boll_upper'] = u; r['boll_mid'] = m; r['boll_lower'] = l
    
    if n >= 20:
        avg_v = v.tail(20).mean()
        cur_v = v.iloc[-1]
        r['vol_ratio'] = round(float(cur_v/avg_v), 2) if avg_v > 0 else None
    
    sigs = []
    price = float(p.iloc[-1])
    if r['ma5'] and r['ma10'] and r['ma20']:
        if r['ma5'] > r['ma10'] > r['ma20']: sigs.append('多头📈')
        elif r['ma5'] < r['ma10'] < r['ma20']: sigs.append('空头📉')
        else: sigs.append('均线交织')
        sigs.append(f"MA20以上" if price > r['ma20'] else "MA20以下")
    
    if r['rsi']:
        if r['rsi'] > 70: sigs.append('超买')
        elif r['rsi'] < 30: sigs.append('超卖')
    
    if r['boll_upper'] and price >= r['boll_upper']: sigs.append('触上轨')
    elif r['boll_lower'] and price <= r['boll_lower']: sigs.append('触下轨')
    
    r['trend'] = ' | '.join(sigs) if sigs else '震荡'
    
    # 支撑压力
    if r['ma20']:
        if price > r['ma20']:
            r['support'] = r['ma20']
            r['resistance'] = r['boll_upper'] or round(price*1.05, 2)
        else:
            r['resistance'] = r['ma20']
            r['support'] = r['boll_lower'] or r['ma10'] or round(price*0.95, 2)
    
    # 详情
    parts = [f"涨跌{chg:+.2f}%" for chg in [r['change_pct']] if chg is not None]
    if r['vol_ratio']:
        parts.append('放量' if r['vol_ratio']>1.5 else ('缩量' if r['vol_ratio']<0.7 else '量平'))
    if '多头' in r['trend']: parts.append('趋势偏强')
    elif '空头' in r['trend']: parts.append('趋势偏弱')
    if r['rsi'] and r['rsi'] > 70: parts.append('⚠️超买')
    elif r['rsi'] and r['rsi'] < 30: parts.append('💡超卖')
    r['detail'] = '，'.join(parts) if parts else '正常波动'
    
    if r['change_pct'] is not None:
        if r['change_pct'] >= 5: r['signal'] = '持有/强势'
        elif r['change_pct'] > 0: r['signal'] = '持有'
        elif r['change_pct'] > -3: r['signal'] = '观望'
        elif r['change_pct'] > -5: r['signal'] = '关注止跌'
        else: r['signal'] = '警惕/减仓'
    
    return r

def main():
    print("=" * 60)
    print("📊 A股每日复盘分析 - 2026年7月28日（周二）")
    print("=" * 60)
    
    print("\n📡 获取K线数据（baostock）...")
    all_kline = fetch_all_baostock()
    
    print(f"\n📈 分析中...")
    results = []
    for code, name in STOCKS.items():
        r = analyze(code, name, all_kline.get(code))
        results.append(r)
        if r['price'] and r['price'] != 'B股暂不可用':
            print(f"  {code} {name}: ¥{r['price']} ({r['change_pct']:+.2f}%) RSI={r['rsi']}")
    
    valid = [r for r in results if r['change_pct'] is not None and r['change_pct'] != 'B股暂不可用']
    avg = np.mean([float(r['change_pct'] or 0) for r in valid])
    pos = len([r for r in valid if float(r['change_pct'] or 0) > 0])
    neg = len([r for r in valid if float(r['change_pct'] or 0) < 0])
    zero = len([r for r in valid if float(r['change_pct'] or 0) == 0])
    print(f"\n📊 统计: 上涨{pos}只 下跌{neg}只 平{zero}只 平均{avg:+.2f}%")
    
    # 生成报告
    report = gen_report(results)
    fname = "/Users/duguke/.openclaw/workspace/analysis/daily/2026-07-28.md"
    with open(fname, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"✅ 报告保存: {fname}")
    
    # 摘要
    sorted_r = sorted(valid, key=lambda x: float(x['change_pct'] or 0), reverse=True)
    summary = {
        'date': '2026年7月28日（周二）',
        'avg_change': round(avg, 2), 'positive': pos, 'negative': neg, 'total': len(valid),
        'up_5': len([r for r in sorted_r if float(r['change_pct'] or 0) >= 5]),
        'down_3': len([r for r in sorted_r if float(r['change_pct'] or 0) <= -3]),
        'down_5': len([r for r in sorted_r if float(r['change_pct'] or 0) <= -5]),
        'top_gainers': ' | '.join([f"{r['name']}({r['change_pct']:+.2f}%)" for r in sorted_r[:3]]),
        'top_losers': ' | '.join([f"{r['name']}({r['change_pct']:+.2f}%)" for r in sorted_r[-3:]]) if len(sorted_r)>=3 else '无'
    }
    sf = '/Users/duguke/.openclaw/workspace/analysis/daily/2026-07-28_summary.json'
    with open(sf, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    print(f"\n📋 核心摘要")
    print(f"  关注股上涨{pos}只/下跌{neg}只，平均{avg:+.2f}%")
    print(f"  📈 {summary['top_gainers']}")
    print(f"  📉 {summary['top_losers']}")
    
    return report, summary

def gen_report(results):
    valid = [r for r in results if r['change_pct'] is not None and r['change_pct'] != 'B股暂不可用']
    sr = sorted(valid, key=lambda x: float(x['change_pct'] or 0), reverse=True)
    pos = len([r for r in sr if float(r['change_pct'] or 0) > 0])
    neg = len([r for r in sr if float(r['change_pct'] or 0) < 0])
    zero = len([r for r in sr if float(r['change_pct'] or 0) == 0])
    avg = np.mean([float(r['change_pct'] or 0) for r in sr])
    
    up5 = [r for r in sr if float(r['change_pct'] or 0) >= 5]
    up3 = [r for r in sr if 3 <= float(r['change_pct'] or 0) < 5]
    up0 = [r for r in sr if 0 < float(r['change_pct'] or 0) < 3]
    dn0 = [r for r in sr if -3 < float(r['change_pct'] or 0) <= 0]
    dn3 = [r for r in sr if -5 < float(r['change_pct'] or 0) <= -3]
    dn5 = [r for r in sr if float(r['change_pct'] or 0) <= -5]
    
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
        f"| **上涨** | ✅ {pos}只 ({pos/max(len(sr),1)*100:.0f}%) |",
        f"| **下跌** | 🔴 {neg}只 ({neg/max(len(sr),1)*100:.0f}%) |",
        f"| **平盘** | {zero}只 |",
        f"| **平均涨跌幅** | {avg:+.2f}% |",
        f"| **涨幅≥5%** | {len(up5)}只 |",
        f"| **跌幅≥3%** | {len(dn3)}只 |",
        f"| **跌幅≥5%** | {len(dn5)}只 |",
        "",
        f"> **一句话总结**：关注股上涨{pos}只/下跌{neg}只，平均涨跌幅{avg:+.2f}%，{'整体偏弱，亏钱效应明显' if avg < -1 else '窄幅震荡偏弱' if avg < 0 else '窄幅震荡偏强' if avg < 1 else '整体偏强，结构性行情'}。",
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
        lines += ["### 🟢 小幅上涨（涨幅0%~3%）", "",
                  "| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 说明 |",
                  "|------|------|--------|--------|-----|------|"]
        for r in up0:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | **+{r['change_pct']:.2f}%** | {r['rsi']} | {r['detail']} |")
        lines.append("")
    
    if dn0:
        lines += ["### 🟡 微幅回调（跌幅0%~-3%）", "",
                  "| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 说明 |",
                  "|------|------|--------|--------|-----|------|"]
        for r in dn0:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['change_pct']:+.2f}% | {r['rsi']} | {r['detail']} |")
        lines.append("")
    
    if dn3:
        lines += ["### 🔴 深度回调（跌幅-3%~-5%）", "",
                  "| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 预警 |",
                  "|------|------|--------|--------|-----|------|"]
        for r in dn3:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['change_pct']:+.2f}% | {r['rsi']} | ⚠️ {r['detail']} |")
        lines.append("")
    
    if dn5:
        lines += ["### 🚨 严重回调（跌幅≥-5%）", "",
                  "| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 预警 |",
                  "|------|------|--------|--------|-----|------|"]
        for r in dn5:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['change_pct']:+.2f}% | {r['rsi']} | 🚨 {r['detail']} |")
        lines.append("")
    
    b = [r for r in results if r['code'] == '900925']
    if b:
        lines += ["### ⚠️ 数据缺失（B股）", "",
                  "| 代码 | 名称 | 状态 |",
                  "|------|------|------|",
                  "| 900925 | 机电B股 | B股数据暂不可用 |", ""]
    
    lines += ["---", "", "### 📊 详细技术指标", ""]
    
    lines.append("| 代码 | 名称 | 现价 | 涨跌 | MA5 | MA10 | MA20 | MA60 | RSI | MACD | 量比 | 趋势 |")
    lines.append("|------|------|------|------|-----|------|------|------|-----|------|------|------|")
    for r in sr:
        c = r['code']; n = r['name']; p = r['price']; ch = f"{r['change_pct']:+.2f}%"
        m5 = r['ma5'] or '-'; m10 = r['ma10'] or '-'; m20 = r['ma20'] or '-'; m60 = r['ma60'] or '-'
        rsi = r['rsi'] or '-'; macd = r['macd_str']; vr = r['vol_ratio'] or '-'
        tr = r['trend'][:15] if len(r['trend']) > 15 else r['trend']
        lines.append(f"| {c} | {n} | {p} | {ch} | {m5} | {m10} | {m20} | {m60} | {rsi} | {macd} | {vr} | {tr} |")
    lines.append("")
    
    lines += ["---", "", "### 🎯 关键支撑压力位", "",
              "| 代码 | 名称 | 现价 | 支撑位 | 压力位 |",
              "|------|------|------|--------|--------|"]
    for r in sr:
        lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['support'] or '-'} | {r['resistance'] or '-'} |")
    lines.append("")
    
    lines += ["---", "", "### 🔮 操作建议", ""]
    
    strong = [r for r in sr if r['signal'] == '持有/强势']
    hold = [r for r in sr if r['signal'] == '持有']
    watch = [r for r in sr if r['signal'] in ['观望', '关注止跌']]
    danger = [r for r in sr if '警惕' in (r['signal'] or '') or '减仓' in (r['signal'] or '')]
    
    if strong:
        lines.append("**✅ 持有/强势**")
        for r in strong:
            lines.append(f"- {r['name']}（{r['code']}）：{r['detail']}")
        lines.append("")
    
    if hold:
        lines.append(f"**✅ 持有不动** — {', '.join([r['name'] for r in hold])}")
        lines.append("")
    
    if watch:
        lines.append("**👀 观望/等待企稳**")
        for r in watch:
            lines.append(f"- {r['name']}（{r['code']}）：{r['detail']}")
        lines.append("")
    
    if danger:
        lines.append("**⚠️ 警惕/减仓**")
        for r in danger:
            lines.append(f"- {r['name']}（{r['code']}）：{r['detail']}")
        lines.append("")
    
    lines += ["---", "", "### 💡 建议仓位", ""]
    if avg > 1:
        lines.append("> 市场偏强，建议仓位 **7~8成**")
    elif avg > 0:
        lines.append("> 市场震荡偏强，建议仓位 **5~7成**")
    elif avg > -1:
        lines.append("> 市场震荡偏弱，建议仓位 **3~5成**")
    else:
        lines.append("> 市场弱势，建议仓位 **2~3成**")
    lines.append("")
    
    # 明日关注
    lines += ["---", "", "### 🔭 明日关注", ""]
    
    oversold = [r for r in sr if r['rsi'] and r['rsi'] < 30 and float(r['change_pct'] or 0) <= -5]
    if oversold:
        lines.append("**超跌反弹关注（RSI<30）：**")
        for r in oversold:
            lines.append(f"- {r['name']}（{r['code']}）：RSI={r['rsi']}，价格{r['price']}，支撑{r['support']}，关注是否止跌企稳")
        lines.append("")
    
    strong2 = [r for r in sr if float(r['change_pct'] or 0) > 0 and r['rsi'] and r['rsi'] < 60]
    if strong2:
        lines.append("**强势延续关注（上涨且RSI未超买）：**")
        for r in strong2[:3]:
            lines.append(f"- {r['name']}（{r['code']}）：涨{r['change_pct']:+.2f}%，RSI={r['rsi']}")
        lines.append("")
    
    if not oversold and not strong2:
        lines.append("> 明日整体偏弱，建议回避追高，等待情绪企稳后左侧布局超跌优质标的。")
        lines.append("")
    
    lines.append("---")
    lines.append("")
    lines.append("*数据来源：baostock（东方财富接口当日不可用，启用兜底方案） | 分析时间：2026-07-28 15:42*")
    
    return "\n".join(lines)

if __name__ == "__main__":
    main()
