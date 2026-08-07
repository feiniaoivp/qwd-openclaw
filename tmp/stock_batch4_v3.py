#!/usr/bin/env python3
"""
第4批股票分析 - 基于 baostock + akshare(降级)
002413 雷科防务, 688981 中芯国际, 601865 福莱特, 000157 中联重科, 300719 安达维尔
"""

import akshare as ak
import baostock as bs
import pandas as pd
import numpy as np
import json, warnings, time, traceback
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

CURRENT_DATE = "2026-07-26"

stocks = [
    {"code": "002413", "name": "雷科防务", "bs_code": "sz.002413"},
    {"code": "688981", "name": "中芯国际", "bs_code": "sh.688981"},
    {"code": "601865", "name": "福莱特", "bs_code": "sh.601865"},
    {"code": "000157", "name": "中联重科", "bs_code": "sz.000157"},
    {"code": "300719", "name": "安达维尔", "bs_code": "sz.300719"},
]

# ==================== 技术指标计算 ====================

def calc_tech(df):
    """统一计算技术指标, df需含日期,open,high,low,close,volume,amount"""
    df = df.sort_values('日期').reset_index(drop=True)
    dt = df.tail(120)
    if len(dt) < 5:
        return None
    
    c = dt['close'].values; h = dt['high'].values; lv = dt['low'].values; v = dt['volume'].values
    lt = dt.iloc[-1]
    
    tech = {}
    tech["最新日期"] = str(lt['日期'].date()) if hasattr(lt['日期'], 'date') else str(lt['日期'])
    tech["开盘价"] = round(float(lt['open']), 2)
    tech["收盘价"] = round(float(lt['close']), 2)
    tech["最高价"] = round(float(lt['high']), 2)
    tech["最低价"] = round(float(lt['low']), 2)
    tech["成交量"] = int(lt['volume'])
    tech["成交额(万元)"] = round(float(lt['amount']) / 10000, 2) if 'amount' in dt.columns else None
    
    # MA
    sc = pd.Series(c)
    for p in [5, 10, 20, 60]:
        if len(c) >= p:
            tech[f"MA{p}"] = round(float(sc.tail(p).mean()), 3)
    
    # MACD
    ema12 = sc.ewm(span=12, adjust=False).values
    ema26 = sc.ewm(span=26, adjust=False).values
    dif = ema12 - ema26
    dea = pd.Series(dif).ewm(span=9, adjust=False).values
    macd_bar = 2 * (dif - dea)
    tech["MACD_DIF"] = round(float(dif[-1]), 4)
    tech["MACD_DEA"] = round(float(dea[-1]), 4)
    tech["MACD_柱状值"] = round(float(macd_bar[-1]), 4)
    tech["MACD_柱状前日"] = round(float(macd_bar[-2]), 4) if len(macd_bar) > 1 else None
    tech["MACD_柱状方向"] = "扩张" if abs(macd_bar[-1]) >= abs(macd_bar[-2]) else "收缩"
    tech["MACD_红绿"] = "红柱" if macd_bar[-1] >= 0 else "绿柱"
    if macd_bar[-1] >= 0 and abs(macd_bar[-1]) < abs(macd_bar[-2]):
        tech["MACD_红绿"] = "红柱缩短"
    elif macd_bar[-1] < 0 and abs(macd_bar[-1]) < abs(macd_bar[-2]):
        tech["MACD_红绿"] = "绿柱缩短"
    
    # RSI
    def rsi(s, n):
        delta = s.diff().fillna(0)
        gain = delta.clip(lower=0).rolling(n).mean()
        loss = -delta.clip(upper=0).rolling(n).mean()
        loss = loss.replace(0, np.nan)
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    r6 = rsi(sc, 6); r12 = rsi(sc, 12)
    tech["RSI6"] = round(float(r6.iloc[-1]), 2) if pd.notna(r6.iloc[-1]) else None
    tech["RSI12"] = round(float(r12.iloc[-1]), 2) if pd.notna(r12.iloc[-1]) else None
    if tech.get("RSI6") is not None:
        tech["RSI6_状态"] = "超买" if tech["RSI6"] > 70 else ("超卖" if tech["RSI6"] < 30 else "中性")
    
    # VOL量比
    if len(v) >= 6:
        v5 = float(pd.Series(v[-6:-1]).mean())
        tech["量比(较5日均量)"] = round(float(v[-1]) / v5, 2) if v5 > 0 else None
    
    # 支撑/压力
    r60 = dt.tail(60); r20 = dt.tail(20)
    tech["近60日最高"] = round(float(r60['high'].max()), 2)
    tech["近60日最低"] = round(float(r60['low'].min()), 2)
    tech["近20日支撑(最低)"] = round(float(r20['low'].min()), 2)
    tech["近20日压力(最高)"] = round(float(r20['high'].max()), 2)
    tech["MA60支撑参考"] = tech.get("MA60")
    tech["MA20压力参考"] = tech.get("MA20")
    
    cp = tech["收盘价"]
    pos = []
    if tech.get("MA20"): pos.append("站上MA20" if cp > tech["MA20"] else "跌破MA20")
    if tech.get("MA60"): pos.append("站上MA60" if cp > tech["MA60"] else "跌破MA60")
    tech["均线位置"] = ", ".join(pos)
    
    # 涨跌幅
    if len(c) >= 2:
        tech["涨跌幅%"] = round((c[-1]/c[-2] - 1) * 100, 2)
    
    return tech


# ==================== 主流程 ====================

print("=== 登录 baostock ===")
lg = bs.login()
print(f"  {lg.error_code}: {lg.error_msg}")

results = []

for stk in stocks:
    code = stk["code"]
    name = stk["name"]
    bs_code = stk["bs_code"]
    
    print(f"\n{'='*50}\n{code} {name}\n{'='*50}")
    
    result = {"code": code, "name": name, "财务数据": {}, "机构调研": {}, "资金流向": {}, "技术面": {}}
    
    # ==================== 1. 财务数据 ====================
    
    # 1a. akshare stock_yjyg_em (业绩预告)
    for dt_ in ["20250630", "20260331"]:
        try:
            time.sleep(0.8)
            df = ak.stock_yjyg_em(date=dt_)
            if df is not None and not df.empty:
                sub = df[df['股票代码'] == code]
                if not sub.empty:
                    recs = [{c: (str(r[c].date()) if isinstance(r[c], (pd.Timestamp, datetime)) 
                                 else (round(float(r[c]), 4) if isinstance(r[c], float) else r[c]))
                             for c in sub.columns if pd.notna(r[c])}
                            for _, r in sub.iterrows()]
                    result["财务数据"][f"业绩预告_{dt_}"] = recs
                    print(f"  stock_yjyg_em({dt_}): {len(recs)}条")
                    break
        except Exception as e:
            if 'RemoteDisconnected' in str(e):
                print(f"  stock_yjyg_em({dt_}): 连接断开")
            else:
                print(f"  stock_yjyg_em({dt_}): {str(e)[:60]}")
    
    # 1b. baostock 利润表
    try:
        for q in ["2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31"]:
            y, qq = q[:4], str(int(q[5:7])//3)
            rs = bs.query_profit_data(bs_code, year=y, quarter=qq)
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rows:
                fin = {rs.fields[i]: (round(float(rows[0][i]), 2) if rows[0][i] not in ['', 'None'] else None) 
                       for i in range(len(rs.fields))}
                result["财务数据"][f"baostock利润表_{q}"] = fin
                print(f"  baostock利润表({q}): {fin.get('profitDedt', 'N/A')}")
            time.sleep(0.2)
    except Exception as e:
        print(f"  baostock: {str(e)[:60]}")
    
    # ==================== 2. 机构调研 ====================
    print(f"\n--- 机构调研 ---")
    
    # 使用 akshare stock_research_report_em
    # 实际上 stock_research_report_em 是研究报告接口
    try:
        for dt_ in ["2026-07-24", "2026-07-23"]:
            time.sleep(0.8)
            try:
                df_r = ak.stock_research_report_em(date=dt_.replace("-", ""))
                if df_r is not None and not df_r.empty:
                    sub = df_r[df_r['股票代码'] == code] if '股票代码' in df_r.columns else df_r
                    if isinstance(sub, pd.DataFrame) and not sub.empty:
                        recs = [{c: (str(r[c].date()) if isinstance(r[c], (pd.Timestamp, datetime)) else r[c])
                                 for c in sub.columns[:8] if pd.notna(r[c])}
                                for _, r in sub.head(5).iterrows()]
                        result["机构调研"]["研究报告"] = recs
                        print(f"  研报({dt_}): {len(recs)}条")
            except Exception as e:
                if 'arg' in str(e): 
                    # 参数不对，尝试无参数
                    time.sleep(0.5)
                    df_r = ak.stock_research_report_em()
                    if df_r is not None and not df_r.empty:
                        sub = df_r[df_r['股票代码'] == code] if '股票代码' in df_r.columns else df_r
                        if isinstance(sub, pd.DataFrame) and not sub.empty:
                            recs = [{c: (str(r[c].date()) if isinstance(r[c], (pd.Timestamp, datetime)) else r[c])
                                     for c in sub.columns[:8] if pd.notna(r[c])}
                                    for _, r in sub.head(5).iterrows()]
                            result["机构调研"]["研究报告"] = recs
                            print(f"  研报(最新): {len(recs)}条")
    except Exception as e:
        print(f"  研报: {str(e)[:80]}")
    
    # 尝试stock_lhb_jgstatistic_em(机构龙虎榜)
    try:
        df_jg = ak.stock_lhb_jgstatistic_em()
        if df_jg is not None and not df_jg.empty:
            sub = df_jg[df_jg['股票代码'] == code] if '股票代码' in df_jg.columns else df_jg
            if isinstance(sub, pd.DataFrame) and not sub.empty:
                recs = []
                for _, r in sub.head(5).iterrows():
                    rec = {}
                    for c in sub.columns[:10]:
                        v = r[c]
                        if pd.notna(v):
                            rec[c] = str(v.date()) if isinstance(v, (pd.Timestamp, datetime)) else v
                    recs.append(rec)
                result["机构调研"]["龙虎榜机构统计"] = recs
                print(f"  龙虎榜机构: {len(recs)}条")
    except Exception as e:
        if 'attribute' in str(e):
            print(f"  stock_lhb_jgstatistic_em: 不存在")
        elif 'RemoteDisconnected' not in str(e):
            print(f"  龙虎榜机构: {str(e)[:60]}")
    
    # ==================== 3. 资金流向 ====================
    print(f"\n--- 资金流向 ---")
    
    mkt = "sh" if code.startswith("6") else "sz"
    for attempt in range(3):
        try:
            time.sleep(1)
            df_f = ak.stock_individual_fund_flow(stock=code, market=mkt)
            if df_f is not None and not df_f.empty:
                recent = df_f.head(20)
                nc = [c for c in recent.columns if '净额' in c]
                fund_flow = {}
                if nc:
                    col = nc[0]
                    vs = pd.to_numeric(recent[col].values, errors='coerce')
                    fund_flow = {
                        "近5日净流入": round(float(np.nansum(vs[:5])), 2) if len(vs) >= 5 else None,
                        "近10日净流入": round(float(np.nansum(vs[:10])), 2) if len(vs) >= 10 else None,
                        "近20日净流入": round(float(np.nansum(vs[:20])), 2) if len(vs) >= 20 else None,
                        "近5日明细": []
                    }
                    for _, r in recent.head(5).iterrows():
                        rr = {}
                        for c in recent.columns:
                            v = r[c]
                            if pd.notna(v):
                                rr[c] = str(v.date()) if isinstance(v, (pd.Timestamp, datetime)) else (round(v,2) if isinstance(v, float) else v)
                        fund_flow["近5日明细"].append(rr)
                result["资金流向"] = fund_flow
                print(f"  fund_flow: 5日={fund_flow.get('近5日净流入')}, 10日={fund_flow.get('近10日净流入')}, 20日={fund_flow.get('近20日净流入')}")
                break
        except Exception as e:
            print(f"  fund_flow(尝试{attempt+1}): {str(e)[:60]}")
            time.sleep(3)
    else:
        result["资金流向"] = "获取失败(网络断开)"
    
    # ==================== 4. 技术面 ====================
    print(f"\n--- 技术面 ---")
    
    tech = None
    
    # 方案A: baostock (稳定)
    for yr in ["2025-07-01", "2025-01-01", "2024-07-01"]:
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,code,open,high,low,close,preclose,volume,amount,turn",
                start_date=yr, end_date=CURRENT_DATE,
                frequency="d", adjustflag="2"
            )
            dl = []
            while rs.next():
                dl.append(rs.get_row_data())
            if dl and len(dl) >= 20:
                cols = rs.fields
                df = pd.DataFrame(dl, columns=cols)
                for c in cols[2:]:
                    df[c] = pd.to_numeric(df[c], errors='coerce')
                df['日期'] = pd.to_datetime(df['date'])
                df = df.sort_values('日期').reset_index(drop=True)
                tech = calc_tech(df)
                print(f"  baostock: {len(dl)}天数据 (从{yr})")
                break
        except Exception as e:
            print(f"  baostock({yr}): {str(e)[:60]}")
    
    # 方案B: akshare (如果baostock不够)
    if tech is None:
        for at in range(3):
            try:
                time.sleep(2)
                df_h = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                          start_date="20250701", end_date=CURRENT_DATE.replace("-",""), adjust="qfq")
                if df_h is not None and not df_h.empty:
                    tech = calc_tech(df_h) if len(df_h) >= 20 else None
                    print(f"  akshare: {len(df_h)}天")
                    break
            except Exception as e:
                print(f"  akshare(尝试{at+1}): {str(e)[:60]}")
                time.sleep(3)
    
    if tech:
        result["技术面"] = tech
    else:
        result["技术面"] = "所有数据源均失败"
    
    results.append(result)
    print(f"  ✅ {name} 完成")

bs.logout()

# ==================== 输出 ====================
output = {
    "报告日期": CURRENT_DATE,
    "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "第4批股票分析": results
}

path = "/Users/duguke/.openclaw/workspace/tmp/batch4_final.json"
with open(path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

print(f"\n{'='*60}")
print(f"✅ 汇总已保存: {path}")
print(f"{'='*60}")

# 打印简要结果
for r in results:
    tc = r.get("技术面", {})
    fc = r.get("资金流向", {})
    if isinstance(tc, dict):
        print(f"\n{r['code']} {r['name']}:")
        print(f"  收盘={tc.get('收盘价')} MA5={tc.get('MA5')} MA20={tc.get('MA20')} MA60={tc.get('MA60')}")
        print(f"  MACD柱={tc.get('MACD_柱状值')} RSI6={tc.get('RSI6')} 量比={tc.get('量比(较5日均量)')}")
        print(f"  支撑~{tc.get('近20日支撑(最低)')} 压力~{tc.get('近20日压力(最高)')}")
        if isinstance(fc, dict):
            print(f"  资金5日={fc.get('近5日净流入')} 10日={fc.get('近10日净流入')}")
    else:
        print(f"\n{r['code']} {r['name']}: {tc}")
