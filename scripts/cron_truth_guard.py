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
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")

# 产物映射表：Job ID 精确映射 -> 产物路径模板/类型
# 类型: 'file' (日期文件), 'static' (固定文件)
# 使用 Job ID 而非关键字，避免关键字撞车误报（如 "close_scan" 可能命中多个 job）
TRUTH_MAP = {
    # 交易日链（需先通过 is_trading_day 校验）
    "3950d3cc-0230-4241-a8ff-c0fdea3b33f2": ("file", "analysis/daily/{{date}}_dual.md"),       # daily-a-share-telegram-push
    "4ecdf11f-7b93-4d53-83f2-4536c007e8a5": ("file", "data/runtime/auction/{{date}}_9_25.json"),       # auction-analyze-0926
    "cc1e9a93-1c47-4483-932a-2bdea2695be5": ("file", "data/runtime/auction/signal_push_{{date}}.json"), # auction-signal-push
    "ed92007e-67ab-4113-9059-008151f417e1": ("static", "data/market_breadth_output.json"),      # market-breadth-open-scan
    "c57f99fb-b3bc-4957-8c4b-c123dcec3ab3": ("static", "data/runtime/intraday_alert_output.json"),      # intraday-alert-watch
    "3ca4931a-b31f-4dfe-9a10-c70159c8096c": ("static", "data/runtime/intraday_alert_output.json"),      # intraday-alert-watch-pm (same output file)
    "b954c808-fde1-46de-b2b4-8f83bb59d07d": ("static", "data/runtime/portfolio_equity.csv"),           # portfolio-sim-daily
    "fbba023a-8caf-4194-abf8-7fb097599c61": ("file", "analysis/daily/{{date}}_dual.md"),       # evening-dual-refresh-and-shadow
    "bd9843f1-2a6f-44e6-8bf2-8f3aa5b9bca3": ("file", "analysis/backtest/{{date}}.md"),   # quant-backtest-daily
    
    # 非交易日/全天候链（无需 is_trading_day 校验）
    "a07898c0-c659-45d0-823b-93e744f9a417": ("file", "memory/dreaming/daily-report-{{date}}.md"),  # Memory Dreaming Promotion
    "a8c18aed-8916-4afa-82e7-2b6501dcbfcf": ("file", "memory/dreaming/daily-report-{{date}}.md"),  # Dreaming Daily Report
    "2fe5c58d-adaa-4a3e-b262-e1ae37e51b69": ("static", "data/runtime/premarket_outlook_output.json"),     # premarket-outlook-daily
    "40731f98-d532-4c18-b33f-99f386f7f8be": ("file", "data/news/daily_{{date}}.md"),             # news-monitor-morning
    "7e0dc37b-b79a-4eee-a845-ba9cf79ccb26": ("static", "data/news/knowledge_base.md"),           # news-monitor-afternoon
    "537eb1ff-9268-4bbb-a0fa-3c58c249cf2c": ("file", "data/news/daily_{{date}}.md"),               # daily-news-reading-push
    # "181b0304-070e-46a8-8ddc-7603a9950050": ("static", "memory/investment_philosophy.md"),        # obsidian-notes-to-investment-philosophy (LLM agentTurn, 无确定性产物文件)
    "c768b322-16a6-4170-be7f-417322013454": ("file", "analysis/daily/{{date}}_brief.md"), # daily-evening-brief-push
    "117facdb-c8e6-40de-82b8-1cc53b309be2": ("static", "data/lowopen_shadow_ledger.json"),       # lowopen-shadow-capture
    "7d1b9562-9eaf-47d9-b527-b90f07d4e66d": ("static", "data/fib_tracking_state.json"),           # fib-tracking-daily
    # "a7456f8b-3348-4e0b-9de5-4ae66a0448ad": daily-supervision-review (信号审计+挑战复盘，产物为 Telegram 推送，signal_audit.md 由早盘扫描生成，非本 job 产出)
    
    # 周/月链（按 schedule 判断是否交易日相关，暂全放行）
    # "c4cb94a5-1b61-43cc-a870-0a96d24f6a6d": weekly-backtest-pipeline (agentTurn LLM 任务，无确定性产物文件)
    "864cfb26-46c8-43da-af3a-b7512109f487": ("file", "memory/{{date}}-weekly-review.md"),       # weekly-review-trading (实际输出: memory/2026-10-31-weekly-review.md)
    "6cd3fcb4-9052-4059-9b2d-33964dd06bec": ("file", "analysis/sector_review/weekly_{{friday_date}}.md"), # sector-overseas-weekly-review (产物以周五日期命名，周六运行)
    # "ecd94d5d-ce4a-4534-836b-1764108508d9": power-overseas-weekly (纯文本stdout推送，无持久化文件产物，跳过审计)
    "44616767-f4d0-4c1a-afd3-39a6f2f19777": ("file", "analysis/daily/{{date}}_portfolio_weekly.md"), # portfolio-weekly-report
    # "14e04f9b-fc75-4468-b2a3-c0f373a24466": power-overseas-backtest-monthly (产物命名为 YYYYMMDD_HHMMSS，时间戳不固定，跳过精确审计)
    "e3604f95-09d4-4dd7-ab26-bd50b7f80480": ("static", "data/corr_clusters.json"),                # corr-clusters-monthly-refresh
    "824d1039-a6e0-445d-a6ea-ce20a4d38b51": ("file", "analysis/monthly/param_exec_reconciliation_{{month}}.md"),  # param-exec-reconciliation-monthly (产物为月度md，2026-11-08 修正：旧期望 data/param_exec_reconcile.json 从非脚本产物路径)
    # "53fb14c9-2ea3-4837-a909-ccee477c9161": sector-overseas-tender-monitor (事件驱动监控，仅有事件时推送，无持久化产物文件)
    "1410e346-127e-4c9b-b593-f0dcb60df9e6": ("file", "data/commodity_fx_{{date}}.json"),             # sector-overseas-commodity-fx
    # "7fac42a1-e98f-4316-a342-a32a3d4a541a": power-overseas-eod (Telegram 推送脚本，无持久化文件产物)
    "96ce82c1-8597-489d-898a-3408e97d9085": ("file", "data/context/market_context_{{prev_date}}.json"),       # market-context-generate (产物为前一日日期)
    "5a7b8aa2-f40d-49ad-b866-86f463396100": ("static", "data/market_activity_legu_history.json"),   # legu-activity-daily-capture
    
    # 维护/工具类（无固定产物，跳过）
    # ca08c2b7 error-pattern-injector
    # d77fad30 blogwatcher-daily-scan
    # e8e3d01c skill-version-watcher
    # b5943dcf cron-health-check
    # c50849c8 daily-skill-auto-update
    # 01b13330 memory-maintenance-monday
    # 8f676e04 memory-maintenance-check
    # 9c483c25 memory-maintenance-thursday
    # 3aafd4ce test-pipeline-integration
    # 269ab910 test-arbitration-logic
    # 87c6b7f9 daily-premarket-analysis-cmd
    # 917a20ef news-morning-reading
    # c7a8a955 zhonglian-monitor-morning-cmd
    # f046d135 power-overseas-intraday
    # f4920382 auction-feed-0915
    # 8da42083 zhonglian-monitor-noon
    # ff8df2a8 zhonglian-monitor-close
    
    # 一次性 job（不在此映射，动态处理）
    # a141a848 q3-disclosure-monday-snapshot
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
    jid = job.get("id", "")
    name = (job.get("name") or job.get("displayName") or "unknown").lower()
    
    # 只审计 isolated/command 模式
    if job.get("sessionTarget") != "isolated":
        return None

    # 精确 Job ID 映射
    template_info = TRUTH_MAP.get(jid)
    
    if not template_info:
        return None  # 无映射，不审计
    
    # 检查调度状态
    state = job.get("state") or {}
    last_status = state.get("lastRunStatus")
    last_run_at = state.get("lastRunAtMs")
    
    if not last_run_at:
        return None  # 从未运行过

    # 只有在调度报 OK 的时候才检查是否有 "Fake OK"
    if last_status != "ok":
        return None

    # 交易日相关 job：非交易日不审计（避免误报）
    trading_day_jobs = {
        "3950d3cc-0230-4241-a8ff-c0fdea3b33f2",  # daily-a-share-telegram-push
        "4ecdf11f-7b93-4d53-83f2-4536c007e8a5",  # auction-analyze-0926
        "cc1e9a93-1c47-4483-932a-2bdea2695be5",  # auction-signal-push
        "ed92007e-67ab-4113-9059-008151f417e1",  # market-breadth-open-scan
        "c57f99fb-b3bc-4957-8c4b-c123dcec3ab3",  # intraday-alert-watch
        "3ca4931a-b31f-4dfe-9a10-c70159c8096c",  # intraday-alert-watch-pm
        "b954c808-fde1-46de-b2b4-8f83bb59d07d",  # portfolio-sim-daily
        "fbba023a-8caf-4194-abf8-7fb097599c61",  # evening-dual-refresh-and-shadow
        "bd9843f1-2a6f-44e6-8bf2-8f3aa5b9bca3",  # quant-backtest-daily
    }
    
    if jid in trading_day_jobs:
        try:
            # 使用 SSOT is_trading_day
            import sys
            sys.path.insert(0, WORKSPACE)
            sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))
            from analysis.service import is_trading_day
            dt_local = datetime.fromtimestamp(last_run_at / 1000)
            if not is_trading_day(dt_local.strftime("%Y-%m-%d")):
                return None  # 非交易日，跳过审计
        except Exception as e:
            print(f"⚠️ is_trading_day 校验失败（不阻断）: {e}", file=sys.stderr)
            # 校验失败时不阻断，按原逻辑继续
    
    if not last_run_at:
        return None  # 从未运行过

    # 只有在调度报 OK 的时候才检查是否有 "Fake OK"
    if last_status != "ok":
        return None

    mode, template = template_info
    
    # 解析日期
    # 本地时区取日期：本地 08:00 前运行的 job 若用 UTC，{{date}} 会解析到前一天
    dt = datetime.fromtimestamp(last_run_at / 1000)
    date_str = dt.strftime("%Y-%m-%d")
    
    # 处理 {{prev_date}}：前一交易日（用于 market-context-generate 等产物为前一日日期的 job）
    prev_date_str = None
    if "{{prev_date}}" in template:
        try:
            import sys
            sys.path.insert(0, WORKSPACE)
            sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))
            from analysis.service import is_trading_day
            from datetime import timedelta
            
            # 获取前一交易日
            d = dt - timedelta(days=1)
            for _ in range(10):
                if is_trading_day(d.strftime("%Y-%m-%d")):
                    prev_date_str = d.strftime("%Y-%m-%d")
                    break
                d -= timedelta(days=1)
        except Exception as e:
            print(f"⚠️ prev_date 计算失败（不阻断）: {e}", file=sys.stderr)
            # 回退到普通日期
            prev_date_str = date_str
    
    # 处理 {{friday_date}}：本周周五日期（用于周六运行、产物以周五命名的 job）
    friday_date_str = None
    if "{{friday_date}}" in template:
        try:
            import sys
            sys.path.insert(0, WORKSPACE)
            sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))
            from analysis.service import is_trading_day, get_trading_days
            from datetime import timedelta
            
            # 找到本周周五（如果是周六运行，找前一天；如果是周日运行，找前两天）
            d = dt
            for _ in range(10):
                if d.weekday() == 4 and is_trading_day(d.strftime("%Y-%m-%d")):  # 4 = Friday
                    friday_date_str = d.strftime("%Y-%m-%d")
                    break
                d -= timedelta(days=1)
            # 回退：如果 dt 是周六，周五是昨天
            if friday_date_str is None:
                d = dt - timedelta(days=1)
                if d.weekday() == 4 and is_trading_day(d.strftime("%Y-%m-%d")):
                    friday_date_str = d.strftime("%Y-%m-%d")
        except Exception as e:
            print(f"⚠️ friday_date 计算失败（不阻断）: {e}", file=sys.stderr)
            friday_date_str = date_str
    
    # 处理 {{date_short}}：YYYYMMDD_HHMMSS 格式（用于 power-overseas-backtest-monthly 等带时间戳命名的产物）
    date_short_str = None
    if "{{date_short}}" in template:
        # 取运行时间的 YYYYMMDD_HHMMSS 格式
        date_short_str = dt.strftime("%Y%m%d_%H%M%S")
    
    # 处理 {{month}}：YYYY-MM 格式（用于 monthly job 的月度命名产物，如 param_exec_reconciliation_2026-11.md）
    month_str = None
    if "{{month}}" in template:
        month_str = dt.strftime("%Y-%m")
    
    if mode == "file":
        actual_path = os.path.join(ws, template.replace("{{date}}", date_str).replace("{{prev_date}}", prev_date_str or date_str).replace("{{friday_date}}", friday_date_str or date_str).replace("{{date_short}}", date_short_str or date_str).replace("{{month}}", month_str or date_str[:7]))
    else:  # static
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
      C8 交易日 job 在非交易日 → 不审计（新增：is_trading_day SSOT 校验）
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
            _bak(path)
            with open(path, "w") as f:
                f.write(content)
            os.utime(path, (mtime_ms / 1000, mtime_ms / 1000))
            return path

        def job(jid, status="ok", run_at=run_ms):
            return {
                "id": jid,
                "name": jid,
                "sessionTarget": "isolated",
                "state": {"lastRunStatus": status, "lastRunAtMs": run_at},
            }

        # C1 时区陷阱：{{date}} 必须解析为本地日期 2026-01-15（UTC 解析会落到 01-14 → 缺文件误报）
        # 使用 trading-day job ID：3950d3cc-0230-4241-a8ff-c0fdea3b33f2
        mkfile("analysis/daily/2026-01-15_dual.md", run_ms)
        r = verify_truth(job("3950d3cc-0230-4241-a8ff-c0fdea3b33f2"), workspace=td)
        c1 = ("C1 时区陷阱(本地00:30→{{date}}须取本地日期)→放行", False, r)

        # C2 追加型：mtime 晚 3h 是活性证据，不是 Fake OK（防 +3600s 上限窗口回潮）
        mkfile("data/news/knowledge_base.md", run_ms + 3 * 3600 * 1000)
        r = verify_truth(job("7e0dc37b-b79a-4eee-a845-ba9cf79ccb26"), workspace=td)
        c2 = ("C2 追加型产物晚于运行3h→放行(防上限回潮)", False, r)

        # C3 陈旧产物：早 1h（超出 -10min 容差）→ 必须报 Fake OK
        mkfile("data/runtime/auction/2026-01-15_9_25.json", run_ms - 3600 * 1000, "{}")
        r = verify_truth(job("4ecdf11f-7b93-4d53-83f2-4536c007e8a5"), workspace=td)
        c3 = ("C3 产物早于运行1h→Fake OK", True, r)

        # C4 产物缺失 → Fake OK（该 job 的静态产物尚未创建）
        r = verify_truth(job("ed92007e-67ab-4113-9059-008151f417e1"), workspace=td)
        c4 = ("C4 产物缺失→Fake OK", True, r)

        # C5 产物为空 → Fake OK
        mkfile("data/market_breadth_output.json", run_ms, content="")
        r = verify_truth(job("ed92007e-67ab-4113-9059-008151f417e1"), workspace=td)
        c5 = ("C5 产物为空→Fake OK", True, r)

        # C6/C7：非 ok 状态 / 从未运行 → 不在 Fake OK 审计范围
        r = verify_truth(job("3950d3cc-0230-4241-a8ff-c0fdea3b33f2", status="error"), workspace=td)
        c6 = ("C6 lastStatus=error→不审计", False, r)
        r = verify_truth(job("3950d3cc-0230-4241-a8ff-c0fdea3b33f2", run_at=None), workspace=td)
        c7 = ("C7 无 lastRunAtMs→不审计", False, r)

        # C8：交易日 job 在非交易日运行 → 不审计
        # 2026-01-17 是周六（非交易日）
        non_trade_dt = datetime(2026, 1, 17, 15, 30, 0)
        non_trade_ms = int(non_trade_dt.timestamp() * 1000)
        mkfile("analysis/daily/2026-01-17_dual.md", non_trade_ms)
        r = verify_truth(job("3950d3cc-0230-4241-a8ff-c0fdea3b33f2", run_at=non_trade_ms), workspace=td)
        c8 = ("C8 交易日job在非交易日→不审计(is_trading_day)", False, r)

        cases = [c1, c2, c3, c4, c5, c6, c7, c8]
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
        print("\n✅ cron_truth_guard selftest 8/8 通过")
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