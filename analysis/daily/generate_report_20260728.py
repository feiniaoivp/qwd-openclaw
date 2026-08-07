#!/usr/bin/env python3
"""
每日A股复盘分析 - 2026-07-28（周二）
针对25只关注股票
"""
import akshare as ak
import pandas as pd
import numpy as np
import json
import warnings
import time
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

# 关注股票列表（从 analysis_summary_all.md 提取）
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
    """获取股票前缀用于新浪接口"""
    code_str = str(code).strip()
    if code_str.startswith('6') or code_str.startswith('9'):
        return f"sh{code_str}"
    elif code_str.startswith('0') or code_str.startswith('3'):
        return f"sz{code_str}"
    elif code_str.startswith('4') or code_str.startswith('8'):
        return f"bj{code_str}"
    return code_str

def safe_get_spot():
    """获取实时行情"""
    for attempt in range(2):
        try:
            df = ak.stock_zh_a_spot()
            return df
        except Exception as e:
            print(f"  [WARN] 新浪接口尝试 {attempt+1} 失败: {e}")
            time.sleep(2)
    return None

def safe_get_kline(code, days=120):
    """获取历史K线"""
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                start_date="20260101", adjust="qfq")
        return df
    except Exception as e:
        print(f"  [WARN] K线获取失败 {code}: {e}")
        return None

def safe_get_news(max_pages=3):
    """获取财经新闻"""
    for attempt in range(2):
        try:
            df = ak.stock_info_global_em()
            if df is not None and not df.empty:
                return df
        except:
            pass
        try:
            df = ak.stock_news_em()
            if df is not None and not df.empty:
                return df
        except:
            pass
        time.sleep(1)
    return None

def safe_get_index():
    """获取大盘指数"""
    try:
        index_codes = ["sh000001", "sz399001", "sz399006"]  # 上证, 深证, 创业板
        df = ak.stock_zh_index_spot_em()
        if df is not None and not df.empty:
            return df
    except:
        pass
    return None

def compute_ma(series, window):
    """计算移动平均线"""
    if len(series) < window:
        return None
    return series.tail(window).mean()

def compute_macd(prices, fast=12, slow=26, signal=9):
    """计算MACD"""
    if len(prices) < slow:
        return None, None, None
    exp1 = prices.ewm(span=fast, adjust=False).mean()
    exp2 = prices.ewm(span=slow, adjust=False).mean()
    macd_line = exp1 - exp2
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line.iloc[-1], signal_line.iloc[-1], histogram.iloc[-1]

def compute_rsi(prices, window=14):
    """计算RSI"""
    if len(prices) < window:
        return None
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).tail(window)
    loss = (-delta.where(delta < 0, 0)).tail(window)
    avg_gain = gain.mean()
    avg_loss = loss.mean()
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def compute_bollinger(prices, window=20):
    """计算布林带"""
    if len(prices) < window:
        return None, None, None
    ma = prices.tail(window).mean()
    std = prices.tail(window).std()
    upper = ma + 2 * std
    lower = ma - 2 * std
    return upper, ma, lower

def analyze_stock(code, name, spot_df, all_kline_data):
    """分析单只股票"""
    result = {
        "code": code,
        "name": name,
        "price": None,
        "change_pct": None,
        "volume": None,
        "turnover": None,
        "ma5": None, "ma10": None, "ma20": None, "ma60": None,
        "macd": None, "rsi": None,
        "boll_upper": None, "boll_mid": None, "boll_lower": None,
        "vol_ratio": None,
        "trend": "未知",
        "support": None,
        "resistance": None,
        "signal": "持有",
        "detail": ""
    }
    
    # 如果B股
    if code == "900925":
        result["price"] = "B股数据暂不可用"
        result["change_pct"] = 0
        result["detail"] = "B股（机电B股），新浪接口不覆盖B股，暂无实时行情"
        return result
    
    # 从实时行情中匹配
    prefix = get_stock_prefix(code)
    if spot_df is not None:
        spot_row = spot_df[spot_df['代码'] == prefix] if '代码' in spot_df.columns else None
        if spot_row is not None and len(spot_row) > 0:
            row = spot_row.iloc[0]
            try:
                result["price"] = float(row.get('最新价', row.get('现价', 0)))
                result["change_pct"] = float(row.get('涨跌幅', 0))
                result["volume"] = float(row.get('成交量', row.get('成交额', 0)))
                result["turnover"] = float(row.get('成交额', 0))
            except:
                pass
    
    # 如果新浪接口没匹配到，尝试直接从K线获取
    if result["price"] is None:
        kline = all_kline_data.get(code)
        if kline is not None and len(kline) > 0:
            last_row = kline.iloc[-1]
            result["price"] = float(last_row.get('收盘', last_row.get('close', 0)))
            result["change_pct"] = float(last_row.get('涨跌幅', 0))
            result["volume"] = float(last_row.get('成交量', last_row.get('volume', 0)))
            result["turnover"] = float(last_row.get('成交额', last_row.get('amount', 0)))
    
    # 计算技术指标
    kline = all_kline_data.get(code)
    if kline is not None and len(kline) > 20:
        # 确定收盘价列名
        close_col = None
        for col in ['收盘', 'close', 'Close']:
            if col in kline.columns:
                close_col = col
                break
        
        if close_col:
            prices = kline[close_col].astype(float)
            volumes = kline.get('成交量', kline.get('volume', kline.get('Volume', None)))
            
            # MA指标
            result["ma5"] = round(float(compute_ma(prices, 5)), 2) if len(prices) >= 5 else None
            result["ma10"] = round(float(compute_ma(prices, 10)), 2) if len(prices) >= 10 else None
            result["ma20"] = round(float(compute_ma(prices, 20)), 2) if len(prices) >= 20 else None
            result["ma60"] = round(float(compute_ma(prices, 60)), 2) if len(prices) >= 60 else None
            
            # MACD
            macd_line, signal_line, histogram = compute_macd(prices)
            if macd_line is not None:
                result["macd"] = {
                    "macd": round(float(macd_line), 3),
                    "signal": round(float(signal_line), 3),
                    "histogram": round(float(histogram), 3)
                }
            
            # RSI
            result["rsi"] = round(float(compute_rsi(prices)), 1) if compute_rsi(prices) is not None else None
            
            # 布林带
            upper, mid, lower = compute_bollinger(prices)
            if upper is not None:
                result["boll_upper"] = round(float(upper), 2)
                result["boll_mid"] = round(float(mid), 2)
                result["boll_lower"] = round(float(lower), 2)
            
            # 成交量变化（当前成交量/20日均量）
            if volumes is not None:
                volumes = volumes.astype(float)
                if len(volumes) >= 20:
                    avg_vol = volumes.tail(20).mean()
                    cur_vol = volumes.iloc[-1]
                    result["vol_ratio"] = round(float(cur_vol / avg_vol), 2) if avg_vol > 0 else None
            
            # 趋势判断
            price = float(prices.iloc[-1])
            signals = []
            
            if result["ma5"] and result["ma10"] and result["ma20"]:
                # 多头排列
                if result["ma5"] > result["ma10"] > result["ma20"]:
                    signals.append("多头排列")
                # 空头排列
                elif result["ma5"] < result["ma10"] < result["ma20"]:
                    signals.append("空头排列")
                # 价格在均线上方
                if price > result["ma20"]:
                    signals.append("站上MA20")
                elif price < result["ma20"]:
                    signals.append("跌破MA20")
            
            if result["macd"]:
                if result["macd"]["histogram"] > 0:
                    signals.append("MACD红柱")
                else:
                    signals.append("MACD绿柱")
                if result["macd"]["macd"] > result["macd"]["signal"]:
                    signals.append("MACD金叉" if histogram and histogram > 0 else "MACD多头")
                else:
                    signals.append("MACD死叉" if histogram and histogram < 0 else "MACD空头")
            
            if result["rsi"]:
                if result["rsi"] > 70:
                    signals.append("RSI超买")
                elif result["rsi"] < 30:
                    signals.append("RSI超卖")
            
            if result["boll_upper"] and result["boll_lower"]:
                if price >= result["boll_upper"]:
                    signals.append("触及布林上轨")
                elif price <= result["boll_lower"]:
                    signals.append("触及布林下轨")
            
            result["trend"] = " | ".join(signals) if signals else "震荡"
            
            # 支撑压力位
            if result["ma10"]:
                result["support"] = result["ma10"]
            if result["ma20"]:
                result["resistance"] = result["ma20"]
            
            # 如果价格低于MA20，MA20是压力位；如果高于MA20，MA20是支撑位
            if result["ma20"]:
                if price < result["ma20"]:
                    result["resistance"] = result["ma20"]
                    result["support"] = result["boll_lower"] if result["boll_lower"] else result["ma10"]
                else:
                    result["support"] = result["ma20"]
                    result["resistance"] = result["boll_upper"] if result["boll_upper"] else result["ma5"] * 1.05
            
            # 操作建议
            if result["change_pct"] is not None:
                if result["change_pct"] > 3:
                    result["signal"] = "持有/强势"
                elif result["change_pct"] > 0:
                    result["signal"] = "持有"
                elif result["change_pct"] > -3:
                    result["signal"] = "观望"
                elif result["change_pct"] > -5:
                    result["signal"] = "关注止跌"
                else:
                    result["signal"] = "警惕/减仓"
            
            # 详细说明
            parts = []
            if result["change_pct"] is not None:
                if result["change_pct"] > 0:
                    parts.append(f"今日上涨{result['change_pct']:.2f}%")
                else:
                    parts.append(f"今日下跌{result['change_pct']:.2f}%")
            
            if result["vol_ratio"]:
                if result["vol_ratio"] > 1.5:
                    parts.append("放量显著")
                elif result["vol_ratio"] > 1.2:
                    parts.append("温和放量")
                elif result["vol_ratio"] < 0.7:
                    parts.append("缩量明显")
            
            if "多头排列" in signals:
                parts.append("均线多头排列，趋势向上")
            elif "空头排列" in signals:
                parts.append("均线空头排列，趋势偏弱")
            
            if result["rsi"] and result["rsi"] > 70:
                parts.append("短期超买，注意回调风险")
            elif result["rsi"] and result["rsi"] < 30:
                parts.append("短期超卖，关注反弹机会")
            
            result["detail"] = "，".join(parts) if parts else "正常波动"
    
    return result

def main():
    print("=" * 60)
    print("📊 A股每日复盘分析 - 2026年7月28日（周二）")
    print("=" * 60)
    
    report_date = "2026-07-28"
    report_date_cn = "2026年7月28日（周二）"
    
    # Step 1: 获取实时行情
    print("\n📡 获取实时行情（新浪接口）...")
    spot_df = safe_get_spot()
    if spot_df is not None:
        print(f"  ✅ 获取成功，共 {len(spot_df)} 条数据")
    else:
        print("  ⚠️ 新浪接口失败，将尝试从K线获取")
    
    # Step 2: 获取所有K线数据
    print("\n📡 获取个股K线数据...")
    all_kline = {}
    for code, name in STOCKS.items():
        if code == "900925":
            continue  # B股跳过
        print(f"  {code} {name}...", end=" ")
        kline = safe_get_kline(code)
        if kline is not None:
            all_kline[code] = kline
            print(f"✅ ({len(kline)}条)")
        else:
            print(f"❌")
        time.sleep(0.3)  # 避免接口限流
    
    # Step 3: 获取新闻
    print("\n📡 获取财经新闻...")
    news_df = safe_get_news()
    if news_df is not None:
        print(f"  ✅ 获取成功，共 {len(news_df)} 条")
    else:
        print("  ⚠️ 新闻接口不可用")
    
    # Step 4: 分析每只股票
    print("\n" + "=" * 60)
    print("📈 分析每只股票...")
    results = []
    for code, name in STOCKS.items():
        result = analyze_stock(code, name, spot_df, all_kline)
        results.append(result)
        print(f"  {code} {name}: {result['price']} ({result['change_pct']}%) → {result['trend'][:30] if len(result['trend']) > 30 else result['trend']}")
    
    # Step 5: 统计盘面
    valid_results = [r for r in results if r['change_pct'] is not None and r['change_pct'] != "B股数据暂不可用"]
    positive = [r for r in valid_results if r['change_pct'] is not None and r['change_pct'] > 0]
    negative = [r for r in valid_results if r['change_pct'] is not None and r['change_pct'] < 0]
    zero = [r for r in valid_results if r['change_pct'] is not None and r['change_pct'] == 0]
    
    avg_change = np.mean([float(r['change_pct']) for r in valid_results if r['change_pct'] is not None])
    
    up_5 = [r for r in valid_results if r['change_pct'] is not None and r['change_pct'] >= 5]
    down_3 = [r for r in valid_results if r['change_pct'] is not None and r['change_pct'] <= -3]
    down_5 = [r for r in valid_results if r['change_pct'] is not None and r['change_pct'] <= -5]
    
    print(f"\n📊 统计: 上涨{len(positive)}只 | 下跌{len(negative)}只 | 平均{avg_change:.2f}%")
    
    # Step 6: 生成报告
    report = generate_markdown_report(results, report_date_cn, spot_df, news_df if news_df is not None else None)
    
    # Step 7: 保存报告
    filename = f"/Users/duguke/.openclaw/workspace/analysis/daily/{report_date}.md"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n✅ 报告已保存: {filename}")
    
    # Step 8: 生成摘要
    summary = generate_summary(results, avg_change, len(positive), len(negative), report_date_cn)
    summary_file = f"/Users/duguke/.openclaw/workspace/analysis/daily/{report_date}_summary.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"✅ 摘要已保存: {summary_file}")
    
    print("\n" + "=" * 60)
    print("📋 核心摘要")
    print("=" * 60)
    print(summary["brief"])
    print(f"\n涨幅前3: {summary['top_gainers']}")
    print(f"跌幅前3: {summary['top_losers']}")
    print(f"\n操作建议: {summary['action_advice']}")
    
    return report, summary

def generate_summary(results, avg_change, pos_count, neg_count, report_date_cn):
    """生成核心摘要"""
    valid = [r for r in results if r['change_pct'] is not None and r['change_pct'] != "B股数据暂不可用"]
    
    # 排序
    sorted_by_change = sorted(valid, key=lambda x: float(x['change_pct'] or 0), reverse=True)
    
    top3_gainers = [(r['code'], r['name'], f"{r['change_pct']:+.2f}%") for r in sorted_by_change[:3]]
    top3_losers = [(r['code'], r['name'], f"{r['change_pct']:+.2f}%") for r in sorted_by_change[-3:] if float(r['change_pct'] or 0) < 0]
    
    # 操作建议汇总
    hold = [r['name'] for r in valid if r['signal'] in ['持有', '持有/强势']]
    watch = [r['name'] for r in valid if r['signal'] in ['观望', '关注止跌']]
    danger = [r['name'] for r in valid if '警惕' in (r['signal'] or '') or '减仓' in (r['signal'] or '')]
    
    # 盘面特征
    if avg_change > 1:
        market_desc = "强势"
    elif avg_change > 0:
        market_desc = "偏强"
    elif avg_change > -1:
        market_desc = "偏弱"
    else:
        market_desc = "弱势"
    
    brief = f"""【{report_date_cn} A股复盘】
盘面特征：{market_desc}，关注股上涨{pos_count}只/下跌{neg_count}只，平均涨跌幅{avg_change:+.2f}%
涨幅≥5%：{len([r for r in sorted_by_change if float(r['change_pct'] or 0) >= 5])}只
跌幅≥3%：{len([r for r in sorted_by_change if float(r['change_pct'] or 0) <= -3])}只
跌幅≥5%：{len([r for r in sorted_by_change if float(r['change_pct'] or 0) <= -5])}只"""

    return {
        "date": report_date_cn,
        "avg_change": round(avg_change, 2),
        "positive": pos_count,
        "negative": neg_count,
        "total": len(valid),
        "up_5": len([r for r in sorted_by_change if float(r['change_pct'] or 0) >= 5]),
        "down_3": len([r for r in sorted_by_change if float(r['change_pct'] or 0) <= -3]),
        "down_5": len([r for r in sorted_by_change if float(r['change_pct'] or 0) <= -5]),
        "brief": brief,
        "top_gainers": " | ".join([f"{n}({c})" for _, n, c in top3_gainers]),
        "top_losers": " | ".join([f"{n}({c})" if c else "无" for _, n, c in top3_losers]) if top3_losers else "无",
        "action_advice": f"持有: {len(hold)}只 | 观望: {len(watch)}只 | 警惕: {len(danger)}只",
    }

def generate_markdown_report(results, report_date_cn, spot_df, news_df):
    """生成Markdown报告"""
    
    valid_results = [r for r in results if r['change_pct'] is not None and r['change_pct'] != "B股数据暂不可用"]
    
    # 按涨跌幅排序
    sorted_results = sorted(valid_results, key=lambda x: float(x['change_pct'] or 0), reverse=True)
    
    pos_count = len([r for r in sorted_results if float(r['change_pct'] or 0) > 0])
    neg_count = len([r for r in sorted_results if float(r['change_pct'] or 0) < 0])
    zero_count = len([r for r in sorted_results if float(r['change_pct'] or 0) == 0])
    avg_change = np.mean([float(r['change_pct'] or 0) for r in sorted_results])
    
    up_5 = [r for r in sorted_results if float(r['change_pct'] or 0) >= 5]
    up_3 = [r for r in sorted_results if 3 <= float(r['change_pct'] or 0) < 5]
    up_0 = [r for r in sorted_results if 0 < float(r['change_pct'] or 0) < 3]
    
    down_0_3 = [r for r in sorted_results if -3 < float(r['change_pct'] or 0) <= 0]
    down_3_5 = [r for r in sorted_results if -5 < float(r['change_pct'] or 0) <= -3]
    down_5 = [r for r in sorted_results if float(r['change_pct'] or 0) <= -5]
    
    b_share = [r for r in results if r['code'] == "900925"]
    
    lines = []
    lines.append(f"## 📊 {report_date_cn} A股收盘复盘")
    lines.append("**—— 针对你的25只关注股票**")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("### 📈 盘面总览")
    lines.append("")
    lines.append("| 指标 | 数据 |")
    lines.append("|------|------|")
    lines.append(f"| **上涨** | ✅ {pos_count}只 ({pos_count/len(valid_results)*100:.0f}%) |")
    lines.append(f"| **下跌** | 🔴 {neg_count}只 ({neg_count/len(valid_results)*100:.0f}%) |")
    lines.append(f"| **平盘** | {zero_count}只 |")
    lines.append(f"| **平均涨跌幅** | {avg_change:+.2f}% |")
    lines.append(f"| **涨幅≥5%** | {len(up_5)}只 |")
    lines.append(f"| **跌幅≥5%** | {len(down_5)}只 |")
    lines.append(f"| **跌停(≥-10%)** | {len([r for r in sorted_results if float(r['change_pct'] or 0) <= -10])}只 |")
    lines.append("")
    
    # 盘面特征一句话
    if avg_change > 1:
        summary_text = "今日整体偏强"
    elif avg_change > 0:
        summary_text = "今日窄幅震荡偏强"
    elif avg_change > -1:
        summary_text = "今日窄幅震荡偏弱"
    else:
        summary_text = "今日整体偏弱"
    
    lines.append(f"> **一句话总结**：关注股上涨{pos_count}只/下跌{neg_count}只，平均{avg_change:+.2f}%，{summary_text}。")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # 🟢 涨幅≥5%
    if up_5:
        lines.append("### 🟢 逆势大涨（涨幅≥5%）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 | 说明 |")
        lines.append("|------|------|--------|--------|------|")
        for r in up_5:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | **+{r['change_pct']:.2f}%** | {r['detail']} |")
        lines.append("")
    
    # 🟢 涨幅3%~5%
    if up_3:
        lines.append("### 🟢 中度上涨（涨幅3%~5%）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 | 说明 |")
        lines.append("|------|------|--------|--------|------|")
        for r in up_3:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | **+{r['change_pct']:.2f}%** | {r['detail']} |")
        lines.append("")
    
    # 🟢 涨幅0%~3%
    if up_0:
        lines.append("### 🟢 小幅上涨（涨幅0%~3%）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 | 说明 |")
        lines.append("|------|------|--------|--------|------|")
        for r in up_0:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | **+{r['change_pct']:.2f}%** | {r['detail']} |")
        lines.append("")
    
    # 🟡 微幅回调
    if down_0_3:
        lines.append("### 🟡 微幅回调（跌幅0%~-3%）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 | 预警 |")
        lines.append("|------|------|--------|--------|------|")
        for r in down_0_3:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['change_pct']:+.2f}% | {r['detail']} |")
        lines.append("")
    
    # 🔴 深度回调
    if down_3_5:
        lines.append("### 🔴 深度回调（跌幅-3%~-5%）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 | 预警 |")
        lines.append("|------|------|--------|--------|------|")
        for r in down_3_5:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['change_pct']:+.2f}% | ⚠️ {r['detail']} |")
        lines.append("")
    
    # 🚨 严重回调
    if down_5:
        lines.append("### 🚨 严重回调（跌幅≥-5%）")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 | 预警 |")
        lines.append("|------|------|--------|--------|------|")
        for r in down_5:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['change_pct']:+.2f}% | 🚨 {r['detail']} |")
        lines.append("")
    
    # B股
    if b_share:
        lines.append("### ⚠️ 数据缺失")
        lines.append("")
        lines.append("| 代码 | 名称 | 状态 |")
        lines.append("|------|------|------|")
        for r in b_share:
            lines.append(f"| {r['code']} | {r['name']} | B股数据暂不可用（新浪接口不覆盖B股） |")
        lines.append("")
    
    lines.append("---")
    lines.append("")
    
    # 技术分析汇总
    lines.append("### 📊 技术指标分析")
    lines.append("")
    for r in sorted_results[:5]:  # 只显示前5只详细技术指标
        lines.append(f"**{r['code']} {r['name']}** — 现价{r['price']}")
        lines.append(f"- 均线: MA5={r['ma5']} MA10={r['ma10']} MA20={r['ma20']} MA60={r['ma60']}")
        lines.append(f"- MACD: {'红柱' if r.get('macd') and r['macd']['histogram'] > 0 else '绿柱'} | RSI(14)={r['rsi']}")
        lines.append(f"- 布林带: 上轨{r['boll_upper']} 中轨{r['boll_mid']} 下轨{r['boll_lower']}")
        lines.append(f"- 量比: {r['vol_ratio']} | 趋势: {r['trend']}")
        lines.append("")
    
    lines.append("---")
    lines.append("")
    
    # 🔮 操作建议
    lines.append("### 🔮 操作建议")
    lines.append("")
    
    strong_hold = [r for r in sorted_results if r['signal'] == '持有/强势']
    hold = [r for r in sorted_results if r['signal'] == '持有']
    watch = [r for r in sorted_results if r['signal'] in ['观望', '关注止跌']]
    danger = [r for r in sorted_results if '警惕' in (r['signal'] or '') or '减仓' in (r['signal'] or '')]
    
    if strong_hold:
        lines.append("**✅ 持有/强势（趋势向上，可继续持有）**")
        for r in strong_hold:
            lines.append(f"- **{r['name']}**（{r['code']}）：{r['detail']}")
        lines.append("")
    
    if hold:
        lines.append("**✅ 持有不动（正常波动，基本面未恶化）**")
        names = [r['name'] for r in hold]
        lines.append(f"—— {', '.join(names)}")
        lines.append("")
    
    if watch:
        lines.append("**👀 观望/等待企稳（短期偏弱，需观察）**")
        for r in watch:
            lines.append(f"- **{r['name']}**（{r['code']}）：{r['detail']}")
        lines.append("")
    
    if danger:
        lines.append("**⚠️ 警惕/关注（偏弱，需设置止损）**")
        for r in danger:
            lines.append(f"- **{r['name']}**（{r['code']}）：{r['detail']}")
        lines.append("")
    
    lines.append("---")
    lines.append("")
    lines.append("### 💡 建议仓位")
    lines.append("")
    
    if avg_change > 1:
        lines.append("> 市场偏强，建议仓位 **7~8成**")
    elif avg_change > 0:
        lines.append("> 市场震荡偏强，建议仓位 **5~7成**")
    elif avg_change > -1:
        lines.append("> 市场震荡偏弱，建议仓位 **3~5成**")
    else:
        lines.append("> 市场弱势，建议仓位 **2~3成**")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*数据来源：akshare 新浪接口 | 分析时间：2026-07-28 15:42*")
    
    return "\n".join(lines)

if __name__ == "__main__":
    report, summary = main()
