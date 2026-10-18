#!/usr/bin/env python3
"""簇门禁窗口口径反事实 A/B：全期映射 vs 滚动 252d 映射（2026-10-18 深挖 ①-A/B）。

方法: 复用生产回测引擎 PortfolioExecutor.run_backtest（真实批次顺序：
      信号融合→tier→030→策略切换→钝化→簇上限→去重→撑铃约束→execute_trade），
      唯一差异 = 注入的 sym_to_cluster 映射：
        arm_prod : data/corr_clusters.json（生产全期）
        arm_roll : 逐日滚动 252d 相关重算（因果，当日及以前数据）
        arm_off  : 关闭簇门禁 = 现状基线
      价格走本地缓存（35 只，000708 缺价跳过），零网络。
产出: analysis/research/<今日>_cluster_window_ab.json
"""
import os
import sys
import json
import pickle
from datetime import date
from itertools import combinations

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

# 进程级 socket 兜底（18:2x 实测: 逐日循环中 030/数据源有裸 socket 调用 → 永久 sock_read 阻塞）
from analysis.net_guard import install_default_timeout
install_default_timeout(15)

CACHE = os.path.join(WORKSPACE, "data/research_price_cache")
CLU_JSON = os.path.join(WORKSPACE, "data/corr_clusters.json")
THRESHOLD = 0.55
ROLL = 252
MIN_PERIODS = 200
START = "2024-07-01"   # 预留 252d 滚动预热（缓存起点 2024-04-01）
END = "2026-10-16"


def load_prices():
    with open(CLU_JSON) as f:
        prod = json.load(f)
    syms = sorted(prod.get("symbol_to_cluster", {}).keys())
    data, missing = {}, []
    for s in syms:
        p = os.path.join(CACHE, f"{s}.pkl")
        if not os.path.exists(p):
            missing.append(s)
            continue
        df = pd.read_pickle(p)
        df = df[["date", "open", "high", "low", "close", "volume"]].copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        data[s] = df
    return prod, data, missing


def build_roll_maps(data):
    """逐日滚动相关 → {date_str: {sym: cluster}}（并查集 0.55）。"""
    syms = list(data.keys())
    px = pd.DataFrame({s: data[s]["close"] for s in syms}).sort_index()
    ret = px.pct_change()
    dates = px.index
    maps = {}
    for i in range(MIN_PERIODS, len(dates)):
        day = dates[i]
        win = ret.iloc[max(0, i - ROLL + 1): i + 1]
        corr = win.corr(min_periods=MIN_PERIODS)
        parent = {s: s for s in syms}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in combinations(syms, 2):
            v = corr.loc[a, b]
            if pd.notna(v) and v >= THRESHOLD:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
        groups = {}
        for s in syms:
            groups.setdefault(find(s), []).append(s)
        m = {}
        for gi, g in enumerate(sorted([v for v in groups.values() if len(v) >= 2], key=len, reverse=True)):
            for s in g:
                m[s] = f"C{gi+1:02d}"
        maps[day.strftime("%Y-%m-%d")] = m
    return maps


def main():
    prod, data, missing = load_prices()
    syms = sorted(data.keys())
    names = {}
    try:
        from analysis.portfolio_core import STOCKS as CORE_STOCKS
        names = dict(CORE_STOCKS)
    except Exception:
        pass
    stocks = [(s, names.get(s, s)) for s in syms]
    print(f"[ab] 可用 {len(stocks)} 票 | 缺价 {missing}", flush=True)

    print("[ab] 计算逐日滚动簇映射...", flush=True)
    roll_maps = build_roll_maps(data)
    print(f"[ab] 滚动映射 {len(roll_maps)} 日", flush=True)

    import analysis.portfolio_core as core
    from analysis.portfolio_executor import PortfolioExecutor
    from analysis.execution_port import BacktestPort

    # 标准化1: 三因素共振门控 off。execute_trade 每次买入 instantiates 新 ResonanceGate
    # → 每次买入都拉 akshare 情绪/基本面（实测 sock_read 挂起点，且跨买入缓存失效）。
    # 三臂同口径置中性，簇映射是唯一变量。
    import analysis.three_factor_helper as tfh

    class _NeutralGate:
        def check_buy(self, symbol, tech_score=None):
            return True, {"detail": "AB-off"}

    tfh.ResonanceGate = _NeutralGate

    # 标准化2: 030 风控全臂只刷一次（避免 556 天重复网络刷新引入噪声）
    core.RiskGuard._last_refresh_key = None

    def fetch(sym):
        df = data.get(sym)
        if df is None:
            return None
        d = df.reset_index()
        return d

    results = {}
    arms = {
        "arm_prod": prod.get("symbol_to_cluster", {}),
        "arm_roll": roll_maps.get(END, {}),
        "arm_off": {},
    }

    orig = core._load_corr_clusters
    # 安全阀: 三个生产文件全部重定向到 /tmp，回测结束按 md5 复核未被改写
    orig_state, orig_trades, orig_equity = core.STATE_FILE, core.TRADES_FILE, core.EQUITY_FILE
    core.STATE_FILE = "/tmp/_ab_state.json"
    core.TRADES_FILE = "/tmp/_ab_trades.json"
    core.EQUITY_FILE = "/tmp/_ab_equity.csv"
    for arm, mapping in arms.items():
        print(f"\n===== {arm} ({len(mapping)} 票入簇) =====", flush=True)
        core._load_corr_clusters = lambda m=mapping: dict(m)
        try:
            port = BacktestPort(initial_capital=core.INITIAL_CAPITAL * len(stocks),
                                commission=core.COMMISSION, slippage=core.SLIPPAGE,
                                price_data={s: data[s] for s in syms})
            ex = PortfolioExecutor(port, stocks=stocks,
                                   state_file="/tmp/_never_write_state.json")
            # 保证回测不碰生产 state
            ex.state = {"last_signal_date": None, "positions": {}}
            r = ex.run_backtest(start_date=START, end_date=END)
        finally:
            core._load_corr_clusters = orig
            core.BACKTEST_MODE = True
        results[arm] = r
        print(f"[ab] {arm}: {json.dumps({k: v for k, v in r.items() if not isinstance(v, (list, dict))}, ensure_ascii=False)}", flush=True)

    core.STATE_FILE, core.TRADES_FILE, core.EQUITY_FILE = orig_state, orig_trades, orig_equity
    out = {"date": date.today().isoformat(), "window": f"{START}~{END}",
           "roll_days": ROLL, "threshold": THRESHOLD,
           "stocks": len(stocks), "missing": missing, "arms": {}}
    for arm, r in results.items():
        trades = r.get("trades") or r.get("trade_log") or []
        out["arms"][arm] = {
            "total_return_pct": r.get("total_return_pct"),
            "final_equity": r.get("final_equity"),
            "max_drawdown_pct": r.get("max_drawdown_pct"),
            "sharpe": r.get("sharpe") or r.get("sharpe_ratio"),
            "n_trades": len(trades) if isinstance(trades, list) else r.get("n_trades"),
            "raw_keys": sorted(r.keys()),
            "daily_results": r.get("daily_results"),
        }
    path = os.path.join(WORKSPACE, f"analysis/research/{date.today().isoformat()}_cluster_window_ab.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n[ab] 已写 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
