#!/usr/bin/env python3
"""bucket==core 永假修复（C→A 方案）前置对照回放（2026-10-20）。

背景：pos["bucket"] 存的是 tier 词表（tier1/2/3），而单股限额判据全写
`== "core"`（L863/L966/L1088/L1151）→ 永假 → 全池按 satellite 5%（5,000 元）
跑，classify_bucket() 的 core/satellite 分类结果从未进入仓位链路。

A 方案：classify_bucket 结果写入 pos["bucket"]（24 core=15% / 12 sat=5%），
tier 词表另立 pos["tier"] 供 _apply_tier_filter。本脚本在动刀前量化：
同信号流下，现行 5k 全池 vs A 方案分桶定容的组合前向损益差异。

口径（对齐 cluster_cap_counterfactual.py）：
  - 信号流 = dual_signal_eval_days.json（双策略口径，生产信号融合源头）
  - 持有模型 = 10 交易日（同票 held 期内不重复建仓，两臂一致）
  - 单笔损益 = size × fwd − size × round_trip_cost
  - 三臂：current(5k 全池) / planA(core 15k, sat 5k) / planA_poweronly(仅 8 票电力出海 core)
  - 总仓上限（core 80% / sat 20%）两臂均实现，按事件流累计检验是否触界
输出：analysis/research/<今日>_bucket_cap_comparison.{json,md}
"""
import os
import sys
import json
import glob
from datetime import date

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np

from analysis.portfolio_core import (
    classify_bucket, COMMISSION, SLIPPAGE, INITIAL_CAPITAL,
    REFERENCE_TOTAL_CAPITAL,
)
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


EVAL = os.path.join(WORKSPACE, "analysis", "research", "dual_signal_eval_days.json")
HOLD_DAYS = 10
WINSOR = 0.15          # 单笔前向收益稳健化剪裁（±15%，对齐组件审计口径）
POWER_OVERSEAS = {"600089", "600406", "000400", "601179",
                  "600312", "002028", "002270", "002130"}


def latest_plan():
    files = sorted(glob.glob(os.path.join(WORKSPACE, "data", "position_plan_*.json")),
                   key=os.path.getmtime)
    if not files:
        return {"single_stock_max_amount": 100_000.0, "buy_signal_multiplier": 1.0}, None
    with open(files[-1]) as f:
        return json.load(f), os.path.basename(files[-1])


def size_of(sym, bucket_map, plan, arm):
    """单笔买入额 = min(现金, plan上限, single_cap)。现金按单票桶 100k。"""
    max_amount = float(plan.get("single_stock_max_amount", 100_000)) * \
        float(plan.get("buy_signal_multiplier", 1.0))
    cash = INITIAL_CAPITAL
    if arm == "current":
        cap = INITIAL_CAPITAL * 0.05          # 现行：bucket∈tier词表 → ==core 永假 → 全 5%
    elif arm == "planA":
        cap = INITIAL_CAPITAL * (0.15 if bucket_map.get(sym) == "core" else 0.05)
    elif arm == "planA_poweronly":
        cap = INITIAL_CAPITAL * (0.15 if sym in POWER_OVERSEAS else 0.05)
    elif arm == "all15k":
        cap = INITIAL_CAPITAL * 0.15              # 安慰剂：不分类全池放大
    else:
        raise ValueError(arm)
    return min(cash, max_amount, cap)


def main() -> int:
    recs = json.load(open(EVAL))
    plan, plan_file = latest_plan()
    round_trip = 2 * (COMMISSION + SLIPPAGE)
    bucket_map = {s: classify_bucket(s)[0] for s in
                  sorted({r["symbol"] for r in recs})}

    buys = [r for r in recs if any("买入" in a for a in r.get("actions", []))]
    buys.sort(key=lambda r: (r["date"], r["symbol"]))

    print(f"[cmp] 信号流 {len(recs)} 条 | 买入 {len(buys)} 笔 | plan={plan_file} "
          f"(max_amount={float(plan.get('single_stock_max_amount', 0)) * float(plan.get('buy_signal_multiplier', 1)):,.0f})"
          f" | round_trip_cost={round_trip:.4f}", flush=True)
    n_core = sum(1 for s in bucket_map if bucket_map[s] == "core")
    print(f"[cmp] classify_bucket: core {n_core} / sat {len(bucket_map) - n_core}", flush=True)

    arms = ["current", "planA", "planA_poweronly", "all15k"]
    trades = {a: [] for a in arms}
    held = {a: {} for a in arms}
    deployed = {a: {"core": 0.0, "sat": 0.0} for a in arms}
    cap_hits = {a: [] for a in arms}
    core_limit = REFERENCE_TOTAL_CAPITAL * 0.80
    sat_limit = REFERENCE_TOTAL_CAPITAL * 0.20
    # 日序索引（日期字符串不可加法，对齐 counterfactual 的 day_ix 口径）
    all_days = sorted({r["date"] for r in recs})
    day_ix = {d: i for i, d in enumerate(all_days)}

    for r in buys:
        sym, d = r["symbol"], r["date"]
        i = day_ix.get(d)
        if i is None:
            continue
        fwd5 = (r.get("fwd") or {}).get("5")
        fwd10 = (r.get("fwd") or {}).get("10")
        if fwd5 is None:
            continue
        for a in arms:
            expired = [o for o, (e, _sz, _c) in held[a].items() if e <= i]
            for o in expired:
                e, osz, ocls = held[a].pop(o)
                deployed[a][ocls] -= osz
            if sym in held[a]:
                continue
            cls = "core" if (bucket_map.get(sym) == "core" if a == "planA"
                             else sym in POWER_OVERSEAS if a == "planA_poweronly"
                             else False) else "sat"
            sz = size_of(sym, bucket_map, plan, a)
            # A 臂总仓上限检查（80/20）：现行臂 bucket 恒 sat 语义，同式核对
            lim = core_limit if cls == "core" else sat_limit
            if deployed[a][cls] + sz > lim:
                cap_hits[a].append({"symbol": sym, "date": d, "cls": cls})
                continue
            deployed[a][cls] += sz
            held[a][sym] = (i + HOLD_DAYS, sz, cls)
            pnl5 = sz * (fwd5 - round_trip)
            pnl10 = sz * (fwd10 - round_trip) if fwd10 is not None else None
            fw5w = float(np.clip(fwd5, -WINSOR, WINSOR))
            trades[a].append({"symbol": sym, "name": r.get("name"), "date": d,
                              "size": sz, "cls": cls, "fwd5": fwd5, "fwd10": fwd10,
                              "fwd5_w": fw5w, "pnl5": pnl5, "pnl10": pnl10})

    out = {"date": date.today().isoformat(), "plan_file": plan_file,
           "hold_days": HOLD_DAYS, "total_buys": len(buys),
           "round_trip_cost": round_trip,
           "bucket_split": {"core": n_core, "satellite": len(bucket_map) - n_core},
           "arms": {}}
    rng = np.random.default_rng(42)
    cur = trades["current"]
    for a in arms:
        ts = trades[a]
        pnl5s = np.array([t["pnl5"] for t in ts])
        pnl5w = np.array([t["size"] * (t["fwd5_w"] - round_trip) for t in ts])
        boot = None
        if a != "current" and cur:
            # 配对（同事件）bootstrap：Δpnl = A臂 - current臂
            cur_map = {(t["symbol"], t["date"]): t["pnl5"] for t in cur}
            pairs = [(t["pnl5"] - cur_map[(t["symbol"], t["date"])])
                     for t in ts if (t["symbol"], t["date"]) in cur_map]
            if len(pairs) >= 5:
                p = np.array(pairs)
                db = [float(rng.choice(p, len(p)).mean()) for _ in range(4000)]
                lo, hi = np.percentile(db, [2.5, 97.5])
                boot = {"n_pairs": int(len(p)), "mean_diff": round(float(p.mean()), 2),
                        "ci95": [round(float(lo), 2), round(float(hi), 2)],
                        "p_gt0": round(float(np.mean(np.array(db) > 0)), 3)}
        boot_vs = {}
        if a not in ("current", "planA") and trades["planA"]:
            pla_map = {(t["symbol"], t["date"]): t["pnl5"] for t in trades["planA"]}
            pairs = [(t["pnl5"] - pla_map[(t["symbol"], t["date"])])
                     for t in ts if (t["symbol"], t["date"]) in pla_map]
            if len(pairs) >= 5:
                p = np.array(pairs)
                db = [float(rng.choice(p, len(p)).mean()) for _ in range(4000)]
                lo, hi = np.percentile(db, [2.5, 97.5])
                boot_vs["vs_planA"] = {"n_pairs": int(len(p)),
                                        "mean_diff": round(float(p.mean()), 2),
                                        "ci95": [round(float(lo), 2), round(float(hi), 2)],
                                        "p_gt0": round(float(np.mean(np.array(db) > 0)), 3)}
        # 分年
        by_year = {}
        for t in ts:
            y = t["date"][:4]
            by_year.setdefault(y, {"n": 0, "pnl5": 0.0})
            by_year[y]["n"] += 1
            by_year[y]["pnl5"] += t["pnl5"]
        out["arms"][a] = {
            "n": len(ts),
            "deployed": {k: round(v, 0) for k, v in deployed[a].items()},
            "cap_hits": len(cap_hits[a]),
            "pnl5_total": round(float(pnl5s.sum()), 0) if len(pnl5s) else 0,
            "pnl5_winsor_total": round(float(pnl5w.sum()), 0) if len(pnl5w) else 0,
            "pnl5_mean": round(float(pnl5s.mean()), 2) if len(pnl5s) else None,
            "pnl5_median": round(float(np.median(pnl5s)), 2) if len(pnl5s) else None,
            "by_year": {y: {"n": v["n"], "pnl5": round(v["pnl5"], 0)}
                        for y, v in sorted(by_year.items())},
            "vs_current_boot": boot,
            "vs_other_boot": boot_vs,
        }
        # 效率口径：每元成交额的前向损益（分类价值 = 选股效率，非绝对 pnl）
        notional = sum(t["size"] for t in ts)
        out["arms"][a]["notional_total"] = notional
        out["arms"][a]["pnl5_per_yuan"] = (round(out["arms"][a]["pnl5_total"] / notional, 5)
                                            if notional else None)
        out["arms"][a]["pnl5w_per_yuan"] = (round(out["arms"][a]["pnl5_winsor_total"] / notional, 5)
                                              if notional else None)
        print(f"[cmp] {a:16s} n={len(ts)} deployed={deployed[a]} "
              f"pnl5={out['arms'][a]['pnl5_total']:,.0f} "
              f"winsor={out['arms'][a]['pnl5_winsor_total']:,.0f} "
              f"cap_hits={len(cap_hits[a])}", flush=True)
    # 敏感性：core 命中即 3× 的贡献分解（planA vs current 逐笔）
    cur_map = {(t["symbol"], t["date"]): t for t in cur}
    delta_core = [t["pnl5"] - cur_map[(t["symbol"], t["date"])]["pnl5"]
                  for t in trades["planA"]
                  if (t["symbol"], t["date"]) in cur_map and t["cls"] == "core"]
    out["planA_delta_from_core_buys"] = round(float(np.sum(delta_core)), 0) if delta_core else 0
    out["planA_core_buys_n"] = len(delta_core)
    print(f"[cmp] planA 增量来自 core 3× 部分: {out['planA_delta_from_core_buys']:,.0f} "
          f"(n={len(delta_core)})", flush=True)

    # 分类价值直接检验：core vs satellite 买入事件的前向收益（不依赖仓位）
    core_f = [t["fwd5"] for t in trades["planA"] if t["cls"] == "core"]
    sat_f = [t["fwd5"] for t in trades["planA"] if t["cls"] == "sat"]
    if len(core_f) >= 5 and len(sat_f) >= 5:
        c, s = np.array(core_f), np.array(sat_f)
        db = [float(rng.choice(c, len(c)).mean() - rng.choice(s, len(s)).mean())
              for _ in range(4000)]
        lo, hi = np.percentile(db, [2.5, 97.5])
        out["core_vs_sat_fwd5"] = {
            "core_n": len(core_f), "core_mean_pct": round(float(c.mean()) * 100, 3),
            "sat_n": len(sat_f), "sat_mean_pct": round(float(s.mean()) * 100, 3),
            "diff_pct": round(float(c.mean() - s.mean()) * 100, 3),
            "ci95_pct": [round(float(lo) * 100, 3), round(float(hi) * 100, 3)],
        }
        print(f"[cmp] core fwd5 {out['core_vs_sat_fwd5']['core_mean_pct']}% vs sat "
              f"{out['core_vs_sat_fwd5']['sat_mean_pct']}% diff "
              f"{out['core_vs_sat_fwd5']['diff_pct']}% CI{out['core_vs_sat_fwd5']['ci95_pct']}", flush=True)

    jpath = os.path.join(WORKSPACE, "analysis", "research",
                         f"{date.today().isoformat()}_bucket_cap_comparison.json")
    _bak(jpath)
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    md = [f"# bucket==core 永假修复 C 阶段对照（{date.today().isoformat()}）", "",
          f"- 信号流：dual_signal_eval_days.json，买入 {len(buys)} 笔，持有 {HOLD_DAYS} 日，"
          f"成本 round-trip {round_trip:.4f}（plan={plan_file}）",
          f"- 分桶：classify_bucket → core {n_core} / satellite {len(bucket_map) - n_core}", ""]
    md.append("| 臂 | 笔数 | 部署 core/sat | pnl5 合计 | winsor 后 | 分年 |")
    md.append("|---|---|---|---|---|---|")
    for a in arms:
        ar = out["arms"][a]
        yrs = " / ".join(f"{y}:{v['pnl5']:,.0f}({v['n']})" for y, v in ar["by_year"].items())
        md.append(f"| {a} | {ar['n']} | {ar['deployed']['core']:,.0f}/{ar['deployed']['sat']:,.0f} "
                  f"| {ar['pnl5_total']:,.0f} | {ar['pnl5_winsor_total']:,.0f} | {yrs} |")
    md.append("")
    md.append("| 臂 | 成交额 | pnl5/元 | winsor/元 |")
    md.append("|---|---|---|---|")
    for a in arms:
        ar = out["arms"][a]
        md.append(f"| {a} | {ar['notional_total']:,.0f} | {ar['pnl5_per_yuan']:.4f} | {ar['pnl5w_per_yuan']:.4f} |")
    md.append("")
    for a in ("planA", "planA_poweronly", "all15k"):
        b = out["arms"][a].get("vs_current_boot")
        if b:
            md.append(f"- **{a} vs current**（配对 bootstrap，Δpnl5/笔）："
                      f"{b['mean_diff']:+,.0f} 元 CI[{b['ci95'][0]:,.0f}, {b['ci95'][1]:,.0f}]，"
                      f"P(Δ>0)={b['p_gt0']}，n={b['n_pairs']}")
        v = out["arms"][a].get("vs_other_boot", {}).get("vs_planA")
        if v:
            md.append(f"- **{a} vs planA**：{v['mean_diff']:+,.0f} 元 CI[{v['ci95'][0]:,.0f}, {v['ci95'][1]:,.0f}]，"
                      f"P(Δ>0)={v['p_gt0']}")
    cv = out.get("core_vs_sat_fwd5")
    if cv:
        md.append(f"- **分类价值直接检验**：core 买入 fwd5 {cv['core_mean_pct']}%（n={cv['core_n']}）"
                  f" vs satellite {cv['sat_mean_pct']}%（n={cv['sat_n']}），"
                  f"差 {cv['diff_pct']}% CI{cv['ci95_pct']}")
    md.append(f"- planA 增量中来自 core 3× 放大部分：{out['planA_delta_from_core_buys']:,.0f} 元"
              f"（{out['planA_core_buys_n']} 笔 core 买入）")
    md.append("- 总仓上限 80/20：三臂触界次数 " +
              ", ".join(f"{a}={len(cap_hits[a])}" for a in arms))
    mpath = jpath.replace(".json", ".md")
    _bak(mpath)
    with open(mpath, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"[cmp] 已写 {jpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
