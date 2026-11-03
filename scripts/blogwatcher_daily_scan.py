#!/usr/bin/env python3
"""blogwatcher 每日扫描推送：scan → 未读过滤 → 摘要 → Telegram 推送 → 噪声清理。

设计要点（2026-10-27 与用户拍板口径一致，2026-11-02 更新）：
- 当前 13 源：A股动态/FT中文网/中财证券 (Google News) + 博客园/中新网要闻/财经 + 财联社/财新/金十/东财4研报 (RSSHub)
- 噪声清理只动「明显非文章」条目（空标题/导航页/栏目页），宁漏勿错；
- 重复推文只影响展示去重，不做自动已读；
- --dry-run 供验证：不推送、不标已读。
"""
import argparse
import datetime
import os
import re
import subprocess
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.fib_extension_scan import WATCHLIST  # 关注池 SSOT
from send_telegram import send_with_retry  # workspace 根，含代理兜底

# 保守噪声判据：仅匹配「明显不是文章」的形态（10-27 人工清理实录 + 11-02 新增）
JUNK_EXACT = {"-", "E", "R", ""}
JUNK_CONTAINS = (
    "每日一詞", "每日一词", "一周新闻小测", "双语电台", "偏好设置",
    "高端订阅", "标准订阅", "文章档案", "双语阅读", "一周热门文章",
    "精选财经资讯", "新闻联播", "早报", "晚报", "快讯：", "直播：",
)
NAV_TITLE_RE = re.compile(r"^(FT ?中文网|FT 金融时报)\s*-\s*(FT Chinese|Financial Times Chinese)$")

# 宏观关键词（11-02 扩充：央行换锚/回购贷款/证监会立案等）
MACRO_KWS = ("央行", "证监会", "降准", "降息", "IPO", "回购", "吸并", "换股",
             "减持", "万亿", "北向", "油价", "美联储", "关税", "稀土",
             "换锚", "逆回购", "MLF", "LPR", "存款准备金", "回购贷款",
             "立案", "财务造假", "三季报", "分红", "增持", "注销式回购")


def run_cmd(args, stdin_text=None):
    r = subprocess.run(args, input=stdin_text, text=True,
                       capture_output=True, timeout=120)
    if r.returncode != 0:
        print(f"[blogwatcher-daily] 命令失败 {args}: rc={r.returncode} {r.stderr[:300]}")
        sys.exit(1)
    return r.stdout


def parse_articles(raw):
    """解析 blogwatcher articles 输出 → [(id, title, blog, published)]"""
    out = []
    for block in re.split(r"\n(?=\s*\[\d+\])", raw):
        m = re.search(r"\[(\d+)\]\s*\[new\]\s*(.*)", block)
        if not m:
            continue
        aid = int(m.group(1))
        title = m.group(2).strip()
        blog = (re.search(r"Blog:\s*(.+)", block) or [None, "?"])[1].strip() if re.search(r"Blog:\s*(.+)", block) else "?"
        pub = re.search(r"Published:\s*(.+)", block)
        out.append((aid, title, blog, pub.group(1).strip()[:10] if pub else "?"))
    return out


def is_junk(title):
    t = title.strip()
    if t in JUNK_EXACT or len(t) <= 1:
        return True
    if NAV_TITLE_RE.match(t):
        return True
    return any(k in t for k in JUNK_CONTAINS)


def norm_title(title):
    base = re.split(r"\s+-\s+", title)[0]
    return re.sub(r"[\s，。：:、,]", "", base)[:20]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只产摘要，不推送不标读")
    args = ap.parse_args()

    os.makedirs(os.path.join(WORKSPACE, "logs"), exist_ok=True)
    today = datetime.date.today().isoformat()

    run_cmd(["blogwatcher", "scan"])
    raw = run_cmd(["blogwatcher", "articles"])
    articles = parse_articles(raw)
    if not articles:
        msg = f"📰 blogwatcher {today}：无新增未读，一切干净。"
        print(msg)
        if not args.dry_run:
            r = send_with_retry(msg)
            if not r.get("ok"):
                print(f"[blogwatcher-daily] 推送失败: {r.get('error')}")
                sys.exit(1)
        return

    junk = [(aid, t) for aid, t, _, _ in articles if is_junk(t)]
    if junk and not args.dry_run:
        for aid, _t in junk:
            run_cmd(["blogwatcher", "read", str(aid)], stdin_text="y\n")
    keep = [(aid, t, b, p) for aid, t, b, p in articles if (aid, t) not in set(junk)]

    names = list(WATCHLIST.values())
    seen_titles = set()
    pool_hits, macro_hits, others = [], [], 0
    for aid, t, b, p in keep:
        key = norm_title(t)
        if key in seen_titles:
            continue
        seen_titles.add(key)
        hit = next((n for n in names if n and n in t), None)
        if hit:
            pool_hits.append(f"• {hit} | {p} | {t[:60]}")
        elif any(k in t for k in MACRO_KWS):
            macro_hits.append(f"• {p} | {t[:60]}")
        else:
            others += 1

    lines = [f"📰 blogwatcher 每日扫描 {today}",
             f"未读 {len(articles)} 条 | 清噪声 {len(junk)} | 去重后 {len(keep)} 条"]
    if pool_hits:
        lines.append(f"\n【关注池命中 {len(pool_hits)}】")
        lines += pool_hits[:10]
    if macro_hits:
        lines.append(f"\n【宏观要点 {len(macro_hits)}】")
        lines += macro_hits[:8]
    lines.append(f"\n其余 {others} 条见 blogwatcher articles")
    msg = "\n".join(lines)
    if len(msg) > 3900:
        msg = msg[:3900] + "\n…(截断)"
    print(msg)

    if args.dry_run:
        print("[dry-run] 未推送、未标读")
        return
    r = send_with_retry(msg)
    if not r.get("ok"):
        print(f"[blogwatcher-daily] 推送失败: {r.get('error')}")
        sys.exit(1)
    print(f"[blogwatcher-daily] 推送成功 msg_id={r.get('result', {}).get('message_id', '?')}")


if __name__ == "__main__":
    main()
