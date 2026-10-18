#!/usr/bin/env python3
"""Tier 分层屏蔽契约测试（2026-10-18）。

背景（约束层盘点红灯4）: Tier2/3 硬编码名单会在信号层静默吞掉买入
（1068 笔买入信号中 220 笔 = 20.6%），评估口径看不到「被吞的那部分赚不赚钱」。
用户拍板改为「配置 + 显式标注」：信号照常生成，被吞时打
  suppressed_by / suppressed_from / suppressed_reason
并追加 data/tier_suppressed_signals.jsonl 供研究统计。

本测试锁 8 件事：
  1. Tier2 空仓买入 → 持有 + suppressed_by=tier2
  2. Tier3 空仓买入 → **必须也拦**（原实现 `pos.get("position")` 使空仓票漏拦）
  3. Tier1 买入不受影响
  4. suppressed_* 字段保真（from/reason 不丢）
  5. 每次屏蔽都追加 jsonl 一条
  6. Tier3 有持仓 → 强制卖出（语义不变）
  7. Tier3 卖出信号不重复标记
  8. 原动作 持有 在 Tier2 下不被改写
跑法: python3 scripts/tier_suppression_test.py   (全过 exit 0)
"""
import os
import sys
import json
import tempfile

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis import portfolio_core as pc

PASS, FAIL = [], []


def check(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("✅" if cond else "❌") + " " + label)


# 隔离落盘文件：把 WORKSPACE 指到临时目录，避免污染真实 data/
tmpdir = tempfile.mkdtemp(prefix="tier_test_")
old_ws = pc.WORKSPACE
pc.WORKSPACE = tmpdir
JLOG = os.path.join(tmpdir, "data", "tier_suppressed_signals.jsonl")


def lines():
    if not os.path.exists(JLOG):
        return []
    with open(JLOG) as f:
        return [json.loads(x) for x in f if x.strip()]


def sig(action="买入", reason="MACD金叉", sym="600030", name="中信证券"):
    return {"action": action, "reason": reason, "price": 10.0, "_symbol": sym, "name": name}


try:
    # 1 Tier2 空仓买入 → 拦 + 标记
    out = pc._apply_tier_filter(sig(), {"position": False}, "tier2")
    check(out["action"] == "持有" and out.get("suppressed_by") == "tier2",
          "场景1 Tier2空仓买入→持有+标记tier2")

    # 2 【核心回归】Tier3 空仓买入必须拦（原实现漏拦）
    out = pc._apply_tier_filter(sig(sym="601995", name="中金公司"), {"position": False}, "tier3")
    check(out["action"] == "持有" and out.get("suppressed_by") == "tier3",
          "场景2 Tier3空仓买入→必须拦截(原实现漏拦)")

    # 3 Tier1 买入不受影响
    out = pc._apply_tier_filter(sig(reason="MACD金叉"), {"position": False}, "tier1")
    check(out["action"] == "买入" and "suppressed_by" not in out,
          "场景3 Tier1买入不受影响")

    # 4 保真字段
    out = pc._apply_tier_filter(sig(reason="EMA金叉+量能放大"), {"position": False}, "tier2")
    check(out.get("suppressed_from") == "买入" and "EMA金叉+量能放大" in out.get("suppressed_reason", ""),
          "场景4 suppressed_from/reason 保真")

    # 5 落盘条数
    recs = lines()
    check(len(recs) == 3, f"场景5 每次屏蔽追加 jsonl (实测 {len(recs)} 条, 期望 3)")
    check(recs and recs[0]["tier"] == "tier2" and recs[0]["symbol"] == "600030",
          "场景6 jsonl 字段完整(tier/symbol)")

    # 6 Tier3 有持仓 → 强制卖出
    out = pc._apply_tier_filter(sig(action="持有"), {"position": True}, "tier3")
    check("卖出" in out["action"], f"场景7 Tier3有持仓→强制卖出(实测 {out['action']})")

    # 7 Tier3 卖出信号不标记 suppressed
    out = pc._apply_tier_filter(sig(action="卖出"), {"position": True}, "tier3")
    check("suppressed_by" not in out, "场景8 Tier3卖出不做抑制标记")

    # 8 Tier2 持有信号不被改写
    out = pc._apply_tier_filter(sig(action="持有"), {"position": False}, "tier2")
    check(out["action"] == "持有" and "suppressed_by" not in out,
          "场景9 Tier2持有信号不被改写")
finally:
    pc.WORKSPACE = old_ws

print()
print(f"合计: {len(PASS)} 过 / {len(FAIL)} 败")
sys.exit(1 if FAIL else 0)
