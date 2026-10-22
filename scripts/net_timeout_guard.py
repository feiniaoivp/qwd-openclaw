#!/usr/bin/env python3
"""网络调用缺 timeout 门禁（net timeout guard）

缘起（err.dist-io-no-timeout / 2026-10-22 第六轮固化门禁）
--------------------------------------------------------
网络调用超时铁律（TOOLS.md）：入口装 net_guard ≠ 全链路免疫，被循环/回测反复调用的
取数函数必须在函数体内自带超时 + 降级路径。实证挂死形态：sock_recv → internal_select
永久阻塞、CPU≈0、无产出，上层 cron 被 SIGKILL。

10-22 精确扫描：requests/httpx/curl_cffi.requests 的 get/post/... + urlopen +
Session 变量回溯，全仓 **0 处**缺 timeout（net_guard 全量接线后的成果）。
故本门禁为**直接 HIGH 型**（零存量，无棘轮需求），防回潮。

判定（精确，排除 dict.get 之类误报）：
- `<requests|httpx>.<get|post|put|delete|head|patch|request>` 缺 timeout kwarg
- `curl_cffi.requests.<method>`（Attribute 链末段 requests）同上
- `urlopen(...)`（urllib）缺 timeout
- 函数内 `s = requests.Session()`（含 curl_cffi.requests.Session）后 `s.<method>` 缺 timeout

退出码：0 = 通过；1 = 存在缺 timeout 的网络调用。
用法：
  python3 scripts/net_timeout_guard.py            # 全扫
  python3 scripts/net_timeout_guard.py --selftest # 对抗性自测
"""
import argparse
import ast
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
NET_OBJS = {"requests", "httpx"}
NET_METHODS = {"get", "post", "put", "delete", "head", "patch", "request"}


def _chain(node):
    """属性链文本：requests.get / curl_cffi.requests / s.get；非 Name/Attribute 返回 ''。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _chain(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


def _has_timeout(call):
    return any(kw.arg == "timeout" for kw in call.keywords)


def _is_net_target(value):
    """调用对象是否网络库：requests / httpx / *.requests（curl_cffi.requests）。"""
    s = _chain(value)
    if not s:
        return False
    parts = s.split(".")
    return parts[-1] in NET_OBJS


def _check_call(n, sess_vars):
    """返回 (kind, label) 或 None。kind ∈ {urlopen, net}。"""
    if not isinstance(n.func, ast.Attribute):
        return None
    attr = n.func.attr
    if attr == "urlopen" and not _has_timeout(n):
        return ("urlopen", "urlopen")
    if attr in NET_METHODS and not _has_timeout(n):
        v = n.func.value
        if _is_net_target(v):
            return ("net", f"{_chain(v)}.{attr}")
        if isinstance(v, ast.Name) and v.id in sess_vars:
            return ("net", f"{v.id}.{attr} [Session]")
    return None


def scan_file(path):
    """返回 (line, kind, label) 列表。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError:
        return []
def scan_file(path):
    """返回 (line, kind, label) 列表。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError:
        return []
    hits = []
    # 文件级统一收集 Session 变量（模块级 + 函数级；作用域不精确但保守方向正确）
    sess_vars = set()
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Attribute)
            and n.value.func.attr == "Session"
            and _is_net_target(n.value.func.value)
        ):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    sess_vars.add(t.id)
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            r = _check_call(n, sess_vars)
            if r:
                hits.append((n.lineno, *r))
    return hits


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
    import tempfile
    bad = [
        # requests.get 缺 timeout
        "import requests\nr = requests.get('https://x')\n",
        # httpx.post 缺 timeout
        "import httpx\nr = httpx.post('https://x', json={})\n",
        # curl_cffi.requests.get 缺 timeout
        "from curl_cffi import requests\nr = requests.get('https://x')\n",
        # urllib urlopen 缺 timeout
        "import urllib.request\nr = urllib.request.urlopen('https://x')\n",
        # Session 变量回溯
        "import requests\ns = requests.Session()\nr = s.get('https://x')\n",
    ]
    good = [
        # 带 timeout 的 requests.get
        "import requests\nr = requests.get('https://x', timeout=10)\n",
        # dict.get（非网络）→ 放过
        "d = {}\nv = d.get('k', 1)\n",
        # 带 timeout 的 urlopen
        "import urllib.request\nr = urllib.request.urlopen('https://x', timeout=5)\n",
        # 带 timeout 的 Session.get
        "import requests\ns = requests.Session()\nr = s.get('https://x', timeout=8)\n",
        # .get 出现在其他对象（pandas DataFrame.get）→ 放过
        "import pandas as pd\ndf = pd.DataFrame()\nv = df.get('col')\n",
    ]
    fails = []
    with tempfile.TemporaryDirectory() as td:
        for i, s in enumerate(bad):
            fp = os.path.join(td, f"bad{i}.py")
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write(s)
            if not scan_file(fp):
                fails.append(f"漏报 bad#{i}")
        for i, s in enumerate(good):
            fp = os.path.join(td, f"good{i}.py")
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write(s)
            if scan_file(fp):
                fails.append(f"误报 good#{i}: {scan_file(fp)}")
    if fails:
        print("❌ selftest 失败：")
        for x in fails:
            print("  " + x)
        return 1
    print(f"✅ selftest 通过：缺 timeout {len(bad)}/{len(bad)} 命中、合法 {len(good)}/{len(good)} 放过")
    return 0


def main():
    ap = argparse.ArgumentParser(description="网络调用缺 timeout 门禁")
    ap.add_argument("--root", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    roots = [a.root] if a.root else [os.path.join(WORKSPACE, "analysis"), os.path.join(WORKSPACE, "scripts")]
    files = []
    for r in roots:
        files.extend(iter_py(r))
    hits = []
    for f in files:
        rel = os.path.relpath(f, WORKSPACE)
        for ln, kind, label in scan_file(f):
            hits.append((rel, ln, kind, label))
    print(f"扫描 {len(files)} 个脚本")
    print(f"🔴 HIGH（网络调用缺 timeout）: {len(hits)}")
    for rel, ln, kind, label in hits[:15]:
        print(f"  {rel}:{ln}  [{kind}] {label}(...)")
    if hits:
        print("\n规约：所有网络调用必须显式 timeout；取数函数体内用 network_timeout_guard 包裹 + 降级路径。")
        print("详见 TOOLS.md「网络调用超时铁律」。")
        print("❌ 门禁失败（exit 1）")
        return 1
    print("\n✅ 门禁通过（exit 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
