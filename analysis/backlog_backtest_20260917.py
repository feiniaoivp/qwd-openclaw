#!/usr/bin/env python3
"""
回测批次 C (2026-09-17) - 5 核心策略 × 27 只
===========================================================
使用 backtest_strategies 中的策略函数 + run_simulation
"""

import sys, os, json, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, '/Users/duguke/.openclaw/workspace')

import pandas as pd
import numpy as np
from datetime import datetime

from analysis.portfolio_core import STOCKS
from analysis.backtest_strategies import (
    strategy_macd, strategy_ema_cross, strategy_bollinger_atr,
    strategy_kdj_cci, strategy_ema_obv, strategy_bull_trend_follow,
    fetch_data, run_simulation,
)

START_DATE = "20240101"
END_DATE   = "20260916"

CORE_STRATEGIES = {
    "macd": strategy_macd,
    "ema_cross": strategy_ema_cross,
    "bollinger_atr": strategy_bollinger_atr,
    "kdj_cci": strategy_kdj_cci,
    "ema_obv": strategy_ema_obv,
    "bull_trend": strategy_bull_trend_follow,
}

# ── 数据获取 ──────────────────────────────────────
from analysis.backtest_strategies import fetch_data, run_simulation

def main():
    print(f"📊 回测批次 C 启动 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"标的: 27 只 | 区间: 2024-01-01 ~ 2026-09-16")
    print(f"策略: {list(CORE_STRATEGIES.keys())}")
    
    # 预加载数据
    print("📥 预加载历史数据...")
    data_cache = {}
    for symbol, name in STOCKS:
        df = fetch_data(symbol, name, start=START_DATE, end=END_DATE)
        if df is not None and len(df) >= 60:
            data_cache[symbol] = df
    print(f"  成功加载: {len(data_cache)}/27")
    
    CORE_STRATEGIES = {
        "macd": strategy_macd,
        "ema_cross": strategy_ema_cross,
        "bollinger_atr": strategy_bollinger_atr,
        "kdj_cci": strategy_kdj_cci,
        "ema_obv": strategy_ema_obv,
        "bull_trend": strategy_bull_trend_follow,
    }
    
    results = {s: {} for s in CORE_STRATEGIES}
    summary_rows = []
    
    for strat_name, strat_func in CORE_STRATEGIES.items():
        print(f"\n🔬 策略: {strat_name}")
        all_ret, all_sharpe, all_dd, all_trades, all_wr = [], [], [], [], []
        success = 0
        
        for symbol, name in STOCKS:
            if symbol not in data_cache:
                continue
            df = data_cache[symbol]
            res = run_one(symbol, name, data_cache[symbol], strat_name, strat_func)
            if "error" not in res:
                success += 1
                all_ret.append(res["return_pct"])
                all_sharpe.append(res["sharpe"])
                all_dd.append(res["max_dd"])
                all_trades.append(res["trades"])
                all_wr.append(res["win_rate"])
            else:
                print(f"   {symbol} 失败: {res['error'][:60]}")
        
        if success == 0:
            print(f"  {strat_name}: 全失败")
            continue
        
        avg_ret = np.mean(all_ret)
        avg_sharpe = np.mean(all_sharpe)
        avg_dd = np.mean(all_dd)
        avg_trades = np.mean(all_trades)
        avg_wr = np.mean(all_wr)
        
        results[strat_name] = {
            "success": success,
            "avg_return_pct": round(avg_ret, 2),
            "avg_sharpe": round(avg_sharpe, 3),
            "avg_max_dd_pct": round(avg_dd, 2),
            "avg_trades": round(avg_trades, 1),
            "avg_win_rate_pct": round(avg_wr, 1),
        }
        
        summary_rows.append({
            "strategy": strat_name,
            "success": success,
            "avg_return": round(avg_ret, 2),
            "sharpe": round(avg_sharpe, 3),
            "max_dd": round(avg_dd, 2),
            "trades": round(avg_trades, 1),
            "win_rate": round(avg_wr, 1),
        })
        
        print(f"  成功 {success}/27 | 收益 {avg_ret:+.2f}% | 夏普 {avg_sharpe:.3f} | 回撤 {avg_dd:.2f}% | 交易 {avg_trades:.1f} | 胜率 {avg_wr:.1f}%")
    
    out_path = f"analysis/backlog_backtest_20260917_{datetime.now().strftime('%H%M')}.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({"strategies": results, "summary": summary_rows}, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 结果已保存: {out_path}")
    
    print("\n📊 策略汇总表 (27 只平均):")
    print(f"{'策略':<16} {'成功':>4} {'收益%':>8} {'夏普':>7} {'回撤%':>7} {'交易数':>7} {'胜率%':>7}")
    for row in summary_rows:
        print(f"{row['strategy']:<16} {row['success']:>4} {row['avg_return']:>+8.2f} {row['sharpe']:>7.3f} {row['max_dd']:>7.2f} {row['trades']:>7.1f} {row['win_rate']:>7.1f}")

if __name__ == "__main__":
    import sys, os, json
    sys.path.insert(0, '/Users/duguke/.openclaw/workspace')
    import pandas as pd
    import numpy as np
    from datetime import datetime
    from analysis.portfolio_core import STOCKS
    from analysis.backtest_strategies import (
        strategy_macd, strategy_ema_cross, strategy_bollinger_atr,
        strategy_kdj_cci, strategy_ema_obv, strategy_bull_trend_follow,
        fetch_data, run_simulation,
    )
    
    START_DATE = "20240101"
    END_DATE   = "20260916"
    
    CORE_STRATEGIES = {
        "macd": strategy_macd,
        "ema_cross": strategy_ema_cross,
        "bollinger_atr": strategy_bollinger_atr,
        "kdj_cci": strategy_kdj_cci,
        "ema_obv": strategy_ema_obv,
        "bull_trend": strategy_bull_trend_follow,
    }
    
    def run_one(symbol, name, df, strat_name, strat_func):
        if df is None or len(df) < 60:
            return {"error": "数据不足"}
        try:
            actions = strat_func(df)
            res = run_simulation(df, actions)
            # Extract from run_simulation's return format
            return {
                "return_pct": float(res.get("total_return_pct", 0)),
                "sharpe": float(res.get("sharpe_ratio", 0)),
                "max_dd": float(res.get("max_drawdown_pct", 0)),
                "trades": int(res.get("total_trades", 0)),
                "win_rate": float(res.get("win_rate_pct", 0)),
            }
        except Exception as e:
            return {"error": str(e)}
    
    # ── 主流程 ──────────────────────────────────────
    print(f"📊 回测批次 C 启动 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"标的: 27 只 | 区间: 2024-01-01 ~ 2026-09-16")
    print(f"策略: {list(CORE_STRATEGIES.keys())}")
    
    print("📥 预加载历史数据...")
    data_cache = {}
    for symbol, name in STOCKS:
        df = fetch_data(symbol, name, start=START_DATE, end=END_DATE)
        if df is not None and len(df) >= 60:
            data_cache[symbol] = df
    print(f"  成功加载: {len(data_cache)}/27")
    
    CORE_STRATEGIES = {
        "macd": strategy_macd,
        "ema_cross": strategy_ema_cross,
        "bollinger_atr": strategy_bollinger_atr,
        "kdj_cci": strategy_kdj_cci,
        "ema_obv": strategy_ema_obv,
        "bull_trend": strategy_bull_trend_follow,
    }
    
    def run_one(symbol, name, df, strat_name, strat_func):
        if df is None or len(df) < 60:
            return {"error": "数据不足"}
        try:
            actions = strat_func(df)
            res = run_simulation(df, actions)
            return {
                "return_pct": float(res.get("total_return_pct", 0)),
                "sharpe": float(res.get("sharpe_ratio", 0)),
                "max_dd": float(res.get("max_drawdown_pct", 0)),
                "trades": int(res.get("total_trades", 0)),
                "win_rate": float(res.get("win_rate_pct", 0)),
            }
        except Exception as e:
            return {"error": str(e)}
    
    print(f"📊 回测批次 C 启动 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"标的: 27 只 | 区间: 2024-01-01 ~ 2026-09-16")
    print(f"策略: {list(CORE_STRATEGIES.keys())}")
    
    print("📥 预加载历史数据...")
    data_cache = {}
    for symbol, name in STOCKS:
        df = fetch_data(symbol, name, start=START_DATE, end=END_DATE)
        if df is not None and len(df) >= 60:
            data_cache[symbol] = df
    print(f"  成功加载: {len(data_cache)}/27")
    
    results = {s: {} for s in CORE_STRATEGIES}
    summary_rows = []
    
    for strat_name, strat_func in CORE_STRATEGIES.items():
        print(f"\n🔬 策略: {strat_name}")
        all_ret, all_sharpe, all_dd, all_trades, all_wr = [], [], [], [], []
        success = 0
        
        for symbol, name in STOCKS:
            if symbol not in data_cache:
                continue
            df = data_cache[symbol]
            res = run_one(symbol, name, data_cache[symbol], strat_name, strat_func)
            if "error" not in res:
                success += 1
                all_ret.append(res["return_pct"])
                all_sharpe.append(res["sharpe"])
                all_dd.append(res["max_dd"])
                all_trades.append(res["trades"])
                all_wr.append(res["win_rate"])
            else:
                print(f"   {symbol} 失败: {res['error'][:60]}")
        
        if success == 0:
            print(f"  {strat_name}: 全失败")
            continue
        
        avg_ret = np.mean(all_ret)
        avg_sharpe = np.mean(all_sharpe)
        avg_dd = np.mean(all_dd)
        avg_trades = np.mean(all_trades)
        avg_wr = np.mean(all_wr)
        
        results[strat_name] = {
            "success": success,
            "avg_return_pct": round(avg_ret, 2),
            "avg_sharpe": round(avg_sharpe, 3),
            "avg_max_dd_pct": round(avg_dd, 2),
            "avg_trades": round(avg_trades, 1),
            "avg_win_rate_pct": round(avg_wr, 1),
        }
        
        summary_rows.append({
            "strategy": strat_name,
            "success": success,
            "avg_return": round(avg_ret, 2),
            "sharpe": round(avg_sharpe, 3),
            "max_dd": round(avg_dd, 2),
            "trades": round(avg_trades, 1),
            "win_rate": round(avg_wr, 1),
        })
        
        print(f"  成功 {success}/27 | 收益 {avg_ret:+.2f}% | 夏普 {avg_sharpe:.3f} | 回撤 {avg_dd:.2f}% | 交易 {avg_trades:.1f} | 胜率 {avg_wr:.1f}%")
    
    out_path = f"analysis/backlog_backtest_20260917_{datetime.now().strftime('%H%M')}.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({"strategies": results, "summary": summary_rows}, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 结果已保存: {out_path}")
    
    print("\n📊 策略汇总表 (27 只平均):")
    print(f"{'策略':<16} {'成功':>4} {'收益%':>8} {'夏普':>7} {'回撤%':>7} {'交易数':>7} {'胜率%':>7}")
    for row in summary_rows:
        print(f"{row['strategy']:<16} {row['success']:>4} {row['avg_return']:>+8.2f} {row['sharpe']:>7.3f} {row['max_dd']:>7.2f} {row['trades']:>7.1f} {row['win_rate']:>7.1f}")

if __name__ == "__main__":
    import sys, os, json
    sys.path.insert(0, '/Users/duguke/.openclaw/workspace')
    import pandas as pd
    import numpy as np
    from datetime import datetime
    from analysis.portfolio_core import STOCKS
    from analysis.backtest_strategies import (
        strategy_macd, strategy_ema_cross, strategy_bollinger_atr,
        strategy_kdj_cci, strategy_ema_obv, strategy_bull_trend_follow,
        fetch_data, run_simulation,
    )
    
    START_DATE = "20240101"
    END_DATE   = "20260916"
    
    CORE_STRATEGIES = {
        "macd": strategy_macd,
        "ema_cross": strategy_ema_cross,
        "bollinger_atr": strategy_bollinger_atr,
        "kdj_cci": strategy_kdj_cci,
        "ema_obv": strategy_ema_obv,
        "bull_trend": strategy_bull_trend_follow,
    }
    
    def run_one(symbol, name, df, strat_name, strat_func):
        if df is None or len(df) < 60:
            return {"error": "数据不足"}
        try:
            actions = strat_func(df)
            res = run_simulation(df, actions)
            return {
                "return_pct": float(res.get("total_return_pct", 0)),
                "sharpe": float(res.get("sharpe_ratio", 0)),
                "max_dd": float(res.get("max_drawdown_pct", 0)),
                "trades": int(res.get("total_trades", 0)),
                "win_rate": float(res.get("win_rate_pct", 0)),
            }
        except Exception as e:
            return {"error": str(e)}
    
    print(f"📊 回测批次 C 启动 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"标的: 27 只 | 区间: 2024-01-01 ~ 2026-09-16")
    print(f"策略: {list(CORE_STRATEGIES.keys())}")
    
    print("📥 预加载历史数据...")
    data_cache = {}
    for symbol, name in STOCKS:
        df = fetch_data(symbol, name, start=START_DATE, end=END_DATE)
        if df is not None and len(df) >= 60:
            data_cache[symbol] = df
    print(f"  成功加载: {len(data_cache)}/27")
    
    results = {s: {} for s in CORE_STRATEGIES}
    summary_rows = []
    
    for strat_name, strat_func in CORE_STRATEGIES.items():
        print(f"\n🔬 策略: {strat_name}")
        all_ret, all_sharpe, all_dd, all_trades, all_wr = [], [], [], [], []
        success = 0
        
        for symbol, name in STOCKS:
            if symbol not in data_cache:
                continue
            df = data_cache[symbol]
            res = run_one(symbol, name, data_cache[symbol], strat_name, strat_func)
            if "error" not in res:
                success += 1
                all_ret.append(res["return_pct"])
                all_sharpe.append(res["sharpe"])
                all_dd.append(res["max_dd"])
                all_trades.append(res["trades"])
                all_wr.append(res["win_rate"])
            else:
                print(f"   {symbol} 失败: {res['error'][:60]}")
        
        if success == 0:
            print(f"  {strat_name}: 全失败")
            continue
        
        avg_ret = np.mean(all_ret)
        avg_sharpe = np.mean(all_sharpe)
        avg_dd = np.mean(all_dd)
        avg_trades = np.mean(all_trades)
        avg_wr = np.mean(all_wr)
        
        results[strat_name] = {
            "success": success,
            "avg_return_pct": round(avg_ret, 2),
            "avg_sharpe": round(avg_sharpe, 3),
            "avg_max_dd_pct": round(avg_dd, 2),
            "avg_trades": round(avg_trades, 1),
            "avg_win_rate_pct": round(avg_wr, 1),
        }
        
        summary_rows.append({
            "strategy": strat_name,
            "success": success,
            "avg_return": round(avg_ret, 2),
            "sharpe": round(avg_sharpe, 3),
            "max_dd": round(avg_dd, 2),
            "trades": round(avg_trades, 1),
            "win_rate": round(avg_wr, 1),
        })
        
        print(f"  成功 {success}/27 | 收益 {avg_ret:+.2f}% | 夏普 {avg_sharpe:.3f} | 回撤 {avg_dd:.2f}% | 交易 {avg_trades:.1f} | 胜率 {avg_wr:.1f}%")
    
    out_path = f"analysis/backlog_backtest_20260917_{datetime.now().strftime('%H%M')}.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({"strategies": results, "summary": summary_rows}, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 结果已保存: {out_path}")
    
    print("\n📊 策略汇总表 (27 只平均):")
    print(f"{'策略':<16} {'成功':>4} {'收益%':>8} {'夏普':>7} {'回撤%':>7} {'交易数':>7} {'胜率%':>7}")
    for row in summary_rows:
        print(f"{row['strategy']:<16} {row['success']:>4} {row['avg_return']:>+8.2f} {row['sharpe']:>7.3f} {row['max_dd']:>7.2f} {row['trades']:>7.1f} {row['win_rate']:>7.1f}")

if __name__ == "__main__":
    import sys, os, json
    sys.path.insert(0, '/Users/duguke/.openclaw/workspace')
    import pandas as pd
    import numpy as np
    from datetime import datetime
    from analysis.portfolio_core import STOCKS
    from analysis.backtest_strategies import (
        strategy_macd, strategy_ema_cross, strategy_bollinger_atr,
        strategy_kdj_cci, strategy_ema_obv, strategy_bull_trend_follow,
        fetch_data, run_simulation,
    )
    
    START_DATE = "20240101"
    END_DATE   = "20260916"
    
    CORE_STRATEGIES = {
        "macd": strategy_macd,
        "ema_cross": strategy_ema_cross,
        "bollinger_atr": strategy_bollinger_atr,
        "kdj_cci": strategy_kdj_cci,
        "ema_obv": strategy_ema_obv,
        "bull_trend": strategy_bull_trend_follow,
    }
    
    def run_one(symbol, name, df, strat_name, strat_func):
        if df is None or len(df) < 60:
            return {"error": "数据不足"}
        try:
            actions = strat_func(df)
            res = run_simulation(df, actions)
            return {
                "return_pct": float(res.get("total_return_pct", 0)),
                "sharpe": float(res.get("sharpe_ratio", 0)),
                "max_dd": float(res.get("max_drawdown_pct", 0)),
                "trades": int(res.get("total_trades", 0)),
                "win_rate": float(res.get("win_rate_pct", 0)),
            }
        except Exception as e:
            return {"error": str(e)}
    
    print(f"📊 回测批次 C 启动 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"标的: 27 只 | 区间: 2024-01-01 ~ 2026-09-16")
    print(f"策略: {list(CORE_STRATEGIES.keys())}")
    
    print("📥 预加载历史数据...")
    data_cache = {}
    for symbol, name in STOCKS:
        df = fetch_data(symbol, name, start=START_DATE, end=END_DATE)
        if df is not None and len(df) >= 60:
            data_cache[symbol] = df
    print(f"  成功加载: {len(data_cache)}/27")
    
    results = {s: {} for s in CORE_STRATEGIES}
    summary_rows = []
    
    for strat_name, strat_func in CORE_STRATEGIES.items():
        print(f"\n🔬 策略: {strat_name}")
        all_ret, all_sharpe, all_dd, all_trades, all_wr = [], [], [], [], []
        success = 0
        
        for symbol, name in STOCKS:
            if symbol not in data_cache:
                continue
            df = data_cache[symbol]
            res = run_one(symbol, name, data_cache[symbol], strat_name, strat_func)
            if "error" not in res:
                success += 1
                all_ret.append(res["return_pct"])
                all_sharpe.append(res["sharpe"])
                all_dd.append(res["max_dd"])
                all_trades.append(res["trades"])
                all_wr.append(res["win_rate"])
            else:
                print(f"   {symbol} 失败: {res['error'][:60]}")
        
        if success == 0:
            print(f"  {strat_name}: 全失败")
            continue
        
        avg_ret = np.mean(all_ret)
        avg_sharpe = np.mean(all_sharpe)
        avg_dd = np.mean(all_dd)
        avg_trades = np.mean(all_trades)
        avg_wr = np.mean(all_wr)
        
        results[strat_name] = {
            "success": success,
            "avg_return_pct": round(avg_ret, 2),
            "avg_sharpe": round(avg_sharpe, 3),
            "avg_max_dd_pct": round(avg_dd, 2),
            "avg_trades": round(avg_trades, 1),
            "avg_win_rate_pct": round(avg_wr, 1),
        }
        
        summary_rows.append({
            "strategy": strat_name,
            "success": success,
            "avg_return": round(avg_ret, 2),
            "sharpe": round(avg_sharpe, 3),
            "max_dd": round(avg_dd, 2),
            "trades": round(avg_trades, 1),
            "win_rate": round(avg_wr, 1),
        })
        
        print(f"  成功 {success}/27 | 收益 {avg_ret:+.2f}% | 夏普 {avg_sharpe:.3f} | 回撤 {avg_dd:.2f}% | 交易 {avg_trades:.1f} | 胜率 {avg_wr:.1f}%")
    
    out_path = f"analysis/backlog_backtest_20260917_{datetime.now().strftime('%H%M')}.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({"strategies": results, "summary": summary_rows}, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 结果已保存: {out_path}")
    
    print("\n📊 策略汇总表 (27 只平均):")
    print(f"{'策略':<16} {'成功':>4} {'收益%':>8} {'夏普':>7} {'回撤%':>7} {'交易数':>7} {'胜率%':>7}")
    for row in summary_rows:
        print(f"{row['strategy']:<16} {row['success']:>4} {row['avg_return']:>+8.2f} {row['sharpe']:>7.3f} {row['max_dd']:>7.2f} {row['trades']:>7.1f} {row['win_rate']:>7.1f}")

    main()
