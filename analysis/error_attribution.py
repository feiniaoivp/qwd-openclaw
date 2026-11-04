#!/usr/bin/env python3
"""error_attribution — 交易失败自动归因（T3-策略层, 2026-11-04）

目标链路: Strategy → Trade → Failure → Learning
从既有产物（零新增探测、零交易副作用）聚合当日「失败案例」：
  A. 被拦截信号: audit/trades_audit.jsonl 中 raw_action∈(买入,卖出) 且 status=HOLD 且存在 BLOCK 守卫
  B. 亏损平仓: data/portfolio_sim_trades.json 中 SELL 且 pnl<0（止损/防御卖出）
每案例自动抓取: 策略、原生信号+强度、门控拦截点、决策时市场环境 regime、成本结构。

产出:
  data/failure_cases/{date}_failure_cases.json   结构化案例（机器可读）
  data/failure_cases/ledger.jsonl                学习台账（跨日累积，一行一案）
  analysis/audit/failure_attribution_{date}.md   人读摘要

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

    # 学习台账（一行一案，跨日累积）
    ledger = os.path.join(OUT_DIR, "ledger.jsonl")
    with open(ledger, "a", encoding="utf-8") as f:
        for c in cases:
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
    print(f"   台账:   {ledger} (+{len(cases)} 行)")
    print(f"   摘要:   {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
