#!/usr/bin/env python3
import os
import json
import re
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
KB_PATH = os.path.join(WORKSPACE, "data", "news", "knowledge_base.md")
SYNTHESIZED_FILE = os.path.join(WORKSPACE, "data", "news", "batch_1_synthesized.json")
BACKFILL_LOG = os.path.join(WORKSPACE, "data", "news", "backfill_progress.json")

def apply_detailed_updates():
    if not os.path.exists(SYNTHESIZED_FILE):
        print(f"❌ Synthesized file not found: {SYNTHESIZED_FILE}")
        return

    with open(SYNTHESIZED_FILE, "r", encoding="utf-8") as f:
        updates = json.load(f)

    with open(KB_PATH, "r", encoding="utf-8") as f:
        kb_content = f.read()

    updated_count = 0
    for entry in updates:
        title = entry["title"]
        detailed = entry["detailed_content"]
        
        # 构造正则：匹配以 bullet 开头且标题为 title 的行，及其随后的所有 Details/内容/详情 行
        # 模式：^[\s]*[•-]\s*TITLE\s*(\n\s*-\s*(?:Details|内容|详情):.*)*
        # 注意：我们需要把整个块替换为新内容
        
        # 转义标题中的特殊字符
        escaped_title = re.escape(title)
        pattern = rf"^[ \t]*[•-]\s*{escaped_title}.*?(\n\s*-\s*(?:Details|内容|详情):.*)*"
        
        # 构造新内容：保持原标题，更新/增加 Details 和 Insight
        # 我们将 detailed_content 分割为 Conclusion, Data, Impact
        # 转换为 KB 格式
        lines = detailed.split('\\n')
        conclusion = ""
        data = ""
        impact = ""
        for l in lines:
            if l.startswith("Conclusion:"): conclusion = l.replace("Conclusion:", "").strip()
            elif l.startswith("Data:"): data = l.replace("Data:", "").strip()
            elif l.startswith("Impact:"): impact = l.replace("Impact:", "").strip()
        
        # 如果是分行形式的 (有些LLM会直接给 \n)
        if not conclusion:
            # 尝试简单匹配
            conclusion = detailed.split("Data:")[0].replace("Conclusion:", "").strip() if "Data:" in detailed else "Detailed analysis updated."
        
        replacement = f"• {title}\n      - Details: {conclusion}\n      - Data: {data}\n      - Insight: {impact}"
        
        if re.search(pattern, kb_content, re.MULTILINE):
            kb_content = re.sub(pattern, replacement, kb_content, flags=re.MULTILINE)
            updated_count += 1
            print(f"✅ Updated: {title}")
        else:
            # 如果没搜到，可能是标题微调了（如 mojibake 修复），尝试模糊匹配
            print(f"⚠️ Not found: {title}")

    _bak(KB_PATH)
    with open(KB_PATH, "w", encoding="utf-8") as f:
        f.write(kb_content)
    
    # 更新进度日志
    progress = {}
    if os.path.exists(BACKFILL_LOG):
        with open(BACKFILL_LOG, "r", encoding="utf-8") as f:
            progress = json.load(f)
    
    for entry in updates:
        progress[entry["title"]] = "Detailed"
        
    _bak(BACKFILL_LOG)
    with open(BACKFILL_LOG, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

    print(f"\\n🎉 Successfully updated {updated_count} entries in Knowledge Base.")

if __name__ == "__main__":
    apply_detailed_updates()
