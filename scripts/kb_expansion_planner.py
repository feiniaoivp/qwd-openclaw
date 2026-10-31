#!/usr/bin/env python3
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
# sys.path 样板（TOOLS.md 强制）：工作区根 + analysis/，保障脚本模式导入
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import re
from collections import Counter
import json

# 关注池单一事实来源（TOOLS.md 口径规则）：不硬编码股票清单
from analysis.fib_extension_scan import WATCHLIST
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)

KB_PATH = os.path.join(WORKSPACE, "data", "news", "knowledge_base.md")
DREAMING_DIR = os.path.join(WORKSPACE, "memory", "dreaming")
OUTPUT_PATH = os.path.join(WORKSPACE, "data", "news", "expansion_priority.json")

def get_kb_entries():
    if not os.path.exists(KB_PATH):
        return {}
    
    with open(KB_PATH, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    entries = {}
    current_title = None
    
    # 噪声黑名单：过滤掉统计行、报头、以及非知识条目的汇总行
    black_list = ["条", "条目", "简报", "抓取到", "宏观/政策", "关注股公告", "个股新闻"]
    
    def _is_junk(title: str) -> bool:
        if not title or len(title) < 6:
            return True
        if "**Related**" in title or title.startswith("**"):
            return True
        return False
    
    for line in lines:
        stripped = line.strip()
        if not stripped: continue
        
        is_detail_line = any(k in stripped for k in ["Details:", "内容:", "详情:", "Data:", "Insight:", "Related:"])
        if stripped.startswith(("•", "-")) and not is_detail_line:
            # 过滤噪声行
            if any(word in stripped for word in black_list):
                continue
            
            current_title = stripped.lstrip("•- ").strip()
            if _is_junk(current_title):
                current_title = None
                continue
            entries[current_title] = {"content_len": 0, "grade": "Empty"}
        elif current_title:
            # 记录正文长度
            clean_text = re.sub(r'^.*(?:Details|内容|详情):\s*', '', stripped)
            entries[current_title]["content_len"] += len(clean_text)
    
    # 更新 Grade
    for title, data in entries.items():
        L = data["content_len"]
        if L > 100: data["grade"] = "Detailed"
        elif L > 30: data["grade"] = "Summary"
        elif L > 5: data["grade"] = "Stub"
        else: data["grade"] = "Empty"
        
    return entries

def calculate_priorities():
    kb = get_kb_entries()
    if not kb:
        print("❌ No KB entries found.")
        return []
    
    recall_counts = Counter()
    # 扫描 Dreaming 目录下所有 .md 文件
    for root, _, files in os.walk(DREAMING_DIR):
        for file in files:
            if file.endswith(".md"):
                try:
                    with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                        text = f.read()
                        for title in kb.keys():
                            # 匹配标题的前 15 个字符以提高鲁棒性
                            if len(title) > 15 and title[:15] in text:
                                recall_counts[title] += 1
                            elif len(title) <= 15 and title in text:
                                recall_counts[title] += 1
                except Exception:
                    continue
    
    grade_map = {"Detailed": 1.0, "Summary": 0.5, "Stub": 0.1, "Empty": 0.0}
    
    # 实体加权：标题含关注池股票名的条目优先回填（missingness 越高加权越大）
    entity_names = sorted(set(WATCHLIST.keys()), key=len, reverse=True)

    priorities = []
    for title, data in kb.items():
        freq = recall_counts[title]
        missingness = 1.0 - grade_map[data["grade"]]
        entity_bonus = 2.0 if any(name in title for name in entity_names) else 0.0
        score = (freq + entity_bonus) * missingness
        priorities.append({
            "title": title,
            "score": score,
            "recall_freq": freq,
            "entity": bool(entity_bonus),
            "grade": data["grade"],
            "length": data["content_len"]
        })
    
    # 按分数降序，分数相同时按 Recall 频次降序
    priorities.sort(key=lambda x: (x["score"], x["recall_freq"]), reverse=True)
    top_n = int(os.getenv("KB_TOP_N", "50"))
    return priorities[:top_n]

if __name__ == "__main__":
    print("🚀 Starting KB Expansion Planning (Noise-Filtered Version)...")
    top_50 = calculate_priorities()
    
    if top_50:
        _bak(OUTPUT_PATH)
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(top_50, f, ensure_ascii=False, indent=2)
        
        print(f"✅ Pure Priority list saved to {OUTPUT_PATH}")
        print("\nTop 10 High-Value Expansion Targets:")
        print("-" * 60)
        for i, item in enumerate(top_50[:10], 1):
            print(f"{i}. [{item['grade']}] {item['title']}")
            print(f"   Score: {item['score']:.2f} | Recall: {item['recall_freq']} | Len: {item['length']}")
            print("-" * 60)
    else:
        print("❌ No viable targets found.")
