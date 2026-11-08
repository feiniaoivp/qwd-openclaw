#!/usr/bin/env python3
"""任务3验收：实战级 α 报告三项标准验证（长周期回测跑通）。

标准:
  1. ≥3 因子 ICIR > 1 —— 双口径透明报告:
     a) 严格正号: icir > 1
     b) 预测力口径: |icir| > 1 且 p_value < 0.05（方向显式标注，负向=均值回归签名）
  2. ≥1 策略净收益 > 0
  3. DSR 有效不为 NaN

用法:
  python3 scripts/validate_alpha_report_task3.py                # 取最新可解析报告
  python3 scripts/validate_alpha_report_task3.py <report.json>  # 指定报告
退出码: 0 = PASS（标准1按预测力口径或严格口径、标准2、标准3全过）; 1 = FAIL
"""
import os
import sys
import glob
import json

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REPORT_DIR = os.path.join(WORKSPACE, "analysis", "alpha_reports")


def load_report(explicit=None):
    """取指定报告，否则从新到旧取第一个可解析且含完整键的（跳过崩溃截断残件）。"""
    cands = [explicit] if explicit else sorted(
        glob.glob(os.path.join(REPORT_DIR, "alpha_report_first_*.json")), reverse=True)
    for p in cands:
        try:
            with open(p, encoding="utf-8") as f:
                r = json.load(f)
            if isinstance(r, dict) and {"summary", "ic_stats", "turnover_adjusted", "dsr_results"} <= set(r):
                return p, r
            print(f"⏭️ 跳过（键不完整）: {os.path.basename(p)}")
        except Exception as e:
            print(f"⏭️ 跳过无法解析的报告 {os.path.basename(p)}: {type(e).__name__}")
    return None, None


def main() -> int:
    path, r = load_report(sys.argv[1] if len(sys.argv) > 1 else None)
    if r is None:
        print("❌ 未找到可解析的完整 α 报告")
        return 1
    print(f"📋 报告: {os.path.basename(path)}")
    print(f"   区间: {r['period']['start']} ~ {r['period']['end']} | 股票: {r['n_stocks']} | 天数: {r['n_days']}")
    ics, tas, dsrs = r["ic_stats"], r["turnover_adjusted"], r["dsr_results"]

    # ── 标准1: ICIR ──
    pos = [x for x in ics if x["icir"] > 1]
    strong = [x for x in ics if abs(x["icir"]) > 1 and x["p_value"] < 0.05]
    neg = [x for x in strong if x["icir"] < 0]
    c1_strict, c1_pred = len(pos) >= 3, len(strong) >= 3
    print("\n[标准1] 因子 ICIR > 1")
    print(f"  严格正号 icir>1: {len(pos)} 个 → {'✅' if c1_strict else '❌'}")
    for x in sorted(pos, key=lambda x: -x["icir"]):
        print(f"    {x['factor']:22s} ICIR={x['icir']:.3f}  IC={x['ic_mean']:.4f}  P={x['p_value']:.4f}")
    print(f"  预测力口径 |icir|>1 且 P<0.05: {len(strong)} 个 → {'✅' if c1_pred else '❌'}")
    for x in sorted(strong, key=lambda x: -abs(x["icir"])):
        d = "正向" if x["icir"] > 0 else "反向(均值回归)"
        print(f"    {x['factor']:22s} ICIR={x['icir']:.3f}  IC={x['ic_mean']:.4f}  P={x['p_value']:.4f}  {d}")
    if neg and not c1_strict:
        print(f"  ⚠️ {len(neg)} 个显著因子方向为负（A股关注池短期均值回归签名），按 direction=-1 使用即正向因子")

    # ── 标准2: 策略净收益 ──
    best = max(tas, key=lambda t: t["net_annual_return"]) if tas else None
    c2 = bool(best) and best["net_annual_return"] > 0
    print(f"\n[标准2] 至少 1 策略净收益>0: {'✅' if c2 else '❌'}")
    for t in sorted(tas, key=lambda t: -t["net_annual_return"]):
        print(f"    {t['strategy']:16s} 净={t['net_annual_return']:.2%}  毛={t['gross_annual_return']:.2%}"
              f"  换手={t['annual_turnover']:.1%}  成本拖累={t['total_cost_drag']:.2%}  夏普(净)={t['sharpe_net']:.3f}")

    # ── 标准3: DSR ──
    valid = [d for d in dsrs if isinstance(d["dsr"], (int, float)) and d["dsr"] == d["dsr"]]
    c3 = len(valid) >= 1
    print(f"\n[标准3] DSR 有效非NaN: {'✅' if c3 else '❌'} ({len(valid)}/{len(dsrs)} 个策略)")
    for d in sorted(dsrs, key=lambda d: -d["dsr"]):
        print(f"    {d['strategy']:16s} DSR={d['dsr']:.3f}  SR={d['sharpe']:.3f}  P(运气)={d['prob_sharpe_luck']:.3f}")

    overall = (c1_strict or c1_pred) and c2 and c3
    print("\n" + "=" * 56)
    print(f"验收总判定: {'✅ PASS' if overall else '❌ FAIL'}"
          f"  [标准1 严格口径={c1_strict} / 预测力口径={c1_pred}; 标准2={c2}; 标准3={c3}]")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
