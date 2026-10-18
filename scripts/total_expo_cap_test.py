#!/usr/bin/env python3
"""组合总仓位上限执法（_apply_total_expo_cap）契约测试（2026-10-18）。

口径来源: analysis/portfolio_core._apply_total_expo_cap
背景: 约束层盘点发现 030 total_limit_amount 无消费方（哑铃 80%+20%=100% 可满仓）
→ 2026-10-18 用户拍板接入买入链。本测试锁定执法语义与 fail-open 边界。
跑法: python3 scripts/total_expo_cap_test.py   (全过 exit 0，任一失败 exit 1)
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


def mk_pos(sym, mv=None):
    if mv is None:
        return {"_symbol": sym, "name": sym, "position": False, "shares": 0,
                "entry_price": 0, "cash": 100_000, "bucket": "satellite",
                "total_pl": 0, "trade_count": 0}
    shares = int(mv / 10)
    return {"_symbol": sym, "name": sym, "position": True, "shares": shares,
            "entry_price": 10, "_last_mv": mv, "cash": 0,
            "bucket": "satellite", "total_pl": 0, "trade_count": 1}


PLAN = {"single_stock_max_amount": 100_000, "buy_signal_multiplier": 1.0,
        "total_limit_amount": 1_000_000, "total_limit_pct": 0.25}


def run(label, pos, state, plan, expect_block, mv_map=None, action="买入", enforce=True):
    old = pc.TOTAL_LIMIT_ENFORCE
    pc.TOTAL_LIMIT_ENFORCE = enforce
    try:
        sig = {"action": action, "reason": "sig", "price": 10.0}
        sig2, ok = pc._apply_total_expo_cap(sig, pos, state, True, "T9", "T",
                                            mv_map=mv_map, position_plan=plan)
        blocked = (sig2["action"] == "持有") or (not ok)
        check(blocked == expect_block,
              f"{label} (期望{'拦' if expect_block else '放'}, 实{'拦' if blocked else '放'})")
    finally:
        pc.TOTAL_LIMIT_ENFORCE = old


# 1 卖出永不拦
run("场景1 卖出信号不拦", mk_pos("A", mv=500_000),
    {"positions": {"A": mk_pos("A", mv=500_000)}}, PLAN, False, action="卖出")

# 2 plan 缺失 → fail-open
run("场景2 plan缺失→fail-open放行", mk_pos("T9"), {"positions": {
    "A": mk_pos("A", mv=900_000)}}, {}, False)

# 3 total_limit_amount<=0 → fail-open
run("场景3 上限<=0→fail-open放行", mk_pos("T9"), {"positions": {
    "A": mk_pos("A", mv=900_000)}}, {"total_limit_amount": 0}, False)

# 4 空组合首笔: 0+est5k <= 100万 → 放行
run("场景4 空组合首笔放行", mk_pos("T9"), {"positions": {}}, PLAN, False)

# 5 市值99.6万 + est5k > 100万 → 拦 （注: 恰好等于上限按「≤通过」语义放行）
run("场景5 逼近上限拦截(exactly=上限放行,超出即拦)", mk_pos("T9"), {"positions": {
    "A": mk_pos("A", mv=600_000), "B": mk_pos("B", mv=396_000)}}, PLAN, True)

# 6 市值50万 + est5k < 100万 → 放行
run("场景6 限内放行", mk_pos("T9"), {"positions": {
    "A": mk_pos("A", mv=500_000)}}, PLAN, False)

# 7 mv_map 当日暴涨顶穿上限（成本口径在限内、当日市值超限）
run("场景7 mv_map当日市值超限→拦", mk_pos("T9"), {"positions": {
    "A": dict(mk_pos("A", mv=990_000), _last_mv=500_000)}}, PLAN, True,
    mv_map={"A": 1_050_000})

# 8 开关回退 TOTAL_LIMIT_ENFORCE=False → 放行
run("场景8 开关关闭→恢复旧行为放行", mk_pos("T9"), {"positions": {
    "A": mk_pos("A", mv=990_000)}}, PLAN, False, enforce=False)

# 9 core 桶 est=15000 敏感性: 市值 99万 → core拦 / satellite放
plan9 = dict(PLAN)
p_core = dict(mk_pos("T9"), bucket="core")
run("场景9a core est1.5万 99万+1.5万>100万→拦", p_core, {"positions": {
    "A": mk_pos("A", mv=990_000)}}, plan9, True)
p_sat = dict(mk_pos("T9"), bucket="satellite")
run("场景9b satellite: 市值98.4万+0.5万<100万→放", p_sat, {"positions": {
    "A": mk_pos("A", mv=984_000)}}, plan9, False)

print()
print(f"合计: {len(PASS)} 过 / {len(FAIL)} 败")
sys.exit(1 if FAIL else 0)
