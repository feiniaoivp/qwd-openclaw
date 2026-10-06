#!/usr/bin/env python3
"""
Cron 空转假成功门禁 (cron_noop_guard.py)
========================================
背景（2026-10-06 立项，用户批准）：
  3950d3cc「每日收盘推送」曾长期 lastRunStatus=ok 但实际从未执行脚本
  （main+systemEvent 注入即返回，duration 仅毫秒级）。修复后全量扫描发现
  仍有约 11 个同款嫌疑 job。cron_health_check 只看 status/成功率，
  **毫秒级的 ok 在它眼里永远是健康** —— 这是监控体系的结构性盲区。

检测口径（数据源：~/.openclaw/state/openclaw.sqlite 只读直查，毫秒级）：
  1) main + systemEvent：last_duration_ms < 3000 且 lastRunStatus=ok
     → 事件注入后主会话什么都没干 = 空转假成功。
     子类型：
       SHELL-type     payload 本身是 shell 命令（`cd ... && python3 ...`）
                      → 配置错误：应为 command+isolated（如 3950d3cc 同款）
       INSTRUCTION-type payload 是给 agent 的自然语言指令但毫秒返回
                      → agent 未执行（被忽略/被吞）
  2) isolated agentTurn：ok 且 duration < 3000ms → LLM 轮次不可能这么快，可疑
  3) isolated command：ok 且 duration < 100ms → 脚本瞬间返回（大概率 no-op），可疑
  从未运行过的 job（lastRunStatus 为空）跳过，误报无意义。

棘轮模式（同 silent_except_guard 哲学）：
  基线 data/cron_noop_baseline.json 记录已知存量（历史债务仅报告），
  **基线之外新增**的嫌疑 job 才 exit 1。修复一个 job 后用
  `--update-baseline` 收缩基线（基线只降不升为健康演化）。

接入点：weekly_full_pipeline.py 步骤 0h（失败仅告警不阻断）。
退出码：0=无新增违规；1=发现基线外新增空转 job；2=数据源不可用（监控失明必须响）。
"""
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DB_PATH = os.path.expanduser("~/.openclaw/state/openclaw.sqlite")
BASELINE_PATH = os.path.join(WORKSPACE, "data", "cron_noop_baseline.json")

MAIN_SYSTEMEVENT_MS = 3000     # main+systemEvent 低于此 = 注入即返回
AGENT_TURN_MS = 3000           # isolated agentTurn LLM 轮次下限
COMMAND_MS = 100               # isolated command 脚本瞬时返回下限

# payload 首段像 shell 命令（而非给 agent 的自然语言指令）
SHELL_RE = re.compile(r"^\s*(cd\s|python3?\b|bash\b|sh\s|openclaw\b|/usr/|\./)")
SCRIPT_HINT_RE = re.compile(r"python3?\s|scripts/|analysis/|\.py\b|bash\s")


def fetch_jobs():
    """只读直查 cron_jobs（毫秒级；失败抛异常由 main 处理为退出码 2）。"""
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"sqlite 不存在: {DB_PATH}")
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    try:
        con.execute("PRAGMA busy_timeout=3000")
        rows = con.execute(
            """
            select job_id, name, payload_kind, session_target, enabled,
                   last_run_status, last_duration_ms, last_run_at_ms
            from cron_jobs
            where enabled = 1
            """
        ).fetchall()
        return [
            {
                "job_id": r[0], "name": r[1], "kind": r[2], "target": r[3],
                "enabled": r[4], "last_status": r[5], "duration_ms": r[6],
                "last_run_at_ms": r[7],
            }
            for r in rows
        ]
    finally:
        con.close()


def payload_text(job_id):
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    try:
        con.execute("PRAGMA busy_timeout=3000")
        row = con.execute(
            "select payload_message from cron_jobs where job_id = ?", (job_id,)
        ).fetchone()
        return (row[0] or "") if row else ""
    finally:
        con.close()


def classify(job):
    """返回 (verdict, subtype, reason)；verdict in {suspect, skip}"""
    if job["last_status"] != "ok":
        return "skip", "", "最近一次运行非 ok（已有显性信号，非本门禁职责）"
    d = job["duration_ms"]
    if d is None:
        return "skip", "", "从未运行/无 duration"
    key = (job["kind"], job["target"])
    if key == ("systemEvent", "main"):
        if d < MAIN_SYSTEMEVENT_MS:
            txt = payload_text(job["job_id"])
            if SHELL_RE.match(txt):
                sub = "SHELL-type"
                why = "payload 是 shell 命令但走 main+systemEvent，注入即 ok，脚本从未执行 → 应改 command+isolated"
            elif SCRIPT_HINT_RE.search(txt):
                sub = "INSTRUCTION-type(含脚本)"
                why = "payload 要求跑脚本但主会话毫秒返回，未执行"
            else:
                sub = "INSTRUCTION-type"
                why = "自然语言指令但毫秒返回，agent 未响应"
            return "suspect", sub, why
        return "skip", "", f"duration={d}ms 正常执行"
    if key == ("agentTurn", "isolated") and d < AGENT_TURN_MS:
        return "suspect", "agentTurn 瞬时返回", f"LLM 轮次仅 {d}ms，疑似未真正运行"
    if key == ("command", "isolated") and d < COMMAND_MS:
        return "suspect", "command 瞬时返回", f"command 仅 {d}ms，疑似 no-op/脚本秒退"
    return "skip", "", "正常"


def load_baseline():
    if os.path.exists(BASELINE_PATH):
        try:
            with open(BASELINE_PATH) as f:
                return set(json.load(f).get("job_ids", []))
        except Exception as e:  # 基线损坏必须可见
            print(f"⚠️ 基线读取失败（视为空基线）: {e}")
    return set()


def save_baseline(job_ids):
    os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
    tmp = BASELINE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(
            {
                "updated_at": datetime.now().isoformat(),
                "note": "cron 空转假成功已知存量（棘轮基线，修复后用 --update-baseline 收缩）",
                "job_ids": sorted(job_ids),
            },
            f, ensure_ascii=False, indent=2,
        )
    os.replace(tmp, BASELINE_PATH)


def main():
    quiet = "--quiet" in sys.argv
    as_json = "--json" in sys.argv
    update = "--update-baseline" in sys.argv

    try:
        jobs = fetch_jobs()
    except Exception as e:
        print(f"❌ [cron_noop_guard] 数据源不可用（监控失明，必须人工排查）: {type(e).__name__}: {e}")
        sys.exit(2)

    baseline = load_baseline()
    suspects = []
    for j in jobs:
        verdict, sub, why = classify(j)
        if verdict == "suspect":
            suspects.append({**j, "subtype": sub, "reason": why})

    sus_ids = {s["job_id"] for s in suspects}
    new_ones = [s for s in suspects if s["job_id"] not in baseline]
    known = [s for s in suspects if s["job_id"] in baseline]
    fixed = baseline - sus_ids  # 基线中已消失 = 已修复（或被删除/停用）

    if as_json:
        print(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "total_enabled_jobs": len(jobs),
            "suspects": [{k: s[k] for k in ("job_id", "name", "kind", "target", "duration_ms", "subtype")} for s in suspects],
            "new_violations": [s["name"] for s in new_ones],
            "known_debt": [s["name"] for s in known],
            "fixed_since_baseline": sorted(fixed),
        }, ensure_ascii=False, indent=2))
    elif not quiet:
        print(f"=== Cron 空转假成功门禁 | enabled jobs={len(jobs)} | 基线={len(baseline)} ===")
        for s in new_ones:
            last = datetime.fromtimestamp(s["last_run_at_ms"] / 1000).strftime("%m-%d %H:%M") if s["last_run_at_ms"] else "?"
            print(f"  ❌ 新增: {s['name']} [{s['kind']}/{s['target']}] duration={s['duration_ms']}ms last={last} — {s['subtype']}: {s['reason']}")
        for s in known:
            print(f"  ⚠️ 存量: {s['name']} [{s['kind']}/{s['target']}] duration={s['duration_ms']}ms — {s['subtype']}")
        if fixed:
            print(f"  ✅ 基线中已恢复/移除 {len(fixed)} 个: {', '.join(sorted(fixed))}（请确认修复后跑 --update-baseline 收缩基线）")
        if not suspects and not fixed:
            print("  ✅ 无空转嫌疑 job")

    if update:
        save_baseline(sus_ids)
        print(f"📝 基线已更新: {len(sus_ids)} 个存量嫌疑（新增违规判定以此为基线）")
        # 更新模式本身不判违规（否则修复当场误报）
        sys.exit(0)

    if new_ones:
        print(f"🚨 基线外新增 {len(new_ones)} 个空转假成功 job（status=ok 但毫秒返回，脚本/指令未执行）")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
