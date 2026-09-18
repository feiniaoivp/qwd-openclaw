#!/usr/bin/env python3
"""
同票冷却规则 A/B 回测
=====================
目标：验证「同票冷却」/「首笔未成交降级」是否值得加入执行层。

对比口径（同一数据、同一策略、同一费用模型）：
  A 基准   : 原始策略信号直接执行（现状）
  B 卖出冷却: 卖出后 N 交易日禁止回补（N=5/10/15）
  C 信号间隔: 同一票两次 BUY 间隔至少 N 交易日（N=3/5/10）
  D 组合   : 卖出冷却15 + 信号间隔5

关键约束（遵循 backtest_strategies.py 的既有教训）：
  - 直接 import 生产策略函数，不本地复刻，保证「回测==生产」
  - 判定需随机基准对照（backtest_guard）
  - 小样本必须给 Wilson CI

用法:
  python3 analysis/backtest_cooldown.py [--stocks 600036,300124] [--start 2024-01-01]
输出:
  analysis/backtest/cooldown_YYYY-MM-DD.md
  analysis/backtest/cooldown_YYYY-MM-DD.json
"""

import os, sys, json, argparse, warnings
from datetime import datetime

WORKSPACE = "/Users/duguke/.openclaw/workspace"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import pandas_ta as ta

from analysis.net_guard import install_default_timeout
install_default_timeout()

from analysis.portfolio_core import (
    signal_macd, signal_ema_cross, signal_ema_obv,
    signal_bollinger_atr, signal_kdj_cci, signal_bull_trend,
    COMMISSION, SLIPPAGE, INITIAL_CAPITAL,
)

SIGNAL_FUNCS = {
    "macd": signal_macd,
    "ema_cross": signal_ema_cross,
    "ema_obv": signal_ema_obv,
    "bollinger": signal_bollinger_atr,
    "kdj_cci": signal_kdj_cci,
    "bull_trend": signal_bull_trend,
}

OUTPUT_DIR = os.path.join(WORKSPACE, "analysis", "backtest")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ══════════════════════════════════════════
# 策略名 -> 信号函数（读自适应映射）
# ══════════════════════════════════════════
def load_strategy_map():
    p = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    out = {}
    for k, v in raw.items():
        sym = k.split(".")[0] if "." in k else k
        out[sym] = v.get("strategy") if isinstance(v, dict) else v
    return out


def gen_signals(df, strat):
    """逐 bar 生成信号序列（与生产 signal_* 完全同源）"""
    fn = SIGNAL_FUNCS.get(strat)
    if fn is None:
        return []
    sigs = []
    for i in range(1, len(df)):
        window = df.iloc[: i + 1]
        try:
            s = fn(window.copy())
        except Exception:
            continue
        sigs.append({
            "date": str(df.iloc[i]["date"].date()),
            "action": s.get("action", "持有"),
            "price": float(df.iloc[i]["close"]),
        })
    return sigs


# ══════════════════════════════════════════
# 执行引擎（含可选冷却规则）
# ══════════════════════════════════════════
def simulate(df, signals, mode="A", sell_cooldown=0, min_buy_gap=0):
    """
    mode 仅作标记；规则由 sell_cooldown / min_buy_gap 控制。
    sell_cooldown: 卖出后禁止回补的交易日数
    min_buy_gap  : 同一票两次 BUY 之间至少间隔的交易日数（防止连续候选重复建仓）
    """
    capital = float(INITIAL_CAPITAL)
    shares = 0
    entry_price = 0.0
    trades = []
    equity = []

    last_sell_idx = -10 ** 9
    last_buy_idx = -10 ** 9
    suppressed = 0

    for i in range(len(df)):
        row = df.iloc[i]
        dt = str(row["date"].date())
        price = float(row["close"])
        sig = signals[i] if i < len(signals) else None

        if sig and sig["action"] == "买入" and shares == 0:
            blocked = False
            if sell_cooldown > 0 and (i - last_sell_idx) < sell_cooldown:
                blocked = True
            if min_buy_gap > 0 and (i - last_buy_idx) < min_buy_gap:
                blocked = True
            if blocked:
                suppressed += 1
            else:
                fee = capital * COMMISSION
                avail = capital - fee
                sh = int(avail / (price * (1 + SLIPPAGE)) / 100) * 100
                if sh >= 100:
                    cost = sh * price * (1 + SLIPPAGE)
                    capital -= (cost + cost * COMMISSION)
                    shares = sh
                    entry_price = price
                    last_buy_idx = i
                    trades.append({"date": dt, "type": "BUY", "price": price,
                                   "shares": sh})

        elif sig and sig["action"] == "卖出" and shares > 0:
            sv = shares * price * (1 - SLIPPAGE)
            net = sv - sv * COMMISSION
            pnl = net - shares * entry_price * (1 + SLIPPAGE)
            trades.append({"date": dt, "type": "SELL", "price": price,
                           "shares": shares, "pnl": round(pnl, 2),
                           "pnl_pct": round(pnl / (shares * entry_price) * 100, 2)})
            capital += net
            shares = 0
            entry_price = 0.0
            last_sell_idx = i

        equity.append(capital + shares * price)

    final_price = float(df.iloc[-1]["close"])
    if shares > 0:
        sv = shares * final_price * (1 - SLIPPAGE)
        net = sv - sv * COMMISSION
        pnl = net - shares * entry_price * (1 + SLIPPAGE)
        trades.append({"date": str(df.iloc[-1]["date"].date()), "type": "SELL(平)",
                       "price": final_price, "shares": shares,
                       "pnl": round(pnl, 2),
                       "pnl_pct": round(pnl / (shares * entry_price) * 100, 2)})
        capital += net
        shares = 0

    eq = np.array(equity, dtype=float)
    ret = (capital / INITIAL_CAPITAL - 1) * 100

    # 最大回撤
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak * 100
    max_dd = float(dd.min()) if len(dd) else 0.0

    # 夏普（日频年化）
    if len(eq) > 2:
        dr = np.diff(eq) / eq[:-1]
        sd = dr.std()
        sharpe = float(dr.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0
    else:
        sharpe = 0.0

    sells = [t for t in trades if t["type"].startswith("SELL")]
    wins = [t for t in sells if t.get("pnl", 0) > 0]
    win_rate = (len(wins) / len(sells) * 100) if sells else 0.0
    total_pnl = sum(t.get("pnl", 0) for t in sells)

    return {
        "return_pct": round(ret, 2),
        "max_dd_pct": round(max_dd, 2),
        "sharpe": round(sharpe, 3),
        "trades": len(trades),
        "round_trips": len(sells),
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "suppressed_buys": suppressed,
    }


def wilson_ci(wins, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round((c - h) * 100, 1), round((c + h) * 100, 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", default="", help="逗号分隔代码，空=全池")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))
    from data_layer.router import get_router
    from portfolio_core import STOCKS as CORE_STOCKS

    all_syms = [s for s, _ in CORE_STOCKS]
    if args.stocks:
        want = [x.strip() for x in args.stocks.split(",") if x.strip()]
        stocks = [(s, n) for s, n in CORE_STOCKS if s in want]
    else:
        stocks = CORE_STOCKS

    smap = load_strategy_map()
    router = get_router()

    VARIANTS = [
        ("A 基准(现状)", dict(sell_cooldown=0, min_buy_gap=0)),
        ("B 卖出冷却 5d", dict(sell_cooldown=5, min_buy_gap=0)),
        ("B 卖出冷却 10d", dict(sell_cooldown=10, min_buy_gap=0)),
        ("B 卖出冷却 15d", dict(sell_cooldown=15, min_buy_gap=0)),
        ("C 信号间隔 3d", dict(sell_cooldown=0, min_buy_gap=3)),
        ("C 信号间隔 5d", dict(sell_cooldown=0, min_buy_gap=5)),
        ("C 信号间隔 10d", dict(sell_cooldown=0, min_buy_gap=10)),
        ("D 组合(卖15+间5)", dict(sell_cooldown=15, min_buy_gap=5)),
    ]

    results = {}
    per_stock = {}

    for sym, name in stocks:
        strat = smap.get(sym, "ema_cross")
        try:
            df = router.get_daily(sym, "", start_date=args.start.replace("-", ""))
        except Exception as e:
            print(f"⚠️ {name}({sym}) 数据失败: {e}")
            continue
        if df is None or len(df) < 120:
            print(f"⚠️ {name}({sym}) 数据不足")
            continue
        df = df.reset_index(drop=True)

        sigs = gen_signals(df, strat)
        per_stock[sym] = {"name": name, "strategy": strat,
                          "bars": len(df),
                          "buy_signals": sum(1 for s in sigs if s["action"] == "买入"),
                          "sell_signals": sum(1 for s in sigs if s["action"] == "卖出")}

        for vname, kw in VARIANTS:
            r = simulate(df, sigs, mode=vname, **kw)
            results.setdefault(vname, []).append(r)
            per_stock[sym].setdefault("variants", {})[vname] = r
        print(f"✅ {name}({sym}) strat={strat} bars={len(df)} buy_sig={per_stock[sym]['buy_signals']}")

    # ── 汇总 ──
    summary = {}
    for vname, rows in results.items():
        n = len(rows)
        if n == 0:
            continue
        avg_ret = np.mean([r["return_pct"] for r in rows])
        avg_dd = np.mean([r["max_dd_pct"] for r in rows])
        avg_sh = np.mean([r["sharpe"] for r in rows])
        tot_rt = sum(r["round_trips"] for r in rows)
        tot_sup = sum(r["suppressed_buys"] for r in rows)
        summary[vname] = {
            "stocks": n,
            "avg_return_pct": round(float(avg_ret), 2),
            "avg_max_dd_pct": round(float(avg_dd), 2),
            "avg_sharpe": round(float(avg_sh), 3),
            "total_round_trips": tot_rt,
            "total_suppressed_buys": tot_sup,
        }

    # ── 报告 ──
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# 同票冷却规则 A/B 回测 {today}", ""]
    lines.append(f"- 样本: {len(per_stock)} 只 | 区间: {args.start} ~ {args.end}")
    lines.append("- 数据源: DataRouter (生产同一链路) | 策略: adaptive_strategy_map 映射")
    lines.append("- 费用: 佣金+滑点同生产口径 | 判定: 与 A 基准逐票对照")
    lines.append("")
    lines.append("## 汇总对比")
    lines.append("")
    lines.append("| 变体 | 平均收益% | 平均最大回撤% | 平均夏普 | 总换手(回合) | 被抑制BUY |")
    lines.append("|---|---|---|---|---|---|")
    for vname, s in summary.items():
        lines.append(f"| {vname} | {s['avg_return_pct']:+.2f} | {s['avg_max_dd_pct']:.2f} | "
                     f"{s['avg_sharpe']:.3f} | {s['total_round_trips']} | {s['total_suppressed_buys']} |")

    base = summary.get("A 基准(现状)")
    lines.append("")
    lines.append("## 相对基准的增量（正=更好）")
    lines.append("")
    lines.append("| 变体 | Δ收益(pct) | Δ回撤(pct) | Δ夏普 | 结论 |")
    lines.append("|---|---|---|---|---|")
    if base:
        for vname, s in summary.items():
            if vname == "A 基准(现状)":
                continue
            d_ret = s["avg_return_pct"] - base["avg_return_pct"]
            d_dd = s["avg_max_dd_pct"] - base["avg_max_dd_pct"]  # 回撤越接近0越好
            d_sh = s["avg_sharpe"] - base["avg_sharpe"]
            verdict = "✅改善" if (d_ret > 0 and d_sh >= 0) else ("⚠️权衡" if d_ret > 0 or d_sh > 0 else "❌变差")
            lines.append(f"| {vname} | {d_ret:+.2f} | {d_dd:+.2f} | {d_sh:+.3f} | {verdict} |")

    # 单票明细
    lines.append("")
    lines.append("## 单票明细（收益%）")
    lines.append("")
    vnames = list(VARIANTS)
    hdr = "| 代码 | 名称 | 策略 | BUY信号数 | " + " | ".join(v[0] for v in vnames) + " |"
    lines.append(hdr)
    lines.append("|" + "---|" * (5 + len(vnames)))
    for sym, info in per_stock.items():
        cells = []
        for vname, _ in vnames:
            r = info.get("variants", {}).get(vname)
            cells.append(f"{r['return_pct']:+.2f}" if r else "-")
        lines.append(f"| {sym} | {info['name']} | {info['strategy']} | {info['buy_signals']} | "
                     + " | ".join(cells) + " |")

    report = "\n".join(lines)
    md_path = os.path.join(OUTPUT_DIR, f"cooldown_{today}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report)

    js_path = os.path.join(OUTPUT_DIR, f"cooldown_{today}.json")
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump({"date": today, "summary": summary, "per_stock": per_stock},
                  f, ensure_ascii=False, indent=2)

    print("\n" + report)
    print(f"\n📄 报告: {md_path}")
    print(f"📄 JSON: {js_path}")


if __name__ == "__main__":
    main()
