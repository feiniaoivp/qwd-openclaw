"""
磁盘缓存模块
=============
基于文件的简单缓存，支持 TTL 过期。
"""

import os
import json
import pickle
import time
import hashlib
import logging
from typing import Any, Optional
from pathlib import Path

log = logging.getLogger(__name__)

class DiskCache:
    """简单的磁盘缓存"""
    
    def __init__(self, cache_dir: str = None, default_ttl: int = 3600):
        if cache_dir is None:
            cache_dir = os.path.join(os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace"), "data_cache")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl
    
    def _key_to_path(self, key: str) -> Path:
        """key -> 文件路径 (用 hash 避免文件名过长/特殊字符)"""
        h = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self.cache_dir / f"{h}.cache"
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取缓存，过期返回 default"""
        path = self._key_to_path(key)
        if not path.exists():
            return default
        
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
            
            # 检查过期
            if time.time() > data.get("expire_at", 0):
                path.unlink(missing_ok=True)
                return default
            
            return data.get("value", default)
        except Exception as e:
            log.warning(f"[Cache] 读取失败 {key}: {e}")
            return default
    
    def set(self, key: str, value: Any, ttl: int = None) -> None:
        """设置缓存"""
        if ttl is None:
            ttl = self.default_ttl
        
        path = self._key_to_path(key)
        try:
            data = {
                "value": value,
                "expire_at": time.time() + ttl,
                "created_at": time.time(),
            }
            with open(path, "wb") as f:
                pickle.dump(data, f)
        except Exception as e:
            log.warning(f"[Cache] 写入失败 {key}: {e}")
    
    def delete(self, key: str) -> None:
        """删除缓存"""
        path = self._key_to_path(key)
        path.unlink(missing_ok=True)
    
    def clear_expired(self) -> int:
        """清理过期缓存，返回清理数量"""
        count = 0
        now = time.time()
        for path in self.cache_dir.glob("*.cache"):
            try:
                with open(path, "rb") as f:
                    data = pickle.load(f)
                if now > data.get("expire_at", 0):
                    path.unlink()
                    count += 1
            except Exception:
                path.unlink(missing_ok=True)
                count += 1
        return count


# 全局单例
_instance: Optional[DiskCache] = None

def get_cache(cache_dir: str = None, default_ttl: int = 3600) -> DiskCache:
    global _instance
    if _instance is None:
        _instance = DiskCache(cache_dir, default_ttl)
    return _instance