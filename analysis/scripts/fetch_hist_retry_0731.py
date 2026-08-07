#!/usr/bin/env python3
"""重试获取历史K线（东方财富 stock_zh_a_hist），重试+兜底 baostock"""
import akshare as ak
import pandas as pd
import numpy as np
import json, os, time, warnings, sys
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
B_STOCKS = {"900925"}

def fetch_hist_em(code, retries=3):
    """东方财富历史K线 前复权"""
    for i in range(retries):
        try:
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                    start_date="20250101", end_date="20260731",
                                    adjust="qfq")
            if df is not None and len(df) >= 60:
                df.rename(columns={"日期":"日期","开盘":"开盘","最高":"最高","最低":"最低",
                                   "收盘":"收盘","成交量":"成交量","成交额":"成交额"}, inplace=True)
                df['日期'] = pd.to_datetime(df['日期'])
                df.sort_values('日期', inplace=True)
                return df
            return None
        except Exception as e:
            if i < retries-1:
                time.sleep(2)
            else:
                return None
    return None

def fetch_hist_baostock(code):
    """兜底 baostock"""
    try:
        import baostock as bs
        if not hasattr(bs, '_login_done') or not bs._login_done:
            lg = bs.login()
        code_map = {"6":"sh","9":"sh"}.get(code[0], "sz")
        bs_code = f"{code_map}.{code}"
        lg = bs.login()
        rs = bs.query_history_k_data_plus(bs_code,
            "date,open,high,low,close,volume,amount",
            start_date='2025-01-01', end_date='2026-07-31',
            frequency="d", adjustflag="2")  # 前复权
        rows = []
        while (rs.error_code == '0') & rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["日期","开盘","最高","最低","收盘","成交量","成交额"])
        for c in ["开盘","最高","最低","收盘","成交量","成交额"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df['日期'] = pd.to_datetime(df['日期'])
        df = df.dropna(subset=["收盘"])
        df = df.fillna({"成交量": 0, "成交额": 0})
        df.sort_values('日期', inplace=True)
        return df
    except Exception:
        return None

def calc_indicators(hist):
    if hist is None or len(hist) < 20:
        return {}
    close = hist['收盘'].values.astype(float)
    high = hist['最高'].values.astype(float)
    low = hist['最低'].values.astype(float)
    volume = hist['成交量'].values.astype(float)
    def ma(n): return round(np.mean(close[-n:]),2) if len(close)>=n else None
    ma5, ma10, ma20, ma60 = ma(5), ma(10), ma(20), ma(60)
    ema12=close.copy(); ema26=close.copy()
    for i in range(1,len(close)):
        ema12[i]=ema12[i-1]*11/13+close[i]*2/13
        ema26[i]=ema26[i-1]*25/27+close[i]*2/27
    dif=ema12-ema26; dea=dif.copy()
    for i in range(1,len(dif)):
        dea[i]=dea[i-1]*8/10+dif[i]*2/10
    macd=2*(dif-dea)
    # RSI14
    rsi=None
    if len(close)>=15:
        d=np.diff(close); g=np.where(d>0,d,0); l=np.where(d<0,-d,0)
        ag=np.mean(g[-14:]); al=np.mean(l[-14:])
        rsi=round(100 if al==0 else 100-(100/(1+ag/al)),1)
    # 布林带
    bb_mid=bb_up=bb_lo=None
    if len(close)>=20:
        bb_mid=ma20; s=np.std(close[-20:]); bb_up=round(bb_mid+2*s,2); bb_lo=round(bb_mid-2*s,2)
    vol_r=None
    if len(volume)>=10:
        v5=np.mean(volume[-5:]); v10=np.mean(volume[-10:])
        vol_r=round(v5/v10 if v10>0 else 1,2)
    cur=round(float(close[-1]),2)
    sig=[]
    if ma5 and ma10 and ma20:
        if cur>ma5>ma10>ma20: sig.append("多头排列")
        elif cur<ma5<ma10<ma20: sig.append("空头排列")
        elif cur>ma5 and ma5>ma20: sig.append("短期多头")
        elif cur<ma5 and ma5<ma20: sig.append("短期空头")
        else: sig.append("均线交织")
    if rsi is not None: sig.append(f"RSI{rsi}")
    if bb_up and cur>bb_up: sig.append("突破上轨")
    elif bb_lo and cur<bb_lo: sig.append("跌破下轨")
    elif bb_mid and cur>bb_mid: sig.append("中轨上方")
    elif bb_mid: sig.append("中轨下方")
    if vol_r:
        if vol_r>2: sig.append("显著放量")
        elif vol_r>1.5: sig.append("放量")
        elif vol_r<0.6: sig.append("显著缩量")
        elif vol_r<0.8: sig.append("缩量")
        else: sig.append("量能正常")
    support = bb_lo if bb_lo else (ma20 if ma20 else None)
    resistance = bb_up if bb_up else (ma5 if ma5 else None)
    macd_bull = dif[-1]>dea[-1] if len(dif)>0 else None
    macd_gold = (len(dif)>=2 and dif[-2]<dea[-2] and dif[-1]>dea[-1])
    macd_dead = (len(dif)>=2 and dif[-2]>dea[-2] and dif[-1]<dea[-1])
    return {
        "ma5":ma5,"ma10":ma10,"ma20":ma20,"ma60":ma60,
        "macd_dif":round(float(dif[-1]),4) if len(dif)>0 else None,
        "macd_dea":round(float(dea[-1]),4) if len(dea)>0 else None,
        "macd_hist":round(float(macd[-1]),4) if len(macd)>0 else None,
        "macd_bullish":bool(macd_bull) if macd_bull is not None else None,
        "macd_golden_cross":bool(macd_gold),"macd_dead_cross":bool(macd_dead),
        "rsi":rsi,"bb_upper":bb_up,"bb_mid":bb_mid,"bb_lower":bb_lo,
        "vol_ratio":vol_r,"support":round(support,2) if support else None,
        "resistance":round(resistance,2) if resistance else None,
        "trend_signals":sig,"current_price":cur,
        "last_date":str(hist['日期'].iloc[-1].date()),
        "n_days":len(close)
    }

out = {}
em_ok = bs_ok = 0
for code, name in STOCKS.items():
    if code in B_STOCKS:
        out[code] = {"status":"B股","name":name}
        continue
    df = fetch_hist_em(code)
    src = "em"
    if df is None:
        df = fetch_hist_baostock(code)
        src = "baostock"
    if df is not None and len(df) >= 20:
        ind = calc_indicators(df)
        ind["source"] = src
        out[code] = {"status":"ok","name":name,"indicators":ind}
        if src=="em": em_ok+=1
        else: bs_ok+=1
        print(f"  ✅ {name}({code}) [{src}] n={ind['n_days']} last={ind['last_date']} MA5={ind['ma5']} MA20={ind['ma20']} RSI={ind['rsi']}")
    else:
        out[code] = {"status":"fail","name":name}
        print(f"  ❌ {name}({code}) 获取失败")
    time.sleep(0.3)

with open("/Users/duguke/.openclaw/workspace/analysis/daily/2026-07-31_indicators.json","w",encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"\n完成: EM成功{em_ok}, baostock成功{bs_ok}, 失败{sum(1 for v in out.values() if v.get('status')=='fail')}")
