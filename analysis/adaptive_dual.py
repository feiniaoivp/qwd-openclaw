#!/usr/bin/env python3
"""
自适应双策略分配器 — 每股跑最优的两个策略
=============================================
基于 validation_*.json 的多窗口稳健评分，为每只股票找出
**score 排名前二**的策略，分别跑出当日信号，输出对比。

数据源: baostock(前复权) + 新浪实时补丁（当日数据）
"""

import os, sys, json, time, warnings
from datetime import datetime
import pandas as pd
import pandas_ta as ta
import baostock as bs
import numpy as np
import urllib.request

warnings.filterwarnings("ignore")

WORKSPACE = "/Users/duguke/.openclaw/workspace"
OUTPUT_DIR = os.path.join(WORKSPACE, "analysis", "daily")
VALIDATION_FILE = os.path.join(WORKSPACE, "analysis", "validation_2026-08-01.json")
PARAMS_FILE = os.path.join(WORKSPACE, "data", "adaptive_params.json")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_latest_validation():
    """合并所有 validation_*.json (日期新者优先)，返回 {symbol: info}。
    解决：新股补评分写到新 validation 文件后，能被 adaptive 双策略自动拾取，
    同时保留旧文件里已有股票的评分。批量文件缺失/损坏时回退到旧的 VALIDATION_FILE。"""
    import glob
    files = glob.glob(os.path.join(WORKSPACE, "analysis", "validation_*.json"))
    files = [f for f in files if os.path.abspath(f) != os.path.abspath(VALIDATION_FILE) or True]
    files.sort()  # 文件名含日期, 自然升序 → 新的在后
    merged = {}
    for f in files:
        try:
            with open(f) as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                merged.update(data)  # 新文件覆盖同 key
        except Exception:
            continue
    if not merged and os.path.exists(VALIDATION_FILE):
        with open(VALIDATION_FILE) as fh:
            merged = json.load(fh)
    return merged

# ── 31只关注股 ──
STOCKS = [
    ("002318","久立特材"),("300014","亿纬锂能"),("601066","中信建投"),
    ("600030","中信证券"),("300124","汇川技术"),("601995","中金公司"),
    ("600584","长电科技"),("002156","通富微电"),("002466","天齐锂业"),
    ("600036","招商银行"),("600570","恒生电子"),("605566","福莱蒽特"),
    ("000987","越秀资本"),("603308","应流股份"),("300285","国瓷材料"),
    ("002413","雷科防务"),("688981","中芯国际"),("601865","福莱特"),
    ("000157","中联重科"),("300719","安达维尔"),("601061","中信金属"),
    ("600660","福耀玻璃"),("002335","科华数据"),
    ("601100","恒立液压"),
    ("600160","巨化股份"),
    ("600346","恒力石化"),("000708","中信特钢"),("300748","金力永磁"),
    # ── 2026-08-06 替换: 撤上能电气, 加打印机国产替代龙头 ──
    ("002180","奔图科技"),("300847","中船汉光"),
]

# ── validation中文策略名 → 信号函数key 映射 ──
VALIDATION_STRAT_MAP = {
    "布林带+ATR": "bollinger",
    "KDJ+CCI": "kdj_cci",
    "EMA+OBV": "ema_obv",
    "EMA12/26金叉(基准C)": "ema_cross",
    "纯MACD(基准B)": "macd",
}

STRATEGY_LABELS = {
    "bollinger": "📊 布林带+ATR",
    "kdj_cci": "🎯 KDJ+RSI",
    "ema_obv": "📈 EMA+OBV",
    "ema_cross": "💹 EMA12/26",
    "macd": "📉 纯MACD",
}

# 各策略默认参数 (param_tune 未覆盖时使用)
DEFAULT_PARAMS = {
    "bollinger": {"length": 20, "std": 2.0, "atr_mult": 1.5},
    "kdj_cci": {"length": 9, "rsi_threshold": 40},
    "ema_obv": {"length": 20},
    "ema_cross": {"fast": 12, "slow": 26},
    "macd": {"fast": 12, "slow": 26, "signal": 9},
}

DEFAULT_STRATEGY = "ema_cross"


# ════════════════════════════════════════
# 数据获取（复用 adaptive_trader 逻辑）
# ════════════════════════════════════════

def fetch_today_realtime(symbols):
    prefix_map = {s: ("sh" if s.startswith("6") else "sz") for s in symbols}
    codes = [f"{prefix_map[s]}{s}" for s in symbols]
    url = "https://hq.sinajs.cn/list=" + ",".join(codes)
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    result = {}
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        text = resp.read().decode("gbk")
        for s in symbols:
            prefix = prefix_map[s]
            start = text.find(f"hq_str_{prefix}{s}=\"")
            if start < 0:
                continue
            start += len(f"hq_str_{prefix}{s}=\"")
            end = text.find("\"", start)
            if end < 0:
                continue
            fields = text[start:end].split(",")
            if len(fields) < 4:
                continue
            try:
                cur = float(fields[3])
                oh = float(fields[1])
                hi = float(fields[4]) if fields[4] else cur
                lo = float(fields[5]) if fields[5] else cur
                vol = int(fields[8]) if fields[8] else 0
                result[s] = {"date": datetime.now().strftime("%Y-%m-%d"),
                             "open": oh, "close": cur, "high": hi, "low": lo, "volume": vol}
            except (ValueError, IndexError):
                pass
        return result
    except Exception:
        return {}


def _fetch_sina_daily(symbol: str, n: int = 300):
    """新浪日K jsonp 接口(可靠)。baostock 整体失败时的历史兜底，能过 60 条门槛并算技术指标。
    只提供原始日K(非前复权)，指标用相对阈值兼容。返回 df[date,open,high,low,close,volume]。"""
    import re as _re
    pref = "sh" if symbol.startswith("6") else "sz"
    u = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService."
         f"getKLineData?symbol={pref}{symbol}&scale=240&ma=no&datalen={n}")
    req = urllib.request.Request(u, headers={"Referer": "https://finance.sina.com.cn",
                                             "User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
    m = _re.search(r'=\s*\((\[.*\])\)\s*;', raw, _re.S)
    if not m:
        i, j = raw.find("["), raw.rfind("]")
        if i < 0 or j <= i:
            return None
        import json as _json
        arr = _json.loads(raw[i:j+1])
    else:
        import json as _json
        arr = _json.loads(m.group(1))
    if not arr:
        return None
    df = pd.DataFrame(arr)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["day"])
    df = df[["date", "open", "high", "low", "close", "volume"]].dropna()
    return df.sort_values("date").reset_index(drop=True)


def fetch_data(symbol, start="20250101", max_retry=3, realtime_fallback=True):
    bs_code = f"sh.{symbol}" if symbol.startswith("6") else f"sz.{symbol}"
    start_ymd = f"{start[0:4]}-{start[4:6]}-{start[6:8]}"
    end_ymd = datetime.now().strftime("%Y-%m-%d")
    today_str = datetime.now().strftime("%Y-%m-%d")

    for attempt in range(max_retry):
        try:
            lg = bs.login()
            if lg.error_code != "0":
                raise ConnectionError(lg.error_msg)
            rs = bs.query_history_k_data_plus(
                bs_code, "date,open,close,high,low,volume",
                start_date=start_ymd, end_date=end_ymd,
                frequency="d", adjustflag="2")
            data = []
            while rs.next():
                data.append(rs.get_row_data())
            bs.logout()
            if not data:
                raise ValueError("空数据")
            df = pd.DataFrame(data, columns=["date","open","close","high","low","volume"])
            for c in ["open","close","high","low","volume"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True).dropna()

            if realtime_fallback:
                latest_bs_date = df["date"].iloc[-1].strftime("%Y-%m-%d")
                if latest_bs_date < today_str:
                    import datetime as dt
                    if dt.date.today().weekday() < 5:
                        rt = fetch_today_realtime([symbol])
                        if symbol in rt:
                            r = rt[symbol]
                            new_row = {
                                "date": pd.Timestamp(r["date"]),
                                "open": r["open"], "close": r["close"],
                                "high": r["high"], "low": r["low"],
                                "volume": r["volume"],
                            }
                            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                            print(f"  [补丁] {symbol} baostock最新{latest_bs_date} → 新浪拼接{r['date']} 收盘¥{r['close']:.2f}")
            return df
        except Exception:
            time.sleep(1)
        finally:
            try:
                bs.logout()
            except:
                pass

    # baostock彻底失败 → 兜底（先新浪日K历史，能过60门槛+算指标；失败再退回新浪实时单条）
    if realtime_fallback:
        df = _fetch_sina_daily(symbol)
        if df is not None and len(df) >= 60:
            # 确保包含当日，若新浪日K不含今日且为交易日，拼一条实时
            latest = df["date"].iloc[-1].strftime("%Y-%m-%d")
            if latest < today_str:
                rt = fetch_today_realtime([symbol])
                if symbol in rt:
                    r = rt[symbol]
                    new_row = {"date": pd.Timestamp(r["date"]), "open": r["open"],
                               "close": r["close"], "high": r["high"], "low": r["low"],
                               "volume": r["volume"]}
                    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            print(f"  [兜底] {symbol} baostock失败 → 新浪日K {len(df)}条, 收盘¥{df['close'].iloc[-1]:.2f}")
            return df
        rt = fetch_today_realtime([symbol])
        if symbol in rt:
            r = rt[symbol]
            df = pd.DataFrame([{
                "date": pd.Timestamp(r["date"]), "open": r["open"],
                "close": r["close"], "high": r["high"], "low": r["low"],
                "volume": r["volume"],
            }])
            print(f"  [兜底] {symbol} baostock失败，纯新浪实时: ¥{r['close']:.2f}")
            return df
    return None


# ════════════════════════════════════════
# 五个策略的信号发生器
# ════════════════════════════════════════

def signal_bollinger_atr(df, params=None):
    p = dict(DEFAULT_PARAMS["bollinger"]); p.update(params or {})
    bb = ta.bbands(df["close"], length=p["length"], std=p["std"])
    bbu = bb[[c for c in bb.columns if "BBU" in c.upper()][0]]
    bbm = bb[[c for c in bb.columns if "BBM" in c.upper()][0]]
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    if float(prev["close"]) <= float(bbu.iloc[-2]) and float(row["close"]) > float(bbu.iloc[-1]):
        action = "🟢买入"; reason = f"布林上轨突破(¥{float(bbu.iloc[-1]):.2f})"
    elif float(prev["close"]) >= float(bbm.iloc[-2]) and float(row["close"]) < float(bbm.iloc[-1]):
        action = "🔴卖出"; reason = f"跌破中轨(¥{float(bbm.iloc[-1]):.2f})"
    return {"action": action, "reason": reason, "price": price,
            "indicators": f"上轨{float(bbu.iloc[-1]):.2f} 中轨{float(bbm.iloc[-1]):.2f} ATR{float(atr.iloc[-1]):.2f}"}


def signal_kdj_cci(df, params=None):
    p = dict(DEFAULT_PARAMS["kdj_cci"]); p.update(params or {})
    cci = ta.cci(df["high"], df["low"], df["close"], length=14)
    cci_pct = cci.rolling(60).apply(lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-10) * 100)
    kdj = ta.kdj(df["high"], df["low"], df["close"], length=p["length"], signal=3)
    k_col = [c for c in kdj.columns if "K_" in c.upper()][0]
    d_col = [c for c in kdj.columns if "D_" in c.upper()][0]
    j_col = [c for c in kdj.columns if "J_" in c.upper()][0]
    k, d, j = kdj[k_col], kdj[d_col], kdj[j_col]
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    cpi = float(cci_pct.iloc[-1]) if pd.notna(cci_pct.iloc[-1]) else 50
    j_cross_up = float(j.iloc[-2]) <= float(k.iloc[-2]) and float(j.iloc[-1]) > float(k.iloc[-1])
    j_cross_down = float(j.iloc[-2]) >= float(k.iloc[-2]) and float(j.iloc[-1]) < float(k.iloc[-1])
    if cpi < 20 and j_cross_up:
        action = "🟢买入"; reason = f"CCI低位({cpi:.0f}%)+KDJ金叉 K={float(k.iloc[-1]):.1f}"
    elif cpi > 80 or j_cross_down:
        reasons = []
        if cpi > 80:
            reasons.append(f"CCI高位({cpi:.0f}%)")
        if j_cross_down:
            reasons.append("KDJ死叉")
        action = "🔴卖出"; reason = " | ".join(reasons)
    return {"action": action, "reason": reason, "price": price,
            "indicators": f"CCI_pct={cpi:.0f}% K={float(k.iloc[-1]):.1f} J={float(j.iloc[-1]):.1f}"}


def signal_ema_obv(df, params=None):
    p = dict(DEFAULT_PARAMS["ema_obv"]); p.update(params or {})
    ema20 = ta.ema(df["close"], length=p["length"])
    obv = ta.obv(df["close"], df["volume"])
    obv_ema20 = ta.ema(obv, length=p["length"])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    e20 = float(ema20.iloc[-1])
    obv_now = float(obv.iloc[-1])
    obv_ema = float(obv_ema20.iloc[-1])
    obv_cross_up = float(obv.iloc[-2]) <= float(obv_ema20.iloc[-2]) and obv_now > obv_ema
    obv_cross_down = float(obv.iloc[-2]) >= float(obv_ema20.iloc[-2]) and obv_now < obv_ema
    if price > e20 and obv_cross_up:
        action = "🟢买入"; reason = f"EMA20上方(¥{e20:.2f}) + OBV上穿"
    elif price < e20 or obv_cross_down:
        reasons = []
        if price < e20:
            reasons.append(f"跌破EMA20(¥{e20:.2f})")
        if obv_cross_down:
            reasons.append("OBV下穿")
        action = "🔴卖出"; reason = " | ".join(reasons)
    return {"action": action, "reason": reason, "price": price,
            "indicators": f"EMA20={e20:.2f} OBV/EMA={obv_now/obv_ema:.2f}x" if obv_ema > 0 else f"EMA20={e20:.2f}"}


def signal_ema_cross(df, params=None):
    p = dict(DEFAULT_PARAMS["ema_cross"]); p.update(params or {})
    ema12 = ta.ema(df["close"], length=p["fast"])
    ema26 = ta.ema(df["close"], length=p["slow"])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    e12, e26 = float(ema12.iloc[-1]), float(ema26.iloc[-1])
    pe12, pe26 = float(ema12.iloc[-2]), float(ema26.iloc[-2])
    if pe12 <= pe26 and e12 > e26:
        action = "🟢买入"; reason = f"EMA{p['fast']}({e12:.2f})金叉EMA{p['slow']}({e26:.2f})"
    elif pe12 >= pe26 and e12 < e26:
        action = "🔴卖出"; reason = f"EMA{p['fast']}({e12:.2f})死叉EMA{p['slow']}({e26:.2f})"
    return {"action": action, "reason": reason, "price": price,
            "indicators": f"EMA{p['fast']}={e12:.2f} EMA{p['slow']}={e26:.2f}"}


def signal_macd(df, params=None):
    p = dict(DEFAULT_PARAMS["macd"]); p.update(params or {})
    macd_df = ta.macd(df["close"], fast=p["fast"], slow=p["slow"], signal=p["signal"])
    dif_col = [c for c in macd_df.columns if c.upper().startswith("MACD_")][0]
    sig_col = [c for c in macd_df.columns if "MACDS_" in c.upper()][0]
    macd_line = macd_df[dif_col]
    signal_line = macd_df[sig_col]
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    m = float(macd_line.iloc[-1]); s = float(signal_line.iloc[-1])
    pm = float(macd_line.iloc[-2]); ps = float(signal_line.iloc[-2])
    if pm <= ps and m > s:
        action = "🟢买入"; reason = f"MACD金叉({m:.4f}/{s:.4f})"
    elif pm >= ps and m < s:
        action = "🔴卖出"; reason = f"MACD死叉({m:.4f}/{s:.4f})"
    return {"action": action, "reason": reason, "price": price,
            "indicators": f"MACD柱={m-s:.4f} Signal={s:.4f}"}


SIGNAL_FUNCS = {
    "bollinger": signal_bollinger_atr,
    "kdj_cci": signal_kdj_cci,
    "ema_obv": signal_ema_obv,
    "ema_cross": signal_ema_cross,
    "macd": signal_macd,
}


def calc_change_pct(df):
    cur = float(df.iloc[-1]["close"])
    prev = float(df.iloc[-2]["close"])
    return round((cur / prev - 1) * 100, 2)


# ════════════════════════════════════════
# 主扫描
# ════════════════════════════════════════

def load_dual_strategy_map():
    """读取每股 score 前二的策略 + 最优参数
    优先从 param_tune 结果 (adaptive_params.json) 读取每股各策略的 best_score 与 best_params;
    若 param_tune 未覆盖该股, 回退到 validation json 的策略级评分(用默认参数)。
    """
    dual = {}

    # ① 优先: param_tune 参数级结果
    params_map = None
    if os.path.exists(PARAMS_FILE):
        try:
            with open(PARAMS_FILE) as f:
                params_map = json.load(f).get("stocks", {})
        except Exception:
            params_map = None
    if params_map:
        for symbol, info in params_map.items():
            details = info.get("details", {}) if isinstance(info, dict) else {}
            scored = []
            for strat_key, dinfo in details.items():
                if not isinstance(dinfo, dict):
                    continue
                bp = dinfo.get("best_params")
                bs = dinfo.get("best_score")
                skey = dinfo.get("best_strategy_key", strat_key)
                # strat_key 可能是中文名, 转成信号key
                key = VALIDATION_STRAT_MAP.get(strat_key) or strat_key
                if key in SIGNAL_FUNCS and bp is not None and bs is not None:
                    scored.append({"strategy": key, "score": bs,
                                   "params": bp,
                                   "avg_return": dinfo.get("avg_return"),
                                   "avg_sharpe": dinfo.get("avg_sharpe")})
            if len(scored) >= 2:
                scored.sort(key=lambda x: x["score"], reverse=True)
                dual[symbol] = scored[:2]

    # ② 回退/补齐: 对 param_tune 未覆盖或不足2个策略的股票用 validation(合并所有validation文件,新者优先)
    val = load_latest_validation()
    for symbol, info in val.items():
        if symbol in dual and len(dual[symbol]) >= 2:
            continue
        details = info.get("details", [])
        ranked = sorted(details, key=lambda x: x.get("score", 0), reverse=True)
        top2 = []
        for d in ranked[:2]:
            key = VALIDATION_STRAT_MAP.get(d.get("strategy"))
            if key:
                top2.append({"strategy": key,
                             "score": d.get("score"),
                             "params": None,  # 用默认参数
                             "avg_return": d.get("avg_return"),
                             "avg_sharpe": d.get("avg_sharpe")})
        if top2:
            dual[symbol] = top2

    # 兜底: 凡在 STOCKS 但在 dual_map 无覆盖的股票(如新增新股 validation 未生成评分),
    # 给默认双策略(ema_cross + macd, 默认参数), 避免"validation缺失"导致现价显示0
    for symbol, name in STOCKS:
        if symbol not in dual:
            dual[symbol] = [
                {"strategy": "ema_cross", "score": 0.0, "params": None,
                 "avg_return": None, "avg_sharpe": None},
                {"strategy": "macd", "score": 0.0, "params": None,
                 "avg_return": None, "avg_sharpe": None},
            ]
    return dual


def scan_all():
    today = datetime.now().strftime("%Y-%m-%d")
    dual_map = load_dual_strategy_map()
    results = []
    stats = {"buy": 0, "sell": 0, "hold": 0, "agree": 0, "disagree": 0, "error": 0}
    strat_usage = {k: 0 for k in STRATEGY_LABELS}

    for idx, (symbol, name) in enumerate(STOCKS):
        top2 = dual_map.get(symbol)
        if not top2:
            results.append({"symbol": symbol, "name": name,
                            "error": "validation缺失", "strategies": [],
                            "price": None, "change_pct": 0, "consensus": "-"})
            stats["error"] += 1
            continue

        df = fetch_data(symbol)
        if df is None or len(df) < 60:
            results.append({"symbol": symbol, "name": name,
                            "error": "数据获取失败", "strategies": [],
                            "price": None, "change_pct": 0, "consensus": "-"})
            stats["error"] += 1
            continue

        change_pct = calc_change_pct(df)
        cur_price = float(df.iloc[-1]["close"])
        sigs = []
        for entry in top2:
            sname = entry["strategy"]
            strat_usage[sname] = strat_usage.get(sname, 0) + 1
            params = entry.get("params")
            try:
                sig = SIGNAL_FUNCS[sname](df, params)
                sigs.append({
                    "strategy": sname,
                    "strategy_label": STRATEGY_LABELS[sname],
                    "validation_score": entry["score"],
                    "avg_return": entry.get("avg_return"),
                    "params": params or DEFAULT_PARAMS.get(sname, {}),
                    "price": cur_price,
                    "action": sig["action"],
                    "reason": sig["reason"],
                    "indicators": sig["indicators"],
                })
            except Exception as e:
                sigs.append({"strategy": sname, "error": str(e), "action": "错误"})

        # 判断两策略是否一致
        act1 = sigs[0]["action"] if sigs else "持有"
        act2 = sigs[1]["action"] if len(sigs) > 1 else "持有"
        # 归一化动作类型
        def typ(a):
            if "买入" in a:
                return "buy"
            if "卖出" in a:
                return "sell"
            return "hold"
        t1, t2 = typ(act1), typ(act2)
        agree = (t1 == t2 and t1 != "hold") or (t1 == "hold" and t2 == "hold")
        if (t1 != "hold" and t2 == t1):
            if t1 == "buy":
                stats["buy"] += 1
            else:
                stats["sell"] += 1
            stats["agree"] += 1
        elif t1 != "hold" or t2 != "hold":
            stats["disagree"] += 1
        else:
            stats["hold"] += 1

        results.append({
            "symbol": symbol, "name": name,
            "price": cur_price,
            "change_pct": change_pct,
            "strategies": sigs,
            "consensus": "一致" if (t1 == t2) else "分歧",
        })

    return {"date": today, "stock_count": len(STOCKS), "stats": stats,
            "dual_strategy_map": dual_map, "strategy_usage": strat_usage,
            "details": results}


def format_brief(output):
    lines = []
    s = output["stats"]
    lines.append(f"📊 **双策略最优扫描** ({output['date']})")
    lines.append(f"覆盖 {output['stock_count']} 只 | 每股跑score前2策略")
    lines.append(f"🟢双买 {s['buy']} | 🔴双卖 {s['sell']} | ⚪双持有 {s['hold']} | ⚡分歧 {s['disagree']} | ❌失败 {s['error']}")
    lines.append("")

    # 一致性买/卖
    for title, tgt in [("🟢 **双策略一致买入**", "buy"), ("🔴 **双策略一致卖出**", "sell")]:
        rows = []
        for r in output["details"]:
            if "strategies" not in r or len(r["strategies"]) < 2:
                continue
            acts = [x.get("action", "") for x in r["strategies"]]
            a1, a2 = acts[0], acts[1]
            if ("买入" in a1) == ("买入" in a2) and tgt == "buy" and "买入" in a1:
                rows.append(r)
            elif ("卖出" in a1) == ("卖出" in a2) and tgt == "sell" and "卖出" in a1:
                rows.append(r)
        if rows:
            lines.append(title)
            lines.append("| 股票 | 策略1 | 策略2 | 现价 | 涨跌 |")
            lines.append("|:---|:---|:---|:---:|:---:|")
            for r in rows:
                lbl1 = r["strategies"][0].get("strategy_label", "-").replace("📊 ","").replace("🎯 ","").replace("📈 ","").replace("💹 ","").replace("📉 ","")
                lbl2 = r["strategies"][1].get("strategy_label", "-").replace("📊 ","").replace("🎯 ","").replace("📈 ","").replace("💹 ","").replace("📉 ","")
                price = r.get("price", 0) or 0
                lines.append(f"| {r['name']}({r['symbol']}) | {lbl1} | {lbl2} | ¥{price:.2f} | {r['change_pct']:+.2f}% |")
            lines.append("")

    # 分歧股
    rows = []
    for r in output["details"]:
        if "strategies" not in r or len(r["strategies"]) < 2:
            continue
        acts = [("买入" in x.get("action","")) for x in r["strategies"]]
        acts2 = [("卖出" in x.get("action","")) for x in r["strategies"]]
        if sum(acts) == 1 or sum(acts2) == 1:
            rows.append(r)
    if rows:
        lines.append("⚡ **双策略分歧**")
        lines.append("| 股票 | 策略1 | 策略2 | 现价 |")
        lines.append("|:---|:---|:---|:---:|")
        for r in rows:
            lbl1 = r["strategies"][0].get("strategy_label", "-").replace("📊 ","").replace("🎯 ","").replace("📈 ","").replace("💹 ","").replace("📉 ","")
            lbl2 = r["strategies"][1].get("strategy_label", "-").replace("📊 ","").replace("🎯 ","").replace("📈 ","").replace("💹 ","").replace("📉 ","")
            a1 = r["strategies"][0].get("action","").split(" ")[0]
            a2 = r["strategies"][1].get("action","").split(" ")[0]
            price = r.get("price", 0) or 0
            lines.append(f"| {r['name']}({r['symbol']}) | {lbl1} {a1} | {lbl2} {a2} | ¥{price:.2f} |")
        lines.append("")

    # 📋 全标的双策略信号总榜（覆盖所有关注股）
    all_rows = []
    for r in output["details"]:
        if "strategies" not in r or len(r["strategies"]) < 2 or "error" in r:
            continue
        acts = ["买入" in x.get("action", "") for x in r["strategies"]]
        sells = ["卖出" in x.get("action", "") for x in r["strategies"]]
        buy_n, sell_n = sum(acts), sum(sells)
        if buy_n == 2:
            tag = "🟢双买"
        elif sell_n == 2:
            tag = "🔴双卖"
        elif buy_n == 1 or sell_n == 1:
            tag = "⚡分歧"
        else:
            tag = "⚪双持有"
        all_rows.append((tag, r))
    if all_rows:
        # 排序: 双买/双卖 在前, 分歧次之, 双持有最后
        order = {"🟢双买": 0, "🔴双卖": 1, "⚡分歧": 2, "⚪双持有": 3}
        all_rows.sort(key=lambda x: order.get(x[0], 9))
        lines.append("📋 **全标的双策略信号总榜**（覆盖 " + str(len(all_rows)) + " 只）")
        lines.append("| 股票 | 状态 | 策略1 | 策略2 | 现价 | 涨跌 |")
        lines.append("|:---|:---:|:---|:---|:---:|:---:|")
        for tag, r in all_rows:
            lbl1 = r["strategies"][0].get("strategy_label", "-").replace("📊 ","").replace("🎯 ","").replace("📈 ","").replace("💹 ","").replace("📉 ","")
            lbl2 = r["strategies"][1].get("strategy_label", "-").replace("📊 ","").replace("🎯 ","").replace("📈 ","").replace("💹 ","").replace("📉 ","")
            a1 = r["strategies"][0].get("action", "").split(" ")[0]
            a2 = r["strategies"][1].get("action", "").split(" ")[0]
            price = r.get("price", 0) or 0
            cp = r.get("change_pct") or 0
            lines.append(f"| {r['name']}({r['symbol']}) | {tag} | {lbl1} {a1} | {lbl2} {a2} | ¥{price:.2f} | {cp:+.2f}% |")
        lines.append("")

    # 策略使用分布
    lines.append("📈 **双策略使用分布**（每股用的前2策略累计）")
    for sk, sv in output["strategy_usage"].items():
        if sv > 0:
            lines.append(f"  {STRATEGY_LABELS[sk]}: {sv}次")
    lines.append("")

    return "\n".join(lines)


def main():
    print("📡 开始双策略最优扫描（每股跑score前2策略）...")
    output = scan_all()

    print("\n=====DUAL_SCAN_RESULT=====")
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    print("=====DUAL_SCAN_END=====")

    print("\n" + format_brief(output))

    today = datetime.now().strftime("%Y-%m-%d")
    report_path = os.path.join(OUTPUT_DIR, f"{today}_dual.md")
    with open(report_path, "w") as f:
        f.write(f"# 双策略最优扫描日报 {today}\n\n")
        f.write(format_brief(output))
        # 每只股票的详细信号
        f.write("\n---\n\n## 逐股双策略信号详情\n")
        for r in output["details"]:
            if "strategies" not in r:
                f.write(f"\n### {r['name']}({r['symbol']}) — {r.get('error','')}\n")
                continue
            f.write(f"\n### {r['name']}({r['symbol']}) 现价¥{(r.get('price') or 0):.2f} ({r.get('change_pct') or 0:+.2f}%) [{r['consensus']}]\n")
            for sg in r["strategies"]:
                score = sg.get("validation_score", "?")
                f.write(f"- **{sg.get('strategy_label')}** (score={score}): {sg.get('action')} — {sg.get('reason')}\n")
    print(f"📄 日报已保存: {report_path}")


if __name__ == "__main__":
    main()
