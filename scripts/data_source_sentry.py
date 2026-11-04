#!/usr/bin/env python3
"""data_source_sentry — 核心数据源实时哨兵（T2-逻辑层, 2026-11-04）

从「事后审计」转向「实时预警」：每 30 分钟探测核心数据源，
连续 N 次（默认 3）失败 → 立即 Telegram Critical 告警（每次事故只发一次）；
恢复后补发恢复通知。同时生成 Heartbeat Dashboard（Markdown 表格）。

探测对象（轻量、秒级、零副作用）:
- baostock: 登录态 + 单票日线查询
- sina_hq:  实时行情 HTTP 探测（带 Referer）
- sim_state: 模拟盘状态文件新鲜度（>48h 告警，不参与连续计数——非交易日本来就不更新）

状态: data/sentry_state.json（连续失败计数 + 告警幂等标记）
面板: analysis/dashboards/heartbeat_dashboard.md

用法:
    python3 scripts/data_source_sentry.py            # 单次探测 tick
    python3 scripts/data_source_sentry.py --no-push  # 只探测/更新面板，不发 Telegram（测试用）
"""
import os
import sys
import json
import time
import socket
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "scripts"), os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SENTRY_STATE = os.path.join(WORKSPACE, "data", "sentry_state.json")
DASHBOARD_MD = os.path.join(WORKSPACE, "analysis", "dashboards", "heartbeat_dashboard.md")
SIM_STATE = os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")
MIRROR_CSV = os.path.join(WORKSPACE, "data", "daily_equity_mirror.csv")

ALERT_THRESHOLD = int(os.getenv("SENTRY_FAIL_THRESHOLD", "3"))
NO_PUSH = "--no-push" in sys.argv


def _load_state():
    if os.path.exists(SENTRY_STATE):
        try:
            with open(SENTRY_STATE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ sentry 状态读取失败（重置）: {e}")
    return {"sources": {}}


def _save_state(state):
    # 重写型写点备份保护（backup_guard 铁律）
    if os.path.exists(SENTRY_STATE):
        import shutil
        try:
            shutil.copy2(SENTRY_STATE, SENTRY_STATE + ".bak")
        except Exception as e:
            print(f"⚠️ sentry 状态备份失败（继续写入）: {e}")
    tmp = SENTRY_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SENTRY_STATE)


def _push_critical(text):
    """Telegram Critical 推送（脚本自推，带代理兜底；失败可见不静默）"""
    if NO_PUSH:
        print(f"🧪 [no-push 模式] 本应推送: {text[:120]}")
        return False
    try:
        from send_telegram import send_with_retry
        r = send_with_retry(f"🚨 [CRITICAL] 数据源哨兵\n\n{text}", max_retries=2, retry_delay=2)
        if not r.get("ok"):
            print(f"❌ Critical 推送失败: {r.get('error')}")
        return bool(r.get("ok"))
    except Exception as e:
        print(f"❌ Critical 推送异常: {e}")
        return False


# ════════════════ 探测器（每个返回 (ok: bool, detail: str)）════════════════

def probe_baostock():
    """baostock 登录态 + 单票日线查询（600030 近 5 根）"""
    try:
        from analysis.bs_session import ensure_login
        import baostock as bs
        if not ensure_login():
            return False, "登录失败（ensure_login=False）"
        rs = bs.query_history_k_data_plus(
            "sh.600030", "date,close",
            start_date="2026-10-28", end_date="2026-11-04",
            frequency="d", adjustflag="2")
        if rs is None or rs.error_code != "0":
            return False, f"查询错误: {getattr(rs, 'error_msg', 'None') if rs else 'rs=None'}"
        rows = []
        while rs.next() and len(rows) < 10:
            rows.append(rs.get_row_data())
        if len(rows) >= 1:
            return True, f"正常 ({len(rows)} 根K线)"
        return False, "查询成功但 0 行（空结果）"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:100]}"


def probe_sina():
    """新浪实时行情 HTTP 探测（中信证券 sh600030）"""
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://hq.sinajs.cn/list=sh600030",
            headers={"Referer": "https://finance.sina.com.cn"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("gbk", errors="ignore")
        if "600030" in body and '"' in body:
            return True, "正常 (HTTP 200, 有报价)"
        return False, f"响应异常: {body[:60]!r}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:100]}"


def check_sim_state():
    """模拟盘状态新鲜度（软检查，不参与连续失败计数）"""
    if not os.path.exists(SIM_STATE):
        return False, "state 文件缺失"
    age_h = (time.time() - os.path.getmtime(SIM_STATE)) / 3600
    if age_h > 48:
        return False, f"state 滞后 {age_h:.0f}h"
    return True, f"新鲜 ({age_h:.1f}h 前)"


PROBES = {"baostock": probe_baostock, "sina_hq": probe_sina}
SOFT_CHECKS = {"sim_state": check_sim_state}


def run_tick():
    state = _load_state()
    sources = state.setdefault("sources", {})
    alerts, recoveries = [], []

    for name, probe in PROBES.items():
        t0 = time.time()
        try:
            ok, detail = probe()
        except Exception as e:
            ok, detail = False, f"probe 异常: {e}"
        dur = round(time.time() - t0, 2)
        rec = sources.setdefault(name, {"consecutive_fails": 0, "last_ok_ts": None,
                                        "incident_alerted": False, "last_detail": ""})
        rec["last_detail"] = detail
        rec["last_probe_ts"] = datetime.now().isoformat(timespec="seconds")
        rec["last_probe_dur_s"] = dur
        if ok:
            rec["consecutive_fails"] = 0
            rec["last_ok_ts"] = rec["last_probe_ts"]
            if rec.get("incident_alerted"):
                recoveries.append(f"✅ {name} 已恢复（{detail}）")
                rec["incident_alerted"] = False
        else:
            rec["consecutive_fails"] += 1
            if rec["consecutive_fails"] >= ALERT_THRESHOLD and not rec.get("incident_alerted"):
                alerts.append(f"🔴 {name} 连续 {rec['consecutive_fails']} 次失败（阈值 {ALERT_THRESHOLD}）\n   详情: {detail}")
                rec["incident_alerted"] = True
                rec["first_alert_ts"] = rec["last_probe_ts"]

    # 软检查（不计数，仅面板 + 超 48h 告警一次）
    for name, chk in SOFT_CHECKS.items():
        try:
            ok, detail = chk()
        except Exception as e:
            ok, detail = False, str(e)[:80]
        rec = sources.setdefault(name, {"consecutive_fails": 0, "last_ok_ts": None,
                                        "incident_alerted": False, "last_detail": ""})
        prev = rec.get("last_detail", "")
        rec["last_detail"] = detail
        rec["last_probe_ts"] = datetime.now().isoformat(timespec="seconds")
        if not ok and detail != prev and "滞后" in detail:
            alerts.append(f"🟡 {name}: {detail}")

    _save_state(state)

    # ── Telegram 推送（Critical 优先，幂等：每事故一次）──
    if alerts:
        sent = _push_critical("\n\n".join(alerts))
        if sent:
            print(f"📨 Critical 已推送: {len(alerts)} 条")
    if recoveries:
        try:
            from send_telegram import send_with_retry
            send_with_retry("🟢 [RECOVERY] 数据源哨兵\n\n" + "\n".join(recoveries), max_retries=2)
        except Exception as e:
            print(f"⚠️ 恢复通知失败: {e}")

    write_dashboard(state)
    return alerts, recoveries


def write_dashboard(state):
    """生成 Heartbeat Dashboard（Markdown 表格）"""
    os.makedirs(os.path.dirname(DASHBOARD_MD), exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 💓 Heartbeat Dashboard",
        f"_自动生成 @ {now}（每 30 分钟刷新 by data_source_sentry）_",
        "",
        "## 数据源哨兵",
        "| 数据源 | 状态 | 连续失败 | 最近探测 | 详情 |",
        "|:---|:---|:---:|:---|:---|",
    ]
    for name, rec in state.get("sources", {}).items():
        fails = rec.get("consecutive_fails", 0)
        if name in SOFT_CHECKS:
            status = "🟢" if ("滞后" not in rec.get("last_detail", "") and "缺失" not in rec.get("last_detail", "")) else "🟡"
        else:
            status = "🟢" if fails == 0 else ("🟡" if fails < ALERT_THRESHOLD else "🔴")
        lines.append(f"| {name} | {status} | {fails} | {rec.get('last_probe_ts','-')[5:16]} | {rec.get('last_detail','-')[:50]} |")

    lines += ["", "## 模拟盘快照", ""]
    try:
        if os.path.exists(MIRROR_CSV):
            import pandas as pd
            df = pd.read_csv(MIRROR_CSV).sort_values("date")
            last = df.iloc[-1]
            lines.append(f"- 最近权益镜像: **{last['date']}** 总权益 ¥{last['total_equity']:,.0f} ({last['return_pct']:+.2f}%)，已累计 {len(df)} 个交易日")
        else:
            lines.append("- 权益镜像: 未生成（T1-4 待首跑）")
    except Exception as e:
        lines.append(f"- 权益镜像读取失败: {e}")

    active_ov = []
    ov_file = os.path.join(WORKSPACE, "config", "gate_overrides.json")
    if os.path.exists(ov_file):
        try:
            with open(ov_file, encoding="utf-8") as f:
                ovs = json.load(f).get("overrides", {})
            now = datetime.now()
            for cat, ov in ovs.items():
                until = datetime.fromisoformat(ov["disabled_until"])
                if now < until:
                    active_ov.append(f"{cat}（至 {ov['disabled_until'][5:16]}: {ov.get('reason','')[:30]}）")
        except Exception as e:
            print(f"⚠️ overrides 读取失败（面板开关状态缺失）: {e}")
    lines += ["", "## 门禁开关状态", ""]
    if active_ov:
        lines += [f"- ⏸️ 临时禁用: {'; '.join(active_ov)}"]
    else:
        lines.append("- ✅ 全部 15 门禁启用（无 override）")

    # 重写型写点备份保护（backup_guard 铁律）
    if os.path.exists(DASHBOARD_MD):
        import shutil
        try:
            shutil.copy2(DASHBOARD_MD, DASHBOARD_MD + ".bak")
        except Exception as e:
            print(f"⚠️ 面板备份失败（继续写入）: {e}")
    tmp = DASHBOARD_MD + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, DASHBOARD_MD)


def main():
    alerts, recoveries = run_tick()
    if alerts:
        print(f"🔴 {len(alerts)} 条告警")
        return 1
    if recoveries:
        print(f"🟢 {len(recoveries)} 条恢复通知")
    print("✅ 哨兵 tick 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
