#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_news_reader.py
====================
针对 30 只关注股，自动阅读「当天」相关新闻，生成解读简报。

数据源：data/news/raw/YYYY-MM-DD_{盘前,盘后}.json  (news_monitor.py 抓取，权威逐次快照)
「当天」= 目标日期（默认用最新一次抓取的日期）。同一日期的 盘前+盘后 两个快照会合并。

按真实时间戳过滤（stock_news.time / notices.date / macro.time），
避免 knowledge_base.md 里的「粘性重复项」污染当天解读。

用法：
  .venv-ml/bin/python data/news/daily_news_reader.py              # 最新一期
  .venv-ml/bin/python data/news/daily_news_reader.py --date 2026-08-06
  .venv-ml/bin/python data/news/daily_news_reader.py --save       # 写盘 daily_日期.md
  .venv-ml/bin/python data/news/daily_news_reader.py --topn-macro 5   # 宏观要点条数
  .venv-ml/bin/python data/news/daily_news_reader.py --all-stock-newest  # 无当天新闻时回退到该股最新一条
"""

import argparse
import glob
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE, "raw")

# 权威 30 只关注股清单（来源 memory/watchlist.md，绝不凭记忆改动）
WATCHLIST = [
    ("600030", "中信证券"), ("601066", "中信建投"), ("600036", "招商银行"),
    ("601995", "中金公司"), ("000987", "越秀资本"),
    ("600584", "长电科技"), ("688981", "中芯国际"), ("002156", "通富微电"),
    ("002413", "雷科防务"),
    ("300014", "亿纬锂能"), ("002466", "天齐锂业"), ("601865", "福莱特"),
    ("300285", "国瓷材料"), ("603308", "应流股份"), ("300124", "汇川技术"),
    ("601100", "恒立液压"), ("002318", "久立特材"), ("300719", "安达维尔"),
    ("002335", "科华数据"), ("300748", "金力永磁"),
    ("002180", "奔图科技"), ("300847", "中船汉光"),
    ("600160", "巨化股份"), ("600346", "恒力石化"),
    ("000708", "中信特钢"),
    ("600660", "福耀玻璃"), ("600570", "恒生电子"), ("605566", "福莱蒽特"),
    ("000157", "中联重科"), ("601061", "中信金属"),
]
NAME_TO_CODE = {n: c for c, n in WATCHLIST}
CODE_TO_NAME = {c: n for c, n in WATCHLIST}


def list_snapshots():
    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.json")))
    dates = {}
    for f in files:
        base = os.path.basename(f).replace(".json", "")  # e.g. 2026-08-06_盘后
        if "_" not in base:
            continue
        date, mode = base.rsplit("_", 1)
        dates.setdefault(date, []).append(mode)
    return dates


def latest_date():
    dates = list_snapshots()
    return max(dates)


def load_snapshots(date):
    """加载指定日期的所有快照（盘前/盘后），合并"""
    data = {"macro": [], "notices": [], "stock_news": {}}
    for f in sorted(glob.glob(os.path.join(RAW_DIR, f"{date}_*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            print(f"⚠️ 读取 {f} 失败: {e}")
            continue
        # macro
        for m in d.get("macro", {}).get("news", []) or []:
            data["macro"].append(m)
        # notices
        for n in d.get("notices", {}).get("notices", []) or []:
            data["notices"].append(n)
        # stock_news: symbol -> list of {title,time,source,url}
        for sn in d.get("stock_news", []) or []:
            sym = sn.get("symbol")
            if sym is None:
                continue
            news_list = data["stock_news"].setdefault(sym, [])
            for n in sn.get("news", []) or []:
                if isinstance(n, dict):
                    news_list.append(n)
    return data


def fetch_time_of(item):
    return item.get("time", "")[:10]  # 2026-08-06


def build_report(date, topn_macro=6, all_stock_newest=False):
    data = load_snapshots(date)
    macro = data["macro"]
    notices = data["notices"]
    stock_news = data["stock_news"]  # symbol -> list

    # ---- 宏观：取 target 日期的，优先行业相关，按时间倒序取前 topn_macro ----
    macro_today = [m for m in macro if fetch_time_of(m) == date]
    macro_today.sort(key=lambda m: m.get("time", ""), reverse=True)
    rel = [m for m in macro_today if macro_relevant(m)]
    macro_pick = (rel + macro_today)[:topn_macro]  # 相关优先，不足用一般宏观补

    # ---- 公告：匹配日期，并按 url/title 去重（盘前盘后会重复） ----
    seen_n = set()
    notices_today = []
    for n in notices:
        if n.get("date", "") != date:
            continue
        key = n.get("url") or n.get("title", "")
        if key in seen_n:
            continue
        seen_n.add(key)
        notices_today.append(n)

    # ---- 个股新闻：按股票聚合，过滤目标日期 ----
    per_stock = {}
    for sym in CODE_TO_NAME:
        per_stock[sym] = []
    for sym, news_list in stock_news.items():
        seen = set()
        for n in news_list:
            t = n.get("title", "")
            if t in seen:
                continue
            seen.add(t)
            if fetch_time_of(n) == date:
                per_stock.setdefault(sym, []).append((fetch_time_of(n), "新闻", n))
            elif all_stock_newest and not per_stock.get(sym):
                per_stock.setdefault(sym, []).append((fetch_time_of(n), "新闻", n))
        per_stock.setdefault(sym, []).sort(key=lambda x: x[0], reverse=True)

    # 把当日公告并入对应股票（公告标记 "公告" 类），并按标题去重
    for n in notices_today:
        sym = n.get("symbol", "")
        if sym not in per_stock:
            continue
        title = n.get("title", "")
        exists = any(it[2].get("title", "") == title for it in per_stock[sym])
        if exists:
            continue
        per_stock[sym].append((n.get("date", ""), "公告", n))
        per_stock[sym].sort(key=lambda x: x[0], reverse=True)

    return {
        "date": date,
        "macro": macro_pick[:topn_macro],
        "notices": notices_today,
        "stocks": {sym: items for sym, items in per_stock.items() if items},
    }


def significance_level(title):
    """给新闻/公告打重要度标签。返回 (level, tag)，level 高=越重要。"""
    rule = [
        (4, ["回购", "增持", "减持", "举牌", "业绩预增", "业绩大增", "扭亏"]),
        (4, ["涨停", "跌停", "立案", "处罚", "诉讼", "337调查", "债务", "违约"]),
        (4, ["重大合同", "中标", "收购", "重组", "定增"]),
        (4, ["董事长", "CEO", "高管变动", "辞职", "辞任"]),
        (3, ["净利润", "中报", "年报", "半年报", "一季报", "营收"]),
        (3, ["分红", "现金分红", "转增"]),
        (2, ["龙虎榜", "融资客", "主力资金", "大宗交易", "筹码"]),
        (2, ["涨价", "调价", "提价"]),
        (1, ["月报表", "证券变动", "进度"]),
    ]
    best = 0
    tag = ""
    for lv, kws in rule:
        for kw in kws:
            if kw in title and lv > best:
                best = lv
                tag = kw
    if best == 0:
        tag = "资讯"
    return best, tag


def interpret_stock(sym, items):
    """给单只股票当天的个股新闻+公告生成解读要点。"""
    pts = []
    for _dstr, _kind, n in items:
        title = n.get("title", "")
        lv, tag = significance_level(title)
        if lv >= 3:  # 只解读重要项
            pts.append((lv, tag, title))
    pts.sort(key=lambda x: -x[0])
    return pts


# 宏观候选里与关注股行业相关的关键词（用于挑选“值得看的宏观/行业”要点）
SECTOR_KW = [
    "半导体", "芯片", "晶圆", "封测", "存储", "MLCC", "PCB", "消费电子",
    "新能源", "锂电", "电池", "锂盐", "锂矿", "光伏", "储能", "固态电池",
    "券商", "证券", "投行", "IPO", "股市", "A股", "沪指", "创业板", "北向",
    "化工", "制冷剂", "氟化工", "炼化", "钢铁", "特钢", "机械", "机器人",
    "军工", "航空发动机", "核电", "可控核聚变", "高铁", "汽车", "玻璃",
    "AI", "人工智能", "算力", "服务器", "光模块", "大模型", "信创", "打印机",
    "稀土", "永磁", "银行", "大基金", "国产替代", "反制",
]


def macro_relevant(m):
    t = m.get("title", "") + m.get("summary", "")
    return any(k in t for k in SECTOR_KW)


def render(report, show_notice=True):
    L = []
    L.append("=" * 62)
    L.append(f"📰 关注股当日新闻解读 · {report['date']}")
    L.append("=" * 62)

    # 宏观要点
    macro = report["macro"]
    L.append(f"\n🏛 宏观/行业要点（{len(macro)}条）")
    if macro:
        for m in macro:
            t = m.get("title", "")
            L.append(f"  • {t}")
    else:
        L.append("  (目标日期无宏观快照)")

    # 公告
    notices = report["notices"]
    if show_notice and notices:
        L.append(f"\n📢 关注股公告（当日 {len(notices)} 条）")
        for n in notices:
            nm = CODE_TO_NAME.get(n.get("symbol", ""), n.get("symbol", ""))
            title = n.get("title", "")
            # 标题常以 "股票名:" 开头（如 "国瓷材料:2026年半年度报告"），去掉避免重复
            if nm and title.startswith(nm):
                title = title[len(nm):].lstrip(":： ")
            L.append(f"  • [{n.get('type','')}] {nm}: {title}")

    # 个股新闻 + 解读
    stocks = report["stocks"]
    L.append(f"\n📌 个股新闻与解读（命中 {len(stocks)} 只）")
    if stocks:
        for sym, items in stocks.items():
            if sym not in CODE_TO_NAME:
                continue  # 已撤出的股票（如上能电气300827）不进当天阅读
            nm = CODE_TO_NAME.get(sym, sym)
            L.append(f"\n  ◆ {nm}({sym})")
            for dstr, kind, n in items:
                tag = "公告" if kind == "公告" else "新闻"
                title = n.get("title", "")
                # 公告标题常带 "股票名:" 前缀，去掉避免重复
                if kind == "公告" and title.startswith(nm):
                    title = title[len(nm):].lstrip(":： ")
                src = n.get("source", "") if kind == "新闻" else n.get("type", "")
                suffix = f"  ({src})" if src else ""
                L.append(f"     [{dstr}] [{tag}] {title}{suffix}")
            # 重要度解读
            sig = interpret_stock(sym, items)
            if sig:
                tags = []
                for lv, kw, _ in sig:
                    if kw not in tags:
                        tags.append(kw)
                L.append(f"     ⚡ 要点: " + "、".join(tags[:4]))
    else:
        L.append("  (目标日期无个股新闻)")

    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="关注股当天新闻阅读")
    ap.add_argument("--date", default=None, help="目标日期 YYYY-MM-DD（默认最新一期）")
    ap.add_argument("--save", action="store_true", help="写盘 data/news/daily_日期.md")
    ap.add_argument("--topn-macro", type=int, default=6, help="宏观要点展示条数")
    ap.add_argument("--all-stock-newest", action="store_true",
                    help="无当天新闻的股票回退显示其最新一条（标注旧日期）")
    ap.add_argument("--no-notice", action="store_true", help="不显示公告")
    args = ap.parse_args()

    dates = list_snapshots()
    if not dates:
        print("❌ data/news/raw/ 下没有快照文件")
        return
    date = args.date or latest_date()
    print(f"已抓取日期: {sorted(dates)[-3:]} ... 共 {len(dates)} 期 | 读取: {date}")

    report = build_report(date, args.topn_macro, args.all_stock_newest)
    text = render(report, show_notice=not args.no_notice)
    print(text)

    if args.save:
        out = os.path.join(BASE, f"daily_{date}.md")
        with open(out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n✅ 已保存 -> {out}")


if __name__ == "__main__":
    main()
