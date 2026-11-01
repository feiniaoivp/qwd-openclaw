#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_related_dedup.py — 条目块内重复 Related 子行合并（2026-11-01 专项处置）

背景：kb_linker.py 的幂等守卫曾失效（m.group(1) 只含标题行，永远查不到 Related:），
每轮 linker 运行向同一批条目追加一行 Related，重灾块堆到 13-15 行（全库 82 行）。
本脚本把每个条目块内的多条 `      - Related: [[a]], [[b]]...` 合并为一行
（目标按首次出现顺序并集、精确串去重），不碰 Details/Data/Insight 与 Insight 内联链接。

门禁（全过才落盘）：
  G1 Details/Data/Insight 行集合逐字节不变
  G2 每个 [[目标]] 精确串在备份中存在 → 新文件中仍存在（零断链）
  G3 行数严格下降
  G4 剔除被处理行后，其余行逐行不变

用法：默认 dry-run；--apply 落盘（先写 .bak-reldedup-<ts> 备份）。
退出码：0 = 通过；1 = 门禁失败或结构异常（unsafe）。
"""
import os
import re
import sys
import shutil
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
KB_PATH = os.path.join(WORKSPACE, "data", "news", "knowledge_base.md")

REL_LINE = re.compile(r"^(\s*)-\s*Related:\s*(.+)$")
SUB_LINE = re.compile(r"^\s*-\s*(Details|Data|Insight|Related)\s*:")
TITLE_LINE = re.compile(r"^\s*[•\-\*]\s+")
GROUP_LINE = re.compile(r"^\s*\*\*[^*]+\*\*\s*:\s*$")
TARGET = re.compile(r"\[\[([^\]]+)\]\]")


def scan_blocks(lines):
    """返回 (blocks, unsafe)。
    blocks = [ (first_rel_idx, [all_rel_idxs], ordered_targets) ]
    unsafe = [ (上下文描述, 残留文本样例) ]
    """
    blocks, unsafe = [], []
    cur_title, cur_rels, targets_seq = None, [], []

    def flush():
        nonlocal cur_title, cur_rels, targets_seq
        if cur_rels:
            resid = [r for (_, _, _, r) in cur_rels if r]
            if resid:
                unsafe.append((cur_title or "(无主)", resid))
            elif len(cur_rels) > 1:
                blocks.append((cur_rels[0][0], [x[0] for x in cur_rels], list(targets_seq)))
        cur_title, cur_rels, targets_seq = None, [], []

    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or GROUP_LINE.match(ln) or ln.lstrip().startswith("#"):
            flush()
            continue
        m = REL_LINE.match(ln)
        if m:
            ts = TARGET.findall(m.group(2))
            residual = TARGET.sub("", m.group(2)).replace(",", "").strip()
            if cur_title is None:
                unsafe.append(("(无主 Related 行)", [residual or m.group(2)[:40]]))
                continue
            cur_rels.append((i, m.group(1), ts, residual))
            for t in ts:
                if t not in targets_seq:
                    targets_seq.append(t)
            continue
        if SUB_LINE.match(ln):
            continue
        if TITLE_LINE.match(ln):
            flush()
            cur_title = s.lstrip("•-* ").strip()
    flush()
    return blocks, unsafe


def build_new_lines(lines, blocks):
    """块内多条 Related → 首位置一行合并（并集、首现顺序）。多块索引安全。
    返回 (out, merged_new_idx)：merged_new_idx = 新文件中合并行所在下标。"""
    replace_at = {}
    for first, idxs, targets in blocks:
        for i in idxs:
            replace_at[i] = None          # 待删除
        replace_at[first] = targets       # 首位置改为插入合并行
    out, merged_new_idx = [], []
    for i, ln in enumerate(lines):
        if i in replace_at:
            targets = replace_at[i]
            if targets is not None:
                indent = ln[: len(ln) - len(ln.lstrip())]
                out.append(indent + "- Related: " + ", ".join(f"[[{t}]]" for t in targets))
                merged_new_idx.append(len(out) - 1)
            continue
        out.append(ln)
    return out, merged_new_idx


def gates(old_lines, new_lines, blocks, merged_new_idx):
    ok = True
    ddl = lambda ls: [l for l in ls if re.match(r"^\s*-\s*(Details|Data|Insight)\s*:", l)]
    g1 = ddl(old_lines) == ddl(new_lines)
    print(f"G1 Details/Data/Insight 逐行不变: {'✅' if g1 else '❌'}")
    ok &= g1

    old_targets = set(TARGET.findall("\n".join(l for l in old_lines if REL_LINE.match(l))))
    new_targets = set(TARGET.findall("\n".join(l for l in new_lines if REL_LINE.match(l))))
    lost = old_targets - new_targets
    g2 = not lost
    print(f"G2 Related 目标零丢失: {'✅' if g2 else '❌ 丢失' + str(sorted(lost)[:5])}")
    ok &= g2

    g3 = len(new_lines) < len(old_lines)
    print(f"G3 行数下降: {len(old_lines)}→{len(new_lines)} {'✅' if g3 else '❌'}")
    ok &= g3

    drop_set = {i for b in blocks for i in b[1]}
    keep_old = [l for i, l in enumerate(old_lines) if i not in drop_set]
    new_wo_merge = [l for i, l in enumerate(new_lines) if i not in set(merged_new_idx)]
    g4 = keep_old == new_wo_merge
    print(f"G4 其余行逐行不变: {'✅' if g4 else '❌'}")
    ok &= g4
    return ok


def main():
    apply = "--apply" in sys.argv
    with open(KB_PATH, encoding="utf-8") as f:
        lines = f.read().splitlines()

    blocks, unsafe = scan_blocks(lines)
    n_drop = sum(len(b[1]) for b in blocks)
    print(f"多 Related 行块: {len(blocks)} | 可去除行: {n_drop} | unsafe(残留文本)块: {len(unsafe)}")
    for t, r in unsafe[:5]:
        print(f"  ⚠️ UNSAFE 跳过: {t} | {r[:2]}")
    for first, idxs, targets in blocks[:6]:
        title = "?"
        for j in range(first, max(-1, first - 12), -1):
            if TITLE_LINE.match(lines[j]) and not SUB_LINE.match(lines[j]):
                title = lines[j].strip().lstrip("•-* ").strip()
                break
        print(f"  🔗 {len(idxs)}行→1行: {title[:50]} (目标 {len(targets)} 个)")

    if unsafe:
        print("❌ 存在 unsafe 块（Related 行含非 [[..]] 残留文本），拒绝处理，先人工裁决。")
        return 1
    if not blocks:
        print("无可合并项。")
        return 0

    new_lines, merged_new_idx = build_new_lines(lines, blocks)
    if not gates(lines, new_lines, blocks, merged_new_idx):
        print("❌ 门禁未过，拒绝落盘。")
        return 1
    if not apply:
        print("[dry-run] 未写盘。加 --apply 落盘。")
        return 0

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = f"{KB_PATH}.bak-reldedup-{ts}"
    shutil.copy2(KB_PATH, bak)
    print(f"✅ 备份 {bak}")
    tmp = KB_PATH + ".dedup.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")
    os.replace(tmp, KB_PATH)
    print(f"✅ 已落盘: {KB_PATH} ({len(new_lines)} 行)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
