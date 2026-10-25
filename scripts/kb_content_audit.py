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

        # 优先判定是否为正文引导行 (处理 "- Details: ..." 模式)
        # 2026-10-26 修复: Data/Insight/Related 同属正文子行，不再误判为新条目
        is_detail_line = any(k in stripped for k in ["Details:", "内容:", "详情:", "Data:", "Insight:", "Related:"])

        if stripped.startswith(("•", "-")) and not is_detail_line:
            if current_entry:
                entries.append(current_entry)
            title = stripped.lstrip("•- ").strip()
            current_entry = {"title": title, "content": []}
        elif current_entry:
            # 无论是缩进行还是 Detail 引导行，都归为正文
            # 剔除引导词以保证长度统计纯净
            clean_text = re.sub(r'^.*(?:Details|内容|详情):\s*', '', stripped)
            current_entry["content"].append(clean_text)

    if current_entry:
        entries.append(current_entry)

    total = len(entries)
    if total == 0:
        print("❌ No entries found in KB.")
        return

    # 统计正文分布
    has_content = 0
    content_lengths = []
    grade_dist = Counter()

    for e in entries:
        full_text = " ".join(e["content"])
        length = len(full_text)
        if length > 5:  # 基础门槛
            has_content += 1
            content_lengths.append(length)
            # 分级：Stub (<30), Summary (30-100), Detailed (>100)
            grade = "Detailed" if length > 100 else ("Summary" if length > 30 else "Stub")
            grade_dist[grade] += 1

    print("="*50)
    print("📊 knowledge_base.md 含金量审计报告")
    print("="*50)
    print(f"解析总条目数:   {total}")
    print(f"含正文条目数:   {has_content}")
    print(f"正文覆盖率:     {(has_content/total*100):.2f}%")
    
    print(f"含金量分布:     {dict(grade_dist)}")
    
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
