#!/usr/bin/env python3
"""
平高电气(600312) 单只深度回测报告
==================================
基于关注池分配的最优策略(纯MACD)做:
  1. 逐年收益/夏普/回撤/交易数
  2. 每笔交易完整复盘(BUY/SELL/盈亏)
  3. 5策略逐年对比(验证纯MACD是否稳健)
  4. 最新持仓状态与信号
数据: baostock 前复权, 独立进程拉取
"""

import os, sys, json, warnings, datetime, subprocess
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
WORKSPACE = "/Users/duguke/.openclaw/workspace"
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

from validate_strategies import slice_window
from backtest_strategies import run_simulation, ALL_STRATEGIES

SYMBOL, NAME = "600312", "平高电气"
CSV = f"/tmp/gh_{SYMBOL}.csv"
START, END = "2020-01-01", "2026-08-29"

def fetch_baostock(symbol, out):
    """独立子进程拉数据, 避免 baostock 会话连接断"""
    prefix = f"sh.{symbol}" if symbol.startswith("6") else f"sz.{symbol}"
    code = f'''
import sys, warnings, baostock as bs, pandas as pd
warnings.filterwarnings('ignore')
lg = bs.login()
if lg.error_code != '0': print("LOGIN_FAIL"); sys.exit(2)
rs = bs.query_history_k_data_plus(
    "{prefix}", "date,open,high,low,close,volume,amount,adjustflag",
    start_date="{START}", end_date="{END}", frequency="d", adjustflag="2")
rows = []
while (rs.error_code == '0') and rs.next():
    rows.append(rs.get_row_data())
bs.logout()
if rs.error_code != '0': print("QUERY_FAIL"); sys.exit(3)
if len(rows) < 150: print(f"TOO_FEW {{len(rows)}}"); sys.exit(4)
df = pd.DataFrame(rows, columns=["date","open","high","low","close","volume","amount","adjustflag"])
for c in ["open","high","low","close","volume","amount"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")
df.to_csv("{out}", index=False)
print(f"OK {{len(df)}}条")
'''
    r = subprocess.run(["python3", "-c", code], capture_output=True, text=True, timeout=120)
    out_line = [l for l in r.stdout.splitlines() if l.startswith(("OK","QUERY","TOO","LOGIN"))]
    print(f"  数据拉取: {out_line[-1] if out_line else r.stdout.strip()[:80]}")
    return os.path.exists(out)

def load_df():
    df = pd.read_csv(CSV)
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    return df

def get_actions(df, sname):
    for n, f in ALL_STRATEGIES:
        if n == sname:
            a = f(df) or []
            return a
    return []

def run_trade_by_trade(df):
    """纯MACD逐笔交易明细"""
    sname = "纯MACD(基准B)"
    actions = get_actions(df, sname)
    # 模拟逐笔
    capital = 1000000.0
    shares = 0
    entry_price = 0
    trades = []
    pending_buy = None
    action_by_date = {a["date"]: a for a in actions}
    for i in range(len(df)):
        row = df.iloc[i]
        dt = str(row["date"].date())
        price = float(row["close"])
        if dt in action_by_date:
            act = action_by_date[dt]
            if act["type"] == "BUY" and shares == 0:
                fee = capital * 0.0003  # 佣金
                available = capital - fee
                shares = int(available / (price * 1.001) / 100) * 100
                if shares >= 100:
                    cost = shares * price * 1.001
                    fee2 = cost * 0.0003
                    capital -= (cost + fee2)
                    entry_price = price
                    pending_buy = {"date": dt, "price": price, "shares": shares}
            elif act["type"] == "SELL" and shares > 0:
                sell_value = shares * price * 0.999
                fee = sell_value * 0.0003
                net = sell_value - fee
                pnl = net - shares * entry_price * 1.001
                pnl_pct = round((pnl / (shares * entry_price)) * 100, 2)
                hold_days = (pd.to_datetime(dt) - pd.to_datetime(pending_buy["date"])).days if pending_buy else 0
                trades.append({
                    "buy_date": pending_buy["date"] if pending_buy else "",
                    "sell_date": dt,
                    "buy_price": round(entry_price, 2),
                    "sell_price": round(price, 2),
                    "pnl_pct": pnl_pct,
                    "hold_days": hold_days,
                    "reason": act["reason"],
                })
                capital += net
                shares = 0
                pending_buy = None
    return trades, shares, capital

def yearly_breakdown(df, sname):
    """该策略逐年表现"""
    years = {}
    df["year"] = df["date"].dt.year
    actions = get_actions(df, sname)
    for yr, g in df.groupby("year"):
        yr_actions = [a for a in actions if str(a["date"]).startswith(str(yr))]
        m = run_simulation(g, yr_actions)
        years[int(yr)] = m
    return years

def main():
    print(f"📊 {NAME}({SYMBOL}) 深度回测报告生成中...")
    if not os.path.exists(CSV):
        print("  拉取历史数据...")
        fetch_baostock(SYMBOL, CSV)
    df = load_df()
    print(f"  ✅ {len(df)}条日线 ({df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()})")

    report = []
    report.append(f"# {NAME}({SYMBOL}) 深度回测报告")
    report.append(f"\n> 生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report.append(f"> 数据源: baostock 前复权 | 窗口: {START} ~ {END} | 初始资金: 100万")
    report.append(f"> 分配策略: 纯MACD金叉死叉 (来自自适应策略映射)")
    report.append(f"> 最新收盘: {df['date'].iloc[-1].date()} 价格 {df['close'].iloc[-1]:.2f}")

    # === 1. 全周期纯MACD表现 ===
    sname = "纯MACD(基准B)"
    actions = get_actions(df, sname)
    m_all = run_simulation(df, actions)
    report.append("\n## 一、全周期表现 (纯MACD)")
    report.append("| 指标 | 数值 |")
    report.append("|------|------|")
    report.append(f"| 总收益 | **{m_all['total_return_pct']}%** |")
    report.append(f"| 年化收益 | {m_all['annualized_return_pct']}% |")
    report.append(f"| 夏普比率 | {m_all['sharpe_ratio']} |")
    report.append(f"| 年化波动 | {m_all['annualized_volatility_pct']}% |")
    report.append(f"| 最大回撤 | {m_all['max_drawdown_pct']}% |")
    report.append(f"| 胜率 | {m_all['win_rate_pct']}% |")
    report.append(f"| 盈亏比 | {m_all['profit_loss_ratio']} |")
    report.append(f"| 总交易 | {m_all['total_trades']}笔 |")
    report.append(f"| 最大连亏 | {m_all['max_consecutive_losses']}笔 |")
    report.append(f"| 期末权益 | {m_all['final_equity']:,.0f} |")

    # === 2. 逐年明细 ===
    report.append("\n## 二、逐年表现 (纯MACD)")
    report.append("| 年份 | 收益% | 夏普 | 回撤% | 交易 | 胜率% | 盈亏比 |")
    report.append("|------|-------|------|-------|------|-------|--------|")
    yb = yearly_breakdown(df, sname)
    for yr in sorted(yb):
        m = yb[yr]
        report.append(f"| {yr} | {m['total_return_pct']:.1f} | {m['sharpe_ratio']} | {m['max_drawdown_pct']:.1f} | {m['total_trades']} | {m['win_rate_pct']} | {m['profit_loss_ratio']} |")

    # === 3. 逐笔交易复盘 ===
    trades, shares_left, capital_left = run_trade_by_trade(df)
    report.append(f"\n## 三、逐笔交易复盘 (纯MACD, 共{len(trades)}笔平仓)")
    report.append("| # | 买入日 | 卖出日 | 买价 | 卖价 | 盈亏% | 持有 |")
    report.append("|---|--------|--------|------|------|-------|------|")
    for idx, t in enumerate(trades, 1):
        report.append(f"| {idx} | {t['buy_date']} | {t['sell_date']} | {t['buy_price']} | {t['sell_price']} | **{t['pnl_pct']:+.2f}%** | {t['hold_days']}天 |")
    wins = [t for t in trades if t['pnl_pct'] > 0]
    losses = [t for t in trades if t['pnl_pct'] <= 0]
    win_rate = len(wins)/len(trades)*100 if trades else 0
    avg_win = np.mean([t['pnl_pct'] for t in wins]) if wins else 0
    avg_loss = np.mean([t['pnl_pct'] for t in losses]) if losses else 0
    report.append(f"\n**交易统计**: 盈利{len(wins)}笔 亏损{len(losses)}笔 | 胜率{win_rate:.1f}% | 平均盈利{avg_win:+.2f}% | 平均亏损{avg_loss:+.2f}% | 盈亏比{abs(avg_win/avg_loss) if avg_loss else 0:.2f}")
    if shares_left > 0:
        report.append(f"\n**当前持仓**: {shares_left}股 (持仓中, 未平仓)")

    # === 4. 五策略逐年对比 ===
    report.append("\n## 四、五策略逐年收益对比")
    report.append("验证纯MACD是否是最优且稳健的选择")
    report.append("| 年份 | 布林带+ATR | KDJ+CCI | EMA+OBV | EMA金叉 | **纯MACD** |")
    report.append("|------|-----------|---------|---------|---------|-----------|")
    strategy_names = ["布林带+ATR", "KDJ+CCI", "EMA+OBV", "EMA12/26金叉(基准C)", "纯MACD(基准B)"]
    all_yearly = {}
    for sn in strategy_names:
        all_yearly[sn] = yearly_breakdown(df, sn)
    for yr in sorted(all_yearly[strategy_names[0]]):
        row = [str(yr)]
        for sn in strategy_names:
            m = all_yearly[sn].get(yr, {})
            val = m.get("total_return_pct", "—")
            if isinstance(val, (int, float)):
                mark = "**" if sn == "纯MACD(基准B)" else ""
                row.append(f"{mark}{val:+.1f}%{mark}")
            else:
                row.append("—")
        report.append("| " + " | ".join(row) + " |")

    # === 5. 最新信号 ===
    report.append("\n## 五、当前持仓状态")
    if shares_left > 0:
        avg_cost = (trades[-1]['sell_price'] if trades else None)
        report.append(f"- 当前持有 {shares_left} 股，最新价 {df['close'].iloc[-1]:.2f}")
    else:
        report.append("- 当前空仓（无未平仓持仓）")
    last_action = actions[-1] if actions else None
    if last_action:
        report.append(f"- 最近一次信号: **{last_action['type']}** @ {last_action['date']} ({last_action['reason']})")

    # === 输出 ===
    outfile = os.path.join(WORKSPACE, "analysis", "backtest", f"deep_gh_{SYMBOL}_{datetime.datetime.now().strftime('%Y-%m-%d')}.md")
    os.makedirs(os.path.dirname(outfile), exist_ok=True)
    with open(outfile, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"\n✅ 报告已保存: {outfile}")
    print("="*70)
    print("\n".join(report))

if __name__ == "__main__":
    main()
