#!/usr/bin/env python3
import os
import json
import re
from collections import defaultdict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
KB_PATH = os.path.join(WORKSPACE, "data", "news", "knowledge_base.md")
# 这里的 assets 应该是我们之前合成的 json 文件
SYNTHESIZED_FILES = [
    os.path.join(WORKSPACE, "data", "news", "batch_1_synthesized.json"),
    os.path.join(WORKSPACE, "data", "news", "batch_2_synthesized.json"),
    os.path.join(WORKSPACE, "data", "news", "batch_3_synthesized.json"),
]

def build_relation_matrix():
    """
    扫描所有已合成的 Detailed 条目，根据标题和内容建立关联矩阵。
    """
    all_entries = []
    for f_path in SYNTHESIZED_FILES:
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
        kb_content = f.read()

    updated_count = 0
    # 我们只为那些已经在 KB 中且有 Detailed 内容的条目添加链接
    for title, related_titles in rel_map.items():
        # 查找该条目在 KB 中的位置
        escaped_title = re.escape(title)
        # 匹配该条目的末尾（直到下一个 bullet 之前）
        pattern = rf"(^[ \t]*[•-]\s*{escaped_title}.*?)(?=\n\s*[•-]| \Z)"
        
        # 构造链接字符串
        links = [f"[[{rt}]]" for rt in list(related_titles)[:5]] # 最多关联 5 个
        link_str = "\\n      - Related: " + ", ".join(links)
        
        m = re.search(pattern, kb_content, re.MULTILINE | re.DOTALL)
        if m:
            # 2026-10-26 修复两处：
            #   1. 原替换串 rf"\\1" 是字面量反斜杠+1（非分组引用），曾把 54 个标题行毁成孤立 \1
            #   2. 幂等保护：已含 Related: 的条目跳过，防重复追加堆叠
            if "Related:" in m.group(1):
                continue
            kb_content = re.sub(pattern, rf"\g<1>{link_str}", kb_content, count=1, flags=re.MULTILINE | re.DOTALL)
            updated_count += 1
            print(f"🔗 Linked: {title} -> {len(links)} related")

    with open(KB_PATH, "w", encoding="utf-8") as f:
        f.write(kb_content)
        
    print(f"\\n🎉 Knowledge Linking Complete. Updated {updated_count} entries.")

if __name__ == "__main__":
    apply_linking()
