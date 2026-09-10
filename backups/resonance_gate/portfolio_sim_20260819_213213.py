#!/usr/bin/env python3
"""
全组合多股模拟盘 (Portfolio Simulator)
=========================================
将模拟盘从"仅中联重科"扩展为"全部关注股"，每只独立建仓/持仓/记账。

- 30只关注股（不含机电B股900925无数据，见备注）
- 每只用 adaptive_strategy_map.json 分配的最优策略（5种）
- 每只独立 10万 初始资金，全仓进出
- 状态持久化: data/portfolio_sim_state.json
- 数据源: baostock 前复权 + 新浪实时补丁

运行方式: python3 analysis/portfolio_sim.py
输出: JSON（供agent解析）+ 人类可读简报
"""
import os, sys, json, time, warnings
from datetime import datetime

# 确保可以从工作区根导入 analysis 包（python3 analysis/portfolio_sim.py 直接运行时
# sys.path[0] 是 analysis/ 目录而非工作区根，需手动加入工作区根）
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)
import pandas as pd
import pandas_ta as ta
import baostock as bs
import numpy as np
import urllib.request

warnings.filterwarnings("ignore")

# 导入护城河因子用于哑铃策略仓位管理
from analysis.moat_factor import calc_moat_score, is_core_moat_stock

# ═══════════════════════════════════════════
# 030 核心模块导入
# ═══════════════════════════════════════════
try:
    from analysis.fatal_risk_detector import FatalRiskDetector
    from analysis.position_sizer import PositionSizer, load_market_context as load_position_context
    from analysis.signal_arbitrator import SignalArbitrator, RawSignal, SignalAction
    HAS_030_MODULES = True
except ImportError as e:
    HAS_030_MODULES = False
    print(f"⚠️ 030模块导入失败: {e}")

WORKSPACE = "/Users/duguke/.openclaw/workspace"
OUTPUT_DIR = os.path.join(WORKSPACE, "analysis", "daily")
STRATEGY_MAP_FILE = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")
STATE_FILE = os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")
TRADES_FILE = os.path.join(WORKSPACE, "data", "portfolio_sim_trades.json")  # 成交流水日志(C方案赛后验证用)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── 30只关注股（机电B股900925 baostock无数据，暂不纳入模拟盘）──
STOCKS = [
    ("002318","久立特材"),("300014","亿纬锂能"),("601066","中信建投"),
    ("600030","中信证券"),("300124","汇川技术"),("601995","中金公司"),
    ("600584","长电科技"),("002156","通富微电"),("002466","天齐锂业"),
    ("600036","招商银行"),("600570","恒生电子"),("605566","福莱蒽特"),
    ("000987","越秀资本"),("603308","应流股份"),("300285","国瓷材料"),
    ("002413","雷科防务"),("688981","中芯国际"),("601865","福莱特"),
    ("000157","中联重科"),("300719","安达维尔"),("601061","中信金属"),
    ("600660","福耀玻璃"),("002335","科华数据"),("601100","恒立液压"),
    ("600160","巨化股份"),
    ("600346","恒力石化"),("000708","中信特钢"),("300748","金力永磁"),
    # ── 2026-08-06 替换: 撤上能电气, 加打印机国产替代龙头 ──
    ("002180","奔图科技"),("300847","中船汉光"),
]

DEFAULT_STRATEGY = "ema_cross"

STRATEGY_LABELS = {
    "bollinger": "📊 布林带+ATR",
    "kdj_cci": "🎯 KDJ+RSI(均值回归)",
    "ema_obv": "📈 EMA+OBV",
    "ema_cross": "💹 EMA12/26",
    "macd": "📉 纯MACD",
}

COMMISSION = 0.0003
SLIPPAGE = 0.001
INITIAL_CAPITAL = 100_000  # 每只初始资金


# ════════════════════════════════════════
# 数据获取（复用 adaptive_trader 稳定链路）
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
            marker = f'hq_str_{prefix}{s}="'
            start = text.find(marker)
            if start < 0:
                continue
            start += len(marker)
            end = text.find("\"", start)
            if end < 0:
                continue
            fields = text[start:end].split(",")
            if len(fields) < 9:
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
                            df = pd.concat([df, pd.DataFrame([{
                                "date": pd.Timestamp(r["date"]),
                                "open": r["open"], "close": r["close"],
                                "high": r["high"], "low": r["low"], "volume": r["volume"],
                            }])], ignore_index=True)
            return df
        except Exception:
            time.sleep(1)
        finally:
            try:
                bs.logout()
            except Exception:
                pass

    if realtime_fallback:
        rt = fetch_today_realtime([symbol])
        if symbol in rt:
            r = rt[symbol]
            return pd.DataFrame([{
                "date": pd.Timestamp(r["date"]),
                "open": r["open"], "close": r["close"],
                "high": r["high"], "low": r["low"], "volume": r["volume"],
            }])
    return None


# ════════════════════════════════════════
# 五策略信号发生器（与 adaptive_trader 完全一致）
# ════════════════════════════════════════

def signal_bollinger_atr(df):
    bb = ta.bbands(df["close"], length=20, std=2)
    bbu = bb[[c for c in bb.columns if "BBU" in c.upper()][0]]
    bbm = bb[[c for c in bb.columns if "BBM" in c.upper()][0]]
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    if float(prev["close"]) <= float(bbu.iloc[-2]) and float(row["close"]) > float(bbu.iloc[-1]):
        action = "买入"; reason = f"布林上轨突破(¥{float(bbu.iloc[-1]):.2f}) ATR={float(atr.iloc[-1]):.2f}"
    elif float(prev["close"]) >= float(bbm.iloc[-2]) and float(row["close"]) < float(bbm.iloc[-1]):
        action = "卖出"; reason = f"跌破中轨(¥{float(bbm.iloc[-1]):.2f})"
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"上轨{float(bbu.iloc[-1]):.2f} 中轨{float(bbm.iloc[-1]):.2f} ATR{float(atr.iloc[-1]):.2f}"}


def signal_kdj_cci(df):
    cci = ta.cci(df["high"], df["low"], df["close"], length=14)
    cci_pct = cci.rolling(60).apply(lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-10) * 100)
    kdj = ta.kdj(df["high"], df["low"], df["close"], length=9, signal=3)
    k, d, j = kdj["K_9_3"], kdj["D_9_3"], kdj["J_9_3"]
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    cpi = float(cci_pct.iloc[-1]) if pd.notna(cci_pct.iloc[-1]) else 50
    j_cross_up = float(j.iloc[-2]) <= float(k.iloc[-2]) and float(j.iloc[-1]) > float(k.iloc[-1])
    j_cross_down = float(j.iloc[-2]) >= float(k.iloc[-2]) and float(j.iloc[-1]) < float(k.iloc[-1])
    if cpi < 20 and j_cross_up:
        action = "买入"; reason = f"CCI低位({cpi:.0f}%)+KDJ金叉 K={float(k.iloc[-1]):.1f}"
    elif cpi > 80 or j_cross_down:
        reasons = []
        if cpi > 80: reasons.append(f"CCI高位({cpi:.0f}%)")
        if j_cross_down: reasons.append("KDJ死叉")
        action = "卖出"; reason = " | ".join(reasons)
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"CCI_pct={cpi:.0f}% K={float(k.iloc[-1]):.1f} J={float(j.iloc[-1]):.1f}"}


def signal_ema_obv(df):
    ema20 = ta.ema(df["close"], length=20)
    obv = ta.obv(df["close"], df["volume"])
    obv_ema20 = ta.ema(obv, length=20)
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    e20 = float(ema20.iloc[-1])
    obv_now, obv_ema = float(obv.iloc[-1]), float(obv_ema20.iloc[-1])
    obv_cross_up = float(obv.iloc[-2]) <= float(obv_ema20.iloc[-2]) and obv_now > obv_ema
    obv_cross_down = float(obv.iloc[-2]) >= float(obv_ema20.iloc[-2]) and obv_now < obv_ema
    if price > e20 and obv_cross_up:
        action = "买入"; reason = f"EMA20上方(¥{e20:.2f}) + OBV上穿均线(量能放大)"
    elif price < e20 or obv_cross_down:
        reasons = []
        if price < e20: reasons.append(f"跌破EMA20(¥{e20:.2f})")
        if obv_cross_down: reasons.append("OBV下穿均线(资金撤离)")
        action = "卖出"; reason = " | ".join(reasons)
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"EMA20={e20:.2f} OBV/EMA={obv_now/obv_ema:.2f}x" if obv_ema > 0 else f"EMA20={e20:.2f}"}


def signal_ema_cross(df):
    ema12 = ta.ema(df["close"], length=12)
    ema26 = ta.ema(df["close"], length=26)
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    e12, e26 = float(ema12.iloc[-1]), float(ema26.iloc[-1])
    pe12, pe26 = float(ema12.iloc[-2]), float(ema26.iloc[-2])
    if pe12 <= pe26 and e12 > e26:
        action = "买入"; reason = f"EMA12({e12:.2f})金叉EMA26({e26:.2f})"
    elif pe12 >= pe26 and e12 < e26:
        action = "卖出"; reason = f"EMA12({e12:.2f})死叉EMA26({e26:.2f})"
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"EMA12={e12:.2f} EMA26={e26:.2f}"}


def signal_macd(df):
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    macd_line = macd_df["MACD_12_26_9"]
    signal_line = macd_df["MACDs_12_26_9"]
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    action, reason = "持有", "无信号"
    m, s = float(macd_line.iloc[-1]), float(signal_line.iloc[-1])
    pm, ps = float(macd_line.iloc[-2]), float(signal_line.iloc[-2])
    if pm <= ps and m > s:
        action = "买入"; reason = f"MACD金叉({m:.4f}/{s:.4f})"
    elif pm >= ps and m < s:
        action = "卖出"; reason = f"MACD死叉({m:.4f}/{s:.4f})"
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"MACD={m-s:.4f} Signal={s:.4f}"}


SIGNAL_FUNCS = {
    "bollinger": signal_bollinger_atr,
    "kdj_cci": signal_kdj_cci,
    "ema_obv": signal_ema_obv,
    "ema_cross": signal_ema_cross,
    "macd": signal_macd,
}


# ════════════════════════════════════════
# 持仓状态管理
# ════════════════════════════════════════

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
        # 补全新股
        for sym, name in STOCKS:
            if sym not in state.get("positions", {}):
                state["positions"][sym] = _new_position(sym, name)
        return state
    state = {"last_signal_date": None, "positions": {}}
    for sym, name in STOCKS:
        state["positions"][sym] = _new_position(sym, name)
    return state


def _new_position(symbol, name):
    return {
        "name": name,
        "strategy": None,          # 由策略映射填充
        "position": False,
        "entry_price": 0,
        "entry_date": None,
        "shares": 0,
        "cash": INITIAL_CAPITAL,
        "total_pl": 0.0,           # 累计已实现盈亏
        "trade_count": 0,
    }


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)


def append_trade(trade: dict):
    """追加一条成交流水到 data/portfolio_sim_trades.json (C方案赛后验证用)。
    trade = {date, symbol, name, action(BUY/SELL), price, shares, pnl, pnl_pct, strategy} """
    log = []
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE) as f:
                log = json.load(f)
        except Exception:
            log = []
    log.append(trade)
    os.makedirs(os.path.dirname(TRADES_FILE), exist_ok=True)
    with open(TRADES_FILE, "w") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def execute_trade(pos, signal, dt, strategy=None):
    """基于信号执行一次买卖，返回 (action, msg)。每次成交追加一条流水到 TRADES_FILE。
    集成哑铃策略仓位管理：
    - 核心仓(护城河≥2.0)：单票上限 15%，总仓位上限 80%
    - 卫星仓(护城河<2.0)：单票上限 5%，总仓位上限 20%
    """
    price = signal["price"]
    action = signal["action"]
    strat_name = strategy or pos.get("strategy")

    # 哑铃策略仓位管理
    symbol = pos.get("_symbol", "")
    moat_score = 0.0
    is_core = False
    try:
        from analysis.moat_factor import calc_moat_score, is_core_moat_stock
        moat_score, _ = calc_moat_score(symbol)
        is_core = is_core_moat_stock(symbol)
    except Exception:
        pass
    
    if is_core:
        max_single_pct = 0.15  # 核心仓单票 15%
        max_total_pct = 0.80   # 核心仓总仓位 80%
        bucket = "core"
    else:
        max_single_pct = 0.05  # 卫星仓单票 5%
        max_total_pct = 0.20   # 卫星仓总仓位 20%
        bucket = "satellite"
    
    INITIAL_TOTAL_CAPITAL = INITIAL_CAPITAL * 30  # 30只股票总资金
    
    # 计算当前bucket已用资金
    # 注意：这里需要遍历state["positions"]，但execute_trade只有pos，需要传入state或全局引用
    # 简化：单票限额基于单只初始资金，总仓位由外层控制
    single_cap = INITIAL_CAPITAL * max_single_pct

    if action == "买入" and not pos["position"]:
        # ── 2026-08-19 三因素共振门控：买入前需通过 情绪+基本面 两维（技术由策略信号确认）──
        try:
            from analysis.three_factor_helper import ResonanceGate
            _ok, _info = ResonanceGate().check_buy(str(symbol))
            if not _ok:
                return "HOLD", (f"⛔ **{pos['name']}({dt}) 买入被三因素门控拦截**\n"
                                 f"{_info.get('reason','')}\n技术信号: {signal['reason']}")
        except Exception:
            pass  # 门控异常时不阻断原买入
        # 单票资金上限
        fee = pos["cash"] * COMMISSION
        available = pos["cash"] - fee
        max_shares_by_cap = int(single_cap / (price * (1 + SLIPPAGE)) / 100) * 100
        shares = int(min(available, single_cap) / (price * (1 + SLIPPAGE)) / 100) * 100
        
        if shares >= 100:
            cost = shares * price * (1 + SLIPPAGE)
            fee2 = cost * COMMISSION
            pos["cash"] -= (cost + fee2)
            pos["shares"] = shares
            pos["position"] = True
            pos["entry_price"] = price
            pos["entry_date"] = dt
            pos["trade_count"] += 1
            pos["bucket"] = bucket  # 记录仓位分类
            append_trade({"date": dt, "symbol": pos.get("_symbol", ""), "name": pos["name"],
                          "action": "BUY", "price": price, "shares": shares,
                          "pnl": 0, "pnl_pct": 0.0, "strategy": strat_name, "bucket": bucket})
            bucket_emoji = "🏰" if bucket == "core" else "🛰️"
            return "BUY", f"{bucket_emoji} **{pos['name']}({dt}) 买入**\n价格 ¥{price:.2f} | {shares}股({bucket}) | 理由: {signal['reason']}"
    elif action == "卖出" and pos["position"]:
        sell_value = pos["shares"] * price * (1 - SLIPPAGE)
        fee = sell_value * COMMISSION
        net = sell_value - fee
        cost_basis = pos["shares"] * pos["entry_price"] * (1 + SLIPPAGE)
        pnl = net - cost_basis
        pos["cash"] += net
        pos["total_pl"] += pnl
        pos["shares"] = 0
        pos["position"] = False
        pos["trade_count"] += 1
        append_trade({"date": dt, "symbol": pos.get("_symbol", ""), "name": pos["name"],
                      "action": "SELL", "price": price, "shares": 0,
                      "pnl": pnl, "pnl_pct": round((pnl / cost_basis) * 100, 2) if cost_basis else 0.0,
                      "strategy": strat_name})
        pnl_pct = round((pnl / cost_basis) * 100, 2) if cost_basis else 0
        emoji = "🟢" if pnl > 0 else "🔴"
        return "SELL", f"{emoji} **{pos['name']}({dt}) 卖出**\n价格 ¥{price:.2f} | 盈亏 {pnl_pct:+.2f}% | 理由: {signal['reason']}"
    return None, None


def calc_position_value(pos, price):
    if pos["position"]:
        return pos["cash"] + pos["shares"] * price
    return pos["cash"]


# ════════════════════════════════════════
# 主入口
# ════════════════════════════════════════

def load_strategy_map():
    strategy_map = {}
    if os.path.exists(STRATEGY_MAP_FILE):
        with open(STRATEGY_MAP_FILE) as f:
            strategy_map = json.load(f)
    return strategy_map


def main():
    today_str = datetime.now().strftime("%Y-%m-%d")
    strategy_map = load_strategy_map()
    state = load_state()

    # ═══════════════════════════════════════════
    # 030 预检：致命风险 + 仓位计划 + 市场上下文
    # ═══════════════════════════════════════════
    fatal_risk = {"fatal_triggered": False, "high_risk_triggered": False}
    position_plan = {"buy_signal_multiplier": 1.0, "single_stock_max_pct": 0.05,
                     "total_limit_pct": 0.5, "total_limit_amount": 1_500_000,
                     "direction_allocation": {}}
    market_context = {"health_score": 5, "market_stage": "震荡筑底", "emotion_cycle": "修复"}
    
    if HAS_030_MODULES:
        try:
            fatal_detector = FatalRiskDetector()
            fatal_risk = fatal_detector.check()
            
            pos_context = load_position_context()
            sizer = PositionSizer(total_capital=3_000_000)
            pos_plan_obj = sizer.calculate(
                market_stage=pos_context["market_stage"],
                emotion_cycle=pos_context["emotion_cycle"],
                health_score=pos_context["health_score"],
                fatal_risk=fatal_risk,
                held_positions=pos_context.get("held_positions"),
                watchlist=[s for s, _ in STOCKS]
            )
            position_plan = {
                "buy_signal_multiplier": pos_plan_obj.buy_signal_multiplier,
                "single_stock_max_pct": pos_plan_obj.single_stock_max_pct,
                "single_stock_max_amount": pos_plan_obj.single_stock_max_amount,
                "total_limit_pct": pos_plan_obj.total_limit_pct,
                "total_limit_amount": pos_plan_obj.total_limit_amount,
                "direction_allocation": pos_plan_obj.direction_allocation,
                "risk_warnings": pos_plan_obj.risk_warnings,
            }
            market_context = pos_context
            print(f"✅ 030预检完成: 仓位上限{position_plan['total_limit_pct']:.1%}, 买入乘数{position_plan['buy_signal_multiplier']:.1f}x")
        except Exception as e:
            print(f"⚠️ 030预检模块运行失败: {e}")
    
    # 填充策略映射到持仓
    for sym, pos in state["positions"].items():
        pos["strategy"] = strategy_map.get(sym, DEFAULT_STRATEGY)

    print(f"📡 全组合模拟盘扫描开始 ({today_str})...")
    all_msgs = []
    trade_count = 0
    errors = []

    # 缓存批量实时价用于避免重复请求（逐只拉取，保持简单）
    results = []

    for sym, name in STOCKS:
        pos = state["positions"].get(sym)
        if pos is None:
            continue
        pos["_symbol"] = sym  # 供成交流水记录 symbol
        strat = pos["strategy"]
        slabel = STRATEGY_LABELS[strat]

        df = fetch_data(sym)
        if df is None or len(df) < 60:
            errors.append(f"{name}({sym}) 数据获取失败")
            results.append({"symbol": sym, "name": name, "error": "数据获取失败",
                            "position": pos["position"], "strategy": slabel})
            continue

        sig_func = SIGNAL_FUNCS[strat]
        try:
            signal = sig_func(df)
        except Exception as e:
            errors.append(f"{name}({sym}) 信号计算失败: {e}")
            results.append({"symbol": sym, "name": name, "error": str(e),
                            "position": pos["position"], "strategy": slabel})
            continue

        # 030 致命风险/高风险防御：强制覆盖信号
        if fatal_risk.get("fatal_triggered", False):
            # 致命风险：强制清仓所有持仓，禁止买入
            if pos["position"]:
                signal["action"] = "卖出"
                signal["reason"] = "☠️ 030致命风险触发：强制清仓"
            else:
                signal["action"] = "持有"
                signal["reason"] = "☠️ 030致命风险触发：禁止买入"
        elif fatal_risk.get("high_risk_triggered", False):
            # 高风险防御：持仓减仓，不开新仓
            if pos["position"]:
                signal["action"] = "卖出"
                signal["reason"] = "🟡 030高风险防御：核心中军≤-3%，减仓止损"
            else:
                signal["action"] = "持有"
                signal["reason"] = "🟡 030高风险防御：不开新仓"

        # 只在新交易日执行交易：
        # 首日(last_signal_date为空)仅初始化记账不建仓（避免历史信号批量建仓导致起点混乱）
        # 之后的交易日仅当数据日期变化时才执行该日信号
        data_date = df["date"].iloc[-1].strftime("%Y-%m-%d")
        if state.get("last_signal_date") is not None and data_date != state.get("last_signal_date"):
            # 030 仓位管理：根据仓位计划动态调整买入资金
            if signal["action"] == "买入" and not pos["position"]:
                # 应用买入信号乘数和单股上限
                max_amount = position_plan.get("single_stock_max_amount", 100_000) * position_plan.get("buy_signal_multiplier", 1.0)
                # 暂存原始现金，执行后恢复（execute_trade 内部会用 pos["cash"]）
                # 这里通过临时调整 pos["cash"] 来限制买入金额
                original_cash = pos["cash"]
                pos["cash"] = min(pos["cash"], max_amount)
                action, msg = execute_trade(pos, signal, data_date, strategy=strat)
                pos["cash"] = original_cash
            elif signal["action"] == "卖出" and pos["position"]:
                action, msg = execute_trade(pos, signal, data_date, strategy=strat)
            else:
                action, msg = None, None
            
            if action:
                trade_count += 1
                all_msgs.append(msg)

        pos_val = calc_position_value(pos, signal["price"])
        pos_pl = pos["total_pl"]
        if pos["position"]:
            pos_pl += (pos["shares"] * signal["price"]) - (pos["shares"] * pos["entry_price"] * (1 + SLIPPAGE))
        pos_return = round((pos_val / INITIAL_CAPITAL - 1) * 100, 2)
        pos_pl_round = round(pos_pl, 2)

        results.append({
            "symbol": sym, "name": name,
            "strategy": slabel,
            "price": signal["price"],
            "position": pos["position"],
            "entry_price": pos["entry_price"] if pos["position"] else 0,
            "entry_date": pos["entry_date"],
            "shares": pos["shares"],
            "cash": round(pos["cash"], 2),
            "value": round(pos_val, 2),
            "unrealized_pl": round(pos_pl_round, 2),
            "return_pct": pos_return,
            "trade_count": pos["trade_count"],
            "signal": signal["action"],
            "reason": signal["reason"],
            # 030 新增字段
            "fatal_risk_override": fatal_risk.get("fatal_triggered", False) or fatal_risk.get("high_risk_triggered", False),
            "position_mult": position_plan.get("buy_signal_multiplier", 1.0),
            "max_buy_amount": round(position_plan.get("single_stock_max_amount", 100_000) * position_plan.get("buy_signal_multiplier", 1.0), 2),
        })

    state["last_signal_date"] = datetime.now().strftime("%Y-%m-%d")
    save_state(state)

    # ── 组合汇总 ──
    total_value = sum(r["value"] for r in results if "value" in r)
    total_initial = INITIAL_CAPITAL * len(STOCKS)
    total_return = round((total_value - total_initial) / total_initial * 100, 2)
    pos_count = sum(1 for r in results if r.get("position"))
    buy_qty = sum(1 for r in results if r.get("signal") == "买入")
    sell_qty = sum(1 for r in results if r.get("signal") == "卖出")

    # 030 仓位使用情况
    core_positions = []
    sat_positions = []
    for r in results:
        if r.get("position"):
            try:
                from analysis.moat_factor import is_core_moat_stock
                if is_core_moat_stock(r["symbol"]):
                    core_positions.append(r)
                else:
                    sat_positions.append(r)
            except:
                sat_positions.append(r)
    
    core_value = sum(r["value"] for r in core_positions)
    sat_value = sum(r["value"] for r in sat_positions)
    core_limit = total_initial * 0.8  # 核心仓总上限 80%
    sat_limit = total_initial * 0.2   # 卫星仓总上限 20%
    
    output = {
        "date": today_str,
        "stock_count": len(STOCKS),
        "portfolio": {
            "total_value": round(total_value, 2),
            "total_initial": total_initial,
            "total_return_pct": total_return,
            "positions_held": pos_count,
            "buy_signals_today": buy_qty,
            "sell_signals_today": sell_qty,
            "trades_executed": trade_count,
            # 030 新增
            "core_positions": len(core_positions),
            "satellite_positions": len(sat_positions),
            "core_value": round(core_value, 2),
            "satellite_value": round(sat_value, 2),
            "core_limit_pct": round(core_value / total_initial * 100, 2) if total_initial > 0 else 0,
            "sat_limit_pct": round(sat_value / total_initial * 100, 2) if total_initial > 0 else 0,
            "fatal_risk_triggered": fatal_risk.get("fatal_triggered", False),
            "high_risk_triggered": fatal_risk.get("high_risk_triggered", False),
            "position_plan": position_plan,
        },
        "errors": errors,
        "details": results,
    }

    print("=====PORTFOLIO_SIM_RESULT=====")
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    print("=====PORTFOLIO_SIM_END=====")

    # ── 人类可读简报 ──
    lines = []
    lines.append(f"💼 **全组合模拟盘日报** ({today_str})")
    lines.append(f"组合总资产: ¥{total_value:,.2f} ({total_return:+.2f}%) | 持仓 {pos_count}/{len(STOCKS)} 只")
    lines.append(f"今日信号: 🟢买入 {buy_qty} | 🔴卖出 {sell_qty} | 实际成交 {trade_count} 笔")
    
    # 030 状态
    if fatal_risk.get("fatal_triggered", False):
        lines.append("☠️ **030致命风险触发**: 强制清仓，禁止买入")
    elif fatal_risk.get("high_risk_triggered", False):
        lines.append("🟡 **030高风险防御**: 核心中军≤-3%，减仓止损，不开新仓")
    else:
        lines.append(f"📐 **030仓位计划**: 总上限{position_plan.get('total_limit_pct', 0):.1%}(¥{position_plan.get('total_limit_amount', 0):,.0f}) | 单股上限{position_plan.get('single_stock_max_pct', 0):.1%}(¥{position_plan.get('single_stock_max_amount', 0):,.0f}) | 买入乘数{position_plan.get('buy_signal_multiplier', 1.0):.1f}x")
        lines.append(f"🏰 **哑铃仓位**: 核心仓{len(core_positions)}只(¥{core_value:,.0f}/{core_limit:,.0f}) | 卫星仓{len(sat_positions)}只(¥{sat_value:,.0f}/{sat_limit:,.0f})")
    lines.append("")

    if all_msgs:
        lines.append("📝 **今日成交**")
        lines.extend(all_msgs)
        lines.append("")

    lines.append("📈 **持仓明细**(按收益率排序)")
    held = sorted([r for r in results if r.get("position")], key=lambda x: -x.get("return_pct", 0))
    if held:
        lines.append("| 股票 | 策略 | 现价 | 成本 | 持仓股 | 收益率 | 仓位类型 |")
        lines.append("|:---|:---|:---:|:---:|:---:|:---:|:---:|")
        for r in held:
            bucket = r.get("bucket", "未知")
            bucket_emoji = "🏰" if bucket == "core" else "🛰️" if bucket == "satellite" else "❓"
            lines.append(f"| {r['name']}({r['symbol']}) | {r['strategy']} | ¥{r['price']:.2f} | ¥{r['entry_price']:.2f} | {r['shares']} | {r['return_pct']:+.2f}% | {bucket_emoji} |")
        lines.append("")

    lines.append("🎯 **今日信号**")
    buys = [r for r in results if r.get("signal") == "买入"]
    sells = [r for r in results if r.get("signal") == "卖出"]
    if buys:
        lines.append("🟢 买入信号:")
        for r in buys:
            max_amt = r.get("max_buy_amount", 0)
            lines.append(f"  • {r['name']}({r['symbol']}) [{r['strategy']}] {r['reason']} | 限额¥{max_amt:,.0f}")
    if sells:
        lines.append("🔴 卖出信号:")
        for r in sells:
            fatal_override = " ☠️" if r.get("fatal_risk_override", False) else ""
            lines.append(f"  • {r['name']}({r['symbol']}) [{r['strategy']}] {r['reason']}{fatal_override}")
    if not buys and not sells:
        lines.append("  今日无买卖信号，全部持有/观望")

    if errors:
        lines.append("")
        lines.append("⚠️ 数据异常:")
        for e in errors:
            lines.append(f"  • {e}")

    print("\n" + "\n".join(lines))

    # 保存日报
    report_path = os.path.join(OUTPUT_DIR, f"{today_str}_portfolio_sim.md")
    with open(report_path, "w") as f:
        f.write(f"# 全组合模拟盘日报 {today_str}\n\n")
        f.write("\n".join(lines))
    print(f"\n📄 日报已保存: {report_path}")


if __name__ == "__main__":
    main()
