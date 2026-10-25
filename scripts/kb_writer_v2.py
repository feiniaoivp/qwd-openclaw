#!/usr/bin/env python3
import os
import json
import re
import subprocess

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
KB_PATH = os.path.join(WORKSPACE, "data", "news", "knowledge_base.md")
BACKFILL_LOG = os.path.join(WORKSPACE, "data", "news", "backfill_progress.json")
NEWS_DIR = os.path.join(WORKSPACE, "data", "news")
LINKER_SCRIPT = os.path.join(WORKSPACE, "scripts/kb_linker.py")

# 2026-10-26 修复后的写入规则：
#   1. 替换范围 = 标题行 + 全部旧子行（防堆叠，旧版无 DOTALL 导致旧子行残留）
#   2. 追加前查重（归一化标题已存在则跳过，防跨区双写）
SUB_KINDS = r"(?:Details|Data|Insight|Related)"

def _norm_title(t: str) -> str:
    t = t.replace("행동", "行动").replace("行動", "行动")
    return t.translate(str.maketrans({"(": "(", ")": ")", "（": "(", "）": ")", " ": "", "\u3000": ""})).strip()

def _kb_title_set(kb_content):
    titles = set()
    for ln in kb_content.splitlines():
        st = ln.strip()
        if st.startswith(("•", "-", "*")) and not re.match(rf"^\s*-\s*{SUB_KINDS}\s*:", st):
            titles.add(_norm_title(st.lstrip("•-* ")))
    return titles

def apply_detailed_updates(synthesized_file):
    """Process a single synthesized JSON file and update the KB with Append-Fallback."""
    print(f"\n🚀 Processing: {os.path.basename(synthesized_file)}")
    
    try:
        with open(synthesized_file, "r", encoding="utf-8") as f:
            updates = json.load(f)
    except Exception as e:
        print(f"❌ Failed to read {synthesized_file}: {e}")
        return 0

    if not os.path.exists(KB_PATH):
        print(f"❌ KB file not found: {KB_PATH}")
        return 0

    with open(KB_PATH, "r", encoding="utf-8") as f:
        kb_content = f.read()

    updated_count = 0
    appended_count = 0
    skipped_count = 0
    skipped_richer = 0
    
    for entry in updates:
        title = entry.get("title")
        detailed = entry.get("detailed_content", "")
        if not title: continue
        
        # Normalize title for matching
        escaped_title = re.escape(title.strip())
        # 2026-10-26: 标题行 + 其后连续子行一起替换（修复堆叠）；子行 = Details/Data/Insight/Related
        pattern = (rf"^[ \t]*[•\-\*][ \t]*{escaped_title}[^\n]*\n"
                   rf"(?:[ \t]*-[ \t]*{SUB_KINDS}[ \t]*:[^\n]*\n?)*")

        # Parse detailed_content into structured format
        conclusion, data, impact = "", "", ""
        if "Conclusion:" in detailed:
            parts = detailed.split("Data:")
            conclusion = parts[0].replace("Conclusion:", "").strip()
            if len(parts) > 1:
                sub_parts = parts[1].split("Impact:")
                data = sub_parts[0].strip()
                if len(sub_parts) > 1:
                    impact = sub_parts[1].strip()
        else:
            conclusion = detailed[:100] + "..." if len(detailed) > 100 else detailed
        
        replacement = f"• {title}\n      - Details: {conclusion}\n      - Data: {data}\n      - Insight: {impact}"
        
        m_exist = re.search(pattern, kb_content, re.MULTILINE)
        if m_exist:
            # 2026-10-26 降级保护：现有内容更富时跳过更新（多次运行单调收敛到最富版本）
            existing_block = m_exist.group(0)
            existing_len = sum(len(v) for v in re.findall(rf"[ \t]*-[ \t]*{SUB_KINDS}[ \t]*:([^\n]*)", existing_block))
            if existing_len > len(detailed) * 1.1:
                skipped_richer += 1
                print(f"⏭️  Skip (现有内容更富 {existing_len}>{len(detailed)}，防降级): {title}")
                continue
            # Mode: Update（整块替换，含旧子行）
            kb_content = re.sub(pattern, replacement + "\n", kb_content, count=1, flags=re.MULTILINE)
            updated_count += 1
            print(f"✅ Updated: {title}")
        elif _norm_title(title) in _kb_title_set(kb_content):
            # 防重复：标题变体已存在（如全半角括号/乱码差异），不追加，报告人工对齐
            skipped_count += 1
            print(f"⏭️  Skip (标题变体已存在，防双写): {title}")
            continue
        else:
            # Mode: Append
            # Ensure KB ends with newline before appending
            if kb_content and not kb_content.endswith("\n"):
                kb_content += "\n"
            kb_content += f"\n{replacement}"
            appended_count += 1
            print(f"➕ Appended: {title}")

    # Only write to disk if changes were made
    if updated_count > 0 or appended_count > 0:
        with open(KB_PATH, "w", encoding="utf-8") as f:
            f.write(kb_content)
        
        # Update progress log
        progress = {}
        if os.path.exists(BACKFILL_LOG):
            try:
                with open(BACKFILL_LOG, "r", encoding="utf-8") as f:
                    progress = json.load(f)
            except: progress = {}
        
        for entry in updates:
            progress[entry.get("title")] = "Detailed"
            
        with open(BACKFILL_LOG, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Finished {os.path.basename(synthesized_file)}: Updated {updated_count}, Appended {appended_count}, Skipped-dup {skipped_count}, Skipped-richer {skipped_richer}.")
    return updated_count + appended_count

def run_pipeline():
    print("=== KB Backfill Pipeline Start (Append-Fallback Mode) ===")
    
    if not os.path.exists(NEWS_DIR):
        print(f"❌ News directory not found: {NEWS_DIR}")
        return

    files = [os.path.join(NEWS_DIR, f) for f in os.listdir(NEWS_DIR) if f.endswith("_synthesized.json")]
    files.sort()
    
    if not files:
        print("🔍 No synthesized files found. Nothing to do.")
        return

    total_processed = 0
    for f in files:
        total_processed += apply_detailed_updates(f)
    
    print(f"\n🎉 Pipeline Update Complete: Total {total_processed} entries synchronized to KB.")
    
    if os.path.exists(LINKER_SCRIPT):
        print("\n🔗 Triggering kb_linker.py to refresh associations...")
        try:
            result = subprocess.run(["python3", LINKER_SCRIPT], capture_output=True, text=True, check=True)
            print(result.stdout)
            print("✅ KB-Linker synchronized successfully.")
        except subprocess.CalledProcessError as e:
            print(f"❌ kb_linker.py failed:\n{e.stderr}")
    else:
        print(f"\n⚠️ Linker script not found at {LINKER_SCRIPT}. Skipping linkage phase.")

if __name__ == "__main__":
    run_pipeline()
