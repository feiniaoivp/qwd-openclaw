#!/usr/bin/env python3
"""双买信号稳健性深挖（2026-10-17）：n=43 的 +3.7%@5d 是真信号还是假象？

检查: ①分年分布 ②个股集中度 ③事件扎堆去重(10日) ④bootstrap 95%CI ⑤月度聚类(行情beta)
数据: analysis/research/dual_signal_eval_days.json（研究脚本已落盘明细）
"""
import os
import sys
import json
import random
from collections import Counter, defaultdict
from datetime import date

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

random.seed(42)

rows = json.load(open(os.path.join(WORKSPACE, "analysis/research/dual_signal_eval_days.json")))
sb = sorted([r for r in rows if r["cls"] == "双买"], key=lambda r: r["date"])
print(f"双买事件总数: {len(sb)}")

# ① 分年分布（+5d）
by_year = defaultdict(list)
for r in sb:
    if "5" in r["fwd"]:
        by_year[r["date"][:4]].append(r["fwd"]["5"])
print("\n① 分年分布 (+5d):")
for y in sorted(by_year):
    v = by_year[y]
    m = sum(v) / len(v)
    print(f"  {y}: n={len(v)} 均值 {m:+.2%} 胜率 {sum(x>0 for x in v)/len(v):.0%}")

# ② 个股集中度
c = Counter(r["symbol"] for r in sb)
print(f"\n② 个股集中度: {len(c)} 只贡献 {len(sb)} 事件; Top5: {c.most_common(5)}")
top1_sym, top1_n = c.most_common(1)[0]
excl = [r["fwd"].get("5") for r in sb if r["symbol"] != top1_sym and "5" in r["fwd"]]
m_excl = sum(excl) / len(excl)
print(f"  剔除最高频个股({top1_sym},{top1_n}次)后: n={len(excl)} 均值 {m_excl:+.2%}")

# ③ 事件扎堆去重（同一票 10 日内只留首个）
def parse_d(s):
    p = s.split("-")
    return date(int(p[0]), int(p[1]), int(p[2]))

seen = {}
dedup = []
for r in sb:  # 已按日期排序
    d0 = parse_d(r["date"])
    last = seen.get(r["symbol"])
    if last is not None and (d0 - last).days < 10:
        continue
    seen[r["symbol"]] = d0
    dedup.append(r)
v5 = [r["fwd"]["5"] for r in dedup if "5" in r["fwd"]]
print(f"\n③ 扎堆去重(10日/票): {len(sb)} → {len(dedup)} 个独立事件, +5d 均值 {sum(v5)/len(v5):+.2%}")

# ④ bootstrap 95% CI（+5d，全样本与去重样本）
def boot_ci(vals, n_boot=5000):
    vals = [v for v in vals if v is not None]
    means = []
    for _ in range(n_boot):
        s = [random.choice(vals) for _ in range(len(vals))]
        means.append(sum(s) / len(s))
    means.sort()
    return means[int(0.025 * n_boot)], means[int(0.975 * n_boot)]

all5 = [r["fwd"]["5"] for r in sb if "5" in r["fwd"]]
lo, hi = boot_ci(all5)
lo2, hi2 = boot_ci(v5)
print(f"\n④ bootstrap 95%CI (+5d): 全样本 [{lo:+.2%}, {hi:+.2%}] | 去重后 [{lo2:+.2%}, {hi2:+.2%}]")

# ⑤ 月度聚类（是否扎堆于特定行情月）
mc = Counter(r["date"][:7] for r in sb)
print(f"\n⑤ 月度分布 Top8: {mc.most_common(8)}")

# ⑥ 与大盘日的关系代理: 事件日该票 fwd 分布的分位数
v10 = sorted([r["fwd"]["10"] for r in sb if "10" in r["fwd"]])
if v10:
    print(f"\n⑥ +10d 分布: min {v10[0]:+.2%} / p25 {v10[len(v10)//4]:+.2%} / 中位 {v10[len(v10)//2]:+.2%} / p75 {v10[3*len(v10)//4]:+.2%} / max {v10[-1]:+.2%}")
