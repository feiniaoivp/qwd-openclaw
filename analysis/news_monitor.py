#!/usr/bin/env python3
"""
财经新闻/舆情抓取监控 (News Monitor)
=========================================
三类数据源（东财接口，国内网络已验证可用）：
1. stock_news_em(symbol)     — 29只关注股个股新闻
2. stock_info_global_em()    — 宏观/行业政策快讯（200条）
3. stock_notice_report()     — 全市场公告（业绩预告/减持/立案）

增量抓取 + 归档到 data/news/：
- raw/        原始JSON（每日一份）
- last_run.json  增量状态（记录已抓取标题，避免重复）
- 输出 JSON 供 agent 解析，并生成可读摘要

运行: python3 analysis/news_monitor.py [--mode 盘前|盘后]
"""
import os, sys, json, hashlib
from datetime import datetime
from collections import OrderedDict

import akshare as ak
import pandas as pd

WORKSPACE = "/Users/duguke/.openclaw/workspace"
NEWS_DIR = os.path.join(WORKSPACE, "data", "news")
RAW_DIR = os.path.join(NEWS_DIR, "raw")
LAST_RUN_FILE = os.path.join(NEWS_DIR, "last_run.json")
os.makedirs(RAW_DIR, exist_ok=True)

# ── 27只关注股（2026-08-30 移除 300847/002180/601865）──
STOCKS = [
    ("002318","久立特材"),("300014","亿纬锂能"),("601066","中信建投"),
    ("600030","中信证券"),("300124","汇川技术"),("601995","中金公司"),
    ("600584","长电科技"),("002156","通富微电"),("002466","天齐锂业"),
    ("600036","招商银行"),("600570","恒生电子"),("605566","福莱蒽特"),
    ("000987","越秀资本"),("603308","应流股份"),("300285","国瓷材料"),
    ("002413","雷科防务"),("688981","中芯国际"),
    ("000157","中联重科"),("300719","安达维尔"),("601061","中信金属"),
    ("600660","福耀玻璃"),("002335","科华数据"),("601100","恒立液压"),
    ("600160","巨化股份"), ("600346","恒力石化"),
    ("000708","中信特钢"), ("300748","金力永磁"),
]


def load_last_run():
    if os.path.exists(LAST_RUN_FILE):
        with open(LAST_RUN_FILE) as f:
            return json.load(f)
    return {"seen_titles": [], "last_date": None}


def save_last_run(state):
    with open(LAST_RUN_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def is_new_title(title, seen_titles):
    """基于标题哈希判断是否增量（避免重复归档）"""
    h = hashlib.md5(title.encode("utf-8")).hexdigest()
    if h in seen_titles:
        return False, h
    return True, h


def fetch_stock_news(symbol, name, seen_titles):
    """抓取单只个股新闻，返回 {name,symbol,news:[{title,time,source,url}]}"""
    result = {"symbol": symbol, "name": name, "news": [], "new_count": 0}
    try:
        df = ak.stock_news_em(symbol=symbol)
        if df is None or df.empty:
            return result
        for _, row in df.iterrows():
            title = str(row.get("新闻标题", "")).strip()
            if not title:
                continue
            is_new, h = is_new_title(title, seen_titles)
            entry = {
                "title": title,
                "time": str(row.get("发布时间", "")),
                "source": str(row.get("文章来源", "")),
                "url": str(row.get("新闻链接", "")),
            }
            result["news"].append(entry)
            if is_new:
                result["new_count"] += 1
                seen_titles.add(h)
    except Exception as e:
        result["error"] = f"抓取失败: {type(e).__name__} {str(e)[:120]}"
    return result


def fetch_macro_news(seen_titles):
    """抓取宏观/行业政策快讯"""
    result = {"news": [], "new_count": 0}
    try:
        df = ak.stock_info_global_em()
        if df is None or df.empty:
            return result
        for _, row in df.iterrows():
            title = str(row.get("标题", "")).strip()
            if not title:
                continue
            is_new, h = is_new_title(title, seen_titles)
            entry = {
                "title": title,
                "summary": str(row.get("摘要", "")),
                "time": str(row.get("发布时间", "")),
                "url": str(row.get("链接", "")),
            }
            result["news"].append(entry)
            if is_new:
                result["new_count"] += 1
                seen_titles.add(h)
    except Exception as e:
        result["error"] = f"抓取失败: {type(e).__name__} {str(e)[:120]}"
    return result


def fetch_notices(seen_titles):
    """抓取全市场公告，过滤出关注股"""
    result = {"notices": [], "new_count": 0}
    try:
        today = datetime.now().strftime("%Y%m%d")
        df = ak.stock_notice_report(symbol="全部", date=today)
        if df is None or df.empty:
            return result
        # 关注股代码集合
        focus_codes = {s for s, _ in STOCKS}
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).strip()
            # 过滤出关注股（东财公告代码可能带前缀，取数字部分）
            digit_code = "".join(ch for ch in code if ch.isdigit())
            if digit_code not in focus_codes:
                continue
            title = str(row.get("公告标题", "")).strip()
            if not title:
                continue
            is_new, h = is_new_title(title, seen_titles)
            entry = {
                "symbol": digit_code,
                "title": title,
                "type": str(row.get("公告类型", "")),
                "date": str(row.get("公告日期", "")),
                "url": str(row.get("网址", "")),
            }
            result["notices"].append(entry)
            if is_new:
                result["new_count"] += 1
                seen_titles.add(h)
    except Exception as e:
        result["error"] = f"抓取失败: {type(e).__name__} {str(e)[:120]}"
    return result


def main():
    mode = "盘后"
    if len(sys.argv) > 1 and sys.argv[1] == "--mode":
        idx = sys.argv.index("--mode")
        if len(sys.argv) > idx + 1:
            mode = sys.argv[idx + 1]

    today_str = datetime.now().strftime("%Y-%m-%d")
    last_run = load_last_run()
    seen_titles = set(last_run.get("seen_titles", []))
    print(f"📡 新闻监控抓取开始 ({today_str} · {mode})")

    # ── 1. 宏观/行业政策 ──
    print("  [1/3] 抓取宏观/行业政策快讯...")
    macro = fetch_macro_news(seen_titles)

    # ── 2. 全市场公告 → 过滤关注股 ──
    print("  [2/3] 抓取全市场公告(过滤关注股)...")
    notices = fetch_notices(seen_titles)

    # ── 3. 29只个股新闻 ──
    print("  [3/3] 抓取29只关注股个股新闻...")
    stock_news = []
    for symbol, name in STOCKS:
        r = fetch_stock_news(symbol, name, seen_titles)
        stock_news.append(r)

    # ── 归档原始JSON ──
    raw_data = {
        "date": today_str,
        "mode": mode,
        "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "macro": macro,
        "notices": notices,
        "stock_news": stock_news,
    }
    raw_path = os.path.join(RAW_DIR, f"{today_str}_{mode}.json")
    with open(raw_path, "w") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)

    # ── 更新增量状态（保留最近2000个标题）──
    last_run["seen_titles"] = list(seen_titles)[-2000:]
    last_run["last_date"] = today_str
    save_last_run(last_run)

    # ── 汇总统计 ──
    new_macro = macro.get("new_count", 0)
    new_notices = notices.get("new_count", 0)
    new_stock = sum(r.get("new_count", 0) for r in stock_news)
    total_new = new_macro + new_notices + new_stock

    # ── 生成可读摘要 ──
    lines = []
    lines.append(f"📰 **新闻/舆情抓取简报** ({today_str} {mode})")
    lines.append(f"抓取到新资讯: {total_new} 条")
    lines.append(f"   • 宏观/政策: {new_macro} 条")
    lines.append(f"   • 关注股公告: {new_notices} 条")
    lines.append(f"   • 个股新闻: {new_stock} 条")
    lines.append("")

    # 公告优先（业绩预告/减持/立案这类最重要）
    if notices.get("notices"):
        lines.append("📢 **关注股公告**")
        for n in notices["notices"]:
            nm = next((nm for s, nm in STOCKS if s == n["symbol"]), n["symbol"])
            lines.append(f"  • **{nm}** {n['title']} [{n['type']}]")
        lines.append("")

    # 宏观政策（取新增的前10条）
    macro_news = [n for n in macro.get("news", [])]
    if macro_news:
        lines.append("🏛 **宏观/行业政策**")
        for n in macro_news[:10]:
            lines.append(f"  • {n['title']}")
        lines.append("")

    # 个股新闻（只列新增的，每只最多2条）
    lines.append("📌 **个股新闻速览**")
    shown = 0
    for r in stock_news:
        new_items = [n for n in r.get("news", [])]
        if new_items and r.get("new_count", 0) > 0:
            lines.append(f"  **{r['name']}({r['symbol']})**:")
            for n in new_items[:2]:
                lines.append(f"    - {n['title']}")
            shown += 1
            if shown >= 10:
                lines.append("  ... 等")
                break
    if shown == 0:
        lines.append("  今日无新增个股新闻")

    brief = "\n".join(lines)
    print("=====NEWS_MONITOR_RESULT=====")
    print(json.dumps({
        "date": today_str, "mode": mode,
        "total_new": total_new,
        "macro_new": new_macro, "notices_new": new_notices, "stock_new": new_stock,
        "raw_file": raw_path,
        "notices": notices.get("notices", []),
        "macro_top": macro_news[:10],
        "stock_news": [{"symbol": r["symbol"], "name": r["name"], "news": r.get("news", [])} for r in stock_news],
    }, ensure_ascii=False, indent=2, default=str))
    print("=====NEWS_MONITOR_END=====")
    print("\n" + brief)

    # 保存可读知识库片段
    kb_file = os.path.join(NEWS_DIR, "knowledge_base.md")
    with open(kb_file, "a") as f:
        f.write(f"\n## {today_str} {mode}\n\n")
        f.write(brief + "\n\n")
    print(f"\n📄 原始数据: {raw_path}")
    print(f"📄 知识库追加: {kb_file}")


if __name__ == "__main__":
    main()
