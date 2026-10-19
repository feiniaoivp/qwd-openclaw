#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dual_backfill_selftest.py — adaptive_dual 滞后补录链契约自测（2026-10-19）

复现场景：新浪族同窗全挂（2026-10-19 18:30 实测——jsonp 日K 与 hq.sinajs spot 同时失败），
验证 push2 跨族兜底（_fetch_push2_today_bar）能把 expected 当日 bar 补回。

真实网络：仅 push2 stock/get 一次；f86 日期守卫与 _expected_bar_date() 同源，
盘前/盘中/盘后/周末全时段语义一致，可任意时间重跑。
退出码: 0 = 契约通过 / 1 = 失败
"""
#!/usr/bin/env python3
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd

import adaptive_dual as ad


def main() -> int:
    # 打桩：复现"新浪族同窗全挂"——两个新浪源全部返回 None
    ad._fetch_sina_daily = lambda *a, **k: None
    ad.fetch_today_realtime = lambda *a, **k: None

    symbol = "600160"
    expected = ad._expected_bar_date()

    # 合成滞后 df（截止 expected 的前一交易日，镜像 baostock/CSV 未更新的形态；
    # 收盘价取 600160 真实近期值，跳变自检不会误报）
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]),
        "open": [31.0, 30.8, 30.2, 29.9],
        "high": [31.4, 30.9, 30.3, 30.0],
        "low": [30.9, 30.3, 29.9, 29.6],
        "close": [31.21, 30.56, 30.07, 29.75],
        "volume": [1.2e6, 1.0e6, 0.9e6, 1.1e6],
    })

    out = ad._backfill_stale_bars(symbol, df.copy(), "2026-10-16", expected)
    last = out.iloc[-1]
    last_date = pd.Timestamp(last["date"]).strftime("%Y-%m-%d")
    print(f"expected={expected} 补后最新={last_date} close={last['close']} volume={last['volume']}")

    if last_date != expected or float(last["close"]) <= 0:
        print("❌ push2 兜底未能补回当日 bar（push2 不可达或 f86 日期守卫拦截）")
        return 1
    if float(last["volume"]) < 1e4:
        print("❌ volume 量级异常（疑似漏做 f47 手→股 ×100 换算）")
        return 1
    print("✅ push2 跨族兜底契约通过（新浪族全挂场景下仍能补回当日 bar）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
