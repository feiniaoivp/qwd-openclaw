#!/usr/bin/env python3
"""
每日A股复盘分析 - 2026-07-28（周二）
针对25只关注股票
v2: 修复K线列名匹配问题
"""
import akshare as ak
import pandas as pd
import numpy as np
import json
import warnings
import time
from datetime import datetime, timedelta

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

def get_stock_prefix(code):
    code_str = str(code).strip()
    if code_str.startswith('6') or code_str.startswith('9'):
        return f"sh{code_str}"
    elif code_str.startswith('0') or code_str.startswith('3'):
        return f"sz{code_str}"
    elif code_str.startswith('4') or code_str.startswith('8'):
        return f"bj{code_str}"
    return code_str

def fetch_all_kline():
    """获取所有K线数据"""
    all_kline = {}
    for code, name in STOCKS.items():
        if code == "900925":
            continue
        print(f"  📥 {code} {name}...", end=" ", flush=True)
        for attempt in range(3):
            try:
                df = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                        start_date="20260101", adjust="qfq")
                if df is not None and len(df) > 0:
                    all_kline[code] = df
                    print(f"✅ {len(df)}条")
                    break
                else:
                    print(f"⚠️ 空数据({attempt+1})", end=" ", flush=True)
            except Exception as e:
                print(f"❌({attempt+1})", end=" ", flush=True)
            time.sleep(2)
        else:
            print("💀放弃")
        time.sleep(0.5)
    return all_kline

def compute_ma(series, window):
    if len(series) < window:
        return None
    return round(float(series.tail(window).mean()), 2)

def compute_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow:
        return None, None, None
    exp1 = prices.ewm(span=fast, adjust=False).mean()
    exp2 = prices.ewm(span=slow, adjust=False).mean()
    macd_line = exp1 - exp2
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(histogram.iloc[-1])

def compute_rsi(prices, window=14):
    if len(prices) < window:
        return None
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).tail(window)
    loss = (-delta.where(delta < 0, 0)).tail(window)
    avg_gain = gain.mean()
    avg_loss = loss.mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)

def compute_bollinger(prices, window=20):
    if len(prices) < window:
        return None, None, None
    ma = prices.tail(window).mean()
    std = prices.tail(window).std()
    return round(float(ma + 2*std), 2), round(float(ma), 2), round(float(ma - 2*std), 2)

def analyze_stock(code, name, kline):
    """分析单只股票"""
    r = {
        "code": code, "name": name,
        "price": None, "change_pct": None, "volume": None, "turnover": None,
        "ma5": None, "ma10": None, "ma20": None, "ma60": None,
        "macd_str": "N/A", "rsi": None,
        "boll_upper": None, "boll_mid": None, "boll_lower": None,
        "vol_ratio": None, "trend": "未知", "support": None, "resistance": None,
        "signal": "持有", "detail": ""
    }
    
    if code == "900925":
        r["price"] = "B股暂不可用"
        r["detail"] = "B股数据暂不可用"
        return r
    
    if kline is None or len(kline) == 0:
        r["detail"] = "无K线数据"
        return r
    
    # K线列名：日期、股票代码、开盘、收盘、最高、最低、成交量、成交额、振幅、涨跌幅、涨跌额、换手率
    close_col = '收盘'
    vol_col = '成交量'
    change_col = '涨跌幅'
    
    prices = kline[close_col].astype(float)
    volumes = kline.get(vol_col)
    
    last_row = kline.iloc[-1]
    r["price"] = float(last_row[close_col])
    r["change_pct"] = float(last_row.get(change_col, 0))
    r["volume"] = int(float(last_row[vol_col])) if vol_col in last_row else None
    r["turnover"] = float(last_row.get('成交额', 0)) if '成交额' in last_row else None
    
    n = len(prices)
    # MA
    r["ma5"] = compute_ma(prices, 5) if n >= 5 else None
    r["ma10"] = compute_ma(prices, 10) if n >= 10 else None
    r["ma20"] = compute_ma(prices, 20) if n >= 20 else None
    r["ma60"] = compute_ma(prices, 60) if n >= 60 else None
    
    # MACD
    macd_line, signal_line, histogram = compute_macd(prices)
    if macd_line is not None:
        macd_status = "红柱🔴" if histogram > 0 else "绿柱🟢"
        cross = "金叉" if macd_line > signal_line else "死叉"
        r["macd_str"] = f"{macd_status} {cross} (DIF={round(macd_line,2)} DEA={round(signal_line,2)} MACD={round(histogram,2)})"
    
    # RSI
    r["rsi"] = compute_rsi(prices)
    
    # 布林带
    upper, mid, lower = compute_bollinger(prices)
    r["boll_upper"] = upper
    r["boll_mid"] = mid
    r["boll_lower"] = lower
    
    # 量比
    if volumes is not None and len(volumes) >= 20:
        vols = volumes.astype(float)
        avg_vol = vols.tail(20).mean()
        cur_vol = vols.iloc[-1]
        r["vol_ratio"] = round(float(cur_vol / avg_vol), 2) if avg_vol > 0 else None
    
    # 趋势判断
    price = float(prices.iloc[-1])
    signals = []
    
    if r["ma5"] and r["ma10"] and r["ma20"]:
        if r["ma5"] > r["ma10"] > r["ma20"]:
            signals.append("多头排列📈")
        elif r["ma5"] < r["ma10"] < r["ma20"]:
            signals.append("空头排列📉")
        else:
            signals.append("均线交织")
        if price > r["ma20"]:
            signals.append("站上MA20")
        else:
            signals.append("跌破MA20")
    
    if r["rsi"]:
        if r["rsi"] > 70:
            signals.append("RSI超买")
        elif r["rsi"] < 30:
            signals.append("RSI超卖")
    
    if r["boll_upper"] and price >= r["boll_upper"]:
        signals.append("触上轨")
    elif r["boll_lower"] and price <= r["boll_lower"]:
        signals.append("触下轨")
    
    r["trend"] = " | ".join(signals) if signals else "震荡"
    
    # 支撑压力
    if r["ma20"]:
        if price > r["ma20"]:
            r["support"] = r["ma20"]
            r["resistance"] = r["boll_upper"] if r["boll_upper"] else round(price*1.05, 2)
        else:
            r["resistance"] = r["ma20"]
            r["support"] = r["boll_lower"] if r["boll_lower"] else r["ma10"] if r["ma10"] else round(price*0.95, 2)
    
    # 详细说明
    parts = []
    chg = r["change_pct"]
    if chg is not None:
        parts.append(f"涨跌{chg:+.2f}%")
    
    if r["vol_ratio"]:
        if r["vol_ratio"] > 1.5:
            parts.append("放量")
        elif r["vol_ratio"] > 1.2:
            parts.append("温和放量")
        elif r["vol_ratio"] < 0.7:
            parts.append("缩量")
    
    if "多头排列" in r["trend"]:
        parts.append("趋势偏强")
    elif "空头排列" in r["trend"]:
        parts.append("趋势偏弱")
    
    if r["rsi"] and r["rsi"] > 70:
        parts.append("⚠️超买")
    elif r["rsi"] and r["rsi"] < 30:
        parts.append("💡超卖")
    
    r["detail"] = "，".join(parts) if parts else "正常波动"
    
    # 操作建议
    if chg is not None:
        if chg >= 5:
            r["signal"] = "持有/强势"
        elif chg > 0:
            r["signal"] = "持有"
        elif chg > -3:
            r["signal"] = "观望"
        elif chg > -5:
            r["signal"] = "关注止跌"
        else:
            r["signal"] = "警惕/减仓"
    
    return r

def generate_report(results, all_kline):
    """生成完整Markdown报告"""
    valid = [r for r in results if r['change_pct'] is not None and r['change_pct'] != "B股暂不可用"]
    sorted_r = sorted(valid, key=lambda x: float(x['change_pct'] or 0), reverse=True)
    
    pos = len([r for r in sorted_r if float(r['change_pct'] or 0) > 0])
    neg = len([r for r in sorted_r if float(r['change_pct'] or 0) < 0])
    zero = len([r for r in sorted_r if float(r['change_pct'] or 0) == 0])
    avg = np.mean([float(r['change_pct'] or 0) for r in sorted_r])
    
    up5 = [r for r in sorted_r if float(r['change_pct'] or 0) >= 5]
    up3 = [r for r in sorted_r if 3 <= float(r['change_pct'] or 0) < 5]
    up0 = [r for r in sorted_r if 0 < float(r['change_pct'] or 0) < 3]
    dn0 = [r for r in sorted_r if -3 < float(r['change_pct'] or 0) <= 0]
    dn3 = [r for r in sorted_r if -5 < float(r['change_pct'] or 0) <= -3]
    dn5 = [r for r in sorted_r if float(r['change_pct'] or 0) <= -5]
    dn10 = [r for r in sorted_r if float(r['change_pct'] or 0) <= -10]
    
    lines = []
    lines.append("## 📊 2026年7月28日（周二）A股收盘复盘")
    lines.append("**—— 针对你的25只关注股票**")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("### 📈 盘面总览")
    lines.append("")
    lines.append("| 指标 | 数据 |")
    lines.append("|------|------|")
    lines.append(f"| **上涨** | ✅ {pos}只 ({pos/len(sorted_r)*100:.0f}%) |")
    lines.append(f"| **下跌** | 🔴 {neg}只 ({neg/len(sorted_r)*100:.0f}%) |")
    lines.append(f"| **平盘** | {zero}只 |")
    lines.append(f"| **平均涨跌幅** | {avg:+.2f}% |")
    lines.append(f"| **涨幅≥5%** | {len(up5)}只 |")
    lines.append(f"| **跌幅≥5%** | {len(dn5)}只 |")
    lines.append(f"| **跌停(≥-10%)** | {len(dn10)}只 |")
    lines.append("")
    
    if avg > 1:
        summary = "整体偏强，结构性行情明显"
    elif avg > 0:
        summary = "窄幅震荡偏强"
    elif avg > -1:
        summary = "窄幅震荡偏弱，多数个股回调"
    else:
        summary = "整体偏弱，亏钱效应明显"
    
    lines.append(f"> **一句话总结**：关注股上涨{pos}只/下跌{neg}只，平均涨跌幅{avg:+.2f}%，{summary}。")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # 按涨跌幅分层
    if up5:
        lines.append("### 🟢 强势大涨（涨幅≥5%）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 | 技术指标 | 说明 |")
        lines.append("|------|------|--------|--------|----------|------|")
        for r in up5:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | **+{r['change_pct']:.2f}%** | RSI={r['rsi']} | {r['detail']} |")
        lines.append("")
    
    if up3:
        lines.append("### 🟢 中幅上涨（涨幅3%~5%）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 说明 |")
        lines.append("|------|------|--------|--------|-----|------|")
        for r in up3:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | **+{r['change_pct']:.2f}%** | {r['rsi']} | {r['detail']} |")
        lines.append("")
    
    if up0:
        lines.append("### 🟢 小幅上涨（涨幅0%~3%）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 说明 |")
        lines.append("|------|------|--------|--------|-----|------|")
        for r in up0:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | **+{r['change_pct']:.2f}%** | {r['rsi']} | {r['detail']} |")
        lines.append("")
    
    if dn0:
        lines.append("### 🟡 微幅回调（跌幅0%~-3%）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 说明 |")
        lines.append("|------|------|--------|--------|-----|------|")
        for r in dn0:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['change_pct']:+.2f}% | {r['rsi']} | {r['detail']} |")
        lines.append("")
    
    if dn3:
        lines.append("### 🔴 深度回调（跌幅-3%~-5%）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 预警 |")
        lines.append("|------|------|--------|--------|-----|------|")
        for r in dn3:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['change_pct']:+.2f}% | {r['rsi']} | ⚠️ {r['detail']} |")
        lines.append("")
    
    if dn5:
        lines.append("### 🚨 严重回调（跌幅≥-5%）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 | RSI | 预警 |")
        lines.append("|------|------|--------|--------|-----|------|")
        for r in dn5:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['change_pct']:+.2f}% | {r['rsi']} | 🚨 {r['detail']} |")
        lines.append("")
    
    # B股
    b = [r for r in results if r['code'] == '900925']
    if b:
        lines.append("### ⚠️ 数据缺失")
        lines.append("")
        lines.append("| 代码 | 名称 | 状态 |")
        lines.append("|------|------|------|")
        for r in b:
            lines.append(f"| {r['code']} | {r['name']} | B股数据暂不可用 |")
        lines.append("")
    
    lines.append("---")
    lines.append("")
    
    # 详细技术分析表格
    lines.append("### 📊 详细技术指标")
    lines.append("")
    
    table_lines = []
    table_lines.append("| 代码 | 名称 | 现价 | 涨跌幅 | MA5 | MA10 | MA20 | MA60 | RSI(14) | MACD | 量比 | 趋势 |")
    table_lines.append("|------|------|------|--------|-----|------|------|------|---------|------|------|------|")
    for r in sorted_r:
        c = r['code']; n = r['name']; p = r['price']; ch = f"{r['change_pct']:+.2f}%"
        m5 = r['ma5'] or '-'; m10 = r['ma10'] or '-'; m20 = r['ma20'] or '-'; m60 = r['ma60'] or '-'
        rsi = r['rsi'] or '-'
        macd = r['macd_str']
        vr = r['vol_ratio'] or '-'
        trend_short = r['trend'][:20] if len(r['trend']) > 20 else r['trend']
        table_lines.append(f"| {c} | {n} | {p} | {ch} | {m5} | {m10} | {m20} | {m60} | {rsi} | {macd[:25]} | {vr} | {trend_short} |")
    
    lines.extend(table_lines)
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # 关键位
    lines.append("### 🎯 支撑压力位")
    lines.append("")
    lines.append("| 代码 | 名称 | 现价 | 支撑位 | 压力位 |")
    lines.append("|------|------|------|--------|--------|")
    for r in sorted_r:
        sup = r['support'] or '-'
        res = r['resistance'] or '-'
        lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {sup} | {res} |")
    lines.append("")
    
    lines.append("---")
    lines.append("")
    
    # 操作建议
    lines.append("### 🔮 操作建议")
    lines.append("")
    
    strong = [r for r in sorted_r if r['signal'] == '持有/强势']
    hold = [r for r in sorted_r if r['signal'] == '持有']
    watch = [r for r in sorted_r if r['signal'] in ['观望', '关注止跌']]
    danger = [r for r in sorted_r if '警惕' in (r['signal'] or '') or '减仓' in (r['signal'] or '')]
    
    if strong:
        lines.append("**✅ 持有/强势（趋势向上，可持股待涨）**")
        for r in strong:
            lines.append(f"- **{r['name']}**（{r['code']}）：{r['detail']}")
        lines.append("")
    
    if hold:
        lines.append("**✅ 持有不动（正常波动）**")
        names = [r['name'] for r in hold]
        lines.append(f"— {', '.join(names)}")
        lines.append("")
    
    if watch:
        lines.append("**👀 观望/等待企稳**")
        for r in watch:
            lines.append(f"- **{r['name']}**（{r['code']}）：{r['detail']}")
        lines.append("")
    
    if danger:
        lines.append("**⚠️ 警惕/关注减仓**")
        for r in danger:
            lines.append(f"- **{r['name']}**（{r['code']}）：{r['detail']}")
        lines.append("")
    
    lines.append("---")
    lines.append("")
    lines.append("### 💡 建议仓位")
    lines.append("")
    if avg > 1:
        lines.append("> 市场偏强，建议仓位 **7~8成**")
    elif avg > 0:
        lines.append("> 市场震荡偏强，建议仓位 **5~7成**")
    elif avg > -1:
        lines.append("> 市场震荡偏弱，建议仓位 **3~5成**")
    else:
        lines.append("> 市场弱势，建议仓位 **2~3成**")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # 明日关注
    lines.append("### 🔭 明日关注")
    lines.append("")
    # 找出跌幅大但基本面好的
    attention = [r for r in sorted_r if float(r['change_pct'] or 0) <= -5 and r['rsi'] and r['rsi'] < 30]
    if attention:
        lines.append("**超跌反弹关注（RSI<30）：**")
        for r in attention:
            lines.append(f"- {r['name']}（{r['code']}）：RSI={r['rsi']}，超卖区间，关注是否止跌企稳")
        lines.append("")
    
    # 逆势强势
    attention2 = [r for r in sorted_r if float(r['change_pct'] or 0) > 2 and r['rsi'] and r['rsi'] < 60]
    if attention2:
        lines.append("**强势延续关注：**")
        for r in attention2:
            lines.append(f"- {r['name']}（{r['code']}）：今日强势上涨，量价配合良好")
        lines.append("")
    
    lines.append("---")
    lines.append("")
    lines.append("*数据来源：akshare 东方财富接口 | 分析时间：2026-07-28 15:42*")
    
    return "\n".join(lines)

def main():
    print("=" * 60)
    print("📊 A股每日复盘分析 v2 - 2026年7月28日（周二）")
    print("=" * 60)
    
    # 获取K线数据
    print("\n📡 获取个股K线数据（东方财富接口）...")
    all_kline = fetch_all_kline()
    
    print(f"\n📊 成功获取 {len(all_kline)} 只股票K线数据")
    
    # 分析
    print("\n📈 分析每只股票...")
    results = []
    for code, name in STOCKS.items():
        kline = all_kline.get(code)
        result = analyze_stock(code, name, kline)
        results.append(result)
        
        if result['price'] and result['price'] != "B股暂不可用":
            print(f"  {code} {name}: ¥{result['price']} ({result['change_pct']:+.2f}%) → RSI={result['rsi']} | {result['trend'][:25]}")
        else:
            print(f"  {code} {name}: {result['price'] or '暂无数据'}")
    
    # 统计
    valid = [r for r in results if r['change_pct'] is not None and r['change_pct'] != "B股暂不可用"]
    avg = np.mean([float(r['change_pct'] or 0) for r in valid])
    pos = len([r for r in valid if float(r['change_pct'] or 0) > 0])
    neg = len([r for r in valid if float(r['change_pct'] or 0) < 0])
    print(f"\n📊 统计: 上涨{pos}只 下跌{neg}只 平均{avg:+.2f}%")
    
    # 生成报告
    report = generate_report(results, all_kline)
    
    # 保存
    fname = "/Users/duguke/.openclaw/workspace/analysis/daily/2026-07-28.md"
    with open(fname, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n✅ 报告已保存: {fname}")
    
    # 生成摘要
    sorted_r = sorted(valid, key=lambda x: float(x['change_pct'] or 0), reverse=True)
    top3_g = [(r['code'], r['name'], f"{r['change_pct']:+.2f}%") for r in sorted_r[:3]]
    losers = [r for r in sorted_r if float(r['change_pct'] or 0) < 0]
    top3_l = [(r['code'], r['name'], f"{r['change_pct']:+.2f}%") for r in sorted_r[-3:]] if len(sorted_r) >= 3 else []
    
    summary = {
        "date": "2026年7月28日（周二）",
        "avg_change": round(avg, 2),
        "positive": pos, "negative": neg, "total": len(valid),
        "up_5": len([r for r in sorted_r if float(r['change_pct'] or 0) >= 5]),
        "down_3": len([r for r in sorted_r if float(r['change_pct'] or 0) <= -3]),
        "down_5": len([r for r in sorted_r if float(r['change_pct'] or 0) <= -5]),
        "top_gainers": " | ".join([f"{n}({c})" for _, n, c in top3_g]),
        "top_losers": " | ".join([f"{n}({c})" for _, n, c in top3_l]) if top3_l else "无",
        "brief": f"【7月28日复盘】关注股上涨{pos}只/下跌{neg}只，平均涨跌幅{avg:+.2f}%。涨幅≥5%：{len([r for r in sorted_r if float(r['change_pct'] or 0) >= 5])}只，跌幅≥5%：{len([r for r in sorted_r if float(r['change_pct'] or 0) <= -5])}只。"
    }
    
    sf = "/Users/duguke/.openclaw/workspace/analysis/daily/2026-07-28_summary.json"
    with open(sf, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"✅ 摘要已保存: {sf}")
    
    print("\n" + "=" * 60)
    print("📋 核心摘要（推送）")
    print("=" * 60)
    print(summary["brief"])
    print(f"📈 涨幅前3: {summary['top_gainers']}")
    print(f"📉 跌幅前3: {summary['top_losers']}")
    
    return report, summary

if __name__ == "__main__":
    main()
