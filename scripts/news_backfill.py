#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_backfill.py — E（新闻归档回填 09-09~09-29）正式回填

可行性结论（analysis/research/2026-10-19_news_backfill_feasibility.json）：
  - 公告: ✅ 东财公告历史接口按日期可查（09-15=2258 行 / 09-22=1717 行）→ 唯一可回填维
  - 个股新闻: ❌ search-api 已退化（600030 仅 10 条，最早 10-10），够不到缺口
  - 宏观快讯: ❌ 快讯流仅最新 ~3.5 小时（200 条），无历史窗口

设计（与生产 reader 语义严格对齐）：
  1. raw 粒度 = 交易日：非交易日（周末/中秋）公告折入下一交易日的 raw/{T}_回填.json，
     每条保留真实 公告日期 —— build_report 的 news_span 过滤天然放行（周一含周末）。
  2. 结构镜像生产快照: {"macro":{"news":[]}, "notices":{"notices":[...]}, "stock_news":[], "_backfill":{...}}
  3. 关注股过滤用 reader 的 CODE_TO_NAME（SSOT，与消费端完全一致）。
  4. 已有真实 raw（如 09-13_盘后）的公告并入对应交易日的 _回填 文件（url/title 去重，reader 端再兜底去重）。
  5. daily_*.md 仅重建「319B 空壳桩」的交易日档（size<400 且含 '命中 0 只'），
     尾部追加回填血统标注；周末/假期假桩（09-19/20/25/27）不动，留用户拍板。
  6. 幂等：raw 已存在 _backfill 标记则跳过；重跑安全。

用法: python3 scripts/news_backfill.py [--dry-run]
产出: analysis/research/<date>_news_backfill_result.json
"""
#!/usr/bin/env python3
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis"), os.path.join(WORKSPACE, "data", "news")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
import time
from datetime import date as ddate, timedelta
from typing import Any, Dict, List, Optional

from analysis.net_guard import network_timeout_guard
from analysis.service import is_trading_day
import daily_news_reader as rdr

RAW_DIR = rdr.RAW_DIR
WINDOW = ("2026-09-09", "2026-09-29")
OUT_DIR = os.path.join(WORKSPACE, "analysis", "research")
SLEEP = 0.6
PROVENANCE = ("\n> ⚠️ 回填重建（2026-10-19）：宏观快讯/个股新闻源无历史窗口不可恢复，"
              "本档仅含公告维（东财公告历史接口，关注股过滤口径与生产一致）；"
              "非交易日公告已按折叠语义并入最近交易日。详见 analysis/research/2026-10-19_news_backfill_feasibility.json\n")


def next_trading_day(d: ddate, limit: ddate) -> Optional[ddate]:
    cur = d
    for _ in range(12):
        if is_trading_day(cur.strftime("%Y-%m-%d")):
            return cur
        cur += timedelta(days=1)
        if cur > limit:
            break
    return None


def fetch_notices(day: str) -> List[Dict[str, Any]]:
    import akshare as ak
    with network_timeout_guard(40):
        df = ak.stock_notice_report(symbol="全部", date=day.replace("-", ""))
    out: List[Dict[str, Any]] = []
    if df is None or df.empty:
        return out
    focus = set(rdr.CODE_TO_NAME)
    for _, row in df.iterrows():
        digit = "".join(ch for ch in str(row.get("代码", "")) if ch.isdigit())
        if digit not in focus:
            continue
        out.append({
            "symbol": digit,
            "title": str(row.get("公告标题", "")).strip(),
            "type": str(row.get("公告类型", "")),
            "date": str(row.get("公告日期", ""))[:10],
            "url": str(row.get("网址", "")),
        })
    return out


def existing_raw_notices(span: set, self_date: str) -> List[Dict[str, Any]]:
    """并入窗口内已有真实 raw 快照（盘前/盘后）中的关注股公告（如 09-13_盘后）。"""
    import glob
    merged: List[Dict[str, Any]] = []
    focus = set(rdr.CODE_TO_NAME)
    for s in sorted(span):
        if s == self_date:
            continue  # 当日真实 raw 若存在，reader 读取时天然合并，无需重复
        for f in glob.glob(os.path.join(RAW_DIR, f"{s}_*.json")):
            if f.endswith("_回填.json"):
                continue
            try:
                with open(f, encoding="utf-8") as fh:
                    d = json.load(fh)
            except Exception as e:  # noqa: BLE001 —— 血统合并失败必须可见
                print(f"⚠️ 读取既有 raw 失败 {f}: {e}")
                continue
            for n in (d.get("notices", {}) or {}).get("notices", []) or []:
                if str(n.get("symbol", "")) in focus:
                    merged.append(n)
    return merged


def main() -> int:
    dry = "--dry-run" in sys.argv
    t0 = time.time()
    start, end = ddate.fromisoformat(WINDOW[0]), ddate.fromisoformat(WINDOW[1])
    hard_limit = end + timedelta(days=4)

    days: List[ddate] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)

    # 1) 逐日抓取（按公历日，含非交易日）
    fetched: Dict[str, List[Dict[str, Any]]] = {}
    fetch_errors: Dict[str, str] = {}
    for d in days:
        s = d.strftime("%Y-%m-%d")
        try:
            items = fetch_notices(s)
            fetched[s] = items
        except Exception as e:  # noqa: BLE001 —— 单日失败不阻塞整窗，必须留痕
            fetched[s] = []
            fetch_errors[s] = f"{type(e).__name__}: {str(e)[:120]}"
        print(f"  fetch {s}: {len(fetched[s])} 条关注股公告" + (f" ⚠️ {fetch_errors[s]}" if s in fetch_errors else ""))
        time.sleep(SLEEP)

    # 2) 分桶到下一交易日
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    dropped: List[Dict[str, str]] = []
    for s, items in fetched.items():
        for n in items:
            nd = ddate.fromisoformat(n["date"])
            td = next_trading_day(nd, hard_limit)
            if td is None:
                dropped.append({"date": n["date"], "title": n["title"][:40]})
                continue
            buckets.setdefault(td.strftime("%Y-%m-%d"), []).append(n)

    # 3) 并入既有真实 raw + 写 raw/{T}_回填.json
    raw_written: Dict[str, int] = {}
    raw_skipped: List[str] = []
    for td_str, items in sorted(buckets.items()):
        span = rdr._news_span(td_str)
        extra = existing_raw_notices(span, td_str)
        seen = {(n.get("url") or n.get("title")) for n in items}
        for n in extra:
            k = n.get("url") or n.get("title")
            if k not in seen:
                items.append(n)
                seen.add(k)
        if dry:
            raw_written[td_str] = len(items)
            continue
        path = os.path.join(RAW_DIR, f"{td_str}_回填.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                if "_backfill" in json.load(fh):
                    raw_skipped.append(td_str)
                    continue
        payload = {
            "macro": {"news": []},
            "notices": {"notices": items},
            "stock_news": [],
            "_backfill": {
                "done_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "source": "ak.stock_notice_report 历史日期 + 既有真实 raw 合并",
                "recoverable_dims": ["notices"],
                "unrecoverable_dims": ["macro", "stock_news"],
                "dates_covered": sorted(span),
            },
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        raw_written[td_str] = len(items)

    # 4) 重建空壳桩 daily（仅交易日）
    daily_rebuilt: Dict[str, int] = {}
    daily_untouched: List[str] = []
    for d in days:
        s = d.strftime("%Y-%m-%d")
        if not is_trading_day(s):
            continue
        daily_path = os.path.join(rdr.BASE, f"daily_{s}.md")
        if os.path.exists(daily_path):
            with open(daily_path, encoding="utf-8") as fh:
                content = fh.read()
            if not (len(content) < 400 and "命中 0 只" in content):
                daily_untouched.append(s)  # 已有真实内容，不覆盖
                continue
        if s not in buckets and s not in raw_written and not any(
                k.startswith(s) for k in buckets):
            daily_untouched.append(s)
            continue
        if dry:
            daily_rebuilt[s] = -1
            continue
        report = rdr.build_report(s)
        text = rdr.render(report)
        with open(daily_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n" + PROVENANCE)
        n_notices = len(report.get("notices", []))
        daily_rebuilt[s] = n_notices

    result: Dict[str, Any] = {
        "date": time.strftime("%Y-%m-%d"),
        "window": WINDOW,
        "trading_days": sum(1 for d in days if is_trading_day(d.strftime("%Y-%m-%d"))),
        "fetch_per_day": {s: len(v) for s, v in fetched.items()},
        "fetch_errors": fetch_errors,
        "raw_written": raw_written,
        "raw_skipped_existing": raw_skipped,
        "dropped_no_trading_day": dropped,
        "daily_rebuilt": daily_rebuilt,
        "daily_untouched": daily_untouched,
        "pending_user_decision": "非交易日假桩 daily_2026-09-{19,20,25,27}.md（319B 空壳，语义上周末档已废弃）是否 trash，待拍板",
        "dry_run": dry,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    suffix = "_dryrun" if dry else ""
    out_json = os.path.join(OUT_DIR, f"{result['date']}_news_backfill_result{suffix}.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(f"\n[saved] {out_json}")
    print(f"raw 回填 {len(raw_written)} 个交易日 / 重建 daily {len(daily_rebuilt)} 个 / 跳过 {len(raw_skipped)}")
    print(f"drop(找不到承接交易日): {len(dropped)}；elapsed={result['elapsed_sec']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
