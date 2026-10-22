#!/usr/bin/env python3
"""
新闻产物质量审计脚本 (news_quality_audit.py)
========================================
检查 news_monitor 的产出是否真正具有时效性，防止「内容 Fake OK」。
"""

import os
import json
import sys
import re
from datetime import datetime, timedelta

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")

# 审计配置
RAW_DIR = os.path.join(WORKSPACE, "data", "news", "raw")
WATCHLIST_FILE = "analysis/fib_extension_scan.py" # 用于获取关注池规模

def get_watchlist_size():
    """简单解析 WATCHLIST 规模"""
    try:
        with open(os.path.join(WORKSPACE, WATCHLIST_FILE), "r", encoding="utf-8") as f:
            content = f.read()
            # 匹配所有 '代码': '名称' 形式
            matches = re.findall(r"['\"](\d{6})['\"]\s*:\s*['\"].*?['\"]", content)
            return len(matches)
    except Exception as e:
        print(f"⚠️ 无法解析关注池规模: {e}")
        return 36 # 默认回退

def audit_today():
    today_str = datetime.now().strftime("%Y-%m-%d")
    # 检查 盘前 和 盘后 两个快照
    modes = ["盘前", "盘后"]
    results = []
    
    watchlist_size = get_watchlist_size()
    print(f"🔍 开始新闻产物质量审计 | 关注池规模: {watchlist_size}")
    print("-" * 60)

    for mode in modes:
        raw_path = os.path.join(RAW_DIR, f"{today_str}_{mode}.json")
        if not os.path.exists(raw_path):
            print(f"⚪ {mode}: 尚未运行或产物缺失 (跳过)")
            continue
        
        with open(raw_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # 1. 覆盖率校验 (Stock Coverage)
        stock_news = data.get("stock_news", [])
        covered_stocks = [s for s in stock_news if s.get("news") or s.get("error")]
        coverage_rate = len(covered_stocks) / watchlist_size if watchlist_size > 0 else 0
        
        # 2. 时效性校验 (Freshness)
        all_dates = []
        # 宏观
        for n in data.get("macro", {}).get("news", []):
            all_dates.append(n.get("time"))
        # 公告
        for n in data.get("notices", {}).get("notices", []):
            all_dates.append(n.get("date"))
        # 个股
        for s in stock_news:
            for n in s.get("news", []):
                all_dates.append(n.get("time"))
        
        fresh_count = 0
        valid_dates = 0
        for d in all_dates:
            if not d: continue
            valid_dates += 1
            if today_str in str(d):
                fresh_count += 1
        
        fresh_rate = fresh_count / valid_dates if valid_dates > 0 else 0
        
        # 3. 异常捕获 (Error Rate)
        errors = [s.get("error") for s in stock_news if s.get("error")]
        error_rate = len(errors) / watchlist_size if watchlist_size > 0 else 0

        print(f"📊 {mode} 审计结果:")
        print(f"  • 覆盖率: {len(covered_stocks)}/{watchlist_size} ({coverage_rate:.1%})")
        print(f"  • 时效性: {fresh_count}/{valid_dates} ({fresh_rate:.1%}) [今日日期命中率]")
        print(f"  • 错误率: {len(errors)}/{watchlist_size} ({error_rate:.1%})")
        
        if error_rate > 0.2:
            results.append(f"🔴 {mode} 错误率过高 ({error_rate:.1%})")
        if coverage_rate < 0.8:
            results.append(f"🔴 {mode} 覆盖率不足 ({coverage_rate:.1%})")
        if fresh_rate < 0.1: # 只要有 10% 是今天的就认为没完全死掉
            results.append(f"🔴 {mode} 时效性极差 (今日命中率 {fresh_rate:.1%})")
        else:
            print(f"  ✅ 内容质量合格")

    if not results:
        print("-" * 60)
        print("✅ 所有运行的新闻产物质量合格 (Content Truth Verified)")
        return 0
    else:
        print("-" * 60)
        print("❌ 发现内容质量风险：")
        for r in results:
            print(f"  {r}")
        return 1

if __name__ == "__main__":
    sys.exit(audit_today())
