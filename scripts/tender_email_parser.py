#!/usr/bin/env python3
"""
海外招标邮件解析器 - 标准化 JSON 输入接口
用于解析 Google Alerts 邮件转发、人工粘贴、或未来 Gmail API 接入的招标信息

输入格式 (JSON Lines 或 JSON 数组):
每条记录包含:
{
  "source": "SEC|DEWA|ONS|GOOGLE_ALERTS|MANUAL",
  "title": "招标公告标题",
  "url": "原文链接",
  "date": "2026-09-10",  # 发布日期
  "country": "SA|AE|BR|其他",
  "keywords_matched": ["HVDC", "GIS", "换流站"],
  "raw_text": "邮件/网页原始文本片段"
}

输出: data/tender_hits_YYYY-MM-DD.json (追加模式，去重)
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

# 关键词配置
TENDER_KEYWORDS = [
    # 英文
    "HVDC", "GIS", "Substation", "Transmission Line", "Converter Station",
    "±800kV", "±1100kV", "800kV", "1100kV", "EPC", "BOO", "BOT",
    "Power Transmission", "Grid Connection", "Interconnector",
    # 中文
    "换流站", "特高压", "GIS", "变压器", "阀厅", "直流输电",
    "输电线路", "变电站", "EPC总包", "海外工程", "中标", "招标",
    # 西班牙语/葡萄牙语 (巴西/中东/南美)
    "Linha de Transmissão", "Subestação", "HVDC", "EPC",
]

# 来源标准化映射
SOURCE_MAP = {
    "sec": "SEC",
    "saudi electricity": "SEC",
    "dewa": "DEWA",
    "dubai": "DEWA",
    "ons": "ONS",
    "brazil": "ONS",
    "aneel": "ONS",
    "google": "GOOGLE_ALERTS",
    "alert": "GOOGLE_ALERTS",
    "manual": "MANUAL",
}

def normalize_source(source: str) -> str:
    """标准化来源名称"""
    source_lower = source.lower().strip()
    for key, val in SOURCE_MAP.items():
        if key in source_lower:
            return val
    return source.upper()[:20]

def extract_keywords(text: str) -> list[str]:
    """从文本提取命中的关键词"""
    text_upper = text.upper()
    matched = [kw for kw in TENDER_KEYWORDS if kw.upper() in text_upper]
    return matched

def infer_country(text: str, source: str) -> str:
    """推断国家"""
    text_lower = text.lower()
    country_map = {
        "sa": ["saudi", "沙特", "sec", "riyadh", "利雅得"],
        "ae": ["dubai", "dewa", "阿联酋", "迪拜", "uae"],
        "br": ["brazil", "巴西", "ons", "aneel", "巴西利亚"],
        "cl": ["chile", "智利"],
        "ph": ["philippines", "菲律宾"],
        "id": ["indonesia", "印尼"],
        "my": ["malaysia", "马来西亚"],
        "mn": ["mongolia", "蒙古"],
    }
    for code, keywords in country_map.items():
        if any(kw in text_lower for kw in keywords):
            return code.upper()
    # 根据来源推断
    if "SEC" in source.upper():
        return "SA"
    if "DEWA" in source.upper():
        return "AE"
    if "ONS" in source.upper():
        return "BR"
    return "UNKNOWN"

def parse_input(input_data: str) -> list[dict]:
    """解析输入 (支持 JSON 数组、JSON Lines、单行 JSON)"""
    input_data = input_data.strip()
    if not input_data:
        return []
    
    # 尝试 JSON 数组
    try:
        data = json.loads(input_data)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
    except json.JSONDecodeError:
        pass
    
    # 尝试 JSON Lines (每行一个 JSON)
    lines = input_data.strip().split('\n')
    results = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            # 非 JSON 行，尝试简单解析 (标题 | 链接 | 日期)
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 1:
                results.append({"title": parts[0], "url": parts[1] if len(parts) > 1 else "", "date": parts[2] if len(parts) > 2 else ""})
    return results

def load_existing_hits(data_dir: Path, date_str: str) -> list[dict]:
    """加载现有命中记录"""
    file_path = data_dir / f"tender_hits_{date_str}.json"
    if file_path.exists():
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def save_hits(data_dir: Path, date_str: str, hits: list[dict]):
    """保存命中记录 (去重)"""
    file_path = data_dir / f"tender_hits_{date_str}.json"
    # 去重：基于 URL
    seen_urls = set()
    unique_hits = []
    for h in hits:
        url = h.get("url", "")
        if url and url in seen_urls:
            continue
        seen_urls.add(url)
        unique_hits.append(h)
    file_path.write_text(json.dumps(unique_hits, ensure_ascii=False, indent=2))
    print(f"[SAVE] {len(unique_hits)} unique hits -> {file_path}")

def main():
    """主函数 - 支持三种模式:
    1. 管道输入: cat alerts.json | python tender_email_parser.py
    2. 文件参数: python tender_email_parser.py alerts.json
    3. 交互模式: python tender_email_parser.py (逐行粘贴，空行结束)
    """
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 读取输入
    if len(sys.argv) > 1:
        # 文件参数
        input_file = Path(sys.argv[1])
        if input_file.exists():
            input_text = input_file.read_text(encoding="utf-8")
        else:
            print(f"[ERROR] File not found: {input_file}")
            return 1
    elif not sys.stdin.isatty():
        # 管道输入
        input_text = sys.stdin.read()
    else:
        # 交互模式
        print("=== 海外招标录入 (交互模式) ===")
        print("每行粘贴 JSON 或 '标题|链接|日期'，空行结束:")
        lines = []
        while True:
            line = input("> ").strip()
            if not line:
                break
            lines.append(line)
        input_text = "\n".join(lines)
    
    if not input_text.strip():
        print("[INFO] No input provided")
        return 0
    
    # 解析输入
    raw_records = parse_input(input_text)
    print(f"[PARSE] Got {len(raw_records)} raw records")
    
    # 加载现有记录
    existing = load_existing_hits(data_dir, today)
    existing_urls = {h.get("url", "") for h in existing if h.get("url")}
    
    # 处理每条记录
    new_hits = []
    for rec in raw_records:
        title = rec.get("title", "").strip()
        url = rec.get("url", "").strip()
        date = rec.get("date", "").strip() or today
        source = normalize_source(rec.get("source", "MANUAL"))
        
        if not title:
            continue
        
        # 去重
        if url and url in existing_urls:
            print(f"  [SKIP] Duplicate URL: {url[:60]}")
            continue
        
        # 关键词匹配
        full_text = f"{title} {rec.get('raw_text', '')}"
        keywords = extract_keywords(full_text)
        if not keywords:
            print(f"  [SKIP] No keyword match: {title[:60]}")
            continue
        
        country = infer_country(full_text, source)
        
        hit = {
            "source": source,
            "title": title,
            "url": url,
            "date": date,
            "country": country,
            "keywords_matched": keywords,
            "raw_text": rec.get("raw_text", "")[:500],
            "detected_at": datetime.now().isoformat(),
        }
        new_hits.append(hit)
        existing_urls.add(url)
        print(f"  [HIT] [{source}] {title[:60]}... ({country}) keywords: {keywords}")
    
    # 合并保存
    all_hits = existing + new_hits
    save_hits(data_dir, today, all_hits)
    
    print(f"\n=== 完成: 新增 {len(new_hits)} 条，总计 {len(all_hits)} 条 ===")
    return 0

if __name__ == "__main__":
    sys.exit(main())