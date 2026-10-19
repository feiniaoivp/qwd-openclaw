#!/usr/bin/env python3
"""单票 cash 桶历史污染修复（草稿，默认 dry-run）

根因（2026-10-20 坐实）：
  2026-08 期间 execute_trade 的买入分支未扣减 pos["cash"]（只记 shares/entry，
  不扣 cost+fee），卖出分支正常回笼 net。因此该时期完成「买→卖」的票，
  cash 桶 = INITIAL_CAPITAL + 卖出净额，凭空多出「一整笔买入成本」。
  019b40f（09-10 F-5 资金口径复核）修了代码（现有 `pos["cash"] -= (cost + fee2)`），
  但**没有回填被污染的存量 cash 桶** → 残留至今，且直接进权益曲线。

指纹判据（对空仓票）：cash ≈ INITIAL_CAPITAL + (SELL.net) 且 cash-本金 != total_pl

修复口径：空仓票 cash := INITIAL_CAPITAL + total_pl（total_pl 由卖出分支正常累计，
本身可信；唯一例外是 601066 这类「买入覆盖 entry_price 导致 cost_basis 失真」的，
其 total_pl 也偏小，但量级仅百元级，见报告）。

用法：
  python3 scripts/ledger_cash_repair.py            # dry-run，只打印将改什么
  python3 scripts/ledger_cash_repair.py --apply    # 真正写入（自动备份 .prefile）
"""
import argparse
import json
import os
import shutil
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.portfolio_core import STATE_FILE, EQUITY_FILE, INITIAL_CAPITAL  # noqa: E402

TOLERANCE = 50.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    args = ap.parse_args()

    with open(STATE_FILE) as f:
        state = json.load(f)
    positions = state.get("positions", {})

    items = []
    for sym, p in positions.items():
        if not isinstance(p, dict) or p.get("position"):
            continue
        cash = float(p.get("cash", 0) or 0)
        pl = float(p.get("total_pl", 0) or 0)
        diff = (cash - INITIAL_CAPITAL) - pl
        if abs(diff) > TOLERANCE:
            items.append((sym, p.get("name", ""), cash, pl, diff, INITIAL_CAPITAL + pl))

    print(f"扫描 {len(positions)} 个单票资金桶，命中污染 {len(items)} 项"
          f"（本金 {INITIAL_CAPITAL:,}/票，容忍 {TOLERANCE:.0f} 元）")
    total_delta = 0.0
    for sym, name, cash, pl, diff, target in items:
        total_delta += target - cash
        print(f"  {sym} {name}: cash {cash:,.2f} → {target:,.2f} "
              f"(虚增 {diff:+,.2f}, total_pl={pl:+,.2f})")
    print(f"合计账面修正: {total_delta:+,.2f}")

    if not items:
        print("无需修复。")
        return 0

    # 影响面：权益曲线是 sum(cash) + 持仓市值，当前全空仓
    print(f"权益口径: 修正前 {sum(float(p.get('cash', 0) or 0) for p in positions.values()):,.2f}"
          f" → 修正后 {sum(float(p.get('cash', 0) or 0) for p in positions.values()) + total_delta:,.2f}")

    if not args.apply:
        print("\n[dry-run] 未写入。确认无误后加 --apply。")
        return 0

    bak = f"{STATE_FILE}.pre_cash_repair.{os.getpid()}"
    shutil.copy2(STATE_FILE, bak)
    for sym, name, cash, pl, diff, target in items:
        positions[sym]["cash"] = target
        positions[sym]["_cash_repair"] = {
            "date": "2026-10-20", "old_cash": cash, "delta": round(target - cash, 2),
            "reason": "pre-F5 buy-side cash not deducted (2026-08 legacy corruption)",
        }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✅ 已写入，备份：{bak}")
    print(f"⚠️ 历史 portfolio_equity.csv 未回填（时序证据链需保留），"
          f"建议在告警/简报里对 {total_delta:+,.2f} 做口径说明。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
