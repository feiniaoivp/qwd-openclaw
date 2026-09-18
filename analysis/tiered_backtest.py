#!/usr/bin/env python3
"""
分层回测对比：A/B/C 三组
=================================
A组：原 adaptive 全套（每只股票用其最优择时策略全天候执行）
B组：分层框架
  - Tier1 (21只)：买入持有，不择时卖出
  - Tier2 (7只)：仅跑择时止损，不追涨买入
  - Tier3 (7只)：空仓，不配置
C组：全池子买入持有（等权）

数据源：analysis/timing_vs_hold_20260916.json (35只完整数据)
"""

import json
import os
from datetime import datetime

WORKSPACE = "/Users/duguke/.openclaw/workspace"

# ── 分层定义（基于 timing_vs_hold 结论）──
TIER1_HOLD = [  # 21只：适合持有，择时跑赢 0/5 或极少
    "300285", "603308", "002156", "002028", "601100", "601061",
    "002335", "600160", "300748", "000708", "600036",
    "600584", "688981", "300014", "002466", "002413",
    "000987", "600660", "605566", "600346", "601066",  # 中信建投虽判"都不行"但B&H为负，归Tier2更合理，这里暂按verdict
]

TIER2_TIMING = [  # 7只：适合择时（主要是下跌票，择时=风控）
    "600570", "000400", "300124", "002318", "300719", "605566", "000157"
]

TIER3_EXCLUDE = [  # 7只：都不行，剔除
    "600030", "601066", "601995", "600406", "002130", "002270", "600312"
]

# 修正：中信建投verdict是"都不行"但B&H为负，应归Tier2
# 再核对一次verdict
TIER1_HOLD = [
    "300285", "603308", "002156", "002028", "601100", "601061",
    "002335", "600160", "300748", "000708", "600036",
    "600584", "688981", "300014", "002466", "002413",
    "000987", "600660", "600346",  # 19只
]

TIER2_TIMING = [
    "600570", "000400", "300124", "002318", "300719", "605566", "000157",
    "601066",  # 中信建投：B&H负，择时能少亏
]

TIER3_EXCLUDE = [
    "600030", "601995", "600406", "002130", "002270", "600312",
    "600584",  # 长电科技：verdict适合持有但已在Tier1
    "601100",  # 恒立液压：verdict适合持有
]

# 等等，重新按verdict分：
# 从timing_vs_hold读取verdict更准确

def load_data():
    with open(os.path.join(WORKSPACE, "analysis", "timing_vs_hold_20260916.json"), "r") as f:
        return json.load(f)["results"]

def classify_by_verdict(data):
    tier1, tier2, tier3 = [], [], []
    for sym, info in data.items():
        v = info.get("verdict", "")
        if "适合持有" in v:
            tier1.append(sym)
        elif "适合择时" in v:
            tier2.append(sym)
        elif "都不行" in v:
            tier3.append(sym)
    return tier1, tier2, tier3

def get_best_strategy_oos(info):
    """取OOS表现最好的策略收益"""
    best_ret = -999
    for sname, sdata in info["strategies"].items():
        if "oos" in sdata and "total_return_pct" in sdata["oos"]:
            ret = sdata["oos"]["total_return_pct"]
            if ret > best_ret:
                best_ret = ret
    return best_ret if best_ret > -999 else 0

def get_bh_oos(info):
    return info["bh_oos"]["total_return_pct"]

def get_bh_full(info):
    return info["bh_full"]["total_return_pct"]

def simulate_group_a(data):
    """A组：每只股票用其OOS最优择时策略"""
    returns = []
    for sym, info in data.items():
        ret = get_best_strategy_oos(info)
        returns.append(ret)
    return returns

def simulate_group_b(data, tier1, tier2, tier3):
    """B组：分层框架"""
    returns = []
    # Tier1: 买入持有
    for sym in tier1:
        if sym in data:
            returns.append(get_bh_oos(data[sym]))
    # Tier2: 择时止损（取OOS最优择时，但仅作风控理解，这里用择时收益）
    for sym in tier2:
        if sym in data:
            ret = get_best_strategy_oos(data[sym])
            returns.append(ret)
    # Tier3: 空仓 = 0收益
    for sym in tier3:
        returns.append(0.0)
    return returns

def simulate_group_c(data):
    """C组：全池子买入持有"""
    returns = []
    for sym, info in data.items():
        returns.append(get_bh_oos(info))
    return returns

def simulate_group_c_full(data):
    """C组全样本：全池子买入持有"""
    returns = []
    for sym, info in data.items():
        returns.append(get_bh_full(info))
    return returns

def stats(returns, name):
    if not returns:
        return {}
    import numpy as np
    arr = np.array(returns)
    return {
        "name": name,
        "n": len(arr),
        "avg_return": round(np.mean(arr), 2),
        "median_return": round(np.median(arr), 2),
        "std_return": round(np.std(arr), 2),
        "min_return": round(np.min(arr), 2),
        "max_return": round(np.max(arr), 2),
        "win_rate": round(np.sum(arr > 0) / len(arr) * 100, 1),
        "total_return_eq": round(np.mean(arr), 2),  # 等权组合近似
    }

def main():
    data = load_data()
    tier1, tier2, tier3 = classify_by_verdict(data)
    
    print("=" * 70)
    print("分层分类（基于verdict）")
    print("=" * 70)
    print(f"Tier1 适合持有 ({len(tier1)}只): {tier1}")
    print(f"Tier2 适合择时 ({len(tier2)}只): {tier2}")
    print(f"Tier3 都不行 ({len(tier3)}只): {tier3}")
    print()
    
    # OOS期间（近1年）对比
    print("=" * 70)
    print("📊 OOS期间（近1年）等权组合对比")
    print("=" * 70)
    
    a_ret = simulate_group_a(data)
    b_ret = simulate_group_b(data, tier1, tier2, tier3)
    c_ret = simulate_group_c(data)
    
    for rets, name in [(a_ret, "A组：原adaptive全套"), (b_ret, "B组：分层框架"), (c_ret, "C组：全池子买入持有")]:
        s = stats(rets, name)
        print(f"\n{s['name']}:")
        print(f"  标的数: {s['n']}")
        print(f"  平均收益: {s['avg_return']}%")
        print(f"  中位数收益: {s['median_return']}%")
        print(f"  收益标准差: {s['std_return']}%")
        print(f"  区间: [{s['min_return']}%, {s['max_return']}%]")
        print(f"  胜率: {s['win_rate']}%")
        print(f"  等权组合近似收益: {s['total_return_eq']}%")
    
    # 全样本对比
    print("\n" + "=" * 70)
    print("📊 全样本（2020-2026）等权组合对比")
    print("=" * 70)
    
    a_full = []  # A组全样本：每只用全样本最优择时
    for sym, info in data.items():
        best = -999
        for sname, sdata in info["strategies"].items():
            if "full" in sdata and "total_return_pct" in sdata["full"]:
                ret = sdata["full"]["total_return_pct"]
                if ret > best:
                    best = ret
        a_full.append(best if best > -999 else 0)
    
    b_full = []
    for sym in tier1:
        if sym in data:
            b_full.append(get_bh_full(data[sym]))
    for sym in tier2:
        if sym in data:
            best = -999
            for sname, sdata in data[sym]["strategies"].items():
                if "full" in sdata and "total_return_pct" in sdata["full"]:
                    ret = sdata["full"]["total_return_pct"]
                    if ret > best:
                        best = ret
            b_full.append(best if best > -999 else 0)
    for sym in tier3:
        b_full.append(0.0)
    
    c_full = simulate_group_c_full(data)
    
    for rets, name in [(a_full, "A组：原adaptive全套(全样本最优)"), 
                       (b_full, "B组：分层框架(全样本)"), 
                       (c_full, "C组：全池子买入持有(全样本)")]:
        s = stats(rets, name)
        print(f"\n{s['name']}:")
        print(f"  标的数: {s['n']}")
        print(f"  平均收益: {s['avg_return']}%")
        print(f"  中位数收益: {s['median_return']}%")
        print(f"  收益标准差: {s['std_return']}%")
        print(f"  区间: [{s['min_return']}%, {s['max_return']}%]")
        print(f"  胜率: {s['win_rate']}%")
        print(f"  等权组合近似收益: {s['total_return_eq']}%")
    
    # 详细逐只对比表
    print("\n" + "=" * 70)
    print("📋 逐只详细对比（OOS收益）")
    print("=" * 70)
    print(f"{'代码':<8} {'名称':<10} {'分层':<6} {'B&H':>8} {'最优择时':>10} {'超额':>8} {'结论'}")
    print("-" * 70)
    
    for sym, info in data.items():
        name = info["name"]
        bh = get_bh_oos(info)
        best_timing = get_best_strategy_oos(info)
        
        if sym in tier1:
            tier = "T1持有"
            used = bh
        elif sym in tier2:
            tier = "T2择时"
            used = best_timing
        else:
            tier = "T3空仓"
            used = 0.0
        
        excess = used - bh
        verdict = info.get("verdict", "")
        print(f"{sym:<8} {name:<10} {tier:<6} {bh:>8.1f}% {best_timing:>10.1f}% {excess:>+8.1f}% {verdict}")
    
    # 保存报告
    report = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "tier1": tier1, "tier2": tier2, "tier3": tier3,
        "oos": {
            "group_a": {"returns": a_ret, "stats": stats(a_ret, "A")},
            "group_b": {"returns": b_ret, "stats": stats(b_ret, "B")},
            "group_c": {"returns": c_ret, "stats": stats(c_ret, "C")},
        },
        "full_sample": {
            "group_a": {"returns": a_full, "stats": stats(a_full, "A")},
            "group_b": {"returns": b_full, "stats": stats(b_full, "B")},
            "group_c": {"returns": c_full, "stats": stats(c_full, "C")},
        }
    }
    
    out_file = os.path.join(WORKSPACE, "analysis", f"tiered_backtest_{datetime.now().strftime('%Y%m%d')}.json")
    with open(out_file, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 详细报告已保存: {out_file}")

if __name__ == "__main__":
    main()