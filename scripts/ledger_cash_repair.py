#!/usr/bin/env python3
"""单票 cash 桶污染修复 v3（默认 dry-run）

🔴 归因终版（2026-10-20 第三轮深挖，两次自我推翻）：
  v1 说"8 月漏扣 cash" → ❌ 被 f71ed07 源码证伪（买入分支确有扣款）
  v2 说"state 重建导致" → ❌ 只解释漂移，不解释「实付 cash 低于净付出」

  真凶（f71ed07 `analysis/portfolio_sim.py` 第 551-557 行）：
      original_cash = pos["cash"]
      pos["cash"] = min(pos["cash"], max_amount)      # 临时压缩
      action, msg = execute_trade(pos, signal, ...)    # 内部正确扣款
      pos["cash"] = original_cash                      # ⚠️ 恢复 → 扣款被抹掉
  结果：仓位建了（shares/entry_price/total_pl 全对），但现金没减。

  数值指纹（三票同时满足，误差 <1 分）：
    - 股数按「单票满额本金」成交：恒生 4300×23.18≈99.77k、国瓷 1400×67.27≈94.27k
    - total_pl = 卖出净额 − 真实成本（对应上述股数）✅ 逐分吻合
    - cash = 100,000 + 卖出净额 − **再一次**成本（复现被抹掉的扣款在后续被重放一次）

  为什么表现不一：601061（先卖后买）与 002180（8/19 即被移出关注池）没吃到；
  601066/000987 只吃到「一次」成本。三者都说明本 bug 属**间歇生效**
  （推测与当日 `max_amount`/cash 相对关系或 restore 路径有关），不是全时段全票。

⚠️ 500 端口径：8 月 single_cap = 单票本金全额（≈100k），现行是 5,000/15,000。
   历史交易不能用现行规则复算。

修复口径：空仓票 cash := INITIAL_CAPITAL + total_pl
  total_pl 由卖出分支 `pos["total_pl"] += pnl` 累加，**不读 cash** → 不受本 bug 影响，
  是幸存的核心锚点。

已知残留（本脚本不处理）：
  601066 8/19 BUY 500 股被 8/27 BUY 覆盖 entry_price（25.12→25.78），
  卖出 cost_basis 用错 → total_pl 少记约 -163 元。quirk 至今仍在代码里。

用法：
  python3 scripts/ledger_cash_repair.py            # dry-run
  python3 scripts/ledger_cash_repair.py --apply    # 写入（自动备份 + 留痕）
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

from analysis.portfolio_core import STATE_FILE, INITIAL_CAPITAL  # noqa: E402

TOLERANCE = 50.0


def find_desync(positions):
    items = []
    total_cash = 0.0
    total_truth = 0.0
    for sym, p in positions.items():
        if not isinstance(p, dict):
            continue
        cash = float(p.get("cash", 0) or 0)
        pl = float(p.get("total_pl", 0) or 0)
        total_cash += cash
        total_truth += INITIAL_CAPITAL + pl
        if p.get("position"):
            continue
        diff = (cash - INITIAL_CAPITAL) - pl
        if abs(diff) > TOLERANCE:
            items.append((sym, p.get("name", ""), cash, pl, diff, INITIAL_CAPITAL + pl))
    return items, total_cash, total_truth


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    args = ap.parse_args()

    with open(STATE_FILE) as f:
        state = json.load(f)
    positions = state.get("positions", {})
    items, total_cash, total_truth = find_desync(positions)

    print(f"扫描 {len(positions)} 个单票资金桶，命中脱钩 {len(items)} 项"
          f"（本金 {INITIAL_CAPITAL:,}/票，容忍 {TOLERANCE:.0f} 元）")
    delta = 0.0
    for sym, name, cash, pl, diff, target in items:
        delta += target - cash
        print(f"  {sym} {name}: cash {cash:,.2f} → {target:,.2f} "
              f"(虚增 {diff:+,.2f}, total_pl={pl:+,.2f})")
    print(f"合计账面修正: {delta:+,.2f}")
    print(f"权益口径: 修正前 {total_cash:,.2f} → 修正后 {total_truth:,.2f} "
          f"({(total_truth - 2_700_000) / 2_700_000 * 100:+.2f}% vs 本金 270 万)")

    if not items:
        print("无需修复。")
        return 0
    if not args.apply:
        print("\n[dry-run] 未写入。确认无误后加 --apply。")
        return 0

    bak = f"{STATE_FILE}.pre_cash_repair.{os.getpid()}"
    shutil.copy2(STATE_FILE, bak)
    for sym, name, cash, pl, diff, target in items:
        positions[sym]["cash"] = target
        positions[sym]["_cash_repair"] = {
            "date": "2026-10-20",
            "old_cash": cash,
            "delta": round(target - cash, 2),
            "reason": "f71ed07 portfolio_sim restore-cash bug (buy deduction lost), anchored to total_pl",
        }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✅ 已写入，备份：{bak}")
    print("⚠️ 历史 portfolio_equity.csv 不回填（保留时序证据链），"
          "建议在简报/告警中对本差额做一次性口径说明。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
