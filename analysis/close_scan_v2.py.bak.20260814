#!/usr/bin/env python3
"""
盘后全扫描 v2.1 — 25只关注股信号 + 中联重科策略持仓
===================================================
数据源: 新浪 spot (稳定) + 历史日线(东财/baostock降级)
升级: 弃用 baostock, 优先新浪实时接口, 历史接口失败时自动降级

运行: python3 analysis/close_scan_v2.py
输出: JSON(stdout) + 可读简报(stderr)
"""

import os, sys, json, time
import pandas as pd
import akshare as ak
from datetime import datetime, date
import logging
import traceback

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
STATE_FILE = os.path.join(WORKSPACE, "data", "zhonglian_state.json")

WATCHLIST = [
    ("600030", "中信证券"), ("601066", "中信建投"), ("600036", "招商银行"),
    ("601995", "中金公司"), ("000987", "越秀资本"),
    ("600584", "长电科技"), ("688981", "中芯国际"), ("002156", "通富微电"),
    ("002413", "雷科防务"),
    ("300014", "亿纬锂能"), ("002466", "天齐锂业"), ("601865", "福莱特"),
    ("300285", "国瓷材料"), ("603308", "应流股份"), ("300124", "汇川技术"),
    ("601100", "恒立液压"), ("002318", "久立特材"), ("300719", "安达维尔"),
    ("002335", "科华数据"),
    ("600660", "福耀玻璃"), ("600570", "恒生电子"), ("605566", "福莱蒽特"),
    ("000157", "中联重科"), ("601061", "中信金属"),
    # ── 2026-07-29 新增5只 ──
    ("600160", "巨化股份"),
    ("600346", "恒力石化"), ("000708", "中信特钢"), ("300748", "金力永磁"),
    # ── 2026-08-06 替换: 撤上能电气, 加打印机国产替代龙头 ──
    ("002180", "奔图科技"), ("300847", "中船汉光"),
]

CAPITAL = 100_000
COMMISSION = 0.0003
SLIPPAGE = 0.001


def is_trading_day() -> bool:
    """检查今天是否是A股交易日"""
    today = datetime.now()
    if today.weekday() >= 5:
        log.info("今天是周末，非交易日，跳过。")
        return False
    try:
        trade_cal = ak.tool_trade_date_hist_sina()
        today_str = today.strftime("%Y-%m-%d")
        if today_str in trade_cal["trade_date"].values:
            row = trade_cal[trade_cal["trade_date"] == today_str]
            if not row.empty and row.iloc[0]["is_open"] == 1:
                return True
            log.info(f"{today_str} 非交易日，跳过。")
            return False
        return True
    except Exception:
        log.warning("交易日历接口异常，默认放行。")
        return True


def fetch_hq_dict(codes: list[str]) -> dict:
    """用新浪 hq.sinajs.cn 批量拉行情（需 Referer header）。
    返回 {代码: {'name','open','pre_close','last','high','low',
                    'volume','amount','date','time'}}
    必须加 Referer: https://finance.sina.com.cn 否则被拦。
    """
    import urllib.request
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
            val = line.split('"')[1].split(',')
            if len(val) < 10:
                continue
            symbol = key[2:]  # 去掉 sh/sz 前缀
            def f(x):
                try: return float(x)
                except: return 0.0
            out[symbol] = {
                "name": val[0],
                "open": f(val[1]), "pre_close": f(val[2]), "last": f(val[3]),
                "high": f(val[4]), "low": f(val[5]),
                "volume": int(float(val[8])), "amount": f(val[9]),
                "date": val[30] if len(val) > 30 else "",
                "time": val[31] if len(val) > 31 else "",
            }
    return out


def get_spot_scan() -> tuple[list[dict], str]:
    """用新浪 hq.sinajs.cn 拉31只自选股行情（最稳定，收盘后周末也能用）。
    返回 (results, data_date) —— data_date 为实际数据日期(最近交易日)。"""
    log.info("📡 新浪 hq.sinajs.cn 行情扫描...")
    codes = []
    for code, _name in WATCHLIST:
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

def get_daily_hist(symbol: str, name: str, start_date: str = "20250101",
                   max_retries: int = 3) -> pd.DataFrame | None:
    """获取历史日线 — 数据源路由: 新浪日K(稳定,首选) → pytdx → akshare(东财,单次)。
    2026-08 修复: 本机网络 akshare东财被拦截,此前放第1层并 sleep(3)重试会拖慢全扫描,
    故把实测稳定快(0.1s)的新浪日K提到最前;akshare只试1次不 sleep。
    """
    # ── 第1层: 新浪日K(稳定,实测0.1s/只) ──
    try:
        df = _fetch_sina_daily(symbol)
        if df is not None and len(df) >= 2:
            if start_date:
                cut = pd.to_datetime(start_date)
                df = df[df["date"] >= cut]
            if len(df) >= 2:
                log.info(f"  ✅ {name}({symbol}): {len(df)} 条日线 [新浪日K]")
                return df.reset_index(drop=True)
    except Exception as e:
        log.warning(f"  ⚠️ {name} 新浪日K失败: {e}")

    # ── 第2层: pytdx ──
    try:
        df = _fetch_pytdx_daily(symbol)
        if df is not None and len(df) >= 2:
            if start_date:
                cut = pd.to_datetime(start_date)
                df = df[df["date"] >= cut]
            if len(df) >= 2:
                log.info(f"  ✅ {name}({symbol}): {len(df)} 条日线 [pytdx]")
                return df.reset_index(drop=True)
    except Exception as e:
        log.warning(f"  ⚠️ {name} pytdx历史失败: {e}")

    # ── 第3层: akshare 东方财富(单次,失败不sleep) ──
    try:
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                start_date=start_date, adjust="qfq")
        if df is None or df.empty:
            raise ValueError("数据为空")
        df = df[["日期", "开盘", "收盘", "最高", "最低", "成交量"]]
        df.columns = ["date", "open", "close", "high", "low", "volume"]
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        log.info(f"  ✅ {name}({symbol}): {len(df)} 条日线 [akshare]")
        return df
    except Exception as e:
        log.warning(f"  ⚠️ {name} akshare历史失败: {e}")

    return None


def _fetch_pytdx_daily(symbol: str, ip: str = "123.125.108.14", port: int = 7709):
    """尝试 pytdx 通达信日K(前复权近似用不复权)。失败返回 None。"""
    try:
        from pytdx.hq import TdxHq_API
    except Exception:
        return None
    api = TdxHq_API()
    try:
        ok = api.connect(ip, port, time_out=5)
        if not ok:
            return None
        market = 0 if symbol[0] in "69" else 1
        bars = api.get_security_bars(9, market, symbol, 0, 300)
        if not bars:
            return None
        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["datetime"], unit="s")
        df = df[["date", "open", "high", "low", "close", "vol"]]
        df.columns = ["date", "open", "close", "high", "low", "volume"]
        df = df.sort_values("date").reset_index(drop=True).dropna()
        return df
    except Exception:
        return None
    finally:
        try:
            api.disconnect()
        except Exception:
            pass


def _fetch_sina_daily(symbol: str, n: int = 300):
    """新浪日K jsonp 接口(可靠)。删除前复权，只提供原始日K；指标用相对阈值兼容。"""
    import re
    import urllib.request
    pref = "sh" if symbol[0] in "69" else "sz"
    u = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService."
         f"getKLineData?symbol={pref}{symbol}&scale=240&ma=no&datalen={n}")
    req = urllib.request.Request(u, headers={"Referer": "https://finance.sina.com.cn",
                                             "User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
    m = re.search(r'=\s*\((\[.*\])\)\s*;', raw, re.S)
    if not m:
        i, j = raw.find("["), raw.rfind("]")
        if i < 0 or j <= i:
            return None
        import json as _json
        arr = _json.loads(raw[i:j+1])
    else:
        import json as _json
        arr = _json.loads(m.group(1))
    df = pd.DataFrame(arr)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["day"])
    return df[["date", "open", "high", "low", "close", "volume"]].dropna()



def calc_spot_signal(spot: dict) -> dict:
    """基于spot实时数据快速判定信号方向"""
    chg = spot.get("change_pct", 0)
    vol_ratio = 1.0  # 没有历史量对比，默认

    # 简易信号判定
    score = 0
    reasons = []
    risks = []

    if chg > 3:
        score += 2
        reasons.append(f"涨幅{chg:.1f}%")
    elif chg > 1:
        score += 1
        reasons.append(f"微涨{chg:.1f}%")
    elif chg < -5:
        score -= 2
        risks.append(f"跌幅{chg:.1f}%")
    elif chg < -2:
        score -= 1
        risks.append(f"下跌{chg:.1f}%")
    else:
        score += 0

    # 换手率估算（缺历史比，只做参考标签）
    vol = spot.get("volume", 0)
    # 股票流通市值没有，简化处理

    if score >= 2:
        level = "🟢 强烈买入"
    elif score >= 1:
        level = "🟡 关注"
    elif score == 0:
        level = "⚪ 中性"
    elif score == -1:
        level = "🟠 谨慎"
    else:
        level = "🔴 强烈卖出"

    return {
        "price": spot["price"],
        "change_pct": spot["change_pct"],
        "volume": spot["volume"],
        "amount": spot["amount"],
        "signal": {
            "level": level,
            "score": score,
            "reasons": "; ".join(reasons) if reasons else "无",
            "risks": "; ".join(risks) if risks else "无",
        }
    }


def calc_full_signal(df: pd.DataFrame) -> dict:
    """完整技术指标信号（需历史日线数据）"""
    import pandas_ta as ta
    close = df["close"]

    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    macd_line = macd_df.iloc[:, 0]
    signal_line = macd_df.iloc[:, 1]
    ema12 = ta.ema(close, length=12)
    ema26 = ta.ema(close, length=26)
    rsi14 = ta.rsi(close, length=14)
    vol_ma20 = df["volume"].rolling(20).mean()

    cur = df.iloc[-1]
    close_now = float(cur["close"])
    close_prev = float(df.iloc[-2]["close"])
    change_pct = round((close_now / close_prev - 1) * 100, 2)

    macd_now = float(macd_line.iloc[-1])
    macd_prev = float(macd_line.iloc[-2])
    sig_now = float(signal_line.iloc[-1])
    sig_prev = float(signal_line.iloc[-2])

    rsi = float(rsi14.iloc[-1]) if pd.notna(rsi14.iloc[-1]) else 50.0
    ema12_v = float(ema12.iloc[-1]) if pd.notna(ema12.iloc[-1]) else close_now
    ema26_v = float(ema26.iloc[-1]) if pd.notna(ema26.iloc[-1]) else close_now
    vol_ratio = float(cur["volume"] / vol_ma20.iloc[-1]) if vol_ma20.iloc[-1] > 0 else 1.0

    macd_bull = macd_now > sig_now
    macd_cross_up = (macd_prev <= sig_prev) and (macd_now > sig_now)
    macd_cross_down = (macd_prev >= sig_prev) and (macd_now < sig_now)
    ema_bull = ema12_v > ema26_v

    score = 0
    reasons = []
    risks = []

    if macd_cross_up and rsi < 55:
        score += 2
        reasons.append(f"MACD金叉+RSI{rsi:.0f}<55")
    elif macd_bull:
        score += 1
        reasons.append("MACD多头")
    if ema_bull:
        score += 1
        reasons.append("EMA多头排列")
    if rsi < 30:
        score += 1
        reasons.append(f"RSI超卖({rsi:.0f})")
    elif rsi > 70:
        score -= 1
        risks.append(f"RSI超买({rsi:.0f})")
    if vol_ratio > 0.6 and close_now > close_prev:
        score += 1
        reasons.append("放量上涨")
    if macd_cross_down:
        score -= 2
        risks.append("MACD死叉")
    if not ema_bull:
        score -= 1
        risks.append("EMA空头排列")

    if score >= 3:
        level = "🟢 强烈买入"
    elif score >= 1:
        level = "🟡 关注"
    elif score <= -2:
        level = "🔴 强烈卖出"
    elif score <= -1:
        level = "🟠 谨慎"
    else:
        level = "⚪ 中性"

    return {
        "price": close_now,
        "change_pct": change_pct,
        "volume": int(cur["volume"]),
        "vol_ratio": round(vol_ratio, 2),
        "indicators": {
            "MACD": round(macd_now - sig_now, 4),
            "MACD_bull": macd_bull,
            "MACD_cross_up": macd_cross_up,
            "MACD_cross_down": macd_cross_down,
            "EMA12": round(ema12_v, 2),
            "EMA26": round(ema26_v, 2),
            "EMA_bull": ema_bull,
            "RSI14": round(rsi, 1),
        },
        "signal": {
            "level": level,
            "score": score,
            "reasons": "; ".join(reasons) if reasons else "无",
            "risks": "; ".join(risks) if risks else "无",
        },
        "data_date": str(cur["date"].date()) if hasattr(cur["date"], "date") else str(cur["date"]),
    }


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "last_signal_date": None,
        "strategy1": {"name": "MACD+RSI<50", "position": False, "entry_price": 0, "entry_date": None, "capital": CAPITAL, "shares": 0},
        "strategy2": {"name": "综合最优(EMA+MACD+RSI)", "position": False, "entry_price": 0, "entry_date": None, "capital": CAPITAL, "shares": 0},
    }


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)


def analyze_zhonglian(df: pd.DataFrame) -> dict:
    """中联重科双策略分析"""
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    latest_date = str(latest["date"].date())

    close = df["close"]
    ef = close.ewm(span=12, adjust=False).mean()
    es = close.ewm(span=26, adjust=False).mean()
    dif = ef - es
    dea = dif.ewm(span=9, adjust=False).mean()

    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

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

    dif_direction = "📗金叉" if dif.iloc[-1] > dea.iloc[-1] else "📕死叉"
    ema_direction = "📗EMA12>26" if ema12.iloc[-1] > ema26.iloc[-1] else "📕EMA12<26"

    return {
        "date": latest_date,
        "price": round(float(latest["close"]), 2),
        "change_pct": round((float(latest["close"]) / float(prev["close"]) - 1) * 100, 2),
        "volume": int(latest["volume"]),
        "indicators": {
            "MACD_DIF": round(float(dif.iloc[-1]), 4),
            "MACD_DEA": round(float(dea.iloc[-1]), 4),
            "MACD_state": dif_direction,
            "EMA12": round(float(ema12.iloc[-1]), 2),
            "EMA26": round(float(ema26.iloc[-1]), 2),
            "EMA_state": ema_direction,
            "RSI14": round(rsi_now, 1),
        },
        "signals": {
            "strategy1": {"name": "MACD+RSI<50", "buy": bool(sig1_buy), "sell": bool(sig1_sell),
                          "rsi_filter": rsi_now < 50,
                          "desc": f"MACD{'金叉' if macd_bull else '状态'} + RSI{round(rsi_now,1)}"},
            "strategy2": {"name": "综合最优(EMA+MACD+RSI)", "buy": bool(sig2_buy), "sell": bool(sig2_sell),
                          "desc": f"MACD金叉:{macd_bull} EMA12>26:{ema12.iloc[-1] > ema26.iloc[-1]} RSI<60:{rsi_now < 60}"}
        }
    }


def execute_trade(state, signals):
    """双策略交易执行"""
    price = signals["price"]
    dt = signals["date"]
    msgs = []
    actions = []

    s1 = state["strategy1"]
    sig1 = signals["signals"]["strategy1"]
    if sig1["buy"] and not s1["position"]:
        fee = s1["capital"] * COMMISSION
        available = s1["capital"] - fee
        shares = int(available / (price * (1 + SLIPPAGE)) / 100) * 100
        if shares >= 100:
            cost = shares * price * (1 + SLIPPAGE)
            fee2 = cost * COMMISSION
            s1["capital"] -= (cost + fee2)
            s1["shares"] = shares
            s1["position"] = True
            s1["entry_price"] = price
            s1["entry_date"] = dt
            actions.append(("strategy1", "BUY"))
            msgs.append(f"🟢 策略1 买入 ¥{price} x {shares}股")
    elif sig1["sell"] and s1["position"]:
        sell_value = s1["shares"] * price * (1 - SLIPPAGE)
        fee = sell_value * COMMISSION
        net = sell_value - fee
        pnl = round(net - s1["shares"] * s1["entry_price"] * (1 + SLIPPAGE), 2)
        s1["capital"] += net
        s1["shares"] = 0
        s1["position"] = False
        emoji = "🟢" if pnl > 0 else "🔴"
        actions.append(("strategy1", "SELL"))
        msgs.append(f"{emoji} 策略1 卖出 ¥{price} 盈亏¥{pnl}")

    s2 = state["strategy2"]
    sig2 = signals["signals"]["strategy2"]
    if sig2["buy"] and not s2["position"]:
        fee = s2["capital"] * COMMISSION
        available = s2["capital"] - fee
        shares = int(available / (price * (1 + SLIPPAGE)) / 100) * 100
        if shares >= 100:
            cost = shares * price * (1 + SLIPPAGE)
            fee2 = cost * COMMISSION
            s2["capital"] -= (cost + fee2)
            s2["shares"] = shares
            s2["position"] = True
            s2["entry_price"] = price
            s2["entry_date"] = dt
            actions.append(("strategy2", "BUY"))
            msgs.append(f"🟢 策略2 买入 ¥{price} x {shares}股")
    elif sig2["sell"] and s2["position"]:
        sell_value = s2["shares"] * price * (1 - SLIPPAGE)
        fee = sell_value * COMMISSION
        net = sell_value - fee
        pnl = round(net - s2["shares"] * s2["entry_price"] * (1 + SLIPPAGE), 2)
        s2["capital"] += net
        s2["shares"] = 0
        s2["position"] = False
        emoji = "🟢" if pnl > 0 else "🔴"
        actions.append(("strategy2", "SELL"))
        msgs.append(f"{emoji} 策略2 卖出 ¥{price} 盈亏¥{pnl}")

    total_val = s1["capital"] + (s1["shares"] * price if s1["position"] else 0) \
              + s2["capital"] + (s2["shares"] * price if s2["position"] else 0)
    total_return = round((total_val - CAPITAL * 2) / (CAPITAL * 2) * 100, 2)
    return actions, msgs, state, round(total_val, 2), total_return


# ════════════════════════════════════════
# 主入口
# ════════════════════════════════════════

def main():
    today = datetime.now().strftime("%Y-%m-%d")
    output = {
        "date": today,
        "is_trading_day": True,
        "data_source": "spot_scan",  # spot_scan | hist_scan
        "watchlist_scan": [],
        "zhonglian": None,
        "zhonglian_actions": [],
        "zhonglian_messages": [],
        "summary": {"strong_buy": 0, "watch": 0, "neutral": 0, "caution": 0, "strong_sell": 0, "fail_count": 0},
        "market_overview": {},
    }

    # 不再硬拦截非交易日：周末/节假日跑时取最近交易日(hq.sinajs.cn)数据，
    # 仅标记 is_trading_day + data_date 供 agent 正确解读数据日期
    if not is_trading_day():
        output["is_trading_day"] = False

    # ── Step 1: 快速spot扫描(hq.sinajs.cn) ──
    spot_data, data_date = get_spot_scan()
    output["data_date"] = data_date or today

    # ── Step 2: 尝试获取历史数据做完整信号计算（可选增强）──
    # 先试抓第一只股票的历史数据看接口是否可用
    test_df = get_daily_hist("000157", "中联重科", start_date="20260725")
    use_full = test_df is not None and len(test_df) >= 2

    if use_full:
        log.info("📡 历史接口可用，进行完整技术指标扫描...")
        output["data_source"] = "hist_scan"
        for item in spot_data:
            if "error" in item:
                output["watchlist_scan"].append(item)
                output["summary"]["fail_count"] += 1
                continue
            sym = item["symbol"]
            df = get_daily_hist(sym, item["name"])
            if df is not None and len(df) >= 60:
                try:
                    sig = calc_full_signal(df)
                    sig["symbol"] = sym
                    sig["name"] = item["name"]
                    output["watchlist_scan"].append(sig)
                    lv = sig["signal"]["level"]
                    if "强烈买入" in lv: output["summary"]["strong_buy"] += 1
                    elif "关注" in lv: output["summary"]["watch"] += 1
                    elif "中性" in lv: output["summary"]["neutral"] += 1
                    elif "谨慎" in lv: output["summary"]["caution"] += 1
                    elif "强烈卖出" in lv: output["summary"]["strong_sell"] += 1
                except Exception as e:
                    output["summary"]["fail_count"] += 1
                    output["watchlist_scan"].append({**item, "error": str(e)})
            else:
                # 历史数据不够就用 spot 信号
                sig = calc_spot_signal(item)
                sig["symbol"] = sym
                sig["name"] = item["name"]
                sig["data_source"] = "spot"
                output["watchlist_scan"].append(sig)
                lv = sig["signal"]["level"]
                if "强烈买入" in lv: output["summary"]["strong_buy"] += 1
                elif "关注" in lv: output["summary"]["watch"] += 1
                elif "中性" in lv: output["summary"]["neutral"] += 1
                elif "谨慎" in lv: output["summary"]["caution"] += 1
                elif "强烈卖出" in lv: output["summary"]["strong_sell"] += 1
    else:
        log.info("📡 历史接口不可用，仅用spot数据做快速扫描")
        for item in spot_data:
            if "error" in item:
                output["watchlist_scan"].append(item)
                output["summary"]["fail_count"] += 1
                continue
            sig = calc_spot_signal(item)
            sig["symbol"] = item["symbol"]
            sig["name"] = item["name"]
            sig["data_source"] = "spot"
            output["watchlist_scan"].append(sig)
            lv = sig["signal"]["level"]
            if "强烈买入" in lv: output["summary"]["strong_buy"] += 1
            elif "关注" in lv: output["summary"]["watch"] += 1
            elif "中性" in lv: output["summary"]["neutral"] += 1
            elif "谨慎" in lv: output["summary"]["caution"] += 1
            elif "强烈卖出" in lv: output["summary"]["strong_sell"] += 1

    # ── 市场概览 ──
    up = sum(1 for s in spot_data if "error" not in s and s["change_pct"] > 0)
    down = sum(1 for s in spot_data if "error" not in s and s["change_pct"] < 0)
    total_amt = round(sum(s.get("amount", 0) for s in spot_data if "error" not in s) / 1e8, 2)
    output["market_overview"] = {"up": up, "down": down, "total": len(spot_data), "total_amount_billion": total_amt}

    # ── 中联重科双策略 ──
    log.info("📡 分析中联重科双策略...")
    if test_df is not None and len(test_df) >= 2:
        try:
            # 如果之前只抓了3天数据，需要再抓完整数据
            zl_full = get_daily_hist("000157", "中联重科", start_date="20240101")
            if zl_full is None or len(zl_full) < 120:
                zl_full = test_df
            zl_sig = analyze_zhonglian(zl_full)
            state = load_state()
            if zl_sig["date"] != state.get("last_signal_date") and zl_sig["date"] <= today:
                state["last_signal_date"] = zl_sig["date"]
                actions, msgs, state, total_val, total_ret = execute_trade(state, zl_sig)
                output["zhonglian_actions"] = actions
                output["zhonglian_messages"] = msgs
            else:
                s1 = state["strategy1"]
                s2 = state["strategy2"]
                total_val = s1["capital"] + (s1["shares"] * zl_sig["price"] if s1["position"] else 0) \
                          + s2["capital"] + (s2["shares"] * zl_sig["price"] if s2["position"] else 0)
                total_ret = round((total_val - CAPITAL * 2) / (CAPITAL * 2) * 100, 2)
            save_state(state)
            output["zhonglian"] = {**zl_sig, "portfolio": {
                "strategy1": {"position": state["strategy1"]["position"], "entry_price": state["strategy1"]["entry_price"],
                              "entry_date": state["strategy1"]["entry_date"], "capital": round(state["strategy1"]["capital"], 2),
                              "shares": state["strategy1"]["shares"]},
                "strategy2": {"position": state["strategy2"]["position"], "entry_price": state["strategy2"]["entry_price"],
                              "entry_date": state["strategy2"]["entry_date"], "capital": round(state["strategy2"]["capital"], 2),
                              "shares": state["strategy2"]["shares"]},
                "total_value": round(total_val, 2), "total_return_pct": total_ret,
            }}
        except Exception as e:
            log.error(f"中联重科分析失败: {e}")
            output["zhonglian"] = {"error": str(e)}

    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
