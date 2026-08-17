---
pageType: entity
id: entity.zhonglian-heavy
title: 中联重科 (000157)
entityType: stock
canonicalId: "000157.SZ"
aliases:
  - "中联重科"
  - "000157"
sourceIds:
  - source.bridge.workspace-3bf1d827.memory-2d62c497
claims:
  - text: "周期股代表，三年长周期回测最优策略：MACD金叉+RSI<50 (+26.46%，夏普0.545)"
    status: verified
    confidence: 0.95
    evidence:
      - kind: backtest
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/zhonglian_monitor.py"
    updatedAt: "2026-07-24"
  - text: "自适应分配：EMA+OBV -> EMA12/26金叉 (2026-08-01验证后从ema_obv改为ema_cross)"
    status: verified
    confidence: 0.9
    evidence:
      - kind: validation
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/validation_2026-08-01.json"
    updatedAt: "2026-08-01"
  - text: "专项盯盘系统：3个cron(09:00/13:00/15:30)双策略持仓，状态持久化 zhonglian_state.json"
    status: verified
    confidence: 0.95
    evidence:
      - kind: system
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/zhonglian_monitor.py"
    updatedAt: "2026-07-24"
bestUsedFor:
  - "工程机械周期底部左侧布局"
  - "出海+基建双驱动趋势跟踪"
  - "双策略融合(EMA+MACD+RSI)回撤控制"
notEnoughFor:
  - "短线高频交易"
  - "非周期行情强趋势跟踪"
confidence: 0.9
status: active
updatedAt: "2026-08-15"
---

# 中联重科 (000157)

## 概况
- **代码**: 000157.SZ (主板)
- **行业**: 工程机械 (混凝土机械/起重机/桩工龙头)
- **关注池分类**: 高端制造/材料 + 周期股专项

## 核心定位
**典型周期股** — 受基建投资/房地产/出口三重驱动，波动周期明显，需专用策略。

## 专项回测 (3年长周期，8策略对比)
| 策略 | 3年收益 | 夏普 | 最大回撤 | 评价 |
|------|---------|------|----------|------|
| **MACD金叉+RSI<50** ⭐ | **+26.46%** | **0.545** | -22.1% | 单策略最优 |
| **EMA+MACD+RSI综合** | +22.81% | **0.701** | **-12.88%** | 回撤控制最好 |
| EMA12/26金叉 | +18.23% | 0.38 | -28.4% | 一般 |
| 纯MACD | +15.67% | 0.32 | -31.2% | 弱 |
| 布林带+ATR | +8.45% | 0.21 | -25.6% | 弱 |
| KDJ+RSI | +5.12% | 0.15 | -18.9% | 震荡适用 |
| EMA+OBV | +12.34% | 0.28 | -35.1% | 初始分配 |

## 自适应分配历史
- **2026-07-26 初始**: EMA+OBV (弱趋势股归类)
- **2026-08-01 验证**: **EMA12/26 金叉 (ema_cross)** - 跨周期验证显示趋势策略更稳健
- **当前生效**: EMA12/26 金叉

## 专项盯盘系统 (独立于通用扫描)
| Cron | 时间 | 脚本 | 功能 |
|------|------|------|------|
| `zhonglian-monitor-morning` | ~~09:00~~ | 已停用 | 早盘误差4次，已停用 |
| `zhonglian-monitor-midday` | 13:00 | `zhonglian_monitor.py` | 午盘单独盯盘 |
| `zhonglian-monitor-close` | 15:30 | `zhonglian_monitor.py` | 收盘双策略信号+持仓同步 |

**持久化状态**: `data/zhonglian_state.json` (双策略持仓+信号历史)

## 核心优势
1. **混凝土机械全球龙头**: 市占率 40%+，定价权强
2. **出海标杆**: 海外营收占比超 30%，非洲/东南亚/南美深耕
3. **新质生产力**: 智能建造/绿色制造/数字化转型标杆

## 关键风险提示
1. **房地产周期**: 国内销量高度相关房地产投资
2. **原材料成本**: 钢材/液压件成本波动传导
3. **汇率双刃剑**: 出海受益美元强，但成本端也有外币敞口

## 监控要点
- [ ] MACD金叉+RSI<50 买入 / 死叉卖出 (核心专项策略)
- [ ] 综合最优策略 (EMA+MACD+RSI) 卖出信号 → 强制 SELL (030风控)
- [ ] 月度挖掘机/起重机销量数据 (行业先行指标)
- [ ] 海外订单/发货金额指引