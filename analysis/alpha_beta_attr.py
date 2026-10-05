#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alpha/beta/净敞口归因 —— 适配自 frank-quant/ai-trading-videos EP007 (scripts/alpha_beta.py)
==========================================================================================
口径(与 EP007 方法一致,适配 A 股):
  基准        = 等权关注池日收益均值(与原版「20 币等权大盘」同方法论),或 --benchmark-csv 指定
  策略净值    = 逐日盯市(资金曲线 CSV 的 total_value)
  beta / alpha= 策略日收益 对 基准日收益 做 OLS;alpha 为截距,年化 ×252
  beta 拖累   = beta × 基准区间总收益
  alpha 贡献  = 策略区间总收益 − beta 拖累
  持仓占比    = 资金曲线 positions_held>0 的天数占比(原版为净敞口,本工作区
                资金曲线无名义敞口字段,无字段 = 直说没有,不脑补)

与原版(frank-quant EP007 scripts/alpha_beta.py)的差异:
  - 基准从 Freqtrade feather 改为等权关注池(/Users/duguke/stock_data/*.csv)或指定 CSV
  - 年化 ×252(A股),原版 ×365(加密)
  - 原件保留在 external/frank-quant-EP007/scripts/ 供溯源

⚠️ 数据要求:T≥30 个共同交易日才有统计意义;当前资金曲线过短时会明确拒绝。

用法:
  python3 analysis/alpha_beta_attr.py --equity-csv data/portfolio_equity.csv
  python3 analysis/alpha_beta_attr.py --equity-csv data/portfolio_equity.csv \
      --benchmark-csv data/benchmark_000300.csv --out data/alpha_beta_sim.json
"""
import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

STOCK_DATA = os.getenv("STOCK_DATA", "/Users/duguke/stock_data")
MIN_T = 30  # OLS 显著性最低共同交易日数


def load_equity(path):
    """资金曲线 CSV → 日收益 Series(index=date)。"""
    if not os.path.exists(path):
        print(f"[ab] 资金曲线不存在: {path}", file=sys.stderr)
        sys.exit(1)
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    if "date" not in cols or "total_value" not in cols:
        print(f"[ab] 资金曲线缺 date/total_value 列(现有列: {list(df.columns)})", file=sys.stderr)
        sys.exit(1)
    idx = pd.to_datetime(df[cols["date"]], errors="coerce")
    tv = pd.to_numeric(df[cols["total_value"]], errors="coerce")
    held = pd.to_numeric(df[cols.get("positions_held", cols["date"])], errors="coerce") \
        if "positions_held" in cols else None
    r = pd.Series(tv.pct_change().values, index=idx).dropna()
    held_frac = float((held > 0).mean()) if held is not None else None
    return r, held_frac


def load_benchmark_from_csv(path):
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    if "date" not in cols or "close" not in cols:
        print(f"[ab] 基准 CSV 缺 date/close 列(现有列: {list(df.columns)})", file=sys.stderr)
        sys.exit(1)
    idx = pd.to_datetime(df[cols["date"]], errors="coerce")
    s = pd.to_numeric(df[cols["close"]], errors="coerce")
    return pd.Series(s.pct_change().values, index=idx).dropna()


def load_benchmark_pool(stock_dir):
    """等权关注池基准:全部 stock_data/*.csv 日收益等权均值(与 EP007「20币等权」同方法论)。"""
    files = sorted(glob.glob(os.path.join(stock_dir, "*.csv")))
    if not files:
        print(f"[ab] {stock_dir} 无股票 CSV,等权基准不可用", file=sys.stderr)
        sys.exit(1)
    px = {}
    for f in files:
        sym = os.path.basename(f).split("_")[0]
        try:
            df = pd.read_csv(f, usecols=["date", "close"])
        except Exception as exc:
            print(f"[ab] ⚠️ 跳过不可读 CSV {f}: {exc}")
            continue
        idx = pd.to_datetime(df["date"], errors="coerce")
        px[sym] = pd.Series(pd.to_numeric(df["close"], errors="coerce").values, index=idx)
    if not px:
        print("[ab] 无可用股票数据,等权基准不可用", file=sys.stderr)
        sys.exit(1)
    P = pd.DataFrame(px).sort_index()
    return P.pct_change().mean(axis=1).dropna(), len(px)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--equity-csv", required=True, help="资金曲线 CSV(date,total_value)")
    ap.add_argument("--benchmark-csv", default=None, help="基准 CSV(date,close);不给用等权关注池")
    ap.add_argument("--stock-data", default=STOCK_DATA, help="等权关注池目录")
    ap.add_argument("--annual", type=float, default=252.0, help="年化因子(A股252)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    r_s, held_frac = load_equity(a.equity_csv)
    if a.benchmark_csv:
        mkt = load_benchmark_from_csv(a.benchmark_csv)
        bench_desc = a.benchmark_csv
    else:
        mkt, n_pool = load_benchmark_pool(a.stock_data)
        bench_desc = f"等权关注池({n_pool} 只, {a.stock_data})"

    # 对齐共同交易日
    common = r_s.index.intersection(mkt.index)
    if len(common) < MIN_T:
        print(f"[ab] ⛔ 共同交易日仅 {len(common)} 天(<{MIN_T}),OLS 无统计意义。"
              f"资金曲线需积累(当前 {a.equity_csv} 只有 {len(r_s)} 行)。", file=sys.stderr)
        sys.exit(1)
    m = mkt.reindex(common).to_numpy(float)
    s = r_s.reindex(common).to_numpy(float)
    ok = np.isfinite(m) & np.isfinite(s)
    beta, alpha_d = np.polyfit(m[ok], s[ok], 1)
    pred = beta * m[ok] + alpha_d
    ss_res = float(((s[ok] - pred) ** 2).sum())
    ss_tot = float(((s[ok] - s[ok].mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0

    # 策略区间总收益(按资金曲线首尾)
    first, last = r_s.index.min(), r_s.index.max()
    total = float((1 + r_s.loc[first:last]).prod() - 1)
    mkt_total = float((1 + mkt.reindex(r_s.index).fillna(0.0)).prod() - 1)
    drag = beta * mkt_total

    res = {
        "equity_file": a.equity_csv,
        "benchmark": bench_desc,
        "window": f"{first.date()}..{last.date()}",
        "n_common_days": int(ok.sum()),
        "market_total_return_pct": round(mkt_total * 100, 2),
        "strategy_total_return_pct": round(total * 100, 2),
        "beta": round(float(beta), 3),
        "r2": round(float(r2), 3),
        "alpha_annual_pct": round(float(alpha_d) * a.annual * 100, 1),
        "beta_drag_pct": round(drag * 100, 1),
        "alpha_contrib_pct": round((total - drag) * 100, 1),
        "held_days_frac_pct": round(held_frac * 100, 1) if held_frac is not None else None,
        "net_exposure_pct": None,  # 资金曲线无名义敞口字段,无字段 = 直说
    }
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"[ab] -> {a.out}")


if __name__ == "__main__":
    main()
