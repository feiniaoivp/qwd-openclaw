#!/usr/bin/env python3
"""
简单的测试脚本，用于验证MultiDimensionStrategy技能是否可以导入和使用。
"""

import sys
import os

# 添加技能路径，以便我们可以导入multi_dimension_strategy
skills_path = "/Users/duguke/.openclaw/workspace/skills"
if skills_path not in sys.path:
    sys.path.insert(0, skills_path)

import pandas as pd
import numpy as np

def main():
    print("=== 测试MultiDimensionStrategy技能 ===")
    
    # 1. 导入策略类
    try:
        from multi_dimension_strategy import run_multi_dimension_strategy, get_latest_signal
        print("✓ 成功导入multi_dimension_strategy模块")
    except ImportError as e:
        print(f"✗ 导入失败: {e}")
        return False
    
    # 2. 创建一些模拟数据
    print("\n=== 生成模拟股票数据 ===")
    np.random.seed(42)  # 为了结果可重复
    days = 50
    # 生成随机价格数据
    price_changes = np.random.normal(0.001, 0.02, days)
    initial_price = 100
    prices = initial_price * (1 + np.cumsum(price_changes))
    
    # 创建日期范围
    dates = pd.date_range(end=pd.Timestamp.now(), periods=days, freq='D')
    
    # 创建DataFrame，必须包含'date'和'close'列
    mock_data = pd.DataFrame({
        'date': dates,
        'close': prices,
        # 添加一些额外的列虽然策略不需要，但为了完整性
        'open': prices * (1 + np.random.uniform(-0.01, 0.01, days)),
        'high': prices * (1 + np.random.uniform(0, 0.02, days)),
        'low': prices * (1 + np.random.uniform(-0.02, 0, days)),
        'volume': np.random.randint(1000, 10000, days)
    })
    
    print(f"生成了 {len(mock_data)} 天的模拟数据")
    print(f"日期范围: {mock_data['date'].min().date()} 到 {mock_data['date'].max().date()}")
    print(f"价格范围: {mock_data['close'].min():.2f} - {mock_data['close'].max():.2f}")
    
    # 3. 应用策略
    print("\n=== 应用多维度量化策略 ===")
    try:
        result_df = run_multi_dimension_strategy(mock_data.copy())
        print("✓ 策略应用成功")
    except Exception as e:
        print(f"✗ 策略应用失败: {e}")
        return False
    
    # 4. 打印结果
    print("\n=== 策略计算结果 ===")
    print(f"数据列: {list(result_df.columns)}")
    
    # 显示最后5天的数据
    print("\n最近5天数据:")
    print(result_df[['date', 'close', 'MA_Fast', 'MA_Slow', 'MACD', 'RSI', 'Signal']].tail())
    
    # 获取最新信号
    signal_info = get_latest_signal(result_df)
    print(f"\n最新交易信号: {signal_info}")
    
    # 统计信号
    buy_signals = (result_df['Signal'] == 1).sum()
    sell_signals = (result_df['Signal'] == -1).sum()
    print(f"\n信号统计: 买入信号 {buy_signals} 次, 卖出信号 {sell_signals} 次")
    
    # 5. 保存结果到文件以供检查
    output_path = "/Users/duguke/.openclaw/workspace/strategy_test_result.csv"
    result_df.to_csv(output_path, index=False)
    print(f"\n✓ 完整结果已保存到: {output_path}")
    
    print("\n=== 测试完成 ===")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)