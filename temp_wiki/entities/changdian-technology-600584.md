---
pageType: entity
id: entity.changdian-technology
title: 长电科技 (600584)
entityType: stock
canonicalId: "600584.SH"
aliases:
  - "长电科技"
  - "600584"
sourceIds:
  - source.bridge.workspace-3bf1d827.memory-2d62c497
claims:
  - text: "2025-06-29 至 2026-06-29 年化收益 +207.66%，全组合第2"
    status: verified
    confidence: 0.95
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "最优策略：纯 MACD (基准B)，回测收益 +207%"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "KDJ+RSI 策略单票收益 +61.72% (均值回归爆发)"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "自适应分配策略：EMA12/26金叉 (ema_cross)"
    status: verified
    confidence: 0.85
    evidence:
      - kind: validation
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/validation_2026-08-01.json"
    updatedAt: "2026-08-01"
bestUsedFor:
  - "半导体封测龙头趋势跟踪"
  - "AI芯片封装需求受益股"
notEnoughFor:
  - "低波动防御配置"
confidence: 0.9
status: active
updatedAt: "2026-08-15"
---

# 长电科技 (600584)

## 概况
- **代码**: 600584.SH (主板)
- **行业**: 半导体封装测试 (OSAT全球前三，国内龙头)
- **关注池分类**: 半导体/TMT

## 核心表现 (2025-06 ~ 2026-06)
| 指标 | 数值 | 全组合排名 |
|------|------|------------|
| 年化收益 | **+207.66%** | 🥈 2/30 |
| 最大回撤 | -38.5% | 较高 |

## 策略适配性
| 策略 | 回测收益 | 夏普 | 适配度 |
|------|----------|------|--------|
| **纯 MACD** ⭐ | **+207%** | 0.48 | 最优 |
| EMA12/26 金叉 | +189% | 0.45 | 次优 |
| KDJ+RSI | **+61.72%** | 0.35 | 爆发力强 |
| 布林带+ATR | +112% | 0.29 | 中 |
| EMA+OBV | +67% | 0.21 | 弱 |

## 自适应分配历史
- **2026-07-26 初始**: EMA12/26 金叉 (ema_cross)
- **2026-08-01 验证**: EMA12/26 金叉 (ema_cross) 保持
- **当前生效**: EMA12/26 金叉

## 核心优势
1. **OSAT全球龙头**: FC-BGA/Chiplet/2.5D/3D 先进封装全产线布局
2. **AI算力受益**: GPU/HBM/高性能计算封装需求爆发
3. **客户多元**: 苹果/英伟达/AMD/华为/中芯国际等头部客户

## 关键风险提示
1. **周期性强**: 半导体周期下行业绩弹性大
2. **汇率敏感**: 出口占比高，美元走弱利好
3. **产能爬坡**: 新产线爬坡期固定成本压利润

## 监控要点
- [ ] EMA12/26 金叉死叉 (核心策略)
- [ ] FC-BGA/Chiplet 产能利用率
- [ ] AI芯片封装订单指引
- [ ] 季度毛利率/出货量