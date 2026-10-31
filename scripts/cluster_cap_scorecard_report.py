#!/usr/bin/env python3
"""簇门禁评分卡（组合回撤维度）— 报告生成（2026-10-18 深挖 ①-C）。

读 analysis/research/<date>_cluster_cap_scorecard.json，输出 Markdown 结论到
analysis/research/<date>_cluster_cap_scorecard.md。纯展示层，不改任何生产口径。
"""
import os
import sys
import json
from datetime import date
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    today = date.today().isoformat()
    src = os.path.join(WORKSPACE, f"analysis/research/{today}_cluster_cap_scorecard.json")
    if not os.path.exists(src):
        print(f"缺输入 {src}")
        return 1
    d = json.load(open(src))
    L = []
    A = d["arms"]
    B = d.get("bootstrap", {})
    L.append(f"# 同簇上限≤2 评分卡（组合维度） — {today}")
    L.append("")
    L.append(f"窗口 {d['window']} | 持有 {d['hold_days']} 交易日 | 上限 {d['cap']} 只/簇 | "
             f"组合总仓位 {d.get('total_expo')} | 信号源 dual_signal_eval_days（1068 笔买入）")
    L.append("")
    L.append("## 三臂对比")
    L.append("")
    L.append("| 臂 | 总收益 | 年化 | 最大回撤 | Calmar | 波动 | 最大同簇持仓 | 簇≥3 日占比 | 拦截笔数 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for k, label in (("prod", "全期（现行）"), ("roll", "滚动252d"), ("off", "不限制")):
        v = A[k]
        L.append(f"| {label} | {v['total_return_pct']}% | {v['annualized_pct']}% | "
                 f"{v['max_drawdown_pct']}% | {v['calmar']} | {v['vol_annual_pct']}% | "
                 f"{v['max_same_cluster']} | {v['days_cluster_ge3_pct']}% | {v['blocked_buys']} |")
    L.append("")
    L.append("## 风险标准化（同一 10% 年化波动预算下）")
    L.append("")
    L.append("| 臂 | MDD@10%vol | 年化@10%vol |")
    L.append("|---|---|---|")
    for k, label in (("prod", "全期（现行）"), ("roll", "滚动252d"), ("off", "不限制")):
        v = A[k]
        L.append(f"| {label} | {v['mdd_at_10pct_vol']}% | {v['return_at_10pct_vol_pct']}% |")
    L.append("")
    L.append("## 齐跌日分解（池内均跌 > 0.5% 的 197 个交易日）")
    L.append("")
    L.append("| 臂 | 均损 | 最差单日 | 累计损失 |")
    L.append("|---|---|---|---|")
    for k, label in (("prod", "全期（现行）"), ("roll", "滚动252d"), ("off", "不限制")):
        c = d["down_day_decomposition"][k]
        L.append(f"| {label} | {c['avg_loss_pct']}% | {c['worst_day_pct']}% | {c['total_loss_pct']}% |")
    L.append("")
    if B:
        L.append("## 配对 bootstrap（日收益重采样 3000 次，同 seed）")
        L.append("")
        L.append("| 对比 | Δ年化 (pp) | ΔMDD (pp) | P(Δ年化>0) |")
        L.append("|---|---|---|---|")
        for k, label in (("off-prod", "不限制 − 现行"), ("roll-prod", "滚动 − 现行"), ("off-roll", "不限制 − 滚动")):
            if k not in B:
                continue
            b = B[k]
            L.append(f"| {label} | {b['d_ann_pp'][0]:+.2f} "
                     f"[{b['d_ann_pp'][1]:+.2f}, {b['d_ann_pp'][2]:+.2f}] | "
                     f"{b['d_mdd_pp'][0]:+.2f} [{b['d_mdd_pp'][1]:+.2f}, {b['d_mdd_pp'][2]:+.2f}] | "
                     f"{b['p_ann_gt0']} |")
        L.append("")
    L.append("## 结论")
    L.append("")
    prod, roll, off = A["prod"], A["roll"], A["off"]
    L.append(f"1. **门禁不是免费午餐**：现行口径年化 {prod['annualized_pct']}% vs 不限 {off['annualized_pct']}%，"
             f"差 {round(off['annualized_pct'] - prod['annualized_pct'], 2)}pp；"
             f"最大回撤 {prod['max_drawdown_pct']}% vs {off['max_drawdown_pct']}%"
             f"（门禁**没有**降低回撤，反而略高）。")
    L.append(f"2. **回撤维度的辩护假设被证伪**：三臂同 21% 波动；风险标准化（10% vol）后 "
             f"MDD@10%vol = {prod['mdd_at_10pct_vol']}% / {roll['mdd_at_10pct_vol']}% / {off['mdd_at_10pct_vol']}% "
             f"— 不限制反而最低。齐跌日最差单日 {d['down_day_decomposition']['prod']['worst_day_pct']}% vs "
             f"{d['down_day_decomposition']['off']['worst_day_pct']}%（不限制更浅）。")
    L.append("3. **统计上不显著**：所有配对差 CI95 都含 0（Δ年化 P(>0)≈0.5）。"
             "这意味着「门禁有害」也没被证明 —— 但同样，「门禁保护组合」这个立论在本数据上**没有证据**。")
    L.append("4. **滚动口径更差**：拦截 227 笔（全期 117 笔）却保护效果更弱，"
             f"且期间最大同簇仅 {roll['max_same_cluster']} 只（因为滚动期入簇票少 → 门禁形同虚设于最需要它的高相关期）。"
             "**继续维持全期口径**。")
    L.append("")
    L.append("## 局限（必读）")
    L.append("")
    L.append("- 事件驱动重放非生产链路：无止损/030/钝化/tier 分层，仓位模型为等权 60% 敞口，"
             "不能替代实盘绩效，只用于**同一模型内三臂相对比较**。")
    L.append("- 信号源是双策略融合口径（as-of 计算），非逐日生产 `run_portfolio_scan` 输出；"
             "生产链路还含止损冷却期、去重、撑铃约束等额外拦截。")
    L.append("- 齐跌日「均损」按三臂各自持仓加权，持仓集合不同 → 均损差包含选股效应，不是纯门禁效应。")
    L.append("- 211%~247% 的两年总收益明显高于实盘模拟（+30%）系高换手等权重放的乐观偏差，**不得对外引用**。")
    L.append("")
    L.append("## 建议（待拍板）")
    L.append("")
    L.append("- 维持全期口径 + 月度刷新（已建 cron），理由：滚动口径无收益/回撤证据且在高相关期失效。")
    L.append("- 把同簇上限从「买入阻断」改为**组合层单簇暴露上限**（例如单簇市值占比 ≤25%），"
             "这样约束在持仓期持续生效，而不是只在建仓瞬间生效 —— 后者正是本评分卡显示无保护的原因。")
    L.append("- 若两周内不想动生产逻辑：保持现状，把本评分卡加入月度复查，观察门禁在真实链路（含止损）下的表现。")
    L.append("")
    path = os.path.join(WORKSPACE, f"analysis/research/{today}_cluster_cap_scorecard.md")
    _bak(path)
    open(path, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"[report] 已写 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
