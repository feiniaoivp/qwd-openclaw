#!/usr/bin/env python3
"""
电网装备/特高压出海板块 - 监控推送 (Command 模式)
==================================================
直接运行 power_equipment_overseas_monitor.py 并把 JSON 渲染为 TG 消息推送。
用于 command cron 直连，**绕开 LLM**，从机制上杜绝伪造数据。

背景 (2026-09-13): 原配置为 LLM agentTurn + toolsAllow 含 write/apply_patch，
导致 agent 编造数据落盘（伪造招标/营收/未来日期周报）。改为脚本直跑直推。

用法:
  python3 analysis/power_overseas_push.py --mode eod
  python3 analysis/power_overseas_push.py --mode intraday
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

WORKSPACE = "/Users/duguke/.openclaw/workspace"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

SCRIPT = "analysis/power_equipment_overseas_monitor.py"


def run_monitor(mode: str) -> tuple[dict | None, str]:
    """运行监控脚本并解析 JSON 输出。返回 (data, raw_stdout)"""
    result = subprocess.run(
        [sys.executable, SCRIPT, "--mode", mode],
        cwd=WORKSPACE, capture_output=True, text=True, timeout=300,
    )
    out = (result.stdout or "").strip()
    if not out:
        return None, (result.stderr or "无输出").strip()
    # 取最后一个完整 JSON 对象
    start = out.find("{")
    if start < 0:
        return None, out[:800]
    try:
        return json.loads(out[start:]), out
    except json.JSONDecodeError as e:
        return None, f"JSON解析失败: {e}\n{out[:500]}"


def render_eod(data: dict) -> str:
    s = data.get("sector_summary", {})
    lines = [
        f"⚡ 电网装备/特高压出海 · 收盘复盘",
        f"📅 {data.get('date','')}  |  数据源: 新浪实时行情",
        "",
        f"板块: 涨 {s.get('up',0)} / 跌 {s.get('down',0)} | 均涨跌 {s.get('avg_chg',0):+.2f}%",
        "",
    ]
    # 按涨跌幅排序
    stocks = sorted(data.get("stocks", []), key=lambda x: x.get("change_pct", 0), reverse=True)
    for st in stocks:
        chg = st.get("change_pct", 0)
        mark = "🟢" if chg > 0 else ("🔴" if chg < 0 else "⚪")
        # 均线位置简写
        ma = []
        if st.get("above_ema20"): ma.append("20")
        if st.get("above_ema50"): ma.append("50")
        if st.get("above_ema200"): ma.append("200")
        ma_txt = f"站上MA{','.join(ma)}" if ma else "破全部均线"
        macd = "MACD金叉" if st.get("macd_cross_up") else ("MACD死叉" if st.get("macd_cross_down") else ("零轴上" if st.get("macd_above_zero") else "零轴下"))
        vr = st.get("vol_ratio", 0) or 0
        lines.append(
            f"{mark} {st.get('name','')}({st.get('symbol','')}) "
            f"{st.get('price','-')} {chg:+.2f}% | 量比{vr:.2f} | {ma_txt} | {macd}"
        )
    # 最强/最弱
    if stocks:
        lines += ["", f"最强: {stocks[0].get('name')} {stocks[0].get('change_pct',0):+.2f}%",
                  f"最弱: {stocks[-1].get('name')} {stocks[-1].get('change_pct',0):+.2f}%"]
    lines += ["", f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} | 数据日期见各标的"]
    return "\n".join(lines)


def render_intraday(data: dict) -> str:
    alerts = data.get("alerts", [])
    lines = [f"⚡ 电网装备/特高压出海 · 盘中预警", f"📅 {data.get('date','')}", ""]
    if not alerts:
        lines.append("无异动信号")
    else:
        for a in alerts:
            lines.append(f"⚠️ {a.get('symbol','')} {a.get('name','')}: {a.get('reason','')}")
    lines += ["", f"🕐 {datetime.now().strftime('%H:%M')}"]
    return "\n".join(lines)


def run_monitor_raw(mode: str) -> tuple[str, int]:
    """运行监控脚本，返回 (stdout, returncode)。用于 weekly 这类纯文本输出模式。"""
    result = subprocess.run(
        [sys.executable, SCRIPT, "--mode", mode],
        cwd=WORKSPACE, capture_output=True, text=True, timeout=300,
    )
    return (result.stdout or "").strip(), result.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["eod", "intraday", "weekly"], default="eod")
    ap.add_argument("--no-push", action="store_true", help="只渲染不推送(调试用)")
    args = ap.parse_args()

    # weekly 模式脚本输出纯文本(非JSON)，直接透传 stdout
    if args.mode == "weekly":
        out, rc = run_monitor_raw("weekly")
        if not out:
            print("[SKIP] weekly 无输出")
            return 0
        text = out
        print(text)
        if not args.no_push:
            from send_telegram import send_with_retry
            send_with_retry(text, parse_mode="")
            print("\n[PUSHED]")
        return 0

    data, raw = run_monitor(args.mode)
    if data is None:
        # 非窗口/无输出属正常，静默退出
        if "窗口" in raw or "退出" in raw:
            print(f"[SKIP] {raw}")
            return 0
        msg = f"❌ 电网装备出海监控执行异常({args.mode})\n{raw[:600]}"
        print(msg)
        if not args.no_push:
            try:
                from send_telegram import send_with_retry
                send_with_retry(msg, parse_mode="")
            except Exception as e:
                print(f"[WARN] 推送失败: {e}")
        return 1

    if args.mode == "eod":
        text = render_eod(data)
    else:
        text = render_intraday(data)
    print(text)
    if not args.no_push:
        from send_telegram import send_with_retry
        send_with_retry(text, parse_mode="")
        print("\n[PUSHED]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
