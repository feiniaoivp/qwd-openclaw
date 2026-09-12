#!/usr/bin/env python3
"""
中联重科(000157) 最优策略盯盘脚本
- 每日获取最新K线
- 计算MACD+RSI<50 / 综合最优 两个策略信号
- 检测信号变化，输出交易指令
- 保持持仓状态跟踪

运行方式: python3 analysis/zhonglian_monitor.py
输出格式: JSON 包含信号、持仓、净值等
"""
import os, sys, json
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, date

WORKSPACE = "/Users/duguke/.openclaw/workspace"
STATE_FILE = os.path.join(WORKSPACE, "data", "zhonglian_state.json")
COMMISSION = 0.0003
SLIPPAGE = 0.001
CAPITAL = 100_000

# 共享策略库 (2026-09-11): 中联重科双策略统一实现, 消除多脚本重复漂移
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))
from strategies import (
    compute_indicators as _compute_indicators,
    zhonglian_strategy1 as _zhonglian_strategy1,
    zhonglian_strategy2 as _zhonglian_strategy2,
)

# ---------- 指标计算 ----------
def calc_ema(s, w): return s.ewm(span=w, adjust=False).mean()
def calc_macd(close, f=12, s=26, sig=9):
    ef = close.ewm(span=f, adjust=False).mean()
    es = close.ewm(span=s, adjust=False).mean()
    dif = ef - es
    dea = dif.ewm(span=sig, adjust=False).mean()
    return dif, dea

def load_state():
    """加载历史持仓状态"""
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

def check_signals(df):
    """计算两个策略的最新信号。

    2026-09-11: 指标计算与信号判定改用共享模块 analysis/strategies.py
    (消除与 service.py / close_scan_v2.py 的三处重复漂移)。
    本地仅保留本脚本特有的 desc 文案格式。
    """
    ind = _compute_indicators(df)
    latest, prev = ind["latest"], ind["prev"]
    latest_date = str(latest["date"].date())

    ema12_now = ind["ema12_now"]
    ema26_now = ind["ema26_now"]
    rsi_now = ind["rsi_now"]

    # 当前持仓方向
    dif_direction = "📗金叉" if ind["dif_now"] > ind["dea_now"] else "📕死叉"
    ema_direction = "📗EMA12>26" if ema12_now > ema26_now else "📕EMA12<26"

    sig1 = _zhonglian_strategy1(ind)
    sig2 = _zhonglian_strategy2(ind)

    return {
        "date": latest_date,
        "price": round(ind["close_now"], 2),
        "change_pct": round((ind["close_now"] / ind["close_prev"] - 1) * 100, 2),
        "volume": int(latest["volume"]),
        "indicators": {
            "MACD_DIF": round(ind["dif_now"], 4),
            "MACD_DEA": round(ind["dea_now"], 4),
            "MACD_state": dif_direction,
            "EMA12": round(ema12_now, 2),
            "EMA26": round(ema26_now, 2),
            "EMA_state": ema_direction,
            "RSI14": round(rsi_now, 1),
        },
        "signals": {
            "strategy1": {
                "name": sig1["name"],
                "buy": sig1["buy"],
                "sell": sig1["sell"],
                "rsi_filter": sig1["rsi_filter"],
                "desc": f"MACD{'金叉' if ind['macd_bull'] else '状态'} + RSI{round(rsi_now,1)} {'<50 ✅买入条件' if (ind['macd_bull'] and rsi_now < 50) else '❌'}"
            },
            "strategy2": {
                "name": sig2["name"],
                "buy": sig2["buy"],
                "sell": sig2["sell"],
                "sell_event": sig2["sell_event"],
                "below_ema26": sig2["below_ema26"],
                "desc": f"MACD金叉:{ind['macd_bull']} EMA12>26:{ema12_now > ema26_now} RSI<60:{rsi_now < 60} 卖出信号:{sig2['sell']}"
            }
        }
    }


def execute_trade_strategy1(state, signals):
    """策略1执行交易 - MACD+RSI<50"""
    s = state["strategy1"]
    sig = signals["signals"]["strategy1"]
    price = signals["price"]
    dt = signals["date"]

    action = None
    msg = None

    if sig["buy"] and not s["position"]:
        # 买入
        fee = s["capital"] * COMMISSION
        available = s["capital"] - fee
        shares = int(available / (price * (1 + SLIPPAGE)) / 100) * 100
        if shares >= 100:
            cost = shares * price * (1 + SLIPPAGE)
            fee2 = cost * COMMISSION
            s["capital"] -= (cost + fee2)
            s["shares"] = shares
            s["position"] = True
            s["entry_price"] = price
            s["entry_date"] = dt
            action = "BUY"
            msg = f"🟢 **策略1 {s['name']} 买入信号**\n价格: ¥{price} | 数量: {shares}股\n理由: MACD金叉 + RSI<50底部区域"

    elif sig["sell"] and s["position"]:
        # 卖出
        sell_value = s["shares"] * price * (1 - SLIPPAGE)
        fee = sell_value * COMMISSION
        net = sell_value - fee
        pnl = net - s["shares"] * s["entry_price"] * (1 + SLIPPAGE)
        pnl_pct = round((pnl / (s["shares"] * s["entry_price"])) * 100, 2)
        hold_days = (datetime.strptime(dt, "%Y-%m-%d") - datetime.strptime(s["entry_date"], "%Y-%m-%d")).days if s["entry_date"] else 0
        s["capital"] += net
        s["shares"] = 0
        s["position"] = False
        emoji = "🟢" if pnl > 0 else "🔴"
        action = "SELL"
        msg = f"{emoji} **策略1 {s['name']} 卖出信号**\n价格: ¥{price} | 盈亏: {pnl_pct}% | 持仓: {hold_days}天"

    return action, msg, state

def execute_trade_strategy2(state, signals):
    """策略2执行交易 - 综合最优"""
    s = state["strategy2"]
    sig = signals["signals"]["strategy2"]
    price = signals["price"]
    dt = signals["date"]

    action = None
    msg = None

    if sig["buy"] and not s["position"]:
        fee = s["capital"] * COMMISSION
        available = s["capital"] - fee
        shares = int(available / (price * (1 + SLIPPAGE)) / 100) * 100
        if shares >= 100:
            cost = shares * price * (1 + SLIPPAGE)
            fee2 = cost * COMMISSION
            s["capital"] -= (cost + fee2)
            s["shares"] = shares
            s["position"] = True
            s["entry_price"] = price
            s["entry_date"] = dt
            action = "BUY"
            indicators = signals["indicators"]
            msg = f"🟢 **策略2 {s['name']} 买入信号**\n价格: ¥{price} | 数量: {shares}股\nEMA12({indicators['EMA12']}) > EMA26({indicators['EMA26']}) RSI({indicators['RSI14']})<60"

    elif sig["sell"] and s["position"]:
        sell_value = s["shares"] * price * (1 - SLIPPAGE)
        fee = sell_value * COMMISSION
        net = sell_value - fee
        pnl = net - s["shares"] * s["entry_price"] * (1 + SLIPPAGE)
        pnl_pct = round((pnl / (s["shares"] * s["entry_price"])) * 100, 2)
        hold_days = (datetime.strptime(dt, "%Y-%m-%d") - datetime.strptime(s["entry_date"], "%Y-%m-%d")).days if s["entry_date"] else 0
        s["capital"] += net
        s["shares"] = 0
        s["position"] = False
        emoji = "🟢" if pnl > 0 else "🔴"
        action = "SELL"
        msg = f"{emoji} **策略2 {s['name']} 卖出信号**\n价格: ¥{price} | 盈亏: {pnl_pct}% | 持仓: {hold_days}天"
        # 说明卖出原因
        sig_desc = ""
        if signals["indicators"]["MACD_state"] == "📕死叉":
            sig_desc += "MACD死叉 "
        if price < signals["indicators"]["EMA26"]:
            sig_desc += f"跌破EMA26({signals['indicators']['EMA26']})"
        if sig_desc:
            msg += f"\n理由: {sig_desc}"

    return action, msg, state

def calc_portfolio_value(state, price):
    """计算总资产"""
    v1 = state["strategy1"]["capital"] + (state["strategy1"]["shares"] * price if state["strategy1"]["position"] else 0)
    v2 = state["strategy2"]["capital"] + (state["strategy2"]["shares"] * price if state["strategy2"]["position"] else 0)
    return round(v1 + v2, 2)

def main():
    try:
        # 获取最新数据
        df = ak.stock_zh_a_daily(symbol='sz000157',
            start_date='20250101',
            end_date=datetime.now().strftime('%Y%m%d'),
            adjust='qfq')
        if df is None or df.empty:
            raise Exception("无数据")

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        # 需要至少3个月数据计算指标
        if len(df) < 120:
            # 补数据
            df_long = ak.stock_zh_a_daily(symbol='sz000157',
                start_date='20240101',
                end_date=datetime.now().strftime('%Y%m%d'),
                adjust='qfq')
            if df_long is not None and not df_long.empty:
                df_long["date"] = pd.to_datetime(df_long["date"])
                df_long = df_long.sort_values("date").reset_index(drop=True)
                df = df_long

    except Exception as e:
        print(json.dumps({"error": f"数据获取失败: {e}"}, ensure_ascii=False))
        return

    signals = check_signals(df)
    state = load_state()
    prev_signals = state.get("last_signal_date")

    # 最新数据日期
    today_str = datetime.now().strftime("%Y-%m-%d")
    data_date = signals["date"]
    
    actions = []
    msgs = []
    has_new = False

    # 只在有数据更新时才执行交易
    if data_date != state.get("last_signal_date") and data_date <= today_str:
        state["last_signal_date"] = data_date
        
        # 策略1
        a1, m1, state = execute_trade_strategy1(state, signals)
        if a1:
            actions.append(("strategy1", a1))
            msgs.append(m1)
            has_new = True

        # 策略2
        a2, m2, state = execute_trade_strategy2(state, signals)
        if a2:
            actions.append(("strategy2", a2))
            msgs.append(m2)
            has_new = True

        # 计算总资产
        total_val = calc_portfolio_value(state, signals["price"])
        initial_total = CAPITAL * 2  # 两个策略各10万
        total_return = round((total_val - initial_total) / initial_total * 100, 2)

        # 构建输出
        output = {
            "date": today_str,
            "data_date": data_date,
            "price": signals["price"],
            "price_change": signals["change_pct"],
            "volume": signals["volume"],
            "indicators": signals["indicators"],
            "signals": signals["signals"],
            "has_new_signal": has_new,
            "actions": actions,
            "messages": msgs,
            "portfolio": {
                "strategy1": {
                    "position": state["strategy1"]["position"],
                    "entry_price": state["strategy1"]["entry_price"],
                    "entry_date": state["strategy1"]["entry_date"],
                    "capital": round(state["strategy1"]["capital"], 2),
                    "shares": state["strategy1"]["shares"],
                    "value": round(state["strategy1"]["capital"] + (state["strategy1"]["shares"] * signals["price"] if state["strategy1"]["position"] else 0), 2),
                },
                "strategy2": {
                    "position": state["strategy2"]["position"],
                    "entry_price": state["strategy2"]["entry_price"],
                    "entry_date": state["strategy2"]["entry_date"],
                    "capital": round(state["strategy2"]["capital"], 2),
                    "shares": state["strategy2"]["shares"],
                    "value": round(state["strategy2"]["capital"] + (state["strategy2"]["shares"] * signals["price"] if state["strategy2"]["position"] else 0), 2),
                },
                "total_value": total_val,
                "total_return_pct": total_return,
            }
        }
    else:
        output = {
            "date": today_str,
            "data_date": data_date,
            "price": signals["price"],
            "has_new_signal": False,
            "note": "无新数据更新",
            "portfolio": {
                "strategy1": {
                    "position": state["strategy1"]["position"],
                    "entry_price": state["strategy1"]["entry_price"],
                    "entry_date": state["strategy1"]["entry_date"],
                    "capital": round(state["strategy1"]["capital"], 2),
                    "shares": state["strategy1"]["shares"],
                },
                "strategy2": {
                    "position": state["strategy2"]["position"],
                    "entry_price": state["strategy2"]["entry_price"],
                    "entry_date": state["strategy2"]["entry_date"],
                    "capital": round(state["strategy2"]["capital"], 2),
                    "shares": state["strategy2"]["shares"],
                },
                "total_value": calc_portfolio_value(state, signals["price"]),
            }
        }

    # 保存状态
    save_state(state)
    
    # 输出JSON（给agent解析用）
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    
    # 输出摘要格式
    print("=====ZHONGLIAN_MONITOR_BRIEF=====")
    if msgs:
        for m in msgs:
            print(m)
    
    # 每日持仓状态简报
    s1 = state["strategy1"]
    s2 = state["strategy2"]
    s1_status = "📗持仓中" if s1["position"] else "📕空仓"
    s2_status = "📗持仓中" if s2["position"] else "📕空仓"
    s1_entry = f"¥{s1['entry_price']}({s1['entry_date']})" if s1["position"] else "-"
    s2_entry = f"¥{s2['entry_price']}({s2['entry_date']})" if s2["position"] else "-"
    print(f"\n📊 **中联重科盯盘简报 {today_str}**")
    print(f"当前价: ¥{signals['price']} ({signals['change_pct']:+.2f}%)")
    print(f"MACD: {signals['indicators']['MACD_state']} | RSI: {signals['indicators']['RSI14']} | EMA12/26: {signals['indicators']['EMA_state']}")
    print(f"策略1(MACD+RSI): {s1_status} {s1_entry}")
    print(f"策略2(综合最优): {s2_status} {s2_entry}")
    pp = output.get("portfolio", {})
    if "total_return_pct" in pp:
        print(f"总资产: ¥{pp['total_value']:,.2f} (总收益: {pp['total_return_pct']:+.2f}%)")
    print("=====ZHONGLIAN_MONITOR_END=====")

if __name__ == "__main__":
    main()
