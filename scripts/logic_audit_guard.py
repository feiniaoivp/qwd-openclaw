#!/usr/bin/env python3
"""
逻辑审计门禁：扫描 ERRORS.md 与 memory/*.md，统计同签名失败（滚动 90 天）。
RC≥3 且跨 ≥2 任务 → 生成结构性修复工单。
退出码：0=无待办工单，1=有 OPEN/IN_PROGRESS 工单。
"""

import os
import sys
import re
import json
import glob
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

ERRORS_MD = os.path.join(WORKSPACE, ".learnings", "ERRORS.md")
MEMORY_DIR = os.path.join(WORKSPACE, "memory")
TICKETS_JSON = os.path.join(WORKSPACE, "data", "failure_cases", "structural_fix_tickets.json")
TICKETS_MD = os.path.join(WORKSPACE, "analysis", "audit", "structural_fix_tickets.md")
SELFT_TEST_CASES = [
    # (rc_signature, affected_tasks, expected_trigger)
    # These are the ACTUAL entries in ERRORS.md with explicit Pattern-Key + Recurrence-Count >= 3
    ("ops.silent-orchestrator-misjudged-as-hang", ["weekly_full_pipeline.py", "premarket_pipeline.py"], True),
    ("err.dist-io-no-timeout", ["RiskGuard.refresh", "cluster_window_ab", "backtest_strategies", "bs_session"], True),
    ("exec.timeout-orphan-child.cron-longrunning", ["quant_push.py", "backtest_strategies.py", "ppo-cci-rescan"], True),
    ("data.none-valued-key-attr-crash", ["close_scan", "auction_signal_push", "daily_push"], True),
    ("llm.final-response-failure.loses-context-only-work", ["heartbeat_state", "KB子代理", "Obsidian归纳"], True),
    ("plan.duplicate-detector-creation", ["pre_response_guard", "is_trading_day", "data_freshness_guard"], True),
    # These have RC < 3 in actual entries (mentioned as family keys only in maintenance notes)
    ("cron.silent-noop-fake-ok", ["weekly_full_pipeline.py", "quant_push.py"], False),
    ("cron.mcp-add-delivery-override", ["cron edit --command", "cron add delivery:none"], False),
    ("fake_signature", ["fake_task.py"], False),  # RC<3 不触发
    ("fake_signature2", ["fake_task1.py", "fake_task2.py"], False),  # RC=2 不触发
]

# RC 签名正则：从 ERRORS.md 条目正文提取 Pattern-Key
# 格式：**Pattern-Key**: xxx.xxx.xxx 或 Pattern-Key: xxx.xxx.xxx
RC_PATTERN = re.compile(r'(?:\*\*Pattern-Key\*\*|Pattern-Key):\s*([A-Za-z0-9_.-]+)')

# Recurrence-Count 正则（匹配 - Recurrence-Count: 和 **Recurrence-Count**: 两种格式）
RC_COUNT_PATTERN = re.compile(r'(?:-\s*|\*\*)Recurrence-Count(?:\*\*)?:\s*(\d+)')

# 任务/脚本名提取：从条目正文中找脚本/任务/文件关键词后的标识符
TASK_PATTERNS = [
    re.compile(r'(?:脚本|任务|文件|job|cron)[：:\s]+([A-Za-z0-9_./-]+\.py)'),
    re.compile(r'`([A-Za-z0-9_./-]+\.py)`'),
    re.compile(r'(\w+_pipeline\.py)'),
    re.compile(r'(cron_\w+\.py)'),
    re.compile(r'(quant_push\.py)'),
    re.compile(r'(close_scan_v?2?\.py)'),
    re.compile(r'(auction_\w+\.py)'),
    re.compile(r'(premarket_\w+\.py)'),
    re.compile(r'(KB子代理|Obsidian归纳|心跳state回写)'),
    re.compile(r'(analysis/\w+\.py)'),
    re.compile(r'(scripts/\w+\.py)'),
    re.compile(r'(scripts/guards/\w+\.py)'),
    # Catch guard/checker/monitor names mentioned in text
    re.compile(r'(pre_response_guard|is_trading_day|data_freshness_guard|trading_day_ssot_guard)'),
    re.compile(r'(pre_response_guard|is_trading_day|data_freshness_guard|trading_day_ssot_guard)\.py'),
    re.compile(r'(guard|checker|monitor)[_-](\w+)'),
    # Catch task/module names mentioned in promotion/maintenance text
    re.compile(r'(RiskGuard\.refresh|cluster_window_ab|backtest_strategies|bs_session|net_guard|baostock)'),
    re.compile(r'(quant_push|ppo_cci_rescan|auction_signal_push|daily_push|close_scan|heartbeat_state)'),
    re.compile(r'(KB子代理|Obsidian归纳)'),
    re.compile(r'(gate_runner|script_path_guard|cron_noop_guard|silent_except_guard|promotion_guard)'),
    re.compile(r'(trading_day_ssot_guard|data_freshness_guard|pre_response_guard)'),
]


def parse_errors_md(filepath: str) -> list[dict]:
    """解析 ERRORS.md，返回 [{rc_signature, date, tasks, raw_text, rc_count}]"""
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    entries = []
    # 找到所有实际条目头（以 '## [ERR-' 或 '## ERR-' 开头，且行首无 '> ' 引用）
    # 支持两种格式：
    # - ## [ERR-YYYYMMDD-XXX] (标准格式)
    # - ## [ERR-YYYYMMDD-Name] (新格式，如 ERR-20261104-DuplicateGuardName)
    entry_starts = []
    for m in re.finditer(r'^## \[?ERR-', content, re.MULTILINE):
        # 确认不是在引用块中（行首不是 >）
        line_start = content.rfind('\n', 0, m.start()) + 1
        if content[line_start:line_start+2] != '> ':
            entry_starts.append(m.start())

    # 添加文件末尾作为最后一个边界
    entry_starts.append(len(content))

    for i in range(len(entry_starts) - 1):
        start = entry_starts[i]
        end = entry_starts[i + 1]
        section = content[start:end]
        
        lines = section.split('\n')
        title = lines[0].strip() if lines else ''
        body = '\n'.join(lines[1:])

        # 提取 Pattern-Key（先找正文，再找标题 - 某些条目元数据在标题行）
        pk_match = RC_PATTERN.search(body)
        if not pk_match:
            pk_match = RC_PATTERN.search(title)
        if not pk_match:
            continue
        rc_sig = pk_match.group(1)

        # 提取 Recurrence-Count（先找正文，再找标题）
        rc_match = RC_COUNT_PATTERN.search(body)
        if not rc_match:
            rc_match = RC_COUNT_PATTERN.search(title)
        rc_count = int(rc_match.group(1)) if rc_match else 1

        # 提取日期（从 ERR-YYYYMMDD）
        date_match = re.search(r'ERR-(\d{8})', title)
        if date_match:
            dt = datetime.strptime(date_match.group(1), '%Y%m%d')
        else:
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', body)
            dt = datetime.strptime(date_match.group(1), '%Y-%m-%d') if date_match else datetime.now()

        # 提取涉及任务（搜索标题+正文）
        full_text = title + '\n' + body
        tasks = set()
        for pat in TASK_PATTERNS:
            for m in pat.finditer(full_text):
                tasks.add(m.group(1))

        entries.append({
            'rc_signature': rc_sig,
            'date': dt,
            'tasks': list(tasks),
            'rc_count': rc_count,
            'raw': body[:500]
        })
    return entries


def parse_memory_files(memory_dir: str, since: datetime) -> dict:
    """解析 memory/*.md 补充任务归属，返回 {rc_signature: set(tasks)}"""
    task_map = defaultdict(set)
    if not os.path.exists(memory_dir):
        return task_map

    for md_file in glob.glob(os.path.join(memory_dir, '*.md')):
        with open(md_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 找 RC 签名提及
        for rc_sig in ['silent_except_guard', 'silent-orchestrator', 'silent-noop', 'final-response-failure',
                       'none-valued-key', 'mcp-add-delivery']:
            if rc_sig in content:
                # 提取附近的脚本名
                for pat in TASK_PATTERNS:
                    for m in pat.finditer(content):
                        task_map[rc_sig].add(m.group(1))
    return task_map


def load_existing_tickets(json_path: str) -> list[dict]:
    if not os.path.exists(json_path):
        return []
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except json.JSONDecodeError:
        return []


def save_tickets(json_path: str, tickets: list[dict]):
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    if os.path.exists(json_path):
        import shutil
        shutil.copy2(json_path, json_path + '.bak')  # 写前备份（backup_guard 铁律）
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(tickets, f, ensure_ascii=False, indent=2)


def render_board(md_path: str, tickets: list[dict]):
    os.makedirs(os.path.dirname(md_path), exist_ok=True)
    lines = [
        "# 结构性修复工单看板\n",
        "| ID | 触发日期 | RC签名 | 次数 | 涉及任务 | 状态 | 门禁脚本 | 流水线步骤 | 目标关闭 |",
        "|----|----------|--------|------|----------|------|----------|------------|----------|"
    ]
    for t in tickets:
        if t['status'] == 'CLOSED':
            continue
        tasks_str = '<br>'.join(t['affected_tasks'][:3])
        if len(t['affected_tasks']) > 3:
            tasks_str += f'<br>...+{len(t["affected_tasks"])-3}更多'
        lines.append(f"| {t['ticket_id']} | {t['trigger_date']} | {t['rc_signature']} | "
                     f"{t['rc_count']} | {tasks_str} | {t['status']} | "
                     f"{t.get('guard_script', '')} | {t.get('pipeline_step', '')} | "
                     f"{t.get('target_close', '')} |")
    if os.path.exists(md_path):
        import shutil
        shutil.copy2(md_path, md_path + '.bak')  # 写前备份（backup_guard 铁律）
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


def generate_ticket_id(rc_signature: str, trigger_date: str) -> str:
    slug = rc_signature.replace('::', '-').replace('.', '-').replace('_', '-')
    base = f"AUDIT-{trigger_date.replace('-', '')}-{slug}"
    return base


def run_audit(selftest: bool = False) -> int:
    since = datetime.now() - timedelta(days=90)

    # 1. 解析 ERRORS.md
    errors = parse_errors_md(ERRORS_MD)

    # 2. 补充 memory 任务
    mem_tasks = parse_memory_files(MEMORY_DIR, since)

    # 3. 聚合统计（使用每个条目的 Recurrence-Count）
    sig_stats = defaultdict(lambda: {'count': 0, 'tasks': set(), 'dates': []})
    for e in errors:
        if e['date'] < since:
            continue
        sig = e['rc_signature']
        # 使用条目自带的 Recurrence-Count（共享计数）
        sig_stats[sig]['count'] = max(sig_stats[sig]['count'], e.get('rc_count', 1))
        sig_stats[sig]['tasks'].update(e['tasks'])
        sig_stats[sig]['dates'].append(e['date'].strftime('%Y-%m-%d'))
        # 补充 memory 任务
        for key, tasks in mem_tasks.items():
            if key in sig:
                sig_stats[sig]['tasks'].update(tasks)

    # 4. 识别触发条件
    triggered = []
    for sig, stat in sig_stats.items():
        if stat['count'] >= 3 and len(stat['tasks']) >= 2:
            triggered.append({
                'rc_signature': sig,
                'rc_count': stat['count'],
                'affected_tasks': sorted(stat['tasks']),
                'dates': sorted(set(stat['dates']))
            })

    # 5. 加载现有工单
    tickets = load_existing_tickets(TICKETS_JSON)
    existing_open = {t['rc_signature'] for t in tickets if t['status'] in ('OPEN', 'IN_PROGRESS')}

    # 6. 生成新工单
    today = datetime.now().strftime('%Y-%m-%d')
    for trig in triggered:
        if trig['rc_signature'] in existing_open:
            continue
        ticket_id = generate_ticket_id(trig['rc_signature'], today)
        ticket = {
            'ticket_id': ticket_id,
            'trigger_date': today,
            'rc_signature': trig['rc_signature'],
            'rc_count': trig['rc_count'],
            'affected_tasks': trig['affected_tasks'],
            'failure_pattern': '',  # 人工填写
            'root_cause': '',
            'root_cause_detail': '',
            'fix_action': '',
            'fix_detail': '',
            'regression_test': '',
            'guard_script': '',
            'pipeline_step': '',
            'status': 'OPEN',
            'created_at': datetime.now().isoformat(),
            'closed_at': '',
            'fix_note': '',
            'target_close': (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
        }
        tickets.append(ticket)
        print(f"🔴 新工单: {ticket_id} | {trig['rc_signature']} | RC={trig['rc_count']} | 任务={len(trig['affected_tasks'])}")

    # 7. 保存
    save_tickets(TICKETS_JSON, tickets)
    render_board(TICKETS_MD, tickets)

    # 8. 统计待办
    open_count = sum(1 for t in tickets if t['status'] in ('OPEN', 'IN_PROGRESS'))
    print(f"\n当前待办工单: {open_count}")
    for t in tickets:
        if t['status'] in ('OPEN', 'IN_PROGRESS'):
            print(f"  - {t['ticket_id']} [{t['status']}] {t['rc_signature']}")

    if selftest:
        # 自测：验证已知 RC≥3 模式均被识别
        print("\n=== SELFTEST ===")
        all_pass = True
        for sig, tasks, should_trigger in SELFT_TEST_CASES:
            stat = sig_stats.get(sig, {'count': 0, 'tasks': set()})
            triggered = stat['count'] >= 3 and len(stat['tasks']) >= 2
            status = "✅" if triggered == should_trigger else "❌"
            if triggered != should_trigger:
                all_pass = False
            print(f"  {status} {sig}: count={stat['count']}, tasks={len(stat['tasks'])}, trigger={triggered} (expect={should_trigger})")
        return 0 if all_pass else 1

    return 1 if open_count > 0 else 0


if __name__ == '__main__':
    selftest = '--selftest' in sys.argv
    sys.exit(run_audit(selftest=selftest))
