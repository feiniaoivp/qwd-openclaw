#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_variant_merge.py — 同事件标题变体合并 (2026-10-26) v2
索引制删除（不依赖 id()），逐行门禁。
"""
import os
import re
import sys
import shutil

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "scripts"))
from kb_repair_v1 import parse_blocks, entry_richness  # noqa: E402

KB_PATH = os.path.join(WORKSPACE, "data", "news", "knowledge_base.md")

_CLAUSE_RE = re.compile(r"(同比较去年同期|较去年同期|同比增长[0-9%.．]+|同比[增下]涨?[0-9%.．]*)")
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+")

def variant_key(title: str) -> str:
    t = re.sub(r"^(机构风向标\s*\|?\s*)", "", title)
    t = _CLAUSE_RE.sub("", t)
    return _PUNCT_RE.sub("", t)

def main():
    apply = "--apply" in sys.argv
    with open(KB_PATH, encoding="utf-8") as f:
        lines = f.read().splitlines()
    blocks = parse_blocks(lines)

    # 索引制：entry_blocks = [(block_index, block)]
    entry_idx = [i for i, b in enumerate(blocks) if b["kind"] == "entry"]
    groups = {}
    for n, i in enumerate(entry_idx):
        groups.setdefault(variant_key(blocks[i]["title"]), []).append(n)

    drop_block_idx = set()   # 要删除的 block 索引
    merge_report = []
    for k, nums in groups.items():
        if len(nums) < 2:
            continue
        members = [(n, blocks[entry_idx[n]]) for n in nums]
        rich = [(n, b) for n, b in members if entry_richness(b) > 0]
        bare = [(n, b) for n, b in members if entry_richness(b) == 0]
        if len(rich) == 1:
            keep_n, keep_b = rich[0]
            for n, b in bare:
                drop_block_idx.add(entry_idx[n])
                merge_report.append((b["title"], keep_b["title"]))
        elif len(rich) == 0:
            keep_n, keep_b = members[0]
            for n, b in members[1:]:
                drop_block_idx.add(entry_idx[n])
                merge_report.append((b["title"], keep_b["title"]))
        # 多 rich → 不动，打印
        if len(rich) > 1:
            print(f"  ⚠️ 多rich组保留不动: {[b['title'][:36] for _, b in rich]}")

    print(f"可自动合并组: {len(merge_report)} 组 / 删除裸变体 {len(drop_block_idx)} 行")
    for t, k in merge_report[:15]:
        print(f"  ✂️  {t[:52]} →→ {k[:36]}")
    if len(merge_report) > 15:
        print(f"  ... 共 {len(merge_report)} 组")

    new_lines = []
    for i, b in enumerate(blocks):
        if i in drop_block_idx:
            continue
        if b["kind"] == "entry":
            new_lines.append(f"{' ' * b['indent']}{b['bullet']} {b['title']}")
            for kk, tt in b["sublines"]:
                new_lines.append(f"      - {kk}: {tt}" if kk != "Raw" else f"      {tt}")
        else:
            new_lines.append(b["line"])

    def content_lines(ls):
        return [l for l in ls if re.match(r"^\s*-\s*(Details|Data|Insight)\s*:\s*\S", l)]
    cb, ca = content_lines(lines), content_lines(new_lines)
    lost = [l for l in cb if l not in set(ca)]
    g1 = not lost
    g2 = len(new_lines) <= len(lines)
    print(f"\n门禁: 内容行 {len(cb)}→{len(ca)} {'✅' if g1 else '❌ 丢失' + str(len(lost))} | 行数 {len(lines)}→{len(new_lines)} {'✅' if g2 else '❌'}")
    if lost:
        for l in lost[:8]:
            print(f"  丢失: {l[:90]}")

    if not apply:
        print("[dry-run] 未写盘。加 --apply 落盘。")
        return 0 if (g1 and g2) else 1
    if not (g1 and g2):
        print("❌ 门禁未过，拒绝落盘。")
        return 1
    bak = KB_PATH + ".bak-variantmerge"
    if not os.path.exists(bak):
        shutil.copy2(KB_PATH, bak); print(f"✅ 备份 {bak}")
    tmp = KB_PATH + ".merged"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")
    os.replace(tmp, KB_PATH)
    print(f"✅ 已落盘: {KB_PATH} ({len(new_lines)} 行)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
