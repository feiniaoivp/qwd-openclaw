#!/usr/bin/env python3
"""
SQLite Analytics — 统一查询接口
替代分散的 JSON/JSONL 文件读写，提供类型安全、索引加速、事务支持的统一访问层
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from contextlib import contextmanager
from pathlib import Path

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
DB_PATH = os.path.join(WORKSPACE, "data", "analytics.db")


def get_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction():
    """事务上下文管理器"""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================
# 结构性修复工单
# ============================================================

def get_open_tickets() -> List[Dict]:
    """获取所有 OPEN 状态工单"""
    with get_connection() as conn:
        cur = conn.execute("""
            SELECT * FROM structural_fix_tickets
            WHERE status = 'OPEN'
            ORDER BY created_at DESC
        """)
        return [dict(row) for row in cur.fetchall()]


def get_ticket(ticket_id: str) -> Optional[Dict]:
    """按 ID 获取工单"""
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM structural_fix_tickets WHERE ticket_id = ?", (ticket_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def update_ticket(ticket_id: str, **fields) -> bool:
    """更新工单字段（仅允许特定字段）"""
    allowed = {"status", "closed_at", "fix_note", "failure_pattern", "root_cause", 
               "root_cause_detail", "fix_action", "fix_detail", "regression_test",
               "guard_script", "pipeline_step", "target_close"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    
    updates["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [ticket_id]
    
    with get_connection() as conn:
        cur = conn.execute(f"UPDATE structural_fix_tickets SET {set_clause} WHERE ticket_id = ?", values)
        return cur.rowcount > 0


def close_ticket(ticket_id: str, fix_note: str) -> bool:
    """关闭工单"""
    return update_ticket(ticket_id, status="CLOSED", closed_at=datetime.now().isoformat(), fix_note=fix_note)


def get_tickets_by_signature(rc_signature: str) -> List[Dict]:
    """按 RC 签名查工单"""
    with get_connection() as conn:
        cur = conn.execute("""
            SELECT * FROM structural_fix_tickets
            WHERE rc_signature = ?
            ORDER BY created_at DESC
        """, (rc_signature,))
        return [dict(row) for row in cur.fetchall()]


# ============================================================
# 失败案例台账
# ============================================================

def get_failure_cases(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    case_type: Optional[str] = None,
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    limit: int = 100
) -> List[Dict]:
    """查询失败案例，支持多维过滤"""
    conditions = []
    params = []
    
    if date_from:
        conditions.append("date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("date <= ?")
        params.append(date_to)
    if case_type:
        conditions.append("type = ?")
        params.append(case_type)
    if symbol:
        conditions.append("symbol = ?")
        params.append(symbol)
    if strategy:
        conditions.append("strategy = ?")
        params.append(strategy)
    
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(limit)
    
    with get_connection() as conn:
        cur = conn.execute(f"""
            SELECT * FROM failure_cases
            {where}
            ORDER BY timestamp DESC
            LIMIT ?
        """, params)
        return [dict(row) for row in cur.fetchall()]


def get_blocked_signals(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    guard: Optional[str] = None
) -> List[Dict]:
    """查询被拦截信号，可按守卫过滤"""
    conditions = ["type = 'blocked_signal'"]
    params = []
    
    if date_from:
        conditions.append("date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("date <= ?")
        params.append(date_to)
    if guard:
        conditions.append("json_extract(blocked_by, '$." + guard + "') IS NOT NULL")
    
    where = "WHERE " + " AND ".join(conditions)
    
    with get_connection() as conn:
        cur = conn.execute(f"""
            SELECT * FROM failure_cases
            {where}
            ORDER BY timestamp DESC
        """, params)
        return [dict(row) for row in cur.fetchall()]


def get_loss_exits(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    strategy: Optional[str] = None
) -> List[Dict]:
    """查询亏损平仓"""
    conditions = ["type = 'loss_exit'"]
    params = []
    
    if date_from:
        conditions.append("date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("date <= ?")
        params.append(date_to)
    if strategy:
        conditions.append("strategy = ?")
        params.append(strategy)
    
    where = "WHERE " + " AND ".join(conditions)
    
    with get_connection() as conn:
        cur = conn.execute(f"""
            SELECT * FROM failure_cases
            {where}
            ORDER BY timestamp DESC
        """, params)
        return [dict(row) for row in cur.fetchall()]


def get_guard_stats(days: int = 30) -> Dict[str, int]:
    """获取守卫拦截统计（最近 N 天）"""
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    with get_connection() as conn:
        cur = conn.execute("""
            SELECT blocked_by FROM failure_cases
            WHERE type = 'blocked_signal' AND date >= ?
        """, (date_from,))
        
        stats = {}
        for row in cur.fetchall():
            blocked_by = json.loads(row["blocked_by"])
            for guard in blocked_by:
                stats[guard] = stats.get(guard, 0) + 1
        return stats


def get_strategy_stats(days: int = 30) -> Dict[str, Dict]:
    """获取策略级失败统计"""
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    with get_connection() as conn:
        cur = conn.execute("""
            SELECT strategy, type, COUNT(*) as cnt
            FROM failure_cases
            WHERE date >= ?
            GROUP BY strategy, type
        """, (date_from,))
        
        stats = {}
        for row in cur.fetchall():
            strat = row["strategy"] or "unknown"
            if strat not in stats:
                stats[strat] = {"blocked": 0, "loss": 0, "total": 0}
            if row["type"] == "blocked_signal":
                stats[strat]["blocked"] = row["cnt"]
            else:
                stats[strat]["loss"] = row["cnt"]
            stats[strat]["total"] = stats[strat]["blocked"] + stats[strat]["loss"]
        return stats


# ============================================================
# 每日失败汇总
# ============================================================

def get_daily_summary(date: str) -> Optional[Dict]:
    """获取某日失败汇总"""
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM daily_failure_summary WHERE date = ?", (date,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_recent_summaries(days: int = 30) -> List[Dict]:
    """获取最近 N 天汇总"""
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with get_connection() as conn:
        cur = conn.execute("""
            SELECT * FROM daily_failure_summary
            WHERE date >= ?
            ORDER BY date DESC
        """, (date_from,))
        return [dict(row) for row in cur.fetchall()]


# ============================================================
# 执行审计
# ============================================================

def get_execution_audit(date: str) -> List[Dict]:
    """获取某日执行审计"""
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM execution_audit WHERE date = ?", (date,))
        return [dict(row) for row in cur.fetchall()]


def get_execution_rate_trend(days: int = 30) -> List[Dict]:
    """获取执行率趋势"""
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with get_connection() as conn:
        cur = conn.execute("""
            SELECT date, n_actionable, n_executed, execution_rate, verdict
            FROM execution_audit
            WHERE date >= ?
            ORDER BY date
        """, (date_from,))
        return [dict(row) for row in cur.fetchall()]


# ============================================================
# 交易意向
# ============================================================

def get_pending_intents(date: Optional[str] = None) -> List[Dict]:
    """获取待执行意向"""
    with get_connection() as conn:
        if date:
            cur = conn.execute("""
                SELECT * FROM trade_intents
                WHERE date = ? AND status = 'PENDING'
                ORDER BY created_at
            """, (date,))
        else:
            cur = conn.execute("""
                SELECT * FROM trade_intents
                WHERE status = 'PENDING'
                ORDER BY date, created_at
            """)
        return [dict(row) for row in cur.fetchall()]


def update_intent_status(symbol: str, date: str, status: str) -> bool:
    """更新意向状态"""
    with get_connection() as conn:
        cur = conn.execute("""
            UPDATE trade_intents SET status = ?, updated_at = ?
            WHERE symbol = ? AND date = ?
        """, (status, datetime.now().isoformat(), symbol, date))
        return cur.rowcount > 0


# ============================================================
# 哨兵状态
# ============================================================

def get_sentry_state(key: str) -> Optional[Dict]:
    """获取哨兵状态"""
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM sentry_state WHERE key = ?", (key,))
        row = cur.fetchone()
        if row:
            return {"key": row["key"], "value": json.loads(row["value"]), "updated_at": row["updated_at"]}
        return None


def set_sentry_state(key: str, value: Dict) -> bool:
    """设置哨兵状态"""
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT OR REPLACE INTO sentry_state (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, json.dumps(value, ensure_ascii=False), datetime.now().isoformat()))
        return cur.rowcount > 0


# ============================================================
# 统计/看板
# ============================================================

def get_ticket_board() -> List[Dict]:
    """获取工单看板数据（OPEN 工单 + 交付物清单）"""
    tickets = get_open_tickets()
    for t in tickets:
        t["affected_tasks"] = json.loads(t.get("affected_tasks", "[]"))
    return tickets


def get_failure_dashboard(days: int = 30) -> Dict:
    """失败归因仪表盘聚合数据"""
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    with get_connection() as conn:
        # 总览
        cur = conn.execute("""
            SELECT 
                SUM(CASE WHEN type='blocked_signal' THEN 1 ELSE 0 END) as blocked_total,
                SUM(CASE WHEN type='loss_exit' THEN 1 ELSE 0 END) as loss_total,
                SUM(CASE WHEN type='loss_exit' THEN COALESCE(pnl,0) ELSE 0 END) as loss_pnl_sum
            FROM failure_cases
            WHERE date >= ?
        """, (date_from,))
        overview = dict(cur.fetchone())
        
        # 守卫分布
        guard_stats = get_guard_stats(days)
        
        # 策略分布
        strategy_stats = get_strategy_stats(days)
        
        # 执行率趋势
        exec_trend = get_execution_rate_trend(days)
        
        # OPEN 工单
        open_tickets = get_open_tickets()
        
        return {
            "period_days": days,
            "date_from": date_from,
            "overview": overview,
            "guard_stats": guard_stats,
            "strategy_stats": strategy_stats,
            "execution_trend": exec_trend,
            "open_tickets": len(open_tickets),
            "ticket_details": open_tickets
        }


# ============================================================
# 维护/工具
# ============================================================

def vacuum():
    """整理数据库"""
    with get_connection() as conn:
        conn.execute("VACUUM")
    print("✅ VACUUM 完成")


def backup_db(backup_path: Optional[str] = None) -> str:
    """备份数据库"""
    if backup_path is None:
        backup_path = DB_PATH + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    import shutil
    shutil.copy2(DB_PATH, backup_path)
    print(f"✅ 备份完成: {backup_path}")
    return backup_path


def get_migration_log() -> List[Dict]:
    """获取迁移日志"""
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM migration_log ORDER BY migrated_at DESC")
        return [dict(row) for row in cur.fetchall()]


def main():
    """CLI 测试入口"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dash", action="store_true", help="显示仪表盘")
    ap.add_argument("--tickets", action="store_true", help="显示 OPEN 工单")
    ap.add_argument("--cases", action="store_true", help="显示最近失败案例")
    ap.add_argument("--days", type=int, default=30, help="天数")
    ap.add_argument("--vacuum", action="store_true", help="整理数据库")
    ap.add_argument("--backup", action="store_true", help="备份数据库")
    args = ap.parse_args()
    
    if args.vacuum:
        vacuum()
        return
    if args.backup:
        backup_db()
        return
    
    if args.dash:
        dash = get_failure_dashboard(args.days)
        print(json.dumps(dash, ensure_ascii=False, indent=2, default=str))
    elif args.tickets:
        tickets = get_open_tickets()
        for t in tickets:
            t["affected_tasks"] = json.loads(t.get("affected_tasks", "[]"))
        print(json.dumps(tickets, ensure_ascii=False, indent=2, default=str))
    elif args.cases:
        cases = get_failure_cases(date_from=(datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d"), limit=20)
        print(json.dumps(cases, ensure_ascii=False, indent=2, default=str))
    else:
        print("用法: python3 analysis/sqlite_analytics.py [--dash|--tickets|--cases] [--days N]")


if __name__ == "__main__":
    main()