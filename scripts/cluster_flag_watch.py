#!/usr/bin/env python3
"""C04 簇 update_flag 触发检查（只读门禁，2026-10-30 新增）。

缘起：commit 4e2b522 给 data/corr_clusters.json 挂了 update_flags.C04
（2026 滚动窗口存续失效 0.457 < 0.55，2025 为 0.602；全期口径仍成簇），
但生产侧 portfolio_core._load_corr_clusters() 只读 symbol_to_cluster、
不消费 update_flags，簇限仓仍按亿纬+天齐全量同簇处理（fail-safe 方向）。

本门禁语义：
- flagged 簇成员均无持仓 → exit 0（触发条件未满足，折算动作继续挂起）；
- 任一 flagged 簇成员实际持仓（shares>0 或 position=true）→ exit 1 + 告警，
  提醒人工拍板「按 flag.action 折算簇联动或停用」。
  风控参数变更须用户 /approve，本脚本只告警、绝不改配置。

fail-open 口径与生产 _load_corr_clusters() 一致：数据文件不可读时打印告警
并返回 0（不阻断流水线，与「失败仅告警不阻断」门禁接入约定一致）。
"""
import json
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")


def main() -> int:
    cc_path = os.path.join(WORKSPACE, "data", "corr_clusters.json")
    ps_path = os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")

    try:
        with open(cc_path) as f:
            flags = json.load(f).get("update_flags") or {}
    except Exception as e:  # noqa: BLE001 - 门禁 fail-open，告警可见
        print(f"⚠️ cluster_flag_watch: corr_clusters.json 不可读({e})，跳过检查")
        return 0

    try:
        with open(ps_path) as f:
            positions = json.load(f).get("positions") or {}
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ cluster_flag_watch: portfolio_sim_state.json 不可读({e})，跳过检查")
        return 0

    flagged_members = []
    for cluster, meta in flags.items():
        for sym in (meta.get("members") or []):
            flagged_members.append((cluster, sym, meta))

    hits = []
    for cluster, sym, meta in flagged_members:
        pos = positions.get(sym) or {}
        shares = pos.get("shares") or 0
        if pos.get("position") or shares:
            hits.append((cluster, sym, pos.get("name", sym), shares, meta))

    if not hits:
        print(
            f"✅ cluster_flag_watch: {len(flagged_members)} 个 flagged 簇成员均无持仓"
            f"（触发条件未满足，折算动作继续挂起）"
        )
        return 0

    for cluster, sym, name, shares, meta in hits:
        print(f"🚨 update_flag 触发: {cluster} 成员 {name}({sym}) 持仓 {shares} 股")
        print(f"   reason: {meta.get('reason', '')}")
        print("   → 需人工拍板: 按 flag.action 折算簇联动或停用（风控变更须 /approve）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
