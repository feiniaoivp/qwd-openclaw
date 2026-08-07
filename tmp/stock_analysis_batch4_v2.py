#!/usr/bin/env python3
"""
第2轮: 使用 baostock + 其他稳定接口获取数据
Stock: 002413 雷科防务, 688981 中芯国际, 601865 福莱特, 000157 中联重科, 300719 安达维尔
"""

import akshare as ak
import baostock as bs
import pandas as pd
import numpy as np
import json
import warnings
import time
import urllib3
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CURRENT_DATE = "2026-07-26"
CURRENT_DATE_DT = datetime.strptime(CURRENT_DATE, "%Y-%m-%d")

stocks = [
    {"code": "002413", "name": "雷科防务", "bs_code": "sz.002413"},
    {"code": "688981", "name": "中芯国际", "bs_code": "sh.688981"},
    {"code": "601865", "name": "福莱特", "bs_code": "sh.601865"},
    {"code": "000157", "name": "中联重科", "bs_code": "sz.000157"},
    {"code": "300719", "name": "安达维尔", "bs_code": "sz.300719"},
]

# ========== 1. 尝试获取可用的akshare接口 ==========
print("=== 检查可用akshare接口 ===")
ak_functions = dir(ak)
search_terms = ['research', 'yjyg', 'fund_flow', 'lhb', 'zlzj', 'individual_fund']
for term in search_terms:
    matches = [f for f in ak_functions if term in f.lower()]
    if matches:
        print(f"  {term}: {matches}")

# ========== 2. 登录 baostock ==========
print("\n=== 登录 baostock ===")
lg = bs.login()
print(f"  baostock login: {lg.error_code} - {lg.error_msg}")

results = []

for stk in stocks:
    code = stk["code"]
    name = stk["name"]
    bs_code = stk["bs_code"]
    
    print(f"\n{'='*60}")
    print(f"采集: {code} {name}")
    print(f"{'='*60}")
    
    result = {
        "code": code,
        "name": name,
        "财务数据": {},
        "机构调研": {},
        "资金流向": {},
        "技术面": {}
    }
    
    # ==================== 财务数据 ====================
    print(f"\n[财务数据]")
    
    # 用 akshare stock_yjyg_em 试试缩小区间
    for date_param in ["20250630", "20260331", "20251231"]:
        try:
            time.sleep(0.5)
            df_yj = ak.stock_yjyg_em(date=date_param)
            if df_yj is not None and not df_yj.empty:
                stk_yj = df_yj[df_yj['股票代码'] == code]
                if not stk_yj.empty:
                    records = []
                    for _, row in stk_yj.head(6).iterrows():
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
                        records.append(rec)
                    result["财务数据"][f"业绩预告_{date_param}"] = records
                    print(f"  stock_yjyg_em({date_param}): {len(records)}条")
                    break
        except Exception as e:
            err_msg = str(e)[:80]
            if "RemoteDisconnected" in err_msg or "ConnectionError" in err_msg:
                print(f"  stock_yjyg_em({date_param}): 连接断开，跳过")
            else:
                print(f"  stock_yjyg_em({date_param}): {err_msg}")
    
    # 用 baostock 获取历史财务指标
    try:
        # 获取最近可用的季度
        for q in ["2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31", "2024-12-31"]:
            rs = bs.query_balance_data(bs_code, year=q[:4], quarter=str(int(q[5:7])//3))
            bal_data = []
            while rs.next():
                row = rs.get_row_data()
                bal_data.append(row)
            if bal_data:
                print(f"  baostock 资产负债表({q}): {len(bal_data)}行")
            time.sleep(0.3)
        
        # 利润表
        for q in ["2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31", "2024-12-31"]:
            rs = rs = bs.query_profit_data(bs_code, year=q[:4], quarter=str(int(q[5:7])//3))
            profit_data = []
            while rs.next():
                row = rs.get_row_data()
                profit_data.append(row)
            if profit_data:
                print(f"  baostock 利润表({q}): {len(profit_data)}行")
                fin_rec = {}
                for row in profit_data:
                    cols = rs.fields
                    for i, col in enumerate(cols):
                        val = row[i]
                        if val and val not in ['', 'None']:
                            try:
                                fin_rec[col] = round(float(val), 4) if '.' in val else val
                            except:
                                fin_rec[col] = val
                result["财务数据"]["baostock_利润表"] = fin_rec
            time.sleep(0.3)
            
    except Exception as e:
        print(f"  baostock财务: {str(e)[:100]}")
    
    # ==================== 机构调研 ====================
    print(f"\n[机构调研]")
    
    # 尝试 stock_zlzj_em (主力资金 - 可能含机构信息)
    try:
        df_zlzj = ak.stock_zlzj_em()
        if df_zlzj is not None and not df_zlzj.empty:
            print(f"  stock_zlzj_em: {len(df_zlzj)}行, 列: {list(df_zlzj.columns)}")
            # 看是否为机构数据
            col_string = str(list(df_zlzj.columns))
            if '机构' in col_string:
                stk_zl = df_zlzj[df_zlzj['股票代码'] == code]
                if not stk_zl.empty:
                    recs = []
                    for _, row in stk_zl.head(5).iterrows():
                        rec = {}
                        for col in stk_zl.columns:
                            val = row[col]
                            if pd.notna(val):
                                if isinstance(val, (pd.Timestamp, datetime)):
                                    rec[col] = str(val.date())
                                else:
                                    rec[col] = val
                        recs.append(rec)
                    result["机构调研"]["主力资金机构"] = recs
                    print(f"    找到{len(recs)}条")
    except Exception as e:
        err = str(e)[:80]
        if 'attribute' in err:
            print(f"  stock_zlzj_em: 不存在")
        elif 'RemoteDisconnected' in err:
            print(f"  stock_zlzj_em: 连接断开")
        else:
            print(f"  stock_zlzj_em: {err}")
    
    # 尝试 stock_lhb_detail_em (龙虎榜)
    for lhb_date in ["2026-07-24", "2026-07-23", "2026-07-22", "2026-07-18"]:
        try:
            time.sleep(0.5)
            df_lhb = ak.stock_lhb_detail_em(date=lhb_date.replace("-", ""))
            if df_lhb is not None and not df_lhb.empty:
                stk_lhb = df_lhb[df_lhb['股票代码'] == code]
                if not stk_lhb.empty:
                    recs = []
                    for _, row in stk_lhb.head(5).iterrows():
                        rec = {}
                        for col in stk_lhb.columns:
                            val = row[col]
                            if pd.notna(val):
                                rec[col] = val
                        recs.append(rec)
                    result.setdefault("机构调研", {})
                    result["机构调研"]["龙虎榜"] = recs
                    print(f"  龙虎榜({lhb_date}): {len(recs)}条")
        except Exception as e:
            err = str(e)[:60]
            if 'RemoteDisconnected' in err:
                pass  # silent on connection errors
            else:
                print(f"  龙虎榜({lhb_date}): {err}")
    
    # ==================== 资金流向 ====================
    print(f"\n[资金流向]")
    
    # 确定市场代码
    if code.startswith("6"):
        mkt = "sh"
    elif code.startswith("0") or code.startswith("3"):
        mkt = "sz"
    else:
        mkt = "sh"
    
    # 用 stock_individual_fund_flow
    fund_flow = {}
    for flow_label in ["主力净流入", "超大单净流入", "大单净流入", "中单净流入", "小单净流入"]:
        try:
            time.sleep(0.5)
            df_fund = ak.stock_individual_fund_flow(stock=code, market=mkt)
            if df_fund is not None and not df_fund.empty:
                fund_data = {
                    "label": flow_label,
                    "近5日净额": None,
                    "近10日净额": None,
                    "近20日净额": None,
                    "明细": []
                }
                recent = df_fund.head(20)
                # 找净额列
                n_cols = [c for c in recent.columns if '净额' in c]
                if n_cols:
                    col = n_cols[0]
                    vals = recent[col].values
                    try:
                        vals_f = pd.to_numeric(vals, errors='coerce')
                        fund_data["近5日净额"] = round(float(np.nansum(vals_f[:5])), 2) if len(vals_f) >= 5 else None
                        fund_data["近10日净额"] = round(float(np.nansum(vals_f[:10])), 2) if len(vals_f) >= 10 else None
                        fund_data["近20日净额"] = round(float(np.nansum(vals_f[:20])), 2) if len(vals_f) >= 20 else None
                    except:
                        pass
                
                for _, row in recent.head(5).iterrows():
                    rec = {}
                    for c in recent.columns:
                        v = row[c]
                        if pd.notna(v):
                            if isinstance(v, (pd.Timestamp, datetime)):
                                rec[c] = str(v.date())
                            elif isinstance(v, float):
                                rec[c] = round(v, 2)
                            else:
                                rec[c] = v
                    fund_data["明细"].append(rec)
                
                fund_flow[flow_label] = fund_data
                print(f"  stock_individual_fund_flow({flow_label}): 5日={fund_data['近5日净额']}, 10日={fund_data['近10日净额']}, 20日={fund_data['近20日净额']}")
                break  # 一次即可，数据包含所有分类
        except Exception as e:
            err = str(e)[:60]
            if 'RemoteDisconnected' in err:
                pass
            else:
                print(f"  fund_flow({flow_label}): {err}")
    
    if fund_flow:
        result["资金流向"] = fund_flow
    else:
        result["资金流向"] = "数据获取失败(网络连接断开)"
    
    # ==================== 技术面数据 ====================
    print(f"\n[技术面]")
    
    tech_data = None
    
    # 方案A: akshare stock_zh_a_hist (带重试)
    for attempt in range(3):
        try:
            time.sleep(1.5)
            df_hist = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date="20250701",
                end_date=CURRENT_DATE.replace("-", ""),
                adjust="qfq"
            )
            if df_hist is not None and not df_hist.empty:
                # 如果数据太少,再拉长日期
                if len(df_hist) < 30:
                    time.sleep(1)
                    df_hist2 = ak.stock_zh_a_hist(
                        symbol=code, period="daily",
                        start_date="20260101",
                        end_date=CURRENT_DATE.replace("-", ""),
                        adjust="qfq"
                    )
                    if df_hist2 is not None and not df_hist2.empty and len(df_hist2) > len(df_hist):
                        df_hist = df_hist2
                
                tech_data = calc_technical_indicators(df_hist)
                print(f"  akshare stock_zh_a_hist: {len(df_hist)}天数据")
                break
            else:
                print(f"  akshare stock_zh_a_hist: 空数据 (尝试{attempt+1})")
        except Exception as e:
            err = str(e)[:60]
            if 'RemoteDisconnected' in err:
                print(f"  akshare stock_zh_a_hist: 连接断开 (尝试{attempt+1})")
                time.sleep(3)
            else:
                print(f"  akshare stock_zh_a_hist: {err} (尝试{attempt+1})")
                break
    
    # 方案B: baostock (如果akshare失败)
    if tech_data is None:
        print("  -> 降级到 baostock...")
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,code,open,high,low,close,preclose,volume,amount,turn",
                start_date='2025-07-01',
                end_date=CURRENT_DATE,
                frequency="d",
                adjustflag="2"  # 前复权
            )
            data_list = []
            while rs.next():
                data_list.append(rs.get_row_data())
            
            if data_list and len(data_list) > 10:
                cols = rs.fields
                df_bs = pd.DataFrame(data_list, columns=cols)
                for c in ['open', 'high', 'low', 'close', 'preclose', 'volume', 'amount', 'turn']:
                    df_bs[c] = pd.to_numeric(df_bs[c], errors='coerce')
                df_bs['日期'] = pd.to_datetime(df_bs['date'])
                df_bs = df_bs.sort_values('日期').reset_index(drop=True)
                
                # 如果数据不够,拉更长时间
                if len(df_bs) < 60:
                    rs2 = bs.query_history_k_data_plus(
                        bs_code,
                        "date,code,open,high,low,close,preclose,volume,amount,turn",
                        start_date='2025-01-01',
                        end_date=CURRENT_DATE,
                        frequency="d", adjustflag="2"
                    )
                    data_list2 = []
                    while rs2.next():
                        data_list2.append(rs2.get_row_data())
                    if data_list2 and len(data_list2) > len(data_list):
                        df_bs = pd.DataFrame(data_list2, columns=cols)
                        for c in ['open', 'high', 'low', 'close', 'preclose', 'volume', 'amount', 'turn']:
                            df_bs[c] = pd.to_numeric(df_bs[c], errors='coerce')
                        df_bs['日期'] = pd.to_datetime(df_bs['date'])
                        df_bs = df_bs.sort_values('日期').reset_index(drop=True)
                
                tech_data = calc_technical_indicators_bs(df_bs)
                print(f"  baostock: {len(df_bs)}天数据")
            else:
                print(f"  baostock: 数据不足({len(data_list) if data_list else 0})")
        except Exception as e:
            print(f"  baostock: {str(e)[:100]}")
    
    if tech_data:
        result["技术面"] = tech_data
    else:
        result["技术面"] = "全部数据源失败(网络问题)"
    
    results.append(result)
    print(f"\n✅ {name} 完成")

bs.logout()

# ==================== 输出汇总 ====================
output = {
    "报告日期": CURRENT_DATE,
    "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "第4批股票分析": results
}

with open("/Users/duguke/.openclaw/workspace/tmp/batch4_v2_summary.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

print("\n\n✅ 最终汇总: tmp/batch4_v2_summary.json")
print(f"文件大小: {len(json.dumps(output, ensure_ascii=False, default=str))} bytes")


def calc_technical_indicators(df):
    """基于 akshare 日K数据算技术指标"""
    df = df.sort_values('日期').reset_index(drop=True)
    df_latest = df.tail(120)
    
    close = df_latest['收盘'].values
    high = df_latest['最高'].values
    low = df_latest['最低'].values
    vol = df_latest['成交量'].values
    
    tech = {}
    latest = df_latest.iloc[-1]
    
    tech["最新日期"] = str(latest['日期'].date()) if hasattr(latest['日期'], 'date') else str(latest['日期'])
    tech["开盘价"] = float(latest['开盘'])
    tech["收盘价"] = float(latest['收盘'])
    tech["最高价"] = float(latest['最高'])
    tech["最低价"] = float(latest['最低'])
    tech["成交量"] = int(latest['成交量'])
    tech["成交额"] = float(latest['成交额'])
    
    # MA
    for p in [5, 10, 20, 60]:
        if len(close) >= p:
            tech[f"MA{p}"] = round(float(pd.Series(close).tail(p).mean()), 3)
    
    # MACD
    s = pd.Series(close)
    ema12 = s.ewm(span=12, adjust=False).values
    ema26 = s.ewm(span=26, adjust=False).values
    dif = ema12 - ema26
    dea = pd.Series(dif).ewm(span=9, adjust=False).values
    macd_bar = 2 * (dif - dea)
    
    tech["MACD_DIF"] = round(float(dif[-1]), 4)
    tech["MACD_DEA"] = round(float(dea[-1]), 4)
    tech["MACD_柱状值"] = round(float(macd_bar[-1]), 4)
    tech["MACD_柱状前值"] = round(float(macd_bar[-2]), 4)
    tech["MACD_柱状变化方向"] = "缩短" if abs(macd_bar[-1]) < abs(macd_bar[-2]) else "增长"
    if macd_bar[-1] >= 0:
        tech["MACD_红绿柱"] = "红柱" if macd_bar[-1] >= macd_bar[-2] else "红柱缩短"
    else:
        tech["MACD_红绿柱"] = "绿柱" if macd_bar[-1] <= macd_bar[-2] else "绿柱缩短"
    
    # RSI
    def rsi(s, n):
        delta = s.diff()
        gain = delta.clip(lower=0).rolling(n).mean()
        loss = -delta.clip(upper=0).rolling(n).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    s_close = pd.Series(close)
    rsi6 = rsi(s_close, 6)
    rsi12 = rsi(s_close, 12)
    tech["RSI6"] = round(float(rsi6.iloc[-1]), 2) if pd.notna(rsi6.iloc[-1]) else None
    tech["RSI12"] = round(float(rsi12.iloc[-1]), 2) if pd.notna(rsi12.iloc[-1]) else None
    if tech.get("RSI6") and tech["RSI6"] is not None:
        tech["RSI6_状态"] = "超买" if tech["RSI6"] > 70 else ("超卖" if tech["RSI6"] < 30 else "中性")
    
    # 量比
    if len(vol) >= 6:
        vol_5ma = float(pd.Series(vol[-6:-1]).mean())
        tech["量比(较5日均量)"] = round(float(vol[-1]) / vol_5ma, 2) if vol_5ma > 0 else None
    
    # 支撑/压力
    r60 = df_latest.tail(60)
    r20 = df_latest.tail(20)
    tech["近60日最高价"] = float(r60['最高'].max())
    tech["近60日最低价"] = float(r60['最低'].min())
    tech["近20日支撑位(最低)"] = float(r20['最低'].min())
    tech["近20日压力位(最高)"] = float(r20['最高'].max())
    tech["MA60支撑参考"] = tech.get("MA60")
    tech["MA20压力参考"] = tech.get("MA20")
    
    # 与均线关系
    close_p = tech["收盘价"]
    ma20_val = tech.get("MA20")
    ma60_val = tech.get("MA60")
    if ma20_val and close_p > ma20_val:
        tech["均线位置"] = "站上MA20"
    elif ma20_val:
        tech["均线位置"] = "跌破MA20"
    if ma60_val and close_p > ma60_val:
        tech["均线位置"] = tech.get("均线位置", "") + ", 站上MA60"
    elif ma60_val:
        tech["均线位置"] = tech.get("均线位置", "") + ", 跌破MA60"
    
    return tech


def calc_technical_indicators_bs(df):
    """基于 baostock 日K数据算技术指标"""
    df = df.sort_values('日期').reset_index(drop=True)
    df_latest = df.tail(120)
    
    close = df_latest['close'].values
    high = df_latest['high'].values
    low = df_latest['low'].values
    vol = df_latest['volume'].values
    
    tech = {}
    latest = df_latest.iloc[-1]
    
    tech["最新日期"] = str(latest['日期'].date())
    tech["开盘价"] = float(latest['open'])
    tech["收盘价"] = float(latest['close'])
    tech["最高价"] = float(latest['high'])
    tech["最低价"] = float(latest['low'])
    tech["成交量"] = int(latest['volume'])
    tech["成交额"] = float(latest['amount'])
    
    # MA
    for p in [5, 10, 20, 60]:
        if len(close) >= p:
            tech[f"MA{p}"] = round(float(pd.Series(close).tail(p).mean()), 3)
    
    # MACD
    s = pd.Series(close)
    ema12 = s.ewm(span=12, adjust=False).values
    ema26 = s.ewm(span=26, adjust=False).values
    dif = ema12 - ema26
    dea = pd.Series(dif).ewm(span=9, adjust=False).values
    macd_bar = 2 * (dif - dea)
    
    tech["MACD_DIF"] = round(float(dif[-1]), 4)
    tech["MACD_DEA"] = round(float(dea[-1]), 4)
    tech["MACD_柱状值"] = round(float(macd_bar[-1]), 4)
    tech["MACD_柱状前值"] = round(float(macd_bar[-2]), 4)
    tech["MACD_变化方向"] = "扩张" if abs(macd_bar[-1]) > abs(macd_bar[-2]) else "收缩"
    
    # RSI
    def rsi(s, n):
        delta = s.diff()
        gain = delta.clip(lower=0).rolling(n).mean()
        loss = -delta.clip(upper=0).rolling(n).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    s_close = pd.Series(close)
    rsi6 = rsi(s_close, 6)
    rsi12 = rsi(s_close, 12)
    tech["RSI6"] = round(float(rsi6.iloc[-1]), 2) if pd.notna(rsi6.iloc[-1]) else None
    tech["RSI12"] = round(float(rsi12.iloc[-1]), 2) if pd.notna(rsi12.iloc[-1]) else None
    if tech.get("RSI6") and tech["RSI6"] is not None:
        tech["RSI6_状态"] = "超买" if tech["RSI6"] > 70 else ("超卖" if tech["RSI6"] < 30 else "中性")
    
    # 量比
    if len(vol) >= 6:
        vol_5ma = float(pd.Series(vol[-6:-1]).mean())
        tech["量比(较5日均量)"] = round(float(vol[-1]) / vol_5ma, 2) if vol_5ma > 0 else None
    
    # 支撑/压力
    r60 = df_latest.tail(60)
    r20 = df_latest.tail(20)
    tech["近60日最高价"] = float(r60['high'].max())
    tech["近60日最低价"] = float(r60['low'].min())
    tech["近20日支撑位(最低)"] = float(r20['low'].min())
    tech["近20日压力位(最高)"] = float(r20['high'].max())
    tech["MA60支撑参考"] = tech.get("MA60")
    tech["MA20压力参考"] = tech.get("MA20")
    
    close_p = tech["收盘价"]
    ma20_val = tech.get("MA20")
    ma60_val = tech.get("MA60")
    pos = []
    if ma20_val:
        pos.append("站上MA20" if close_p > ma20_val else "跌破MA20")
    if ma60_val:
        pos.append("站上MA60" if close_p > ma60_val else "跌破MA60")
    tech["均线位置"] = ", ".join(pos) if pos else "N/A"
    
    return tech
