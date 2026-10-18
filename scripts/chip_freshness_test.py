#!/usr/bin/env python3
"""chip 缓存新鲜度契约门禁（2026-10-18 新增，三层防线之机器层）。

背景
----
chip 第 5 维复活（26cf2ec）后接进生产，但两个读写点都是**无条件命中**：
  * chip_distribution_study.fetch_chip_input() — `if exists: return`（不校验日期）
  * chip_distribution_study.main()            — chipmetrics 存在即读，不重算
  * trader_stock_picks.load_chip_metrics()    — 只取 iloc[-1]，不看多旧
且 cron 无 chip 刷新任务。缓存一旦冻结：
  cost_bias = close(T)/avg_cost(T0) - 1 → 上行行情被系统性推高 → −0.20 档永不触发
  → 加分项静默失效（不报错、分数正常、维度已死）。

本测试用**合成数据 + 临时目录**锁定行为契约，零网络、秒级：
  1. lag <= MAX_LAG_DAYS(5)          → ok，可用
  2. MAX_LAG < lag <= CRITICAL(10)   → ok=False 但 trustworthy=True（告警可用）
  3. lag > CRITICAL                  → trustworthy=False → 生产侧该维 None
  4. chip 比价格新很多（价格缓存落后）→ 判不可信（同样口径失真）
  5. load_chip_metrics 陈旧到不可信  → 返回 None（禁止用旧数据打分）
  6. chip_metrics 增量模式 == 全量模式（数值一致，防增量算法漂移）
  7. refresh 同步判据含「全市场行情基准」（两边同时落后也必须追）

退出码: 0=契约完好, 1=被破坏。
"""
import os
import sys
import tempfile
import shutil

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

from analysis import chip_freshness as cf

FAILS = []
TMP = None


def check(name, cond, got=""):
    if cond:
        print(f"  PASS {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name}  got={got!r}")


def _mk_price(sym, n=140, start="2026-04-01", end_price=10.0):
    """合成价格缓存 pkl（date/open/close）。"""
    days = pd.bdate_range(start=start, periods=n)
    rng = np.random.default_rng(7)
    close = end_price * np.cumprod(1 + rng.normal(0, 0.012, n))
    df = pd.DataFrame({"date": days.strftime("%Y-%m-%d"),
                       "open": close * 1.001, "close": close})
    df.to_pickle(os.path.join(TMP, f"{sym}.pkl"))
    return df


def _mk_chipmetrics(sym, last_date):
    df = pd.DataFrame({"date": ["2026-08-01", last_date],
                       "winner_pct": [0.5, 0.6],
                       "cost_bias": [-0.05, -0.22],
                       "conc90": [0.2, 0.2]})
    df.to_pickle(os.path.join(TMP, f"chipmetrics_{sym}.pkl"))


def t_freshness_thresholds():
    print("\n[1-4] 新鲜度分档")
    idx = cf.trading_index()
    ref = "2026-10-16"
    # 1. 新鲜
    c = cf.check("X", "2026-10-15", ref_date=ref, index=idx)
    check("lag=1 → ok/trustworthy", c["ok"] and c["trustworthy"], c)
    # 2. 陈旧但容忍内（10-12 → 10-16 约 4 交易日? 用 10-02 → >5 交易日）
    c = cf.check("X", "2026-10-02", ref_date=ref, index=idx)
    check("lag 在 (5,10] → 不 ok 但仍 trustworthy",
          0 < (c["lag"] or 0) <= cf.STALE_CRITICAL_DAYS and not c["ok"] and c["trustworthy"], c)
    # 3. 严重陈旧
    c = cf.check("X", "2026-09-01", ref_date=ref, index=idx)
    check("lag>10 → trustworthy=False", c["lag"] > cf.STALE_CRITICAL_DAYS
          and not c["ok"] and not c["trustworthy"], c)
    # 4. chip 比价格新很多 → 价格缓存落后，同样失真
    c = cf.check("X", "2026-10-16", ref_date="2026-09-01", index=idx)
    check("chip 远新于价格 → 不可信", not c["ok"] and not c["trustworthy"], c)


def t_load_chip_metrics_stale_none():
    print("\n[5] load_chip_metrics 陈旧 → None")
    import analysis.trader_stock_picks as tsp
    sym = "888001"
    _mk_price(sym, n=140, end_price=10.0)          # 价格到 ~2026-10 附近
    px_last = cf.price_ref_date(sym, cache_dir=TMP)
    check("合成价格缓存可读", px_last is not None, px_last)

    old = tsp.CHIP_CACHE_DIR
    tsp.CHIP_CACHE_DIR = TMP
    try:
        # 5a. 新鲜 → 有值，且 cost_bias<-0.20 应给 8.5
        _mk_chipmetrics(sym, px_last)
        m = tsp.load_chip_metrics(sym, price_ref=px_last)
        check("新鲜缓存 → 返回指标", m is not None and m["date"] == px_last, m)
        s = tsp.score_chip_structure(sym)
        check("cost_bias<-0.20 → 8.5 分", abs((s or 0) - 8.5) < 1e-6, s)
        # 5b. 冻结 60 交易日 → None（禁止用旧数据打分）
        _mk_chipmetrics(sym, "2026-06-01")
        m2 = tsp.load_chip_metrics(sym, price_ref=px_last)
        check("陈旧到不可信 → 该维 None（不静默用旧值）", m2 is None, m2)
        # 5c. 缺失 → None
        os.remove(os.path.join(TMP, f"chipmetrics_{sym}.pkl"))
        check("缓存缺失 → None", tsp.load_chip_metrics(sym, price_ref=px_last) is None)
        # 5d. 生产路径下（cache_dir 与价格缓存同目录）必须自带兜底：
        #     不传 price_ref 也要能拿到基准（防止隐式目录耦合再次复发）
        _mk_chipmetrics(sym, px_last)
        m3 = tsp.load_chip_metrics(sym)
        check("不传 price_ref 时自身能解析基准（同目录）", m3 is not None, m3)
    finally:
        tsp.CHIP_CACHE_DIR = old


def t_incremental_equals_full():
    print("\n[6] 增量重算 == 全量重算")
    # 直接复用生产脚本的 chip_metrics（合成 OHLCV+turn）
    import importlib.util
    p = os.path.join(WORKSPACE, "scripts", "chip_distribution_study.py")
    spec = importlib.util.spec_from_file_location("cds_test", p)
    cds = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cds)

    n = 220
    days = pd.bdate_range(start="2025-11-01", periods=n).strftime("%Y-%m-%d")
    rng = np.random.default_rng(11)
    close = 10 * np.cumprod(1 + rng.normal(0, 0.015, n))
    df = pd.DataFrame({
        "date": days,
        "high": close * 1.012, "low": close * 0.988, "close": close,
        "volume": rng.integers(1e6, 5e6, n).astype(float),
        "turn": rng.uniform(0.5, 4.0, n),
    })
    full = cds.chip_metrics(df)
    trunc = df.iloc[:-25]
    prev = cds.chip_metrics(trunc)
    inc = cds.chip_metrics(df, prev=prev, n_warmup=60)
    a = full.set_index("date")["cost_bias"]
    b = inc.set_index("date")["cost_bias"]
    common = [d for d in df["date"].iloc[-25:] if d in a.index and d in b.index]
    check("尾部 25 行均有结果", len(common) == 25, len(common))
    dmax = float(np.abs(a.loc[common] - b.loc[common]).max()) if common else 9.9
    check(f"max|Δcost_bias| < 1e-9 (实测 {dmax:.2e})", dmax < 1e-9, dmax)
    # 历史段必须保留未变
    hist = [d for d in prev["date"] if d in a.index and d in b.index]
    dh = float(np.abs(a.loc[hist] - b.loc[hist]).max()) if hist else 9.9
    check(f"历史段保持一致 (实测 {dh:.2e})", dh < 1e-9, dh)


def t_refresh_sync_criterion():
    print("\n[7] refresh 同步判据含全市场基准")
    p = os.path.join(WORKSPACE, "scripts", "chip_distribution_study.py")
    src = open(p).read()
    check("refresh_metrics 存在", "def refresh_metrics" in src)
    check("计算 market_ref（跨票最新行情日）", "market_ref" in src)
    check("同步判据用 all(...) 覆盖多目标", "up_to_date = bool(m_last and i_last) and all(" in src,
          "未找到多目标判据")
    check("fetch_chip_input 支持增量尾巴",
          "start = START if not last else" in src, "未找到增量起点逻辑")
    check("fetch_chip_input 不再无条件命中",
          "if os.path.exists(cache):\n        return pd.read_pickle(cache)" not in src,
          "仍存在旧的无条件命中")


def main():
    global TMP
    TMP = tempfile.mkdtemp(prefix="chipfresh_")
    print(f"临时目录: {TMP}")
    try:
        t_freshness_thresholds()
        t_load_chip_metrics_stale_none()
        t_incremental_equals_full()
        t_refresh_sync_criterion()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print("\n" + "=" * 50)
    if FAILS:
        print(f"❌ 契约被破坏 {len(FAILS)} 项: {FAILS}")
        return 1
    print("✅ chip 新鲜度契约完好")
    return 0


if __name__ == "__main__":
    sys.exit(main())
