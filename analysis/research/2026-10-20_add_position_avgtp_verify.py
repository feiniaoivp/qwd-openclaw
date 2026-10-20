#!/usr/bin/env python3
"""
加仓 entry_price 口径验证脚本（只读，不改 state、不写 trades）
=============================================================
用途：验证「已持仓再收到 BUY 信号」时，现行 execute_trade 的行为是
      「覆盖 entry_price（旧口径）」还是「加权均价（建议口径）」，
      并量化 601066 案例（8/19 500股@25.12 → 8/27 500股@25.78 → 8/31 卖@25.60）
      两种口径下卖出 pnl 的差值。

安全性：
  - STATE_FILE / TRADES_FILE 均指向 tempfile，绝不触碰 data/ 下真实文件
  - 只调用 analysis.portfolio_core.execute_trade（读路径），不做任何写盘

用法：
  python3 analysis/research/2026-10-20_add_position_avgtp_verify.py
"""
import os
import sys
import json
import tempfile

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMPDIR = tempfile.mkdtemp(prefix="avg_entry_verify_")
os.environ["BACKTEST_MODE"] = "1"  # 兜底：append_trade 直接 return

import analysis.portfolio_core as pc  # noqa: E402

pc.STATE_FILE = os.path.join(_TMPDIR, "state.json")
pc.TRADES_FILE = os.path.join(_TMPDIR, "trades.json")

COMMISSION = pc.COMMISSION
SLIPPAGE = pc.SLIPPAGE


def _new_pos(symbol="601066", name="中信建投", cash=100_000.0):
    return {
        "name": name, "strategy": "macd", "position": False, "entry_price": 0.0,
        "entry_date": None, "shares": 0, "cash": cash, "total_pl": 0.0,
        "trade_count": 0, "_symbol": symbol, "bucket": "core",
        "_buy_streak": 0, "fib_targets": {},
    }


PLAN = {"single_stock_max_amount": 3_000_000.0, "buy_signal_multiplier": 1.0}


def run_scenario():
    """复刻真实流水：8/19 BUY 500@25.12 → 8/27 BUY 500@25.78 → 8/31 SELL@25.60"""
    pos = _new_pos()
    out = []

    a1, _ = pc.execute_trade(pos, {"action": "买入", "price": 25.12, "reason": "sig"},
                             "2026-08-19", "kdj_cci", PLAN)
    out.append(("2026-08-19 BUY@25.12", a1, dict(pos)))

    a2, _ = pc.execute_trade(pos, {"action": "买入", "price": 25.78, "reason": "sig"},
                             "2026-08-27", "macd", PLAN)
    out.append(("2026-08-27 BUY@25.78", a2, json.loads(json.dumps(pos))))

    a3, _ = pc.execute_trade(pos, {"action": "卖出", "price": 25.60, "reason": "sig"},
                             "2026-08-31", "macd", PLAN)
    out.append(("2026-08-31 SELL@25.60", a3, json.loads(json.dumps(pos))))
    return out


def expected_pnl_weighted(p1, s1, p2, s2, px_sell):
    """加权均价口径下的卖出 pnl（与 execute_trade 卖出分支同口径）"""
    avg = (p1 * s1 + p2 * s2) / (s1 + s2)
    shares = s1 + s2
    net = shares * px_sell * (1 - SLIPPAGE) * (1 - COMMISSION)
    cost = shares * avg * (1 + SLIPPAGE)
    return avg, net, cost, net - cost


def main():
    print(f"[tmp] state={pc.STATE_FILE}\n[tmp] trades={pc.TRADES_FILE}")
    steps = run_scenario()

    print("\n=== 现行 execute_trade 实际轨迹（加仓分支实测）===")
    for label, action, snapshot in steps:
        print(f"{label:26s} action={action!s:5s} position={snapshot['position']!s:5s} "
              f"shares={snapshot['shares']:>5} entry_price={snapshot['entry_price']:.2f} "
              f"cash={snapshot['cash']:.2f} total_pl={snapshot['total_pl']:.4f}")

    final = steps[-1][2]
    print("\n=== 601066 事件对比（同流水）===")
    avg, net, cost, pnl_weighted = expected_pnl_weighted(25.12, 500, 25.78, 500, 25.60)
    print(f"加权均价口径: avg_entry={avg:.4f}  shares=1000  net={net:.4f} cost={cost:.4f} "
          f"pnl={pnl_weighted:.4f}")
    print(f"现行(覆盖/不成交)口径: entry_price={final['shares'] and final['entry_price'] or 25.78:.4f}  "
          f"shares={final['shares']}  total_pl={final['total_pl']:.4f}")
    print(f"差值: {final['total_pl'] - pnl_weighted:+.4f}")

    # 判定
    second = steps[1][2]
    if steps[1][1] is None:
        verdict = "加仓分支不存在：已持仓时的 BUY 信号被 execute_trade 静默丢弃（return None）"
    elif abs(second["entry_price"] - 25.78) < 1e-9:
        verdict = "加仓分支覆盖 entry_price（旧口径）"
    else:
        verdict = f"加仓分支改写 entry_price={second['entry_price']:.4f}（非简单覆盖）"
    print(f"\n判定: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
