#!/usr/bin/env python3
"""外部数据 astype(float) 裸转棘轮门禁（astype ratchet guard）

缘起（2026-10-08 事故 / 2026-10-22 第六轮固化门禁）
--------------------------------------------------
外部源（baostock/akshare/新浪）停牌或缺失日可能返回**空字符串 ''** 而非 NaN，
`astype(float)` 会直接 ValueError 崩掉整批扫描（2026-10-08 关注池 35 只全量扫描
在第 4 只票崩）。规约：统一 `pd.to_numeric(errors='coerce')` + `dropna()`。

10-22 盘点：全仓仍有 43 处 `.astype(float)` 系裸转（多为 `df["close"].astype(float)`，
对象正是外部数据列）。一次性全修范围过大且部分是纯内部合成数据（合法），
故采用 silent_except_guard 同款**棘轮模式**：
- 存量冻结：基线 Counter{file: count}，存量只报告；
- **新增即拦**：任一文件计数超过基线 → exit 1；
- 自动降基线：修复后计数下降，基线跟随收紧（只降不升）。

判定范围：`.astype(float)` / `.astype('float')` / `.astype("float64")` / `.astype(np.float64)`。
`.astype(str)` 不在内（10-01 教训的方向是转 str 统一匹配，属防御正确方向）。

退出码：0 = 无新增；1 = 新增裸转。
用法：
  python3 scripts/astype_ratchet_guard.py            # 棘轮检查
  python3 scripts/astype_ratchet_guard.py --selftest # 对抗性自测
"""
import argparse
import ast
import json
import os
import sys
from collections import Counter

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
BASELINE_PATH = os.path.join(WORKSPACE, "data", "astype_ratchet_baseline.json")

FLOAT_HINTS = {"float", "float64", "np.float64"}


def _astype_float_arg(call):
    """astype(float 系) 调用返回 True。"""
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "astype"):
        return False
    if not call.args:
        return False
    a = call.args[0]
    if isinstance(a, ast.Constant) and isinstance(a.value, str):
        return a.value.lower() in {"float", "float64"}
    if isinstance(a, ast.Constant) and a.value is float:
        return True
    if isinstance(a, ast.Attribute) and a.attr == "float64":
        # np.float64 / numpy.float64
        return True
    if isinstance(a, ast.Name) and a.id == "float":
        return True
    return False


def scan_file(path):
    """返回 float 裸转行号列表。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError:
        return []
    return [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call) and _astype_float_arg(n)]


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
        'import pandas as pd\ndf = pd.DataFrame()\nx = df["close"].astype(float)\n',
        "import pandas as pd\ndf = pd.DataFrame()\nx = df['close'].astype('float')\n",
        "import pandas as pd, numpy as np\ndf = pd.DataFrame()\nx = df['close'].astype(np.float64)\n",
    ]
    good = [
        # to_numeric 容错写法（规约推荐）→ 放过
        "import pandas as pd\ndf = pd.DataFrame()\nx = pd.to_numeric(df['close'], errors='coerce')\n",
        # astype(str)（10-01 教训方向）→ 放过
        "import pandas as pd\ndf = pd.DataFrame()\nx = df['d'].astype(str)\n",
        # astype(int) → 放过
        "import pandas as pd\ndf = pd.DataFrame()\nx = df['v'].astype(int)\n",
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
    print(f"✅ selftest 通过：裸转 {len(bad)}/{len(bad)} 命中、合法 {len(good)}/{len(good)} 放过")
    return 0


def main():
    ap = argparse.ArgumentParser(description="astype(float) 裸转棘轮门禁")
    ap.add_argument("--root", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    roots = [a.root] if a.root else [os.path.join(WORKSPACE, "analysis"), os.path.join(WORKSPACE, "scripts")]
    files = []
    for r in roots:
        files.extend(iter_py(r))
    current = Counter()
    detail = {}
    for f in files:
        lines = scan_file(f)
        if lines:
            rel = os.path.relpath(f, WORKSPACE)
            current[rel] = len(lines)
            detail[rel] = lines
    baseline = Counter()
    baseline_existed = os.path.exists(BASELINE_PATH)
    if baseline_existed:
        with open(BASELINE_PATH, "r", encoding="utf-8") as f:
            baseline = Counter(json.load(f))
    # 新增 = 超基线部分；首扫（基线不存在）不拦，只固化基线（存量冻结，silent_except_guard 同款）
    new_debt = (
        {k: current[k] - baseline.get(k, 0) for k in current if current[k] > baseline.get(k, 0)}
        if baseline_existed
        else {}
    )
    fixed = {k: v for k, v in baseline.items() if current.get(k, 0) < v}
    total = sum(current.values())
    print(f"扫描 {len(files)} 个脚本 | 存量裸转 {total} 处（基线 {'存在' if baseline_existed else '本次首次建立'}）")
    if fixed:
        print(f"🎉 较基线修复 {sum(fixed.values())} 处：{fixed}")
    if baseline_existed:
        for rel in sorted(current):
            print(f"  {rel}:{detail[rel]}")
    # 更新基线（只降不升：新增未通过时基线不写新值）
    os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
    if not new_debt:
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(dict(current), f, ensure_ascii=False, indent=1)
    if new_debt:
        print(f"\n🔴 新增 astype(float) 裸转 {sum(new_debt.values())} 处（外部数据空串会 ValueError 崩批）：")
        for rel, extra in sorted(new_debt.items()):
            print(f"  {rel} +{extra}（行 {detail[rel]}）")
        print("规约：改用 pd.to_numeric(errors='coerce') + dropna()；详见 TOOLS.md 配套铁律。")
        print("❌ 门禁失败（exit 1）")
        return 1
    if not baseline_existed:
        print(f"基线已固化：{BASELINE_PATH}（{total} 处存量冻结，新增将被拦截）")
    print("\n✅ 门禁通过（exit 0）：无新增裸转")
    return 0


if __name__ == "__main__":
    sys.exit(main())
