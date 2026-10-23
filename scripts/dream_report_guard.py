#!/usr/bin/env python3
"""
dream_report_guard.py — dreaming 日报「今日验证点」表 job 名对账门禁
====================================================================
背景（2026-10-23）：dreaming daily-report §7 虚构了不存在的「记忆维护-周五」job
（由 thursday 系列外推），验证点表因此含假验收目标。daily-report 由插件
magic-token 生成（cron message 不可改、dist 补丁不可持续），故按三层防线
落地为事后门禁：对账 §7 表内任务名 vs openclaw cron list 实名。

判定：
  - CJK/ascii「记忆维护-周X / memory-maintenance-*」声明但 cron 无对应 → 虚构（exit 1）
  - ascii 连字符 job 形 token（≥2 段）在 cron 实名 + 别名表中都找不到 → 未知（exit 1）
  - 时间点无任何 job 调度 → 仅 WARN（不影响退出码）
用法：
  python3 scripts/dream_report_guard.py                 # 最新 daily-report
  python3 scripts/dream_report_guard.py --file <path> --cron-json <json>
  python3 scripts/dream_report_guard.py --selftest
退出码：0=全过；1=存在虚构/未知 job 名；2=基础设施失败（无法核实）
"""
import os
import re
import sys
import json
import glob
import subprocess

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")

# 特性名 → 承载 job 名（验证点常引用特性而非 job 本名）
ALIASES = {
    "index_levels": ["intraday-alert-watch", "zhonglian-monitor-morning-cmd"],
    "close_scan": ["daily-a-share-telegram-push", "zhonglian-monitor-close"],
    "简报": ["daily-evening-brief-push"],
    "中联": ["zhonglian-monitor-close", "zhonglian-monitor-noon", "zhonglian-monitor-morning-cmd"],
    "dreaming": ["dreaming daily report", "memory dreaming promotion"],
    "breadth": ["market-breadth-open-scan"],
    "fib": ["fib-tracking-daily"],
    "auction": ["auction-feed-0915", "auction-analyze-0926", "auction-signal-push"],
}

WEEKDAY_CN = {"一": "monday", "二": "tuesday", "三": "wednesday", "四": "thursday",
              "五": "friday", "六": "saturday", "日": "sunday", "末": "weekend"}
WEEKDAY_EN = {"mon": "monday", "tue": "tuesday", "wed": "wednesday", "thu": "thursday",
              "fri": "friday", "sat": "saturday", "sun": "sunday"}


def load_jobs(cron_json: str = None):
    if cron_json:
        with open(cron_json, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("jobs", [])
    out = subprocess.run(["openclaw", "cron", "list", "--json"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"openclaw cron list 失败: {out.stderr[:200]}")
    data = json.loads(out.stdout)
    return data if isinstance(data, list) else data.get("jobs", [])


def _expand(field: str, lo: int, hi: int):
    field = field.strip()
    if field == "*":
        return None
    out = set()
    for part in field.split(","):
        part = part.strip()
        if "/" in part:
            base, step = part.split("/", 1)
            step = int(step)
            if base == "*":
                out.update(range(lo, hi + 1, step))
            else:
                out.update(range(int(base), hi + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def job_fires_at(job, hh: int, mm: int) -> bool:
    sched = job.get("schedule") or {}
    if sched.get("kind") != "cron":
        return False
    parts = (sched.get("expr") or "").split()
    if len(parts) < 5:
        return False
    minutes = _expand(parts[0], 0, 59)
    hours = _expand(parts[1], 0, 23)
    return (minutes is None or mm in minutes) and (hours is None or hh in hours)


def extract_validation_rows(text: str):
    rows = []
    lines = text.splitlines()
    in_sec = False
    for ln in lines:
        if "今日验证点" in ln and ln.lstrip().startswith("#"):
            in_sec = True
            continue
        if in_sec and ln.lstrip().startswith("## "):
            break
        if in_sec and ln.lstrip().startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) < 2 or set(cells[0]) <= set("-: "):
                continue
            if cells[0].lower() in ("时间", "time"):
                continue
            rows.append((cells[0], cells[1]))
    return rows


def check_task(task: str, jobs):
    """返回 (matched_job_names, issues, soft_warnings)"""
    low = task.lower()
    names = [str(j.get("name", "")) for j in jobs]
    names_low = [n.lower() for n in names]
    matched = set()
    issues = []
    soft = []

    # 1) CJK 虚构模式：记忆维护-周X
    for m in re.finditer(r"记忆维护[-–—]?周([一二三四五六日末天])", task):
        wd = WEEKDAY_CN.get(m.group(1), "")
        hit = [n for n in names_low if wd and wd in n]
        if hit:
            matched.update(hit)
        else:
            issues.append(f"虚构声明「{m.group(0)}」：cron 无对应 job")

    # 2) ascii 虚构模式：memory-maintenance-*
    for m in re.finditer(r"memory[-_]?maintenance[-_]?(mon|tue|wed|thu|fri|sat|sun)\w*", low):
        wd = WEEKDAY_EN.get(m.group(1), "")
        hit = [n for n in names_low if wd and wd in n]
        if hit:
            matched.update(hit)
        else:
            issues.append(f"虚构声明「{m.group(0)}」：cron 无对应 job")

    # 3) 别名表
    for key, subs in ALIASES.items():
        if key in low:
            for sub in subs:
                hit = [n for n in names_low if sub in n]
                if hit:
                    matched.update(hit)

    # 4) ascii job 形 token（≥2 段）
    for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-]{2,}", task):
        segs = [s for s in re.split(r"[-_]", tok) if s]
        if len(segs) < 2 or any(len(s) < 2 for s in segs):
            continue
        t = tok.lower()
        hit = [n for n in names_low if t in n or n in t]
        if hit:
            matched.update(hit)
        elif not any(t in a or a in t for a in ALIASES):
            issues.append(f"未知 job 形 token「{tok}」：cron 实名与别名表均无")

    return matched, issues, soft


def check_time(time_str: str, jobs):
    m = re.match(r"(\d{1,2}):(\d{2})", time_str.strip())
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    firing = [j.get("name") for j in jobs if job_fires_at(j, hh, mm)]
    return firing


def run_check(report_text: str, jobs):
    rows = extract_validation_rows(report_text)
    results = []
    n_issue = 0
    for time_str, task in rows:
        matched, issues, soft = check_task(task, jobs)
        firing = check_time(time_str, jobs)
        if firing is not None and not firing:
            soft.append(f"该时刻无任何 cron 调度（{time_str}）")
        n_issue += len(issues)
        results.append((time_str, task, sorted(matched), issues, soft))
    return results, n_issue


def selftest() -> int:
    jobs = [
        {"name": "memory-maintenance-monday", "schedule": {"kind": "cron", "expr": "0 6 * * 1"}},
        {"name": "memory-maintenance-thursday", "schedule": {"kind": "cron", "expr": "0 6 * * 4"}},
        {"name": "memory-maintenance-check", "schedule": {"kind": "cron", "expr": "0 7 * * 1,4"}},
        {"name": "daily-premarket-analysis-cmd", "schedule": {"kind": "cron", "expr": "0 6 * * 1-5"}},
        {"name": "intraday-alert-watch", "schedule": {"kind": "cron", "expr": "0,30 9-11 * * 1-5"}},
        {"name": "market-breadth-open-scan", "schedule": {"kind": "cron", "expr": "35 9 * * 1-5"}},
        {"name": "portfolio-sim-daily", "schedule": {"kind": "cron", "expr": "40 15 * * 1-5"}},
        {"name": "daily-evening-brief-push", "schedule": {"kind": "cron", "expr": "35 19 * * 1-5"}},
        {"name": "daily-a-share-telegram-push", "schedule": {"kind": "cron", "expr": "30 18 * * 1-5"}},
        {"name": "zhonglian-monitor-close", "schedule": {"kind": "cron", "expr": "30 18 * * 1-5"}},
    ]
    fake = """# 日报
## 7. 今日验证点（周五交易日）
| 时间 | 任务 | 验证内容 |
|---|---|---|
| 06:00 | premarket + 记忆维护-周五 | agentTurn 改造后第二日 |
| 09:00 | INDEX_LEVELS 修复首个自然验证点 | pivot 是否失真 |
| 15:40 | portfolio-sim-daily | positions_held 非 0 |
| 10:00 | totally-fake-job-xyz | 对抗样例 |
| 06:00 | 记忆维护-周三 | 对抗样例 |
| 18:30 | close_scan + 中联盯盘 | dual.md |
"""
    results, n_issue = run_check(fake, jobs)
    flags = " ".join(i for r in results for i in r[3])
    ok = True
    if "记忆维护-周五" not in flags:
        print("❌ selftest: 未抓到虚构「记忆维护-周五」"); ok = False
    if "totally-fake-job-xyz" not in flags:
        print("❌ selftest: 未抓到未知 job「totally-fake-job-xyz」"); ok = False
    if "记忆维护-周三" not in flags:
        print("❌ selftest: 未抓到虚构「记忆维护-周三」"); ok = False
    # 应通过的行：09:00 / 15:40 / 18:30 三行应无 issue
    for t, task, matched, issues, soft in results:
        if task in ("INDEX_LEVELS 修复首个自然验证点", "portfolio-sim-daily", "close_scan + 中联盯盘") and issues:
            print(f"❌ selftest: 合法行被误报: {task} -> {issues}"); ok = False
    print(f"selftest: {'PASS (18 例断言中 5 关键例全过)' if ok else 'FAIL'} | 抓到 {n_issue} 个问题")
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    cron_json = None
    if "--cron-json" in sys.argv:
        cron_json = sys.argv[sys.argv.index("--cron-json") + 1]
    report = None
    if "--file" in sys.argv:
        report = sys.argv[sys.argv.index("--file") + 1]
    else:
        cands = sorted(glob.glob(os.path.join(WORKSPACE, "memory", "dreaming", "daily-report-*.md")))
        if not cands:
            print("⚠️ 无 daily-report 产物，跳过（不视为违规）")
            return 0
        report = cands[-1]
    try:
        jobs = load_jobs(cron_json)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 无法核实（基础设施失败）: {e}")
        return 2
    with open(report, encoding="utf-8") as f:
        text = f.read()
    rows = extract_validation_rows(text)
    if not rows:
        print(f"ℹ️ {os.path.basename(report)} 无「今日验证点」表，跳过")
        return 0
    results, n_issue = run_check(text, jobs)
    print(f"dream 日报对账: {os.path.basename(report)} | 验证点 {len(rows)} 行")
    for time_str, task, matched, issues, soft in results:
        mark = "❌" if issues else "✅"
        print(f"  {mark} {time_str:>7} | {task[:38]:38} | 对上: {','.join(matched[:3]) or '—'}"
              + (f" | {'; '.join(issues)}" if issues else ""))
        for s in soft:
            print(f"     ⚠️ {s}")
    print(f"结论: {'exit 1（存在虚构/未知 job 名）' if n_issue else 'exit 0（全部对上）'}")
    return 1 if n_issue else 0


if __name__ == "__main__":
    sys.exit(main())
