#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘中策略预警推送 — command cron 直跑脚本 (绕开 LLM，防超时/防 fallback 崩)
==========================================================================
流程:
  1. 调用 close_scan_v2.py --intraday 扫描实时行情，对照策略配置得到预警
  2. 只取 triggered_this_run (本轮新触发的预警，per-价位去重)
  3. 有触发 → 组纯文本简报 → send_telegram 推送 (parse_mode='' 纯文本，规避实体 bug)
    无触发 → 静默退出(不推送，避免 noise)
用法:
  python3 scripts/push_intraday_alerts.py
输出: 0 = 正常运行(推送或静默); 非0 = 出错
"""

import os
import sys
import json
import subprocess
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)

OUTPUT_FILE = os.path.join(WORKSPACE, "data", "intraday_alert_output.json")


def run_scan() -> dict:
    """跑 --intraday 扫描并把结果 (JSON stdout) 解析回 dict"""
    cmd = [sys.executable, os.path.join(WORKSPACE, "analysis", "close_scan_v2.py"), "--intraday"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           cwd=WORKSPACE, env={**os.environ, "WORKSPACE": WORKSPACE})
    except subprocess.TimeoutExpired:
        print("intraday scan timeout", file=sys.stderr)
        return {}
    if r.returncode != 0:
        print(f"intraday scan 失败 rc={r.returncode}: {r.stderr[-500:]}", file=sys.stderr)
        return {}
    try:
        return json.loads(r.stdout)
    except Exception as e:
        print(f"解析 intraday 输出失败: {e}", file=sys.stderr)
        return {}


# ── 大盘关键位监控 (上证指数) ──
INDEX_LEVELS = {
    "pivot": 3900.0,
    "support": [3850.0, 3800.0, 3764.0],
    "resistance": [3929.0, 3943.0, 3986.0],
}
INDEX_STATE_FILE = os.path.join(WORKSPACE, "data", "intraday_index_alert_state.json")


def _load_index_state() -> dict:
    if os.path.exists(INDEX_STATE_FILE):
        try:
            with open(INDEX_STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_index_state(st: dict) -> None:
    os.makedirs(os.path.dirname(INDEX_STATE_FILE), exist_ok=True)
    with open(INDEX_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def check_index_levels() -> list:
    """检查上证指数是否穿越关键位，返回本轮新触发的提示(去重)"""
    try:
        from analysis.data_layer import get_router
        router = get_router()
        spot = router.get_spot(["sh000001"])
        if not spot:
            return []
        item = list(spot.values())[0]
        price = float(item.get("close") or 0)
        if price <= 0:
            return []
        chg = (price / item["pre_close"] - 1) * 100 if item.get("pre_close") else 0.0
    except Exception as e:
        print(f"指数行情获取失败: {e}", file=sys.stderr)
        return []

    st = _load_index_state()
    fired = st.get("fired", {})
    out = []

    def _fire(key, text, severity):
        if fired.get(key) == price:
            return
        fired[key] = price
        out.append({"name": "上证指数", "symbol": "000001", "price": round(price, 2),
                    "change_pct": round(chg, 2), "msg": text, "severity": severity,
                    "level_name": "关键位"})

    for lv in INDEX_LEVELS["support"]:
        if price <= lv:
            _fire(f"support:{lv}", f"⚠️ 上证跌破支撑 {lv:.0f} (现 {price:.2f})，注意加速下行风险", "HIGH")
    for lv in INDEX_LEVELS["resistance"]:
        if price >= lv:
            _fire(f"resistance:{lv}", f"✅ 上证升破压力 {lv:.0f} (现 {price:.2f})，短线转强信号", "MEDIUM")
    piv = INDEX_LEVELS["pivot"]
    if price >= piv:
        _fire(f"pivot_up:{piv}", f"上证站上分水岭 {piv:.0f} (现 {price:.2f})", "LOW")
    else:
        _fire(f"pivot_dn:{piv}", f"上证位于分水岭 {piv:.0f} 下方 (现 {price:.2f})，弱势", "LOW")

    st["fired"] = fired
    st["last_price"] = price
    st["updated_at"] = datetime.now().isoformat()
    _save_index_state(st)
    return out


def build_message(data: dict, triggered: list = None) -> str:
    """纯文本简报(不带 Markdown 特殊字符，规避 TG 实体 400)"""
    t = data.get("generated_at", "") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if triggered is None:
        triggered = data.get("triggered_this_run", [])
    lines = [f"⚠️ 盘中策略预警 ({t})", "━━━━━━━━━━━━━━━━━"]
    sev = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🔵"}
    lname = {"break_below": "跌破止损", "break_above": "升破关键位", "buy_zone": "低吸区", "drop_pct": "急跌预警", "surge_pct": "急涨预警"}
    for a in triggered:
        emoji = sev.get(a.get("severity"), "⚪")
        label = a.get("level_name") or lname.get(a.get("code"), "")
        lines.append(
            f"{emoji} {a['name']}({a['symbol']}) · {label}\n"
            f"   现价 {a['price']} · 日内 {a['change_pct']:+.2f}%\n"
            f"   提示: {a['msg']}"
        )
    return "\n".join(lines)


def main():
    data = run_scan()
    if not data:
        # 扫描失败时不静默，返回非0便于 cron 失败告警
        print("扫描失败/无数据", file=sys.stderr)
        return 2

    triggered = list(data.get("triggered_this_run") or [])
    # 追加大盘关键位提示(上证)
    try:
        idx_alerts = check_index_levels()
    except Exception as e:
        print(f"指数关键位检查失败: {e}", file=sys.stderr)
        idx_alerts = []
    triggered = idx_alerts + triggered
    if not triggered:
        # 本轮无新触发，静默不推送 (防 noise)
        return 0

    msg = build_message(data, triggered)

    # 落盘简报供审计
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({**data, "brief": msg, "pushed_at": datetime.now().isoformat()},
                  f, ensure_ascii=False, indent=2, default=str)

    # 推送 (纯文本)
    from send_telegram import send_message
    resp = send_message(msg, parse_mode="")
    if isinstance(resp, dict) and resp.get("ok"):
        print(f"已推送 {len(triggered)} 条预警")
        return 0
    print(f"推送失败: {resp}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
