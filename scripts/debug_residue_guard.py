#!/usr/bin/env python3
"""
debug_residue_guard.py — 调试残留门禁（AST 静态扫描，单进程秒级）
==================================================================
背景（2026-10-11 python-debugpy 技能落实）：breakpoint() / pdb.set_trace() /
debugpy.listen() 若被提交进仓库，cron 无人值守进程会挂死（等 stdin）或
在本机开调试端口。本门禁用 AST 扫描调用点，注释/docstring 不误报。

风险分级：
- 🔴 HIGH（exit 1，阻断式调试入口）：
    breakpoint(...) / pdb.set_trace(...) /
    debugpy.listen(...) / debugpy.wait_for_client(...) /
    debugpy.connect(...) / debugpy.breakpoint(...)
- 🟡 WARN（仅报告，不阻断）：
    import pdb / import debugpy / from debugpy ... / pdb.post_mortem(...)

配套人工检查（提交前手动跑一次，rg 快速版）：
    rg -n 'breakpoint\\(|pdb\\.set_trace|debugpy\\.' --type py

用法：
  python3 scripts/debug_residue_guard.py             # 扫描 analysis/ scripts/
  python3 scripts/debug_residue_guard.py --selftest  # 对抗性自测
  python3 scripts/debug_residue_guard.py --quiet     # 只输出摘要与发现
退出码：0=通过，1=存在 HIGH 违规
"""

import ast
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
SCAN_DIRS = ["analysis", "scripts"]
EXCLUDE_DIRS = {"__pycache__", ".learnings", "backtest", "backups"}

# 🔴 阻断式调试入口（属性链小写全名）
HIGH_CALLS = {
    "breakpoint",
    "pdb.set_trace",
    "debugpy.listen",
    "debugpy.wait_for_client",
    "debugpy.connect",
    "debugpy.breakpoint",
}
# 🟡 仅报告
WARN_CALLS = {"pdb.post_mortem"}
WARN_IMPORTS = {"pdb", "debugpy"}


def _dotted_name(node):
    """还原 Attribute/Name 调用链的小写点号全名（如 pdb.set_trace）"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr.lower())
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id.lower())
        return ".".join(reversed(parts))
    return None


def scan_tree(tree):
    """对已解析 AST 返回 [(level, desc, lineno)]（scan_file 与自测共用）"""
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name.split(".")[0].lower() for a in node.names]
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.append(node.module.split(".")[0].lower())
            for m in set(mods):
                if m in WARN_IMPORTS:
                    findings.append(
                        ("WARN", f"{m} 导入（确认非交互用途，建议用 analysis.debug_pm）", node.lineno)
                    )
        elif isinstance(node, ast.Call):
            name = _dotted_name(node.func)
            if name in HIGH_CALLS:
                findings.append(("HIGH", f"阻断式调试调用 {name}()", node.lineno))
            elif name in WARN_CALLS:
                findings.append(("WARN", f"{name}() 调用（确认已用 try/except 包裹且可见）", node.lineno))
    return findings


def scan_file(path):
    """返回 [(level, desc, lineno)]"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except SyntaxError as e:
        return [("SKIP", f"语法解析失败跳过: {e}", getattr(e, "lineno", 0))]
    except Exception as e:
        return [("SKIP", f"读取失败跳过: {type(e).__name__}", 0)]
    return scan_tree(tree)


def run_scan():
    highs, warns, skipped = [], [], []
    files_scanned = 0
    for d in SCAN_DIRS:
        root = os.path.join(WORKSPACE, d)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames if x not in EXCLUDE_DIRS]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                files_scanned += 1
                rel = os.path.relpath(p, WORKSPACE)
                for level, desc, lineno in scan_file(p):
                    entry = f"{rel}:{lineno} {desc}"
                    if level == "HIGH":
                        highs.append(entry)
                    elif level == "WARN":
                        warns.append(entry)
                    else:
                        skipped.append(entry)
    return files_scanned, highs, warns, skipped


def selftest():
    """对抗性验证：坏样本必须全命中，好样本（含注释/docstring 干扰）必须零误报。
    断言口径 = 全部 findings 的 level 集合（避免遍历顺序短路掩盖 HIGH）。"""
    cases = [
        ("breakpoint()", {"HIGH"}, "裸 breakpoint 调用"),
        ("import pdb\npdb.set_trace()", {"HIGH", "WARN"}, "pdb.set_trace（import+调用双命中）"),
        ("import debugpy\ndebugpy.listen(('127.0.0.1', 5678))", {"HIGH", "WARN"}, "debugpy.listen"),
        ("import debugpy\ndebugpy.wait_for_client()", {"HIGH", "WARN"}, "debugpy.wait_for_client"),
        ("import pdb", {"WARN"}, "仅 import pdb → WARN"),
        ("# breakpoint() 注释里提及", set(), "注释提及不误报"),
        ('"""docstring 里 breakpoint()/pdb.set_trace 提及"""', set(), "docstring 提及不误报"),
        ("from analysis.debug_pm import pm_dump\npm_dump('x')", set(), "自家验尸工具放行"),
        ("import json", set(), "普通 import 放行"),
    ]
    ok = True
    for src, expect, desc in cases:
        tree = ast.parse(src)
        levels = {lv for lv, _, _ in scan_tree(tree)}
        if levels != expect:
            print(f"  ❌ 自测失败: {desc}（期望 {sorted(expect)}，实际 {sorted(levels)}）")
            ok = False
        else:
            print(f"  ✅ {desc} → {sorted(levels) or '合规'}")
    return ok


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        print("=== 对抗性自测 ===")
        sys.exit(0 if selftest() else 1)
    quiet = "--quiet" in args

    files, highs, warns, skipped = run_scan()
    print(f"扫描 {files} 个脚本（analysis/ + scripts/）")
    print(f"🔴 HIGH（阻断式调试入口）: {len(highs)}")
    for e in highs[:40]:
        print(f"   {e}")
    if len(highs) > 40:
        print(f"   ...（还有 {len(highs)-40} 处）")
    if warns and not quiet:
        print(f"🟡 WARN（仅报告）: {len(warns)}")
        for e in warns[:10]:
            print(f"   {e}")
        if len(warns) > 10:
            print(f"   ...（还有 {len(warns)-10} 处）")
    if skipped:
        print(f"⚪ SKIP（语法/读取失败）: {len(skipped)}")
        for e in skipped[:5]:
            print(f"   {e}")

    if highs:
        print("\n❌ 门禁未通过（exit 1）：存在阻断式调试残留")
        print("   breakpoint()/pdb.set_trace 提交后会让 cron 无人值守进程挂死等 stdin，")
        print("   debugpy.listen 会在本机开调试端口。交互调试完成后必须移除。")
        print("   人工快速复核：rg -n 'breakpoint\\(|pdb\\.set_trace|debugpy\\.' --type py")
        sys.exit(1)
    print("\n✅ 门禁通过（exit 0）：无调试残留")
    sys.exit(0)


if __name__ == "__main__":
    main()
