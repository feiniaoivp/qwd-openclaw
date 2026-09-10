#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘前9:00竞价/实时扫描 — 2026-08-07 复用 service.py 的 fetch_hq_dict"""
import sys, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from service import fetch_hq_dict, WATCHLIST


def build_rows():
    codes = []
    for code, _name in WATCHLIST:
        codes.append(("sh" if code[0] in "69" else "sz") + code)
    hq = fetch_hq_dict(codes)
    rows = []
    for code, name in WATCHLIST:
        r = hq.get(code)
        if not r or r["last"] == 0:
            rows.append({"code": code, "name": name, "error": "无行情/未成交"})
            continue
        pre = r["pre_close"]
        last = r["last"]
        openp = r["open"]
        chg_pct = (last / pre - 1) * 100 if pre else 0
        open_chg = (openp / pre - 1) * 100 if pre else 0
        amp = (r["high"] - r["low"]) / pre * 100 if pre else 0
        amt_yi = r["amount"] / 1e8
        rows.append({
            "code": code, "name": name, "price": round(last, 2),
            "chg_pct": round(chg_pct, 2), "open": round(openp, 2),
            "open_chg_pct": round(open_chg, 2), "amp_pct": round(amp, 2),
            "amount_yi": round(amt_yi, 3), "volume": r["volume"],
            "high": round(r["high"], 2), "low": round(r["low"], 2),
            "time": r["time"], "date": r["date"],
        })
    return rows


def build_brief(rows, ts: str) -> str:
    """规则化组装盘前简报文本（不经 LLM，供 cron 直推）。"""
    lines = [f"📊 盘前竞价扫描 · {ts}", ""]
    strong, weak, lim_up = [], [], []
    for r in rows:
        if r.get("error"):
            continue
        chg, op = r.get("chg_pct"), r.get("open_chg_pct")
        if chg is not None and chg > 2:
            strong.append(f"{r['name']}({r['code']}) {chg:+.2f}% 额{r.get('amount_yi',0):.2f}亿")
        if chg is not None and chg < -2:
            weak.append(f"{r['name']}({r['code']}) {chg:+.2f}% 额{r.get('amount_yi',0):.2f}亿")
        if op is not None and ((op >= 9.5 and r['code'][0] in '036') or (op >= 19.5 and r['code'][0] in '30')):
            lim_up.append(f"{r['name']}({r['code']}) 开{op:+.2f}%")
    def _s(t, items):
        return ([f"**{t}**"] + [f"- {i}" for i in items] + [""]) if items else []
    lines += _s("🚀 高开(>2%)", strong)
    lines += _s("📉 低开(<-2%)", weak)
    lines += _s("🔒 涨停预判", lim_up)
    lines.append("**📋 明细**")
    for r in rows:
        if r.get("error"):
            lines.append(f"- {r['name']}({r['code']}) {r['error']}")
            continue
        lines.append(f"- {r['name']}({r['code']}) {r['chg_pct']:+.2f}% 开{r['open_chg_pct']:+.2f}% 额{r.get('amount_yi',0):.2f}亿")
    return "\n".join(lines)


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(build_rows(), ensure_ascii=False, indent=2))

