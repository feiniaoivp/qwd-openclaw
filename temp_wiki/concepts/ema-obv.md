---
pageType: concept
id: concept.ema-obv
title: EMA + OBV 量价共振策略
entityType: strategy
canonicalId: "strategy.ema-obv"
aliases:
  - "EMA+OBV"
  - "EMA20+OBV"
  - "量价共振策略"
sourceIds:
  - source.bridge.workspace-3bf1d827.memory-2d62c497
claims:
  - text: "2024-01 至 2026-07 25只股票回测平均收益 +19.59%，总分 14.2 第4"
    status: verified
    confidence: 0.95
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "盈亏比 4.29 (全策略最高)，胜率最低 24.7%，最大回撤 -35.6%"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "适用于 4 只弱趋势/震荡股：福莱特、中联重科、中信金属、科华数据 (唯一盈利策略)"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "2026-08-01 验证：通富微电从 ema_cross 改为 ema_obv (样本量18笔满权，回撤-20%健康)；天齐锂业从 ema_cross 改为 ema_obv"
    status: verified
    confidence: 0.85
    evidence:
      - kind: validation
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/validation_2026-08-01.json"
    updatedAt: "2026-08-01"
bestUsedFor:
  - "弱趋势/震荡股反转捕捉"
  - "量价背离/共振确认"
  - "主力进场明显的放量突破股"
notEnoughFor:
  - "强趋势单边行情 (EMA12/26更优)"
  - "高波动题材股 (布林带ATR更优)"
  - "低胜率心理承受力弱的账户"
confidence: 0.85
status: active
updatedAt: "2026-08-15"
---

# EMA + OBV 量价共振策略

## 策略定义
- **买入**: 收盘价站上 EMA20 **且** OBV 上穿其 N 日移动线 (量价同步向上)
- **卖出**: 收盘价跌破 EMA20 **或** OBV 下穿移动线 (量价背离)
- **参数**: EMA(20) + OBV(20日均线)

## 核心指标 (25只 × 1.5年回测)
| 指标 | 数值 | 排名 |
|------|------|------|
| 平均收益 | +19.59% | 4/5 |
| 夏普比率 | 0.28 | 5/5 |
| 最大回撤 | -35.6% | 5/5 (最大) |
| 胜率 | **24.7%** | 5/5 (最低) |
| 盈亏比 | **4.29** | 🥇 1/5 (最高) |
| 综合总分 | 14.2 | 4/5 |

## 适用股票 (4只初始 + 2只验证后新增 = 6只)
| 股票 | 代码 | 板块 | 回测收益 | 状态 |
|------|------|------|----------|------|
| 福莱特 | 601865 | 光伏玻璃 | +18% | 初始最优 |
| 中联重科 | 000157 | 工程机械 | +22% | 初始最优 |
| 中信金属 | 601061 | 有色金属 | +31% | 初始最优 |
| 科华数据 | 002335 | 数据中心 | +15% | 初始最优 |
| **通富微电** | 002156 | 半导体 | +178% | **验证后新增** ⭐ |
| **天齐锂业** | 002466 | 锂电 | +142% | **验证后新增** ⭐ |

## 优势特征
1. **盈亏比惊人 4.29**: 该赚的抓得极狠，一次盈利覆盖 4 次亏损
2. **量价共振过滤**: OBV 确认主力资金真实进场，过滤假突破
3. **弱趋势救星**: 福莱特/中联重科等其他策略全亏、仅此策略盈利
4. **反转捕捉强**: 底部放量突破 EMA20 往往是主升浪起点

## 劣势与局限
1. **胜率极低 24.7%**: 4 次交易 3 次亏，心理压力极大
2. **最大回撤 -35.6%**: 连续假信号会大幅回撤，需严格资金管理
3. **假突破多**: 震荡市 OBV 频繁穿越均线，假信号密集
4. **参数依赖**: OBV 均线周期、EMA 周期需针对品种调优

## 关键技术点
- OBV 计算: `OBV[i] = OBV[i-1] + volume[i] * sign(close[i] - close[i-1])`
- 信号确认: 价格站上 EMA20 **且** OBV > OBV_MA 同时成立

## 实施代码位置
- **回测**: `analysis/backtest_strategies.py` → `StrategyEmaObv`
- **扫描**: `analysis/adaptive_trader.py` → `run_ema_obv()`
- **映射**: `data/adaptive_strategy_map.json` → `"ema_obv"`

## 关键参数
```python
EMA_PERIOD = 20
OBV_MA_PERIOD = 20
```