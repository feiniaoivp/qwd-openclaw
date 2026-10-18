#!/usr/bin/env python3
"""三因素门控（ResonanceGate）真实口径标定（2026-10-18）。

背景
----
旧研究（scripts/gate_threshold_study.py）用「技术分×0.45 + 中性50×0.55」的代理分
标定 GATE_BUY_LINE，结论是「55 线有区分度」→ 但该代理分方向性错误（见下），且情绪/
基本面两维的历史实值从**未**重建过，恒用 50 顶替，结论不可信。

本脚本重建真实的三因素历史，做严谨标定。

生产真口径（已读源码坐实）
--------------------------
生产三处调用都 `check_buy(symbol)` 不传 tech_score → tech 硬编码 90。
因此 buy_score = 90×0.45 + fund×0.30 + sent×0.25 = 40.5 + 0.30·fund + 0.25·sent。

关键事实（旧代理分错在哪里）：
  1. tech 是**常量** 90（贡献 40.5），不是变量 → 旧代理分把 tech 当变量是错的。
  2. fund/sent 才是**变量**（各自 0~100）→ 旧代理分把这两维当常量 50 也是错的。
  3. 门控实际只由「基本面 + 情绪」两维驱动，且确定性下限 40.5 分。

所以买分可能在 [40.5, 95.5] 区间，中性约 68。55 线意味着 fund+sent 加权只要
≥14.5 分就放行（0.30·fund+0.25·sent ≥ 14.5），门槛其实很低。

三维重建
--------
* 技术: 信号日生产同源，恒 90（还原真实）
* 基本面: scripts/fund_history_builder.py 产出的 research_fund_history.json，
          按「信号日当时已披露」的最新财报（防前视），用生产 _norm01 归一合成
* 情绪: 关注池 36 只**自算当日上涨占比**（面板 close.pct_change>0 占比），
        按生产公式 up_ratio×70 + active×30；active 用关注池当日平均「涨跌离差的
        活跃度」代理（active_norm = 关注池当日 |chg| 中位数映射，无全市场活跃度历史）。

       ⚠️ 情绪是关注池代理，不是乐咕全市场涨跌家数。这一点在结论里必须声明：
       关注池有板块集中（半导体/电网/券商），上涨占比可能偏离全市场真值。

统计
----
* 基准: 全池等权 5 日收益（按交易日块 bootstrap，抗同日相关）
* 阈值扫描: 拦截组(score<T) vs 放行组(score>=T) 的 5d 超额差 + bootstrap CI
* 分年稳健性: 2024/2025/2026 逐年验证方向一致
* 随机基准对照: 打乱 score 后的拦截差异分布（判断「有区分度」是否超出自然命中率）

铁律: 只读，绝不写 data/portfolio_*.json / portfolio_equity.csv

用法
----
  python3 scripts/gate_threshold_study_v2.py           # 全量
  python3 scripts/gate_threshold_study_v2.py --quick   # 抽样 300 预演
"""
import os
import sys
import json
import random
import statistics as st
from collections import defaultdict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd

from analysis.net_guard import install_default_timeout
install_default_timeout(15)

CACHE = os.path.join(WORKSPACE, "data", "research_price_cache")
FUND_JSON = os.path.join(WORKSPACE, "data", "research_fund_history.json")
OUT_DIR = os.path.join(WORKSPACE, "analysis", "research")
EVAL_JSON = os.path.join(OUT_DIR, "dual_signal_eval_days.json")
HOLD = 5
QUICK = "--quick" in sys.argv

# 生产权重 + tech 常量（三处调用均不传 tech_score → tech=90）
W_TECH, W_FUND, W_SENT = 0.45, 0.30, 0.25
TECH_CONST = 90.0


# ── 生产基本面归一（ResonanceGate._norm01 硬阈值口径）────────────────
def _norm01(v, lo, hi):
    if v is None or hi <= lo:
        return None
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def fund_score(row):
    """生产 single-point 归一合成 0~100。返回 None 表示数据缺失。"""
    roe = row.get("roe")
    yoy = row.get("yoy_pni")
    npm = row.get("np_margin")
    s_roe = _norm01(roe, 0.0, 0.25) if roe is not None else None       # 0~25%
    s_yoy = _norm01(yoy, -100.0, 100.0) if yoy is not None else None    # -100~100
    s_npm = _norm01(npm, 0.0, 30.0) if npm is not None else None        # 0~30%
    parts = [p for p in (s_roe, s_yoy, s_npm) if p is not None]
    if not parts:
        return None
    return sum(parts) / len(parts) * 100.0


# ── 情绪: 关注池自算上涨占比 ─────────────────────────────────────
def build_sentiment_panel(prices):
    """{date: up_ratio}。关注池 36 只当日 close.pct_change>0 占比。"""
    chgs = {}
    for sym, ser in prices.items():
        arr = ser.values
        ds = [str(x)[:10] for x in ser.index]
        for i in range(1, len(arr)):
            if arr[i - 1] > 0:
                chgs.setdefault(ds[i], []).append(1.0 if arr[i] > arr[i - 1] else 0.0)
    up_ratio = {}
    for d, v in chgs.items():
        if v:
            up_ratio[d] = sum(v) / len(v)
    return up_ratio


def sentiment_score_hist(date_str, up_ratio):
    """生产情绪分 = up_ratio×70 + active_norm×30。无活跃度历史 → active 用 0.5 中性。"""
    u = up_ratio.get(date_str)
    if u is None:
        return None
    return u * 70.0 + 0.5 * 30.0   # active_norm 中性 0.5（已声明局限）


def load_prices():
    out = {}
    for fn in os.listdir(CACHE):
        if not fn.endswith(".pkl") or fn.startswith("chip"):
            continue
        sym = fn[:-4]
        try:
            df = pd.read_pickle(os.path.join(CACHE, fn))
            if "date" not in df.columns or "close" not in df.columns:
                continue
            df = df.copy()
            df["_d"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            df = df[df["_d"] != "1970-01-01"].drop_duplicates("_d").sort_values("_d")
            out[sym] = df.set_index("_d")["close"].astype(float)
        except Exception as e:
            print(f"⚠️ {fn} 读取失败: {e}")
    return out


def load_fund_hist():
    if not os.path.exists(FUND_JSON):
        return {}
    with open(FUND_JSON) as f:
        raw = json.load(f)
    # 预排序：每只按 pub 升序
    out = {}
    for sym, recs in raw.items():
        out[sym] = sorted(recs, key=lambda r: r.get("pub", ""))
    return out


def fund_asof(sym, date_str, fund_hist):
    """取 signal 日 T 当时已披露的最新财报（pub <= T 的最后一条）。防前视。"""
    recs = fund_hist.get(sym)
    if not recs:
        return None
    best = None
    for r in recs:
        if r.get("pub", "") <= date_str:
            best = r
        else:
            break
    return best


def boot_ci_by_day(buckets_dict, n=4000, seed=42):
    """buckets_dict: {date: [ex5,...]}。按交易日块重采样。返回 (lo,hi) 或 None。"""
    days = list(buckets_dict)
    if len(days) < 5:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        pick = [buckets_dict[rng.choice(days)] for _ in days]
        flat = [x for g in pick for x in g]
        if flat:
            means.append(sum(flat) / len(flat))
    means.sort()
    return means[int(n * 0.025)], means[int(n * 0.975)]


def main():
    prices = load_prices()
    fund_hist = load_fund_hist()
    up_ratio = build_sentiment_panel(prices)

    with open(EVAL_JSON) as f:
        all_rows = json.load(f)
    sigs = [r for r in all_rows if any("买入" in str(a) for a in (r.get("actions") or []))]
    sigs.sort(key=lambda r: r["date"])
    if QUICK:
        step = max(1, len(sigs) // 300)
        sigs = sigs[::step][:300]

    print(f"价格 {len(prices)} 只 / 基本面历史 {len(fund_hist)} 只 / 信号 {len(sigs)} 条")

    _IDX = {sym: {str(d)[:10]: k for k, d in enumerate(ser.index)} for sym, ser in prices.items()}
    # 基准: 全池等权 5 日收益
    bench = defaultdict(list)
    for sym, ser in prices.items():
        arr = ser.values
        ds = [str(x)[:10] for x in ser.index]
        for i in range(len(arr) - HOLD):
            if arr[i] > 0:
                bench[ds[i]].append((arr[i + HOLD] / arr[i] - 1) * 100)
    bench5 = {d: sum(v) / len(v) for d, v in bench.items() if v}

    rows = []
    miss_fund = miss_sent = 0
    for s in sigs:
        sym = str(s.get("symbol") or "")
        d = str(s.get("date") or "")[:10]
        ser = prices.get(sym)
        if ser is None or sym not in _IDX or d not in _IDX[sym]:
            continue
        i = _IDX[sym][d]
        if i + HOLD >= len(ser.values):
            continue
        if d not in bench5:
            continue
        ret5 = (ser.values[i + HOLD] / ser.values[i] - 1) * 100
        ex5 = ret5 - bench5[d]
        # 基本面 as-of
        fr = fund_asof(sym, d, fund_hist)
        fund = fund_score(fr) if fr else None
        if fund is None:
            miss_fund += 1
        # 情绪
        sent = sentiment_score_hist(d, up_ratio)
        if sent is None:
            miss_sent += 1
        # 生产真实 buy_score（tech=90 常量）
        parts = {"tech": W_TECH * TECH_CONST}
        if fund is not None:
            parts["fund"] = W_FUND * fund
        if sent is not None:
            parts["sent"] = W_SENT * sent
        score = sum(parts.values())
        rows.append({"sym": sym, "date": d, "fund": fund, "sent": sent,
                     "score": round(score, 2), "ret5": round(ret5, 3), "ex5": round(ex5, 3)})

    if not rows:
        print("❌ 无可评估样本")
        return 1
    print(f"可评估 {len(rows)} 条（fund 缺失 {miss_fund}，sent 缺失 {miss_sent}）")

    # 量纲自检
    med = st.median([r["ex5"] for r in rows])
    if abs(med) > 20:
        print(f"❌ 超额量纲异常(中位 {med:+.1f}%)，终止")
        return 2

    exs = [r["ex5"] for r in rows]
    print(f"全样本 5d 超额: 均值 {st.mean(exs):+.2f}% 中位 {st.median(exs):+.2f}%")

    # buy_score 分布（关键：看门控分实际落在哪）
    scores = [r["score"] for r in rows]
    print(f"\n真实 buy_score 分布: min {min(scores):.1f} / 中位 {st.median(scores):.1f} "
          f"/ max {max(scores):.1f}")
    # 各分位占比（生产线 55 的实际拦截率）
    for T in (50, 55, 60, 65, 70):
        below = sum(1 for x in scores if x < T)
        print(f"  score < {T}: {below}/{len(scores)} = {below/len(scores):.1%}")

    # 按真实分分档
    buckets = defaultdict(list)
    for r in rows:
        buckets[int(r["score"] // 5) * 5].append(r["ex5"])
    print(f"\n分档（真实三因素 buy_score）:")
    print(f"{'区间':>12} {'n':>5} {'均值':>8} {'中位':>8}  CI95")
    by_day = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_day[int(r["score"] // 5) * 5][r["date"]].append(r["ex5"])
    for k in sorted(buckets):
        v = buckets[k]
        ci = boot_ci_by_day(by_day[k])
        ci_s = f"[{ci[0]:+.2f},{ci[1]:+.2f}]" if ci else "n/a"
        print(f"{k:>4}-{k+5:<5} {len(v):>5} {st.mean(v):>+7.2f}% {st.median(v):>+7.2f}%  {ci_s}")

    # 阈值扫描
    print(f"\n阈值扫描（拦截组=score<T）：")
    print(f"{'T':>5} {'拦截n':>6} {'拦截均值':>9} {'放行均值':>9} {'差(拦-放)':>10} {'CI95':>22}")
    scan = []
    for T in (50, 55, 60, 65, 70):
        blocked = [r for r in rows if r["score"] < T]
        passed = [r for r in rows if r["score"] >= T]
        if len(blocked) < 10 or len(passed) < 10:
            print(f"{T:>5} {len(blocked):>6}  样本不足跳过")
            continue
        bm = st.mean([r["ex5"] for r in blocked])
        pm = st.mean([r["ex5"] for r in passed])
        bd = defaultdict(lambda: ([], []))
        for r in rows:
            (bd[r["date"]][0] if r["score"] < T else bd[r["date"]][1]).append(r["ex5"])
        days = list(bd)
        diffs = []
        rng = random.Random(42)
        for _ in range(3000):
            pick = [bd[rng.choice(days)] for _ in days]
            b = [x for p in pick for x in p[0]]
            q = [x for p in pick for x in p[1]]
            if b and q:
                diffs.append(sum(b) / len(b) - sum(q) / len(q))
        diffs.sort()
        ci = f"[{diffs[len(diffs)//40]:+.2f},{diffs[-len(diffs)//40]:+.2f}]"
        sig_ = "显著" if (diffs[len(diffs)//40] > 0 or diffs[-len(diffs)//40] < 0) else "不显著"
        print(f"{T:>5} {len(blocked):>6} {bm:>+8.2f}% {pm:>+8.2f}% {bm-pm:>+9.2f}% {ci:>22} {sig_}")
        scan.append({"T": T, "blocked_n": len(blocked), "blocked_mean": round(bm, 3),
                     "passed_mean": round(pm, 3), "diff": round(bm - pm, 3),
                     "ci95": ci, "significant": sig_ == "显著"})

    # 分年稳健性（重点看 55 线的拦截组是否三年都负超额）
    print(f"\n分年稳健性（拦截组=score<55 的 5d 超额，逐年）：")
    yearly = defaultdict(list)
    for r in rows:
        yearly[r["date"][:4]].append((r["score"] < 55, r["ex5"]))
    for y in sorted(yearly):
        blk = [e for (isb, e) in yearly[y] if isb]
        pas = [e for (isb, e) in yearly[y] if not isb]
        print(f"  {y}: 拦截组 n={len(blk)} 均 {st.mean(blk) if blk else 0:+.2f}% | "
              f"放行组 n={len(pas)} 均 {st.mean(pas) if pas else 0:+.2f}%")

    # 随机基准对照：打乱 score 后，55 线的拦截差异分布（判断真区分度）
    print(f"\n随机基准对照（打乱 buy_score 后 55 线拦截差异的零分布）：")
    base_scores = [r["score"] for r in rows]
    ex_by_date = defaultdict(list)
    for r in rows:
        ex_by_date[r["date"]].append(r["ex5"])
    rng = random.Random(99)
    null_diffs = []
    days = list(ex_by_date)
    real_blk = st.mean([r["ex5"] for r in rows if r["score"] < 55])
    real_pas = st.mean([r["ex5"] for r in rows if r["score"] >= 55])
    real_diff = real_blk - real_pas
    for _ in range(2000):
        shuffled = rng.sample(base_scores, len(base_scores))
        b_ = [r["ex5"] for r, sc in zip(rows, shuffled) if sc < 55]
        p_ = [r["ex5"] for r, sc in zip(rows, shuffled) if sc >= 55]
        if b_ and p_:
            null_diffs.append(st.mean(b_) - st.mean(p_))
    null_diffs.sort()
    pct = sum(1 for x in null_diffs if x <= real_diff) / len(null_diffs) * 100
    print(f"  真实拦截差异 {real_diff:+.2f}%，零分布 2.5%={null_diffs[len(null_diffs)//40]:+.2f}% "
          f"97.5%={null_diffs[-len(null_diffs)//40]:+.2f}%（真实差异位于零分布 {pct:.1f} 分位）")

    out = {
        "date": __import__("datetime").date.today().isoformat(),
        "hold_days": HOLD, "n_signals": len(rows), "quick": QUICK,
        "miss_fund": miss_fund, "miss_sent": miss_sent,
        "overall_ex5_mean": round(st.mean(exs), 3),
        "overall_ex5_median": round(st.median(exs), 3),
        "score_dist": {"min": round(min(scores), 1), "median": round(st.median(scores), 1),
                       "max": round(max(scores), 1)},
        "caveat": [
            "tech 常量 90（生产三处 check_buy 均不传 tech_score，还原真实口径）",
            "基本面 = 信号日当时已披露的最新财报，用生产 _norm01 单点归一（防前视）",
            "情绪 = 关注池 36 只自算上涨占比×70 + 活跃度中性 0.5×30（非乐咕全市场涨跌家数，有板块集中偏差）",
        ],
        "buckets": [{"lo": k, "n": len(buckets[k]), "mean": round(st.mean(buckets[k]), 3),
                     "median": round(st.median(buckets[k]), 3)} for k in sorted(buckets)],
        "threshold_scan": scan,
        "random_baseline": {"real_diff": round(real_diff, 3),
                            "null_lo": round(null_diffs[len(null_diffs)//40], 3),
                            "null_hi": round(null_diffs[-len(null_diffs)//40], 3),
                            "real_percentile": round(pct, 1)},
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{out['date']}_gate_threshold_study_v2.json")
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n📄 落盘: {os.path.relpath(path, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
