#!/usr/bin/env python3
"""高开档执行折扣研究：把买侧信号的次日跳空分档，看各档「开盘执行」的真实折扣。

背景（2026-10-18 执行时点研究遗留细化项）:
  analysis/research/execution_timing_study.json 已证：买侧 close-to-close 口径虚高约 29%，
  信号日平均跳空 gap = +0.21%（2026 年仅 +0.09%）。本脚本把 gap 分层回答：
  「次日高开 0~+1% 档，开盘执行的折扣到底多少、还能不能执行」。

口径（严格复刻 scripts/execution_timing_study.py）:
  T = 信号日（dual_signal_eval_days.json 的 date，位置 k）
  gap      = open[k+1]/close[k] - 1                隔夜跳空
  oc1      = close[k+1]/open[k+1] - 1              开盘进、当日收盘出
  fwd5_oc  = close[k+5]/open[k+1] - 1              次日开盘进、T+5 收盘出
  fwd5_cc  = close[k+5]/close[k]   - 1             收盘口径（对照）
  超额基准 = 同年全样本（全部 cls，含双持有）同口径均值（与原脚本一致，非池均值逐日对齐）
  ex5_oc   = fwd5_oc - 该年 bm_oc

样本: cls ∈ {分歧买, 双买}
分桶: (-inf,-1%), [-1%,0), [0,+1%), [+1%,+3%), [+3%,+inf)

纯本地数据（price_cache pkl + eval json），零网络。
幂等：可重复运行，覆盖写同一 JSON/MD。
输出: analysis/research/2026-10-20_gap_tier_execution.json / .md
"""
import os
import sys
import json
import time
import logging

import pandas as pd

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None
    print("[warn] numpy 不可用，CI 将跳过", flush=True)

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
# 必须同时插入：工作区根（让 from analysis.xxx 在脚本模式可解析）+ analysis/
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from analysis.net_guard import install_default_timeout

    install_default_timeout(15)
except Exception as _e:  # 纯本地脚本，护栏缺失不阻断但必须可见
    print(f"[warn] net_guard 未装上: {_e}", flush=True)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("gap_tier")

EVAL_JSON = os.path.join(WORKSPACE, "analysis/research/dual_signal_eval_days.json")
PRICE_CACHE_DIR = os.path.join(WORKSPACE, "data/research_price_cache")
REF_JSON = os.path.join(WORKSPACE, "analysis/research/execution_timing_study.json")
OUT_JSON = os.path.join(WORKSPACE, "analysis/research/2026-10-20_gap_tier_execution.json")
OUT_MD = os.path.join(WORKSPACE, "analysis/research/2026-10-20_gap_tier_execution.md")

BUY_CLASSES = ("分歧买", "双买")
KEY_CLASS = "分歧买"  # 主样本（n 足够做分年/CI）
H = 5
BOOT_N = 3000
BOOT_SEED = 42
IDENT_TOL = 1e-4
REF_GAP = 0.00211690948085088  # exec study 分歧买 gap mean

# 分桶：(label, lo, hi) 左闭右开，末档含右端
TIERS = [
    ("gap<-1%", None, -0.01),
    ("-1%~0%", -0.01, 0.0),
    ("0~+1%", 0.0, 0.01),
    ("+1%~+3%", 0.01, 0.03),
    ("gap>+3%", 0.03, None),
]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def win_rate(xs):
    xs = [x for x in xs if x is not None]
    return sum(1 for x in xs if x > 0) / len(xs) if xs else None


def boot_ci(xs, n=BOOT_N, seed=BOOT_SEED):
    """iid bootstrap 95% CI（与原脚本同法：单一 seed、同一 rng 逐次调用）。"""
    xs = [x for x in xs if x is not None]
    if np is None or len(xs) < 20:
        return None
    a = np.asarray(xs, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(n, len(a)))
    ms = a[idx].mean(axis=1)
    return [float(np.percentile(ms, 2.5)), float(np.percentile(ms, 97.5))]


def tier_of(gap):
    for label, lo, hi in TIERS:
        if (lo is None or gap >= lo) and (hi is None or gap < hi):
            return label
    return TIERS[-1][0]


def build_recs():
    with open(EVAL_JSON) as f:
        rows = json.load(f)
    by_sym = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)

    miss_cache, dropped_no_bar = [], 0
    recs = []  # 全部 cls，用于同年基线（口径同原脚本）
    for sym, srows in sorted(by_sym.items()):
        p = os.path.join(PRICE_CACHE_DIR, f"{sym}.pkl")
        if not os.path.exists(p):
            miss_cache.append(sym)
            continue
        df = pd.read_pickle(p)
        closes = df["close"].astype(float).tolist()
        opens = df["open"].astype(float).tolist()
        dates = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in df["date"]]
        pos = {d: k for k, d in enumerate(dates)}
        for r in srows:
            k = pos.get(r["date"])
            if k is None:
                continue
            if k + 1 >= len(closes):
                if r["cls"] in BUY_CLASSES:
                    dropped_no_bar += 1
                continue
            gap = opens[k + 1] / closes[k] - 1
            oc1 = closes[k + 1] / opens[k + 1] - 1
            fwd5_cc = fwd5_oc = None
            if k + H < len(closes):
                fwd5_cc = closes[k + H] / closes[k] - 1
                fwd5_oc = closes[k + H] / opens[k + 1] - 1
            recs.append({"symbol": sym, "date": r["date"], "cls": r["cls"],
                         "year": r["date"][:4], "gap": gap, "oc1": oc1,
                         "fwd5_cc": fwd5_cc, "fwd5_oc": fwd5_oc})
    return recs, miss_cache, dropped_no_bar


def year_baseline(recs):
    """同年全样本同口径基线（与原 exec study 完全一致：全部 cls 混合）。"""
    base_cc, base_oc = {}, {}
    for r in recs:
        if r["fwd5_cc"] is None:
            continue
        base_cc.setdefault(r["year"], []).append(r["fwd5_cc"])
        base_oc.setdefault(r["year"], []).append(r["fwd5_oc"])
    return ({y: sum(v) / len(v) for y, v in base_cc.items()},
            {y: sum(v) / len(v) for y, v in base_oc.items()})


def tier_stats(sub, bm_cc, bm_oc):
    n = len(sub)
    if n == 0:
        return {"n": 0}
    gaps = [r["gap"] for r in sub]
    oc1s = [r["oc1"] for r in sub]
    xs_cc, xs_oc, drags = [], [], []
    for r in sub:
        if r["fwd5_oc"] is None:
            continue
        xs_cc.append(r["fwd5_cc"] - bm_cc[r["year"]])
        xs_oc.append(r["fwd5_oc"] - bm_oc[r["year"]])
        drags.append(r["fwd5_oc"] - r["fwd5_cc"])
    return {
        "n": n,
        "gap_mean": mean(gaps),
        "oc1_mean": mean(oc1s),
        "oc1_win": win_rate(oc1s),
        "oc1_ci": boot_ci(oc1s),
        "n_fwd5": len(xs_oc),
        "ex5_cc_mean": mean(xs_cc), "ex5_cc_ci": boot_ci(xs_cc),
        "ex5_oc_mean": mean(xs_oc), "ex5_oc_ci": boot_ci(xs_oc),
        "oc_minus_cc": mean(drags),  # fwd5_oc - fwd5_cc（负=开盘执行比收盘口径差）
    }


def pct(v, nd=2):
    return "n/a" if v is None else f"{v * 100:+.{nd}f}%"


def ci_s(ci):
    return "n/a" if not ci else f"[{ci[0] * 100:+.2f}%, {ci[1] * 100:+.2f}%]"


def main():
    t0 = time.time()
    recs, miss_cache, dropped = build_recs()
    if miss_cache:
        log.warning("缺价格缓存 %d 只: %s", len(miss_cache), miss_cache)
    log.info("recs=%d dropped_no_next_bar=%d (%.1fs)", len(recs), dropped, time.time() - t0)

    bm_cc, bm_oc = year_baseline(recs)

    out = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "run_date": time.strftime("%Y-%m-%d"),
            "eval_json": EVAL_JSON,
            "price_cache": PRICE_CACHE_DIR,
            "buy_classes": list(BUY_CLASSES),
            "h": H, "boot_n": BOOT_N, "boot_seed": BOOT_SEED,
            "baseline_rule": "同年全样本(全部cls)同口径均值（与原 execution_timing_study.py 一致）",
            "dropped_no_next_bar": dropped,
            "miss_cache": miss_cache,
            "tiers": [t[0] for t in TIERS],
        },
        "baseline": {"fwd5_cc": bm_cc, "fwd5_oc": bm_oc},
        "classes": {},
    }

    # 恒等式自检：与 exec study 的「分歧买」同口径对拍（同一类，n 应同为 984）
    main_buy = [r for r in recs if r["cls"] == KEY_CLASS]
    gap_all = mean([r["gap"] for r in main_buy])
    all_buy = [r for r in recs if r["cls"] in BUY_CLASSES]
    out["meta"]["buy_gap_mean_all"] = gap_all
    out["meta"]["buy_n"] = len(main_buy)
    out["meta"]["buy_union_n"] = len(all_buy)
    out["meta"]["buy_union_gap_mean"] = mean([r["gap"] for r in all_buy])
    ident = {
        "ref_gap": REF_GAP,
        "measured_gap": gap_all,
        "abs_diff": abs(gap_all - REF_GAP) if gap_all is not None else None,
        "tol": IDENT_TOL,
        "pass": (gap_all is not None and abs(gap_all - REF_GAP) <= IDENT_TOL),
        "ref_n": 984,
        "measured_n": len(main_buy),
        "note": "对拍对象为分歧买单类（exec study 原口径）；双买不计入，union 均值另存于 meta.buy_union_gap_mean 仅供参照",
    }
    out["identity_check"] = ident
    log.info("恒等式自检: measured=%.7f ref=%.7f diff=%.3g pass=%s",
             gap_all or 0, REF_GAP, ident["abs_diff"] or 0, ident["pass"])

    for cls in BUY_CLASSES:
        sub = [r for r in recs if r["cls"] == cls]
        info = {"n": len(sub), "gap_mean": mean([r["gap"] for r in sub]), "tiers": {}}
        for label, _, _ in TIERS:
            tsub = [r for r in sub if tier_of(r["gap"]) == label]
            info["tiers"][label] = tier_stats(tsub, bm_cc, bm_oc)
        # 分年（重点档 + 全档），仅对主样本做逐年（双买 n=43 逐年更碎）
        info["by_year"] = {}
        for y in sorted({r["year"] for r in sub}):
            ysub = [r for r in sub if r["year"] == y]
            yinfo = {"n": len(ysub), "gap_mean": mean([r["gap"] for r in ysub]), "tiers": {}}
            for label, _, _ in TIERS:
                tsub = [r for r in ysub if tier_of(r["gap"]) == label]
                st = tier_stats(tsub, bm_cc, bm_oc)
                # 分年亚组 n 极小，CI 只在 n>=20 时给出（boot_ci 内部规则）
                yinfo["tiers"][label] = st
            info["by_year"][y] = yinfo
        out["classes"][cls] = info

    with open(OUT_JSON, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info("已落盘 %s", OUT_JSON)

    write_md(out)
    log.info("已落盘 %s (%.0fs)", OUT_MD, time.time() - t0)

    # stdout 摘要
    print("\n=====GAP_TIER=====")
    print(f"buy(分歧买) n={len(main_buy)} gap_mean={gap_all:+.4%} identity_pass={ident['pass']} "
          f"| union n={len(all_buy)} gap={out['meta']['buy_union_gap_mean']:+.4%}")
    for cls in BUY_CLASSES:
        info = out["classes"][cls]
        print(f"\n[{cls}] n={info['n']} gap={info['gap_mean']:+.3%}")
        for label, _, _ in TIERS:
            s = info["tiers"][label]
            if not s.get("n"):
                print(f"  {label:10s} n=0")
                continue
            print(f"  {label:10s} n={s['n']:4d} gap={pct(s['gap_mean'])} oc1={pct(s['oc1_mean'])} "
                  f"win={s['oc1_win']:.0%} ex5_cc={pct(s['ex5_cc_mean'])} "
                  f"ex5_oc={pct(s['ex5_oc_mean'])} CI={ci_s(s['ex5_oc_ci'])} "
                  f"oc-cc={pct(s['oc_minus_cc'])}")
    print("=====GAP_TIER_END=====")


def write_md(out):
    b = out["baseline"]
    ident = out["identity_check"]
    key = out["classes"][KEY_CLASS]
    dual = out["classes"]["双买"]
    kt = key["tiers"]["0~+1%"]
    all_main = key["tiers"]
    lines = []
    A = lines.append

    A("# 高开档执行折扣研究（gap tier × 开盘执行）")
    A("")
    A(f"- 生成时间: {out['meta']['run_date']}")
    A(f"- 脚本: `scripts/gap_tier_execution_test.py`（纯本地，零网络；幂等可重跑）")
    A(f"- 数据: `analysis/research/dual_signal_eval_days.json` + `data/research_price_cache/{{sym}}.pkl`")
    A(f"- 样本: 分歧买 {key['n']} + 双买 {dual['n']}；T+1 无 bar 丢弃 {out['meta']['dropped_no_next_bar']} 条；缺缓存 {len(out['meta']['miss_cache'])} 只")
    A("")
    A("## 一句话结论")
    A("")
    if kt.get("n"):
        sig = kt.get("ex5_oc_ci")
        signif = sig and (sig[0] > 0 or sig[1] < 0)
        A(f"**分歧买在次日高开 0~+1% 档（n={kt['n']}）开盘执行，5 日超额 {pct(kt['ex5_oc_mean'])}"
          f"（CI {ci_s(sig)}，{'显著' if signif else '不显著'}），"
          f"开盘进当日收 {pct(kt['oc1_mean'])}（胜率 {kt['oc1_win']:.0%}）；"
          f"换开盘口径的代价 {pct(kt['oc_minus_cc'])}（收盘口径 {pct(kt['ex5_cc_mean'])} → 开盘口径 {pct(kt['ex5_oc_mean'])}，留存 "
          f"{(kt['ex5_oc_mean'] / kt['ex5_cc_mean'] * 100):.0f}%）。**")
    A("")
    A("> 口径说明：超额基准 = **同年全样本（全部 cls，含双持有）同口径均值**，与原 `execution_timing_study.py` 完全一致（不是逐日池均值对齐）。")
    A("")
    A("## 恒等式自检（仅数值核对，不作结论）")
    A("")
    A(f"- 实测**分歧买**全样本 gap 均值 = {ident['measured_gap']:+.7f}（n={ident['measured_n']}）")
    A(f"- exec study 分歧买 gap mean = {ident['ref_gap']:+.7f}（n={ident['ref_n']}）")
    A(f"- 绝对差 = {ident['abs_diff']:.3g}，容差 {ident['tol']:.0e} → **{'通过' if ident['pass'] else '未通过（见差异说明）'}**")
    A(f"- 参照：买侧 union（分歧买+双买）gap 均值 = {out['meta']['buy_union_gap_mean']:+.7f}（n={out['meta']['buy_union_n']}），仅作参照，非对拍对象")
    if not ident["pass"]:
        A("  - 未通过通常来自价格缓存更新或样本口径差异，实测值以上方为准。")
    A("")

    def tier_table(info, title, note_n_small=True):
        A(f"### {title}")
        A("")
        A("| 桶 | n | gap均值 | 开盘→当日收 | 胜率 | 5日超额(收盘口径) | 5日超额(开盘口径) | 开盘口径 CI | 开盘-收盘 |")
        A("|---|---|---|---|---|---|---|---|---|")
        for label, _, _ in TIERS:
            s = info["tiers"][label]
            if not s.get("n"):
                A(f"| {label} | 0 | - | - | - | - | - | - | - |")
                continue
            A(f"| {label} | {s['n']} | {pct(s['gap_mean'])} | {pct(s['oc1_mean'])} | {s['oc1_win']:.0%} "
              f"| {pct(s['ex5_cc_mean'])} {ci_s(s['ex5_cc_ci'])} | {pct(s['ex5_oc_mean'])} "
              f"| {ci_s(s['ex5_oc_ci'])} | {pct(s['oc_minus_cc'])} |")
        A("")
        if note_n_small:
            small = [l for l, _, _ in TIERS if info["tiers"][l].get("n") and info["tiers"][l]["n"] < 20]
            A(f"- n<20 的桶：{', '.join(small) if small else '无'}；这些桶 CI 为 n/a（bootstrap 需 n≥20），结果不可解读。")
            A("")

    A("## 分桶表")
    A("")
    tier_table(key, f"分歧买（主样本，n={key['n']}，全样本 gap 均值 {pct(key['gap_mean'])}）")
    tier_table(dual, f"双买（n={dual['n']}，样本过小，仅作方向参考，**不作结论**）")

    A("## 重点档 0~+1% 分年")
    A("")
    A("| 年份 | n | gap均值 | 开盘→当日收 | 胜率 | 5日超额(收盘) | 5日超额(开盘) | 开盘 CI |")
    A("|---|---|---|---|---|---|---|---|")
    for y, yi in key["by_year"].items():
        s = yi["tiers"]["0~+1%"]
        if not s.get("n"):
            A(f"| {y} | 0 | - | - | - | - | - | - |")
            continue
        A(f"| {y} | {s['n']} | {pct(s['gap_mean'])} | {pct(s['oc1_mean'])} | {s['oc1_win']:.0%} "
          f"| {pct(s['ex5_cc_mean'])} | {pct(s['ex5_oc_mean'])} | {ci_s(s['ex5_oc_ci'])} |")
    A("")
    A("## 分档折扣对比（开盘口径 vs 收盘口径，分歧买）")
    A("")
    A("> 留存率 = 开盘口径 / 收盘口径；分母为负或接近 0 时留存率无经济含义（标 n/m）。")
    A("")
    A("| 桶 | 收盘口径超额 | 开盘口径超额 | 开盘-收盘（换口径的代价） | 留存率 |")
    A("|---|---|---|---|---|")
    for label, _, _ in TIERS:
        s = all_main[label]
        if not s.get("n") or s.get("ex5_cc_mean") is None:
            continue
        cc, oc = s["ex5_cc_mean"], s["ex5_oc_mean"]
        keep = None
        if cc is not None and abs(cc) > 0.002 and oc is not None:  # 分母过小则不给留存率
            keep = oc / cc
        A(f"| {label} | {pct(cc)} | {pct(oc)} | {pct(oc - cc)} | {('%.0f%%' % (keep * 100)) if keep is not None else 'n/m'} |")
    A("")
    A("## 对 trade_plan 的可执行建议")
    A("")
    A(build_advice(key, dual))
    A("")
    A("## 局限")
    A("")
    A("1. **基准为同年全样本均值**，非逐日池均值；与原 exec study 一致，故不能用它判断「相对池」的择时能力，只能看同一口径内的相对变化。")
    A("2. **gap 分桶是事后分层**：实盘时你能看到 gap 再决定，所以分档结论可直接用；但桶内样本非随机，桶间比较包含「gap 本身携带的信息」（如大幅高开常伴随更强动量）。")
    A("3. **开盘执行假设能以 open 成交**：未建模竞价滑点、涨跌停无法成交、流动性不足；对小盘/涨停板样本偏乐观。")
    A("4. **双买 n=43**：分桶后每桶 3~18 条，所有 CI 均为 n/a 或不显著，仅供方向参考，未纳入结论。")
    A("5. **重叠样本**：同股票相邻信号日的 5 日窗口重叠，iid bootstrap 会低估真实不确定性；CI 应视为偏窄下限。")
    A("6. **T+1 无 bar 丢弃**：含停牌/退市尾部样本，数量已记录，可能带来轻微生存偏差。")
    A("7. 2026 年样本截至数据缓存最后交易日，年内涵盖不完整。")
    A("")

    with open(OUT_MD, "w") as f:
        f.write("\n".join(lines))


def build_advice(key, dual):
    """基于实测数字生成规则建议（不臆造数字）。"""
    t = key["tiers"]
    out = []
    for label, _, _ in TIERS:
        s = t[label]
        if not s.get("n"):
            out.append(f"- **{label}**：样本 n=0，无数据支持。")
            continue
        ci = s.get("ex5_oc_ci")
        sig = ci and ci[0] > 0
        out.append(f"- **{label}**（n={s['n']}）：开盘口径 5 日超额 {pct(s['ex5_oc_mean'])} "
                   f"CI {ci_s(ci)} → {'正超额且 CI 下界 >0，开盘执行有数据支撑' if sig else 'CI 含 0，开盘执行优势未被证实'}。")
    out.append("")
    kt = t["0~+1%"]
    big = t["gap>+3%"]
    mid = t["+1%~+3%"]
    verdict = []
    if kt.get("n") and kt.get("ex5_oc_ci") and kt["ex5_oc_ci"][0] > 0:
        verdict.append("「高开 <1% 开盘执行」**有**数据支撑（见上）。")
    else:
        verdict.append("「高开 <1% 开盘执行」**未获**显著证据支撑，属可接受但非优势。")
    if mid.get("n") and mid.get("ex5_oc_ci") and mid["ex5_oc_ci"][1] < 0:
        verdict.append("「1~3% 等回踩」**有**数据支撑（该档开盘口径显著为负）。")
    else:
        verdict.append("「1~3% 等回踩」**缺乏**显著证据，仅可作为风控偏好。")
    if big.get("n") and big.get("ex5_oc_ci"):
        verdict.append(f"「>3% 放弃」：该档开盘口径 {pct(big['ex5_oc_mean'])} CI {ci_s(big['ex5_oc_ci'])} —— "
                       f"{'支持放弃' if big['ex5_oc_ci'][1] < 0 else '不构成放弃依据'}。")
    out.append("**三档规则评估**：" + " ".join(verdict))
    return "\n".join(out)


if __name__ == "__main__":
    main()
