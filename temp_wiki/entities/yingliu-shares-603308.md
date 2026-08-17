---
pageType: entity
id: entity.yingliu-shares
title: 应流股份 (603308)
entityType: stock
canonicalId: "603308.SH"
aliases:
  - "应流股份"
  - "603308"
sourceIds:
  - source.bridge.workspace-3bf1d827.memory-2d62c497
claims:
  - text: "2025-06-29 至 2026-06-29 年化收益 +158.10%，全组合第4"
    status: verified
    confidence: 0.95
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "最优策略：EMA12/26金叉 (ema_cross)，回测收益 +201%"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "自适应分配策略：EMA12/26金叉 (ema_cross)，验证后保持"
    status: verified
    confidence: 0.85
    evidence:
      - kind: validation
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/validation_2026-08-01.json"
    updatedAt: "2026-08-01"
bestUsedFor:
  - "高端装备材料趋势跟踪"
  - "稀缺材料国产替代受益股"
notEnoughFor:
  - "低波动防御配置"
confidence: 0.9
status: active
updatedAt: "2026-08-15"
---

# 应流股份 (603308)

## 概况
- **代码**: 603308.SH (主板)
- **行业**: 高性能合金/特种材料/航空航天材料
- **关注池分类**: 高端制造/材料

## 核心表现 (2025-06 ~ 2026-06)
| 指标 | 数值 | 全组合排名 |
|------|------|------------|
| 年化收益 | **+158.10%** | 4/30 |

## 策略适配性
| 策略 | 回测收益 | 夏普 | 适配度 |
|------|----------|------|--------|
| **EMA12/26 金叉** ⭐ | **+201%** | 0.52 | 最优 |
| 纯 MACD | +178% | 0.47 | 强 |
| 布林带+ATR | +98% | 0.31 | 中 |
| EMA+OBV | +56% | 0.18 | 弱 |
| KDJ+RSI | +12% | 0.08 | 失效 |

## 自适应分配历史
- **2026-07-26 初始**: EMA12/26 金叉 (ema_cross)
- **2026-08-01 验证**: EMA12/26 金叉 (ema_cross) 保持
- **当前生效**: EMA12/26 金叉

## 核心优势
1. **稀缺材料龙头**: 高温合金/钛合金/超合金军民两用
2. **国产替代核心**: 航空发动机/燃气轮机关键材料突围
3. **订单确定性高**: 军工/民航双轮驱动，排产饱满

## 关键风险提示
1. **军工保密性**: 信息披露滞后，基本面验证难
2. **原材料成本**: 钨/钴/铼等稀有金属价格波动传导
3. **下游周期**: 民航/军工周期共振风险

## 监控要点
- [ ] EMA12/26 金叉死叉 (核心策略)
- [ ] 军工订单/型号定型进度
- [ ] 稀有金属价格指数
- [ ] 季度扣非净利润增速