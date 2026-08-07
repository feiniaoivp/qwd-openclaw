#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨周期多窗口策略验证 (2026-08-01)
==================================
目标: 解决单窗口回测过拟合 —— 为每只股票选"多窗口稳定"的最优策略，
      而非单一窗口内"恰好获胜"的策略。

方法:
  1. 用 baostock 拉取 2020-01-01 起的长历史 (覆盖 2020成长牛/2021抱团/2022-23熊/2024-26)
  2. 对每只股票跑 4 个窗口: W1全量 / W2近3年 / W3近1.5年 / W4近1年
  3. 每个策略在每个窗口计算: 收益排名 + 夏普 + 回撤 + 交易样本量
  4. 跨窗口稳定性评分 = 各窗口表现加权，样本量不足/单窗口侥幸的降权
  5. 选出"多窗口稳健最优"策略，输出 JSON 供 adaptive 系统使用

运行: python3 analysis/validate_strategies.py [--stocks 代码,代码] [--write-map]
       --write-map  将稳健最优策略写回 data/adaptive_strategy_map.json
"""

import os
import sys
import json
import datetime
import numpy as np
import pandas as pd
import baostock as bs

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

# ── 复用主回测脚本的策略与模拟函数 ──
from backtest_strategies import (
    INITIAL_CAPITAL, COMMISSION, SLIPPAGE,
    run_simulation, ALL_STRATEGIES,
)

# ── 多窗口定义 (起点, 年数) ──
WIN_START = "2020-01-01"          # 长历史起点 (6.5年)
WINDOWS = [
    ("W1_全量6.5年", 0, 0),       # 2020 至今
    ("W2_近3年", 3, 0),
    ("W3_近1.5年", 1.5, 0),
    ("W4_近1年", 1, 0),
]

# 对应 baostock 代码前缀
def baostock_code(symbol):
    return ("sh." if symbol.startswith("6") or symbol.startswith("9")
            else "sz.") + symbol


def fetch_long(symbol, name, start, end, max_retry=3):
    """拉取长历史日线(前复权)"""
    code = baostock_code(symbol)
    for attempt in range(max_retry):
        try:
            rs = bs.query_history_k_data_plus(
                code, "date,open,high,low,close,volume,amount",
                start_date=start, end_date=end,
                frequency="d", adjustflag="2")  # 2=前复权
            rows = []
            while (rs.error_code == "0") and rs.next():
                rows.append(rs.get_row_data())
            if len(rows) >= 100:
                df = pd.DataFrame(rows, columns=[
                    "date", "open", "high", "low", "close", "volume", "amount"])
                for c in ["open", "high", "low", "close", "volume", "amount"]:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                df["date"] = pd.to_datetime(df["date"])
                df = df.dropna(subset=["close"]).reset_index(drop=True)
                return df
        except Exception as e:
            if attempt == max_retry - 1:
                print(f"    ⚠️ {name} 拉取失败: {e}")
    return None


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


def main():
    write_map = "--write-map" in sys.argv
    stock_arg = None
    if "--stocks" in sys.argv:
        i = sys.argv.index("--stocks")
        stock_arg = sys.argv[i+1].split(",")

    today = datetime.datetime.now().strftime("%Y-%m-%d")

    login = bs.login()
    if login.error_code != "0":
        print("❌ baostock 登录失败:", login.error_msg); sys.exit(1)

    # 股票清单 (默认全量30只)
    from backtest_strategies import STOCKS
    stocks = [s for s in STOCKS if stock_arg is None or s[0] in stock_arg]

    end = datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"🔍 跨周期多窗口验证开始 | {WIN_START} ~ {end} | {len(stocks)} 只股票")
    print("=" * 70)

    results = {}
    for sidx, (symbol, name) in enumerate(stocks):
        print(f"\n[{sidx+1}/{len(stocks)}] {name}({symbol}) — 拉取长历史...")
        df = fetch_long(symbol, name, WIN_START, end)
        if df is None or len(df) < 150:
            print(f"  ❌ 数据不足，跳过")
            results[symbol] = {"error": "数据不足"}
            continue

        stock_out = {"name": name, "windows": {}, "best_strategy": None}
        for wname, years_back, _ in WINDOWS:
            wdf = slice_window(df, years_back, 0)
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

    bs.logout()

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
    print(f"\n✅ 稳健策略验证结果保存: {outfile}")

    # ── 可选: 写回 adaptive_strategy_map.json (带置信度护栏) ──
    # 护栏: 只有得分>=25 且 夏普>=0.25 的变更才写回，否则保留原策略(避免低置信度过拟合)
    if write_map:
        mapfile = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")
        with open(mapfile, "r", encoding="utf-8") as f:
            cur = json.load(f)
        changes = 0
        skipped = []
        for symbol, info in best_map.items():
            if "error" in info or "strategy" not in info:
                continue
            new_s = info["strategy"]
            old_s = cur.get(symbol)
            if old_s == new_s:
                continue
            # 置信度护栏: 得分和夏普都达标才覆盖; 低笔数策略需更高夏普(防低样本侥幸)
            score = info.get("score", -999)
            sharpe = info.get("avg_sharpe", -999)
            trades = info.get("avg_trades", 999)
            # 笔数<6 时, 要求夏普>=0.4 才允许覆盖(更严格); 笔数>=6 用 0.25
            sharpe_threshold = 0.4 if trades < 6 else 0.25
            if score >= 25 and sharpe >= sharpe_threshold:
                print(f"  📝 {info.get('name', symbol)}: {old_s} -> {new_s} "
                      f"(得分{score} 夏普{sharpe} 交易{trades}笔)")
                cur[symbol] = new_s
                changes += 1
            else:
                skipped.append((symbol, info.get("name", symbol), old_s, new_s, score, sharpe, trades, sharpe_threshold))
        with open(mapfile, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 已写回 {mapfile} (变更 {changes} 处)")
        if skipped:
            print("\n⏸️ 以下因低置信度保留原策略(不覆盖):")
            for sym, name, old_s, new_s, score, sharpe, trades, thr in skipped:
                reason = f"夏普{sharpe}<{thr}(低笔数{int(trades)}笔需更严)" if trades < 6 else f"得分{score}<25 或 夏普{sharpe}<{thr}"
                print(f"   {name}({sym}): {old_s} 保留 (稳健候选{new_s} {reason})")
    else:
        print("\n(未写入 adaptive_strategy_map.json，需加 --write-map 才会覆盖)")


if __name__ == "__main__":
    main()
