#!/usr/bin/env python3
"""低开甜点深究：稳健性 → 机制 → 规则化（2026-10-20）。

背景
----
2026-10-20_gap_tier_execution 发现：分歧买信号次日跳空分层中，唯一显著为正的
开盘执行档 = gap < −1%（ex5_oc +1.96%，CI 下界 +0.53%，n=95）——「低开甜点」。
本脚本回答三个递进问题：

Q1 稳健性：低开甜点分年 / regime（trend vs chop）/ 幅度细分（<−3、−3~−2、−2~−1）
   是否稳定？还是单年/单 regime 驱动？
Q2 机制：低开日的盘中正漂移是**市场-wide 隔夜反转**（全体票日低开都这样），
   还是**信号交互**（只有分歧买信号+低开才好）？
   → 关键对照：同一 gap 桶内「分歧买 − 全体票日」差分（bootstrap CI）。
Q3 规则化：把甜点变成执行规则（跳过高开 / 只执行低开等），在分歧买全样本上
   模拟覆盖率 × 执行超额，并评估卖侧（分歧卖）在低开日开盘卖出的成本。

口径（全部继承 scripts/gap_tier_execution_test.py，单一来源防分叉）
----
* recs/gap/oc1/fwd5_cc/fwd5_oc/同年基线/分桶/CI 全部 import 复用该模块
* gap = open(T+1)/close(T) − 1；oc1 = close(T+1)/open(T+1) − 1
* ex5_oc = fwd5_oc − 同年全样本（全部 cls）均值
* bootstrap seed=42, n=3000
* 规则模拟以 T+1 开盘价可见为前提（开盘后决策），无前视
"""
import os
import sys
import json

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.net_guard import install_default_timeout
install_default_timeout(15)

import gap_tier_execution_test as gt   # 口径单一来源（build_recs/year_baseline/boot_ci/tier_of）
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


OUT_JSON = os.path.join(WORKSPACE, "analysis/research/2026-10-20_low_open_sweetspot.json")
OUT_MD = os.path.join(WORKSPACE, "analysis/research/2026-10-20_low_open_sweetspot.md")

FINE_TIERS = [  # 低开幅度细分（左闭右开，末档含右端）
    ("gap<-3%", None, -0.03),
    ("-3%~-2%", -0.03, -0.02),
    ("-2%~-1%", -0.02, -0.01),
    ("-1%~0%", -0.01, 0.0),
]


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def boot_diff(xs, ys, n=gt.BOOT_N, seed=gt.BOOT_SEED):
    """两组独立重采样均值差 CI（xs−ys）。"""
    import numpy as np
    if len(xs) < 20 or len(ys) < 20:
        return None
    a = np.asarray(xs, dtype=float)
    b = np.asarray(ys, dtype=float)
    rng = np.random.default_rng(seed)
    ia = rng.integers(0, len(a), size=(n, len(a)))
    ib = rng.integers(0, len(b), size=(n, len(b)))
    d = a[ia].mean(axis=1) - b[ib].mean(axis=1)
    return [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]


def bucket_stats(sub, bm_oc):
    n = len(sub)
    if n == 0:
        return {"n": 0}
    oc1s = [r["oc1"] for r in sub]
    xs_oc = [r["fwd5_oc"] - bm_oc[r["year"]] for r in sub if r["fwd5_oc"] is not None]
    return {
        "n": n,
        "oc1_mean": mean(oc1s), "oc1_win": gt.win_rate(oc1s), "oc1_ci": gt.boot_ci(oc1s),
        "n_fwd5": len(xs_oc),
        "ex5_oc_mean": mean(xs_oc), "ex5_oc_ci": gt.boot_ci(xs_oc),
    }


def main():
    recs, miss_cache, dropped = gt.build_recs()
    bm_cc, bm_oc = gt.year_baseline(recs)

    # ── regime 映射（eval 面板自带字段，(sym,date) → regime_er）──
    rows = json.load(open(gt.EVAL_JSON))
    regime = {(r["symbol"], r["date"]): r.get("regime_er") for r in rows}

    main_buy = [r for r in recs if r["cls"] == gt.KEY_CLASS]
    all_rows = recs

    # ── 恒等式自检（不过不得输出结论）──
    gap_mean = mean([r["gap"] for r in main_buy])
    ident = {
        "n_recs": len(recs), "n_eval_rows": len(rows), "dropped_no_next_bar": dropped,
        "miss_cache": miss_cache,
        "buy_n": len(main_buy),
        "buy_gap_mean": gap_mean,
        "ref_gap": gt.REF_GAP,
        "gap_abs_diff": abs(gap_mean - gt.REF_GAP),
        "gap_tol": gt.IDENT_TOL,
        "ok": abs(gap_mean - gt.REF_GAP) <= gt.IDENT_TOL and len(main_buy) == 984,
    }
    if not ident["ok"]:
        print(f"❌ 恒等式自检未过: {ident}")
        return 1
    print(f"恒等式自检 ✅（recs={len(recs)} 分歧买={len(main_buy)} gap={gap_mean:+.7f}）")

    out = {"meta": {"ident": ident,
                    "note": "全体对照含信号行自身（~6%），桶内差分偏保守（向 0 偏）"}}

    # ═══ Q1a：全体票日按 gap 分桶（市场-wide 隔夜反转画像）═══
    print("\n=== Q1a 全体票日 gap 分桶（市场基线）===")
    base_by_tier = {}
    for label, lo, hi in gt.TIERS:
        sub = [r for r in all_rows if (lo is None or r["gap"] >= lo) and (hi is None or r["gap"] < hi)]
        base_by_tier[label] = bucket_stats(sub, bm_oc)
        b = base_by_tier[label]
        print(f"  {label:>9} n={b.get('n',0):>5} oc1={gt.pct(b.get('oc1_mean'))} "
              f"ex5_oc={gt.pct(b.get('ex5_oc_mean'))} {gt.ci_s(b.get('ex5_oc_ci'))}")
    out["q1a_market_by_tier"] = base_by_tier

    # ═══ Q1b：分歧买低开幅度细分 ═══
    print("\n=== Q1b 分歧买低开细分 ===")
    fine = {}
    for label, lo, hi in FINE_TIERS:
        sub = [r for r in main_buy if (lo is None or r["gap"] >= lo) and (hi is None or r["gap"] < hi)]
        fine[label] = bucket_stats(sub, bm_oc)
        b = fine[label]
        print(f"  {label:>9} n={b.get('n',0):>4} oc1={gt.pct(b.get('oc1_mean'))} {gt.ci_s(b.get('oc1_ci'))} "
              f"ex5_oc={gt.pct(b.get('ex5_oc_mean'))} {gt.ci_s(b.get('ex5_oc_ci'))}")
    out["q1b_buy_fine_low"] = fine

    # 低开甜点（gap<-1%）分年
    lo_sub = [r for r in main_buy if r["gap"] < -0.01]
    lo_year = {}
    for y in ("2024", "2025", "2026"):
        sub = [r for r in lo_sub if r["year"] == y]
        lo_year[y] = bucket_stats(sub, bm_oc)
    out["q1b_sweetspot_by_year"] = lo_year
    print("  甜点分年:", {y: f"n={v.get('n',0)} ex5_oc={gt.pct(v.get('ex5_oc_mean'))}" for y, v in lo_year.items()})

    # 低开甜点 × regime
    lo_regime = {}
    for rg in ("trend", "chop"):
        sub = [r for r in lo_sub if regime.get((r["symbol"], r["date"])) == rg]
        lo_regime[rg] = bucket_stats(sub, bm_oc)
    out["q1b_sweetspot_by_regime"] = lo_regime
    print("  甜点×regime:", {k: f"n={v.get('n',0)} ex5_oc={gt.pct(v.get('ex5_oc_mean'))} {gt.ci_s(v.get('ex5_oc_ci'))}" for k, v in lo_regime.items()})

    # ═══ Q2：桶内「分歧买 − 全体」差分（机制归属）═══
    print("\n=== Q2 桶内差分：分歧买 − 全体（正=超出市场基线的信号交互）===")
    mech = {}
    for label, lo, hi in gt.TIERS:
        sb = [r for r in main_buy if (lo is None or r["gap"] >= lo) and (hi is None or r["gap"] < hi)]
        sa = [r for r in all_rows if (lo is None or r["gap"] >= lo) and (hi is None or r["gap"] < hi)]
        oc_b = [r["oc1"] for r in sb]
        oc_a = [r["oc1"] for r in sa]
        xb = [r["fwd5_oc"] - bm_oc[r["year"]] for r in sb if r["fwd5_oc"] is not None]
        xa = [r["fwd5_oc"] - bm_oc[r["year"]] for r in sa if r["fwd5_oc"] is not None]
        mech[label] = {
            "n_buy": len(sb), "n_all": len(sa),
            "delta_oc1": (mean(oc_b) - mean(oc_a)) if oc_b and oc_a else None,
            "delta_oc1_ci": boot_diff(oc_b, oc_a),
            "delta_ex5_oc": (mean(xb) - mean(xa)) if xb and xa else None,
            "delta_ex5_oc_ci": boot_diff(xb, xa),
        }
        m = mech[label]
        print(f"  {label:>9} Δoc1={gt.pct(m['delta_oc1'])} {gt.ci_s(m['delta_oc1_ci'])} "
              f"Δex5_oc={gt.pct(m['delta_ex5_oc'])} {gt.ci_s(m['delta_ex5_oc_ci'])}")
    out["q2_within_bucket_delta"] = mech

    # ═══ Q3a：执行规则模拟（分歧买，entry=T+1 open，决策基于开盘可见信息）═══
    print("\n=== Q3a 执行规则模拟（分歧买）===")
    elig = [r for r in main_buy if r["fwd5_oc"] is not None]
    rules = {
        "R0_全部执行": lambda g: True,
        "R1_跳过gap>+3%": lambda g: g < 0.03,
        "R2_跳过gap>+1%": lambda g: g < 0.01,
        "R3_仅低开执行": lambda g: g < 0.0,
        "R4_仅深低开执行": lambda g: g < -0.01,
    }
    rule_out = {}
    for name, pred in rules.items():
        ex = [r for r in elig if pred(r["gap"])]
        sk = [r for r in elig if not pred(r["gap"])]
        xs = [r["fwd5_oc"] - bm_oc[r["year"]] for r in ex]
        sks = [r["fwd5_oc"] - bm_oc[r["year"]] for r in sk]
        rule_out[name] = {
            "n_exec": len(ex), "coverage": len(ex) / len(elig) if elig else None,
            "exec_ex5_oc": mean(xs), "exec_ci": gt.boot_ci(xs),
            "n_skip": len(sk), "skip_ex5_oc": mean(sks),
        }
        r_ = rule_out[name]
        print(f"  {name:<12} 覆盖{r_['coverage']:.0%} 执行ex5_oc={gt.pct(r_['exec_ex5_oc'])} "
              f"{gt.ci_s(r_['exec_ci'])} 放弃组={gt.pct(r_['skip_ex5_oc'])} (n={r_['n_skip']})")
    out["q3a_rule_sim"] = rule_out

    # ═══ Q3b：卖侧对称性（分歧卖：低开日开盘卖出的成本）═══
    print("\n=== Q3b 卖侧：分歧卖 gap 分桶（oc1=开盘卖 vs 收盘卖的机会差）===")
    sells = [r for r in recs if r["cls"] == "分歧卖"]
    sell_out = {}
    for label, lo, hi in gt.TIERS:
        sub = [r for r in sells if (lo is None or r["gap"] >= lo) and (hi is None or r["gap"] < hi)]
        sell_out[label] = bucket_stats(sub, bm_oc)
        b = sell_out[label]
        print(f"  {label:>9} n={b.get('n',0):>5} oc1={gt.pct(b.get('oc1_mean'))} {gt.ci_s(b.get('oc1_ci'))}")
    out["q3b_sell_side"] = sell_out

    _bak(OUT_JSON)
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n📄 落盘: {os.path.relpath(OUT_JSON, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
