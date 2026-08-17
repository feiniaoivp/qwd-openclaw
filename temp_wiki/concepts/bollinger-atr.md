---
pageType: concept
id: concept.bollinger-atr
title: 布林带 + ATR 策略
entityType: strategy
canonicalId: "strategy.bollinger-atr"
aliases:
  - "布林带ATR"
  - "布林带策略"
sourceIds:
  - source.bridge.workspace-3bf1d827.memory-2d62c497
claims:
  - text: "2024-01 至 2026-07 25只股票回测平均收益 +44.27%，总分 22.4 季军"
    status: verified
    confidence: 0.95
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "盈亏比 2.79 (全策略最高)，交易次数最少 (仅 11 次)，夏普 0.34，最大回撤 -28.9%"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "适用于 1 只高爆发题材股：亿纬锂能 (最优 +68%)"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
bestUsedFor:
  - "爆发力强、波动大的题材/成长股"
  - "趋势启动早期捕捉"
  - "动态止损保护利润"
notEnoughFor:
  - "震荡/横盘行情"
  - "低波动蓝筹股"
  - "高频交易需求"
confidence: 0.85
status: active
updatedAt: "2026-08-15"
---

# 布林带 + ATR 策略

## 策略定义
- **买入**: 收盘价突破布林带上轨 (20周期，2倍标准差)
- **卖出**: 收盘价跌破布林带中轨 (20周期移动平均线)
- **止损**: ATR(14) × 2.0 动态止损，随波动率自适应调整
- **参数**: BB(20,2) + ATR(14)×2.0

## 核心指标 (25只 × 1.5年回测)
| 指标 | 数值 | 排名 |
|------|------|------|
| 平均收益 | +44.27% | 3/5 |
| 夏普比率 | 0.34 | 4/5 |
| 最大回撤 | -28.9% | 2/5 |
| 胜率 | 32.1% | 4/5 |
| 盈亏比 | **2.79** | 🥇 1/5 |
| 交易次数 | **11次** | 🥇 最少 |
| 综合总分 | 22.4 | 3/5 |

## 适用股票 (1只)
| 股票 | 代码 | 板块 | 回测收益 |
|------|------|------|----------|
| 亿纬锂能 | 300014 | 锂电/储能 | **+68%** (最优) |

## 优势特征
1. **盈亏比之王**: 2.79 远超其他策略，该赚的赚足、该止损的止得快
2. **动态止损**: ATR 随波动率自适应，保护利润回撤最小 (-28.9%)
3. **低频高胜**: 仅 11 次交易，手续费/滑点成本极低
4. **趋势启动捕捉**: 上轨突破往往对应主升浪启动

## 劣势与局限
1. **错失震荡机会**: 交易极少，横盘/宽幅震荡完全空仓
2. **假突破风险**: 上轨突破后快速回落的假突破会触发止损
3. **参数敏感**: BB 周期/倍数、ATR 倍数需针对品种调优
4. **不适合蓝筹**: 低波动股票布林带收窄，信号噪音比极低

## 关键技术坑 (已修复)
- **pandas_ta bbands 列名版本差异**: `BBU_20_2.0` vs `BBU_20_2.0_2.0` → 动态列名匹配修复

## 实施代码位置
- **回测**: `analysis/backtest_strategies.py` → `StrategyBollingerAtr`
- **扫描**: `analysis/adaptive_trader.py` → `run_bollinger_atr()`
- **映射**: `data/adaptive_strategy_map.json` → `"bollinger_atr"`

## 关键参数
```python
BB_PERIOD = 20
BB_STD = 2.0
ATR_PERIOD = 14
ATR_MULTIPLIER = 2.0
```