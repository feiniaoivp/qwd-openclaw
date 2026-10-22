#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_content_audit.py — 知识库正文含金量审计 (2026-10-22)
目标：验证 knowledge_base.md 是否仅包含标题，统计正文覆盖率。
"""

import os
import re
from collections import Counter

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
KB_PATH = os.path.join(WORKSPACE, "data", "news", "knowledge_base.md")

def audit():
    if not os.path.exists(KB_PATH):
        print(f"❌ KB file not found: {KB_PATH}")
        return

    with open(KB_PATH, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    entries = []
    current_entry = None
    
    # 简单解析 KB 结构
    for line in lines:
        stripped = line.strip()
        if not stripped: continue
        
        # 匹配 bullet point (标题行)
        if stripped.startswith(("•", "-")):
            if current_entry:
                entries.append(current_entry)
            
            # 提取标题内容
            title = stripped.lstrip("•- ").strip()
            current_entry = {"title": title, "content": []}
        elif current_entry:
            # 认为缩进的内容是正文
            current_entry["content"].append(stripped)

    if current_entry:
        entries.append(current_entry)

    total = len(entries)
    if total == 0:
        print("❌ No entries found in KB.")
        return

    # 统计正文分布
    has_content = 0
    content_lengths = []
    
    for e in entries:
        full_text = " ".join(e["content"])
        if len(full_text) > 20: # 认为超过 20 字才算有实质内容
            has_content += 1
            content_lengths.append(len(full_text))

    print("="*50)
    print("📊 knowledge_base.md 含金量审计报告")
    print("="*50)
    print(f"解析总条目数:   {total}")
    print(f"含正文条目数:   {has_content}")
    print(f"正文覆盖率:     {(has_content/total*100):.2f}%")
    
    if content_lengths:
        print(f"正文平均长度:   {sum(content_lengths)//len(content_lengths)} 字")
        print(f"正文最大长度:   {max(content_lengths)} 字")
    
    print("="*50)
    
    if total > 0:
        print("\n💡 抽样展示 (随机 3 条):")
        import random
        samples = random.sample(entries, min(3, total))
        for i, s in enumerate(samples):
            print(f"Sample {i+1}:")
            print(f"  Title: {s['title']}")
            print(f"  Content: {' '.join(s['content']) if s['content'] else '[EMPTY]'}")
            print("-" * 20)

if __name__ == "__main__":
    audit()
