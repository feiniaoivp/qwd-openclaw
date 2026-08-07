#!/usr/bin/env python3
"""
A股每日复盘分析 v2 - 25只关注股
只使用新浪实时行情接口（已验证可用）+ 兜底K线
"""
import akshare as ak
import pandas as pd
import json
import sys
import time
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

def get_spot_data():
    """获取实时行情"""
    print(">>> 获取新浪实时行情...", file=sys.stderr)
    spot_df = ak.stock_zh_a_spot()
    
    results = {}
    for _, row in spot_df.iterrows():
        code = str(row.get('代码', ''))
        if code in results:
            continue
        results[code] = {
            'name': row.get('名称', ''),
            'price': float(row.get('最新价', 0)) if row.get('最新价') not in [None, '', '-'] else 0,
            'change_pct': float(row.get('涨跌幅', 0)) if row.get('涨跌幅') not in [None, '', '-'] else 0,
            'change_amt': float(row.get('涨跌额', 0)) if row.get('涨跌额') not in [None, '', '-'] else 0,
            'volume': float(row.get('成交量', 0)) if row.get('成交量') not in [None, '', '-'] else 0,
            'amount': float(row.get('成交额', 0)) if row.get('成交额') not in [None, '', '-'] else 0,
            'turnover': float(row.get('换手率', 0)) if row.get('换手率') not in [None, '', '-'] else 0,
            'high': float(row.get('最高', 0)) if row.get('最高') not in [None, '', '-'] else 0,
            'low': float(row.get('最低', 0)) if row.get('最低') not in [None, '', '-'] else 0,
            'open': float(row.get('开盘', 0)) if row.get('开盘') not in [None, '', '-'] else 0,
            'pre_close': float(row.get('昨收', 0)) if row.get('昨收') not in [None, '', '-'] else 0,
            'amplitude': float(row.get('振幅', 0)) if row.get('振幅') not in [None, '', '-'] else 0,
            'pe': row.get('市盈率-动态', None),
            'market_cap': row.get('总市值', None),
            'circulating_cap': row.get('流通市值', None),
            'pb': row.get('市净率', None),
        }
    print(f"新浪行情共 {len(results)} 条记录", file=sys.stderr)
    return results


def main():
    # 获取实时行情
    spot_data = get_spot_data()
    
    # 构建关注股数据
    data = {}
    for code, name in STOCKS:
        stock_info = {"code": code, "name": name, "errors": []}
        
        if code == "900925":
            stock_info["note"] = "B股数据暂不可用"
            stock_info["price"] = None
            stock_info["change_pct"] = None
            data[code] = stock_info
            continue
        
        # 从实时行情中查找
        found = None
        if code in spot_data:
            found = spot_data[code]
        else:
            # 尝试不同前缀
            for key in spot_data:
                if key.endswith(code):
                    found = spot_data[key]
                    break
        
        if found:
            stock_info.update(found)
            data[code] = stock_info
        else:
            stock_info["note"] = "行情数据未获取到"
            stock_info["price"] = None
            stock_info["change_pct"] = None
            data[code] = stock_info
    
    # 尝试获取K线（单个请求，减少并发）
    print("\n>>> 尝试获取K线数据（逐个请求，带间隔）...", file=sys.stderr)
    for code, info in data.items():
        if code == "900925" or info.get('price') is None:
            continue
        try:
            print(f"  获取 {code} {info['name']} K线...", file=sys.stderr)
            time.sleep(0.5)
            hist = ak.stock_zh_a_hist(symbol=code, period="daily",
                                      start_date="2026-05-01", end_date="2026-07-17",
                                      adjust="qfq")
            if hist is not None and len(hist) > 0:
                info["kline_count"] = len(hist)
                prices = hist['收盘'].values
                volume = hist['成交量'].values
                
                # MA
                for p in [5, 10, 20, 60]:
                    if len(prices) >= p:
                        info[f"MA{p}"] = round(sum(prices[-p:]) / p, 2)
                
                # 20日涨跌
                if len(prices) >= 20:
                    info["change_20d"] = round((prices[-1] - prices[-21]) / prices[-21] * 100, 2)
                if len(prices) >= 60:
                    info["change_60d"] = round((prices[-1] - prices[-61]) / prices[-61] * 100, 2) if len(prices) > 60 else None
                
                # 量能
                vol5 = volume[-5:].mean() if len(volume) >= 5 else 0
                vol10 = volume[-10:-5].mean() if len(volume) >= 10 else 0
                info["vol_change"] = round((vol5 - vol10) / vol10 * 100, 1) if vol10 > 0 else 0
                
                # 支撑压力
                info["support"] = round(min(prices[-20:]), 2) if len(prices) >= 20 else 0
                info["resistance"] = round(max(prices[-20:]), 2) if len(prices) >= 20 else 0
                
                # MACD
                ema12 = [prices[0]]
                ema26 = [prices[0]]
                for i in range(1, len(prices)):
                    ema12.append(prices[i] * 2/13 + ema12[-1] * 11/13)
                    ema26.append(prices[i] * 2/27 + ema26[-1] * 25/27)
                dif = [ema12[i] - ema26[i] for i in range(len(prices))]
                dea = [dif[0]]
                for i in range(1, len(dif)):
                    dea.append(dif[i] * 2/10 + dea[-1] * 8/10)
                
                info["macd_dif"] = round(dif[-1], 3)
                info["macd_dea"] = round(dea[-1], 3)
                info["macd_hist"] = round(2 * (dif[-1] - dea[-1]), 3)
                
                # RSI
                def rsi(p, period):
                    if len(p) <= period: return 50
                    gains = losses = 0
                    for i in range(-period, 0):
                        c = p[i] - p[i-1]
                        if c > 0: gains += c
                        else: losses -= c
                    ag, al = gains/period, losses/period
                    if al == 0: return 100
                    return round(100 - 100/(1+ag/al), 1)
                
                info["rsi6"] = rsi(prices, 6)
                info["rsi12"] = rsi(prices, 12)
                
                # BB
                if len(prices) >= 20:
                    ma20 = prices[-20:].mean()
                    std20 = prices[-20:].std()
                    info["bb_mid"] = round(ma20, 2)
                    info["bb_upper"] = round(ma20 + 2*std20, 2)
                    info["bb_lower"] = round(ma20 - 2*std20, 2)
                
                # 趋势信号
                signals = []
                p0 = prices[-1]
                if info.get("MA5") and p0 >= info["MA5"]: signals.append("站上MA5")
                elif info.get("MA5"): signals.append("跌破MA5")
                if info.get("MA20") and p0 >= info["MA20"]: signals.append("站上MA20")
                elif info.get("MA20"): signals.append("跌破MA20")
                if info.get("MA60") and p0 >= info["MA60"]: signals.append("站上MA60")
                elif info.get("MA60"): signals.append("跌破MA60")
                
                if info.get("MA5") and info.get("MA10") and info.get("MA20"):
                    m5 = info.get(f"MA5_internal", info.get("MA5"))
                    # actually we have MA5, MA20 but not MA10 from K-line directly... let's compute
                
                # simpler approach: just compute raw values again
                if len(prices) >= 10:
                    ma10_val = sum(prices[-10:]) / 10
                    ma20_val = sum(prices[-20:]) / 20 if len(prices) >= 20 else 0
                    ma5_val = info.get("MA5", 0)
                    if ma5_val > ma10_val > ma20_val:
                        signals.append("均线多头排列")
                    elif ma5_val < ma10_val < ma20_val:
                        signals.append("均线空头排列")
                    elif info.get("MA5") and info.get("MA20"):
                        signals.append("均线粘合/震荡")
                
                info["signals"] = signals
                print(f"    ✅ {len(hist)}条K线, 收盘{prices[-1]:.2f}", file=sys.stderr)
        except Exception as e:
            print(f"    ❌ K线失败: {str(e)[:60]}", file=sys.stderr)
            info["errors"].append(f"K线失败")
    
    # 生成报告
    report = generate_report(data)
    
    # 保存
    report_path = "/Users/duguke/.openclaw/workspace/analysis/daily/2026-07-17.md"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n>>> 报告已保存: {report_path}", file=sys.stderr)
    
    # 输出摘要
    print("\n>>> 数据摘要:", file=sys.stderr)
    valid = {k: v for k, v in data.items() if v.get('change_pct') is not None}
    for code, info in sorted(valid.items(), key=lambda x: x[1]['change_pct'], reverse=True):
        chg = info['change_pct']
        arr = '🟢' if chg >= 0 else '🔴'
        kline = f"K{info.get('kline_count',0)}" if info.get('kline_count') else "无K"
        print(f"  {arr} {code} {info['name']:　<4} {info.get('price','N/A'):>8}  {chg:+.2f}%  [{kline}]", file=sys.stderr)
    
    # 返回摘要给推送
    return generate_summary(data)


def generate_summary(data):
    """生成核心摘要"""
    valid = {k: v for k, v in data.items() if v.get('change_pct') is not None}
    up = sum(1 for v in valid.values() if v['change_pct'] > 0)
    down = sum(1 for v in valid.values() if v['change_pct'] < 0)
    avg = sum(v['change_pct'] for v in valid.values()) / len(valid) if valid else 0
    
    best = max(valid.items(), key=lambda x: x[1]['change_pct']) if valid else None
    worst = min(valid.items(), key=lambda x: x[1]['change_pct']) if valid else None
    
    lines = []
    lines.append("## 📊 A股周报速递 · 7月17日（周五）")
    lines.append("")
    lines.append(f"**25只关注股：** 🟢上涨 {up}只 | 🔴下跌 {down}只 | 平均 {avg:+.2f}%")
    if best:
        lines.append(f"🏆 最强：**{best[1]['name']}** ({best[1]['price']}) +{best[1]['change_pct']:.2f}%")
    if worst:
        lines.append(f"⚠️ 最弱：**{worst[1]['name']}** ({worst[1]['price']}) {worst[1]['change_pct']:.2f}%")
    lines.append("")
    
    # 严重回调
    dangers = sorted([(k,v) for k,v in valid.items() if v['change_pct'] <= -3], key=lambda x: x[1]['change_pct'])
    if dangers:
        lines.append("**🚨 需关注的回调标的：**")
        for code, info in dangers:
            lines.append(f"  • {info['name']}（{code}）：{info['change_pct']:.2f}%")
        lines.append("")
    
    lines.append("📁 完整报告已保存至 `analysis/daily/2026-07-17.md`")
    lines.append("")
    lines.append("> ⚠️ 非交易日自动复盘，数据均为最近收盘价")
    
    return '\n'.join(lines)


def generate_report(data):
    """生成完整报告"""
    lines = []
    lines.append("# 📊 2026年7月17日（周五）A股收盘复盘")
    lines.append("")
    lines.append("**—— 针对你的25只关注股票**")
    lines.append("")
    td = datetime.now()
    lines.append(f"> 📅 数据来源：akshare 新浪实时行情 | 报告生成：{td.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> ⚠️ 当前为周日（非交易日），数据为最近交易日(7/17周五)收盘快照。K线数据可能存在API限制。")
    lines.append("")
    
    valid = {k: v for k, v in data.items() if v.get('change_pct') is not None}
    up = sum(1 for v in valid.values() if v['change_pct'] > 0)
    down = sum(1 for v in valid.values() if v['change_pct'] < 0)
    flat = sum(1 for v in valid.values() if v['change_pct'] == 0)
    avg = round(sum(v['change_pct'] for v in valid.values()) / len(valid), 2) if valid else 0
    up5 = sum(1 for v in valid.values() if v['change_pct'] >= 5)
    down5 = sum(1 for v in valid.values() if v['change_pct'] <= -5)
    down10 = sum(1 for v in valid.values() if v['change_pct'] <= -10)
    
    # 盘面总览
    lines.append("## 📈 盘面总览")
    lines.append("")
    lines.append("| 指标 | 数据 |")
    lines.append("|------|------|")
    lines.append(f"| **上涨** | ✅ {up}只 ({round(up/len(valid)*100,1)}%) |")
    lines.append(f"| **下跌** | 🔴 {down}只 ({round(down/len(valid)*100,1)}%) |")
    lines.append(f"| **平盘** | ⚪ {flat}只 |")
    lines.append(f"| **平均涨跌幅** | {avg:+.2f}% |")
    lines.append(f"| **涨幅≥5%** | {up5}只 |")
    lines.append(f"| **跌幅≥5%** | {down5}只 |")
    if down10 > 0: lines.append(f"| **跌停(≥-10%)** | 🚨 {down10}只 |")
    
    best = max(valid.items(), key=lambda x: x[1]['change_pct']) if valid else None
    worst = min(valid.items(), key=lambda x: x[1]['change_pct']) if valid else None
    best_n = best[1]['name'] if best else ''
    worst_n = worst[1]['name'] if worst else ''
    
    if avg > 0:
        market = "涨多跌少，分化格局"
    elif avg > -1:
        market = "偏弱调整，权重股拖累"
    elif avg > -3:
        market = "普遍下跌，亏钱效应明显"
    else:
        market = "大幅回调，需警惕系统性风险"
    
    lines.append("")
    lines.append(f"> **盘面特征：** 25只关注股{market}。")
    if best and best[1]['change_pct'] > 0:
        lines.append(f"> 🏆 **领涨：** {best_n}（{best[1]['change_pct']:+.2f}%）")
    if worst and worst[1]['change_pct'] < 0:
        lines.append(f"> ⚠️ **领跌：** {worst_n}（{worst[1]['change_pct']:.2f}%）")
    lines.append("")
    
    # 分层
    green = sorted([(k,v) for k,v in valid.items() if v['change_pct'] > 0], key=lambda x: -x[1]['change_pct'])
    yellow = sorted([(k,v) for k,v in valid.items() if -3 < v['change_pct'] <= 0], key=lambda x: -x[1]['change_pct'])
    red = sorted([(k,v) for k,v in valid.items() if -5 < v['change_pct'] <= -3], key=lambda x: -x[1]['change_pct'])
    danger = sorted([(k,v) for k,v in valid.items() if v['change_pct'] <= -5], key=lambda x: x[1]['change_pct'])
    
    if green:
        lines.append("## 🟢 逆势上涨（强势）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌% | 换手率% | MA5 | RSI | 20日涨跌% |")
        lines.append("|------|------|--------|-------|---------|-----|-----------|--------|")
        for code, info in green:
            ma5 = info.get("MA5", "-")
            rsi = info.get("rsi6", "-")
            c20 = info.get("change_20d", "-")
            lines.append(f"| {code} | {info['name']} | {info['price']:.2f} | +{info['change_pct']:.2f}% | {info.get('turnover','-')} | {ma5} | {rsi} | {c20 if isinstance(c20,str) else f'{c20:+.2f}%'} |")
        lines.append("")
    
    if yellow:
        lines.append("## 🟡 小幅回调（观望）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌% | MA5 | 说明 |")
        lines.append("|------|------|--------|-------|-----|------|")
        for code, info in yellow:
            sigs = '、'.join(info.get('signals', ['正常波动']))[:30]
            ma5 = info.get("MA5", "-")
            lines.append(f"| {code} | {info['name']} | {info['price']:.2f} | {info['change_pct']:.2f}% | {ma5} | {sigs} |")
        lines.append("")
    
    if red:
        lines.append("## 🔴 深度回调（需关注）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌% | 支撑位 | 风险提示 |")
        lines.append("|------|------|--------|-------|--------|----------|")
        for code, info in red:
            sup = info.get("support", "-")
            sigs = '、'.join(info.get('signals', ['短期回调']))[:30]
            lines.append(f"| {code} | {info['name']} | {info['price']:.2f} | {info['change_pct']:.2f}% | {sup} | {sigs} |")
        lines.append("")
    
    if danger:
        lines.append("## 🚨 严重回调（需警惕）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌% | 20日涨跌% | RSI6 | MACD | 建议 |")
        lines.append("|------|------|--------|-------|-----------|------|------|------|")
        for code, info in danger:
            c20 = info.get("change_20d", "-")
            r6 = info.get("rsi6", "-")
            macd = info.get("macd_dif", "-")
            
            chg = info['change_pct']
            if chg <= -10:
                suggest = "⚠️ 跌停，勿盲目抄底"
            elif r6 != '-' and r6 <= 25:
                suggest = "超卖，等待企稳反弹信号"
            elif info.get("change_20d") and isinstance(info.get("change_20d"), (int,float)) and info["change_20d"] < -20:
                suggest = "中期走坏，反弹减仓"
            else:
                suggest = "短期回调，关注支撑"
            
            c20_str = f"{c20}%" if isinstance(c20, (int,float)) else str(c20)
            lines.append(f"| {code} | {info['name']} | {info['price']:.2f} | {info['change_pct']:.2f}% | {c20_str} | {r6} | {macd} | {suggest} |")
        lines.append("")
    
    # B股说明
    b_stock = data.get("900925", {})
    if b_stock:
        lines.append("## ℹ️ 机电B股（900925）")
        lines.append("")
        lines.append(f"> B股行情数据暂不可用。最新参考价：1.45美元（来源于前次分析）。")
        lines.append("")
    
    # 深度分析重点个股
    lines.append("## 🔍 重点个股分析")
    lines.append("")
    
    # 分析跌幅最大的
    focus = []
    if danger:
        focus.extend(danger[:3])
    if green:
        focus.append(green[0])
    if len(focus) < 3 and red:
        focus.extend(red[:3-len(focus)])
    
    for code, info in focus:
        name = info['name']
        price = info.get('price', 0)
        chg = info['change_pct']
        chg_str = f"+{chg:.2f}%" if chg > 0 else f"{chg:.2f}%"
        
        lines.append(f"### {name}（{code}）")
        lines.append("")
        lines.append(f"- **最新价：** {price:.2f} | **涨跌幅：** {chg_str}")
        lines.append(f"- **换手：** {info.get('turnover','-')}% | **振幅：** {info.get('amplitude','-')}%")
        lines.append(f"- **PE：** {info.get('pe','-')} | **市值：** {'{:.0f}亿'.format(info.get('market_cap',0)/1e8) if info.get('market_cap') else '-'}")
        
        ma5 = info.get("MA5", "-")
        ma20 = info.get(f"ma20", info.get("MA5", "-"))
        lines.append(f"- **均线：** MA5={ma5}")
        if info.get("bb_mid"):
            lines.append(f"- **布林：** 上轨={info['bb_upper']:.2f} 中轨={info['bb_mid']:.2f} 下轨={info['bb_lower']:.2f}")
            if price <= info['bb_lower']:
                lines.append(f"  📍 当前价已跌破布林下轨，超卖区域")
        if info.get("support"):
            lines.append(f"- **支撑：** {info['support']:.2f} | **压力：** {info.get('resistance',0):.2f}")
        if info.get("macd_dif"):
            m_status = "金叉" if info['macd_dif'] > info['macd_dea'] else "死叉"
            lines.append(f"- **MACD：** DIF={info['macd_dif']} DEA={info['macd_dea']} 柱={info['macd_hist']} **{m_status}**")
        if info.get("rsi6"):
            r_status = "超卖" if info['rsi6'] <= 25 else ("超买" if info['rsi6'] >= 75 else "中性")
            lines.append(f"- **RSI：** RSI6={info['rsi6']} RSI12={info.get('rsi12','-')} **{r_status}**")
        if info.get("vol_change"):
            arrow = '↑' if info['vol_change'] > 0 else '↓'
            lines.append(f"- **量能：** 近5日均量较前5日 {arrow} {abs(info['vol_change']):.1f}%")
        if info.get("signals"):
            lines.append(f"- **趋势：** {' ｜ '.join(info['signals'])}")
        
        lines.append("")
        if chg > 0:
            lines.append("> ✅ **操作建议：** 持有不动。趋势向好，关注压力位突破情况。")
        elif chg <= -10:
            lines.append("> ⚠️ **操作建议：** 跌停/暴跌。恐慌释放中，等待企稳（缩量+十字星）。控制仓位。")
        elif chg <= -5:
            if info.get("rsi6", 50) and info["rsi6"] <= 25:
                lines.append("> 👀 **操作建议：** 严重超卖，有技术反弹需求。轻仓试探，设好止损。")
            else:
                lines.append("> ⚠️ **操作建议：** 跌幅较大，关注支撑位。若放量跌破支撑则考虑减仓。")
        else:
            lines.append("> 👀 **操作建议：** 等待企稳。关注量和布林下轨支撑。")
        lines.append("")
    
    # 综合建议
    lines.append("## 💡 综合操作建议")
    lines.append("")
    lines.append("| 建议 | 数量 | 说明 |")
    lines.append("|------|------|------|")
    lines.append(f"| ✅ **持有不动** | {up}只 | 上涨/抗跌，继续持有 |")
    yellow_count = len(yellow) + len(red)
    lines.append(f"| 👀 **等待企稳** | {yellow_count}只 | 小幅回调，等待方向 |")
    if danger:
        lines.append(f"| ⚠️ **关注/减仓** | {len(danger)}只 | 跌超5%，注意风险 |")
    lines.append("")
    lines.append("### 关键预案")
    lines.append("")
    lines.append("1. **大盘若继续走弱：** 控制总仓位5成以下")
    lines.append("2. **出现反弹：** 放量站上5日线可适当回补")
    lines.append("3. **止损纪律：** 单只浮亏超8%减仓，超15%清仓")
    lines.append("4. **关注方向：** 超跌反弹机会（严重超卖标的）+ 强势延续性")
    lines.append("")
    
    # 明日关注
    lines.append("## 📋 明日关注清单")
    lines.append("")
    lines.append("| 代码 | 名称 | 关注理由 | 关键观察点 |")
    lines.append("|------|------|----------|------------|")
    watch = []
    if danger:
        for code, info in danger[:3]:
            watch.append((code, info['name'], "超跌反弹", f"支撑{info.get('support','?')}"))
    if green:
        for code, info in green[:2]:
            watch.append((code, info['name'], "强势延续", f"压力{info.get('resistance','?')}"))
    for code, name, reason, point in watch:
        lines.append(f"| {code} | {name} | {reason} | {point} |")
    lines.append("")
    lines.append("---")
    lines.append("*⚠️ 免责声明：基于技术指标和公开数据，不构成投资建议*")
    lines.append(f"*生成时间：{td.strftime('%Y-%m-%d %H:%M')}*")
    
    return '\n'.join(lines)


if __name__ == "__main__":
    summary = main()
    print(summary)
