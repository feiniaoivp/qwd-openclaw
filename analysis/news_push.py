#!/usr/bin/env python3
"""
盘前/盘后新闻推送脚本 (Command 模式)
=====================================
直接运行 news_monitor.py 并通过 send_telegram 推送结果 (纯文本)。
"""

import sys
import json
import subprocess

WORKSPACE = "/Users/duguke/.openclaw/workspace"
sys.path.insert(0, WORKSPACE)

from send_telegram import send_with_retry


def run_and_push(mode="盘前"):
    # 运行 news_monitor.py
    result = subprocess.run(
        [sys.executable, "analysis/news_monitor.py", "--mode", mode],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        timeout=180
    )
    
    if result.returncode != 0:
        error_msg = f"❌ news_monitor 执行失败 ({mode}):\n{result.stderr[-2000:]}"
        print(error_msg)
        send_with_retry(error_msg, parse_mode='')
        return False
    
    # 提取 JSON (在 =====NEWS_MONITOR_RESULT===== 和 =====NEWS_MONITOR_END===== 之间)
    output = result.stdout
    try:
        start = output.index("=====NEWS_MONITOR_RESULT=====") + len("=====NEWS_MONITOR_RESULT=====")
        end = output.index("=====NEWS_MONITOR_END=====")
        json_str = output[start:end].strip()
        data = json.loads(json_str)
    except (ValueError, json.JSONDecodeError) as e:
        # 如果解析失败，直接发送 stdout 的后半部分
        summary = f"📰 {mode}新闻抓取完成 (JSON解析失败，见完整输出)\n\n{output[-3000:]}"
        send_with_retry(summary, parse_mode='')
        return True
    
    # 生成简报文本
    summary = format_summary(data, mode)
    
    # 发送到 Telegram (纯文本模式)
    send_result = send_with_retry(summary, parse_mode='')
    print(f"Telegram 发送结果: {send_result.get('ok')}")
    
    return send_result.get('ok', False)


def format_summary(data: dict, mode: str) -> str:
    """格式化新闻简报 (纯文本)"""
    from datetime import datetime
    
    today = datetime.now().strftime("%Y-%m-%d")
    lines = []
    lines.append(f"📰 {mode}新闻快讯 {today}")
    lines.append("")
    
    total_new = data.get('total_new', 0)
    macro_new = data.get('macro_new', 0)
    notices_new = data.get('notices_new', 0)
    stock_new = data.get('stock_new', 0)
    
    lines.append(f"抓取到新资讯: {total_new} 条")
    lines.append(f"   • 宏观/政策: {macro_new} 条")
    lines.append(f"   • 关注股公告: {notices_new} 条")
    lines.append(f"   • 个股新闻: {stock_new} 条")
    lines.append("")
    
    # 公告优先
    notices = data.get('notices', [])
    if notices:
        lines.append("📢 关注股公告:")
        for n in notices[:10]:
            symbol = n.get('symbol', '')
            title = n.get('title', '')
            ntype = n.get('type', '')
            lines.append(f"  {symbol} {title} [{ntype}]")
        if len(notices) > 10:
            lines.append(f"  ... 等共 {len(notices)} 条")
        lines.append("")
    
    # 宏观政策
    macro_top = data.get('macro_top', [])
    if macro_top:
        lines.append("🏛 宏观/行业政策:")
        for n in macro_top[:8]:
            title = n.get('title', '')
            lines.append(f"  • {title}")
        if len(macro_top) > 8:
            lines.append(f"  ... 等共 {macro_new} 条")
        lines.append("")
    
    # 个股新闻
    stock_news = data.get('stock_news', [])
    if stock_news:
        lines.append("📌 个股新闻速览:")
        shown = 0
        for r in stock_news:
            news = r.get('news', [])
            if news:
                name = r.get('name', '')
                symbol = r.get('symbol', '')
                lines.append(f"  {name}({symbol}):")
                for n in news[:2]:
                    title = n.get('title', '')
                    lines.append(f"    - {title}")
                shown += 1
                if shown >= 8:
                    lines.append("  ... 等")
                    break
        lines.append("")
    
    lines.append("---")
    lines.append(f"原始数据: data/news/raw/{today}_{mode}.json")
    lines.append(f"知识库: data/news/knowledge_base.md")
    
    return "\n".join(lines)


if __name__ == "__main__":
    mode = "盘前"
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    success = run_and_push(mode)
    sys.exit(0 if success else 1)