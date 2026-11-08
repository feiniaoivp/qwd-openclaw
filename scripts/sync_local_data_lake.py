#!/usr/bin/env python3
"""
本地数据湖增量同步脚本 (07:00 cron)
====================================
每日 07:00 运行，从 baostock 拉取近 60 日增量，追加写入 data/runtime/stock_data/*.csv
作为网络数据源的最后一级兜底。

使用方式：
    python3 scripts/sync_local_data_lake.py

Cron 配置（在 cron 中配置）：
    0 7 * * 1-5 /usr/bin/python3 /Users/duguke/.openclaw/workspace/scripts/sync_local_data_lake.py
"""

import os
import sys
import logging
from datetime import datetime

WORKSPACE = "/Users/duguke/.openclaw/workspace"
sys.path.insert(0, WORKSPACE)

from analysis.data_layer.local_store import sync_batch
from analysis.fib_extension_scan import WATCHLIST

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(WORKSPACE, "logs", "sync_local_data_lake.log")),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

def get_baostock_fetch_func():
    """返回 baostock 获取函数"""
    from analysis.data_layer.providers.baostock import get_provider
    provider = get_provider()
    
    def fetch_func(symbol: str, start_date: str = "20240101", adjust: str = "qfq"):
        return provider.get_daily(symbol, start_date=start_date, adjustflag="2" if adjust == "qfq" else "3")
    
    return fetch_func

def main():
    print(f"📦 本地数据湖增量同步启动: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 获取关注池股票代码（去重后的 36 只）
    symbols = [s for s in WATCHLIST.keys()]
    print(f"📊 同步标的: {len(symbols)} 只")
    
    # 获取 baostock fetch 函数
    fetch_func = get_baostock_fetch_func()
    
    # 执行批量同步
    results = sync_batch(
        symbols=symbols,
        fetch_func=fetch_func,
        adjust="qfq",
        source="baostock",
        lookback_days=60
    )
    
    success_count = sum(1 for v in results.values() if v)
    failed = [s for s, v in results.items() if not v]
    
    print(f"✅ 同步完成: {success_count}/{len(symbols)} 成功")
    if failed:
        print(f"❌ 失败标的: {', '.join(failed)}")
    
    # 生成新鲜度报告
    from analysis.data_layer.local_store import get_freshness_report
    freshness = get_freshness_report(symbols)
    fresh_count = sum(1 for v in freshness.values() if v.get("is_fresh"))
    print(f"📈 数据新鲜度: {fresh_count}/{len(symbols)} 标的在 2 天内同步")
    
    return 0 if success_count > 0 else 1

if __name__ == "__main__":
    os.makedirs(os.path.join(WORKSPACE, "logs"), exist_ok=True)
    sys.exit(main())