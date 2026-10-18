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


def fetch_chip_input(sym: str, ref_date: str = None):
    """baostock 拉 date,high,low,close,volume,turn（含磁盘缓存）。turn 为百分数。

    2026-10-18 修复：旧实现 `if exists: return` 无条件命中，缓存永不更新。
    现策略：
      * 缓存缺失 → 全量拉取（START 起）
      * 缓存最后日期 >= 目标日期 → 直接用
      * 否则 → **只拉增量尾巴**（最后日期+1 ~ 目标日期），拼接到旧缓存后写回
    目标日期默认取价格缓存最新日（与该票行情对齐），拿不到才用今天。
    """
    cache = os.path.join(CHIP_CACHE_DIR, f"chip_{sym}.pkl")
    target = ref_date or _price_ref_date(sym) or pd.Timestamp.now().strftime("%Y-%m-%d")
    cached = None
    if os.path.exists(cache):
        try:
            cached = pd.read_pickle(cache)
        except Exception as e:
            log.warning("%s chip 输入缓存损坏，将全量重建: %s", sym, e)
            cached = None

    last = None
    if cached is not None and len(cached):
        last = str(cached["date"].max())[:10]
    if last and last >= target:
        return cached

    import baostock as bs
    code = f"sh.{sym}" if sym.startswith(("6", "9")) else f"sz.{sym}"
    start = START if not last else (
        (pd.Timestamp(last) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    rs = bs.query_history_k_data_plus(
        code, "date,high,low,close,volume,turn",
        start_date=start, end_date=target, frequency="d", adjustflag="2",
    )
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rs.error_code != "0":
        raise RuntimeError(f"{sym}: {rs.error_msg}")
    new = pd.DataFrame(rows, columns=["date", "high", "low", "close", "volume", "turn"])
    for c in ("high", "low", "close", "volume", "turn"):
        new[c] = pd.to_numeric(new[c], errors="coerce")
    new = new.dropna(subset=["close"])

    if cached is not None and len(cached):
        if len(new) == 0:
            return cached            # 无新行：不动盘（幂等）
        df = pd.concat([cached, new], ignore_index=True)
        df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    else:
        df = new.reset_index(drop=True)

    df.to_pickle(cache)
    return df


def _price_ref_date(sym: str):
    """价格缓存（data/research_price_cache/{sym}.pkl）最新日期；无则 None。"""
    try:
        from analysis import chip_freshness as _cf
        return _cf.price_ref_date(sym)
    except Exception:
        return None


def chip_metrics(df: pd.DataFrame, prev: pd.DataFrame = None, n_warmup: int = 60) -> pd.DataFrame:
    """逐日衰减法筹码分布 → 每日三指标（因果，只用 ≤T 数据）。

    2026-10-18 增量模式：传入 `prev`（已算好的历史指标）时，只重算尾部
    `n_warmup` 行之后的部分。

    ⚠️ 为什么只回退 n_warmup 行而不是完全增量：
    筹码网格的下界/上界取的是**整个窗口**的 min_low/max_high（_ALL_ 口径）。
    若只拿新行算网格，网格会随窗口滑动而变，新旧两段的 cost_bias 不同源。
    折中：网格仍用全窗口（与旧结果口径一致），但状态（chips 分布）从
    `len(df)-n_warmup` 处起步——筹码衰减是指数型的，60 个交易日后旧筹码
    ×(1-turn)^60 残留极小（turn≈2% 时 ≈0.3%），足以消除截断误差。
    """
    lo_all, hi_all = df["low"].min(), df["high"].max()
    if not np.isfinite(lo_all) or hi_all <= lo_all:
        return pd.DataFrame()
    grid = np.linspace(lo_all, hi_all, N_BINS)
    centers = (grid[:-1] + grid[1:]) / 2

    warm = 0
    keep = None
    if prev is not None and len(prev):
        warm = max(0, len(df) - n_warmup)
        if warm > 0:
            keep = prev[prev["date"].astype(str) < str(df["date"].iloc[warm])]

    chips = np.zeros(N_BINS - 1)
    out = []
    for j, row in enumerate(df.itertuples(index=False)):
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
        if j < warm:
            continue                      # 暖机段：只推进筹码状态，不输出
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

    tail = pd.DataFrame(out, columns=["date", "winner_pct", "cost_bias", "conc90"])
    if keep is not None and len(keep):
        tail = pd.concat([keep, tail], ignore_index=True)
    return tail


def refresh_metrics(force: bool = False) -> int:
    """生产维护模式：把 chipmetrics 推到与价格缓存对齐的最新日期。

    与 main() 的区别：main() 是**研究**入口（跑五分位、算 CI、写 study json）；
    refresh 只做数据维护，不重算研究结论。周流水线挂这个。

    对每只票：
      1. chip 输入拉到目标日（fetch_chip_input 已改为增量尾巴）
      2. chipmetrics 缺失 / 过期 / 输入有新增行 → 增量重算尾部
      3. 仍然陈旧且无法重算 → 计入 failed（告警，不阻断）
    幂等：数据已同步时全部 skip，不碰盘。
    """
    from analysis.fib_extension_scan import WATCHLIST
    from analysis import chip_freshness as _cf

    syms = sorted(WATCHLIST.keys())
    idx = _cf.trading_index()
    stats = {"total": len(syms), "refreshed": 0, "skipped": 0, "failed": 0}
    failures = []

    # 🔴 2026-10-18 截断测试揭出的真缺陷：
    # 旧逻辑用「chip 输入最后日 >= 价格缓存最后日」判同步。当**两边同时**落后
    # （主因：价格缓存本身未刷新，或两者一起被截断）时，该条件恒真 → 全 skip，
    # 永远不会追。实测：把价格与 chip 都截到 10-09 → refresh 报 36/36 skipped。
    # 修正：同步判据必须同时包含「不落后于全市场最新行情」。基准取价格缓存
    # 跨票最新日（代表本体系最后一次成功拉到的行情日），而非 date.today()。
    market_ref = None
    for s in syms:
        d = _cf.price_ref_date(s)
        if d and (market_ref is None or d > market_ref):
            market_ref = d
    if market_ref:
        log.info("行情基准日 = %s", market_ref)

    need_bs = any(True for _ in syms)  # 惰性登录：只在真的要拉数据时
    logged = False
    try:
        for i, sym in enumerate(syms, 1):
            mpath = os.path.join(CHIP_CACHE_DIR, f"chipmetrics_{sym}.pkl")
            ipath = os.path.join(CHIP_CACHE_DIR, f"chip_{sym}.pkl")
            ref = _cf.price_ref_date(sym) or market_ref
            m_last = None
            if os.path.exists(mpath):
                try:
                    m_last = str(pd.read_pickle(mpath)["date"].max())[:10]
                except Exception:
                    m_last = None
            i_last = None
            if os.path.exists(ipath):
                try:
                    i_last = str(pd.read_pickle(ipath)["date"].max())[:10]
                except Exception:
                    i_last = None

            # 同步 = 不落后于本票行情 且 不落后于全市场行情基准
            targets = [t for t in (ref, market_ref) if t]
            up_to_date = bool(m_last and i_last) and all(
                m_last >= t and i_last >= t for t in targets)
            if not force and up_to_date:
                stats["skipped"] += 1
                continue

            try:
                if not logged and need_bs:
                    import baostock as bs
                    lg = bs.login()
                    if lg.error_code != "0":
                        raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
                    globals()["_bs"] = bs
                    logged = True
                bs = globals()["_bs"]
                df = fetch_chip_input(sym, ref_date=ref)
                if df is None or len(df) < 60:
                    raise RuntimeError(f"输入不足 {0 if df is None else len(df)} 行")
                prev = None
                if os.path.exists(mpath):
                    try:
                        prev = pd.read_pickle(mpath)
                    except Exception:
                        prev = None
                m = chip_metrics(df, prev=prev)
                if m is None or m.empty:
                    raise RuntimeError("指标重算为空")
                m.to_pickle(mpath)
                chk = _cf.check(sym, str(m["date"].max())[:10], ref_date=ref, index=idx)
                if not chk["ok"] and not chk["trustworthy"]:
                    raise RuntimeError(chk["reason"])
                stats["refreshed"] += 1
            except Exception as e:
                stats["failed"] += 1
                failures.append((sym, str(e)[:120]))
                log.warning("chip 刷新失败 %s: %s", sym, e)
            if i % 10 == 0:
                log.info("刷新进度 %d/%d", i, len(syms))
    finally:
        if logged:
            try:
                globals()["_bs"].logout()
            except Exception:
                pass

    print("=====CHIP_REFRESH=====")
    print(f"total={stats['total']} refreshed={stats['refreshed']} "
          f"skipped={stats['skipped']} failed={stats['failed']}")
    for sym, err in failures:
        print(f"  FAIL {sym}: {err}")
    print("=====CHIP_REFRESH_END=====")
    # 只要有失败就返 1（周流水线接「不阻断但告警」语义）
    return 1 if stats["failed"] else 0


def main():
    if "--refresh" in sys.argv:
        return refresh_metrics(force="--force" in sys.argv)
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
