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
# TOOLS.md 强制样板: 工作区根 + analysis/ 双插入，保证脚本模式/包模式都能 import 同目录模块
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
DATA_DIR = os.path.join(WORKSPACE, "data")
DAILY_DIR = os.path.join(WORKSPACE, "analysis", "daily")

NEW_FILE = os.path.join(DATA_DIR, "adaptive_params.json")
PREV_FILE = os.path.join(DATA_DIR, "adaptive_params_prev.json")
TRADES_FILE = os.path.join(DATA_DIR, "portfolio_sim_trades.json")
CHANGE_LOG_FILE = os.path.join(DATA_DIR, "param_change_log.json")
MAX_LOG_ENTRIES = 12


# ──────────────────────────────────────
# 0. 变更分类/归因助手 (审计 F1/F2 修复, 2026-10-17)
# ──────────────────────────────────────

def _best_by_strategy(sym_details):
    """提取每策略 best_params+best_score（无则跳过）。"""
    out = {}
    if isinstance(sym_details, dict):
        for k, v in sym_details.items():
            if isinstance(v, dict) and "best_params" in v:
                out[k] = {"params": v.get("best_params"), "score": v.get("best_score")}
    return out


def _params_only(best_map):
    """只取参数（忽略分数），用于判定真参数变化。"""
    return {k: v.get("params") for k, v in (best_map or {}).items()}


def classify_changes(prev_params, new_params):
    """三分类对比（审计 R3）:
      param_changed — 共有策略的 best_params 实际不同（真调参）
      set_changed   — 策略增删（剪枝/新增，同样改变信号行为）
      score_only    — 参数与策略集全同、仅分数漂移（窗口右移所致，非调参效果）
    """
    param_changed, set_changed, score_only = {}, {}, []
    for sym in sorted(set(prev_params) | set(new_params)):
        pb = _best_by_strategy((prev_params.get(sym) or {}).get("details"))
        nb = _best_by_strategy((new_params.get(sym) or {}).get("details"))
        pp, npp = _params_only(pb), _params_only(nb)
        shared = set(pp) & set(npp)
        if any(pp[k] != npp[k] for k in shared):
            # 共有策略的参数变了（含既有策略变参为主、附带增删的情形）
            param_changed[sym] = {"prev": pb, "new": nb}
        elif set(pp) != set(npp):
            # 共有参数全同，仅策略增删（剪枝/新增/全新股票无基线）
            set_changed[sym] = {"prev": pb, "new": nb}
        else:
            # 策略集与参数全同（分数可能漂移）
            score_only.append(sym)
    return {"param_changed": param_changed, "set_changed": set_changed, "score_only": score_only}


def _per_strategy_diff(prev_info, new_info):
    """单只股票的每策略参数差异: 返回 (changed:set, removed:set, added:set)。"""
    pb = _best_by_strategy((prev_info or {}).get("details"))
    nb = _best_by_strategy((new_info or {}).get("details"))
    pp, npp = _params_only(pb), _params_only(nb)
    changed = {k for k in set(pp) & set(npp) if pp[k] != npp[k]}
    removed = set(pp) - set(nb)
    added = set(nb) - set(pp)
    return changed, removed, added


_REPLAY_DF_CACHE = {}


def _strategy_best_params(stocks_map, sym, strat):
    info = stocks_map.get(sym) or {}
    return ((info.get("details") or {}).get(strat) or {}).get("best_params")


def _signal_fired(sym, strat, on_date, params):
    """在 on_date 当日切片上用给定 params 跑信号, 返回 True(有买/卖信号)/False(无)/None(无法验证)。
    近似说明: 用 prev/new 文件参数回放——参数周对周高度收敛(审计 F2), 偏差可忽略。"""
    try:
        import adaptive_dual as ad
        df = _REPLAY_DF_CACHE.get(sym)
        if df is None:
            df = ad.fetch_data(sym, realtime_fallback=False)
            _REPLAY_DF_CACHE[sym] = df
        if df is None or len(df) == 0:
            return None
        sub = df[df["date"].astype(str) <= str(on_date)]
        if len(sub) < 60:
            return None
        fn = ad.SIGNAL_FUNCS.get(strat)
        if fn is None:
            return None
        act = (fn(sub, params) or {}).get("action", "")
        return ("买入" in act) or ("卖出" in act)
    except Exception as e:
        print(f"  ⚠️ 信号回放失败 {sym}/{strat}@{on_date}: {e}")
        return None


# ──────────────────────────────────────────
# 1. 调参决策日志
# ──────────────────────────────────────────

def backup_diff_to_log():
    """weekly cron 步骤5后调用: 对比 prev vs 新的 adaptive_params.json, 记录本次调参变更。
    审计修正(2026-10-17, F2/R3):
      - 三分类(真变参/策略增删/仅分数漂移)，changes 只收前两类 —— 分数漂移是窗口右移，
        不是调参效果；旧口径把分数漂移也记成变更，导致赛后验证误归因
      - 同日去重: tune 一天多跑只保留最后一条
      - 记录基线来源日期(prev 文件 meta.date)，基线无轮转时立刻可见
    """
    new_params = _load_stocks_map(NEW_FILE)
    prev_params = _load_stocks_map(PREV_FILE)
    cls = classify_changes(prev_params, new_params)

    real_changes = {}
    real_changes.update(cls["param_changed"])
    real_changes.update(cls["set_changed"])

    prev_meta_date = None
    try:
        with open(PREV_FILE) as f:
            prev_meta_date = (json.load(f) or {}).get("date")
    except Exception:
        prev_meta_date = None

    today = datetime.now().strftime("%Y-%m-%d")
    log = []
    if os.path.exists(CHANGE_LOG_FILE):
        try:
            with open(CHANGE_LOG_FILE) as f:
                log = json.load(f)
        except Exception:
            log = []
    log = [e for e in log if isinstance(e, dict) and e.get("date") != today]  # 同日去重

    entry = {
        "date": today,
        "prev_baseline_date": prev_meta_date,
        "changed_count": len(real_changes),
        "counts": {
            "param_changed": len(cls["param_changed"]),
            "set_changed": len(cls["set_changed"]),
            "score_only": len(cls["score_only"]),
        },
        "changes": real_changes,
        "score_only_syms": cls["score_only"],
    }
    log.append(entry)
    log = log[-MAX_LOG_ENTRIES:]
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CHANGE_LOG_FILE, "w") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    print(f"📝 调参变更日志: 真变参 {len(cls['param_changed'])} / 策略增删 {len(cls['set_changed'])} / 仅分数漂移 {len(cls['score_only'])} (对照基线 {prev_meta_date or '未知'})")
    for sym, c in list(real_changes.items())[:20]:
        print(f"  {sym}: {len(c['prev'])}->{len(c['new'])} 策略条目")
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

    # ⭐ 逐笔归因(审计 F1/R2, 2026-10-17): 顶部对比(effectively_changed_symbols)只能证明
    #    「top1 换过」，不能证明「这笔交易是新参数做的」——雷科买侧 ema_obv 参数相同、
    #    两笔出场都是止损非信号，旧口径把两笔全记成「恶化」。改为在下方循环内逐笔归因。
    # 收集该时段内被调参的股票(change log 的 changes 自 R3 起只含真变参/策略增删)
    tuned_symbols = set()
    for entry in change_log:
        if entry.get("date", "") >= since:
            tuned_symbols.update(entry.get("changes", {}).keys())

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

    # 评估被调参股票（逐笔归因）
    prev_stocks = _load_stocks_map(PREV_FILE)
    new_stocks = _load_stocks_map(NEW_FILE)
    eval_rows = []
    not_attributable = 0
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
        trades_l = info["trades"]
        changed_s, removed_s, added_s = _per_strategy_diff(prev_stocks.get(sym), new_stocks.get(sym))
        attr_trades, non_attr_n, doubts, reasons = [], 0, 0, []
        for t in trades_l:
            s = t.get("strategy")
            act = t.get("action", "")
            if s in added_s or s in removed_s:
                attr, why = True, "策略增删"
            elif s in changed_s:
                # 真正的因果判据: 新旧参数在当日是否给出**不同**信号
                # (参数变了但当日信号相同 → 交易与调参无关; 审计 F1 招行买侧即此情形)
                of = _signal_fired(sym, s, t.get("date"), _strategy_best_params(prev_stocks, sym, s))
                nf = _signal_fired(sym, s, t.get("date"), _strategy_best_params(new_stocks, sym, s))
                if of is None or nf is None:
                    attr, why = True, "信号回放失败,保守计入"
                    doubts += 1
                elif of != nf:
                    attr, why = True, "调参改变当日信号(旧:%s→新:%s)" % ("有信号" if of else "无", "有信号" if nf else "无")
                else:
                    attr, why = False, "新旧参数当日信号相同(非调参所致)"
            else:
                attr, why = False, "该策略参数未变"
            if attr:
                attr_trades.append((t, why))
            else:
                non_attr_n += 1
            reasons.append(why)
        if not attr_trades:
            not_attributable += 1
            eval_rows.append({
                "symbol": sym, "name": name,
                "verdict": "与调参无关", "severity": "LOW",
                "realized_pl": round(info["realized_pl"], 2), "trade_count": info["count"],
                "msg": f"{name}({sym}) {info['count']} 笔成交均不可归因于调参({'；'.join(sorted(set(reasons)))}), 盈亏 ¥{info['realized_pl']:.2f} 与本次调参无关",
            })
            continue
        pl = sum(t.get("pnl", 0.0) for t, _ in attr_trades)
        count = len(attr_trades)
        avg_pct = round(sum(t.get("pnl_pct", 0.0) for t, _ in attr_trades) / count, 2) if count else 0
        if pl > 0:
            verdict, sev = "有效", "LOW"
        elif pl == 0:
            verdict, sev = "持平", "LOW"
        else:
            verdict, sev = "恶化", "HIGH"
        extra = ""
        if non_attr_n:
            extra += f"；另 {non_attr_n} 笔不可归因已剔除"
        if doubts:
            extra += f"（含 {doubts} 笔出场回放失败保守计入）"
        eval_rows.append({
            "symbol": sym, "name": name,
            "verdict": verdict, "severity": sev,
            "realized_pl": round(pl, 2), "trade_count": count,
            "avg_pnl_pct": avg_pct,
            "msg": f"{name}({sym}) 可归因 {count} 笔成交, 已实现盈亏 ¥{pl:.2f} (均{avg_pct:+.2f}%) → {verdict}{extra}",
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
        "not_attributable": not_attributable,
        "observing": len([r for r in eval_rows if r["verdict"] == "观察中"]),
        "evaluations": eval_rows,
        "rollback_recommendation": rollback,
        "stale_observe": stale_observe,
    }
    return output


def format_eval(out):
    lines = []
    lines.append(f"🔄 **调参赛后验证** ({out['since']} 以来)")
    lines.append(f"被调参 {out['tuned_count']} 只 | 有成交 {out['traded_count']} | ✅有效{out['effective']} ⚠️恶化{out['deteriorated']} ⚪持平{out['flat']} 🔗无关{out.get('not_attributable', 0)} 🔭观察中{out['observing']}")
    lines.append("")
    if out["evaluations"]:
        for r in out["evaluations"]:
            tag = {"有效": "🟢", "持平": "⚪", "恶化": "🔴", "观察中": "🔭", "与调参无关": "🔗"}.get(r["verdict"], "⚪")
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
    lines.append("⚠️ 说明: 本段由独立脚本基于模拟盘成交流水生成, 未受分析师主观影响；盈亏已按『新旧参数当日信号是否不同』逐笔因果归因, 不可归因部分不计入判定、不进回滚建议。")
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
    # 🔴 数据新鲜度标注（供 scripts/data_freshness_guard.py 门禁校验）
    # 本报告评估的是模拟盘成交（截至今日），基准日 since 为调参生效日
    data_date = today
    with open(os.path.join(DAILY_DIR, f"{today}_param_eval.md"), "w") as f:
        f.write(f"# 调参赛后验证 {today}\n\n")
        f.write(f"数据日期: {data_date}\n")
        f.write(f"> 口径：模拟盘成交截至 {data_date}；`{out['since']}` 为调参基准日（评估起点），非数据日期。\n\n")
        f.write(report)
    print(f"\n📄 评估报告已保存: {os.path.join(DAILY_DIR, f'{today}_param_eval.md')}")


if __name__ == "__main__":
    main()
