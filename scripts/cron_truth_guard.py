#!/usr/bin/env python3
"""命令型 Cron 真实性门禁 (cron_truth_guard.py)

缘起（2026-09-29 / 2026-10-06 / 2026-10-22 第六轮深究）
--------------------------------------------------------
问题：`cron list` 显示 `lastStatus=ok` 并不代表任务真实执行成功。
如果脚本内部使用了 `try...except: pass` 或 `sys.exit(0)` 但未产出预期文件，
会导致「空转假成功 (Fake OK)」——调度层认为 OK，但业务产物缺失。

规约：命令型 cron 的健康判定必须看产物 mtime/内容，不采信 lastStatus/lastDurationMs。

本门禁逻辑：
1. 遍历所有 isolated/command 模式的 job。
2. 根据映射表寻找其应产出的 `expected_output_path`。
3. 校验：产物必须存在 && mtime 落在 lastRunAtMs 之后 (±30min) && 文件非空。
4. 判定：若 lastStatus == ok 但产物校验失败 -> 报红 (Fake OK)。

退出码：0 = 无假成功；1 = 发现静默失效。
"""
import argparse
import json
import os
import sys
import subprocess
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")

# 产物映射表：Job 名称关键字 -> 产物路径模板/类型
# 类型: 'file' (日期文件), 'static' (固定文件)
TRUTH_MAP = {
    "close_scan": ("file", "analysis/daily/{{date}}_dual.md"),
    "dual-refresh": ("file", "analysis/daily/{{date}}_dual.md"),
    "adaptive": ("file", "analysis/daily/{{date}}_adaptive.md"),
    "premarket": ("static", "data/premarket_outlook_output.json"),
    "auction": ("file", "data/auction/{{date}}_9_25.json"),
    "market-breadth": ("static", "data/market_breadth_output.json"),
    "portfolio-sim": ("static", "data/portfolio_equity.csv"),
    "strategy_map": ("static", "data/adaptive_strategy_map.json"),
    "news-monitor": ("static", "data/news/knowledge_base.md"),
}

def get_cron_jobs():
    try:
        out = subprocess.run(["openclaw", "cron", "list", "--json"], capture_output=True, text=True).stdout
        data = json.loads(out)
        return data.get("jobs", data) if isinstance(data, dict) else data
    except Exception as e:
        print(f"❌ 无法获取 cron 列表: {e}")
        sys.exit(1)

def verify_truth(job, workspace=None):
    ws = workspace or WORKSPACE
    jid = job.get("id")
    name = (job.get("name") or job.get("displayName") or "unknown").lower()
    
    # 只审计 isolated/command 模式
    if job.get("sessionTarget") != "isolated":
        return None

    # 寻找映射
    template_info = None
    for key, info in TRUTH_MAP.items():
        if key in name:
            template_info = info
            break
    
    if not template_info:
        return None # 无映射，不审计

    mode, template = template_info

    # 检查调度状态
    state = job.get("state") or {}
    last_status = state.get("lastRunStatus")
    last_run_at = state.get("lastRunAtMs")
    
    if not last_run_at:
        return None # 从未运行过

    # 只有在调度报 OK 的时候才检查是否有 "Fake OK"
    if last_status != "ok":
        return None

    # 解析日期
    # 本地时区取日期：本地 08:00 前运行的 job 若用 UTC，{{date}} 会解析到前一天
    dt = datetime.fromtimestamp(last_run_at / 1000)
    date_str = dt.strftime("%Y-%m-%d")
    
    if mode == "file":
        actual_path = os.path.join(ws, template.replace("{{date}}", date_str))
    else: # static
        actual_path = os.path.join(ws, template)
    
    # 物理校验
    if not os.path.exists(actual_path):
        return f"🔴 [Fake OK] 调度报 ok 但产物缺失: {actual_path}"
    
    mtime = os.path.getmtime(actual_path)
    run_time = last_run_at / 1000
    
    # 容差升级：
    # 对于某些脚本，产物可能是由前序步骤生成，或在调度记录 run_time 之前已落盘
    # 只要 mtime 在 run_time 的合理窗口内（例如 ±10分钟），即视为有效。
    # 只设下限：判 fake 只看「产物是否早于运行」（真信号）。
    # 不设上限——静态共享/追加型产物（knowledge_base.md、premarket 两阶段终稿）
    # 会被后续 job 合法地 touch，mtime 晚于运行是活性证据，不是假成功。
    if mtime < run_time - 600:
        return f"🔴 [Fake OK] 产物时间戳 {datetime.fromtimestamp(mtime)} 早于运行时间 {dt:%Y-%m-%d %H:%M} 超过 10 分钟"
    
    if os.path.getsize(actual_path) == 0:
        return f"🔴 [Fake OK] 产物文件为空: {actual_path}"
    
    return None

def selftest():
    """对抗自测（2026-10-23 补齐，dreaming 报告建议③）。

    直接构造临时文件 + job dict 驱动 verify_truth，不依赖真实舰队：
      C1 时区陷阱：本地 00:30 运行（UTC 已是前一天），产物按本地日期落盘 → 放行
      C2 追加型产物：mtime 晚于运行 3h（后续 job 合法 touch）→ 放行（防上限窗口回潮）
      C3 陈旧产物：mtime 早于运行 1h（超 -10min 容差）→ Fake OK
      C4 产物缺失 → Fake OK
      C5 产物为空 → Fake OK
      C6 lastStatus != ok → 不审计
      C7 从未运行（无 lastRunAtMs）→ 不审计
    """
    import tempfile
    from datetime import timezone

    with tempfile.TemporaryDirectory() as td:
        run_dt = datetime(2026, 1, 15, 0, 30, 0)  # 本地 CST；UTC=01-14 16:30，日期必不同
        run_ms = int(run_dt.timestamp() * 1000)
        # 自测前提：本机必须是 UTC+8，否则 C1 的时区陷阱不再成立（测试会失去对抗性）
        utc_date = datetime.fromtimestamp(run_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        if utc_date != "2026-01-14":
            print(f"❌ selftest 前提失效：本机时区非 UTC+8 (UTC date={utc_date})，C1 陷阱不成立")
            return 1

        def mkfile(rel, mtime_ms, content="report-body"):
            path = os.path.join(td, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            os.utime(path, (mtime_ms / 1000, mtime_ms / 1000))
            return path

        def job(name, status="ok", run_at=run_ms):
            return {
                "id": "selftest-0000",
                "name": name,
                "sessionTarget": "isolated",
                "state": {"lastRunStatus": status, "lastRunAtMs": run_at},
            }

        # C1 时区陷阱：{{date}} 必须解析为本地日期 2026-01-15（UTC 解析会落到 01-14 → 缺文件误报）
        mkfile("analysis/daily/2026-01-15_dual.md", run_ms)
        r = verify_truth(job("close_scan"), workspace=td)
        c1 = ("C1 时区陷阱(本地00:30→{{date}}须取本地日期)→放行", False, r)

        # C2 追加型：mtime 晚 3h 是活性证据，不是 Fake OK（防 +3600s 上限窗口回潮）
        mkfile("data/news/knowledge_base.md", run_ms + 3 * 3600 * 1000)
        r = verify_truth(job("news-monitor-morning"), workspace=td)
        c2 = ("C2 追加型产物晚于运行3h→放行(防上限回潮)", False, r)

        # C3 陈旧产物：早 1h（超出 -10min 容差）→ 必须报 Fake OK
        mkfile("data/auction/2026-01-15_9_25.json", run_ms - 3600 * 1000, "{}")
        r = verify_truth(job("auction-feed"), workspace=td)
        c3 = ("C3 产物早于运行1h→Fake OK", True, r)

        # C4 产物缺失 → Fake OK（该 job 的静态产物尚未创建）
        r = verify_truth(job("market-breadth-open-scan"), workspace=td)
        c4 = ("C4 产物缺失→Fake OK", True, r)

        # C5 产物为空 → Fake OK
        mkfile("data/market_breadth_output.json", run_ms, content="")
        r = verify_truth(job("market-breadth-open-scan"), workspace=td)
        c5 = ("C5 产物为空→Fake OK", True, r)

        # C6/C7：非 ok 状态 / 从未运行 → 不在 Fake OK 审计范围
        r = verify_truth(job("close_scan", status="error"), workspace=td)
        c6 = ("C6 lastStatus=error→不审计", False, r)
        r = verify_truth(job("close_scan", run_at=None), workspace=td)
        c7 = ("C7 无 lastRunAtMs→不审计", False, r)

        cases = [c1, c2, c3, c4, c5, c6, c7]
        failed = []
        for desc, expect_violation, r in cases:
            got = bool(r)
            ok = (got == expect_violation)
            print(f"  {'✅' if ok else '❌'} {desc} -> {'violation' if got else 'pass'}")
            if not ok:
                failed.append((desc, r))
        if failed:
            print("\n❌ cron_truth_guard selftest 失败")
            return 1
        print("\n✅ cron_truth_guard selftest 7/7 通过")
        return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest: return selftest()

    jobs = get_cron_jobs()
    violations = []
    
    print(f"审计 {len(jobs)} 个 Cron 任务真实性...")
    for j in jobs:
        res = verify_truth(j)
        if res:
            violations.append(f"{j.get('id', 'unknown')[:8]} | {j.get('name', 'unknown')} -> {res}")

    if violations:
        print("\n发现静默失效 (Fake OK) 任务：")
        for v in violations:
            print(f"  {v}")
        print("\n❌ 门禁失败 (exit 1)")
        return 1
    
    print("\n✅ 所有调度 OK 任务均有真实产物支撑 (exit 0)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
