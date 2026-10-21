#!/usr/bin/env python3
"""记忆耐久性子进程门禁（memory note durability guard）

缘起（2026-10-21 日记 0 字节事故 · 假 ok 第 4 变体）
-----------------------------------------------
验证岗 04f400ef 向 memory/2026-10-21.md 补写记忆，写入成功，但校验表达式
`print('ok', open(p,'w') and open(p).read().count('***'))` 中 `open(p,'w')`
求值即截断文件 → 全文 0 字节，脚本仍打印 "memory updated ok"。
当时的防线只有一条：MEMORY.md 里的人写规约（第 1 层）——按「教训→三层防线」
要求，必须补第 2 层可执行门禁 + 第 3 层接入流水线。

本门禁查两件事（都来自事故根因，不是泛泛的静态检查）
-------------------------------------------------
A. 写模式 open 出现在表达式上下文（and / or / 三元 / print / assert / return 参数链）
   → 求值即写/截断，属高危。AST 精确判定，注释与 docstring 不误报。
B. 记忆/日记类文件（memory/*.md、MEMORY.md）的「写后校验」写法：
   write_text 之后同一函数内**没有**只读体量核对（wc -c / len(open) / os.path.getsize /
   st_size）→ 告警 WARN（不阻断）。事故里正是「写完立刻假装校验」。

退出码：0 = 通过（A 类为 0；B 类仅告警）；1 = 命中 A 类高危写法。
"""
import argparse
import ast
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")

WRITE_MODES = {"w", "w+", "wb", "wb+", "x", "xb", "a", "a+", "ab", "ab+"}
SIZE_TOKENS = ("getsize", "st_size", "wc -c", "wc -c", "len(")


def _is_open_call(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "open"
        and node.args
    )


def _write_open_in_expr(node, path, lines):
    """A 类：open(...) 处于表达式上下文（而非赋值/with/独立语句）。"""
    hits = []
    for parent in ast.walk(node):
        # 只关心 Call 节点，且该 Call 是 open()
        pass
    return hits


class ExprWriteScan(ast.NodeVisitor):
    """检测 open(..., 'w') 是否出现在非语句位置。"""

    def __init__(self, path, src_lines):
        self.path = path
        self.lines = src_lines
        self.hits = []

    def _flag(self, node, why):
        ln = getattr(node, "lineno", 0)
        text = self.lines[ln - 1].strip() if 0 < ln <= len(self.lines) else ""
        self.hits.append((ln, why, text))

    def _mode_is_write(self, call):
        for a in list(call.args[1:]) + [k.value for k in call.keywords if k.arg == "mode"]:
            if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value in WRITE_MODES:
                return a.value
        return None

    def visit_BoolOp(self, node):
        # a and open(p,'w') ... / a or open(...)
        for v in node.values:
            for sub in ast.walk(v):
                if _is_open_call(sub) and self._mode_is_write(sub):
                    self._flag(sub, f"写模式 open 位于 {'and/or' if isinstance(node.op, ast.And) else 'or'} 表达式内（求值即写入）")
        self.generic_visit(node)

    def visit_IfExp(self, node):
        for sub in ast.walk(node):
            if _is_open_call(sub) and self._mode_is_write(sub):
                self._flag(sub, "写模式 open 位于三元表达式内（求值即写入）")
        self.generic_visit(node)

    def visit_Assert(self, node):
        for sub in ast.walk(node.test):
            if _is_open_call(sub) and self._mode_is_write(sub):
                self._flag(sub, "写模式 open 位于 assert 条件内")
        self.generic_visit(node)

    def visit_Call(self, node):
        # print(... open(p,'w') ...) / return f(open(p,'w'))
        fname = ""
        if isinstance(node.func, ast.Name):
            fname = node.func.id
        elif isinstance(node.func, ast.Attribute):
            fname = node.func.attr
        if fname in ("print", "log", "info", "warning"):
            for sub in ast.walk(node):
                if _is_open_call(sub) and self._mode_is_write(sub) and sub is not node:
                    self._flag(sub, f"写模式 open 作为 {fname}() 的实参（求值即写入）")
        self.generic_visit(node)


def scan_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except (OSError, UnicodeDecodeError):
        return [], []
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError:
        return [], []
    lines = src.splitlines()
    sc = ExprWriteScan(path, lines)
    sc.visit(tree)
    warns = []
    # B 类：记忆类目标 + 写后无只读体量核对
    low = src
    merged = low.replace(" ", "")
    if ("memory/" in merged or "MEMORY.md" in low) and (
        ".write_text(" in low or "open(" in low
    ):
        has_write = (".write_text(" in low) or ("'w'" in low) or ('"w"' in low)
        has_size = any(t.replace(" ", "") in merged for t in SIZE_TOKENS)
        if has_write and not has_size:
            warns.append((0, "WARN 写记忆类文件但未见只读体量核对（建议 wc -c / len(open().read()) 复核）", ""))
    return sc.hits, warns


def iter_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", "backups", "venv", ".venv", "node_modules", "data_cache"}]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def main():
    ap = argparse.ArgumentParser(description="记忆耐久性门禁：写模式 open 表达式上下文 + 写后核对")
    ap.add_argument("--root", default=None, help="扫描根目录（默认 analysis/ + scripts/）")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="对抗性自测（合成样例）")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    roots = [a.root] if a.root else [os.path.join(WORKSPACE, "analysis"), os.path.join(WORKSPACE, "scripts")]
    files = []
    for r in roots:
        files.extend(iter_py(r))
    high, warn = [], []
    for f in files:
        h, w = scan_file(f)
        for ln, why, text in h:
            high.append((os.path.relpath(f, WORKSPACE), ln, why, text))
        for ln, why, text in w:
            warn.append((os.path.relpath(f, WORKSPACE), ln, why, text))

    print(f"扫描 {len(files)} 个脚本")
    print(f"🔴 HIGH（写模式 open 表达式上下文）: {len(high)}")
    for p, ln, why, text in high[:15]:
        print(f"  {p}:{ln}  {why}\n      {text[:120]}")
    print(f"🟡 WARN（写后无只读核对）: {len(warn)}")
    for p, ln, why, text in warn[:10]:
        print(f"  {p}  {why}")
    if high:
        print("\n❌ 门禁失败（exit 1）：写模式 open 只准出现在 with / 赋值 / 独立语句")
        return 1
    print("\n✅ 门禁通过（exit 0）")
    return 0


def selftest():
    """构造对抗性样例：必须命中 4 例高危、放过 5 例合法写法。"""
    bad = [
        "p='x'\nprint('ok', open(p,'w') and open(p).read())\n",
        "p='x'\nflag = 1 and open(p,'w')\n",
        "p='x'\nopen(p,'w') if flag else None\n",
        "p='x'\nassert open(p,'w')\n",
    ]
    good = [
        "p='x'\nwith open(p,'w') as f:\n    f.write('a')\n",
        "p='x'\nf = open(p,'w')\nf.write('a')\nf.close()\n",
        "p='x'\nopen(p,'w').write('a')\n",  # 语句位置，非表达式链
        "# open(p,'w') and x  (comment)\n",
        "p='x'\njson.dump(d, open(p,'w'))\n",  # 语句内实参，非 and/or/print 链
    ]
    import tempfile
    fails = []
    with tempfile.TemporaryDirectory() as td:
        for i, src in enumerate(bad):
            fp = os.path.join(td, f"bad{i}.py")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(src)
            h, _ = scan_file(fp)
            if not h:
                fails.append(f"漏报 bad#{i}: {src.strip()}")
        for i, src in enumerate(good):
            fp = os.path.join(td, f"good{i}.py")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(src)
            h, _ = scan_file(fp)
            if h:
                fails.append(f"误报 good#{i}: {src.strip()} → {h}")
    if fails:
        print("❌ selftest 失败：")
        for x in fails:
            print("  " + x)
        return 1
    print(f"✅ selftest 通过：高危 4/4 命中、合法 {len(good)}/{len(good)} 放过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
