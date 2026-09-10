#!/usr/bin/env python3
"""
Baostock 单例会话管理器
=======================
全进程复用同一个 baostock 登录会话，避免连接池耗尽 ([Errno 9] Bad file descriptor)。
所有需要 baostock 的脚本统一 import 此模块。
"""

import atexit
import baostock as bs
import threading
import time
import logging

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
]