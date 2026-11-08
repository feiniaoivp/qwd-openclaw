#!/usr/bin/env python3
"""
执行审计模块 v1.1 (Execution Audit P0, 2026-11-01 落地)
========================================================
源自 2026-10-28 教训：「执行率 < 100% = 无系统」——信号发出了但未执行
（锚定成本/等确认/健康度乘数合理化不作为），必须让执行缺口每日可见。

审计口径（v1.1，10-28 实证校准）：
  持仓标记（held at D 开盘）:
    - 权威口径: arbitration_{D} 中 stop_loss 非空（030 仲裁仅为真实持仓 shares>0
      计算止损，2026-10-29 幽灵持仓过滤后可靠；此前日期含幽灵污染，报告标注降级）。
    - 不用成交档案重建: 实证 SELL 单 15/15 shares=0（平仓量不入档），重建必虚增。
  应执行项（actionable）:
    - 卖侧: 裁决 强制清仓/卖出/减仓 且持仓标记非空
    - 买侧: 裁决 买入/小仓买入/积极买入 且 position_mult > 0
  已执行匹配（10-28 校准: SELL 单 shares 恒为 0，匹配不看数量）:
    - 卖侧: 当日存在同 symbol 的 SELL 成交且 note 不含「冲销」
    - 买侧: 当日存在同 symbol 的 BUY 成交且 shares>0 且 note 不含「冲销」
  透明行（不计执行率，供复盘）:
    - 持仓股在前交易日 dual 日报/当日日报有「🔴卖出」信号，但仲裁未转卖出
      （10-28 越秀事故形态: 10-27 日报纯MACD卖出 → 10-28 未减仓）
    - 系统拦截买入（mult=0）
产出: data/runtime/execution_audit/execution_audit_{D}.json + analysis/daily/{D}_execution_audit.md
"""

import os
import sys
import re
import json
import glob
import shutil
import logging
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

log = logging.getLogger("execution_audit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DATA_DIR = os.path.join(WORKSPACE, "data")
TRADES_FILE = os.path.join(DATA_DIR, "portfolio_sim_trades.json")
OUT_DIR = os.path.join(DATA_DIR, "execution_audit")
DAILY_DIR = os.path.join(WORKSPACE, "analysis", "daily")

SELL_ACTIONS = {"强制清仓", "卖出", "减仓"}
BUY_ACTIONS = {"买入", "小仓买入", "积极买入"}
GHOST_FIX_DATE = "2026-10-29"  # 仲裁幽灵持仓过滤上线日（f470472），此前持仓标记含幽灵风险


def _bak(path: str):
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
    except Exception as e:  # noqa: BLE001
        log.warning("写前备份失败(不阻断) %s: %s", path, e)


def load_trades() -> list:
    if not os.path.exists(TRADES_FILE):
        log.warning("成交档案缺失: %s", TRADES_FILE)
        return []
    with open(TRADES_FILE, encoding="utf-8") as f:
        trades = json.load(f)
    trades.sort(key=lambda t: (t.get("date", ""), t.get("idx", 0)))
    return trades


def prev_trading_day(date: str):
    """SSOT 交易日历取前一个交易日；不可得时回退自然日-1（标注降级）"""
    try:
        from analysis.service import get_trading_days
        days = get_trading_days(min_date="2024-01-01")
        if days:
            prior = [d for d in days if d < date]
            return (prior[-1] if prior else None), "calendar"
    except Exception as e:  # noqa: BLE001
        log.warning("交易日历不可得(降级自然日-1): %s", e)
    from datetime import timedelta
    return (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d"), "fallback"


def parse_dual_sells(dual_path: str) -> dict:
    """解析 dual 日报表格中的 🔴卖出 信号: symbol -> [strategy, ...]（缺文件返回空 dict）"""
    sells = {}
    if not os.path.exists(dual_path):
        return sells
    try:
        with open(dual_path, encoding="utf-8") as f:
            text = f.read()
    except Exception as e:  # noqa: BLE001
        log.warning("dual 日报读取失败 %s: %s", dual_path, e)
        return sells
    for m in re.finditer(r"\|\s*([^|()]+?)\((\d{6})\)\s*\|([^|\n]*🔴卖出[^|\n]*)\|", text):
        name, sym, cell = m.group(1).strip(), m.group(2), m.group(3)
        strategies = re.findall(r"([A-Za-z0-9+/]+)\s*🔴卖出", cell)
        if strategies:
            sells.setdefault(sym, {"name": name, "strategies": []})
            sells[sym]["strategies"].extend(strategies)
    return sells


def held_marker_from_arbitration(arbitration: list) -> dict:
    """持仓标记: stop_loss 非空 ⟺ 真实持仓（10-29 幽灵过滤后的仲裁口径）"""
    return {r.get("symbol"): r.get("stop_loss")
            for r in (arbitration or []) if r.get("stop_loss")}


def audit(date: str) -> dict:
    arb_path = os.path.join(DATA_DIR, f"arbitration_{date}.json")
    if not os.path.exists(arb_path):
        log.warning("仲裁档案缺失: %s", arb_path)
        return {"date": date, "error": "arbitration 档案缺失，无法审计"}
    with open(arb_path, encoding="utf-8") as f:
        arbitration = json.load(f)

    trades = load_trades()
    day_trades = [t for t in trades if t.get("date") == date]

    held = held_marker_from_arbitration(arbitration)
    held_quality = ("reliable" if date >= GHOST_FIX_DATE else
                    "ghost-risk（仲裁幽灵过滤 10-29 上线前，卖侧应执行项可能含幽灵持仓）")

    prev_day, cal_mode = prev_trading_day(date)
    dual_sells = {}
    for p in (os.path.join(DAILY_DIR, f"{prev_day}_dual.md"),
              os.path.join(DAILY_DIR, f"{date}_dual.md")):
        for sym, info in parse_dual_sells(p).items():
            entry = dual_sells.setdefault(sym, {"name": info["name"], "strategies": [], "sources": []})
            entry["strategies"].extend(info["strategies"])
            entry["sources"].append(os.path.basename(p))

    result = classify_audit(arbitration, held, day_trades, dual_sells)
    result.update({
        "date": date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "held_marker": held,
        "held_marker_quality": held_quality,
        "signal_basis": {"prev_trading_day": prev_day, "calendar_mode": cal_mode,
                         "dual_sell_stocks": sorted(dual_sells.keys())},
        "trades_count": len([t for t in day_trades if "冲销" not in (t.get("note") or "")]),
    })
    _write_outputs(date, result)
    return result


def classify_audit(arbitration: list, held: dict, day_trades: list, dual_sells: dict) -> dict:
    """纯逻辑分类（可对抗自测）。held: {symbol: stop_loss}；dual_sells: {symbol: {name, strategies, sources}}"""
    executed_by_sym = {}
    for t in day_trades:
        # 冲销单不算已执行；SELL 不看 shares（平仓量不入档），BUY 需 shares>0
        if "冲销" in (t.get("note") or ""):
            continue
        action = (t.get("action") or "").upper()
        if action == "SELL":
            executed_by_sym.setdefault(t.get("symbol"), []).append("SELL")
        elif action == "BUY" and (t.get("shares", 0) or 0) > 0:
            executed_by_sym.setdefault(t.get("symbol"), []).append("BUY")

    actionable, blocked_buys, transparency = [], [], []
    for r in arbitration or []:
        sym = r.get("symbol")
        name = r.get("name", "")
        act = r.get("final_action", "")
        mult = r.get("position_mult", 0) or 0
        is_held = sym in held

        if act in SELL_ACTIONS and is_held:
            done = "SELL" in executed_by_sym.get(sym, [])
            actionable.append({
                "kind": "sell", "symbol": sym, "name": name, "action": act,
                "stop_loss": held.get(sym), "executed": done,
                "status": "已执行" if done else "未执行-纪律未执行",
                "reason": r.get("reason", ""),
            })
        elif act in BUY_ACTIONS and mult > 0:
            done = "BUY" in executed_by_sym.get(sym, [])
            actionable.append({
                "kind": "buy", "symbol": sym, "name": name, "action": act,
                "position_mult": mult, "executed": done,
                "status": "已执行" if done else "未执行-纪律未执行",
                "reason": r.get("reason", ""),
            })
        elif act in BUY_ACTIONS and mult <= 0:
            blocked_buys.append({"symbol": sym, "name": name, "action": act,
                                 "reason": r.get("reason", ""),
                                 "resonance_gate": r.get("resonance_gate"),
                                 "trace_stages": [t.get("stage") for t in (r.get("decision_trace") or [])
                                                  if isinstance(t, dict) and t.get("stage")]})

        # 透明行: 持仓股有日报🔴卖出信号但仲裁未转卖出（10-28 越秀形态）
        # B阶段 2026-11-01: 附仲裁 reason + 决策轨迹关键阶段 → 透明行能回答「为什么没转卖」
        if is_held and act not in SELL_ACTIONS and sym in dual_sells:
            info = dual_sells[sym]
            transparency.append({
                "type": "日报卖出未转仲裁", "symbol": sym, "name": name or info["name"],
                "arbitration": act,
                "arb_reason": r.get("reason", ""),
                "arb_trace_stages": [t.get("stage") for t in (r.get("decision_trace") or [])
                                     if isinstance(t, dict) and t.get("stage")],
                "sell_strategies": sorted(set(info["strategies"])),
                "sources": info["sources"],
            })

    n_actionable = len(actionable)
    n_exec = sum(1 for a in actionable if a["executed"])
    exec_rate = round(n_exec / n_actionable, 3) if n_actionable else None

    return {
        "actionable": actionable,
        "blocked_buys": blocked_buys,
        "transparency": transparency,
        "n_actionable": n_actionable,
        "n_executed": n_exec,
        "execution_rate": exec_rate,
        "verdict": ("无应执行项" if n_actionable == 0
                    else ("执行率 100% ✅" if n_exec == n_actionable
                          else f"⚠️ 执行率 {n_exec}/{n_actionable} —— 执行率 < 100% = 无系统")),
    }


def _write_outputs(date: str, result: dict):
    os.makedirs(OUT_DIR, exist_ok=True)
    jpath = os.path.join(OUT_DIR, f"execution_audit_{date}.json")
    _bak(jpath)
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, default=str)
    mpath = os.path.join(DAILY_DIR, f"{date}_execution_audit.md")
    _bak(mpath)
    with open(mpath, "w", encoding="utf-8") as f:
        f.write(render_brief(result))


def render_brief(result: dict) -> str:
    d = result.get("date", "?")
    lines = [f"📋 执行审计 {d}", ""]
    if result.get("error"):
        lines.append(f"❌ {result['error']}")
        return "\n".join(lines) + "\n"
    quality = result.get("held_marker_quality", "")
    q_flag = "" if quality == "reliable" else " ⚠️持仓口径降级"
    lines.append(f"持仓标记: {len(result.get('held_marker') or {})} 只{q_flag} | "
                 f"当日成交: {result.get('trades_count', 0)} 笔 | "
                 f"信号基底: {result.get('signal_basis', {}).get('prev_trading_day', '?')} 日报")
    lines.append(f"结论: {result.get('verdict', '?')}")
    lines.append("")
    if result.get("actionable"):
        lines.append("| 标的 | 方向 | 裁决 | 状态 |")
        lines.append("|---|---|---|---|")
        for a in result["actionable"]:
            kind = "卖" if a["kind"] == "sell" else "买"
            lines.append(f"| {a['name']}({a['symbol']}) | {kind} | {a['action']} | {a['status']} |")
        lines.append("")
    if result.get("transparency"):
        lines.append("**透明行（供复盘，不计执行率）**:")
        for t in result["transparency"]:
            why = (t.get("arb_reason") or "").strip()
            stages = [s for s in (t.get("arb_trace_stages") or [])
                      if s in ("REVERSAL_OVERRIDE", "RESONANCE_GATE_BLOCK", "RESONANCE_GATE_FAIL_OPEN",
                               "CONSECUTIVE_SELL_HARD_RULE", "DEFAULT_HOLD", "DOWNGRADE_NOTE")]
            why_s = f" 原因:{why[:70]}" if why else ""
            stage_s = f" 轨迹:{'>'.join(stages[-3:])}" if stages else ""
            lines.append(f"- 🟡 {t['name']}({t['symbol']}): 日报🔴卖出({','.join(t['sell_strategies'])}) "
                         f"但仲裁={t['arbitration']}{why_s}{stage_s} [{','.join(t['sources'])}]")
        lines.append("")
    if result.get("blocked_buys"):
        lines.append(f"系统拦截买入: {len(result['blocked_buys'])} 只（mult=0，不计应执行项）")
        for b in result["blocked_buys"][:5]:
            gate = b.get("resonance_gate")
            gate_s = f" 门控:{gate}" if gate else ""
            stages = [s for s in (b.get("trace_stages") or [])
                      if s in ("RESONANCE_GATE_BLOCK", "RESONANCE_GATE_FAIL_OPEN", "POSITION_MULT")]
            stage_s = f" 轨迹:{'>'.join(stages[-2:])}" if stages else ""
            lines.append(f"  - 🔵 {b['name']}({b['symbol']}): {b['action']} 原因:{(b.get('reason') or '')[:60]}{gate_s}{stage_s}")
    return "\n".join(lines) + "\n"


# ══════════════════════════════════════
# 对抗自测（无 IO）
# ══════════════════════════════════════

def _selftest() -> int:
    def arb(sym, name, act, mult, stop=None):
        return {"symbol": sym, "name": name, "final_action": act,
                "position_mult": mult, "stop_loss": stop, "reason": "test"}

    held = {"000001": 6.5}  # 持仓标记形态
    dual = {}

    # 场景1: 卖出+有仓标记+当日有 SELL 成交(shares=0 平仓单) -> 已执行
    r = classify_audit([arb("000001", "甲", "卖出", 0.0, 6.5)], held,
                       [{"symbol": "000001", "action": "SELL", "shares": 0}], dual)
    t1 = r["actionable"][0]["status"] == "已执行" and r["execution_rate"] == 1.0
    # 场景2: 卖出+有仓+无成交 -> 纪律未执行
    r = classify_audit([arb("000001", "甲", "减仓", 0.0, 6.5)], held, [], dual)
    t2 = r["actionable"][0]["status"] == "未执行-纪律未执行" and r["execution_rate"] == 0.0
    # 场景3: 卖出+无仓标记 -> 不算应执行项
    r = classify_audit([arb("000002", "乙", "卖出", 0.0, 9.9)], {"000009": 1.0}, [], dual)
    t3 = r["n_actionable"] == 0 and r["verdict"] == "无应执行项"
    # 场景4: 买入 mult>0 未成交 -> 应执行未执行
    r = classify_audit([arb("000003", "丙", "买入", 0.7)], held, [], dual)
    t4 = r["actionable"][0]["status"] == "未执行-纪律未执行"
    # 场景5: 买入 mult=0 -> 系统拦截，非应执行项
    r = classify_audit([arb("000003", "丙", "买入", 0.0)], held, [], dual)
    t5 = r["n_actionable"] == 0 and len(r["blocked_buys"]) == 1
    # 场景6: 持仓股有日报🔴卖出但仲裁=持有 -> 透明行（越秀形态）
    dsells = {"000001": {"name": "甲", "strategies": ["纯MACD"], "sources": ["x_dual.md"]}}
    r = classify_audit([arb("000001", "甲", "持有", 0.0, 6.5)], held, [], dsells)
    t6 = len(r["transparency"]) == 1 and r["transparency"][0]["arbitration"] == "持有"
    # 场景7: 冲销单不算已执行
    r = classify_audit([arb("000001", "甲", "卖出", 0.0, 6.5)], held,
                       [{"symbol": "000001", "action": "SELL", "shares": 0, "note": "冲销"}], dual)
    t7 = r["actionable"][0]["executed"] is False
    # 场景8: 透明行附仲裁原因+轨迹阶段（B阶段 2026-11-01 决策留痕契约）
    arb_with_trace = {"symbol": "000001", "name": "甲", "final_action": "持有",
                      "position_mult": 0.0, "stop_loss": 6.5, "reason": "情绪反转确认覆盖技术卖出",
                      "decision_trace": [{"stage": "REVERSAL_OVERRIDE", "confirmed_date": "2026-10-28"},
                                          {"stage": "POSITION_MULT", "final": 0.0}]}
    r = classify_audit([arb_with_trace], held, [], dsells)
    t8 = (len(r["transparency"]) == 1
          and r["transparency"][0].get("arb_reason") == "情绪反转确认覆盖技术卖出"
          and "REVERSAL_OVERRIDE" in r["transparency"][0].get("arb_trace_stages", []))
    # 场景9: 旧格式仲裁记录（无 decision_trace 字段）不报错、正常分类（向后兼容）
    legacy = {"symbol": "000001", "name": "甲", "final_action": "持有",
              "position_mult": 0.0, "stop_loss": 6.5, "reason": "旧记录"}
    r = classify_audit([legacy], held, [], dsells)
    t9 = len(r["transparency"]) == 1 and r["transparency"][0].get("arb_trace_stages") == []

    cases = [("卖出已执行(零股平仓单)", t1), ("卖出未执行", t2), ("无仓标记不算应执行", t3),
             ("买入未执行", t4), ("系统拦截透明", t5), ("日报卖出未转仲裁", t6), ("冲销单排除", t7),
             ("透明行附原因+轨迹", t8), ("旧格式仲裁向后兼容", t9)]
    failed = [n for n, ok in cases if not ok]
    for n, ok in cases:
        print(f"  {'✅' if ok else '❌'} {n}")
    if failed:
        print(f"SELFTEST FAILED: {failed}")
        return 1
    print("SELFTEST PASSED (9/9)")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--selftest" in args:
        return _selftest()
    date = None
    for a in args:
        if a.startswith("--date="):
            date = a.split("=", 1)[1]
    result = audit(date or datetime.now().strftime("%Y-%m-%d"))
    print(render_brief(result))
    print("\n=====EXECUTION_AUDIT=====")
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    print("=====EXECUTION_AUDIT_END=====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
