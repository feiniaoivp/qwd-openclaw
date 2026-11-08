#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""guard_effectiveness_audit.py — 守卫有效性审计（Learning→Guard 闭环的另一半，2026-11-05）

背景（用户点题「Learning → Guard 进化链条」）：
  现有链条把失败转化为守卫（教训→门禁），但**守卫自身从不被审计**——链条只收紧、不校正。
  一个系统性拦掉 winner 的守卫本身就是 bug。本脚本用失败台账 + 后续行情回答：
  「被守卫拦掉的操作，事后看是对了还是亏了？」

方法：
  对 ledger.jsonl 中每条 blocked_signal，取信号日后 T+1/T+3/T+5 交易日收盘价：
    - 被拦买入:  个股后续收益 − 池中位数 = 超额。>0 → 守卫拦掉了 winner（成本）
    - 被拦卖出（转持有）: 个股后续收益 − 池中位数。>0 → 守卫拦对了（卖出会踏空）
  按 guard（tier/risk_override/dedup/...）聚合：样本数、逐票超额中位数、拦对率。
  ⚠️ 样本 <20 的 guard 仅观察不下结论（10-21 教训：小样本单票结论=幸存者偏差）。

价格源（合并多源，按日期取最新可用）：
  data/stock_data/{sym}.csv（历史湖，可能滞后）
  data/pipeline_cache/close_scan_{date}.json（近几日收盘全池）

产出:
  analysis/audit/guard_effectiveness_{date}.md   人读报告
  data/failure_cases/guard_effectiveness_{date}.json  结构化结果

退出码: 恒 0（报告型，不阻断；供周流水线/人工阅读）
"""
import os
import sys
import json
import glob
import statistics
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LEDGER = os.path.join(WORKSPACE, "data", "failure_cases", "ledger.jsonl")
STOCK_DIR = os.path.join(WORKSPACE, "data", "stock_data")
CACHE_DIR = os.path.join(WORKSPACE, "data", "pipeline_cache")
AUDIT_MD_DIR = os.path.join(WORKSPACE, "analysis", "audit")
OUT_JSON_DIR = os.path.join(WORKSPACE, "data", "failure_cases")
HORIZONS = (1, 3, 5)
MIN_SAMPLE_FOR_VERDICT = 20


# ═══════════════════ 价格源合并 ═══════════════════

def build_price_map():
    """{sym: {date: close}}——历史湖 + 近日 close_scan 合并"""
    prices = {}
    # 1) 历史湖
    for fp in glob.glob(os.path.join(STOCK_DIR, "*.csv")):
        sym = os.path.basename(fp).replace(".csv", "")
        d = {}
        try:
            import csv
            with open(fp, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    date = (row.get("date") or "").strip()[:10]
                    try:
                        d[date] = float(row["close"])
                    except (KeyError, TypeError, ValueError):
                        continue
        except OSError:
            continue
        if d:
            prices.setdefault(sym, {}).update(d)
    # 2) 近日收盘全池：slim（analysis/daily，键 stocks）与 full（pipeline_cache，键 watchlist_scan）双形态兼容
    scan_files = (sorted(glob.glob(os.path.join(CACHE_DIR, "close_scan_*.json")))
                  + sorted(glob.glob(os.path.join(WORKSPACE, "analysis", "daily", "*_close_scan.json"))))
    for fp in scan_files:
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
            dd = data.get("data_date")
            if not dd:
                continue
            for s in (data.get("stocks") or data.get("watchlist_scan") or []):
                sym = s.get("symbol")
                p = s.get("price")
                if sym and p:
                    prices.setdefault(sym, {})[dd] = float(p)
        except (json.JSONDecodeError, OSError):
            continue
    return prices


def trading_dates_sorted(prices_by_sym):
    """全池并集交易日（升序）——用于 T+k 定位"""
    dates = set()
    for d in prices_by_sym.values():
        dates.update(d.keys())
    return sorted(dates)


def forward_return(prices: dict, sig_date: str, k: int) -> tuple:
    """信号日 → T+k 收盘收益。返回 (ret or None, 信号日价, T+k 价)。"""
    p0 = prices.get(sig_date)
    if p0 is None or p0 <= 0:
        return None, p0, None
    later = sorted(d for d in prices if d > sig_date)
    if len(later) < k:
        return None, p0, None
    pk = prices[later[k - 1]]
    return pk / p0 - 1, p0, pk


# ═══════════════════ 审计主逻辑 ═══════════════════

def load_cases():
    cases = []
    if not os.path.exists(LEDGER):
        return cases
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") != "blocked_signal":
                continue
            guards = e.get("blocked_by") or {}
            primary = sorted(guards.keys())[0] if guards else "unknown"
            cases.append({
                "date": e.get("date"), "symbol": e.get("symbol"), "name": e.get("name"),
                "action": e.get("raw_action"), "strategy": e.get("strategy"),
                "guard": primary, "price": (e.get("signal") or {}).get("price"),
            })
    return cases


def audit():
    prices_map = build_price_map()
    all_dates = trading_dates_sorted(prices_map)
    cases = load_cases()

    # 逐案评估（按主 guard 与 horizon）
    rows = []
    for c in cases:
        sym_prices = prices_map.get(c["symbol"], {})
        evals = {}
        for k in HORIZONS:
            ret, p0, pk = forward_return(sym_prices, c["date"], k)
            evals[k] = {"ret": ret, "p0": p0, "pk": pk}
            if ret is None:
                evals[k]["status"] = "pending"
                continue
            # 池基准：同窗口全池中位收益（剔除本票）
            pool = []
            for sym2, d2 in prices_map.items():
                if sym2 == c["symbol"]:
                    continue
                r2, _, _ = forward_return(d2, c["date"], k)
                if r2 is not None:
                    pool.append(r2)
            bench = statistics.median(pool) if pool else None
            excess = (ret - bench) if bench is not None else None
            # 判定：买入被拦 & 超额>0 → 拦了 winner（守卫成本）；卖出被拦 & 超额>0 → 拦对
            if excess is None:
                evals[k]["verdict"] = "unknown"
            elif c["action"] == "买入":
                evals[k]["verdict"] = "guard_cost" if excess > 0 else "guard_right"
            else:  # 卖出被拦 → 持有
                evals[k]["verdict"] = "guard_right" if excess > 0 else "guard_cost"
            evals[k]["excess"] = excess
            evals[k]["pool_bench"] = bench
            evals[k]["status"] = "evaluated"
        rows.append({**c, "evals": evals})

    # 按 guard × horizon 聚合
    summary = {}
    for g in sorted({r["guard"] for r in rows}):
        for k in HORIZONS:
            sub = [r for r in rows if r["guard"] == g and r["evals"][k]["status"] == "evaluated"]
            if not sub:
                continue
            exc = [r["evals"][k]["excess"] for r in sub if r["evals"][k].get("excess") is not None]
            rights = sum(1 for r in sub if r["evals"][k]["verdict"] == "guard_right")
            summary.setdefault(g, {})[k] = {
                "n": len(sub),
                "median_excess": round(statistics.median(exc), 4) if exc else None,
                "guard_right_rate": round(rights / len(sub), 3) if sub else None,
                "action_mix": {
                    "买入被拦": sum(1 for r in sub if r["action"] == "买入"),
                    "卖出被拦": sum(1 for r in sub if r["action"] != "买入"),
                },
            }
    return rows, summary


def write_report(rows, summary):
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    lines = [f"# ⚖️ 守卫有效性审计 ({date_str})", "",
             f"_Learning→Guard 闭环的另一半：被拦操作的事后对错。价格源=历史湖+close_scan 合并；"
             f"基准=同窗口全池中位。样本 <{MIN_SAMPLE_FOR_VERDICT} 仅观察。_",
             f"_台账累计 {len(rows)} 案。_", ""]

    # 逐 guard 摘要
    lines += ["## 按 Guard × Horizon 汇总", "",
              "| guard | horizon | 已评估 | 超额中位 | 拦对率 | 买/卖构成 | 结论 |",
              "|:---|:---|---:|---:|---:|:---|:---|"]
    for g, hs in sorted(summary.items()):
        for k in sorted(hs):
            s = hs[k]
            am = f"{s['action_mix']['买入被拦']}买/{s['action_mix']['卖出被拦']}卖"
            if s["n"] < MIN_SAMPLE_FOR_VERDICT:
                concl = "观察期（样本不足）"
            else:
                concl = "✅ 有效" if (s["guard_right_rate"] or 0) >= 0.5 else "⚠️ 疑似过严（系统性拦 winner）"
            me = f"{s['median_excess']:+.2%}" if s["median_excess"] is not None else "—"
            gr = f"{s['guard_right_rate']:.0%}" if s["guard_right_rate"] is not None else "—"
            lines.append(f"| {g} | T+{k} | {s['n']} | {me} | {gr} | {am} | {concl} |")
    lines.append("")

    # 逐案明细（最新 20 条）
    lines += ["## 逐案明细（最新在前）", ""]
    for r in reversed(rows[-20:]):
        parts = [f"- {r['date']} {r['name']}({r['symbol']}) **{r['action']}被拦** [{r['strategy']}] 守卫={r['guard']}"]
        for k in HORIZONS:
            e = r["evals"][k]
            if e["status"] == "pending":
                parts.append(f"T+{k}=待数据")
            else:
                emoji = "✅" if e["verdict"] == "guard_right" else "💸"
                parts.append(f"T+{k}: {e['ret']:+.2%} (超额 {e['excess']:+.2%}) {emoji}{e['verdict']}")
        lines.append(" | ".join(parts))

    pending = sum(1 for r in rows for k in HORIZONS if r["evals"][k]["status"] == "pending")
    if pending:
        lines += ["", f"> ⏳ {pending} 个 (案×horizon) 组合待后续行情补充，逐日自动变可评估。"]

    md_path = os.path.join(AUDIT_MD_DIR, f"guard_effectiveness_{date_str}.md")
    os.makedirs(AUDIT_MD_DIR, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    out_json = os.path.join(OUT_JSON_DIR, f"guard_effectiveness_{date_str}.json")
    os.makedirs(OUT_JSON_DIR, exist_ok=True)
    if os.path.exists(out_json):
        import shutil
        shutil.copy2(out_json, out_json + ".bak")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"date": date_str, "cases": len(rows), "summary": summary,
                   "rows": rows}, f, ensure_ascii=False, indent=1, default=str)
    print(f"⚖️ 守卫审计完成: {len(rows)} 案 | 报告: {md_path}")
    print(f"   结构化: {out_json}")
    return 0


def main():
    rows, summary = audit()
    return write_report(rows, summary)


if __name__ == "__main__":
    sys.exit(main())
