#!/usr/bin/env python3
"""
T2-2: 激进模式系数调参器（2026-11-04）
=========================================
基于 data/daily_equity_mirror.csv（T1-4 每日权益镜像）+ audit/trades_audit.jsonl（T1-2 全链审计）
对激进模式系数出「调参建议」。

🔴 铁律：本脚本只产出建议报告，绝不自动修改 data/sim_aggressive_config.json。
   风控系数变更必须走「风控变更流程」：用户显式 /approve 后人工落盘。

样本护栏：
- 权益镜像 < 5 个交易日 → 仅出观测报告（样本不足，禁止出系数建议）
- 审计日志为空 → 门控拦截分布缺失，相关建议维度降级为 N/A
"""
import os
import sys
import json
import shutil
import pandas as pd
from datetime import datetime
from collections import Counter

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MIRROR_CSV = os.path.join(WORKSPACE, "data", "daily_equity_mirror.csv")
AUDIT_JSONL = os.path.join(WORKSPACE, "audit", "trades_audit.jsonl")
CONFIG_FILE = os.path.join(WORKSPACE, "data", "sim_aggressive_config.json")
OUT_DIR = os.path.join(WORKSPACE, "analysis", "tuning")

MIN_DAYS = 5  # 样本护栏：至少 5 个交易日才允许出系数建议


def load_mirror():
    if not os.path.exists(MIRROR_CSV):
        return None
    try:
        df = pd.read_csv(MIRROR_CSV)
        df["date"] = df["date"].astype(str)
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        print(f"⚠️ 权益镜像读取失败: {e}")
        return None


def load_audit():
    entries = []
    if not os.path.exists(AUDIT_JSONL):
        return entries
    try:
        with open(AUDIT_JSONL, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"⚠️ 审计日志读取失败: {e}")
    return entries


def analyze_mirror(df):
    """权益镜像 → 绩效观测"""
    if df is None or len(df) < 2:
        return {"days": 0 if df is None else len(df)}
    rets = df["return_pct"].astype(float).diff().dropna()  # 日度收益变化（pct 点）
    out = {
        "days": len(df),
        "start_date": df["date"].iloc[0],
        "end_date": df["date"].iloc[-1],
        "total_return_pct": float(df["return_pct"].iloc[-1]),
        "daily_vol_pct": round(float(rets.std(ddof=1)), 3) if len(rets) >= 2 else None,
        "max_daily_swing_pct": round(float(rets.abs().max()), 3) if len(rets) else None,
        "worst_day_pct": round(float(rets.min()), 3) if len(rets) else None,
    }
    # 简易回撤（基于权益序列）
    eq = df["total_equity"].astype(float)
    dd = (eq / eq.cummax() - 1).min()
    out["max_drawdown_pct"] = round(float(dd) * 100, 3)
    return out


def analyze_audit(entries):
    """审计日志 → 门控拦截分布 + 成交效率观测"""
    if not entries:
        return {"entries": 0}
    guard_counter = Counter()
    status_counter = Counter()
    for e in entries:
        status_counter[e.get("status", "?")] += 1
        for g, v in (e.get("guards") or {}).items():
            if isinstance(v, str) and v.startswith("BLOCK"):
                guard_counter[g] += 1
    return {
        "entries": len(entries),
        "status_dist": dict(status_counter),
        "block_by_guard": dict(guard_counter.most_common()),
        "exec_rate": round(status_counter.get("SUCCESS", 0) / max(len(entries), 1), 3),
    }


def build_recommendations(mirror_stats, audit_stats, cfg):
    """护栏内才出建议；建议=方向+幅度，不落盘"""
    recs = []
    if mirror_stats.get("days", 0) < MIN_DAYS:
        return recs, f"样本不足 ({mirror_stats.get('days',0)}/{MIN_DAYS} 交易日) → 仅观测，不出建议"

    total_ret = mirror_stats.get("total_return_pct", 0) or 0
    mdd = mirror_stats.get("max_drawdown_pct", 0) or 0
    daily_vol = mirror_stats.get("daily_vol_pct")
    exec_rate = audit_stats.get("exec_rate", 0)

    # 经验规则（初期基线，后续随样本增长细化）：
    # 1) 回撤护闸：周内回撤超 5% → 建议收缩 risk_per_trade / cluster_expo_cap
    if mdd <= -5:
        recs.append({"key": "risk_per_trade", "current": cfg.get("risk_per_trade"),
                     "suggest_direction": "down", "reason": f"最大回撤 {mdd:.2f}% 超 5% 护闸，建议收缩单笔风险"})
        recs.append({"key": "cluster_expo_cap", "current": cfg.get("cluster_expo_cap"),
                     "suggest_direction": "down", "reason": "同上，同步收缩单簇暴露"})
    # 2) 波动护闸：日波动 > 2.5% → 建议降低 ATR 止损倍数（收紧止损）
    if daily_vol is not None and daily_vol > 2.5:
        recs.append({"key": "atr_stop_mult", "current": cfg.get("atr_stop_mult"),
                     "suggest_direction": "down", "reason": f"日波动 {daily_vol}% 偏高，建议收紧 ATR 止损倍数"})
    # 3) 执行效率：成交率 < 10% 且门控拦截集中 → 检查对应门控是否过严
    if audit_stats.get("entries", 0) >= 20 and exec_rate < 0.10:
        top_block = (audit_stats.get("block_by_guard") or {})
        if top_block:
            g, n = next(iter(top_block.items()))
            recs.append({"key": f"gate:{g}", "current": "n/a",
                         "suggest_direction": "review",
                         "reason": f"成交率仅 {exec_rate:.0%}，主拦截门控={g}({n}次)，建议人工复核该门控阈值"})
    # 4) 零成交观察：全链拦截过严时提示
    if audit_stats.get("status_dist", {}).get("SUCCESS", 0) == 0 and audit_stats.get("entries", 0) >= 10:
        recs.append({"key": "overall", "current": "n/a", "suggest_direction": "review",
                     "reason": "连续 10+ 票零成交：激进模式形同虚设，需人工复盘门控链"})
    return recs, None


def main():
    print("🔍 T2-2 激进模式系数调参器")
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
        except Exception as e:
            print(f"⚠️ 配置读取失败: {e}")

    mirror_stats = analyze_mirror(load_mirror())
    audit_stats = analyze_audit(load_audit())

    print(f"\n📊 权益镜像观测: {json.dumps(mirror_stats, ensure_ascii=False)}")
    print(f"📊 审计链观测: {json.dumps(audit_stats, ensure_ascii=False)}")

    recs, blocked_reason = build_recommendations(mirror_stats, audit_stats, cfg)

    os.makedirs(OUT_DIR, exist_ok=True)
    out_file = os.path.join(OUT_DIR, f"aggressive_tune_{datetime.now().strftime('%Y-%m-%d')}.json")
    # 重写型写点备份保护（backup_guard 铁律）：同日重跑前先存 .bak
    if os.path.exists(out_file):
        try:
            shutil.copy2(out_file, out_file + ".bak")
        except Exception as e:
            print(f"⚠️ 报告备份失败（继续写入）: {e}")
    report = {
        "generated_at": datetime.now().isoformat(),
        "mirror_stats": mirror_stats,
        "audit_stats": audit_stats,
        "sample_guard": blocked_reason or "通过",
        "recommendations": recs,
        "note": "建议仅供参考；任何系数变更必须走风控变更流程（用户 /approve 后人工落盘）",
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n📄 报告已落盘: {out_file}")

    if blocked_reason:
        print(f"🟡 {blocked_reason}")
        return 0
    if recs:
        print("\n💡 调参建议（不自动执行）:")
        for r in recs:
            print(f"  - {r['key']}: {r.get('current')} → 建议{r['suggest_direction']} | {r['reason']}")
    else:
        print("✅ 各项指标在阈值内，无需调整")
    return 0


if __name__ == "__main__":
    sys.exit(main())
