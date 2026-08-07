# 多维度量化策略 (Multi-Dimension Quantitative Strategy)

这是一个融合趋势、动量和超买超卖指标的量化交易策略技能。该策略设计用于A股市场，结合了多个技术指标来生成买卖信号，并包含风险控制机制。

## 功能特点

1. **多维度信号融合**
   - 趋势维度：快慢均线（MA_Fast/MA_Slow）金叉/死叉
   - 动量维度：MACD指标确认趋势强度
   - 超买超卖维度：RSI指标过滤信号质量
   - 风险控制：5%硬性止损线保护资本

2. **易于集成**
   - 标准化接口：接受包含`date`和`close`列的DataFrame
   - 返回增强的DataFrame，包含所有技术指标和交易信号
   - 提供便捷函数获取最新交易信号

3. **可配置参数**
   - 均线周期：快线(default=10)、慢线(default=30)
   - RSI参数：周期(default=14)、买入阈值(default=40)、卖出阈值(default=70)
   - 止损比例：default=5%

## 使用方法

### 基本使用
```python
import pandas as pd
from multi_dimension_strategy import run_multi_dimension_strategy, get_latest_signal

# 准备数据（必须包含date和close列）
df = pd.DataFrame({
    'date': date_range',
    'close': [])})

# 应用应用 
sult_df = run_multi_dimension_strategy(df.copy())

# 获取最新交易信号
ignal_info = get_latest_signal(result_df)
print(f"最新信号: {signal_info}")

# 统计交易信号
买入信号 = (result_df['Signal'] == 1).sum()
卖出信号 = (result_df['Signal'] == -1).sum()
print(f"买入信号: {buy_signal}次, 卖出信号: {sell_signal}次")
```

### 高级用法 - 自定义参数
```python
from multi_dimension_strategy import MultiDimensionStrategy

# 创建自定义参数的策略实例
strategy = MultiDimensionStrategy(
    fast_ma=5,      # 更敏感的快线
    slow_ma=20,     # 中期慢线
    rsi_period=10,  # 更短的RSI周期
    rsi_buy_thresh=30,  # 更严格的超卖买入
    rsi_sell_thresh=80, # 更宽松的超卖卖出
    stop_loss_pct=0.03  # 3%更紧的止损
)

# 使用自定义策略
结果df = strategy.run_backtest(您的数据框副本)
```

## 返回值说明

`run_multi_dimension_strategy()` 或 `strategy.run_backtest()` 返回的DataFrame包含以下列：

- 原始数据列：date, open, close, high, low, volume（如果输入包含）
- 计算的技术指标：
  - MA_Fast: 快速移动平均线
  - MA_Slow: 慢速移动平均线
  - MACD: MACD线
  - Signal_Line: MACD信号线
  - RSI: 相对强弱指数 (0-100)
- 交易信号：
  - Signal: 1=买入, -1=卖出, 0=持有/观望

`get_latest_signal(df)` 返回一个字典，包含：
- **最新交易信号**（1买入，-1卖出，0持有）
  - `close`: **最新收盘价**
  - `rsi`: **最新RSI值**
  - `macd`: **最新MACD值**
  - `signal_line`: **最新MACD信号线值**
  - `ma_fast`: **最新快速均线值**
  - `ma_slow`: **最新慢速均线值**

## 与gh-data技能集成

此技能设计为可以无缝集成到现有的gh-data技能工作流程中：

1. 使用gh-data获取股票K线数据：
   ```python
   from ghdata import data_fetcher as fetcher
   klines = fetcher.fetch_kline("600160", 100)  # 获取100天数据
   ```

2. 转换为DataFrame并应用多维度策略：
   ```python
   import pandas as pd
   from multi_dimension_strategy import run_multi_dimension_strategy
   
   df = pd.DataFrame(klines)
   result_df = run_multi_dimension_strategy(df)
   ```

3. 生成增强报告：
   - 策略信号可以添加到报告的第14章作为"多维度量化共振模型评级"
   - 可以在报告中显示最新信号、历史信号统计等信息

## 风险提示

- 此策略仅用于技术分析和研究目的
- 过去的表现不代表未来结果
- 实际交易请结合基本面分析、市场环境和个人风险承受能力
- 建议在模拟盘或小资金情况下先进行充分测试

## 参考文献

1. 移动平均线交叉策略 - 经典趋势跟踪方法
2. MACD指标 - 趋势动量的经典工具
3. RSI指标 - 相对强弱指标，衡量超买超卖
4. 止损策略 - 风险管理的基本工具

## 版本信息

- 版本: 1.0.0
- 创建日期: 2026-06-30
- 依赖: pandas, numpy
- 与gh-data技能完全兼容