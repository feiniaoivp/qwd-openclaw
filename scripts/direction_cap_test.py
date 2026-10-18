#!/usr/bin/env python3
"""方向分配上限契约测试（2026-10-18）。

背景: 约束层盘点发现 position_plan["direction_allocation"] 恒为空 dict
（RiskGuard.refresh 传的 watchlist 为空 → _calc_direction_allocation 直接 return {}）
→ _check_barbell_constraints 第 3 条「方向上限」形同死代码。
修复: refresh 回落 WATCHLIST SSOT 取关注池。本测试锁 3 件事:
  1. alloc 非空（供给端真的产出）
  2. 已用+本笔 > 方向上限 → 拦
  3. alloc 空时 fail-open（保持旧行为，不误拦）
跑法: python3 scripts/direction_cap_test.py   (全过 exit 0)
"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis import portfolio_core as pc

PASS, FAIL = [], []


def check(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("✅" if cond else "❌") + " " + label)


def _pos(direction="券商", sym="600030"):
    return {"_symbol": sym, "position": False, "shares": 0, "entry_price": 0,
            "cash": 100_000, "bucket": "satellite", "direction": direction}


def _plan(alloc):
    return {"single_stock_max_amount": 100_000, "buy_signal_multiplier": 1.0,
            "total_limit_amount": 1_080_000, "direction_allocation": alloc}


SIG = {"action": "买入", "reason": "t", "price": 30.0}


def arm(used_shares, used_price=30.0, direction="券商"):
    return {"positions": {"A": {"_symbol": "A", "position": True,
                                "shares": used_shares, "entry_price": used_price,
                                "cash": 0, "direction": direction}}}


# 1 供给端: 真实 RiskGuard 算出非空 alloc（离线，不依赖网络）
try:
    pc.RiskGuard.refresh(cache_key="dir-cap-test")
    alloc = pc.RiskGuard.get_position_plan().get("direction_allocation") or {}
    check(len(alloc) > 0, f"场景1 方向分配非空供给 (方向数={len(alloc)})")
    check("券商" in alloc and alloc["券商"] > 0, f"场景2 券商方向有额度 (¥{alloc.get('券商', 0):,.0f})")
except Exception as e:
    check(False, f"场景1/2 供给端异常: {e}")

# 3 方向已满 → 拦
plan = _plan({"券商": 90_000.0})
ok, reason = pc._check_barbell_constraints(_pos(), SIG, plan, arm(3000))  # 9万+5k > 9万
check(not ok and "方向" in str(reason), "场景3 方向已满→拦截")

# 4 方向有余量 → 放行
ok, _ = pc._check_barbell_constraints(_pos(), SIG, plan, arm(2000))  # 6万+5k < 9万
check(ok, "场景4 方向有余量→放行")

# 5 alloc 空（fail-open，旧行为）→ 放行
ok, _ = pc._check_barbell_constraints(_pos(), SIG, _plan({}), arm(3000))
check(ok, "场景5 alloc空→fail-open放行")

# 6 未配置方向 → 放行（不因缺映射误拦）
ok, _ = pc._check_barbell_constraints(_pos(direction="未配置方向"), SIG, plan, arm(3000))
check(ok, "场景6 未配置方向→放行")

# 7 卖出不受方向上限影响
ok, _ = pc._check_barbell_constraints(_pos(), {"action": "卖出", "reason": "t", "price": 30.0},
                                     plan, arm(3000))
check(ok, "场景7 卖出不受方向上限影响")

print()
print(f"合计: {len(PASS)} 过 / {len(FAIL)} 败")
sys.exit(1 if FAIL else 0)
