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

def adjudicate(blocks, entry_idx, groups, lines):
    """内建裁决（2026-11-01 专项处置后固化）：逐 drop 检查风险，返回 (plan, risks)。
    plan 同原逻辑；risks = [(drop_title, keep_title, 风险类型)]。
    风险类型：variant(非同串) / raw_info(Raw 续行含实质信息，非『... 等』渲染残留)
    """
    plan, risks = [], []
    RAW_NOISE = re.compile(r"^[\.。…\s]*等?\s*$")  # '... 等' 类零信息残留
    for k, nums in groups.items():
        if len(nums) < 2:
            continue
        members = [(n, blocks[entry_idx[n]]) for n in nums]
        rich = [(n, b) for n, b in members if entry_richness(b) > 0]
        bare = [(n, b) for n, b in members if entry_richness(b) == 0]
        if len(rich) > 1:
            continue  # 多 rich 不动
        if len(rich) == 1:
            keep_n, keep_b = rich[0]
            drops = [b for _, b in bare]
        else:
            keep_n, keep_b = members[0]
            drops = [b for _, b in members[1:]]
        for b in drops:
            if b["title"].strip() != keep_b["title"].strip():
                risks.append((b["title"], keep_b["title"], "variant"))
            for kk, tt in b.get("sublines", []):
                if kk == "Raw" and tt.strip() and not RAW_NOISE.match(tt.strip()):
                    risks.append((b["title"], keep_b["title"], f"raw_info: {tt.strip()[:40]}"))
        plan.append({"keep": keep_b["title"], "drops": drops})
    return plan, risks


def main():
    apply = "--apply" in sys.argv
    allow_variants = "--allow-variants" in sys.argv
    allow_raw_info = "--allow-raw-info" in sys.argv
    with open(KB_PATH, encoding="utf-8") as f:
        lines = f.read().splitlines()
    blocks = parse_blocks(lines)

    # 索引制：entry_blocks = [(block_index, block)]
    entry_idx = [i for i, b in enumerate(blocks) if b["kind"] == "entry"]
    groups = {}
    for n, i in enumerate(entry_idx):
        groups.setdefault(variant_key(blocks[i]["title"]), []).append(n)

    plan, risks = adjudicate(blocks, entry_idx, groups, lines)

    drop_block_idx = set()   # 要删除的 block 索引（重建：由 plan 的块引用回查）
    merge_report = []
    for p in plan:
        for b in p["drops"]:
            merge_report.append((b["title"], p["keep"]))
    # 用同一遍历重建 drop 索引（保持与原实现一致：基于 blocks 索引）
    for k, nums in groups.items():
        if len(nums) < 2:
            continue
        members = [(n, blocks[entry_idx[n]]) for n in nums]
        rich = [(n, b) for n, b in members if entry_richness(b) > 0]
        bare = [(n, b) for n, b in members if entry_richness(b) == 0]
        if len(rich) > 1:
            print(f"  ⚠️ 多rich组保留不动: {[b['title'][:36] for _, b in rich]}")
            continue
        if len(rich) == 1:
            for n, b in bare:
                drop_block_idx.add(entry_idx[n])
        else:
            for n, b in members[1:]:
                drop_block_idx.add(entry_idx[n])

    # 裁决门禁：风险 drop 默认拒绝落盘（2026-11-01 固化，防同串前提被静默破坏）
    var_risks = [r for r in risks if r[2] == "variant"]
    raw_risks = [r for r in risks if r[2].startswith("raw_info")]
    if var_risks and apply and not allow_variants:
        print(f"❌ 裁决门禁：{len(var_risks)} 个非同串变体 drop（需 --allow-variants 或先人工裁决）:")
        for t, k, _ in var_risks[:10]:
            print(f"   ⚠️ {t[:56]} ≠ {k[:40]}")
        return 1
    if raw_risks and apply and not allow_raw_info:
        print(f"❌ 裁决门禁：{len(raw_risks)} 个 drop 块的 Raw 续行含实质信息（需 --allow-raw-info 或先人工裁决）:")
        for t, _, why in raw_risks[:10]:
            print(f"   ⚠️ {t[:52]} | {why}")
        return 1

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
