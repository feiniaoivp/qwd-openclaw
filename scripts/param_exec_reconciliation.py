#!/usr/bin/env python3
"""
param_exec_reconciliation.py — 调参×模拟盘执行月度对账（2026-10-11 #10）
=======================================================================
背景：周流水线"赛后验证"窗口只有 14 天、且成交极稀疏（08-08 以来 27 只被调参
仅 1 笔成交）——调参是否真的改善执行，当前闭环是断的。本脚本按月出对账：
    1) 调参批次/涉及股票（param_change_log 全量）
    2) 调参日之后的模拟盘成交归因（portfolio_sim_trades.json）
    3) 按策略统计成交表现（策略切换有没有兑现成交易）
    4) 执行链健康度：有成交的被调参股票占比；过低则显式声明"调参有效性不可验证"
产出：analysis/monthly/param_exec_reconciliation_{YYYY-MM}.md + Telegram 推送摘要。
"""

import os
import sys
import json
import csv
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
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = os.path.join(WORKSPACE, "data")
OUT_DIR = os.path.join(WORKSPACE, "analysis", "monthly")
TRADES_FILE = os.path.join(DATA, "portfolio_sim_trades.json")
CHANGE_LOG = os.path.join(DATA, "param_change_log.json")
MAP_FILE = os.path.join(DATA, "adaptive_strategy_map.json")
EQUITY_FILE = os.path.join(DATA, "portfolio_equity.csv")


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"⚠️ 读取失败 {path}: {e}\n")
        return default


def main() -> int:
    from analysis.service import is_trading_day  # noqa: F401  (口径一致性；对账本身不看交易日)

    now = datetime.now()
    month = now.strftime("%Y-%m")

    changes_log = _load_json(CHANGE_LOG, [])
    trades = _load_json(TRADES_FILE, [])
    smap = _load_json(MAP_FILE, {})
    if not changes_log:
        print("⚠️ param_change_log 为空，退出")
        return 1

    # ---- 1. 调参概览 ----
    batch_dates = [c.get("date", "") for c in changes_log]
    touched = {}  # code -> [调参日...]
    for c in changes_log:
        for code in (c.get("changes") or {}):
            touched.setdefault(code, []).append(c.get("date", ""))
    total_codes = len(touched)

    # ---- 2. 成交归因（调参日之后、且发生在该股票上的成交） ----
    trades.sort(key=lambda t: t.get("date", ""))
    suspicious = [t for t in trades if "伪造" in (t.get("note") or "") or "回补" in (t.get("note") or "")]
    rows = []  # 每笔成交的归因
    for t in trades:
        code = t.get("symbol", "")
        ch_dates = touched.get(code, [])
        last_chg = max([d for d in ch_dates if d <= t.get("date", "")], default=None)
        if last_chg:
            days_after = (datetime.strptime(t["date"], "%Y-%m-%d")
                          - datetime.strptime(last_chg, "%Y-%m-%d")).days
        else:
            days_after = None
        rows.append({
            "date": t.get("date"), "code": code, "name": t.get("name", ""),
            "action": t.get("action"), "pnl_pct": t.get("pnl_pct", 0),
            "strategy": t.get("strategy", "?"), "assigned_now": smap.get(code, "?"),
            "last_change_before": last_chg, "days_after": days_after,
            "suspicious": "伪造" in (t.get("note") or "") or "回补" in (t.get("note") or ""),
        })

    traded_codes = {r["code"] for r in rows if r["last_change_before"]}
    covered = len(traded_codes & set(touched))
    coverage = covered / total_codes * 100 if total_codes else 0.0

    # ---- 3. 按策略统计（剔除可疑成交） ----
    by_strategy = {}
    for r in rows:
        if r["suspicious"]:
            continue
        s = by_strategy.setdefault(r["strategy"], {"n": 0, "pnl": 0.0, "wins": 0})
        s["n"] += 1
        s["pnl"] += r.get("pnl_pct") or 0
        if (r.get("pnl_pct") or 0) > 0:
            s["wins"] += 1

    # ---- 4. 权益曲线尾部快照 ----
    equity_tail = None
    try:
        with open(EQUITY_FILE, encoding="utf-8") as f:
            erows = list(csv.DictReader(f))
            if erows:
                equity_tail = erows[-1]
    except Exception:  # noqa: BLE001
        pass

    # ---- 报告 ----
    L = [f"# 调参×执行月度对账 {month}", ""]
    L.append(f"- 调参批次: {len(changes_log)} 次（{min(batch_dates)} ~ {max(batch_dates)}）")
    L.append(f"- 被调参股票: {total_codes} 只 | 调参后有过成交的: {covered} 只（覆盖率 {coverage:.1f}%）")
    L.append(f"- 成交笔数(全历史): {len(trades)} | 其中标记可疑/回补: {len(suspicious)} 笔（不计入策略统计）")
    if coverage < 20:
        L.append(f"- 🔴 **执行链健康度差**：覆盖率 {coverage:.1f}% < 20%，"
                 f"调参有效性基本不可验证——信号与模拟盘执行链路需要专门治理，"
                 f"先检查 executor_bridge / 信号过滤条件，而不是继续调参。")
    L.append("")
    L.append("## 成交归因明细")
    L.append("| 日期 | 股票 | 动作 | 策略(当时) | 当前分配 | 距最近调参 | 盈亏% | 备注 |")
    L.append("|---|---|---|---|---|---|---:|---|")
    for r in rows:
        note = "⚠️可疑" if r["suspicious"] else ""
        L.append(f"| {r['date']} | {r['name']}({r['code']}) | {r['action']} | {r['strategy']} "
                 f"| {r['assigned_now']} | {r['days_after'] if r['days_after'] is not None else '-'}天 "
                 f"| {r['pnl_pct']:+.2f}% | {note} |")
    L.append("")
    L.append("## 按策略统计（剔可疑）")
    if by_strategy:
        for s, v in sorted(by_strategy.items(), key=lambda kv: -kv[1]["n"]):
            wr = v["wins"] / v["n"] * 100 if v["n"] else 0
            L.append(f"- {s}: {v['n']}笔, 胜率{wr:.0f}%, 累计{v['pnl']:+.2f}%"
                     + ("（样本<3，仅参考）" if v["n"] < 3 else ""))
    else:
        L.append("- （无可统计成交）")
    if equity_tail:
        L.append("")
        L.append(f"## 权益曲线尾部")
        L.append(f"- {equity_tail.get('date')}: 总值 ¥{equity_tail.get('total_value')} "
                 f"({equity_tail.get('total_return_pct')}%) | 持仓 {equity_tail.get('positions_held')}")

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"param_exec_reconciliation_{month}.md")
    _bak(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"📄 已写 {out_path}")

    # ---- Telegram 摘要 ----
    brief = [f"📊 调参×执行月度对账 {month}",
             f"调参 {len(changes_log)} 批 / {total_codes} 只 | 调参后有成交: {covered} 只（{coverage:.1f}%）",
             f"成交 {len(trades)} 笔（可疑标记 {len(suspicious)}）"]
    for s, v in sorted(by_strategy.items(), key=lambda kv: -kv[1]["n"])[:5]:
        brief.append(f"  {s}: {v['n']}笔 累计{v['pnl']:+.2f}%")
    if coverage < 20:
        brief.append("🔴 执行链覆盖率<20%，调参有效性不可验证（详见月度对账报告）")
    try:
        from send_telegram import send_with_retry
        res = send_with_retry("\n".join(brief), parse_mode="")
        print(f"Telegram 摘要 ok={res.get('ok')}")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"⚠️ 推送失败(报告已落盘): {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
