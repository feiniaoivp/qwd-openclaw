#!/usr/bin/env python3
"""
rm 前置门禁 (rm_guard) - v2 精准版
===================================
扫描工作区内所有 .py/.sh 文件中的实际可执行 rm -rf/rm -r 调用，
拦截指向危险路径的删除操作。

关键改进：跳过 markdown 代码块、注释行、字符串字面量、文档示例。

退出码：0=通过, 1=存在 HIGH 违规
"""

import os
import re
import sys
from pathlib import Path

WORKSPACE = Path("/Users/duguke/.openclaw/workspace")

# 危险路径模式（HIGH 级：直接 exit 1） - 仅匹配实际可执行的 rm 命令
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/($|\s)",           # rm -rf / (行尾或空格后)
    r"rm\s+-rf\s+~/($|\s)",          # rm -rf ~/
    r"rm\s+-rf\s+\$HOME($|\s)",      # rm -rf $HOME
    r"rm\s+-rf\s+\*($|\s)",          # rm -rf *
    r"rm\s+-rf\s+\.\*($|\s)",        # rm -rf .*
    r"rm\s+-r\s+/($|\s)",            # rm -r /
    r"rm\s+-r\s+~/($|\s)",           # rm -r ~/
    r"rm\s+-r\s+\$HOME($|\s)",       # rm -r $HOME
]

# 待确认模式（WARN 级：仅告警，不阻断） - 针对变量/相对路径
WARN_PATTERNS = [
    r"rm\s+-rf\s+\$\w+($|\s)",       # rm -rf $VAR
    r"rm\s+-rf\s+\./($|\s)",         # rm -rf ./
]

def is_in_md_code_block(lines, line_idx):
    """检查行是否在 markdown 代码块内（``` ... ```）"""
    in_block = False
    for i in range(line_idx + 1):
        stripped = lines[i].strip()
        if stripped.startswith("```"):
            in_block = not in_block
    return in_block

def is_comment_or_string(line, in_py_string=False):
    """简化判断：行是否为注释或字符串字面量"""
    stripped = line.strip()
    # Python 注释
    if stripped.startswith("#"):
        return True
    # Shell 注释
    if stripped.startswith("#"):
        return True
    # 纯字符串字面量（简化：行首是引号且行尾也是引号）
    if (stripped.startswith('"') and stripped.endswith('"')) or \
       (stripped.startswith("'") and stripped.endswith("'")):
        return True
    return False

def is_executable_rm_line(line):
    """判断是否为实际可执行的 rm 命令行（非注释、非字符串、非markdown代码块）"""
    stripped = line.strip()
    # 必须以 rm 开头（可能前面有缩进）
    if not re.match(r"^\s*rm\s", stripped):
        return False
    # 排除赋值语句: cmd = "rm -rf ..."
    if "=" in stripped and stripped.split("=")[0].strip().isidentifier():
        # 简单检查：等号前是变量名
        left = stripped.split("=")[0].strip()
        if left and not left.startswith("rm"):
            return False
    return True

def scan_py_sh_file(filepath: Path) -> list:
    """扫描 .py/.sh 文件，返回违规列表"""
    issues = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return issues
    
    lines = content.split("\n")
    for i, line in enumerate(lines):
        # 跳过注释行
        if is_comment_or_string(line):
            continue
        # 必须是可执行的 rm 行
        if not is_executable_rm_line(line):
            continue
        
        # 检查 HIGH 模式
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, line):
                issues.append({
                    "file": str(filepath.relative_to(WORKSPACE)),
                    "line": i + 1,
                    "code": line.strip(),
                    "level": "HIGH",
                    "pattern": pattern
                })
        
        # 检查 WARN 模式
        for pattern in WARN_PATTERNS:
            if re.search(pattern, line):
                issues.append({
                    "file": str(filepath.relative_to(WORKSPACE)),
                    "line": i + 1,
                    "code": line.strip(),
                    "level": "WARN",
                    "pattern": pattern
                })
    return issues

def scan_md_file(filepath: Path) -> list:
    """扫描 .md 文件，仅检查 markdown 代码块外的实际命令（极少见）"""
    issues = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return issues
    
    lines = content.split("\n")
    for i, line in enumerate(lines):
        # 跳过 markdown 代码块内的内容
        if is_in_md_code_block(lines, i):
            continue
        # 跳过注释行（markdown 无行注释，但可能有 HTML 注释）
        if line.strip().startswith("<!--"):
            continue
        # 仅检查以 rm 开头的裸露命令行（非代码块）
        if not is_executable_rm_line(line):
            continue
        
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, line):
                issues.append({
                    "file": str(filepath.relative_to(WORKSPACE)),
                    "line": i + 1,
                    "code": line.strip(),
                    "level": "HIGH",
                    "pattern": pattern
                })
    return issues

def main():
    all_issues = []
    high_issues = []
    warn_issues = []
    
    # 扫描 .py, .sh 文件（主要可执行脚本）
    for ext in [".py", ".sh"]:
        for filepath in WORKSPACE.rglob(f"*{ext}"):
            # 跳过 .git, __pycache__, backups, data_cache 等目录
            if any(part in filepath.parts for part in [".git", "__pycache__", "backups", "data_cache", "node_modules", ".venv", "venv"]):
                continue
            issues = scan_py_sh_file(filepath)
            all_issues.extend(issues)
    
    # 扫描 .md 文件（仅裸露命令行，不含代码块）
    for filepath in WORKSPACE.rglob("*.md"):
        if any(part in filepath.parts for part in [".git", "__pycache__", "backups", "data_cache", "node_modules", ".venv", "venv"]):
            continue
        issues = scan_md_file(filepath)
        all_issues.extend(issues)
    
    for issue in all_issues:
        if issue["level"] == "HIGH":
            high_issues.append(issue)
        else:
            warn_issues.append(issue)
    
    # 输出
    if high_issues:
        print(f"🔴 rm_guard: {len(high_issues)} 项 HIGH 违规")
        for issue in high_issues:
            print(f"   {issue['file']}:{issue['line']} | {issue['code']}")
        return 1
    
    if warn_issues:
        print(f"🟡 rm_guard: {len(warn_issues)} 项告警（不阻断）")
        for issue in warn_issues[:10]:
            print(f"   {issue['file']}:{issue['line']} | {issue['code']}")
        if len(warn_issues) > 10:
            print(f"   ... 及其他 {len(warn_issues)-10} 项")
    
    if not high_issues and not warn_issues:
        print("✅ rm_guard: 通过（无危险 rm 调用）")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
