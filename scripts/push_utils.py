#!/usr/bin/env python3
"""
推送工具库：幂等键、指数退避重试、熔断器、心跳写入
供 power_overseas_push.py / fetch_commodity_fx.py 等脚本复用
"""

import hashlib
import json
import os
import time
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable, Any
from functools import wraps

# 配置
HEARTBEAT_DIR = Path("data/heartbeats")
HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)

IDEMPOTENCY_DIR = Path("data/idempotency")
IDEMPOTENCY_DIR.mkdir(parents=True, exist_ok=True)

CIRCUIT_BREAKER_FILE = IDEMPOTENCY_DIR / "circuit_breaker.json"

# Telegram 配置 (从环境变量读取)
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "626141741")
TG_API_URL = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage" if TG_BOT_TOKEN else ""

# 熔断器状态
def load_circuit_breaker() -> dict:
    if CIRCUIT_BREAKER_FILE.exists():
        try:
            return json.loads(CIRCUIT_BREAKER_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"failures": 0, "last_failure": 0, "open_until": 0}

def save_circuit_breaker(state: dict):
    CIRCUIT_BREAKER_FILE.write_text(json.dumps(state, ensure_ascii=False))

def check_circuit_breaker() -> bool:
    """返回 True 表示熔断器开启（拒绝请求）"""
    state = load_circuit_breaker()
    now = time.time()
    if state.get("open_until", 0) > now:
        return True
    return False

def record_failure():
    state = load_circuit_breaker()
    state["failures"] = state.get("failures", 0) + 1
    state["last_failure"] = time.time()
    if state["failures"] >= 3:
        state["open_until"] = time.time() + 300  # 熔断 5 分钟
        print(f"[CIRCUIT] Breaker OPEN for 5 min (failures={state['failures']})")
    save_circuit_breaker(state)

def record_success():
    state = load_circuit_breaker()
    if state.get("failures", 0) > 0:
        state["failures"] = 0
        state["open_until"] = 0
        save_circuit_breaker(state)

def idempotency_key(mode: str, date: str, payload: dict) -> str:
    """生成幂等键：mode + date + payload_hash"""
    payload_str = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(payload_str.encode()).hexdigest()[:16]
    return f"push:{mode}:{date}:{h}"

def check_and_set_idempotent(key: str, ttl_hours: int = 24) -> bool:
    """检查并设置幂等键。返回 True 表示已存在（跳过），False 表示新写入（可执行）"""
    key_file = IDEMPOTENCY_DIR / f"{key}.json"
    now = time.time()
    if key_file.exists():
        try:
            data = json.loads(key_file.read_text(encoding="utf-8"))
            if now - data.get("ts", 0) < ttl_hours * 3600:
                return True  # 已存在且未过期
        except Exception:
            pass
    # 写入新键
    key_file.write_text(json.dumps({"ts": now, "key": key}, ensure_ascii=False))
    return False

def send_telegram(text: str, parse_mode: str = "", disable_web_page_preview: bool = True) -> bool:
    """发送 TG 消息，带指数退避重试"""
    if not TG_BOT_TOKEN:
        print("[TG] No bot token configured, skipping send")
        return False
    
    if check_circuit_breaker():
        print("[TG] Circuit breaker OPEN, skipping send")
        return False
    
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    
    max_retries = 3
    base_delay = 1.0
    
    for attempt in range(max_retries):
        try:
            resp = requests.post(TG_API_URL, json=payload, timeout=10)
            if resp.status_code == 200:
                record_success()
                return True
            elif resp.status_code == 429:
                # Rate limited - 读取 Retry-After
                retry_after = int(resp.headers.get("Retry-After", base_delay * (2 ** attempt)))
                print(f"[TG] Rate limited, waiting {retry_after}s (attempt {attempt+1}/{max_retries})")
                time.sleep(retry_after)
            else:
                print(f"[TG] HTTP {resp.status_code}: {resp.text[:200]}")
                if attempt < max_retries - 1:
                    time.sleep(base_delay * (2 ** attempt))
        except requests.RequestException as e:
            print(f"[TG] Network error: {e}")
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
    
    record_failure()
    return False

def write_heartbeat(job_name: str, metadata: dict = None):
    """写入心跳文件"""
    hb_file = HEARTBEAT_DIR / f"{job_name}.json"
    hb_file.write_text(json.dumps({
        "job": job_name,
        "timestamp": datetime.now().isoformat(),
        "metadata": metadata or {}
    }, ensure_ascii=False))

def get_heartbeat_age(job_name: str) -> Optional[float]:
    """获取心跳文件年龄(秒)，不存在返回 None"""
    hb_file = HEARTBEAT_DIR / f"{job_name}.json"
    if not hb_file.exists():
        return None
    try:
        data = json.loads(hb_file.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(data["timestamp"]).timestamp()
        return time.time() - ts
    except Exception:
        return None

def with_retry_and_idempotent(mode: str, date: str, payload_builder: Callable[[], dict]):
    """
    装饰器：幂等检查 + 熔断器 + 重试 + 心跳
    payload_builder: 无参函数，返回要推送的 payload dict (含 text 字段)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 1. 熔断器检查
            if check_circuit_breaker():
                print(f"[SKIP] Circuit breaker open, {func.__name__} aborted")
                return False
            
            # 2. 构建 payload 用于幂等键
            payload = payload_builder()
            key = idempotency_key(mode, date, payload)
            
            # 3. 幂等检查
            if check_and_set_idempotent(key):
                print(f"[SKIP] Idempotent key exists: {key}")
                return True  # 视为成功（已推送过）
            
            # 4. 执行实际推送逻辑
            try:
                result = func(*args, **kwargs)
                if result:
                    write_heartbeat(f"push_{mode}", {"key": key, "date": date})
                return result
            except Exception as e:
                print(f"[ERROR] {func.__name__} failed: {e}")
                raise
        return wrapper
    return decorator

# 简单的重试装饰器（非幂等场景）
def retry(max_attempts: int = 3, base_delay: float = 1.0, exceptions: tuple = (Exception,)):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts - 1:
                        raise
                    delay = base_delay * (2 ** attempt)
                    print(f"[RETRY] {func.__name__} attempt {attempt+1} failed: {e}, waiting {delay}s")
                    time.sleep(delay)
        return wrapper
    return decorator

if __name__ == "__main__":
    # 自测
    print("=== push_utils 自测 ===")
    
    # 测试幂等键
    k1 = idempotency_key("eod", "2026-09-13", {"text": "test"})
    k2 = idempotency_key("eod", "2026-09-13", {"text": "test"})
    k3 = idempotency_key("eod", "2026-09-13", {"text": "different"})
    assert k1 == k2, "同一 payload 应生成同一键"
    assert k1 != k3, "不同 payload 应生成不同键"
    print(f"幂等键测试通过: {k1}")
    
    # 测试幂等检查
    assert check_and_set_idempotent("test_key_1") == False
    assert check_and_set_idempotent("test_key_1") == True
    print("幂等检查测试通过")
    
    # 测试熔断器
    state = load_circuit_breaker()
    state["failures"] = 0
    save_circuit_breaker(state)
    assert check_circuit_breaker() == False
    for _ in range(3):
        record_failure()
    assert check_circuit_breaker() == True
    print("熔断器测试通过")
    
    # 恢复
    record_success()
    assert check_circuit_breaker() == False
    
    # 测试心跳
    write_heartbeat("test_job", {"test": True})
    age = get_heartbeat_age("test_job")
    assert age is not None and age < 2
    print(f"心跳测试通过: age={age:.1f}s")
    
    print("\n✅ 所有自测通过")