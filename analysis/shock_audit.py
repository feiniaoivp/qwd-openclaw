#!/usr/bin/env python3
import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)

from analysis.fib_extension_scan import WATCHLIST
from analysis.data_layer.router import get_router

def apply_shock_decay(score, daily_return, flow_shock=False):
    decay = 1.0
    if daily_return <= -0.08: decay = 0.3
    elif daily_return <= -0.05: decay = 0.5
    elif daily_return <= -0.03: decay = 0.8
    
    if flow_shock: decay *= 0.9
    return score * decay

def get_signal_level(score):
    if score >= 20: return "🟢 强烈买入"
    elif score >= 10: return "🟢 关注"
    elif score >= 0: return "🟡 中性"
    elif score >= -10: return "🟠 谨慎"
    else: return "🔴 强烈卖出"

def main():
    router = get_router()
    symbols = list(WATCHLIST.keys())
    names = list(WATCHLIST.values())
    
    all_results = []
    
    for symbol, name in WATCHLIST.items():
        df = router.get_daily(symbol, name, start_date="20250101")
        if df is None or len(df) < 100: continue
        
        # 模拟最近一年的交易日
        # 我们关注所有跌幅 <= -3% 的日子
        df['ret'] = df['close'].pct_change()
        
        # 为了简化，我们假设一个基础评分 (用 20-40 之间的随机数模拟 Buy 信号)
        # 实际上我们需要知道原有的 score，但这里我们通过模拟原信号为 "Buy" 来测试拦截率
        
        shocks = df[df['ret'] <= -0.03].copy()
        
        for idx, row in shocks.iterrows():
            date = str(row['date'])
            ret = row['ret']
            
            # 模拟原分数为 25 (🟢 关注)
            orig_score = 25 
            decayed_score = apply_shock_decay(orig_score, ret)
            
            # 计算 T+3, T+5 收益 (实盘验证拦截价值)
            try:
                loc = df.index.get_loc(idx)
                if loc + 5 < len(df):
                    ret_3 = (df['close'].iloc[loc+3] / row['close']) - 1
                    ret_5 = (df['close'].iloc[loc+5] / row['close']) - 1
                else:
                    continue
            except:
                continue
                
            all_results.append({
                'symbol': symbol,
                'date': date,
                'ret': ret,
                'orig_sig': get_signal_level(orig_score),
                'decay_sig': get_signal_level(decayed_score),
                'ret_3': ret_3,
                'ret_5': ret_5
            })

    res_df = pd.DataFrame(all_results)
    if res_df.empty:
        print("No shock days found.")
        return

    # 拦截定义：原信号 Buy -> 衰减后 Hold/Sell
    intercepted = res_df[res_df['decay_sig'] != "🟢 关注"]
    
    # 拦截成功：T+5 收益 < 0 (确实避坑了)
    success = intercepted[intercepted['ret_5'] < 0]
    # 拦截失败 (误杀)：T+5 收益 > 0.05 (错过了大反弹)
    false_alarm = intercepted[intercepted['ret_5'] > 0.05]
    
    print(f"--- Shock Filter Audit Report ---")
    print(f"Total Shock Days (<= -3%): {len(res_df)}")
    print(f"Intercepted Signals: {len(intercepted)}")
    print(f"Successful Avoidance (T+5 < 0): {len(success)} ({len(success)/len(intercepted)*100:.1f}%)")
    print(f"False Alarms (T+5 > 5%): {len(false_alarm)} ({len(false_alarm)/len(intercepted)*100:.1f}%)")
    print(f"Average T+5 Return of Intercepted: {res_df[res_df['decay_sig'] != '🟢 关注']['ret_5'].mean()*100:.2f}%")
    print(f"Average T+5 Return of Non-Shock Days (Control): {res_df[res_df['ret'] > -0.03]['ret_5'].mean()*100 if not res_df[res_df['ret'] > -0.03].empty else 0:.2f}%")

if __name__ == "__main__":
    main()
