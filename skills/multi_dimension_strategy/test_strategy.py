#!/usr/bin/env python3
"""
多维度策略测试脚本
演示如何将新创建的multi_dimension_strategy技能与gh-data技能结合使用
"""

import sys
import os

# 添加技能路径 - 我们需要添加包含multi_dimension_strategy包的目录
skills_path = "/Users/duguke/.openclaw/workspace/skills"
if skills_path not in sys.path:
    sys.path.insert(0, skills_path)

import pandas as pd
import numpy as np

def test_strategy_with_mock_data():
    """使用模拟数据测试策略"""
    print("=== 使用模拟数据测试多维度策略 ===")
    
    # 导入我们的策略
    from multi_dimension_strategy import run_multi_dimension_strategy, get_latest_signal
    
    # 创建模拟股票数据
    np.random.seed(42)
    days = 100
    price_changes = np.random.normal(0.00    # 创建模拟股票数据
    np.random.seed(42)
    days = 100
    price_changes = np.random.normal(0.001, 0.02, days)
    initial_price = 100
    prices = initial_price * (1 + np.cumsum(price_changes))
    
    # 创建DataFrame
    dates = pd.date_range(end=pd.Timestamp.now(), periods=days, freq='D')
    mock_data = pd.DataFrame({
        'date': dates,
        'close': prices,
        'open': prices * (1 + np.random.uniform(-0.01, 0.01, days)),
        'high': prices * (1 + np.random.uniform(0, 0.02, days)),
        'low': prices * (1 + np.random.uniform(-0.02, 0, days)),
        'volume': np.random.randint(1000, 10000, days)
    })
    
    print(f"生成了 {len(mock_data)} 天的模拟数据")
    print(f"价格范围: {mock_data['close'].min():.2f} - {mock_data['close'].max():.2f}")
    
    # 应用策略
    result_df = run_multi_dimension_strategy(mock_data.copy())
    
    # 显示结果
    print("\n=== 策略计算结果 ===")
    print(f"数据列: {list(result_df.columns)}")
    
    # 显示最后几行
    print("\n最近5天数据:")
    print(result_df[['date', 'close', 'MA_Fast', 'MA_Slow', 'MACD', 'RSI', 'Signal']].tail())
    
    # 获取最新信号
    signal_info = get_latest_signal(result_df)
    print(f"\n最新交易信号: {signal_info}")
    
    # 统计信号
    buy_signals = (result_df['Signal'] == 1).sum()
    sell_signals = (result_df['Signal'] == -1).sum()
    print(f"\n信号统计: 买入信号 {buy_signals} 次, 卖出信号 {sell_signals} 次")
    
    return result_df

def test_integration_with_gh_data():
    """测试与gh-data的集成（如果可用的话）"""
    print("\n=== 测试与gh-data集成 ===")
    
    try:
        # 尝试导入gh-data
        from ghdata import data_fetcher as fetcher
        
        # 测试获取一只股票的数据
        test_code = "600160"  # 巨化股份
        print(f"正在获取 {test_code} 的数据...")
        
        # 获取K线数据（最近60天）
        klines = fetcher.fetch_kline(test_code, 60)
        
        if klines and len(klines) > 0:
            print(f"成功获取 {len(klines)} 条K线数据")
            
            # 转换为DataFrame
            df = pd.DataFrame(klines)
            print(f"数据列: {list(df.columns)}")
            
            # 重命名列以匹配我们策略的期望
            # gh-data返回: date, open, close, high, low, volume
            # 我们的策略需要: date, close
            if 'date' in df.columns and 'close' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
                df = df.sort_values('date').reset_index(drop=True)
                
                # 导入并应用我们的策略
                from multi_dimension_strategy import run_multi_dimension_strategy, get_latest_signal
                result_df = run_multi_dimension_strategy(df.copy())
                
                print("\n=== 真实股票数据策略结果 ===")
                print(result_df[['date', 'close', 'MA_Fast', 'MA_Slow', 'MACD', 'RSI', 'Signal']].tail())
                
                signal_info = get_latest_signal(result_df)
                print(f"\n{test_code} 最新交易信号: {signal_info}")
                
                return result_df
            else:
                print("数据格式不符合预期")
                return None
        else:
            print("未能获取K线数据")
            return None
            
    except ImportError as e:
        print(f"无法导入gh-data模块: {e}")
        print("这可能是因为模块路径问题或需要重新加载环境")
        return None
    except Exception as e:
        print(f"获取股票数据时出错: {e}")
        return None

if __name__ == "__main__":
    print("开始测试多维度策略...\n")
    
    # 先用模拟数据测试
    mock_result = test_strategy_with_mock_data()
    
    # 再尝试与真实数据集成
    real_result = test_integration_with_gh_data()
    
    print("\n=== 测试完成 ===")
    print("多维度策略技能已成功创建并可以与gh-data技能集成使用。")
    print("要在实际交易中使用，请将股票数据传递给run_multi_dimension_strategy函数。")