#!/usr/bin/env python3
"""
第4批修复版 - 修正ewm调用语法
"""

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

def calc_tech(df):
    """修复版: 不用 .values 而是直接操作 pandas"""
    df = df.sort_values('日期').reset_index(drop=True)
    dt = df.tail(120)
    if len(dt) < 5:
        return None
    
    lt = dt.iloc[-1]
    
    tech = {}
    tech["最新日期"] = str(lt['日期'].date())
    tech["开盘价"] = round(float(lt['open']), 2)
    tech["收盘价"] = round(float(lt['close']), 2)
    tech["最高价"] = round(float(lt['high']), 2)
    tech["最低价"] = round(float(lt['low']), 2)
    tech["成交量(手)"] = int(lt['volume'])
    tech["成交额(万元)"] = round(float(lt['amount']) / 10000, 2)
    
    c = dt['close']
    
    # MA
    for p in [5, 10, 20, 60]:
        if len(c) >= p:
            tech[f"MA{p}"] = round(float(c.tail(p).mean()), 3)
    
    # MACD - 使用 pandas 的 ewm
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_bar = 2 * (dif - dea)
    
    tech["MACD_DIF"] = round(float(dif.iloc[-1]), 4)
    tech["MACD_DEA"] = round(float(dea.iloc[-1]), 4)
    tech["MACD_柱状值"] = round(float(macd_bar.iloc[-1]), 4)
    tech["MACD_柱状前日"] = round(float(macd_bar.iloc[-2]), 4) if len(macd_bar) > 1 else None
    
    # MACD方向判断
    mb_now = macd_bar.iloc[-1]; mb_prev = macd_bar.iloc[-2]
    tech["MACD_柱状方向"] = "扩张" if abs(mb_now) >= abs(mb_prev) else "收缩"
    if mb_now >= 0:
        tech["MACD_红绿"] = "红柱" if mb_now >= mb_prev else "红柱缩短"
    else:
        tech["MACD_红绿"] = "绿柱" if mb_now <= mb_prev else "绿柱缩短"
    
    # RSI - 手动计算避免rolling问题
    def calc_rsi(prices, period=14):
        deltas = prices.diff().fillna(0)
        gains = deltas.clip(lower=0)
        losses = -deltas.clip(upper=0)
        
        avg_gain = gains.rolling(period, min_periods=period).mean()
        avg_loss = losses.rolling(period, min_periods=period).mean()
        
        # 第二个值开始用平滑
        for i in range(period, len(avg_gain)):
            avg_gain.iloc[i] = (avg_gain.iloc[i-1] * (period - 1) + gains.iloc[i]) / period
            avg_loss.iloc[i] = (avg_loss.iloc[i-1] * (period - 1) + losses.iloc[i]) / period
        
        # 处理除零
        avg_loss = avg_loss.replace(0, np.nan)
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50)
    
    rsi6 = calc_rsi(c, 6)
    rsi12 = calc_rsi(c, 12)
    tech["RSI6"] = round(float(rsi6.iloc[-1]), 2) if pd.notna(rsi6.iloc[-1]) else None
    tech["RSI12"] = round(float(rsi12.iloc[-1]), 2) if pd.notna(rsi12.iloc[-1]) else None
    if tech.get("RSI6") is not None:
        tech["RSI6_状态"] = "超买(>70)" if tech["RSI6"] > 70 else ("超卖(<30)" if tech["RSI6"] < 30 else "中性")
    
    # 量比
    v = dt['volume']
    if len(v) >= 6:
        v5 = float(v.iloc[-6:-1].mean())
        tech["量比(较5日均量)"] = round(float(v.iloc[-1]) / v5, 2) if v5 > 0 else None
    
    # 支撑/压力
    r60 = dt.tail(60); r20 = dt.tail(20)
    tech["近60日最高价"] = round(float(r60['high'].max()), 2)
    tech["近60日最低价"] = round(float(r60['low'].min()), 2)
    tech["近20日支撑位"] = round(float(r20['low'].min()), 2)
    tech["近20日压力位"] = round(float(r20['high'].max()), 2)
    
    cp = tech["收盘价"]
    pos = []
    if tech.get("MA20"):
        pos.append("站上MA20" if cp > tech["MA20"] else "跌破MA20")
    if tech.get("MA60"):
        pos.append("站上MA60" if cp > tech["MA60"] else "跌破MA60")
    tech["均线位置"] = ", ".join(pos) if pos else "N/A"
    
    if len(c) >= 2:
        tech["涨跌幅%"] = round((c.iloc[-1]/c.iloc[-2] - 1) * 100, 2)
    
    return tech


# ==================== 主流程 ====================
print("=== baostock 技术面获取 ===")
lg = bs.login()
print(f"  login: {lg.error_code}")

# 先加载已有的财务数据
existing = {}
try:
    with open(f"{TMP}/batch4_final.json", "r", encoding="utf-8") as f:
        existing_data = json.load(f)
        for r in existing_data.get("第4批股票分析", []):
            existing[r["code"]] = r
        print(f"  已加载已有数据: {len(existing)}只")
except:
    print("  没有已有数据")

results_tech = []

for stk in stocks:
    code = stk["code"]
    name = stk["name"]
    
    bs_code = f"sh.{code}" if code.startswith("6") else f"sz.{code}"
    
    print(f"\n{'='*40}\n{code} {name} ({bs_code})")
    
    tech = None
    
    for sd in ["2025-07-01", "2025-01-01", "2024-07-01"]:
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,code,open,high,low,close,preclose,volume,amount,turn",
                start_date=sd, end_date=CURRENT_DATE,
                frequency="d", adjustflag="2"
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
                print(f"  baostock: {len(dl)}天, 收盘={tech.get('收盘价')}")
                break
            else:
                print(f"  {sd}: 仅{len(dl)}天")
        except Exception as e:
            print(f"  {sd}: {str(e)[:80]}")
    
    result = existing.get(code, {"code": code, "name": name, "财务数据": {}, "机构调研": {}, "资金流向": {}, "技术面": {}})
    if tech:
        result["技术面"] = tech
    else:
        result["技术面"] = "技术面获取失败"
    
    results_tech.append(result)
    print(f"  ✅ {name}")

bs.logout()

# ==================== 汇总输出 ====================
# 合并财务数据: 如果之前有stock_yjyg_em数据但结果里没有，再补一次
import akshare as ak

print("\n=== 补全业绩预告数据 ===")
for r in results_tech:
    code = r["code"]
    name = r["name"]
    if not r["财务数据"] or "2025年中报业绩预告" not in r.get("财务数据", {}):
        try:
            time.sleep(0.5)
            df_yj = ak.stock_yjyg_em(date="20250630")
            if df_yj is not None and not df_yj.empty:
                sub = df_yj[df_yj['股票代码'] == code]
                if not sub.empty:
                    recs = []
                    for _, row in sub.iterrows():
                        rec = {}
                        for c in sub.columns:
                            v = row[c]
                            if pd.notna(v):
                                rec[c] = str(v.date()) if isinstance(v, (pd.Timestamp, datetime)) else (round(v,4) if isinstance(v, float) else v)
                        recs.append(rec)
                    if "财务数据" not in r: r["财务数据"] = {}
                    r["财务数据"]["2025年中报业绩预告"] = recs
                    print(f"  {code} {name}: 补全业绩预告{len(recs)}条")
        except Exception as e:
            print(f"  {code} {name}: 无法补全 - {str(e)[:60]}")

# 输出
output = {
    "报告日期": CURRENT_DATE,
    "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "第4批股票分析": results_tech
}

fp = f"{TMP}/batch4_final.json"
with open(fp, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

print(f"\n✅ 最终汇总: {fp} ({os.path.getsize(fp)} bytes)")

for r in results_tech:
    tc = r.get("技术面", {})
    print(f"\n{r['code']} {r['name']}:")
    if isinstance(tc, dict) and tc:
        print(f"  收盘={tc.get('收盘价')} | MA5={tc.get('MA5')} MA10={tc.get('MA10')} MA20={tc.get('MA20')} MA60={tc.get('MA60')}")
        print(f"  MACD={tc.get('MACD_柱状值')}({tc.get('MACD_红绿','')}) RSI6={tc.get('RSI6')} 量比={tc.get('量比(较5日均量)')}")
        print(f"  支撑≈{tc.get('近20日支撑位')} 压力≈{tc.get('近20日压力位')} | {tc.get('均线位置')}")
    else:
        print(f"  技术面: {tc}")
    
    fds = r.get("财务数据", {})
    if "2025年中报业绩预告" in fds:
        for rd in fds["2025年中报业绩预告"]:
            p = rd.get('预测指标','')
            v = rd.get('业绩变动','')[:60]
            print(f"  📊 {p}: {v}")
    
    ff = r.get("资金流向", {})
    if ff and isinstance(ff, dict):
        print(f"  💰 资金: {list(ff.keys())}")
