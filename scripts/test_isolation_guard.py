#!/usr/bin/env python3
"""测试隔离门禁（test isolation guard）

缘起（2026-10-21 深究第四轮 D 项，用户拍板 1+2）
------------------------------------------------
实证事故：2026-10-20 18:0x，e9fb539「bucket 拆分功能验证」的临时冒烟脚本直调
`portfolio_core.execute_trade()`，用占位价 `price=10.0` 跑真交易，**未把
STATE_FILE / TRADES_FILE / EQUITY_FILE 重定向到 tempfile**。结果两笔假成交
（国电南瑞 1400 股 core、招商银行 400 股 satellite）写进生产底账
`data/portfolio_sim_trades.json`，两个月后才被察觉。

为什么 `BACKTEST_MODE` 挡不住
-----------------------------
`portfolio_core` 里 `BACKTEST_MODE` 只守卫 `append_trade` / `save_state` /
`append_equity_snapshot` 三个函数——它**不会自己变 True**。手动跑的临时脚本没有
任何机制去设它，于是「测试」与「生产」共用同一套文件路径常量，直调即污染。

同一批事故里，正确的做法其实已经存在：`analysis/research/2026-10-20_add_position_avgtp_verify.py`
开头就把 `pc.STATE_FILE` / `pc.TRADES_FILE` 都指到 tempfile，并额外设
`os.environ["BACKTEST_MODE"]="1"` 兜底。本门禁把那个样板固化为**强制规则**。

判定规则（HIGH = exit 1）
------------------------
H1. 文件 import 了 `portfolio_core`（或 from ... import execute_trade）**且**调用了
    `execute_trade(` / `append_trade(` / `append_equity_snapshot(` / `save_state(` 之一，
    但**没有把三个持久化路径常量全部重定向**到临时目录 → HIGH。
    「全部重定向」= 对 STATE_FILE / TRADES_FILE / EQUITY_FILE 各有一个赋值语句，
    且右值里出现 tempfile/mkdtemp/TemporaryDirectory/tmp_path/"/tmp/" 之一。
H2. 重定向写在调用**之后**（AST 行号比较）→ 同样 HIGH（顺序错 = 等于没重定向）。

豁免（白名单）
-------------
- 生产入口本身：`analysis/portfolio_core.py`（定义方）、`analysis/portfolio_executor.py`
  （回测器，构建时自设 BACKTEST_MODE）、`analysis/portfolio_sim.py`（模拟盘主流程）。
- 文件内含 `ISOLATION_GUARD_OK` 注释且给出理由（人工豁免留痕）。

退出码：0 = 通过；1 = 命中 HIGH。
用法：
  python3 scripts/test_isolation_guard.py             # 全扫 analysis/ + scripts/
  python3 scripts/test_isolation_guard.py --selftest  # 对抗性自测
  python3 scripts/test_isolation_guard.py --verbose   # 打印全部命中文件
"""
import argparse
import ast
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")

# 会写生产账本/状态的函数（execute_trade 内部会调 append_trade，故只查这些入口）
SIDE_EFFECT_CALLS = {
    "execute_trade",
    "append_trade",
    "append_equity_snapshot",
    "save_state",
}

# 必须全部重定向的三个持久化路径常量
REQUIRED_REDIRECTS = ("STATE_FILE", "TRADES_FILE", "EQUITY_FILE")

# 生产入口白名单（相对 workspace 的路径）
WHITELIST = {
    "analysis/portfolio_core.py",
    "analysis/portfolio_executor.py",
    "analysis/portfolio_sim.py",
    # LangGraph 生产节点：盘前流水线真实调用 save_state/execute_zhonglian_trade，
    # 是账本的正规写入方，不是测试（2026-10-21 实扫确认）
    "analysis/agent/nodes.py",
}

# 右值里出现这些即视为指向临时目录
TEMP_MARKERS = ("tempfile", "mkdtemp", "TemporaryDirectory", "tmp_path", "/tmp/", "tempdir")


def _iter_py_files():
    for sub in ("analysis", "scripts"):
        root_dir = os.path.join(WORKSPACE, sub)
        if not os.path.isdir(root_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git", "node_modules")]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def _temp_names(tree):
    """收集「右值直接是临时目录构造」的局部变量名，用于间接引用回溯。

    例：tmp = tempfile.mkdtemp() → {"tmp"}
    否则 os.path.join(tmp, "s.json") 这类间接引用永远判不出来（实测踩过）。
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _raw_is_temp(node.value):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
    return names


def _raw_is_temp(node):
    try:
        src = ast.unparse(node)
    except Exception:
        return False
    low = src.lower()
    return any(m.lower() in low for m in TEMP_MARKERS)


def _is_temp_expr(node, temp_names=None):
    """判断赋值右值是否指向临时目录（支持经局部变量间接引用）。"""
    if _raw_is_temp(node):
        return True
    if temp_names:
        try:
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        except Exception:
            return False
        if used & temp_names:
            return True
    return False


def _imports_portfolio_core(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if "portfolio_core" in a.name:
                    return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if "portfolio_core" in mod:
                return True
            for a in node.names:
                if a.name in SIDE_EFFECT_CALLS:
                    return True
    return False


def _redirects(tree):
    """返回 {CONST_NAME: min_lineno}，只收录右值指向临时目录的赋值。"""
    found = {}
    tnames = _temp_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if not _is_temp_expr(node.value, tnames):
                continue
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr in REQUIRED_REDIRECTS:
                    ln = node.lineno
                    if tgt.attr not in found or ln < found[tgt.attr]:
                        found[tgt.attr] = ln
                elif isinstance(tgt, ast.Name) and tgt.id in REQUIRED_REDIRECTS:
                    ln = node.lineno
                    if tgt.id not in found or ln < found[tgt.id]:
                        found[tgt.id] = ln
    return found


def _side_effect_calls(tree):
    """返回 [(lineno, func_name)]。"""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in SIDE_EFFECT_CALLS:
                out.append((node.lineno, name))
    return out


def scan_source(src, rel_path):
    """返回 (highs, exempted) 两个列表。"""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [(0, f"语法错误，无法扫描: {e}")], False

    if "ISOLATION_GUARD_OK" in src:
        return [], True
    if rel_path in WHITELIST:
        return [], True

    if not _imports_portfolio_core(tree):
        return [], False

    calls = _side_effect_calls(tree)
    if not calls:
        return [], False

    redirs = _redirects(tree)
    missing = [c for c in REQUIRED_REDIRECTS if c not in redirs]
    first_call_line = min(ln for ln, _ in calls)
    call_names = sorted({n for _, n in calls})

    highs = []
    if missing:
        highs.append((
            first_call_line,
            f"调用了 {','.join(call_names)} 但未把 {','.join(missing)} 重定向到临时目录"
            f" → 会写入生产账本（STATE_FILE/TRADES_FILE/EQUITY_FILE 三者必须全部重定向）",
        ))
    else:
        # 顺序检查：所有重定向必须早于第一次副作用调用
        # 注意：ast.Call 的 lineno 是函数名所在行，赋值行 push 上界需放宽 1 行
        late = [c for c, ln in redirs.items() if ln > first_call_line]
        if late:
            highs.append((
                first_call_line,
                f"重定向 {','.join(sorted(late))} 写在 {call_names} 调用之后 → 顺序错等于没重定向",
            ))
    return highs, False


def run_selftest():
    """对抗性自测：4 例应命中、4 例应放过。"""
    cases = [
        # --- 应命中 ---
        ("bad_no_redirect", '''
import analysis.portfolio_core as pc
pc.execute_trade(pos, sig, "2026-10-20", "macd", plan)
''', True),
        ("bad_partial_redirect", '''
import analysis.portfolio_core as pc
import tempfile, os
tmp = tempfile.mkdtemp()
pc.STATE_FILE = os.path.join(tmp, "s.json")
pc.TRADES_FILE = os.path.join(tmp, "t.json")
pc.execute_trade(pos, sig, "2026-10-20", "macd", plan)
''', True),
        ("bad_redirect_after_call", '''
import analysis.portfolio_core as pc
import tempfile, os
pc.execute_trade(pos, sig, "2026-10-20", "macd", plan)
tmp = tempfile.mkdtemp()
pc.STATE_FILE = os.path.join(tmp, "s.json")
pc.TRADES_FILE = os.path.join(tmp, "t.json")
pc.EQUITY_FILE = os.path.join(tmp, "e.csv")
''', True),
        ("bad_append_trade_only", '''
from analysis.portfolio_core import append_trade
append_trade({"date": "2026-10-20", "action": "BUY"})
''', True),
        # --- 应放过 ---
        ("ok_full_redirect", '''
import analysis.portfolio_core as pc
import tempfile, os
tmp = tempfile.mkdtemp()
pc.STATE_FILE = os.path.join(tmp, "s.json")
pc.TRADES_FILE = os.path.join(tmp, "t.json")
pc.EQUITY_FILE = os.path.join(tmp, "e.csv")
pc.execute_trade(pos, sig, "2026-10-20", "macd", plan)
''', False),
        ("ok_no_side_effect", '''
import analysis.portfolio_core as pc
print(pc.INITIAL_CAPITAL)
''', False),
        ("ok_unrelated_module", '''
import json
def f():
    json.dump({}, open("x.json", "w"))
''', False),
        ("ok_whitelisted_entry", '''
import tempfile
from analysis.portfolio_core import execute_trade
execute_trade(pos, sig, "2026-10-20", "macd", plan)
''', False),
    ]
    fails = 0
    for name, src, expect_hit in cases:
        rel = "analysis/portfolio_sim.py" if name == "ok_whitelisted_entry" else "analysis/tmp_case.py"
        highs, exempted = scan_source(src, rel)
        hit = bool(highs)
        ok = (hit == expect_hit)
        if not ok:
            fails += 1
        print(f"  {'✅' if ok else '❌'} {name}: 期望{'命中' if expect_hit else '放过'} "
              f"实际{'命中' if hit else '放过'}{' (豁免)' if exempted else ''}")
    print(f"\n自测结果: {'PASS' if fails == 0 else f'FAIL ({fails} 例不符)'}")
    return 0 if fails == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return run_selftest()

    highs = []
    n_files = 0
    n_exempt = 0
    for path in _iter_py_files():
        n_files += 1
        rel = os.path.relpath(path, WORKSPACE)
        try:
            with open(path, encoding="utf-8") as f:
                src = f.read()
        except Exception as e:
            print(f"⚠️ 读取失败 {rel}: {e}")
            continue
        file_highs, exempted = scan_source(src, rel)
        if exempted:
            n_exempt += 1
            continue
        for ln, msg in file_highs:
            highs.append((rel, ln, msg))

    print(f"扫描 {n_files} 个脚本，豁免 {n_exempt} 个")
    if highs:
        print(f"\n🔴 HIGH（测试未隔离，会污染生产账本）: {len(highs)}")
        for rel, ln, msg in highs:
            print(f"  {rel}:{ln} {msg}")
        return 1
    print("\n✅ 无 HIGH：所有副作用调用点均已隔离或位于生产入口白名单")
    return 0


if __name__ == "__main__":
    sys.exit(main())
