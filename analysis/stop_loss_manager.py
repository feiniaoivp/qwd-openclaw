#!/usr/bin/env python3
import os
import sys
import json
import shutil
import logging
from datetime import datetime

# 强制 sys.path 样板
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("StopLossManager")

class StopLossManager:
    """
    止损地图持久化管理类 (落地 10-21 铁律: 写-备份-验)
    防止 stop_loss_map.json 被静默清空或非法覆盖。
    """
    PATH = os.path.join(WORKSPACE, "data/stop_loss_map.json")
    BAK_PATH = os.path.join(WORKSPACE, "data/stop_loss_map.json.bak")

    @classmethod
    def load(cls) -> dict:
        """加载止损地图，缺失或损坏时尝试从备份恢复"""
        if not os.path.exists(cls.PATH):
            return {}
        try:
            with open(cls.PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log.error(f"读取止损地图失败: {e}")
            if os.path.exists(cls.BAK_PATH):
                log.warning(f"尝试从备份 {cls.BAK_PATH} 恢复...")
                try:
                    with open(cls.BAK_PATH, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except Exception:
                    pass
            return {}

    @classmethod
    def save(cls, new_map: dict) -> bool:
        """安全保存止损地图 (防止静默清空 + 强制备份 + 写入验证)"""
        if not isinstance(new_map, dict):
            log.error(f"非法写入格式: 期望 dict, 得到 {type(new_map)}")
            return False
        
        old_map = cls.load()
        
        # 1. 完整性拦截: 禁止用空字典覆盖非空地图 (防止静默清空)
        if not new_map and old_map:
            log.error(f"🛑 拦截非法空覆盖: 旧地图 {len(old_map)} 条 -> 新地图 0 条")
            return False
        
        # 2. 规模拦截: 若新旧地图规模差异 > 50% 且均不为空，触发警告
        if old_map and new_map:
            diff = abs(len(new_map) - len(old_map)) / len(old_map)
            if diff > 0.5:
                log.warning(f"⚠️ 止损地图规模剧变: {len(old_map)} -> {len(new_map)} ({diff:.1%})")

        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(cls.PATH), exist_ok=True)

            # 备份旧文件
            if os.path.exists(cls.PATH):
                shutil.copy2(cls.PATH, cls.BAK_PATH)
            
            # 写入新文件
            with open(cls.PATH, 'w', encoding='utf-8') as f:
                json.dump(new_map, f, indent=2, ensure_ascii=False)
            
            # 强制同步一份备份，防止 cold-start 第一次写入损坏且无备份可回
            shutil.copy2(cls.PATH, cls.BAK_PATH)
            
            # 3. 写入校验 (10-21 铁律: 独立读取验证，禁止在写表达式里 open('w'))
            with open(cls.PATH, 'r', encoding='utf-8') as f:
                verified = json.load(f)
                if verified != new_map:
                    raise ValueError("写入验证失败: 读取内容与写入内容不一致")
            
            log.info(f"止损地图已安全保存: {len(new_map)} 条")
            return True
        except Exception as e:
            log.error(f"保存止损地图崩溃: {e}")
            if os.path.exists(cls.BAK_PATH):
                log.warning("尝试还原备份...")
                shutil.copy2(cls.BAK_PATH, cls.PATH)
            return False

def _selftest() -> int:
    """对抗自测"""
    import pandas as pd
    # 使用临时路径避免污染生产
    original_path = StopLossManager.PATH
    original_bak = StopLossManager.BAK_PATH
    StopLossManager.PATH = "data/test_sl.json"
    StopLossManager.BAK_PATH = "data/test_sl.json.bak"
    
    try:
        # 1) 正常写入
        m1 = {"600030": 25.5, "000001": 10.2}
        assert StopLossManager.save(m1) == True
        assert StopLossManager.load() == m1
        
        # 2) 拦截空覆盖
        m2 = {}
        assert StopLossManager.save(m2) == False
        assert StopLossManager.load() == m1
        
        # 3) 损坏恢复
        with open(StopLossManager.PATH, "w") as f: f.write("CORRUPT")
        assert StopLossManager.load() == m1
        
        print("StopLossManager 自测通过!")
        return 0
    finally:
        StopLossManager.PATH = original_path
        StopLossManager.BAK_PATH = original_bak
        if os.path.exists("data/test_sl.json"): os.remove("data/test_sl.json")
        if os.path.exists("data/test_sl.json.bak"): os.remove("data/test_sl.json.bak")

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("StopLossManager 模块加载成功")
