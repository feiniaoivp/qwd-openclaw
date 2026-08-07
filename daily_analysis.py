#!/usr/bin/env python3
"""
A股每日复盘分析 - 25只关注股
数据日期: 2026-07-17 (周五, 最近交易日)
"""
import akshare as ak
import pandas as pd
import json
import sys
import traceback
from datetime import datetime, timedelta

# ===== 25只关注股 =====
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

def get_stock_data():
    """获取个股实时行情和K线数据"""
    results = {}
    
    # 1. 获取实时行情 - 新浪接口
    print(">>> 获取新浪实时行情...", file=sys.stderr)
    try:
        spot_df = ak.stock_zh_a_spot()
        # 构建代码->行映射
        code_map = {}
        for _, row in spot_df.iterrows():
            code = row.get('代码', '')
            if code:
                code_map[code] = row
        print(f"新浪行情共 {len(code_map)} 条记录", file=sys.stderr)
    except Exception as e:
        print(f"新浪行情失败: {e}", file=sys.stderr)
        code_map = {}
    
    # 2. 对每只股票获取详细数据
    for code, name in STOCKS:
        print(f"\n>>> 分析 {code} {name}...", file=sys.stderr)
        stock_info = {"code": code, "name": name, "errors": []}
        
        # 跳过B股
        if code == "900925":
            stock_info["note"] = "B股数据暂不可用"
            stock_info["price"] = None
            stock_info["change_pct"] = None
            stock_info["change_amt"] = None
            stock_info["volume"] = None
            stock_info["amount"] = None
            stock_info["turnover"] = None
            stock_info["high"] = None
            stock_info["low"] = None
            stock_info["open"] = None
            stock_info["pre_close"] = None
            results[code] = stock_info
            continue
        
        # 构建新浪行情代码 (sz/sh/bj)
        for prefix in ['sz', 'sh', 'bj']:
            if code.startswith('6') or code.startswith('9'):
                full_code = f"sh{code}"
            elif code.startswith('0') or code.startswith('3'):
                full_code = f"sz{code}"
            elif code.startswith('8') or code.startswith('4'):
                full_code = f"bj{code}"
            else:
                full_code = f"sz{code}"
            break
        
        # 从实时行情中获取数据
        if code in code_map:
            row = code_map[code]
            stock_info["price"] = float(row.get('最新价', 0))
            stock_info["change_pct"] = float(row.get('涨跌幅', 0))
            stock_info["change_amt"] = float(row.get('涨跌额', 0))
            stock_info["volume"] = float(row.get('成交量', 0))
            stock_info["amount"] = float(row.get('成交额', 0))
            stock_info["turnover"] = float(row.get('换手率', 0))
            stock_info["high"] = float(row.get('最高', 0))
            stock_info["low"] = float(row.get('最低', 0))
            stock_info["open"] = float(row.get('开盘', 0))
            stock_info["pre_close"] = float(row.get('昨收', 0))
            stock_info["amplitude"] = float(row.get('振幅', 0))
            stock_info["pe"] = row.get('市盈率-动态', None)
            stock_info["market_cap"] = row.get('总市值', None)
            stock_info["circulating_cap"] = row.get('流通市值', None)
            stock_info["pb"] = row.get('市净率', None)
            
            try:
                stock_info["pe"] = float(stock_info["pe"]) if stock_info["pe"] not in [None, '', '-'] else None
            except:
                stock_info["pe"] = None
            try:
                stock_info["market_cap"] = float(stock_info["market_cap"]) if stock_info["market_cap"] not in [None, '', '-'] else None
            except:
                stock_info["market_cap"] = None
        else:
            # 尝试在行情中按代码后缀查找
            found = False
            for key, row in code_map.items():
                if key.endswith(code):
                    stock_info["price"] = float(row.get('最新价', 0))
                    stock_info["change_pct"] = float(row.get('涨跌幅', 0))
                    stock_info["change_amt"] = float(row.get('涨跌额', 0))
                    stock_info["volume"] = float(row.get('成交量', 0))
                    stock_info["amount"] = float(row.get('成交额', 0))
                    stock_info["turnover"] = float(row.get('换手率', 0))
                    stock_info["high"] = float(row.get('最高', 0))
                    stock_info["low"] = float(row.get('最低', 0))
                    stock_info["open"] = float(row.get('开盘', 0))
                    stock_info["pre_close"] = float(row.get('昨收', 0))
                    stock_info["amplitude"] = float(row.get('振幅', 0))
                    found = True
                    break
            if not found:
                stock_info["note"] = "行情数据未获取到"
                stock_info["price"] = None
                stock_info["change_pct"] = None
        
        # 获取历史K线数据（近90个交易日）
        print(f"  获取 {code} K线数据...", file=sys.stderr)
        try:
            end_date = "2026-07-17"
            start_date = "2026-03-01"
            hist = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                      start_date=start_date, end_date=end_date,
                                      adjust="qfq")
            if hist is not None and len(hist) > 0:
                stock_info["kline_count"] = len(hist)
                stock_info["kline"] = hist.to_dict('records')
                
                # 计算技术指标
                prices = hist['收盘'].values
                volume = hist['成交量'].values
                highs = hist['最高'].values
                lows = hist['最低'].values
                
                # MA5/10/20/60
                mas = {}
                for period in [5, 10, 20, 60]:
                    if len(prices) >= period:
                        ma = round(sum(prices[-period:]) / period, 2)
                    else:
                        ma = None
                    mas[f"MA{period}"] = ma
                stock_info["mas"] = mas
                
                # 成交量变化
                vol_latest = volume[-5:].mean() if len(volume) >= 5 else 0
                vol_prev = volume[-10:-5].mean() if len(volume) >= 10 else 0
                vol_prev2 = volume[-20:-10].mean() if len(volume) >= 20 else 0
                stock_info["vol_change_vs_prev5"] = round((vol_latest - vol_prev) / vol_prev * 100, 1) if vol_prev > 0 else 0
                stock_info["vol_change_vs_20avg"] = round((vol_latest - vol_prev2) / vol_prev2 * 100, 1) if vol_prev2 > 0 and len(volume) >= 20 else 0
                
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
                macd_hist = [2 * (dif[i] - dea[i]) for i in range(len(prices))]
                
                stock_info["macd"] = {
                    "dif": round(dif[-1], 3),
                    "dea": round(dea[-1], 3),
                    "hist": round(macd_hist[-1], 3)
                }
                
                # RSI(6/12/24)
                def calc_rsi(prices_arr, period):
                    if len(prices_arr) <= period:
                        return 50
                    gains, losses = 0, 0
                    for i in range(-period, 0):
                        change = prices_arr[i] - prices_arr[i-1]
                        if change > 0: gains += change
                        else: losses -= change
                    avg_gain = gains / period
                    avg_loss = losses / period
                    if avg_loss == 0: return 100
                    rs = avg_gain / avg_loss
                    return round(100 - 100 / (1 + rs), 1)
                
                stock_info["rsi"] = {
                    "RSI6": calc_rsi(prices, 6),
                    "RSI12": calc_rsi(prices, 12),
                    "RSI24": calc_rsi(prices, 24)
                }
                
                # 布林带
                if len(prices) >= 20:
                    ma20 = prices[-20:].mean()
                    std20 = prices[-20:].std()
                    stock_info["bollinger"] = {
                        "mid": round(ma20, 2),
                        "upper": round(ma20 + 2 * std20, 2),
                        "lower": round(ma20 - 2 * std20, 2)
                    }
                
                # 支撑压力位
                if len(prices) >= 20:
                    stock_info["support"] = round(min(prices[-20:]), 2)
                    stock_info["resistance"] = round(max(prices[-20:]), 2)
                
                # 趋势判断
                latest = prices[-1]
                trend_signals = []
                if mas.get("MA5") and latest >= mas["MA5"]: trend_signals.append("站上MA5")
                elif mas.get("MA5"): trend_signals.append("跌破MA5")
                if mas.get("MA20") and latest >= mas["MA20"]: trend_signals.append("站上MA20")
                elif mas.get("MA20"): trend_signals.append("跌破MA20")
                if mas.get("MA60") and latest >= mas["MA60"]: trend_signals.append("站上MA60")
                elif mas.get("MA60"): trend_signals.append("跌破MA60")
                
                # 均线排列
                if mas.get("MA5") and mas.get("MA10") and mas.get("MA20"):
                    if mas["MA5"] > mas["MA10"] > mas["MA20"]:
                        trend_signals.append("均线多头排列")
                    elif mas["MA5"] < mas["MA10"] < mas["MA20"]:
                        trend_signals.append("均线空头排列")
                    else:
                        trend_signals.append("均线粘合/震荡")
                
                stock_info["trend_signals"] = trend_signals
                
                # 20日涨跌幅
                if len(prices) >= 20:
                    stock_info["change_20d"] = round((prices[-1] - prices[-21]) / prices[-21] * 100, 2)
                if len(prices) >= 60:
                    stock_info["change_60d"] = round((prices[-1] - prices[-61]) / prices[-61] * 100, 2) if len(prices) > 60 else None
                
            else:
                stock_info["errors"].append("K线数据为空")
                stock_info["kline_count"] = 0
        except Exception as e:
            stock_info["errors"].append(f"K线获取失败: {str(e)[:100]}")
            print(f"  K线错误: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        
        results[code] = stock_info
    
    return results


def get_news():
    """获取财经新闻"""
    print("\n>>> 获取财经新闻...", file=sys.stderr)
    news_list = []
    try:
        # 尝试东方财富
        news = ak.stock_info_global_em()
        if news is not None and len(news) > 0:
            for _, row in news.head(15).iterrows():
                news_list.append({
                    "title": row.get('新闻标题', str(row.values[0]) if len(row.values) > 0 else ''),
                    "content": row.get('新闻内容', '') if '新闻内容' in row else '',
                    "time": row.get('发布时间', '') if '发布时间' in row else '',
                    "source": "东方财富"
                })
            print(f"获取到 {len(news_list)} 条新闻", file=sys.stderr)
            return news_list
    except Exception as e:
        print(f"东方财富新闻失败: {e}", file=sys.stderr)
    
    try:
        news = ak.stock_news_em()
        if news is not None and len(news) > 0:
            for _, row in news.head(15).iterrows():
                news_list.append({
                    "title": row.get('标题', str(row.values[0]) if len(row.values) > 0 else ''),
                    "time": row.get('发布时间', '') if '发布时间' in row else '',
                    "source": "东方财富快讯"
                })
            print(f"获取到 {len(news_list)} 条快讯", file=sys.stderr)
            return news_list
    except Exception as e:
        print(f"东方财富快讯失败: {e}", file=sys.stderr)
    
    return news_list


def generate_report(data, news):
    """生成复盘报告"""
    lines = []
    lines.append("## 📊 2026年7月17日（周五）A股收盘复盘")
    lines.append("**—— 针对你的25只关注股票**")
    lines.append("")
    lines.append(f"> 📅 数据来源：akshare 新浪实时行情 + 东方财富财经 | 报告生成：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    
    # ===== 盘面总览 =====
    valid_stocks = {k: v for k, v in data.items() if v.get('change_pct') is not None and v.get('change_pct') != ''}
    
    up_count = sum(1 for v in valid_stocks.values() if v['change_pct'] > 0)
    down_count = sum(1 for v in valid_stocks.values() if v['change_pct'] < 0)
    flat_count = sum(1 for v in valid_stocks.values() if v['change_pct'] == 0)
    
    avg_change = sum(v['change_pct'] for v in valid_stocks.values()) / len(valid_stocks) if valid_stocks else 0
    
    up5 = sum(1 for v in valid_stocks.values() if v['change_pct'] >= 5)
    down5 = sum(1 for v in valid_stocks.values() if v['change_pct'] <= -5)
    down10 = sum(1 for v in valid_stocks.values() if v['change_pct'] <= -10)
    
    lines.append("### 📈 盘面总览")
    lines.append("")
    lines.append("| 指标 | 数据 |")
    lines.append("|------|------|")
    lines.append(f"| **上涨** | ✅ {up_count}只 ({round(up_count/len(valid_stocks)*100, 1) if valid_stocks else 0}%) |")
    lines.append(f"| **下跌** | 🔴 {down_count}只 ({round(down_count/len(valid_stocks)*100, 1) if valid_stocks else 0}%) |")
    lines.append(f"| **平盘** | ⚪ {flat_count}只 |")
    lines.append(f"| **平均涨跌幅** | {avg_change:+.2f}% |")
    lines.append(f"| **涨幅≥5%** | {up5}只 |")
    lines.append(f"| **跌幅≥5%** | {down5}只 |")
    lines.append(f"| **跌停(≥-10%)** | {down10}只 |")
    
    # 判断大盘状态
    if avg_change > 2:
        market_summary = "整体强势，普涨格局"
    elif avg_change > 0:
        market_summary = "涨多跌少，偏强震荡"
    elif avg_change > -2:
        market_summary = "跌多涨少，偏弱调整"
    elif avg_change > -4:
        market_summary = "普遍下跌，市场偏弱"
    else:
        market_summary = "全面重挫"
    
    worst = min(valid_stocks.items(), key=lambda x: x[1]['change_pct']) if valid_stocks else (None, None)
    best = max(valid_stocks.items(), key=lambda x: x[1]['change_pct']) if valid_stocks else (None, None)
    
    best_name = data[best[0]]['name'] if best else ''
    worst_name = data[worst[0]]['name'] if worst else ''
    
    lines.append("")
    lines.append(f"> **盘面特征：** 25只关注股 {market_summary}。")
    lines.append(f"> 涨幅最高：**{best_name}** (+{best[1]['change_pct']:.2f}%)" if best and best[1]['change_pct'] > 0 else "")
    lines.append(f"> 跌幅最大：**{worst_name}** ({worst[1]['change_pct']:.2f}%)" if worst and worst[1]['change_pct'] < 0 else "")
    lines.append("")
    
    # ===== 分层展示 =====
    green_list = sorted([(k,v) for k,v in data.items() if v.get('change_pct') and v['change_pct'] > 0], key=lambda x: -x[1]['change_pct'])
    yellow_list = sorted([(k,v) for k,v in data.items() if v.get('change_pct') and -3 < v['change_pct'] <= 0], key=lambda x: -x[1]['change_pct'])
    red_list = sorted([(k,v) for k,v in data.items() if v.get('change_pct') and -5 < v['change_pct'] <= -3], key=lambda x: -x[1]['change_pct'])
    danger_list = sorted([(k,v) for k,v in data.items() if v.get('change_pct') and v['change_pct'] <= -5], key=lambda x: x[1]['change_pct'])
    
    if green_list:
        lines.append("### 🟢 逆势上涨（强势）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌% | MA5 | MA20 | MACD | RSI6 | 趋势 |")
        lines.append("|------|------|--------|-------|-----|------|------|------|------|")
        for code, info in green_list:
            ma5 = info.get('mas', {}).get('MA5', '-')
            ma20 = info.get('mas', {}).get('MA20', '-')
            macd = info.get('macd', {}).get('dif', '-')
            rsi6 = info.get('rsi', {}).get('RSI6', '-')
            trend = '、'.join(info.get('trend_signals', ['-']))[:30]
            lines.append(f"| {code} | {info['name']} | {info.get('price', '-')} | +{info['change_pct']:.2f}% | {ma5} | {ma20} | {macd} | {rsi6} | {trend} |")
        lines.append("")
    
    if yellow_list:
        lines.append("### 🟡 小幅回调（观望）")
        lines.append("")
        lines.append("| 代码 | 名称 | 涨跌% | 说明 |")
        lines.append("|------|------|-------|------|")
        for code, info in yellow_list:
            trend = '、'.join(info.get('trend_signals', ['-']))[:40]
            lines.append(f"| {code} | {info['name']} | {info['change_pct']:.2f}% | {trend} |")
        lines.append("")
    
    if red_list:
        lines.append("### 🔴 深度回调（需关注）")
        lines.append("")
        lines.append("| 代码 | 名称 | 涨跌% | 预警 |")
        lines.append("|------|------|-------|------|")
        for code, info in red_list:
            trend = '、'.join(info.get('trend_signals', ['-']))[:40]
            lines.append(f"| {code} | {info['name']} | {info['change_pct']:.2f}% | {trend} |")
        lines.append("")
    
    if danger_list:
        lines.append("### 🚨 严重回调（需警惕）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌% | 20日涨跌% | 布林中轨 | RSI6 | 建议 |")
        lines.append("|------|------|--------|-------|-----------|---------|------|------|")
        for code, info in danger_list:
            chg20 = info.get('change_20d', '-')
            boll_mid = info.get('bollinger', {}).get('mid', '-') if info.get('bollinger') else '-'
            rsi6 = info.get('rsi', {}).get('RSI6', '-')
            price = info.get('price', '-')
            # 操作建议
            if info.get('change_pct') and info['change_pct'] <= -10:
                suggestion = "⚠️ 跌停，恐慌性抛售，勿盲目抄底"
            elif info.get('rsi', {}).get('RSI6', 50) <= 20:
                suggestion = "超卖区域，等待企稳反弹信号"
            elif info.get('change_20d', 0) and isinstance(info.get('change_20d'), (int, float)):
                if info['change_20d'] < -20:
                    suggestion = "中期趋势已走坏，反弹减仓"
                else:
                    suggestion = "短期回调，关注支撑位企稳"
            else:
                suggestion = "短期回调，关注支撑位企稳"
            lines.append(f"| {code} | {info['name']} | {price} | {info['change_pct']:.2f}% | {chg20}% | {boll_mid} | {rsi6} | {suggestion} |")
        lines.append("")
    
    # B股
    for code, info in data.items():
        if code == "900925":
            lines.append("### ℹ️ B股数据")
            lines.append("")
            lines.append("| 代码 | 名称 | 说明 |")
            lines.append("|------|------|------|")
            lines.append(f"| {code} | {info['name']} | B股数据暂不可用 |")
            lines.append("")
    
    # ===== 深度分析 - 重点关注 =====
    lines.append("### 🔍 重点关注个股分析")
    lines.append("")
    
    # 分析跌幅最大的3只 + 涨幅最大的1只
    focus_stocks = []
    if danger_list:
        focus_stocks.extend(danger_list[:3])
    if green_list:
        focus_stocks.append(green_list[0])
    
    # 如果不够，从红色列表补
    if len(focus_stocks) < 3 and red_list:
        focus_stocks.extend(red_list[:3-len(focus_stocks)])
    
    for code, info in focus_stocks:
        name = info['name']
        price = info.get('price', 'N/A')
        chg = info.get('change_pct', 'N/A')
        chg_str = f"+{chg:.2f}%" if isinstance(chg, (int, float)) and chg > 0 else f"{chg:.2f}%"
        
        lines.append(f"#### {name}（{code}）")
        lines.append("")
        lines.append(f"- **最新价：** {price} 元 | **涨跌幅：** {chg_str}")
        
        ma5 = info.get('mas', {}).get('MA5', 'N/A')
        ma10 = info.get('mas', {}).get('MA10', 'N/A')
        ma20 = info.get('mas', {}).get('MA20', 'N/A')
        ma60 = info.get('mas', {}).get('MA60', 'N/A')
        lines.append(f"- **均线：** MA5={ma5}  MA10={ma10}  MA20={ma20}  MA60={ma60}")
        
        if info.get('bollinger'):
            b = info['bollinger']
            lines.append(f"- **布林带：** 上轨={b['upper']}  中轨={b['mid']}  下轨={b['lower']}")
        
        if info.get('support') and info.get('resistance'):
            lines.append(f"- **支撑位：** {info['support']} | **压力位：** {info['resistance']}")
        
        if info.get('macd'):
            m = info['macd']
            macd_status = "金叉" if m['dif'] > m['dea'] else "死叉"
            lines.append(f"- **MACD：** DIF={m['dif']}  DEA={m['dea']}  柱={m['hist']}  **{macd_status}**")
        
        if info.get('rsi'):
            r = info['rsi']
            rsi_status = "超卖" if r['RSI6'] <= 20 else ("超买" if r['RSI6'] >= 80 else "中性")
            lines.append(f"- **RSI：** RSI6={r['RSI6']}  RSI12={r['RSI12']}  RSI24={r['RSI24']}  **{rsi_status}**")
        
        if info.get('vol_change_vs_prev5') is not None:
            lines.append(f"- **量能：** 近5日均量较前5日 {'↑' if info['vol_change_vs_prev5'] > 0 else '↓'} {abs(info['vol_change_vs_prev5']):.1f}%")
        
        if info.get('trend_signals'):
            lines.append(f"- **趋势信号：** {' | '.join(info['trend_signals'])}")
        
        pe = info.get('pe', 'N/A')
        mc = info.get('market_cap', 'N/A')
        if mc and isinstance(mc, (int, float)):
            mc_str = f"{mc/1e8:.0f}亿"
        else:
            mc_str = 'N/A'
        lines.append(f"- **估值：** PE={pe}  |  市值={mc_str}")
        
        # 操作建议
        lines.append("")
        if isinstance(chg, (int, float)):
            if chg > 0:
                lines.append("> **操作建议：** ✅ 持有不动。逆势上涨，趋势向好。")
            elif chg <= -10:
                lines.append("> **操作建议：** ⚠️ **跌停/暴跌。** 恐慌情绪释放中，不要盲目抄底。等待企稳信号（缩量+十字星/锤头线）。建议减仓止损以控制回撤。")
            elif chg <= -5:
                if info.get('rsi', {}).get('RSI6', 50) <= 25:
                    lines.append("> **操作建议：** 👀 严重超卖，短期或有技术性反弹。可在支撑位附近轻仓试探，但仓位不宜过重。")
                else:
                    lines.append("> **操作建议：** ⚠️ 跌幅较大，若持有较重需考虑减仓。观察20日均线支撑，跌破则止损。")
            else:
                lines.append("> **操作建议：** 👀 等待企稳。关注量和布林下轨支撑，等反弹信号出现。")
        
        lines.append("")
    
    # ===== 新闻解读 =====
    lines.append("### 📰 财经新闻解读")
    lines.append("")
    
    if news:
        lines.append("| # | 新闻标题 | 来源 |")
        lines.append("|---|----------|------|")
        for i, n in enumerate(news[:10], 1):
            title = n.get('title', '')[:60]
            source = n.get('source', '未知')
            lines.append(f"| {i} | {title} | {source} |")
    else:
        lines.append("> ⚠️ 财经新闻接口暂不可用，未获取到实时新闻数据。")
    lines.append("")
    
    # ===== 综合操作建议 =====
    lines.append("### 💡 综合操作建议")
    lines.append("")
    
    # 统计各建议类别
    hold_count = up_count
    wait_count = down_count
    
    lines.append("| 建议 | 数量 | 说明 |")
    lines.append("|------|------|------|")
    
    lines.append(f"| ✅ **持有不动** | {up_count}只 | 上涨/抗跌，趋势尚可，继续持有 |")
    lines.append(f"| 👀 **等待企稳** | {len(yellow_list) + len(red_list)}只 | 小幅回调，等待方向明确 |")
    
    if danger_list:
        lines.append(f"| ⚠️ **关注/减仓** | {len(danger_list)}只 | 跌超5%，需警惕进一步下行风险 |")
    
    lines.append("")
    lines.append("#### 关键预案")
    lines.append("")
    lines.append("1. **大盘若继续走弱：** 控制总仓位在5成以下，优先处理跌幅超5%的标的")
    lines.append("2. **出现反弹：** 确认放量站上5日线可适当回补优质标的")
    lines.append("3. **重要支撑位：** 关注各股20日均线和布林下轨的支撑情况")
    lines.append("4. **止损纪律：** 单只个股浮亏超8%无条件减仓，超15%清仓")
    lines.append("")
    
    # 明日关注
    lines.append("### 📋 明日关注清单")
    lines.append("")
    lines.append("| 代码 | 名称 | 关注理由 | 关键观察点 |")
    lines.append("|------|------|----------|------------|")
    
    # 关注标的：严重回调的 + 逆势上涨的
    watch_items = []
    if danger_list:
        for code, info in danger_list[:3]:
            watch_items.append((code, info['name'], "超跌反弹机会/继续下跌风险", f"支撑位{info.get('support','?')}, RSI{info.get('rsi',{}).get('RSI6','?')}"))
    if green_list:
        for code, info in green_list[:2]:
            watch_items.append((code, info['name'], "强势延续性", f"压力位{info.get('resistance','?')}是否突破"))
    
    for code, name, reason, point in watch_items:
        lines.append(f"| {code} | {name} | {reason} | {point} |")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*⚠️ 免责声明：以上分析基于技术指标和公开数据，不构成投资建议。股市有风险，投资需谨慎。*")
    lines.append("")
    lines.append(f"*报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    
    return '\n'.join(lines)


def main():
    # 获取数据
    data = get_stock_data()
    
    # 获取新闻
    news = get_news()
    
    # 生成报告
    report = generate_report(data, news)
    
    # 保存报告
    report_path = f"/Users/duguke/.openclaw/workspace/analysis/daily/2026-07-17.md"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n>>> 报告已保存到: {report_path}", file=sys.stderr)
    
    # 输出统计
    print("\n>>> ======== 数据统计 ========", file=sys.stderr)
    valid = {k: v for k, v in data.items() if v.get('change_pct') is not None}
    for code, info in sorted(valid.items(), key=lambda x: x[1]['change_pct'], reverse=True):
        chg = info['change_pct']
        arrow = '🟢' if chg >= 0 else '🔴'
        print(f"  {arrow} {code} {info['name']:　<4} {info.get('price','N/A'):>8}  {chg:+.2f}%", file=sys.stderr)
    
    # 输出 JSON 供后续处理
    output = {
        "data": data,
        "news": news,
        "report": report
    }
    # 在报告末尾输出 JSON 边界
    print("\n===JSON_START===", file=sys.stderr)
    # 简化输出
    simplified = {}
    for code, info in data.items():
        simplified[code] = {
            "name": info["name"],
            "price": info.get("price"),
            "change_pct": info.get("change_pct"),
            "change_20d": info.get("change_20d"),
            "trend_signals": info.get("trend_signals", []),
            "rsi6": info.get("rsi", {}).get("RSI6"),
            "macd_status": "golden_cross" if info.get("macd") and info["macd"]["dif"] > info["macd"]["dea"] else "death_cross" if info.get("macd") else None,
        }
    print(json.dumps(simplified, ensure_ascii=False, indent=2), file=sys.stderr)
    print("===JSON_END===", file=sys.stderr)
    
    # 打印摘要
    print("\n>>> ======== 摘要 ========", file=sys.stderr)
    up_count = sum(1 for v in valid.values() if v['change_pct'] > 0)
    down_count = sum(1 for v in valid.values() if v['change_pct'] < 0)
    avg_chg = sum(v['change_pct'] for v in valid.values()) / len(valid) if valid else 0
    print(f"上涨: {up_count} | 下跌: {down_count} | 平均: {avg_chg:+.2f}%", file=sys.stderr)


if __name__ == "__main__":
    main()
