#!/usr/bin/env python3
"""
调参赛后验证与回滚建议 (C方案 · Harness "验证反馈回路")
==========================================================
补上"回测调参"闭环里缺失的一环——**事后实际验证**:

  现有闭环: 周五回测发现历史最优 → 改参数(adaptive_params.json) → 下周一用新参数交易
  C方案补上: 两周后回溯 —— 这次调参到底有没有让模拟盘真正变好? 没有就建议回滚。

三类产出:
  1. 调参决策日志   data/param_change_log.json   — weekly cron 调参时写"本次改了哪些股(策略/参数)"
  2. 赛后评估        — 用 portfolio_sim_trades.json 的真实成交流水, 对比被调参股票在调参后的实际盈亏
  3. 回滚建议        — 对恶化/低置信的调参给出"建议回滚到 prev 参数"清单(由 agent 决定, 不自动改)

用法:
  python3 analysis/param_change_log.py --backup    # weekly cron 步骤2后记录本次调参变更(推荐由 agent 手动调, 或传参)
  python3 analysis/param_evaluate.py [--since 2026-08-04]   # 评估某日之后调参股票的实际表现
输出:
  - stdout 可读评估报告
  - 落盘 analysis/daily/YYYY-MM-DD_param_eval.md
  - stdout PARAM_EVAL_RESULT JSON 供 agent 推送
"""

import os, json, sys, re
from datetime import datetime, timedelta

WORKSPACE = "/Users/duguke/.openclaw/workspace"
DATA_DIR = os.path.join(WORKSPACE, "data")
DAILY_DIR = os.path.join(WORKSPACE, "analysis", "daily")

NEW_FILE = os.path.join(DATA_DIR, "adaptive_params.json")
PREV_FILE = os.path.join(DATA_DIR, "adaptive_params_prev.json")
TRADES_FILE = os.path.join(DATA_DIR, "portfolio_sim_trades.json")
CHANGE_LOG_FILE = os.path.join(DATA_DIR, "param_change_log.json")


# ──────────────────────────────────────────
# 1. 调参决策日志
# ──────────────────────────────────────────

def backup_diff_to_log():
    """weekly cron 步骤2后调用: 对比 prev vs 新的 adaptive_params.json, 记录本次调参变更。"""
    changes = {}
    new_params = {}
    prev_params = {}

    if os.path.exists(NEW_FILE):
        with open(NEW_FILE) as f:
            new_params = json.load(f).get("stocks", {})
    if os.path.exists(PREV_FILE):
        with open(PREV_FILE) as f:
            prev_params = json.load(f).get("stocks", {})

    all_syms = set(new_params) | set(prev_params)
    for sym in sorted(all_syms):
        new_info = new_params.get(sym, {})
        prev_info = prev_params.get(sym, {})
        new_details = new_info.get("details", {}) if isinstance(new_info, dict) else {}
        prev_details = prev_info.get("details", {}) if isinstance(prev_info, dict) else {}
        # 简化为比较最优(策略,参数) — 取每策略 best_params
        def best(sym_details):
            out = {}
            for k, v in sym_details.items():
                if isinstance(v, dict) and "best_params" in v:
                    out[k] = {"params": v.get("best_params"), "score": v.get("best_score")}
            return out
        nb, pb = best(new_details), best(prev_details)
        if nb != pb:
            changes[sym] = {"prev": pb, "new": nb}

    log = []
    if os.path.exists(CHANGE_LOG_FILE):
        try:
            with open(CHANGE_LOG_FILE) as f:
                log = json.load(f)
        except Exception:
            log = []

    entry = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "changed_count": len(changes),
        "changes": changes,
    }
    if changes:
        log.append(entry)
        # 只保留最近12条
        log = log[-12:]
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CHANGE_LOG_FILE, "w") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    print(f"📝 调参变更日志: 本次 {len(changes)} 只股票参数/策略有变")
    for sym, c in list(changes.items())[:20]:
        print(f"  {sym}: {len(c['prev'])}->{len(c['new'])} 策略集变更")
    return entry


# ──────────────────────────────────────────
# 2. 赛后评估
# ──────────────────────────────────────────

def load_trades():
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE) as f:
                return json.load(f)
        except Exception:
            return []
    return []


def load_change_log():
    if os.path.exists(CHANGE_LOG_FILE):
        try:
            with open(CHANGE_LOG_FILE) as f:
                return json.load(f)
        except Exception:
            return []
    return []


def symbol_name_map():
    # 从自适应参数文件取股票名 (new 优先, 缺失时回退 prev)
    m = {}
    for path in (NEW_FILE, PREV_FILE):
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                for sym, info in data.get("stocks", {}).items():
                    if sym in m:
                        continue  # new 优先, 不覆盖
                    if isinstance(info, dict) and info.get("name"):
                        m[sym] = info["name"]
            except Exception:
                pass
    return m


def _load_stocks_map(path):
    """读取 adaptive_params json 的 stocks 字典, 失败/不存在返回空 dict。"""
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f).get("stocks", {}) or {}
        except Exception:
            pass
    return {}


def _effective_param(sym_info):
    """取一只股票『顶部实际生效』的策略名 + 参数。
    若未指定 strategy 或 params 非 dict, 返回 None(视为「未实质调参」)。"""
    if not isinstance(sym_info, dict):
        return None
    strat = sym_info.get("strategy")
    params = sym_info.get("params")
    if not strat or not isinstance(params, dict):
        return None
    # 参数键排序, 保证 {'a':1,'b':2} 与 {'b':2,'a':1} 视为相同
    return (str(strat),), tuple(sorted((str(k), str(v)) for k, v in params.items()))


def effectively_changed_symbols():
    """对比 prev vs new 顶部 strategy+params, 返回『参数实质改变』的股票集合。
    仅在两侧都存在且能取到生效参数时比较; 一侧缺失则视为「无法确认」返回 None,
    由调用方决定(保守起见不误报为实质变更)。"""
    prev = _load_stocks_map(PREV_FILE)
    new = _load_stocks_map(NEW_FILE)
    changed = set()
    for sym in set(prev) | set(new):
        p = _effective_param(prev.get(sym))
        n = _effective_param(new.get(sym))
        if p is None and n is None:
            continue          # 两侧都未指定 → 视为未实质调参
        if p is not None and n is not None:
            if p != n:
                changed.add(sym)
            # 两侧一致 → 未实质变更, 不加入
        else:
            # 一侧有、一侧无 → 无法可靠判定, 加个占位由 evaluate 决定
            changed.add(sym)
    return changed


def evaluate(since=None):
    """评估 since(默认2周前)之后, 被调参股票在模拟盘中的实际交易表现。"""
    if since is None:
        since = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")

    names = symbol_name_map()
    trades = load_trades()
    change_log = load_change_log()

    # ⭐ 过滤「参数实质未变」股票: 用 prev vs new 顶部生效 strategy+params 做实质对比,
    #    仅对真正改参数/换策略的股票纳入赛后评估。
    #    避免 change_log 中因冗余 best_params/策略集数量变化而误报(如某股 details.best_params
    #    变了但顶部生效 strategy+params 不变) → 导致「没改参数却报恶化→建议回滚」的误判。
    eff_changed = effectively_changed_symbols()

    # 收集该时段内被调参且「参数实质变更」的股票
    tuned_symbols = set()
    for entry in change_log:
        if entry.get("date", "") >= since:
            tuned_symbols.update(entry.get("changes", {}).keys())

    if eff_changed is not None:
        # 仅保留实质变更 + 有成交记录(在观察池内)的股票; 无实质变更则本轮无可验证
        tuned_symbols = tuned_symbols & eff_changed

    # 从成交流水统计每只股票该时段的实际盈亏
    by_symbol = {}
    for t in trades:
        sym = t.get("symbol", "")
        if t.get("date", "") < since:
            continue
        by_symbol.setdefault(sym, {"trades": [], "realized_pl": 0.0, "pnl_pct_sum": 0.0, "count": 0})
        by_symbol[sym]["trades"].append(t)
        by_symbol[sym]["realized_pl"] += t.get("pnl", 0)
        by_symbol[sym]["pnl_pct_sum"] += t.get("pnl_pct", 0)
        by_symbol[sym]["count"] += 1

    # 评估被调参股票
    eval_rows = []
    for sym in sorted(tuned_symbols):
        info = by_symbol.get(sym)
        name = names.get(sym, sym)
        if not info or info["count"] == 0:
            # 调参后无交易 → 无法验证, 标记观察中
            eval_rows.append({
                "symbol": sym, "name": name,
                "verdict": "观察中", "severity": "LOW",
                "realized_pl": 0.0, "trade_count": 0,
                "msg": f"{name}({sym}) 调参后{since}以来无成交, 尚无法验证调参效果, 继续观察",
            })
            continue
        pl = info["realized_pl"]
        count = info["count"]
        avg_pct = round(info["pnl_pct_sum"] / count, 2) if count else 0
        if pl > 0:
            verdict, sev = "有效", "LOW"
        elif pl == 0:
            verdict, sev = "持平", "LOW"
        else:
            verdict, sev = "恶化", "HIGH"
        eval_rows.append({
            "symbol": sym, "name": name,
            "verdict": verdict, "severity": sev,
            "realized_pl": round(pl, 2), "trade_count": count,
            "avg_pnl_pct": avg_pct,
            "msg": f"{name}({sym}) 调参后{since}以来 {count} 笔成交, 已实现盈亏 ¥{pl:.2f} (均{avg_pct:+.2f}%) → {verdict}",
        })

    # 回滚建议: 恶化 或 高置信度下仍无改善
    rollback = [r for r in eval_rows if r["verdict"] == "恶化"]
    # 观察中但调参超过2周仍无交易 → 提示资源浪费/可能无效
    stale_observe = [r for r in eval_rows if r["verdict"] == "观察中"]

    output = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "since": since,
        "tuned_count": len(tuned_symbols),
        "traded_count": len([r for r in eval_rows if r["trade_count"] > 0]),
        "effective": len([r for r in eval_rows if r["verdict"] == "有效"]),
        "deteriorated": len([r for r in eval_rows if r["verdict"] == "恶化"]),
        "flat": len([r for r in eval_rows if r["verdict"] == "持平"]),
        "observing": len([r for r in eval_rows if r["verdict"] == "观察中"]),
        "evaluations": eval_rows,
        "rollback_recommendation": rollback,
        "stale_observe": stale_observe,
    }
    return output


def format_eval(out):
    lines = []
    lines.append(f"🔄 **调参赛后验证** ({out['since']} 以来)")
    lines.append(f"被调参 {out['tuned_count']} 只 | 有成交 {out['traded_count']} | ✅有效{out['effective']} ⚠️恶化{out['deteriorated']} ⚪持平{out['flat']} 🔭观察中{out['observing']}")
    lines.append("")
    if out["evaluations"]:
        for r in out["evaluations"]:
            tag = {"有效": "🟢", "持平": "⚪", "恶化": "🔴", "观察中": "🔭"}.get(r["verdict"], "⚪")
            lines.append(f"{tag} {r['msg']}")
    else:
        lines.append("暂无已到期的调参待验证。")
    lines.append("")
    if out["rollback_recommendation"]:
        lines.append("⚠️ **建议回滚**(调参后实际恶化):")
        for r in out["rollback_recommendation"]:
            lines.append(f"  - {r['name']}({r['symbol']}): {r['msg']}")
        lines.append("  请 agent 结合当前行情决定是否把该股参数回滚到 adaptive_params_prev.json 版本。")
    elif not out["stale_observe"] and out["tuned_count"] > 0:
        lines.append("✅ 无恶化股票, 无需回滚。")
    lines.append("")
    lines.append("⚠️ 说明: 本段由独立脚本基于模拟盘成交流水生成, 未受分析师主观影响。")
    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", action="store_true", help="记录本次调参变更到日志(weekly cron步骤2后调用)")
    parser.add_argument("--since", default=None, help="评估起始日期 YYYY-MM-DD, 默认14天前")
    args = parser.parse_args()

    if args.backup:
        backup_diff_to_log()
        return

    since = args.since
    out = evaluate(since)
    report = format_eval(out)

    print(report)
    print("\n=====PARAM_EVAL_RESULT=====")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("=====PARAM_EVAL_END=====")

    today = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(DAILY_DIR, exist_ok=True)
    with open(os.path.join(DAILY_DIR, f"{today}_param_eval.md"), "w") as f:
        f.write(f"# 调参赛后验证 {today}\n\n")
        f.write(report)
    print(f"\n📄 评估报告已保存: {os.path.join(DAILY_DIR, f'{today}_param_eval.md')}")


if __name__ == "__main__":
    main()
