#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ML v2 特征研究（2026-11-01）：纯价量 → 增强特征能否翻盘负结果
================================================================

背景：ML v1（13 个 OHLCV 特征）首验 36 只 A 口径均值 +1.79%/夏普 −0.15，
不敌既有规则臂（EMA金叉 +48.8 等，1.5 年窗）。负结果后的下一步杠杆=特征升级。

数据面盘点结论（立项前）：
- factor_engine 双因子分= 2026-09-03 快照，无历史时序 → 不能当 per-stock 时序特征（诚实排除）
- baostock provider 无 turn/amount 字段 → 不为研究改生产数据路径
- 五维无每日历史存档
→ v2 特征全部从已有日线历史/panel 重建，零生产数据路径改动。

v2 = v1(13) + 5：
  f_52w_high_dist（距 250 日高点）/ f_vol_ratio_5_20（量能扩张）
  f_vwap250_dist（chip 获利盘代理——五维里唯一"绝对分档三年全正"的维度方向）
  f_xs_rank_ret20 / f_xs_rank_vol20（横截面分位，panel 逐日重算，仅用当日截面=无前视）

方法纪律：
- v1/v2 **同窗（2020+）同 harness 对照**（v1 首验是 1.5 年窗，跨窗不可比）；
  单变量=特征集，模型/阈值/walk-forward 参数全部不动
- 截断攻击自测双集已过（analysis/ml_strategy.py selftest）
- 产出：analysis/backtest/ml_v2_feature_study_<today>.md/.json（写前 _bak）
运行：nohup python3 scripts/ml_v2_feature_study.py > logs/ml_v2_feature_study_<today>.log 2>&1 &
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
import pandas as pd

from analysis.backtest_strategies import STOCKS, fetch_data, run_simulation
from analysis.ml_strategy import strategy_ml_rf

TODAY = datetime.now().strftime("%Y-%m-%d")
START = "20200101"
OUT_MD = os.path.join(WORKSPACE, "analysis", "backtest", f"ml_v2_feature_study_{TODAY}.md")
OUT_JSON = os.path.join(WORKSPACE, "analysis", "backtest", f"ml_v2_feature_study_{TODAY}.json")
RULE_FLOOR_1P5Y = {"arm": "KDJ+CCI", "mean_ret": 1.80, "sharpe": None}  # 最弱规则臂参照（1.5年窗，仅作心理锚非同窗对照）


def _bak(p):
    if os.path.exists(p):
        shutil.copy2(p, p + ".bak")


def build_panel_ranks(stock_dfs):
    """panel 对齐 → 逐日期横截面 rank（0~1 分位，仅用当日截面，无前视）。
    返回 {symbol: DataFrame(date, f_xs_rank_ret20, f_xs_rank_vol20)}"""
    ret20, vol20 = {}, {}
    for sym, df in stock_dfs.items():
        c = pd.to_numeric(df["close"], errors="coerce")
        ret20[sym] = pd.Series(c.pct_change(20).values, index=df["date"].dt.normalize())
        vol20[sym] = pd.Series(c.pct_change().rolling(20).std().values, index=df["date"].dt.normalize())
    ret_panel = pd.DataFrame(ret20)     # index=date, columns=symbol
    vol_panel = pd.DataFrame(vol20)
    rank_ret = ret_panel.rank(axis=1, pct=True)     # 逐行（同日全池）分位
    rank_vol = vol_panel.rank(axis=1, pct=True)
    out = {}
    for sym, df in stock_dfs.items():
        key = df["date"].dt.normalize()
        out[sym] = pd.DataFrame({
            "f_xs_rank_ret20": rank_ret[sym].reindex(key).values,
            "f_xs_rank_vol20": rank_vol[sym].reindex(key).values,
        }, index=df.index)
    return out


def _run_one(args):
    """模块级 worker（macOS spawn 可 pickle）：单票 v1+v2 × A/C 推理与模拟。"""
    sym, name, df, rank_df = args
    df = df.copy().join(rank_df)
    out = []
    for fset in ("v1", "v2"):
        actions = strategy_ml_rf(df, feature_set=fset)
        for mk, fm, el in (("A", "signal_close", False), ("C", "next_open", True)):
            m = run_simulation(df, actions, symbol=sym, fill_mode=fm, enforce_limit=el)
            if "error" in m:
                continue
            out.append({"symbol": sym, "name": name, "fset": fset, "mode": mk,
                        "ret": m["total_return_pct"], "sharpe": m["sharpe_ratio"],
                        "mdd": m["max_drawdown_pct"], "trades": m["total_trades"],
                        "win_rate": m["win_rate_pct"]})
    return out


def main():
    stock_dfs = {}
    for sym, name in STOCKS:
        df = fetch_data(sym, name, start=START)
        if df is None or len(df) >= 150:
            if df is not None:
                stock_dfs[sym] = df
        else:
            print(f"⚠️ {name}({sym}) 数据不足", flush=True)
    fetched = len(stock_dfs)
    if fetched < 30:
        print(f"❌ 覆盖不足 {fetched}/36 —— 拒绝出结论")
        return 1
    print(f"✅ 面板就绪 {fetched} 只", flush=True)

    ranks = build_panel_ranks(stock_dfs)

    # 跨股票进程池并行（param_tune 同款模式）；n_jobs=1 避免 loky 套娃孵化
    os.environ["ML_RF_JOBS"] = "1"
    from concurrent.futures import ProcessPoolExecutor

    tasks = [(s, n, stock_dfs[s], ranks[s]) for s, n in STOCKS if s in stock_dfs]
    rows = []
    with ProcessPoolExecutor(max_workers=4) as ex:
        for i, out in enumerate(ex.map(_run_one, tasks), 1):
            rows.extend(out)
            print(f"[{i}/{len(tasks)}] {out[0]['name'] if out else '?'} v1+v2 推理完成", flush=True)

    def agg(fset, mode):
        sel = [r for r in rows if r["fset"] == fset and r["mode"] == mode]
        if not sel:
            return {}
        rets = [r["ret"] for r in sel]
        return {"n": len(sel),
                "mean_ret": round(float(np.mean(rets)), 2),
                "median_ret": round(float(np.median(rets)), 2),
                "winsor15": round(float(np.mean(np.clip(rets, -15, 15))), 2),
                "mean_sharpe": round(float(np.mean([r["sharpe"] for r in sel])), 3),
                "mean_mdd": round(float(np.mean([r["mdd"] for r in sel])), 2),
                "mean_trades": round(float(np.mean([r["trades"] for r in sel])), 1),
                "positive": int(sum(1 for v in rets if v > 0))}

    summary = {f"{fs}_{mk}": agg(fs, mk) for fs in ("v1", "v2") for mk in ("A", "C")}
    # 配对差分（同股同臂基线）
    paired = []
    for mk in ("A", "C"):
        v1 = {(r["symbol"]): r["ret"] for r in rows if r["fset"] == "v1" and r["mode"] == mk}
        v2 = {(r["symbol"]): r["ret"] for r in rows if r["fset"] == "v2" and r["mode"] == mk}
        common = sorted(set(v1) & set(v2))
        d = [v2[s] - v1[s] for s in common]
        paired.append({"mode": mk, "n": len(common),
                       "mean_delta_v2_minus_v1": round(float(np.mean(d)), 2),
                       "median_delta": round(float(np.median(d)), 2),
                       "v2_better_count": int(sum(1 for x in d if x > 0))})

    payload = {"date": TODAY, "start": START, "fetched": fetched,
               "summary": summary, "paired_delta": paired, "rows": rows}
    _bak(OUT_JSON)
    with open(OUT_JSON, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    L = [f"# ML v2 特征研究（v1 vs v2 同窗对照）{TODAY}\n",
         f"- 窗口: {START} ~ 今 | {fetched} 只 | 模型/阈值/walk-forward 不变，单变量=特征集",
         "- v2 = v1(13) + 52周高位置/量能比/chip获利盘代理(vwap250)/横截面动量与波动分位",
         "- 对照锚: v1 首验(1.5年窗)A口径 +1.79%/夏普-0.15（跨窗仅作方向参照）\n"]
    L.append("| 特征集 | 口径 | 均值% | 中位% | winsor% | 夏普 | 回撤% | 交易 | 正收益票 |")
    L.append("|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
    for k, s in summary.items():
        if not s:
            continue
        fs, mk = k.rsplit("_", 1)
        L.append(f"| {fs} | {mk} | {s['mean_ret']:+.2f} | {s['median_ret']:+.2f} | {s['winsor15']:+.2f} | "
                 f"{s['mean_sharpe']:.3f} | {s['mean_mdd']:.1f} | {s['mean_trades']:.1f} | {s['positive']}/{s['n']} |")
    L.append("\n## 配对差分（同股 v2 − v1）\n")
    L.append("| 口径 | n | 均值Δ | 中位Δ | v2 更优票数 |")
    L.append("|:---|:---:|:---:|:---:|:---:|")
    for p in paired:
        L.append(f"| {p['mode']} | {p['n']} | {p['mean_delta_v2_minus_v1']:+.2f} | "
                 f"{p['median_delta']:+.2f} | {p['v2_better_count']}/{p['n']} |")
    L.append("\n---\n判读纪律: 负结果也是结果；v2 若仍不敌规则臂地板（KDJ 1.5年窗 +1.80%），")
    L.append("结论=日线方向分类+本仓样本量下 RF 特征工程到顶，转产线不立项；正向则再过周六验证+DSR。")

    _bak(OUT_MD)
    with open(OUT_MD, "w") as f:
        f.write("\n".join(L) + "\n")

    print("=====ML_V2_STUDY=====")
    print(json.dumps({"summary": summary, "paired_delta": paired},
                     ensure_ascii=False, indent=1))
    print("=====ML_V2_STUDY_END=====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
