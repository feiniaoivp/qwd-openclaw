#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_append_guard.py — KB 收口入库前置守卫（2026-11-01 专项处置产物）

背景：收口日例行简报入库由 agent 会话手工 append，历史行为不查重 → 同一标题
反复以裸 stub 形式堆进索引区（10-31 专项清理时存量 182 组）。本守卫在入库前
剥离「KB 已存在（归一化同串）」的标题行及其 Raw 续行，并清理失去全部子行的
分组头，从源头阻断 bare 重复的制造。

用法：
  python3 scripts/kb_append_guard.py --check <候选文件>      # 只报告，exit 1=有重复
  python3 scripts/kb_append_guard.py --strip <候选文件>      # 输出清理后文本（stdout）
  python3 scripts/kb_append_guard.py --strip <候选> --out <文件>
  python3 scripts/kb_append_guard.py --selftest

判定口径：
  - 标题行 = 弹符行（•/-/*）且非 Details/Data/Insight/Related 子行、非分组头（**X**:）、
    非 # 标题、非 📰/抓取到 简报统计行
  - 重复 = 标题归一化（全半角/空格/행동→行动，同 kb_writer_v2._norm_title）后
    与 KB 现存任一标题同串（保守：不做变体合并判断，变体留给人工）
  - 剥离标题时连带剥离其 Raw 续行（到下一标题/分组头/子行标签为止的非子行文本）
  - 分组头（**X**:）下若无任何存活子行则整头剥离
退出码：0 = 正常；1 = 参数/结构错误；--check 模式下 2 = 发现重复（供脚本判别）。
"""
import os
import re
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
KB_PATH = os.path.join(WORKSPACE, "data", "news", "knowledge_base.md")

SUB_LINE = re.compile(r"^\s*-\s*(Details|Data|Insight|Related)\s*:")
TITLE_LINE = re.compile(r"^(\s*)([•\-\*])\s+(.+)$")
GROUP_LINE = re.compile(r"^\s*\*\*[^*]+\*\*\s*:\s*$")
HEADER_LINE = re.compile(r"^#{1,6}\s")
STAT_LINE = re.compile(r"📰|抓取到|新闻速览|[:：]\s*\d+\s*条")  # 后者=简报计数行（与 kb_repair_v1.STAT_RE 同口径）
GARBAGE_FIX = str.maketrans({"(": "(", ")": ")", "（": "(", "）": ")", " ": "", "\u3000": ""})


def norm_title(t: str) -> str:
    t = t.replace("행동", "行动").replace("行動", "行动")
    return t.strip().strip("*").strip().translate(GARBAGE_FIX)


def is_title_line(ln: str) -> bool:
    if SUB_LINE.match(ln) or GROUP_LINE.match(ln) or HEADER_LINE.match(ln):
        return False
    if STAT_LINE.search(ln):
        return False
    return bool(TITLE_LINE.match(ln))


def kb_title_set(kb_path: str) -> set:
    ts = set()
    with open(kb_path, encoding="utf-8") as f:
        for ln in f:
            if is_title_line(ln):
                m = TITLE_LINE.match(ln)
                ts.add(norm_title(m.group(3)))
    return ts


def strip_candidate(lines, existing: set):
    """返回 (new_lines, stripped_report)。stripped_report = [(行号1-based, 标题, 原因)]
    2026-11-08: existing 内部拷贝 + 新保留标题回填 —— 同块内同串重复也剥（原先只查 KB 存量，
    同一候选内两次出现的同串会双写）"""
    existing = set(existing)
    out, report = [], []
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        if is_title_line(ln):
            m = TITLE_LINE.match(ln)
            nt = norm_title(m.group(3))
            if nt in existing:
                report.append((i + 1, m.group(3).strip()[:60], "KB已存在"))
                i += 1
                # 连带剥离 Raw 续行（非子行、非标题、非分组头/空行的挂靠文本）
                while i < n:
                    nxt = lines[i]
                    if (not nxt.strip() or is_title_line(nxt) or SUB_LINE.match(nxt)
                            or GROUP_LINE.match(nxt) or HEADER_LINE.match(nxt)):
                        break
                    report.append((i + 1, nxt.strip()[:60], "Raw续行随剥离"))
                    i += 1
                continue
            out.append(ln)
            existing.add(nt)  # 块内累积：同块后现同串也会被剥
            i += 1
            continue
        out.append(ln)
        i += 1

    # 二次清理：分组头下无存活子行 → 整头剥离
    cleaned, report2 = [], []
    j = 0
    while j < len(out):
        ln = out[j]
        if GROUP_LINE.match(ln):
            k = j + 1
            has_child = False
            while k < len(out):
                if GROUP_LINE.match(out[k]) or HEADER_LINE.match(out[k]) or not out[k].strip():
                    break
                if is_title_line(out[k]) or SUB_LINE.match(out[k]):
                    has_child = True
                    break
                k += 1
            if has_child:
                cleaned.append(ln)
            else:
                report2.append((j + 1, ln.strip()[:60], "分组头无存活子行"))
            j += 1
            continue
        cleaned.append(ln)
        j += 1
    return cleaned, report + report2


def _selftest():
    kb_fixture = [
        "• 旧标题甲",
        "      - Details: 内容",
        "• 旧标题乙",
        "## 2026-10-30 盘前",
        "  • **甲股** 甲股:旧公告 [三季度报告全文]",
    ]
    cand = [
        "## 2026-11-01 盘后",
        "📢 **关注股公告**",
        "  • **甲股** 甲股:旧公告 [三季度报告全文]",      # KB 已存在 → 剥
        "  • **乙股** 乙股:新公告 [关联交易]",            # 新 → 留
        "  **丙股(000000)**:",
        "    - 旧标题甲",                                  # KB 已存在 → 剥
        "    - 丙股全新利好",                              # 新 → 留
        "  **丁股(111111)**:",
        "    - 旧标题乙",                                  # 唯一子行被剥 → 整头剥
        "",
        "  • 某统计行不属于标题（📰 出现在别处不影响）",
    ]
    import tempfile
    tmp = tempfile.mkdtemp()
    kbp = os.path.join(tmp, "kb.md")
    open(kbp, "w", encoding="utf-8").write("\n".join(kb_fixture) + "\n")
    existing = kb_title_set(kbp)
    new_lines, report = strip_candidate(cand, existing)
    text = "\n".join(new_lines)
    errs = []
    if "旧公告" in text:
        errs.append("旧公告未剥离")
    if "新公告" not in text:
        errs.append("新公告被误剥")
    if "丙股全新利好" not in text:
        errs.append("丙股新行被误剥")
    if "丁股" in text:
        errs.append("空分组头丁股未剥离")
    if "丙股(000000)" not in text.replace("（", "("):
        errs.append("仍有子行的分组头丙股被误剥")
    if "## 2026-11-01 盘后" not in text:
        errs.append("段头被误剥")
    n_strip = sum(1 for _, _, why in report if why == "KB已存在")
    if n_strip != 3:
        errs.append(f"剥离标题数应=3（含公告1+索引2）实际={n_strip}")
    if errs:
        print("❌ kb_append_guard selftest FAILED:")
        for e in errs:
            print("  -", e)
        return 1
    print(f"✅ kb_append_guard selftest 通过（剥旧留新/空组头清理/段头保护/report={len(report)}项）")
    return 0


def main():
    if "--selftest" in sys.argv:
        return _selftest()
    args = [a for a in sys.argv[1:]]
    mode = None
    path = None
    out_path = None
    it = iter(args)
    for a in it:
        if a in ("--check", "--strip"):
            mode = a
        elif a == "--out":
            out_path = next(it, None)
        elif a == "--kb":
            global KB_PATH
            KB_PATH = next(it, KB_PATH)
        elif path is None:
            path = a
    if mode is None or path is None or not os.path.exists(path):
        print(__doc__)
        return 1
    existing = kb_title_set(KB_PATH)
    with open(path, encoding="utf-8") as f:
        cand_lines = f.read().splitlines()
    new_lines, report = strip_candidate(cand_lines, existing)
    n_dup = sum(1 for _, _, w in report if w == "KB已存在")
    print(f"候选 {len(cand_lines)} 行 → 清理后 {len(new_lines)} 行 | 剥离重复标题 {n_dup} | 明细 {len(report)} 项")
    for ln_no, t, why in report[:15]:
        print(f"  ✂️  L{ln_no} [{why}] {t}")
    if len(report) > 15:
        print(f"  ... 共 {len(report)} 项")
    if mode == "--check":
        return 2 if n_dup else 0
    text = "\n".join(new_lines) + "\n"
    if out_path:
        # 写前 .bak 备份（backup_guard 铁律；out 文件可能指向已有文件）
        if os.path.exists(out_path):
            import shutil
            shutil.copy2(out_path, out_path + ".bak")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"✅ 已写 {out_path}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
