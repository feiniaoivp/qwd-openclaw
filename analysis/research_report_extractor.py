#!/usr/bin/env python3
"""
研报观点结构化抽取入库
========================
从东财策略研报/行业研报/券商晨报中抽取：
- 核心观点
- 目标价
- 评级（买入/增持/中性/减持/卖出）
- 关键逻辑/催化剂
- 风险提示

入库位置：data/research/
按日期分区存储，便于复盘时对照
"""

import os
import sys
import json
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from collections import defaultdict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)

# 导入统一数据路由器
from analysis.data_layer.router import get_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# 研报存储目录
RESEARCH_DIR = Path(WORKSPACE) / "data" / "research"
RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

# 评级标准化映射
RATING_MAP = {
    # 买入类
    "买入": "买入", "强烈推荐": "买入", "推荐": "买入", "强推": "买入",
    "增持": "增持", "优于大市": "增持", "跑赢大盘": "增持", "跑赢行业": "增持",
    "买入-A": "买入", "买入-B": "买入",
    # 中性类
    "中性": "中性", "持有": "中性", "谨慎推荐": "中性", "谨慎增持": "中性",
    "同步大市": "中性", "与大盘同步": "中性", "观望": "中性",
    "中性/增持": "中性", "中性/买入": "中性",
    # 减持/卖出类
    "减持": "减持", "劣于大市": "减持", "跑输大盘": "减持", "跑输行业": "减持",
    "卖出": "卖出", "强烈减持": "卖出",
    # 其他
    "无评级": "无评级", "暂无评级": "无评级", "首次覆盖": "首次覆盖"
}

# 目标价提取正则
TARGET_PRICE_PATTERNS = [
    r"目标价[：:]\s*([\d.]+)\s*[元元]",
    r"目标价位[：:]\s*([\d.]+)",
    r"目标股价[：:]\s*([\d.]+)",
    r"合理估值[：:]\s*([\d.]+)",
    r"估值[：:]\s*([\d.]+)\s*[元元]",
    r"target price[：:]\s*([\d.]+)",
    r"TP[：:]\s*([\d.]+)",
    r"(\d+\.?\d*)\s*元.*目标价",
]

# 评级提取正则
RATING_PATTERNS = [
    r"评级[：:]\s*([^\s，。,；;]+)",
    r"投资评级[：:]\s*([^\s，。,；;]+)",
    r"给予\s*([^\s，。,；;]+)\s*评级",
    r"维持\s*([^\s，。,；;]+)\s*评级",
    r"上调.*至\s*([^\s，。,；;]+)",
    r"下调.*至\s*([^\s，。,；;]+)",
    r"首次覆盖.*给予\s*([^\s，。,；;]+)",
]

# 核心观点提取正则（摘要/核心观点/投资逻辑/核心逻辑）
VIEWPOINT_PATTERNS = [
    r"核心观点[：:]\s*([^。]+)",
    r"投资逻辑[：:]\s*([^。]+)",
    r"核心逻辑[：:]\s*([^。]+)",
    r"投资要点[：:]\s*([^。]+)",
    r"摘要[：:]\s*([^。]+)",
    r"核心内容[：:]\s*([^。]+)",
    r"报告精华[：:]\s*([^。]+)",
]

# 关键催化剂关键词
CATALYST_KEYWORDS = [
    "业绩超预期", "业绩预增", "扭亏", "新产品", "新业务", "拓展",
    "中标", "重大合同", "订单", "产能释放", "降本增效", "并购",
    "重组", "注资", "股权激励", "回购", "增持", "分红", "派息",
    "政策利好", "行业景气", "需求爆发", "供给侧改革", "去产能",
    "技术突破", "专利", "独家", "垄断", "龙头", "先发优势"
]

# 风险提示关键词
RISK_KEYWORDS = [
    "业绩不及预期", "业绩预亏", "下游需求", "原材料上涨", "成本上升",
    "竞争加剧", "政策风险", "行业下行", "产能过剩", "库存高企",
    "应收账款", "坏账", "现金流", "汇率波动", "海外风险",
    "关联交易", "内控", "审计", "减持", "质押", "债务违约"
]


def normalize_rating(rating: str) -> str:
    """标准化评级"""
    rating = rating.strip()
    for key, val in RATING_MAP.items():
        if key in rating:
            return val
    return rating


def extract_target_price(text: str) -> Optional[float]:
    """从文本中提取目标价"""
    for pattern in TARGET_PRICE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, IndexError):
                continue
    return None


def extract_rating(text: str) -> Optional[str]:
    """从文本中提取评级"""
    for pattern in RATING_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            rating = match.group(1).strip()
            return normalize_rating(rating)
    return None


def extract_viewpoints(text: str) -> List[str]:
    """提取核心观点"""
    viewpoints = []
    for pattern in VIEWPOINT_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for m in matches:
            m = m.strip()
            if len(m) > 10 and m not in viewpoints:
                viewpoints.append(m)
    return viewpoints[:5]  # 最多 5 条


def extract_catalysts(text: str) -> List[str]:
    """提取关键催化剂"""
    found = []
    for kw in CATALYST_KEYWORDS:
        if kw in text and kw not in found:
            found.append(kw)
    return found[:5]


def extract_risks(text: str) -> List[str]:
    """提取风险提示"""
    found = []
    for kw in RISK_KEYWORDS:
        if kw in text and kw not in found:
            found.append(kw)
    return found[:5]


def parse_report_content(content: str) -> Dict[str, Any]:
    """解析研报正文内容，提取结构化字段"""
    result = {
        "target_price": None,
        "rating": None,
        "viewpoints": [],
        "catalysts": [],
        "risks": [],
        "raw_excerpt": content[:500]  # 保留前 500 字符供人工核对
    }
    
    if not content:
        return result
    
    # 提取各字段
    result["target_price"] = extract_target_price(content)
    result["rating"] = extract_rating(content)
    result["viewpoints"] = extract_viewpoints(content)
    result["catalysts"] = extract_catalysts(content)
    result["risks"] = extract_risks(content)
    
    return result


def fetch_strategy_reports(date: str = None) -> List[Dict]:
    """抓取东财策略研报 (使用 stock_research_report_em)"""
    router = get_router()
    try:
        import akshare as ak
        df = ak.stock_research_report_em()
        if df is None or df.empty:
            return []
        
        reports = []
        for _, row in df.iterrows():
            # 过滤日期 - akshare 返回 datetime.date 对象
            date_val = row.get("日期")
            if date_val is None:
                continue
            pub_date = str(date_val)[:10]
            if date and pub_date != date:
                continue
            
            title = str(row.get("报告名称", ""))
            org = str(row.get("机构", ""))
            rating = str(row.get("东财评级", ""))
            industry = str(row.get("行业", ""))
            url = str(row.get("报告PDF链接", ""))
            
            # 构造内容用于解析
            content = f"{title} 评级:{rating} 行业:{industry} 机构:{org}"
            
            parsed = parse_report_content(content)
            # 覆盖评级（直接从字段获取更准确）
            if rating:
                parsed["rating"] = normalize_rating(rating)
            
            report = {
                "source": "东财策略研报",
                "date": pub_date,
                "title": title,
                "org": org,
                "industry": industry,
                "rating_raw": rating,
                "url": url,
                "content": content,
                **parsed
            }
            reports.append(report)
        
        return reports
    except Exception as e:
        log.warning(f"抓取策略研报失败: {e}")
        return []


def fetch_industry_reports(date: str = None) -> List[Dict]:
    """抓取东财行业研报 - 从通用研报接口筛选行业类"""
    router = get_router()
    try:
        import akshare as ak
        df = ak.stock_research_report_em()
        if df is None or df.empty:
            return []
        
        reports = []
        for _, row in df.iterrows():
            # 过滤日期 - akshare 返回 datetime.date 对象
            date_val = row.get("日期")
            if date_val is None:
                continue
            pub_date = str(date_val)[:10]
            if date and pub_date != date:
                continue
            
            title = str(row.get("报告名称", ""))
            org = str(row.get("机构", ""))
            rating = str(row.get("东财评级", ""))
            industry = str(row.get("行业", ""))
            url = str(row.get("报告PDF链接", ""))
            
            # 仅保留行业相关研报（标题含行业/板块/策略等）
            if not any(kw in title for kw in ["行业", "板块", "策略", "宏观", "展望", "专题"]):
                continue
            
            content = f"{title} 评级:{rating} 行业:{industry} 机构:{org}"
            
            parsed = parse_report_content(content)
            if rating:
                parsed["rating"] = normalize_rating(rating)
            
            report = {
                "source": "东财行业研报",
                "date": pub_date,
                "title": title,
                "org": org,
                "industry": industry,
                "rating_raw": rating,
                "url": url,
                "content": content,
                **parsed
            }
            reports.append(report)
        
        return reports
    except Exception as e:
        log.warning(f"抓取行业研报失败: {e}")
        return []


def fetch_broker_reports(date: str = None) -> List[Dict]:
    """抓取券商晨报/晨会纪要 - 从通用研报接口筛选券商类"""
    router = get_router()
    try:
        import akshare as ak
        df = ak.stock_research_report_em()
        if df is None or df.empty:
            return []
        
        reports = []
        for _, row in df.iterrows():
            # 过滤日期 - akshare 返回 datetime.date 对象
            date_val = row.get("日期")
            if date_val is None:
                continue
            pub_date = str(date_val)[:10]
            if date and pub_date != date:
                continue
            
            title = str(row.get("报告名称", ""))
            org = str(row.get("机构", ""))
            rating = str(row.get("东财评级", ""))
            industry = str(row.get("行业", ""))
            url = str(row.get("报告PDF链接", ""))
            
            # 仅保留券商晨报/晨会/早报/晨会纪要类
            if not any(kw in title for kw in ["晨报", "晨会", "早报", "晨纪", "策略周报", "每日", "早盘"]):
                continue
            
            content = f"{title} 评级:{rating} 行业:{industry} 机构:{org}"
            
            parsed = parse_report_content(content)
            if rating:
                parsed["rating"] = normalize_rating(rating)
            
            report = {
                "source": "券商晨报",
                "date": pub_date,
                "title": title,
                "org": org,
                "industry": industry,
                "rating_raw": rating,
                "url": url,
                "content": content,
                **parsed
            }
            reports.append(report)
        
        return reports
    except Exception as e:
        log.warning(f"抓取券商晨报失败: {e}")
        return []


def fetch_macro_reports(date: str = None) -> List[Dict]:
    """抓取东财宏观研报 - 从通用研报接口筛选宏观类"""
    router = get_router()
    try:
        import akshare as ak
        df = ak.stock_research_report_em()
        if df is None or df.empty:
            return []
        
        reports = []
        for _, row in df.iterrows():
            # 过滤日期 - akshare 返回 datetime.date 对象
            date_val = row.get("日期")
            if date_val is None:
                continue
            pub_date = str(date_val)[:10]
            if date and pub_date != date:
                continue
            
            title = str(row.get("报告名称", ""))
            org = str(row.get("机构", ""))
            rating = str(row.get("东财评级", ""))
            industry = str(row.get("行业", ""))
            url = str(row.get("报告PDF链接", ""))
            
            # 仅保留宏观相关研报
            if not any(kw in title for kw in ["宏观", "经济", "货币", "财政", "央行", "利率", "汇率", "CPI", "PPI", "GDP", "PMI"]):
                continue
            
            content = f"{title} 评级:{rating} 行业:{industry} 机构:{org}"
            
            parsed = parse_report_content(content)
            if rating:
                parsed["rating"] = normalize_rating(rating)
            
            report = {
                "source": "东财宏观研报",
                "date": pub_date,
                "title": title,
                "org": org,
                "industry": industry,
                "rating_raw": rating,
                "url": url,
                "content": content,
                **parsed
            }
            reports.append(report)
        
        return reports
    except Exception as e:
        log.warning(f"抓取宏观研报失败: {e}")
        return []


def save_reports_by_date(reports: List[Dict], date: str = None):
    """按日期保存研报到 data/research/"""
    if not reports:
        return
    
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    
    # 按来源分组
    by_source = defaultdict(list)
    for r in reports:
        by_source[r.get("source", "未知")].append(r)
    
    # 保存每个来源的文件
    for source, items in by_source.items():
        safe_source = source.replace("/", "_").replace(" ", "_")
        filename = RESEARCH_DIR / f"{date}_{safe_source}.json"
        
        # 读取现有文件合并（去重）
        existing = []
        if filename.exists():
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass
        
        # 去重：基于标题+机构
        existing_keys = {(r.get("title", ""), r.get("org", "")) for r in existing}
        for item in items:
            key = (item.get("title", ""), item.get("org", ""))
            if key not in existing_keys:
                existing.append(item)
                existing_keys.add(key)
        
        # 保存
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        
        log.info(f"📄 保存研报: {filename.name} ({len(existing)} 条)")


def load_reports(date: str = None, source: str = None) -> List[Dict]:
    """加载指定日期/来源的研报"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    
    all_reports = []
    
    if source:
        safe_source = source.replace("/", "_").replace(" ", "_")
        filename = RESEARCH_DIR / f"{date}_{safe_source}.json"
        if filename.exists():
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log.warning(f"加载研报失败 {filename}: {e}")
                return []
        return []
    
    # 加载该日期所有来源
    for filepath in RESEARCH_DIR.glob(f"{date}_*.json"):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                all_reports.extend(json.load(f))
        except Exception as e:
            log.warning(f"加载研报失败 {filepath}: {e}")
    
    return all_reports


def get_reports_for_stock(symbol: str, name: str, days: int = 30) -> List[Dict]:
    """获取某只股票相关的研报（近 N 天）"""
    cutoff = datetime.now() - timedelta(days=days)
    results = []
    
    for filepath in RESEARCH_DIR.glob("*.json"):
        try:
            # 从文件名提取日期
            date_str = filepath.stem.split("_")[0]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date < cutoff:
                continue
            
            with open(filepath, "r", encoding="utf-8") as f:
                reports = json.load(f)
            
            for r in reports:
                title = r.get("title", "")
                content = r.get("content", "")
                # 简单匹配：股票代码或名称在标题/内容中
                if symbol in title or name in title or symbol in content or name in content:
                    r["_matched_file"] = filepath.name
                    results.append(r)
        except Exception as e:
            log.warning(f"搜索研报失败 {filepath}: {e}")
    
    # 按日期倒序
    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    return results


def generate_research_summary(date: str = None) -> str:
    """生成研报摘要（用于复盘/推送）"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    
    reports = load_reports(date)
    if not reports:
        return f"📊 {date} 无新增研报"
    
    lines = [f"📊 **{date} 研报速递** ({len(reports)} 篇)", ""]
    
    # 按来源分组
    by_source = defaultdict(list)
    for r in reports:
        by_source[r.get("source", "未知")].append(r)
    
    for source, items in by_source.items():
        lines.append(f"📑 **{source}** ({len(items)} 篇)")
        
        # 有评级/目标价的优先展示
        rated = [r for r in items if r.get("rating") or r.get("target_price")]
        unrated = [r for r in items if not r.get("rating") and not r.get("target_price")]
        
        for r in rated[:5]:  # 每源最多展示 5 条
            title = r.get("title", "")[:60]
            org = r.get("org", "")
            rating = r.get("rating", "")
            target = r.get("target_price")
            
            line = f"  • {title} [{org}]"
            if rating:
                line += f" | {rating}"
            if target:
                line += f" | 目标价 ¥{target}"
            lines.append(line)
        
        if len(rated) > 5:
            lines.append(f"  ... 等 {len(rated)} 条有评级研报")
        
        if unrated:
            lines.append(f"  📝 其余 {len(unrated)} 条仅摘要")
        
        lines.append("")
    
    return "\n".join(lines)


def run_daily_extraction(date: str = None):
    """每日研报抽取主流程"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    
    log.info(f"📚 开始抽取 {date} 研报...")
    
    all_reports = []
    
    # 1. 策略研报
    log.info("  [1/4] 抓取东财策略研报...")
    strategy = fetch_strategy_reports(date)
    all_reports.extend(strategy)
    log.info(f"    获取 {len(strategy)} 条")
    
    # 2. 行业研报
    log.info("  [2/4] 抓取东财行业研报...")
    industry = fetch_industry_reports(date)
    all_reports.extend(industry)
    log.info(f"    获取 {len(industry)} 条")
    
    # 3. 券商晨报
    log.info("  [3/4] 抓取券商晨报...")
    broker = fetch_broker_reports(date)
    all_reports.extend(broker)
    log.info(f"    获取 {len(broker)} 条")
    
    # 4. 宏观研报
    log.info("  [4/4] 抓取东财宏观研报...")
    macro = fetch_macro_reports(date)
    all_reports.extend(macro)
    log.info(f"    获取 {len(macro)} 条")
    
    # 保存
    save_reports_by_date(all_reports, date)
    
    # 生成摘要
    summary = generate_research_summary(date)
    log.info(f"\n{summary}")
    
    return all_reports, summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="研报观点结构化抽取")
    parser.add_argument("--date", type=str, default=None, help="目标日期 YYYY-MM-DD")
    parser.add_argument("--stock", type=str, default=None, help="查询某股票相关研报（代码）")
    parser.add_argument("--name", type=str, default=None, help="股票名称")
    parser.add_argument("--days", type=int, default=30, help="回溯天数")
    args = parser.parse_args()
    
    if args.stock:
        reports = get_reports_for_stock(args.stock, args.name or "", args.days)
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        reports, summary = run_daily_extraction(args.date)
        print("\n===== RESEARCH_SUMMARY =====")
        print(summary)