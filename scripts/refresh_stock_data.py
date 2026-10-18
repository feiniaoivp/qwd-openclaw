#!/usr/bin/env python3
"""基准池 CSV 增量刷新器（数据债修复 2026-10-18）。

背景:
- /Users/duguke/stock_data/*.csv 是 alpha_beta_attr.py 等权基准池的数据源（54 只），
  由一次性脚本 fill_missing_stock_csv.py 创建（end_date 冻结在 2026-09-27），
  之后无任何刷新方 → 2026-10-18 实测 54/54 全部陈旧（最新 10-08，最旧 07-09）。
- 本脚本提供增量刷新：已有 CSV 从最后日期+1 追加；SSOT 关注池缺票从 2025-06-30 全量补建。

设计:
- 关注池 SSOT: analysis.fib_extension_scan.WATCHLIST（36 只），禁止硬编码清单
- 幂等: baostock 无新行即跳过写盘（不依赖交易日历）
- 追加行 outstanding_share=0.0（与既有约定一致；基准池只用 close）
- 前复权口径与存量一致（adjustflag=2）；除权后历史因子漂移接受（基准用途）
- 900925 B股 baostock 无数据，已知跳过
接入: 周六 weekly_full_pipeline 步骤 0m（非阻断，失败仅告警）
"""
import os
import sys
import glob
import logging

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd

STOCK_DATA = os.getenv("STOCK_DATA", "/Users/duguke/stock_data")
FULL_START = "2025-06-30"
OUT_COLS = ["date", "open", "high", "low", "close", "volume", "amount", "outstanding_share", "turnover"]
BS_COLS = ["date", "open", "high", "low", "close", "volume", "amount", "turn", "tradestatus"]  # baostock 字段名是 turn，落盘时重命名为 turnover

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("refresh_stock_data")


def bs_code(sym: str) -> str:
    return f"sh.{sym}" if sym.startswith(("6", "9")) else f"sz.{sym}"


def fetch_bs(bs, sym: str, start: str, end: str):
    rs = bs.query_history_k_data_plus(
        bs_code(sym), ",".join(BS_COLS),
        start_date=start, end_date=end, frequency="d", adjustflag="2",
    )
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rs.error_code != "0":
        raise RuntimeError(f"baostock {sym}: {rs.error_msg}")
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=BS_COLS).rename(columns={"turn": "turnover"})
    df = df[df["tradestatus"] == "1"].copy()
    for c in ["open", "high", "low", "close", "volume", "amount", "turnover"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close"]).reset_index(drop=True)


def to_out(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["outstanding_share"] = 0.0
    return out[OUT_COLS]


def main():
    import baostock as bs
    from analysis.fib_extension_scan import WATCHLIST

    lg = bs.login()
    if lg.error_code != "0":
        log.error("baostock 登录失败: %s", lg.error_msg)
        return 1

    os.makedirs(STOCK_DATA, exist_ok=True)
    existing = sorted(glob.glob(os.path.join(STOCK_DATA, "*.csv")))
    by_sym = {}
    for p in existing:
        by_sym.setdefault(os.path.basename(p).split("_")[0], []).append(p)
    todo, dup_skipped = [], []
    for sym, paths in sorted(by_sym.items()):
        if len(paths) > 1:
            # 去重：优先无空格规范名（基准池等权双计会失真）
            paths = sorted(paths, key=lambda p: (" " in os.path.basename(p), -os.path.getsize(p)))
            for stray in paths[1:]:
                dup_skipped.append(stray)
                log.warning("⏭️ 重复文件跳过（待用户确认删除）: %s", os.path.basename(stray))
        p = paths[0]
        try:
            old = pd.read_csv(p)
            first = str(old["date"].iloc[0])[:10]
            last = str(old["date"].iloc[-1])[:10]
            std = {"date", "open", "high", "low", "close", "volume", "amount", "outstanding_share", "turnover"} <= set(old.columns)
            # 桩文件自愈：正常日线 CSV 不可能 <30 行；<30 视为损坏/截断，全量重建
            stub = std and len(old) < 30
        except Exception as e:
            log.warning("读取失败 %s: %s", p, e)
            continue
        rebuild = (not std) or stub
        rebuild_start = FULL_START if stub else first
        if stub:
            log.warning("🩹 %s 仅 %d 行（截断/损坏），将全量重建", os.path.basename(p), len(old))
        # 非标准格式（旧精简 6 列）→ 全量重建迁移为标准格式，保留原起始日期
        todo.append((sym, None, p, rebuild_start if rebuild else last, rebuild))
    for sym, name in WATCHLIST.items():
        if sym not in by_sym:
            todo.append((sym, name, os.path.join(STOCK_DATA, f"{sym}_{name}.csv"), FULL_START, False))

    ok, skip, fail = 0, 0, []
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    for sym, name, path, start, rebuild in todo:
        if sym == "900925":
            log.info("⏭️ %s 机电B股: baostock 无数据（已知限制），跳过", sym)
            skip += 1
            continue
        try:
            new = fetch_bs(bs, sym, start, today)
            if new is None or new.empty:
                skip += 1
                continue
            if name is None and not rebuild:
                # 增量追加（baostock 含 start 当日，过滤重叠）
                old = pd.read_csv(path)
                last = str(old["date"].iloc[-1])[:10]
                new = new[new["date"] > last]
                if new.empty:
                    skip += 1
                    continue
                add = to_out(new)
                merged = pd.concat([old[OUT_COLS], add], ignore_index=True)
                merged.to_csv(path, index=True, index_label="")
                log.info("✅ %s: +%d 行 (→%s)", os.path.basename(path), len(add), add["date"].iloc[-1])
            else:
                # 全量重建（缺票补建 / 旧格式迁移 / 桩文件自愈）
                # 防倒退检查：重建结果不得明显少于现有行数（除非现有本身就是桩）
                try:
                    old_n = len(pd.read_csv(path))
                except Exception:
                    old_n = 0
                if old_n >= 30 and len(new) < old_n:
                    fail.append((sym, f"重建行数倒退 {len(new)}<{old_n}，拒绝写盘"))
                    log.warning("🛑 %s: %s", sym, fail[-1][1])
                    continue
                import shutil
                shutil.copy2(path, path + ".bak_refresh")
                to_out(new).to_csv(path, index=True, index_label="")
                log.info("🆕 %s: %d 行 (%s，起 %s)", os.path.basename(path), len(new), "重建" if rebuild else "SSOT 补建", start)
            ok += 1
        except Exception as e:
            fail.append((sym, str(e)))
            log.warning("❌ %s: %s", sym, e)

    bs.logout()
    print(f"\n刷新完成: 更新/补建 {ok} | 已最新/跳过 {skip} | 失败 {len(fail)} (共 {len(todo)})")
    if dup_skipped:
        print("重复文件（待用户确认删除）:")
        for p in dup_skipped:
            print("  ", p)
    if fail:
        for sym, msg in fail:
            print(f"  失败 {sym}: {msg}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
