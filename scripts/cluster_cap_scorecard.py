#!/usr/bin/env python3
"""簇门禁评分卡：组合回撤维度（2026-10-18 深挖 ①-C）。

为什么换维度：逐笔 fwd 收益评不出门禁价值（10-18 A/B：被拦笔 fwd5 反而更高）。
同簇持仓上限 ≤2 的定位是**尾部风险**，正确评分口 = 组合级回撤 / 同簇最大暴露 /
齐跌日损失，而不是单笔 alpha。

模型（日频、可复现、零网络）：
- 信号流: analysis/research/dual_signal_eval_days.json（生产信号融合源头）
- 建仓: 出现「买入」的票 → 次一交易日开盘买入（避免当日收盘不可知的乐观，
  沿用 execution_timing_study 的 T+1 开盘执行口径），等权 1/仅数 仓位
- 平仓: 持有 HOLD_DAYS 交易日收盘，或信号出现卖出 → 次日开盘卖
- 同时最多 MAX_POS 个仓位（超过则该买入跳过的规则不启用，用于观察簇暴露）
- 组合日收益 = 各持仓当日 close-to-close 加权平均（现金部分 0 收益）
- 三臂:
    prod : data/corr_clusters.json 全期映射（现行）
    roll : 逐日滚动 252d 相关簇
    off  : 不限制

指标:
  年化收益 / 最大回撤 / Calmar / 同簇最大同时持仓 / 齐跌日(池内≥70% 下跌)平均损失
  簇暴露占比 = 同簇同时持仓≥3 的日子占比
输出: analysis/research/<今日>_cluster_cap_scorecard.json
"""
import os
import sys
import json
from datetime import date
from itertools import combinations

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

CACHE = os.path.join(WORKSPACE, "data", "research_price_cache")
CLU_JSON = os.path.join(WORKSPACE, "data", "corr_clusters.json")
EVAL = os.path.join(WORKSPACE, "analysis", "research", "dual_signal_eval_days.json")
THRESHOLD = 0.55
ROLL = 252
MIN_PERIODS = 200
HOLD_DAYS = 10
CAP = 2
TOTAL_EXPO = 0.60   # 组合总仓位上限（近似 2.7M 参考仓位的实盘口径：非满仓）
MIN_SLOTS = 4        # 最少分散份数（空仓时单票不超过 TOTAL_EXPO/MIN_SLOTS）


def load_prices(syms):
    out = {}
    for s in syms:
        p = os.path.join(CACHE, f"{s}.pkl")
        if not os.path.exists(p):
            continue
        df = pd.read_pickle(p)
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        out[s] = df.set_index("date").sort_index()
    return out


def build_roll_maps(closes):
    ret = closes.pct_change()
    syms = list(closes.columns)
    dates = closes.index
    maps = {}
    for i in range(MIN_PERIODS, len(dates)):
        win = ret.iloc[max(0, i - ROLL + 1): i + 1]
        corr = win.corr(min_periods=MIN_PERIODS)
        parent = {s: s for s in syms}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in combinations(syms, 2):
            v = corr.loc[a, b]
            if pd.notna(v) and v >= THRESHOLD:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
        groups = {}
        for s in syms:
            groups.setdefault(find(s), []).append(s)
        m = {}
        for gi, g in enumerate(sorted([v for v in groups.values() if len(v) >= 2], key=len, reverse=True)):
            for s in g:
                m[s] = f"C{gi+1:02d}"
        maps[dates[i]] = m
    return maps


def run_arm(name, buys, sells, px, mapper, trading_days):
    """事件驱动日频重放。返回日权益序列与统计。"""
    day_ix = {d: i for i, d in enumerate(trading_days)}
    open_ = {s: df["open"].to_dict() for s, df in px.items()}
    close_ = {s: df["close"].to_dict() for s, df in px.items()}

    buys_by_day, sells_by_day = {}, {}
    for b in buys:
        i = day_ix.get(b["date"])
        if i is None or i + 1 >= len(trading_days):
            continue
        buys_by_day.setdefault(trading_days[i + 1], []).append(b["symbol"])
    for s in sells:
        i = day_ix.get(s["date"])
        if i is None or i + 1 >= len(trading_days):
            continue
        sells_by_day.setdefault(trading_days[i + 1], []).append(s["symbol"])

    held = {}          # sym -> {"entry": price, "exit_idx": int}
    equity = 1.0
    series, blocked_log, exposures = [], [], []
    n_blocked = 0

    for i, day in enumerate(trading_days):
        # 1) 卖出（次日开盘）
        for sym in sells_by_day.get(day, []):
            if sym in held:
                p = open_[sym].get(day)
                if p:
                    equity *= (1 + (p / held[sym]["entry"] - 1) * held[sym]["w"])
                del held[sym]
        # 到期平仓
        for sym in [s for s, h in held.items() if h["exit_idx"] <= i]:
            p = open_[sym].get(day) or close_[sym].get(day)
            if p:
                equity *= (1 + (p / held[sym]["entry"] - 1) * held[sym]["w"])
            del held[sym]
        # 2) 买入（次日开盘），簇上限判定
        m = mapper(day)
        for sym in buys_by_day.get(day, []):
            if sym in held:
                continue
            c = m.get(sym)
            if c:
                same = [o for o in held if m.get(o) == c]
                if len(same) >= CAP:
                    n_blocked += 1
                    blocked_log.append({"date": day, "symbol": sym, "cluster": c, "held_same": len(same)})
                    continue
            p = open_[sym].get(day)
            if not p:
                continue
            # 等权: 目标权重随持仓数摊薄，但单票不低于 TOTAL_EXPO/MIN_SLOTS
            w = TOTAL_EXPO / max(MIN_SLOTS, len(held) + 1)
            held[sym] = {"entry": p, "exit_idx": i + HOLD_DAYS, "w": w}

        # 3) 当日 mark-to-market 收益
        day_ret = 0.0
        for sym, h in held.items():
            c0 = close_[sym].get(trading_days[i - 1]) if i > 0 else h["entry"]
            c1 = close_[sym].get(day)
            if c0 and c1:
                day_ret += (c1 / c0 - 1) * h["w"]
        equity *= (1 + day_ret)
        series.append({"date": day, "equity": equity, "ret": day_ret, "n_pos": len(held)})

        # 簇暴露
        cnt = {}
        for o in held:
            cc = m.get(o)
            if cc:
                cnt[cc] = cnt.get(cc, 0) + 1
        maxc = max(cnt.values()) if cnt else 0
        exposures.append(maxc)

    ser = pd.DataFrame(series)
    ser["peak"] = ser["equity"].cummax()
    ser["dd"] = ser["equity"] / ser["peak"] - 1
    n_years = len(ser) / 244.0
    tot = ser["equity"].iloc[-1] - 1
    ann = (1 + tot) ** (1 / n_years) - 1 if n_years > 0 else 0
    mdd = ser["dd"].min()
    return {
        "arm": name,
        "total_return_pct": round(tot * 100, 2),
        "annualized_pct": round(ann * 100, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "calmar": round(ann / abs(mdd), 2) if mdd else None,
        # 风险标准化: 各臂同仓跑会天然有不同波动，按 10% 年化目标等比缩放后比较 MDD
        # (缩放后 MDD 线性近似) —— 只有缩放后的 MDD 差异才反映「同样风险预算下」门禁的真实代价
        "mdd_at_10pct_vol": round(mdd * (0.10 / (ser["ret"].std() * np.sqrt(244)) ) * 100, 2)
                            if ser["ret"].std() > 0 else None,
        "return_at_10pct_vol_pct": round(ann * (0.10 / (ser["ret"].std() * np.sqrt(244))) * 100, 2)
                                   if ser["ret"].std() > 0 else None,
        "vol_annual_pct": round(ser["ret"].std() * np.sqrt(244) * 100, 2),
        "avg_positions": round(ser["n_pos"].mean(), 2),
        "max_same_cluster": int(max(exposures) if exposures else 0),
        "days_cluster_ge2_pct": round(float(np.mean([e >= 2 for e in exposures]) * 100), 2),
        "days_cluster_ge3_pct": round(float(np.mean([e >= 3 for e in exposures]) * 100), 2),
        "blocked_buys": n_blocked,
        "_series": ser,
        "_blocked": blocked_log,
    }


def main():
    prod = json.load(open(CLU_JSON))["symbol_to_cluster"]
    recs = json.load(open(EVAL))
    syms = sorted({r["symbol"] for r in recs})
    px = load_prices(syms)
    syms = [s for s in syms if s in px]
    print(f"[sc] 票 {len(syms)} | 信号 {len(recs)}", flush=True)

    closes = pd.DataFrame({s: px[s]["close"] for s in syms}).sort_index()
    trading_days = [str(d) for d in closes.index]
    roll = build_roll_maps(closes)
    roll_days = sorted(roll.keys())
    print(f"[sc] 滚动映射 {len(roll_days)} 日", flush=True)

    def roll_at(day, _cache={}):
        if day in roll:
            return roll[day]
        prev = [d for d in roll_days if d <= day]
        return roll[prev[-1]] if prev else {}

    buys = [r for r in recs if any("买入" in a for a in r.get("actions", []))]
    sells = [r for r in recs if any("卖出" in a for a in r.get("actions", []))]
    print(f"[sc] 买 {len(buys)} / 卖 {len(sells)}", flush=True)

    arms = {}
    for name, mapper in (("prod", lambda d: prod), ("roll", roll_at), ("off", lambda d: {})):
        r = run_arm(name, buys, sells, px, mapper, trading_days)
        arms[name] = r
        print(f"[sc] {name:5s} 总收益 {r['total_return_pct']:7.2f}% | 年化 {r['annualized_pct']:6.2f}% | "
              f"MDD {r['max_drawdown_pct']:6.2f}% | Calmar {r['calmar']} | "
              f"最大同簇 {r['max_same_cluster']} | ≥3簇日 {r['days_cluster_ge3_pct']}% | 拦截 {r['blocked_buys']}",
              flush=True)

    # 齐跌日分解（池内 ≥70% 下跌的交易日）
    pool_ret = closes.pct_change().mean(axis=1)
    down_days = [str(d) for d in pool_ret[pool_ret < -0.005].index]
    print(f"[sc] 池内均跌>0.5% 的交易日 {len(down_days)} 天", flush=True)
    crash = {}
    for name, r in arms.items():
        ser = r["_series"].set_index("date")
        sel = ser.reindex([d for d in down_days if d in ser.index]).dropna(subset=["ret"])
        crash[name] = {
            "n_days": len(sel),
            "avg_loss_pct": round(float(sel["ret"].mean()) * 100, 3),
            "worst_day_pct": round(float(sel["ret"].min()) * 100, 3),
            "total_loss_pct": round(float(sel["ret"].sum()) * 100, 3),
        }
        print(f"[sc] {name:5s} 齐跌日 均损 {crash[name]['avg_loss_pct']:6.3f}% "
              f"最差 {crash[name]['worst_day_pct']:7.3f}% 累计 {crash[name]['total_loss_pct']:7.2f}%", flush=True)

    out = {"date": date.today().isoformat(), "window": f"{trading_days[0]}~{trading_days[-1]}",
           "hold_days": HOLD_DAYS, "cap": CAP, "total_expo": TOTAL_EXPO,
           "arms": {}, "down_day_decomposition": crash}
    for name, r in arms.items():
        out["arms"][name] = {k: v for k, v in r.items() if not k.startswith("_")}
        out["arms"][name]["blocked_examples"] = r["_blocked"][:15]
        out["arms"][name]["equity_curve"] = [
            {"date": x["date"], "equity": round(x["equity"], 5), "n_pos": x["n_pos"]}
            for x in r["_series"].to_dict("records")
        ]
    path = os.path.join(WORKSPACE, f"analysis/research/{date.today().isoformat()}_cluster_cap_scorecard.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[sc] 已写 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
