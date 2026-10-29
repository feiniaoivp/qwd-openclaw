#!/usr/bin/env python3
"""
财经新闻/舆情抓取监控 (News Monitor)
=========================================
三类数据源（东财接口，国内网络已验证可用）：
1. stock_news_em(symbol)     — 关注股个股新闻（fib_extension_scan.WATCHLIST 派生）
2. stock_info_global_em()    — 宏观/行业政策快讯（200条）
3. stock_notice_report()     — 全市场公告（业绩预告/减持/立案）

增量抓取 + 归档到 data/news/：
- raw/        原始JSON（每日一份；只归档发布时间 ≤72h 的新鲜条目，2026-10-20 起防旧文混入）
- last_run.json  增量状态（记录已抓取标题，避免重复）
- 输出 JSON 供 agent 解析，并生成可读摘要

运行: python3 analysis/news_monitor.py [--mode 盘前|盘后]

已统一使用 analysis.data_layer.router.DataRouter (Phase 1 完成)
"""
import os, sys, json, hashlib
from datetime import datetime

# 导入统一数据路由器
WORKSPACE = "/Users/duguke/.openclaw/workspace"
sys.path.insert(0, WORKSPACE)
from analysis.data_layer.router import get_router

NEWS_DIR = os.path.join(WORKSPACE, "data", "news")
RAW_DIR = os.path.join(NEWS_DIR, "raw")
LAST_RUN_FILE = os.path.join(NEWS_DIR, "last_run.json")
os.makedirs(RAW_DIR, exist_ok=True)

# ── 关注股清单：fib_extension_scan.WATCHLIST 单一事实来源派生（2026-10-20 对齐）──
# 旧版硬编码 27 只（2026-08-30 口径），落后于 WATCHLIST 36 只：
# 特高压出海 8 只 + 翰宇药业的新闻从未被抓取。派生后随关注池自动同步。
# 受 scripts/strategy_map_guard.py E 检查保护（禁止回退硬编码）。
from analysis.fib_extension_scan import WATCHLIST as _WL
STOCKS = sorted(_WL.items())   # [(code, name), ...] 与原格式兼容

# ── 发布时间窗过滤（2026-10-20 增，用户批准的深究落实）──
# 根因：东财"最近N条"搜索按相关度排序，同一快照实测混入 08-19/08-25/09-16 的
# 陈年旧文（与当日新闻并存）。在抓取源头统一拦截，全链路受益：
# 盘前/盘后快讯推送、晚间新闻解读推送、盘前报告要闻段、knowledge_base。
# 72h 窗：工作日覆盖前 3 天；周一盘前可覆盖整个周末（周六 08:30 起）。
FRESH_WINDOW_HOURS = 72

def _parse_pub_time(ts):
    """解析 'YYYY-MM-DD HH:MM:SS'/'YYYY-MM-DD HH:MM'/'YYYY-MM-DD'/'YYYYMMDD'；失败返回 None"""
    ts = str(ts or "").strip()
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(ts[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    return None

def _is_fresh(ts):
    """发布时间距 now ≤ FRESH_WINDOW_HOURS 且非未来时间；无/坏时间戳 → 拦截（宁缺勿旧）"""
    t = _parse_pub_time(ts)
    if t is None:
        return False
    age = (datetime.now() - t).total_seconds() / 3600.0
    return 0 <= age <= FRESH_WINDOW_HOURS


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
        router = get_router()
        df = router.get_stock_news(symbol)
        if df is None or df.empty:
            return result
        for _, row in df.iterrows():
            title = str(row.get("新闻标题", "")).strip()
            if not title:
                continue
            if not _is_fresh(row.get("发布时间", "")):
                result["stale_skipped"] = result.get("stale_skipped", 0) + 1
                # 留痕：被拦旧文记标题+时间（每路 10 条封顶）。2026-10-26 根因排查：
                # 仅计数无内容时无法区分「真无新鲜新闻」与「时间戳解析误拦」。
                if len(result.setdefault("stale_articles", [])) < 10:
                    result["stale_articles"].append({
                        "title": title,
                        "time": str(row.get("发布时间", "")),
                    })
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
        router = get_router()
        df = router.get_global_news()
        if df is None or df.empty:
            return result
        for _, row in df.iterrows():
            title = str(row.get("标题", "")).strip()
            if not title:
                continue
            if not _is_fresh(row.get("发布时间", "")):
                result["stale_skipped"] = result.get("stale_skipped", 0) + 1
                if len(result.setdefault("stale_articles", [])) < 10:
                    result["stale_articles"].append({
                        "title": title,
                        "time": str(row.get("发布时间", "")),
                    })
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
        router = get_router()
        today = datetime.now().strftime("%Y%m%d")
        df = router.get_notices(today)
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
            if not _is_fresh(row.get("公告日期", "")):
                result["stale_skipped"] = result.get("stale_skipped", 0) + 1
                if len(result.setdefault("stale_articles", [])) < 10:
                    result["stale_articles"].append({
                        "title": title,
                        "time": str(row.get("公告日期", "")),
                    })
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

    # ── 3. 关注股个股新闻（数量动态：36 只派生，勿硬编码 —— 2026-10-26 复验：旧文案「29只」
    # 曾被审计 agent 误读为代码事实，衍生假报告）──
    print(f"  [3/3] 抓取{len(STOCKS)}只关注股个股新闻...")
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

    # 保存可读知识库片段（2026-10-30 防重钩子：• 条目行若 KB 已存在同文本则跳过。
    # 根治两类粘性重复：a) 同日双窗口双收（手抓+19:06 cron，10-28 两盘后块 12 条重复）；
    # b) 挂架公告跨日重收（特变业绩说明会 ×3 等）。简报头统计行不参与防重。）
    kb_file = os.path.join(NEWS_DIR, "knowledge_base.md")
    brief_to_write = brief
    try:
        with open(kb_file, encoding="utf-8") as f:
            _kb_existing = f.read()
        _existing = {_ln.strip()[1:].strip() for _ln in _kb_existing.splitlines() if _ln.strip().startswith("•")}
        _kept, _skipped = [], 0
        for _ln in brief.splitlines():
            _s = _ln.strip()
            if _s.startswith("•") and not _s.startswith("• 宏观/政策:") \
                    and not _s.startswith("• 关注股公告:") and not _s.startswith("• 个股新闻:"):
                _t = _s[1:].strip()
                if _t in _existing:
                    _skipped += 1
                    continue
                _existing.add(_t)
            _kept.append(_ln)
        if _skipped:
            print(f"🧹 KB 防重: 跳过已存在条目 {_skipped} 行")
        brief_to_write = "\n".join(_kept)
    except Exception as e:
        print(f"⚠️ KB 防重检查失败({e})，按原文追加")
    with open(kb_file, "a") as f:
        f.write(f"\n## {today_str} {mode}\n\n")
        f.write(brief_to_write + "\n\n")
    print(f"\n📄 原始数据: {raw_path}")
    print(f"📄 知识库追加: {kb_file}")


if __name__ == "__main__":
    main()