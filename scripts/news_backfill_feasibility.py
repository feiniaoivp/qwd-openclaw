#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_backfill_feasibility.py — E（新闻归档回填 09-09~09-29）可行性探针

背景：data/news/daily_2026-09-09..29 共 16 个工作日文件全是 319B 空壳（「0条」假覆盖），
raw 快照缺失（新闻 job 09-09 起坏、09-29/30 修复）。生产三源：
  1) 个股新闻 ak.stock_news_em(search-api)   — 只返回最近 N 条，探测历史覆盖深度
  2) 宏观快讯 ak.stock_info_global_em(快讯流) — 只给最新流，探测最早时间戳
  3) 公告     ak.stock_notice_report(date=)   — 带日期参数，探测历史日期是否可查

产出: analysis/research/<date>_news_backfill_feasibility.json
退出码: 0 = 探测完成
"""
#!/usr/bin/env python3
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
import time
from datetime import date
from typing import Any, Dict, List

from analysis.net_guard import network_timeout_guard

OUT_DIR = os.path.join(WORKSPACE, "analysis", "research")
GAP = ["2026-09-09", "2026-09-29"]  # 缺口窗口（含端点）


def _span(times: List[str]) -> Dict[str, Any]:
    ts = sorted(t for t in times if t and t[:4] == "20")
    return {"n": len(ts), "earliest": ts[0] if ts else None, "latest": ts[-1] if ts else None}


def probe_notices() -> Dict[str, Any]:
    import akshare as ak
    out: Dict[str, Any] = {"probe": "ak.stock_notice_report(symbol='全部', date=历史日)"}
    for d in ("20260915", "20260922"):
        rec: Dict[str, Any] = {"date": d}
        t0 = time.time()
        try:
            with network_timeout_guard(30):
                df = ak.stock_notice_report(symbol="全部", date=d)
            rec["rows"] = 0 if df is None else int(len(df))
            if rec["rows"]:
                cols = list(df.columns)
                dcol = next((c for c in cols if "日期" in str(c)), cols[0])
                rec["date_span"] = _span([str(x) for x in df[dcol]])
            rec["ok"] = bool(rec["rows"])
        except Exception as e:  # noqa: BLE001 —— 失败模式即结论
            rec["ok"] = False
            rec["error"] = f"{type(e).__name__}: {str(e)[:150]}"
        rec["elapsed"] = round(time.time() - t0, 2)
        out[d] = rec
    return out


def probe_stock_news(symbol: str = "600030") -> Dict[str, Any]:
    import akshare as ak
    out: Dict[str, Any] = {"probe": f"ak.stock_news_em('{symbol}')", "note": "返回最近~100条，看最早时间戳能否够到缺口"}
    t0 = time.time()
    try:
        with network_timeout_guard(30):
            df = ak.stock_news_em(symbol=symbol)
        out["rows"] = 0 if df is None else int(len(df))
        if out["rows"]:
            tcol = next((c for c in df.columns if "时间" in str(c)), df.columns[0])
            out["time_span"] = _span([str(x) for x in df[tcol]])
            # 缺口窗口内的命中数
            gap_rows = df[df[tcol].astype(str).str[:10].between(*GAP)]
            out["rows_in_gap"] = int(len(gap_rows))
        out["ok"] = bool(out["rows"])
    except Exception as e:  # noqa: BLE001
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {str(e)[:150]}"
    out["elapsed"] = round(time.time() - t0, 2)
    return out


def probe_global_news() -> Dict[str, Any]:
    import akshare as ak
    out: Dict[str, Any] = {"probe": "ak.stock_info_global_em()", "note": "快讯流，看最早时间戳"}
    t0 = time.time()
    try:
        with network_timeout_guard(30):
            df = ak.stock_info_global_em()
        out["rows"] = 0 if df is None else int(len(df))
        if out["rows"]:
            tcol = next((c for c in df.columns if "时间" in str(c)), df.columns[0])
            out["time_span"] = _span([str(x) for x in df[tcol]])
        out["ok"] = bool(out["rows"])
    except Exception as e:  # noqa: BLE001
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {str(e)[:150]}"
    out["elapsed"] = round(time.time() - t0, 2)
    return out


def main() -> int:
    t0 = time.time()
    result: Dict[str, Any] = {
        "date": str(date.today()),
        "gap_window": GAP,
        "notices": probe_notices(),
        "stock_news": probe_stock_news(),
        "global_news": probe_global_news(),
    }
    # 结论草稿
    verdict = []
    if result["notices"].get("20260915", {}).get("ok"):
        verdict.append("公告: 历史日期可查 → 可回填")
    else:
        verdict.append("公告: 历史日期不可查")
    sn = result["stock_news"]
    if sn.get("rows_in_gap"):
        verdict.append(f"个股新闻: 缺口窗口内命中 {sn['rows_in_gap']} 条 → 部分可回填（快照口径=今日可见存量，非当日实时快照）")
    elif sn.get("ok"):
        verdict.append(f"个股新闻: 最早仅到 {sn.get('time_span', {}).get('earliest')}，够不到缺口 → 不可回填")
    else:
        verdict.append("个股新闻: 接口失败")
    gn = result["global_news"]
    gn_earliest = (gn.get("time_span") or {}).get("earliest") or "9999"
    if gn.get("ok") and gn_earliest > "2026-10-01":
        verdict.append("宏观快讯: 只有最新流 → 不可回填")
    result["verdict_draft"] = verdict

    os.makedirs(OUT_DIR, exist_ok=True)
    out_json = os.path.join(OUT_DIR, f"{date.today()}_news_backfill_feasibility.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[saved] {out_json}")
    for line in verdict:
        print(" -", line)
    print(f"elapsed={round(time.time() - t0, 1)}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
