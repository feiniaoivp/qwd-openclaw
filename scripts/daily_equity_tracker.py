#!/usr/bin/env python3
import os
import sys
import json
import pandas as pd
from datetime import datetime

# 工作区样板
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.portfolio_core import REFERENCE_TOTAL_CAPITAL

# 路径定义
STATE_FILE = os.path.join(WORKSPACE, "data", "sim_state.json")
EQUITY_LOG_FILE = os.path.join(WORKSPACE, "data", "daily_equity_mirror.csv")

def get_current_equity(state):
    """
    计算当前实时总权益 = 现金 + 所有持仓市值
    """
    cash = state.get("cash", REFERENCE_TOTAL_CAPITAL)
    positions = state.get("positions", {})
    
    total_mv = 0.0
    for sym, pos in positions.items():
        if pos.get("position"):
            # 尝试从 state 缓存的 _last_close 获取价格，如果没有则记录为 0
            price = pos.get("_last_close", 0.0)
            total_mv += pos["shares"] * price
            
    return cash + total_mv

def mirror_equity():
    """将当前状态镜像到每日权益追踪表中"""
    if not os.path.exists(STATE_FILE):
        print(f"❌ 状态文件缺失: {STATE_FILE}")
        sys.exit(1)
        
    with open(STATE_FILE, "r") as f:
        state = json.load(f)
        
    today_str = datetime.now().strftime("%Y-%m-%d")
    equity = get_current_equity(state)
    
    # 构造记录
    record = {
        "date": today_str,
        "total_equity": round(equity, 2),
        "cash": round(state.get("cash", REFERENCE_TOTAL_CAPITAL), 2),
        "market_value": round(equity - state.get("cash", REFERENCE_TOTAL_CAPITAL), 2),
        "return_pct": round((equity / REFERENCE_TOTAL_CAPITAL - 1) * 100, 2)  # 分母=组合参考总本金270万（非单票10万，2026-11-04晚修正）
    }
    
    df_new = pd.DataFrame([record])
    
    if os.path.exists(EQUITY_LOG_FILE):
        df_hist = pd.read_csv(EQUITY_LOG_FILE)
        # 如果今天已经记录过，则覆盖更新
        if today_str in df_hist['date'].values:
            df_hist.loc[df_hist['date'] == today_str, :] = record.values()
            df_hist.to_csv(EQUITY_LOG_FILE, index=False)
        else:
            df_hist = pd.concat([df_hist, df_new], ignore_index=True)
            df_hist.to_csv(EQUITY_LOG_FILE, index=False)
    else:
        df_new.to_csv(EQUITY_LOG_FILE, index=False)
        
    print(f"✅ 权益镜像完成 [{today_str}]: Total Equity = {record['total_equity']} ({record['return_pct']}%)")

if __name__ == "__main__":
    mirror_equity()
