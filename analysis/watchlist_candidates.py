#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
操盘手候选盯盘 — 中信金属/恒立液压/中联重科
==========================================
基于 trader-stock-picks 框架核心信号, 每日收盘对这3只候选做盯盘:
  - 洗盘末期判定(缩量+窄幅横盘+贴20日低)
  - 高开低走/出货风险识别(跳空高开+回落)
  - 突破/金叉启动确认(MACD金叉+站上MA20)
数据源: 新浪日K(稳定, 周末可拉最近交易日) + hq.sinajs.cn 实时
输出: stdout 的 WATCH_CANDIDATES JSON 标记, 供 daily cron 简报组装。

用法: python3 analysis/watchlist_candidates.py
"""
import urllib.request, json, re, sys, os
import pandas as pd
import numpy as np
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")

# 操盘手候选: (代码, 名称, 注意点)
CANDIDATES = [
    ("601061", "中信金属", "顺周期/资源, 60日超跌修复, 放量上攻"),
    ("601100", "恒立液压", "高端制造, 低开拉高真强势, MACD金叉"),
    ("000157", "中联重科", "工程机械, 缩量洗盘尾声, 等站上7.7确认"),
]


def code_prefix(code):
    """新浪代码前缀: 6开头=sh, 其余=sz; B股9=sh"""
    return "sh" if code[0] in "69" else "sz"


def fetch_hq(codes):
    """hq.sinajs.cn 批量实时/最近交易日行情"""
    out = {}
    for i in range(0, len(codes), 60):
        batch = [code_prefix(c) + c for c in codes[i:i+60]]
        url = "http://hq.sinajs.cn/list=" + ",".join(batch)
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        raw = urllib.request.urlopen(req, timeout=12).read().decode("gbk")
        for line in raw.splitlines():
            if '="' not in line:
                continue
            key = line.split("hq_str_")[1].split("=")[0]
            val = line.split('"')[1].split(",")
            if len(val) < 10:
                continue
            sym = key[2:]
            def f(x):
                try: return float(x)
                except: return 0.0
            out[sym] = {
                "name": val[0], "open": f(val[1]), "pre_close": f(val[2]), "last": f(val[3]),
                "high": f(val[4]), "low": f(val[5]),
                "volume": int(float(val[8])), "amount": f(val[9]),
                "date": val[30] if len(val) > 30 else "",
                "time": val[31] if len(val) > 31 else "",
            }
    return out


def fetch_kline(symbol, n=120):
    """新浪日K线, 返回DataFrame(close/vol等)。周末也能拉最近交易日。"""
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
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["day"])
    return df[["date", "open", "high", "low", "close", "volume"]].dropna()


def ema(s, span):
    return s.ewm(span=span, adjust=False).mean()


def analyze(code, name, note, hq, df):
    """对单只候选做盯盘研判, 输出紧凑JSON记录"""
    r = hq.get(code, {})
    if not df is None and len(df) >= 60:
        close = df["close"]
        vol = df["volume"]
        dayret = round((r["last"] / r["pre_close"] - 1) * 100, 2) if r.get("pre_close") else 0
        amt_yi = round(r["amount"] / 1e8, 1) if r.get("amount") else 0

        # 均线 / 通道
        ma5 = close.rolling(5).mean().iloc[-1]
        ma10 = close.rolling(10).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        lo20 = close.rolling(20).min().iloc[-1]
        hi20 = close.rolling(20).max().iloc[-1]
        range20 = (hi20 - lo20) / ma20 * 100 if ma20 else 99
        near_low = (r["last"] - lo20) / lo20 * 100 if lo20 else 99

        # MACD
        dif = ema(close, 12) - ema(close, 26)
        dea = ema(dif, 9)
        macd_bull = dif.iloc[-1] > dea.iloc[-1]
        macd_golden = dif.iloc[-2] <= dea.iloc[-2] and dif.iloc[-1] > dea.iloc[-1]

        # 量能
        v5 = vol.rolling(5).mean().iloc[-1]
        v20 = vol.rolling(20).mean().iloc[-1]
        v60 = vol.rolling(60).mean().iloc[-1]
        vr_5_60 = (v5 / v60) if v60 else None
        vr_20_60 = (v20 / v60) if v60 else None

        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = float((100 - 100 / (1 + rs)).iloc[-1]) if np.isfinite(rs.iloc[-1]) else None

        # 高开低走识别: 近5日跳空>2%且收跌>2%
        highopen5 = False; ho_gap = 0; ho_ret = 0
        dl = df.tail(5).reset_index(drop=True)
        for i in range(1, len(dl)):
            prev = dl["close"][i-1]; op = dl["open"][i]; cl = dl["close"][i]
            gap = (op/prev - 1)*100; drag = (cl/op - 1)*100
            if gap > 2 and drag < -2:
                highopen5 = True; ho_gap = gap; ho_ret = drag

        # 当日形态判断
        today_open = r.get("open", 0); today_last = r.get("last", 0)
        today_gap = (today_open / r["pre_close"] - 1) * 100 if r.get("pre_close") else 0
        today_drag = (today_last / today_open - 1) * 100 if today_open else 0
        today_high_low = (today_last / today_open - 1) * 100 if today_open else 0
        is_today_highopen_low = today_gap > 2 and today_drag < -2 and today_high_low < -2

        # ── 盯盘维度打分 (0-10) ──
        # 洗盘末期: 缩量+窄幅+贴低
        wash = 5.0
        why_wash = []
        if range20 < 15:
            wash += 2; why_wash.append(f"20日窄幅震荡({range20:.0f}%)")
        if vr_5_60 and vr_5_60 < 0.75:
            wash += 1.5; why_wash.append(f"缩量(5/60日均量{vr_5_60:.2f})")
        if near_low < 10 and wash >= 6.5:
            wash += 1; why_wash.append(f"贴20日低点(距低{near_low:.0f}%)")
        if macd_golden:
            wash += 1; why_wash.append("MACD金叉")
        wash = min(10, wash)

        # 启动确认
        launch = 5.0
        why_launch = []
        if macd_golden:
            launch += 2.5; why_launch.append("MACD金叉")
        if r["last"] > ma20:
            launch += 1.5; why_launch.append("站上MA20")
        if r["last"] > ma5 > ma10:
            launch += 1; why_launch.append(f"多头排列(价>MA5>{ma10:.2f})")
        if vr_20_60 and vr_20_60 > 1.1 and dayret > 1:
            launch += 1; why_launch.append(f"放量上攻(量比{vr_20_60:.1f})")
        launch = min(10, launch)

        # 风险
        risks = []
        if highopen5:
            risks.append(f"⚠️高位低走(跳空{ho_gap:.0f}%收{ho_ret:.0f}%)")
        if is_today_highopen_low:
            risks.append(f"⚠️当日高开低走(开+{today_gap:.0f}%/收{today_drag:.0f}%)")
        if rsi and rsi > 72:
            risks.append(f"RSI超买({rsi:.0f})")
        if r["last"] < ma20:
            risks.append("跌破MA20")

        # 判定
        signals = []
        if wash >= 7.5 and not highopen5:
            signals.append("🟢洗盘末期(可分批建仓)")
        elif wash >= 6 and not highopen5:
            signals.append("🟡洗盘蓄势中(观察)")
        if launch >= 6.5 and not highopen5:
            signals.append("📈启动确认中")
        if highopen5 or is_today_highopen_low:
            signals.append("🔴高开低走=兑现风险(不加仓)")

        return {
            "code": code, "name": name, "note": note,
            "price": round(r["last"], 2),
            "change_pct": dayret, "amount_yi": amt_yi,
            "data_date": r.get("date", ""),
            "wash_score": round(wash, 1), "launch_score": round(launch, 1),
            "wash_reasons": why_wash, "launch_reasons": why_launch,
            "risks": risks, "signals": signals,
            "indicators": {
                "MA5": round(ma5, 2), "MA20": round(ma20, 2),
                "range20_pct": round(range20, 1), "near_low_pct": round(near_low, 1),
                "vol_ratio_5_60": round(vr_5_60, 2) if vr_5_60 else None,
                "MACD_bull": macd_bull, "MACD_golden": macd_golden,
                "RSI14": rsi,
            },
        }
    # 数据不足
    return {
        "code": code, "name": name, "note": note,
        "error": "数据不足", "data_date": r.get("date", ""),
        "price": round(r.get("last", 0), 2),
        "change_pct": round((r["last"] / r["pre_close"] - 1) * 100, 2) if r.get("pre_close") else 0,
        "signals": [], "risks": [],
    }


def main():
    codes = [c for c, _, _ in CANDIDATES]
    hq = fetch_hq(codes)
    results = []
    for code, name, note in CANDIDATES:
        try:
            df = fetch_kline(code_prefix(code) + code, 120)
            results.append(analyze(code, name, note, hq, df))
        except Exception as e:
            results.append({"code": code, "name": name, "note": note,
                            "error": str(e)[:60], "signals": [], "risks": []})

    out = {
        "type": "操盘手候选盯盘",
        "data_date": next((r.get("data_date", "") for r in results if r.get("data_date")), ""),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "candidates": results,
    }
    print("=====WATCH_CANDIDATES=====")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print("=====WATCH_CANDIDATES_END=====")


if __name__ == "__main__":
    main()
