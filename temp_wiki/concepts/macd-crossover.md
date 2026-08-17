---
pageType: concept
id: concept.macd-crossover
title: 纯 MACD 金叉死叉策略 (基准B)
entityType: strategy
canonicalId: "strategy.macd-crossover"
aliases:
  - "纯MACD"
  - "MACD金叉死叉"
  - "基准B策略"
sourceIds:
  - source.bridge.workspace-3bf1d827.memory-2d62c497
claims:
  - text: "2024-01 至 2026-07 25只股票回测平均收益 +57.78%，总分 29.1 亚军"
    status: verified
    confidence: 0.95
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "夏普比率 0.40 (全策略最高)，最大回撤 -30.2% (最小)，胜率 39.2%，盈亏比 2.71"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "适用于 9 只中大盘蓝筹/趋势平滑股：中信/中金/长电/招商/福莱蒽特/越秀/国瓷/福耀/恒立"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "国瓷材料 +315.80% 单票贡献最大"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
bestUsedFor:
  - "中大盘蓝筹趋势跟踪"
  - "回撤控制要求高的组合"
  - "趋势相对平滑、波动可控的股票"
notEnoughFor:
  - "高波动题材股 (滞后信号错失最佳点位)"
  - "震荡行情 (频繁假突破)"
confidence: 0.9
status: active
updatedAt: "2026-08-15"
---

# 纯 MACD 金叉死叉策略 (基准B)

## 策略定义
- **买入**: MACD 线 (DIF) 上穿 信号线 (DEA) — 金叉
- **卖出**: MACD 线 (DIF) 下穿 信号线 (DEA) — 死叉
- **参数**: 快线 12、慢线 26、信号 9 (标准参数)
- **无 RSI/均线过滤**: 纯 MACD 信号 (2026-07-23 去除 RSI 过滤后收益从 -5.29% 翻到 +28.45%)

## 核心指标 (25只 × 1.5年回测)
| 指标 | 数值 | 排名 |
|------|------|------|
| 平均收益 | **+57.78%** | 🥈 2/5 |
| 夏普比率 | **0.40** | 🥇 1/5 (最高) |
| 最大回撤 | **-30.2%** | 🥇 1/5 (最小) |
| 胜率 | 39.2% | 1/5 |
| 盈亏比 | 2.71 | 2/5 |
| 综合总分 | **29.1** | 🥈 2/5 |

## 适用股票 (9只，自适应分配)
| 股票 | 代码 | 板块 | 回测收益 |
|------|------|------|----------|
| 中信证券 | 600030 | 券商 | +78% |
| 中金公司 | 601995 | 券商 | +92% |
| 长电科技 | 600584 | 半导体 | +207% |
| 招商银行 | 600036 | 银行 | +45% |
| 福莱蒽特 | 605566 | 消费 | +67% |
| 越秀资本 | 000987 | 金融 | +89% |
| 国瓷材料 | 300285 | 材料 | **+315.80%** |
| 福耀玻璃 | 600660 | 消费 | +56% |
| 恒立液压 | 601100 | 高端制造 | +71% |

## 优势特征
1. **夏普王/回撤王**: 双料冠军，风险调整后收益最优
2. **平滑度高**: MACD 双重平滑过滤高频噪音
3. **中大盘友好**: 招行/中信/福耀等权重股表现稳健
4. **参数标准化**: 12/26/9 为全市场通用参数，无拟合嫌疑

## 劣势与局限
1. **滞后性强**: 死叉离场通常回吐 20-30% 利润
2. **震荡市假信号多**: 横盘期 MACD 线频繁穿越零轴/信号线
3. **高波动股滞后**: 国瓷等爆发股买入点往往在主升浪中段
4. **无动态止损**: 纯信号策略，需外挂 ATR/支撑位

## 跨周期验证表现 (2026-08-01)
| 窗口 | 得分 | 夏普 | 笔数 | 权重 | 结论 |
|------|------|------|------|------|------|
| W1 全量 | 65.8 | 0.45 | 31 | 1.0 | 优秀 |
| W2 近3年 | 61.2 | 0.42 | 24 | 1.0 | 优秀 |
| W3 近1.5年 | 52.1 | 0.37 | 16 | 1.0 | 良好 |
| W4 近1年 | 44.3 | 0.32 | 9 | 0.75 | 边际 |

## 实施代码位置
- **回测**: `analysis/backtest_strategies.py` → `StrategyMacdPure`
- **扫描**: `analysis/adaptive_trader.py` → `run_macd_pure()`
- **映射**: `data/adaptive_strategy_map.json` → `"macd"`

## 关键参数
```python
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
```