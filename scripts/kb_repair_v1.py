#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_repair_v1.py — knowledge_base.md 结构修复 (2026-10-26) v2

两类事故损伤：
  A. kb_linker.py rf"\\1" 字面量 bug：54 个条目标题行被替换成孤立 "\\1"
     → Phase A：原始行层面，用 batch_*_synthesized.json 的 Details 内容哈希反查标题并还原
  B. writer 堆叠 + 跨区同题双写 + 空 Data/Insight 壳
     → Phase B：解析为条目后，子行去重（同种保留更长）、跨区同题合并（保留最早区段）、空壳清理

安全机制（.bak 铁律）：
  - 默认 dry-run；--apply 才落盘（先 .bak 时间戳备份 → 写 .repaired → 门禁全过 → os.replace）
  - 门禁：G1 \\1 全还原 / G2 骨架零丢失 / G3 归一化标题零丢失 / G4 内容不低于「每组最大丰富度」之和
"""
import os
import re
import sys
import json
import shutil
from collections import OrderedDict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
KB_PATH = os.path.join(WORKSPACE, "data", "news", "knowledge_base.md")
NEWS_DIR = os.path.join(WORKSPACE, "data", "news")

SUB_LINE_RE = re.compile(r"^\s*-\s*(Details|Data|Insight|Related)\s*:\s?(.*)$")
STAT_RE = re.compile(r"[:：]\s*\d+\s*条")
TITLE_BULLET_RE = re.compile(r"^(\s*)([•\-\*])\s+(.*)$")
GROUP_RE = re.compile(r"^\s*\*\*[^*]+\*\*\s*:\s*$")
GROUP_INLINE_RE = re.compile(r"^(\s*\*\*[^*]+\*\*\s*:\s*)(\S.*)$")
HEADER_RE = re.compile(r"^#{1,6}\s")
BRIEF_RE = re.compile(r"📰|📌|简报|抓取到")

_GARBAGE_FIX = str.maketrans({"(": "(", ")": ")", "（": "(", "）": ")", " ": "", "\u3000": ""})

def norm_title(t: str) -> str:
    t = t.replace("행동", "行动").replace("行動", "行动")
    t = t.strip().strip("*").strip()
    return t.translate(_GARBAGE_FIX)

def load_batches():
    by_title, detail_index = {}, {}
    for i in (1, 2, 3):
        p = os.path.join(NEWS_DIR, f"batch_{i}_synthesized.json")
        if not os.path.exists(p):
            continue
        for e in json.load(open(p, encoding="utf-8")):
            nt = norm_title(e["title"])
            dc = e.get("detailed_content", "")
            if nt not in by_title or len(dc) > len(by_title[nt].get("detailed_content", "")):
                by_title[nt] = e
            m = re.search(r"Conclusion:\s*(.{40})", dc)
            if m:
                detail_index[m.group(1)] = nt
    return by_title, detail_index

# ---------------- Phase A：原始行层面还原 \\1 ----------------

def restore_one_lines(lines, by_title, detail_index):
    rep = {"restored": 0, "unresolved": []}
    out = list(lines)
    i = 0
    while i < len(out):
        if out[i].strip() != "\\1":
            i += 1
            continue
        # 收集 \1 下方连续 subline 行（受损条目的内容）
        j = i + 1
        span_vals = []
        while j < len(out):
            m = SUB_LINE_RE.match(out[j])
            if not m:
                break
            span_vals.append((m.group(1), m.group(2)))
            j += 1
        title = None
        for k, v in span_vals:
            if k != "Details" or len(v.strip()) < 40:
                continue
            v2 = v.strip()
            if v2.startswith("Conclusion:"):
                v2 = v2[len("Conclusion:"):].strip()
            if v2[:40] in detail_index:
                title = by_title[detail_index[v2[:40]]]["title"]
                break
        if title is None:
            rel = next((v for k, v in span_vals if k == "Related"), "")
            for cand in re.findall(r"\[\[([^\]]+)\]\]", rel):
                if norm_title(cand) in by_title:
                    title = cand
                    break
        if title is None:
            rep["unresolved"].append(i + 1)
            i = j
            continue
        # 推断缩进/弹符：默认组内 4 空格 '- '；若上方最近的标题弹是列 0 '•' 且中间无组头，则列 0 '• '
        indent, bullet = 4, "-"
        for back in range(i - 1, max(-1, i - 40), -1):
            s = out[back]
            if GROUP_RE.match(s):
                break
            tb = TITLE_BULLET_RE.match(s)
            if tb:
                kind = s.strip()[:2]
                if tb.group(1) == "" and tb.group(2) == "•" and not STAT_RE.search(tb.group(3)):
                    indent, bullet = 0, "•"
                break
        out[i] = f"{' ' * indent}{bullet} {title}"
        rep["restored"] += 1
        i = j
    return out, rep

# ---------------- Phase B：解析 + 去重 + 重组 ----------------

def parse_blocks(lines):
    blocks, cur = [], None
    for raw in lines:
        s = raw.rstrip("\n")
        if not s.strip():
            if cur: blocks.append(cur); cur = None
            blocks.append({"kind": "blank", "line": s}); continue
        if s.strip() == "\\1":
            if cur: cur["residual_one"] = True
            else: blocks.append({"kind": "other", "line": s})
            continue
        m = SUB_LINE_RE.match(s)
        if m:
            if cur is None:
                blocks.append({"kind": "other", "line": s})
            else:
                cur["sublines"].append((m.group(1), m.group(2)))
            continue
        if GROUP_RE.match(s):
            if cur: blocks.append(cur); cur = None
            blocks.append({"kind": "group", "line": s}); continue
        # 纯加粗分隔行（如 “🏛 **宏观/行业政策**”，无冒号）：同样作为骨架行，防止被误挂到上一条目
        if re.match(r"^\s*\S{0,2}\s*\*\*[^*]+\*\*\s*$", s):
            if cur: blocks.append(cur); cur = None
            blocks.append({"kind": "group", "line": s}); continue
        gi = GROUP_INLINE_RE.match(s)
        if gi:
            if cur: blocks.append(cur); cur = None
            blocks.append({"kind": "group", "line": gi.group(1).rstrip()})
            s_body = gi.group(2)
            tm = TITLE_BULLET_RE.match(s_body)
            if tm:
                cur = {"kind": "entry", "title": tm.group(3), "indent": len(gi.group(1)) + len(tm.group(1)),
                       "bullet": tm.group(2), "sublines": [], "residual_one": False}
            else:
                blocks.append({"kind": "other", "line": s_body})
            continue
        if HEADER_RE.match(s):
            if cur: blocks.append(cur); cur = None
            blocks.append({"kind": "header", "line": s}); continue
        tm = TITLE_BULLET_RE.match(s)
        body = tm.group(3) if tm else s.strip()
        is_stat = bool(STAT_RE.search(body)) or any(k in body for k in ("简报", "抓取到", "新闻速览"))
        if BRIEF_RE.search(s) or (tm and is_stat):
            if cur: blocks.append(cur); cur = None
            blocks.append({"kind": "brief" if BRIEF_RE.search(s) else "stat", "line": s}); continue
        if tm:
            if cur: blocks.append(cur)
            cur = {"kind": "entry", "title": body, "indent": len(tm.group(1)),
                   "bullet": tm.group(2), "sublines": [], "residual_one": False}
            continue
        if cur is not None:
            cur["sublines"].append(("Raw", s.strip()))
        else:
            blocks.append({"kind": "other", "line": s})
    if cur: blocks.append(cur)
    return blocks

def entry_richness(e):
    return sum(1 for k, t in e["sublines"] if k in ("Details", "Data", "Insight") and t.strip())

def dedup_sublines(e, rep):
    best, seen = [], {}
    for k, t in e["sublines"]:
        if k == "Raw":
            best.append((k, t)); continue
        if not t.strip():
            rep["empty_subline_dropped"] += 1
            continue
        if k not in seen:
            seen[k] = t; best.append((k, t))
        elif len(t) > len(seen[k]):
            best = [(kk, tt) for kk, tt in best if kk != k]
            best.append((k, t)); seen[k] = t
            rep["subline_upgraded"] += 1
        else:
            rep["subline_dedup"] += 1
    e["sublines"] = best

def repair_phase_b(blocks, rep):
    rep.setdefault("subline_dedup", 0)
    rep.setdefault("subline_upgraded", 0)
    rep.setdefault("empty_subline_dropped", 0)
    rep.setdefault("cross_dups_removed", 0)
    entries = [b for b in blocks if b["kind"] == "entry"]
    for e in entries:
        dedup_sublines(e, rep)
    by_norm = OrderedDict()
    for e in entries:
        by_norm.setdefault(norm_title(e["title"]), []).append(e)
    drop_ids, expected_min_rich = set(), 0
    for nt, group in by_norm.items():
        expected_min_rich += max(entry_richness(e) for e in group)
        if len(group) < 2:
            continue
        keep = group[0]
        for dup in group[1:]:
            have = {k for k, t in keep["sublines"] if k in ("Details", "Data", "Insight") and t.strip()}
            for k, t in dup["sublines"]:
                if k in ("Details", "Data", "Insight") and t.strip() and k not in have:
                    keep["sublines"].append((k, t)); have.add(k)
            if entry_richness(dup) > entry_richness(keep):
                keep["sublines"], dup["sublines"] = dup["sublines"], []
            rep["cross_dups_removed"] += 1
            drop_ids.add(id(dup))
    kept = [b for b in blocks if not (b["kind"] == "entry" and id(b) in drop_ids)]
    return kept, expected_min_rich

def serialize(blocks):
    out = []
    for b in blocks:
        if b["kind"] == "entry":
            out.append(f"{' ' * b['indent']}{b['bullet']} {b['title']}")
            for k, t in b["sublines"]:
                out.append(f"      - {k}: {t}" if k != "Raw" else f"      {t}")
        else:
            out.append(b["line"])
    return "\n".join(out) + "\n"

def main():
    apply = "--apply" in sys.argv
    with open(KB_PATH, encoding="utf-8") as f:
        orig_lines = f.read().splitlines()
    by_title, detail_index = load_batches()
    print(f"batch 题库: {len(by_title)} 唯一题 / Details 反查索引 {len(detail_index)}")

    lines_a, repA = restore_one_lines(orig_lines, by_title, detail_index)
    blocks_before = parse_blocks(lines_a)
    n_entries_before = sum(1 for b in blocks_before if b["kind"] == "entry")

    kept, expected_min_rich = repair_phase_b(blocks_before, repA)
    rep = {**repA}
    rep["_expected_min_rich"] = expected_min_rich
    new_text = serialize(kept)
    new_lines = new_text.splitlines()
    blocks_after = parse_blocks(new_lines)
    n_entries_after = sum(1 for b in blocks_after if b["kind"] == "entry")

    print("\n===== 修复报告 =====")
    for k in ("restored", "unresolved", "subline_dedup", "subline_upgraded",
              "empty_subline_dropped", "cross_dups_removed"):
        v = rep.get(k, 0)
        print(f"  {k}: {v if not isinstance(v, list) else v[:8]}")
    print(f"  条目数: {n_entries_before} -> {n_entries_after}  |  行数: {len(orig_lines)} -> {len(new_lines)}")

    # 门禁
    n_one_orig = sum(1 for l in orig_lines if l.strip() == "\\1")
    n_one_new = sum(1 for l in new_lines if l.strip() == "\\1")
    g1 = (n_one_new == len(rep["unresolved"]))
    skel_b = [l for l in lines_a if GROUP_RE.match(l) or HEADER_RE.match(l)]
    skel_a = [l for l in new_lines if GROUP_RE.match(l) or HEADER_RE.match(l)]
    g2 = len(skel_a) >= len(skel_b)
    norms_after = {norm_title(b["title"]) for b in blocks_after if b["kind"] == "entry"}
    clean_before = {norm_title(b["title"]) for b in blocks_before if b["kind"] == "entry" and "**" not in b["title"]}
    g3 = clean_before <= norms_after
    rich_after = sum(entry_richness(b) for b in blocks_after if b["kind"] == "entry")
    g4 = rich_after >= rep.get("_expected_min_rich", 0)

    print("\n===== 防倒退门禁 =====")
    print(f"  {'✅' if g1 else '❌'} G1 \\1 全还原 ({n_one_orig}→残留{n_one_new}, 未解{len(rep['unresolved'])})")
    print(f"  {'✅' if g2 else '❌'} G2 骨架行不减少 ({len(skel_b)}→{len(skel_a)})")
    print(f"  {'✅' if g3 else '❌'} G3 归一化标题零丢失 (差异 {len(clean_before - norms_after)})")
    print(f"  {'✅' if g4 else '❌'} G4 内容≥合并理论下限 (rich {rich_after} ≥ {rep.get('_expected_min_rich')})")

    if not apply:
        print("\n[dry-run] 未写盘。加 --apply 落盘。")
        return 0 if (g1 and g2 and g3 and g4) else 1
    if not (g1 and g2 and g3 and g4):
        print("❌ 门禁未全过，拒绝落盘。")
        return 1
    bak = KB_PATH + ".bak-20261026"
    if not os.path.exists(bak):
        shutil.copy2(KB_PATH, bak); print(f"✅ 备份: {bak}")
    tmp = KB_PATH + ".repaired"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_text)
    os.replace(tmp, KB_PATH)
    print(f"✅ 已落盘: {KB_PATH} ({len(new_lines)} 行)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
