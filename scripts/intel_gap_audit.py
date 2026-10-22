#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
intel_gap_audit.py — 情报库「空心化」量化审计 (2026-10-22 v2)
修正：stock_news 在快照中是 List 结构而非 Dict。
"""

import os
import json
import glob
import re
from typing import Dict, List

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
RAW_DIR = os.path.join(WORKSPACE, "data", "news", "raw")
KB_PATH = os.path.join(WORKSPACE, "data", "news", "knowledge_base.md")

SHELL_PATTERNS = [
    r"news\.google\.com/rss/articles",
    r"news\.google\.com/articles",
    r"t\.co/",
    r"bit\.ly/",
]

def is_shell(url: str) -> bool:
    if not url: return False
    return any(re.search(p, url, re.I) for p in SHELL_PATTERNS)

def audit():
    kb_content = ""
    if os.path.exists(KB_PATH):
        with open(KB_PATH, "r", encoding="utf-8") as f:
            kb_content = f.read()

    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.json")))
    total_links = 0
    shell_links = 0
    missing_content_count = 0
    details = []

    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as jf:
                data = json.load(jf)
        except Exception:
            continue

        # 修正：stock_news 是 List[Dict]
        stock_news_list = data.get("stock_news", [])
        if not isinstance(stock_news_list, list):
            continue
        
        for item in stock_news_list:
            sym = item.get("symbol", "Unknown")
            news_entries = item.get("news", [])
            if not isinstance(news_entries, list):
                continue
                
            for n in news_entries:
                if not isinstance(n, dict): continue
                url = n.get("url", "")
                title = n.get("title", "")
                if not url: continue
                
                total_links += 1
                if is_shell(url):
                    shell_links += 1
                    if url not in kb_content:
                        missing_content_count += 1
                        details.append({
                            "date": os.path.basename(f).split('_')[0],
                            "stock": sym,
                            "title": title,
                            "url": url
                        })

    print("="*50)
    print("📊 情报库「空心化」量化审计报告 (v2)")
    print("="*50)
    print(f"扫描快照文件数: {len(files)}")
    print(f"统计总链接数:   {total_links}")
    print(f"其中 Shell 链接: {shell_links} ({ (shell_links/total_links*100 if total_links else 0):.2f}%)")
    print(f"确认缺失正文数: {missing_content_count} (需回填量)")
    print("="*50)
    
    if details:
        print("\n💡 典型缺失样本 (前 5 条):")
        for d in details[:5]:
            print(f"[{d['date']}] {d['stock']} | {d['title']} \n  URL: {d['url']}\n")

if __name__ == "__main__":
    audit()
