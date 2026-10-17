#!/usr/bin/env python3
"""双策略信号（双卖/分歧/双买）信息量事件研究（2026-10-17）。

问题: 每日 dual.md 推送的 双卖/分歧/双买 分类是否携带前瞻信息？
方法: 用 adaptive_dual 的生产 signal_* 函数逐日滚动评估（完全生产口径）：
  - 每股取当前 dual map 前 2 策略（与每日扫描同源；注意: 策略**选择**含轻度
    前视——validation 分数缓慢刷新，与生产日常一致，研究结论按此解读）
  - 对历史每个交易日（warmup 70 根后），滚动切片调 signal_* 得两策略 action
  - 分类: 双卖/双买/分歧卖/分歧买/分歧对撞/双持有
  - 前瞻 +1/+5/+10 收盘收益 vs 同评估日集基线
统计纪律: 报 n/胜率/中位，小样本类不下强结论。
产出: analysis/research/2026-10-17_dual_signal_event_study.json
"""
import os
import sys
import json
import logging
import time

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.getLogger().setLevel(logging.ERROR)

import pandas as pd

from analysis.adaptive_dual import load_dual_strategy_map, fetch_data, SIGNAL_FUNCS, STOCKS

WARMUP = 70
START = "20240101"
HORIZONS = (1, 5, 10)


def classify(a1: str, a2: str):
    sell = ("🔴卖出",)
    if a1 == "🔴卖出" and a2 == "🔴卖出":
        return "双卖"
    if a1 == "🟢买入" and a2 == "🟢买入":
        return "双买"
    if a1 == "🟢买入" and a2 == "🔴卖出" or a1 == "🔴卖出" and a2 == "🟢买入":
        return "分歧对撞"
    if a1 == "🔴卖出" or a2 == "🔴卖出":
        return "分歧卖"
    if a1 == "🟢买入" or a2 == "🟢买入":
        return "分歧买"
    return "双持有"


def main():
    t0 = time.time()
    dual = load_dual_strategy_map()
    print(f"[study] dual map 覆盖 {len(dual)} 只", flush=True)

    rows = []          # {symbol, name, date, cls, close, fwd{h}}
    skipped = []
    eval_fail_days = 0

    for sym, name in STOCKS:
        entry = dual.get(sym)
        if not entry or len(entry) < 2:
            skipped.append(f"{name}(无dual)")
            continue
        df = fetch_data(sym, start=START)
        if df is None or len(df) < WARMUP + HORIZONS[-1] + 5:
            skipped.append(f"{name}(数据不足)")
            continue
        close = df["close"].astype(float).reset_index(drop=True)
        if "date" in df.columns:
            dates = df["date"].astype(str).reset_index(drop=True)
        else:
            dates = pd.Series([f"row{i}" for i in range(len(df))])
        n = len(close)
        dates = dates.reset_index(drop=True)
        for i in range(WARMUP, n):
            slice_df = df.iloc[: i + 1]
            actions = []
            ok = True
            for st in entry[:2]:
                fn = SIGNAL_FUNCS.get(st["strategy"])
                if fn is None:
                    ok = False
                    break
                try:
                    r = fn(slice_df, params=st.get("params"))
                    actions.append(r["action"])
                except Exception:
                    ok = False
                    break
            if not ok or len(actions) < 2:
                eval_fail_days += 1
                continue
            fwd = {}
            for h in HORIZONS:
                if i + h < n:
                    fwd[str(h)] = round(float(close.iloc[i + h] / close.iloc[i] - 1), 5)
            rows.append({
                "symbol": sym, "name": name, "date": str(dates.iloc[i]),
                "cls": classify(actions[0], actions[1]),
                "actions": actions, "close": float(close.iloc[i]), "fwd": fwd,
            })
        print(f"[study] {name} 完成 ({time.time()-t0:.0f}s)", flush=True)

    def stats(sub):
        if not sub:
            return None
        s = pd.Series(sub).dropna()
        if s.empty:
            return None
        return {"n": int(len(s)), "mean": round(float(s.mean()), 5),
                "median": round(float(s.median()), 5),
                "win_rate": round(float((s > 0).mean()), 4)}

    out = {"date": "2026-10-17", "window_start": START, "warmup": WARMUP,
           "evaluated_days": len(rows), "eval_fail_days": eval_fail_days,
           "skipped": skipped, "classes": {}}
    baseline = {h: stats([r["fwd"][str(h)] for r in rows if str(h) in r["fwd"]]) for h in HORIZONS}
    out["baseline"] = baseline
    for cls in ("双卖", "分歧卖", "分歧买", "双买", "分歧对撞", "双持有"):
        sub = [r for r in rows if r["cls"] == cls]
        out["classes"][cls] = {
            "count": len(sub),
            "fwd": {h: stats([r["fwd"][str(h)] for r in sub if str(h) in r["fwd"]]) for h in HORIZONS},
        }
    for cls in out["classes"]:
        for h in HORIZONS:
            ev = out["classes"][cls]["fwd"].get(h)
            ba = baseline.get(h)
            out["classes"][cls][f"excess_{h}d"] = (
                round(ev["mean"] - ba["mean"], 5) if ev and ba else None)

    path = os.path.join(WORKSPACE, "analysis/research/2026-10-17_dual_signal_event_study.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"[study] 评估日 {len(rows)} | 评估失败日 {eval_fail_days} | 跳过 {skipped}")
    print(f"[study] 基线: " + " | ".join(
        f"+{h}d {baseline[h]['mean']:+.3%}(n={baseline[h]['n']})" for h in HORIZONS if baseline[h]))
    for cls, info in out["classes"].items():
        if info["count"] == 0:
            continue
        parts = []
        for h in HORIZONS:
            ev = info["fwd"].get(h)
            ex = info[f"excess_{h}d"]
            if ev:
                parts.append(f"+{h}d {ev['mean']:+.2%}(胜率{ev['win_rate']:.0%},超额{ex:+.2%})")
        print(f"[study] {cls}: {info['count']}日 | " + " | ".join(parts))
    print(f"[study] 已写 {path} | 总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
