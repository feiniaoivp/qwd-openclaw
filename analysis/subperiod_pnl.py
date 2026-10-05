#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
逐月/逐季盈亏拆分 —— 适配自 frank-quant/ai-trading-videos EP007 (scripts/subperiod.py)
======================================================================================
问的是:这笔钱是均匀赚出来的,还是集中在某一两个月?
只在某一段赚 = 脆;各段都不太亏 = 相对可信。

输出(每期一行):
  - 期数、笔数、盈亏、胜率、各策略拆分
  - 集中度:最赚一期占总盈亏、去掉最赚一期后的残余
  - 可选 --benchmark-csv:同期基准收益对照

与原版(frank-quant EP007 scripts/subperiod.py)的差异:
  - 输入适配本工作区:JSON 逐笔列表(date+pnl 字段,如 data/portfolio_sim_trades.json)
    或 CSV;原版读 Freqtrade backtest zip(原件保留在 external/frank-quant-EP007/)
  - 多空拆分(原版 long/short)改为策略拆分(本工作区无做空维度)
  - 原件的 Windows 路径/feather 基准依赖不保留

用法:
  python3 analysis/subperiod_pnl.py --trades data/portfolio_sim_trades.json
  python3 analysis/subperiod_pnl.py --trades data/portfolio_sim_trades.json \
      --freq Q --benchmark-csv data/benchmark_000300.csv --out data/subperiod_sim.json
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def load_trades(path, pnl_field, strategy_field):
    """从 JSON 逐笔列表 / CSV 还原(date, pnl, strategy)。"""
    if not os.path.exists(path):
        print(f"[sub] 文件不存在: {path}", file=sys.stderr)
        sys.exit(1)
    if str(path).lower().endswith(".json"):
        data = json.load(open(path, encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("trades")
        if not isinstance(data, list):
            print("[sub] JSON 结构无法识别(需逐笔 list 或 {'trades': [...]})", file=sys.stderr)
            sys.exit(1)
        df = pd.DataFrame(data)
    else:
        df = pd.read_csv(path)
    if "date" not in df.columns or pnl_field not in df.columns:
        print(f"[sub] 缺 date / {pnl_field} 列(现有列: {list(df.columns)})", file=sys.stderr)
        sys.exit(1)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["pnl"] = pd.to_numeric(df[pnl_field], errors="coerce").fillna(0.0)
    df["strategy"] = df[strategy_field] if strategy_field in df.columns else "未知"
    return df


def load_benchmark_periods(path, freq):
    """从 CSV(date,close) 还原每期基准收益。"""
    if not os.path.exists(path):
        print(f"[sub] 基准 CSV 不存在: {path}(跳过基准对照)", file=sys.stderr)
        return None
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    if "date" not in cols or "close" not in cols:
        print(f"[sub] 基准 CSV 缺 date/close 列(现有列: {list(df.columns)}),跳过", file=sys.stderr)
        return None
    idx = pd.to_datetime(df[cols["date"]], errors="coerce")
    s = pd.to_numeric(df[cols["close"]], errors="coerce")
    r = pd.Series(s.pct_change().values, index=idx).dropna()
    return r.groupby(r.index.to_period(freq)).apply(lambda x: (1 + x).prod() - 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True, help="逐笔 JSON/CSV")
    ap.add_argument("--freq", default="M", help="M=按月 Q=按季")
    ap.add_argument("--pnl-field", default="pnl", help="盈亏字段(回退链: pnl → profit_abs)")
    ap.add_argument("--strategy-field", default="strategy", help="策略字段")
    ap.add_argument("--exclude-note", default=None, help="note 含此子串的逐笔剔除(如 伪造)")
    ap.add_argument("--benchmark-csv", default=None, help="可选基准 CSV(date,close)")
    ap.add_argument("--out", default=None, help="JSON 输出路径")
    a = ap.parse_args()

    df = load_trades(a.trades, a.pnl_field, a.strategy_field)
    if a.exclude_note:
        before = len(df)
        df = df[~df.get("note", pd.Series("", index=df.index)).astype(str).str.contains(a.exclude_note, na=False)]
        dropped = before - len(df)
        if dropped:
            print(f"[sub] 已按 --exclude-note '{a.exclude_note}' 剔除 {dropped} 笔(标注异常)")

    df["per"] = df["date"].dt.to_period(a.freq)
    mkt = load_benchmark_periods(a.benchmark_csv, a.freq) if a.benchmark_csv else None

    rows = []
    for per, g in df.groupby("per"):
        pnl = float(g["pnl"].sum())
        by_strat = g.groupby("strategy")["pnl"].sum()
        row = dict(
            period=str(per), n=int(len(g)),
            pnl=round(pnl, 1),
            win_rate=round(float((g["pnl"] > 0).mean()) * 100, 1),
            strategy_pnl={k: round(float(v), 1) for k, v in by_strat.items()},
        )
        if mkt is not None and per in mkt.index:
            row["market_pct"] = round(float(mkt.get(per)) * 100, 2)
        rows.append(row)

    tot = float(df["pnl"].sum())
    pos = df[df["pnl"] > 0]["pnl"].sum()

    print(f"{'期':8s} {'笔数':>4s} {'盈亏':>10s} {'胜率%':>6s}  策略拆分" + ("  基准%" if mkt is not None else ""))
    for r in rows:
        strat_str = ",".join(f"{k}:{v}" for k, v in r["strategy_pnl"].items())
        mkt_str = f"  {r.get('market_pct', '')}" if mkt is not None else ""
        print(f"{r['period']:8s} {r['n']:>4d} {r['pnl']:>10.1f} {r['win_rate']:>6.1f}  {strat_str}{mkt_str}")
    print()
    print(f"区间总盈亏      : {tot:,.1f}")
    print(f"盈利期数/总期数 : {int((pd.Series([r['pnl'] for r in rows]) > 0).sum())} / {len(rows)}")
    if rows:
        best = max(rows, key=lambda r: r["pnl"])
        print(f"最赚的一期      : {best['period']}  {best['pnl']:,.1f}")
        if tot > 0 and best["pnl"] > 0:
            print(f"★ 最赚一期占总盈亏 : {best['pnl'] / tot * 100:.0f}%")
            print(f"★ 去掉最赚一期后   : {tot - best['pnl']:,.1f}")
        print(f"正盈亏期合计    : {float(pos):,.1f};负盈亏期合计: {tot - float(pos):,.1f}")

    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        json.dump({"trades_file": a.trades, "freq": a.freq, "rows": rows,
                   "total_pnl": round(tot, 1),
                   "n_positive": int((pd.Series([r['pnl'] for r in rows]) > 0).sum()),
                   "n_periods": len(rows)},
                  open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
        print(f"\n[sub] -> {a.out}")


if __name__ == "__main__":
    main()
