---
pageType: entity
id: entity.guoci-materials
title: 国瓷材料 (300285)
entityType: stock
canonicalId: "300285.SZ"
aliases:
  - "国瓷材料"
  - "300285"
sourceIds:
  - source.bridge.workspace-3bf1d827.memory-2d62c497
claims:
  - text: "2025-06-29 至 2026-06-29 年化收益 +513.66%，全组合冠军"
    status: verified
    confidence: 0.95
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
        lines: "120-125"
    updatedAt: "2026-07-26"
  - text: "年化波动率 72.80%，全组合最高"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "最优策略：纯 MACD (基准B)，回测收益 +315.80%"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "自适应分配策略：纯 MACD (2026-08-01 验证后保持)"
    status: verified
    confidence: 0.85
    evidence:
      - kind: validation
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/validation_2026-08-01.json"
    updatedAt: "2026-08-01"
bestUsedFor:
  - "强趋势题材股全波段捕捉"
  - "高波动成长股趋势跟踪"
notEnoughFor:
  - "震荡/弱趋势行情"
  - "低风险防御配置"
confidence: 0.9
status: active
updatedAt: "2026-08-15"
---

# 国瓷材料 (300285)

## 概况
- **代码**: 300285.SZ (创业板)
- **行业**: 特种陶瓷 / 半导体封装材料 / 新能源材料
- **关注池分类**: 高端制造/材料

## 核心表现 (2025-06 ~ 2026-06)
| 指标 | 数值 | 全组合排名 |
|------|------|------------|
| 年化收益 | **+513.66%** | 🥇 1/30 |
| 年化波动率 | **72.80%** | 🥇 1/30 (最高) |
| 最大回撤 | -45.2% | 高风险 |

## 策略适配性
| 策略 | 回测收益 | 夏普 | 适配度 |
|------|----------|------|--------|
| **纯 MACD** ⭐ | **+315.80%** | 0.52 | 最优 |
| EMA12/26 金叉 | +298.40% | 0.48 | 次优 |
| 布林带+ATR | +156.20% | 0.31 | 一般 |
| EMA+OBV | +89.50% | 0.22 | 弱 |
| KDJ+RSI | -12.30% | -0.15 | 失效 |

## 自适应分配历史
- **2026-07-26 初始**: 纯 MACD
- **2026-08-01 验证**: 纯 MACD (得分 68.4，夏普 0.51，笔数 22，满权)
- **当前生效**: 纯 MACD

## 关键风险提示
1. **极高波动**: 单日振幅常超 10%，回撤深达 -45%
2. **流动性风险**: 日均成交额中等，大单冲击成本高
3. **政策敏感**: 半导体材料受出口管制/产业政策影响大
4. **估值偏高**: 市盈率常处于行业高位，基本面支撑需持续验证

## 监控要点
- [ ] MACD 金叉/死叉信号 (核心策略触发器)
- [ ] 成交量异常放大 (主力进出信号)
- [ ] 半导体封装材料行业政策/产能扩张公告
- [ ] 季度财报毛利率/出货量变化