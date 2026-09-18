#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
executor_bridge - 生产入口桥接
==============================

把「生产脚本 (portfolio_sim / cron)」接到「统一执行器 PortfolioExecutor」上，
同时保证信号/风控/仓位逻辑只有一份实现（portfolio_core.run_portfolio_scan）。

为什么需要这层：
- portfolio_executor.PortfolioExecutor 是执行层的统一门面（sim/live/backtest）
- 但生产脚本的契约是 dict 输入输出，且要传自定义 fetch_data_func
- 这里做一次 adapter，避免生产脚本直接 import 执行层内部细节

设计原则（重要）：
    持仓/现金的权威账本只有一份 = portfolio_core 的 state 文件。
    ExecutionPort 只负责「撮合/报单」,不得自行维护第二套账本。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from analysis.execution_port import SimulationPort
from analysis.portfolio_executor import PortfolioExecutor, ScanResult


def run_portfolio_scan_via_executor(
    stocks: List[Tuple[str, str]],
    fetch_data_func: Callable,
    today_str: str = None,
    state_file: str = None,
) -> Dict:
    """生产入口：走 PortfolioExecutor（模拟盘模式），返回旧格式 dict。

    与 portfolio_core.run_portfolio_scan 的返回结构完全兼容，
    因此调用方（portfolio_sim / cron）无需改动输出解析逻辑。
    """
    port = SimulationPort(initial_capital=100_000)
    executor = PortfolioExecutor(port, stocks=stocks, state_file=state_file)
    result: ScanResult = executor.run_daily_scan(
        today_str=today_str,
        fetch_data_func=fetch_data_func,
    )
    return {
        "date": result.date,
        "stock_count": result.stock_count,
        "portfolio": result.portfolio,
        "errors": result.errors,
        "details": result.details,
        "messages": result.messages,
        "state": result.state,
    }
