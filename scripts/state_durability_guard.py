#!/usr/bin/env python3
"""状态文件耐久性子进程门禁（state durability guard）

缘起（2026-10-21 深究第四轮）
----------------------------
TOOLS.md / MEMORY.md 早已写明铁律：「任何『重写型』数据脚本必须有写前 .bak 备份
+ 行数防倒退检查」。但 10-21 全仓扫描：60 个含 json.dump 的脚本里**提到 .bak 的 0 个**，
4 个 os.replace 原子写、0 个临时文件写法。铁律只有文档层（第 1 层），机器层（第 2 层）为空。
实证受害者：`data/portfolio_equity.csv` 自 2026-09-23 起 22 个交易日 total_value / 持仓数
全部冻结不变（整文件重写 + 读失败退化为空列表的组合，会让历史行在异常路径下静默归零）。

检查项（每条都对应真实铁律，不是泛泛规范）
------------------------------------------
C. **清空式重写高危**：同一函数内先 `open(X,"w")` 之后才读取 `existing_rows` 的回退分支
   （读失败 → 空列表 → 写回只剩表头）。属"重写 + 退化路径"组合，直接可致数据全灭。
W1. **重写型无备份**：函数内出现 `open(<命名文件>, "w")` 但全文件无 `.bak` 字样 → 告警。
W2. **CSV/JSON 无原子写**：整文件重写且无 `os.replace` / 临时文件 → 告警（只报 WARN，
    因为大量脚本是纯产出物，重跑即可重建；状态类是重点）。

退出码：0 = 通过；1 = 命中 C（清空式重写高危）。
用法：
  python3 scripts/state_durability_guard.py            # 全扫 analysis/ + scripts/
  python3 scripts/state_durability_guard.py --selftest # 对抗性自测
"""
import argparse
import ast
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")


def _is_named_write_open(node):
    """open(<非空第1参数>, "w"...)：针对具名路径文件（排除 stdout 之类）。"""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"):
        return False
    if not node.args:
        return False
    first = node.args[0]
    looks_path = isinstance(first, (ast.Name, ast.Attribute, ast.Subscript, ast.JoinedStr, ast.Call))
    if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.startswith("<"):
        return False
    modes = list(node.args[1:]) + [k.value for k in node.keywords if k.arg == "mode"]
    for m in modes:
        if isinstance(m, ast.Constant) and isinstance(m.value, str) and m.value.startswith("w"):
            return looks_path
    return False


def _call_name(node):
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
    return ""


def scan_file(path):
    """返回 (critical, warns) 列表，元素形如 (line, msg)。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except (OSError, UnicodeDecodeError):
        return [], []
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError:
        return [], []
    has_bak = ".bak" in src
    has_atomic = "os.replace" in src or "NamedTemporaryFile" in src or "mkstemp" in src
    crit, warns = [], []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        body_nodes = sorted(
            [n for n in ast.walk(fn) if hasattr(n, "lineno")], key=lambda n: n.lineno
        )
        write_lines = [n.lineno for n in body_nodes if _is_named_write_open(n)]
        if not write_lines:
            continue
        first_write = write_lines[0]
        # C: 读回退在写之后（或在写之后才首读 existing_rows）
        reads_after = []
        for n in body_nodes:
            if n.lineno <= first_write:
                continue
            name = _call_name(n)
            if name in ("DictReader", "load", "read_csv", "read_text", "loads"):
                reads_after.append(n.lineno)
        assigns_empty = any(
            isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in ("existing_rows", "rows", "merged") for t in n.targets)
            and isinstance(n.value, ast.List)
            and n.lineno > first_write
            for n in body_nodes
        )
        if reads_after and (assigns_empty or len(reads_after) >= 2):
            crit.append((
                first_write,
                f"{fn.name}(): open(...,'w') 在行 {first_write} 已截断，但行 {reads_after[0]} 才读旧数据"
                f"{'（且失败分支赋空列表）' if assigns_empty else '（读与写顺序颠倒）'} → 清空式重写风险",
            ))
        # W1/W2
        if not has_bak:
            warns.append((first_write, f"{fn.name}(): 重写型写文件但全文件无 .bak 备份"))
        if not has_atomic and path.endswith(("portfolio_core.py",)):
            warns.append((first_write, f"{fn.name}(): 关键状态文件整文件重写且非原子（无 os.replace）"))
    return crit, warns


def iter_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in {".git", "__pycache__", "backups", "venv", ".venv", "node_modules", "data_cache"}
        ]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def selftest():
    bad = [
        # 写在前、读在后 + 空列表回退 = 清空式重写
        "import csv\ndef f(p):\n"
        "    with open(p, 'w') as g:\n        g.write('x')\n"
        "    try:\n        rows = list(csv.DictReader(open(p)))\n"
        "    except Exception:\n        rows = []\n    return rows\n",
    ]
    good = [
        # 先读后写（正确顺序）
        "import csv\ndef f(p):\n"
        "    rows = list(csv.DictReader(open(p)))\n"
        "    open(p, 'w').write('x')\n    return rows\n",
        # 只写不读
        "def f(p):\n    with open(p, 'w') as g:\n        g.write('x')\n",
    ]
    fails = []
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        for i, s in enumerate(bad):
            fp = os.path.join(td, f"bad{i}.py")
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write(s)
            c, _ = scan_file(fp)
            if not c:
                fails.append(f"漏报 bad#{i}")
        for i, s in enumerate(good):
            fp = os.path.join(td, f"good{i}.py")
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write(s)
            c, _ = scan_file(fp)
            if c:
                fails.append(f"误报 good#{i}: {c}")
    if fails:
        print("❌ selftest 失败：")
        for x in fails:
            print("  " + x)
        return 1
    print(f"✅ selftest 通过：高危 {len(bad)}/{len(bad)} 命中、合法 {len(good)}/{len(good)} 放过")
    return 0


def main():
    ap = argparse.ArgumentParser(description="状态文件耐久性门禁（重写/备份/原子写）")
    ap.add_argument("--root", default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    roots = [a.root] if a.root else [os.path.join(WORKSPACE, "analysis"), os.path.join(WORKSPACE, "scripts")]
    files = []
    for r in roots:
        files.extend(iter_py(r))
    crit, warn = [], []
    for f in files:
        c, w = scan_file(f)
        rel = os.path.relpath(f, WORKSPACE)
        crit.extend((rel, ln, m) for ln, m in c)
        warn.extend((rel, ln, m) for ln, m in w)
    print(f"扫描 {len(files)} 个脚本")
    print(f"🔴 HIGH（清空式重写）: {len(crit)}")
    for p, ln, m in crit[:10]:
        print(f"  {p}:{ln}  {m}")
    print(f"🟡 WARN（重写无备份/非原子）: {len(warn)}")
    for p, ln, m in warn[:6]:
        print(f"  {p}:{ln}  {m}")
    if crit:
        print("\n❌ 门禁失败（exit 1）：重写型脚本必须先读后写，失败分支不得以空列表继续写")
        return 1
    print("\n✅ 门禁通过（exit 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
