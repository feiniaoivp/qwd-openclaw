#!/usr/bin/env python3
"""数据湖文件新鲜度门禁 (Data Lake Freshness Guard)

⚠️ 与 scripts/data_freshness_guard.py 同族不同职（2026-11-04 改名防混淆）：
- 本脚本：检查关注池 CSV 数据湖 + 模拟盘 state 文件的 mtime 新鲜度（T1-3）
- data_freshness_guard.py：检查日报文本内的数据日期标注（周流水线 0e）
"""
import os
import sys
import json
import pandas as pd
from datetime import datetime, timedelta

# 必须包含工作区样板
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.fib_extension_scan import WATCHLIST

# 配置项
DATA_DIR = os.path.join(WORKSPACE, "data", "stock_data")
STATE_FILE = os.path.join(WORKSPACE, "data", "sim_state.json")
ALERT_THRESHOLD_HOURS = 24  # 超过24小时未更新则告警

def check_csv_freshness():
    """检查关注池所有股票的 CSV 文件内容最新日期（读取 date 列）"""
    stale_stocks = []
    now = datetime.now()
    
    print(f"🔍 检查数据湖新鲜度... (关注池: {len(WATCHLIST)} 只)")
    
    for sym, name in WATCHLIST.items():
        file_path = os.path.join(DATA_DIR, f"{sym}.csv")
        if not os.path.exists(file_path):
            stale_stocks.append(f"{name}({sym}) [缺失文件]")
            continue
        
        # 读取 CSV 最后一行的 date 列作为内容最新日期
        try:
            df = pd.read_csv(file_path)
            if df.empty or "date" not in df.columns:
                stale_stocks.append(f"{name}({sym}) [无数据或缺 date 列]")
                continue
            # 取最后一行 date（假设按日期升序）
            last_date_str = str(df["date"].iloc[-1])
            # 兼容 YYYY-MM-DD 或 YYYYMMDD
            if len(last_date_str) == 8 and last_date_str.isdigit():
                last_date = datetime.strptime(last_date_str, "%Y%m%d")
            else:
                last_date = datetime.strptime(last_date_str[:10], "%Y-%m-%d")
        except Exception as e:
            stale_stocks.append(f"{name}({sym}) [读取失败: {type(e).__name__}]")
            continue
        
        # 计算与当前的天数差（仅交易日差估算用自然日，精确版留给交易日历）
        diff_days = (now.date() - last_date.date()).days
        if diff_days > ALERT_THRESHOLD_HOURS // 24:
            stale_stocks.append(f"{name}({sym}) [内容滞后 {diff_days}d, 最新 {last_date.strftime('%Y-%m-%d')}]")
            
    return stale_stocks

def check_state_freshness():
    """检查模拟盘状态文件更新时间"""
    if not os.path.exists(STATE_FILE):
        return ["模拟盘状态文件 (sim_state.json) 缺失"]
    
    mtime = datetime.fromtimestamp(os.path.getmtime(STATE_FILE))
    diff = datetime.now() - mtime
    if diff.total_seconds() > ALERT_THRESHOLD_HOURS * 3600:
        return [f"模拟盘状态滞后 {diff.days}d {diff.seconds//3600}h"]
    return []

def main():
    stale_csv = check_csv_freshness()
    stale_state = check_state_freshness()
    
    all_stale = stale_csv + stale_state
    
    if all_stale:
        print("🔴 [DATA_STALE_ALERT] 检测到数据滞后：")
        for item in all_stale:
            print(f" - {item}")
        sys.exit(1) # 触发 cron 告警
    else:
        print("✅ 所有核心数据源新鲜。")
        sys.exit(0)

if __name__ == "__main__":
    main()
