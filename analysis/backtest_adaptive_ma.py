#!/usr/bin/env python3
"""
均线自适应专项回测（政权自适应EMA周期）
==========================================
对比：
  1. 固定EMA12/26金叉（基准C）
  2. 政权自适应EMA：
     - 趋势 regime: EMA12/26 (标准)
     - 震荡 regime: EMA5/20 (快速捕捉波段)
     - 弱趋势/夹缝: EMA20/60 (慢速避免假突破)
  3. ADX自适应：ADX>25用快线，ADX<20用慢线

验证：自适应均线是否在不同市场状态下都能保持稳健
"""

import os, sys, json, warnings, time
from datetime import datetime
import pandas as pd
import pandas_ta as ta
import numpy as np

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

warnings.filterwarnings("ignore")

OUTPUT_DIR = os.path.join(WORKSPACE, "analysis", "backtest")
os.makedirs(OUTPUT_DIR, exist_ok=True)

START_DATE = os.environ.get("BT_START", "20240101")
END_DATE = os.environ.get("BT_END", "20260726")
INITIAL_CAPITAL = 100_000
COMMISSION = 0.0003
SLIPPAGE = 0.001

STOCKS = [
    ("600030","中信证券"), ("601066","中信建投"), ("600036","招商银行"),
    ("601995","中金公司"), ("000987","越秀资本"),
    ("600584","长电科技"), ("688981","中芯国际"), ("002156","通富微电"), ("002413","雷科防务"),
    ("300014","亿纬锂能"), ("002466","天齐锂业"),
    ("300285","国瓷材料"), ("603308","应流股份"), ("300124","汇川技术"), ("601100","恒立液压"),
    ("002318","久立特材"), ("300719","安达维尔"), ("002335","科华数据"), ("300748","金力永磁"),
    ("600160","巨化股份"), ("600346","恒力石化"),
    ("000708","中信特钢"),
    ("600660","福耀玻璃"), ("600570","恒生电子"), ("605566","福莱蒽特"), ("000157","中联重科"), ("601061","中信金属"),
]

from analysis.data_layer.router import get_router

# 导入政权检测器
try:
    from analysis.regime_detector import detect_regime
    HAS_REGIME = True
except ImportError:
    HAS_REGIME = False
    print("⚠️ regime_detector 不可用，使用简化ADX自适应")


def strategy_ema_fixed(df):
    """固定 EMA12/26 金叉死叉（基准C）"""
    df = df.copy()
    df["EMA12"] = ta.ema(df["close"], length=12)
    df["EMA26"] = ta.ema(df["close"], length=26)
    actions, position = [], False
    for i in range(1, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date()); price = float(row["close"])
        e12, e26 = float(row["EMA12"]), float(row["EMA26"])
        pe12, pe26 = float(prev["EMA12"]), float(prev["EMA26"])
        cross_up = pe12 <= pe26 and e12 > e26
        cross_down = pe12 >= pe26 and e12 < e26
        if not position and cross_up:
            actions.append({"date": dt, "type": "BUY", "price": price,
                            "reason": f"EMA12({e12:.2f})上穿EMA26({e26:.2f})"})
            position = True
        elif position and cross_down:
            actions.append({"date": dt, "type": "SELL", "price": price,
                            "reason": f"EMA12({e12:.2f})下穿EMA26({e26:.2f})"})
            position = False
    return actions


def strategy_ema_adaptive_regime(df):
    """政权自适应EMA：根据regime_detector切换EMA周期"""
    if not HAS_REGIME:
        return strategy_ema_fixed(df)
    
    df = df.copy()
    actions, position = [], False
    
    for i in range(60, len(df)):
        # 用截至当日的数据判断政权
        sub_df = df.iloc[:i+1].copy()
        regime_res = detect_regime(sub_df)
        
        # 根据政权选择EMA周期
        if regime_res.regime in ("trending_up", "trending_down"):
            fast, slow = 12, 26  # 趋势用标准
        elif regime_res.regime == "choppy":
            fast, slow = 5, 20   # 震荡用快线
        else:  # weak/avoid
            fast, slow = 20, 60  # 夹缝用慢线
        
        # 计算当期EMA
        sub_df[f"EMA{fast}"] = ta.ema(sub_df["close"], length=fast)
        sub_df[f"EMA{slow}"] = ta.ema(sub_df["close"], length=slow)
        
        row, prev = sub_df.iloc[-1], sub_df.iloc[-2]
        dt = str(row["date"].date()); price = float(row["close"])
        ef, es = float(row[f"EMA{fast}"]), float(row[f"EMA{slow}"])
        pef, pes = float(prev[f"EMA{fast}"]), float(prev[f"EMA{slow}"])
        
        cross_up = pef <= pes and ef > es
        cross_down = pef >= pes and ef < es
        
        # 避免交易日重复信号：只在交叉发生当天记录
        if not position and cross_up:
            actions.append({"date": dt, "type": "BUY", "price": price,
                            "reason": f"自适应EMA{fast}/{slow}金叉 regime={regime_res.regime}"})
            position = True
        elif position and cross_down:
            actions.append({"date": dt, "type": "SELL", "price": price,
                            "reason": f"自适应EMA{fast}/{slow}死叉 regime={regime_res.regime}"})
            position = False
    return actions


def strategy_ema_adaptive_adx(df):
    """ADX自适应EMA：ADX>25快线，ADX<20慢线，中间标准"""
    df = df.copy()
    # 预计算ADX
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
    df["ADX"] = adx_df["ADX_14"]
    df["DI_plus"] = adx_df["DMP_14"]
    df["DI_minus"] = adx_df["DMN_14"]
    
    actions, position = [], False
    
    for i in range(30, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date()); price = float(row["close"])
        adx = float(row["ADX"]) if pd.notna(row["ADX"]) else 25
        
        # ADX自适应周期选择
        if adx > 25:
            fast, slow = 8, 21   # 强趋势：更快捕捉
        elif adx < 20:
            fast, slow = 20, 50  # 无趋势/弱趋势：更慢滤噪
        else:
            fast, slow = 12, 26  # 标准
        
        # 计算当前EMA
        ema_fast = ta.ema(df["close"].iloc[:i+1], length=fast).iloc[-1]
        ema_slow = ta.ema(df["close"].iloc[:i+1], length=slow).iloc[-1]
        ema_fast_prev = ta.ema(df["close"].iloc[:i], length=fast).iloc[-1]
        ema_slow_prev = ta.ema(df["close"].iloc[:i], length=slow).iloc[-1]
        
        ef, es = float(ema_fast), float(ema_slow)
        pef, pes = float(ema_fast_prev), float(ema_slow_prev)
        
        cross_up = pef <= pes and ef > es
        cross_down = pef >= pes and ef < es
        
        if not position and cross_up:
            actions.append({"date": dt, "type": "BUY", "price": price,
                            "reason": f"ADX自适应EMA{fast}/{slow}金叉 ADX={adx:.1f}"})
            position = True
        elif position and cross_down:
            actions.append({"date": dt, "type": "SELL", "price": price,
                            "reason": f"ADX自适应EMA{fast}/{slow}死叉 ADX={adx:.1f}"})
            position = False
    return actions


def strategy_ema_adaptive_volatility(df):
    """波动率自适应EMA：ATR percentile 决定周期"""
    df = df.copy()
    df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    df["ATR_pct"] = df["ATR"] / df["close"] * 100
    
    actions, position = [], False
    
    for i in range(60, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date()); price = float(row["close"])
        
        # 计算ATR百分位（过去60天）
        atr_pct_series = df["ATR_pct"].iloc[max(0,i-60):i+1]
        atr_pct = float(row["ATR_pct"]) if pd.notna(row["ATR_pct"]) else atr_pct_series.median()
        atr_percentile = (atr_pct_series < atr_pct).mean() * 100
        
        # 高波动用慢线，低波动用快线
        if atr_percentile > 70:      # 高波动
            fast, slow = 20, 50
        elif atr_percentile < 30:    # 低波动
            fast, slow = 8, 21
        else:
            fast, slow = 12, 26
        
        ema_fast = ta.ema(df["close"].iloc[:i+1], length=fast).iloc[-1]
        ema_slow = ta.ema(df["close"].iloc[:i+1], length=slow).iloc[-1]
        ema_fast_prev = ta.ema(df["close"].iloc[:i], length=fast).iloc[-1]
        ema_slow_prev = ta.ema(df["close"].iloc[:i], length=slow).iloc[-1]
        
        ef, es = float(ema_fast), float(ema_slow)
        pef, pes = float(ema_fast_prev), float(ema_slow_prev)
        
        cross_up = pef <= pes and ef > es
        cross_down = pef >= pes and ef < es
        
        if not position and cross_up:
            actions.append({"date": dt, "type": "BUY", "price": price,
                            "reason": f"波动自适应EMA{fast}/{slow}金叉 ATR%ile={atr_percentile:.0f}%"})
            position = True
        elif position and cross_down:
            actions.append({"date": dt, "type": "SELL", "price": price,
                            "reason": f"波动自适应EMA{fast}/{slow}死叉 ATR%ile={atr_percentile:.0f}%"})
            position = False
    return actions


def run_simulation(df, actions):
    capital = float(INITIAL_CAPITAL)
    shares = 0
    entry_price = 0
    trades = []
    equity_curve = []
    action_map = {a["date"]: a for a in actions}

    for i in range(len(df)):
        row = df.iloc[i]
        dt = str(row["date"].date())
        price = float(row["close"])

        if dt in action_map:
            act = action_map[dt]
            if act["type"] == "BUY" and shares == 0:
                fee = capital * COMMISSION
                available = capital - fee
                shares = int(available / (price * (1 + SLIPPAGE)) / 100) * 100
                if shares >= 100:
                    cost = shares * price * (1 + SLIPPAGE)
                    fee2 = cost * COMMISSION
                    capital -= (cost + fee2)
                    entry_price = price
                    trades.append({"date": dt, "type": "BUY", "price": price,
                                   "shares": shares, "reason": act["reason"]})
            elif act["type"] == "SELL" and shares > 0:
                sell_value = shares * price * (1 - SLIPPAGE)
                fee = sell_value * COMMISSION
                net = sell_value - fee
                pnl = net - shares * entry_price * (1 + SLIPPAGE)
                pnl_pct = round((pnl / (shares * entry_price)) * 100, 2)
                trades.append({"date": dt, "type": "SELL", "price": price,
                               "shares": shares, "pnl": round(pnl, 2),
                               "pnl_pct": pnl_pct, "reason": act["reason"]})
                capital += net
                shares = 0
                entry_price = 0

        equity_curve.append({"date": dt, "equity": capital + shares * price, "position": shares > 0})

    final_price = float(df.iloc[-1]["close"])
    if shares > 0:
        sell_value = shares * final_price * (1 - SLIPPAGE)
        fee = sell_value * COMMISSION
        net = sell_value - fee
        pnl = net - shares * entry_price * (1 + SLIPPAGE)
        pnl_pct = round((pnl / (shares * entry_price)) * 100, 2)
        trades.append({"date": str(df.iloc[-1]["date"].date()), "type": "SELL(平)",
                       "price": final_price, "shares": shares, "pnl": round(pnl, 2),
                       "pnl_pct": pnl_pct, "reason": "期末平仓"})
        capital += net
        shares = 0

    eq = pd.DataFrame(equity_curve)["equity"].values
    if len(eq) < 2:
        return {"error": "数据不足"}

    total_return = ((eq[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    years = len(eq) / 245
    ann = ((1 + total_return / 100) ** (1 / years) - 1) * 100 if years > 0 else 0
    daily = np.diff(eq) / eq[:-1]
    vol = np.std(daily, ddof=1) * np.sqrt(245) * 100
    sharpe = ((ann / 100) - 0.02) / (vol / 100) if vol > 0 else 0
    peak = np.maximum.accumulate(eq)
    mdd = ((eq - peak) / peak * 100).min()

    closed = [t for t in trades if t["type"].startswith("SELL")]
    wins = [t for t in closed if t.get("pnl", 0) > 0]
    win_rate = len(wins) / len(closed) * 100 if closed else 0
    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
    losses = [t for t in closed if t.get("pnl", 0) <= 0]
    avg_loss = abs(np.mean([t["pnl"] for t in losses])) if losses else 1
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else 0

    return {
        "total_return_pct": round(total_return, 2),
        "annualized_return_pct": round(ann, 2),
        "annualized_volatility_pct": round(vol, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(mdd, 2),
        "win_rate_pct": round(win_rate, 1),
        "profit_loss_ratio": round(pl_ratio, 2),
        "total_trades": len(closed),
        "final_equity": round(eq[-1], 2),
    }


def fetch_data(symbol, name, start=START_DATE, end=END_DATE):
    router = get_router()
    try:
        df = router.get_daily(symbol, name, start_date=start)
        if df is not None and len(df) >= 100:
            return df
    except Exception as e:
        print(f"  ⚠️ {name}({symbol}) DataRouter获取失败: {e}")
    return None


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"🚀 均线自适应专项回测 | {START_DATE} ~ {END_DATE} | {len(STOCKS)} 只")
    print("=" * 70)

    STRATEGIES = {
        "ema_fixed": ("固定EMA12/26", strategy_ema_fixed),
        "ema_adaptive_regime": ("政权自适应EMA", strategy_ema_adaptive_regime),
        "ema_adaptive_adx": ("ADX自适应EMA", strategy_ema_adaptive_adx),
        "ema_adaptive_vol": ("波动率自适应EMA", strategy_ema_adaptive_volatility),
    }

    results = []
    for idx, (symbol, name) in enumerate(STOCKS):
        print(f"[{idx+1}/{len(STOCKS)}] {name}({symbol}) 获取数据...")
        df = fetch_data(symbol, name)
        if df is None or len(df) < 100:
            print("  ❌ 数据不足，跳过")
            results.append({"symbol": symbol, "name": name, "error": "数据不足"})
            continue

        df = df[df["date"] <= pd.to_datetime(END_DATE)].reset_index(drop=True)
        stock_result = {"symbol": symbol, "name": name, "strategies": {}}
        
        for skey, (sname, sfunc) in STRATEGIES.items():
            try:
                actions = sfunc(df)
                metrics = run_simulation(df, actions)
                stock_result["strategies"][skey] = {"label": sname, **metrics}
                print(f"  {sname}: {metrics.get('total_return_pct', 'err'):.1f}% | 夏普={metrics.get('sharpe_ratio', 'err'):.3f} | 回撤={metrics.get('max_drawdown_pct', 'err'):.1f}% | 交易={metrics.get('total_trades', 'err')}")
            except Exception as e:
                stock_result["strategies"][skey] = {"label": sname, "error": str(e)}
                print(f"  {sname}: 错误 - {e}")
        
        results.append(stock_result)

    # ── 聚合对比 ──
    print("\n" + "=" * 70)
    print("📊 聚合对比：固定EMA vs 三种自适应EMA")
    
    for skey, (sname, _) in STRATEGIES.items():
        rets, sharpes, mdds, trades, wrs = [], [], [], [], []
        for r in results:
            if "error" in r: continue
            sd = r["strategies"].get(skey, {})
            if "error" in sd: continue
            for key, vals in [("total_return_pct", rets), ("sharpe_ratio", sharpes),
                              ("max_drawdown_pct", mdds), ("total_trades", trades),
                              ("win_rate_pct", wrs)]:
                if key in sd and sd[key] is not None:
                    vals.append(sd[key])
        
        if not rets: continue
        print(f"\n--- {sname} ---")
        print(f"  平均收益: {np.mean(rets):+.2f}% | 夏普: {np.mean(sharpes):.3f} | 最大回撤: {np.mean(mdds):.2f}% | 交易: {np.mean(trades):.1f} | 胜率: {np.mean(wrs):.1f}%")

    # 逐股胜负：自适应vs固定
    for skey in ["ema_adaptive_regime", "ema_adaptive_adx", "ema_adaptive_vol"]:
        sname = STRATEGIES[skey][0]
        win_adapt = win_fixed = 0
        for r in results:
            if "error" in r: continue
            sd_f = r["strategies"].get("ema_fixed", {})
            sd_a = r["strategies"].get(skey, {})
            if "total_return_pct" in sd_f and "total_return_pct" in sd_a:
                if sd_a["total_return_pct"] > sd_f["total_return_pct"]:
                    win_adapt += 1
                else:
                    win_fixed += 1
        print(f"  {sname} vs 固定: 自适应胜 {win_adapt} 只 vs 固定胜 {win_fixed} 只")

    # 保存结果
    payload = {
        "date": today,
        "window": {"start": START_DATE, "end": END_DATE},
        "note": "固定EMA12/26 vs 政权自适应(趋势12/26/震荡5/20/夹缝20/60) vs ADX自适应 vs 波动率自适应",
        "results": results,
    }
    
    json_path = os.path.join(OUTPUT_DIR, f"adaptive_ma_backtest_{today}.json")
    with open(json_path, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✅ 结果已保存: {json_path}")


if __name__ == "__main__":
    main()