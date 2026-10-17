#!/usr/bin/env python3
"""五维评分 None 契约门禁（2026-10-17 新增，三层防线之机器层）。

背景: 10-08 立项「隐性 5.0 填充」改造（98820ca 缺维 None 化 + 3977840 维内填充清零）。
本测试用合成输入锁定行为契约，防止未来改码时回潮：
  1. 数据缺失的维度必须返回 None（禁止 or 0 / or 50 / 5.0 托底）
  2. 总分 = 有效维度均值 ×5，输出携带 valid_dims / dims_available
  3. 全维缺失 → total_score=None + advice「无法评估」
  4. 推送表缺失维渲染为「—」且强制展示口径
  5. RSI 口径（2026-10-17）: <30 不加分，>70 罚 2

纯逻辑 + mock，零网络，秒级。退出码: 0=契约完好, 1=被破坏。
"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import io
import contextlib
from unittest import mock

import analysis.trader_stock_picks as tsp

FAILS = []


def check(name, cond, got=""):
    if cond:
        print(f"  PASS {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name}  got={got!r}")


def t_chip():
    check("chip 无源恒 None", tsp.score_chip_structure("000001") is None)


def t_volume_price():
    check("vp 空 spot → None", tsp.score_volume_price("X", "X", {}) is None)
    check("vp 无价格 → None", tsp.score_volume_price("X", "X", {"change_pct": 3.0}) is None)
    v = tsp.score_volume_price("X", "X", {"price": 10.0, "change_pct": 1.0})
    check("vp 有真实行情 → 数值", isinstance(v, (int, float)) and 0 <= v <= 10, v)


def t_fund_flow():
    with mock.patch.object(tsp, "get_fund_flow", return_value={}):
        check("fund_flow 无数据 → None", tsp.score_fund_flow("X", "X") is None)
    with mock.patch.object(tsp, "get_fund_flow", return_value={"main_net_in": None}):
        check("fund_flow main_net_in 缺失 → None（禁 or 0）", tsp.score_fund_flow("X", "X") is None)
    with mock.patch.object(tsp, "get_fund_flow", return_value={"main_net_in": 4e7}):
        v = tsp.score_fund_flow("X", "X")
        check("fund_flow 正流入 → 高于中性（线性段）", isinstance(v, float) and v > 5, v)
    with mock.patch.object(tsp, "get_fund_flow", return_value={"main_net_in": -4e7}):
        v = tsp.score_fund_flow("X", "X")
        check("fund_flow 负流入 → 低于中性（线性段）", isinstance(v, float) and v < 5, v)


def t_fundamentals():
    base = {"pe": 20.0, "pb": 2.0, "roe": 10.0}
    for miss in ("pe", "pb", "roe"):
        d = dict(base)
        d[miss] = None
        with mock.patch.object(tsp, "get_fundamentals", return_value=d):
            check(f"fundamentals 缺 {miss} → None（禁 or 0）", tsp.score_fundamentals("X", "X") is None)
    with mock.patch.object(tsp, "get_fundamentals", return_value=base):
        v = tsp.score_fundamentals("X", "X")
        check("fundamentals 三要素齐 → 数值", isinstance(v, (int, float)) and 0 <= v <= 10, v)


def t_technical():
    check("tech 空 → None", tsp.score_technical("X", "X", tech={}) is None)
    check("tech 缺 rsi → None", tsp.score_technical("X", "X", tech={"dif": 1.0, "dea": 0.5}) is None)
    v = tsp.score_technical("X", "X", tech={"dif": 1.0, "dea": 0.5, "rsi": 25.0})
    check("RSI<30 不加分（口径 2026-10-17）", v == 7.0, v)
    v = tsp.score_technical("X", "X", tech={"dif": 1.0, "dea": 0.5, "rsi": 75.0})
    check("RSI>70 仍罚 2", v == 5.0, v)
    v = tsp.score_technical("X", "X", tech={"dif": 1.0, "dea": 0.5, "rsi": 50.0})
    check("RSI 中性 + 金叉", v == 7.0, v)


def _run_picks_with_scores(scores):
    """mock 五维打分与取数，跑单票聚合。scores: dict dim->value|None。"""
    def fake_vp(symbol, name, spot_item):
        return scores["volume_price"]

    with mock.patch.object(tsp, "get_spot_scan", return_value=([{"symbol": "TEST", "price": 10.0}], None)), \
         mock.patch.object(tsp, "get_technical_from_hist", return_value={}), \
         mock.patch.object(tsp, "score_chip_structure", return_value=scores["chip"]), \
         mock.patch.object(tsp, "score_volume_price", side_effect=fake_vp), \
         mock.patch.object(tsp, "score_fund_flow", return_value=scores["fund_flow"]), \
         mock.patch.object(tsp, "score_fundamentals", return_value=scores["fundamentals"]), \
         mock.patch.object(tsp, "score_technical", return_value=scores["technical"]):
        return tsp.get_trader_picks([("TEST", "测试票")])


def t_aggregation():
    picks = _run_picks_with_scores({"chip": None, "volume_price": 6.0, "fund_flow": 4.0,
                                    "fundamentals": 8.0, "technical": 7.0})
    p = picks[0]
    check("总分=有效维均值×5（4维=31.25）", abs(p["total_score"] - 31.25) < 1e-6, p["total_score"])
    check("valid_dims=4", p["valid_dims"] == 4, p["valid_dims"])
    check("dims_available 不含 chip", "chip" not in p["dims_available"], p["dims_available"])

    picks = _run_picks_with_scores({"chip": None, "volume_price": None, "fund_flow": None,
                                    "fundamentals": None, "technical": None})
    p = picks[0]
    check("全维缺失 → total None", p["total_score"] is None, p["total_score"])
    check("全维缺失 → advice 无法评估", "无法评估" in p["advice"], p["advice"])


def t_format():
    picks = _run_picks_with_scores({"chip": None, "volume_price": 6.0, "fund_flow": 4.0,
                                    "fundamentals": 8.0, "technical": 7.0})
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        table = tsp.format_picks_for_report(picks)
    check("推送表含维度覆盖行", "维度覆盖" in table)
    check("推送表含口径 4/5", "4/5" in table)
    check("缺失维渲染 —", "—" in table)


def main():
    print("== 五维评分 None 契约门禁 ==")
    t_chip()
    t_volume_price()
    t_fund_flow()
    t_fundamentals()
    t_technical()
    t_aggregation()
    t_format()
    total = 0
    print(f"== 结果: {'全部通过' if not FAILS else '契约被破坏'} ==")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
