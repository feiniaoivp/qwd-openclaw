#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨周期多窗口策略验证 (2026-08-01)
==================================
目标: 解决单窗口回测过拟合 —— 为每只股票选"多窗口稳定"的最优策略，
      而非单一窗口内"恰好获胜"的策略。

方法:
  1. 用 DataRouter 统一数据源拉取 2020-01-01 起的长历史 (覆盖 2020成长牛/2021抱团/2022-23熊/2024-26)
  2. 对每只股票跑 4 个窗口: W1全量 / W2近3年 / W3近1.5年 / W4近1年
  3. 每个策略在每个窗口计算: 收益排名 + 夏普 + 回撤 + 交易样本量
  4. 跨窗口稳定性评分 = 各窗口表现加权，样本量不足/单窗口侥幸的降权
  5. 选出"多窗口稳健最优"策略，输出 JSON 供 adaptive 系统使用

运行: python3 analysis/validate_strategies.py [--stocks 代码,代码] [--write-map]
       --write-map  将稳健最优策略写回 data/adaptive_strategy_map.json

已统一使用 analysis.data_layer.router.DataRouter (Phase 1 完成)
"""

import os
import sys
import json
import datetime
import numpy as np
import pandas as pd

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE)

# 导入统一数据路由器
from analysis.data_layer.router import get_router

# ── 复用主回测脚本的策略与模拟函数 ──
from analysis.backtest_strategies import (
    INITIAL_CAPITAL, COMMISSION, SLIPPAGE,
    run_simulation, ALL_STRATEGIES,
)

# ── 回测护栏：随机基准对照 ──
from analysis.backtest_guard import compare_to_random, assert_beats_random

# ════════════════════════════════════════
# MACD参数联动EMA12/26最优参数
# ════════════════════════════════════════
def derive_macd_params_from_ema(ema_fast: int, ema_slow: int) -> dict:
    """
    MACD快/慢线直接复用EMA12/26的最优参数，信号线取快线周期的75%向上取整。
    逻辑：MACD本质是EMA快线-慢线，参数耦合可降低过拟合风险。
    """
    return {
        "fast": ema_fast,
        "slow": ema_slow,
        "signal": max(7, round(ema_fast * 0.75))
    }

# EMA12/26参数网格（验证/调优时共用）
EMA_CROSS_GRID = [
    (8, 21), (10, 26), (12, 26), (15, 30),
    (10, 21), (12, 21), (8, 26), (15, 26),
]

def get_macd_grid_from_ema(ema_pairs=None):
    """基于EMA最优参数生成MACD参数网格（MACD参数直接衍生自EMA）"""
    if ema_pairs is None:
        ema_pairs = EMA_CROSS_GRID
    grid = []
    for fast, slow in ema_pairs:
        if fast < slow:
            macd = derive_macd_params_from_ema(fast, slow)
            if macd not in grid:
                grid.append(macd)
    return grid

# ── 多窗口定义 (起点, 年数) ──
WIN_START = "2020-01-01"          # 长历史起点 (6.5年)
WINDOWS = [
    ("W1_全量6.5年", 0, 0),       # 2020 至今
    ("W2_近3年", 3, 0),
    ("W3_近1.5年", 1.5, 0),
    ("W4_近1年", 1, 0),
]

def slice_window(df, start_years_back, _):
    """按年份截取子窗口 (支持小数年，用天数近似)"""
    if start_years_back == 0:
        return df.copy()
    end_date = df["date"].max()
    start_date = end_date - pd.Timedelta(days=int(start_years_back * 365))
    return df[df["date"] >= start_date].copy()

def pick_safe_strategy_names(df, sfunc):
    """对窗口内数据跑策略并安全地找到交易信号（复用策略函数）"""
    return sfunc(df)

def fetch_long_history(symbol: str, name: str, start: str, end: str) -> pd.DataFrame:
    """统一历史日K获取 - 走 DataRouter (内含降级链: 新浪日K -> pytdx -> baostock -> akshare)"""
    router = get_router()
    try:
        df = router.get_daily(symbol, name, start_date=start.replace("-", ""))
        if df is not None and len(df) >= 150:
            # 截取日期范围（用 pd.to_datetime 保持与 router 一致的 datetime 口径，
            # 避免依赖 pandas 对 str/str 比较的隐式行为）
            df = df[(df["date"] >= pd.to_datetime(start)) &
                    (df["date"] <= pd.to_datetime(end))].copy()
            if len(df) >= 150:
                return df
    except Exception as e:
        print(f"  {symbol} DataRouter获取失败: {e}")
    return None


def main(stocks=None, write_map=False, arbitrate=False):
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    if stocks is None:
        # 从 WATCHLIST 获取 (35只)
        from analysis.fib_extension_scan import WATCHLIST
        stocks = list(WATCHLIST.keys())
    
    print(f"🔍 跨周期稳健验证开始 | {WIN_START} ~ {today} | {len(stocks)} 只股票 x 4窗口 x 6策略")
    print("=" * 70)

    # ── 拉取长历史数据 (每只跑4窗口 x 6策略) ──
    results = {}
    for symbol in stocks:
        name = "未知"
        try:
            from analysis.fib_extension_scan import WATCHLIST
            name = WATCHLIST.get(symbol, "未知")
        except Exception:
            pass
        
        print(f"\n[{symbol}] {name} — 拉取长历史...")
        df = fetch_long_history(symbol, name, WIN_START, today)
        if df is None or len(df) < 150:
            print(f"  ❌ 数据不足 ({len(df) if df is not None else 0} 条)，跳过")
            results[symbol] = {"symbol": symbol, "name": name, "error": "数据不足"}
            continue
        
        print(f"  ✅ {len(df)} 条日线 ({str(df['date'].min().date())} ~ {str(df['date'].max().date())})")
        
        stock_out = {"symbol": symbol, "name": name, "windows": {}}
        
        for wname, years_back, _ in WINDOWS:
            wdf = slice_window(df, years_back, 0)
            if len(wdf) < 60:
                print(f"    {wname}: 数据不足 ({len(wdf)} 条)，跳过")
                stock_out["windows"][wname] = {"error": "数据不足"}
                continue
            
            print(f"    {wname}: {len(wdf)} 条 ({str(wdf['date'].min().date())} ~ {str(wdf['date'].max().date())})")
            wmetrics = {}
            for sname, sfunc in ALL_STRATEGIES:
                try:
                    actions = sfunc(wdf)
                    m = run_simulation(wdf, actions)
                    m["signals"] = len([a for a in actions if a["type"] == "BUY"])
                    m["trades"] = m.get("total_trades", 0)
                    wmetrics[sname] = m
                except Exception as e:
                    wmetrics[sname] = {"error": str(e)}
            stock_out["windows"][wname] = wmetrics
        results[symbol] = stock_out

    # ── 跨窗口稳定性评分 ──
    print("\n" + "=" * 70)
    print("跨窗口稳定性评分 (收益排名 + 夏普 + 回撤 + 样本量)")

    best_map = {}
    for symbol, out in results.items():
        if "error" in out:
            print(f"❌ {symbol}: {out['error']}")
            continue
        name = out["name"]
        # 统计每个策略在4个窗口的表现
        strat_points = {}   # 策略 -> {收益, 夏普, 回撤, 次数, 出现次数}
        for sname, _ in ALL_STRATEGIES:
            pts = {"returns": [], "sharpes": [], "dd": [], "trades": [], "cnt": 0}
            for wname in [w for w, _, _ in WINDOWS]:
                w = out["windows"].get(wname, {})
                m = w.get(sname, {})
                if "error" in m:
                    continue
                pts["returns"].append(m.get("total_return_pct", 0))
                pts["sharpes"].append(m.get("sharpe_ratio", 0))
                pts["dd"].append(m.get("max_drawdown_pct", 0))
                pts["trades"].append(m.get("trades", 0))
                pts["cnt"] += 1

            strat_points[sname] = pts

        # 计算每个策略的稳定性得分
        # 规则:
        #  - 只统计有 >=2 个窗口可用的策略(避免单窗口侥幸)
        #  - 得分 = (夏普*60 + 收益*0.6 - 回撤惩罚) * 样本量权重
        #  - 样本量按分档权重: 交易笔数越多越可信; 低笔数好策略不误杀但降权
        scored = []
        for sname, pts in strat_points.items():
            if pts["cnt"] < 2:
                continue
            avg_ret = np.mean(pts["returns"])
            avg_sharpe = np.mean(pts["sharpes"])
            avg_dd = np.mean(pts["dd"])          # 负数
            avg_trades = np.mean(pts["trades"])
            # 样本量分档权重 (尊重新鲜交易的参考价值, 但趋势策略笔数少不直接否决)
            if avg_trades >= 10:
                sample_weight = 1.0
            elif avg_trades >= 6:
                sample_weight = 0.75
            elif avg_trades >= 3:
                sample_weight = 0.4
            else:
                sample_weight = 0.15
            # 回撤惩罚: 超过 -30% 扣分
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
            best_map[symbol] = {"strategy": "ema_cross", "name": name, "reason": "数据不足"}
            continue

        # 排序选最优稳定策略
        scored.sort(key=lambda x: x["score"], reverse=True)
        best = scored[0]
        # 策略名转映射key
        key_map = {
            "布林带+ATR": "bollinger", "KDJ+CCI": "kdj_cci", "EMA+OBV": "ema_obv",
            "EMA12/26金叉(基准C)": "ema_cross", "纯MACD(基准B)": "macd",
            "牛市趋势跟踪": "bull_trend",
        }
        best_map[symbol] = {
            "strategy": key_map.get(best["strategy"], "ema_cross"),
            "name": name, "score": best["score"],
            "avg_return": best["avg_return"], "avg_sharpe": best["avg_sharpe"],
            "avg_dd": best["avg_dd"], "avg_trades": best["avg_trades"],
            "windows_used": best["windows"],
            "details": scored[:3],
        }
        print(f"  {name}({symbol}): 最优 {best['strategy']} "
              f"得分{best['score']} (多年化夏普{best['avg_sharpe']} 收益{best['avg_return']}% 回撤{best['avg_dd']}%)")

    # ── 保存结果 ──
    os.makedirs(os.path.join(WORKSPACE, "analysis"), exist_ok=True)
    outfile = os.path.join(WORKSPACE, "analysis", f"validation_{today}.json")
    with open(outfile, "w", encoding="utf-8") as f:
        json.dump(best_map, f, ensure_ascii=False, indent=2)

    # ── 全局数据完整度门槛：至少需有效股票数达标才允许写回映射 ──
    valid_stocks = sum(1 for s in results.values() if "error" not in s)
    MIN_VALID_STOCKS = 20
    if write_map and valid_stocks < MIN_VALID_STOCKS:
        print(f"\n⚠️ 全局数据完整度不足: 仅 {valid_stocks}/{len(stocks)} 只股票有完整跨周期数据，低于阈值 {MIN_VALID_STOCKS}")
        print(f"   拒绝写回 adaptive_strategy_map.json，保留原映射")
        write_map = False

    print(f"\n✅ 稳健策略验证结果保存: {outfile}")

    # ── 读取 param_tune 的候选策略（用于仲裁）──
    tune_map = {}
    tune_details = {}
    params_file = os.path.join(WORKSPACE, "data", "adaptive_params.json")
    if os.path.exists(params_file):
        try:
            with open(params_file, "r", encoding="utf-8") as f:
                tune_data = json.load(f)
            tune_stocks = tune_data.get("stocks", {})
            for symbol, info in tune_stocks.items():
                if "error" not in info and "strategy" in info:
                    tune_map[symbol] = info["strategy"]
                    tune_details[symbol] = info
        except Exception as e:
            print(f"  ⚠️ 读取 param_tune 结果失败: {e}")

    # ── 置信度仲裁：验证结果(跨窗口稳健) vs 调优结果(近期最优参数) ──
    def confidence_guard(score, sharpe, trades, hit_rate=None, total_windows=None):
        """统一的置信度护栏"""
        if score < 25:
            return False, f"得分{score}<25"
        thr = 0.4 if trades < 6 else 0.25
        if sharpe < thr:
            return False, f"夏普{sharpe}<{thr}(低笔数{int(trades)}笔需更严)"
        # 随机基准护栏：命中率必须显著超越随机基准
        if hit_rate is not None and total_windows is not None:
            try:
                assert_beats_random("命中率", int(hit_rate * total_windows), total_windows, random_hit_prob=0.63)
            except AssertionError as e:
                return False, f"随机基准未过: {e}"
        return True, ""

    if write_map or arbitrate:
        mapfile = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")
        with open(mapfile, "r", encoding="utf-8") as f:
            _raw = json.load(f)
        # 保留原格式（含 bucket 等字段），仅归一化取 strategy 用于比对
        cur_raw = _raw
        cur = {k: (v.get("strategy") if isinstance(v, dict) else v)
               for k, v in cur_raw.items()}
        
        changes = 0
        skipped = []
        arbitrated = []
        
        for symbol, info in best_map.items():
            if "error" in info or "strategy" not in info:
                continue
            val_s = info["strategy"]           # 验证推荐
            tune_s = tune_map.get(symbol)       # 调优推荐
            old_s = cur.get(symbol)             # 当前生效
            
            val_score = info.get("score", -999)
            val_sharpe = info.get("avg_sharpe", -999)
            val_trades = info.get("avg_trades", 999)
            
            # 验证候选是否通过护栏
            val_ok, val_reason = confidence_guard(val_score, val_sharpe, val_trades)
            
            # 调优候选是否通过护栏
            tune_ok = False
            tune_score = tune_sharpe = tune_trades = -999
            if tune_s and tune_s in tune_details.get(symbol, {}).get("details", {}):
                tinfo = tune_details[symbol]["details"][tune_s]
                tune_score = tinfo.get("best_score", -999)
                tune_sharpe = tinfo.get("avg_sharpe", -999)
                tune_trades = tinfo.get("avg_trades", 999)
                tune_ok, _ = confidence_guard(tune_score, tune_sharpe, tune_trades)
            
            # ── 仲裁逻辑 ──
            final_s = old_s
            reason = ""
            
            # 情况 1: 调优无候选 (tune_s 为 None 或空)
            if not tune_s:
                if val_ok:
                    final_s = val_s
                    reason = f"仅验证有候选且过护栏: {val_s}"
                else:
                    reason = f"验证候选未过护栏({val_reason})，保留原策略"
            # 情况 2: 双方一致
            elif val_s == tune_s:
                if val_ok or tune_ok:
                    final_s = val_s
                    reason = f"双方一致({val_s}) 验证={'✅' if val_ok else '❌'} 调优={'✅' if tune_ok else '❌'}"
                else:
                    reason = f"双方一致({val_s})但均未过护栏，保留原策略"
            # 情况 3: 双方不一致
            else:
                # 调优显著更优: 三维度同时满足 (分数+10, 夏普+0.1, 样本量>=6)
                if tune_ok and tune_score > val_score + 10 and tune_sharpe > val_sharpe + 0.1 and tune_trades >= 6:
                    final_s = tune_s
                    reason = f"调优显著更优: {tune_s}(分{tune_score} 夏普{tune_sharpe}) > {val_s}(分{val_score} 夏普{val_sharpe})"
                # 默认采信验证(跨窗口稳健)
                elif val_ok:
                    final_s = val_s
                    reason = f"采信验证(跨窗口稳健): {val_s}(分{val_score} 夏普{val_sharpe}) vs 调优:{tune_s}(分{tune_score} 夏普{tune_sharpe})"
                # 无明确优势方，保留原策略
                else:
                    reason = f"验证未过护栏({val_reason})，调优{'过' if tune_ok else '未过'}护栏，保留原策略"
            
            if final_s != old_s:
                print(f"  📝 {info.get('name', symbol)}: {old_s} -> {final_s} ({reason})")
                if isinstance(cur_raw.get(symbol), dict):
                    cur_raw[symbol]["strategy"] = final_s
                    cur[symbol] = final_s
                else:
                    cur[symbol] = final_s
                changes += 1
                if val_s != tune_s:
                    arbitrated.append((symbol, info.get('name', symbol), val_s, tune_s, final_s, reason))
            else:
                skipped.append((symbol, info.get('name', symbol), old_s, val_s, tune_s, val_score, val_sharpe, val_trades, val_reason))
        
        # 使用 Strategy Registry 版本化写入
        try:
            from analysis.strategy_registry import create_version
            metadata = {
                "validation_date": datetime.datetime.now().strftime("%Y-%m-%d"),
                "validation_method": "cross_window_4_windows",
                "changes": changes,
                "arbitrated_count": len(arbitrated),
                "skipped_count": len(skipped),
            }
            create_version(cur, source="validate_strategies_arbitrate", metadata=metadata)
            print(f"\n✅ 已写回 {mapfile} (版本化, 变更 {changes} 处)")
        except Exception as e:
            # 回退：直接写文件
            with open(mapfile, "w", encoding="utf-8") as f:
                json.dump(cur_raw if cur_raw else cur, f, ensure_ascii=False, indent=2)
            print(f"\n✅ 已写回 {mapfile} (回退, 变更 {changes} 处)")
        
        if arbitrated:
            print(f"\n⚖️ 仲裁决策 ({len(arbitrated)} 处):")
            for sym, name, val_s, tune_s, final_s, reason in arbitrated:
                print(f"   {name}({sym}): 验证={val_s} 调优={tune_s} -> 最终={final_s} ({reason})")
        
        if skipped:
            print("\n⏸️ 以下保留原策略:")
            for sym, name, old_s, val_s, tune_s, v_sc, v_sh, v_tr, v_reason in skipped:
                print(f"   {name}({sym}): 当前={old_s} 验证={val_s}(分{v_sc} 夏普{v_sh}) 调优={tune_s} -> 保留 ({v_reason})")
    else:
        print("\n(未写入 adaptive_strategy_map.json，需加 --write-map 或 --arbitrate 才会覆盖)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--stocks", help="股票代码逗号分隔")
    parser.add_argument("--write-map", action="store_true")
    parser.add_argument("--arbitrate", action="store_true")
    args = parser.parse_args()
    
    stocks = args.stocks.split(",") if args.stocks else None
    main(stocks=stocks, write_map=args.write_map, arbitrate=args.arbitrate)