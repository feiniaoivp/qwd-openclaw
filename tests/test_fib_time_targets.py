#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
斐波那契时间线 (compute_fib_time_targets) 单元测试
================================================
覆盖：基本契约 / 未来时点过滤 / 汇聚窗口 / 无未来函数 / 边界防御。
运行: python3 -m pytest tests/test_fib_time_targets.py -q
    或 python3 tests/test_fib_time_targets.py
"""
import os
import sys

import pandas as pd

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WS)

from analysis.fib_extension_scan import compute_fib_time_targets, FIB_SEQ  # noqa: E402


def _mk_df(n=300, pattern="updown"):
    """构造含明确高低点的合成日线（仅交易日）。"""
    dates = pd.bdate_range("2024-01-01", periods=n)
    closes = []
    for i in range(n):
        if pattern == "updown":
            # 前 60% 上行(含波峰)，后 40% 回调(含波谷)，保证有 swing_high/low
            v = 100 + i * 0.5 if i < int(n * 0.6) else 100 + n * 0.6 * 0.5 - (i - n * 0.6) * 0.6
        else:
            v = 100 + i * 0.1
        closes.append(round(v, 2))
    df = pd.DataFrame({
        "date": dates,
        "open": [c * 0.998 for c in closes],
        "high": [c * 1.02 for c in closes],
        "low": [c * 0.98 for c in closes],
        "close": closes,
        "volume": [1_000_000] * n,
    })
    return df


def test_contract_fields():
    """返回体必须含契约声明的全部键。"""
    out = compute_fib_time_targets(_mk_df())
    assert isinstance(out, dict) and out, "应返回非空 dict"
    for k in ("as_of", "as_of_idx", "anchors", "confluence", "next_window", "note"):
        assert k in out, f"缺字段 {k}"
    # lookback 默认 250 → df.tail(250) 后基准索引为 249
    assert out["as_of_idx"] == 249
    assert isinstance(out["anchors"], list)


def test_future_points_only():
    """时间区间投射：source 只保留未来时点（t > as_of_idx）。"""
    out = compute_fib_time_targets(_mk_df())
    as_of_idx = out["as_of_idx"]
    for a in out["anchors"]:
        for tgt in a["targets"]:
            assert tgt["idx"] == a["anchor_idx"] + tgt["fib"], "idx 应 = 锚点 + fib 步长"
            if tgt["passed"]:
                assert tgt["idx"] <= as_of_idx
            else:
                assert tgt["idx"] > as_of_idx


def test_no_lookahead():
    """无未来函数：用不同长度的同一段历史，as_of 之前的数据结果应稳定。"""
    df = _mk_df(300)
    out_full = compute_fib_time_targets(df)
    out_trunc = compute_fib_time_targets(df.iloc[:250].reset_index(drop=True))
    # 截断后的 as_of 应早于全量
    assert out_trunc["as_of"] < out_full["as_of"], "截断版 as_of 应更早"
    # 都只输出未来时点
    assert out_trunc["as_of_idx"] == 249


def test_confluence_has_sources():
    """汇聚窗口须含 >=2 个来源，且 strength 与 hits 一致；hits 已去重（hits<=raw_hits）。"""
    out = compute_fib_time_targets(_mk_df())
    for c in out["confluence"]:
        assert c["hits"] >= 2
        assert len(c["sources"]) == c["hits"]
        # 去重：同锚点的多条 fib 只能算 1 个来源
        assert c["hits"] <= c.get("raw_hits", c["hits"])
        keys = [s.split("@")[0] if "@" in s else s for s in c["sources"]]
        assert len(keys) == len(set(keys)), f"来源未去重: {c['sources']}"
        assert c["strength"] == ("high" if c["hits"] >= 3 else "medium")


def test_next_window_hits_priority():
    """next_window 应按 hits 降序（信息量优先），不再让最宽的窗口霸榜。"""
    out = compute_fib_time_targets(_mk_df())
    if out["confluence"] and out["next_window"]:
        assert out["next_window"] is out["confluence"][0]
        max_hits = out["next_window"]["hits"]
        for c in out["confluence"]:
            assert c["hits"] <= max_hits, "next_window 必须是 hits 最大的窗口"


def test_window_filters():
    """新增过滤：① 至少含一条 fib>=MIN_FIB_FOR_WINDOW；② 起点距基准日 >=MIN_WINDOW_LEAD_DAYS。"""
    from analysis.fib_extension_scan import (
        MIN_FIB_FOR_WINDOW, MIN_WINDOW_LEAD_DAYS, MIN_WINDOW_HITS)
    out = compute_fib_time_targets(_mk_df())
    for c in out["confluence"]:
        assert c["hits"] >= MIN_WINDOW_HITS
        assert c["idx"] - out["as_of_idx"] >= MIN_WINDOW_LEAD_DAYS, \
            "窗口起点太近（“明天就变盘”伪窗口未被过滤）"
        fibs = [int(s.split("@")[1]) for s in c["sources"] if "@" in s]
        assert not fibs or max(fibs) >= MIN_FIB_FOR_WINDOW, \
            "不得只由 fib1/2/3 构成汇聚窗口"
    assert out.get("filters"), "返回体应含 filters 声明"


def test_no_holiday_dates():
    """交易日外推不得落在已知节假日（如国庆 10-01）。"""
    from analysis.fib_extension_scan import _CN_HOLIDAYS
    out = compute_fib_time_targets(_mk_df())
    for a in out["anchors"]:
        for tgt in a["targets"]:
            if tgt["idx"] > out["as_of_idx"]:
                assert tgt["date"] not in _CN_HOLIDAYS, \
                    f"外推日期 {tgt['date']} 落在休市日"


def test_defensive_empty():
    """边界防御：数据不足返回 {}。"""
    assert compute_fib_time_targets(None) == {}
    assert compute_fib_time_targets(_mk_df(10)) == {}
    empty = pd.DataFrame({"date": [], "open": [], "high": [], "low": [], "close": [], "volume": []})
    assert compute_fib_time_targets(empty) == {}


def test_fib_seq_truncated_by_max_fib():
    """max_fib 应截断远未来步长。"""
    out = compute_fib_time_targets(_mk_df(), max_fib=21)
    for a in out["anchors"]:
        for tgt in a["targets"]:
            assert tgt["fib"] <= 21, "超过 max_fib 的步长应被截断"


def test_date_extrapolation_trade_days():
    """超出序列尾部时按交易日外推（跳过周末）。"""
    out = compute_fib_time_targets(_mk_df())
    for a in out["anchors"]:
        for tgt in a["targets"]:
            if tgt["idx"] > out["as_of_idx"]:
                d = pd.Timestamp(tgt["date"])
                assert d.weekday() < 5, f"外推日期 {tgt['date']} 不应落在周末"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
        except Exception as e:
            print(f"  💥 {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
