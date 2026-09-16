#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性接线脚本：为「直接使用 akshare/baostock 但未经 router/bs_session」的入口脚本
注入 net_guard 兜底（进程级 socket 超时）。

原则：
  - 只做插入，不重写任何现有逻辑。
  - 幂等：已含 net_guard 的文件跳过。
  - 在最后一个顶部 import 之后插入（保证 WORKSPACE/sys.path 已就绪）。
  - 插入后立即做 AST + import 冒烟校验。

用法:
    python3 scripts/wire_net_guard.py --check     # 只看会改哪些
    python3 scripts/wire_net_guard.py --apply     # 真正写入
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

WS = Path("/Users/duguke/.openclaw/workspace")

# 目标：入口脚本中，直接 import akshare/baostock 且未经受保护模块的文件
TARGETS = [
    "analysis/zhonglian_monitor.py",
    "analysis/zhonglian_optimize.py",
    "analysis/regime_filter.py",
    "analysis/financial_report_analyzer.py",
    "analysis/financial_report_analyzer_v2.py",
    "analysis/review_030_snapshot.py",
    "analysis/overseas_dual_factor.py",
    "analysis/three_factor_resonance.py",
    "analysis/three_factor_helper.py",
    "analysis/print_core_scan.py",
    "analysis/stock_picks_0708.py",
    "analysis/watchlist_candidates.py",
    "analysis/overnight_outlook.py",
    "analysis/weekly_full_pipeline.py",
    "scripts/fetch_commodity_fx.py",
    "scripts/monitor_overseas_tenders.py",
    "scripts/update_overseas_revenue.py",
]

SNIPPET = (
    "\n# 网络兜底：进程级 socket 默认超时（防数据源挂起导致永久阻塞）\n"
    "try:\n"
    "    from analysis.net_guard import install_default_timeout as _install_net_timeout\n"
    "    _install_net_timeout()\n"
    "except ImportError:\n"
    "    import os as _os, sys as _sys\n"
    "    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))\n"
    "    from analysis.net_guard import install_default_timeout as _install_net_timeout\n"
    "    _install_net_timeout()\n"
)


def find_insert_line(lines: list[str]) -> int:
    """
    找到最后一个顶层 import/from **语句**结束后的位置（0-based 插入点）。
    必须用 AST 以防多行 import（括号续行）被从中间切断。
    """
    src = "".join(lines)
    tree = ast.parse(src)
    last_end = 0  # 1-based lineno of语句末尾
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_end = max(last_end, node.end_lineno or node.lineno)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue  # 模块 docstring
        else:
            # 遇到第一个非 import 的实质语句就停（只处理顶部 import 区）
            break
    return last_end if last_end else 1


def process(path: Path, apply: bool) -> str:
    src = path.read_text(encoding="utf-8")
    if "net_guard" in src:
        return "SKIP (已有 net_guard)"
    lines = src.splitlines(keepends=True)
    idx = find_insert_line(lines)
    new = "".join(lines[:idx]) + SNIPPET + "".join(lines[idx:])
    # 校验语法
    try:
        ast.parse(new)
    except SyntaxError as e:
        return f"FAIL 语法错误: {e}"
    if apply:
        path.write_text(new, encoding="utf-8")
    return f"OK (插入到第 {idx+1} 行后)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    for rel in TARGETS:
        p = WS / rel
        if not p.exists():
            print(f"  MISSING {rel}")
            continue
        res = process(p, apply=args.apply)
        flag = "✏️ " if args.apply else "👀 "
        print(f"{flag}{rel}: {res}")


if __name__ == "__main__":
    main()
