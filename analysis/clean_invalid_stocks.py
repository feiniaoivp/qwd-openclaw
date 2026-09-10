#!/usr/bin/env python3
"""
清理已移出关注池的无效股票
从 adaptive_strategy_map.json, adaptive_params.json, adaptive_params_prev.json 中移除
"""

import os
import json

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE, "data")

# 已移出关注池的股票 (2026-08-30 移除)
INVALID_STOCKS = {
    "002180": "奔图科技",
    "300847": "中船汉光", 
    "601865": "福莱特",
}

def clean_file(filepath, description):
    """清理单个 JSON 文件"""
    if not os.path.exists(filepath):
        print(f"  ⚠️ {description} 不存在: {filepath}")
        return 0
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        removed = 0
        if isinstance(data, dict):
            # 处理 adaptive_strategy_map.json 格式: {"symbol": "strategy"}
            for sym in list(data.keys()):
                if sym in INVALID_STOCKS:
                    del data[sym]
                    removed += 1
                    print(f"  🗑️ {description}: 移除 {INVALID_STOCKS[sym]}({sym})")
        
        elif isinstance(data, dict) and "stocks" in data:
            # 处理 adaptive_params.json 格式: {"date": "...", "mode": "...", "stocks": {...}}
            stocks = data.get("stocks", {})
            for sym in list(stocks.keys()):
                if sym in INVALID_STOCKS:
                    del stocks[sym]
                    removed += 1
                    print(f"  🗑️ {description}: 移除 {INVALID_STOCKS[sym]}({sym})")
        
        if removed > 0:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"  ✅ {description}: 已清理 {removed} 只无效股票")
        else:
            print(f"  ✅ {description}: 无需清理")
        
        return removed
        
    except Exception as e:
        print(f"  ❌ {description} 清理失败: {e}")
        return 0

def main():
    print("🧹 清理已移出关注池的无效股票")
    print("=" * 50)
    for sym, name in INVALID_STOCKS.items():
        print(f"  - {name}({sym})")
    print()
    
    files = [
        (os.path.join(DATA_DIR, "adaptive_strategy_map.json"), "策略映射"),
        (os.path.join(DATA_DIR, "adaptive_params.json"), "参数配置"),
        (os.path.join(DATA_DIR, "adaptive_params_prev.json"), "参数配置备份"),
    ]
    
    total_removed = 0
    for filepath, desc in files:
        total_removed += clean_file(filepath, desc)
    
    print(f"\n🎉 清理完成，共移除 {total_removed} 条无效记录")

if __name__ == "__main__":
    main()
