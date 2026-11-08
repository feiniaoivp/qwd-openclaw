#!/usr/bin/env python3
"""
SQLite 迁移脚本：将 JSON/JSONL 存储迁移到 SQLite
统一索引/查询/事务，替代分散文件读写
"""

import os
import sys
import json
import sqlite3
import glob
from datetime import datetime
from pathlib import Path

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
DB_PATH = os.path.join(WORKSPACE, "data", "analytics.db")

# 确保目录存在
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn):
    """初始化数据库 Schema"""
    cur = conn.cursor()

    # 1. 结构性修复工单表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS structural_fix_tickets (
            ticket_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            trigger_date TEXT NOT NULL,
            rc_signature TEXT NOT NULL,
            rc_count INTEGER NOT NULL,
            affected_tasks TEXT NOT NULL,  -- JSON array
            failure_pattern TEXT,
            root_cause TEXT,
            root_cause_detail TEXT,
            fix_action TEXT,
            fix_detail TEXT,
            regression_test TEXT,
            guard_script TEXT,
            pipeline_step TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            closed_at TEXT,
            fix_note TEXT,
            target_close TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 索引
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON structural_fix_tickets(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tickets_rc_sig ON structural_fix_tickets(rc_signature)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tickets_trigger_date ON structural_fix_tickets(trigger_date)")

    # 2. 失败案例台账表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS failure_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            type TEXT NOT NULL,  -- 'blocked_signal' | 'loss_exit'
            symbol TEXT NOT NULL,
            name TEXT,
            strategy TEXT,
            raw_action TEXT,
            blocked_by TEXT,  -- JSON object
            final_action TEXT,
            signal TEXT,  -- JSON object
            regime TEXT,  -- JSON object
            timestamp TEXT NOT NULL,
            pnl REAL,
            pnl_pct REAL,
            price REAL,
            shares INTEGER,
            reason TEXT,
            bucket TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, symbol, raw_action, timestamp)
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_fc_date ON failure_cases(date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fc_type ON failure_cases(type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fc_symbol ON failure_cases(symbol)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fc_strategy ON failure_cases(strategy)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fc_timestamp ON failure_cases(timestamp)")

    # 3. 每日失败案例汇总表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_failure_summary (
            date TEXT PRIMARY KEY,
            generated_at TEXT NOT NULL,
            audit_entries_scanned INTEGER,
            trades_scanned INTEGER,
            blocked_total INTEGER,
            loss_total INTEGER,
            loss_pnl_sum REAL,
            blocked_by_guard TEXT,  -- JSON object
            cases_json_path TEXT,
            ledger_path TEXT,
            md_path TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 4. 执行审计表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS execution_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            kind TEXT NOT NULL,  -- 'sell' | 'buy' | 'other'
            symbol TEXT NOT NULL,
            name TEXT,
            action TEXT,
            stop_loss REAL,
            executed INTEGER NOT NULL DEFAULT 0,
            status TEXT,
            reason TEXT,
            n_actionable INTEGER,
            n_executed INTEGER,
            execution_rate REAL,
            verdict TEXT,
            held_marker TEXT,  -- JSON object
            held_marker_quality TEXT,
            signal_basis TEXT,  -- JSON object
            generated_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, symbol, kind, action)
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_ea_date ON execution_audit(date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ea_symbol ON execution_audit(symbol)")

    # 5. 交易意向表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_intents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            action TEXT,
            reason TEXT,
            price REAL,
            bucket TEXT,
            strategy TEXT,
            atr REAL,
            atr_stop REAL,
            fib_tp TEXT,  -- JSON object
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, symbol, action)
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_ti_date ON trade_intents(date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ti_symbol ON trade_intents(symbol)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ti_status ON trade_intents(status)")

    # 6. 哨兵状态表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sentry_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            value TEXT NOT NULL,  -- JSON
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 7. 迁移记录表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS migration_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            record_count INTEGER NOT NULL,
            status TEXT NOT NULL,  -- 'success' | 'partial' | 'failed'
            error_message TEXT,
            migrated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    print("✅ Schema 初始化完成")


def migrate_structural_fix_tickets(conn):
    """迁移结构性修复工单"""
    cur = conn.cursor()
    file_path = os.path.join(WORKSPACE, "data", "failure_cases", "structural_fix_tickets.json")
    
    if not os.path.exists(file_path):
        print(f"⚠️ 文件不存在: {file_path}")
        return 0

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    tickets = data.get("tickets", data) if isinstance(data, dict) else data
    count = 0
    for t in tickets:
        try:
            cur.execute("""
                INSERT OR REPLACE INTO structural_fix_tickets
                (ticket_id, created_at, trigger_date, rc_signature, rc_count, affected_tasks,
                 failure_pattern, root_cause, root_cause_detail, fix_action, fix_detail,
                 regression_test, guard_script, pipeline_step, status, closed_at, fix_note, target_close)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                t.get("ticket_id"),
                t.get("created_at"),
                t.get("trigger_date"),
                t.get("rc_signature"),
                t.get("rc_count"),
                json.dumps(t.get("affected_tasks", []), ensure_ascii=False),
                t.get("failure_pattern", ""),
                t.get("root_cause", ""),
                t.get("root_cause_detail", ""),
                t.get("fix_action", ""),
                t.get("fix_detail", ""),
                t.get("regression_test", ""),
                t.get("guard_script", ""),
                t.get("pipeline_step", ""),
                t.get("status", "OPEN"),
                t.get("closed_at", ""),
                t.get("fix_note", ""),
                t.get("target_close", "")
            ))
            count += 1
        except Exception as e:
            print(f"  ❌ 工单迁移失败 {t.get('ticket_id')}: {e}")

    conn.commit()
    print(f"✅ structural_fix_tickets: {count} 条迁移完成")
    return count


def migrate_failure_cases_ledger(conn):
    """迁移失败案例台账"""
    cur = conn.cursor()
    file_path = os.path.join(WORKSPACE, "data", "failure_cases", "ledger.jsonl")
    
    if not os.path.exists(file_path):
        print(f"⚠️ 文件不存在: {file_path}")
        return 0

    count = 0
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except json.JSONDecodeError:
                continue

            try:
                cur.execute("""
                    INSERT OR IGNORE INTO failure_cases
                    (date, type, symbol, name, strategy, raw_action, blocked_by,
                     final_action, signal, regime, timestamp, pnl, pnl_pct,
                     price, shares, reason, bucket)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    c.get("date"),
                    c.get("type"),
                    c.get("symbol"),
                    c.get("name"),
                    c.get("strategy"),
                    c.get("raw_action"),
                    json.dumps(c.get("blocked_by", {}), ensure_ascii=False),
                    c.get("final_action"),
                    json.dumps(c.get("signal", {}), ensure_ascii=False),
                    json.dumps(c.get("regime", {}), ensure_ascii=False),
                    c.get("timestamp"),
                    c.get("pnl"),
                    c.get("pnl_pct"),
                    c.get("price"),
                    c.get("shares"),
                    c.get("reason"),
                    c.get("bucket")
                ))
                count += 1
            except Exception as e:
                print(f"  ❌ 案例迁移失败 {c.get('symbol')}: {e}")

    conn.commit()
    print(f"✅ failure_cases ledger: {count} 条迁移完成")
    return count


def migrate_daily_failure_cases(conn):
    """迁移每日失败案例汇总"""
    cur = conn.cursor()
    dir_path = os.path.join(WORKSPACE, "data", "failure_cases")
    files = glob.glob(os.path.join(dir_path, "*_failure_cases.json"))
    
    count = 0
    for file_path in files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            date_str = data.get("date")
            if not date_str:
                continue

            cur.execute("""
                INSERT OR REPLACE INTO daily_failure_summary
                (date, generated_at, audit_entries_scanned, trades_scanned,
                 blocked_total, loss_total, loss_pnl_sum, blocked_by_guard,
                 cases_json_path, ledger_path, md_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date_str,
                data.get("generated_at"),
                data.get("audit_entries_scanned"),
                data.get("trades_scanned"),
                data.get("stats", {}).get("blocked_total"),
                data.get("stats", {}).get("loss_total"),
                data.get("stats", {}).get("loss_pnl_sum"),
                json.dumps(data.get("stats", {}).get("blocked_by_guard", {}), ensure_ascii=False),
                file_path,
                os.path.join(dir_path, "ledger.jsonl"),
                os.path.join(WORKSPACE, "analysis", "audit", f"failure_attribution_{date_str}.md")
            ))
            count += 1
        except Exception as e:
            print(f"  ❌ 每日汇总迁移失败 {file_path}: {e}")

    conn.commit()
    print(f"✅ daily_failure_summary: {count} 条迁移完成")
    return count


def migrate_execution_audit(conn):
    """迁移执行审计"""
    cur = conn.cursor()
    dir_path = os.path.join(WORKSPACE, "data", "execution_audit")
    files = glob.glob(os.path.join(dir_path, "execution_audit_*.json"))
    
    count = 0
    for file_path in files:
        if ".bak" in file_path:
            continue
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            date_str = data.get("date")
            if not date_str:
                continue

            # actionable (sell)
            for a in data.get("actionable", []):
                cur.execute("""
                    INSERT OR REPLACE INTO execution_audit
                    (date, kind, symbol, name, action, stop_loss, executed, status, reason,
                     n_actionable, n_executed, execution_rate, verdict, held_marker,
                     held_marker_quality, signal_basis, generated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    date_str,
                    "sell",
                    a.get("symbol"),
                    a.get("name"),
                    a.get("action"),
                    a.get("stop_loss"),
                    1 if a.get("executed") else 0,
                    a.get("status"),
                    a.get("reason"),
                    data.get("n_actionable"),
                    data.get("n_executed"),
                    data.get("execution_rate"),
                    data.get("verdict"),
                    json.dumps(data.get("held_marker", {}), ensure_ascii=False),
                    data.get("held_marker_quality"),
                    json.dumps(data.get("signal_basis", {}), ensure_ascii=False),
                    data.get("generated_at")
                ))
                count += 1

            # blocked_buys
            for a in data.get("blocked_buys", []):
                cur.execute("""
                    INSERT OR REPLACE INTO execution_audit
                    (date, kind, symbol, name, action, stop_loss, executed, status, reason,
                     n_actionable, n_executed, execution_rate, verdict, held_marker,
                     held_marker_quality, signal_basis, generated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    date_str,
                    "buy",
                    a.get("symbol"),
                    a.get("name"),
                    a.get("action"),
                    a.get("stop_loss"),
                    0,
                    a.get("status"),
                    a.get("reason"),
                    data.get("n_actionable"),
                    data.get("n_executed"),
                    data.get("execution_rate"),
                    data.get("verdict"),
                    json.dumps(data.get("held_marker", {}), ensure_ascii=False),
                    data.get("held_marker_quality"),
                    json.dumps(data.get("signal_basis", {}), ensure_ascii=False),
                    data.get("generated_at")
                ))
                count += 1

        except Exception as e:
            print(f"  ❌ 执行审计迁移失败 {file_path}: {e}")

    conn.commit()
    print(f"✅ execution_audit: {count} 条迁移完成")
    return count


def migrate_trade_intents(conn):
    """迁移交易意向"""
    cur = conn.cursor()
    dir_path = os.path.join(WORKSPACE, "data", "trade_intents")
    files = glob.glob(os.path.join(dir_path, "*_intents.json"))
    
    count = 0
    for file_path in files:
        if ".bak" in file_path:
            continue
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Extract date from filename
            date_str = os.path.basename(file_path).split("_")[0]
            
            for symbol, intent in data.items():
                cur.execute("""
                    INSERT OR REPLACE INTO trade_intents
                    (date, symbol, name, action, reason, price, bucket, strategy,
                     atr, atr_stop, fib_tp, created_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    date_str,
                    symbol,
                    intent.get("name"),
                    intent.get("action"),
                    intent.get("reason"),
                    intent.get("price"),
                    intent.get("bucket"),
                    intent.get("strategy"),
                    intent.get("atr"),
                    intent.get("atr_stop"),
                    json.dumps(intent.get("fib_tp", {}), ensure_ascii=False),
                    intent.get("created_at"),
                    intent.get("status", "PENDING")
                ))
                count += 1
        except Exception as e:
            print(f"  ❌ 交易意向迁移失败 {file_path}: {e}")

    conn.commit()
    print(f"✅ trade_intents: {count} 条迁移完成")
    return count


def migrate_sentry_state(conn):
    """迁移哨兵状态"""
    cur = conn.cursor()
    file_path = os.path.join(WORKSPACE, "data", "sentry_state.json")
    
    if not os.path.exists(file_path):
        print(f"⚠️ 文件不存在: {file_path}")
        return 0

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        count = 0
        for key, value in data.items():
            cur.execute("""
                INSERT OR REPLACE INTO sentry_state (key, value)
                VALUES (?, ?)
            """, (key, json.dumps(value, ensure_ascii=False)))
            count += 1

        conn.commit()
        print(f"✅ sentry_state: {count} 条迁移完成")
        return count
    except Exception as e:
        print(f"  ❌ 哨兵状态迁移失败: {e}")
        return 0


def record_migration(conn, source_file, record_count, status, error_message=None):
    """记录迁移日志"""
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO migration_log (source_file, record_count, status, error_message)
        VALUES (?, ?, ?, ?)
    """, (source_file, record_count, status, error_message))
    conn.commit()


def main():
    print(f"🗄️ SQLite 迁移开始: {DB_PATH}")
    
    conn = get_connection()
    try:
        init_schema(conn)
        
        # 执行迁移
        total = 0
        total += migrate_structural_fix_tickets(conn)
        total += migrate_failure_cases_ledger(conn)
        total += migrate_daily_failure_cases(conn)
        total += migrate_execution_audit(conn)
        total += migrate_trade_intents(conn)
        total += migrate_sentry_state(conn)
        
        print(f"\n🎉 迁移完成！总计 {total} 条记录")
        
        # 验证
        cur = conn.cursor()
        tables = ["structural_fix_tickets", "failure_cases", "daily_failure_summary",
                  "execution_audit", "trade_intents", "sentry_state"]
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            print(f"  {t}: {cur.fetchone()[0]} 条")
            
    finally:
        conn.close()


if __name__ == "__main__":
    main()