#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P0 成交真实性对照研究（2026-11-01）
====================================
问题：backtest_strategies 历来在信号日收盘无条件成交，从不检查涨跌停可成交性。
     「EMA金叉在强势股碾压」类结论有多少是涨停板买不进贡献的纸面收益？

三口径对照（全部基于同一份信号 actions，仅成交模拟不同 —— 多臂减同一基准）：
  A = signal_close + 不检查涨跌停   （存量口径 = 基准）
  B = signal_close + 涨跌停检查     （封板涨停拒买 / 封板跌停卖单顺延）
  C = next_open   + 涨跌停检查     （最接近实盘「信号次日开盘执行」纪律）

方法论纪律（.learnings 固化）：
  - 多臂对照减同一个基准 A；arm 间差异只来自成交口径
  - 恒等式自检：A 口径下 fill_stats 必须全零（引擎已单独通过 HEAD 双引擎
    逐位恒等验证），不过则 exit 1 拒绝出结论
  - 聚合均值带 winsorize(±15%)（肥尾面板，10-20 事件研究教训）
  - 结论先看全池中位数，再看逐票离群（防小样本幸存者偏差）

运行（预估 2~5 分钟，走 detached）：
  nohup python3 scripts/fill_impact_study.py > logs/fill_impact_study.log 2>&1 &
输出：
  analysis/backtest/fill_impact_<date>.md / .json（写前 _bak）
  stdout =====FILL_IMPACT_RESULT===== JSON 摘要块
"""

import os
import sys
import json
import shutil
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np

from analysis.backtest_strategies import (
    STOCKS, CANDIDATE_STRATEGIES, fetch_data, run_simulation,
)

CORE_ARMS = ["布林带+ATR", "KDJ+CCI", "EMA+OBV", "EMA12/26金叉(基准C)", "纯MACD(基准B)"]
MODES = [
    ("A", "signal_close", False),
    ("B", "signal_close", True),
    ("C", "next_open", True),
]
FOCUS_NAMES = ["国瓷材料", "中芯国际", "长电科技", "通富微电", "应流股份"]
TODAY = datetime.now().strftime("%Y-%m-%d")
OUT_MD = os.path.join(WORKSPACE, "analysis", "backtest", f"fill_impact_{TODAY}.md")
OUT_JSON = os.path.join(WORKSPACE, "analysis", "backtest", f"fill_impact_{TODAY}.json")


def _bak(path):
    """写前 .bak 备份（backup_guard 铁律；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


def winsor_mean(vals, lim=15.0):
    if not vals:
        return None
    return float(np.mean(np.clip(np.array(vals, dtype=float), -lim, lim)))


def main():
    arm_funcs = {n: f for n, f in CANDIDATE_STRATEGIES}
    missing = [a for a in CORE_ARMS if a not in arm_funcs]
    if missing:
        print(f"❌ 候选臂缺失: {missing}")
        return 1

    rows = []          # 每行: (symbol, name, arm, mode, metrics)
    stock_data = {}    # symbol -> df
    fetched, failed = 0, []
    for idx, (sym, name) in enumerate(STOCKS, 1):
        df = fetch_data(sym, name)
        if df is None or len(df) < 100:
            failed.append(f"{name}({sym})")
            continue
        fetched += 1
        stock_data[sym] = (name, df)
        print(f"[{idx}/{len(STOCKS)}] {name} 数据就绪", flush=True)
        for arm in CORE_ARMS:
            actions = arm_funcs[arm](df)
            for mode_key, fm, el in MODES:
                m = run_simulation(df, actions, symbol=sym, fill_mode=fm, enforce_limit=el)
                if "error" in m:
                    rows.append({"symbol": sym, "name": name, "arm": arm, "mode": mode_key,
                                 "error": m["error"]})
                    continue
                rows.append({
                    "symbol": sym, "name": name, "arm": arm, "mode": mode_key,
                    "ret": m["total_return_pct"], "sharpe": m["sharpe_ratio"],
                    "mdd": m["max_drawdown_pct"], "trades": m["total_trades"],
                    "win_rate": m["win_rate_pct"],
                    "rejected_buys": m["fill_stats"]["rejected_buys"],
                    "blocked_sells": m["fill_stats"]["blocked_sells"],
                    "final_mtm": m["fill_stats"]["final_marked_to_market"],
                })

    if fetched < 30:
        print(f"❌ 数据覆盖不足: 仅 {fetched}/{len(STOCKS)} 只就绪，失败 {failed} —— 拒绝出结论")
        return 1

    # ── 恒等式自检：A 口径 fill_stats 必须全零 ──
    a_rows = [r for r in rows if r.get("mode") == "A" and "error" not in r]
    bad_a = [r for r in a_rows
             if r["rejected_buys"] or r["blocked_sells"] or r["final_mtm"]]
    if bad_a:
        print(f"❌ 恒等式失败: A 口径出现 {len(bad_a)} 行非零 fill_stats（成交规则泄漏进基准口径）")
        for r in bad_a[:5]:
            print("   ", r["name"], r["arm"], r["fill_stats"] if "fill_stats" in r else r)
        return 1
    print(f"✅ 恒等式: A 口径 {len(a_rows)} 行 fill_stats 全零")

    # ── 聚合 ──
    def agg(mode):
        out = {}
        for arm in CORE_ARMS:
            sel = [r for r in rows if r.get("arm") == arm and r.get("mode") == mode
                   and "error" not in r]
            rets = [r["ret"] for r in sel]
            out[arm] = {
                "n": len(sel),
                "mean_ret": round(float(np.mean(rets)), 2) if rets else None,
                "median_ret": round(float(np.median(rets)), 2) if rets else None,
                "winsor_ret": round(winsor_mean(rets), 2) if rets else None,
                "mean_sharpe": round(float(np.mean([r["sharpe"] for r in sel])), 3) if sel else None,
                "mean_mdd": round(float(np.mean([r["mdd"] for r in sel])), 2) if sel else None,
                "mean_trades": round(float(np.mean([r["trades"] for r in sel])), 1) if sel else None,
                "total_rejected_buys": int(sum(r["rejected_buys"] for r in sel)),
                "total_blocked_sells": int(sum(r["blocked_sells"] for r in sel)),
                "total_final_mtm": int(sum(r["final_mtm"] for r in sel)),
            }
        return out

    agg_by_mode = {k: agg(k) for k, _, _ in MODES}

    # ── 逐票 delta（B−A / C−A，减同一基准）──
    def key(sym, arm, mode):
        return (sym, arm, mode)

    lut = {(r["symbol"], r["arm"], r["mode"]): r for r in rows if "error" not in r}
    deltas = []
    for sym, (name, _df) in stock_data.items():
        for arm in CORE_ARMS:
            ra, rb, rc = (lut.get(key(sym, arm, m)) for m in ("A", "B", "C"))
            if not (ra and rb and rc):
                continue
            deltas.append({
                "symbol": sym, "name": name, "arm": arm,
                "ret_A": ra["ret"], "ret_B": rb["ret"], "ret_C": rc["ret"],
                "d_B": round(rb["ret"] - ra["ret"], 2),
                "d_C": round(rc["ret"] - ra["ret"], 2),
                "rejected_buys": rb["rejected_buys"] + rc["rejected_buys"],
                "blocked_sells": rb["blocked_sells"] + rc["blocked_sells"],
            })

    d_ema = [d for d in deltas if d["arm"] == "EMA12/26金叉(基准C)"]
    d_ema_sorted = sorted(d_ema, key=lambda x: x["d_B"])

    # ── 汇总统计 ──
    summary = {}
    for arm in CORE_ARMS:
        ds_b = [d["d_B"] for d in deltas if d["arm"] == arm]
        ds_c = [d["d_C"] for d in deltas if d["arm"] == arm]
        summary[arm] = {
            "n": len(ds_b),
            "d_B_mean": round(float(np.mean(ds_b)), 2),
            "d_B_median": round(float(np.median(ds_b)), 2),
            "d_B_winsor": round(winsor_mean(ds_b), 2),
            "d_B_affected_gt1pp": int(sum(1 for v in ds_b if abs(v) > 1.0)),
            "d_C_mean": round(float(np.mean(ds_c)), 2),
            "d_C_median": round(float(np.median(ds_c)), 2),
            "d_C_winsor": round(winsor_mean(ds_c), 2),
            "d_C_affected_gt1pp": int(sum(1 for v in ds_c if abs(v) > 1.0)),
        }

    # ── 落盘 ──
    payload = {
        "date": TODAY, "modes": {k: {"fill_mode": fm, "enforce_limit": el}
                                 for k, fm, el in MODES},
        "arms": CORE_ARMS, "fetched": fetched, "failed": failed,
        "agg_by_mode": agg_by_mode, "delta_summary": summary,
        "deltas": deltas,
    }
    _bak(OUT_JSON)
    with open(OUT_JSON, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # ── Markdown 报告 ──
    L = []
    L.append(f"# P0 成交真实性对照研究 {TODAY}\n")
    L.append(f"- 数据: {fetched}/{len(STOCKS)} 只前复权日线（DataRouter），失败: {failed or '无'}")
    L.append("- 口径: A=信号日收盘·不限涨跌停(存量基准) | B=信号日收盘·涨跌停检查 | C=信号次日开盘·涨跌停检查")
    L.append("- 同一份信号 actions 三口径重放（多臂减同一基准）；恒等式: A 口径 fill_stats 全零 ✅\n")

    L.append("## 三口径总表（全池均值 | 中位数 | winsor±15%）\n")
    L.append("| 策略 | 口径 | 平均收益% | 中位收益% | winsor收益% | 夏普 | 回撤% | 交易数 | 拒买 | 卖顺延 |")
    L.append("|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
    for arm in CORE_ARMS:
        for mk, _, _ in MODES:
            a = agg_by_mode[mk][arm]
            L.append(f"| {arm} | {mk} | {a['mean_ret']:+.2f} | {a['median_ret']:+.2f} | "
                     f"{a['winsor_ret']:+.2f} | {a['mean_sharpe']:.3f} | {a['mean_mdd']:.1f} | "
                     f"{a['mean_trades']:.1f} | {a['total_rejected_buys']} | {a['total_blocked_sells']} |")

    L.append("\n## 成交口径冲击汇总（d = B或C − A，减同一基准）\n")
    L.append("| 策略 | d(B−A) 均值 | 中位 | winsor | 受影响>1pp | d(C−A) 均值 | 中位 | winsor | 受影响>1pp |")
    L.append("|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
    for arm in CORE_ARMS:
        s = summary[arm]
        L.append(f"| {arm} | {s['d_B_mean']:+.2f} | {s['d_B_median']:+.2f} | {s['d_B_winsor']:+.2f} | "
                 f"{s['d_B_affected_gt1pp']}/{s['n']} | {s['d_C_mean']:+.2f} | {s['d_C_median']:+.2f} | "
                 f"{s['d_C_winsor']:+.2f} | {s['d_C_affected_gt1pp']}/{s['n']} |")

    L.append("\n## EMA12/26金叉 逐票冲击（按 d(B−A) 升序 = 纸面收益缩水最多在前）\n")
    L.append("| 股票 | A% | B% | C% | d(B−A) | d(C−A) | 拒买 | 卖顺延 |")
    L.append("|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
    for d in d_ema_sorted:
        flag = " ⭐" if d["name"] in FOCUS_NAMES else ""
        L.append(f"| {d['name']}{flag} | {d['ret_A']:+.2f} | {d['ret_B']:+.2f} | {d['ret_C']:+.2f} | "
                 f"{d['d_B']:+.2f} | {d['d_C']:+.2f} | {d['rejected_buys']} | {d['blocked_sells']} |")
    L.append("\n⭐ = 07-26 招牌结论点名的强势股样本")

    L.append("\n## 全臂逐票冲击明细\n")
    L.append("见同目录 fill_impact_" + TODAY + ".json 的 deltas 字段（每行含 A/B/C 收益与双向 delta）。\n")
    L.append("---\n方法论: 同一信号三口径重放; winsorize ±15%; 受影响阈值 |d|>1pp。")
    L.append("规则实现: analysis/fill_rules.py（自测通过）; 引擎: backtest_strategies.run_simulation")
    L.append("(HEAD 双引擎恒等验证通过, 存量口径零漂移)。本报告不构成投资建议。")

    _bak(OUT_MD)
    with open(OUT_MD, "w") as f:
        f.write("\n".join(L) + "\n")

    # ── stdout 摘要块 ──
    print("=====FILL_IMPACT_RESULT=====")
    print(json.dumps({
        "date": TODAY, "fetched": fetched,
        "delta_summary": summary,
        "agg_by_mode": {mk: {arm: agg_by_mode[mk][arm] for arm in CORE_ARMS} for mk, _, _ in MODES},
        "ema_focus": [d for d in d_ema_sorted if d["name"] in FOCUS_NAMES],
        "report": OUT_MD,
    }, ensure_ascii=False, indent=1, default=str))
    print("=====FILL_IMPACT_END=====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
