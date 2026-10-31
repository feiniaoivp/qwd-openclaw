#!/usr/bin/env python3
"""回测数据口径三臂对照审计（2026-10-21 回测线深究第一轮）。

背景（provenance 坐实）：
- 2026-10-21 20:30 quant-backtest-daily 运行时 baostock 登录抖动，36 只中 34 只
  经 DataRouter 降级链滑到「新浪日K · 不复权 · datalen=300」（约 15 个月窗口），
  仅 2 只命中 akshare qfq。而报告头部声称「数据范围: 20240101 ~ 20261021」。
- backtest_strategies.py 不消费 provenance，降级无任何标注。

本脚本量化两种失真的实际伤害（三臂，同款 run_simulation 模拟器，唯一变量=数据口径）：
- A1 现状降级形态: 新浪日K 300 根不复权（复刻今晚 34/36 票实况）
- A2 同窗口 qfq:   与 A1 完全相同日期范围，baostock 前复权 → 隔离【复权效应】
- A3 全窗口 qfq:   2024-01-01 起 baostock 前复权 → 隔离【窗口效应】(A3 vs A2)

输出:
- analysis/research/2026-10-21_exec_caliber_data_audit.json（分臂增量落盘）
- analysis/research/2026-10-21_exec_caliber_data_audit.md
"""
import os
import sys
import json
import time
import logging
import traceback

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

from analysis.bs_session import ensure_login, query_history_k_data_plus_retry
from analysis.service import _fetch_sina_daily
from analysis.fib_extension_scan import WATCHLIST
from analysis.backtest_strategies import (
    run_simulation, ALL_STRATEGIES, BENCHMARK_NAMES,
)
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("exec_caliber_audit")

OUT_JSON = os.path.join(WORKSPACE, "analysis/research/2026-10-21_exec_caliber_data_audit.json")
OUT_MD = os.path.join(WORKSPACE, "analysis/research/2026-10-21_exec_caliber_data_audit.md")

END_DATE = "2026-10-21"
STRATEGIES = [(n, f) for n, f in ALL_STRATEGIES if n not in BENCHMARK_NAMES]
STRATEGIES.append(("买入并持有(个股)[参照]", None))  # 特殊处理：首日买入持有


def bs_symbol(sym: str) -> str:
    return (f"sh.{sym}" if sym[0] in "69" else f"sz.{sym}")


def fetch_qfq_full(sym: str, name: str) -> pd.DataFrame | None:
    """baostock 前复权全窗口日线（与 canonical 回测的 qfq 口径一致）。"""
    rs = query_history_k_data_plus_retry(
        bs_symbol(sym),
        "date,open,high,low,close,volume",
        start_date="2024-01-01", end_date=END_DATE,
        frequency="d", adjustflag="2",
        max_retries=3, wait_seconds=1.0,
    )
    if rs is None:
        return None
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True).dropna()
    return df if len(df) >= 100 else None


def fetch_sina_300(sym: str) -> pd.DataFrame | None:
    """新浪日K 300 根不复权（复刻降级链实际形态）。"""
    df = _fetch_sina_daily(sym)  # timeout=15 内置
    if df is None or len(df) < 100:
        return None
    return df.sort_values("date").reset_index(drop=True)


def buy_hold_actions(df: pd.DataFrame):
    """首日买入、持有到期（run_simulation 会在期末强制平仓）。"""
    return [{"date": str(df.iloc[0]["date"].date()), "type": "BUY",
             "price": float(df.iloc[0]["close"]), "reason": "参照:买入并持有"}]


def run_arm(df: pd.DataFrame, strat_list) -> dict:
    out = {}
    for sname, sfn in strat_list:
        try:
            actions = buy_hold_actions(df) if sfn is None else sfn(df)
            m = run_simulation(df, actions)
            if "error" in m:
                out[sname] = {"error": m["error"]}
            else:
                out[sname] = {
                    "total_return_pct": m["total_return_pct"],
                    "sharpe_ratio": m["sharpe_ratio"],
                    "max_drawdown_pct": m["max_drawdown_pct"],
                    "total_trades": m["total_trades"],
                    "win_rate_pct": m["win_rate_pct"],
                }
        except Exception as e:
            log.warning(f"    策略 {sname} 模拟失败: {e}")
            out[sname] = {"error": str(e)[:200]}
    return out


def gap_jumps(a1: pd.DataFrame, a2: pd.DataFrame) -> dict:
    """A1(不复权) vs A2(qfq) 逐日对比 → 复权跳变（除权日假跌幅）机制证据。"""
    m = pd.merge(a1[["date", "close"]].rename(columns={"close": "c_raw"}),
                 a2[["date", "close"]].rename(columns={"close": "c_qfq"}),
                 on="date", how="inner")
    if len(m) < 30:
        return {"aligned_rows": len(m), "jump_days": None}
    r1 = m["c_raw"].pct_change()
    r2 = m["c_qfq"].pct_change()
    diff = (r1 - r2).abs()
    # 复权因子跳变日 = 两口径单日收益差显著大（>1%）
    jumps = m.loc[diff > 0.01, ["date"]].copy()
    jumps["fake_drag_pp"] = (diff[diff > 0.01] * 100).round(2).values
    return {
        "aligned_rows": len(m),
        "jump_days_gt1pp": int((diff > 0.01).sum()),
        "max_single_day_distortion_pp": round(float(diff.max() * 100), 2),
        "jump_dates": [{"date": str(d.date()), "drag_pp": float(p)}
                       for d, p in zip(jumps["date"], jumps["fake_drag_pp"])][:8],
    }


def main():
    stocks = [(sym, meta["name"] if isinstance(meta, dict) else meta)
              for sym, meta in WATCHLIST.items()]
    log.info(f"三臂口径审计: {len(stocks)} 只 × {len(STRATEGIES)} 策略 × 3 臂")

    if not ensure_login():
        log.error("baostock 登录失败，A2/A3 无法获取 qfq —— 显式终止，不降级（本审计就是要量化降级伤害）")
        sys.exit(2)

    result = {
        "meta": {
            "generated_at": time.strftime("%F %T"),
            "end_date": END_DATE,
            "n_stocks": len(stocks),
            "strategies": [n for n, _ in STRATEGIES],
            "arms": {
                "A1": "新浪日K 300根不复权（复刻 2026-10-21 20:30 降级形态）",
                "A2": "与 A1 同日期窗口 baostock qfq（隔离复权效应）",
                "A3": "2024-01-01 起 baostock qfq（隔离窗口效应）",
            },
        },
        "stocks": {},
    }

    for sym, name in stocks:
        t0 = time.time()
        entry = {"name": name, "miss": []}
        try:
            a3 = fetch_qfq_full(sym, name)
            a1 = fetch_sina_300(sym)
            if a3 is None:
                entry["miss"].append("A3:qfq全窗口拉取失败")
            if a1 is None:
                entry["miss"].append("A1:新浪300拉取失败")
            if a3 is None or a1 is None:
                result["stocks"][sym] = entry
                _save(result)
                continue
            # A2 = A3 截取到 A1 的日期范围（两臂唯一差异=复权）
            a2 = a3[a3["date"] >= a1["date"].min()].reset_index(drop=True)
            entry["a1_rows"] = len(a1)
            entry["a1_range"] = [str(a1["date"].min().date()), str(a1["date"].max().date())]
            entry["a3_rows"] = len(a3)
            entry["a3_range"] = [str(a3["date"].min().date()), str(a3["date"].max().date())]
            entry["a2_rows"] = len(a2)
            entry["gap_jumps"] = gap_jumps(a1, a2)
            entry["arms"] = {
                "A1": run_arm(a1, STRATEGIES),
                "A2": run_arm(a2, STRATEGIES),
                "A3": run_arm(a3, STRATEGIES),
            }
            log.info(f"✅ {name}({sym}) A1={len(a1)}根 A3={len(a3)}根 跳变日="
                     f"{entry['gap_jumps'].get('jump_days_gt1pp')} 用时 {time.time()-t0:.1f}s")
        except Exception as e:
            log.warning(f"❌ {name}({sym}) 处理失败: {e}\n{traceback.format_exc()[:300]}")
            entry["miss"].append(f"处理异常: {str(e)[:120]}")
        result["stocks"][sym] = entry
        _save(result)  # 每股增量落盘（>1min 计算，主结果先落盘铁律）

    _save(result)
    log.info(f"完成，共 {len(result['stocks'])} 只，落盘 {OUT_JSON}")


def _save(obj):
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    _bak(OUT_JSON)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
