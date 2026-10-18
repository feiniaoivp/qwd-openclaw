#!/usr/bin/env python3
"""三因素门控契约测试（2026-10-18 口径修订后）。

锁定 5 件事（ERR-20261018-C03 + 用户拍板方案 B/A）：
  1. 缺失 ≠ 中性：情绪/基本面缺失时 score 为 None，不再按 50 参与加权
  2. GATE_MISSING_POLICY 三种策略（fail_closed 默认 / fail_open 旧行为 / ignore 重归一）
  3. 门槛 GATE_BUY_LINE 默认 55，且可由环境变量覆盖
  4. 「技术90 + 双缺」在 fail_closed 下必须拦截（原实现是 68 分恒放行 = 本次要治的病）
  5. fail_open 回退路径能还原旧口径（可回滚性）
跑法: python3 scripts/gate_missing_policy_test.py   (全过 exit 0)
"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis import three_factor_helper as tfh

PASS, FAIL = [], []


def check(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("✅" if cond else "❌") + " " + label)


class FakeGate(tfh.ResonanceGate):
    """离线桩：注入情绪/基本面定值（含 None），不触网。"""

    def __init__(self, sent, fund):
        super().__init__()
        self._fake_sent = sent
        self._fake_fund = fund

    def sentiment(self):
        return {"score": self._fake_sent, "up_ratio": 0.5, "detail": "stub"}

    def fundamental(self, symbol):
        return {"score": self._fake_fund, "roe": 0.1, "yoy_pni": 10, "np_margin": 5}


def run(sent, fund, tech=None, policy=None):
    old = os.environ.get("GATE_MISSING_POLICY")
    if policy:
        os.environ["GATE_MISSING_POLICY"] = policy
    try:
        g = FakeGate(sent, fund)
        return g.check_buy("600030", tech_score=tech)
    finally:
        if policy:
            if old is None:
                os.environ.pop("GATE_MISSING_POLICY", None)
            else:
                os.environ["GATE_MISSING_POLICY"] = old


# 1 默认策略是 fail_closed
check((os.getenv("GATE_MISSING_POLICY", "fail_closed") == "fail_closed"),
      "场景1 默认缺失策略 = fail_closed")

# 2 【核心回归】技术90+双缺 → 必须拦截（旧实现 68 分放行）
ok, info = run(None, None, tech=90.0)
check(not ok, f"场景2 技术90+双缺 fail_closed → 拦截 (buy_score={info['buy_score']})")
check(info["buy_score"] is None, "场景3 缺失时 buy_score 为 None（未塞 50）")
check(set(info["missing"]) >= {"基本面"}, f"场景4 缺失维被记录: {info['missing']}")

# 5 情绪缺 + 技术90 + 基本面90 → 仍拦（任一维缺失）
ok, _ = run(None, 90.0, tech=90.0)
check(not ok, "场景5 情绪缺(即使技术/基本面极强) fail_closed → 拦截")

# 6 fail_open 回退 = 旧口径（技术90+双缺 = 68 分放行）
ok, info = run(None, None, tech=90.0, policy="fail_open")
check(ok and abs(info["buy_score"] - 68.0) < 0.1,
      f"场景6 fail_open 回退旧口径 (buy_score={info['buy_score']}, 应=68.0)")

# 7 ignore: 缺失维剔除后重归一（技术90独占 → 90 分放行）
ok, info = run(None, None, tech=90.0, policy="ignore")
check(ok and abs(info["buy_score"] - 90.0) < 0.1,
      f"场景7 ignore 缺失维重归一 (buy_score={info['buy_score']}, 应=90.0)")

# 8 全维可用: 90/90/90 = 90 分 → 放行
ok, info = run(90.0, 90.0, tech=90.0)
check(ok and abs(info["buy_score"] - 90.0) < 0.1, f"场景8 三维齐全 (buy_score={info['buy_score']})")

# 9 门槛默认 55
check(abs(tfh.GATE_BUY_LINE - 55.0) < 1e-6, f"场景9 门槛默认 55 (实测 {tfh.GATE_BUY_LINE})")

# 10 低分被拦: 60/30/30 = 42 分 → 拦
ok, info = run(30.0, 30.0, tech=60.0)
check(not ok and info["buy_score"] < tfh.GATE_BUY_LINE,
      f"场景10 低分拦截 (buy_score={info['buy_score']} < {tfh.GATE_BUY_LINE})")

# 11 边界: 恰好等于门槛 → 放行（>= 语义）
g = FakeGate(55.0, 55.0)
ok, info = g.check_buy("600030", tech_score=55.0)
check(ok and abs(info["buy_score"] - 55.0) < 0.1, "场景11 恰好等于门槛 → 放行(>=)")

# 12 门槛下调的实际差异: 构造 56 分（新 55 线放行 / 旧 60 线会拦）
#    90×0.45 + 40×0.30 + 26×0.25 = 40.5+12+6.5 = 59.0 → 用 90/30/32 得 40.5+9+8=57.5
g = FakeGate(32.0, 30.0)
ok, info = g.check_buy("600030", tech_score=90.0)
check(ok and 55.0 <= info["buy_score"] < 60.0,
      f"场景12 {info['buy_score']}分：新55线放行/旧60线会拦")

print()
print(f"合计: {len(PASS)} 过 / {len(FAIL)} 败")
sys.exit(1 if FAIL else 0)
