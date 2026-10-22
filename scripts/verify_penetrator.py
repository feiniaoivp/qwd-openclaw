#!/usr/bin/env python3
import os
import sys
import re
from typing import Optional

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.intel_penetrator import IntelPenetrator

def test_penetration(url: str, mock_html: str):
    print(f"Testing URL: {url}")
    print("-" * 40)
    
    # 1. 检测是否为 Shell
    is_shell = IntelPenetrator.is_shell(url)
    print(f"Is Shell: {is_shell}")
    
    if not is_shell:
        print("Result: Direct link, no penetration needed.")
        return
    
    # 2. 模拟 web_fetch(mode='raw') 拿到的 HTML
    print("Simulating raw fetch... extracting canonical URL...")
    final_url = IntelPenetrator.extract_canonical_url(mock_html)
    
    if final_url:
        print(f"Success! Found Final URL: {final_url}")
    else:
        print("Failed to extract final URL.")

if __name__ == "__main__":
    # 测试用例 1: Google News 壳 (模拟真实 c-wiz 结构)
    google_url = "https://news.google.com/rss/articles/CBMia0FVX3lxTE8tY3YxVm0yUmRaNUVkTm1jSDV1aGZ0a2prOVc2WVZuUXlxMFFUcUhSNVBPSUdiaUtVaU9BRzJ2VG1BeWZPVEZ2OGRzV3lxaVEyTlY1c2hyYmJXSWxwSTROVVk0aXBLcWN0NFMw?oc=5"
    # 模拟一个包含 canonical link 的 HTML 壳
    mock_html_1 = '<html><head><link rel="canonical" href="https://www.caixin.com/article/policy-bottom-discussion"></head><body>...</body></html>'
    
    # 测试用例 2: 启发式提取 (无 canonical，但有外部链接)
    mock_html_2 = '<html><body><a href="https://www.bloomberg.com/news/articles/123">Read more at Bloomberg</a></body></html>'

    test_penetration(google_url, mock_html_1)
    print("\n")
    test_penetration(google_url, mock_html_2)
