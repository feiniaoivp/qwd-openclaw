#!/usr/bin/env python3
"""
第4批股票分析 - baostock主数据源 + 已完成财务数据合并
提取已有数据 + baostock补全技术面
"""

import akshare as ak
import baostock as bs
import pandas as pd
import numpy as np
import json, warnings, time, os
from datetime import datetime

warnings.filterwarnings('ignore')

CURRENT_DATE = "2026-07-26"
TMP = "/Users/duguke/.openclaw/workspace/tmp"

stocks = [
    {"code": "002413", "name": "雷科防务"},
    {"code": "688981", "name": "中芯国际"},
    {"code": "601865", "name": "福莱特"},
    {"code": "000157", "name": "中联重科"},
    {"code": "300719", "name": "安达维尔"},
]

# ==================== 技术面计算 ====================
def calc_tech(df):
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
    tech["成交量(手)"] = int(lt['volume']) if 'volume' in lt and pd.notna(lt['volume']) else None
    tech["成交额(万元)"] = round(float(lt['amount']) / 10000, 2) if 'amount' in lt and pd.notna(lt['amount']) else None
    
    sc = pd.Series(c)
    for p in [5, 10, 20, 60]:
        if len(c) >= p:
            tech[f"MA{p}"] = round(float(sc.tail(p).mean()), 3)
    
    # MACD
    ema12 = sc.ewm(span=12, adjust=False).values
    ema26 = sc.ewm(span=26, adjust=False).values
    dif = ema12 - ema26
    dea = pd.Series(dif).ewm(span=9, adjust=False).values
    mbar = 2 * (dif - dea)
    tech["MACD_DIF"] = round(float(dif[-1]), 4)
    tech["MACD_DEA"] = round(float(dea[-1]), 4)
    tech["MACD_柱状值"] = round(float(mbar[-1]), 4)
    tech["MACD_柱状前日"] = round(float(mbar[-2]), 4) if len(mbar) > 1 else None
    
    # RSI
    def rsi(s, n):
        delta = s.diff()
        gain = delta.clip(lower=0).rolling(n, min_periods=1).mean()
        loss = -delta.clip(upper=0).rolling(n, min_periods=1).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    r6 = rsi(sc, 6); r12 = rsi(sc, 12)
    tech["RSI6"] = round(float(r6.iloc[-1]), 2) if pd.notna(r6.iloc[-1]) else None
    tech["RSI12"] = round(float(r12.iloc[-1]), 2) if pd.notna(r12.iloc[-1]) else None
    if tech.get("RSI6") is not None:
        tech["RSI6_状态"] = "超买(>70)" if tech["RSI6"] > 70 else ("超卖(<30)" if tech["RSI6"] < 30 else "中性")
    
    # 量比
    if len(v) >= 6:
        v5 = float(pd.Series(v[-6:-1]).mean())
        tech["量比(较5日均量)"] = round(float(v[-1]) / v5, 2) if v5 > 0 else None
    
    # 支撑/压力
    r60 = dt.tail(60); r20 = dt.tail(20)
    tech["近60日最高价"] = round(float(r60['high'].max()), 2)
    tech["近60日最低价"] = round(float(r60['low'].min()), 2)
    tech["近20日支撑位"] = round(float(r20['low'].min()), 2)
    tech["近20日压力位"] = round(float(r20['high'].max()), 2)
    tech["MA60支撑参考"] = tech.get("MA60")
    tech["MA20压力参考"] = tech.get("MA20")
    
    cp = tech["收盘价"]
    pos = []
    if tech.get("MA20"): pos.append("站上MA20" if cp > tech["MA20"] else "跌破MA20")
    if tech.get("MA60"): pos.append("站上MA60" if cp > tech["MA60"] else "跌破MA60")
    tech["均线位置"] = ", ".join(pos)
    
    if len(c) >= 2:
        tech["涨跌幅%"] = round((c[-1]/c[-2] - 1) * 100, 2)
    
    return tech


# ==================== 使用baostock ====================
print("=== 使用 baostock 获取技术面 + 财务 ===")
lg = bs.login()
print(f"  login: {lg.error_code} - {lg.error_msg}")

results = []

for stk in stocks:
    code = stk["code"]
    name = stk["name"]
    
    # 正确的baostock代码格式
    if code.startswith("6"):
        bs_code = f"sh.{code}"
    elif code.startswith("0") or code.startswith("3"):
        bs_code = f"sz.{code}"
    else:
        bs_code = f"sz.{code}"
    
    print(f"\n{'='*50}\n{code} {name} (bs_code={bs_code})\n{'='*50}")
    
    result = {
        "code": code,
        "name": name,
        "财务数据": {},
        "机构调研": {},
        "资金流向": {},
        "技术面": {}
    }
    
    # ===== 1. 财务数据 (已有stock_yjyg_em + baostock补利润表) =====
    
    # 先用akshare的业绩预告 (这个成功了)
    try:
        time.sleep(0.5)
        df_yj = ak.stock_yjyg_em(date="20250630")
        if df_yj is not None and not df_yj.empty:
            sub = df_yj[df_yj['股票代码'] == code]
            if not sub.empty:
                recs = []
                for _, r in sub.iterrows():
                    rec = {}
                    for c in sub.columns:
                        v = r[c]
                        if pd.notna(v):
                            rec[c] = str(v.date()) if isinstance(v, (pd.Timestamp, datetime)) else (round(v,4) if isinstance(v, float) else v)
                    recs.append(rec)
                result["财务数据"]["2025年中报业绩预告"] = recs
                print(f"  业绩预告: {len(recs)}条")
    except Exception as e:
        print(f"  业绩预告: {str(e)[:60]}")
    
    # baostock利润表 - 用正确的频率
    for y in ["2025", "2024"]:
        for q in ["4", "3", "2", "1"]:
            try:
                rs = bs.query_profit_data(bs_code, year=y, quarter=q)
                dl = []
                while rs.next():
                    dl.append(rs.get_row_data())
                if dl:
                    fin = {}
                    fields = rs.fields
                    for i, f in enumerate(fields):
                        v = dl[0][i]
                        if v and v not in ['', 'None']:
                            try:
                                fin[f] = round(float(v), 2)
                            except:
                                fin[f] = v
                    if fin:
                        result["财务数据"][f"利润表_{y}Q{q}"] = fin
                        print(f"  利润表_{y}Q{q}: OK", end=" ")
            except Exception as e:
                pass
        print()
    
    # ===== 2. 机构调研 =====
    # 用 stock_lhb_jgstatistic_em 查机构龙虎榜
    try:
        time.sleep(1)
        df_jg = ak.stock_lhb_jgstatistic_em()
        if df_jg is not None and not df_jg.empty:
            # 这表可能没有直接股票代码匹配，但有名称
            nm_col = None
            for c in df_jg.columns:
                if '名称' in c or '简称' in c:
                    nm_col = c
                    break
            if nm_col:
                sub = df_jg[df_jg[nm_col].str.contains(name, na=False)]
                if not sub.empty:
                    recs = []
                    for _, r in sub.head(5).iterrows():
                        rec = {c: (str(r[c].date()) if isinstance(r[c], (pd.Timestamp, datetime)) else r[c])
                               for c in sub.columns[:10] if pd.notna(r[c])}
                        recs.append(rec)
                    result["机构调研"]["龙虎榜机构统计"] = recs
                    print(f"  龙虎榜机构(按名称匹配): {len(recs)}条")
    except Exception as e:
        if 'RemoteDisconnected' not in str(e):
            print(f"  龙虎榜: {str(e)[:60]}")
    
    # ===== 3. 资金流向 =====
    # EastMoney fund_flow 失败 - 用 stock_lhb_detail_daily_sina(新浪)
    for lhb_dt in ["2026-07-24", "2026-07-23", "2026-07-22", "2026-07-18"]:
        try:
            time.sleep(0.8)
            df_lhb_sina = ak.stock_lhb_detail_daily_sina(date=lhb_dt.replace("-", ""))
            if df_lhb_sina is not None and not df_lhb_sina.empty:
                sub = df_lhb_sina[df_lhb_sina['股票代码'] == code] if '股票代码' in df_lhb_sina.columns else df_lhb_sina
                if isinstance(sub, pd.DataFrame) and not sub.empty:
                    recs = [{c: r[c] for c in sub.columns if pd.notna(r[c])} for _, r in sub.head(3).iterrows()]
                    result["资金流向"]["龙虎榜"] = recs
                    print(f"  龙虎榜_detail({lhb_dt}): {len(recs)}条")
        except Exception as e:
            if 'attribute' in str(e):
                pass
            elif 'RemoteDisconnected' not in str(e):
                print(f"  龙虎榜({lhb_dt}): {str(e)[:60]}")
    
    # ===== 4. 技术面 =====
    tech = None
    
    # baostock 获取日K线
    for sd in ["2025-07-01", "2025-01-01", "2024-07-01", "2024-01-01"]:
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,code,open,high,low,close,preclose,volume,amount,turn",
                start_date=sd, end_date=CURRENT_DATE,
                frequency="d", adjustflag="2"  # 2=前复权
            )
            dl = []
            while rs.next():
                dl.append(rs.get_row_data())
            
            if len(dl) >= 20:
                cols = rs.fields
                df = pd.DataFrame(dl, columns=cols)
                for c in cols[2:]:
                    df[c] = pd.to_numeric(df[c], errors='coerce')
                df['日期'] = pd.to_datetime(df['date'])
                df = df.sort_values('日期').reset_index(drop=True)
                
                tech = calc_tech(df)
                print(f"  技术面(baostock): {len(dl)}天, 最新收盘={tech.get('收盘价')}")
                break
            else:
                print(f"  baostock({sd}): 仅{len(dl)}天数据")
        except Exception as e:
            print(f"  baostock({sd}): {str(e)[:80]}")
    
    if tech:
        result["技术面"] = tech
    else:
        result["技术面"] = "数据不足"
    
    results.append(result)
    print(f"  ✅ {name} 完成")

bs.logout()

# ==================== 输出 ====================
output = {
    "报告日期": CURRENT_DATE,
    "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "第4批股票分析": results
}

fp = f"{TMP}/batch4_final_v4.json"
with open(fp, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

print(f"\n{'='*60}")
print(f"✅ 最终汇总: {fp}")
print(f"文件大小: {os.path.getsize(fp)} bytes")
print(f"{'='*60}")

for r in results:
    tc = r.get("技术面", {})
    fc = r.get("资金流向", {})
    if isinstance(tc, dict) and tc:
        print(f"\n{r['code']} {r['name']}:")
        print(f"  收盘={tc.get('收盘价')} MA5={tc.get('MA5')} MA10={tc.get('MA10')} MA20={tc.get('MA20')} MA60={tc.get('MA60')}")
        print(f"  MACD={tc.get('MACD_柱状值')} RSI6={tc.get('RSI6')}({tc.get('RSI6_状态','')}) 量比={tc.get('量比(较5日均量)')}")
        print(f"  支撑={tc.get('近20日支撑位')} 压力={tc.get('近20日压力位')} 均线={tc.get('均线位置')}")
        fds = r.get("财务数据", {})
        if "2025年中报业绩预告" in fds:
            recs = fds["2025年中报业绩预告"]
            for rd in recs:
                print(f"  业绩: {rd.get('预测指标','')} → {rd.get('业绩变动','')[:60]}")
    else:
        print(f"\n{r['code']} {r['name']}: 技术面数据不可用")
