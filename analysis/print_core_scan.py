#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""国产打印机/复印机核心标的：实时行情 + 技术面 + 资金异动检测"""
import json, urllib.request, datetime, sys

# 核心标的
STOCKS = {
    "sz002180": ("纳思达", "002180"),      # 奔图母公司
    "sz300054": ("鼎龙股份", "300054"),
    "sz300847": ("中船汉光", "300847"),
    "sz300531": ("优博讯", "300531"),
}

HEADERS = {"Referer": "https://finance.sina.com.cn"}

def sina_realtime(symbols):
    url = "https://hq.sinajs.cn/list=" + ",".join(symbols)
    req = urllib.request.Request(url, headers=HEADERS)
    data = urllib.request.urlopen(req, timeout=8).read().decode("gbk", "ignore")
    out = {}
    for line in data.strip().split("\n"):
        if "=" not in line: continue
        var, payload = line.split("=", 1)
        code = var.replace("var hq_str_", "").strip()
        fields = payload.strip('";').split(",")
        if len(fields) < 32: continue
        out[code] = fields
    return out

def sina_kline(symbol, n=90):
    url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_{symbol}=/CN_MarketDataService.getKLineData"
           f"?symbol={symbol}&scale=240&ma=no&datalen={n}")
    req = urllib.request.Request(url, headers=HEADERS)
    raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "ignore")
    # jsonp 包裹：_symbol=([...]);
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1: return []
    arr = json.loads(raw[start:end+1])
    return arr  # keys: day, open, high, low, close, volume

def ema(vals, n):
    k = 2/(n+1); e = vals[0]
    for v in vals[1:]:
        e = v*k + e*(1-k)
    return e

def calc_ols_slope(y):
    n = len(y)
    xs = list(range(n))
    mx, my = sum(xs)/n, sum(y)/n
    num = sum((x-mx)*(yy-my) for x,yy in zip(xs,y))
    den = sum((x-mx)**2 for x in xs) or 1
    return num/den

def rsi(vals, n=14):
    if len(vals) < n+1: return None
    gains, losses = [], []
    for i in range(1, len(vals)):
        d = vals[i]-vals[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    ag = sum(gains[-n:])/n; al = sum(losses[-n:])/n
    if al == 0: return 100.0
    return 100 - 100/(1+ag/al)

def analyze(symbol, name, code, rt, kl):
    last = float(rt[3]); prev = float(rt[2]); openp = float(rt[1])
    high = float(rt[4]); low = float(rt[5]); vol = float(rt[8]); amt = float(rt[9])
    chg = (last-prev)/prev*100 if prev else 0
    amp = (high-low)/prev*100 if prev else 0
    
    closes = [float(x["close"]) for x in kl]
    vols = [float(x["volume"]) for x in kl]
    days = [x["day"] for x in kl]
    
    ma5 = sum(closes[-5:])/5; ma10 = sum(closes[-10:])/10; ma20 = sum(closes[-20:])/20
    vma5 = sum(vols[-5:])/5; vma10 = sum(vols[-10:])/10
    
    trend = "多头" if closes[-1] > ma20 else ("空头" if closes[-1] < ma20 else "震荡")
    pos = "站上MA20" if last > ma20 else "跌破MA20"
    
    vol_ratio_5 = vma5/vma10 if vma10 else 0  # 近5日均量/近10日
    vol_ratio_today = vol/vma5 if vma5 else 0  # 今日量/近5日均量
    
    r14 = rsi(closes)
    slope20 = calc_ols_slope(closes[-20:])
    # 60日新高
    if len(closes) >= 60:
        new60 = last >= max(closes[-60:])
        vs60 = (last/max(closes[-60:])-1)*100
    else:
        new60, vs60 = False, None
    
    # 资金异动信号
    signals = []
    if abs(chg) >= 5: signals.append(f"涨跌幅{chg:+.2f}%偏大")
    if amp >= 8: signals.append(f"振幅{amp:.1f}%高")
    if vol_ratio_today >= 2: signals.append(f"今日量能为5日均{vol_ratio_today:.1f}倍(放量)")
    if vol_ratio_5 >= 1.5: signals.append(f"5日量能放大至10日均{vol_ratio_5:.1f}倍")
    if last == high and chg > 0: signals.append("收在日内最高(强势)")
    if new60: signals.append("创60日新高")
    if r14 and r14 > 70: signals.append(f"RSI {r14:.0f}超买")
    if r14 and r14 < 30: signals.append(f"RSI {r14:.0f}超卖")
    if last < openp and chg < 0: signals.append("高开低走(出货警示)")

    return {
        "名称": name, "代码": code, "现价": last, "涨跌": chg,
        "开": openp, "高": high, "低": low, "振幅": amp,
        "成交额(亿)": amt/1e8, "MA5": ma5, "MA10": ma10, "MA20": ma20,
        "趋势": trend, "位置": pos, "RSI14": r14,
        "斜率20": slope20, "5日量比": vol_ratio_5, "今日量比": vol_ratio_today,
        "60日新高": new60, "距60日高": vs60, "异动": signals, "最新日": days[-1],
    }

def main():
    rt_map = sina_realtime(list(STOCKS.keys()))
    print(f"{'='*78}")
    print(f"国产打印机/复印机核心标的 · 实时行情+技术面+异动   {datetime.datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*78}")
    for sym, (name, code) in STOCKS.items():
        rt = rt_map.get(sym)
        if not rt: 
            print(f"\n[{name}] 实时行情获取失败"); continue
        kl = sina_kline(sym)
        if not kl:
            print(f"\n[{name}] K线获取失败"); continue
        a = analyze(sym, name, code, rt, kl)
        print(f"\n{'─'*78}")
        print(f"◆ {a['名称']}({a['代码']})  现价 {a['现价']:.2f}  {a['涨跌']:+.2f}%   "
              f"开{a['开']:.2f} 高{a['高']:.2f} 低{a['低']:.2f} 振幅{a['振幅']:.1f}%")
        print(f"  成交额 {a['成交额(亿)']:.2f}亿  MA5={a['MA5']:.2f} MA10={a['MA10']:.2f} MA20={a['MA20']:.2f}")
        print(f"  趋势:{a['趋势']} 位置:{a['位置']}  RSI14={a['RSI14']:.1f}  20日斜率={a['斜率20']:.4f}")
        print(f"  5日量比(近5/近10)={a['5日量比']:.2f}  今日量比={a['今日量比']:.2f}  "
              f"60日新高={a['60日新高']} 距60日高={a['距60日高']:+.1f}%")
        if a["异动"]:
            print(f"  ⚠️ 异动: {'; '.join(a['异动'])}")
        else:
            print(f"  资金面: 平稳，无明显异动")
    print(f"\n{'='*78}")
    print(f"数据源: 新浪实时+K线 | 最新交易日: {rt_map[list(STOCKS)[0]][30]}")
    print(f"⚠️ 本报告仅作技术面扫描，非投资建议。")

if __name__ == "__main__":
    main()
