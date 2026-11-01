#!/usr/bin/env python3
import os
import json
import re
from collections import defaultdict
import sys
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
KB_PATH = os.path.join(WORKSPACE, "data", "news", "knowledge_base.md")
NEWS_DIR = os.path.join(WORKSPACE, "data", "news")


def _synthesized_files():
    """2026-11-01 升级：扫描全部 batch_*_synthesized.json（旧版硬编码 batch_1~3，
    关系矩阵只覆盖前三批，后续批次的实体共现全部丢失）。"""
    import glob
    return sorted(glob.glob(os.path.join(NEWS_DIR, "batch_*_synthesized.json")))

def build_relation_matrix():
    """
    扫描所有已合成的 Detailed 条目，根据标题和内容建立关联矩阵。
    """
    all_entries = []
    for f_path in _synthesized_files():
        if os.path.exists(f_path):
            with open(f_path, "r", encoding="utf-8") as f:
                all_entries.extend(json.load(f))
    
    # 建立关键词 -> 条目标题的映射
    # 核心关键词：公司名、行业词
    keyword_map = defaultdict(set)
    
    # 预定义核心实体 (从 WATCHLIST 简化)
    entities = [
        "中信证券", "中信建投", "招商银行", "中金公司", "越秀资本", 
        "长电科技", "中芯国际", "通富微电", "雷科防务", "亿纬锂能", 
        "天齐锂业", "国瓷材料", "应流股份", "汇川技术", "恒立液压", 
        "久立特材", "安达维尔", "科华数据", "金力永磁", "巨化股份", 
        "恒力石化", "中信特钢", "福耀玻璃", "恒生电子", "福莱蒽特", 
        "中联重科", "中信金属", "特变电工", "国电南瑞", "许继电气", 
        "中国西电", "思源电气", "华明装备", "沃尔核材", "平高电气"
    ]
    
    for entry in all_entries:
        title = entry["title"]
        content = entry["detailed_content"]
        text = title + " " + content
        
        for ent in entities:
            if ent in text:
                keyword_map[ent].add(title)
    
    # 构建标题 -> 相关标题 的映射
    relation_map = defaultdict(set)
    for ent, titles in keyword_map.items():
        for t1 in titles:
            for t2 in titles:
                if t1 != t2:
                    relation_map[t1].add(t2)
                    
    return relation_map

def apply_linking():
    rel_map = build_relation_matrix()
    if not rel_map:
        print("❌ No relations found.")
        return

    with open(KB_PATH, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    SUB = re.compile(r"^\s*-\s*(Details|Data|Insight|Related)\s*:")
    updated_count = 0
    # 我们只为那些已经在 KB 中且有 Detailed 内容的条目添加链接
    for title, related_titles in rel_map.items():
        # 行级定位：首个「弹符+标题全文」相等的行（含索引区子行，与旧版 prefix 正则同族）
        tgt = None
        for i, ln in enumerate(lines):
            m = re.match(r"^[ \t]*[•\-\*][ \t]*(.+)$", ln)
            if m and m.group(1).strip() == title.strip():
                tgt = i
                break
        if tgt is None:
            continue
        # 块扫描：标题行后的连续子行区间；已有 Related 子行 → 跳过（幂等）
        # 修复记录（2026-11-01）：旧守卫 `if "Related:" in m.group(1)` 因 lazy .*? 在首个子行
        # 前就停（lookahead `\n\s*[•-]` 连子行都匹配），group(1) 永远只含标题行，
        # 守卫形同虚设 → 每轮运行重复追加，重灾块堆到 15 行（已由 kb_related_dedup.py 治理存量）。
        j = tgt + 1
        has_rel = False
        block_end = tgt
        while j < len(lines):
            msub = SUB.match(lines[j])
            if not msub:
                break
            # 2026-11-01 升级：独立 Related 子行或子行文本内联 [[Related: ...]] 均算已链接
            # （synthesis 风格把 Related 写在 Insight 尾部，旧版只认独立子行会对已策展条目重复挂机械链接）
            if msub.group(1) == "Related" or "[[Related:" in lines[j]:
                has_rel = True
            block_end = j
            j += 1
        if has_rel:
            continue
        links = [f"[[{rt}]]" for rt in list(related_titles)[:5]]  # 最多关联 5 个
        link_line = "      - Related: " + ", ".join(links)
        lines.insert(block_end + 1, link_line)
        updated_count += 1
        print(f"🔗 Linked: {title} -> {len(links)} related")

    _bak(KB_PATH)
    with open(KB_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n🎉 Knowledge Linking Complete. Updated {updated_count} entries.")

def _selftest():
    """对抗自测：①已有 Related 子行 → 跳过；②无 Related → 恰插 1 行；
    ③Related 位于子行中段也能识别；④二跑幂等（0 更新）；⑤插入位置在块尾。"""
    import tempfile
    fixture = [
        "• 标题A",
        "      - Details: 甲",
        "      - Related: [[标题B]]",
        "• 标题C",
        "      - Data: 乙",
        "      - Insight: 丙",
        "• 标题D",
        "",
        "• 标题E",
        "      - Details: 丁",
        "      - Insight: 戊，另见。[[Related: 标题D]]",
        "  **分组头**:",
        "    - 某索引行",
    ]
    rel = {
        "标题A": {"标题B"},
        "标题C": {"标题D"},
        "标题D": {"标题C"},
        "标题E": {"标题A"},   # E 已有内联 Related → 守卫必须跳过
    }
    global KB_PATH
    old_kb, old_rel = KB_PATH, build_relation_matrix
    tmpdir = tempfile.mkdtemp()
    KB_PATH = os.path.join(tmpdir, "kb.md")
    with open(KB_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(fixture) + "\n")
    # monkeypatch rel_map 数据源
    globals()["build_relation_matrix"] = lambda: dict(rel)
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            apply_linking()
        out1 = open(KB_PATH, encoding="utf-8").read().splitlines()
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            apply_linking()
        out2 = open(KB_PATH, encoding="utf-8").read().splitlines()
    finally:
        KB_PATH = old_kb
        globals()["build_relation_matrix"] = old_rel

    errs = []
    # ① A 块未被追加；E（内联 Related）被跳过 → 全库独立 Related 行 = A/C/D 各 1 行 = 3 行
    n_rel_a = sum(1 for l in out1 if l.strip().lstrip("- ").startswith("Related:"))
    # A/C/D 各恰好 1 行 Related = 3 行
    if n_rel_a != 3:
        errs.append(f"Related 行数应=3 实际={n_rel_a}")
    # ② C 的插入位置：紧跟其 Insight 子行后
    if "标题C" in "\n".join(out1):
        ic = next(i for i, l in enumerate(out1) if l.strip() == "• 标题C")
        if not out1[ic + 3].strip().lstrip("- ").startswith("Related:"):
            errs.append(f"C 块插入位置错: {out1[ic+1:ic+4]}")
    # ③ 二跑幂等
    if out1 != out2:
        errs.append("二跑不幂等（第二轮仍产生变更）")
    # ⑤ E 的 Insight 内联 Related 未被拆出成独立行（保持 1 行）
    e_insight = [l for l in out1 if "[[Related: 标题D]]" in l]
    if len(e_insight) != 1 or not e_insight[0].strip().lstrip("- ").startswith("Insight:"):
        errs.append(f"E 内联 Related 被改动: {e_insight}")
    # ⑥ 分组头后的索引行未被误挂
    if any("某索引行" in l and "Related" in l for l in out1):
        errs.append("索引行被误改")

    if errs:
        print("❌ selftest FAILED:")
        for e in errs:
            print("  -", e)
        return 1
    print("✅ kb_linker selftest 6/6 通过（跳过已有/恰插一行/中段识别/二跑幂等/不误挂索引/内联Related感知）")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    apply_linking()
