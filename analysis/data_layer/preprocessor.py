#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行情数据预处理层（P2，2026-11-01）：把散落各脚本的血泪规则收拢为单一出口
============================================================================

覆盖的已固化教训（来源见 .learnings/ERRORS.md）：
1. 空字符串崩溃（2026-10-08）：外部源停牌/缺失日返回 '' 而非 NaN，
   astype(float) 直接 ValueError → 统一 pd.to_numeric(errors="coerce")。
2. 键存在但值为 None（2026-10-25 RC=3）：嵌套 dict 取值统一 (d.get(k) or default)，
   dict.get(k, {}) 的默认值只在键不存在时生效。
3. 坏值拦截（2026-10-13 601061）：结构性不可能的 bar（close≤0 / high<low /
   close 偏离当根 high-low 中枢 >25%）显式剔除并 log.warning —— 注意本层只做
   **bar 内结构校验**；跨源交叉校验（spot vs K线）属 close_scan_v2 已有防线，
   不在本层职责内。
4. 静默 except 禁令：所有规则失败路径必须 log.warning 可见。

设计约束：
- 对干净数据必须是**零改动直通**（router 六个日线落盘点已接入，坏一个正常
  bar = 生产事故）；全链路无抛出（返回最可用结果 + 告警），消费方 len 检查兜底。
- selftest 含「干净数据直通」对抗用例（10-08/10-13 同族回归）。

自测：python3 analysis/data_layer/preprocessor.py   （退出码 0=过 / 1=违规）
"""

import os
import sys
import logging

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd

log = logging.getLogger(__name__)

REQUIRED_COLS = ("date", "open", "high", "low", "close")
CLOSE_MID_DEVIATION_LIMIT = 0.25  # 601061 教训：>25% 视为结构性坏 bar


def safe_get(d, key, default=None):
    """键不存在 **或值为 None** 都返回 default（(d.get(k) or default) 的显式版）。
    注意与 d.get(k, default) 的区别：后者只在键缺失时生效。"""
    if not isinstance(d, dict):
        return default
    v = d.get(key, default)
    return default if v is None else v


def coerce_numeric(df, cols):
    """指定列安全数值化（空字符串/脏值 → NaN，绝不抛出）。"""
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def clean_daily(df, symbol="", name="", dropna_required=True):
    """日线 bar 结构清洗（对干净数据零改动直通）。

    步骤：数值化 → 结构规则过滤 → 排序去重。全程不抛出；
    剔除行数 >0 时 log.warning 留痕（静默禁令）。
    """
    if df is None or len(df) == 0:
        return df
    try:
        df = df.copy()
        n0 = len(df)
        df = coerce_numeric(df, [c for c in ("open", "high", "low", "close", "volume")
                                 if c in df.columns])
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")

        if dropna_required:
            have = [c for c in REQUIRED_COLS if c in df.columns]
            df = df.dropna(subset=have)

        if {"high", "low", "close"}.issubset(df.columns) and len(df):
            mid = (df["high"] + df["low"]) / 2.0
            bad_struct = (df["close"] <= 0) | (df["high"] < df["low"])
            with_mid = mid.replace(0, pd.NA)
            bad_dev = ((df["close"] - mid).abs() / mid) > CLOSE_MID_DEVIATION_LIMIT
            bad = bad_struct | bad_dev.fillna(False)
            if bool(bad.any()):
                bad_dates = df.loc[bad, "date"].astype(str).tolist()[:5]
                log.warning("⚠️ preprocessor 剔除 %s(%s) 坏 bar %d/%d 行(结构/偏离>=%s): %s",
                            name or "?", symbol or "?", int(bad.sum()), n0,
                            CLOSE_MID_DEVIATION_LIMIT, bad_dates)
                df = df[~bad]

        if "date" in df.columns and len(df):
            df = df.sort_values("date").drop_duplicates(subset="date", keep="last")
        df = df.reset_index(drop=True)
        return df
    except Exception as e:  # noqa: BLE001 —— 预处理绝不阻断取数主链，但必须可见
        log.warning("⚠️ preprocessor.clean_daily 异常(%s %s)，返回原数据: %s",
                    name or "?", symbol or "?", e)
        return df


# ════════════════════════════════════════
# 自测
# ════════════════════════════════════════

def _selftest() -> int:
    failures = []
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07",
                            "2026-01-08", "2026-01-09", "2026-01-12"])

    # 1) 干净数据零改动直通（对抗：误伤正常 bar = 生产事故）
    clean = pd.DataFrame({
        "date": dates, "open": [10.0] * 6, "high": [10.5] * 6,
        "low": [9.5] * 6, "close": [10.2] * 6,
        "volume": [1e6] * 6,
    })
    out = clean_daily(clean, "600000", "直通测试")
    if len(out) != 6:
        failures.append(f"干净数据被误伤: {len(out)}/6")
    elif not out["close"].equals(clean["close"]):
        failures.append("干净数据数值被改动")

    # 2) 空字符串列 → coerce 不崩（10-08 教训；外部源真实返回的 object 列）
    dirty = clean.copy()
    dirty["close"] = dirty["close"].astype(object)
    dirty.loc[1, "close"] = ""
    out2 = clean_daily(dirty, "600000", "空串测试")
    if len(out2) != 5:
        failures.append(f"空字符串行应被 dropna 剔除: 实际 {len(out2)}")

    # 3) 结构坏 bar：high<low / close≤0 / close 偏离中枢 >25%（10-13 教训）
    bad = clean.copy()
    bad.loc[0, "high"], bad.loc[0, "low"] = 9.0, 10.0          # high<low
    bad.loc[2, "close"] = 0.0                                   # close<=0
    bad.loc[4, "close"] = 20.0                                  # 偏离中枢(10.0) 100%
    out3 = clean_daily(bad, "600000", "坏值测试")
    if len(out3) != 3:
        failures.append(f"3 个坏 bar 应全部剔除: 实际 {len(out3)}")

    # 4) 排序去重
    dup = pd.concat([clean, clean.iloc[[0]]]).reset_index(drop=True)  # 首行重复
    dup = dup.iloc[::-1].reset_index(drop=True)                        # 打乱顺序
    out4 = clean_daily(dup, "600000", "去重测试")
    if len(out4) != 6 or not out4["date"].is_monotonic_increasing:
        failures.append("排序/去重失败")

    # 5) safe_get：None 值与缺键同权（10-25 RC=3 教训）
    if safe_get({"a": None}, "a", {}) != {}:
        failures.append("safe_get 对 None 值未返回默认")
    if safe_get({}, "a", {}) != {}:
        failures.append("safe_get 对缺键未返回默认")
    if safe_get({"a": 5}, "a", {}) != 5:
        failures.append("safe_get 丢失了正常值")

    # 6) None 输入与异常输入不抛出
    if clean_daily(None) is not None:
        failures.append("None 输入应原样返回 None")

    if failures:
        print("❌ preprocessor 自测失败:")
        for x in failures:
            print("   -", x)
        return 1
    print("✅ preprocessor 自测通过: 直通/空串/坏bar/排序去重/safe_get/不抛出 共 6 组")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
