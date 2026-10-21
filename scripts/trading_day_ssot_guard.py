#!/usr/bin/env python3
"""交易日判定 SSOT 门禁（trading day SSOT guard）

缘起（2026-10-01 立项 / 2026-10-22 第五轮固化门禁）
--------------------------------------------------
2026-10-01 一天内 3 个文件踩同一个坑（trade_date 列是 date 对象、字符串匹配永假 →
节假日误放行），根因是 is_trading_day 三份实现互不复用。当日已把判定收敛到
analysis/service.is_trading_day（SSOT），但「禁止自持日历」只有文档层（TOOLS.md），
机器层为空 —— 本次固化。

检查项
------
H1. **重复实现**：`def is_trading_day(` 出现在 analysis/service.py 之外 → HIGH。
    （薄包装允许：包装应转调 service 版，如 verify_market_data.is_trading_day_check
    用了不同函数名，不会被 H1 命中。）
H2. **自持日历数据获取**：`tool_trade_date_hist_sina`（新浪交易日历接口）以
    Attribute/Name 形式出现在 analysis/service.py 之外 → HIGH。
    豁免：scripts/data_freshness_guard.py —— 静态 HOLIDAYS 表，有意离线设计
    （freshness 门禁不能依赖网络，TOOLS.md 已注明理由）。
    2026-10-22 起 service.get_trading_days() 提供日历**序列**接口，消费方一律改走
    service；本门禁防第二份获取路径回潮（首扫即抓出 chip_freshness.py 并已收敛）。

用 AST（Attribute.attr / Name.id）判定，注释/docstring/日志字符串里的提及不误报。

退出码：0 = 通过；1 = 命中 HIGH。
用法：
  python3 scripts/trading_day_ssot_guard.py            # 全扫 analysis/ + scripts/
  python3 scripts/trading_day_ssot_guard.py --selftest # 对抗性自测
"""
import argparse
import ast
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")

SSOT_FILE = os.path.join("analysis", "service.py")
SINA_CAL_API = "tool_trade_date_hist_sina"
# 豁免清单：文件相对路径 → 理由（必须在 TOOLS.md 有出处）
EXEMPT = {
    os.path.join("scripts", "data_freshness_guard.py"): "静态 HOLIDAYS 表，有意离线设计（freshness 门禁不能依赖网络）",
}


def scan_file(path, rel):
    """返回 HIGH 列表，元素形如 (line, rule, msg)。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError:
        return []
    if rel in EXEMPT:
        return []
    if rel == SSOT_FILE:
        # SSOT 本体自身是 is_trading_day / sina 日历的唯一合法持有者
        return []
    highs = []
    for node in ast.walk(tree):
        # H1: 重复实现判定逻辑
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "is_trading_day":
            highs.append((node.lineno, "H1", f"重复实现 is_trading_day()：交易日判定 SSOT 在 analysis/service.py，此处应改为 `from analysis.service import is_trading_day` 薄引用"))
        # H2: 自持日历数据获取（Attribute 形式 ak.tool_trade_date_hist_sina() / Name 形式）
        if isinstance(node, ast.Attribute) and node.attr == SINA_CAL_API:
            highs.append((node.lineno, "H2", f"自持日历获取 {SINA_CAL_API}()：应改走 service.get_trading_days() / is_trading_day()（SSOT）"))
        if isinstance(node, ast.Name) and node.id == SINA_CAL_API:
            highs.append((node.lineno, "H2", f"自持日历获取 {SINA_CAL_API}：应改走 service.get_trading_days() / is_trading_day()（SSOT）"))
    return highs


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
        # bad0: service 之外重复实现 is_trading_day
        "def is_trading_day(d):\n    return d.weekday() < 5\n",
        # bad1: 自持日历获取（Attribute 形式）
        "import akshare as ak\ndef f():\n    cal = ak.tool_trade_date_hist_sina()\n    return cal\n",
        # bad2: 自持日历获取（裸 Name 形式，如 from akshare import tool_trade_date_hist_sina 后直呼）
        "from akshare import tool_trade_date_hist_sina\ndef f():\n    return tool_trade_date_hist_sina()\n",
    ]
    good = [
        # good0: 薄引用 SSOT（合规消费方）
        "from analysis.service import is_trading_day\nprint(is_trading_day())\n",
        # good1: 薄包装但函数名不同（不撞 SSOT 名，如 verify_market_data.is_trading_day_check）
        "def is_trading_day_check(d=None):\n    from analysis.service import is_trading_day\n    return is_trading_day(d)\n",
        # good2: docstring/注释/日志字符串提及接口名 —— AST 不误报
        "import logging\ndef f():\n    \"\"\"替代 tool_trade_date_hist_sina 的说明\"\"\"\n    # 曾直调 ak.tool_trade_date_hist_sina()，已收敛\n    logging.warning(\"tool_trade_date_hist_sina 接口失败\")\n    return None\n",
    ]
    fails = []
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        for i, s in enumerate(bad):
            fp = os.path.join(td, f"bad{i}.py")
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write(s)
            if not scan_file(fp, os.path.basename(fp)):
                fails.append(f"漏报 bad#{i}")
        for i, s in enumerate(good):
            fp = os.path.join(td, f"good{i}.py")
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write(s)
            hits = scan_file(fp, os.path.basename(fp))
            if hits:
                fails.append(f"误报 good#{i}: {hits}")
        # 豁免文件必须放过（同名代码在豁免清单里不报）
        fp = os.path.join(td, "data_freshness_guard.py")
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(bad[0])
        if scan_file(fp, os.path.join("scripts", "data_freshness_guard.py")):
            fails.append("误报: 豁免文件 data_freshness_guard.py 被命中")
        # SSOT 本体必须放过（service.py 自身持有实现是合法的）
        fp = os.path.join(td, "service.py")
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(bad[0] + bad[1])
        if scan_file(fp, os.path.join("analysis", "service.py")):
            fails.append("误报: SSOT 本体 service.py 被命中")
    if fails:
        print("❌ selftest 失败：")
        for x in fails:
            print("  " + x)
        return 1
    print(f"✅ selftest 通过：违规 {len(bad)}/{len(bad)} 命中、合法 {len(good)}/{len(good)} 放过、豁免 1/1 生效")
    return 0


def main():
    ap = argparse.ArgumentParser(description="交易日判定 SSOT 门禁")
    ap.add_argument("--root", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    roots = [a.root] if a.root else [os.path.join(WORKSPACE, "analysis"), os.path.join(WORKSPACE, "scripts")]
    files = []
    for r in roots:
        files.extend(iter_py(r))
    highs = []
    for f in files:
        rel = os.path.relpath(f, WORKSPACE)
        for ln, rule, m in scan_file(f, rel):
            highs.append((rel, ln, rule, m))
    print(f"扫描 {len(files)} 个脚本")
    print(f"🔴 HIGH（交易日判定违反 SSOT）: {len(highs)}")
    for p, ln, rule, m in highs[:10]:
        print(f"  {p}:{ln} [{rule}] {m}")
    if highs:
        print("\n❌ 门禁失败（exit 1）：交易日判定/日历获取必须收敛到 analysis.service（SSOT）")
        return 1
    print("\n✅ 门禁通过（exit 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
