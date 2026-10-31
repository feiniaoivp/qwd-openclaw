#!/usr/bin/env python3
"""动量衰减曲线 / 最优持有期研究（B2）。

问题: 动量确认型信号（买在涨后+5%）买入后, 超额收益在第几个交易日见顶/衰减?
       → 为「时间止损/最优持有窗口」提供依据, 直接服务周一执行。

方法:
- 信号日清单复用 analysis/research/dual_signal_eval_days.json（21068 评估日, 不重跑信号）
- 价格走 DataRouter（fetch_data, 与生产同源降级链）
- 前瞻收益 fwd_h = close[t+h]/close[t]-1, h ∈ {1,2,3,5,7,10,15,20}（按交易日位置）
- 超额 = 同年去均值（2026-10-17 方法论: 分年超额必须用同年基线）
- 一致性校验: 重算 fwd1/5/10 与 eval JSON 存量值对比（防价格源错位）
输出: analysis/research/momentum_decay_study.json + stdout 标记块
"""
import os
import sys
import json
import time
import logging
import pandas as pd
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


try:
    import numpy as np
except Exception:
    np = None
    print("[warn] numpy 不可用，bootstrap CI 将跳过", flush=True)

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("momentum_decay")

EVAL_JSON = os.path.join(WORKSPACE, "analysis/research/dual_signal_eval_days.json")
OUT_JSON = os.path.join(WORKSPACE, "analysis/research/momentum_decay_study.json")
PRICE_CACHE_DIR = os.path.join(WORKSPACE, "data/research_price_cache")
HORIZONS = (1, 2, 3, 5, 7, 10, 15, 20)
CLASSES = ("分歧买", "双买", "分歧卖", "双卖")   # 买侧两类 + 卖侧参照


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def win_rate(xs):
    xs = [x for x in xs if x is not None]
    return sum(1 for x in xs if x > 0) / len(xs) if xs else None


def main():
    from analysis.adaptive_dual import fetch_data

    t0 = time.time()
    with open(EVAL_JSON) as f:
        rows = json.load(f)
    log.info("载入 %d 评估日", len(rows))

    # 按票分组 eval 日
    by_sym = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)
    log.info("涉及 %d 只", len(by_sym))

    # 拉价格 + 逐票算多档 fwd
    fwd_recalc = []          # 与 eval JSON 同字段（fwd1/5/10），用于一致性校验
    decay_rows = []          # {symbol,date,cls,year,h,fwd}
    fetch_fail = []
    os.makedirs(PRICE_CACHE_DIR, exist_ok=True)
    for i, (sym, srows) in enumerate(sorted(by_sym.items()), 1):
        df = None
        # 磁盘缓存：研究期内历史价格不变（周末/盘后跑），避免重复拉网
        cache_path = os.path.join(PRICE_CACHE_DIR, f"{sym}.pkl")
        if os.path.exists(cache_path):
            try:
                df = pd.read_pickle(cache_path)
            except Exception as e:
                log.warning("缓存读取失败 %s: %s", sym, e)
                df = None
        if df is None:
            try:
                df = fetch_data(sym, start="20240401")
            except Exception as e:
                log.warning("fetch %s 异常: %s", sym, e)
            if df is not None and len(df) >= 60:
                try:
                    df.to_pickle(cache_path)
                except Exception as e:
                    log.warning("缓存写入失败 %s: %s", sym, e)
        if df is None or len(df) < 60:
            fetch_fail.append(sym)
            continue
        closes = df["close"].astype(float).tolist()
        dates = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in df["date"]]
        pos = {d: k for k, d in enumerate(dates)}
        for r in srows:
            k = pos.get(r["date"])
            if k is None:
                continue
            c0 = closes[k]
            for h in HORIZONS:
                if k + h < len(closes):
                    decay_rows.append({
                        "symbol": sym, "date": r["date"], "cls": r["cls"],
                        "year": r["date"][:4], "h": h,
                        "fwd": closes[k + h] / c0 - 1,
                    })
            # 一致性校验样本（仅存量 fwd1/5/10 存在的行）
            chk = {"symbol": sym, "date": r["date"]}
            for h, key in ((1, "1"), (5, "5"), (10, "10")):
                stored = r.get("fwd", {}).get(key)
                mine = closes[k + h] / c0 - 1 if k + h < len(closes) else None
                if stored is not None and mine is not None:
                    chk[key] = abs(mine - stored)
            fwd_recalc.append(chk)
        if i % 10 == 0:
            log.info("进度 %d/%d (%.0fs)", i, len(by_sym), time.time() - t0)

    if fetch_fail:
        log.warning("拉取失败 %d 只: %s", len(fetch_fail), fetch_fail)

    # 一致性: 重算 vs 存量 的平均绝对差
    mad = {}
    for key in ("1", "5", "10"):
        vals = [c[key] for c in fwd_recalc if key in c]
        mad[key] = sum(vals) / len(vals) if vals else None
    log.info("一致性校验 |Δ|均值 fwd1/5/10 = %s", {k: (f"{v:.5f}" if v is not None else None) for k, v in mad.items()})

    # 同年全样本基线（全部 decay_rows，按 (year,h) 汇总）—— 分母用全部评估日（含双持有），与既有一致
    base = {}
    for r in decay_rows:
        base.setdefault((r["year"], r["h"]), []).append(r["fwd"])
    bmean = {k: sum(v) / len(v) for k, v in base.items()}
    bn = {k: len(v) for k, v in base.items()}

    def boot_ci_excess(cls, h, n_draws=3000):
        """同年去均值超额的 bootstrap 95%CI（iid；逐票聚类风险由逐票分布检查另行覆盖）。"""
        if np is None:
            return None
        xs = np.array([
            r["fwd"] - bmean[(r["year"], r["h"])]
            for r in decay_rows if r["cls"] == cls and r["h"] == h
        ])
        if len(xs) < 20:
            return None
        rng = np.random.default_rng(42)
        idx = rng.integers(0, len(xs), size=(n_draws, len(xs)))
        means = xs[idx].mean(axis=1)
        return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]

    # 各信号类衰减曲线
    out = {"consistency_mad": mad, "fetch_fail": fetch_fail,
           "baseline_mean": {f"{y}|h{h}": bmean[(y, h)] for (y, h) in bmean},
           "baseline_n": {f"{y}|h{h}": bn[(y, h)] for (y, h) in bmean}, "classes": {}}
    for cls in CLASSES:
        sub = [r for r in decay_rows if r["cls"] == cls]
        info = {"count_rows": len(sub), "curve": {}, "by_year": {}}
        for h in HORIZONS:
            hs = [r for r in sub if r["h"] == h]
            info["curve"][str(h)] = {
                "n": len(hs),
                "fwd": mean([r["fwd"] for r in hs]),
                "excess": mean([r["fwd"] - bmean[(r["year"], h)] for r in hs]) if hs else None,
                "win": win_rate([r["fwd"] for r in hs]),
            }
            ci = boot_ci_excess(cls, h)
            if ci is not None:
                info["curve"][str(h)]["excess_ci95"] = ci
        for y in sorted({r["year"] for r in sub}):
            ys = [r for r in sub if r["year"] == y]
            info["by_year"][y] = {
                "n": len([r for r in ys if r["h"] == 5]),
                "excess": {str(h): mean([r["fwd"] - bmean[(r["year"], h)] for r in ys if r["h"] == h]) for h in HORIZONS},
            }
        out["classes"][cls] = info

    # 先落盘再打印
    _bak(OUT_JSON)
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info("已落盘 %s (%.0fs)", OUT_JSON, time.time() - t0)

    print("\n=====MOMENTUM_DECAY=====")
    for cls in CLASSES:
        info = out["classes"].get(cls, {})
        print(f"\n[{cls}] 事件-横截面对 n={info.get('count_rows')}")
        print("  h      |  超额   |  绝对收益 | 胜率  | n")
        prev = None
        for h in HORIZONS:
            c = info["curve"].get(str(h), {})
            e, fw, w, n = c.get("excess"), c.get("fwd"), c.get("win"), c.get("n")
            if e is None:
                continue
            marg = f"(边际{e-prev:+.2%})" if prev is not None else ""
            ci = c.get("excess_ci95")
            ci_s = f" CI[{ci[0]:+.2%},{ci[1]:+.2%}]" if ci else ""
            sig = "✅" if ci and ci[0] > 0 else ("🔴" if ci and ci[1] < 0 else "⚪")
            print(f"  {h:>2}d    | {e:+.2%} | {fw:+.2%}  | {w:>4.0%} | {n}  {marg}{ci_s} {sig}")
            prev = e
        for y, yi in info.get("by_year", {}).items():
            line = "  ".join(f"h{h}={v:+.1%}" for h, v in yi["excess"].items() if v is not None)
            print(f"  {y}(n={yi['n']}): {line}")
    print("=====MOMENTUM_DECAY_END=====")


if __name__ == "__main__":
    main()
