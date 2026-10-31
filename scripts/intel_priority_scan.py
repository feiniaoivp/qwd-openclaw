#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
intel_priority_scan.py — 高价值情报回填优先级扫描 (2026-10-22)
目标：扫描 raw/*.json，筛选出 significance_level >= 3 的高价值情报，生成回填清单。
"""

import os
import json
import glob
import re
from typing import List, Dict
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
RAW_DIR = os.path.join(WORKSPACE, "data", "news", "raw")

def significance_level(title):
    """复用 daily_news_reader.py 的重要度判定逻辑"""
    rule = [
        (4, ["回购", "增持", "减持", "举牌", "业绩预增", "业绩大增", "扭亏"]),
        (4, ["涨停", "跌停", "立案", "处罚", "诉讼", "337调查", "债务", "违约"]),
        (4, ["重大合同", "中标", "收购", "重组", "定增"]),
        (4, ["董事长", "CEO", "高管变动", "辞职", "辞任"]),
        (3, ["净利润", "中报", "年报", "半年报", "一季报", "营收"]),
        (3, ["分红", "现金分红", "转增"]),
    ]
    best = 0
    tag = ""
    for lv, kws in rule:
        for kw in kws:
            if kw in title and lv > best:
                best = lv
                tag = kw
    return best, tag

def scan():
    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.json")))
    priority_list = []
    
    print(f"Scanning {len(files)} snapshots for high-value intel...")
    
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as jf:
                data = json.load(jf)
        except Exception:
            continue

        stock_news = data.get("stock_news", [])
        if not isinstance(stock_news, list): continue
        
        date_str = os.path.basename(f).split('_')[0]
        
        for item in stock_news:
            sym = item.get("symbol", "Unknown")
            news_entries = item.get("news", [])
            if not isinstance(news_entries, list): continue
            
            for n in news_entries:
                if not isinstance(n, dict): continue
                title = n.get("title", "")
                url = n.get("url", "")
                if not url: continue
                
                lv, tag = significance_level(title)
                if lv >= 3:
                    priority_list.append({
                        "date": date_str,
                        "symbol": sym,
                        "title": title,
                        "url": url,
                        "level": lv,
                        "tag": tag
                    })

    # 去重 (基于 URL)
    seen_urls = set()
    unique_priority = []
    for item in priority_list:
        if item["url"] not in seen_urls:
            unique_priority.append(item)
            seen_urls.add(item["url"])

    print("="*50)
    print(f"🎯 高价值情报回填清单审计")
    print("="*50)
    print(f"筛选标准: significance_level >= 3")
    print(f"发现高价值条目数: {len(unique_priority)}")
    
    # 统计分布
    lv_dist = {}
    tag_dist = {}
    for item in unique_priority:
        lv_dist[item["level"]] = lv_dist.get(item["level"], 0) + 1
        tag_dist[item["tag"]] = tag_dist.get(item["tag"], 0) + 1

    print(f"等级分布: {lv_dist}")
    print(f"热门标签: {sorted(tag_dist.items(), key=lambda x: x[1], reverse=True)[:5]}")
    print("="*50)

    # 保存为待处理清单
    out_path = os.path.join(WORKSPACE, "data", "news", "backfill_priority.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    _bak(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(unique_priority, f, ensure_ascii=False, indent=2)
    print(f"✅ 清单已导出至: {out_path}")

if __name__ == "__main__":
    scan()
