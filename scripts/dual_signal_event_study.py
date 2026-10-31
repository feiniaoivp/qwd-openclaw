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
import numpy as np

try:
    import pandas_ta as ta
    _HAS_TA = True
except Exception:
    _HAS_TA = False

from analysis.adaptive_dual import load_dual_strategy_map, fetch_data, SIGNAL_FUNCS, STOCKS
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


WARMUP = 70
START = "20240101"
HORIZONS = (1, 5, 10)
ER_WIN = 20
ER_TH = 0.25      # 事前设定（非数据调优）：ER>=0.25 视为趋势态
ADX_TH = 20.0     # 业界标准阈值
CHOP_TH = 61.8    # P1 政权检测器阈值
ZONE_TH = 0.3     # P1 夹缝阈值 (EMA gap / ATR)


def regime_series(df):
    """全序列一次算好 ER20 / ADX14 / P1组件（CHOP、EMA gap/ATR），均为因果指标按日索引。"""
    close = df["close"].astype(float).reset_index(drop=True)
    diff = close.diff().abs()
    er = (close.diff(ER_WIN).abs() / diff.rolling(ER_WIN).sum()).fillna(0.0)
    adx_s = None
    chop_s = pd.Series([50.0] * len(close))
    gap_s = pd.Series([0.0] * len(close))
    if _HAS_TA:
        try:
            adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
            col = [c for c in adx_df.columns if c.upper().startswith("ADX")][0]
            adx_s = adx_df[col].reset_index(drop=True)
        except Exception:
            adx_s = None
        try:
            # P1 compute_chop 同式: ATR(length=1) 滚动和 / 振幅
            atr1 = ta.atr(df["high"], df["low"], df["close"], length=1)
            atr_sum = atr1.rolling(14).sum()
            hh = df["high"].rolling(14).max()
            ll = df["low"].rolling(14).min()
            rng = (hh - ll).replace(0, np.nan)
            chop_s = (100 * np.log10(atr_sum / rng) / np.log10(14)).fillna(50.0)
            ema12 = ta.ema(df["close"], length=12)
            ema26 = ta.ema(df["close"], length=26)
            atr14 = ta.atr(df["high"], df["low"], df["close"], length=14)
            gap_s = ((ema12 - ema26).abs() / atr14.replace(0, np.nan)).fillna(0.0)
        except Exception:
            pass
    return er, adx_s, chop_s, gap_s


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
        er_s, adx_s, chop_s, gap_s = regime_series(df)
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
            # 前置收益（检验双卖=洗盘末端假设：双卖事件前置跌幅应大于分歧卖）
            pre_5d = round(float(close.iloc[i] / close.iloc[i - 5] - 1), 5)
            er_v = round(float(er_s.iloc[i]), 4) if pd.notna(er_s.iloc[i]) else None
            adx_v = (round(float(adx_s.iloc[i]), 2)
                     if adx_s is not None and pd.notna(adx_s.iloc[i]) else None)
            p1_chop = bool(chop_s.iloc[i] > CHOP_TH)
            p1_zone = bool(gap_s.iloc[i] < ZONE_TH)
            rows.append({
                "symbol": sym, "name": name, "date": str(dates.iloc[i]),
                "cls": classify(actions[0], actions[1]),
                "actions": actions, "close": float(close.iloc[i]), "fwd": fwd,
                "pre_5d": pre_5d,
                "er20": er_v, "adx14": adx_v,
                "regime_er": ("trend" if er_v is not None and er_v >= ER_TH else "chop") if er_v is not None else None,
                "regime_adx": ("trend" if adx_v is not None and adx_v >= ADX_TH else "chop") if adx_v is not None else None,
                "chop_idx": round(float(chop_s.iloc[i]), 1),
                "ema_gap_atr": round(float(gap_s.iloc[i]), 2),
                "p1_chop": p1_chop, "p1_zone": p1_zone,
                "p1_avoid": p1_chop or p1_zone,
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
            "pre_5d": stats([r.get("pre_5d") for r in sub if r.get("pre_5d") is not None]),
        }
    for cls in out["classes"]:
        for h in HORIZONS:
            ev = out["classes"][cls]["fwd"].get(h)
            ba = baseline.get(h)
            out["classes"][cls][f"excess_{h}d"] = (
                round(ev["mean"] - ba["mean"], 5) if ev and ba else None)

    # 产出文件名动态日期（2026-10-20：旧硬编码 2026-10-17_ 会在重放时静默覆写当日研究档）
    from datetime import date as _date
    path = os.path.join(WORKSPACE, f"analysis/research/{_date.today().isoformat()}_dual_signal_event_study.json")
    _bak(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    # 事件明细单独落盘（供离线稳健性分析：分年/个股集中度/去重/bootstrap）
    # 注：固定名 dual_signal_eval_days.json 是下游研究的 SSOT 面板（多脚本引用），不改名
    rows_path = os.path.join(WORKSPACE, "analysis/research/dual_signal_eval_days.json")
    _bak(rows_path)
    with open(rows_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    print(f"[study] 明细已写 {rows_path} ({len(rows)} 行)")

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
        pre = info.get("pre_5d")
        pre_str = f" | 前置5日 {pre['mean']:+.2%}(中位{pre['median']:+.2%})" if pre else ""
        print(f"[study] {cls}: {info['count']}日 | " + " | ".join(parts) + pre_str)
    print(f"[study] 已写 {path} | 总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
