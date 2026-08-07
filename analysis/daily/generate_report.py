#!/usr/bin/env python3
"""
每日A股复盘报告生成器
分析25只关注股 + 大盘指数 + 板块轮动
"""
import akshare as ak
import pandas as pd
import numpy as np
import json
import warnings
import traceback
from datetime import datetime, timedelta
import os

warnings.filterwarnings('ignore')

# ============ 配置 ============
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

TODAY = datetime.now()
DATE_STR = TODAY.strftime("%Y-%m-%d")

# ============ 技术指标计算 ============

def calc_ma(series, window):
    return series.rolling(window).mean()

def calc_macd(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast).mean()
    ema_slow = close.ewm(span=slow).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal).mean()
    macd = 2 * (dif - dea)
    return dif, dea, macd

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calc_bollinger(close, window=20, num_std=2):
    ma = close.rolling(window).mean()
    std = close.rolling(window).std()
    upper = ma + num_std * std
    lower = ma - num_std * std
    return upper, ma, lower

def calc_volume_ratio(volume, window=5):
    avg_vol = volume.rolling(window).mean()
    return volume / avg_vol

# ============ 数据获取 ============

def get_index_data():
    """获取大盘指数"""
    results = {}
    indices = {
        "上证指数": "sh000001",
        "深证成指": "sz399001",
        "创业板指": "sz399006",
        "科创50": "sh000688"
    }
    try:
        # 实时行情
        spot = ak.stock_zh_index_spot_em()
        for name, code in indices.items():
            row = spot[spot['代码'] == code]
            if not row.empty:
                r = row.iloc[0]
                results[name] = {
                    "最新价": float(r.get('最新价', 0)),
                    "涨跌幅": float(r.get('涨跌幅', 0)),
                    "涨跌额": float(r.get('涨跌额', 0)),
                    "成交量": float(r.get('成交量', 0)),
                    "成交额": float(r.get('成交额', 0)),
                    "今开": float(r.get('今开', 0)),
                    "昨收": float(r.get('昨收', 0)),
                    "最高": float(r.get('最高', 0)),
                    "最低": float(r.get('最低', 0)),
                }
            else:
                results[name] = {"error": "未找到行情"}
    except Exception as e:
        print(f"获取指数数据失败: {e}")
        traceback.print_exc()
    
    # 尝试获取日K线
    try:
        end_date = TODAY.strftime("%Y%m%d")
        start_date = (TODAY - timedelta(days=120)).strftime("%Y%m%d")
        
        for name, code in indices.items():
            try:
                prefix = code[:2]
                index_code = code[2:]
                if prefix == 'sh':
                    symbol = f"sh{index_code}"
                else:
                    symbol = f"sz{index_code}"
                hist = ak.stock_zh_index_daily(symbol=symbol)
                if hist is not None and not hist.empty:
                    hist = hist.sort_index()
                    recent = hist.tail(60)
                    if not recent.empty:
                        close = recent['close']
                        if name not in results:
                            results[name] = {}
                        results[name]['hist'] = {
                            'close_5': float(close.tail(5).mean()) if len(close) >= 5 else None,
                            'close_10': float(close.tail(10).mean()) if len(close) >= 10 else None,
                            'close_20': float(close.tail(20).mean()) if len(close) >= 20 else None,
                            'close_60': float(close.tail(60).mean()) if len(close) >= 60 else None,
                            'latest_close': float(close.iloc[-1]),
                        }
                        # 计算MACD
                        dif, dea, macd = calc_macd(close)
                        results[name]['hist']['macd_dif'] = float(dif.iloc[-1]) if not pd.isna(dif.iloc[-1]) else None
                        results[name]['hist']['macd_dea'] = float(dea.iloc[-1]) if not pd.isna(dea.iloc[-1]) else None
                        results[name]['hist']['macd'] = float(macd.iloc[-1]) if not pd.isna(macd.iloc[-1]) else None
            except Exception as e:
                print(f"获取{name}K线失败: {e}")
    except Exception as e:
        print(f"获取指数K线失败: {e}")
    
    return results

def get_sector_data():
    """获取板块轮动数据"""
    try:
        # 东方财富板块热点
        sector = ak.stock_board_industry_name_em()
        if sector is not None and not sector.empty:
            top = sector.head(10)
            return top.to_dict('records')
    except Exception as e:
        print(f"获取板块数据失败: {e}")
    return []

def get_sector_flow():
    """获取板块资金流向"""
    try:
        flow = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业板块")
        if flow is not None and not flow.empty:
            return flow.head(10).to_dict('records')
    except Exception as e:
        print(f"获取板块资金流失败: {e}")
    return []

def get_stock_data(code, name):
    """获取个股数据"""
    result = {"code": code, "name": name, "error": None}
    
    # 1. 实时行情
    try:
        spot = ak.stock_zh_a_spot_em()
        row = spot[spot['代码'] == code]
        if not row.empty:
            r = row.iloc[0]
            result['spot'] = {
                "最新价": float(r.get('最新价', 0)),
                "涨跌幅": float(r.get('涨跌幅', 0)),
                "涨跌额": float(r.get('涨跌额', 0)),
                "成交量": float(r.get('成交量', 0)),
                "成交额": float(r.get('成交额', 0)),
                "换手率": float(r.get('换手率', 0)),
                "今开": float(r.get('今开', 0)),
                "昨收": float(r.get('昨收', 0)),
                "最高": float(r.get('最高', 0)),
                "最低": float(r.get('最低', 0)),
                "市盈率": float(r.get('市盈率-动态', 0)) if not pd.isna(r.get('市盈率-动态', None)) else None,
                "总市值": float(r.get('总市值', 0)),
                "流通市值": float(r.get('流通市值', 0)),
            }
    except Exception as e:
        result['error'] = f"实时行情: {e}"
    
    # 2. 历史K线（前复权）- 用于技术分析
    try:
        end_date = TODAY.strftime("%Y%m%d")
        start_date = (TODAY - timedelta(days=180)).strftime("%Y%m%d")
        hist = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                  start_date=start_date, end_date=end_date, 
                                  adjust="qfq")
        if hist is not None and not hist.empty:
            close = hist['收盘']
            volume = hist['成交量']
            high = hist['最高']
            low = hist['最低']
            open_p = hist['开盘']
            
            tech = {}
            
            # MA
            tech['ma5'] = float(calc_ma(close, 5).iloc[-1]) if len(close) >= 5 else None
            tech['ma10'] = float(calc_ma(close, 10).iloc[-1]) if len(close) >= 10 else None
            tech['ma20'] = float(calc_ma(close, 20).iloc[-1]) if len(close) >= 20 else None
            tech['ma60'] = float(calc_ma(close, 60).iloc[-1]) if len(close) >= 60 else None
            
            # MA多头排列判断
            ma_values = [v for v in [tech['ma5'], tech['ma10'], tech['ma20'], tech['ma60']] if v is not None]
            latest_close = float(close.iloc[-1])
            
            if len(ma_values) >= 3:
                sorted_desc = all(ma_values[i] >= ma_values[i+1] for i in range(len(ma_values)-1))
                sorted_asc = all(ma_values[i] <= ma_values[i+1] for i in range(len(ma_values)-1))
                tech['ma_arrangement'] = '多头排列' if sorted_desc else ('空头排列' if sorted_asc else '交叉')
                # 价格相对于MA的位置
                above_count = sum(1 for v in ma_values if latest_close > v)
                tech['price_vs_ma'] = f"收于{above_count}/{len(ma_values)}均线上方"
            else:
                tech['ma_arrangement'] = '数据不足'
                tech['price_vs_ma'] = '数据不足'
            
            # MACD
            dif, dea, macd_val = calc_macd(close)
            tech['macd_dif'] = float(dif.iloc[-1]) if not pd.isna(dif.iloc[-1]) else None
            tech['macd_dea'] = float(dea.iloc[-1]) if not pd.isna(dea.iloc[-1]) else None
            tech['macd'] = float(macd_val.iloc[-1]) if not pd.isna(macd_val.iloc[-1]) else None
            # MACD金叉/死叉判断
            if len(dif) >= 2 and len(dea) >= 2:
                prev_dif, prev_dea = float(dif.iloc[-2]), float(dea.iloc[-2])
                cur_dif, cur_dea = float(dif.iloc[-1]), float(dea.iloc[-1])
                if prev_dif < prev_dea and cur_dif >= cur_dea:
                    tech['macd_signal'] = '金叉'
                elif prev_dif > prev_dea and cur_dif <= cur_dea:
                    tech['macd_signal'] = '死叉'
                elif cur_dif > cur_dea:
                    tech['macd_signal'] = '多方'
                else:
                    tech['macd_signal'] = '空方'
            else:
                tech['macd_signal'] = '数据不足'
            
            # RSI
            rsi = calc_rsi(close)
            current_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else None
            tech['rsi'] = current_rsi
            if current_rsi is not None:
                if current_rsi > 70:
                    tech['rsi_signal'] = '超买'
                elif current_rsi < 30:
                    tech['rsi_signal'] = '超卖'
                elif current_rsi > 50:
                    tech['rsi_signal'] = '偏强'
                else:
                    tech['rsi_signal'] = '偏弱'
            else:
                tech['rsi_signal'] = '数据不足'
            
            # 布林带
            upper, mid, lower = calc_bollinger(close)
            tech['boll_upper'] = float(upper.iloc[-1]) if not pd.isna(upper.iloc[-1]) else None
            tech['boll_mid'] = float(mid.iloc[-1]) if not pd.isna(mid.iloc[-1]) else None
            tech['boll_lower'] = float(lower.iloc[-1]) if not pd.isna(lower.iloc[-1]) else None
            if tech['boll_upper'] is not None:
                if latest_close > tech['boll_upper']:
                    tech['boll_position'] = '上轨上方（超涨）'
                elif latest_close > tech['boll_mid']:
                    tech['boll_position'] = '中轨与上轨之间'
                elif latest_close > tech['boll_lower']:
                    tech['boll_position'] = '中轨与下轨之间'
                else:
                    tech['boll_position'] = '下轨下方（超跌）'
            
            # 成交量变化
            vol_ratio = calc_volume_ratio(volume)
            tech['volume_ratio'] = float(vol_ratio.iloc[-1]) if not pd.isna(vol_ratio.iloc[-1]) else None
            if tech['volume_ratio'] is not None:
                if tech['volume_ratio'] > 1.5:
                    tech['volume_signal'] = '放量'
                elif tech['volume_ratio'] < 0.7:
                    tech['volume_signal'] = '缩量'
                else:
                    tech['volume_signal'] = '平量'
            
            # 支撑压力位
            recent_high = float(high.tail(20).max())
            recent_low = float(low.tail(20).min())
            tech['support'] = recent_low
            tech['resistance'] = recent_high
            
            # 趋势判断
            if len(close) >= 20:
                sma_20 = float(close.tail(20).mean())
                sma_5 = float(close.tail(5).mean())
                if sma_5 > sma_20 and latest_close > sma_5:
                    tech['trend'] = '多头'
                elif sma_5 < sma_20 and latest_close < sma_5:
                    tech['trend'] = '空头'
                else:
                    tech['trend'] = '震荡'
            else:
                tech['trend'] = '数据不足'
            
            result['tech'] = tech
            result['latest_price'] = latest_close
    except Exception as e:
        if result['error']:
            result['error'] += f"; K线: {e}"
        else:
            result['error'] = f"K线: {e}"
        traceback.print_exc()
    
    return result

def get_news(code, name):
    """获取个股新闻/公告"""
    try:
        news = ak.stock_news_em(symbol=code)
        if news is not None and not news.empty:
            items = []
            for _, row in news.head(5).iterrows():
                items.append({
                    "title": row.get('标题', ''),
                    "time": str(row.get('发布时间', '')) if '发布时间' in row else ''
                })
            return items
    except Exception as e:
        print(f"获取{name}新闻失败: {e}")
    return []

# ============ 趋势分析函数 ============

def analyze_stock_trend(data):
    """综合分析个股趋势"""
    tech = data.get('tech', {})
    spot = data.get('spot', {})
    news = data.get('news', [])
    
    if not tech:
        return {"signal": "数据不足", "reason": "技术指标数据不完整", "advice": "暂不操作", "risk": "数据不足"}
    
    trend = tech.get('trend', '未知')
    rsi_signal = tech.get('rsi_signal', '')
    macd_signal = tech.get('macd_signal', '')
    ma_arr = tech.get('ma_arrangement', '')
    boll_pos = tech.get('boll_position', '')
    vol_signal = tech.get('volume_signal', '')
    latest_price = data.get('latest_price', 0)
    support = tech.get('support', 0)
    resistance = tech.get('resistance', 0)
    change_pct = spot.get('涨跌幅', 0) if spot else 0
    
    signal = '持有'
    reason_parts = []
    advice = ''
    risk = ''
    
    # 综合判断
    bull_signals = 0
    bear_signals = 0
    
    if trend == '多头':
        bull_signals += 2
        reason_parts.append('中期多头趋势')
    elif trend == '空头':
        bear_signals += 2
        reason_parts.append('中期空头趋势')
    else:
        reason_parts.append('震荡格局')
    
    if '多头排列' in ma_arr:
        bull_signals += 2
        reason_parts.append('均线多头排列')
    elif '空头排列' in ma_arr:
        bear_signals += 2
        reason_parts.append('均线空头排列')
    
    if macd_signal == '金叉':
        bull_signals += 2
        reason_parts.append('MACD金叉')
    elif macd_signal == '死叉':
        bear_signals += 2
        reason_parts.append('MACD死叉')
    elif macd_signal == '多方':
        bull_signals += 1
        reason_parts.append('MACD多方运行')
    elif macd_signal == '空方':
        bear_signals += 1
        reason_parts.append('MACD空方运行')
    
    if rsi_signal == '超买':
        bear_signals += 1
        reason_parts.append('RSI超买')
    elif rsi_signal == '超卖':
        bull_signals += 1
        reason_parts.append('RSI超卖')
    elif rsi_signal == '偏强':
        bull_signals += 1
        reason_parts.append('RSI偏强')
    elif rsi_signal == '偏弱':
        bear_signals += 1
        reason_parts.append('RSI偏弱')
    
    if boll_pos:
        if '超涨' in boll_pos:
            bear_signals += 1
            reason_parts.append('触及布林上轨')
        elif '超跌' in boll_pos:
            bull_signals += 1
            reason_parts.append('触及布林下轨')
    
    if vol_signal == '放量':
        if change_pct > 0:
            bull_signals += 1
            reason_parts.append('放量上涨')
        elif change_pct < 0:
            bear_signals += 1
            reason_parts.append('放量下跌')
    elif vol_signal == '缩量':
        if change_pct > 0:
            reason_parts.append('缩量上涨（动能不足）')
        elif change_pct < 0:
            reason_parts.append('缩量调整')
    
    # 信号判定
    if bull_signals >= bear_signals + 2:
        signal = '买入'
        advice = f"技术面偏多，如未持有可逢低关注，跌破支撑{support:.2f}止损"
        risk = f"压力位{resistance:.2f}附近注意回落"
    elif bear_signals >= bull_signals + 2:
        signal = '卖出/减仓'
        advice = f"技术面偏空，建议减仓观望，反弹至压力位{resistance:.2f}附近可减"
        risk = f"若跌破支撑{support:.2f}进一步看跌"
    else:
        signal = '持有观察'
        if bull_signals > bear_signals:
            advice = f"多方略占优，持有观察，关注能否突破{resistance:.2f}"
        elif bear_signals > bull_signals:
            advice = f"空方略占优，谨慎持有，支撑位{support:.2f}"
        else:
            advice = f"多空均衡，等待方向选择，区间{support:.2f}-{resistance:.2f}"
        risk = f"区间震荡，注意突破方向"
    
    return {
        "signal": signal,
        "bull_signals": bull_signals,
        "bear_signals": bear_signals,
        "reason": "; ".join(reason_parts) if reason_parts else "无明显信号",
        "advice": advice,
        "risk": risk,
        "support": support,
        "resistance": resistance,
        "trend": trend
    }

# ============ 主流程 ============

def main():
    report = []
    report.append(f"# A股每日复盘报告 - {DATE_STR}")
    report.append(f"\n> 生成时间: {TODAY.strftime('%Y-%m-%d %H:%M')}")
    report.append(f"> ⚠️ 本报告基于技术面分析，仅供参考，不构成投资建议\n")
    report.append("---\n")
    
    # 1. 大盘指数
    print("📊 获取大盘指数...")
    index_data = get_index_data()
    report.append("## 一、大盘指数走势\n")
    report.append("| 指数 | 最新价 | 涨跌幅 | 涨跌额 | MA5 | MA10 | MA20 | MACD信号 |")
    report.append("|------|--------|--------|--------|-----|------|------|---------|")
    
    for name in ["上证指数", "深证成指", "创业板指", "科创50"]:
        d = index_data.get(name, {})
        if d and 'error' not in d:
            price = d.get('最新价', '-')
            change_pct = d.get('涨跌幅', '-')
            change_val = d.get('涨跌额', '-')
            hist = d.get('hist', {})
            ma5 = f"{hist.get('close_5', '-'):.2f}" if hist.get('close_5') else '-'
            ma10 = f"{hist.get('close_10', '-'):.2f}" if hist.get('close_10') else '-'
            ma20 = f"{hist.get('close_20', '-'):.2f}" if hist.get('close_20') else '-'
            
            macd_dif = hist.get('macd_dif')
            macd_dea = hist.get('macd_dea')
            if macd_dif is not None and macd_dea is not None:
                macd_signal = '金叉' if macd_dif > macd_dea else '死叉/空方'
            else:
                macd_signal = '-'
            
            if isinstance(change_pct, (int, float)):
                chg_str = f"{change_pct:+.2f}%"
            else:
                chg_str = str(change_pct)
            if isinstance(change_val, (int, float)):
                val_str = f"{change_val:+.2f}"
            else:
                val_str = str(change_val)
            
            report.append(f"| {name} | {price} | {chg_str} | {val_str} | {ma5} | {ma10} | {ma20} | {macd_signal} |")
        else:
            report.append(f"| {name} | - | - | - | - | - | - | - |")
    
    report.append("")
    
    # 指数分析简评
    report.append("**指数点评：**")
    for name in ["上证指数", "深证成指", "创业板指", "科创50"]:
        d = index_data.get(name, {})
        if d and 'error' not in d:
            change = d.get('涨跌幅', 0)
            if isinstance(change, (int, float)):
                direction = "上涨" if change > 0 else ("下跌" if change < 0 else "平盘")
                report.append(f"- {name}今日{direction}{abs(change):.2f}%")
            hist = d.get('hist', {})
            latest = d.get('最新价', 0)
            ma20_val = hist.get('close_20', None)
            if latest and ma20_val and isinstance(latest, (int, float)) and isinstance(ma20_val, (int, float)):
                if latest > ma20_val:
                    report.append(f"  - 收于MA20（{ma20_val:.2f}）之上，中期偏多")
                else:
                    report.append(f"  - 收于MA20（{ma20_val:.2f}）之下，中期偏弱")
    
    report.append("")
    
    # 2. 板块轮动
    print("📈 获取板块热点...")
    sectors = get_sector_data()
    if sectors:
        report.append("## 二、板块轮动热点\n")
        report.append("| 排名 | 板块名称 | 涨跌幅 | 上涨家数 | 下跌家数 |")
        report.append("|------|----------|--------|----------|----------|")
        for i, s in enumerate(sectors[:10]):
            name = s.get('板块名称', s.get('name', ''))
            pct = s.get('涨跌幅', s.get('pct', '-'))
            up = s.get('上涨家数', s.get('up_count', '-'))
            down = s.get('下跌家数', s.get('down_count', '-'))
            if isinstance(pct, (int, float)):
                pct_str = f"{pct:+.2f}%"
            else:
                pct_str = str(pct)
            report.append(f"| {i+1} | {name} | {pct_str} | {up} | {down} |")
        report.append("")
    
    # 资金流向
    sector_flow = get_sector_flow()
    if sector_flow:
        report.append("**板块资金流向（TOP10）：**\n")
        report.append("| 板块 | 主力净流入 | 今日涨跌幅 |")
        report.append("|------|-----------|----------|")
        for s in sector_flow[:10]:
            name = s.get('名称', s.get('板块名称', ''))
            flow_val = s.get('主力净流入', s.get('流入', '-'))
            pct = s.get('今日涨跌幅', s.get('涨跌幅', '-'))
            report.append(f"| {name} | {flow_val} | {pct} |")
        report.append("")
    
    # 3. 个股分析
    print("📉 获取个股数据（共25只）...")
    report.append("## 三、个股分析\n")
    
    all_stock_analysis = []
    
    for idx, (code, name) in enumerate(STOCKS, 1):
        print(f"  [{idx}/25] {code} {name}...")
        
        data = get_stock_data(code, name)
        
        # 获取新闻
        news = get_news(code, name)
        data['news'] = news
        
        # 趋势分析
        analysis = analyze_stock_trend(data)
        data['analysis'] = analysis
        
        spot = data.get('spot', {}) or {}
        tech = data.get('tech', {}) or {}
        
        # 格式化
        price = spot.get('最新价', data.get('latest_price', '-'))
        change = spot.get('涨跌幅', '-')
        if isinstance(change, (int, float)):
            change_str = f"{change:+.2f}%"
        else:
            change_str = str(change)
        
        trend_str = tech.get('trend', tech.get('ma_arrangement', '数据不足'))
        signal_str = analysis.get('signal', '数据不足')
        support = analysis.get('support', '-')
        resistance = analysis.get('resistance', '-')
        rsi_val = tech.get('rsi', '-')
        if isinstance(rsi_val, float):
            rsi_str = f"{rsi_val:.1f}"
        else:
            rsi_str = str(rsi_val)
        
        vol_signal = tech.get('volume_signal', '-')
        macd_sig = tech.get('macd_signal', '-')
        ma_arr = tech.get('ma_arrangement', '-')
        
        # 新闻摘要
        news_summary = ""
        if news:
            news_items = [n['title'][:40] for n in news[:3]]
            news_summary = " | 新闻: " + "; ".join(news_items)
        
        report.append(f"### {idx}. {name}（{code}）")
        report.append(f"- **最新价**: {price} | **涨跌幅**: {change_str}")
        report.append(f"- **均线**: {ma_arr} | **RSI**: {rsi_str}({tech.get('rsi_signal', '-')})")
        report.append(f"- **MACD**: {macd_sig} | **成交量**: {vol_signal}")
        report.append(f"- **趋势判断**: {trend_str} | **信号**: {signal_str}")
        report.append(f"- **支撑位**: {support} | **压力位**: {resistance}")
        report.append(f"- **操作建议**: {analysis.get('advice', '-')}")
        report.append(f"- **风险提示**: {analysis.get('risk', '-')}")
        if news_summary:
            report.append(f"- {news_summary}")
        report.append("")
        
        all_stock_analysis.append({
            "code": code,
            "name": name,
            "price": price,
            "change": change,
            "trend": trend_str,
            "signal": signal_str,
            "advice": analysis.get('advice', ''),
            "risk": analysis.get('risk', ''),
            "support": support,
            "resistance": resistance,
            "rsi": rsi_str,
        })
    
    # 4. 明日关注
    print("🎯 生成明日关注清单...")
    report.append("---\n")
    report.append("## 四、明日关注清单 & 预案\n")
    
    # 选出信号为"买入"或偏多的股票
    watch_list = [s for s in all_stock_analysis if s.get('signal') in ['买入', '持有观察'] and s.get('trend') == '多头']
    if watch_list:
        report.append("### 🟢 重点买入关注\n")
        report.append("| 代码 | 名称 | 现价 | 趋势 | 信号 | 操作建议 |")
        report.append("|------|------|------|------|------|----------|")
        for s in watch_list[:5]:
            report.append(f"| {s['code']} | {s['name']} | {s['price']} | {s['trend']} | {s['signal']} | {s['advice']} |")
        report.append("")
    
    # 弱势股（卖出信号）
    weak_list = [s for s in all_stock_analysis if s.get('signal') == '卖出/减仓']
    if weak_list:
        report.append("### 🔴 注意风险（建议减仓/回避）\n")
        report.append("| 代码 | 名称 | 现价 | 趋势 | 信号 | 风险提示 |")
        report.append("|------|------|------|------|------|----------|")
        for s in weak_list[:5]:
            report.append(f"| {s['code']} | {s['name']} | {s['price']} | {s['trend']} | {s['signal']} | {s['risk']} |")
        report.append("")
    
    # 震荡待观察
    neutral_list = [s for s in all_stock_analysis if s['signal'] == '持有观察' and s.get('trend') in ['震荡', '']]
    if neutral_list:
        report.append("### 🟡 震荡观望\n")
        report.append("| 代码 | 名称 | 现价 | 支撑 | 压力 | 操作建议 |")
        report.append("|------|------|------|------|------|----------|")
        for s in neutral_list[:5]:
            report.append(f"| {s['code']} | {s['name']} | {s['price']} | {s['support']} | {s['resistance']} | {s['advice']} |")
        report.append("")
    
    # 5. 综合建议
    report.append("### 📋 明日策略预案\n")
    
    # 大盘环境判断
    sh_data = index_data.get("上证指数", {})
    if sh_data and 'hist' in sh_data:
        sh_ma20 = sh_data['hist'].get('close_20', 0)
        sh_latest = sh_data.get('最新价', 0)
        if sh_latest and sh_ma20:
            if sh_latest > sh_ma20:
                report.append(f"- **大盘中期偏多**（上证指数收于MA20上方），仓位可保持5-7成")
            else:
                report.append(f"- **大盘中期偏弱**（上证指数收于MA20下方），建议控制仓位3-5成")
    
    report.append("- 关注量能变化：放量突破压力位可加仓，缩量反弹注意逢高减仓")
    report.append("- 板块轮动较快，避免追高已大幅上涨板块")
    report.append("- 个股严格止损，跌破支撑位及时离场")
    report.append("")
    
    report.append("---")
    report.append(f"\n*报告自动生成于 {TODAY.strftime('%Y-%m-%d %H:%M')} | 数据来源: akShare 新浪/东方财富接口*")
    
    # 保存报告
    output_path = f"/Users/duguke/.openclaw/workspace/analysis/daily/{DATE_STR}.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report))
    
    print(f"\n✅ 报告已保存至: {output_path}")
    
    # 同时保存JSON用于webchat推送
    summary = {
        "date": DATE_STR,
        "index": {k: {"latest": v.get("最新价"), "change": v.get("涨跌幅")} 
                  for k, v in index_data.items() if 'error' not in v},
        "stocks": all_stock_analysis,
        "watch_buy": [s for s in all_stock_analysis if s.get('signal') == '买入'],
        "watch_sell": [s for s in all_stock_analysis if s.get('signal') == '卖出/减仓'],
        "trend_summary": {
            "bullish": sum(1 for s in all_stock_analysis if s.get('trend') == '多头'),
            "bearish": sum(1 for s in all_stock_analysis if s.get('trend') == '空头'),
            "neutral": sum(1 for s in all_stock_analysis if s.get('trend') == '震荡'),
        }
    }
    
    json_path = f"/Users/duguke/.openclaw/workspace/analysis/daily/{DATE_STR}_summary.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    print(f"✅ JSON摘要已保存至: {json_path}")
    return output_path, json_path

if __name__ == "__main__":
    main()
