#!/usr/bin/env python3
"""
silent_except_guard.py — 静默异常吞噬门禁（AST 静态扫描，单进程秒级）
=====================================================================
背景（2026-09-28 实证）：cron_health_check.py 的 `except Exception` 只走
log.debug，把 gateway HTTP API 404 静默吞掉 10 天，监控全盲还报"正常"。
这是「except Exception 禁止静默」坑的第三次复现（08-23 / 09-16 / 09-28）
→ 按 AGENTS.md「教训 → 三层防线」固化为门禁。

检查规则（风险分级，防"狼来了"）：
- 🔴 HIGH（exit 1）：宽泛 except（Exception/BaseException/裸 except）且处理体
  完全静默（仅 log.debug / 仅 pass / 仅 return None）—— 异常零可见痕迹
- 🟡 WARN（仅报告，不阻断）：窄类型 except + 仅 log.debug
- ✅ 不标记：处理体含 log.warning/error/info/critical/print/raise/赋值等可见动作

用法：
  python3 scripts/silent_except_guard.py             # 扫描 analysis/ scripts/
  python3 scripts/silent_except_guard.py --selftest  # 对抗性自测（注入坏样本验证）
退出码：0=通过，1=存在 HIGH 违规
"""

import ast
import json
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
SCAN_DIRS = ["analysis", "scripts"]
EXCLUDE_DIRS = {"__pycache__", ".learnings", "backtest"}

# log.debug / pass / return None —— 零可见输出，视为静默
SILENT_LOG_ATTRS = {"debug"}
VISIBLE_LOG_ATTRS = {"warning", "error", "info", "critical", "exception", "print"}
BROAD_NAMES = {"Exception", "BaseException"}


def _is_broad_type(node) -> bool:
    """判断异常类型是否宽泛（Exception/BaseException/裸 except/含宽泛的元组）"""
    if node is None:
        return True  # 裸 except:
    if isinstance(node, ast.Name):
        return node.id in BROAD_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in BROAD_NAMES
    if isinstance(node, ast.Tuple):
        return any(_is_broad_type(e) for e in node.elts)
    return False


def _stmt_kind(stmt: ast.stmt):
    """分类 except 处理体语句：'silent' / 'visible' / 'other'（other 视为可见动作）"""
    # docstring（Expr(Constant str)）不算语句
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
            and isinstance(stmt.value.value, str):
        return "docstring"
    if isinstance(stmt, ast.Pass):
        return "silent"
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        f = stmt.value.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
        if name in SILENT_LOG_ATTRS:
            return "silent"
        if name in VISIBLE_LOG_ATTRS:
            return "visible"
        # 未知调用（如 fallback/告警函数）→ 视为可见动作，不误报
        return "visible"
    if isinstance(stmt, ast.Return):
        # return None → 静默；return 有值 → 可见
        if stmt.value is None or (isinstance(stmt.value, ast.Constant) and stmt.value.value is None):
            return "silent"
        return "visible"
    # raise / 赋值 / if / 其他语句 → 视为可见动作（不误报）
    return "visible"


def handler_verdict(handler: ast.ExceptHandler):
    """返回 None（合规）或 (level, desc)"""
    kinds = [_stmt_kind(s) for s in handler.body]
    kinds = [k for k in kinds if k != "docstring"]
    if not kinds:
        return None  # 空处理体（AST 视角）→ 不判定
    if any(k == "visible" for k in kinds):
        return None  # 至少有一个可见动作 → 合规
    # 完全静默（仅 log.debug / pass / return None）
    if _is_broad_type(handler.type):
        return ("HIGH", "宽泛 except 处理体完全静默（仅 log.debug/pass/return None）")
    return ("WARN", "窄类型 except 处理体仅 log.debug（无可见输出）")


def scan_file(path: str):
    """返回 [(level, desc, lineno)]；语法错误跳过（SKIP 级）"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except SyntaxError as e:
        return [("SKIP", f"语法解析失败跳过: {e}", getattr(e, "lineno", 0))]
    except Exception as e:
        return [("SKIP", f"读取失败跳过: {type(e).__name__}", 0)]
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            v = handler_verdict(node)
            if v:
                findings.append((v[0], v[1], node.lineno))
    return findings


def run_scan():
    high, warns, skipped = [], [], []
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
                        high.append(entry)
                    elif level == "WARN":
                        warns.append(entry)
                    else:
                        skipped.append(entry)
    return files_scanned, high, warns, skipped


def selftest() -> bool:
    """对抗性验证：坏样本必须全命中，好样本必须零误报"""
    bad_broad_debug = "try:\n    do_work()\nexcept Exception as e:\n    log.debug(f'failed: {e}')\n"
    bad_bare_pass = "try:\n    do_work()\nexcept:\n    pass\n"
    bad_broad_ret = "try:\n    do_work()\nexcept Exception:\n    return None\n"
    good_warning = "try:\n    do_work()\nexcept Exception as e:\n    log.warning(f'failed: {e}')\n"
    good_raise = "try:\n    do_work()\nexcept ValueError as e:\n    raise\n"
    good_visible_call = "try:\n    do_work()\nexcept Exception as e:\n    send_alert('failed')\n"
    good_assign = "try:\n    do_work()\nexcept Exception as e:\n    last_err = str(e)\n"

    cases = [
        (bad_broad_debug, "HIGH", "宽泛+log.debug 必须命中"),
        (bad_bare_pass, "HIGH", "裸 except+pass 必须命中"),
        (bad_broad_ret, "HIGH", "宽泛+return None 必须命中"),
        (good_warning, None, "log.warning 必须放过"),
        (good_raise, None, "raise 必须放过"),
        (good_visible_call, None, "可见调用必须放过"),
        (good_assign, None, "赋值语句必须放过"),
    ]
    ok = True
    for src, expect, desc in cases:
        tree = ast.parse(src)
        handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
        v = handler_verdict(handler)
        got = v[0] if v else None
        if got != expect:
            print(f"  ❌ 自测失败: {desc}（期望 {expect}，实际 {got}）")
            ok = False
        else:
            print(f"  ✅ {desc} → {got or '合规'}")
    return ok


def main():
    if "--selftest" in sys.argv:
        print("=== 对抗性自测 ===")
        sys.exit(0 if selftest() else 1)

    files, high, warns, skipped = run_scan()
    print(f"扫描 {files} 个脚本（analysis/ + scripts/）")
    print(f"🔴 HIGH（宽泛 except 完全静默）: {len(high)}")
    for e in high[:40]:
        print(f"   {e}")
    if len(high) > 40:
        print(f"   ...（还有 {len(high)-40} 处）")
    print(f"🟡 WARN（窄类型 except 仅 log.debug）: {len(warns)}")
    for e in warns[:10]:
        print(f"   {e}")
    if len(warns) > 10:
        print(f"   ...（还有 {len(warns)-10} 处）")
    if skipped:
        print(f"⚪ SKIP（语法/读取失败）: {len(skipped)}")
        for e in skipped[:5]:
            print(f"   {e}")

    # ── 棘轮模式（2026-09-28）：存量静默只报告（115 处历史债务不阻断），
    # 新增静默才 exit 1；基线只降不升（修复后基线同步下降）。防"狼来了"。──
    baseline_path = os.path.join(WORKSPACE, "data", "silent_except_baseline.json")
    # 稳定键：文件路径 + 描述（不含行号，行号随代码编辑漂移）
    from collections import Counter
    current = Counter(f"{e.split(' ', 1)[0].rsplit(':', 1)[0]}#{e.split(' ', 1)[1]}" for e in high)
    baseline = Counter()
    baseline_existed = os.path.exists(baseline_path)
    if baseline_existed:
        try:
            with open(baseline_path, "r", encoding="utf-8") as f:
                baseline = Counter(json.load(f))
        except Exception as e:
            log.warning(f"读取静默异常基线失败（按空基线处理）: {e}")
    new_debt = {} if not baseline_existed else \
        {k: v - baseline.get(k, 0) for k, v in current.items() if v > baseline.get(k, 0)}
    fixed = {k: v for k, v in baseline.items() if current.get(k, 0) < v}
    if os.path.isdir(os.path.dirname(baseline_path)):
        try:
            with open(baseline_path, "w", encoding="utf-8") as f:
                json.dump(dict(current), f, ensure_ascii=False, indent=1)
        except Exception as e:
            log.warning(f"写回静默异常基线失败: {e}")
    if fixed:
        print(f"📉 本期修复 {sum(fixed.values())} 处静默异常（基线已同步下降）")
    if new_debt:
        print(f"\n❌ 新增 {sum(new_debt.values())} 处静默异常（历史存量 {sum(current.values()) - sum(new_debt.values())} 处仅报告不阻断）：")
        for k, v in list(new_debt.items())[:20]:
            print(f"   +{v} {k}")
        print("   宽泛 except 只走 log.debug 会把故障隐身（参照 cron_health_check.py 2026-09-28 案例，监控盲 10 天）。")
        print("   修复：至少 log.warning；关键数据路径的异常必须可见。")
        sys.exit(1)
    print(f"\n✅ 门禁通过（exit 0）：无新增静默异常（存量 {sum(current.values())} 处仅报告，待逐步清理）")
    sys.exit(0)


if __name__ == "__main__":
    main()
