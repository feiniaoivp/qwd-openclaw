#!/usr/bin/env python3
"""补齐 stock_data 目录中缺失的关注股日线 CSV（baostock 前复权）。

背景：2026-09-27 关注池统一为35只，但 /Users/duguke/stock_data 缺10只
（电网8股 + 中信特钢000708 + 金力永磁300748），导致 analyze_all_stocks.py
只能分析 25/35。本脚本按现有 CSV 格式补齐。
"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import baostock as bs
import pandas as pd

STOCK_DATA = "/Users/duguke/stock_data"

MISSING = {
    "000400": "许继电气",
    "000708": "中信特钢",
    "002028": "思源电气",
    "002130": "沃尔核材",
    "002270": "华明装备",
    "300748": "金力永磁",
    "600089": "特变电工",
    "600312": "平高电气",
    "600406": "国电南瑞",
    "601179": "中国西电",
}


def bs_code(sym: str) -> str:
    return f"sh.{sym}" if sym.startswith(("6", "9")) else f"sz.{sym}"


def main():
    lg = bs.login()
    if lg.error_code != "0":
        print(f"❌ baostock 登录失败: {lg.error_msg}")
        return 1

    ok, fail = 0, []
    for sym, name in MISSING.items():
        try:
            rs = bs.query_history_k_data_plus(
                bs_code(sym),
                "date,open,high,low,close,volume,amount,turn,tradestatus",
                start_date="2025-06-30",
                end_date="2026-09-27",
                frequency="d",
                adjustflag="2",  # 前复权
            )
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                fail.append((sym, name, "无数据"))
                continue
            df = pd.DataFrame(
                rows,
                columns=["date", "open", "high", "low", "close", "volume", "amount", "turnover", "tradestatus"],
            )
            # 仅保留交易日
            df = df[df["tradestatus"] == "1"].copy()
            for c in ["open", "high", "low", "close", "volume", "amount", "turnover"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna(subset=["close"]).reset_index(drop=True)
            df["outstanding_share"] = 0.0
            out_cols = ["date", "open", "high", "low", "close", "volume", "amount", "outstanding_share", "turnover"]
            path = os.path.join(STOCK_DATA, f"{sym}_{name}.csv")
            df[out_cols].to_csv(path, index=True, index_label="")
            print(f"✅ {sym} {name}: {len(df)} 行 -> {path}")
            ok += 1
        except Exception as e:
            fail.append((sym, name, str(e)))
            print(f"⚠️ {sym} {name}: {e}")

    bs.logout()
    print(f"\n补齐完成: {ok}/{len(MISSING)} 只")
    if fail:
        print("失败:")
        for f in fail:
            print("  ", f)
    return 0 if ok == len(MISSING) else 1


if __name__ == "__main__":
    sys.exit(main())
