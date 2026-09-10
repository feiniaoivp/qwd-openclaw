#!/usr/bin/env python3
"""
候选股票评估脚本 — 特高压/输变电出海标的
=========================================
对三只候选股(不在现有关注池)做跨周期多窗口验证, 复用 validate_strategies 的
fetch_long/slice_window + backtest_strategies 的 run_simulation/ALL_STRATEGIES。
输出: 每只的最优稳定策略 + 得分 + 夏普/收益/回撤 + 与现池股票对比建议。

候选股:
  特变电工 600089 | 平高电气 600312 | 思源电气 300207
"""

import os, sys, json, warnings, datetime
import numpy as np

warnings.filterwarnings("ignore")
WORKSPACE = "/Users/duguke/.openclaw/workspace"
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

import baostock as bs
from validate_strategies import (
    WIN_START, WINDOWS, baostock_code, slice_window,
)
from backtest_strategies import run_simulation, ALL_STRATEGIES

# 只用 5 个核心价量策略 (与关注池口径一致), 剔除指数基准/买入持有/牛市(它们连 baostock 或非价量)
EVAL_STRATEGIES = [s for s in ALL_STRATEGIES if s[0] in (
    "布林带+ATR", "KDJ+CCI", "EMA+OBV", "EMA12/26金叉(基准C)", "纯MACD(基准B)",
)]

# 多窗口用全部 WINDOWS
EVAL_WINDOWS = WINDOWS


CANDIDATES = [
    ("600089", "特变电工"),
    ("600312", "平高电气"),
    ("300207", "思源电气"),
]


def load_csv(symbol):
    """从预先拉好的 baostock CSV 读取日线(独立进程拉得, 避免 baostock 会话连接断)"""
    import pandas as pd
    path = f"/tmp/cand_{symbol}.csv"
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    if df.empty or len(df) < 150:
        return None
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    return df


# 现池对应策略映射 (用于对比, 判断是否达到纳入门槛)
# 门槛参考: 验证脚本护栏 score>=25 且 夏普>=0.25(高样本); 低样本(<6笔)需夏普>=0.40
GATE_SCORE = 25
GATE_SHARPE_HIGH = 0.40   # 低样本(<6笔)
GATE_SHARPE_LOW = 0.25    # 高样本(>=6笔)

def baostock_login():
    login = bs.login()
    if login.error_code != "0":
        print("❌ baostock 登录失败:", login.error_msg)
        sys.exit(1)

def evaluate_stock(symbol, name):
    print(f"\n{'='*70}")
    print(f"📊 {name}({symbol}) — 拉取长历史 {WIN_START} 起 ...")
    df = load_csv(symbol)
    if df is None or len(df) < 150:
        print(f"  ❌ 数据不足，跳过")
        return {"error": "数据不足"}

    # 按窗口运行所有策略
    stock_out = {"name": name, "windows": {}}
    for wname, years_back, _ in EVAL_WINDOWS:
        wdf = slice_window(df, years_back, 0)
        wmetrics = {}
        for sname, sfunc in EVAL_STRATEGIES:
            try:
                actions = sfunc(wdf)
                if actions is None:
                    actions = []
                m = run_simulation(wdf, actions)
                m["signals"] = len([a for a in actions if a["type"] == "BUY"])
                m["trades"] = m.get("total_trades", 0)
                wmetrics[sname] = m
            except Exception as e:
                wmetrics[sname] = {"error": str(e)}
        stock_out["windows"][wname] = wmetrics

    # 跨窗口稳定性评分 (同 validate_strategies)
    strat_points = {}
    for sname, _ in EVAL_STRATEGIES:
        pts = {"returns": [], "sharpes": [], "dd": [], "trades": [], "cnt": 0}
        for wname in [w for w, _, _ in EVAL_WINDOWS]:
            m = stock_out["windows"].get(wname, {}).get(sname, {})
            if "error" in m:
                continue
            pts["returns"].append(m.get("total_return_pct", 0))
            pts["sharpes"].append(m.get("sharpe_ratio", 0))
            pts["dd"].append(m.get("max_drawdown_pct", 0))
            pts["trades"].append(m.get("trades", 0))
            pts["cnt"] += 1
        strat_points[sname] = pts

    scored = []
    for sname, pts in strat_points.items():
        if pts["cnt"] < 2:
            continue
        avg_ret = np.mean(pts["returns"])
        avg_sharpe = np.mean(pts["sharpes"])
        avg_dd = np.mean(pts["dd"])
        avg_trades = np.mean(pts["trades"])
        if avg_trades >= 10:
            sample_weight = 1.0
        elif avg_trades >= 6:
            sample_weight = 0.75
        elif avg_trades >= 3:
            sample_weight = 0.4
        else:
            sample_weight = 0.15
        dd_penalty = max(0, (abs(avg_dd) - 30)) * 0.5
        raw = avg_sharpe * 60 + avg_ret * 0.6 - dd_penalty
        score = raw * sample_weight
        scored.append({
            "strategy": sname, "score": round(score, 2),
            "avg_return": round(avg_ret, 2),
            "avg_sharpe": round(avg_sharpe, 3),
            "avg_dd": round(avg_dd, 2),
            "avg_trades": round(avg_trades, 1),
            "sample_weight": sample_weight,
            "windows": pts["cnt"],
        })

    if not scored:
        return {"error": "所有策略无有效窗口"}

    scored.sort(key=lambda x: x["score"], reverse=True)
    best = scored[0]
    key_map = {
        "布林带+ATR": "bollinger", "KDJ+CCI": "kdj_cci", "EMA+OBV": "ema_obv",
        "EMA12/26金叉(基准C)": "ema_cross", "纯MACD(基准B)": "macd",
        "牛市趋势跟踪": "bull_trend",
    }
    result = {
        "name": name, "strategy": key_map.get(best["strategy"], "ema_cross"),
        "strategy_cn": best["strategy"], "score": best["score"],
        "avg_return": best["avg_return"], "avg_sharpe": best["avg_sharpe"],
        "avg_dd": best["avg_dd"], "avg_trades": best["avg_trades"],
        "windows_used": best["windows"], "details": scored[:3],
    }

    # 护栏判定
    sharpe_thr = GATE_SHARPE_HIGH if result["avg_trades"] < 6 else GATE_SHARPE_LOW
    result["pass"] = result["score"] >= GATE_SCORE and result["avg_sharpe"] >= sharpe_thr
    result["sharpe_threshold"] = sharpe_thr
    print(f"  ✅ 最优 {best['strategy']} 得分{best['score']} "
          f"(夏普{best['avg_sharpe']} 收益{best['avg_return']}% 回撤{best['avg_dd']}% 交易{best['avg_trades']}笔)")
    print(f"  {'✅ 通过纳入护栏' if result['pass'] else '❌ 未达护栏门槛'}"
          f" (得分需≥{GATE_SCORE}, 夏普需≥{sharpe_thr})")
    for d in scored[:3]:
        print(f"      └ {d['strategy']}: 得分{d['score']} 夏普{d['avg_sharpe']} 收益{d['avg_return']}% 回撤{d['avg_dd']}% 交易{d['avg_trades']}笔")
    return result

def main():
    results = {}
    for symbol, name in CANDIDATES:
        results[symbol] = evaluate_stock(symbol, name)

    # 汇总
    print("\n" + "=" * 70)
    print("📋 候选股评估汇总")
    print("=" * 70)
    for symbol, r in results.items():
        if "error" in r:
            print(f"  {symbol}: {r['error']}")
            continue
        flag = "✅纳入" if r["pass"] else "❌不纳入"
        print(f"  {r['name']}({symbol}) [{flag}] {r['strategy_cn']} 得分{r['score']} "
              f"夏普{r['avg_sharpe']} 收益{r['avg_return']}% 回撤{r['avg_dd']}%")

    # 保存
    outfile = os.path.join(WORKSPACE, "analysis", "eval_candidates_uhv.json")
    def _clean(o):
        import math
        import numpy as np
        if isinstance(o, (np.floating, np.integer, float)):
            o = float(o)
            if math.isnan(o) or math.isinf(o):
                return None
            return o
        if isinstance(o, bool) or o is None:
            return o
        if isinstance(o, (int, str)):
            return o
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_clean(v) for v in o]
        return str(o)
    with open(outfile, "w", encoding="utf-8") as f:
        json.dump({"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                   "gate": {"score": GATE_SCORE, "sharpe_high": GATE_SHARPE_HIGH, "sharpe_low": GATE_SHARPE_LOW},
                   "results": _clean(results)}, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 结果已保存: {outfile}")

if __name__ == "__main__":
    main()
