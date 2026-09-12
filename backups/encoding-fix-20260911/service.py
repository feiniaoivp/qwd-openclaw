#!/usr/bin/env python3
"""
服务�层：提供统计的数据获取、技术指标计算、信号生成等纯�函数。
所有�函数仅返回数据，不做打印或文件写入，便于被上�层 Agent �� 调用。
"""

import os, sys, json, time, logging, urllib.request, re, socket
from datetime import datetime, date
import pandas as pd
import akshare as ak
import pandas_ta as ta

# 防御性兜底：外部数据接口有时会间歇性 TLS/IP 挂起，绕过硬编码的 per-call timeout
# 并在 poll 上无限阻塞（实测 quotes.sina.cn 多次触发，最终导致上层 cron SIGKILL）。
# 对所有新建 socket（含 https/SSL 握手）设置全局默认超时，保证任何一步都绝不无限阻塞。
socket.setdefaulttimeout(15)

log = logging.getLogger(__name__)

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
STATE_FILE = os.path.join(WORKSPACE, "data", "zhonglian_state.json")
CAPITAL = 100_000
COMMISSION = 0.0003
SLIPPAGE = 0.001

# �监控股票列表（与之前脚本保持一致）
WATCHLIST = [
    ("600030","中信证�券"),("601066","中信建投"),("600036","�招商银行"),
    ("601995","中金公司"),("000987","越秀资本"),
    ("600584","长电科技"),("688981","中�芯国际"),("002156","通富微电"),
    ("002413","雷科防务"),
    ("300014","亿�纬�锂能"),("002466","天�齐�锂业"),("601865","福莱特"),
    ("300285","国�瓷材料"),("603308","应流股份"),("300124","�汇川技术"),
    ("601100","�恒立�液压"),("002318","久立特材"),("300719","安达维尔"),
    ("002335","科华数据"),
    ("600660","福�耀�玻�璃"),("600570","�恒生电子"),("605566","福莱�蒽特"),
    ("000157","中联重科"),("601061","中信金属"),
    ("600160","巨化股份"),
    ("600346","�恒力石化"),("000708","中信特�钢"),("300748","金力永�磁"),
    ("002180","�奔图科技"),("300847","中船汉光"),
]

# -------------------- 交易日判定 --------------------
def is_trading_day(check_date=None) -> bool:
    """判断指定日期是否为交易日。check_date 为 None 时判断当天。"""
    if check_date is None:
        check_date = datetime.now()
    elif isinstance(check_date, str):
        check_date = datetime.strptime(check_date, "%Y-%m-%d")
    
    if check_date.weekday() >= 5:
        return False
    try:
        cal = ak.tool_trade_date_hist_sina()
        date_str = check_date.strftime("%Y-%m-%d")
        if date_str in cal["trade_date"].values:
            return cal[cal["trade_date"] == date_str].iloc[0]["is_open"] == 1
    except Exception:
        pass
    return True

# -------------------- 新�浪 hq 实时行情 --------------------
def fetch_hq_dict(codes: list[str]) -> dict:
    out = {}
    for i in range(0, len(codes), 60):
        batch = codes[i:i+60]
        url = "http://hq.sinajs.cn/list=" + ",".join(batch)
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        raw = urllib.request.urlopen(req, timeout=12).read().decode("gbk")
        for line in raw.splitlines():
            if '="' not in line:
                continue
            key = line.split('hq_str_')[1].split('=')[0]
            vals = line.split('"')[1].split(',')
            if len(vals) < 10:
                continue
            sym = key[2:]   # � 去�掉 sh/sz 前�缀
            def f(x):
                try: return float(x)
                except: return 0.0
            out[sym] = {
                "name": vals[0],
                "open": f(vals[1]), "pre_close": f(vals[2]), "last": f(vals[3]),
                "high": f(vals[4]), "low": f(vals[5]),
                "volume": int(float(vals[8])), "amount": f(vals[9]),
                "date": vals[30] if len(vals) > 30 else "",
                "time": vals[31] if len(vals) > 31 else "",
            }
    return out

def get_spot_scan() -> tuple[list[dict], str]:
    """返回 (results, data_date) —— data_date 为实际数据日期(最近交易日)"""
    codes = []
    for code, _ in WATCHLIST:
        codes.append(("sh" if code[0] in "69" else "sz") + code)
    hq = fetch_hq_dict(codes)
    data_date = ""
    results = []
    for code, name in WATCHLIST:
        r = hq.get(code)
        if r is None or r["last"] == 0:
            results.append({"symbol": code, "name": name, "error": "无行情"})
            continue
        if not data_date:
            data_date = r["date"]
        chg = (r["last"] / r["pre_close"] - 1) * 100 if r["pre_close"] else 0
        results.append({
            "symbol": code,
            "name": name,
            "price": round(r["last"], 2),
            "change_pct": round(chg, 2),
            "open": round(r["open"], 2),
            "high": round(r["high"], 2),
            "low": round(r["low"], 2),
            "pre_close": round(r["pre_close"], 2),
            "volume": r["volume"],
            "amount": round(r["amount"], 2),
            "data_date": r["date"],
        })
    return results, data_date

# -------------------- � 历史日线（降级路径） --------------------
def _fetch_pytdx_daily(symbol: str):
    try:
        from pytdx.hq import TdxHq_API
    except Exception:
        return None
    api = TdxHq_API()
    try:
        if not api.connect("123.125.108.14", 7709, time_out=5):
            return None
        market = 0 if symbol[0] in "69" else 1
        bars = api.get_security_bars(9, market, symbol, 0, 300)
        if not bars:
            return None
        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["datetime"], unit="s")
        df = df[["date", "open", "high", "low", "close", "vol"]]
        df.columns = ["date", "open", "close", "high", "low", "volume"]
        return df.sort_values("date").reset_index(drop=True).dropna()
    finally:
        try: api.disconnect()
        except: pass

def _fetch_sina_daily(symbol: str, n: int = 300):
    import urllib.request, re, json as _json
    pref = "sh" if symbol[0] in "69" else "sz"
    url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService."
           f"getKLineData?symbol={pref}{symbol}&scale=240&ma=no&datalen={n}")
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn",
                                               "User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
    m = re.search(r'=\s*\((\[.*\])\)\s*;', raw, re.S)
    if not m:
        i, j = raw.find("["), raw.rfind("]")
        arr = _json.loads(raw[i:j+1]) if i>=0 and j>i else []
    else:
        arr = _json.loads(m.group(1))
    df = pd.DataFrame(arr)
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["day"])
    return df[["date","open","high","low","close","volume"]].dropna()

def get_daily_hist(symbol: str, name: str, start_date: str = "20250101",
                   max_retries: int = 3) -> pd.DataFrame | None:
    """数据源降级获取历史日线（前复权近似）。

    优先级改为：新浪日K(稳定, 实测0.1s) → pytdx → akshare(东财, 国内常被拦截)。
    说明：2026-08 实测本机网络 新浪日K 稳定可用，而 akshare(东财) 每次 RemoteDisconnected
    失败且 sleep(3) 重试，30只股会造成 ~270s 纯耗时导致 cron 超时，故把新浪提到最前。
    """
    # 1️⃣ 新浪日K（稳定兜底，实测快）
    try:
        df = _fetch_sina_daily(symbol)
        if df is not None and len(df) >= 2:
            if start_date:
                cut = pd.to_datetime(start_date)
                df = df[df["date"] >= cut]
            if len(df) >= 2:
                log.info(f"  ✅ {name}({symbol}): {len(df)} 条 [新浪日K]")
                return df.reset_index(drop=True)
    except Exception as e:
        log.warning(f"  ⚠️ {name} 新浪日K失败: {e}")

    # 2️⃣ pytdx
    try:
        df = _fetch_pytdx_daily(symbol)
        if df is not None and len(df) >= 2:
            if start_date:
                cut = pd.to_datetime(start_date)
                df = df[df["date"] >= cut]
            if len(df) >= 2:
                log.info(f"  ✅ {name}({symbol}): {len(df)} 条 [pytdx]")
                return df.reset_index(drop=True)
    except Exception as e:
        log.warning(f"  ⚠️ {name} pytdx失败: {e}")

    # 3️⃣ akshare 东财（国内常被拦，最多 1 次，失败不再 sleep 重试）
    try:
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                start_date=start_date, adjust="qfq")
        if df is None or df.empty:
            raise ValueError("数据为空")
        df = df[["日期","开盘","收盘","最高","最低","成交量"]]
        df.columns = ["date","open","close","high","low","volume"]
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        log.info(f"  ✅ {name}({symbol}): {len(df)} 条 [akshare]")
        return df
    except Exception as e:
        log.warning(f"  ⚠️ {name} akshare失败: {e}")

    return None

# -------------------- � 技术指标计算（MACD、EMA、RSI 等） --------------------
def calc_full_signal(df: pd.DataFrame) -> dict:
    close = df["close"]
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    macd_line = macd_df.iloc[:,0]
    signal_line = macd_df.iloc[:,1]
    ema12 = ta.ema(close, length=12)
    ema26 = ta.ema(close, length=26)
    rsi14 = ta.rsi(close, length=14)
    vol_ma20 = df["volume"].rolling(20).mean()

    cur = df.iloc[-1]
    prev = df.iloc[-2]
    close_now = float(cur["close"])
    close_prev = float(prev["close"])
    change_pct = round((close_now/close_prev - 1)*100, 2)

    macd_now = float(macd_line.iloc[-1])
    macd_prev = float(macd_line.iloc[-2])
    sig_now   = float(signal_line.iloc[-1])
    sig_prev  = float(signal_line.iloc[-2])

    rsi = float(rsi14.iloc[-1]) if pd.notna(rsi14.iloc[-1]) else 50.0
    ema12_v = float(ema12.iloc[-1]) if pd.notna(ema12.iloc[-1]) else close_now
    ema26_v = float(ema26.iloc[-1]) if pd.notna(ema26.iloc[-1]) else close_now
    vol_ratio = float(cur["volume"] / vol_ma20.iloc[-1]) if vol_ma20.iloc[-1] > 0 else 1.0

    macd_bull = macd_now > sig_now
    macd_cross_up   = (macd_prev <= sig_prev) and (macd_now > sig_now)
    macd_cross_down = (macd_prev >= sig_prev) and (macd_now < sig_now)
    ema_bull = ema12_v > ema26_v

    score = 0
    reasons, risks = [], []
    if macd_cross_up and rsi < 55:
        score += 2; reasons.append(f"MACD金�叉+RSI{rsi:.0f}<55")
    elif macd_bull:
        score += 1; reasons.append("MACD多头")
    if ema_bull:
        score += 1; reasons.append("EMA多头排列")
    if rsi < 30:
        score += 1; reasons.append(f"RSI超�卖({rsi:.0f})")
    elif rsi > 70:
        score -= 1; risks.append(f"RSI超�买({rsi:.0f})")
    if vol_ratio > 0.6 and close_now > close_prev:
        score += 1; reasons.append("放量上�涨")
    if macd_cross_down:
        score -= 2; risks.append("MACD死�叉")
    if not ema_bull:
        score -= 1; risks.append("EMA空头排列")

    if score >= 3:
        level = "���🟢 � 强烈�买入"
    elif score >= 1:
        level = "���🟡 关注"
    elif score <= -2:
        level = "���🔴 � 强烈�卖出"
    elif score <= -1:
        level = "���🟠 �� 谨�慎"
    else:
        level = "��⚪ 中性"

    return {
        "price": close_now,
        "change_pct": change_pct,
        "volume": int(cur["volume"]),
        "vol_ratio": round(vol_ratio,2),
        "indicators": {
            "MACD": round(macd_now - sig_now,4),
            "MACD_bull": macd_bull,
            "MACD_cross_up": macd_cross_up,
            "MACD_cross_down": macd_cross_down,
            "EMA12": round(ema12_v,2),
            "EMA26": round(ema26_v,2),
            "EMA_bull": ema_bull,
            "RSI14": round(rsi,1),
        },
        "signal": {
            "level": level,
            "score": score,
            "reasons": "; ".join(reasons) if reasons else "无",
            "risks": "; ".join(risks) if risks else "无",
        },
        "data_date": str(cur["date"].date()) if hasattr(cur["date"],"date") else str(cur["date"]),
    }

def calc_spot_signal(spot: dict) -> dict:
    """基于spot实时数据快速判定信号方向（保持与原来脚本一致的简单规则）"""
    chg = spot.get("change_pct",0)
    score = 0
    reasons, risks = [], []
    if chg > 3:
        score += 2; reasons.append(f"�涨幅{chg:.1f}%")
    elif chg > 1:
        score += 1; reasons.append(f"微�涨{chg:.1f}%")
    elif chg < -5:
        score -= 2; risks.append(f"�跌幅{chg:.1f}%")
    elif chg < -2:
        score -= 1; risks.append(f"下�跌{chg:.1f}%")
    if score >= 2:
        level = "���🟢 � 强烈�买入"
    elif score >= 1:
        level = "���🟡 关注"
    elif score == 0:
        level = "��⚪ 中性"
    elif score == -1:
        level = "���🟠 �� 谨�慎"
    else:
        level = "���🔴 � 强烈�卖出"
    return {
        "price": spot["price"],
        "change_pct": spot["change_pct"],
        "volume": spot["volume"],
        "amount": spot.get("amount",0),
        "signal": {
            "level": level,
            "score": score,
            "reasons": "; ".join(reasons) if reasons else "无",
            "risks": "; ".join(risks) if risks else "无",
        }
    }

# -------------------- 中联重科双策略 --------------------
def analyze_zhonglian(df: pd.DataFrame) -> dict:
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    close = df["close"]
    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    # RSI
    delta = close.diff()
    gain = delta.where(delta>0,0).rolling(14).mean()
    loss = (-delta.where(delta<0,0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100/(1+rs))

    macd_bull = (dif.iloc[-1] > dea.iloc[-1]) and (dif.iloc[-2] <= dea.iloc[-2])
    macd_bear = (dif.iloc[-1] < dea.iloc[-1]) and (dif.iloc[-2] >= dea.iloc[-2])
    rsi_low = rsi.iloc[-1] < 50
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    rsi_now = float(rsi.iloc[-1])

    sig1_buy = macd_bull and rsi_low
    sig1_sell = macd_bear
    sig2_buy = macd_bull and (ema12.iloc[-1] > ema26.iloc[-1]) and (rsi_now < 60)
    sig2_sell = macd_bear or (latest["close"] < ema26.iloc[-1])

    dif_dir = "���📗金�叉" if dif.iloc[-1] > dea.iloc[-1] else "���📕死�叉"
    ema_dir = "���📗EMA12>26" if ema12.iloc[-1] > ema26.iloc[-1] else "���📕EMA12<26"

    return {
        "date": str(latest["date"].date()),
        "price": round(float(latest["close"]),2),
        "change_pct": round((float(latest["close"])/float(prev["close"])-1)*100,2),
        "volume": int(latest["volume"]),
        "indicators": {
            "MACD_DIF": round(float(dif.iloc[-1]),4),
            "MACD_DEA": round(float(dea.iloc[-1]),4),
            "MACD_state": dif_dir,
            "EMA12": round(float(ema12.iloc[-1]),2),
            "EMA26": round(float(ema26.iloc[-1]),2),
            "EMA_state": ema_dir,
            "RSI14": round(rsi_now,1),
        },
        "signals": {
            "strategy1": {"name":"MACD+RSI<50","buy":bool(sig1_buy),"sell":bool(sig1_sell),
                          "rsi_filter":rsi_now<50,
                          "desc":f"MACD{'金�叉' if macd_bull else '状态'} + RSI{round(rsi_now,1)}"},
            "strategy2": {"name":"�综合最�优(EMA+MACD+RSI)","buy":bool(sig2_buy),"sell":bool(sig2_sell),
                          "desc":f"MACD金�叉:{macd_bull} EMA12>26:{ema12.iloc[-1] > ema26.iloc[-1]} RSI<60:{rsi_now < 60}"}
        }
    }

# -------------------- � 持�仓/交易�执行（中联重科专用） --------------------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "last_signal_date": None,
        "strategy1": {"name":"MACD+RSI<50","position":False,"entry_price":0,"entry_date":None,
                      "capital":CAPITAL,"shares":0},
        "strategy2": {"name":"�综合最�优(EMA+MACD+RSI)","position":False,"entry_price":0,"entry_date":None,
                      "capital":CAPITAL,"shares":0},
    }

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE,"w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)

def execute_zhonglian_trade(state, signals):
    price = signals["price"]; dt = signals["date"]
    msgs = []; actions = []
    s1 = state["strategy1"]; sig1 = signals["signals"]["strategy1"]
    if sig1["buy"] and not s1["position"]:
        fee = s1["capital"]*COMMISSION
        avail = s1["capital"] - fee
        shares = int(avail/(price*(1+SLIPPAGE))/100)*100
        if shares>=100:
            cost = shares*price*(1+SLIPPAGE)
            fee2 = cost*COMMISSION
            s1["capital"] -= (cost+fee2)
            s1["shares"] = shares
            s1["position"] = True
            s1["entry_price"] = price
            s1["entry_date"] = dt
            actions.append(("strategy1","BUY"))
            msgs.append(f"���🟢 策略1 �买入 � ¥{price} x {shares}股")
    elif sig1["sell"] and s1["position"]:
        sell_val = s1["shares"]*price*(1-SLIPPAGE)
        fee = sell_val*COMMISSION
        net = sell_val - fee
        pnl = round(net - s1["shares"]*s1["entry_price"]*(1+SLIPPAGE),2)
        s1["capital"] += net
        s1["shares"] = 0
        s1["position"] = False
        emoji = "���🟢" if pnl>0 else "���🔴"
        actions.append(("strategy1","SELL"))
        msgs.append(f"{emoji} 策略1 �卖出 � ¥{price} �盈�亏�¥{pnl}")

    s2 = state["strategy2"]; sig2 = signals["signals"]["strategy2"]
    if sig2["buy"] and not s2["position"]:
        fee = s2["capital"]*COMMISSION
        avail = s2["capital"] - fee
        shares = int(avail/(price*(1+SLIPPAGE))/100)*100
        if shares>=100:
            cost = shares*price*(1+SLIPPAGE)
            fee2 = cost*COMMISSION
            s2["capital"] -= (cost+fee2)
            s2["shares"] = shares
            s2["position"] = True
            s2["entry_price"] = price
            s2["entry_date"] = dt
            actions.append(("strategy2","BUY"))
            msgs.append(f"���🟢 策略2 �买入 � ¥{price} x {shares}股")
    elif sig2["sell"] and s2["position"]:
        sell_val = s2["shares"]*price*(1-SLIPPAGE)
        fee = sell_val*COMMISSION
        net = sell_val - fee
        pnl = round(net - s2["shares"]*s2["entry_price"]*(1+SLIPPAGE),2)
        s2["capital"] += net
        s2["shares"] = 0
        s2["position"] = False
        emoji = "���🟢" if pnl>0 else "���🔴"
        actions.append(("strategy2","SELL"))
        msgs.append(f"{emoji} 策略2 �卖出 � ¥{price} �盈�亏�¥{pnl}")

    total_val = (s1["capital"] + (s1["shares"]*price if s1["position"] else 0) +
                 s2["capital"] + (s2["shares"]*price if s2["position"] else 0))
    total_ret = round((total_val - CAPITAL*2)/(CAPITAL*2)*100,2)
    return actions, msgs, state, round(total_val,2), total_ret

# -------------------- � 常用�盘面概�览 --------------------
def market_overview(spot_data: list[dict]) -> dict:
    up = sum(1 for s in spot_data if "error" not in s and s["change_pct"]>0)
    down = sum(1 for s in spot_data if "error" not in s and s["change_pct"]<0)
    total_amt = round(sum(s.get("amount",0) for s in spot_data if "error" not in s)/1e8,2)
    return {"up":up,"down":down,"total":len(spot_data),"total_amount_billion":total_amt}