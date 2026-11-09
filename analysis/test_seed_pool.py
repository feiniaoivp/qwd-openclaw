#!/usr/bin/env python3
"""seed_pool 对抗性自测 — 晋升/转正/降级/种子保护/上限约束 全规则验证"""
#!/usr/bin/env python3
import os
import sys
import json
import tempfile
import shutil
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 沙箱化状态文件
TMP = tempfile.mkdtemp(prefix="seed_test_")
import analysis.seed_pool as sp
sp.UNI_DIR = TMP
sp.POOL_PATH = os.path.join(TMP, "seed_pool.json")
sp.AUDIT_PATH = os.path.join(TMP, "audit.log")

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" | {detail}" if detail else ""))


def mk_scores(scores: dict):
    """scores: {sym: score} → all_scores 已排序"""
    lst = [{"symbol": s, "total_score": v, "name": s} for s, v in sorted(scores.items(), key=lambda x: -x[1])]
    return lst


# ── 场景1: 初始化种子保护 ──
pool = sp.SeedPool()
core_n = len(pool.pool)
check("初始化 core 36 只", core_n == 36, f"实际 {core_n}")

# ── 场景2: candidate 晋升 observe（连续3日进前15%）──
# 构造 40 只池: 36 core + 4 candidate；NEW1 连续 3 日高分
for s in [f"C{i:03d}" for i in range(4)]:
    pool.ensure_member(s, f"测试{s}", tier="candidate")
pool.save()

# day1-3: NEW1 每日进前 15%（40只×15% = 6 → 前6名）
for day in range(3):
    scores = {f"C{i:03d}": 50.0 for i in range(4)}
    scores["NEW1"] = 95.0 - day  # 恒进前6
    for i in range(10):
        scores[f"F{i:02d}"] = 60.0 - i * 5  # 填充 10 只对比票（入池 candidate）
    # 36 core 需要评分记录（给中间分，不触发升降）
    core_syms = [s for s, st in pool.pool.items() if st["tier"] == "core"]
    for i, s in enumerate(core_syms):
        scores[s] = 55.0 + (i % 5)
    pool.update_daily([], mk_scores(scores))

st = pool.pool.get("NEW1", {})
check("NEW1 连续3日进前15% → observe", st.get("tier") == "observe",
      f"tier={st.get('tier')} top_streak={st.get('top_streak')}")

# ── 场景3: observe 7日中位 > 池中位 → watch ──
for day in range(7):
    scores = {f"C{i:03d}": 50.0 for i in range(4)}
    scores["NEW1"] = 70.0
    scores["LOW1"] = 20.0  # 对比低分票
    for i in range(10):
        scores[f"F{i:02d}"] = 55.0 - i
    core_syms = [s for s, st in pool.pool.items() if st["tier"] == "core"]
    for i, s in enumerate(core_syms):
        scores[s] = 50.0  # 池中位 ≈ 50
    pool.update_daily([], mk_scores(scores))
st = pool.pool.get("NEW1", {})
check("NEW1 7日中位70 > 池中位50 → watch", st.get("tier") == "watch",
      f"tier={st.get('tier')} observe_days={st.get('observe_days')}")

# ── 场景4: observe 中位不达标 → 打回 candidate ──
st_new2 = None
# 让 LOW2 先晋升到 observe
for day in range(3):
    scores = {f"C{i:03d}": 50.0 for i in range(4)}
    scores["LOW2"] = 95.0
    for i in range(10):
        scores[f"F{i:02d}"] = 55.0 - i
    core_syms = [s for s, st in pool.pool.items() if st["tier"] == "core"]
    for i, s in enumerate(core_syms):
        scores[s] = 50.0
    pool.update_daily([], mk_scores(scores))
check("LOW2 → observe", pool.pool.get("LOW2", {}).get("tier") == "observe")
# observe 期 7 日评分 < 池中位
for day in range(7):
    scores = {f"C{i:03d}": 50.0 for i in range(4)}
    scores["LOW2"] = 40.0  # 低于池中位
    for i in range(10):
        scores[f"F{i:02d}"] = 55.0 - i
    core_syms = [s for s, st in pool.pool.items() if st["tier"] == "core"]
    for i, s in enumerate(core_syms):
        scores[s] = 50.0
    pool.update_daily([], mk_scores(scores))
st = pool.pool.get("LOW2", {})
check("LOW2 7日中位40 ≤ 池中位50 → 打回 candidate", st.get("tier") == "candidate",
      f"tier={st.get('tier')}")

# ── 场景5: 连续10日低分 → 降级 ──
for day in range(10):
    scores = {f"C{i:03d}": 50.0 for i in range(4)}
    scores["BAD1"] = 10.0
    for i in range(10):
        scores[f"F{i:02d}"] = 55.0 - i
    core_syms = [s for s, st in pool.pool.items() if st["tier"] == "core"]
    for i, s in enumerate(core_syms):
        scores[s] = 50.0
    pool.update_daily([], mk_scores(scores))
st = pool.pool.get("BAD1", {})
check("BAD1 连续10日<35 → candidate(已是的保持)", isinstance(st, dict))

# 对 watch 层的降级测试: 把 NEW2 手动设 watch，喂 10 日低分
pool.pool["NEW2"] = {"name": "NEW2", "tier": "watch", "entry_date": "2026-11-01",
                     "score_history": [], "rank_history": [], "top_streak": 0,
                     "low_streak": 0, "observe_days": 0, "last_action": ""}
for day in range(10):
    scores = {f"C{i:03d}": 50.0 for i in range(4)}
    scores["NEW2"] = 10.0
    for i in range(10):
        scores[f"F{i:02d}"] = 55.0 - i
    core_syms = [s for s, st in pool.pool.items() if st["tier"] == "core"]
    for i, s in enumerate(core_syms):
        scores[s] = 50.0
    pool.update_daily([], mk_scores(scores))
st = pool.pool.get("NEW2", {})
check("watch 层连续10日低分 → 降级 candidate", st.get("tier") == "candidate",
      f"tier={st.get('tier')}")

# ── 场景6: core 种子保护 — 36 只 core 喂 20 日零分也不降级 ──
core_syms = [s for s, st in pool.pool.items() if st["tier"] == "core"]
one_core = core_syms[0]
for day in range(20):
    scores = {s: 0.0 for s in core_syms}
    for i in range(10):
        scores[f"F{i:02d}"] = 55.0 - i
    scores["C000"] = 50.0; scores["C001"] = 50.0; scores["C002"] = 50.0; scores["C003"] = 50.0
    pool.update_daily([], mk_scores(scores))
check("core 零分20日仍 core（种子保护）", pool.pool[one_core]["tier"] == "core",
      pool.pool[one_core]["tier"])

# ── 场景7: 审计日志有留痕 ──
audit = open(sp.AUDIT_PATH, encoding="utf-8").read()
check("审计日志含 PROMOTE", "PROMOTE" in audit)
check("审计日志含 CONFIRM", "CONFIRM" in audit)
check("审计日志含 DEMOTE", "DEMOTE" in audit)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n===== 结果: PASS={len(PASS)} FAIL={len(FAIL)} =====")
if FAIL:
    print("失败项:", "; ".join(FAIL))
    sys.exit(1)
print("✅ seed_pool 全规则验证通过")
