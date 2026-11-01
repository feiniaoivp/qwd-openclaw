#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股涨跌停可成交性规则层（2026-11-01 P0 落地，源自构建计划借鉴对照）
====================================================================

背景（为什么必须有）：
    backtest_strategies.run_simulation 此前在信号日收盘价**无条件成交**，
    从不检查该价格是否可成交。涨停封板买不进、跌停封板卖不出，是 A 股回测
    虚高的头号来源 —— 对「EMA金叉在强势股碾压」类结论威胁最大（强势股恰好
    是涨停聚集地）。本模块把可成交性规则做成独立可测的层，由 run_simulation
    按需启用（默认关闭 = 存量口径，历史结果可比）。

口径规则：
    - limit_ratio(symbol): 688/300/301 开头 → 20%（科创板/创业板），
      其余（60/00/002/900 等）→ 10%。北交所 30% 当前关注池无此类代码。
    - 封板判定（日线 bar 的实用近似）：
      当日价格相对昨收的涨跌幅达到 ±(ratio - tol) 视为封板。
      tol 按价格自适应（吸收前复权舍入误差）：t = max(0.0015, 0.006/prev_close)。
      依据：真实涨停价 = round(昨收×(1+ratio), 2)，在**前复权比例空间**里
      涨跌停带保真（两日同乘复权因子），仅剩舍入偏差 ≈ 半个 tick / 价格。
    - one_word_board: high≈low（相对价差<0.001）= 一字板，全天不可成交，
      供研究统计用（成交规则仍按收盘/开盘封板口径）。

成交口径（fill_mode，由 run_simulation 消费）：
    - "signal_close"  信号日收盘成交（存量口径，默认）
    - "next_open"     信号次日开盘成交（与实盘「信号次日开盘执行」纪律一致）

enforce_limit=True 时的可成交性动作：
    - 买入遇封板涨停 → 拒绝成交（机会作废，计 rejected_buys）
    - 卖出遇封板跌停 → 卖单顺延到下一可成交日（计 blocked_sells）

T+1 说明：
    - next_open 口径天然满足（买入成交次一交易日开盘后才可能有卖出成交）；
    - signal_close 口径由日线 bar 结构天然满足（策略函数每日至多产出一个
      动作，当日买入当日卖出无法发生；卖出后当日再买入 = A 股合法）。

自测：python3 analysis/fill_rules.py   （退出码 0=过 / 1=违规）
"""

import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# 创业板/科创板 20% 涨跌幅前缀（用户关注池内出现的类别）
_GROWTH_PREFIXES = ("688", "300", "301")


def limit_ratio(symbol) -> float:
    """按代码前缀返回涨跌停幅度。symbol 形如 '600584' / 'sh.600584' 均可。"""
    code = str(symbol).split(".")[-1]
    if code.startswith(_GROWTH_PREFIXES):
        return 0.20
    return 0.10


def _seal_tol(prev_close: float) -> float:
    """封板判定的容差：吸收前复权舍入。

    舍入噪声上界 ≈ 两个价格各半个最小变动价位(0.01 元)，合计 1 个 tick；
    相对值 = 0.01/prev_close，另设 0.0015 下限防高价股容差过窄。"""
    if prev_close is None or prev_close <= 0:
        return 0.0015
    return max(0.0015, 0.01 / prev_close)


def is_sealed(prev_close, price, ratio, direction="up", tol=None) -> bool:
    """判断 price（当日收盘或次日开盘）相对 prev_close 是否触及涨/跌停封板区。

    direction: "up" | "down"
    缺数据（prev_close/price 为 None 或 <=0、ratio<=0）一律返回 False（不拦截），
    由调用方决定是否对缺数据场景显式告警 —— 本层不做静默兜底决策。
    """
    if prev_close is None or price is None:
        return False
    if prev_close <= 0 or price <= 0 or ratio is None or ratio <= 0:
        return False
    t = tol if tol is not None else _seal_tol(prev_close)
    chg = price / prev_close - 1.0
    # +1e-9：吸收浮点误差（如 2.19/2.0-1 = 0.09499...92 < 0.095 的边界）
    if direction == "up":
        return chg + 1e-9 >= (ratio - t)
    if direction == "down":
        return chg - 1e-9 <= -(ratio - t)
    raise ValueError(f"未知 direction: {direction!r}")


def one_word_board(high, low) -> bool:
    """一字板判定：high≈low（相对价差 < 0.1%）。缺数据返回 False。"""
    if high is None or low is None or low <= 0:
        return False
    return (high - low) / low < 0.001


# ════════════════════════════════════════
# 自测（对抗用例，退出码 0=过 / 1=违规）
# ════════════════════════════════════════

def _selftest() -> int:
    failures = []

    # 1) 涨跌停幅度分档（用名称索引真实关注池代码，避免硬编码清单漂移）
    from analysis.backtest_strategies import STOCKS, WATCHLIST
    by_name = {name: sym for sym, name in STOCKS}
    checks = [
        ("中芯国际", 0.20),   # 科创板
        ("国瓷材料", 0.20),   # 创业板
        ("长电科技", 0.10),   # 沪市主板
        ("通富微电", 0.10),   # 深市主板
    ]
    for nm, want in checks:
        sym = by_name.get(nm)
        if sym is None:
            failures.append(f"关注池缺 {nm}")
            continue
        got = limit_ratio(sym)
        if abs(got - want) > 1e-9:
            failures.append(f"limit_ratio({nm}={sym}) = {got}, 期望 {want}")

    # 2) 封板判定 —— 沪市主板 10%
    cases = [
        # (prev, price, direction, expect, 说明)
        (10.00, 11.00, "up", True,  "恰好涨停"),
        (10.00, 11.02, "up", True,  "越过涨停(复权舍入)"),
        (10.00, 10.95, "up", False, "9.5% 未封板"),
        (10.00, 10.98, "up", False, "9.8% 接近但未到容忍下界(0.0015容差)"),
        (10.00, 10.99, "up", True,  "9.9% 达到容忍下界视为封板"),
        (10.00,  9.00, "down", True,  "恰好跌停"),
        (10.00,  8.98, "down", True,  "越过跌停"),
        (10.00,  9.05, "down", False, "-9.5% 未封板"),
        # 2 元低价股：1 tick 容差 = 0.005，涨停价舍入后 2.19/2.0=9.5% 应判封板
        (2.00, 2.19,  "up", True,  "低价股舍入涨停(9.5%)"),
        (2.00, 2.15,  "up", False, "低价股 7.5% 未封板"),
    ]
    for prev, price, d, expect, note in cases:
        got = is_sealed(prev, price, 0.10, direction=d)
        if got != expect:
            failures.append(f"is_sealed({prev},{price},{d}) [{note}] = {got}, 期望 {expect}")

    # 3) 创业板 20% 档
    if not is_sealed(20.00, 24.00, 0.20, direction="up"):
        failures.append("创业板 20% 涨停未判封板")
    if is_sealed(20.00, 22.00, 0.20, direction="up"):
        failures.append("创业板 10% 误判封板")

    # 4) 缺数据 fail-open（不拦截）+ 非法 direction 必须抛错
    if is_sealed(None, 11.0, 0.10):
        failures.append("prev_close=None 应返回 False")
    if is_sealed(10.0, None, 0.10):
        failures.append("price=None 应返回 False")
    if is_sealed(10.0, 11.0, 0.0):
        failures.append("ratio=0 应返回 False")
    try:
        is_sealed(10.0, 11.0, 0.10, direction="sideways")
        failures.append("非法 direction 未抛错")
    except ValueError:
        pass

    # 5) 一字板
    if not one_word_board(11.00, 11.00):
        failures.append("high==low 应判一字板")
    if one_word_board(11.00, 10.50):
        failures.append("正常振幅误判一字板")

    # 6) 20% 档代码不应混入 10% 判定（对抗：limit_ratio 输入带市场前缀）
    if abs(limit_ratio("sh.688981") - 0.20) > 1e-9:
        failures.append("带前缀代码 688 档位判定失败")

    if failures:
        print("❌ fill_rules 自测失败:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"✅ fill_rules 自测通过（{len(checks)} 档位 + {len(cases)} 封板用例 + 边界 8 项）")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
