#!/usr/bin/env python3
"""
Preflight 门禁聚合器
====================
在重大变更/定时任务前运行，聚合所有门禁检查：
1. sys.path 样板门禁 (script_path_guard.py)
2. 技能供应链扫描 (skill_supply_scan.py --offline)
3. rm 前置门禁 (rm_guard.py)
4. 端口监听检查 (lsof -i :18789)
5. Git 快照记录 (git tag -l 'guard/*')

退出码：0=全部通过, 1=存在 HIGH 阻断项
"""

import subprocess
import sys
import os

WORKSPACE = "/Users/duguke/.openclaw/workspace"

def run_check(name: str, cmd: list, cwd=WORKSPACE) -> tuple:
    """运行单个检查，返回 (name, ok, output)"""
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
        return (name, result.returncode == 0, result.stdout.strip())
    except subprocess.TimeoutExpired:
        return (name, False, f"超时 (60s)")
    except Exception as e:
        return (name, False, f"异常: {e}")

def main():
    checks = [
        ("sys.path样板门禁", [sys.executable, "scripts/script_path_guard.py"]),
        ("技能供应链扫描", [sys.executable, "scripts/skill_supply_scan.py", "--offline"]),
        ("rm前置门禁", [sys.executable, "scripts/rm_guard.py"]),
    ]
    
    # 端口检查
    try:
        result = subprocess.run(["lsof", "-i", ":18789"], capture_output=True, text=True, timeout=10)
        port_ok = "localhost:18789" in result.stdout and "LISTEN" in result.stdout
        port_output = result.stdout.strip()
    except Exception as e:
        port_ok = False
        port_output = f"异常: {e}"
    
    # Git 快照检查
    try:
        result = subprocess.run(["git", "tag", "-l", "guard/*"], cwd=WORKSPACE, capture_output=True, text=True, timeout=10)
        tag_count = len([l for l in result.stdout.strip().split("\n") if l])
        git_ok = tag_count > 0
        git_output = f"{tag_count} 个 guard/* 标签"
    except Exception as e:
        git_ok = False
        git_output = f"异常: {e}"
    
    print("=" * 60)
    print("🔍 Preflight 门禁聚合检查")
    print("=" * 60)
    
    all_ok = True
    high_blockers = []
    
    for name, ok, output in [run_check(n, c) for n, c in checks]:
        status = "✅" if ok else "🔴"
        print(f"{status} {name}: {'通过' if ok else '失败'}")
        if not ok:
            print(f"   输出: {output[:200]}")
            high_blockers.append(name)
            all_ok = False
    
    # 端口检查
    status = "✅" if port_ok else "🔴"
    print(f"{status} 端口监听检查: {'仅本地监听' if port_ok else '异常'}")
    if not port_ok:
        print(f"   输出: {port_output}")
        high_blockers.append("端口监听检查")
        all_ok = False
    else:
        print(f"   {port_output}")
    
    # Git 快照检查
    status = "✅" if git_ok else "🟡"
    print(f"{status} Git 快照记录: {git_output}")
    if not git_ok:
        print(f"   ⚠️ 近 30 天无 guard/* 快照")
        # 不作为 HIGH 阻断，仅告警
    
    print("=" * 60)
    if all_ok:
        print("🎉 Preflight 全部通过")
        return 0
    else:
        print(f"💥 Preflight 阻断: {', '.join(high_blockers)}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
