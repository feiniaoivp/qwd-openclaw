---
pageType: concept
id: concept.ema12-26-crossover
title: EMA12/26 金叉死叉策略 (基准C)
entityType: strategy
canonicalId: "strategy.ema12-26-crossover"
aliases:
  - "EMA12/26金叉"
  - "EMA双线交叉"
  - "基准C策略"
sourceIds:
  - source.bridge.workspace-3bf1d827.memory-2d62c497
claims:
  - text: "2024-01 至 2026-07 25只股票回测平均收益 +58.94%，总分 29.8 冠军"
    status: verified
    confidence: 0.95
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "夏普比率 0.37，最大回撤 -31.4%，胜率 38.6%，盈亏比 2.68"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "适用于 9 只强趋势股：久立/中信建投/汇川/通富/天齐/恒生/应流/雷科/中芯"
    status: verified
    confidence: 0.9
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/backtest/2026-07-26.md"
    updatedAt: "2026-07-26"
  - text: "2026-08-01 跨周期验证：中联重科从 ema_obv 改为 ema_cross；越秀资本从 bollinger 改为 macd(非ema_cross)"
    status: verified
    confidence: 0.85
    evidence:
      - kind: validation
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/validation_2026-08-01.json"
    updatedAt: "2026-08-01"
bestUsedFor:
  - "明确上升趋势、成交量配合的成长股"
  - "强趋势题材股全波段捕捉"
  - "中大盘趋势平滑股"
notEnoughFor:
  - "震荡/弱趋势行情 (福莱特、中联重科等频繁打脸)"
  - "高频交易/超短线"
confidence: 0.9
status: active
updatedAt: "2026-08-15"
---

# EMA12/26 金叉死叉策略 (基准C)

## 策略定义
- **买入**: EMA12 上穿 EMA26 (金叉)
- **卖出**: EMA12 下穿 EMA26 (死叉)
- **止损**: 无固定止损，依赖死叉离场
- **持仓**: 全仓单向，无对冲

## 核心指标 (25只 × 1.5年回测)
| 指标 | 数值 | 排名 |
|------|------|------|
| 平均收益 | **+58.94%** | 🥇 1/5 |
| 夏普比率 | 0.37 | 3/5 |
| 最大回撤 | -31.4% | 3/5 |
| 胜率 | 38.6% | 2/5 |
| 盈亏比 | 2.68 | 3/5 |
| 综合总分 | **29.8** | 🥇 1/5 |

## 适用股票 (9只，自适应分配)
| 股票 | 代码 | 板块 | 回测收益 |
|------|------|------|----------|
| 久立特材 | 002318 | 高端制造 | +234% |
| 中信建投 | 601066 | 券商 | +89% |
| 汇川技术 | 300124 | 自动化 | +167% |
| 通富微电 | 002156 | 半导体 | +195% |
| 天齐锂业 | 002466 | 锂电 | +112% |
| 恒生电子 | 600570 | 金融科技 | +78% |
| 应流股份 | 603308 | 材料 | +201% |
| 雷科防务 | 002413 | 军工 | +143% |
| 中芯国际 | 688981 | 半导体 | +156% |

## 优势特征
1. **趋势跟踪纯粹**: 双指数平滑过滤噪音，捕捉主升浪
2. **强势股碾压**: 国瓷 +513%、应流 +158%、长电 +207% 全吃波段
3. **参数鲁棒**: EMA12/26 为经典参数，跨市场/跨周期稳定
4. **计算轻量**: 仅需两条 EMA，实时监控延迟极低

## 劣势与局限
1. **震荡市打脸**: 横盘/宽幅震荡频繁金叉死叉，手续费吞噬利润
2. **滞后性**: 死叉离场通常回吐 15-25% 利润
3. **弱趋势失效**: 福莱特、中联重科等弱趋势股胜率 < 30%
4. **无风控模块**: 纯信号策略，需外挂 ATR/支撑位止损

## 跨周期验证表现 (2026-08-01)
| 窗口 | 得分 | 夏普 | 笔数 | 权重 | 结论 |
|------|------|------|------|------|------|
| W1 全量 | 62.3 | 0.41 | 28 | 1.0 | 稳健 |
| W2 近3年 | 58.7 | 0.38 | 22 | 1.0 | 稳健 |
| W3 近1.5年 | 45.2 | 0.31 | 14 | 1.0 | 良好 |
| W4 近1年 | 38.9 | 0.26 | 8 | 0.75 | 边际 |

## 实施代码位置
- **回测**: `analysis/backtest_strategies.py` → `StrategyEmaCross`
- **扫描**: `analysis/adaptive_trader.py` → `run_ema_cross()`
- **映射**: `data/adaptive_strategy_map.json` → `"ema_cross"`

## 关键参数
```python
EMA_FAST = 12
EMA_SLOW = 26
SIGNAL_CONFIRM_BARS = 1  # 金叉确认根数
```