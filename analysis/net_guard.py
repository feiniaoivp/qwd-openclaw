#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网络超时护栏（全局兜底）
========================
背景（2026-09-16 事故）：
  `timing_vs_hold.py` 在拉取第 15 只股票时**永久阻塞在 `socket.recv`**
  （/usr/bin/sample 抓到线程栈：sock_recv → sock_call_ex），进程 CPU 98%
  但 7 分钟无任何产出。这类「网络读挂起」与 2026-08-28 记录的
  `daily_premarket_pipeline_timeout`（cron 1800s 超时被杀）同源。

根因：
  依赖库（baostock / akshare / pandas_datareader 等）内部的 socket 调用
  **多数不传 timeout**，默认是「永久等待」。个别 provider 显式传了
  timeout（如 sina_daily=15s、sina_realtime=12s），但覆盖不全，
  且无法约束第三方库内部新建的连接。

对策：
  `socket.setdefaulttimeout()` 设置**进程级默认超时**，对之后创建的所有
  socket（含第三方库内部）生效。这是最小改动、最大覆盖的兜底方案。

用法（在所有「入口脚本」顶部，import 第三方库之后立即调用）：
    from analysis.net_guard import install_default_timeout
    install_default_timeout()          # 默认 20s

设计原则：
  - 只做兜底，不替换各 provider 已有的精细 timeout（那些更贴合各自场景）。
  - 幂等：重复调用无副作用。
  - 可用环境变量 NET_TIMEOUT_SEC 覆盖（默认 20s）。
  - 显式暴露 `network_timeout_guard` 上下文管理器，供零散调用点临时收紧。
"""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager

DEFAULT_TIMEOUT = float(os.getenv("NET_TIMEOUT_SEC", "20"))
_installed = False


def install_default_timeout(timeout: float | None = None) -> float:
    """
    安装进程级 socket 默认超时（幂等）。

    参数:
        timeout: 秒；None 时取环境变量 NET_TIMEOUT_SEC，再回退 DEFAULT_TIMEOUT。

    返回:
        实际生效的超时秒数。
    """
    global _installed
    if timeout is None:
        timeout = float(os.getenv("NET_TIMEOUT_SEC", str(DEFAULT_TIMEOUT)))
    # 已有更严格的设置则不放大（避免覆盖调用方更严的诉求）
    current = socket.getdefaulttimeout()
    if current is not None and current <= timeout:
        _installed = True
        return current
    socket.setdefaulttimeout(timeout)
    _installed = True
    return timeout


@contextmanager
def network_timeout_guard(timeout: float):
    """
    临时收紧 socket 默认超时（退出后恢复原值）。

    用法:
        with network_timeout_guard(8):
            risky_call()
    """
    prev = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        yield
    finally:
        socket.setdefaulttimeout(prev)


def is_installed() -> bool:
    return _installed or socket.getdefaulttimeout() is not None


if __name__ == "__main__":
    t = install_default_timeout()
    print(f"✅ socket 默认超时 = {t}s (installed={is_installed()})")
