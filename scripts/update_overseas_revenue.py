#!/usr/bin/env python3
"""
海外营收/订单追踪器 - 更新 data/overseas_revenue_tracker.csv
运行方式: python scripts/update_overseas_revenue.py [--full]
  --full: 季报日全量刷新(人工录入模式)
常规运行: 仅更新最新公告增量(抓取近30天公告，关键词筛选)
输出: data/overseas_revenue_tracker.csv + 变更日志
"""

import csv
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
import argparse

# 追踪标的配置 (8只核心 + 自选池重叠股)
TRACK_TARGETS = [
    {"code": "002130", "name": "沃尔核材", "market": "SZ", "focus": "电缆附件/绝缘材料/海缆出口"},
    {"code": "600312", "name": "平高电气", "market": "SH", "focus": "GIS/变压器/特高压出口"},
    {"code": "002028", "name": "思源电气", "market": "SZ", "focus": "换流阀/GIS/变压器出口"},
    {"code": "600089", "name": "特变电工", "market": "SH", "focus": "变压器/电缆/EPC出口"},
    {"code": "601179", "name": "中国西电", "market": "SH", "focus": "±1100kV换流阀/变压器出口"},
    {"code": "600406", "name": "国电南瑞", "market": "SH", "focus": "换流阀(47-49%市占)/电网自动化出口"},
    {"code": "000400", "name": "许继电气", "market": "SZ", "focus": "换流站设备/柔直技术出口"},
    {"code": "002270", "name": "华明装备", "market": "SZ", "focus": "分接开关/变压器零部件出口"},
]

# CSV 字段定义
CSV_FIELDS = [
    "date",           # 记录日期
    "code",           # 股票代码
    "name",           # 股票名称
    "overseas_revenue",      # 海外营收(亿元)
    "overseas_revenue_pct",  # 海外营收占比(%)
    "new_orders",            # 新签订单(亿元)
    "backlog_orders",        # 在手订单(亿元)
    "gross_margin",          # 毛利率(%)
    "source_announcement",   # 来源公告标题
    "source_url",            # 来源公告链接
    "quarter",               # 所属季度(如 2026Q3)
    "data_status",           # 数据状态: official(季报/公告正式口径) | estimate(估算) | pending(待披露)
    "notes",                 # 备注(新纳税单/首单等)
]

CSV_FILE = Path("data/overseas_revenue_tracker.csv")
CHANGELOG_FILE = Path("data/overseas_revenue_changelog.jsonl")

# 关键词配置
OVERSEAS_KEYWORDS = [
    "海外营收", "出口收入", "海外收入", "境外收入", "国际业务收入",
    "海外订单", "出口订单", "境外订单", "国际订单",
    "新签", "新增订单", "中标", "签约", "合同",
    "在手订单", "在手合同", "订单储备",
    "纳税单", "首单", "突破", "首次",
    "海外市场", "国际市场", "出海", "走出去",
]

FINANCIAL_REPORT_KEYWORDS = [
    "季度报告", "半年度报告", "年度报告", "业绩预告", "业绩快报",
    "财务报告", "经营数据", "营收", "利润",
]

def ensure_csv_exists():
    """确保CSV文件存在并写入表头"""
    if not CSV_FILE.exists():
        CSV_FILE.parent.mkdir(exist_ok=True)
        with CSV_FILE.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_FIELDS)

def load_existing_records() -> dict:
    """加载现有记录，返回 {(code, quarter): row_dict}"""
    records = {}
    if CSV_FILE.exists():
        with CSV_FILE.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = (row["code"], row["quarter"])
                records[key] = row
    return records

def append_changelog(entry: dict):
    """追加变更日志"""
    CHANGELOG_FILE.parent.mkdir(exist_ok=True)
    with CHANGELOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def fetch_latest_announcements(code: str, market: str, days_back: int = 30) -> list[dict]:
    """
    抓取最新公告 - 使用 akshare 东财公告接口
    返回: [{"title": "", "url": "", "date": "", "type": "", "code": "", "name": ""}, ...]
    """
    try:
        import akshare.stock_fundamental.stock_notice as sn
    except ImportError:
        print(f"  [WARN] akshare not available")
        return []
    
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    
    try:
        df = sn._stock_notice_report(
            security=code,
            symbol="全部",
            begin_date=start_date,
            end_date=end_date,
        )
    except KeyError as e:
        # akshare 内部 KeyError: '代码' - 通常是返回空数据但仍尝试构造列
        print(f"  [WARN] No announcements data for {code} (empty result)")
        return []
    except Exception as e:
        print(f"  [WARN] Fetch announcements failed for {code}: {e}")
        return []
    
    if df.empty:
        return []
    
    # 防御性检查：确保必要列存在
    required_cols = ["代码", "名称", "公告标题", "公告类型", "公告日期", "网址"]
    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        print(f"  [WARN] Missing columns for {code}: {missing_cols}, available: {list(df.columns)}")
        return []
    
    announcements = []
    for _, row in df.iterrows():
        announcements.append({
            "code": row.get("代码", code),
            "name": row.get("名称", ""),
            "title": row.get("公告标题", ""),
            "type": row.get("公告类型", ""),
            "date": str(row.get("公告日期", "")),
            "url": row.get("网址", ""),
        })
    
    return announcements

def keyword_match(text: str, keywords: list[str]) -> list[str]:
    """返回命中的关键词列表"""
    text_lower = text.lower()
    return [kw for kw in keywords if kw.lower() in text_lower]

def extract_numbers_from_text(text: str) -> list[float]:
    """从文本中提取数字(亿元/万元/万/亿)"""
    # 匹配: 1.23亿, 123亿元, 12300万, 1.23亿元, 123,456.78万元 等
    patterns = [
        r'([\d,]+\.?\d*)\s*亿元',
        r'([\d,]+\.?\d*)\s*亿[元人民币]?',
        r'([\d,]+\.?\d*)\s*万元',
        r'([\d,]+\.?\d*)\s*万[元人民币]?',
        r'([\d,]+\.?\d*)\s*百万美元',
        r'([\d,]+\.?\d*)\s*千万美元',
        r'([\d,]+\.?\d*)\s*万美元',
        r'([\d,]+\.?\d*)\s*亿美元',
        r'([\d,]+\.?\d*)\s*%',
    ]
    numbers = []
    for pat in patterns:
        matches = re.findall(pat, text)
        for m in matches:
            try:
                val = float(m.replace(',', ''))
                # 统一转为亿元
                if '亿' in pat and '万' not in pat:
                    numbers.append(val)
                elif '万' in pat and '亿' not in pat:
                    numbers.append(val / 10000)
                elif '百万' in pat:
                    numbers.append(val / 100)
                elif '千万' in pat:
                    numbers.append(val / 10)
                elif '万美' in pat or '万美元' in pat:
                    numbers.append(val * 7.2 / 10000)  # 粗略汇率换算
                elif '亿美元' in pat:
                    numbers.append(val * 7.2)
                elif '%' in pat:
                    numbers.append(val)  # 百分比单独处理
            except ValueError:
                pass
    return numbers

def infer_quarter_from_date(date_str: str) -> str:
    """从日期推断季度"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        quarter = (dt.month - 1) // 3 + 1
        return f"{dt.year}Q{quarter}"
    except Exception:
        return datetime.now().strftime("%YQ%q")

def parse_announcement_for_overseas_data(ann: dict, target: dict) -> dict | None:
    """
    从公告标题/类型判断是否包含海外营收/订单信息
    返回结构化字段供人工复核/录入
    """
    title = ann.get("title", "")
    ann_type = ann.get("type", "")
    date = ann.get("date", "")
    
    # 1. 关键词匹配
    matched_overseas = keyword_match(title, OVERSEAS_KEYWORDS)
    matched_financial = keyword_match(title, FINANCIAL_REPORT_KEYWORDS)
    
    if not matched_overseas and not matched_financial:
        return None
    
    # 2. 提取数字
    numbers = extract_numbers_from_text(title)
    
    # 3. 推断季度
    quarter = infer_quarter_from_date(date)
    
    # 4. 生成备注
    notes_parts = []
    if matched_overseas:
        notes_parts.append(f"关键词: {', '.join(matched_overseas)}")
    if matched_financial:
        notes_parts.append(f"财报类: {', '.join(matched_financial)}")
    if numbers:
        notes_parts.append(f"数字: {numbers}")
    
    # 返回待人工确认的结构化数据
    # 实际数值需人工从公告全文/附件中提取，这里只做标记
    return {
        "overseas_revenue": None,
        "overseas_revenue_pct": None,
        "new_orders": None,
        "backlog_orders": None,
        "gross_margin": None,
        "quarter": quarter,
        "notes": " | ".join(notes_parts) + " [需人工核实全文]",
    }

def manual_entry_mode(targets: list[dict]) -> list[dict]:
    """人工录入模式(用于季报日全量刷新)"""
    print("\n=== 人工录入模式 (季报全量刷新) ===")
    print("逐只录入，留空跳过。")
    print("格式: 海外营收(亿) 占比(%) 新签(亿) 在手(亿) 毛利率(%) 季度 [official|estimate] 备注")
    print("(第7段可选，默认 estimate；无正式出处请勿标 official)")
    new_rows = []
    for t in targets:
        print(f"\n--- {t['code']} {t['name']} ---")
        line = input("输入> ").strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 6:
            print("  字段不足，跳过")
            continue
        try:
            row = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "code": t["code"],
                "name": t["name"],
                "overseas_revenue": float(parts[0]),
                "overseas_revenue_pct": float(parts[1]),
                "new_orders": float(parts[2]),
                "backlog_orders": float(parts[3]),
                "gross_margin": float(parts[4]),
                "source_announcement": "人工录入(季报)",
                "source_url": "",
                "quarter": parts[5],
                "data_status": parts[6] if len(parts) > 6 and parts[6] in ("official", "estimate", "pending") else "estimate",
                "notes": " ".join(parts[7:]) if len(parts) > 7 else "",
            }
            new_rows.append(row)
            print(f"  ✓ 录入: {row['quarter']} 海外营收{row['overseas_revenue']}亿 占比{row['overseas_revenue_pct']}%")
        except ValueError:
            print("  格式错误，跳过")
    return new_rows

def auto_incremental_mode(targets: list[dict], days_back: int = 30) -> list[dict]:
    """自动增量模式(抓取最新公告关键词筛选)"""
    print(f"\n=== 自动增量模式 (抓取近{days_back}天公告+关键词筛选) ===")
    new_rows = []
    for t in targets:
        print(f"[FETCH] {t['code']} {t['name']}...")
        anns = fetch_latest_announcements(t["code"], t["market"], days_back=days_back)
        if not anns:
            print(f"  [INFO] 近30天无公告")
            continue
        
        hit_count = 0
        for ann in anns:
            parsed = parse_announcement_for_overseas_data(ann, t)
            if parsed:
                hit_count += 1
                row = {
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "code": t["code"],
                    "name": t["name"],
                    **parsed,
                    "source_announcement": ann["title"],
                    "source_url": ann["url"],
                }
                new_rows.append(row)
                print(f"  [HIT] {ann['title'][:60]}... ({ann['date']})")
                print(f"       -> 季度: {parsed['quarter']} | 备注: {parsed['notes']}")
        
        if hit_count == 0:
            print(f"  [INFO] 近30天公告 {len(anns)} 条，无海外/财报关键词命中")
    
    print(f"\n[SUMMARY] 共命中 {len(new_rows)} 条待核实公告")
    return new_rows

def merge_and_save(new_rows: list[dict], existing: dict):
    """合并新旧记录并保存CSV"""
    updated = 0
    inserted = 0
    
    for row in new_rows:
        key = (row["code"], row["quarter"])
        if key in existing:
            old = existing[key]
            changed = any(str(old.get(f, "")) != str(row.get(f, "")) for f in CSV_FIELDS if f not in ["date", "source_announcement", "source_url", "notes"])
            if changed:
                # 保护: 不得用 estimate/pending 覆盖已有的 official 正式口径数据
                if old.get("data_status") == "official" and row.get("data_status") != "official":
                    print(f"  [SKIP] {row['code']} {row['quarter']} 已有 official 数据，拒绝被 {row.get('data_status')} 覆盖")
                    continue
                existing[key] = row
                updated += 1
                append_changelog({
                    "action": "UPDATE",
                    "timestamp": datetime.now().isoformat(),
                    "code": row["code"],
                    "quarter": row["quarter"],
                    "changes": {f: {"old": old.get(f), "new": row.get(f)} for f in CSV_FIELDS if old.get(f) != row.get(f)},
                })
                print(f"  [UPDATE] {row['code']} {row['quarter']}")
        else:
            existing[key] = row
            inserted += 1
            append_changelog({
                "action": "INSERT",
                "timestamp": datetime.now().isoformat(),
                "code": row["code"],
                "quarter": row["quarter"],
                "data": row,
            })
            print(f"  [INSERT] {row['code']} {row['quarter']}")
    
    # 重写CSV(按代码+季度排序)
    sorted_rows = sorted(existing.values(), key=lambda r: (r["code"], r["quarter"]))
    with CSV_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(sorted_rows)
    
    print(f"\n[SAVE] CSV updated: {inserted} inserted, {updated} updated -> {CSV_FILE}")
    return inserted, updated

def main():
    parser = argparse.ArgumentParser(description="海外营收/订单追踪器更新")
    parser.add_argument("--full", action="store_true", help="季报日全量刷新(人工录入模式)")
    parser.add_argument("--days", type=int, default=30, help="增量模式回溯天数(默认30)")
    args = parser.parse_args()
    
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"=== 海外营收追踪器更新 {today} ===")
    print(f"模式: {'全量刷新(人工录入)' if args.full else f'增量自动(回溯{args.days}天)'}")
    
    ensure_csv_exists()
    existing = load_existing_records()
    print(f"[LOAD] Existing records: {len(existing)}")

    # 瘦身：仅保留配置内标的（防止已从 TRACK_TARGETS 移除的标的残留）
    valid_codes = {t["code"] for t in TRACK_TARGETS}
    for k in [k for k in list(existing) if k[0] not in valid_codes]:
        print(f"[PRUNE] 移除不在 TRACK_TARGETS 的记录: {k}")
        existing.pop(k)
    
    if args.full:
        new_rows = manual_entry_mode(TRACK_TARGETS)
    else:
        new_rows = auto_incremental_mode(TRACK_TARGETS, days_back=args.days)
    
    if not new_rows:
        print("[INFO] No new data to process")
        return 0
    
    merge_and_save(new_rows, existing)
    print("\n=== 完成 ===")
    return 0

if __name__ == "__main__":
    sys.exit(main())