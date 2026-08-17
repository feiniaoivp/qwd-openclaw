---
pageType: concept
id: concept.kdj-rsi
title: KDJ + RSI 均值回归策略 (v2，去CCI)
entityType: strategy
canonicalId: "strategy.kdj-rsi"
aliases:
  - "KDJ+RSI"
  - "KDJ RSI 均值回归"
sourceIds:
  - source.bridge.workspace-3bf1d827.memory-2d62c497
claims:
  - text: "2024-01 至 2026-07 25只股票回测平均收益 +1.72%，总分 3.1 垫底"
    status: verified
    confidence: 0.95
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "最大回撤 -18.31% (最小)，胜率 35.8%，盈亏比 1.35，但中芯国际 +64.57%、长电科技 +61.72% 单票爆发"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "适用于 1 只：安达维尔 (最优)；去除 CCI 后 v2 版本大幅改善"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
bestUsedFor:
  - "超跌核心成长股反弹捕捉"
  - "区间震荡股高抛低吸"
  - "RSI超卖+KDJ金叉双重确认"
notEnoughFor:
  - "强趋势单边行情 (易被套)"
  - "趋势跟踪需求"
  - "整体组合核心策略 (收益太低)"
confidence: 0.8
status: active
updatedAt: "2026-08-15"
---

# KDJ + RSI 均值回归策略 (v2，去CCI)

## 策略定义
- **买入**: KDJ 金叉 (K 上穿 D) **且** RSI(14) < 40 (超卖区)
- **卖出**: KDJ 死叉 (K 下穿 D)
- **版本演进**: v1 包含 CCI 过滤 → 前复权导致 CCI 失真 → v2 去除 CCI，仅保留 KDJ+RSI
- **参数**: KDJ(9,3,3) + RSI(14)

## 核心指标 (25只 × 1.5年回测)
| 指标 | 数值 | 排名 |
|------|------|------|
| 平均收益 | +1.72% | 5/5 |
| 夏普比率 | 0.18 | 5/5 |
| 最大回撤 | **-18.31%** | 🥇 1/5 (最小) |
| 胜率 | 35.8% | 3/5 |
| 盈亏比 | 1.35 | 5/5 |
| 综合总分 | 3.1 | 5/5 |

## 适用股票 (1只)
| 股票 | 代码 | 板块 | 回测收益 |
|------|------|------|----------|
| 安达维尔 | 300719 | 磁材/新能源 | **最优** |

## 单票爆发案例
| 股票 | 代码 | 该策略收益 | 备注 |
|------|------|------------|------|
| 中芯国际 | 688981 | **+64.57%** | 超跌反弹完美捕捉 |
| 长电科技 | 600584 | **+61.72%** | 均值回归极致表现 |

## 优势特征
1. **回撤最小**: -18.31% 远优于其他策略，防守极强
2. **超跌反弹精准**: RSI<40 过滤掉追高风险，KDJ 金叉确认反转
3. **核心成长股友好**: 中芯/长电等超跌优质股表现惊艳
4. **频率适中**: 交易次数适中，不过度交易

## 劣势与局限
1. **整体收益太低**: +1.72% 几乎跑不赢指数，不适合作为核心策略
2. **趋势行情易被套**: 单边下跌中 RSI 长期超卖、KDJ 反复金叉死叉
3. **前复权陷阱**: CCI 基于偏离均值，前复权下数值偏负数千，已去除
4. **滞后离场**: 死叉卖出往往在反弹末期，回吐部分利润

## 关键技术坑 (已修复)
- **前复权导致 CCI/KDJ 失真**: 所有值偏负几千 → 改用滚动百分位数阈值，v2 彻底去除 CCI

## 实施代码位置
- **回测**: `analysis/backtest_strategies.py` → `StrategyKdjRsi`
- **扫描**: `analysis/adaptive_trader.py` → `run_kdj_rsi()`
- **映射**: `data/adaptive_strategy_map.json` → `"kdj_rsi"`

## 关键参数
```python
KDJ_N = 9
KDJ_M1 = 3
KDJ_M2 = 3
RSI_PERIOD = 14
RSI_OVERSOLD = 40
```