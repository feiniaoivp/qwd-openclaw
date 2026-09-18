#!/usr/bin/env python3
"""
脚本 sys.path 样板检查器 (script_path_guard)
=============================================
背景（2026-09-19）：
  本项目第二次踩「脚本模式 sys.path 缺失」坑 —— 脚本模式下 sys.path[0] 是脚本
  所在目录而非工作区根，导致 `from analysis.xxx import ...` 抛 ModuleNotFoundError；
  若又被 `except Exception` 吞掉，表现为「数据全空/静默失效」，极难排查。

本检查器把 TOOLS.md 的强制样板变成**可执行的门禁**，避免"只写在记忆里"。

规则:
  R1  脚本直接 `import analysis.xxx` 或 `from analysis.xxx import` 时，
      必须存在插「工作区根」的 sys.path.insert（或等价 sys.path.append）。
  R2  脚本直接 `from data_layer.xxx` / `from <subpkg> import` 时，
      必须存在插「analysis/」的 sys.path.insert。
  R3  工作区根绝不能用硬编码以外的相对推法（仅告警，不阻断）。

用法:
  python3 scripts/script_path_guard.py            # 检查全部
  python3 scripts/script_path_guard.py --fix-hint # 附修复建议
  python3 scripts/script_path_guard.py a.py b.py  # 检查指定文件
退出码: 0=全部通过, 1=存在违规
"""

import ast
import os
import re
import sys
import argparse

WORKSPACE = os.path.expanduser("~/.openclaw/workspace")

# 扫描范围
SCAN_DIRS = ["analysis", "scripts"]
SKIP_DIR_NAMES = {"__pycache__", "backups", "agent", "data_layer", "rag", "prompts", "sites"}


def _has_workspace_root_insert(src: str) -> bool:
    """是否插入了工作区根（兼容 WORKSPACE / _WORKSPACE 两种命名）"""
    if re.search(r"sys\.path\.(insert|append)\(\s*0\s*,\s*_?WORKSPACE\s*\)", src):
        return True
    if re.search(r"sys\.path\.(insert|append)\(\s*0\s*,\s*[\"']/Users/duguke/\.openclaw/workspace[\"']\s*\)", src):
        return True
    # 循环写法: for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis"))
    if re.search(r"for\s+\w+\s+in\s*\(\s*_?WORKSPACE\s*,", src):
        return True
    return False


def _has_analysis_dir_insert(src: str) -> bool:
    """是否插入了 analysis/ 目录"""
    if re.search(r"_?os\.path\.join\(\s*_?WORKSPACE\s*,\s*[\"']analysis[\"']\s*\)", src):
        return True
    if re.search(r"sys\.path\.(insert|append)\(\s*0\s*,\s*[\"'][^\"']*[/\\\\]analysis[\"']\s*\)", src):
        return True
    # sys.path.insert(0, os.path.dirname(__file__)) —— 在 analysis/ 内的脚本等价于插 analysis/
    if re.search(r"sys\.path\.(insert|append)\(\s*0\s*,\s*os\.path\.dirname\(\s*__file__\s*\)\s*\)", src):
        return True
    return False


def _toplevel_imports(src: str):
    """返回模块级 import 的目标包名集合（只取顶层，函数内 import 不算）"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    pkgs = set()
    for node in tree.body:  # 只看模块级
        if isinstance(node, ast.Import):
            for a in node.names:
                pkgs.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                pkgs.add(node.module.split(".")[0])
    return pkgs


SUBPKGS = {"data_layer", "data"}  # analysis/ 下的子包，需 analysis/ 在 path 上


def check_file(path: str):
    """返回 (violations, warnings)：violations 为 (code, msg) 列表"""
    violations, warnings = [], []
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
    except Exception as e:
        return [("READ", f"无法读取: {e}")], []

    pkgs = _toplevel_imports(src)
    if pkgs is None:
        return [("SYNTAX", "语法错误，无法解析")], []

    has_root = _has_workspace_root_insert(src)
    has_analysis = _has_analysis_dir_insert(src)

    # R1: 模块级 import analysis.*
    if "analysis" in pkgs and not has_root:
        violations.append(
            ("R1", "模块级 import analysis.* 但缺少「工作区根」sys.path 插入"
                   "（脚本模式下会 ModuleNotFoundError）")
        )

    # R2: 模块级 from data_layer.* / 其他 analysis 子包
    hit_sub = pkgs & SUBPKGS
    if hit_sub and not has_root:
        violations.append(
            ("R2", f"模块级 from {sorted(hit_sub)} import 但缺少「工作区根」sys.path 插入")
        )
    if hit_sub and not has_analysis and not has_root:
        violations.append(
            ("R2", f"模块级 from {sorted(hit_sub)} import 但缺少「analysis/」sys.path 插入")
        )

    # R3 (告警): 静默 except
    if re.search(r"except\s+Exception\s*:\s*\n\s*(pass|continue)\s*$", src, re.M):
        warnings.append(("R3", "存在 `except Exception: pass/continue` 静默吞异常（建议至少 log.warning）"))

    return violations, warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="指定文件；缺省扫描 analysis/ scripts/")
    ap.add_argument("--fix-hint", action="store_true", help="打印修复样板")
    args = ap.parse_args()

    if args.files:
        targets = [os.path.abspath(f) for f in args.files]
    else:
        targets = []
        for d in SCAN_DIRS:
            root = os.path.join(WORKSPACE, d)
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [x for x in dirnames if x not in SKIP_DIR_NAMES]
                for fn in filenames:
                    if fn.endswith(".py") and not fn.startswith("_"):
                        targets.append(os.path.join(dirpath, fn))

    total_v = 0
    total_w = 0
    bad_files = []
    for p in sorted(targets):
        v, w = check_file(p)
        if v:
            total_v += len(v)
            bad_files.append(p)
            rel = os.path.relpath(p, WORKSPACE)
            print(f"❌ {rel}")
            for code, msg in v:
                print(f"     [{code}] {msg}")
        total_w += len(w)

    print()
    print(f"扫描: {len(targets)} 个脚本 | 违规文件: {len(bad_files)} | 违规项: {total_v} | 告警: {total_w}")

    if total_v == 0:
        print("✅ 全部通过：无缺失 sys.path 样板的脚本")

    if args.fix_hint and total_v:
        print("\n修复样板（照抄到文件顶部，WORKSPACE 定义之后）：")
        print("""
import os, sys
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
""")

    return 1 if total_v else 0


if __name__ == "__main__":
    sys.exit(main())
