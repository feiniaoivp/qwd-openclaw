#!/usr/bin/env python3
"""重写型脚本备份保护棘轮门禁 (backup_guard.py)

背景（2026-10-25 首建 / 2026-10-31 用户批准棘轮化并接入周流水线）：
  铁律「重写型脚本必须有写前 .bak 备份 + 行数防倒退」（.learnings/ERRORS.md
  ERR-20261018-C07 / C01）已写入文档，但 2026-10-21 防线体检发现全仓
  60 个含 json.dump 的脚本落实数为 0 —— 只有文档层、缺门禁层。
  本门禁补齐第三层（接入流水线自动跑，不依赖人记得）。

检测口径（AST 静态秒级；范围 analysis/*.py + scripts/*.py 平铺，同首版）：
  - 触发点：json.dump(...) 调用、open(..., 'w') 调用；
  - 文件级宽启发豁免：源码（unparse）中出现 '.bak' / shutil.copy / 'backup'
    即视为有备份逻辑 —— 宁可放过交人工 review，不误伤；
  - 文件名含 fill_missing / builder 豁免（纯生成类）。

棘轮模式（同 silent_except_guard / astype_ratchet_guard 哲学）：
  - 首次运行（无基线）：固化当前存量进 data/backup_guard_baseline.json，exit 0；
  - 日常运行：任一文件违规数 > 基线 → exit 1（新增即拦）；
  - 自动降基线：无新增时基线跟随当前收紧（只降不升，修复即时兑现）；
  - 判定失败时**绝不写基线**（防「基线吞没」假 ok —— 10-31 silent_except_guard
    复跑洗白教训的同族预防）。
  基线随本门禁入 git，删除/篡改基线在 git status 可见。

退出码：0 = 无新增违规；1 = 基线外新增违规。
用法：
  python3 scripts/backup_guard.py                    # 棘轮检查
  python3 scripts/backup_guard.py --update-baseline  # 显式固化/重建基线（慎用）
  python3 scripts/backup_guard.py --selftest         # 对抗性自测
"""
import ast
import glob
import json
import os
import sys
from collections import Counter

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
BASELINE_PATH = os.path.join(WORKSPACE, "data", "backup_guard_baseline.json")

FILENAME_EXEMPT = ("fill_missing", "builder")


def scan_file(filepath):
    """返回违规点列表（'L<行号>: <描述>'）。语法错误 → 空列表 + stderr 提醒。"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            src = f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"⚠️ [backup_guard] 无法读取 {filepath}: {e}", file=sys.stderr)
        return []
    try:
        tree = ast.parse(src, filename=filepath)
    except SyntaxError as e:
        print(f"⚠️ [backup_guard] 语法错误跳过 {filepath}: {e}", file=sys.stderr)
        return []

    # 文件级宽启发：整份源码里出现过备份关键字即放行（同首版口径，勿收窄否则基线失真）
    unparsed = ast.unparse(tree)
    if ".bak" in unparsed or "shutil.copy" in unparsed or "backup" in unparsed.lower():
        return []

    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # 1) json.dump(...)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "dump":
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "json":
                violations.append(f"L{node.lineno}: json.dump 缺少备份保护")
        # 2) open(..., 'w')
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            if len(node.args) >= 2:
                mode = node.args[1]
                if isinstance(mode, ast.Constant) and mode.value == "w":
                    violations.append(f"L{node.lineno}: open(..., 'w') 缺少备份保护")
    return violations


def iter_scope():
    files = sorted(
        glob.glob(os.path.join(WORKSPACE, "analysis", "*.py"))
        + glob.glob(os.path.join(WORKSPACE, "scripts", "*.py"))
    )
    for f in files:
        if any(k in f for k in FILENAME_EXEMPT):
            continue
        yield f


def diff_counts(baseline, current):
    """纯函数（供自测）：返回 (new_debt, fixed) 文件名列表。
    new_debt = 当前计数超过基线；fixed = 基线计数未被当前完全覆盖（含文件消失）。"""
    new = sorted(r for r, c in current.items() if c > baseline.get(r, 0))
    fixed = sorted(r for r, b in baseline.items() if current.get(r, 0) < b)
    return new, fixed


def load_baseline():
    if not os.path.exists(BASELINE_PATH):
        return None
    try:
        with open(BASELINE_PATH, "r", encoding="utf-8") as f:
            return {k: int(v) for k, v in json.load(f).get("file_counts", {}).items()}
    except Exception as e:  # 基线损坏必须可见，绝不静默当空基线（防吞没）
        print(f"❌ [backup_guard] 基线读取失败（拒绝误判，请人工检查 {BASELINE_PATH}）: {e}")
        sys.exit(2)


def save_baseline(counts):
    os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
    tmp = BASELINE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            {
                "updated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                "note": "重写型脚本缺备份保护已知存量（棘轮基线，只降不升；修复后自动收紧）",
                "file_counts": {k: v for k, v in sorted(counts.items()) if v > 0},
            },
            f,
            ensure_ascii=False,
            indent=1,
        )
    os.replace(tmp, BASELINE_PATH)


def selftest():
    import tempfile

    cases = [
        # (源码, 期望违规数>0 ?)
        ('import json\nd={"a":1}\njson.dump(d, open("x.json","w"))\n', True),
        ('open("out.txt", "w").write("hi")\n', True),
        # 有备份关键字（宽启发）→ 放过。注意：ast.unparse 会剥注释，
        # 故注释里的 .bak 不算；字符串常量/标识符里的才算（首版口径）
        ('import json, shutil\nshutil.copy("x.json","x.json.bak")\njson.dump({}, open("x.json","w"))\n', False),
        ('BACKUP_HINT = ".bak"  # 写前先落备份\nopen("f.txt","w").write("v")\n', False),
        # 非 'w' 模式 / 非 json.dump → 放过
        ('open("f.txt","a").write("v")\n', False),
        ('import json\njson.dumps({}, indent=2)\n', False),
    ]
    fails = []
    with tempfile.TemporaryDirectory() as td:
        for i, (src, expect_hit) in enumerate(cases):
            fp = os.path.join(td, f"case{i}.py")
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write(src)
            got = scan_file(fp)
            if expect_hit and not got:
                fails.append(f"漏报 case#{i}")
            if not expect_hit and got:
                fails.append(f"误报 case#{i}: {got}")
        # diff_counts 棘轮纯函数
        d = diff_counts  # 便于断言书写
        if d({"a": 1}, {"a": 1}) != ([], []):
            fails.append("ratchet: 持平误判")
        if d({"a": 1}, {"a": 2}) != (["a"], []):
            fails.append("ratchet: 新增未拦截")
        if d({"a": 2}, {"a": 1}) != ([], ["a"]):
            fails.append("ratchet: 修复未降基线")
        if d({}, {"a": 1}) != (["a"], []):
            fails.append("ratchet: 新文件违规未拦截")
        if d({"a": 2}, {}) != ([], ["a"]):
            fails.append("ratchet: 文件删除未判 fixed")
    if fails:
        print("❌ [backup_guard] selftest 失败：")
        for x in fails:
            print("  " + x)
        return 1
    print(f"✅ [backup_guard] selftest 通过：扫描 {len(cases)}/{len(cases)} 判定正确 + 棘轮 5 项正确")
    return 0


def main():
    if "--selftest" in sys.argv:
        return selftest()

    current = Counter()
    details = {}
    for fp in iter_scope():
        v = scan_file(fp)
        if v:
            rel = os.path.relpath(fp, WORKSPACE)
            current[rel] = len(v)
            details[rel] = v

    baseline = load_baseline()
    new, fixed = diff_counts(baseline or {}, dict(current))

    total_current = sum(current.values())
    total_new = sum(current[r] for r in new)
    print(f"=== 备份保护棘轮门禁 | 扫描脚本 {sum(1 for _ in iter_scope())} 个 | 违规 {total_current} 点/{len(current)} 文件 | 基线 {sum((baseline or {}).values())} 点 ===")

    if baseline is None:
        save_baseline(dict(current))
        print(f"📝 首次运行：基线已固化 {total_current} 点/{len(current)} 文件（存量冻结，此后新增即拦）")
        return 0

    for r in new:
        print(f"  ❌ 新增: {r} +{current[r] - baseline.get(r, 0)}（现 {current[r]} 点，行 {', '.join(details[r])}）")
    known = [r for r in current if r not in new and baseline.get(r, 0) > 0]
    if known:
        print(f"  ⚠️ 存量 {len(known)} 文件 {sum(current[r] for r in known)} 点（冻结中，修复后自动降基线）: " +
              ", ".join(f"{r}({current[r]})" for r in sorted(known)[:15]) +
              (" ..." if len(known) > 15 else ""))
    for r in fixed:
        print(f"  ✅ 基线下降: {r} {baseline[r]}→{current.get(r, 0)} 点")

    if new:
        print(f"\n🚨 基线外新增 {len(new)} 文件/{total_new} 点「重写型写点无备份保护」（写前 .bak 教训复发）")
        print("规约：重写前 shutil.copy / *.bak 副本 + 行数防倒退检查；详见 AGENTS.md 门禁清单。")
        return 1

    if current != baseline:
        save_baseline(dict(current))  # 只会吸收下降（无新增时 current ≤ baseline 逐文件成立）
    if not current:
        print("✅ [backup_guard] 所有重写脚本均已包含备份逻辑")
    return 0


if __name__ == "__main__":
    sys.exit(main())
