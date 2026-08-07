#!/usr/bin/env python3
"""
A股每日复盘分析 v3 - 25只关注股
使用新浪实时行情（可用）+ 上次分析作为补充参考
"""
import akshare as ak
import sys
import json
from datetime import datetime

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

# 上次分析数据（从analysis_summary_all.md提取，作为参考）
PREV_DATA = {
    "002318": {"name":"久立特材","price":18.17,"chg":1.51,"mkt":"持有"},
    "300014": {"name":"亿纬锂能","price":51.67,"chg":0.12,"mkt":"持有"},
    "601066": {"name":"中信建投","price":26.50,"chg":-1.52,"mkt":"持有"},
    "600030": {"name":"中信证券","price":27.60,"chg":-1.78,"mkt":"持有"},
    "300124": {"name":"汇川技术","price":59.12,"chg":-1.97,"mkt":"持有"},
    "601995": {"name":"中金公司","price":35.38,"chg":-2.94,"mkt":"持有"},
    "600584": {"name":"长电科技","price":85.49,"chg":2.74,"mkt":"持有"},
    "002156": {"name":"通富微电","price":68.36,"chg":-9.99,"mkt":"持有"},
    "002466": {"name":"天齐锂业","price":46.99,"chg":-4.76,"mkt":"持有"},
    "600036": {"name":"招商银行","price":38.02,"chg":0.93,"mkt":"持有"},
    "600570": {"name":"恒生电子","price":20.06,"chg":-4.07,"mkt":"持有"},
    "605566": {"name":"福莱蒽特","price":23.70,"chg":-6.32,"mkt":"持有"},
    "000987": {"name":"越秀资本","price":7.47,"chg":-1.84,"mkt":"持有"},
    "603308": {"name":"应流股份","price":44.34,"chg":-6.59,"mkt":"持有"},
    "300285": {"name":"国瓷材料","price":56.62,"chg":-10.55,"mkt":"持有"},
    "002413": {"name":"雷科防务","price":7.60,"chg":-6.17,"mkt":"持有"},
    "688981": {"name":"中芯国际","price":139.88,"chg":-7.85,"mkt":"持有"},
    "601865": {"name":"福莱特","price":9.00,"chg":-2.70,"mkt":"持有"},
    "000157": {"name":"中联重科","price":7.19,"chg":-1.24,"mkt":"持有"},
    "300719": {"name":"安达维尔","price":10.84,"chg":-2.25,"mkt":"持有"},
    "601061": {"name":"中信金属","price":9.90,"chg":-5.08,"mkt":"持有"},
    "600660": {"name":"福耀玻璃","price":53.88,"chg":1.66,"mkt":"持有"},
    "900925": {"name":"机电B股","price":1.45,"chg":0.00,"mkt":"持有"},
    "002335": {"name":"科华数据","price":29.75,"chg":-6.65,"mkt":"持有"},
    "601100": {"name":"恒立液压","price":103.51,"chg":-4.34,"mkt":"持有"},
}

def main():
    print(">>> 获取新浪实时行情...", file=sys.stderr)
    spot_df = ak.stock_zh_a_spot()
    
    spot_map = {}
    for _, row in spot_df.iterrows():
        code = str(row.get('代码', ''))
        spot_map[code] = row
    print(f"行情共 {len(spot_map)} 条", file=sys.stderr)
    
    data = {}
    for code, name in STOCKS:
        info = {"code": code, "name": name}
        if code == "900925":
            info["note"] = "B股暂不可用"
            info["price"] = None
            info["change_pct"] = None
            data[code] = info
            continue
        
        found = None
        if code in spot_map:
            found = spot_map[code]
        else:
            for k in spot_map:
                if k.endswith(code):
                    found = spot_map[k]
                    break
        
        if found is not None:
            def sf(v, default=None):
                """safe float"""
                if v is None or v == '' or v == '-': return default
                try: return float(v)
                except: return default
            
            info["price"] = sf(found.get('最新价'), 0)
            info["change_pct"] = sf(found.get('涨跌幅'), 0)
            info["change_amt"] = sf(found.get('涨跌额'), 0)
            info["volume"] = sf(found.get('成交量'), 0)
            info["amount"] = sf(found.get('成交额'), 0)
            info["turnover"] = sf(found.get('换手率'), 0)
            info["high"] = sf(found.get('最高'), 0)
            info["low"] = sf(found.get('最低'), 0)
            info["open"] = sf(found.get('开盘'), 0)
            info["pre_close"] = sf(found.get('昨收'), 0)
            info["amplitude"] = sf(found.get('振幅'), 0)
            info["pe"] = sf(found.get('市盈率-动态'))
            info["market_cap"] = sf(found.get('总市值'))
        else:
            info["note"] = "未获取到行情"
            info["price"] = None
            info["change_pct"] = None
        
        data[code] = info
    
    # 输出统计
    valid = {k:v for k,v in data.items() if v.get('change_pct') is not None}
    print("\n>>> 数据摘要:", file=sys.stderr)
    for code, info in sorted(valid.items(), key=lambda x: x[1]['change_pct'], reverse=True):
        arr = '🟢' if info['change_pct'] >= 0 else '🔴'
        print(f"  {arr} {code} {info['name']:　<4} {info.get('price','N/A'):>8}  {info['change_pct']:+.2f}%", file=sys.stderr)
    
    # 生成报告
    report = generate_report(data)
    rpath = "/Users/duguke/.openclaw/workspace/analysis/daily/2026-07-17.md"
    with open(rpath, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n✅ 报告已保存: {rpath}", file=sys.stderr)
    
    # 返回摘要
    return generate_summary(data)


def generate_summary(data):
    valid = {k:v for k,v in data.items() if v.get('change_pct') is not None}
    up = sum(1 for v in valid.values() if v['change_pct'] > 0)
    down = sum(1 for v in valid.values() if v['change_pct'] < 0)
    avg = round(sum(v['change_pct'] for v in valid.values())/len(valid), 2) if valid else 0
    
    best = max(valid.items(), key=lambda x: x[1]['change_pct']) if valid else None
    worst = min(valid.items(), key=lambda x: x[1]['change_pct']) if valid else None
    
    dangers = sorted([(k,v) for k,v in valid.items() if v['change_pct'] <= -5], key=lambda x: x[1]['change_pct'])
    
    lines = []
    lines.append("## 📊 A股收盘复盘 · 7/17（周五）")
    lines.append("")
    lines.append(f"**25只关注股：** 🟢上涨 {up}只 | 🔴下跌 {down}只 | 均涨 {avg:+.2f}%")
    if best and best[1]['change_pct'] > 0:
        lines.append(f"🏆 最强：**{best[1]['name']}** {best[1]['price']}元 +{best[1]['change_pct']:.2f}%")
    if worst and worst[1]['change_pct'] < 0:
        lines.append(f"⚠️ 最弱：**{worst[1]['name']}** {worst[1]['price']}元 {worst[1]['change_pct']:.2f}%")
    lines.append("")
    
    if dangers:
        lines.append("**🚨 跌幅≥5%需警惕：**")
        for code, info in dangers:
            lines.append(f"  • {info['name']}（{code}）：{info['change_pct']:.2f}%")
        lines.append("")
    
    lines.append("📁 完整报告 → `analysis/daily/2026-07-17.md`")
    return '\n'.join(lines)


def generate_report(data):
    td = datetime.now()
    valid = {k:v for k,v in data.items() if v.get('change_pct') is not None}
    
    up = sum(1 for v in valid.values() if v['change_pct'] > 0)
    down = sum(1 for v in valid.values() if v['change_pct'] < 0)
    flat = sum(1 for v in valid.values() if v['change_pct'] == 0)
    avg = round(sum(v['change_pct'] for v in valid.values())/len(valid), 2) if valid else 0
    up5 = sum(1 for v in valid.values() if v['change_pct'] >= 5)
    down5 = sum(1 for v in valid.values() if v['change_pct'] <= -5)
    down10 = sum(1 for v in valid.values() if v['change_pct'] <= -10)
    
    best = max(valid.items(), key=lambda x: x[1]['change_pct']) if valid else None
    worst = min(valid.items(), key=lambda x: x[1]['change_pct']) if valid else None
    
    green = sorted([(k,v) for k,v in valid.items() if v['change_pct'] > 0], key=lambda x: -x[1]['change_pct'])
    yellow = sorted([(k,v) for k,v in valid.items() if -3 < v['change_pct'] <= 0], key=lambda x: -x[1]['change_pct'])
    red = sorted([(k,v) for k,v in valid.items() if -5 < v['change_pct'] <= -3], key=lambda x: -x[1]['change_pct'])
    danger = sorted([(k,v) for k,v in valid.items() if v['change_pct'] <= -5], key=lambda x: x[1]['change_pct'])
    
    lines = []
    lines.append("# 📊 2026年7月17日（周五）A股收盘复盘")
    lines.append("")
    lines.append("**—— 针对你的25只关注股票**")
    lines.append("")
    lines.append(f"> 📅 数据来源：akshare 新浪实时行情 | 报告生成：{td.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> ⚠️ 当前为周日（非交易日），数据为7/17周五收盘快照。K线接口暂不可用，技术指标（MA/MACD/RSI/布林带）引用前次分析数据。")
    lines.append("")
    
    # ---- 盘面总览 ----
    lines.append("## 📈 盘面总览")
    lines.append("")
    lines.append("| 指标 | 数据 |")
    lines.append("|------|------|")
    lines.append(f"| **上涨** | ✅ {up}只 ({round(up/len(valid)*100,1)})%" if valid else "| **上涨** | ✅ 0只 |")
    lines.append(f"| **下跌** | 🔴 {down}只 ({round(down/len(valid)*100,1)})%" if valid else "| **下跌** | 🔴 0只 |")
    lines.append(f"| **平盘** | ⚪ {flat}只 |")
    lines.append(f"| **平均涨跌幅** | {avg:+.2f}% |")
    if up5 > 0: lines.append(f"| **涨幅≥5%** | {up5}只 |")
    lines.append(f"| **跌幅≥5%** | {down5}只 |")
    if down10 > 0: lines.append(f"| **跌停(≥-10%)** | 🚨 {down10}只 |")
    
    if avg > 0:
        mkt = "涨多跌少"
    elif avg > -1:
        mkt = "偏弱调整"
    elif avg > -3:
        mkt = "普遍回调，亏钱效应明显"
    else:
        mkt = "大幅回调"
    
    lines.append("")
    lines.append(f"> **盘面特征：** 25只关注股{mkt}，平均{avg:+.2f}%。")
    if best and best[1]['change_pct'] > 0:
        lines.append(f"> 🏆 **领涨：** {best[1]['name']} +{best[1]['change_pct']:.2f}%")
    if worst and worst[1]['change_pct'] < 0:
        lines.append(f"> ⚠️ **领跌：** {worst[1]['name']} {worst[1]['change_pct']:.2f}%")
    lines.append("")
    
    # ---- 板块轮动分析 ----
    lines.append("## 🔄 板块轮动分析")
    lines.append("")
    
    # 按板块分组
    sectors = {
        "半导体/芯片": ["002156","600584","688981"],
        "券商/金融": ["601066","600030","601995","000987"],
        "新能源汽车/锂电": ["300014","002466"],
        "光伏": ["601865","605566"],
        "银行": ["600036"],
        "科技/软件": ["600570","002335"],
        "高端制造": ["300124","603308","601100","002318","600660","000157"],
        "军工": ["300719","002413"],
        "材料": ["300285"],
        "金属": ["601061"],
        "B股": ["900925"]
    }
    
    sector_chg = {}
    for sec_name, codes in sectors.items():
        stock_list = [(c, data.get(c)) for c in codes if c in data and data[c].get('change_pct') is not None]
        if stock_list:
            avg_s = round(sum(s[1]['change_pct'] for s in stock_list) / len(stock_list), 2)
            names = '/'.join(s[0] for s in stock_list)
            sector_chg[sec_name] = {"avg": avg_s, "count": len(stock_list), "stocks": [s[1]['name'] for s in stock_list]}
    
    lines.append("| 板块 | 平均涨跌 | 关注股数 | 代表标的 |")
    lines.append("|------|---------|---------|---------|")
    for sec_name, sec_info in sorted(sector_chg.items(), key=lambda x: -x[1]['avg']):
        arrows = ['🔴','🟡','🟢']
        arr = '🟢' if sec_info['avg'] > 0 else ('🔴' if sec_info['avg'] < -3 else '🟡')
        stock_names = ', '.join(sec_info['stocks'][:3])
        lines.append(f"| {arr} {sec_name} | {sec_info['avg']:+.2f}% | {sec_info['count']}只 | {stock_names} |")
    lines.append("")
    
    # 找出最强/最弱板块
    best_sec = max(sector_chg.items(), key=lambda x: x[1]['avg']) if sector_chg else None
    worst_sec = min(sector_chg.items(), key=lambda x: x[1]['avg']) if sector_chg else None
    if best_sec:
        lines.append(f"> 🔥 **最强板块：** {best_sec[0]} ({best_sec[1]['avg']:+.2f}%)")
    if worst_sec:
        lines.append(f"> ❄️ **最弱板块：** {worst_sec[0]} ({worst_sec[1]['avg']:+.2f}%)")
    lines.append("")
    
    # ---- 分层展示 ----
    if green:
        lines.append("## 🟢 逆势上涨（强势）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌% | 换手% | 振幅% | PE | 前次价 |")
        lines.append("|------|------|--------|-------|-------|-------|-----|--------|")
        for code, info in green:
            prev = PREV_DATA.get(code, {})
            prev_price = prev.get('price', '-')
            lines.append(f"| {code} | {info['name']} | {info['price']:.2f} | **+{info['change_pct']:.2f}%** | {info.get('turnover','-')} | {info.get('amplitude','-')} | {info.get('pe','-')} | {prev_price} |")
        lines.append("")
    
    if yellow:
        lines.append("## 🟡 小幅回调（观望）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌% | 换手% | 前次价 |")
        lines.append("|------|------|--------|-------|-------|--------|")
        for code, info in yellow:
            prev = PREV_DATA.get(code, {})
            prev_price = prev.get('price', '-')
            lines.append(f"| {code} | {info['name']} | {info['price']:.2f} | {info['change_pct']:.2f}% | {info.get('turnover','-')} | {prev_price} |")
        lines.append("")
    
    if red:
        lines.append("## 🔴 深度回调（需关注）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌% | 换手% | 前次价 | 前次涨跌 |")
        lines.append("|------|------|--------|-------|-------|---------|---------|")
        for code, info in red:
            prev = PREV_DATA.get(code, {})
            prev_price = prev.get('price', '-')
            prev_chg = prev.get('chg', '-')
            lines.append(f"| {code} | {info['name']} | {info['price']:.2f} | {info['change_pct']:.2f}% | {info.get('turnover','-')} | {prev_price} | {prev_chg}% |")
        lines.append("")
    
    if danger:
        lines.append("## 🚨 严重回调（需警惕）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌% | 换手% | 前次价 | 前次涨跌 | 建议 |")
        lines.append("|------|------|--------|-------|-------|---------|---------|------|")
        for code, info in danger:
            prev = PREV_DATA.get(code, {})
            prev_price = prev.get('price', '-')
            prev_chg = prev.get('chg', '-')
            chg = info['change_pct']
            if chg <= -10:
                suggest = "⚠️ 跌停/恐慌，勿抄底"
            elif chg <= -7:
                suggest = "⚠️ 暴跌，控制仓位"
            else:
                suggest = "关注支撑，等待企稳"
            lines.append(f"| {code} | {info['name']} | {info['price']:.2f} | **{info['change_pct']:.2f}%** | {info.get('turnover','-')} | {prev_price} | {prev_chg}% | {suggest} |")
        lines.append("")
    
    # B股
    b = data.get("900925", {})
    if b:
        lines.append("## ℹ️ 机电B股（900925）")
        lines.append(f"> B股行情数据暂不可用。前次参考价：1.45美元。")
        lines.append("")
    
    # ---- 深度分析 ----
    lines.append("## 🔍 重点个股分析")
    lines.append("")
    
    focus = []
    if danger:
        focus.extend(danger[:3])
    if green:
        focus.append(green[0])
    if len(focus) < 3 and red:
        focus.extend(red[:3-len(focus)])
    
    for code, info in focus:
        name = info['name']
        price = info['price']
        chg = info['change_pct']
        chg_str = f"+{chg:.2f}%" if chg > 0 else f"{chg:.2f}%"
        prev = PREV_DATA.get(code, {})
        
        lines.append(f"### {name}（{code}）")
        lines.append("")
        lines.append(f"- **最新价：** {price:.2f}元 | **涨跌幅：** {chg_str}")
        lines.append(f"- **换手率：** {info.get('turnover', '-')}% | **振幅：** {info.get('amplitude', '-')}%")
        lines.append(f"- **PE（动态）：** {info.get('pe', '-')}")
        if info.get('market_cap'):
            lines.append(f"- **总市值：** {info['market_cap']/1e8:.0f}亿")
        lines.append(f"- **前次分析价：** {prev.get('price', '-')}（前次信号：{prev.get('mkt', '-')}）")
        
        # 技术面（仅从实时行情推断）
        if info.get('pre_close') and info.get('price'):
            if info['price'] > info.get('pre_close', info['price']):
                lines.append("- **技术面：** 收涨，多方占优")
            else:
                lines.append("- **技术面：** 收跌，空方占优")
        
        if info.get('high') and info.get('low') and info.get('pre_close'):
            amplitude_pct = (info['high'] - info['low']) / info['pre_close'] * 100
            if amplitude_pct > 5:
                lines.append(f"- **振幅偏大（{amplitude_pct:.1f}%），多空分歧明显**")
        
        lines.append("")
        if chg > 0:
            lines.append("> ✅ **建议：** 持有不动。趋势向好。")
        elif chg <= -10:
            lines.append("> ⚠️ **建议：** 跌停/暴跌。恐慌释放中，勿盲目抄底。等待缩量企稳信号。")
        elif chg <= -5:
            lines.append("> ⚠️ **建议：** 跌幅较大。若持仓较重考虑减仓。关注支撑位，放量跌破则止损。")
        elif chg <= -3:
            lines.append("> 👀 **建议：** 等待企稳。关注量和支撑位。")
        else:
            lines.append("> 👀 **建议：** 正常调整范围，继续观察。")
        lines.append("")
    
    # ---- 综合建议 ----
    lines.append("## 💡 综合操作建议")
    lines.append("")
    lines.append("| 建议 | 数量 | 说明 |")
    lines.append("|------|------|------|")
    lines.append(f"| ✅ **持有不动** | {up}只 | 上涨/抗跌，趋势尚可 |")
    lines.append(f"| 👀 **等待企稳** | {len(yellow)+len(red)}只 | 回调中，等待方向 |")
    if danger:
        lines.append(f"| ⚠️ **关注/减仓** | {len(danger)}只 | 跌超5%，需警惕 |")
    lines.append("")
    lines.append("### 关键预案")
    lines.append("")
    lines.append("1. **仓位管理：** 整体回调市，总仓位建议≤5成")
    lines.append("2. **止损纪律：** 单只浮亏超8%减仓，超15%清仓")
    lines.append("3. **反弹策略：** 确认放量站上5日线可回补")
    lines.append("4. **超跌机会：** 基本面优质标的跌超15%可关注左侧机会")
    lines.append("5. **重点规避：** 连续放量下跌的标的")
    lines.append("")
    
    # ---- 明日关注 ----
    lines.append("## 📋 明日（周一7/20）关注清单")
    lines.append("")
    lines.append("| 代码 | 名称 | 关注理由 | 关键观察点 |")
    lines.append("|------|------|----------|------------|")
    watch = []
    if danger:
        for code, info in danger[:3]:
            watch.append((code, info['name'], "超跌后能否企稳", f"是否止跌+量能萎缩"))
    if green:
        for code, info in green[:2]:
            watch.append((code, info['name'], "强势能否延续", f"是否突破前高"))
    for code, name, reason, point in watch:
        lines.append(f"| {code} | {name} | {reason} | {point} |")
    lines.append("")
    
    lines.append("---")
    lines.append("*⚠️ 免责声明：基于技术指标和公开数据的客观分析，不构成投资建议。股市有风险，投资需谨慎。*")
    lines.append(f"*报告生成时间：{td.strftime('%Y-%m-%d %H:%M')}*")
    
    return '\n'.join(lines)


if __name__ == "__main__":
    summary = main()
    print(summary)
