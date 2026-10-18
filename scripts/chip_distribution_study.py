#!/usr/bin/env python3
"""chip 维复活研究（数据债③）：自算筹码分布 → 事件研究验证 → 决定是否接入五维评分。

背景:
- 五维评分的 chip 维（score_chip_structure）自 10-09 起恒 None（东财筹码端点被拦截，诚实退出）。
- 筹码分布可以从日线 OHLCV+换手率自算（经典衰减法），数据全本地可得 → 替代被拦截的外部接口。

算法（经典筹码衰减法）:
- 价格网格: 全窗口 [min_low, max_high] 均分 400 档
- 初始: 首日成交量均匀摊在 [low, high]
- 每日: 老筹码 × (1-turn) 衰减，当日量按三角分布摊在 [low, high]（峰值=加权均价）
- 指标: winner_pct(获利盘比例) / avg_cost_bias(现价偏离成本) / conc(90%成本区间宽度/现价)

验证（纪律: 未验证不接入评分）:
- eval JSON 全部评估日 × 三指标，年内五分位 → 同年基线超额 fwd5 分位曲线
- 单调性（Spearman）+ 首尾分位 bootstrap CI → 有稳定单调关系才提案接入
输出: analysis/research/chip_distribution_study.json + stdout 标记块
"""
import os
import sys
import json
import time
import logging

import numpy as np
import pandas as pd

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("chip_study")

EVAL_JSON = os.path.join(WORKSPACE, "analysis/research/dual_signal_eval_days.json")
CHIP_CACHE_DIR = os.path.join(WORKSPACE, "data/research_price_cache")
OUT_JSON = os.path.join(WORKSPACE, "analysis/research/chip_distribution_study.json")
N_BINS = 400
TURN_CAP = 0.5
START = "2024-04-01"  # baostock 日期必须带横线


def boot_ci(xs, n=3000):
    if len(xs) < 20:
        return None
    a = np.asarray(xs, dtype=float)
    rng = np.random.default_rng(42)
    idx = rng.integers(0, len(a), size=(n, len(a)))
    ms = a[idx].mean(axis=1)
    return [float(np.percentile(ms, 2.5)), float(np.percentile(ms, 97.5))]


def fetch_chip_input(sym: str):
    """baostock 拉 date,high,low,close,volume,turn（含磁盘缓存）。turn 为百分数。"""
    cache = os.path.join(CHIP_CACHE_DIR, f"chip_{sym}.pkl")
    if os.path.exists(cache):
        return pd.read_pickle(cache)
    import baostock as bs
    code = f"sh.{sym}" if sym.startswith(("6", "9")) else f"sz.{sym}"
    rs = bs.query_history_k_data_plus(
        code, "date,high,low,close,volume,turn",
        start_date=START, end_date=pd.Timestamp.now().strftime("%Y-%m-%d"),
        frequency="d", adjustflag="2",
    )
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rs.error_code != "0":
        raise RuntimeError(f"{sym}: {rs.error_msg}")
    df = pd.DataFrame(rows, columns=["date", "high", "low", "close", "volume", "turn"])
    for c in ("high", "low", "close", "volume", "turn"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    df.to_pickle(cache)
    return df


def chip_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """逐日衰减法筹码分布 → 每日三指标（因果，只用 ≤T 数据）。"""
    lo_all, hi_all = df["low"].min(), df["high"].max()
    if not np.isfinite(lo_all) or hi_all <= lo_all:
        return pd.DataFrame()
    grid = np.linspace(lo_all, hi_all, N_BINS)
    centers = (grid[:-1] + grid[1:]) / 2
    chips = np.zeros(N_BINS - 1)
    out = []
    for row in df.itertuples(index=False):
        t = row.turn / 100.0 if pd.notna(row.turn) else 0.0  # baostock turn 为百分数
        t = min(max(t, 0.0), TURN_CAP)
        chips *= (1.0 - t)
        # 当日量三角分布: 峰值 = (h+l+2c)/4，底 = [low, high]
        peak = (row.high + row.low + 2 * row.close) / 4.0
        vol = row.volume if pd.notna(row.volume) and row.volume > 0 else 0.0
        if vol > 0:
            # 三角权重: 距 peak 线性衰减，区间外为 0
            half = max(row.high - row.low, 1e-9)
            w = 1.0 - np.abs(centers - peak) / half
            w = np.clip(w, 0.0, None)
            if w.sum() > 0:
                chips += vol * w / w.sum()
        total = chips.sum()
        if total <= 0:
            continue
        cum = np.cumsum(chips) / total
        winner = float(cum[centers <= row.close].max()) if (centers <= row.close).any() else 0.0
        avg_cost = float((chips * centers).sum() / total)
        p10 = float(centers[np.searchsorted(cum, 0.10)])
        p90 = float(centers[np.searchsorted(cum, 0.90)])
        conc = (p90 - p10) / row.close if row.close > 0 else np.nan
        out.append((row.date, winner, row.close / avg_cost - 1 if avg_cost > 0 else np.nan, conc))
    return pd.DataFrame(out, columns=["date", "winner_pct", "cost_bias", "conc90"])


def main():
    t0 = time.time()
    with open(EVAL_JSON) as f:
        rows = json.load(f)
    by_sym = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)
    log.info("评估日 %d，涉及 %d 只", len(rows), len(by_sym))

    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        log.error("baostock 登录失败: %s", lg.error_msg)
        return 1

    # 口径核对: baostock turn vs 基准池 CSV turnover（600036 同日对比）
    try:
        chk = fetch_chip_input("600036")
        csv = pd.read_csv("/Users/duguke/stock_data/600036_招商银行.csv")
        m = chk.merge(csv[["date", "turnover"]], on="date")
        # 同单位核对: baostock turn 与重建后 CSV turnover 同为百分数（ratio 应≈1）
        ratio = (m["turn"] / m["turnover"]).replace([np.inf, -np.inf], np.nan).dropna()
        med = float(ratio.median()) if len(ratio) else float("nan")
        if 0.5 < med < 2.0:
            log.info("turn 口径核对 600036: turn/csv.turnover 中位比 = %.3f（同为百分数 ✓）", med)
        else:
            log.warning("turn 口径异常: 中位比 = %.3f（算法内 t=turn/100 需重审）", med)
    except Exception as e:
        log.warning("口径核对失败（继续）: %s", e)

    metrics_by_sym = {}
    for i, sym in enumerate(sorted(by_sym), 1):
        mcache = os.path.join(CHIP_CACHE_DIR, f"chipmetrics_{sym}.pkl")
        try:
            if os.path.exists(mcache):
                metrics_by_sym[sym] = pd.read_pickle(mcache)
            else:
                df = fetch_chip_input(sym)
                if len(df) < 60:
                    log.warning("%s 数据不足 %d 行，跳过", sym, len(df))
                    continue
                metrics_by_sym[sym] = chip_metrics(df)
                metrics_by_sym[sym].to_pickle(mcache)  # 供后续交互切面离线复用
        except Exception as e:
            log.warning("chip 计算失败 %s: %s", sym, e)
        if i % 10 == 0:
            log.info("进度 %d/%d (%.0fs)", i, len(by_sym), time.time() - t0)
    bs.logout()

    # 挂接 eval 日 + 年内五分位
    recs = []
    for sym, srows in by_sym.items():
        md = metrics_by_sym.get(sym)
        if md is None or md.empty:
            continue
        mmap = dict(zip(md["date"], zip(md["winner_pct"], md["cost_bias"], md["conc90"])))
        for r in srows:
            m = mmap.get(r["date"])
            if m is None or r.get("fwd", {}).get("5") is None:
                continue
            recs.append({"symbol": sym, "date": r["date"], "year": r["date"][:4],
                         "cls": r["cls"], "fwd5": r["fwd"]["5"],
                         "winner_pct": m[0], "cost_bias": m[1], "conc90": m[2]})
    df_all = pd.DataFrame(recs)
    log.info("挂接完成 n=%d (%.0fs)", len(df_all), time.time() - t0)

    base = df_all.groupby("year")["fwd5"].mean().to_dict()  # 同年无条件基线
    df_all["excess5"] = df_all["fwd5"] - df_all["year"].map(base)

    out = {"n": len(df_all), "baseline_mean_by_year": {k: float(v) for k, v in base.items()}, "metrics": {}}
    for metric in ("winner_pct", "cost_bias", "conc90"):
        d = df_all.dropna(subset=[metric]).copy()
        # 年内五分位（跨票混合排名，按年分桶消除漂移）
        d["q"] = d.groupby("year")[metric].transform(lambda x: pd.qcut(x, 5, labels=False, duplicates="drop"))
        info = {"n": len(d), "quintiles": {}, "spearman_by_year": {}}
        for q in sorted(d["q"].dropna().unique()):
            sub = d[d["q"] == q]
            info["quintiles"][str(int(q))] = {
                "n": len(sub),
                "excess5": float(sub["excess5"].mean()),
                "win": float((sub["fwd5"] > 0).mean()),
                "metric_mean": float(sub[metric].mean()),
                "ci": boot_ci(sub["excess5"].tolist()),
            }
        for y in sorted(d["year"].unique()):
            dy = d[d["year"] == y]
            info["spearman_by_year"][y] = float(dy[metric].corr(dy["excess5"], method="spearman"))
        out["metrics"][metric] = info

    with open(OUT_JSON, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    log.info("已落盘 %s (%.0fs)", OUT_JSON, time.time() - t0)

    print("\n=====CHIP_STUDY=====")
    print(f"n={out['n']} | 同年基线 {out['baseline_mean_by_year']}")
    for metric, info in out["metrics"].items():
        print(f"\n[{metric}] n={info['n']} | 年内Spearman: " +
              " ".join(f"{y}={v:+.3f}" for y, v in info["spearman_by_year"].items()))
        for q, qi in info["quintiles"].items():
            ci = qi["ci"]
            ci_s = f" CI[{ci[0]:+.2%},{ci[1]:+.2%}]" if ci else ""
            print(f"  Q{q}: metric={qi['metric_mean']:.3f} 超额5d={qi['excess5']:+.2%} 胜率={qi['win']:.0%} (n={qi['n']}){ci_s}")
    print("=====CHIP_STUDY_END=====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
