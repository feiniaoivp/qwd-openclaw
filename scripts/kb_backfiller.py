#!/usr/bin/env python3
import os
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
PRIORITY_FILE = os.path.join(WORKSPACE, "data", "news", "expansion_priority.json")
KB_PATH = os.path.join(WORKSPACE, "data", "news", "knowledge_base.md")
BACKFILL_LOG = os.path.join(WORKSPACE, "data", "news", "backfill_progress.json")

# 模拟 LLM 合成接口 (实际执行时将调用 OpenClaw 的 llm_tool 或 via session_send)
def synthesize_detailed_content(title, raw_info=""):
    """
    将 Stub 标题转化为 Detailed 结构:
    结论 (Conclusion) -> 支撑数据 (Data) -> 潜在影响 (Impact)
    """
    # 此处在脚本中仅定义结构，实际执行由 agent 通过工具链完成
    # 脚本将产出需要扩充的任务清单
    return f"PENDING_SYNTHESIS: {title}"

def load_priorities():
    if not os.path.exists(PRIORITY_FILE):
        return []
    with open(PRIORITY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def get_current_progress():
    if not os.path.exists(BACKFILL_LOG):
        return {}
    with open(BACKFILL_LOG, "r", encoding="utf-8") as f:
        return json.load(f)

def save_progress(progress):
    _bak(BACKFILL_LOG)
    with open(BACKFILL_LOG, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

def main():
    print("🚀 Initializing KB Backfiller Engine...")
    priorities = load_priorities()
    if not priorities:
        print("❌ No priority list found. Please run kb_expansion_planner.py first.")
        return

    progress = get_current_progress()
    
    # 过滤已完成的
    todo = [p for p in priorities if p["title"] not in progress]
    print(f"📊 Total targets: {len(priorities)} | Completed: {len(progress)} | Remaining: {len(todo)}")

    # 这里的脚本目前作为 '调度器'，它将产出一个任务队列
    # 真正的扩充需要调用 web_search 和 LLM，建议由 Agent 在会话中执行或通过 subagent 批量处理
    
    # 产出本次批次需要处理的清单 (例如 Top 20)
    batch_size = 20
    current_batch = todo[:batch_size]
    
    print(f"📦 Current Batch ({len(current_batch)} items) ready for synthesis.")
    for i, item in enumerate(current_batch, 1):
        print(f"{i}. {item['title']} (Grade: {item['grade']})")

    # 保存待处理队列到临时文件，供 Agent 循环调用
    _bak(os.path.join(WORKSPACE, "data", "news", "current_backfill_queue.json"))
    with open(os.path.join(WORKSPACE, "data", "news", "current_backfill_queue.json"), "w", encoding="utf-8") as f:
        json.dump(current_batch, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
