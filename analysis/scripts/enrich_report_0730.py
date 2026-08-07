#!/usr/bin/env python3
"""Enrich the report with technical indicators from baostock"""
import json

with open('/tmp/indicators_0730.json') as f:
    ind = json.load(f)

# Spot data from the saved report
spots = {
    "002318": {"name":"久立特材","price":20.42,"pct":0.49},
    "300014": {"name":"亿纬锂能","price":54.22,"pct":1.88},
    "601066": {"name":"中信建投","price":25.84,"pct":-0.12},
    "600030": {"name":"中信证券","price":28.49,"pct":-0.04},
    "300124": {"name":"汇川技术","price":63.51,"pct":3.08},
    "601995": {"name":"中金公司","price":35.20,"pct":0.23},
    "600584": {"name":"长电科技","price":64.51,"pct":-10.00},
    "002156": {"name":"通富微电","price":55.89,"pct":-10.00},
    "002466": {"name":"天齐锂业","price":45.46,"pct":-1.34},
    "600036": {"name":"招商银行","price":40.55,"pct":2.24},
    "600570": {"name":"恒生电子","price":22.25,"pct":1.74},
    "605566": {"name":"福莱蒽特","price":21.63,"pct":-6.85},
    "000987": {"name":"越秀资本","price":7.79,"pct":0.39},
    "603308": {"name":"应流股份","price":41.49,"pct":-5.49},
    "300285": {"name":"国瓷材料","price":61.28,"pct":3.65},
    "002413": {"name":"雷科防务","price":7.73,"pct":-2.52},
    "688981": {"name":"中芯国际","price":122.99,"pct":-6.04},
    "601865": {"name":"福莱特","price":9.99,"pct":1.42},
    "000157": {"name":"中联重科","price":7.73,"pct":1.58},
    "300719": {"name":"安达维尔","price":10.98,"pct":-2.14},
    "601061": {"name":"中信金属","price":11.53,"pct":-1.37},
    "600660": {"name":"福耀玻璃","price":59.80,"pct":1.18},
    "002335": {"name":"科华数据","price":27.71,"pct":-3.79},
    "601100": {"name":"恒立液压","price":103.00,"pct":0.07}
}

print("""
## 📊 技术指标汇总

| 代码 | 名称 | 最新价 | 涨跌幅 | 趋势 | MA5 | MA10 | MA20 | MA60 | RSI(14) | 布林下轨 | 布林上轨 | 量比 |
|------|------|--------|--------|------|-----|------|------|------|---------|---------|---------|------|""")

for code, s in spots.items():
    i = ind.get(code, {})
    trend = i.get('trend', 'N/A')
    ma5 = i.get('ma5', 'N/A')
    ma10 = i.get('ma10', 'N/A')
    ma20 = i.get('ma20', 'N/A')
    ma60 = i.get('ma60', 'N/A')
    rsi = i.get('rsi', 'N/A')
    bb_l = i.get('bb_lower', 'N/A')
    bb_u = i.get('bb_upper', 'N/A')
    vr = i.get('vol_ratio', 'N/A')
    pct = s['pct']
    price = s['price']
    
    # Trend emoji
    t_icon = ""
    if '多头排列' in trend: t_icon = "🟢多头"
    elif '短期多头' in trend: t_icon = "🟢短多"
    elif '空头排列' in trend: t_icon = "🔴空头"
    elif '短期空头' in trend: t_icon = "🔴短空"
    else: t_icon = "🟡交织"
    
    print(f"| {code} | {s['name']} | {price} | {pct:+.2f}% | {t_icon} | {ma5} | {ma10} | {ma20} | {ma60} | {rsi} | {bb_l} | {bb_u} | {vr} |")

print()

# Enhanced analysis
print("## 🔍 技术面深度分析\n")

print("### 🟢 多头排列 / 短期多头（趋势向好）")
for code, s in spots.items():
    i = ind.get(code, {})
    trend = i.get('trend', '')
    if '多头' in trend:
        rsi = i.get('rsi', 50)
        rsi_flag = "⚠️超买" if rsi and rsi > 70 else ""
        print(f"- **{s['name']}({code})** {trend} | 价{s['price']} | MA5={i.get('ma5','?')} MA20={i.get('ma20','?')} | RSI={rsi} {rsi_flag}")

print()
print("### 🔴 空头排列 / 短期空头（趋势偏弱）")
for code, s in spots.items():
    i = ind.get(code, {})
    trend = i.get('trend', '')
    if '空头' in trend:
        rsi = i.get('rsi', 50)
        rsi_flag = "💡超卖" if rsi and rsi < 30 else ""
        vol = i.get('vol_ratio', 1)
        vol_flag = "放量下跌" if vol and vol > 1.2 else ""
        print(f"- **{s['name']}({code})** {trend} | 价{s['price']} | MA5={i.get('ma5','?')} MA20={i.get('ma20','?')} | RSI={rsi} {rsi_flag} {vol_flag}")

print()
print("### 🟡 均线交织（方向待定）")
for code, s in spots.items():
    i = ind.get(code, {})
    trend = i.get('trend', '')
    if '交织' in trend:
        print(f"- **{s['name']}({code})** {s['pct']:+.2f}% | 价{s['price']} | MA5={i.get('ma5','?')} MA10={i.get('ma10','?')} MA20={i.get('ma20','?')}")

print()
print("### ⚡ 超买预警（RSI>70，注意回调）")
for code, s in spots.items():
    i = ind.get(code, {})
    rsi = i.get('rsi', 0)
    if rsi and rsi > 70:
        print(f"- **{s['name']}({code})** RSI={rsi} | 最新{s['price']} | 建议逢高减仓")

print()
print("### 💡 超卖机会（RSI<30，关注反弹）")
for code, s in spots.items():
    i = ind.get(code, {})
    rsi = i.get('rsi', 100)
    if rsi and rsi < 30:
        print(f"- **{s['name']}({code})** RSI={rsi} | 最新{s['price']} | 短线超卖，关注反弹机会")
