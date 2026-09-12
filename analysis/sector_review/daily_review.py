#!/usr/bin/env python3
"""
特高压/电网出海板块 每日收盘复盘生成器
运行方式: python analysis/sector_review/daily_review.py [--date YYYY-MM-DD]
输出: analysis/sector_review/daily/YYYY-MM-DD_evening_review.md

数据源: 新浪实时行情 hq.sinajs.cn (需 Referer: https://finance.sina.com.cn)
说明: 这是周报链路的数据源头。缺失此步会导致 weekly_review.py 只有空模板。
字段陷阱: 新浪 A股行情 hq_str_sh600000 中 [3]=最新价(不是[1]，[1]是今开)。
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

# 核心标的 - 从权威配置读取，避免与 power_overseas_config.json 漂移
CONFIG_FILE = Path(__file__).resolve().parents[2] / "data" / "power_overseas_config.json"

def _load_core_stocks() -> list[dict]:
    """从 data/power_overseas_config.json 读取权威标的列表。
    配置不可用时回退到内置常量(已与配置对齐)。
    """
    fallback = [
        {"code": "002130", "name": "沃尔核材", "mkt": "sz"},
        {"code": "600312", "name": "平高电气", "mkt": "sh"},
        {"code": "002028", "name": "思源电气", "mkt": "sz"},
        {"code": "600089", "name": "特变电工", "mkt": "sh"},
        {"code": "601179", "name": "中国西电", "mkt": "sh"},
        {"code": "600406", "name": "国电南瑞", "mkt": "sh"},
        {"code": "000400", "name": "许继电气", "mkt": "sz"},
        {"code": "002270", "name": "华明装备", "mkt": "sz"},
    ]
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        out = []
        for code, meta in cfg.get("stocks", {}).items():
            pct = meta.get("revenue_overseas_pct")
            out.append({
                "code": code,
                "name": meta.get("name", code),
                "oversea_pct": round(pct * 100) if isinstance(pct, (int, float)) else None,
                "mkt": "sh" if code.startswith("6") else "sz",
            })
        return out or fallback
    except Exception as e:
        print(f"[WARN] 读取权威配置失败({e})，使用内置回退列表")
        return fallback

CORE_STOCKS = _load_core_stocks()

REVIEW_DIR = Path("analysis/sector_review/daily")
SINA_URL = "https://hq.sinajs.cn/list="
HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


def fetch_quotes(stocks: list[dict]) -> dict:
    """批量抓取新浪实时行情。返回 {code: {price, open, prev_close, high, low, vol, amount, date}}"""
    symbols = [f"{s['mkt']}{s['code']}" for s in stocks]
    url = SINA_URL + ",".join(symbols)
    req = Request(url, headers=HEADERS)
    try:
        with urlopen(req, timeout=15) as resp:
            text = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        print(f"[ERROR] 新浪行情抓取失败: {e}")
        return {}

    out = {}
    for line in text.strip().split(";"):
        if "=" not in line:
            continue
        var_part, data_part = line.split("=", 1)
        m = re.search(r"hq_str_([a-z]{2})(\d{6})", var_part)
        if not m:
            continue
        code = m.group(2)
        f = data_part.strip().strip('"').split(",")
        if len(f) < 32 or not f[3]:
            continue
        try:
            price = float(f[3])          # [3] = 最新价
            prev_close = float(f[2])     # [2] = 昨收
            open_price = float(f[1])     # [1] = 今开
            high = float(f[4])
            low = float(f[5])
            vol = float(f[8]) if f[8] else 0      # 成交量(股)
            amount = float(f[9]) if f[9] else 0   # 成交额(元)
            date = f[30] if len(f) > 30 else ""
            time_ = f[31] if len(f) > 31 else ""
            chg_pct = (price - prev_close) / prev_close * 100 if prev_close else 0.0
            turnover = amount / 1e8  # 亿元
            out[code] = {
                "price": price, "open": open_price, "prev_close": prev_close,
                "high": high, "low": low, "volume": vol, "amount": amount,
                "chg_pct": chg_pct, "turnover_yi": turnover, "date": date, "time": time_,
            }
        except (ValueError, IndexError) as e:
            print(f"[WARN] {code} 解析失败: {e}")
    return out


def build_review(date_str: str, quotes: dict) -> str:
    up = down = flat = 0
    rows = []
    for s in CORE_STOCKS:
        q = quotes.get(s["code"])
        if not q:
            rows.append(f"| {s['code']} | {s['name']} | 数据缺失 | - | - | - | - |")
            continue
        c = q["chg_pct"]
        if c > 0.05:
            up += 1
        elif c < -0.05:
            down += 1
        else:
            flat += 1
        rows.append(
            f"| {s['code']} | {s['name']} | {q['price']:.2f} | {c:+.2f}% | "
            f"{q['turnover_yi']:.2f} | {q['high']:.2f} | {q['low']:.2f} |"
        )

    lines = [
        f"# 特高压/电网出海板块 收盘复盘 ({date_str})",
        "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 数据源: 新浪实时行情",
        "",
        "## 1. 核心标的涨跌幅排名",
        "",
        "| 代码 | 名称 | 收盘价 | 涨跌幅 | 成交额(亿) | 最高 | 最低 |",
        "|------|------|--------|--------|------------|------|------|",
    ]
    lines.extend(rows)
    lines += [
        "",
        "## 2. 板块广度",
        f"- 上涨: {up} 只",
        f"- 下跌: {down} 只",
        f"- 平盘: {flat} 只",
        "",
        "## 3. 观察",
        f"- 覆盖标的: {sum(1 for s in CORE_STOCKS if s['code'] in quotes)}/{len(CORE_STOCKS)} 只有效",
        "",
        "## 4. 事件驱动",
        "- 见 news_monitor 归档 / 公告抓取",
        "",
        "---",
        "*由 `analysis/sector_review/daily_review.py` 自动生成*",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="复盘日期 YYYY-MM-DD (默认今天)")
    args = ap.parse_args()
    date_str = args.date or datetime.now().strftime("%Y-%m-%d")

    quotes = fetch_quotes(CORE_STOCKS)
    if not quotes:
        print("[FATAL] 未获取到任何行情，拒绝生成空模板。")
        return 1

    data_date = next((q.get("date") for q in quotes.values() if q.get("date")), "")
    if data_date and data_date != date_str:
        print(f"[WARN] 行情日期 {data_date} != 目标日期 {date_str} (非交易日或数据延迟)")

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    content = build_review(date_str, quotes)
    out = REVIEW_DIR / f"{date_str}_evening_review.md"
    out.write_text(content, encoding="utf-8")
    print(f"[SAVE] -> {out} ({len(quotes)}/{len(CORE_STOCKS)} 只有效)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
