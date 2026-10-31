#!/usr/bin/env python3
"""门控情绪维：全市场涨跌家数 vs 关注池代理 的标定对比（2026-10-18）。

对比两种情绪口径对门控标定的影响：
  A. 关注池 36 只自算上涨占比（上一版 gate_threshold_study_v2 的代理）
  B. 全市场沪深 A 股涨跌家数（market_breadth_builder 重建，对齐乐咕口径）

复用 v2 的基本面 + 技术常量口径，唯一变量是情绪维。
输出两套 threshold_scan + 分档，供直接对比。
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
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)

install_default_timeout(15)

CACHE = os.path.join(WORKSPACE, "data", "research_price_cache")
FUND_JSON = os.path.join(WORKSPACE, "data", "research_fund_history.json")
BREADTH_JSON = os.path.join(WORKSPACE, "data", "market_breadth_history.json")
OUT_DIR = os.path.join(WORKSPACE, "analysis", "research")
EVAL_JSON = os.path.join(OUT_DIR, "dual_signal_eval_days.json")
HOLD = 5

W_TECH, W_FUND, W_SENT = 0.45, 0.30, 0.25
TECH_CONST = 90.0


def _norm01(v, lo, hi):
    if v is None or hi <= lo:
        return None
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def fund_score(row):
    roe, yoy, npm = row.get("roe"), row.get("yoy_pni"), row.get("np_margin")
    s_roe = _norm01(roe, 0.0, 0.25) if roe is not None else None
    s_yoy = _norm01(yoy, -100.0, 100.0) if yoy is not None else None
    s_npm = _norm01(npm, 0.0, 30.0) if npm is not None else None
    parts = [p for p in (s_roe, s_yoy, s_npm) if p is not None]
    return sum(parts) / len(parts) * 100.0 if parts else None


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
            print(f"⚠️ {fn}: {e}")
    return out


def load_fund():
    with open(FUND_JSON) as f:
        raw = json.load(f)
    return {s: sorted(v, key=lambda r: r.get("pub", "")) for s, v in raw.items()}


def load_breadth():
    if not os.path.exists(BREADTH_JSON):
        return {}
    with open(BREADTH_JSON) as f:
        return json.load(f)


def fund_asof(sym, d, fund_hist):
    recs = fund_hist.get(sym)
    if not recs:
        return None
    best = None
    for r in recs:
        if r.get("pub", "") <= d:
            best = r
        else:
            break
    return best


def build_pool_sentiment(prices):
    chgs = defaultdict(list)
    for sym, ser in prices.items():
        a = ser.values
        ds = [str(x)[:10] for x in ser.index]
        for i in range(1, len(a)):
            if a[i - 1] > 0:
                chgs[ds[i]].append(1.0 if a[i] > a[i - 1] else 0.0)
    return {d: sum(v) / len(v) for d, v in chgs.items() if v}


def active_score_from_breadth(median_abs_chg):
    """把 |涨跌幅|中位数 映射到 0~100 活跃度。

    乐咕活跃度 58.57% 量纲未知，本处用「|chg|中位数」的单调映射：
    median 0~5% → 0~100（5% 视为极度活跃/高波动）。保守中性基准 0.5。
    """
    if median_abs_chg is None:
        return None
    return max(0.0, min(1.0, median_abs_chg / 5.0))


def sent_score(up_ratio, active_norm):
    if up_ratio is None:
        return None
    a = active_norm if active_norm is not None else 0.5
    return up_ratio * 70.0 + a * 30.0


def boot_ci_by_day(bd, n=4000, seed=42):
    days = list(bd)
    if len(days) < 5:
        return None
    rng = random.Random(seed)
    ms = []
    for _ in range(n):
        pick = [bd[rng.choice(days)] for _ in days]
        flat = [x for g in pick for x in g]
        if flat:
            ms.append(sum(flat) / len(flat))
    ms.sort()
    return ms[int(n * 0.025)], ms[int(n * 0.975)]


def run(mode):
    """mode in {'pool', 'market'}。返回 rows（含 score/ex5/date）。"""
    prices = load_prices()
    fund_hist = load_fund()
    breadth = load_breadth()
    pool_sent = build_pool_sentiment(prices)

    with open(EVAL_JSON) as f:
        all_rows = json.load(f)
    sigs = [r for r in all_rows if any("买入" in str(a) for a in (r.get("actions") or []))]
    sigs.sort(key=lambda r: r["date"])

    _IDX = {sym: {str(d)[:10]: k for k, d in enumerate(ser.index)} for sym, ser in prices.items()}
    bench = defaultdict(list)
    for sym, ser in prices.items():
        a = ser.values
        ds = [str(x)[:10] for x in ser.index]
        for i in range(len(a) - HOLD):
            if a[i] > 0:
                bench[ds[i]].append((a[i + HOLD] / a[i] - 1) * 100)
    bench5 = {d: sum(v) / len(v) for d, v in bench.items() if v}

    rows = []
    miss = 0
    for s in sigs:
        sym = str(s.get("symbol") or "")
        d = str(s.get("date") or "")[:10]
        ser = prices.get(sym)
        if ser is None or sym not in _IDX or d not in _IDX[sym]:
            continue
        i = _IDX[sym][d]
        if i + HOLD >= len(ser.values) or d not in bench5:
            continue
        fr = fund_asof(sym, d, fund_hist)
        fund = fund_score(fr) if fr else None
        # 情绪（两种口径）
        if mode == "pool":
            u = pool_sent.get(d)
            active = None
        else:
            b = breadth.get(d)
            if b:
                # 2026-10-18 口径统一（验证发现的不一致）：生产用 up/(up+down) 忽略平盘，
                # 旧版读 builder 落盘的 up_ratio（含平盘）→ 两口径差均值 1.2pp。
                # 现统一到生产口径；重算后结论不变且更强（-2.17% → -3.28% with active 中性）。
                u = b["up"] / (b["up"] + b["down"]) if (b["up"] + b["down"]) > 0 else None
                active = active_score_from_breadth(b.get("active_median_abs_chg"))
            else:
                u = None
                active = None
        sent = sent_score(u, active)
        if sent is None:
            miss += 1
        score = W_TECH * TECH_CONST
        if fund is not None:
            score += W_FUND * fund
        if sent is not None:
            score += W_SENT * sent
        ex5 = (ser.values[i + HOLD] / ser.values[i] - 1) * 100 - bench5[d]
        rows.append({"sym": sym, "date": d, "fund": fund, "sent": sent,
                     "score": round(score, 2), "ex5": round(ex5, 3)})
    print(f"[{mode}] 可评估 {len(rows)} 条（sent 缺失 {miss}）")
    return rows


def report(rows, label):
    exs = [r["ex5"] for r in rows]
    scores = [r["score"] for r in rows]
    print(f"\n{'='*60}\n{label}")
    print(f"  n={len(rows)}  buy_score [{min(scores):.1f}, {st.median(scores):.1f}, {max(scores):.1f}]")
    for T in (50, 55, 60):
        below = sum(1 for x in scores if x < T)
        print(f"    score<{T}: {below}/{len(scores)} = {below/len(scores):.1%}")
    # 分档
    buckets = defaultdict(list)
    for r in rows:
        buckets[int(r["score"] // 5) * 5].append(r["ex5"])
    print(f"  分档超额:")
    for k in sorted(buckets):
        v = buckets[k]
        print(f"    {k:>3}-{k+5:<3} n={len(v):>4} mean={st.mean(v):>+6.2f}% med={st.median(v):>+6.2f}%")
    # 阈值扫描（关键三档）
    print(f"  阈值扫描:")
    out = {}
    for T in (50, 55, 60):
        blk = [r for r in rows if r["score"] < T]
        pas = [r for r in rows if r["score"] >= T]
        if len(blk) < 10 or len(pas) < 10:
            print(f"    T={T}: 样本不足")
            continue
        bm = st.mean([r["ex5"] for r in blk])
        pm = st.mean([r["ex5"] for r in pas])
        bd = defaultdict(lambda: ([], []))
        for r in rows:
            (bd[r["date"]][0] if r["score"] < T else bd[r["date"]][1]).append(r["ex5"])
        days = list(bd)
        rng = random.Random(42)
        diffs = []
        for _ in range(3000):
            pick = [bd[rng.choice(days)] for _ in days]
            b = [x for p in pick for x in p[0]]
            q = [x for p in pick for x in p[1]]
            if b and q:
                diffs.append(sum(b) / len(b) - sum(q) / len(q))
        diffs.sort()
        ci = f"[{diffs[len(diffs)//40]:+.2f},{diffs[-len(diffs)//40]:+.2f}]"
        sig = "显著" if (diffs[len(diffs)//40] > 0 or diffs[-len(diffs)//40] < 0) else "不显著"
        print(f"    T={T}: 拦截{len(blk)}均{bm:+.2f}% 放行{len(pas)}均{pm:+.2f}% 差{bm-pm:+.2f}% {ci} {sig}")
        out[T] = {"blocked_n": len(blk), "diff": round(bm - pm, 3), "ci": ci, "sig": sig}
    return out


def main():
    pool_rows = run("pool")
    market_rows = run("market")
    r_pool = report(pool_rows, "【A. 关注池代理情绪】")
    r_market = report(market_rows, "【B. 全市场涨跌家数情绪】")

    # 落盘对比
    from datetime import date
    out = {
        "date": date.today().isoformat(),
        "pool": {"n": len(pool_rows), "scan": r_pool},
        "market": {"n": len(market_rows), "scan": r_market},
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{date.today().isoformat()}_sentiment_caliber_compare.json")
    _bak(path)
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n📄 落盘: {os.path.relpath(path, WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
