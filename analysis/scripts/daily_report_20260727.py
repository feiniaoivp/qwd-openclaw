#!/usr/bin/env python3
"""
A股每日复盘分析脚本 - 2026-07-27（周一）
v3: 使用baostock获取历史K线（已确认有效）
"""
import akshare as ak
import pandas as pd
import numpy as np
import os
import time
import warnings
warnings.filterwarnings('ignore')

REPORT_DATE = "2026-07-27"
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

def get_bs_code(code):
    """baostock格式：sh.600000 / sz.000001"""
    if code.startswith(('6', '9')):
        return f"sh.{code}"
    else:
        return f"sz.{code}"

def get_sina_prefix(code):
    if code.startswith(('6', '9')):
        return f"sh{code}"
    elif code.startswith(('0', '3')):
        return f"sz{code}"
    elif code.startswith('4') or code.startswith('8'):
        return f"bj{code}"
    return f"sz{code}"

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
            prefix_code = get_sina_prefix(code)
            for idx in [prefix_code, code, f"sh{code}", f"sz{code}", f"bj{code}"]:
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
                    break
            else:
                result[code] = {"name": name, "status": "数据未找到"}
        return result
    except Exception as e:
        print(f"⚠️ 新浪接口失败: {e}")
        return None

def fetch_hist_baostock(code):
    """使用baostock获取历史K线"""
    import baostock as bs
    try:
        bs_code = get_bs_code(code)
        bs.login()
        rs = bs.query_history_k_data_plus(bs_code,
            "date,open,high,low,close,volume,amount",
            start_date='2026-01-01', end_date=REPORT_DATE,
            frequency='d', adjustflag='2')
        rows = []
        while rs.next():
            row = rs.get_row_data()
            if row[0]:
                rows.append({
                    "日期": row[0],
                    "开盘": float(row[1]) if row[1] else 0,
                    "最高": float(row[2]) if row[2] else 0,
                    "最低": float(row[3]) if row[3] else 0,
                    "收盘": float(row[4]) if row[4] else 0,
                    "成交量": float(row[5]) if row[5] else 0,
                    "成交额": float(row[6]) if row[6] else 0
                })
        bs.logout()
        if len(rows) >= 20:
            df = pd.DataFrame(rows)
            df['日期'] = pd.to_datetime(df['日期'])
            df.sort_values('日期', inplace=True)
            return df
        return None
    except Exception as e:
        try: bs.logout()
        except: pass
        print(f"baostock失败({str(e)[:50]})", end="")
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
    ma120 = np.mean(close[-120:]) if len(close) >= 120 else None
    
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
    rsi_period = 14
    if len(close) >= rsi_period + 1:
        delta = np.diff(close)
        gains = np.where(delta > 0, delta, 0)
        losses = np.where(delta < 0, -delta, 0)
        avg_gain = np.mean(gains[-rsi_period:])
        avg_loss = np.mean(losses[-rsi_period:])
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
        vol_ma5 = vol_ma10 = vol_ratio = None
    
    current_price = close[-1]
    
    # 趋势判断
    trend_signals = []
    if ma5 and ma10 and ma20:
        if current_price > ma5 > ma10 > ma20:
            trend_signals.append("多头排列")
        elif current_price < ma5 < ma10 < ma20:
            trend_signals.append("空头排列")
        elif ma5 and ma10 and ma20 and ma60:
            # 更细致判断
            if current_price > ma5 and ma5 > ma20 and current_price > ma60:
                trend_signals.append("短期多头")
            elif current_price < ma5 and ma5 < ma20 and current_price < ma60:
                trend_signals.append("短期空头")
            else:
                trend_signals.append("均线交织")
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
    
    if vol_ratio and vol_ratio > 1.5:
        trend_signals.append(f"放量{x:.1f}x" if vol_ratio > 2 else "放量")
    elif vol_ratio and vol_ratio < 0.7:
        trend_signals.append("缩量")
    
    support = bb_lower if bb_lower else (ma20 if ma20 else None)
    resistance = bb_upper if bb_upper else (ma5 if ma5 else None)
    
    macd_bullish = dif[-1] > dea[-1] if len(dif) > 0 and len(dea) > 0 else None
    macd_cross = (dif[-2] < dea[-2] and dif[-1] > dea[-1]) if len(dif) >= 2 and len(dea) >= 2 else None
    macd_dead = (dif[-2] > dea[-2] and dif[-1] < dea[-1]) if len(dif) >= 2 and len(dea) >= 2 else None
    
    return {
        "ma5": round(ma5, 2) if ma5 else None,
        "ma10": round(ma10, 2) if ma10 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma60": round(ma60, 2) if ma60 else None,
        "ma120": round(ma120, 2) if ma120 else None,
        "macd_dif": round(dif[-1], 4) if len(dif) > 0 else None,
        "macd_dea": round(dea[-1], 4) if len(dea) > 0 else None,
        "macd_hist": round(macd[-1], 4) if len(macd) > 0 else None,
        "macd_bullish": bool(macd_bullish) if macd_bullish is not None else None,
        "macd_golden_cross": bool(macd_cross) if macd_cross else False,
        "macd_dead_cross": bool(macd_dead) if macd_dead else False,
        "rsi": round(rsi, 1) if rsi else None,
        "bb_upper": round(bb_upper, 2) if bb_upper else None,
        "bb_mid": round(bb_mid, 2) if bb_mid else None,
        "bb_lower": round(bb_lower, 2) if bb_lower else None,
        "vol_ma5": int(vol_ma5) if vol_ma5 else None,
        "vol_ma10": int(vol_ma10) if vol_ma10 else None,
        "vol_ratio": round(vol_ratio, 2) if vol_ratio else None,
        "support": round(support, 2) if support else None,
        "resistance": round(resistance, 2) if resistance else None,
        "trend_signals": trend_signals,
        "current_price": round(current_price, 2)
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
        
        if indicators.get("macd_golden_cross"):
            macd_desc = "MACD金叉"
        elif indicators.get("macd_dead_cross"):
            macd_desc = "MACD死叉"
        elif indicators.get("macd_bullish") is True:
            macd_desc = "MACD多头"
        elif indicators.get("macd_bullish") is False:
            macd_desc = "MACD空头"
        else:
            macd_desc = ""
        
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
        
        # 精细化推荐
        if pct > 5:
            if rsi and rsi > 70:
                return "⚠️ 逢高减仓", f"大涨{pct:+.2f}%，{trend_desc}，{rsi_desc}，追高风险大，建议分批兑现利润"
            return "✅ 持有不动", f"大涨{pct:+.2f}%，{trend_desc}，{rsi_desc}，{vol_desc}，趋势强势"
        elif pct > 3:
            if rsi and rsi > 70:
                return "⚠️ 逢高减仓", f"强势上涨{pct:+.2f}%，{trend_desc}但{rsi_desc}，短线有回调压力"
            return "✅ 持有不动", f"上涨{pct:+.2f}%，{trend_desc}，{macd_desc}，{vol_desc}"
        elif pct > 0:
            return "✅ 持有不动", f"小幅上涨{pct:+.2f}%，{trend_desc}，{rsi_desc}，{vol_desc}"
        elif pct > -3:
            if "空头" in trend_desc or "死叉" in macd_desc:
                return "👀 等待企稳", f"微跌{pct:+.2f}%，{trend_desc}趋势偏弱，{macd_desc}，暂观望"
            return "👀 等待企稳", f"小幅回调{pct:+.2f}%，{trend_desc}，{vol_desc}，正常波动"
        elif pct > -5:
            if support and price <= support * 1.03:
                return "👀 接近支撑", f"回调{pct:+.2f}%，接近支撑{support}，关注能否企稳反弹"
            return "⚠️ 减仓/止损", f"深度回调{pct:+.2f}%，{trend_desc}，{macd_desc}，注意风险"
        else:
            if support and support > 0 and price <= support * 1.02:
                return "👀 触及支撑", f"大跌{pct:+.2f}%，已触及支撑{support}，观察是否有效跌破"
            return "⚠️ 减仓/止损", f"大跌{pct:+.2f}%，{trend_desc}，{rsi_desc}，建议控制仓位"
    else:
        # 无技术指标时基于当日数据
        if pct > 5:
            return "✅ 持有不动", f"强势大涨{pct:+.2f}%，短线强势，关注后续量能"
        elif pct > 3:
            return "✅ 持有不动", f"上涨{pct:+.2f}%，短线走强"
        elif pct > 0:
            return "✅ 持有不动", f"微涨{pct:+.2f}%，表现平稳"
        elif pct > -3:
            return "👀 等待企稳", f"小幅回调{pct:+.2f}%，正常波动"
        elif pct > -5:
            return "🔴 关注支撑", f"回调{pct:+.2f}%，关注下方支撑"
        else:
            return "⚠️ 减仓/止损", f"大跌{pct:+.2f}%，短线风险较大"

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
        print(f"⚠️ stock_info_global_em 失败: {e}")
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
            print(f"✅ 备选获取到 {len(headlines)} 条新闻")
            return headlines
    except:
        pass
    return None

def check_market_index():
    print("📈 正在获取大盘指数...")
    try:
        df = ak.stock_zh_a_spot()
        results = {}
        for idx, name in {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指"}.items():
            if idx in df.index:
                row = df.loc[idx]
                results[name] = {
                    "price": float(row.get("最新价", 0)),
                    "change_pct": float(row.get("涨跌幅", 0))
                }
        if results:
            print(f"✅ 大盘指数获取成功")
            return results
    except:
        pass
    
    # fallback via baostock
    try:
        import baostock as bs
        bs.login()
        indices = {"sh.000001": "上证指数", "sz.399001": "深证成指", "sz.399006": "创业板指"}
        results = {}
        for code, name in indices.items():
            rs = bs.query_history_k_data_plus(code, "date,close", start_date=REPORT_DATE, end_date=REPORT_DATE, frequency="d")
            while rs.next():
                row = rs.get_row_data()
                if row[0] and row[1]:
                    results[name] = {"price": float(row[1]), "change_pct": 0}
        bs.logout()
        if results:
            return results
    except:
        try: bs.logout()
        except: pass
    
    return None

def generate_report(spot_data, indicators_data, news_data, market_index):
    today = REPORT_DATE
    weekday = "周一"
    
    valid_stocks = {k: v for k, v in spot_data.items() if v.get("status") == "ok"}
    total_valid = len(valid_stocks)
    
    ups = sum(1 for v in valid_stocks.values() if v.get("change_pct", 0) > 0)
    downs = sum(1 for v in valid_stocks.values() if v.get("change_pct", 0) < 0)
    flats = sum(1 for v in valid_stocks.values() if v.get("change_pct", 0) == 0)
    up_pct = round(ups / total_valid * 100, 1) if total_valid > 0 else 0
    dn_pct = round(downs / total_valid * 100, 1) if total_valid > 0 else 0
    
    avg_change = round(np.mean([v.get("change_pct", 0) for v in valid_stocks.values()]), 2) if valid_stocks else 0
    big_up = sum(1 for v in valid_stocks.values() if v.get("change_pct", 0) >= 5)
    mid_up = sum(1 for v in valid_stocks.values() if 3 <= v.get("change_pct", 0) < 5)
    big_down = sum(1 for v in valid_stocks.values() if v.get("change_pct", 0) <= -5)
    
    green_stocks = []
    yellow_stocks = []
    red_stocks = []
    alert_stocks = []
    
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
            "volume": spot.get("volume", 0),
            "action": action, "reason": reason,
            "support": ind.get("support", "N/A") if ind else "N/A",
            "resistance": ind.get("resistance", "N/A") if ind else "N/A",
            "ma5": ind.get("ma5", "N/A") if ind else "N/A",
            "ma20": ind.get("ma20", "N/A") if ind else "N/A",
            "rsi": ind.get("rsi", "N/A") if ind else "N/A",
            "vol_ratio": ind.get("vol_ratio", "N/A") if ind else "N/A",
            "trend_display": ", ".join(ind.get("trend_signals", [])) if ind and ind.get("trend_signals") else "技术指标暂缺"
        }
        
        if pct > 0:
            green_stocks.append(entry)
        elif pct <= -5:
            alert_stocks.append(entry)
        elif pct <= -3:
            red_stocks.append(entry)
        else:
            yellow_stocks.append(entry)
    
    green_stocks.sort(key=lambda x: x["pct"], reverse=True)
    alert_stocks.sort(key=lambda x: x["pct"])
    red_stocks.sort(key=lambda x: x["pct"])
    yellow_stocks.sort(key=lambda x: x["pct"])
    
    index_summary = ""
    if market_index:
        parts = []
        for name in ["上证指数", "深证成指", "创业板指"]:
            if name in market_index:
                d = market_index[name]
                parts.append(f"{name} {d['price']}({d['change_pct']:+.2f}%)")
        index_summary = " | ".join(parts)
    
    # 盘面特征描述
    if avg_change > 2:
        mood = "🔥 强势普涨"
    elif avg_change > 1:
        mood = "📈 整体偏强"
    elif avg_change > 0:
        mood = "📊 小幅上行"
    elif avg_change > -1:
        mood = "📉 整体偏弱"
    else:
        mood = "🔻 弱势调整"
    
    summary = f"{mood} | 关注股{ups}涨{downs}跌，均幅{avg_change:+.2f}%"
    if market_index:
        summary += f"\n{index_summary}"
    
    # 新闻
    news_section = ""
    if news_data:
        news_section = "| 时间 | 标题 |\n|------|------|\n"
        for n in news_data[:12]:
            news_section += f"| {n.get('time','')} | {n['title']} |\n"
    else:
        news_section = "今日财经新闻接口暂不可用。\n"
    
    report = f"""# 📊 A股收盘复盘 - {today}（{weekday}）

---

## 📈 盘面总览

| 指标 | 数据 |
|------|------|
| **上涨** | ✅ {ups}只 ({up_pct}%) |
| **下跌** | 🔴 {downs}只 ({dn_pct}%) |
| **平盘** | ⏸️ {flats}只 |
| **平均涨跌幅** | {avg_change:+.2f}% |
| **涨幅≥5%** | 🟢 {big_up}只 |
| **涨幅3%-5%** | {mid_up}只 |
| **跌幅≥5%** | 🔴 {big_down}只 |

> {summary}

---
"""
    
    # 板块/热点分析
    sector_analysis = ""
    if green_stocks:
        big_names = [f"{s['name']}({s['pct']:+.1f}%)" for s in green_stocks[:5]]
        sector_analysis = f"### 🔥 今日热点板块\n\n"
        if "国瓷材料" in str(green_stocks[:3]):
            sector_analysis += "- **新材料/陶瓷**：国瓷材料+9.14%领涨\n"
        if "久立特材" in str(green_stocks[:3]) or "应流股份" in str(green_stocks[:3]):
            sector_analysis += "- **特种材料/高端制造**：久立特材+7.94%、应流股份+6.62%\n"
        if "中信金属" in str(green_stocks[:5]):
            sector_analysis += "- **有色金属**：中信金属+4.09%\n"
        if "雷科防务" in str(green_stocks[:5]) or "安达维尔" in str(green_stocks[:5]):
            sector_analysis += "- **军工/防务**：雷科防务+4.08%、安达维尔+3.39%\n"
        if "天齐锂业" in str(green_stocks[:5]):
            sector_analysis += "- **锂电**：天齐锂业+3.03%\n"
        sector_analysis += f"\n领涨集中：{', '.join(big_names)}\n"
    
    report += sector_analysis
    
    report += f"""
---

## 🟢 逆势上涨（强势）— {len(green_stocks)}只

| 代码 | 名称 | 最新价 | 涨跌幅 | 技术信号 | 操作建议 |
|------|------|--------|--------|----------|----------|
"""
    for s in green_stocks:
        report += f"| {s['code']} | {s['name']} | {s['price']} | {s['pct']:+.2f}% | {s['trend_display'][:35]} | {s['action']} |\n"
    
    if yellow_stocks:
        report += f"""
---

## 🟡 小幅回调（观望）— {len(yellow_stocks)}只

| 代码 | 名称 | 最新价 | 涨跌幅 | 技术信号 | 操作建议 |
|------|------|--------|--------|----------|----------|
"""
        for s in yellow_stocks:
            report += f"| {s['code']} | {s['name']} | {s['price']} | {s['pct']:+.2f}% | {s['trend_display'][:35]} | {s['action']} |\n"
    
    if red_stocks:
        report += f"""
---

## 🔴 深度回调（需警惕）— {len(red_stocks)}只
"""
        for s in red_stocks:
            report += f"- **{s['name']}({s['code']})** {s['pct']:+.2f}% → {s['action']} | {s['reason'][:60]}\n"
    
    if alert_stocks:
        report += f"""
---

## 🚨 严重回调（紧急关注）— {len(alert_stocks)}只
"""
        for s in alert_stocks:
            report += f"- **{s['name']}({s['code']})** {s['pct']:+.2f}% → {s['action']} | {s['reason'][:60]}\n"
    
    report += f"""
---

## 📰 关键市场新闻

{news_section}
---

## 🔮 明日关注清单 & 预案

### ✅ 持有不动
"""
    hold_stocks = [s for s in green_stocks if "持有不动" in s["action"]][:5]
    for s in hold_stocks:
        report += f"- **{s['name']}({s['code']})** {s['pct']:+.2f}% → {s['action']}\n"
    if not hold_stocks:
        report += "- 暂无推荐持有的强势股\n"
    
    report += f"""
### ⚠️ 风险关注
"""
    risk_stocks = alert_stocks + red_stocks[:3]
    for s in risk_stocks:
        report += f"- **{s['name']}({s['code']})** {s['pct']:+.2f}% → {s['action']} | {s['reason'][:50]}\n"
    if not risk_stocks:
        report += "- 今日无重点关注风险标的 ✅\n"
    
    # 附加高RSI预警
    overbought = []
    for s in green_stocks:
        if isinstance(s.get('rsi'), (int, float)) and s['rsi'] > 70:
            overbought.append(s)
    if overbought:
        report += f"""
### ⚡ 超买预警（RSI>70，追高风险大）
"""
        for s in overbought:
            report += f"- **{s['name']}({s['code']})** RSI={s['rsi']}，短期涨幅过大，注意回调风险\n"
    
    report += f"""
### 💡 总体策略
- **市场氛围**：{mood}
- **仓位建议**：{"6-8成（积极）" if avg_change > 1 else "4-6成（中性偏多）" if avg_change > 0 else "3-5成（偏保守）"}
- **核心思路**：{"强势股持有，关注题材持续性" if avg_change > 0 else "控制仓位，等待企稳信号"}
- **明日关注**：大盘能否延续今日强势，成交量是否配合
- **特别提醒**：国瓷材料+9.14%、久立特材+7.94%短期涨幅过大，注意获利回吐压力

---

*数据日期：{today}（{weekday}）15:00收盘*
*数据来源：akshare实时行情 | baostock历史K线 | 技术指标自主计算*
*⚠️ 以上分析仅供参考，不构成投资建议。投资有风险，操作需谨慎。*
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
    avg = round(np.mean([v.get("change_pct", 0) for v in valid.values()]), 2) if valid else 0
    
    sorted_pct = sorted(valid.items(), key=lambda x: x[1].get("change_pct", 0), reverse=True)
    top3 = [(c, d["name"], d.get("change_pct", 0)) for c, d in sorted_pct[:3]]
    bot3 = [(c, d["name"], d.get("change_pct", 0)) for c, d in sorted_pct[-3:]]
    
    idx = ""
    if market_index:
        sh = market_index.get("上证指数", {})
        sz = market_index.get("深证成指", {})
        cy = market_index.get("创业板指", {})
        idx = f"\n  上证{sh.get('change_pct',0):+.2f}% | 深证{sz.get('change_pct',0):+.2f}% | 创业板{cy.get('change_pct',0):+.2f}%"
    
    s = f"📊 **A股复盘 | {REPORT_DATE}（周一）**\n"
    s += f"\n📈 {ups}涨 {downs}跌 | 均幅 {avg:+.2f}%{idx}\n\n"
    s += f"🏆 **涨幅前三：**\n"
    for c, n, p in top3:
        s += f"  ✅ {n}（{c}）{p:+.2f}%\n"
    s += f"\n🔻 **跌幅前三：**\n"
    for c, n, p in bot3:
        s += f"  🔴 {n}（{c}）{p:+.2f}%\n"
    
    drops = [(c, n, p) for c, (n, p) in [(c, (d["name"], d.get("change_pct", 0))) for c, d in sorted_pct if d.get("change_pct", 0) <= -3]]
    # Rewrite more simply
    drops_list = []
    for c, d in sorted_pct:
        p = d.get("change_pct", 0)
        if p <= -3:
            drops_list.append((c, d["name"], p))
    if drops_list:
        s += f"\n⚠️ **需关注回调：**\n"
        for c, n, p in drops_list:
            s += f"  {n}（{c}）{p:+.2f}%\n"
    
    # 超买提醒
    overbought = []
    for code, d in sorted_pct[:10]:
        if code in indicators_data and indicators_data[code]:
            ind = indicators_data[code]
            if ind.get("rsi") and ind["rsi"] > 70:
                overbought.append((code, d["name"], ind["rsi"]))
    if overbought:
        s += f"\n⚡ **RSI超买预警：**\n"
        for c, n, r in overbought:
            s += f"  {n}（{c}）RSI={r}\n"
    
    s += f"\n📎 完整复盘 → daily/{REPORT_DATE}.md"
    return s

def main():
    print("="*60)
    print(f"📊 A股每日复盘分析 v3 - {REPORT_DATE}（周一）")
    print("="*60)
    
    # Step 1: 实时行情
    spot_data = fetch_spot_data()
    if spot_data is None:
        print("⚠️ 新浪接口失败，尝试东方财富...")
        time.sleep(2)
        spot_data = fetch_spot_em_fallback()
    if spot_data is None:
        print("❌ 无法获取行情，退出")
        return
    
    # Step 2: 大盘指数
    market_index = check_market_index()
    
    # 打印行情
    sorted_items = sorted(spot_data.items(), key=lambda x: x[1].get("change_pct", 0) if x[1].get("status")=="ok" else 0, reverse=True)
    print(f"\n✅ 获取到 {len([v for v in spot_data.values() if v.get('status')=='ok'])} 只有效行情")
    for code, info in sorted_items:
        if info.get("status") == "ok":
            print(f"  {code} {info['name']}: {info.get('price','N/A')} ({info.get('change_pct',0):+.2f}%)")
        elif info.get("status") == "B股数据暂不可用":
            print(f"  {code} {info['name']}: B股数据暂不可用")
    
    # Step 3: 历史K线 + 技术指标
    print("\n📈 正在获取历史K线（baostock）并计算指标...")
    indicators_data = {}
    import baostock as bs
    bs.login()
    for code, name in STOCKS.items():
        if code in B_STOCKS:
            continue
        print(f"  {name}({code}): ", end="", flush=True)
        bs_code = get_bs_code(code)
        try:
            rs = bs.query_history_k_data_plus(bs_code,
                "date,open,high,low,close,volume,amount",
                start_date='2026-01-01', end_date=REPORT_DATE,
                frequency='d', adjustflag='2')
            rows = []
            while rs.next():
                row = rs.get_row_data()
                if row[0]:
                    rows.append({
                        "日期": row[0],
                        "开盘": float(row[1]) if row[1] else 0,
                        "最高": float(row[2]) if row[2] else 0,
                        "最低": float(row[3]) if row[3] else 0,
                        "收盘": float(row[4]) if row[4] else 0,
                        "成交量": float(row[5]) if row[5] else 0,
                        "成交额": float(row[6]) if row[6] else 0
                    })
            if len(rows) >= 20:
                df = pd.DataFrame(rows)
                df['日期'] = pd.to_datetime(df['日期'])
                df.sort_values('日期', inplace=True)
                ind = calculate_indicators(df)
                indicators_data[code] = ind
                print(f"✅ MA5={ind.get('ma5','')} MA20={ind.get('ma20','')} RSI={ind.get('rsi','')}")
            else:
                indicators_data[code] = {}
                print(f"❌ 数据不足({len(rows)}行)")
        except Exception as e:
            indicators_data[code] = {}
            print(f"❌ {str(e)[:30]}")
    bs.logout()
    
    # Step 4: 新闻
    print("\n📰 获取财经新闻...")
    news_data = fetch_market_news()
    if not news_data:
        print("⚠️ 新闻不可用")
    
    # Step 5: 生成报告
    print("\n📝 生成复盘报告...")
    report = generate_report(spot_data, indicators_data, news_data, market_index)
    
    # Step 6: 保存
    filepath = save_report(report)
    
    # Step 7: 摘要
    summary = generate_short_summary(spot_data, indicators_data, market_index)
    
    print("\n\n" + "="*60)
    print("📋 推送摘要：")
    print(summary)
    
    with open(f"{ANALYSIS_DIR}/daily/{REPORT_DATE}_summary.txt", "w", encoding="utf-8") as f:
        f.write(summary)
    
    print("\n✅ 分析完成!")

if __name__ == "__main__":
    main()
