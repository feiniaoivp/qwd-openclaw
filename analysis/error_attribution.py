#!/usr/bin/env python3
"""error_attribution — 交易失败自动归因（T3-策略层, 2026-11-04）

目标链路: Strategy → Trade → Failure → Learning
从既有产物（零新增探测、零交易副作用）聚合当日「失败案例」：
  A. 被拦截信号: audit/trades_audit.jsonl 中 raw_action∈(买入,卖出) 且 status=HOLD 且存在 BLOCK 守卫
  B. 亏损平仓: data/portfolio_sim_trades.json 中 SELL 且 pnl<0（止损/防御卖出）
每案例自动抓取: 策略、原生信号+强度、门控拦截点、决策时市场环境 regime、成本结构。

产出:
  data/runtime/failure_cases/{date}_failure_cases.json   结构化案例（机器可读）
  data/runtime/failure_cases/ledger.jsonl                学习台账（跨日累积，一行一案）
  data/runtime/failure_cases/structural_fix_tickets.json 结构性修复工单（RC≥3 自动开票，2026-11-05）
  analysis/audit/failure_attribution_{date}.md   人读摘要
  analysis/audit/structural_fix_tickets.md       开票看板（OPEN 工单+交付物清单）

结构性修复工单（2026-11-05 用户拍板：RC≥3 从“修 bug”升格为“修逻辑”）:
  - 仅对 blocked_signal 模式开票（loss_exit 属策略绩效，走因子审计线，不开票）
  - 按 (pattern_class, strategy, primary_guard) 签名聚合，滚动 90 天 RC≥3 → 自动开票
  - 工单强制交付物: 门禁脚本 / 流水线接入点 / 根因归档；未闭环工单进周一审计周报
  - 关闭方式: 编辑 structural_fix_tickets.json 对应工单 status=CLOSED + fix_note（重跑不覆盖人工字段）

用法:
    python3 analysis/error_attribution.py               # 归因今天
    python3 analysis/error_attribution.py --date 2026-11-04
"""
import os
import sys
import json
import argparse
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

AUDIT_JSONL = os.path.join(WORKSPACE, "audit", "trades_audit.jsonl")
TRADES_FILE = os.path.join(WORKSPACE, "data", "portfolio_sim_trades.json")
SIGNAL_DIR = os.path.join(WORKSPACE, "data", "signal_archive")
OUT_DIR = os.path.join(WORKSPACE, "data", "failure_cases")
MD_DIR = os.path.join(WORKSPACE, "analysis", "audit")
TICKETS_FILE = os.path.join(OUT_DIR, "structural_fix_tickets.json")
TICKETS_MD = os.path.join(MD_DIR, "structural_fix_tickets.md")
TICKET_RC_THRESHOLD = 3   # 滚动窗口内 RC≥3 自动开票
TICKET_WINDOW_DAYS = 90


def load_audit(date_str):
    entries = []
    if not os.path.exists(AUDIT_JSONL):
        return entries
    with open(AUDIT_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                if e.get("timestamp", "").startswith(date_str):
                    entries.append(e)
            except json.JSONDecodeError:
                continue
    return entries


def load_trades(date_str):
    trades = []
    if not os.path.exists(TRADES_FILE):
        return trades
    try:
        with open(TRADES_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        print(f"⚠️ 成交流水读取失败: {e}")
        return trades
    # 兼容 list 或 {date: [...]} 两种历史形态
    items = raw if isinstance(raw, list) else [t for v in raw.values() for t in (v if isinstance(v, list) else [v])]
    for t in items:
        if isinstance(t, dict) and str(t.get("date", "")).startswith(date_str):
            trades.append(t)
    return trades


def load_signal(date_str, sym):
    path = os.path.join(SIGNAL_DIR, f"{date_str}_signals.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get(sym)
    except Exception:
        return None


def signal_strength(raw_signal):
    """从原生信号提取强度特征（各策略字段异构，取非空交集）"""
    if not raw_signal:
        return {}
    rs = raw_signal.get("raw_signal", raw_signal)
    keys = ["price", "atr", "atr_stop", "reason"]
    out = {k: rs.get(k) for k in keys if rs.get(k) is not None}
    # 相对强度: 现价距 ATR 止损的缓冲百分比（正=缓冲厚）
    try:
        if out.get("price") and out.get("atr_stop"):
            out["stop_buffer_pct"] = round((float(out["price"]) / float(out["atr_stop"]) - 1) * 100, 2)
    except (TypeError, ValueError):
        pass
    return out


def blocked_cases(audit_entries, date_str):
    """被拦截信号 → 失败案例"""
    cases = []
    for e in audit_entries:
        if e.get("status") != "HOLD":
            continue
        if e.get("raw_action") not in ("买入", "卖出"):
            continue
        blocks = {g: v for g, v in (e.get("guards") or {}).items()
                  if isinstance(v, str) and v.startswith("BLOCK")}
        if not blocks:
            continue
        sig = load_signal(date_str, e["symbol"])
        cases.append({
            "type": "blocked_signal",
            "symbol": e["symbol"], "name": e.get("name"),
            "strategy": e.get("strategy"),
            "raw_action": e.get("raw_action"),
            "blocked_by": blocks,
            "final_action": e.get("final_action"),
            "signal": signal_strength(sig),
            "regime": e.get("regime", {}),
            "timestamp": e.get("timestamp"),
        })
    return cases


def loss_trades(trades, date_str):
    """亏损平仓 → 失败案例（止损/防御卖出）"""
    cases = []
    for t in trades:
        if t.get("action") != "SELL":
            continue
        try:
            pnl = float(t.get("pnl", 0) or 0)
        except (TypeError, ValueError):
            continue
        if pnl >= 0:
            continue
        cases.append({
            "type": "loss_exit",
            "symbol": t.get("symbol"), "name": t.get("name"),
            "strategy": t.get("strategy"),
            "price": t.get("price"), "shares": t.get("shares"),
            "pnl": pnl, "pnl_pct": t.get("pnl_pct"),
            "reason": t.get("reason", ""),
            "bucket": t.get("bucket"),
            "timestamp": t.get("date"),
        })
    return cases


# ═══════════════ 结构性修复工单（RC≥3 → 修逻辑，2026-11-05）═══════════════

PATTERN_CLASS_RULES = [
    # (关键词匹配目标, 关键词, pattern_class, 人读说明)
    ("guard", "tier", "tier_gate", "策略分层门控拦截（tier1/2/3 分层规则触发）"),
    ("guard", "dedup", "dedup_limit", "单日去重/单日 1 笔硬限拦截"),
    ("guard", "risk_override", "risk_override", "030 风险覆写拦截（致命/高风险降级）"),
    ("guard", "fatal", "risk_override", "030 风险覆写拦截（致命/高风险降级）"),
    ("guard", "auto_exec", "pipeline_state_machine", "管道化状态机拦截（意向排队/消费链）"),
    ("guard", "stale_data", "stale_data", "数据滞后门控拦截"),
    ("guard", "cooldown", "cooldown", "止损冷却期拦截"),
    ("reason", "限额", "position_limit", "仓位/暴露限额拦截"),
    ("reason", "cap", "position_limit", "仓位/暴露限额拦截"),
]


def classify_pattern(case):
    """从拦截点+原因推断根因类（pattern_class）。未命中归 other。"""
    guards = case.get("blocked_by") or {}
    guard_text = " ".join(f"{k}:{v}" for k, v in guards.items())
    reason = str(case.get("signal", {}).get("reason", "")) + " " + str(case.get("raw_action", ""))
    for target, kw, cls, _doc in PATTERN_CLASS_RULES:
        hay = guard_text if target == "guard" else reason
        if kw in hay:
            return cls
    return "other"


def _primary_guard(case):
    guards = case.get("blocked_by") or {}
    return sorted(guards.keys())[0] if guards else "*"


def build_tickets(ledger_path=None, today=None):
    """从 ledger.jsonl 滚动窗口聚合 RC，RC≥3 自动开票（保留人工 status/fix_note）。"""
    ledger_path = ledger_path or os.path.join(OUT_DIR, "ledger.jsonl")
    today = today or datetime.now()
    cutoff = today.timestamp() - TICKET_WINDOW_DAYS * 86400

    # 1) 读台账（容错：坏行跳过，date 解析失败跳过）
    cases = []
    if os.path.exists(ledger_path):
        with open(ledger_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("type") != "blocked_signal":
                    continue  # loss_exit 走因子审计线，不开票
                try:
                    ts = datetime.fromisoformat(str(e.get("timestamp", ""))[:19])
                except (TypeError, ValueError):
                    continue
                if ts.timestamp() < cutoff:
                    continue
                cases.append(e)

    # 2) 签名聚合
    groups = {}
    for c in cases:
        sig = (classify_pattern(c), str(c.get("strategy") or "*"), _primary_guard(c))
        g = groups.setdefault(sig, {"rc": 0, "evidence": [], "last_seen": ""})
        g["rc"] += 1
        g["last_seen"] = max(g["last_seen"], str(c.get("timestamp", "")))
        if len(g["evidence"]) < 5:
            g["evidence"].append({
                "date": c.get("date"), "symbol": c.get("symbol"), "name": c.get("name"),
                "raw": c.get("raw_action"), "guard": _primary_guard(c),
            })

    # 3) 与既有工单合并（保留人工 status/fix_note）
    existing = {}
    if os.path.exists(TICKETS_FILE):
        try:
            with open(TICKETS_FILE, encoding="utf-8") as f:
                for t in json.load(f).get("tickets", []):
                    existing[(t.get("pattern_class"), t.get("strategy"), t.get("primary_guard"))] = t
        except Exception as e:
            print(f"⚠️ 工单文件读取失败（重建，人工字段丢失风险）: {e}")

    tickets, new_count = [], 0
    for (cls, strat, guard), g in sorted(groups.items(), key=lambda x: -x[1]["rc"]):
        prev = existing.get((cls, strat, guard))
        if g["rc"] >= TICKET_RC_THRESHOLD:
            if prev:
                prev.update({"rc_90d": g["rc"], "last_seen": g["last_seen"], "evidence": g["evidence"]})
                tickets.append(prev)
            else:
                new_count += 1
                tickets.append({
                    "ticket_id": f"SFT-{today.strftime('%Y%m%d')}-{len(tickets)+1:03d}",
                    "created": today.isoformat(timespec="seconds"),
                    "pattern_class": cls, "strategy": strat, "primary_guard": guard,
                    "rc_90d": g["rc"], "last_seen": g["last_seen"], "evidence": g["evidence"],
                    "status": "OPEN",
                    "required_deliverables": ["gate_script", "pipeline_hook", "root_cause_note"],
                    "fix_note": "",
                })
    # 已存在但本期未达阈值的工单保留（人工关闭的记录不丢）
    for key, t in existing.items():
        if t not in tickets:
            tickets.append(t)

    # 4) 落盘（重写型写点备份保护）
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(TICKETS_FILE):
        import shutil
        shutil.copy2(TICKETS_FILE, TICKETS_FILE + ".bak")
    with open(TICKETS_FILE, "w", encoding="utf-8") as f:
        json.dump({"updated_at": today.isoformat(timespec="seconds"),
                   "threshold": TICKET_RC_THRESHOLD, "window_days": TICKET_WINDOW_DAYS,
                   "tickets": tickets}, f, ensure_ascii=False, indent=2)
    return tickets, new_count


def write_tickets_board(tickets):
    """开票看板（OPEN 工单 + 交付物清单；供周一审计与人工验收）"""
    open_t = [t for t in tickets if t.get("status") == "OPEN"]
    lines = ["# 🎫 结构性修复工单看板", "",
             f"_更新: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 规则: 滚动 {TICKET_WINDOW_DAYS}d 内同签名 RC≥{TICKET_RC_THRESHOLD} 自动开票（仅 blocked_signal）_",
             f"_OPEN {len(open_t)} / 总 {len(tickets)}_", ""]
    if not open_t:
        lines.append("（当前无 OPEN 工单）")
    for t in open_t:
        lines += [f"## {t['ticket_id']} — {t['pattern_class']} / {t['strategy']} / {t['primary_guard']}",
                  f"- RC(90d): **{t['rc_90d']}** | 最近: {t['last_seen'][:16]} | 创建: {t['created'][:10]}",
                  f"- 交付物: {', '.join(t['required_deliverables'])}"]
        for ev in t.get("evidence", [])[:3]:
            lines.append(f"  - 证据: {ev.get('date')} {ev.get('name')}({ev.get('symbol')}) {ev.get('raw')}→拦 {ev.get('guard')}")
        lines.append("")
    closed = [t for t in tickets if t.get("status") != "OPEN"]
    if closed:
        lines += ["## 已闭环", ""]
        for t in closed:
            lines.append(f"- {t['ticket_id']} [{t['status']}] {t['pattern_class']}/{t['strategy']} — {t.get('fix_note','')[:60]}")
    with open(TICKETS_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    date_str = args.date

    audit_entries = load_audit(date_str)
    trades = load_trades(date_str)
    cases = blocked_cases(audit_entries, date_str) + loss_trades(trades, date_str)

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(MD_DIR, exist_ok=True)

    # 按类型+拦截点聚合统计（Learning 层输入）
    stats = {
        "blocked_total": sum(1 for c in cases if c["type"] == "blocked_signal"),
        "blocked_by_guard": {},
        "loss_total": sum(1 for c in cases if c["type"] == "loss_exit"),
        "loss_pnl_sum": round(sum(c.get("pnl", 0) for c in cases if c["type"] == "loss_exit"), 2),
    }
    for c in cases:
        if c["type"] == "blocked_signal":
            for g in c["blocked_by"]:
                stats["blocked_by_guard"][g] = stats["blocked_by_guard"].get(g, 0) + 1

    out = {
        "date": date_str,
        "generated_at": datetime.now().isoformat(),
        "audit_entries_scanned": len(audit_entries),
        "trades_scanned": len(trades),
        "stats": stats,
        "cases": cases,
    }

    # 重写型写点备份保护（backup_guard 铁律）
    out_file = os.path.join(OUT_DIR, f"{date_str}_failure_cases.json")
    if os.path.exists(out_file):
        import shutil
        shutil.copy2(out_file, out_file + ".bak")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # 学习台账（一行一案，跨日累积；**幂等追加**：同 (date,symbol,action,guard,timestamp) 已存在则跳过，
    # 防同日重跑/手动补跑写入重复案例污染 RC 计数——2026-11-05 实证重复事故后修复）
    ledger = os.path.join(OUT_DIR, "ledger.jsonl")
    def _case_key(c):
        return (str(c.get("date") or date_str), str(c.get("symbol")), str(c.get("raw_action")),
                tuple(sorted((c.get("blocked_by") or {}).keys())), str(c.get("timestamp")))
    existing_keys = set()
    if os.path.exists(ledger):
        with open(ledger, encoding="utf-8") as f:
            for line in f:
                try:
                    existing_keys.add(_case_key(json.loads(line)))
                except (json.JSONDecodeError, AttributeError):
                    continue
    new_cases = [c for c in cases if _case_key(c) not in existing_keys]
    skipped = len(cases) - len(new_cases)
    with open(ledger, "a", encoding="utf-8") as f:
        for c in new_cases:
            f.write(json.dumps({"date": date_str, **c}, ensure_ascii=False) + "\n")

    # 人读摘要
    md = [f"# 🔬 失败归因报告 ({date_str})", "",
          f"- 扫描审计条目: {len(audit_entries)} | 成交流水: {len(trades)}",
          f"- 被拦截信号: **{stats['blocked_total']}** | 亏损平仓: **{stats['loss_total']}** (合计 {stats['loss_pnl_sum']:+,.2f})",
          ""]
    if stats["blocked_by_guard"]:
        md.append("## 拦截点分布")
        md.append("| 守卫 | 次数 |")
        md.append("|:---|---:|")
        for g, n in sorted(stats["blocked_by_guard"].items(), key=lambda x: -x[1]):
            md.append(f"| {g} | {n} |")
        md.append("")
    if cases:
        md.append("## 案例明细")
        for c in cases[:20]:
            if c["type"] == "blocked_signal":
                md.append(f"- 🚫 {c['name']}({c['symbol']}) {c['raw_action']}→{c['final_action']} "
                          f"[{c['strategy']}] 拦截: {', '.join(c['blocked_by'].keys())} | regime: buy_mult={c['regime'].get('buy_mult')}")
            else:
                md.append(f"- 🔻 {c['name']}({c['symbol']}) 亏损平仓 {c.get('pnl_pct')}% "
                          f"[{c['strategy']}] ¥{c.get('pnl'):,.0f} | {c.get('reason','')[:40]}")
    else:
        md.append("（今日无失败案例）")
    md_path = os.path.join(MD_DIR, f"failure_attribution_{date_str}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print(f"🔬 归因完成: 拦截 {stats['blocked_total']} | 亏损平仓 {stats['loss_total']} ({stats['loss_pnl_sum']:+,.2f})")
    print(f"   结构化: {out_file}")
    print(f"   台账:   {ledger} (+{len(new_cases)} 行{f'，幂等跳过 {skipped} 重复' if skipped else ''})")
    print(f"   摘要:   {md_path}")

    # ── 结构性修复工单（RC≥3 → 修逻辑，2026-11-05；含历史台账全量复算）──
    try:
        tickets, new_count = build_tickets()
        write_tickets_board(tickets)
        open_n = sum(1 for t in tickets if t.get("status") == "OPEN")
        print(f"   工单:   {TICKETS_FILE} (OPEN {open_n}/{len(tickets)}，新开 {new_count})")
        print(f"   看板:   {TICKETS_MD}")
    except Exception as e:
        print(f"⚠️ 工单生成失败（不阻断归因主流程）: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
