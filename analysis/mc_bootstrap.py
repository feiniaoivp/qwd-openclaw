#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用蒙特卡洛 / Block Bootstrap —— 适配自 frank-quant/ai-trading-videos EP007
===========================================================================
读逐笔收益序列(交易级 MC),做 block bootstrap,回答:这条资金曲线有多少是运气?

输出:
  - 盈利路径占比 prob(profit)、P5/P50/P95、原始 vs 重采样 equity
  - summary JSON(可落盘)、可选 PNG 路径图

与原版(frank-quant EP007 scripts/mc_bootstrap.py)的差异:
  - 输入适配本工作区:JSON 逐笔列表(data/portfolio_sim_trades.json 的
    pnl_pct / profit_ratio 字段)或 CSV;原件保留 Freqtrade zip 支持供溯源
  - 字体改 macOS(PingFang SC);原件在 external/frank-quant-EP007/scripts/

说明:以「每笔交易」为重采样单位(trade-level MC)。多标的交易在时间上会重叠,
按成交序列处理是业界常见的近似;block 保留一点序列相关性。

用法:
  python3 analysis/mc_bootstrap.py --trades data/portfolio_sim_trades.json --field pnl_pct
  python3 analysis/mc_bootstrap.py --trades data/portfolio_sim_trades.json \
      --field pnl_pct --exclude-note 伪造 --iters 5000 --block 10 \
      --out data/mc_sim.json --plot data/mc_sim.png
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

FIELD_CHAIN = ("profit_ratio", "pnl_pct")  # 收益率字段回退链


def load_profit_ratios(path, field=None, exclude_note=None):
    """从 JSON 逐笔列表 / CSV 还原每笔收益率。"""
    if not os.path.exists(path):
        print(f"[mc] 文件不存在: {path}", file=sys.stderr)
        sys.exit(1)

    recs = None
    if str(path).lower().endswith(".json"):
        data = json.load(open(path, encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("trades")
        if not isinstance(data, list):
            print("[mc] JSON 结构无法识别(需逐笔 list 或 {'trades': [...]})", file=sys.stderr)
            sys.exit(1)
        recs = data
    else:
        df = pd.read_csv(path)
        recs = df.to_dict("records")

    if exclude_note:
        before = len(recs)
        recs = [t for t in recs if exclude_note not in str(t.get("note", ""))]
        dropped = before - len(recs)
        if dropped:
            print(f"[mc] 已按 --exclude-note '{exclude_note}' 剔除 {dropped} 笔(标注异常)")

    # 字段选择:显式 --field 优先,否则走回退链;字段名含 pct → 百分数单位,÷100 转小数
    fields = [field] if field else list(FIELD_CHAIN)
    rs = []
    for t in recs:
        v, v_field = None, None
        for f in fields:
            if f in t and t.get(f) is not None:
                v, v_field = t.get(f), f
                break
        if v is None:  # 回退:profit_abs / stake_amount
            pa, sa = t.get("profit_abs"), t.get("stake_amount")
            if pa is not None and sa:
                v, v_field = float(pa) / float(sa), "profit_abs/stake"
        if v is not None:
            x = float(v)
            if "pct" in v_field.lower():
                x = x / 100.0  # 百分数 → 小数收益率
            rs.append(x)
    if not rs:
        print(f"[mc] 逐笔记录里没有可用收益字段(尝试 {fields} / profit_abs÷stake_amount)", file=sys.stderr)
        sys.exit(1)
    return np.asarray(rs, dtype=float), v_field


def block_bootstrap(r, iters, block, seed=7, start=1000.0, keep_paths=400):
    rng = np.random.default_rng(seed)
    N = len(r)
    finals = np.empty(iters)
    paths = []
    for k in range(iters):
        idx = []
        while len(idx) < N:
            s = int(rng.integers(0, max(1, N - block + 1)))
            idx.extend(range(s, min(s + block, N)))
        idx = np.asarray(idx[:N])
        eq = start * np.cumprod(1.0 + r[idx])
        finals[k] = eq[-1]
        if k < keep_paths:
            paths.append(eq)
    return finals, paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True, help="逐笔 JSON/CSV")
    ap.add_argument("--field", default=None, help="收益率字段(默认回退链 profit_ratio→pnl_pct)")
    ap.add_argument("--exclude-note", default=None, help="note 含此子串的逐笔剔除(如 伪造)")
    ap.add_argument("--iters", type=int, default=5000)
    ap.add_argument("--block", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--start", type=float, default=1000.0)
    ap.add_argument("--out", default=None, help="summary json 输出路径")
    ap.add_argument("--plot", default=None, help="png 图输出路径")
    a = ap.parse_args()

    r, field_used = load_profit_ratios(a.trades, a.field, a.exclude_note)
    finals, paths = block_bootstrap(r, a.iters, a.block, a.seed, a.start)
    orig = a.start * np.cumprod(1.0 + r)
    p5, p50, p95 = (float(x) for x in np.percentile(finals, [5, 50, 95]))
    prob_profit = float((finals > a.start).mean())

    summary = {
        "trades_file": a.trades,
        "field_used": field_used,
        "n_trades": int(len(r)),
        "iters": a.iters,
        "block": a.block,
        "seed": a.seed,
        "start": a.start,
        "orig_final": float(orig[-1]),
        "prob_profit": prob_profit,
        "final_P5": p5,
        "final_P50": p50,
        "final_P95": p95,
        "mean_trade_ret": float(r.mean()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"[mc] summary -> {a.out}")

    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "Heiti TC", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False

        fig, ax = plt.subplots(figsize=(11, 6.2), dpi=130)
        fig.patch.set_facecolor("#0d1117")
        ax.set_facecolor("#0d1117")
        for eq in paths:
            ax.plot(eq, color="#3fb6ff", alpha=0.05, lw=0.7)
        ax.plot(orig, color="#ffd24d", lw=2.6, label="实际回测路径", zorder=10)
        ax.axhline(a.start, color="#888", lw=0.8, ls="--", alpha=0.6)
        ax.set_title(
            f"Block Bootstrap 蒙特卡洛 · {a.iters} 条路径 · 盈利概率={prob_profit*100:.0f}%",
            color="#e6ed3f", fontsize=13, pad=14,
        )
        ax.set_xlabel("交易序号", color="#8b949e")
        ax.set_ylabel(f"净值(起点 {a.start:.0f})", color="#8b949e")
        ax.tick_params(colors="#8b949e")
        for sp in ax.spines.values():
            sp.set_color("#30363d")
        ax.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#e6ed3f", loc="upper left")
        ax.text(0.985, 0.06, f"P5={p5:.0f}  P50={p50:.0f}  P95={p95:.0f}",
                transform=ax.transAxes, ha="right", color="#8b949e", fontsize=10)
        plt.tight_layout()
        plt.savefig(a.plot, facecolor="#0d1117")
        print(f"[mc] plot -> {a.plot}")


if __name__ == "__main__":
    main()
