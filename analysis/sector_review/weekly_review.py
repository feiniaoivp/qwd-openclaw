#!/usr/bin/env python3
"""
周复盘报告生成器 - 特高压/电网出海板块
运行方式: 
  python analysis/sector_review/weekly_review.py                    # 自动检测本周
  python analysis/sector_review/weekly_review.py --monday 2026-09-08 --friday 2026-09-12  # 指定周范围
输出: analysis/sector_review/weekly_YYYY-MM-DD.md
"""

import csv
import json
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# 导入价格分析模块
sys.path.insert(0, str(Path(__file__).parent))
from price_analyzer import CORE_STOCKS, analyze_all_stocks

# 核心标的统一从 price_analyzer 导入(其又从 data/power_overseas_config.json 读取)，
# 保证全链路单一数据源，避免各处硬编码代码漂移。

DATA_DIR = Path("data")
REVIEW_DIR = Path("analysis/sector_review")

def load_json(filepath: Path) -> dict | list | None:
    """安全加载JSON"""
    if filepath.exists():
        try:
            return json.loads(filepath.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARN] Load failed {filepath}: {e}")
    return None

def load_revenue_csv(filepath: Path, monday: str, friday: str) -> list[dict]:
    """加载本周相关的营收数据。

    仅收录 data_status=official 或 estimate 且日期在本周内的记录；
    pending(待披露) 与日期为空的占位行不计入周报，避免拿空值/假值污染结论。
    """
    records = []
    if filepath.exists():
        with filepath.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                status = (row.get("data_status") or "").strip()
                d = (row.get("date") or "").strip()
                if status == "pending" or not d:
                    continue
                if monday <= d <= friday:
                    records.append(row)
    return records

def get_week_range(monday_override: str = None, friday_override: str = None) -> tuple[str, str]:
    """获取周一至周五日期字符串"""
    if monday_override and friday_override:
        return monday_override, friday_override
    
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    return monday.strftime("%Y-%m-%d"), friday.strftime("%Y-%m-%d")

def detect_available_week() -> tuple[str, str]:
    """根据现有数据文件自动检测可用的周范围"""
    # 找所有 commodity_fx 文件的日期
    dates = []
    for f in DATA_DIR.glob("commodity_fx_*.json"):
        try:
            date_str = f.stem.replace("commodity_fx_", "")
            datetime.strptime(date_str, "%Y-%m-%d")
            dates.append(date_str)
        except ValueError:
            pass
    
    if not dates:
        return get_week_range()
    
    dates.sort()
    # 找连续5个工作日的最新一周
    # 简化：取最新日期所在的周
    latest = datetime.strptime(dates[-1], "%Y-%m-%d")
    monday = latest - timedelta(days=latest.weekday())
    friday = monday + timedelta(days=4)
    return monday.strftime("%Y-%m-%d"), friday.strftime("%Y-%m-%d")

def md5_of(path: Path) -> str | None:
    """文件内容 MD5(用于检测多日数据被同一文件复制的伪造)"""
    import hashlib
    if not path.exists():
        return None
    return hashlib.md5(path.read_bytes()).hexdigest()

def validate_data_integrity(data: dict, monday: str) -> list[str]:
    """数据真实性守卫: 返回警告列表。

    捕获两类历史事故:
      1) commodity_fx_*.json 文件名是日期 A，内部 date 字段却是 B(拿今天快照改名充数)
      2) 多日文件 md5 完全一致(同一份数据复制多份)，导致周变化全为 +0.00%
    这类假数据会让周报静默输出 0 广度/0.00% 变化，必须显式报错而非静默。
    """
    warnings = []
    fx = data.get("commodity_fx", {})

    for day, payload in fx.items():
        inner = payload.get("date", "")
        if inner and inner != day:
            warnings.append(
                f"[FABRICATION] commodity_fx_{day}.json 内部 date={inner} ≠ 文件名日期，疑似拿其他日快照改名充数"
            )

    hashes = {}
    for day in sorted(fx.keys()):
        h = md5_of(DATA_DIR / f"commodity_fx_{day}.json")
        if h:
            hashes.setdefault(h, []).append(day)
    for h, days in hashes.items():
        if len(days) > 1:
            warnings.append(
                f"[DUPLICATE] 以下 {len(days)} 天 commodity_fx 文件内容完全相同({h[:8]}): {', '.join(days)} — 周变化将全部为 0，不可信"
            )

    for day, text in data.get("daily_reviews", {}).items():
        if "2026-09-XX" in text or "数据待实时接口填充" in text:
            warnings.append(
                f"[TEMPLATE] {day}_evening_review.md 仍是未填充模板(含占位符)，非真实行情生成"
            )

    if len(fx) < 5:
        warnings.append(
            f"[MISSING] 本周大宗商品汇率仅 {len(fx)}/5 天有数据，周变化区间不完整"
        )

    return warnings

def load_weekly_data(monday: str, friday: str) -> dict:
    """加载本周所有数据文件"""
    data = {
        "daily_reviews": {},
        "commodity_fx": {},
        "tender_hits": [],
        "revenue_updates": [],
    }
    
    # 1. 每日收盘复盘
    for i in range(5):
        day = (datetime.strptime(monday, "%Y-%m-%d") + timedelta(days=i)).strftime("%Y-%m-%d")
        review_file = REVIEW_DIR / "daily" / f"{day}_evening_review.md"
        if review_file.exists():
            data["daily_reviews"][day] = review_file.read_text(encoding="utf-8")
    
    # 2. 大宗商品汇率
    for i in range(5):
        day = (datetime.strptime(monday, "%Y-%m-%d") + timedelta(days=i)).strftime("%Y-%m-%d")
        fx_file = DATA_DIR / f"commodity_fx_{day}.json"
        fx_data = load_json(fx_file)
        if fx_data:
            data["commodity_fx"][day] = fx_data
    
    # 3. 招标命中
    for i in range(5):
        day = (datetime.strptime(monday, "%Y-%m-%d") + timedelta(days=i)).strftime("%Y-%m-%d")
        tender_file = DATA_DIR / f"tender_hits_{day}.json"
        tender_data = load_json(tender_file)
        if tender_data:
            data["tender_hits"].extend(tender_data)
    
    # 4. 营收更新 (读取本周录入的记录)
    revenue_file = DATA_DIR / "overseas_revenue_tracker.csv"
    data["revenue_updates"] = load_revenue_csv(revenue_file, monday, friday)
    
    return data

def parse_daily_review_for_price(text: str, code: str) -> dict | None:
    """从每日复盘文本解析个股价格数据 (简易版)"""
    # TODO: 实现正则解析表格
    return None

def analyze_price_action(daily_reviews: dict, monday: str, friday: str) -> dict:
    """分析本周价格行为 - 调用 price_analyzer 基于 baostock 周线数据"""
    try:
        results = analyze_all_stocks(monday, friday)
        if not results:
            return {"weekly_performance": {}, "sector_breadth": {"up": 0, "down": 0, "flat": 0}, "leaders": [], "laggards": [], "note": "baostock 无数据"}
        
        # 转换为周报所需格式
        weekly_perf = {}
        up = down = flat = 0
        for r in results:
            chg = r["chg_pct"]
            if chg > 0.1:
                up += 1
            elif chg < -0.1:
                down += 1
            else:
                flat += 1
            weekly_perf[r["code"]] = {
                "chg_pct": chg,
                "volume_ratio": r["volume_ratio"],
                "volume_vs_ma5": r["volume_vs_ma5"],
                "ma_trend": r["MA_trend"],
                "macd_signal": r["MACD_signal"],
                "rsi": r["RSI"],
                "bb_position": r["BB_position"],
            }
        
        # 领涨/领跌
        sorted_res = sorted(results, key=lambda x: x["chg_pct"], reverse=True)
        leaders = [f"{r['code']} {r['name']}({r['chg_pct']:+.2f}%)" for r in sorted_res[:2]]
        laggards = [f"{r['code']} {r['name']}({r['chg_pct']:+.2f}%)" for r in sorted_res[-2:]]
        
        return {
            "weekly_performance": weekly_perf,
            "sector_breadth": {"up": up, "down": down, "flat": flat},
            "leaders": leaders,
            "laggards": laggards,
        }
    except Exception as e:
        print(f"[WARN] price_analyzer failed: {e}")
        return {"weekly_performance": {}, "sector_breadth": {"up": 0, "down": 0, "flat": 0}, "leaders": [], "laggards": [], "note": f"分析异常: {e}"}

def analyze_commodity_fx(commodity_fx: dict) -> dict:
    """分析大宗商品汇率周度变化"""
    if not commodity_fx:
        return {"summary": "无数据"}
    
    days = sorted(commodity_fx.keys())
    first, last = days[0], days[-1]
    first_data = commodity_fx[first]
    last_data = commodity_fx[last]
    
    changes = {}
    for category in ["fx", "commodities"]:
        if category in first_data and category in last_data:
            for key in first_data[category]:
                if key in last_data[category]:
                    f_price = first_data[category][key].get("price")
                    l_price = last_data[category][key].get("price")
                    if f_price and l_price and f_price != 0:
                        changes[key] = {
                            "name": last_data[category][key].get("name", key),
                            "unit": last_data[category][key].get("unit", ""),
                            "start": f_price,
                            "end": l_price,
                            "chg_pct": (l_price - f_price) / f_price * 100,
                        }
    return {"changes": changes, "period": f"{first} ~ {last}"}

def analyze_tenders(tender_hits: list) -> dict:
    """分析招标命中"""
    if not tender_hits:
        return {"total": 0, "by_source": {}, "key_projects": []}
    
    by_source = {}
    for h in tender_hits:
        src = h.get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1
    
    key_projects = [h for h in tender_hits if any(kw in h.get("title", "").upper() for kw in ["HVDC", "±800", "±1100", "GIS", "换流站"])]
    
    return {
        "total": len(tender_hits),
        "by_source": by_source,
        "key_projects": key_projects[:10],
    }

def generate_report(monday: str, friday: str, data: dict, analysis: dict) -> str:
    """生成Markdown报告"""
    lines = []
    lines.append(f"# 特高压/电网出海板块 周复盘报告 ({monday} ~ {friday})")
    lines.append("")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    
    # 1. 本周核心观察
    lines.append("## 1. 本周核心观察")
    lines.append("")
    
    perf = analysis.get("price_action", {})
    if perf.get("weekly_performance"):
        lines.append("### 1.1 个股周涨跌幅排名")
        lines.append("")
        lines.append("| 代码 | 名称 | 出海份额 | 周涨跌幅 | 成交量比 | 均线结构 | MACD |")
        lines.append("|------|------|----------|----------|----------|----------|------|")
        for code, p in sorted(perf["weekly_performance"].items(), key=lambda x: x[1].get("chg_pct", 0), reverse=True):
            name = next((s["name"] for s in CORE_STOCKS if s["code"] == code), code)
            oversea = next((s["oversea_pct"] for s in CORE_STOCKS if s["code"] == code), 0)
            lines.append(f"| {code} | {name} | {oversea}% | {p.get('chg_pct',0):+.2f}% | {p.get('volume_ratio',0):.2f}x | {p.get('ma_trend','-')} | {p.get('macd_signal','-')} |")
        lines.append("")
    
    breadth = perf.get("sector_breadth", {})
    lines.append(f"**板块广度**: 上涨 {breadth.get('up',0)} 只 | 下跌 {breadth.get('down',0)} 只 | 平盘 {breadth.get('flat',0)} 只")
    if perf.get("note"):
        lines.append(f"\n> {perf['note']}")
    lines.append("")
    
    if perf.get("leaders"):
        lines.append(f"**领涨**: {', '.join(perf['leaders'])}")
    if perf.get("laggards"):
        lines.append(f"**领跌**: {', '.join(perf['laggards'])}")
    lines.append("")
    
    # 2. 事件驱动复盘
    lines.append("## 2. 事件驱动复盘")
    lines.append("")
    
    tender_analysis = analysis.get("tenders", {})
    if tender_analysis.get("total", 0) > 0:
        lines.append("### 2.1 海外招标/中标跟踪")
        lines.append(f"本周命中 **{tender_analysis['total']}** 条相关招标公告")
        lines.append("")
        lines.append("| 来源 | 命中数 |")
        lines.append("|------|--------|")
        for src, cnt in tender_analysis.get("by_source", {}).items():
            lines.append(f"| {src} | {cnt} |")
        lines.append("")
        
        if tender_analysis.get("key_projects"):
            lines.append("**重点项目**:")
            for p in tender_analysis["key_projects"][:5]:
                lines.append(f"- [{p.get('source_name','')}] {p.get('title','')[:80]} ({p.get('date','')})")
            lines.append("")
    
    lines.append("### 2.2 特高压核准/开工进展")
    lines.append("- 本周国家能源局/发改委新增核准/开工公告: **待人工填入**")
    lines.append("- 受益标的映射: 思源电气/平高电气/许继电气/特变电工/中国西电")
    lines.append("")
    
    # 3. 成本端 & 汇率
    lines.append("## 3. 成本端 & 汇率环境")
    lines.append("")
    cf_analysis = analysis.get("commodity_fx", {})
    if cf_analysis.get("changes"):
        lines.append(f"统计周期: {cf_analysis['period']}")
        lines.append("")
        lines.append("| 指标 | 期初 | 期末 | 周变化 | 影响判断 |")
        lines.append("|------|------|------|--------|----------|")
        for key, chg in cf_analysis["changes"].items():
            impact = "利好出海毛利" if (("copper" in key.lower() or "cu" in key.lower() or "lme" in key.lower()) and chg["chg_pct"] < 0) or ("cny" in key.lower() or "usdcnh" in key.lower()) and chg["chg_pct"] > 0 else "中性/待观察"
            lines.append(f"| {chg['name']} | {chg['start']:.2f} | {chg['end']:.2f} | {chg['chg_pct']:+.2f}% | {impact} |")
        lines.append("")
    else:
        lines.append("本周大宗商品/汇率数据不全，待补齐。")
        lines.append("")
    
    # 4. 基本面兑现进度
    lines.append("## 4. 基本面兑现进度 (季报/公告)")
    lines.append("")
    lines.append("| 代码 | 名称 | 最新季度 | 数据状态 | 海外营收(亿) | 占比 | 新签订单(亿) | 在手订单(亿) | 毛利率 | 关键进展 |")
    lines.append("|------|------|----------|----------|--------------|------|--------------|--------------|--------|----------|")
    
    # 从 revenue_updates 填充
    rev_by_code_q = {}
    for r in data.get("revenue_updates", []):
        key = (r["code"], r["quarter"])
        rev_by_code_q[key] = r
    
    STATUS_LABEL = {"official": "✅官方", "estimate": "⚠️估算", "pending": "⏳待披露"}
    for s in CORE_STOCKS:
        latest_q = "2026Q3"
        rev = rev_by_code_q.get((s["code"], latest_q))
        if rev:
            st = STATUS_LABEL.get(rev.get("data_status", ""), rev.get("data_status", "-"))
            lines.append(f"| {s['code']} | {s['name']} | {latest_q} | {st} | {rev.get('overseas_revenue','-')} | {rev.get('overseas_revenue_pct','-')}% | {rev.get('new_orders','-')} | {rev.get('backlog_orders','-')} | {rev.get('gross_margin','-')}% | {rev.get('notes','-')} |")
        else:
            lines.append(f"| {s['code']} | {s['name']} | {latest_q} | ⏳待披露 | - | - | - | - | - | 2026Q3季报未披露 |")
    lines.append("")
    
    # 5. 技术面周度结构
    lines.append("## 5. 技术面周度结构")
    lines.append("")
    lines.append("- **周线级别**: 待分析(需收盘后复盘)")
    lines.append("- **关键均线**: 20周线/60周线得失情况")
    lines.append("- **MACD周线**: 金叉/死叉/零轴上下")
    lines.append("- **筹码分布**: 获利盘比例、主力成本")
    lines.append("")
    
    # 6. 下周关键监测点
    lines.append("## 6. 下周关键监测点 (Action Items)")
    lines.append("")
    lines.append("| 优先级 | 监测点 | 触发条件 | 预案 |")
    lines.append("|--------|--------|----------|------|")
    lines.append("| 🔴 高 | 特高压新核准/开工 | 官方发布 | 即时推送 + 标的标注 |")
    lines.append("| 🔴 高 | 海外大单中标(>5亿) | 中标公告 | 更新在手订单台账 |")
    lines.append("| 🟡 中 | 核心标的技术企稳 | 站上20日线+MACD金叉 | 列入候选买入池 |")
    lines.append("| 🟡 中 | 铜价回落+人民币贬值 | 铜<9000 + USDCNH>7.25 | 关注出海毛利改善 |")
    lines.append("| 🟢 低 | 季报海外营收披露 | 营收增速>30% | 标记基本面兑现 |")
    lines.append("")
    
    # 7. 数据完整性检查
    lines.append("## 7. 数据完整性检查")
    lines.append("")
    lines.append(f"- 每日收盘复盘: {len(data['daily_reviews'])}/5 份")
    lines.append(f"- 大宗商品汇率: {len(data['commodity_fx'])}/5 日")
    lines.append(f"- 招标命中记录: {len(data['tender_hits'])} 条")
    lines.append(f"- 营收追踪器本周新增: {len(data['revenue_updates'])} 条")
    lines.append("")
    
    lines.append("---")
    lines.append("*报告由 `analysis/sector_review/weekly_review.py` 自动生成，人工复核后发布*")
    
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="周复盘报告生成器")
    parser.add_argument("--monday", help="周一日期 YYYY-MM-DD")
    parser.add_argument("--friday", help="周五日期 YYYY-MM-DD")
    parser.add_argument("--auto", action="store_true", help="自动检测可用数据周")
    args = parser.parse_args()
    
    if args.auto:
        monday, friday = detect_available_week()
        print(f"[AUTO] 检测到可用数据周: {monday} ~ {friday}")
    else:
        monday, friday = get_week_range(args.monday, args.friday)
    
    print(f"=== 生成周复盘报告 {monday} ~ {friday} ===")
    
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    
    data = load_weekly_data(monday, friday)

    # 数据真实性守卫: 先校验，若有伪造/模板/重复则显式报告
    warnings = validate_data_integrity(data, monday)
    if warnings:
        print("\n" + "=" * 60)
        print("⚠️  数据真实性警告 (周报结论可能不可信)")
        print("=" * 60)
        for w in warnings:
            print(f"  {w}")
        print("=" * 60 + "\n")
    else:
        print("[GUARD] 数据真实性检查通过\n")

    analysis = {
        "price_action": analyze_price_action(data["daily_reviews"], monday, friday),
        "commodity_fx": analyze_commodity_fx(data["commodity_fx"]),
        "tenders": analyze_tenders(data["tender_hits"]),
    }

    report = generate_report(monday, friday, data, analysis)

    # 将警告写入报告，避免读者把假数据当真实结论
    if warnings:
        banner = [
            "",
            "## ⚠️ 数据真实性警告",
            "",
            "本报告所用数据存在以下问题，结论仅供参考:",
            "",
        ]
        banner += [f"- {w}" for w in warnings]
        banner += ["", "---", ""]
        report = report.replace("## 1. 本周核心观察", "\n".join(banner) + "## 1. 本周核心观察", 1)
    
    output_file = REVIEW_DIR / f"weekly_{friday}.md"
    output_file.write_text(report, encoding="utf-8")
    print(f"[SAVE] -> {output_file}")
    
    print(f"\n=== 报告摘要 ===")
    print(f"每日复盘: {len(data['daily_reviews'])}/5")
    print(f"汇率商品: {len(data['commodity_fx'])}/5")
    print(f"招标命中: {analysis['tenders']['total']} 条")
    print(f"营收新增: {len(data['revenue_updates'])} 条")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())