#!/usr/bin/env python3
"""
A股第4批股票分析 - 全方位数据采集
股票: 002413 雷科防务, 688981 中芯国际, 601865 福莱特, 000157 中联重科, 300719 安达维尔
"""

import akshare as ak
import pandas as pd
import json
import warnings
import traceback
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

# 当前日期 - 2026年7月26日
CURRENT_DATE = "20260726"
CURRENT_DATE_OBJ = datetime.strptime(CURRENT_DATE, "%Y%m%d")
LOOKBACK_90D = (CURRENT_DATE_OBJ - timedelta(days=90)).strftime("%Y%m%d")
LOOKBACK_365D = (CURRENT_DATE_OBJ - timedelta(days=365)).strftime("%Y%m%d")

stocks = [
    {"code": "002413", "name": "雷科防务", "market": "sz"},
    {"code": "688981", "name": "中芯国际", "market": "sh"},
    {"code": "601865", "name": "福莱特", "market": "sh"},
    {"code": "000157", "name": "中联重科", "market": "sz"},
    {"code": "300719", "name": "安达维尔", "market": "sz"},
]

results = []

def safe_get(func, *args, **kwargs):
    """安全执行akshare函数，失败返回None/空"""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        return f"ERR: {str(e)[:200]}"

def safe_get_df(func, *args, **kwargs):
    """安全获取DataFrame"""
    try:
        df = func(*args, **kwargs)
        if df is None or df.empty:
            return pd.DataFrame()
        return df
    except Exception as e:
        return pd.DataFrame()

for stk in stocks:
    code = stk["code"]
    name = stk["name"]
    market = stk["market"]
    symbol = f"{market}.{code}"
    
    print(f"\n{'='*60}")
    print(f"正在采集: {code} {name}")
    print(f"{'='*60}")
    
    result = {
        "code": code,
        "name": name,
        "财务数据": {},
        "机构调研": {},
        "资金流向": {},
        "技术面": {}
    }
    
    # ==================== 1. 中报预告/业绩快报 ====================
    print(f"\n【1】采集业绩预告/快报...")
    
    # 方法1: 同花顺财务摘要
    try:
        df_yj = ak.stock_yjyg_em(date="20260331")
        if not df_yj.empty:
            stk_yj = df_yj[df_yj['股票代码'] == code]
            if not stk_yj.empty:
                yj_records = []
                for _, row in stk_yj.head(5).iterrows():
                    rec = {}
                    for col in stk_yj.columns:
                        val = row[col]
                        if pd.notna(val):
                            if isinstance(val, (pd.Timestamp, datetime)):
                                rec[col] = str(val.date())
                            elif isinstance(val, float):
                                rec[col] = round(val, 4)
                            else:
                                rec[col] = val
                    yj_records.append(rec)
                result["财务数据"]["业绩预告_2026Q1"] = yj_records
                print(f"  业绩预告: 找到 {len(yj_records)} 条记录")
            else:
                result["财务数据"]["业绩预告_2026Q1"] = "无数据"
                print(f"  业绩预告: 无该股票数据")
        else:
            result["财务数据"]["业绩预告_2026Q1"] = "无数据"
    except Exception as e:
        print(f"  业绩预告失败: {str(e)[:100]}")
        result["财务数据"]["业绩预告_2026Q1"] = f"采集失败: {str(e)[:100]}"
    
    # 方法2: 财务摘要(同花顺)
    try:
        df_abs = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
        if df_abs is not None and not df_abs.empty:
            abs_records = []
            for _, row in df_abs.head(5).iterrows():
                rec = {}
                for col in df_abs.columns:
                    val = row[col]
                    if pd.notna(val):
                        if isinstance(val, (pd.Timestamp, datetime)):
                            rec[col] = str(val.date())
                        elif isinstance(val, float):
                            rec[col] = round(val, 4)
                        else:
                            rec[col] = val
                abs_records.append(rec)
            result["财务数据"]["财务摘要_按报告期"] = abs_records
            print(f"  财务摘要: 找到 {len(abs_records)} 条记录")
        else:
            result["财务数据"]["财务摘要_按报告期"] = "无数据"
    except Exception as e:
        print(f"  财务摘要失败: {str(e)[:100]}")
        result["财务数据"]["财务摘要_按报告期"] = f"采集失败: {str(e)[:100]}"
    
    # 方法3: 最新业绩快报/中报
    try:
        # 测试获取最新业绩预告
        df_yjyg = ak.stock_yjyg_em(date="20250630")
        if not df_yjyg.empty:
            stk_yjyg = df_yjyg[df_yjyg['股票代码'] == code]
            if not stk_yjyg.empty:
                yjyg_records = []
                for _, row in stk_yjyg.head(5).iterrows():
                    rec = {}
                    for col in stk_yjyg.columns:
                        val = row[col]
                        if pd.notna(val):
                            if isinstance(val, (pd.Timestamp, datetime)):
                                rec[col] = str(val.date())
                            elif isinstance(val, float):
                                rec[col] = round(val, 4)
                            else:
                                rec[col] = val
                    yjyg_records.append(rec)
                result["财务数据"]["业绩预告_2025年中报"] = yjyg_records
                print(f"  中报预告: 找到 {len(yjyg_records)} 条记录")
            else:
                result["财务数据"]["业绩预告_2025年中报"] = "无数据"
        else:
            result["财务数据"]["业绩预告_2025年中报"] = "无数据"
    except Exception as e:
        print(f"  中报预告失败: {str(e)[:100]}")
        result["财务数据"]["业绩预告_2025年中报"] = f"采集失败: {str(e)[:100]}"
    
    # ==================== 2. 机构调研 ====================
    print(f"\n【2】采集机构调研记录...")
    try:
        df_research = ak.stock_org_research_em()
        if df_research is not None and not df_research.empty:
            stk_research = df_research[df_research['股票代码'] == code]
            if not stk_research.empty:
                research_records = []
                for _, row in stk_research.head(10).iterrows():
                    rec = {}
                    for col in stk_research.columns:
                        val = row[col]
                        if pd.notna(val):
                            if isinstance(val, (pd.Timestamp, datetime)):
                                rec[col] = str(val.date())
                            elif isinstance(val, float):
                                rec[col] = round(val, 4)
                            else:
                                rec[col] = val
                    research_records.append(rec)
                result["机构调研"]["调研记录"] = research_records
                print(f"  机构调研: 找到 {len(research_records)} 条记录")
            else:
                result["机构调研"]["调研记录"] = "无数据"
        else:
            result["机构调研"]["调研记录"] = "无数据"
    except Exception as e:
        print(f"  机构调研失败: {str(e)[:100]}")
        result["机构调研"]["调研记录"] = f"采集失败: {str(e)[:100]}"
    
    # ==================== 3. 主力资金流向 ====================
    print(f"\n【3】采集资金流向...")
    
    # 大单净流入统计
    # 使用 stock_individual_fund_flow
    fund_flow_data = {}
    
    for flow_type in ["主力净流入", "小单净流入", "中单净流入", "大单净流入", "超大单净流入"]:
        try:
            # 先查个股所属市场
            if code.startswith("6"):
                mkt = "sh"
            elif code.startswith("0") or code.startswith("3"):
                mkt = "sz"
            elif code.startswith("4"):
                mkt = "bj"
            else:
                mkt = "sh"
            
            df_flow = ak.stock_individual_fund_flow(stock=code, market=mkt)
            if df_flow is not None and not df_flow.empty:
                # 取最近20行（约20个交易日）
                recent_flow = df_flow.head(20)
                flow_summary = {
                    "近5日净流入": None,
                    "近10日净流入": None,
                    "近20日净流入": None,
                    "近5日数据": [],
                    "近10日数据": [],
                    "近20日数据": []
                }
                
                for i, (idx, row) in enumerate(recent_flow.iterrows()):
                    rec = {}
                    for col in recent_flow.columns:
                        val = row[col]
                        if pd.notna(val):
                            if isinstance(val, (pd.Timestamp, datetime)):
                                rec[col] = str(val.date())
                            elif isinstance(val, float):
                                rec[col] = round(val, 2)
                            else:
                                rec[col] = val
                    
                    if i < 5:
                        flow_summary["近5日数据"].append(rec)
                    if i < 10:
                        flow_summary["近10日数据"].append(rec)
                    flow_summary["近20日数据"].append(rec)
                
                # 尝试计算净额
                flow_cols = [c for c in recent_flow.columns if '净额' in c or '净流入' in c]
                if flow_cols:
                    n_col = flow_cols[0]
                    vals_5 = recent_flow[n_col].head(5).sum()
                    vals_10 = recent_flow[n_col].head(10).sum()
                    vals_20 = recent_flow[n_col].head(20).sum()
                    flow_summary["近5日净流入"] = round(float(vals_5), 2) if pd.notna(vals_5) else None
                    flow_summary["近10日净流入"] = round(float(vals_10), 2) if pd.notna(vals_10) else None
                    flow_summary["近20日净流入"] = round(float(vals_20), 2) if pd.notna(vals_20) else None
                
                fund_flow_data[flow_type] = flow_summary
                print(f"  资金流向({flow_type}): 获取到 {len(recent_flow)} 天数据")
            else:
                fund_flow_data[flow_type] = "无数据"
        except Exception as e:
            print(f"  资金流向({flow_type})失败: {str(e)[:80]}")
            fund_flow_data[flow_type] = f"采集失败: {str(e)[:80]}"
    
    result["资金流向"] = fund_flow_data
    
    # ==================== 4. 技术面关键数据 ====================
    print(f"\n【4】采集技术面数据...")
    
    try:
        df_hist = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                      start_date=LOOKBACK_365D, 
                                      end_date=CURRENT_DATE, 
                                      adjust="qfq")
        if df_hist is not None and not df_hist.empty:
            df_hist = df_hist.sort_values('日期', ascending=True).reset_index(drop=True)
            df_hist_latest = df_hist.tail(120)  # 近120个交易日足够计算60日均线
            
            print(f"  历史K线: 获取到 {len(df_hist)} 条日K数据")
            
            tech = {}
            
            # 最新收盘价
            latest = df_hist.iloc[-1]
            tech["最新日期"] = str(latest['日期'].date()) if hasattr(latest['日期'], 'date') else str(latest['日期'])
            tech["开盘价"] = float(latest['开盘'])
            tech["收盘价"] = float(latest['收盘'])
            tech["最高价"] = float(latest['最高'])
            tech["最低价"] = float(latest['最低'])
            tech["成交量"] = int(latest['成交量'])
            tech["成交额"] = float(latest['成交额'])
            
            close_vals = df_hist['收盘'].values
            
            # MA5/10/20/60
            def ma(data, period):
                if len(data) >= period:
                    return round(float(data[-period:].mean()), 3)
                return None
            
            tech["MA5"] = ma(df_hist['收盘'], 5)
            tech["MA10"] = ma(df_hist['收盘'], 10)
            tech["MA20"] = ma(df_hist['收盘'], 20)
            tech["MA60"] = ma(df_hist['收盘'], 60)
            print(f"  MA: 5={tech['MA5']}, 10={tech['MA10']}, 20={tech['MA20']}, 60={tech['MA60']}")
            
            # MACD
            ema12 = df_hist['收盘'].ewm(span=12, adjust=False).mean().values
            ema26 = df_hist['收盘'].ewm(span=26, adjust=False).mean().values
            dif = ema12 - ema26
            dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
            macd_bar = 2 * (dif - dea)
            
            if len(macd_bar) >= 3:
                tech["MACD_DIF"] = round(float(dif[-1]), 4)
                tech["MACD_DEA"] = round(float(dea[-1]), 4)
                tech["MACD_柱状值"] = round(float(macd_bar[-1]), 4)
                tech["MACD_柱状前值"] = round(float(macd_bar[-2]), 4)
                tech["MACD_柱状前前值"] = round(float(macd_bar[-3]), 4)
                print(f"  MACD: DIF={tech['MACD_DIF']}, DEA={tech['MACD_DEA']}, BAR={tech['MACD_柱状值']}")
            
            # RSI6/12
            def calc_rsi(close, period):
                close_s = pd.Series(close)
                delta = close_s.diff()
                gain = delta.clip(lower=0)
                loss = -delta.clip(upper=0)
                avg_gain = gain.rolling(window=period, min_periods=period).mean()
                avg_loss = loss.rolling(window=period, min_periods=period).mean()
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
                return rsi.values
            
            rsi6_vals = calc_rsi(close_vals, 6)
            rsi12_vals = calc_rsi(close_vals, 12)
            
            if len(rsi6_vals) > 0 and pd.notna(rsi6_vals[-1]):
                tech["RSI6"] = round(float(rsi6_vals[-1]), 2)
            if len(rsi12_vals) > 0 and pd.notna(rsi12_vals[-1]):
                tech["RSI12"] = round(float(rsi12_vals[-1]), 2)
            print(f"  RSI: 6={tech.get('RSI6','N/A')}, 12={tech.get('RSI12','N/A')}")
            
            # 成交量较5日均量比
            vol_series = df_hist['成交量'].values
            if len(vol_series) >= 5:
                vol_5ma = float(pd.Series(vol_series[-6:-1]).mean())  # 前5日均量（不含今日）
                latest_vol = float(vol_series[-1])
                vol_ratio = round(latest_vol / vol_5ma, 2) if vol_5ma > 0 else None
                tech["最新成交量"] = latest_vol
                tech["前5日均量"] = round(vol_5ma, 2)
                tech["量比(较5日均量)"] = vol_ratio
                print(f"  量比: {vol_ratio}")
            
            # 近期支撑/压力位 (近60日高低点)
            recent_60 = df_hist.tail(60)
            tech["近60日最高价"] = float(recent_60['最高'].max())
            tech["近60日最低价"] = float(recent_60['最低'].min())
            tech["近60日最高价日期"] = str(recent_60.loc[recent_60['最高'].idxmax(), '日期'].date()) if hasattr(recent_60.loc[recent_60['最高'].idxmax(), '日期'], 'date') else str(recent_60.loc[recent_60['最高'].idxmax(), '日期'])
            tech["近60日最低价日期"] = str(recent_60.loc[recent_60['最低'].idxmin(), '日期'].date()) if hasattr(recent_60.loc[recent_60['最低'].idxmin(), '日期'], 'date') else str(recent_60.loc[recent_60['最低'].idxmin(), '日期'])
            
            # 近期支撑位: 近20日最低价附近
            recent_20 = df_hist.tail(20)
            tech["近20日最低价(支撑位参考)"] = float(recent_20['最低'].min())
            tech["近20日最高价(压力位参考)"] = float(recent_20['最高'].max())
            
            # 用MA60和MA20辅助判断支撑/压力
            tech["MA60支撑位参考"] = tech.get("MA60")
            tech["MA20压力位参考"] = tech.get("MA20")
            
            print(f"  近60日: 最高={tech['近60日最高价']}, 最低={tech['近60日最低价']}")
            
            result["技术面"] = tech
        else:
            result["技术面"] = "无数据"
            print(f"  历史K线: 未获取到数据")
    except Exception as e:
        traceback.print_exc()
        print(f"  技术面采集失败: {str(e)[:200]}")
        result["技术面"] = f"采集失败: {str(e)[:200]}"
    
    results.append(result)
    
    # 保存中间结果
    with open(f"/Users/duguke/.openclaw/workspace/tmp/batch4_{code}_{name}.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    
    print(f"\n✅ {name} 数据采集完成")
    print(f"   已保存: tmp/batch4_{code}_{name}.json")

# ==================== 最终汇总 ====================
print("\n\n" + "="*60)
print("所有股票数据采集完成，生成汇总JSON")
print("="*60)

output = {
    "报告日期": CURRENT_DATE,
    "报告时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "第4批股票分析": results
}

with open("/Users/duguke/.openclaw/workspace/tmp/batch4_summary.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

# 输出简化版到控制台
for r in results:
    code = r["code"]
    name = r["name"]
    tech = r.get("技术面", {})
    if isinstance(tech, dict):
        close = tech.get("收盘价", "N/A")
        ma5 = tech.get("MA5", "N/A")
        ma20 = tech.get("MA20", "N/A")
        ma60 = tech.get("MA60", "N/A")
        macd_bar = tech.get("MACD_柱状值", "N/A")
        rsi6 = tech.get("RSI6", "N/A")
        vol_ratio = tech.get("量比(较5日均量)", "N/A")
        support = tech.get("近20日最低价(支撑位参考)", "N/A")
        resist = tech.get("近20日最高价(压力位参考)", "N/A")
        print(f"\n{code} {name}: 收盘{close} MA5={ma5} MA20={ma20} MA60={ma60}")
        print(f"  MACD柱={macd_bar} RSI6={rsi6} 量比={vol_ratio}")
        print(f"  支撑~{support} 压力~{resist}")
    else:
        print(f"\n{code} {name}: 技术面无数据")

print("\n✅ 汇总文件: tmp/batch4_summary.json")
