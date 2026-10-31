#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deflated Sharpe Ratio（Bailey & López de Prado）—— 适配自 frank-quant/ai-trading-videos EP007
=================================================================================
回答一个问题:**这个夏普,有多少是「试了 N 次挑出最好的那次」挑出来的运气?**

搜的配置越多、各次成绩越分散 → 光靠运气就能刷出的最高夏普(SR0)越高 →
实际夏普必须显著高过 SR0,DSR 才接近 1。DSR < 0.95 通常视为「不显著」。

与原版(frank-quant EP007 scripts/deflated_sharpe.py)的差异:
  - 年化因子参数化,默认 252(A股交易日),原版 365(加密币 7x24)
  - 输入适配本工作区:可直接读 analysis/validation_YYYY-MM-DD.json 批量出每只票的运气基线
  - 原版读 Freqtrade hyperopt_results.json + backtest zip,那份原件保留在
    external/frank-quant-EP007/scripts/ 供溯源

⚠️ 口径注意(必须随结果说明):
  批量模式的 trials = 每只票 details 里各策略的「跨窗口均值」avg_sharpe(N=策略数,
  默认 5)。实际评估次数 = 策略数 × 窗口数(默认 20),均值化会抹掉窗口间噪声 →
  本口径的运气基线偏「乐观」(SR0 偏低、DSR 偏高、更容易判显著)。
  升级路径:validate_strategies.py 落盘逐窗口 trials 后,用 N=策略数×窗口数重算。

用法(主机上跑):
  # 批量:对最近一次跨窗口验证的全池出「best-of-N 运气基线」体检
  python3 analysis/dsr_sharpe.py --validation analysis/validation_2026-10-03.json

  # 单次:给一份 trials 列表 JSON(如 [0.3, 0.5, -0.1, ...]) + 可选日收益 CSV(date,close 或 date,total_value)
  python3 analysis/dsr_sharpe.py --trials-file data/my_trials.json --returns-csv data/portfolio_equity.csv
"""
import argparse
import json
import math
import os
import sys

import numpy as np
import pandas as pd
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

# validate_strategies.py 的展示名 → 策略 key 映射(单一来源,与生产一致)
KEY_MAP = {
    "布林带+ATR": "bollinger", "KDJ+CCI": "kdj_cci", "EMA+OBV": "ema_obv",
    "EMA12/26金叉(基准C)": "ema_cross", "纯MACD(基准B)": "macd",
    "牛市趋势跟踪": "bull_trend",
}


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p):
    lo, hi = -10.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if _norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def expected_max_sharpe(trials_sharpes, annual=252.0):
    """纯运气基线 SR0(年化),Bailey & López de Prado。trials_sharpes 为各次评估的年化夏普。"""
    sr = [float(x) for x in trials_sharpes if x is not None and np.isfinite(x)]
    N = max(len(sr), 1)
    if N < 2:
        raise ValueError("有效 trial 太少(<2),无法估计 SR 方差")
    sr_var = float(np.var(sr, ddof=1))
    e = math.e
    gamma = 0.5772156649
    sr0_daily = math.sqrt(sr_var / annual) * (
        (1 - gamma) * _norm_ppf(1 - 1.0 / N) + gamma * _norm_ppf(1 - 1.0 / (N * e))
    )
    return sr0_daily * math.sqrt(annual), sr_var, N


def dsr_from_returns(trials_sharpes, daily_returns, annual=252.0):
    """DSR:observed = 日收益序列算出的年化夏普;运气基线来自 trials。返回 (dsr, dict)。"""
    r = pd.Series(daily_returns, dtype=float).dropna()
    if len(r) < 30:
        raise ValueError(f"日收益样本太短 (T={len(r)}, 需≥30)")
    if r.std() == 0:
        raise ValueError("日收益无波动,DSR 不可用")
    sr_hat = float(r.mean() / r.std() * math.sqrt(annual))
    sr0, sr_var, N = expected_max_sharpe(trials_sharpes, annual)
    return _dsr_core(sr_hat, sr0, r, sr_var, N, annual)


def dsr_best_of_n(trials_sharpes, annual=252.0):
    """DSR(best-of-N 口径):observed = trials 里的最大值。无日收益序列时用这个近似。"""
    sr = [float(x) for x in trials_sharpes if x is not None and np.isfinite(x)]
    if len(sr) < 2:
        raise ValueError("有效 trial 太少(<2)")
    observed = max(sr)
    sr0, sr_var, N = expected_max_sharpe(sr, annual)
    # 无日收益序列,缺偏度/峰度修正与 T-1 缩放 → 退化为正态近似(标注在 verdict note)
    z = (observed - sr0)
    dsr = _norm_cdf(z / max(sr_var, 1e-9) ** 0.5 * math.sqrt(max(len(sr) - 1, 1)))
    out = {
        "n_trials": N,
        "sr_trials_var": round(sr_var, 4),
        "observed_sharpe_annual": round(observed, 3),
        "expected_max_sharpe_annual_by_luck": round(sr0, 3),
        "DSR": round(dsr, 4),
        "mode": "best_of_n(无日收益,未做偏度/峰度/T-1修正,近似口径)",
        "verdict": ("显著(DSR≥0.95)" if dsr >= 0.95 else
                    "偏弱(0.9≤DSR<0.95)" if dsr >= 0.90 else
                    "不显著 —— 大概率是挑出来的运气"),
    }
    return dsr, out


def _dsr_core(sr_hat, sr0, r, sr_var, N, annual):
    sr_daily = float(r.mean() / r.std())
    g3 = float(r.skew())
    g4 = float(r.kurt()) + 3.0  # pandas 给的是超额峰度
    denom = 1.0 - g3 * sr_daily + (g4 - 1.0) / 4.0 * sr_daily ** 2
    if denom <= 0:
        raise ValueError("分母非正(收益分布极端),DSR 不可用")
    z = (sr_daily - sr0) * math.sqrt(len(r) - 1) / math.sqrt(denom)
    dsr = _norm_cdf(z)
    out = {
        "n_trials": N,
        "sr_trials_var": round(sr_var, 4),
        "observed_sharpe_annual": round(sr_hat, 3),
        "expected_max_sharpe_annual_by_luck": round(sr0, 3),
        "T_days": len(r), "skew": round(g3, 3), "kurtosis": round(g4, 3),
        "DSR": round(dsr, 4),
        "mode": "full(含偏度/峰度修正与T-1缩放)",
        "verdict": ("显著(DSR≥0.95)" if dsr >= 0.95 else
                    "偏弱(0.9≤DSR<0.95)" if dsr >= 0.90 else
                    "不显著 —— 大概率是搜出来的运气"),
    }
    return dsr, out


def load_returns_csv(path):
    """从 CSV 还原日收益序列:支持 (date,total_value) / (date,close) 两种列。"""
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    if "date" not in cols:
        raise ValueError(f"CSV 缺 date 列: {path}")
    val_col = None
    for cand in ("total_value", "close", "total_return_pct"):
        if cand in cols:
            val_col = cols[cand]
            break
    if val_col is None:
        raise ValueError(f"CSV 无 total_value/close 列: {path} (现有列: {list(df.columns)})")
    s = pd.to_numeric(df[val_col], errors="coerce")
    if val_col.lower() == "total_return_pct":
        # 累计收益率% → 还原成日收益
        r = s.diff() / 100.0
    else:
        r = s.pct_change()
    idx = pd.to_datetime(df[cols["date"]], errors="coerce")
    return pd.Series(r.values, index=idx).dropna()


def run_batch(validation_path, annual, out_path):
    """批量模式:读 validation JSON,每只票出 best-of-N 运气基线体检。
    优先 dsr_trials(逐策略×逐窗口,N=实际评估次数);旧文件无该字段时
    退化到 details 均值近似(N=策略数,SR0 偏低/DSR 偏高,逐票标注 mode)。"""
    d = json.load(open(validation_path, encoding="utf-8"))
    rows, n_sig, n_weak, n_insig, n_skip = [], 0, 0, 0, 0
    for symbol, v in d.items():
        details = v.get("details") or []
        selected = v.get("strategy")
        sel_display = next((x.get("strategy") for x in details
                            if KEY_MAP.get(x.get("strategy")) == selected), None)
        sel_sharpe = next((x.get("avg_sharpe") for x in details if x.get("strategy") == sel_display),
                          None) if sel_display else None
        n_windows = int((v.get("windows_used") or 0) or (details[0].get("windows") if details else 0) or 0)
        # 精确口径优先:dsr_trials 拉平后 >=2 个样本才启用
        trials, precise = None, False
        dsr_trials = v.get("dsr_trials") or []
        if dsr_trials:
            flat = [x for item in dsr_trials for x in (item.get("sharpes") or [])
                    if x is not None and np.isfinite(x)]
            if len(flat) >= 2:
                trials, precise = flat, True
        if trials is None:
            trials = [x.get("avg_sharpe") for x in details if x.get("avg_sharpe") is not None]
        mode = "precise" if precise else "approx(偏乐观)"
        row = {"symbol": symbol, "name": v.get("name"), "selected": selected,
               "selected_display": sel_display, "selected_sharpe": sel_sharpe,
               "mode": mode, "windows_per_strategy": n_windows,
               "n_evals_actual": len(trials) if precise else len(trials) * (n_windows or 1)}
        if len(trials) < 2:
            row["status"] = "skip:trials<2"
            n_skip += 1
            rows.append(row)
            continue
        try:
            _, out = dsr_best_of_n(trials, annual)
        except ValueError as exc:
            row["status"] = f"skip:{exc}"
            n_skip += 1
            rows.append(row)
            continue
        row.update(out)
        row["mode"] = mode
        row["status"] = "ok"
        if "显著" in out["verdict"] and "不显著" not in out["verdict"]:
            n_sig += 1
        elif "偏弱" in out["verdict"]:
            n_weak += 1
        else:
            n_insig += 1
        rows.append(row)

    L = ["# Deflated Sharpe 全池体检(best-of-N 运气基线)", "",
         f"> 生成: 本脚本 dsr_sharpe.py ｜ 输入: {validation_path}",
         f"> 口径: 优先 dsr_trials(逐策略×逐窗口,N=实际评估次数);无该字段的旧文件退化为策略均值近似(逐票标注 mode)。",
         "> ⚠️ approx 口径的运气基线偏乐观(均值化抹掉窗口间噪声 → SR0 偏低 → DSR 偏高)。", "",
         "| 股票 | 选中策略 | N(trials) | 口径 | 观测夏普 | 运气基线SR0 | DSR | 判定 |",
         "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["status"] != "ok":
            L.append(f"| {r['symbol']} {r.get('name','')} | {r.get('selected','')} | — | — | — | — | — | {r['status']} |")
            continue
        L.append(f"| {r['symbol']} {r.get('name','')} | {r.get('selected_display') or r.get('selected')} "
                 f"| {r['n_trials']} | {r['mode']} | {r['observed_sharpe_annual']} "
                 f"| {r['expected_max_sharpe_annual_by_luck']} | {r['DSR']} | {r['verdict']} |")
    L += ["", f"## 汇总",
          f"- 有效体检: {n_sig + n_weak + n_insig} 只(跳过 {n_skip} 只)",
          f"- 显著(DSR≥0.95): {n_sig} 只 ｜ 偏弱(0.9~0.95): {n_weak} 只 ｜ 不显著: {n_insig} 只",
          "", "## 读法",
          "- DSR 高只说明「在 N 次挑选里这个夏普不全是运气」,不构成对该策略真实期望收益的背书。",
          "- 被判「不显著」的票,其选中策略的历史夏普与随机挑一次不可区分,换策略/调参需额外谨慎。",
          "- approx 票的「显著」在精确口径下可能降级;2026-10-05 起 validate_strategies.py 已落盘 dsr_trials 字段。"]
    txt = "\n".join(L) + "\n"
    print(txt)
    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        _bak(out_path)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(txt)
        json.dump(rows, open(out_path.replace(".md", ".json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2, default=str)
        print(f"[dsr] -> {out_path} (+.json)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validation", default=None, help="validation_*.json 批量模式")
    ap.add_argument("--trials-file", default=None, help="trials 列表 JSON(单次模式)")
    ap.add_argument("--returns-csv", default=None, help="日收益 CSV(date,total_value 或 date,close)")
    ap.add_argument("--annual", type=float, default=252.0, help="年化因子(A股252,加密365)")
    ap.add_argument("--out", default=None, help="落盘路径(批量模式建议 .md)")
    a = ap.parse_args()

    try:
        if a.validation:
            run_batch(a.validation, a.annual, a.out)
            return
        if a.trials_file:
            trials = json.load(open(a.trials_file, encoding="utf-8"))
            if not isinstance(trials, list):
                raise ValueError("--trials-file 必须是数字列表 JSON")
            if a.returns_csv:
                r = load_returns_csv(a.returns_csv)
                _, out = dsr_from_returns(trials, r, a.annual)
            else:
                _, out = dsr_best_of_n(trials, a.annual)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            if a.out:
                os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
                json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                print(f"[dsr] -> {a.out}")
            return
        print("用法: --validation <json> 批量模式,或 --trials-file <json> [--returns-csv <csv>] 单次模式")
        sys.exit(2)
    except ValueError as exc:
        print(f"[dsr] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
