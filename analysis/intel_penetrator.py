#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
intel_penetrator.py — 情报链路穿透工具 (2026-10-22 立项)

背景：
  - Google News 等 RSS 源提供的链接是追踪壳 (Redirect Shell)，直接抓取会导致 
    返回的是 c-wiz/DotsSplashUi 等混淆 HTML，而非实际正文。
  - 现有的 web_fetch 工具具备 Readability 模式，但前提是必须拿到「最终正文 URL」。

SOP (Sieve -> Penetrate -> Structure):
  1. Detect: 判定 URL 是否为 Shell (如 news.google.com)。
  2. Resolve: 若是 Shell，先用 raw 模式请求，提取 canonical link 或跳转目标。
  3. Extract: 使用 web_fetch (Readability) 请求最终 URL。
  4. Clean: 返回纯净的 Markdown 内容。

注：本文件定义逻辑框架。实际网络请求由 OpenClaw 的 web_fetch 工具完成。
"""

import os
import sys
import re
from typing import Optional, Tuple, List

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

class IntelPenetrator:
    """
    情报穿透类：专门处理 RSS 追踪壳、重定向中间页。
    """
    
    # 触发穿透的特征模式
    SHELL_PATTERNS = [
        r"news\.google\.com/rss/articles",
        r"news\.google\.com/articles",
        r"t\.co/",
        r"bit\.ly/",
        r"lnky\.in/",
    ]

    @classmethod
    def is_shell(cls, url: str) -> bool:
        """判定 URL 是否为需要穿透的壳页面"""
        if not url: return False
        return any(re.search(p, url, re.I) for p in cls.SHELL_PATTERNS)

    @classmethod
    def extract_canonical_url(cls, html_content: str) -> Optional[str]:
        """
        从 HTML 壳中提取真实的最终 URL。
        优先级：1. <link rel="canonical"> -> 2. Meta refresh -> 3. 首个非 Google 的 http 链接
        """
        if not html_content: return None

        # 1. 尝试 Canonical Link (标准)
        canonical = re.search(r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']', html_content, re.I)
        if canonical:
            return canonical.group(1)

        # 2. 尝试 Meta Refresh (旧式重定向)
        refresh = re.search(r'<meta\s+http-equiv=["\']refresh["\']\s+content=["\']\d+;\s*url=([^"\']+)["\']', html_content, re.I)
        if refresh:
            return refresh.group(1)

        # 3. 针对 Google News 壳的启发式提取 (寻找第一个非 google.com 的外部链接)
        links = re.findall(r'href=["\'](https?://[^"\']+)["\']', html_content, re.I)
        for link in links:
            if "google.com" not in link.lower() and "gstatic.com" not in link.lower():
                return link
        
        return None

    @classmethod
    def get_strategy(cls, url: str) -> Tuple[bool, str]:
        """
        根据 URL 返回穿透策略：(是否需要穿透, 建议的 web_fetch 模式)
        """
        if cls.is_shell(url):
            return True, "mode='readability'"
        return False, "mode='raw'"

def penetrate_url_logic(url: str, current_html: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    穿透逻辑入口（模拟 Agent 决策流）：
    返回 (是否成功找到新 URL, 新 URL)
    """
    if not IntelPenetrator.is_shell(url):
        return False, None
    
    if not current_html:
        return False, None # 需要先执行一次 web_fetch(mode='raw')
        
    final_url = IntelPenetrator.extract_canonical_url(current_html)
    if final_url:
        return True, final_url
    
    return False, None

if __name__ == "__main__":
    # --- 冒烟测试 ---
    test_cases = [
        ("https://news.google.com/rss/articles/ABC12345", True),
        ("https://www.eastmoney.com/a1.html", False),
        ("https://t.co/xyz789", True),
    ]
    
    print("--- IntelPenetrator Shell Detection Test ---")
    for url, expected in test_cases:
        res = IntelPenetrator.is_shell(url)
        status = "✅" if res == expected else "❌"
        print(f"{status} URL: {url} | Shell: {res}")

    # 模拟 HTML 提取测试
    mock_html = '<html><head><link rel="canonical" href="https://real-news.com/story1"></head><body>...</body></html>'
    extracted = IntelPenetrator.extract_canonical_url(mock_html)
    print(f"\nCanonical Extraction: {extracted} | Success: {extracted == 'https://real-news.com/story1'}")
