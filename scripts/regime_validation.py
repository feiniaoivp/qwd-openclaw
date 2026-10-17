#!/usr/bin/env python3
"""Regime 检测器验证分析（2026-10-17）：状态开关能否分离信号价值？

方法: 对 21k 评估日按逐票 regime（ER20/ADX14，因果无前视）分态，
比较 买入类/卖出类 信号在各态下的前瞻超额；并做 年×状态 交叉检验
（证明 regime 不只是日历的代理变量）。
数据: analysis/research/dual_signal_eval_days.json
"""
import os
import sys
import json
from collections import defaultdict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

rows = json.load(open(os.path.join(WORKSPACE, "analysis/research/dual_signal_eval_days.json")))
print(f"评估日: {len(rows)}")

BUY_CLASSES = {"双买", "分歧买"}
SELL_CLASSES = {"双卖", "分歧卖"}


def m(vals):
    vals = [v for v in vals if v is not None]
    return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)


def excess(sub, regime_key, regime_val, h):
    """该类信号在指定 regime 态下的 fwd(h) 均值 - 同态全日基线均值。"""
    ev = [r["fwd"].get(h) for r in sub if r.get(regime_key) == regime_val]
    ba = [r["fwd"].get(h) for r in rows if r.get(regime_key) == regime_val]
    me, ne = m(ev)
    mb, nb = m(ba)
    if me is None or mb is None:
        return None, ne, mb, nb
    return me - mb, ne, mb, nb


for key, label in (("regime_er", "ER20≥0.25"), ("regime_adx", "ADX14≥20")):
    print(f"\n===== 按 {label} 分态 =====")
    for group, classes in (("买入类(双买+分歧买)", BUY_CLASSES), ("卖出类(双卖+分歧卖)", SELL_CLASSES)):
        sub = [r for r in rows if r["cls"] in classes]
        print(f"-- {group} (事件 {len(sub)}) --")
        for rv in ("trend", "chop"):
            line = f"  {rv:5}: "
            for h in ("1", "5", "10"):
                ex, ne, mb, nb = excess(sub, key, rv, h)
                line += (f"+{h}d 超额 {ex:+.2%}(n={ne}) " if ex is not None else f"+{h}d n={ne} ")
            print(line)

# 年 × 状态 交叉（买入类）：regime 是否在年内也有区分度（不只代理日历）
print("\n===== 买入类 年×ER状态 交叉 (+5d 超额 vs 同年同态基线) =====")
buy = [r for r in rows if r["cls"] in BUY_CLASSES]
years = sorted({r["date"][:4] for r in rows})
for y in years:
    for rv in ("trend", "chop"):
        ev = [r["fwd"].get("5") for r in buy if r["date"][:4] == y and r.get("regime_er") == rv]
        ba = [r["fwd"].get("5") for r in rows if r["date"][:4] == y and r.get("regime_er") == rv]
        me, ne = m(ev)
        mb, nb = m(ba)
        if me is None or mb is None:
            print(f"  {y} {rv:5}: n={ne} (样本不足)")
            continue
        print(f"  {y} {rv:5}: 超额 {me - mb:+.2%} (事件n={ne}, 基线n={nb}, 基线{mb:+.2%})")

# 池广度：每日处于趋势态的票占比分布
by_date = defaultdict(list)
for r in rows:
    if r.get("regime_er"):
        by_date[r["date"]].append(1 if r["regime_er"] == "trend" else 0)
breadth = {d: sum(v) / len(v) for d, v in by_date.items() if len(v) >= 20}
tb = sorted(breadth.values())
n = len(tb)
print(f"\n===== 池广度（{len(breadth)} 个交易日, 逐票ER趋势占比） =====")
print(f"  均值 {sum(tb)/n:.0%} | p10 {tb[int(.1*n)]:.0%} | 中位 {tb[n//2]:.0%} | p90 {tb[int(.9*n)]:.0%}")
trend_days = sum(1 for v in tb if v > 0.5)
print(f"  广度>50% 的交易日: {trend_days}/{n} ({trend_days/n:.0%})")
