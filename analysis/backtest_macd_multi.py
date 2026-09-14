#!/usr/bin/env python3
"""
⑫ MACD 多周期策略回测
=========================
验证「MACD 多周期 + 柱状动能分离 + 裸K 确认」是否显著优于单周期 MACD(12/26/9)。

对应待复核台账 ⑫：MEMORY.md 2026-09-11 新增 —— 《MACD最厲害的用法》
  - 大周期定趋势，小周期找进场点（4H 定方向 + 1H/15min 动能分离过滤 + 裸K 确认）
  - 仅双周期方向一致才入场
  - MACD 是动能过滤器而非圣杯

数据限制说明：
  baostock/DataRouter 提供日线；无稳定分钟级历史接口。
  故本回测用**双时间框架日线近似**：
    - 大周期 = 日线（本级别）
    - 小周期 = 日线的「快线近似」——用 EMA12/26 斜率 + MACD 柱状斜率（HIST 差分）作为动能分离代理
  这是对「多周期」的合规降级近似，回测结论须标注为「日线双框架近似」，
  不等同于真实 60min/15min 多周期，上线前需分钟数据复测。

运行:
  python analysis/backtest_macd_multi.py
输出:
  analysis/backtest/macd_multi_2026-09-14.json
  analysis/backtest/macd_multi_2026-09-14.md
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

# ── 25 只关注股（子任务指定）──
STOCKS = [
    ("600030","中信证券"), ("601066","中信建投"), ("600036","招商银行"),
    ("601995","中金公司"), ("000987","越秀资本"),
    ("600584","长电科技"), ("688981","中芯国际"), ("002156","通富微电"), ("002413","雷科防务"),
    ("300014","亿纬锂能"), ("002466","天齐锂业"),
    ("300285","国瓷材料"), ("603308","应流股份"), ("300124","汇川技术"), ("601100","恒立液压"),
    ("002318","久立特材"), ("300719","安达维尔"), ("002335","科华数据"), ("300748","金力永磁"),
    ("600160","巨化股份"), ("600346","恒力石化"),
    ("000708","中信特钢"),
    ("600660","福耀玻璃"), ("600570","恒生电子"), ("601061","中信金属"),
]


# ════════════════════════════════════════
# 策略 A：单周期纯 MACD（基准 B）
# ════════════════════════════════════════
def strategy_macd_single(df):
    """单周期 MACD 金叉死叉（12/26/9）"""
    df = df.copy()
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    df["MACD"] = macd["MACD_12_26_9"]
    df["MACDs"] = macd["MACDs_12_26_9"]
    df["HIST"] = macd["MACDh_12_26_9"]

    actions, position = [], False
    for i in range(30, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date()); price = float(row["close"])
        m, s = float(row["MACD"]), float(row["MACDs"])
        pm, ps = float(prev["MACD"]), float(prev["MACDs"])
        cross_up = pm <= ps and m > s
        cross_down = pm >= ps and m < s
        if not position and cross_up:
            actions.append({"date": dt, "type": "BUY", "price": price,
                            "reason": f"MACD金叉({m:.4f}/{s:.4f})"})
            position = True
        elif position and cross_down:
            actions.append({"date": dt, "type": "SELL", "price": price,
                            "reason": f"MACD死叉({m:.4f}/{s:.4f})"})
            position = False
    return actions


# ════════════════════════════════════════
# 策略 B：MACD 多周期（大周期趋势 + 小周期动能分离 + 裸K 确认）
# ════════════════════════════════════════
def strategy_macd_multi(df):
    """
    MACD 多周期（日线双框架近似）：
      【大周期】日线 MACD 金叉且 HIST>0 且 HIST 递增 → 多头趋势确立
      【小周期】以 EMA12/EMA26 斜率 + HIST 斜率(lookback=3差分) 作为动能分离代理：
                要求快线斜率向上、HIST 斜率 ≥ 0（动能未衰减）
      【裸K】  实体 > 影线（实体占比 > 50%），且无长上影（上影 < 实体 1.0 倍）
      入场：三者一致 → BUY；HIST 转负 或 大周期死叉 → SELL
    """
    df = df.copy()
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    df["MACD"] = macd["MACD_12_26_9"]
    df["MACDs"] = macd["MACDs_12_26_9"]
    df["HIST"] = macd["MACDh_12_26_9"]
    df["EMA12"] = ta.ema(df["close"], length=12)
    df["EMA26"] = ta.ema(df["close"], length=26)

    actions, position = [], False
    LB = 3  # 动能分离回看

    for i in range(max(30, LB + 5), len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        dt = str(row["date"].date())
        price = float(row["close"])

        m, s = float(row["MACD"]), float(row["MACDs"])
        pm, ps = float(prev["MACD"]), float(prev["MACDs"])
        hist = float(row["HIST"])
        hist_lb = float(df.iloc[i - LB]["HIST"])
        e12, e26 = float(row["EMA12"]), float(row["EMA26"])
        pe12, pe26 = float(prev["EMA12"]), float(prev["EMA26"])

        # ── 大周期趋势：日线 MACD 在信号线上方 且 HIST>0 ──
        big_bull = m > s and hist > 0
        big_cross_up = pm <= ps and m > s
        big_cross_down = pm >= ps and m < s

        # ── 小周期动能分离（代理）：快线斜率向上 + HIST 不衰减 ──
        fast_slope_up = (e12 - pe12) > 0
        hist_rising = (hist - hist_lb) >= 0
        momentum_ok = fast_slope_up and hist_rising

        # ── 裸K 确认：实体占比 > 50% 且 上影 < 实体 ──
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), price
        rng = h - l
        body = abs(c - o)
        upper_shadow = h - max(o, c)
        body_ratio = body / rng if rng > 0 else 0
        bare_k_ok = body_ratio > 0.5 and upper_shadow < max(body, 1e-9)

        if not position:
            # 入场需：大周期多头 + 小周期动能确认 + 裸K健康 + 刚刚金叉（避免追高）
            if big_bull and momentum_ok and bare_k_ok and (big_cross_up or (m > s and hist > 0 and (hist - float(df.iloc[i-1]["HIST"])) > 0)):
                actions.append({"date": dt, "type": "BUY", "price": price,
                                "reason": (f"MACD多周期共振: 日线多头(HIST={hist:.4f}) + "
                                           f"动能分离OK + 裸K实体{body_ratio*100:.0f}%")})
                position = True
        else:
            # 离场：大周期死叉 或 HIST 转负（动能衰竭）
            if big_cross_down or hist < 0:
                why = "日线MACD死叉" if big_cross_down else f"HIST转负({hist:.4f})动能衰竭"
                actions.append({"date": dt, "type": "SELL", "price": price, "reason": why})
                position = False
    return actions


# ════════════════════════════════════════
# 模拟器（与 backtest_strategies.py 口径一致）
# ════════════════════════════════════════
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

        equity_curve.append({"date": dt, "equity": capital + shares * price,
                             "position": shares > 0})

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

    streak = max_streak = 0
    for t in trades:
        if t["type"].startswith("SELL"):
            if t.get("pnl", 0) <= 0:
                streak += 1; max_streak = max(max_streak, streak)
            else:
                streak = 0

    return {
        "total_return_pct": round(total_return, 2),
        "annualized_return_pct": round(ann, 2),
        "annualized_volatility_pct": round(vol, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(mdd, 2),
        "win_rate_pct": round(win_rate, 1),
        "profit_loss_ratio": round(pl_ratio, 2),
        "max_consecutive_losses": max_streak,
        "total_trades": len(closed),
        "final_equity": round(eq[-1], 2),
        "trades": trades,
    }


def fetch_data(symbol, name, start, end, max_retry=3):
    from analysis.data_layer.router import get_router
    router = get_router()
    for attempt in range(max_retry):
        try:
            df = router.get_daily(symbol, name, start_date=start)
            if df is not None and len(df) >= 100:
                return df
        except Exception as e:
            print(f"    ⚠️ {name}({symbol}) 尝试{attempt+1}失败: {e}")
        time.sleep(1)
    return None


def main():
    today = "2026-09-14"
    print(f"🚀 ⑫ MACD 多周期回测 | {START_DATE} ~ {END_DATE} | {len(STOCKS)} 只")
    print("=" * 70)

    results = []
    for idx, (symbol, name) in enumerate(STOCKS):
        print(f"[{idx+1}/{len(STOCKS)}] {name}({symbol}) 获取数据...")
        df = fetch_data(symbol, name, START_DATE, END_DATE)
        if df is None or len(df) < 100:
            print("  ❌ 数据不足，跳过")
            results.append({"symbol": symbol, "name": name, "error": "数据不足"})
            continue
        # 截断到 END_DATE
        df = df[df["date"] <= pd.to_datetime(END_DATE)].reset_index(drop=True)
        rec = {"symbol": symbol, "name": name, "bars": len(df)}
        for sname, sfunc in [("macd_single", strategy_macd_single),
                             ("macd_multi", strategy_macd_multi)]:
            try:
                acts = sfunc(df)
                met = run_simulation(df, acts)
                rec[sname] = met
            except Exception as e:
                rec[sname] = {"error": str(e)}
        results.append(rec)

    # ── 聚合 ──
    def agg(field):
        out = {"macd_single": [], "macd_multi": []}
        for r in results:
            if "error" in r:
                continue
            for k in out:
                v = r.get(k, {}).get(field)
                if v is not None:
                    out[k].append(v)
        return out

    summary = {}
    for s in ["macd_single", "macd_multi"]:
        vals = [r[s] for r in results if "error" not in r and "error" not in r.get(s, {})]
        if not vals:
            continue
        summary[s] = {
            "avg_return_pct": round(float(np.mean([v["total_return_pct"] for v in vals])), 2),
            "avg_annualized_pct": round(float(np.mean([v["annualized_return_pct"] for v in vals])), 2),
            "avg_sharpe": round(float(np.mean([v["sharpe_ratio"] for v in vals])), 3),
            "avg_mdd_pct": round(float(np.mean([v["max_drawdown_pct"] for v in vals])), 2),
            "avg_win_rate_pct": round(float(np.mean([v["win_rate_pct"] for v in vals])), 1),
            "avg_pl_ratio": round(float(np.mean([v["profit_loss_ratio"] for v in vals])), 2),
            "avg_trades": round(float(np.mean([v["total_trades"] for v in vals])), 1),
            "n_stocks": len(vals),
        }

    # 逐股对比
    per_stock = []
    for r in results:
        if "error" in r:
            per_stock.append({"symbol": r["symbol"], "name": r["name"], "error": r["error"]})
            continue
        s1 = r.get("macd_single", {}); s2 = r.get("macd_multi", {})
        row = {"symbol": r["symbol"], "name": r["name"], "bars": r["bars"]}
        for tag, sd in [("single", s1), ("multi", s2)]:
            if "error" not in sd:
                row[f"{tag}_return"] = sd["total_return_pct"]
                row[f"{tag}_sharpe"] = sd["sharpe_ratio"]
                row[f"{tag}_mdd"] = sd["max_drawdown_pct"]
                row[f"{tag}_trades"] = sd["total_trades"]
                row[f"{tag}_win"] = sd["win_rate_pct"]
        if row.get("single_return") is not None and row.get("multi_return") is not None:
            row["win"] = "multi" if row["multi_return"] > row["single_return"] else "single"
        per_stock.append(row)

    win_multi = sum(1 for r in per_stock if r.get("win") == "multi")
    win_single = sum(1 for r in per_stock if r.get("win") == "single")
    valid = [r for r in per_stock if "single_return" in r and "multi_return" in r]

    payload = {
        "date": today,
        "window": {"start": START_DATE, "end": END_DATE},
        "n_stocks": len(STOCKS),
        "n_valid": len(valid),
        "note": "日线双框架近似：小周期用 EMA12/26 斜率 + MACD HIST 斜率代理，非真实60min/15min；上线前需分钟数据复测",
        "summary": summary,
        "head_to_head": {"multi_wins": win_multi, "single_wins": win_single},
        "per_stock": per_stock,
    }

    json_path = os.path.join(OUTPUT_DIR, f"macd_multi_{today}.json")
    with open(json_path, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # ── Markdown 报告 ──
    L = []
    L.append(f"# ⑫ MACD 多周期回测报告 {today}\n")
    L.append(f"窗口: {START_DATE} ~ {END_DATE} | 股票池: {len(STOCKS)} 只 | 有效: {len(valid)} 只\n")
    L.append(f"> ⚠️ 数据限制：无分钟级历史，采用**日线双框架近似**"
             f"（小周期 = EMA12/26 斜率 + MACD HIST 斜率代理）。结论为近似验证，"
             f"不代表真实 60min/15min 多周期；上线前须用分钟数据复测。\n")
    L.append("## 总体对比\n")
    L.append("| 指标 | 单周期MACD(12/26/9) | MACD多周期 | 差异 |")
    L.append("|:-----|:-----|:-----|:-----|")
    if "macd_single" in summary and "macd_multi" in summary:
        s1, s2 = summary["macd_single"], summary["macd_multi"]
        rows = [
            ("平均收益%", "avg_return_pct", "%"),
            ("年化收益%", "avg_annualized_pct", "%"),
            ("夏普比率", "avg_sharpe", ""),
            ("最大回撤%", "avg_mdd_pct", "%"),
            ("胜率%", "avg_win_rate_pct", "%"),
            ("盈亏比", "avg_pl_ratio", ""),
            ("平均交易次数", "avg_trades", ""),
        ]
        for label, key, unit in rows:
            a, b = s1[key], s2[key]
            d = b - a
            sign = "+" if d >= 0 else ""
            L.append(f"| {label} | {a}{unit} | {b}{unit} | {sign}{d:.2f}{unit} |")
    L.append("")
    L.append(f"## 逐股对决\n")
    L.append(f"**多周期胜 {win_multi} 只 vs 单周期胜 {win_single} 只**（共 {len(valid)} 只有效）\n")
    L.append("| 股票 | 单周期收益% | 多周期收益% | 单周期夏普 | 多周期夏普 | 单周期回撤% | 多周期回撤% | 单周期交易 | 多周期交易 | 胜者 |")
    L.append("|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
    for r in per_stock:
        if "single_return" not in r:
            continue
        w = "🟢多周期" if r.get("win") == "multi" else "🔴单周期"
        L.append(f"| {r['name']}({r['symbol']}) | {r['single_return']:+.2f} | {r['multi_return']:+.2f} | "
                 f"{r.get('single_sharpe','-')} | {r.get('multi_sharpe','-')} | "
                 f"{r.get('single_mdd','-')} | {r.get('multi_mdd','-')} | "
                 f"{r.get('single_trades','-')} | {r.get('multi_trades','-')} | {w} |")
    L.append("")
    # 结论
    L.append("## 关键结论\n")
    if "macd_single" in summary and "macd_multi" in summary:
        s1, s2 = summary["macd_single"], summary["macd_multi"]
        better_ret = s2["avg_return_pct"] > s1["avg_return_pct"]
        better_sharpe = s2["avg_sharpe"] > s1["avg_sharpe"]
        fewer_trades = s2["avg_trades"] < s1["avg_trades"]
        L.append(f"- **平均收益**：多周期 {s2['avg_return_pct']:+.2f}% vs 单周期 {s1['avg_return_pct']:+.2f}% → "
                 f"{'✅ 多周期更优' if better_ret else '❌ 多周期更差'}")
        L.append(f"- **夏普**：多周期 {s2['avg_sharpe']} vs 单周期 {s1['avg_sharpe']} → "
                 f"{'✅ 多周期更优' if better_sharpe else '❌ 多周期更差'}")
        L.append(f"- **交易频率**：多周期 {s2['avg_trades']:.1f} 次 vs 单周期 {s1['avg_trades']:.1f} 次 → "
                 f"{'过滤信号（更克制）' if fewer_trades else '信号更频繁'}")
        L.append(f"- **逐股对决**：多周期胜 {win_multi} / 单周期胜 {win_single}")
        verdict = (better_ret and better_sharpe)
        L.append(f"\n**是否值得上线**：{'初步值得（近似口径），但须分钟数据复测后再决策' if verdict else '暂不建议上线，近似口径下未跑赢单周期 MACD'}")
        L.append(f"\n> 提醒：单周期 MACD 在 2026-08-18 回测中曾夺冠；本侧重「多周期过滤」"
                 f"是否能在**降低交易频率**的同时保住收益，是评估核心。")

    md_path = os.path.join(OUTPUT_DIR, f"macd_multi_{today}.md")
    with open(md_path, "w") as f:
        f.write("\n".join(L))

    print("\n" + "=" * 70)
    print(f"✅ 完成 | JSON: {json_path}")
    print(f"✅ 完成 | MD:   {md_path}")
    print("\n".join(L[:40]))


if __name__ == "__main__":
    main()
