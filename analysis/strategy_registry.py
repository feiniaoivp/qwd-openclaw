#!/usr/bin/env python3
"""
策略注册表 - 版本化管理 adaptive_strategy_map.json
=====================================================

功能：
- 统一加载/保存策略映射
- 自动版本归档 (data/strategy_map_versions/)
- 回滚到任意历史版本
- 列出所有版本历史
- 元数据记录 (git commit, 来源, 验证得分等)
"""

import os
import sys
import json
import shutil
import subprocess
from datetime import datetime
from typing import Dict, List, Optional, Any

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
MAP_FILE = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")
VERSIONS_DIR = os.path.join(WORKSPACE, "data", "strategy_map_versions")
os.makedirs(VERSIONS_DIR, exist_ok=True)


def get_git_commit() -> str:
    """获取当前 git commit hash (短)"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=WORKSPACE, capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def get_git_status() -> str:
    """获取 git status 简要信息"""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=WORKSPACE, capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip() or "clean"
    except Exception:
        pass
    return "unknown"


def load_current_map() -> Dict:
    """加载当前生效的策略映射"""
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_current_map(data: Dict) -> None:
    """保存当前生效的策略映射"""
    os.makedirs(os.path.dirname(MAP_FILE), exist_ok=True)
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_versions() -> List[Dict]:
    """列出所有历史版本"""
    versions = []
    for fname in sorted(os.listdir(VERSIONS_DIR)):
        if fname.endswith(".json"):
            fpath = os.path.join(VERSIONS_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    vdata = json.load(f)
                versions.append({
                    "file": fname,
                    "version": vdata.get("version"),
                    "timestamp": vdata.get("metadata", {}).get("timestamp"),
                    "source": vdata.get("metadata", {}).get("source"),
                    "git_commit": vdata.get("metadata", {}).get("git_commit"),
                    "total_stocks": len(vdata.get("strategies", {})),
                })
            except Exception:
                versions.append({"file": fname, "error": "读取失败"})
    return versions


def create_version(data: Dict, source: str = "manual", metadata: Dict = None) -> str:
    """创建新版本并归档"""
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
    version_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 构建完整版本数据
    version_data = {
        "version": version_id,
        "timestamp": timestamp,
        "metadata": {
            "timestamp": timestamp,
            "source": source,
            "git_commit": get_git_commit(),
            "git_status": get_git_status(),
            **(metadata or {}),
        },
        "strategies": data,
    }
    
    # 保存版本文件
    fname = f"strategy_map_v{version_id}.json"
    fpath = os.path.join(VERSIONS_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(version_data, f, ensure_ascii=False, indent=2)
    
    # 同时更新当前生效映射
    save_current_map(data)
    
    print(f"✅ 版本已创建: {fname}")
    print(f"   版本号: {version_id}")
    print(f"   来源: {source}")
    print(f"   股票数: {len(data)}")
    print(f"   Git: {version_data['metadata']['git_commit']} ({version_data['metadata']['git_status']})")
    
    return version_id


def load_version(version_id: str) -> Optional[Dict]:
    """加载指定版本"""
    fname = f"strategy_map_v{version_id}.json"
    fpath = os.path.join(VERSIONS_DIR, fname)
    if not os.path.exists(fpath):
        # 尝试按文件名精确匹配
        for fname in os.listdir(VERSIONS_DIR):
            if version_id in fname and fname.endswith(".json"):
                fpath = os.path.join(VERSIONS_DIR, fname)
                break
        else:
            return None
    
    with open(fpath, "r", encoding="utf-8") as f:
        return json.load(f)


def rollback_to_version(version_id: str) -> bool:
    """回滚到指定版本"""
    vdata = load_version(version_id)
    if vdata is None:
        print(f"❌ 版本不存在: {version_id}")
        return False
    
    strategies = vdata.get("strategies", {})
    create_version(strategies, source=f"rollback_from_{version_id}", 
                   metadata={"rollback_from": version_id})
    print(f"✅ 已回滚到版本: {version_id}")
    return True


def activate_version(version_id: str) -> bool:
    """激活指定版本为当前生效（不创建新版本）"""
    vdata = load_version(version_id)
    if vdata is None:
        print(f"❌ 版本不存在: {version_id}")
        return False
    
    strategies = vdata.get("strategies", {})
    save_current_map(strategies)
    print(f"✅ 已激活版本: {version_id} (共 {len(strategies)} 只股票)")
    return True


def show_version(version_id: str) -> None:
    """显示版本详情"""
    vdata = load_version(version_id)
    if vdata is None:
        print(f"❌ 版本不存在: {version_id}")
        return
    
    meta = vdata.get("metadata", {})
    strategies = vdata.get("strategies", {})
    
    print(f"\n📋 版本详情: {version_id}")
    print(f"   时间: {meta.get('timestamp')}")
    print(f"   来源: {meta.get('source')}")
    print(f"   Git: {meta.get('git_commit')} ({meta.get('git_status')})")
    print(f"   股票数: {len(strategies)}")
    
    # 统计策略分布
    dist = {}
    for sym, strat in strategies.items():
        s = strat if isinstance(strat, str) else strat.get("strategy", "unknown")
        dist[s] = dist.get(s, 0) + 1
    
    print(f"   策略分布:")
    for s, cnt in sorted(dist.items()):
        print(f"     {s}: {cnt}")


def diff_versions(v1: str, v2: str) -> None:
    """对比两个版本的差异"""
    d1 = load_version(v1)
    d2 = load_version(v2)
    if d1 is None or d2 is None:
        print("❌ 版本不存在")
        return
    
    s1 = d1.get("strategies", {})
    s2 = d2.get("strategies", {})
    
    all_syms = set(s1.keys()) | set(s2.keys())
    
    print(f"\n🔍 版本对比: {v1} vs {v2}")
    print(f"{'股票':<10} {'策略1':<15} {'策略2':<15} {'变化'}")
    print("-" * 55)
    
    changes = 0
    for sym in sorted(all_syms):
        strat1 = s1.get(sym, "—")
        strat2 = s2.get(sym, "—")
        if isinstance(strat1, dict): strat1 = strat1.get("strategy", "—")
        if isinstance(strat2, dict): strat2 = strat2.get("strategy", "—")
        
        if strat1 != strat2:
            arrow = "🔄" if strat1 != "—" and strat2 != "—" else ("➕" if strat1 == "—" else "➖")
            print(f"{sym:<10} {strat1:<15} {strat2:<15} {arrow}")
            changes += 1
    
    if changes == 0:
        print("  无差异")
    else:
        print(f"\n共 {changes} 处变更")


# ── CLI 入口 ──

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n用法:")
        print("  python -m analysis.strategy_registry list                    # 列出所有版本")
        print("  python -m analysis.strategy_registry show <version_id>       # 显示版本详情")
        print("  python -m analysis.strategy_registry activate <version_id>   # 激活指定版本")
        print("  python -m analysis.strategy_registry rollback <version_id>   # 回滚到指定版本")
        print("  python -m analysis.strategy_registry diff <v1> <v2>          # 对比两个版本")
        print("  python -m analysis.strategy_registry current                 # 显示当前生效映射")
        return
    
    cmd = sys.argv[1]
    
    if cmd == "list":
        versions = list_versions()
        if not versions:
            print("暂无历史版本")
        else:
            print(f"\n📜 历史版本列表 (共 {len(versions)} 个):")
            print(f"{'版本号':<20} {'时间':<25} {'来源':<20} {'Git':<10} {'股票数'}")
            print("-" * 90)
            for v in versions:
                if "error" in v:
                    print(f"{v['file']:<20} ❌ 读取失败")
                else:
                    ts = v.get("timestamp", "")[:19]
                    src = v.get("source", "")[:18]
                    git = v.get("git_commit", "")[:8]
                    cnt = v.get("total_stocks", 0)
                    print(f"{v['version']:<20} {ts:<25} {src:<20} {git:<10} {cnt}")
    
    elif cmd == "show":
        if len(sys.argv) < 3:
            print("❌ 请指定版本号")
            return
        show_version(sys.argv[2])
    
    elif cmd == "activate":
        if len(sys.argv) < 3:
            print("❌ 请指定版本号")
            return
        activate_version(sys.argv[2])
    
    elif cmd == "rollback":
        if len(sys.argv) < 3:
            print("❌ 请指定版本号")
            return
        rollback_to_version(sys.argv[2])
    
    elif cmd == "diff":
        if len(sys.argv) < 4:
            print("❌ 请指定两个版本号")
            return
        diff_versions(sys.argv[2], sys.argv[3])
    
    elif cmd == "current":
        cur = load_current_map()
        print(f"\n📋 当前生效映射 (共 {len(cur)} 只股票):")
        dist = {}
        for sym, strat in cur.items():
            s = strat if isinstance(strat, str) else strat.get("strategy", "unknown")
            dist[s] = dist.get(s, 0) + 1
        for s, cnt in sorted(dist.items()):
            print(f"  {s}: {cnt}")
    
    else:
        print(f"❌ 未知命令: {cmd}")


if __name__ == "__main__":
    main()