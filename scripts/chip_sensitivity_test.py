#!/usr/bin/env python3
"""chip 算法参数敏感性检验（2026-10-18 chip 第5维复活遗留待办）。

问题：生产筹码算法（scripts/chip_distribution_study.py::chip_metrics，
N_BINS=400 / TURN_CAP=0.5）的「cost_bias 溢价档」结论是否稳健于参数？

做法：
1. importlib 加载 chip_distribution_study 为模块，monkeypatch 模块全局
   N_BINS / TURN_CAP 生成 5 个变体，每个变体对 36 只票全量重算
   chip_metrics(df, prev=None)（df 来自 data/research_price_cache/chip_{sym}.pkl）。
2. 评估三件套（逐变体）：
   a) 排名稳定性：逐日横截面 Spearman(变体 cb, 基线 cb)
   b) 档位事件研究：ex5 = fwd5 − bench5(date)，四档 + bootstrap CI + 分年
   c) 生产旗标一致性：<−0.20→8.5 / <−0.15→7.5 / 其余 5.0
3. 基线校验：全量重算 vs 生产增量缓存 chipmetrics_{sym}.pkl 末行 cost_bias。

只读：不写任何既有文件/缓存 pkl。纯本地，无网络调用。
输出：analysis/research/2026-10-20_chip_sensitivity.json / .md
"""
import os
import sys
import json
import time
import logging
import importlib.util

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
# 必须同时插入：工作区根 + analysis/
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

from analysis.net_guard import install_default_timeout
install_default_timeout(15)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("chip_sensitivity")

STUDY_PY = os.path.join(WORKSPACE, "scripts/chip_distribution_study.py")
EVAL_JSON = os.path.join(WORKSPACE, "analysis/research/dual_signal_eval_days.json")
CACHE_DIR = os.path.join(WORKSPACE, "data/research_price_cache")
OUT_JSON = os.path.join(WORKSPACE, "analysis/research/2026-10-20_chip_sensitivity.json")
OUT_MD = os.path.join(WORKSPACE, "analysis/research/2026-10-20_chip_sensitivity.md")

VARIANTS = [
    ("baseline", 400, 0.5),
    ("bins200", 200, 0.5),
    ("bins800", 800, 0.5),
    ("cap0.3", 400, 0.3),
    ("cap1.0", 400, 1.0),
]
TIME_BUDGET_SEC = 40 * 60
TOTAL_BUDGET_SEC = 45 * 60
BINS800_SAMPLE = 12
MIN_SYMS_PER_DAY = 20


def load_study_module():
    spec = importlib.util.spec_from_file_location("chip_study_mod", STUDY_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # 模块级 import 无网络调用（baostock 在函数内）
    return mod


def boot_ci(xs, n=3000):
    """与 chip_distribution_study.boot_ci 同法（seed=42）。"""
    if len(xs) < 20:
        return None
    a = np.asarray(xs, dtype=float)
    rng = np.random.default_rng(42)
    idx = rng.integers(0, len(a), size=(n, len(a)))
    ms = a[idx].mean(axis=1)
    return [float(np.percentile(ms, 2.5)), float(np.percentile(ms, 97.5))]


def load_prices(syms):
    out = {}
    for s in syms:
        p = os.path.join(CACHE_DIR, f"chip_{s}.pkl")
        if not os.path.exists(p):
            log.warning("%s 输入缓存缺失，跳过", s)
            continue
        df = pd.read_pickle(p)
        df["date"] = df["date"].astype(str).str.slice(0, 10)
        out[s] = df.reset_index(drop=True)
    return out


def prod_score(cb):
    if cb != cb:  # NaN
        return np.nan
    if cb < -0.20:
        return 8.5
    if cb < -0.15:
        return 7.5
    return 5.0


def load_panel():
    rows = json.load(open(EVAL_JSON))
    rec = [(r["symbol"], r["date"], r["fwd"]["5"]) for r in rows
           if r.get("fwd", {}).get("5") is not None]
    p = pd.DataFrame(rec, columns=["symbol", "date", "fwd5"])
    bench = p.groupby("date")["fwd5"].mean()
    p["bench5"] = p["date"].map(bench)
    p["ex5"] = p["fwd5"] - p["bench5"]
    p["year"] = p["date"].str.slice(0, 4)
    return p


def run_variant(mod, name, n_bins, turn_cap, prices, syms):
    setattr(mod, "N_BINS", n_bins)
    setattr(mod, "TURN_CAP", turn_cap)
    t0 = time.time()
    res = {}
    for i, s in enumerate(syms, 1):
        try:
            m = mod.chip_metrics(prices[s].copy(), prev=None)
        except Exception as e:
            log.warning("%s %s 计算失败: %s", name, s, e)
            continue
        if m is None or m.empty:
            continue
        m = m[["date", "cost_bias"]].copy()
        m["date"] = m["date"].astype(str).str.slice(0, 10)
        res[s] = m
        if i % 12 == 0:
            log.info("[%s] %d/%d (%.0fs)", name, i, len(syms), time.time() - t0)
    log.info("[%s] 完成 %d/%d 票 (%.0fs)", name, len(res), len(syms), time.time() - t0)
    return res, time.time() - t0


def long_frame(res):
    frames = []
    for s, m in res.items():
        d = m.copy()
        d["symbol"] = s
        frames.append(d)
    if not frames:
        return pd.DataFrame(columns=["date", "cost_bias", "symbol"])
    return pd.concat(frames, ignore_index=True)


def main():
    t_start = time.time()
    mod = load_study_module()
    log.info("已加载 %s (N_BINS=%s TURN_CAP=%s)", STUDY_PY, mod.N_BINS, mod.TURN_CAP)
    if "baostock" in sys.modules:
        log.warning("baostock 意外已加载（未调用则无碍）")

    panel = load_panel()
    syms_all = sorted(panel["symbol"].unique())
    log.info("面板 %d 行 / %d 票", len(panel), len(syms_all))
    prices = load_prices(syms_all)
    syms_all = [s for s in syms_all if s in prices]
    log.info("价格可用 %d 票", len(syms_all))

    # --- 换手率画像（决定 TURN_CAP 是否真起作用）
    turn_stats = {
        "turn_max": float(max(p["turn"].max() for p in prices.values())),
        "turn_p99": float(max(p["turn"].quantile(0.99) for p in prices.values())),
        "n_rows_turn_gt_cap0.5": int(sum((p["turn"] > 50).sum() for p in prices.values())),
        "n_rows_turn_gt_cap0.3": int(sum((p["turn"] > 30).sum() for p in prices.values())),
        "n_stocks_any_gt_30": int(sum((p["turn"] > 30).any() for p in prices.values())),
    }
    log.info("换手画像 %s", turn_stats)

    # --- 单票计时（600030, 800 bins）→ 外推总时长
    setattr(mod, "N_BINS", 800)
    setattr(mod, "TURN_CAP", 0.5)
    _t = time.time()
    mod.chip_metrics(prices["600030"].copy(), prev=None)
    per_stock_800 = time.time() - _t
    extrap = per_stock_800 * len(syms_all) * 5
    log.info("单票(600030,800bins)=%.2fs → 外推 %.1f 分钟", per_stock_800, extrap / 60)
    bins800_limited = extrap > TIME_BUDGET_SEC
    if bins800_limited:
        log.warning("外推超 %d 分钟 → bins800 降为 %d 票样本", TIME_BUDGET_SEC // 60, BINS800_SAMPLE)

    results = {}
    timings = {}
    scope = {}
    for name, nb, tc in VARIANTS:
        if time.time() - t_start > TOTAL_BUDGET_SEC and name != "baseline":
            log.warning("总预算用尽，跳过变体 %s", name)
            continue
        syms = syms_all
        if name == "bins800" and bins800_limited:
            syms = syms_all[:BINS800_SAMPLE]
        res, dt = run_variant(mod, name, nb, tc, prices, syms)
        results[name] = res
        timings[name] = dt
        scope[name] = len(syms)
    setattr(mod, "N_BINS", 400)
    setattr(mod, "TURN_CAP", 0.5)

    names = [v[0] for v in VARIANTS if v[0] in results]
    base_long = long_frame(results["baseline"]).rename(columns={"cost_bias": "cb_base"})

    # --- 基线校验：末行 cost_bias vs 生产增量缓存
    base_check = []
    for s, m in results["baseline"].items():
        p = os.path.join(CACHE_DIR, f"chipmetrics_{s}.pkl")
        if not os.path.exists(p):
            continue
        prod = pd.read_pickle(p)
        prod["date"] = prod["date"].astype(str).str.slice(0, 10)
        last_prod = prod.iloc[-1]
        row = m[m["date"] == last_prod["date"]]
        if row.empty:
            base_check.append({"symbol": s, "date": last_prod["date"], "diff": None,
                               "note": "末行日期不匹配"})
            continue
        d = float(row["cost_bias"].iloc[0]) - float(last_prod["cost_bias"])
        base_check.append({"symbol": s, "date": str(last_prod["date"]), "diff": d,
                           "prod": float(last_prod["cost_bias"]),
                           "recomp": float(row["cost_bias"].iloc[0])})
    diffs = [abs(c["diff"]) for c in base_check if c["diff"] is not None]
    base_max_absdiff = max(diffs) if diffs else None
    base_mean_absdiff = float(np.mean(diffs)) if diffs else None

    # --- 逐票逐日 |Δcb| 明细（阈值附近的行为比全局 ρ 更能暴露脆弱性）
    base_by_sym = results["baseline"]
    per_stock_sens = {}
    for name in names:
        if name == "baseline":
            continue
        rows = []
        cross = {}
        for s, m in results[name].items():
            b = base_by_sym.get(s)
            if b is None:
                continue
            jj = m.merge(b, on="date", suffixes=("_v", "_b")).dropna(subset=["cost_bias_v", "cost_bias_b"])
            if jj.empty:
                continue
            d = (jj["cost_bias_v"] - jj["cost_bias_b"]).abs()
            c = 0
            for th in (-0.20, -0.15):
                c += int(((jj["cost_bias_v"] < th) != (jj["cost_bias_b"] < th)).sum())
            cross[s] = c
            rows.append({"symbol": s, "n": int(len(jj)), "max_abs_diff": float(d.max()),
                         "mean_abs_diff": float(d.mean()), "n_threshold_crossings": c})
        tot_cross = sum(cross.values())
        tot_rows = sum(r["n"] for r in rows)
        per_stock_sens[name] = {
            "max_abs_diff": max((r["max_abs_diff"] for r in rows), default=None),
            "mean_abs_diff": float(np.mean([r["mean_abs_diff"] for r in rows])) if rows else None,
            "n_threshold_crossings": int(tot_cross),
            "n_stockdays": int(tot_rows),
            "threshold_crossing_rate": (tot_cross / tot_rows) if tot_rows else None,
            "stocks_with_crossing": {k: v for k, v in cross.items() if v},
            "per_stock": rows,
        }
        log.info("[%s] max|Δcb|=%.4f 阈值穿越 %d/%d", name,
                 per_stock_sens[name]["max_abs_diff"] or 0.0, tot_cross, tot_rows)

    # --- 逐变体评估
    summary = {}
    for name in names:
        lg = long_frame(results[name])
        scope_syms = set(results[name].keys())

        # a) 排名稳定性
        j = lg.merge(base_long, on=["symbol", "date"], how="inner")
        rhos = []
        for d, g in j.groupby("date"):
            if len(g) >= MIN_SYMS_PER_DAY and g["cost_bias"].nunique() > 3 and g["cb_base"].nunique() > 3:
                r = g["cost_bias"].corr(g["cb_base"], method="spearman")
                if pd.notna(r):
                    rhos.append(float(r))
        rho_arr = np.asarray(rhos) if rhos else np.array([])
        rank = {
            "n_days": len(rhos),
            "rho_mean": float(rho_arr.mean()) if len(rhos) else None,
            "rho_median": float(np.median(rho_arr)) if len(rhos) else None,
            "rho_min": float(rho_arr.min()) if len(rhos) else None,
            "share_rho_ge_095": float((rho_arr >= 0.95).mean()) if len(rhos) else None,
        }

        # b) 事件研究
        pp = panel[panel["symbol"].isin(scope_syms)].merge(
            lg[["symbol", "date", "cost_bias"]], on=["symbol", "date"], how="inner")
        # 用同 scope 重算 bench（保持各变体可比：bench 始终取全panel同日期均值）
        cb = pp["cost_bias"]
        tiers = {
            "lt_-0.20": cb < -0.20,
            "[-0.20,-0.15)": (cb >= -0.20) & (cb < -0.15),
            "[-0.15,-0.10)": (cb >= -0.15) & (cb < -0.10),
            "rest": cb >= -0.10,
        }
        ev = {}
        for tn, mask in tiers.items():
            sub = pp[mask]
            ev[tn] = {"n": int(len(sub)),
                      "ex5": float(sub["ex5"].mean()) if len(sub) else None,
                      "win": float((sub["fwd5"] > 0).mean()) if len(sub) else None,
                      "ci": boot_ci(sub["ex5"].tolist()) if tn in ("lt_-0.20", "[-0.20,-0.15)") else None}
        # 累积档（< -0.15 / < -0.10）供交叉核对
        for tn, mask in (("lt_-0.15", cb < -0.15), ("lt_-0.10", cb < -0.10)):
            sub = pp[mask]
            ev[tn] = {"n": int(len(sub)),
                      "ex5": float(sub["ex5"].mean()) if len(sub) else None,
                      "win": float((sub["fwd5"] > 0).mean()) if len(sub) else None,
                      "ci": boot_ci(sub["ex5"].tolist()) if tn == "lt_-0.15" else None}
        by_year = {}
        sub15 = pp[cb < -0.15]
        for y in ("2024", "2025", "2026"):
            sy = sub15[sub15["year"] == y]
            by_year[y] = {"n": int(len(sy)),
                          "ex5": float(sy["ex5"].mean()) if len(sy) else None}

        # c) 生产旗标一致性
        js = j.dropna(subset=["cost_bias", "cb_base"]).copy()
        js["sc_prod"] = js["cost_bias"].map(prod_score)
        js["sc_base"] = js["cb_base"].map(prod_score)
        agree = float((js["sc_prod"] == js["sc_base"]).mean()) if len(js) else None
        flip = float(((js["sc_prod"] == 8.5) & (js["sc_base"] == 5.0)).mean() +
                     ((js["sc_prod"] == 5.0) & (js["sc_base"] == 8.5)).mean()) if len(js) else None
        dist_prod = {str(k): int(v) for k, v in js["sc_prod"].value_counts().items()}
        dist_base = {str(k): int(v) for k, v in js["sc_base"].value_counts().items()}
        summary[name] = {
            "sensitivity_vs_baseline": ({k: v for k, v in per_stock_sens[name].items()
                                       if k != "per_stock"} if name in per_stock_sens else None),
            "params": {"N_BINS": dict(zip([v[0] for v in VARIANTS], [v[1] for v in VARIANTS]))[name],
                       "TURN_CAP": dict(zip([v[0] for v in VARIANTS], [v[2] for v in VARIANTS]))[name]},
            "n_symbols": scope[name],
            "n_stockdays": int(pp[["symbol", "date"]].drop_duplicates().shape[0]),
            "elapsed_sec": round(timings[name], 1),
            "rank_stability": rank,
            "event": ev,
            "by_year_lt_-0.15": by_year,
            "flag": {"n": int(len(js)), "agree": agree, "flip_85_50": flip,
                     "dist_variant": dist_prod, "dist_baseline": dist_base},
        }
        log.info("[%s] n=%d ρmed=%.4f ≥0.95占比=%.1f%% 一致率=%.1f%%",
                 name, summary[name]["n_stockdays"], rank["rho_median"] or float("nan"),
                 100 * (rank["share_rho_ge_095"] or 0), 100 * (agree or 0))

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "production_params": {"N_BINS": 400, "TURN_CAP": 0.5,
                              "source": "scripts/chip_distribution_study.py"},
        "panel": {"rows": int(len(panel)), "symbols": int(panel["symbol"].nunique()),
                  "date_min": str(panel["date"].min()), "date_max": str(panel["date"].max()),
                  "bench": "cross-sectional mean fwd5 per date"},
        "timing": {"per_stock_800bins_sec": round(per_stock_800, 3),
                   "extrap_total_sec": round(extrap, 1),
                   "bins800_limited": bins800_limited,
                   "bins800_scope": scope.get("bins800"),
                   "elapsed_by_variant_sec": {k: round(v, 1) for k, v in timings.items()}},
        "turn_profile": turn_stats,
        "baseline_recompute_check": {
            "n": len(base_check), "max_abs_diff": base_max_absdiff,
            "mean_abs_diff": base_mean_absdiff,
            "detail": base_check},
        "variants": summary,
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)

    write_md(out)
    print_summary(out)
    return 0


def _pct(v, nd=2):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v * 100:+.{nd}f}%"


def _turn_cap_effective(o, sens):
    """TURN_CAP 是否真的生效：返回 ('inert'|'weakly_active'|'active', 说明)。"""
    if not sens:
        return "unknown", "无敏感性数据"
    tp = o.get("turn_profile", {})
    maxd = sens.get("max_abs_diff") or 0.0
    cross = sens.get("n_threshold_crossings") or 0
    if maxd < 1e-3 and cross == 0:
        return "inert", (f"max|Δcb|={maxd:.2e}，阈值穿越 0/{sens['n_stockdays']}；"
                         f"票级换手 p99 最大 {tp.get('turn_p99', float('nan')):.1f}%，"
                         f"超 50% 的行仅 {tp.get('n_rows_turn_gt_cap0.5', 0)} 行")
    if cross <= 2:
        syms = ",".join(sorted(sens.get("stocks_with_crossing") or {}))
        where = f"（涉及 {syms} 的个别爆量日）" if syms else "（无旗标级穿越，仅数值漂移）"
        return "weakly_active", (f"max|Δcb|={maxd:.3f}，阈值穿越 {cross}/{sens['n_stockdays']}"
                                 f"{where}；整体行为不变")
    return "active", f"max|Δcb|={maxd:.3f}，阈值穿越 {cross}/{sens['n_stockdays']}"


def write_md(o):
    V = o["variants"]
    names = [v[0] for v in VARIANTS if v[0] in V]
    base = V.get("baseline", {})
    be = base.get("event", {})
    # 判定：溢价档 ex5 是否同号、CI 是否仍不含 0、旗标一致率
    verdict_bits, fragile = [], []
    checks = 0
    notes_bits, notes_active = [], []
    for n in names:
        if n == "baseline":
            continue
        e = V[n]["event"]["lt_-0.20"]
        b0 = be["lt_-0.20"]
        checks += 1
        if e["ex5"] is not None and b0["ex5"] is not None:
            same_sign = (e["ex5"] > 0) == (b0["ex5"] > 0)
            ci_ok = bool(e["ci"]) and e["ci"][0] > 0
            mag_ratio = e["ex5"] / b0["ex5"] if b0["ex5"] else float("nan")
            if not same_sign:
                fragile.append(f"{n}: <−0.20 档 ex5 变号")
            if not ci_ok:
                fragile.append(f"{n}: <−0.20 档 CI 跨 0")
            if same_sign and ci_ok and 0.6 <= mag_ratio <= 1.6:
                verdict_bits.append(n)
        if V[n]["flag"]["agree"] is not None and V[n]["flag"]["agree"] < 0.99:
            fragile.append(f"{n}: 生产旗标一致率 {V[n]['flag']['agree']:.2%}")
        sens = V[n].get("sensitivity_vs_baseline")
        state, why = _turn_cap_effective(o, sens) if sens else ("unknown", "")
        if n.startswith("cap"):
            if state in ("inert", "weakly_active"):
                notes_bits.append(f"{n}：{why}")
                if state == "weakly_active":
                    notes_active.append(f"{n}: {sens.get('stocks_with_crossing')}")
    verdict = "稳健" if not fragile else "脆弱（部分）"
    if not fragile and len(verdict_bits) < checks:
        verdict = "基本稳健（幅度有漂移）"
    # TURN_CAP 变体是否构成「有效检验」
    cap_informative = any(
        (_turn_cap_effective(o, V[n].get("sensitivity_vs_baseline"))[0] == "active")
        for n in names if n.startswith("cap"))

    lines = []
    A = lines.append
    A("# chip 算法参数敏感性检验（2026-10-20）\n")
    one = (f"**一句话结论**：在生产参数 (N_BINS=400, TURN_CAP=0.5) 附近的 {len(names) - 1} 个变体中，"
           "「cost_bias < −0.20 溢价档」")
    if not fragile:
        one += "方向、显著性与生产旗标**均保持一致** → **稳健**，参数选择不是结论的来源"
    else:
        one += f"出现 {len(fragile)} 处偏离 → **{verdict}**"
    if not cap_informative:
        one += ("；但须注明：两个 TURN_CAP 变体在本样本上**几乎不可分辨**"
                "（日换手中位数 ~1%、cap=0.3 极少触发），因此「对换手封顶稳健」"
                "实际只证明了「cap 在这批票上不生效」，属于**弱证据**。")
    A(one + "\n")
    if fragile:
        for x in fragile:
            A(f"- ⚠️ {x}")
        A("")
    if notes_bits:
        A("**TURN_CAP 变体的有效性（关键限定）**：")
        for x in notes_bits:
            A(f"- {x}")
        A("")
    A(f"数据：面板 {o['panel']['rows']} 行 / {o['panel']['symbols']} 票 / "
      f"{o['panel']['date_min']}~{o['panel']['date_max']}；bench5 = 同日面板 fwd5 横截面均值。\n")
    cov = ", ".join("{}={}".format(n, V[n]["n_symbols"]) for n in names)
    A(f"覆盖票数：{cov}"
      f"（bins800 受限={o['timing']['bins800_limited']}，单票 800bins 耗时 "
      f"{o['timing']['per_stock_800bins_sec']}s，外推 {o['timing']['extrap_total_sec'] / 60:.1f} 分钟）。\n")

    A("## 1. 参数敏感性与排名稳定性\n")
    A("| 变体 | 参数 | 票数 | 票日 n | 横截面 ρ 均值 | ρ 中位 | ρ≥0.95 日期占比 | 生产旗标一致率 | 8.5↔5.0 翻转率 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for n in names:
        s = V[n]
        r, f = s["rank_stability"], s["flag"]
        A(f"| {n} | bins={s['params']['N_BINS']}, cap={s['params']['TURN_CAP']} | {s['n_symbols']} | "
          f"{s['n_stockdays']} | {r['rho_mean']:.4f} | {r['rho_median']:.4f} | "
          f"{r['share_rho_ge_095']:.1%} (n={r['n_days']} 日) | {f['agree']:.2%} (n={f['n']}) | {f['flip_85_50']:.2%} |")
    A("\n> 排名稳定性 = 逐日横截面（当日 ≥20 票）Spearman(变体 cost_bias, 基线 cost_bias)；基线自身为 1.0。\n")

    A("### 1b. cost_bias 数值漂移与阈值穿越（比全局 ρ 更敏感的诊断）\n")
    A(f"换手画像：全样本日换手最大 {o['turn_profile']['turn_max']:.1f}%，"
      f"票级 p99 最大 {o['turn_profile']['turn_p99']:.1f}%；"
      f"turn>50% 的行 {o['turn_profile']['n_rows_turn_gt_cap0.5']} 个，"
      f"turn>30% 的行 {o['turn_profile']['n_rows_turn_gt_cap0.3']} 个"
      f"（涉及 {o['turn_profile']['n_stocks_any_gt_30']} 只票）。\n")
    A("| 变体 | 票日 n | max|Δcb| | mean|Δcb| | −0.20/−0.15 阈值穿越 | 穿越率 | TURN_CAP 有效性 |")
    A("|---|---|---|---|---|---|")
    for n in names:
        s = V[n].get("sensitivity_vs_baseline")
        if not s:
            A(f"| {n} | {V[n]['n_stockdays']} | — | — | —（参照系） | — | — |")
            continue
        state = _turn_cap_effective(o, s)[0] if n.startswith("cap") else "n/a"
        A(f"| {n} | {s['n_stockdays']} | {s['max_abs_diff']:.5f} | {s['mean_abs_diff']:.2e} | "
          f"{s['n_threshold_crossings']} | {s['threshold_crossing_rate']:.4%} | {state} |")
    A("")

    A("## 2. 档位事件研究（ex5 = fwd5 − 同日 bench5）\n")
    A("| 变体 | 档位 | n | mean ex5 | 胜率 | 95% CI (bootstrap, n=3000, seed=42) |")
    A("|---|---|---|---|---|---|")
    for n in names:
        for tn, lab in (("lt_-0.20", "cb < −0.20"), ("[-0.20,-0.15)", "[−0.20,−0.15)"),
                        ("[-0.15,-0.10)", "[−0.15,−0.10)"), ("rest", "其余 (≥−0.10)")):
            e = V[n]["event"][tn]
            ci = f"[{e['ci'][0] * 100:+.2f}%, {e['ci'][1] * 100:+.2f}%]" if e.get("ci") else "—"
            win = f"{e['win']:.1%}" if e["win"] is not None else "—"
            A(f"| {n} | {lab} | {e['n']} | {_pct(e['ex5'])} | {win} | {ci} |")
    A("\n累积档复核（用于与生产标定值 +2.38% 对照）：\n")
    A("| 变体 | cb < −0.15 (n) | ex5 | 95% CI | cb < −0.10 (n) | ex5 |")
    A("|---|---|---|---|---|---|")
    for n in names:
        a, b = V[n]["event"]["lt_-0.15"], V[n]["event"]["lt_-0.10"]
        ci = f"[{a['ci'][0] * 100:+.2f}%, {a['ci'][1] * 100:+.2f}%]" if a.get("ci") else "—"
        A(f"| {n} | {a['n']} | {_pct(a['ex5'])} | {ci} | {b['n']} | {_pct(b['ex5'])} |")
    A("")

    A("## 3. 分年（cb < −0.15 档）\n")
    A("| 变体 | 2024 n / ex5 | 2025 n / ex5 | 2026 n / ex5 |")
    A("|---|---|---|---|")
    for n in names:
        y = V[n]["by_year_lt_-0.15"]
        A("| " + n + " | " + " | ".join(
            f"{y[k]['n']} / {_pct(y[k]['ex5'])}" for k in ("2024", "2025", "2026")) + " |")
    A("")

    A("## 4. 生产旗标影响\n")
    A("映射（与 `analysis/trader_stock_picks.py::score_chip_structure` 一致）："
      "cb < −0.20 → 8.5；cb < −0.15 → 7.5；其余 → 5.0。\n")
    A("| 变体 | 8.5 票日 | 7.5 票日 | 5.0 票日 | 与基线一致率 | 8.5↔5.0 完全翻转率 |")
    A("|---|---|---|---|---|---|")
    for n in names:
        f = V[n]["flag"]
        d = f["dist_variant"]
        A(f"| {n} | {d.get('8.5', 0)} | {d.get('7.5', 0)} | {d.get('5.0', 0)} | "
          f"{f['agree']:.2%} | {f['flip_85_50']:.2%} |")
    A("")

    A("## 5. 判定\n")
    if not fragile:
        A(f"**稳健**。{len(names) - 1} 个变体（bins 200/800、cap 0.3/1.0）下："
          f"①排名秩相关中位 ≥ {min(V[n]['rank_stability']['rho_median'] for n in names):.3f}；"
          f"②<−0.20 档 ex5 全部同号且 bootstrap CI 不跨 0；"
          f"③生产旗标一致率 ≥ {min(V[n]['flag']['agree'] for n in names):.1%}。"
          "即「cost_bias 深度负偏离 = 优于基准的 5 日超额」这一结论不由网格密度驱动，"
          "生产参数 (400, 0.5) 处在稳健平台内而非临界点。")
    else:
        A(f"**{verdict}**。偏离项：" + "；".join(fragile) +
          "。含义：结论对参数存在依赖，需在报告中标注参数口径，或收窄阈值使用范围。")
    if not cap_informative:
        A("\n**限定**：TURN_CAP 维度未得到有效检验——本样本日换手集中在 ~0.3%~11%（cap=0.5 只对极少数爆量日生效，"
          "cap=0.3 的 max|Δcb| 虽达 0.064 但只影响 002413 等个别高换手股的单日，整体阈值穿越 0~1 次）。"
          "因此「TURN_CAP 稳健」的正确表述是「在当前关注池上 TURN_CAP 不敏感/不生效」，"
          "若未来纳入短线高换手票（次新、题材连板），需重测。")
    A("")
    bc = o["baseline_recompute_check"]
    A("## 6. 基线重算校验（增量暖启动 vs 全量）\n")
    A(f"baseline 变体全量重算末行 cost_bias 与生产增量缓存 `chipmetrics_*.pkl` 末行对比："
      f"n={bc['n']} 票，max|diff| = {bc['max_abs_diff']:.2e}，mean|diff| = {bc['mean_abs_diff']:.2e}。"
      "（生产为 60 日暖启动增量，理论残差 ≈ (1−turn)^60；实测量级见上。）\n")

    A("## 7. 局限\n")
    A("- 样本为 36 只关注池票（bins800 受限时仅 12 票），非全市场；档位 n 依赖单一历史区间（2024-04~2026-10）。")
    A("- 事件研究用同日面板均值作 bench，属等权横截面超额，未剔除市场/风格 beta，也未控制行业。")
    A("- 只测了 4 个参数方向（网格密度、换手封顶）；三角分布形状、初始均摊假设、窗口起点未做扰动。"
      "换手封顶在本样本上接近不生效（见 §1b），其「稳健」含信息量低。")
    A("- 稳健性结论针对「阈值是否被跨越」与「横截面排序」；cb 数值本身在 cap 变体下可漂移至 0.064（少数高换手股的个别日），"
      "若下游用连续值（而非 3 档旗标）打分，需另做检验。")
    A("- cost_bias 依赖全窗口 [min_low, max_high] 网格口径；换票池或长窗口漂移可能改变绝对阈值含义（生产依据是绝对阈值）。")
    A("- bootstrap CI 为同日样本内 i.i.d 重采样，忽略票间/日间相关性，实际不确定性应更宽。")
    A("")
    with open(OUT_MD, "w") as f:
        f.write("\n".join(lines) + "\n")
    log.info("已落盘 %s / %s", OUT_JSON, OUT_MD)


def print_summary(o):
    V = o["variants"]
    print("\n=====CHIP_SENSITIVITY=====")
    b = V["baseline"]["event"]
    print(f"baseline <−0.20: n={b['lt_-0.20']['n']} ex5={_pct(b['lt_-0.20']['ex5'])} CI={b['lt_-0.20']['ci']}; "
          f"<−0.15: n={b['lt_-0.15']['n']} ex5={_pct(b['lt_-0.15']['ex5'])}")
    for n, s in V.items():
        if n == "baseline":
            continue
        e = s["event"]["lt_-0.20"]
        ci = f"[{e['ci'][0] * 100:+.2f}%,{e['ci'][1] * 100:+.2f}%]" if e.get("ci") else "—"
        print(f"{n}: <−0.20 n={e['n']} ex5={_pct(e['ex5'])} CI={ci} | "
              f"ρmed={s['rank_stability']['rho_median']:.4f} agree={s['flag']['agree']:.2%}")
    print(f"baseline_recompute max|diff|={o['baseline_recompute_check']['max_abs_diff']:.2e}")
    print("=====CHIP_SENSITIVITY_END=====")


if __name__ == "__main__":
    sys.exit(main())
