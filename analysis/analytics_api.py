#!/usr/bin/env python3
"""
FastAPI Analytics API — 暴露 SQLite 查询为 REST 接口
第 2 阶段：Script → FastAPI 服务化
"""

import os
import sys
import json
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

from analysis.sqlite_analytics import (
    get_connection,
    get_open_tickets,
    get_ticket,
    update_ticket,
    close_ticket,
    get_tickets_by_signature,
    get_failure_cases,
    get_blocked_signals,
    get_loss_exits,
    get_guard_stats,
    get_strategy_stats,
    get_daily_summary,
    get_recent_summaries,
    get_execution_audit,
    get_execution_rate_trend,
    get_pending_intents,
    update_intent_status,
    get_sentry_state,
    set_sentry_state,
    get_failure_dashboard,
    vacuum,
    backup_db,
    get_migration_log,
)

# 导入 Telegram 推送功能
try:
    from send_telegram import send_message
except ImportError:
    send_message = None
    print("⚠️ 无法导入 send_telegram，Telegram 推送功能将失效")

# Pydantic 模型
class TicketUpdate(BaseModel):
    status: Optional[str] = None
    fix_note: Optional[str] = None
    failure_pattern: Optional[str] = None
    root_cause: Optional[str] = None
    root_cause_detail: Optional[str] = None
    fix_action: Optional[str] = None
    fix_detail: Optional[str] = None
    regression_test: Optional[str] = None
    guard_script: Optional[str] = None
    pipeline_step: Optional[str] = None
    target_close: Optional[str] = None


class IntentStatusUpdate(BaseModel):
    status: str


class SentryStateUpdate(BaseModel):
    value: Dict[str, Any]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时验证数据库连接
    try:
        conn = get_connection()
        conn.execute("SELECT 1")
        conn.close()
        print("✅ 数据库连接正常")
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
    yield
    # 关闭时清理
    pass


app = FastAPI(
    title="OpenClaw Analytics API",
    description="统一查询接口：工单、失败案例、执行审计、交易意向、哨兵状态",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 健康检查
# ============================================================
@app.get("/health")
async def health():
    try:
        conn = get_connection()
        cur = conn.execute("SELECT COUNT(*) FROM structural_fix_tickets")
        tickets = cur.fetchone()[0]
        cur = conn.execute("SELECT COUNT(*) FROM failure_cases")
        cases = cur.fetchone()[0]
        conn.close()
        return {"status": "ok", "tickets": tickets, "cases": cases, "timestamp": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database error: {e}")


# ============================================================
# 结构性修复工单
# ============================================================
@app.get("/api/tickets", response_model=List[Dict])
async def list_tickets(
    status: Optional[str] = Query(None, description="筛选状态"),
    rc_signature: Optional[str] = Query(None, description="筛选 RC 签名"),
    limit: int = Query(50, ge=1, le=200),
):
    """获取工单列表"""
    with get_connection() as conn:
        conditions = []
        params = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if rc_signature:
            conditions.append("rc_signature = ?")
            params.append(rc_signature)
        
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)
        
        cur = conn.execute(f"""
            SELECT * FROM structural_fix_tickets
            {where}
            ORDER BY created_at DESC
            LIMIT ?
        """, params)
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["affected_tasks"] = json.loads(r.get("affected_tasks", "[]"))
        return rows


@app.get("/api/tickets/open", response_model=List[Dict])
async def list_open_tickets():
    """获取所有 OPEN 状态工单（看板用）"""
    tickets = get_open_tickets()
    for t in tickets:
        t["affected_tasks"] = json.loads(t.get("affected_tasks", "[]"))
    return tickets


@app.get("/api/tickets/{ticket_id}", response_model=Dict)
async def get_ticket_detail(ticket_id: str):
    """获取单个工单详情"""
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket["affected_tasks"] = json.loads(ticket.get("affected_tasks", "[]"))
    return ticket


@app.patch("/api/tickets/{ticket_id}", response_model=Dict)
async def patch_ticket(ticket_id: str, update: TicketUpdate):
    """更新工单"""
    fields = update.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    if update_ticket(ticket_id, **fields):
        return get_ticket_detail(ticket_id)
    raise HTTPException(status_code=404, detail="Ticket not found or no change")


@app.post("/api/tickets/{ticket_id}/close", response_model=Dict)
async def close_ticket_endpoint(ticket_id: str, fix_note: str = Query(..., description="关闭备注")):
    """关闭工单"""
    if close_ticket(ticket_id, fix_note):
        return get_ticket_detail(ticket_id)
    raise HTTPException(status_code=404, detail="Ticket not found")


@app.get("/api/tickets/signature/{rc_signature}", response_model=List[Dict])
async def get_tickets_by_sig(rc_signature: str):
    """按 RC 签名查工单"""
    tickets = get_tickets_by_signature(rc_signature)
    for t in tickets:
        t["affected_tasks"] = json.loads(t.get("affected_tasks", "[]"))
    return tickets


# ============================================================
# 失败案例台账
# ============================================================
@app.get("/api/cases", response_model=List[Dict])
async def list_cases(
    date_from: Optional[str] = Query(None, description="起始日期 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    case_type: Optional[str] = Query(None, description="类型: blocked_signal | loss_exit"),
    symbol: Optional[str] = Query(None, description="股票代码"),
    strategy: Optional[str] = Query(None, description="策略名"),
    limit: int = Query(100, ge=1, le=500),
):
    """查询失败案例"""
    return get_failure_cases(date_from, date_to, case_type, symbol, strategy, limit)


@app.get("/api/cases/blocked", response_model=List[Dict])
async def list_blocked(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    guard: Optional[str] = Query(None, description="按守卫名过滤"),
):
    """查询被拦截信号"""
    return get_blocked_signals(date_from, date_to, guard)


@app.get("/api/cases/loss", response_model=List[Dict])
async def list_loss(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None),
):
    """查询亏损平仓"""
    return get_loss_exits(date_from, date_to, strategy)


@app.get("/api/stats/guards", response_model=Dict[str, int])
async def guard_stats(days: int = Query(30, ge=1, le=365)):
    """守卫拦截统计"""
    return get_guard_stats(days)


@app.get("/api/stats/strategies", response_model=Dict)
async def strategy_stats(days: int = Query(30, ge=1, le=365)):
    """策略级失败统计"""
    return get_strategy_stats(days)


# ============================================================
# 每日汇总
# ============================================================
@app.get("/api/summary/{date}", response_model=Dict)
async def daily_summary(date: str):
    """获取某日汇总"""
    summary = get_daily_summary(date)
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")
    return summary


@app.get("/api/summaries/recent", response_model=List[Dict])
async def recent_summaries(days: int = Query(30, ge=1, le=365)):
    """最近 N 天汇总"""
    return get_recent_summaries(days)


# ============================================================
# 执行审计
# ============================================================
@app.get("/api/execution/{date}", response_model=List[Dict])
async def execution_audit(date: str):
    """某日执行审计"""
    return get_execution_audit(date)


@app.get("/api/execution/trend", response_model=List[Dict])
async def execution_trend(days: int = Query(30, ge=1, le=365)):
    """执行率趋势"""
    return get_execution_rate_trend(days)


# ============================================================
# 交易意向
# ============================================================
@app.get("/api/intents/pending", response_model=List[Dict])
async def pending_intents(date: Optional[str] = Query(None)):
    """待执行意向"""
    return get_pending_intents(date)


@app.patch("/api/intents/{symbol}/{date}", response_model=Dict)
async def update_intent(symbol: str, date: str, update: IntentStatusUpdate):
    """更新意向状态"""
    if update_intent_status(symbol, date, update.status):
        return {"symbol": symbol, "date": date, "status": update.status}
    raise HTTPException(status_code=404, detail="Intent not found")


# ============================================================
# 哨兵状态
# ============================================================
@app.get("/api/sentry/{key}", response_model=Dict)
async def sentry_get(key: str):
    """获取哨兵状态"""
    state = get_sentry_state(key)
    if not state:
        raise HTTPException(status_code=404, detail="Key not found")
    return state


@app.put("/api/sentry/{key}", response_model=Dict)
async def sentry_set(key: str, update: SentryStateUpdate):
    """设置哨兵状态"""
    set_sentry_state(key, update.value)
    return {"key": key, "value": update.value, "updated_at": datetime.now().isoformat()}


# ============================================================
# 仪表盘
# ============================================================
@app.get("/api/dashboard", response_model=Dict)
async def dashboard(days: int = Query(30, ge=1, le=365)):
    """失败归因仪表盘聚合数据"""
    return get_failure_dashboard(days)


# ============================================================
# Telegram 定时推送（早盘/午盘/收盘，复用 send_telegram.py 三级回退链）
# ============================================================

def _is_trading_day_now() -> bool:
    """交易日判定（SSOT: analysis.service.is_trading_day，2026-10-01 口径）。

    - 周末 → False（直接拦截推送）
    - 日历不可得 → SSOT 内部 fail-open 放行
    - SSOT 导入/调用异常 → fail-open 放行并可见告警（推送宁多勿漏）
    """
    try:
        from analysis.service import is_trading_day
        return bool(is_trading_day())
    except Exception as e:
        print(f"[analytics_api] is_trading_day 调用异常，fail-open 放行推送: {e}", flush=True)
        return True


def _tg_send(text: str) -> Dict[str, Any]:
    """发送 Telegram 纯文本消息（复用 send_telegram.py 传输层）。

    send_telegram.py 位于工作区根目录（非 scripts/），WORKSPACE 已在 sys.path：
    - 三级传输回退：curl_cffi 直连 → 本机 7890 代理 → 系统 curl 固定 IP
    - parse_mode 默认纯文本（避免 Markdown 实体 400）
    - >3900 字符自动按行分段（_split_long_text）
    """
    if send_message is None:
        return {"ok": False, "error": "send_telegram module unavailable"}
    return send_message(text)


def _fmt_dashboard_push(days: int = 30) -> str:
    """早盘 09:30：失败归因仪表盘摘要（数据同 /api/dashboard）"""
    d = get_failure_dashboard(days)
    ov = d.get("overview") or {}
    lines = [
        "📊 早盘推送 · 失败归因仪表盘",
        f"统计窗口: 近{d.get('period_days', days)}天 (自 {d.get('date_from', '?')})",
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"• OPEN 工单: {d.get('open_tickets', 0)} 张",
        f"• 被拦截信号: {int(ov.get('blocked_total') or 0)} 条",
    ]
    loss_total = int(ov.get("loss_total") or 0)
    try:
        pnl = float(ov.get("loss_pnl_sum") or 0)
        lines.append(f"• 亏损平仓: {loss_total} 笔 / 合计亏损 {pnl:.2f}")
    except (TypeError, ValueError):
        lines.append(f"• 亏损平仓: {loss_total} 笔")
    guard_stats = d.get("guard_stats") or {}
    if guard_stats:
        top = sorted(guard_stats.items(), key=lambda kv: kv[1], reverse=True)[:3]
        lines.append("")
        lines.append("守卫拦截 Top3: " + ", ".join(f"{k}×{v}" for k, v in top))
    trend = d.get("execution_trend") or []
    if trend:
        lines.append("")
        lines.append("近期执行率 (date: executed/actionable):")
        for row in trend[-3:]:
            if isinstance(row, dict):
                lines.append(
                    f"  {row.get('date', '?')}: {row.get('n_executed', '?')}/{row.get('n_actionable', '?')}"
                    f" = {row.get('execution_rate', '?')} ({row.get('verdict') or '-'})"
                )
            else:
                lines.append(f"  {row}")
    return "\n".join(lines)


def _fmt_open_tickets_push(limit: int = 10) -> str:
    """午盘 13:00：OPEN 工单待办（数据同 /api/tickets/open）"""
    tickets = get_open_tickets()
    lines = [
        "🧾 午盘推送 · OPEN 工单待办",
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"未关闭工单: {len(tickets)} 张",
        "",
    ]
    if not tickets:
        lines.append("✅ 无未关闭工单")
    for t in tickets[:limit]:
        tid = str(t.get("ticket_id") or "?")
        created = str(t.get("created_at") or "?")[:10]
        pattern = (t.get("failure_pattern") or t.get("root_cause") or "").strip()
        lines.append(f"• [{created}] {tid[:16]} RC×{t.get('rc_count', '?')} {t.get('rc_signature') or '?'}")
        if pattern:
            lines.append(f"    {pattern[:80]}")
    if len(tickets) > limit:
        lines.append(f"…另有 {len(tickets) - limit} 张未列出（详见 /api/tickets/open）")
    lines.append("")
    lines.append("关闭示例: curl -X POST 'http://127.0.0.1:8081/api/tickets/<ticket_id>/close?fix_note=...'")
    return "\n".join(lines)


def _fmt_cases_push(limit: int = 10) -> str:
    """收盘 15:30：最近失败案例（数据同 /api/cases?limit=10）"""
    cases = get_failure_cases(limit=limit)
    lines = [
        "📉 收盘推送 · 最近失败案例",
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"最新 {len(cases)} 条 (按 timestamp 倒序):",
        "",
    ]
    if not cases:
        lines.append("✅ 暂无失败案例记录")
    for c in cases:
        ctype = "🚫拦截" if c.get("type") == "blocked_signal" else "💸亏损"
        sym = f"{c.get('symbol') or '?'} {c.get('name') or ''}".strip()
        lines.append(
            f"• [{c.get('date', '?')}] {sym} {ctype} {c.get('strategy') or '-'}"
            + (f" pnl={c.get('pnl')}" if c.get("pnl") is not None else "")
        )
        reason = (c.get("reason") or "").strip()
        if reason:
            lines.append(f"    {reason[:90]}")
    return "\n".join(lines)


def _do_push(kind: str, dry_run: bool = False, force: bool = False, **kw) -> Dict[str, Any]:
    """统一推送执行器：交易日门控（周末/节假日跳过，force=true 可越过，仅供测试/运维）→ 组稿 → dry_run/实发。"""
    result: Dict[str, Any] = {"kind": kind, "timestamp": datetime.now().isoformat()}
    if not force and not _is_trading_day_now():
        result.update(sent=False, skipped="non_trading_day")
        return result
    if kind == "dashboard":
        text = _fmt_dashboard_push(days=int(kw.get("days", 30)))
    elif kind == "tickets":
        text = _fmt_open_tickets_push(limit=int(kw.get("limit", 10)))
    elif kind == "cases":
        text = _fmt_cases_push(limit=int(kw.get("limit", 10)))
    else:
        raise HTTPException(status_code=400, detail=f"unknown push kind: {kind}")
    result["text"] = text
    if dry_run:
        result.update(sent=False, dry_run=True)
        return result
    tg = _tg_send(text)
    result["telegram"] = tg
    result["sent"] = bool(tg.get("ok"))
    return result


@app.get("/api/push/dashboard")
async def push_dashboard(
    days: int = Query(30, ge=1, le=365),
    dry_run: bool = Query(False, description="true=只返回文案不实发"),
    force: bool = Query(False, description="true=跳过交易日门控（仅测试/运维）"),
):
    """早盘 09:30 推送：失败归因仪表盘摘要（cron 入口）"""
    return _do_push("dashboard", dry_run=dry_run, force=force, days=days)


@app.get("/api/push/tickets")
async def push_tickets(
    limit: int = Query(10, ge=1, le=50),
    dry_run: bool = Query(False),
    force: bool = Query(False),
):
    """午盘 13:00 推送：OPEN 工单待办（cron 入口）"""
    return _do_push("tickets", dry_run=dry_run, force=force, limit=limit)


@app.get("/api/push/cases")
async def push_cases(
    limit: int = Query(10, ge=1, le=100),
    dry_run: bool = Query(False),
    force: bool = Query(False),
):
    """收盘 15:30 推送：最近失败案例（cron 入口）"""
    return _do_push("cases", dry_run=dry_run, force=force, limit=limit)


# ============================================================
# 维护
# ============================================================
@app.post("/api/maintenance/vacuum")
async def maintenance_vacuum():
    """整理数据库"""
    vacuum()
    return {"status": "ok", "message": "VACUUM completed"}


@app.post("/api/maintenance/backup")
async def maintenance_backup():
    """备份数据库"""
    path = backup_db()
    return {"status": "ok", "backup_path": path}


@app.get("/api/maintenance/migrations", response_model=List[Dict])
async def migration_log():
    """迁移日志"""
    return get_migration_log()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("ANALYTICS_PORT", "8081")))