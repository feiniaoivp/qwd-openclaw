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
C. **清空式重写高危**：同一函数内先 `open(X,"w")` 截断 X，之后才读取**同一个目标 X**
   的旧数据（读失败 → 空列表 → 写回只剩表头）。属"重写 + 退化路径"组合，直接可致数据全灭。
   （2026-10-22 第五轮升级：引入路径指纹判别——写目标与后读目标**可证明不同**的读不计入，
   消除「写产物 A 后读输入 B」类假阳性，从而具备接入流水线资格。）
W1. **重写型无备份**：函数内出现 `open(<命名文件>, "w")` 但全文件无 `.bak` 字样 → 告警。
W2. **CSV/JSON 无原子写**：整文件重写且无 `os.replace` / 临时文件 → 告警（只报 WARN，
    因为大量脚本是纯产出物，重跑即可重建；状态类是重点）。

路径指纹（第五轮）
------------------
`_resolve_shape()` 把路径表达式解析成可比较指纹：
  Constant 字符串 → ("const", v)；Name 变量 → 追踪函数内赋值表（深度 ≤3）；
  f-string → ("fstr", 首个字面段)；`open(X, ...)` 调用 → 直接取 X 的指纹；
  os.path.join(...) / 属性 / 下标 → 结构化组合。
比较时先 `_unwrap()` 剥掉 `.read_text()` 之类方法链包装；**任一方不可判定 → 保守视为同
目标（保留告警）**；双方均可判定且不等 → 判定为不同文件，不计入 C。

退出码：0 = 通过；1 = 命中 C（清空式重写高危）。
用法：
  python3 scripts/state_durability_guard.py            # 全扫 analysis/ + scripts/
  python3 scripts/state_durability_guard.py --selftest # 对抗性自测（含假阳性对抗样例）
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


# ---------------- 路径指纹（2026-10-22 第五轮） ----------------

def _resolve_shape(expr, varmap, depth=0):
    """把路径表达式解析成可比较指纹；None = 不可判定。"""
    if depth > 3 or expr is None:
        return None
    if isinstance(expr, ast.Constant):
        if isinstance(expr.value, str):
            return ("const", expr.value)
        return None
    if isinstance(expr, ast.Name):
        if expr.id in varmap:
            return _resolve_shape(varmap[expr.id], varmap, depth + 1)
        return ("name", expr.id)
    if isinstance(expr, ast.Attribute):
        return ("attr", _resolve_shape(expr.value, varmap, depth + 1))
    if isinstance(expr, ast.Subscript):
        return ("sub", _resolve_shape(expr.value, varmap, depth + 1))
    if isinstance(expr, ast.JoinedStr):
        parts = [v.value for v in expr.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        return ("fstr", parts[0] if parts else "")
    if isinstance(expr, ast.Call):
        fname = _call_name(expr)
        if fname == "open" and expr.args:
            # open(X, 'r') 的路径就是 X
            return _resolve_shape(expr.args[0], varmap, depth + 1)
        args = tuple(_resolve_shape(x, varmap, depth + 1) for x in expr.args)
        return ("call", fname, args)
    return None


def _unwrap(shape):
    """剥掉方法链/下标包装：open(p).read_text() → p 的指纹。"""
    while isinstance(shape, tuple) and shape and shape[0] in ("attr", "sub") and len(shape) >= 2:
        shape = shape[1]
    return shape


def _same_path(a, b):
    """两个路径指纹是否指向同一目标。任一方不可判定 → 保守 True（保留告警）。"""
    a, b = _unwrap(a), _unwrap(b)
    if a is None or b is None:
        return True
    return a == b


def _build_varmap(fn):
    """函数内 Name → 赋值右值（按行序取最后一次），供路径指纹回溯。"""
    varmap = {}
    nodes = sorted([n for n in ast.walk(fn) if hasattr(n, "lineno")], key=lambda n: n.lineno)
    for n in nodes:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            varmap[n.targets[0].id] = n.value
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
            varmap[n.target.id] = n.value
    return varmap


_READ_FUNCS = {"DictReader", "load", "read_csv", "read_text", "loads"}


def _read_target_expr(n):
    """提取读调用的路径表达式；方法链形式 open(X).read_text() 取 open 的第一参。"""
    if _call_name(n) not in _READ_FUNCS:
        return None
    if n.args:
        return n.args[0]
    if isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Call) and n.func.value.args:
        return n.func.value.args[0]
    return None


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
        write_calls = [n for n in body_nodes if _is_named_write_open(n)]
        if not write_calls:
            continue
        varmap = _build_varmap(fn)
        # 收集读调用：行号 + 路径指纹
        read_calls = []
        for n in body_nodes:
            tgt = _read_target_expr(n)
            if tgt is not None:
                read_calls.append((n.lineno, _resolve_shape(tgt, varmap)))
        for w in write_calls:
            w_line = w.lineno
            w_shape = _resolve_shape(w.args[0], varmap)
            # C: 写之后才读「同一目标」的旧数据
            reads_after = [ln for ln, rshape in read_calls if ln > w_line and _same_path(rshape, w_shape)]
            assigns_empty = any(
                isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id in ("existing_rows", "rows", "merged") for t in n.targets)
                and isinstance(n.value, ast.List)
                and n.lineno > w_line
                for n in body_nodes
            )
            if reads_after and (assigns_empty or len(reads_after) >= 2):
                crit.append((
                    w_line,
                    f"{fn.name}(): open(...,'w') 在行 {w_line} 已截断，但行 {reads_after[0]} 才读同一目标的旧数据"
                    f"{'（且失败分支赋空列表）' if assigns_empty else '（读与写顺序颠倒）'} → 清空式重写风险",
                ))
        # W1/W2（沿用第四轮口径）
        if not has_bak:
            warns.append((write_calls[0].lineno, f"{fn.name}(): 重写型写文件但全文件无 .bak 备份"))
        if not has_atomic and path.endswith(("portfolio_core.py",)):
            warns.append((write_calls[0].lineno, f"{fn.name}(): 关键状态文件整文件重写且非原子（无 os.replace）"))
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
        # bad0: 写在前、读同一 const 路径在后 + 空列表回退 = 清空式重写
        "import csv\ndef f(p):\n"
        "    with open(p, 'w') as g:\n        g.write('x')\n"
        "    try:\n        rows = list(csv.DictReader(open(p)))\n"
        "    except Exception:\n        rows = []\n    return rows\n",
        # bad1: 写目标与读目标是不同变量但赋值指向同一路径（varmap 必须穿透）
        "import json\ndef f():\n"
        "    out = 'state.json'\n    with open(out, 'w') as g:\n        g.write('x')\n"
        "    inp = 'state.json'\n    try:\n        rows = json.load(open(inp))\n    except Exception:\n        rows = []\n    return rows\n",
        # bad2: 读经方法链 open(p).read_text()，_unwrap 必须还原同目标
        "def f(p):\n"
        "    with open(p, 'w') as g:\n        g.write('x')\n"
        "    try:\n        s = open(p).read_text()\n        s2 = open(p).read_text()\n    except Exception:\n        s = ''\n    return s\n",
    ]
    good = [
        # good0: 先读后写（正确顺序）
        "import csv\ndef f(p):\n"
        "    rows = list(csv.DictReader(open(p)))\n"
        "    open(p, 'w').write('x')\n    return rows\n",
        # good1: 只写不读
        "def f(p):\n    with open(p, 'w') as g:\n        g.write('x')\n",
        # good2: 【假阳性对抗】写当日产物 A，之后读输入 B —— 可证明不同目标，必须放过
        #        （2026-10-21 实扫误报：validate_strategies.py 写 validation_{today}.json 后读
        #         adaptive_params.json；adaptive_dual.py 写 arbitration_{today}.json 后读
        #         portfolio_sim_state.json）
        "import json, os\ndef f(WS, today):\n"
        "    outfile = os.path.join(WS, 'analysis', f'validation_{today}.json')\n"
        "    with open(outfile, 'w', encoding='utf-8') as fh:\n        json.dump({}, fh)\n"
        "    params_file = os.path.join(WS, 'data', 'adaptive_params.json')\n"
        "    if os.path.exists(params_file):\n"
        "        try:\n            with open(params_file, 'r', encoding='utf-8') as fh:\n                data = json.load(fh)\n"
        "        except Exception:\n            data = {}\n    return data\n",
        # good3: 【假阳性对抗】写 var A、读 var B，两变量指向不同 const 路径
        "import json\ndef f():\n"
        "    outp = 'result.json'\n    with open(outp, 'w') as g:\n        json.dump({}, g)\n"
        "    inp = 'input_params.json'\n"
        "    try:\n        rows = json.load(open(inp))\n    except Exception:\n        rows = []\n    return rows\n",
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
    print(f"✅ selftest 通过：高危 {len(bad)}/{len(bad)} 命中、合法 {len(good)}/{len(good)} 放过（含 2 例假阳性对抗）")
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
    print(f"🔴 HIGH（清空式重写·同目标先写后读）: {len(crit)}")
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
