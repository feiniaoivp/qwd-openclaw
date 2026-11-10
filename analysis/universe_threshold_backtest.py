#!/usr/bin/env python3
"""
min_score=45 阈值回测验证（2026-11-10 任务③，用户批准）
========================================================
问题: L3 综合评分地板 min_score=45 是否过严/过松？

口径:
  - 样本: data/universe/daily 数据湖 800 只（hs300+zz500，core+extended 主体）
  - 回放: 最近 60 个交易日逐日横截面打分（防未来: 因子全 rolling/trailing ≤T）
  - 打分: 完整复用生产 score_stock（禁第二套算法）；三维对照 = snap 抹掉 chip 键
  - 政权: 逐日阈值法（复用 detect_regime 思路向量化全序列）→ 逐日 boost
  - 候选: 排序 → bb_pos 硬门槛[0.15,0.92] → score≥阈值 → 前 TOP_K=15
  - 收益: ret_h = close[T+h]/close[T] - 1（T 收盘信号尾盘可执行，无未来函数）
  - 超额: 候选均值 - 全池同日均值

判定:
  - 过严: 45 线日均候选 < 3，或 0 候选日 > 50%
  - 过松: 45 线 h5 超额 ≤ 0（地板无筛选价值）
  - 建议: 效用 = h5超额 × sqrt(日均候选数)，候选数≥5 限定下的 argmax

产出: data/universe/threshold_backtest_<date>.json（主结果先落盘，铁律）
运行: nohup python3 analysis/universe_threshold_backtest.py > logs/threshold_backtest.log 2>&1 &
"""
#!/usr/bin/env python3
import os
import sys
import json
import time
import shutil

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis"), os.path.join(WORKSPACE, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

LAKE_DIR = os.path.join(WORKSPACE, "data", "universe", "daily")
OUT_JSON = os.path.join(WORKSPACE, "data", "universe", "threshold_backtest.json")
CHIP_SERIES_PKL = os.path.join(WORKSPACE, "data", "universe", "chip_backtest_series.pkl")
REPLAY_DAYS = 60
THRESHOLDS = [30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 60.0]
N_BINS_DECILE = 10

_t0 = time.time()
def log(msg):
    print(f"[{time.time()-_t0:7.1f}s] {msg}", flush=True)


def load_lake_data(max_days=400):
    """数据湖全量 CSV（tail max_days），含 turn。"""
    data = {}
    for f in sorted(os.listdir(LAKE_DIR)):
        if not f.endswith(".csv"):
            continue
        sym = f.replace(".csv", "").replace("sh", "").replace("sz", "").zfill(6)
        try:
            df = pd.read_csv(os.path.join(LAKE_DIR, f))
            df.columns = [c.lower() for c in df.columns]
            if "turn" not in df.columns:
                continue
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index().tail(max_days)
            for c in ("high", "low", "close", "volume", "turn"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
            if len(df) >= 120:
                data[sym] = df
        except Exception as e:
            log(f"⚠️ {sym} 加载失败: {type(e).__name__}: {e}")
    return data


def chip_series_all(data):
    """逐票 chip_metrics 全量逐日序列（复用研究算法，pickle 缓存按最新日校验）。"""
    if os.path.exists(CHIP_SERIES_PKL):
        try:
            cached = pd.read_pickle(CHIP_SERIES_PKL)
            ok = 0
            for sym, df in data.items():
                m = cached.get(sym)
                if m is not None and len(m) and str(m["date"].max())[:10] >= str(df.index.max())[:10]:
                    ok += 1
            if ok >= len(data) * 0.98:
                log(f"chip 序列缓存命中 {ok}/{len(data)}")
                return cached
            log(f"chip 缓存过期（命中 {ok}/{len(data)}）→ 全量重算")
        except Exception as e:
            log(f"⚠️ chip 缓存损坏重算: {e}")
    from chip_distribution_study import chip_metrics
    out = {}
    for i, (sym, df) in enumerate(data.items(), 1):
        try:
            d2 = df.reset_index()
            d2 = d2.rename(columns={d2.columns[0]: "date"})
            m = chip_metrics(d2)
            if m is not None and len(m):
                out[sym] = m
        except Exception as e:
            log(f"⚠️ {sym} chip 失败: {type(e).__name__}: {e}")
        if i % 200 == 0:
            log(f"chip 进度 {i}/{len(data)}")
    _bak(CHIP_SERIES_PKL)
    pd.to_pickle(out, CHIP_SERIES_PKL)
    return out


def _bak(path):
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
    except Exception as e:
        print(f"⚠️ 备份失败(不阻断): {path}: {e}", file=sys.stderr)


def factor_panel(data):
    """逐票向量化因子全序列 → {sym: DataFrame(date indexed)}。"""
    panels = {}
    for sym, df in data.items():
        close = df["close"]
        high, low, vol = df["high"], df["low"], df["volume"]
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_hist = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        up, dn = high.diff(), -low.diff()
        plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
        minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
        a = 1 / 14
        tr_s = tr.ewm(alpha=a, adjust=False).mean()
        pdi = 100 * plus_dm.ewm(alpha=a, adjust=False).mean() / tr_s.replace(0, np.nan)
        mdi = 100 * minus_dm.ewm(alpha=a, adjust=False).mean() / tr_s.replace(0, np.nan)
        dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
        adx = dx.ewm(alpha=a, adjust=False).mean()
        vol_z = (vol - vol.rolling(20).mean()) / vol.rolling(20).std().replace(0, np.nan)
        direction = np.sign(close.diff().fillna(0))
        obv = (direction * vol).cumsum()
        obv_r5 = (obv - obv.shift(5)) > 0
        ma20, sd20 = close.rolling(20).mean(), close.rolling(20).std()
        bb_pos = (close - (ma20 - 2 * sd20)) / ((ma20 + 2 * sd20) - (ma20 - 2 * sd20))
        hv20 = close.pct_change().rolling(20).std() * np.sqrt(252)
        amount_20d = (close * vol).rolling(20).mean()
        panels[sym] = pd.DataFrame({
            "ema_cross": (ema12 > ema26).astype(int), "close_above_ema20": close > ma20,
            "macd_hist": macd_hist, "adx": adx, "vol_zscore": vol_z,
            "obv_rising_5d": obv_r5, "bb_pos": bb_pos, "hv20": hv20,
            "close": close, "amount_20d": amount_20d,
        })
    return panels


def regime_series(data):
    """逐日政权（全池等权净值，向量化）→ Series(date→boost)。"""
    closes = pd.DataFrame({s: d["close"] for s, d in data.items()}).dropna(how="all")
    ew = closes.pct_change().mean(axis=1).add(1).cumprod()
    ret = ew.pct_change()
    ma5, ma20, ma60 = ew.rolling(5).mean(), ew.rolling(20).mean(), ew.rolling(60).mean()
    sp5_20 = (ma5 - ma20) / ma20
    sp20_60 = (ma20 - ma60) / ma60
    vol20 = ret.rolling(20).std() * np.sqrt(252)
    vol60 = ret.rolling(60).std() * np.sqrt(252)
    boost = pd.Series(1.0, index=ew.index)
    boost[(vol20 > vol60 * 1.5)] = 0.90                      # high_vol
    up = (sp5_20 > 0.01) & (sp20_60 > 0)
    dn = (sp5_20 < -0.01) & (sp20_60 < 0)
    boost[up & ~(vol20 > vol60 * 1.5)] = 1.10                # trend_up
    boost[dn & ~(vol20 > vol60 * 1.5)] = 0.85                # trend_down
    return boost


def snap_at(panels, chip, sym, ts):
    """取 sym 在 ts 日的因子快照 dict（无值 → None，与生产 snap 语义一致）。"""
    p = panels.get(sym)
    if p is None or ts not in p.index:
        return None
    r = p.loc[ts]
    def f(x):
        try:
            x = float(x)
            return x if np.isfinite(x) else None
        except Exception:
            return None
    snap = {
        "ema_cross": int(r["ema_cross"]) if pd.notna(r["ema_cross"]) else None,
        "close_above_ema20": bool(r["close_above_ema20"]) if pd.notna(r["close_above_ema20"]) else None,
        "macd_hist": f(r["macd_hist"]), "adx": f(r["adx"]),
        "vol_zscore": f(r["vol_zscore"]),
        "obv_rising_5d": bool(r["obv_rising_5d"]) if pd.notna(r["obv_rising_5d"]) else None,
        "bb_pos": f(r["bb_pos"]), "hv20": f(r["hv20"]),
        "close": f(r["close"]), "amount_20d": f(r["amount_20d"]),
    }
    m = chip.get(sym)
    if m is not None:
        row = m[m["date"].astype(str) == str(ts)[:10]]
        if len(row):
            for k in ("winner_pct", "cost_bias"):
                v = row[k].iloc[0]
                snap[k] = float(v) if pd.notna(v) else None
    return snap


def main():
    from analysis.signal_scoring import score_stock, HARD_GATES, TOP_K

    log("加载数据湖…")
    data = load_lake_data()
    log(f"样本 {len(data)} 只")
    chip = chip_series_all(data)
    log(f"chip 序列就绪 {len(chip)} 只")
    panels = factor_panel(data)
    log("因子面板就绪")
    boost_s = regime_series(data)

    # 回放日集合: 数据湖跨票最新 60 个交易日
    all_dates = sorted(set().union(*[set(p.index) for p in panels.values()]))
    dates = [d for d in all_dates if d in boost_s.index][-REPLAY_DAYS:]
    log(f"回放 {len(dates)} 日: {dates[0].date()} ~ {dates[-1].date()}")

    # 前向收益面板
    close_pivot = pd.DataFrame({s: p["close"] for s, p in panels.items()})
    ret1 = close_pivot.shift(-1) / close_pivot - 1.0
    ret5 = close_pivot.shift(-5) / close_pivot - 1.0

    # 逐日打分（两模式）: 4d = chip 在场（生产口径）；3d = 抹 chip 键（对照）
    daily = {}
    for ti, ts in enumerate(dates):
        for mode in ("4d", "3d"):
            scored = []
            for sym, p in panels.items():
                if ts not in p.index:
                    continue
                snap = snap_at(panels, chip, sym, ts)
                if snap is None:
                    continue
                if mode == "3d":
                    snap = {k: v for k, v in snap.items() if k not in ("winner_pct", "cost_bias", "conc90")}
                total, dims, active = score_stock(snap)
                scored.append((sym, round(total * float(boost_s.get(ts, 1.0)), 2)))
            daily[(ts, mode)] = scored
        if (ti + 1) % 15 == 0:
            log(f"打分进度 {ti+1}/{len(dates)}")

    # 汇总
    results = {}
    for mode in ("4d", "3d"):
        by_thr = {}
        score_rows = []   # (date, sym, score, ret1, ret5) 全池 → 十分位
        for ts in dates:
            scored = daily[(ts, mode)]
            pool_r1 = ret1.loc[ts].dropna()
            pool_r5 = ret5.loc[ts].dropna()
            for sym, sc in scored:
                r1 = ret1.at[ts, sym] if sym in ret1.columns and pd.notna(ret1.at[ts, sym]) else None
                r5 = ret5.at[ts, sym] if sym in ret5.columns and pd.notna(ret5.at[ts, sym]) else None
                score_rows.append((str(ts.date()), sym, sc, r1, r5))
            bb_gate = []
            for sym, sc in scored:
                snap = snap_at(panels, chip, sym, ts)  # bb_pos 查询复用
                bb = snap.get("bb_pos") if snap else None
                if bb is not None and not (HARD_GATES["bb_pos_low"] <= bb <= HARD_GATES["bb_pos_high"]):
                    continue
                bb_gate.append((sym, sc))
            bb_gate.sort(key=lambda x: x[1], reverse=True)
            for thr in THRESHOLDS:
                cands = [(s, sc) for s, sc in bb_gate if sc >= thr][:TOP_K]
                r1s = [ret1.at[ts, s] for s, _ in cands if s in ret1.columns and pd.notna(ret1.at[ts, s])]
                r5s = [ret5.at[ts, s] for s, _ in cands if s in ret5.columns and pd.notna(ret5.at[ts, s])]
                d = by_thr.setdefault(str(thr), {"n_list": [], "r1": [], "r5": [], "pool_r1": [], "pool_r5": [], "sp5": []})
                d["n_list"].append(len(cands))
                d["r1"] += r1s
                d["r5"] += r5s
                d["pool_r1"].append(float(pool_r1.mean()) if len(pool_r1) else None)
                d["pool_r5"].append(float(pool_r5.mean()) if len(pool_r5) else None)
                if len(cands) >= 3 and len(r5s) >= 3:
                    from scipy.stats import spearmanr
                    xs = [sc for s, sc in cands if s in ret5.columns and pd.notna(ret5.at[ts, s])]
                    if len(xs) >= 3:
                        sp = spearmanr(xs, r5s)[0]
                        d["sp5"].append(float(sp) if np.isfinite(sp) else None)
        summary = {}
        for thr, d in by_thr.items():
            n_arr = np.array(d["n_list"], dtype=float)
            r5 = np.array([x for x in d["r5"] if x is not None], dtype=float)
            r1 = np.array([x for x in d["r1"] if x is not None], dtype=float)
            pool5 = np.array([x for x in d["pool_r5"] if x is not None], dtype=float)
            pool1 = np.array([x for x in d["pool_r1"] if x is not None], dtype=float)
            sp5 = np.array([x for x in d["sp5"] if x is not None], dtype=float)
            summary[thr] = {
                "avg_n_per_day": round(float(n_arr.mean()), 2),
                "zero_days_pct": round(float((n_arr == 0).mean()), 3),
                "h1_mean": round(float(r1.mean()), 5) if len(r1) else None,
                "h5_mean": round(float(r5.mean()), 5) if len(r5) else None,
                "h1_excess": round(float(r1.mean() - pool1.mean()), 5) if len(r1) and len(pool1) else None,
                "h5_excess": round(float(r5.mean() - pool5.mean()), 5) if len(r5) and len(pool5) else None,
                "h5_win_rate_vs_pool": round(float((r5 - pool5[:len(r5)] if len(r5) == len(pool5) else r5).mean() > 0), 3) if len(r5) and len(pool5) else None,
                "spearman_h5_avg": round(float(sp5.mean()), 4) if len(sp5) else None,
                "n_samples": int(len(r5)),
            }
        results[mode] = summary

        # 十分位曲线（全池 score → h5）
        df_rows = pd.DataFrame(score_rows, columns=["date", "sym", "score", "ret1", "ret5"])
        df_valid = df_rows.dropna(subset=["ret5"]).copy()
        try:
            df_valid["decile"] = pd.qcut(df_valid["score"], N_BINS_DECILE, labels=False, duplicates="drop")
            dec = df_valid.groupby("decile").agg(
                n=("ret5", "size"), score_mean=("score", "mean"), h5_mean=("ret5", "mean"))
            pool_mean = df_valid["ret5"].mean()
            dec["h5_excess"] = dec["h5_mean"] - pool_mean
            results[mode + "_decile"] = {str(k): {"n": int(v["n"]), "score": round(float(v["score_mean"]), 1),
                                                   "h5_mean": round(float(v["h5_mean"]), 5), "h5_excess": round(float(v["h5_excess"]), 5)}
                                          for k, v in dec.iterrows()}
        except Exception as e:
            log(f"⚠️ 十分位曲线失败: {e}")

    # verdict（基于 4d 生产口径）
    s45 = results["4d"].get("45.0", {})
    avg_n, zero_pct, h5e = s45.get("avg_n_per_day", 0), s45.get("zero_days_pct", 1), s45.get("h5_excess")
    if avg_n < 3 or zero_pct > 0.5:
        verdict = "过严"
    elif h5e is not None and h5e <= 0:
        verdict = "过松"
    else:
        verdict = "大致合适（看边际）"
    # 建议值: 效用 = h5_excess × sqrt(avg_n)，限定 avg_n ≥ 5
    cand_scores = []
    for thr, s in results["4d"].items():
        if not isinstance(s, dict) or "avg_n_per_day" not in s:
            continue
        if s["avg_n_per_day"] >= 5 and s.get("h5_excess") is not None:
            cand_scores.append((thr, s["h5_excess"] * (s["avg_n_per_day"] ** 0.5), s))
    suggested = max(cand_scores, key=lambda x: x[1])[0] if cand_scores else None

    out = {
        "built_at": pd.Timestamp.now().isoformat(),
        "sample": {"n_stocks": len(data), "replay_days": len(dates),
                   "start": str(dates[0].date()), "end": str(dates[-1].date()),
                   "note": "数据湖 800 只样本；T 收盘信号口径；4d=chip在场(生产)/3d=chip缺席(对照)"},
        "verdict": verdict, "suggested_min_score": suggested,
        "thresholds": results["4d"], "thresholds_3d_control": results["3d"],
        "decile_4d": results.get("4d_decile"), "decile_3d": results.get("3d_decile"),
        "duration_s": round(time.time() - _t0, 1),
    }
    # 主结果先落盘（铁律）
    _bak(OUT_JSON)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log(f"✅ 结果落盘 → {OUT_JSON}")

    # 控制台摘要
    print("\n=====THRESHOLD BACKTEST SUMMARY (4d 生产口径)=====")
    print(f"{'阈值':>5} {'日均候选':>8} {'0候选日%':>8} {'h1超额bp':>9} {'h5超额bp':>9} {'Spearman':>9}")
    for thr in [f"{t}" for t in THRESHOLDS]:
        s = results["4d"].get(thr, {})
        if not s:
            continue
        print(f"{thr:>5} {s['avg_n_per_day']:>8} {s['zero_days_pct']*100:>7.0f}% "
              f"{(s['h1_excess'] or 0)*1e4:>9.1f} {(s['h5_excess'] or 0)*1e4:>9.1f} {s['spearman_h5_avg'] or 0:>9.4f}")
    print(f"\nverdict(45): {verdict} | suggested_min_score: {suggested}")
    print("=====END=====")


if __name__ == "__main__":
    main()
