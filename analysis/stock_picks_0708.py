#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
操盘手选股推荐 (trader-stock-picks框架) — 基于新浪日K + hq实时
数据源: 新浪日K(money...quotes.sina.cn jsonp, 稳定) + hq.sinajs.cn(实时/最近交易日)
对31只自选股打分, 按五维模型输出候选 + 高开低走/洗盘/吸筹等形态识别
用法: python3 analysis/stock_picks_0708.py
"""
import urllib.request, json, re, sys, math
import pandas as pd
import numpy as np

WORKSPACE = "/Users/duguke/.openclaw/workspace"

# 自选股: (代码, 名称, 板块)
WATCHLIST = [
    ("600030","中信证券","券商"),("601066","中信建投","券商"),("600036","招商银行","银行"),
    ("601995","中金公司","券商"),("000987","越秀资本","多元金融"),
    ("600584","长电科技","半导体封测"),("688981","中芯国际","晶圆制造"),("002156","通富微电","半导体封测"),
    ("002413","雷科防务","军工电子"),
    ("300014","亿纬锂能","锂电"),("002466","天齐锂业","锂矿"),("601865","福莱特","光伏玻璃"),
    ("300827","上能电气","逆变器/储能"),
    ("300285","国瓷材料","电子陶瓷"),("603308","应流股份","核电/高端制造"),("300124","汇川技术","工控"),
    ("601100","恒立液压","液压件"),("002318","久立特材","不锈钢管"),("300719","安达维尔","航空机电"),
    ("002335","科华数据","数据中心/储能"),("600378","昊华科技","化工"),("600160","巨化股份","化工(氟)"),
    ("600346","恒力石化","石化"),("000708","中信特钢","特钢"),
    ("600660","福耀玻璃","汽车玻璃"),("600570","恒生电子","金融IT"),("605566","福莱蒽特","光伏胶膜"),
    ("000157","中联重科","工程机械"),("601061","中信金属","金属贸易"),("300748","金力永磁","磁性材料"),
]

def fetch_hq(codes):
    """hq.sinajs.cn 批量实时/最近交易日行情"""
    out = {}
    for i in range(0, len(codes), 60):
        batch = [("sh" if c[0] in "69" else "sz") + c for c in codes[i:i+60]]
        url = "http://hq.sinajs.cn/list=" + ",".join(batch)
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        raw = urllib.request.urlopen(req, timeout=12).read().decode("gbk")
        for line in raw.splitlines():
            if '="' not in line: continue
            key = line.split("hq_str_")[1].split("=")[0]
            val = line.split('"')[1].split(",")
            if len(val) < 10: continue
            sym = key[2:]
            def f(x):
                try: return float(x)
                except: return 0.0
            out[sym] = {
                "name": val[0], "open": f(val[1]), "pre_close": f(val[2]), "last": f(val[3]),
                "high": f(val[4]), "low": f(val[5]), "amount": f(val[9]),
                "date": val[30] if len(val) > 30 else "",
            }
    return out

def fetch_kline(symbol, n=120):
    """新浪日K线, 返回DataFrame(date/open/high/low/close/volume)"""
    u = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService."
         f"getKLineData?symbol={symbol}&scale=240&ma=no&datalen={n}")
    req = urllib.request.Request(u, headers={"Referer": "https://finance.sina.com.cn",
                                             "User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
    m = re.search(r'=\s*\((\[.*\])\)\s*;', raw, re.S)
    if not m:
        i, j = raw.find("["), raw.rfind("]")
        if i < 0 or j <= i: return None
        arr = json.loads(raw[i:j+1])
    else:
        arr = json.loads(m.group(1))
    df = pd.DataFrame(arr)
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c])
    df["date"] = pd.to_datetime(df["day"])
    return df[["date","open","high","low","close","volume"]]

def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def macd(close, fast=12, slow=26, signal=9):
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, signal)
    hist = (dif - dea) * 2
    return dif, dea, hist

def rsi(close, n=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def compute_indicators(df):
    """计算技术维度所需指标"""
    close = df["close"]
    vol = df["volume"]
    ind = {}
    # 均线
    for p in [5, 10, 20, 60]:
        ind[f"ma{p}"] = close.rolling(p).mean().iloc[-1]
    ind["close"] = close.iloc[-1]
    # 均线粘合: 20日最高价 vs 最低价 波动幅度
    lo20 = close.rolling(20).min().iloc[-1]
    hi20 = close.rolling(20).max().iloc[-1]
    ma20 = ind["ma20"]
    ind["range_20d"] = (hi20 - lo20) / ma20 * 100 if ma20 else 0
    # MACD
    dif, dea, hist = macd(close)
    ind["dif"] = dif.iloc[-1]; ind["dea"] = dea.iloc[-1]; ind["hist"] = hist.iloc[-1]
    ind["macd_golden"] = bool(dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2])
    ind["macd_bull"] = bool(dif.iloc[-1] > dea.iloc[-1])
    # RSI
    r = rsi(close)
    ind["rsi"] = round(float(r.iloc[-1]), 1) if not np.isnan(r.iloc[-1]) else None
    # 量能: 5日均量 vs 60日均量 (地量判断)
    v5 = vol.rolling(5).mean().iloc[-1]; v60 = vol.rolling(60).mean().iloc[-1]
    ind["vol_ratio_5"] = v5 / v60 if v60 else None
    ind["vol_ratio_20"] = vol.rolling(20).mean().iloc[-1] / v60 if v60 else None
    # 近1年涨停基因(简单: 存在单日涨幅>=9.5%)
    if len(df) >= 60:
        pct = close.pct_change() * 100
        ind["limit_up_1y"] = int((pct >= 9.5).sum())
    else:
        ind["limit_up_1y"] = 0
    # 近20/60日累计涨幅
    if len(df) >= 61:
        ind["ret_20d"] = round((close.iloc[-1] / close.iloc[-21] - 1) * 100, 1)
        ind["ret_60d"] = round((close.iloc[-1] / close.iloc[-61] - 1) * 100, 1)
    # 趋势斜率(20日): 正=上行
    coef = np.polyfit(range(20), close.tail(20).values, 1)[0] if len(close) >= 20 else 0
    ind["slope_20"] = round(coef / ind["close"] * 100, 2) if ind["close"] else 0
    # 高开低走识别(近5日): 连板/跳空后回落
    ind["week_pattern"] = detect_pattern(df)
    return ind

def detect_pattern(df):
    """识别近5-10日操盘形态: 高开低走/洗盘/吸筹/放量突破/拉升"""
    d = df.tail(10).reset_index(drop=True)
    close = d["close"]; open_ = d["open"]; high = d["high"]; low = d["low"]; vol = d["volume"]
    patterns = []
    # 高开低走: 开盘跳空明显高开(>2%)且收盘低于开盘
    recent_highopen = []
    for i in range(len(d)):
        prev_close = close[i-1] if i > 0 else open_[i]
        if prev_close:
            gap = (open_[i] / prev_close - 1) * 100
            ret = (close[i] / prev_close - 1) * 100
            if gap > 2 and ret < gap - 3:  # 高开低走
                recent_highopen.append(round(gap, 1))
    if recent_highopen:
        patterns.append("⚠️高开低走")
    # 缩量横盘(洗盘): 20日波动<12% 且 5日均量<60日均量
    return patterns

def score_stock(code, name, sector, hq, kline_df):
    """五维模型打分(0-10每维, 满分50)"""
    if kline_df is None or len(kline_df) < 30:
        return None, None
    ind = compute_indicators(kline_df)
    scores = {}
    reasons = []
    risks = []

    chg = hq.get(code, {})
    dayret = round((chg["last"] / chg["pre_close"] - 1) * 100, 2) if chg.get("pre_close") else 0
    amt = chg.get("amount", 0)
    lo20 = kline_df["close"].rolling(20).min().iloc[-1]
    hi20 = kline_df["close"].rolling(20).max().iloc[-1]
    near_low = (chg["last"] - lo20) / lo20 * 100 if lo20 else 99
    near_high = (hi20 - chg["last"]) / hi20 * 100 if hi20 else 99
    range20 = ind.get("range_20d", 99)
    v5 = ind.get("vol_ratio_5"); v20 = ind.get("vol_ratio_20")
    slope = ind.get("slope_20", 0)
    macd_bull = ind.get("macd_bull"); macd_golden = ind.get("macd_golden")
    rsi_v = ind.get("rsi")

    # ── 形态定性(决定维度基调, 核心) ──
    highopen_recent = bool(ind.get("week_pattern"))  # 近10日有高开低走
    # 高开低走重判: 近5日出现跳空高开+回落
    d_last = kline_df.tail(5).reset_index(drop=True)
    highopen5 = False; highopen_gap = 0; highopen_ret = 0
    for i in range(1, len(d_last)):
        prev = d_last["close"][i-1]
        op = d_last["open"][i]; cl = d_last["close"][i]
        gap = (op/prev - 1)*100; drag = (cl/op - 1)*100
        if gap > 2 and drag < -2:
            highopen5 = True; highopen_gap = gap; highopen_ret = drag
    # 放量滞涨: 当日跌但成交额大 / 高位宽幅
    lag_with_vol = (dayret < 0 and amt > 40e8)

    # ══ 第零步: 场景分类 (庄股 vs 估值重估 vs 题材) ══
    # 用技术代理估: 近60日涨幅大+涨停多次+波动大 => 若再有催化则偏估值重估
    ret60 = ind.get("ret_60d", 0); lups = ind.get("limit_up_1y", 0)
    scen = "庄股模式"
    if ret60 > 25 and lups >= 3 and range20 > 30:
        scen = "估值重估/题材加速"  # 需人工确认催化剂

    # ══ 维度一: 筹码结构 25% ══
    cs = 5.0
    if scen == "估值重估/题材加速":
        pass  # 估值重估不适用筹码庄股规则, 保守给中分
    elif range20 < 12:
        cs += 2; reasons.append(f"20日窄幅横盘(振幅{range20:.0f}%),筹码集结")
        if v5 and v5 < 0.7:
            cs += 1.5; reasons.append("缩量至60日均量7成以下,浮筹出清")
        # 横盘+贴低+缩量 = 最佳洗盘末期
        if near_low < 8 and v5 and v5 < 0.8:
            cs += 2; reasons.append(f"贴20日低点({near_low:.0f}%)+缩量,典型洗盘尾声")
    elif v5 and v5 < 0.6:
        cs += 1; reasons.append("极度缩量,地量见底")
    if lups > 0:
        cs += 1; reasons.append(f"近1年涨停基因({lups}次)")
    scores["筹码"] = min(10, cs)

    # ══ 维度二: 量价形态 25% ══
    lj = 5.0
    if highopen5:
        lj -= 3.5; risks.append(f"近期高开低走(跳空{highopen_gap:.0f}%收{highopen_ret:.0f}%),上冲乏力")
        if highopen_gap > 5:
            lj -= 1.5; risks.append("大幅高开回落,疑似出货")
    if lag_with_vol:
        lj -= 2; risks.append(f"放量滞跌/出货迹象(额{amt/1e8:.0f}亿)")
    if scen == "估值重估/题材加速":
        # 估值重估用趋势/量能, 不用高开低走当唯一判据
        if v20 and v20 > 1.2:
            lj += 2; reasons.append("持续放量上行(估值重估特征)")
        elif ret60 > 25:
            lj += 1; reasons.append(f"60日强趋势({ret60:+.0f}%),非脉冲")
    else:
        if near_low < 5 and v5 and v5 < 0.8:
            lj += 2; reasons.append(f"贴20日低点+缩量,吸筹区")
        if v20 and 0.9 < v20 < 1.6 and dayret > 0:
            lj += 1; reasons.append("温和放量上攻")
        if v20 and v20 > 1.5 and dayret > 3:
            lj += 2; reasons.append(f"放量突破(20日量比{v20:.1f})")
    if ind["limit_up_1y"] and 0:
        pass
    scores["量价"] = min(10, max(0, lj))

    # ══ 维度三: 资金流向 20% ══
    zj = 5.0
    if slope > 0.3:
        zj += 2; reasons.append(f"20日趋势向上(斜率{slope:.2f}%/日)")
    elif slope < -0.5:
        zj -= 2; risks.append(f"20日趋势走弱(斜率{slope:.2f})")
    if v20 and v20 > 1 and slope > 0:
        zj += 1.5; reasons.append("放量+趋势向上,资金进场")
    if amt > 30e8:
        zj += 1; reasons.append(f"大额成交({amt/1e8:.0f}亿,关注方向)")
    if near_high < 5 and v20 and v20 < 1:
        zj -= 1; risks.append("高位缩量,上攻乏力")
    scores["资金"] = min(10, max(0, zj))

    # ══ 维度四: 基本面催化剂 15% ══
    base_map = {"券商":6,"半导体封测":7,"晶圆制造":7,"军工电子":6,"锂电":6,"锂矿":6,
                "光伏玻璃":5,"逆变器/储能":7,"电子陶瓷":6,"核电/高端制造":7,"工控":6,
                "液压件":6,"不锈钢管":5,"航空机电":6,"数据中心/储能":7,"化工":6,"化工(氟)":6,
                "石化":5,"特钢":5,"汽车玻璃":6,"金融IT":6,"光伏胶膜":5,"工程机械":6,
                "金属贸易":5,"磁性材料":7,"银行":5,"多元金融":5}
    scores["基本面"] = base_map.get(sector, 5)

    # ══ 维度五: 技术面形态 15% ══
    js = 5.0
    if macd_golden:
        js += 3; reasons.append("MACD金叉")
    elif macd_bull:
        js += 1; reasons.append("MACD多头")
    else:
        js -= 1.5; risks.append("MACD空头")
    if rsi_v is not None:
        if 30 <= rsi_v <= 60:
            js += 1
        elif rsi_v > 75:
            js -= 2; risks.append(f"RSI超买({rsi_v})")
        elif rsi_v < 25:
            js += 1.5; reasons.append(f"RSI超卖({rsi_v}),超跌反抽")
    if chg["last"] > ind["ma20"]:
        js += 1; reasons.append("站上MA20")
    else:
        js -= 1.5; risks.append("跌破MA20")
    if highopen5:
        js -= 1  # 技术面也受高开低走拖累
    scores["技术"] = min(10, max(0, js))

    total = round(sum(scores.values()), 1)
    # 阶段判定(结合形态)
    if highopen5 and total < 32:
        stage = "高开低走/兑现风险"
    elif highopen5:
        stage = "高开低走(需企稳确认)"
    elif total >= 40:
        stage = "强势拉升期(追高危险)"
    elif total >= 30:
        stage = "洗盘末期/即将启动 ⭐"
    elif total >= 20:
        stage = "吸筹期/观察(需等待)"
    else:
        stage = "无主力痕迹/弱势"

    return {
        "code": code, "name": name, "sector": sector, "scenario": scen,
        "price": chg.get("last"), "dayret": dayret,
        "amount_yi": round(amt/1e8, 1) if amt else 0,
        "scores": scores, "total": total, "stage": stage,
        "reasons": reasons, "risks": risks,
        "indicators": {k: (None if isinstance(v, float) and (math.isnan(v) or v is None) else v)
                       for k, v in ind.items() if k != "week_pattern"},
        "data_date": chg.get("date"),
    }, ind

def main():
    print("📡 拉取行情与历史K线...", file=sys.stderr)
    codes = [c for c, _, _ in WATCHLIST]
    hq = fetch_hq(codes)
    results = []
    fails = 0
    for code, name, sector in WATCHLIST:
        symbol = ("sh" if code[0] in "69" else "sz") + code
        try:
            df = fetch_kline(symbol, 120)
            if df is None or len(df) < 30:
                fails += 1
                continue
            rec, ind = score_stock(code, name, sector, hq, df)
            if rec:
                results.append(rec)
        except Exception as e:
            fails += 1
            print(f"  ⚠️ {name}({code}) K线失败: {str(e)[:60]}", file=sys.stderr)

    results.sort(key=lambda x: -x["total"])
    out = {"data_date": next((r["data_date"] for r in results if r["data_date"]), ""),
           "total_analyzed": len(results), "fails": fails,
           "candidates": results}
    with open(f"{WORKSPACE}/data/stock_picks_0708.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # 打印
    print("\n" + "="*70)
    print(f"📊 操盘手选股推荐 (数据日期: {out['data_date']})")
    print("="*70)
    print(f"分析自选股 {len(results)} 只 (失败 {fails})")
    print(f"\n{'排名':<4}{'代码':<8}{'名称':<10}{'收盘':>8}{'日涨':>7}{'额亿':>6}{'总分':>5}  阶段")
    print("-"*70)
    for i, r in enumerate(results, 1):
        print(f"{i:<4}{r['code']:<8}{r['name']:<10}{r['price']:>8.2f}{r['dayret']:>+6.1f}%"
              f"{r['amount_yi']:>6.1f}{r['total']:>5.1f}  {r['stage']}")

    print("\n" + "="*70)
    print("🏆 最佳介入候选 (30-40分: 洗盘末期/即将启动)")
    print("="*70)
    for r in results:
        if 30 <= r["total"] < 40:
            print(f"\n◆ {r['name']}({r['code']}) {r['sector']} 总分{r['total']}/50")
            for dim, s in r["scores"].items():
                print(f"   {dim}: {s}/10", end="")
            print(f"  阶段: {r['stage']}")
            for why in r["reasons"][:4]:
                print(f"     + {why}")
            for wk in r["risks"][:3]:
                print(f"     ⚠ {wk}")

if __name__ == "__main__":
    main()
