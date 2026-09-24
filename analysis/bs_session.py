#!/usr/bin/env python3
"""
Baostock 单例会话管理器
=======================
全进程复用同一个 baostock 登录会话，避免连接池耗尽 ([Errno 9] Bad file descriptor)。
所有需要 baostock 的脚本统一 import 此模块。
"""


# ── sys.path 样板（TOOLS.md 强制；2026-09-19）──
# 脚本模式下 sys.path[0] 是脚本所在目录而非工作区根，
# 必须显式插入工作区根（供 `from analysis.xxx`）+ analysis/（供子包引用）。
import os as _os, sys as _sys
_WORKSPACE = _os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (_WORKSPACE, _os.path.join(_WORKSPACE, "analysis")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import atexit
import baostock as bs
import threading
import time
import logging

# 网络兜底：baostock 内部 socket 多数不传 timeout，默认永久等待
# （2026-09-16 事故：拉取时阻塞在 sock_recv 导致进程永久挂起）
from analysis.net_guard import install_default_timeout

install_default_timeout()

log = logging.getLogger(__name__)

_bs_lock = threading.Lock()
_bs_logged_in = False
_last_login_time = 0.0
_LOGIN_TTL = 1800  # 30 分钟重新登录一次


def _do_login() -> bool:
    """内部登录函数"""
    global _bs_logged_in, _last_login_time
    try:
        lg = bs.login()
        if lg.error_code != "0":
            log.error(f"[BS] 登录失败: {lg.error_msg}")
            return False
        _bs_logged_in = True
        _last_login_time = time.time()
        log.info("[BS] 登录成功")
        return True
    except Exception as e:
        log.error(f"[BS] 登录异常: {e}")
        return False


def ensure_login() -> bool:
    """
    确保已登录（线程安全）。
    - 首次调用时登录
    - 超过 TTL 自动重新登录
    - 并发调用只登录一次
    """
    global _bs_logged_in, _last_login_time
    with _bs_lock:
        if _bs_logged_in and (time.time() - _last_login_time < _LOGIN_TTL):
            return True
        return _do_login()


def logout() -> None:
    """显式登出（进程退出时由 atexit 自动调用）"""
    global _bs_logged_in
    with _bs_lock:
        if _bs_logged_in:
            try:
                bs.logout()
                log.info("[BS] 已登出")
            except Exception:
                pass
            _bs_logged_in = False


def query_history_k_data_plus(*args, **kwargs):
    """
    代理 bs.query_history_k_data_plus，自动保证登录态。
    用法与原接口完全一致。
    如果登录失败返回 None。
    """
    if not ensure_login():
        log.error("[BS] 登录失败，无法执行查询")
        return None
    return bs.query_history_k_data_plus(*args, **kwargs)


def query_history_k_data_plus_retry(*args, max_retries: int = 3, wait_seconds: float = 2.0, **kwargs):
    """
    带重试的查询，自动处理网络抖动。
    返回结果对象或 None（失败时）。
    """
    import socket
    for attempt in range(max_retries):
        # 确保登录
        if not ensure_login():
            log.warning(f"[BS] 登录失败，重试 {attempt+1}/{max_retries}")
            time.sleep(wait_seconds)
            continue
        
        try:
            rs = query_history_k_data_plus(*args, **kwargs)
            if rs is not None and rs.error_code == "0":
                return rs
            elif rs is not None:
                log.warning(f"[BS] 查询错误: {rs.error_msg}, 重试 {attempt+1}/{max_retries}")
            else:
                log.warning(f"[BS] 查询返回 None, 重试 {attempt+1}/{max_retries}")
        except (socket.timeout, TimeoutError, OSError) as e:
            log.warning(f"[BS] 网络异常: {e}, 重试 {attempt+1}/{max_retries}")
        except Exception as e:
            log.warning(f"[BS] 查询异常: {e}, 重试 {attempt+1}/{max_retries}")
        time.sleep(wait_seconds)
    return None


# 进程退出时自动登出
atexit.register(logout)

# 导出常用符号
__all__ = [
    "ensure_login",
    "logout",
    "query_history_k_data_plus",
    "query_history_k_data_plus_retry",
    "get_session_status",
    "force_relogin",
    "SessionContext",
]


# ══════════════════════════════════════
# 会话上下文管理器（供批量查询显式复用会话）
# ══════════════════════════════════════

from contextlib import contextmanager

class SessionContext:
    """
    会话级上下文管理器：
    - 进入时确保登录（如需则登录）
    - 退出时**不**登出（保持会话复用）
    - 仅在显式调用 logout() 或进程退出时才登出
    """
    def __init__(self, auto_login: bool = True):
        self.auto_login = auto_login
        self._logged_in_at_entry = False
    
    def __enter__(self):
        if self.auto_login:
            with _bs_lock:
                self._logged_in_at_entry = _bs_logged_in
                if not _bs_logged_in:
                    _do_login()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # 故意不登出，保持会话复用
        pass


@contextmanager
def session_scope(auto_login: bool = True):
    """便捷函数：with session_scope(): ..."""
    ctx = SessionContext(auto_login=auto_login)
    try:
        yield ctx
    finally:
        pass  # 不登出


def get_session_status() -> dict:
    """返回当前会话状态（供健康检查/监控）"""
    with _bs_lock:
        return {
            "logged_in": _bs_logged_in,
            "last_login_time": _last_login_time,
            "ttl_remaining_sec": max(0, _LOGIN_TTL - (time.time() - _last_login_time)) if _bs_logged_in else 0,
        }


def force_relogin() -> bool:
    """强制重新登录（用于连接异常后的恢复）"""
    with _bs_lock:
        global _bs_logged_in
        _bs_logged_in = False
        return _do_login()