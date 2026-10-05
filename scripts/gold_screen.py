#!/usr/bin/env python3
"""gold_screen.py — 券商10月金股 × 技术面多维筛选（M7 选股维度落地）

数据源: baostock (adjustflag=2 前复权)，截至最近交易日 2026-09-30
维度(M7):
  1) 近20日涨跌幅（动量）
  2) 现价 vs MA20/MA60（趋势/支撑）
  3) RSI14（超买>70 / 超卖<30）
  4) MACD 状态（DIF>DEA 持多 / 近5日金叉）
  5) 量比（近5日均量 / 近20日均量）
"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
import baostock as bs
import pandas as pd

# (代码, 名称, 推荐家数口径) — 券商10月金股可见名单（gold1 OCR + stcn/上证报公开名单，家数=公开可见）
GOLD_CANDIDATES = [
    ("603259", "药明康德", 8), ("300308", "中际旭创", 4),
    ("600176", "中国巨石", 3), ("600060", "海信视像", 3),
    ("601899", "紫金矿业", 2), ("300761", "立华股份", 2),
    ("300593", "新雷能", 2), ("603893", "瑞芯微", 2),
    ("300394", "天孚通信", 2), ("300866", "安克创新", 2),
    ("002353", "杰瑞股份", 2), ("002517", "恺英网络", 2),
    ("002063", "远光软件", 1), ("688256", "寒武纪", 1),
    ("688041", "海光信息", 1), ("603986", "兆易创新", 1),
    ("603296", "华勤技术", 1), ("688200", "华峰测控", 1),
    ("301291", "明阳电气", 1), ("300750", "宁德时代", 1),
    ("600660", "福耀玻璃", 1), ("300285", "国瓷材料", 1),  # 关注池命中2只
]

end = "2026-09-30"   # 国庆前最后交易日
start_long = "2026-06-01"


def norm_code(code):
    if code.startswith(("600", "601", "603", "605", "688")):
        return f"sh.{code}"
    return f"sz.{code}"


def rsi14(close):
    delta = close.diff()
    up = delta.clip(lower=0).rolling(14).mean()
    dn = (-delta.clip(upper=0)).rolling(14).mean()
    rs = up / dn.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


def macd(close):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    return dif, dea


def analyse(code, name, brokers=1):
    rs = bs.query_history_k_data_plus(
        norm_code(code), "date,close,volume",
        start_date=start_long, end_date=end, frequency="d", adjustflag="2")
    rows = [rs.get_row_data() for _ in iter_rs(rs)]
    if len(rows) < 60:
        return {"code": code, "name": name, "brokers": brokers, "status": "no_data", "n": len(rows)}
    df = pd.DataFrame(rows, columns=rs.fields)
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    df = df.reset_index(drop=True)
    c = df["close"]
    last = c.iloc[-1]
    ret20 = (c.iloc[-1] / c.iloc[-21] - 1) * 100
    ma20 = c.rolling(20).mean().iloc[-1]
    ma60 = c.rolling(60).mean().iloc[-1]
    r = rsi14(c).iloc[-1]
    dif, dea = macd(c)
    macd_state = "多" if dif.iloc[-1] > dea.iloc[-1] else "空"
    cross5 = bool(((dif > dea) & (dif.shift(1) <= dea.shift(1))).tail(5).any())
    vol5 = df["volume"].tail(5).mean()
    vol20 = df["volume"].tail(20).mean()
    volr = vol5 / vol20 if vol20 else float("nan")
    return {
        "code": code, "name": name, "brokers": brokers, "status": "ok",
        "date": df["date"].iloc[-1], "close": round(last, 2),
        "ret20_pct": round(ret20, 2),
        "ma20": round(ma20, 2), "ma60": round(ma60, 2),
        "above_ma20": bool(last > ma20), "above_ma60": bool(last > ma60),
        "rsi14": round(r, 1), "macd": macd_state, "macd_cross5d": cross5,
        "volr5_20": round(volr, 2),
    }


def iter_rs(rs):
    while rs.error_code == "0" and rs.next():
        yield None


def main():
    lg = bs.login()
    if lg.error_code != "0":
        print("login fail:", lg.error_msg); sys.exit(1)
    out = []
    for code, name, brokers in GOLD_CANDIDATES:
        try:
            out.append(analyse(code, name, brokers))
            print(f"[{code}] {name} ✅ ({out[-1]['status']})", flush=True)
        except Exception as e:
            print(f"[{code}] {name} ⚠️ {type(e).__name__}: {e}", flush=True)
            out.append({"code": code, "name": name, "brokers": brokers, "status": f"err:{e}"})
    bs.logout()
    ok = [r for r in out if r.get("status") == "ok"]
    # 综合分：MA20+MA60 线上各+1、MACD多+1、RSI 40-70 健康区+1、量比 1.0-3.0 +1、近20日涨幅>0 +1
    for r in ok:
        score = 0
        score += int(r["above_ma20"])
        score += int(r["above_ma60"])
        score += int(r["macd"] == "多")
        score += int(40 <= r["rsi14"] <= 70)
        score += int(1.0 <= r["volr5_20"] <= 3.0)
        score += int(r["ret20_pct"] > 0)
        r["score"] = score
    path = os.path.join(WORKSPACE, "data", "gold_screen_2026-10-05.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n=== 综合分排序 ===")
    for r in sorted(ok, key=lambda x: (-x["score"], -x["brokers"])):
        print(f'{r["score"]} | {r["name"]}({r["code"]}) 家{r["brokers"]} ¥{r["close"]} '
              f'20日{r["ret20_pct"]}% MA20{"↑" if r["above_ma20"] else "↓"} MA60{"↑" if r["above_ma60"] else "↓"} '
              f'RSI{r["rsi14"]} MACD{r["macd"],"金叉5d" if r["macd_cross5d"] else ""} 量比{r["volr5_20"]}')
    print("\n保存:", path)


if __name__ == "__main__":
    main()
