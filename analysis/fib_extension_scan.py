#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
斐波那契扩展位止盈参考扫描 (fib_extension_scan.py)
==================================================
对关注池/持仓股票计算"上升波段的斐波那契扩展位"(1.0/1.272/1.618/2.618)，
标注当前收盘价所处的扩展区间，结合自适应策略信号给出止盈参考提示。

用途：卖出/止盈端的理论参考锚点（不写入模拟盘交易逻辑，纯参考）。
来源理念：Obsidian《9+ 斐波那契最完整教學｜回撤找買點・扩展找出口》(mio来了)
  - 回撤找买点：0.5-0.618 黄金口袋（入场）
  - 扩展找出口：1.0(首目标等长) / 1.272-1.618(最终止盈强阻力) / 2.618(狂热终点)
  - 共振：扩展位若与水平支撑/EMA均线重合，成功率更高
  - 止损：0.786 下方或前波段低点

用法：
  python3 analysis/fib_extension_scan.py            # 全关注池扫描 → stdout
  python3 analysis/fib_extension_scan.py --save     # 扫描并锁定到 data/fib_predictions_YYYY-MM-DD.json
  python3 analysis/fib_extension_scan.py 300285     # 指定单只
输出：stdout JSON（供 agent 解析/推送）
注意：纯新浪日K（快、含当日）；个别拉不到会标 error=LIMIT，不影响其它。
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, date

import pandas as pd


def fetch_sina_kline(symbol, datalen=150):
    """新浪日K jsonp：{date,open,high,low,close,volume}，快且含当日。"""
    prefix = "sh" if symbol.startswith("6") else "sz"
    sym = prefix + symbol
    url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_x=/"
           f"CN_MarketDataService.getKLineData?symbol={sym}"
           f"&scale=240&ma=no&datalen={datalen}")
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    try:
        raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
        m = re.search(r"=\s*\(?(\[.*?\])\s*\)?\s*;?\s*$", raw, re.S)
        if not m:
            return None
        arr = json.loads(m.group(1))
        if not arr:
            return None
        rows = []
        for r in arr:
            rows.append({
                "date": pd.Timestamp(r["day"]),
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "volume": int(float(r.get("volume", 0))),
            })
        df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        return df
    except Exception:
        return None

# 30只关注股 (code: 名称)
WATCHLIST = {
    "600030": "中信证券", "601066": "中信建投", "600036": "招商银行",
    "601995": "中金公司", "000987": "越秀资本", "600584": "长电科技",
    "688981": "中芯国际", "002156": "通富微电", "002413": "雷科防务",
    "300014": "亿纬锂能", "002466": "天齐锂业", "601865": "福莱特",
    "300285": "国瓷材料", "603308": "应流股份", "300124": "汇川技术",
    "601100": "恒立液压", "002318": "久立特材", "300719": "安达维尔",
    "002335": "科华数据", "300748": "金力永磁", "002180": "奔图科技",
    "300847": "中船汉光", "600160": "巨化股份", "600346": "恒力石化",
    "000708": "中信特钢", "600660": "福耀玻璃", "600570": "恒生电子",
    "605566": "福莱蒽特", "000157": "中联重科", "601061": "中信金属",
}

# 自适应策略映射(code: 策略名)
try:
    with open("/Users/duguke/.openclaw/workspace/data/adaptive_strategy_map.json") as f:
        STRAT_MAP = json.load(f)
except Exception:
    STRAT_MAP = {}


def find_latest_uptrend_swing(df, lookback=150):
    """识别最近的上升波段(swing_low -> swing_high)与当前回调低点。
    优化：优先选「当前价格仍高于起点、涨幅合理、最近」的波段，
    而非整个窗口的绝对最高点（避免抓到早已走完的旧大波段）。
    若当前处深度回调/下跌（价已跌破波段起点 1.05 倍），返回 None 并标注场景不适用。"""
    if df is None or len(df) < 30:
        return None
    d = df.tail(lookback).reset_index(drop=True)
    closes = d["close"].values
    highs = d["high"].values
    lows = d["low"].values
    n = len(d)
    cur = float(closes[-1])

    def is_low(idx):
        lo, lg = max(0, idx - 4), min(n, idx + 5)
        return lows[idx] == min(lows[lo:lg]) and lows[idx] <= closes[idx]

    def is_high(idx):
        lo, lg = max(0, idx - 4), min(n, idx + 5)
        return highs[idx] == max(highs[lo:lg])

    # 从最近往回找显著 swing 高点
    candidate_hi = None
    for i in range(n - 2, max(0, n - 90) - 1, -1):
        if is_high(i) and highs[i] > cur:
            candidate_hi = i
            break
    if candidate_hi is None:
        # 当前价已创/接近阶段新高，取最近显著高点
        for i in range(n - 2, max(0, n - 90) - 1, -1):
            if is_high(i):
                candidate_hi = i
                break
    if candidate_hi is None:
        return None
    swing_high = float(highs[candidate_hi])

    # 从该高点往回找波段起点低点（显著低点）
    swing_low_i, swing_low = None, None
    for i in range(candidate_hi - 1, max(0, candidate_hi - 70) - 1, -1):
        if is_low(i):
            swing_low_i, swing_low = i, lows[i]
            break
    if swing_low_i is None:
        swing_low_i, swing_low = 0, float(min(lows[:candidate_hi]))

    run_pct = swing_high / swing_low - 1 if swing_low > 0 else 0
    if run_pct < 0.05:  # 波段涨幅<5% 不算有效
        return None

    # 当前回调低点（swing_high 之后的盘中最低）
    after = lows[candidate_hi:]
    pullback_low = float(min(after))
    pullback_low_i = candidate_hi + int(after.argmin())

    # 有效性护栏：当前价不能深度跌破波段起点（>5%），否则扩展位场景不成立
    if cur < swing_low * 0.95:
        return {"inapplicable": True, "reason": "当前价已跌破波段起点5%以上，处回调/下跌，扩展位不适用",
                "swing_low": float(swing_low), "swing_high": float(swing_high),
                "run_pct": round(run_pct, 3)}

    return {
        "swing_high": swing_high,
        "swing_low": float(swing_low),
        "swing_high_i": int(candidate_hi),
        "swing_low_i": int(swing_low_i),
        "pullback_low": pullback_low,
        "pullback_low_i": int(pullback_low_i),
        "run_pct": round(run_pct, 3),
    }


def compute_fib_levels(sw):
    """基于 回调低点 + (高-低)*fib 算扩展位。"""
    base = sw["pullback_low"]
    amp = sw["swing_high"] - sw["swing_low"]
    levels = {}
    for ratio in (1.0, 1.272, 1.618, 2.618):
        levels[ratio] = round(base + ratio * amp, 2)
    # 关键回撤参考(入场/止损用)
    levels["0.786"] = round(sw["swing_high"] - 0.786 * amp, 2)
    levels["0.618"] = round(sw["swing_high"] - 0.618 * amp, 2)
    return levels


def locate_price(price, levels):
    """判断当前价位于哪个扩展区间。返回 (区间, 提示)。"""
    e100, e127, e161, e261 = levels[1.0], levels[1.272], levels[1.618], levels[2.618]
    e061, e078 = levels["0.618"], levels["0.786"]
    if price <= e100:
        return "未到等长目标(1.0下方)", f"距1.0等长目标还有 {(e100/price-1)*100:.1f}%"
    if price <= e127:
        return "1.0-1.272 首目标区", f"已过1.0等长目标，距1.272还有 {(e127/price-1)*100:.1f}%"
    if price <= e161:
        return "1.272-1.618 波段止盈区⭐", f"进入波段最终阻力区，距1.618还有 {(e161/price-1)*100:.1f}%"
    if price <= e261:
        return "1.618-2.618 强势延伸区", f"已过主止盈区，距2.618狂热终点还有 {(e261/price-1)*100:.1f}%"
    return "超2.618 狂热终点上方", "已突破2.618极端位，警惕见顶回落"


def scan(symbol, name=None):
    name = name or WATCHLIST.get(symbol, symbol)
    df = fetch_sina_kline(symbol)  # 新浪日K（快、含当日），无 baostock 回退
    if df is None or len(df) < 30:
        return {"symbol": symbol, "name": name, "error": "LIMIT"}

    price = float(df["close"].iloc[-1])
    dt = str(df["date"].iloc[-1].date())
    sw = find_latest_uptrend_swing(df)
    if sw is None:
        return {"symbol": symbol, "name": name, "date": dt, "price": price,
                "note": "未识别到有效上升波段", "error": "no_uptrend"}
    if sw.get("inapplicable"):
        return {"symbol": symbol, "name": name, "date": dt, "price": price,
                "strategy": STRAT_MAP.get(symbol, "?"),
                "note": sw["reason"],
                "swing_low": sw["swing_low"], "swing_high": sw["swing_high"],
                "run_pct": sw["run_pct"],
                "error": "inapplicable"}

    levels = compute_fib_levels(sw)
    zone, hint = locate_price(price, levels)
    strat = STRAT_MAP.get(symbol, "?")

    return {
        "symbol": symbol, "name": name, "date": dt, "price": price,
        "strategy": strat,
        "swing_high": sw["swing_high"], "swing_low": sw["swing_low"],
        "pullback_low": sw["pullback_low"],
        "levels": levels,
        "zone": zone, "hint": hint,
    }


def main():
    args = sys.argv[1:]
    save = "--save" in args
    targets = [a for a in args if not a.startswith("--")]
    if not targets:
        targets = list(WATCHLIST.keys())
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(scan, sym): sym for sym in targets}
        for f in as_completed(futs):
            sym = futs[f]
            try:
                results.append(f.result())
            except Exception as e:
                results.append({"symbol": sym, "name": WATCHLIST.get(sym, sym),
                                "error": "EXC:" + str(e)})
    results.sort(key=lambda x: x.get("symbol", ""))

    if save:
        today = date.today().strftime("%Y-%m-%d")
        out = f"/Users/duguke/.openclaw/workspace/data/fib_predictions_{today}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"date": today, "baseline": results}, f, ensure_ascii=False, indent=2)
        print(f"✅ 已锁定止盈位基线 → {out}（{len([r for r in results if not r.get('error')])}/{len(results)} 只有效）")
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
