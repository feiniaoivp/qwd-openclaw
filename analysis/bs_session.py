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
from analysis.net_guard import install_default_timeout, network_timeout_guard

install_default_timeout()

# ── 函数体内超时护栏常量（err.dist-io-no-timeout 铁律①：入口装 net_guard ≠ 全链路免疫）──
_LOGIN_TIMEOUT = 15.0   # 登录握手（内部会新建 socket，guard 保证其继承超时）；10-02 事故失败登录要 ~60s，15s 硬顶可缩短负缓存周期
_QUERY_TIMEOUT = 30.0   # 单次查询请求响应（新 socket 路径的兜底；常驻连接另有 _harden_socket 钉住）
_SOCKET_TIMEOUT = float(_os.getenv("NET_TIMEOUT_SEC", "20"))  # 常驻连接 recv 上限，与 net_guard 默认一致


def _harden_socket() -> None:
    """
    给 baostock 常驻连接（baostock.common.context.default_socket）显式钉住超时。

    为什么必须（09-30 提出、10-19 随 err.dist-io-no-timeout 提升的遗留缺口）：
      socket.setdefaulttimeout() 只影响**之后新建**的 socket；baostock 的
      SocketUtil.connect() 不调 settimeout，常驻连接的超时在创建那一刻被冻结。
      全部查询 I/O 都走这一条连接（sock.send_msg → context.default_socket），
      若它建立早于护栏安装（外部直接 bs.login() 的旧路径 / 外部库把 default
      改回 None 后 TTL 重登），recv 会永久挂起（09-16 / 09-29 事故形态）。
      本函数在登录成功后与每次查询前自愈，且**不放宽**已有更严的超时。
    """
    try:
        import baostock.common.context as _bs_ctx
        sock_obj = getattr(_bs_ctx, "default_socket", None)
        if sock_obj is None:
            return
        cur = sock_obj.gettimeout()
        if cur is None or cur > _SOCKET_TIMEOUT:
            sock_obj.settimeout(_SOCKET_TIMEOUT)
            if cur is None:
                log.warning(f"[BS] 常驻连接原超时=None（挂死风险），已修正为 {_SOCKET_TIMEOUT}s")
            else:
                log.info(f"[BS] 常驻连接超时 {cur}s → {_SOCKET_TIMEOUT}s")
    except Exception as e:
        log.warning(f"[BS] 常驻连接加固失败（不阻断）: {e}")

log = logging.getLogger(__name__)

_bs_lock = threading.RLock()  # 2026-11-04 T2-3: Lock→RLock，支持查询全程持锁（内嵌 ensure_login 不死锁）
_bs_logged_in = False
_last_login_time = 0.0
_LAST_LOGIN_FAIL_TIME = 0.0
_LOGIN_FAIL_COOLDOWN = 300  # 登录失败后 5 分钟内不再重试（2026-10-02：baostock 服务端不可用时每次失败登录要 ~60s，无负缓存会把整个回测拖死）
_LOGIN_TTL = 1800  # 30 分钟重新登录一次

# 会话死亡错误关键词（2026-10-27 故障定性）：
# 服务端会话被踢/连接死亡后，服务端返回「用户未登录」，而本地 _bs_logged_in
# 标志仍在 TTL 内为 True → ensure_login() 直接放行 → 重试永远空转。
# 05:24 事故：登录成功后第 13 只股起连续 24 只全报「用户未登录」×2 重试后放弃。
_SESSION_DEAD_KEYWORDS = ("用户未登录", "登录失效", "会话", "网络接收错误", "接收数据异常")


def _is_session_dead_error(error_msg: str) -> bool:
    """判断错误信息是否指示服务端会话已死亡（需 force_relogin 而非信任本地标志）"""
    if not error_msg:
        return False
    return any(kw in error_msg for kw in _SESSION_DEAD_KEYWORDS)


def _do_login() -> bool:
    """内部登录函数"""
    global _bs_logged_in, _last_login_time, _LAST_LOGIN_FAIL_TIME
    try:
        # 函数体内超时护栏（铁律①）：login 内部新建的 socket 在 guard 生效期间创建，
        # 必然继承超时，不依赖进程级默认值的安装时序
        with network_timeout_guard(_LOGIN_TIMEOUT):
            lg = bs.login()
        if lg.error_code != "0":
            log.error(f"[BS] 登录失败: {lg.error_msg}")
            _LAST_LOGIN_FAIL_TIME = time.time()
            return False
        _bs_logged_in = True
        _last_login_time = time.time()
        _LAST_LOGIN_FAIL_TIME = 0.0
        _harden_socket()  # 登录成功即钉住常驻连接超时（防旧连接先于护栏建立的挂死形态）
        log.info("[BS] 登录成功")
        return True
    except Exception as e:
        log.error(f"[BS] 登录异常: {e}")
        _LAST_LOGIN_FAIL_TIME = time.time()
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
        # 负缓存：失败冷却期内直接返回，不重复发起 ~60s 的失败登录
        if time.time() - _LAST_LOGIN_FAIL_TIME < _LOGIN_FAIL_COOLDOWN:
            return False
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


def force_relogin() -> bool:
    """强制重登（2026-11-05 新增）：TTL 内连接半死时 ensure_login 会误报已登录，
    批量拉取方遇连续失败应调此函数重建会话（logout + 重新 login，忽略负缓存冷却）。"""
    global _last_login_time, _LAST_LOGIN_FAIL_TIME
    with _bs_lock:
        try:
            if _bs_logged_in:
                try:
                    bs.logout()
                except Exception:
                    pass
                _bs_logged_in = False
        finally:
            pass
    _LAST_LOGIN_FAIL_TIME = 0.0   # 清负缓存，允许立即重试登录
    ok = _do_login()
    if ok:
        _last_login_time = time.time()
    return ok


def query_history_k_data_plus(*args, **kwargs):
    """
    代理 bs.query_history_k_data_plus，自动保证登录态。
    用法与原接口完全一致。
    如果登录失败返回 None。
    """
    if not ensure_login():
        log.error("[BS] 登录失败，无法执行查询")
        return None
    _harden_socket()  # 查询前自愈：防外部代码换入无超时的常驻连接
    # 函数体内超时护栏（铁律①）：兜住库内新建 socket 的路径；
    # 常驻连接本身的 recv 上限由 _harden_socket 钉住，与 guard 互补
    # T2-3 线程安全：bs 全局会话非线程安全，RLock 串行化底层查询
    # （并发扫描时新浪 HTTP 主源照常并行，仅 baostock 降级链串行）
    with _bs_lock:
        with network_timeout_guard(_QUERY_TIMEOUT):
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
                # 会话死亡自愈（2026-10-27 事故修复）：服务端踢线后本地登录标志是假的，
                # ensure_login() 会因 TTL 未到直接放行 → 重试永远复现同一错误。
                # 检测到会话类错误时强制重新登录，打破假登录态循环。
                if _is_session_dead_error(rs.error_msg):
                    log.warning("[BS] 检测到会话死亡错误，强制重新登录...")
                    force_relogin()
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