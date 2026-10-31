#!/usr/bin/env python3
"""B1: 分歧买 + 分歧对撞 事件研究（分年同年基线）——补 dual_signal_event_study 未覆盖的两类。

数据源: analysis/research/dual_signal_eval_days.json（21068 评估日，本地落盘，零网络）。
口径:
- 超额 = 信号类 fwd 均值 - 同年全样本(无条件) fwd 均值（2026-10-17 方法论: 事件研究分年超额必须用同年基线）
- 胜率 = fwd > 0 占比
- regime 拆分: er20 >= 0.25 (trend) vs < 0.25 (chop)，与已上线的 ER20 门控同口径
输出: analysis/research/dual_signal_disagree_buy_study.json + stdout 摘要
"""
import os
import sys
import json
from collections import defaultdict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import logging
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("disagree_buy_study")

EVAL_JSON = os.path.join(WORKSPACE, "analysis/research/dual_signal_eval_days.json")
OUT_JSON = os.path.join(WORKSPACE, "analysis/research/dual_signal_disagree_buy_study.json")
HORIZONS = ("1", "5", "10")
TARGET_CLASSES = ("分歧买", "分歧对撞")
ER_TREND_THRESHOLD = 0.25


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def win_rate(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return sum(1 for x in xs if x > 0) / len(xs)


def year_of(date_str):
    return date_str[:4]


def main():
    with open(EVAL_JSON) as f:
        rows = json.load(f)
    log.info("载入 %d 评估日", len(rows))

    # 同年无条件基线（全部评估日，不分信号类）
    base_by_year = defaultdict(lambda: {h: [] for h in HORIZONS})
    for r in rows:
        y = year_of(r["date"])
        for h in HORIZONS:
            v = r.get("fwd", {}).get(h)
            if v is not None:
                base_by_year[y][h].append(v)

    base = {}
    for y, hm in base_by_year.items():
        base[y] = {h: {"mean": mean(v), "win": win_rate(v), "n": len(v)} for h, v in hm.items()}

    results = {}
    for cls in TARGET_CLASSES:
        sub = [r for r in rows if r["cls"] == cls]
        if not sub:
            results[cls] = {"count": 0}
            continue
        info = {"count": len(sub), "by_year": {}, "by_regime": {}, "pre_5d": {}}
        for y in sorted({year_of(r["date"]) for r in sub}):
            ys = [r for r in sub if year_of(r["date"]) == y]
            info["by_year"][y] = {
                "n": len(ys),
                "fwd": {h: mean([r.get("fwd", {}).get(h) for r in ys]) for h in HORIZONS},
                "win": {h: win_rate([r.get("fwd", {}).get(h) for r in ys]) for h in HORIZONS},
            }
            info["by_year"][y]["excess"] = {
                h: (
                    info["by_year"][y]["fwd"][h] - base[y][h]["mean"]
                    if info["by_year"][y]["fwd"][h] is not None and base[y][h]["mean"] is not None
                    else None
                )
                for h in HORIZONS
            }
        # regime 拆分（ER20 门控同口径）
        for label, cond in (
            ("trend(er>=0.25)", lambda r: (r.get("er20") or 0) >= ER_TREND_THRESHOLD),
            ("chop(er<0.25)", lambda r: (r.get("er20") or 0) < ER_TREND_THRESHOLD),
        ):
            rs = [r for r in sub if cond(r)]
            info["by_regime"][label] = {
                "n": len(rs),
                "fwd5": mean([r.get("fwd", {}).get("5") for r in rs]),
                "win5": win_rate([r.get("fwd", {}).get("5") for r in rs]),
            }
        # 前置 5 日语境（动量确认型 vs 抄底型）
        info["pre_5d"] = {
            "mean": mean([r.get("pre_5d") for r in sub]),
            "p10": (
                sorted(x for x in (r.get("pre_5d") for r in sub) if x is not None)[
                    max(0, int(0.1 * len(sub)))
                ]
                if sub
                else None
            ),
            "p90": (
                sorted(x for x in (r.get("pre_5d") for r in sub) if x is not None)[
                    min(len(sub) - 1, int(0.9 * len(sub)))
                ]
                if sub
                else None
            ),
        }
        results[cls] = info

    # 对照组: 分歧卖（已验证的稳定信号，用于同口径参照）
    ref = [r for r in rows if r["cls"] == "分歧卖"]
    ref_info = {}
    for y in sorted({year_of(r["date"]) for r in ref}):
        ys = [r for r in ref if year_of(r["date"]) == y]
        ref_info[y] = {"n": len(ys), "excess5": mean([r.get("fwd", {}).get("5") for r in ys]) - base[y]["5"]["mean"]}
    results["_ref_分歧卖_by_year_excess5"] = ref_info
    results["_meta"] = {
        "source": "analysis/research/dual_signal_eval_days.json",
        "baseline": "同年全样本无条件(2026-10-17 方法论)",
        "er_threshold": ER_TREND_THRESHOLD,
        "range": [min(r["date"] for r in rows), max(r["date"] for r in rows)],
    }

    # 先落盘再打印（分析段崩溃不丢采集数据）
    _bak(OUT_JSON)
    with open(OUT_JSON, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    log.info("已落盘 %s", OUT_JSON)

    print("\n=====DISAGREE_BUY_STUDY=====")
    for cls in TARGET_CLASSES:
        info = results.get(cls, {})
        print(f"\n[{cls}] n={info.get('count')}")
        for y, yi in info.get("by_year", {}).items():
            e5 = yi["excess"]["5"]
            w5 = yi["win"]["5"]
            print(
                f"  {y}: n={yi['n']:5d} | fwd5={yi['fwd']['5']:+.2%} | 超额5d={e5:+.2%} | 胜率5d={w5:.0%}"
                + f" | 超额10d={yi['excess']['10']:+.2%}"
                if e5 is not None
                else f"  {y}: n={yi['n']} 数据不足"
            )
        for label, ri in info.get("by_regime", {}).items():
            if ri["n"]:
                print(f"  {label}: n={ri['n']} fwd5={ri['fwd5']:+.2%} 胜率={ri['win5']:.0%}")
        p = info.get("pre_5d", {})
        if p.get("mean") is not None:
            print(f"  前置5日: mean={p['mean']:+.2%} p10={p['p10']:+.2%} p90={p['p90']:+.2%}")
    print("\n[参照] 分歧卖分年超额5d:")
    for y, ri in ref_info.items():
        print(f"  {y}: n={ri['n']:5d} excess5={ri['excess5']:+.2%}")
    print("=====DISAGREE_BUY_END=====")


if __name__ == "__main__":
    main()
