#!/usr/bin/env python3
"""
auction_signal_push.py — 竞价×盘前信号交集推送（2026-10-11 #5）
================================================================
数据：data/auction/{today}_9_25.json（auction_feed 09:25 快照）
    × analysis/daily/{最新}_close_scan.json（daily_push 18:30 落盘的信号档案）
规则：
    🟢 强烈买入/关注 + 高开≥3%   → 共振关注
    🟢 强烈买入/关注 + 低开≤-3%   → 信号遇低开（回踩支撑观察）
    🔴 强烈卖出/谨慎 + 低开≤-3%   → 共振风险（加速）
    🔴 强烈卖出/谨慎 + 高开≥3%   → 反常（警惕诱多）
    任意 |竞价涨跌|≥5%           → 巨幅异动（无论信号）
无交集 → 静默退出（不推送、不噪音）。产出落盘 data/auction/signal_push_{date}.json。
"""

import os
import sys
import json
import glob

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

AUCTION_DIR = os.path.join(WORKSPACE, "data", "auction")
SCAN_DIR = os.path.join(WORKSPACE, "analysis", "daily")


def _latest_close_scan():
    files = sorted(glob.glob(os.path.join(SCAN_DIR, "*_close_scan.json")))
    if not files:
        return None
    try:
        with open(files[-1], encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"⚠️ close_scan 档案解析失败: {e}\n")
        return None


def _today_auction(today: str):
    path = os.path.join(AUCTION_DIR, f"{today}_9_25.json")
    if not os.path.exists(path):
        cands = [p for p in glob.glob(os.path.join(AUCTION_DIR, f"{today}_*.json"))
                 if "decision" not in os.path.basename(p) and "signal_push" not in os.path.basename(p)]
        if not cands:
            return None
        path = sorted(cands)[-1]
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"⚠️ 竞价快照解析失败: {e}\n")
        return None


def main() -> int:
    from datetime import datetime
    from analysis.service import is_trading_day

    today = datetime.now().strftime("%Y-%m-%d")
    if not is_trading_day():
        print(f"ℹ️ {today} 非交易日，跳过")
        return 0

    auction = _today_auction(today)
    scan = _latest_close_scan()
    if not auction or not auction.get("rows"):
        print(f"ℹ️ {today} 竞价快照缺失，跳过（feed job 自身健康由 health-check 监控）")
        return 0
    if not scan or not scan.get("stocks"):
        print("ℹ️ close_scan 信号档案缺失，跳过")
        return 0

    scan_date = scan.get("data_date", "?")
    signals = {s["symbol"]: s for s in scan["stocks"]}

    GREEN = ("🟢",)
    RED = ("🔴", "🟠")

    hits = []
    for r in auction["rows"]:
        code = r.get("code", "")
        sig = signals.get(code) or {}
        sig_obj = (sig.get("signal") or {}) if isinstance(sig, dict) else {}
        level = sig_obj.get("level", "")
        ochg = r.get("open_chg_pct")
        if ochg is None:
            continue
        tag = None
        if level.startswith(GREEN) and ochg >= 3:
            tag = "🟢信号+高开共振"
        elif level.startswith(GREEN) and ochg <= -3:
            tag = "🟢信号遇低开(回踩观察)"
        elif level.startswith(RED) and ochg <= -3:
            tag = "🔴信号+低开共振(风险加速)"
        elif level.startswith(RED) and ochg >= 3:
            tag = "⚠️卖出信号遇高开(警惕诱多)"
        if tag is None and abs(ochg) >= 5:
            tag = "竞价巨幅异动"
        if tag:
            hits.append({
                "code": code, "name": r.get("name", ""), "tag": tag,
                "open_chg_pct": ochg, "signal_level": level,
                "signal_score": sig_obj.get("score"),
                "signal_date": scan_date,
                "support": (sig or {}).get("support"), "resistance": (sig or {}).get("resistance"),
            })

    if not hits and not [r for r in auction["rows"] if (r.get("open_chg_pct") or 0) >= 2 and not r.get("is_limit_up")]:
        print(f"ℹ️ {today} 竞价×信号无交集且无高开回落风险标的，静默退出")
        return 0

    # ── 2026-10-17: 高开回落风险段（竞价研究 be3f8e9: 高开≥2% 盘中-2.02%/胜率11%，次日-1.97%；缩量高开更虚）──
    fade = []
    _hit_codes = {h["code"] for h in hits}
    for r in auction["rows"]:
        ochg = r.get("open_chg_pct")
        if ochg is None or ochg < 2 or r.get("is_limit_up") or r.get("code") in _hit_codes:
            continue
        vr = r.get("vol_ratio_pct")
        fade.append({
            "code": r.get("code", ""), "name": r.get("name", ""),
            "open_chg_pct": ochg, "vol_ratio_pct": vr,
            "thin": (vr is not None and vr < 50),
        })
    fade.sort(key=lambda x: -x["open_chg_pct"])
    fade = fade[:10]

    hits.sort(key=lambda x: -abs(x["open_chg_pct"]))
    lines = [f"🔔 竞价×信号交集 {today}",
             f"信号基准: {scan_date} 收盘扫描 | 竞价快照: {auction.get('snapshot_time','')}", ""]
    for h in hits:
        lines.append(
            f"{h['tag']} | {h['name']}({h['code']}) 竞价{h['open_chg_pct']:+.2f}%"
            f" | 昨收信号 {h['signal_level']}(score={h['signal_score']})"
            + (f" | 支撑{h['support']}/压力{h['resistance']}" if h.get("support") else "")
        )
    lines.append("")
    lines.append("—— 竞价数据≠开盘走势，9:30 后需二次确认；明细见拍卖分析推送")

    if fade:
        lines.append("")
        lines.append("⚠️ 高开回落风险（研究: 高开≥2% 盘中均值-2.02%/胜率11%，次日-1.97%）")
        for fd in fade:
            vol = f" 缩量(量比{fd['vol_ratio_pct']:.0f}%)" if fd["thin"] else ""
            lines.append(f"  {fd['name']}({fd['code']}) 高开{fd['open_chg_pct']:+.2f}%{vol}")

    text = "\n".join(lines)
    out_path = os.path.join(AUCTION_DIR, f"signal_push_{today}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"date": today, "scan_date": scan_date, "hits": hits, "fade_risk": fade}, f, ensure_ascii=False, indent=1)

    try:
        from send_telegram import send_with_retry
        res = send_with_retry(text, parse_mode="")
        print(f"推送 {len(hits)} 条交集 → Telegram ok={res.get('ok')}")
        return 0 if res.get("ok") else 1
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"⚠️ Telegram 推送失败(交集已落盘 {out_path}): {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
