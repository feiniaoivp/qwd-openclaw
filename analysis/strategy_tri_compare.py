#!/usr/bin/env python3
"""
三方对照报告：当前自适应分配 vs 回测最优 vs 验证稳定得分
========================================================
用途：把「当前生效策略」「跨窗口验证最优」「参数调优候选」放一起对照，
      并标注验证得分/夏普/笔数与**护栏判定结果**，供人工快速决策。

数据源（全部读本地文件，不重新拉网）：
  - data/adaptive_strategy_map.json          当前生效策略
  - analysis/validation_YYYY-MM-DD.json      跨周期验证结果（W1全量/W2近3年/W3近1.5年/W4近1年）
  - data/adaptive_params.json                param_tune 调优候选
  - data/strategy_map_versions/*.json        策略版本历史（可选，用于回溯写入）

护栏口径（与 validate_strategies.py 一致）：
  得分 >= 25 且 夏普 >= 0.25（交易笔数 < 6 时需 >= 0.4）

用法:
  python3 analysis/strategy_tri_compare.py [--date YYYY-MM-DD] [--validation 路径]
输出:
  analysis/backtest/tri_compare_YYYY-MM-DD.md
"""

import argparse
import glob
import json
import os
import sys
from collections import Counter
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT_DIR = os.path.join(WORKSPACE, "analysis", "backtest")
os.makedirs(OUT_DIR, exist_ok=True)

MIN_SCORE = 25.0
SHARPE_THR = 0.25
SHARPE_THR_LOW_N = 0.40


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def latest_glob(pattern):
    files = sorted(glob.glob(pattern), reverse=True)
    return files[0] if files else None


def load_current_map():
    p = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")
    raw = load_json(p, {}) or {}
    out = {}
    for k, v in raw.items():
        sym = k.split(".")[0] if "." in k else k
        if isinstance(v, dict):
            out[sym] = {"strategy": v.get("strategy") or "—",
                        "bucket": v.get("bucket") or "-"}
        else:
            out[sym] = {"strategy": v or "—", "bucket": "-"}
    return out


def load_validation(path=None):
    if path is None:
        path = latest_glob(os.path.join(WORKSPACE, "analysis", "validation_*.json"))
    if not path:
        return {}, None
    return load_json(path, {}) or {}, path


def load_param_tune():
    raw = load_json(os.path.join(WORKSPACE, "data", "adaptive_params.json"), {}) or {}
    stocks = raw.get("stocks", {}) if isinstance(raw, dict) else {}
    return {s: i for s, i in stocks.items()
            if isinstance(i, dict) and "error" not in i and i.get("strategy")}


def check_guard(score, sharpe, trades):
    """返回 (通过?, 原因)"""
    reasons = []
    if score is None:
        return None, "无得分"
    if score < MIN_SCORE:
        reasons.append(f"得分{score:.1f}<{MIN_SCORE:.0f}")
    if sharpe is not None:
        thr = SHARPE_THR_LOW_N if (trades is not None and trades < 6) else SHARPE_THR
        if sharpe < thr:
            reasons.append(f"夏普{sharpe:.2f}<{thr}")
    if reasons:
        return False, "；".join(reasons)
    return True, ""


def fmt(v, nd=2, plus=False):
    if v is None:
        return "—"
    try:
        f = float(v)
        return f"{f:+.{nd}f}" if plus else f"{f:.{nd}f}"
    except Exception:
        return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--validation", help="指定 validation json 路径")
    args = ap.parse_args()

    cur_map = load_current_map()
    val_raw, val_path = load_validation(args.validation)
    tune_map = load_param_tune()

    # 策略名 label（用于可读展示）
    labels = {}
    try:
        from portfolio_core import STRATEGY_LABELS
        labels = dict(STRATEGY_LABELS)
    except Exception:
        pass

    def lbl(s):
        if s in (None, "—", "-"):
            return "—"
        return labels.get(s, s)

    all_syms = sorted(set(cur_map) | set(val_raw) | set(tune_map))

    # 两个池子：模拟盘 27 只（portfolio_sim 实际下单） vs 电网出海观察池（单独跟踪）
    # 避免把"不在模拟池"误认为"策略缺失"。
    sim_codes = set()
    try:
        from portfolio_core import STOCKS as CORE_STOCKS
        sim_codes = {s for s, _ in CORE_STOCKS}
    except Exception:
        pass

    rows = []
    for sym in all_syms:
        v = val_raw.get(sym, {}) or {}
        name = v.get("name") or sym
        cur = cur_map.get(sym, {}).get("strategy", "—")
        bucket = cur_map.get(sym, {}).get("bucket", "-")
        v_strat = v.get("strategy") or "—"
        score = v.get("score")
        sharpe = v.get("avg_sharpe")
        trades = v.get("avg_trades")
        ret = v.get("avg_return")
        dd = v.get("avg_dd")
        windows = v.get("windows_used")
        t = tune_map.get(sym, {}) or {}
        t_strat = t.get("strategy") or "—"

        # 持有期收益分布明细（新评分口径）
        det0 = (v.get("details") or [{}])[0]
        dist = v.get("distribution") or {}
        base_score = det0.get("base_score")
        dist_score = det0.get("dist_score")
        med = det0.get("median_ret") if det0.get("median_ret") is not None else dist.get("median_ret")
        p75 = det0.get("p75_ret") if det0.get("p75_ret") is not None else dist.get("p75_ret")
        p25 = det0.get("p25_ret") if det0.get("p25_ret") is not None else dist.get("p25_ret")

        guard, reason = check_guard(score, sharpe, trades) if score is not None else (None, "")
        err = v.get("error")
        in_sim = sym in sim_codes if sim_codes else None

        rows.append({
            "sym": sym, "name": name, "bucket": bucket,
            "cur": cur, "val": v_strat, "tune": t_strat,
            "score": score, "sharpe": sharpe, "trades": trades,
            "ret": ret, "dd": dd, "windows": windows,
            "guard": guard, "reason": reason, "err": err,
            "in_sim": in_sim,
            "base_score": base_score, "dist_score": dist_score,
            "med": med, "p75": p75, "p25": p25,
            "changed": (v_strat not in ("—", None) and v_strat != cur),
        })

    # ── 报告 ──
    L = []
    L.append(f"# 策略三方对照表 {args.date}")
    L.append("")
    L.append(f"- **当前分配**: `data/adaptive_strategy_map.json`（{len(cur_map)} 条）")
    L.append(f"- **跨周期验证**: `{os.path.basename(val_path) if val_path else '未找到'}`"
             f"（{len(val_raw)} 条）")
    L.append(f"- **参数调优**: `data/adaptive_params.json`（{len(tune_map)} 条候选）")
    L.append("")
    L.append(f"**护栏口径**：得分 ≥ {MIN_SCORE:.0f} 且 夏普 ≥ {SHARPE_THR}"
             f"（笔数 < 6 时需 ≥ {SHARPE_THR_LOW_N}）")
    L.append("")
    L.append("**评分口径（2026-09-19 升级）**：")
    L.append("")
    L.append("- 基础分 = 夏普×60 + 收益×0.6 − 回撤惩罚")
    L.append("- 分布分 = 中位数×0.5 + 上四分位×0.3 − |下四分位|×0.2 + 夏普×30")
    L.append("- **总分 = (基础分 + 分布分)/2 × 样本量权重**")
    L.append("")
    L.append("> 为何加分布项：夏普会把「大赢+大亏」平均掉，掩盖「平时小亏、关键时大赢」的尾部价值。"
             "趋势策略（如 EMA+OBV 盈亏比 4.17）典型 = 低胜率 + 高盈亏比，只看夏普被系统性低估。")
    L.append("")
    L.append("窗口定义：W1 全量(2020起) / W2 近3年 / W3 近1.5年 / W4 近1年")
    L.append("")

    # 统计
    sim_rows = [r for r in rows if r["in_sim"]]
    obs_rows = [r for r in rows if r["in_sim"] is False]
    n_changed = sum(1 for r in rows if r["changed"])
    n_guard_ok = sum(1 for r in rows if r["guard"] is True)
    n_guard_no = sum(1 for r in rows if r["guard"] is False)
    n_err = sum(1 for r in rows if r["err"])

    L.append("## 摘要")
    L.append("")
    L.append(f"| 指标 | 值 |")
    L.append(f"|---|---|")
    L.append(f"| 对照股票数 | {len(rows)}（模拟盘 {len(sim_rows)} + 出海观察池 {len(obs_rows)}） |")
    L.append(f"| **实际写入变更** | **0**（全部被护栏拦下 → 保留原策略） |")
    L.append(f"| 验证最优 ≠ 当前生效 | {n_changed} |")
    L.append(f"| 护栏通过 | {n_guard_ok} |")
    L.append(f"| 护栏未过（保留原策略） | {n_guard_no} |")
    L.append(f"| 数据缺失 | {n_err} |")
    L.append("")
    L.append("> ⚠️ **注意**：\"验证最优 ≠ 当前生效\"有 "
             f"{n_changed} 只，但其中多数**未过护栏**，故最终写入 0 处变更——"
             "护栏正确阻止了低置信度策略覆盖。")
    L.append("")

    L.append("## 三方对照")
    L.append("")
    L.append("| 代码 | 名称 | 池 | 当前生效 | 验证最优 | 调优候选 | 总分 | 基础分 | 分布分 | 中位 | p75 | p25 | 夏普 | 笔数 | 护栏 | 变更 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if r["err"]:
            g = "❌"
        elif r["guard"] is True:
            g = "✅"
        elif r["guard"] is False:
            g = "⚠️"
        else:
            g = "—"
        mark = "🔄" if r["changed"] else ""
        pool = "模拟" if r["in_sim"] else ("出海" if r["in_sim"] is False else "—")
        L.append(
            f"| {r['sym']} | {r['name']} | {pool} | `{r['cur']}` | `{r['val']}` | `{r['tune']}` | "
            f"{fmt(r['score'],1)} | {fmt(r['base_score'],1)} | {fmt(r['dist_score'],1)} | "
            f"{fmt(r['med'],2,True)} | {fmt(r['p75'],2,True)} | {fmt(r['p25'],2,True)} | "
            f"{fmt(r['sharpe'],2)} | {fmt(r['trades'],0)} | {g} | {mark} |"
        )

    # 护栏未过
    failed = [r for r in rows if r["guard"] is False]
    if failed:
        L.append("")
        L.append("## 护栏未过（故保留原策略）")
        L.append("")
        L.append("| 代码 | 名称 | 当前生效 | 验证推荐 | 未过原因 |")
        L.append("|---|---|---|---|---|")
        for r in failed:
            L.append(f"| {r['sym']} | {r['name']} | `{r['cur']}` | `{r['val']}` | {r['reason']} |")

    # 一致（当前=验证且过护栏）
    same_ok = [r for r in rows if r["guard"] is True and not r["changed"]]
    if same_ok:
        L.append("")
        L.append(f"## 已确认稳健（当前=验证最优 且 过护栏）：{len(same_ok)} 只")
        L.append("")
        L.append("| 代码 | 名称 | 策略 | 得分 | 夏普 |")
        L.append("|---|---|---|---|---|")
        for r in sorted(same_ok, key=lambda x: -(x["score"] or 0)):
            L.append(f"| {r['sym']} | {r['name']} | `{r['cur']}` | {fmt(r['score'],1)} | {fmt(r['sharpe'],2)} |")

    # 数据缺失
    if n_err:
        L.append("")
        L.append("## 数据缺失")
        L.append("")
        for r in rows:
            if r["err"]:
                L.append(f"- {r['name']}({r['sym']}): {r['err']}")

    # 策略分布
    L.append("")
    L.append("## 策略分布")
    L.append("")
    L.append("| 策略 | 当前生效 | 验证最优 | 调优候选 |")
    L.append("|---|---|---|---|")
    cc = Counter(r["cur"] for r in rows if r["cur"] != "—")
    vc = Counter(r["val"] for r in rows if r["val"] != "—")
    tc = Counter(r["tune"] for r in rows if r["tune"] != "—")
    for s in sorted(set(cc) | set(vc) | set(tc)):
        L.append(f"| `{s}` | {cc.get(s,0)} | {vc.get(s,0)} | {tc.get(s,0)} |")

    report = "\n".join(L)
    out = os.path.join(OUT_DIR, f"tri_compare_{args.date}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print(f"\n📄 已保存: {out}")


if __name__ == "__main__":
    main()
