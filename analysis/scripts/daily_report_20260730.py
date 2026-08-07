#!/usr/bin/env python3
"""
A股每日复盘分析脚本 - 2026-07-30（周四）
v4: 使用akshare stock_zh_a_hist获取历史K线（更高速）
"""
import akshare as ak
import pandas as pd
import numpy as np
import os
import time
import warnings
warnings.filterwarnings('ignore')

REPORT_DATE = "2026-07-30"
ANALYSIS_DIR = "/Users/duguke/.openclaw/workspace/analysis"

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
B_STOCKS = {"900925": "机电B股"}

def fetch_spot_data():
    print("📊 正在获取实时行情（新浪接口）...")
    try:
        df = ak.stock_zh_a_spot()
        df.set_index("代码", inplace=True)
        result = {}
        for code, name in STOCKS.items():
            if code in B_STOCKS:
                result[code] = {"name": name, "status": "B股数据暂不可用"}
                continue
            if code.startswith(('6', '9')):
                prefixes = [f"sh{code}", code]
            elif code.startswith(('0', '3')):
                prefixes = [f"sz{code}", code]
            else:
                prefixes = [f"sz{code}", code]
            
            found = False
            for idx in prefixes:
                if idx in df.index:
                    row = df.loc[idx]
                    result[code] = {
                        "name": name,
                        "price": float(row.get("最新价", 0)),
                        "change_pct": float(row.get("涨跌幅", 0)),
                        "change_amt": float(row.get("涨跌额", 0)),
                        "volume": float(row.get("成交量", 0)),
                        "amount": float(row.get("成交额", 0)),
                        "high": float(row.get("最高", 0)),
                        "low": float(row.get("最低", 0)),
                        "open": float(row.get("今开", 0)),
                        "pre_close": float(row.get("昨收", 0)),
                        "turnover": float(row.get("换手率", 0)),
                        "pe": float(row.get("市盈率-动态", 0)),
                        "status": "ok"
                    }
                    found = True
                    break
            if not found:
                result[code] = {"name": name, "status": "数据未找到"}
        return result
    except Exception as e:
        print(f"⚠️ 新浪接口失败: {e}")
        return None

def fetch_hist_akshare(code):
    """使用akshare stock_zh_a_hist获取历史K线（前复权）"""
    try:
        # 使用akshare历史行情，前复权
        df = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                start_date="2026-01-01", end_date=REPORT_DATE,
                                adjust="qfq")
        if df is not None and len(df) >= 20:
            df.rename(columns={
                "日期": "日期", "开盘": "开盘", "最高": "最高",
                "最低": "最低", "收盘": "收盘", "成交量": "成交量",
                "成交额": "成交额"
            }, inplace=True)
            df['日期'] = pd.to_datetime(df['日期'])
            df.sort_values('日期', inplace=True)
            return df
        return None
    except Exception as e:
        print(f"❌ {str(e)[:40]}", end="")
        return None

def calculate_indicators(hist_df):
    if hist_df is None or len(hist_df) < 20:
        return {}
    
    close = hist_df['收盘'].values.astype(float)
    high = hist_df['最高'].values.astype(float)
    low = hist_df['最低'].values.astype(float)
    volume = hist_df['成交量'].values.astype(float)
    
    # MA
    ma5 = np.mean(close[-5:]) if len(close) >= 5 else None
    ma10 = np.mean(close[-10:]) if len(close) >= 10 else None
    ma20 = np.mean(close[-20:]) if len(close) >= 20 else None
    ma60 = np.mean(close[-60:]) if len(close) >= 60 else None
    
    # MACD
    ema12 = close[:].copy()
    ema26 = close[:].copy()
    for i in range(1, len(close)):
        ema12[i] = ema12[i-1] * 11/13 + close[i] * 2/13
        ema26[i] = ema26[i-1] * 25/27 + close[i] * 2/27
    dif = ema12 - ema26
    dea = dif[:].copy()
    for i in range(1, len(dif)):
        dea[i] = dea[i-1] * 8/10 + dif[i] * 2/10
    macd = 2 * (dif - dea)
    
    # RSI(14)
    if len(close) >= 15:
        delta = np.diff(close)
        gains = np.where(delta > 0, delta, 0)
        losses = np.where(delta < 0, -delta, 0)
        avg_gain = np.mean(gains[-14:])
        avg_loss = np.mean(losses[-14:])
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
    else:
        rsi = None
    
    # 布林带 (20, 2)
    if len(close) >= 20:
        bb_mid = ma20
        bb_std = np.std(close[-20:])
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
    else:
        bb_mid = bb_upper = bb_lower = None
    
    # 成交量变化
    if len(volume) >= 10:
        vol_ma5 = np.mean(volume[-5:])
        vol_ma10 = np.mean(volume[-10:])
        vol_ratio = vol_ma5 / vol_ma10 if vol_ma10 > 0 else 1
    else:
        vol_ratio = None
    
    current_price = close[-1]
    
    # 趋势判断
    trend_signals = []
    if ma5 and ma10 and ma20:
        if current_price > ma5 > ma10 > ma20:
            trend_signals.append("多头排列")
        elif current_price < ma5 < ma10 < ma20:
            trend_signals.append("空头排列")
        else:
            if current_price > ma5 and ma5 > ma20:
                trend_signals.append("短期多头")
            elif current_price < ma5 and ma5 < ma20:
                trend_signals.append("短期空头")
            else:
                trend_signals.append("均线交织")
    
    if rsi is not None:
        if rsi > 70:
            trend_signals.append(f"RSI超买({rsi:.1f})")
        elif rsi < 30:
            trend_signals.append(f"RSI超卖({rsi:.1f})")
        else:
            trend_signals.append(f"RSI{rsi:.1f}")
    
    if bb_upper and current_price > bb_upper:
        trend_signals.append("突破上轨")
    elif bb_lower and current_price < bb_lower:
        trend_signals.append("跌破下轨")
    elif bb_mid and current_price > bb_mid:
        trend_signals.append("中轨上方")
    elif bb_mid:
        trend_signals.append("中轨下方")
    
    if vol_ratio:
        if vol_ratio > 2: trend_signals.append("显著放量")
        elif vol_ratio > 1.5: trend_signals.append("放量")
        elif vol_ratio < 0.6: trend_signals.append("显著缩量")
        elif vol_ratio < 0.8: trend_signals.append("缩量")
        else: trend_signals.append("量能正常")
    
    support = bb_lower if bb_lower else (ma20 if ma20 else None)
    resistance = bb_upper if bb_upper else (ma5 if ma5 else None)
    
    macd_bullish = dif[-1] > dea[-1] if len(dif) > 0 and len(dea) > 0 else None
    macd_golden = (dif[-2] < dea[-2] and dif[-1] > dea[-1]) if len(dif) >= 2 and len(dea) >= 2 else False
    macd_dead = (dif[-2] > dea[-2] and dif[-1] < dea[-1]) if len(dif) >= 2 and len(dea) >= 2 else False
    
    return {
        "ma5": round(ma5, 2) if ma5 else None,
        "ma10": round(ma10, 2) if ma10 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma60": round(ma60, 2) if ma60 else None,
        "macd_dif": round(dif[-1], 4) if len(dif) > 0 else None,
        "macd_dea": round(dea[-1], 4) if len(dea) > 0 else None,
        "macd_hist": round(macd[-1], 4) if len(macd) > 0 else None,
        "macd_bullish": bool(macd_bullish) if macd_bullish is not None else None,
        "macd_golden_cross": bool(macd_golden),
        "macd_dead_cross": bool(macd_dead),
        "rsi": round(rsi, 1) if rsi else None,
        "bb_upper": round(bb_upper, 2) if bb_upper else None,
        "bb_mid": round(bb_mid, 2) if bb_mid else None,
        "bb_lower": round(bb_lower, 2) if bb_lower else None,
        "vol_ratio": round(vol_ratio, 2) if vol_ratio else None,
        "support": round(support, 2) if support else None,
        "resistance": round(resistance, 2) if resistance else None,
        "trend_signals": trend_signals,
        "current_price": round(current_price, 2),
        "volume_now": int(volume[-1]) if len(volume) > 0 else None
    }

def get_trend_opinion(code, name, spot, indicators):
    if spot.get("status") != "ok":
        return "", "数据不足"
    
    pct = spot.get("change_pct", 0) or 0
    price = spot.get("price", 0) or 0
    signals = indicators.get("trend_signals", []) if indicators else []
    support = indicators.get("support") if indicators else None
    resistance = indicators.get("resistance") if indicators else None
    
    has_technical = bool(indicators and indicators.get("ma5"))
    
    if has_technical:
        if any("多头排列" in s for s in signals):
            trend_desc = "多头排列"
        elif any("短期多头" in s for s in signals):
            trend_desc = "短期多头"
        elif any("空头排列" in s for s in signals):
            trend_desc = "空头排列"
        elif any("短期空头" in s for s in signals):
            trend_desc = "短期空头"
        else:
            trend_desc = "均线交织"
        
        macd_desc = ""
        if indicators.get("macd_golden_cross"): macd_desc = "MACD金叉"
        elif indicators.get("macd_dead_cross"): macd_desc = "MACD死叉"
        elif indicators.get("macd_bullish") is True: macd_desc = "MACD多头"
        elif indicators.get("macd_bullish") is False: macd_desc = "MACD空头"
        
        rsi = indicators.get("rsi")
        vol_ratio = indicators.get("vol_ratio")
        
        rsi_desc = ""
        if rsi:
            if rsi > 70: rsi_desc = "RSI超买"
            elif rsi < 30: rsi_desc = "RSI超卖"
            else: rsi_desc = f"RSI{rsi:.1f}"
        
        vol_desc = ""
        if vol_ratio:
            if vol_ratio > 2: vol_desc = "显著放量"
            elif vol_ratio > 1.5: vol_desc = "放量"
            elif vol_ratio > 0.9: vol_desc = "量能正常"
            elif vol_ratio > 0.6: vol_desc = "缩量"
            else: vol_desc = "显著缩量"
        
        if pct > 5:
            if rsi and rsi > 70:
                return "⚠️ 逢高减仓", f"大涨{pct:+.2f}%，{trend_desc}，{rsi_desc}，追高风险大"
            return "✅ 持有不动", f"大涨{pct:+.2f}%，{trend_desc}，{rsi_desc}，{vol_desc}"
        elif pct > 3:
            if rsi and rsi > 70:
                return "⚠️ 逢高减仓", f"强势{pct:+.2f}%，但{rsi_desc}，短线回调压力"
            return "✅ 持有不动", f"上涨{pct:+.2f}%，{trend_desc}，{macd_desc}，{vol_desc}"
        elif pct > 0:
            return "✅ 持有不动", f"上涨{pct:+.2f}%，{trend_desc}，{rsi_desc}，{vol_desc}"
        elif pct > -3:
            if "空头" in trend_desc or "死叉" in macd_desc:
                return "👀 等待企稳", f"回调{pct:+.2f}%，{trend_desc}偏弱，{macd_desc}"
            return "👀 等待企稳", f"小幅回调{pct:+.2f}%，{trend_desc}，{vol_desc}"
        elif pct > -5:
            if support and price <= support * 1.03:
                return "👀 接近支撑", f"回调{pct:+.2f}%，接近支撑{support}"
            return "⚠️ 减仓关注", f"回调{pct:+.2f}%，{trend_desc}，{macd_desc}"
        else:
            if support and price <= support * 1.02:
                return "👀 触及支撑", f"大跌{pct:+.2f}%，触及支撑{support}，关注有效跌破"
            return "⚠️ 减仓/止损", f"大跌{pct:+.2f}%，{trend_desc}，{rsi_desc}"
    else:
        if pct > 5: return "✅ 持有不动", f"大涨{pct:+.2f}%，短线强势"
        elif pct > 3: return "✅ 持有不动", f"上涨{pct:+.2f}%，走强"
        elif pct > 0: return "✅ 持有不动", f"微涨{pct:+.2f}%"
        elif pct > -3: return "👀 等待企稳", f"回调{pct:+.2f}%，正常波动"
        elif pct > -5: return "🔴 关注支撑", f"回调{pct:+.2f}%，关注支撑"
        else: return "⚠️ 减仓/止损", f"大跌{pct:+.2f}%，风险较大"

def fetch_market_news():
    print("📰 正在获取财经新闻...")
    try:
        news = ak.stock_info_global_em()
        if news is not None and not news.empty:
            headlines = []
            for _, row in news.head(15).iterrows():
                headlines.append({
                    "title": str(row.get("标题", ""))[:80],
                    "time": str(row.get("发布时间", ""))[:16]
                })
            print(f"✅ 获取到 {len(headlines)} 条新闻")
            return headlines
    except Exception as e:
        print(f"⚠️ 新闻接口失败: {e}")
    try:
        time.sleep(1)
        news = ak.stock_news_em()
        if news is not None and not news.empty:
            headlines = []
            for _, row in news.head(15).iterrows():
                headlines.append({
                    "title": str(row.get("标题", ""))[:80],
                    "time": str(row.get("发布时间", ""))[:16]
                })
            return headlines
    except:
        pass
    return None

def check_market_index():
    print("📈 正在获取大盘指数...")
    try:
        df = ak.stock_zh_a_spot()
        results = {}
        for idx, name in {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指", "sh000688": "科创50"}.items():
            if idx in df.index:
                row = df.loc[idx]
                results[name] = {
                    "price": float(row.get("最新价", 0)),
                    "change_pct": float(row.get("涨跌幅", 0))
                }
        if results:
            print(f"✅ 大盘: {list(results.keys())}")
            return results
    except:
        pass
    return None

def generate_sector_analysis(green_stocks, red_stocks, yellow_stocks):
    lines = []
    lines.append("### 🔥 今日板块轮动分析\n")
    
    sectors = {
        "新材料": ["国瓷材料", "久立特材"],
        "高端制造": ["应流股份", "恒立液压", "汇川技术"],
        "锂电池": ["亿纬锂能", "天齐锂业"],
        "证券/金融": ["中信证券", "中信建投", "中金公司", "越秀资本"],
        "军工": ["雷科防务", "安达维尔"],
        "半导体": ["中芯国际", "长电科技", "通富微电"],
        "银行": ["招商银行"],
        "光伏": ["福莱特"],
        "汽配": ["福耀玻璃"],
        "软件": ["恒生电子"],
    }
    
    for sector, names in sectors.items():
        ss = [s for s in green_stocks + yellow_stocks + red_stocks if s['name'] in names]
        if ss:
            avg_pct = round(np.mean([s['pct'] for s in ss]), 1)
            icon = "🔥" if avg_pct > 2 else "✅" if avg_pct > 0.5 else "➖" if avg_pct > -0.5 else "🔻" if avg_pct > -2 else "🔴"
            lines.append(f"- {icon} **{sector}** 均{avg_pct:+.1f}%")
    
    top5 = green_stocks[:5]
    if top5:
        names_str = ", ".join([f"{s['name']}({s['pct']:+.1f}%)" for s in top5])
        lines.append(f"\n🏆 领涨：{names_str}")
    
    return "\n".join(lines)

def generate_report(spot_data, indicators_data, news_data, market_index):
    today = REPORT_DATE
    weekday = "周四"
    
    valid_stocks = {k: v for k, v in spot_data.items() if v.get("status") == "ok"}
    total_valid = len(valid_stocks)
    
    ups = sum(1 for v in valid_stocks.values() if v.get("change_pct", 0) > 0)
    downs = sum(1 for v in valid_stocks.values() if v.get("change_pct", 0) < 0)
    flats = sum(1 for v in valid_stocks.values() if v.get("change_pct", 0) == 0)
    up_pct = round(ups / total_valid * 100, 1) if total_valid else 0
    avg_change = round(np.mean([float(v.get("change_pct", 0)) for v in valid_stocks.values()]), 2) if valid_stocks else 0
    big_up = sum(1 for v in valid_stocks.values() if v.get("change_pct", 0) >= 5)
    mid_up = sum(1 for v in valid_stocks.values() if 3 <= v.get("change_pct", 0) < 5)
    big_down = sum(1 for v in valid_stocks.values() if v.get("change_pct", 0) <= -5)
    mid_down = sum(1 for v in valid_stocks.values() if -5 < v.get("change_pct", 0) <= -3)
    
    green_stocks, yellow_stocks, red_stocks, alert_stocks = [], [], [], []
    
    for code, name in STOCKS.items():
        if code in B_STOCKS:
            continue
        spot = spot_data.get(code, {})
        ind = indicators_data.get(code, {})
        pct = spot.get("change_pct", 0) or 0
        action, reason = get_trend_opinion(code, name, spot, ind)
        
        entry = {
            "code": code, "name": name,
            "price": spot.get("price", "N/A"),
            "pct": pct,
            "change_amt": spot.get("change_amt", 0),
            "turnover": spot.get("turnover", "N/A"),
            "action": action, "reason": reason,
            "support": ind.get("support", "N/A") if ind else "N/A",
            "resistance": ind.get("resistance", "N/A") if ind else "N/A",
            "ma5": ind.get("ma5", "N/A") if ind else "N/A",
            "ma10": ind.get("ma10", "N/A") if ind else "N/A",
            "ma20": ind.get("ma20", "N/A") if ind else "N/A",
            "ma60": ind.get("ma60", "N/A") if ind else "N/A",
            "rsi": ind.get("rsi", "N/A") if ind else "N/A",
            "trend_display": ", ".join(ind.get("trend_signals", [])) if ind and ind.get("trend_signals") else "技术指标暂缺"
        }
        
        if pct > 0: green_stocks.append(entry)
        elif pct <= -5: alert_stocks.append(entry)
        elif pct <= -3: red_stocks.append(entry)
        else: yellow_stocks.append(entry)
    
    green_stocks.sort(key=lambda x: x["pct"], reverse=True)
    alert_stocks.sort(key=lambda x: x["pct"])
    red_stocks.sort(key=lambda x: x["pct"])
    yellow_stocks.sort(key=lambda x: x["pct"])
    
    index_summary = ""
    if market_index:
        parts = []
        for name in ["上证指数", "深证成指", "创业板指", "科创50"]:
            if name in market_index:
                d = market_index[name]
                parts.append(f"{name} {d['price']}({d['change_pct']:+.2f}%)")
        index_summary = " | ".join(parts)
    
    if avg_change > 2: mood = "🔥 强势普涨"
    elif avg_change > 1: mood = "📈 整体偏强"
    elif avg_change > 0.3: mood = "📊 小幅上行"
    elif avg_change > -0.3: mood = "➖ 震荡整理"
    elif avg_change > -1: mood = "📉 整体偏弱"
    else: mood = "🔻 弱势调整"
    
    summary = f"{mood} | 关注股{ups}涨{downs}跌{flats}平，均幅{avg_change:+.2f}%"
    if index_summary:
        summary += f"\n{index_summary}"
    
    news_section = ""
    if news_data:
        news_section = "| 时间 | 标题 |\n|------|------|\n"
        for n in news_data[:12]:
            news_section += f"| {n.get('time','')} | {n['title']} |\n"
    else:
        news_section = "今日财经新闻接口暂不可用。\n"
    
    sector_analysis = generate_sector_analysis(green_stocks, red_stocks, yellow_stocks)
    
    report = f"""# 📊 A股收盘复盘 - {today}（{weekday}）

---

## 📈 盘面总览

| 指标 | 数据 |
|------|------|
| **上涨** | ✅ {ups}/{total_valid} ({up_pct}%) |
| **下跌** | 🔴 {downs} |
| **平盘** | ⏸️ {flats} |
| **均幅** | {avg_change:+.2f}% |
| **≥5% ↑** | 🟢 {big_up}只 |
| **3%-5% ↑** | {mid_up}只 |
| **3%-5% ↓** | 🔶 {mid_down}只 |
| **≥5% ↓** | 🔴 {big_down}只 |

> {summary}

---

{sector_analysis}

---
"""
    
    report += f"""
## 🟢 上涨个股 — {len(green_stocks)}只

| 代码 | 名称 | 最新价 | 涨跌幅 | 换手 | 技术信号 | 建议 |
|------|------|--------|--------|------|----------|------|
"""
    for s in green_stocks:
        report += f"| {s['code']} | {s['name']} | {s['price']} | {s['pct']:+.2f}% | {s['turnover']}% | {s['trend_display'][:40]} | {s['action']} |\n"
    
    if yellow_stocks:
        report += f"""
## 🟡 横盘/微调 — {len(yellow_stocks)}只

| 代码 | 名称 | 最新价 | 涨跌幅 | 换手 | 技术信号 | 建议 |
|------|------|--------|--------|------|----------|------|
"""
        for s in yellow_stocks:
            report += f"| {s['code']} | {s['name']} | {s['price']} | {s['pct']:+.2f}% | {s['turnover']}% | {s['trend_display'][:40]} | {s['action']} |\n"
    
    if red_stocks:
        report += f"""
## 🔴 深度回调（-5%~-3%）— {len(red_stocks)}只
"""
        for s in red_stocks:
            report += f"- **{s['name']}({s['code']})** ${s['price']}$ {s['pct']:+.2f}% | {s['action']} | {s['reason'][:70]}\n"
    
    if alert_stocks:
        report += f"""
## 🚨 严重回调（≤-5%）— {len(alert_stocks)}只
"""
        for s in alert_stocks:
            report += f"- **{s['name']}({s['code']})** ${s['price']}$ {s['pct']:+.2f}% | {s['action']} | {s['reason'][:70]}\n"
    
    report += f"""
---

## 📰 关键市场新闻

{news_section}
---

## 🔮 明日关注清单

### ✅ 持有不动
"""
    hold = [s for s in green_stocks if "持有不动" in s["action"]][:5]
    for s in hold[:5]:
        report += f"- **{s['name']}({s['code']})** {s['pct']:+.2f}% | 支撑{s.get('support','?')} 压力{s.get('resistance','?')}\n"
    if not hold:
        report += "- 暂无\n"
    
    report += f"""
### 👀 等待企稳 / 关注支撑
"""
    watch = [s for s in (yellow_stocks + red_stocks) if "等待企稳" in s["action"] or "支撑" in s["action"]]
    for s in watch[:5]:
        report += f"- **{s['name']}({s['code']})** {s['pct']:+.2f}% | {s['action']} 支撑{s.get('support','?')}\n"
    if not watch:
        report += "- 暂无\n"
    
    report += f"""
### ⚠️ 风险关注
"""
    risk = alert_stocks + [s for s in red_stocks if "减仓" in s.get("action","") or "止损" in s.get("action","")]
    for s in risk[:5]:
        report += f"- **{s['name']}({s['code']})** {s['pct']:+.2f}% | {s['action']} | {s['reason'][:50]}\n"
    if not risk:
        report += "- 暂无\n"
    
    # 超买
    overbought = [s for s in green_stocks if isinstance(s.get('rsi'), (int, float)) and s['rsi'] > 70]
    if overbought:
        report += f"""
### ⚡ 超买预警（RSI>70）
"""
        for s in overbought:
            report += f"- **{s['name']}({s['code']})** RSI={s['rsi']}，追高风险大\n"
    
    # 超卖
    oversold = [s for s in (yellow_stocks + red_stocks + alert_stocks) if isinstance(s.get('rsi'), (int, float)) and s['rsi'] < 30]
    if oversold:
        report += f"""
### 💡 超卖机会（RSI<30）
"""
        for s in oversold:
            report += f"- **{s['name']}({s['code']})** RSI={s['rsi']}，关注反弹\n"
    
    report += f"""
### 💡 总体策略
- **市场氛围**：{mood}
- **仓位建议**：{"6-8成" if avg_change > 1 else "4-6成" if avg_change > 0.3 else "3-5成" if avg_change > -0.5 else "2-4成"}
- **核心思路**：{"强势股持有" if avg_change > 0 else "控制仓位，等待信号"}
- **明日关注**：大盘量能变化，板块轮动方向

*📅 {today}（{weekday}）15:00收盘*
*⚠️ 仅供参考，不构成投资建议*
"""
    return report

def save_report(report):
    filename = f"{ANALYSIS_DIR}/daily/{REPORT_DATE}.md"
    os.makedirs(f"{ANALYSIS_DIR}/daily", exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n✅ 报告已保存: {filename}")
    return filename

def generate_short_summary(spot_data, indicators_data, market_index):
    valid = {k: v for k, v in spot_data.items() if v.get("status") == "ok"}
    ups = sum(1 for v in valid.values() if v.get("change_pct", 0) > 0)
    downs = sum(1 for v in valid.values() if v.get("change_pct", 0) < 0)
    flats = sum(1 for v in valid.values() if v.get("change_pct", 0) == 0)
    avg = round(np.mean([v.get("change_pct", 0) for v in valid.values()]), 2) if valid else 0
    
    sorted_pct = sorted(valid.items(), key=lambda x: x[1].get("change_pct", 0), reverse=True)
    top3 = [(c, d["name"], d.get("change_pct", 0)) for c, d in sorted_pct[:3]]
    bot3 = [(c, d["name"], d.get("change_pct", 0)) for c, d in sorted_pct[-3:]]
    
    idx = ""
    if market_index:
        sh = market_index.get("上证指数", {})
        sz = market_index.get("深证成指", {})
        cy = market_index.get("创业板指", {})
        parts = []
        if sh: parts.append(f"上证{sh.get('price','?')}({sh.get('change_pct',0):+.2f}%)")
        if sz: parts.append(f"深证{sz.get('price','?')}({sz.get('change_pct',0):+.2f}%)")
        if cy: parts.append(f"创业板{cy.get('price','?')}({cy.get('change_pct',0):+.2f}%)")
        idx = "\n  " + " | ".join(parts)
    
    s = f"📊 **A股复盘 | {REPORT_DATE}（周四）**\n"
    s += f"\n📈 关注股 {ups}涨 {downs}跌 {flats}平 | 均幅 {avg:+.2f}%{idx}\n\n"
    
    s += "🏆 **涨幅前三：**\n"
    for c, n, p in top3:
        s += f"  🟢 {n}({c}) {p:+.2f}%\n"
    
    s += "\n🔻 **跌幅前三：**\n"
    for c, n, p in reversed(bot3):
        s += f"  🔴 {n}({c}) {p:+.2f}%\n"
    
    drops = [(c, d["name"], d.get("change_pct", 0)) for c, d in sorted_pct if d.get("change_pct", 0) <= -3]
    if drops:
        s += "\n⚠️ **回调关注：**\n"
        for c, n, p in drops:
            s += f"  {n}({c}) {p:+.2f}%\n"
    
    ob = [(c, d["name"], indicators_data.get(c, {}).get("rsi")) for c, d in sorted_pct if c in indicators_data and indicators_data[c] and indicators_data[c].get("rsi") and indicators_data[c]["rsi"] > 70]
    if ob:
        s += "\n⚡ **超买预警：**\n"
        for c, n, r in ob[:5]:
            s += f"  {n}({c}) RSI={r}\n"
    
    s += f"\n📎 完整版 → daily/{REPORT_DATE}.md"
    return s

def main():
    print("="*60)
    print(f"📊 A股每日复盘分析 - {REPORT_DATE}（周四）")
    print("="*60)
    
    # Step 1: 实时行情
    spot_data = fetch_spot_data()
    if spot_data is None:
        print("❌ 无法获取行情，退出")
        return
    
    # Step 2: 大盘指数
    market_index = check_market_index()
    
    sorted_items = sorted(spot_data.items(), key=lambda x: x[1].get("change_pct", 0) if x[1].get("status")=="ok" else 0, reverse=True)
    print(f"\n✅ 获取到 {len([v for v in spot_data.values() if v.get('status')=='ok'])} 只有效行情")
    for code, info in sorted_items:
        if info.get("status") == "ok":
            print(f"  {code} {info['name']}: {info.get('price','N/A')} ({info.get('change_pct',0):+.2f}%)")
    
    # Step 3: 历史K线 + 技术指标 (akshare)
    print("\n📈 获取历史K线（akshare）并计算指标...")
    indicators_data = {}
    for code, name in STOCKS.items():
        if code in B_STOCKS:
            continue
        print(f"  {name}({code}): ", end="", flush=True)
        df = fetch_hist_akshare(code)
        if df is not None and len(df) >= 20:
            ind = calculate_indicators(df)
            indicators_data[code] = ind
            print(f"✅ MA5={ind.get('ma5','')} MA20={ind.get('ma20','')} RSI={ind.get('rsi','')}")
        else:
            indicators_data[code] = {}
            print("❌ 数据不足")
        time.sleep(0.2)
    
    # Step 4: 新闻
    print("\n📰 获取财经新闻...")
    news_data = fetch_market_news()
    
    # Step 5: 报告
    print("\n📝 生成报告...")
    report = generate_report(spot_data, indicators_data, news_data, market_index)
    
    # Step 6: 保存
    filepath = save_report(report)
    
    # Step 7: 摘要
    summary = generate_short_summary(spot_data, indicators_data, market_index)
    
    print("\n" + "="*60)
    print("📋 摘要：")
    print(summary)
    
    with open(f"{ANALYSIS_DIR}/daily/{REPORT_DATE}_summary.txt", "w", encoding="utf-8") as f:
        f.write(summary)
    
    print(f"\n✅ 完成！{filepath}")

if __name__ == "__main__":
    main()
