---
pageType: entity
id: entity.tongfu-microelectronics
title: 通富微电 (002156)
entityType: stock
canonicalId: "002156.SZ"
aliases:
  - "通富微电"
  - "002156"
sourceIds:
  - source.bridge.workspace-3bf1d827.memory-2d62c497
claims:
  - text: "2025-06-29 至 2026-06-29 年化收益 +184.18%，全组合第3"
    status: verified
    confidence: 0.95
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "年化波动率 57.90%，全组合第2"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "日均成交量 1,012,013 手，全组合第2"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
    updatedAt: "2026-07-26"
  - text: "最优策略：EMA12/26金叉 (基准C)，回测收益 +195%"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "自适应分配策略：EMA+OBV (2026-08-01 验证后从ema_cross改为ema_obv)"
    status: verified
    confidence: 0.85
    evidence:
      - kind: validation
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/validation_2026-08-01.json"
    updatedAt: "2026-08-01"
bestUsedFor:
  - "半导体封装测试龙头趋势跟踪"
  - "高换手率活跃股趋势捕捉"
notEnoughFor:
  - "低波动防御配置"
  - "均值回归策略"
confidence: 0.9
status: active
updatedAt: "2026-08-15"
---

# 通富微电 (002156)

## 概况
- **代码**: 002156.SZ (中小板)
- **行业**: 半导体封装测试 (OSAT龙头)
- **关注池分类**: 半导体/TMT

## 核心表现 (2025-06 ~ 2026-06)
| 指标 | 数值 | 全组合排名 |
|------|------|------------|
| 年化收益 | **+184.18%** | 🥉 3/30 |
| 年化波动率 | **57.90%** | 🥈 2/30 |
| 日均成交量 | **1,012,013 手** | 🥈 2/30 |

## 策略适配性
| 策略 | 回测收益 | 夏普 | 适配度 |
|------|----------|------|--------|
| **EMA12/26 金叉** ⭐ | **+195%** | 0.51 | 初始最优 |
| **EMA+OBV** ⭐ | +178% | 0.49 | **验证后最优** |
| 纯 MACD | +156% | 0.42 | 强 |
| 布林带+ATR | +98% | 0.28 | 中 |
| KDJ+RSI | +23% | 0.12 | 弱 |

## 自适应分配历史
- **2026-07-26 初始**: EMA12/26 金叉 (ema_cross)
- **2026-08-01 验证**: **EMA+OBV (ema_obv)** - 样本量充足(18笔)，回撤-20%健康，得分权重满分
- **当前生效**: EMA+OBV

## 核心优势
1. **OSAT龙头**: 封测市占率国内前三，受益于先进封装(Chiplet/2.5D/3D)放量
2. **高流动性**: 日均千万手，大单进出冲击成本低
3. **业绩弹性**: 产能利用率提升直接放大利润

## 关键风险提示
1. **周期波动**: 半导体周期下行期业绩回撤大
2. **客户集中度**: 头部客户(AMD/华为/中芯等)订单波动影响大
3. **技术迭代**: 先进封装技术路线不确定性

## 监控要点
- [ ] EMA20/OBV 共振信号 (核心策略触发器)
- [ ] 封测行业产能利用率/价格指数
- [ ] 先进封装(FC-BGA/Chiplet)新产线爬坡进度
- [ ] 季度毛利率/净利率变化趋势