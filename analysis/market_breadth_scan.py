#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开盘异动三分类扫描（暴跌重灾区 / 抗跌避风港 / 逆势红盘）
====================================================
用途: 交易日 09:35 开盘后对关注池做一次快照式异动扫描:
  1. 三分类: 暴跌重灾区(≤-2%) / 逆势红盘(≥+1%) / 抗跌避风港(跑赢上证≥0.5pp)
  2. 开盘缺口异动: 深低开≤-2%(警示区) / 温和低开[-2%,-1%)(买侧甜点窗) / 大幅高开≥+3%
  3. 量价异动: 盘中量比≥2 或 振幅≥5% (量比 = 已成交量/已交易分钟 ÷ 昨日全天均量)

数据源:
  - analysis.service.get_spot_scan()  新浪 hq 实时快照(关注池口径, 含 open/high/low/volume)
  - 本地 stock_data CSV 昨日成交量(量比分母, 零网络请求)
设计原则: 快照式只读; 不执行交易; 失败可见告警不静默; 数据日期非今日时显式标注。
"""
import os
import sys
import json
import glob
import csv
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

STOCK_DATA_DIRS = [
    os.path.join(WORKSPACE, "data", "stock_data"),
    "/Users/duguke/stock_data",
]

OUTPUT_FILE = os.path.join(WORKSPACE, "data", "market_breadth_output.json")


def _log(msg):
    print(msg, file=sys.stderr)


def load_yday_volume(code: str, today: str):
    """读本地 CSV 取昨日(最近一个 < today 的交易日)全天成交量(股)。找不到返回 None。"""
    for d in STOCK_DATA_DIRS:
        hits = [f for f in glob.glob(os.path.join(d, code + "_*.csv")) if ".bak" not in f]
        if not hits:
            continue
        path = hits[0]
        best = None
        try:
            with open(path, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    dt = (row.get("date") or "").strip()
                    vol = (row.get("volume") or "").strip()
                    if not dt or dt >= today or not vol:
                        continue
                    try:
                        v = float(vol)
                    except ValueError:
                        continue
                    if best is None or dt > best[0]:
                        best = (dt, v)
            if best:
                return best[1]
        except Exception as e:
            _log(f"⚠️ 昨日量读取失败 {code}: {e}")
            return None
    return None


def _elapsed_trading_minutes(now) -> float:
    """今日已交易分钟数(粗): 09:30-11:30 + 13:00-15:00"""
    t = now.hour * 60 + now.minute
    open1, close1, open2, close2 = 9 * 60 + 30, 11 * 60 + 30, 13 * 60, 15 * 60
    if t <= open1:
        return 0.0
    if t <= close1:
        return float(t - open1)
    if t <= open2:
        return 120.0
    if t <= close2:
        return 120.0 + (t - open2)
    return 240.0


def _scan_inner(now, today) -> dict:
    from analysis.service import get_spot_scan, fetch_hq_dict

    results, data_date = get_spot_scan()

    idx = {}
    try:
        hq = fetch_hq_dict(["sh000001"])
        r = hq.get("000001")
        if r and r.get("pre_close"):
            idx = {
                "name": "上证指数",
                "price": round(r["last"], 2),
                "change_pct": round((r["last"] / r["pre_close"] - 1) * 100, 2),
                "open_gap_pct": round((r["open"] / r["pre_close"] - 1) * 100, 2) if r.get("open") else None,
                "data_date": r.get("date", data_date),
            }
    except Exception as e:
        _log(f"⚠️ 指数行情获取失败: {e}")
    index_chg = idx.get("change_pct", 0.0)

    elapsed = _elapsed_trading_minutes(now)
    in_session = 0 < elapsed < 240

    disaster, gainers, havens, flat, errors = [], [], [], [], []
    movers, gap_deep, gap_sweet, gap_high = [], [], [], []

    for r in results:
        if r.get("error"):
            errors.append({"symbol": r.get("symbol"), "name": r.get("name"), "error": r["error"]})
            continue
        chg = r.get("change_pct") or 0.0
        pre = r.get("pre_close") or 0.0
        op = r.get("open") or 0.0
        hi = r.get("high") or 0.0
        lo = r.get("low") or 0.0
        gap = (op / pre - 1) * 100 if pre and op else None
        amp = (hi - lo) / pre * 100 if pre and hi and lo else None
        vr = None
        if in_session:
            yv = load_yday_volume(r["symbol"], today)
            if yv and elapsed > 0 and (r.get("volume") or 0) > 0:
                vr = round((r["volume"] / elapsed) / (yv / 240.0), 2)

        item = {
            "symbol": r["symbol"], "name": r["name"], "price": r["price"],
            "change_pct": chg,
            "gap_pct": round(gap, 2) if gap is not None else None,
            "amplitude_pct": round(amp, 2) if amp is not None else None,
            "vol_ratio": vr,
        }

        if gap is not None:
            if gap <= -2.0:
                gap_deep.append(item)
            elif gap < -1.0:
                gap_sweet.append(item)
            elif gap >= 3.0:
                gap_high.append(item)

        if chg <= -2.0:
            disaster.append(item)
        elif chg >= 1.0:
            gainers.append(item)
        elif (chg - index_chg) >= 0.5:
            havens.append(item)
        else:
            flat.append(item)

        if (vr is not None and vr >= 2.0) or (amp is not None and amp >= 5.0):
            movers.append(item)

    disaster.sort(key=lambda x: x["change_pct"])
    gainers.sort(key=lambda x: -x["change_pct"])
    havens.sort(key=lambda x: -x["change_pct"])

    out = {
        "generated_at": now.isoformat(timespec="seconds"),
        "scan_for": today,
        "data_date": data_date,
        "in_session": in_session,
        "index": idx,
        "counts": {
            "total": len(results), "disaster": len(disaster), "gainers": len(gainers),
            "havens": len(havens), "flat": len(flat), "errors": len(errors),
        },
        "disaster": disaster,
        "gainers": gainers,
        "havens": havens[:8],
        "movers": movers[:10],
        "gap_deep_low": gap_deep,
        "gap_sweet_low": gap_sweet,
        "gap_high_open": gap_high,
        "errors": errors,
    }
    out["brief"] = build_brief(out)
    return out


def run_scan(now=None) -> dict:
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    try:
        from analysis.net_guard import network_timeout_guard
        with network_timeout_guard(30):
            return _scan_inner(now, today)
    except ImportError:
        _log("⚠️ net_guard 不可用, 使用库默认超时继续")
        return _scan_inner(now, today)


def _fmt_item(it) -> str:
    s = f"  {it['name']} {it['change_pct']:+.2f}%"
    tags = []
    if it.get("gap_pct") is not None:
        g = it["gap_pct"]
        if g <= -2.0:
            tags.append("深低开⚠️")
        elif g < -1.0:
            tags.append("温和低开")
        elif g >= 3.0:
            tags.append("高开")
    if it.get("vol_ratio") is not None:
        tags.append(f"量比{it['vol_ratio']}x")
    if tags:
        s += " (" + ",".join(tags) + ")"
    return s


def build_brief(d: dict) -> str:
    idx = d.get("index") or {}
    c = d.get("counts", {})
    stale = d.get("data_date") != d.get("scan_for")
    lines = []
    head = "🚨 开盘异动扫描"
    if stale:
        head += f" (⚠️数据日期 {d.get('data_date')}, 非今日)"
    lines.append(head)
    lines.append(f"⏰ {d.get('scan_for')} {str(d.get('generated_at', ''))[11:16]}")
    if idx:
        g = idx.get("open_gap_pct")
        gtxt = f" 开盘{g:+.2f}%" if g is not None else ""
        lines.append(f"📊 上证 {idx.get('price')} ({idx.get('change_pct', 0):+.2f}%){gtxt}")
    lines.append(
        f"🧮 关注池{c.get('total', 0)}只: 灾区{c.get('disaster', 0)} "
        f"红盘{c.get('gainers', 0)} 避风港{c.get('havens', 0)} 平淡{c.get('flat', 0)}"
        + (f" 失败{c.get('errors', 0)}" if c.get("errors") else "")
    )
    lines.append("━━━━━━━━━━━━━━━━━")

    if d.get("disaster"):
        lines.append(f"🔴 暴跌重灾区 (≤-2%, {len(d['disaster'])}只):")
        for it in d["disaster"][:8]:
            lines.append(_fmt_item(it))
        if len(d["disaster"]) > 8:
            lines.append(f"  …等 {len(d['disaster'])} 只")
    else:
        lines.append("🔴 暴跌重灾区: 无")

    if d.get("gainers"):
        lines.append(f"🟢 逆势红盘 (≥+1%, {len(d['gainers'])}只):")
        for it in d["gainers"][:8]:
            lines.append(_fmt_item(it))
        if len(d["gainers"]) > 8:
            lines.append(f"  …等 {len(d['gainers'])} 只")
    else:
        lines.append("🟢 逆势红盘: 无")

    if d.get("havens"):
        lines.append("🛡️ 抗跌避风港 (跑赢上证≥0.5pp, 前8):")
        for it in d["havens"]:
            lines.append(_fmt_item(it))
    else:
        lines.append("🛡️ 抗跌避风港: 无")

    if d.get("gap_sweet_low"):
        lines.append(f"🍬 温和低开甜点窗 [-2%,-1%) ({len(d['gap_sweet_low'])}只):")
        for it in d["gap_sweet_low"][:6]:
            lines.append(_fmt_item(it))
    if d.get("gap_high_open"):
        lines.append(f"📈 大幅高开 ≥+3% ({len(d['gap_high_open'])}只):")
        for it in d["gap_high_open"][:6]:
            lines.append(_fmt_item(it))
    if d.get("movers"):
        lines.append("⚡ 量价异动 (量比≥2 或 振幅≥5%):")
        for it in d["movers"]:
            lines.append(_fmt_item(it))
    if d.get("errors"):
        lines.append(f"❌ 行情失败: {', '.join(e.get('name') or e.get('symbol') or '?' for e in d['errors'])}")

    lines.append("——")
    lines.append("数据: 新浪实时快照 [来源: close_scan_v2 数据链] · 仅供参考, 不构成投资建议")
    return "\n".join(lines)


if __name__ == "__main__":
    scan = run_scan()
    if "--brief" in sys.argv:
        print(scan.get("brief", ""))
    else:
        print(json.dumps(scan, ensure_ascii=False, indent=1))
