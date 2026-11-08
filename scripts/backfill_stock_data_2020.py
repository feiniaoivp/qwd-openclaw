#!/usr/bin/env python3
"""一次性回填：关注池 36 只 2020-01-01 至今日线（baostock 前复权 qfq）。

任务 3（长周期历史数据回测跑通）步骤 1-2：
- 覆盖 data/stock_data/{sym}.csv（现仅 41 天 2026-09-07~2026-11-03）为全历史
- 格式与现有完全一致: date,open,high,low,close,volume（无索引列）
- 写前 .bak 备份 + 行数防倒退检查（新行数必须 > 旧行数，否则拒绝写盘）
- 禁止缓存空值：拉取失败/空结果不写盘，计入 fail
- 同步更新 _meta/{sym}.json（last_sync_date/row_count/adjust/source/updated_at）

注意：与 scripts/refresh_stock_data.py（/Users/duguke/stock_data 基准池 9 列格式）互不相干；
本脚本只写工作区 data/stock_data（6 列格式，analyze_all_stocks / alpha 管线数据源）。
"""
import os
import sys
import json
import shutil
import logging
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd

from analysis.bs_session import query_history_k_data_plus_retry, logout
from analysis.fib_extension_scan import WATCHLIST

STOCK_DATA = os.path.join(WORKSPACE, "data", "stock_data")
META_DIR = os.path.join(STOCK_DATA, "_meta")
BACKFILL_START = "2020-01-01"
BS_COLS = ["date", "open", "high", "low", "close", "volume", "tradestatus"]
OUT_COLS = ["date", "open", "high", "low", "close", "volume"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_2020")


def bs_code(sym: str) -> str:
    return f"sh.{sym}" if sym.startswith(("6", "9")) else f"sz.{sym}"


def fetch_full(sym: str, end: str) -> pd.DataFrame | None:
    """拉 2020-01-01 起 qfq 日线，返回清洗后 DataFrame 或 None（失败/空）。"""
    rs = query_history_k_data_plus_retry(
        bs_code(sym), ",".join(BS_COLS),
        start_date=BACKFILL_START, end_date=end,
        frequency="d", adjustflag="2",  # 前复权
        max_retries=3, wait_seconds=2.0,
    )
    if rs is None:
        return None
    if rs.error_code != "0":
        log.error("❌ %s baostock 错误: %s", sym, rs.error_msg)
        return None
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=BS_COLS)
    df = df[df["tradestatus"] == "1"].copy()  # 仅正常交易日
    for c in ["open", "high", "low", "close", "volume"]:
        # 外部源容错：''/None → NaN（astype(float) 会崩，历史事故 2026-10-08）
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    return df[OUT_COLS] if len(df) else None


def main() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(META_DIR, exist_ok=True)
    ok, skipped, failed = 0, 0, []
    for sym, name in WATCHLIST.items():
        path = os.path.join(STOCK_DATA, f"{sym}.csv")
        old_n = 0
        if os.path.exists(path):
            try:
                old_n = max(0, sum(1 for _ in open(path, encoding="utf-8")) - 1)
            except Exception as e:
                failed.append((sym, f"读旧文件失败: {e}"))
                continue
        try:
            df = fetch_full(sym, today)
        except Exception as e:
            failed.append((sym, f"拉取异常: {e}"))
            continue
        if df is None or df.empty:
            # 禁止缓存空值：绝不写空/截断数据覆盖存量
            failed.append((sym, "拉取为空/失败，保留旧数据"))
            continue
        if len(df) <= old_n:
            failed.append((sym, f"防倒退: 新 {len(df)} 行 <= 旧 {old_n} 行，拒绝写盘"))
            continue
        # 校验日期范围合理
        d0, d1 = str(df["date"].iloc[0]), str(df["date"].iloc[-1])
        if d1 < "2026-11-01":
            failed.append((sym, f"数据新鲜度异常: 最新日期 {d1}，拒绝写盘"))
            continue
        shutil.copy2(path, path + ".bak_backfill2020")
        df.to_csv(path, index=False)
        meta = {
            "last_sync_date": d1,
            "row_count": int(len(df)),
            "first_date": d0,
            "adjust": "qfq",
            "source": "baostock",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(os.path.join(META_DIR, f"{sym}.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        log.info("✅ %s %s: %d → %d 行 (%s ~ %s)", sym, name, old_n, len(df), d0, d1)
        ok += 1
    logout()
    log.info("回填完成: 成功 %d | 跳过/失败 %d (共 %d)", ok, len(failed), len(WATCHLIST))
    for sym, msg in failed:
        log.warning("  失败 %s: %s", sym, msg)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
