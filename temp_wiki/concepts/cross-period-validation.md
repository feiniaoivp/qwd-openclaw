---
pageType: concept
id: concept.cross-period-validation
title: 跨周期多窗口验证体系 (防过拟合)
entityType: methodology
canonicalId: "methodology.cross-period-validation"
aliases:
  - "跨周期验证"
  - "多窗口回测验证"
  - "防过拟合验证"
sourceIds:
  - source.bridge.workspace-3bf1d827.memory-2d62c497
claims:
  - text: "解决单窗口回测过拟合：4窗口(W1全量/W2近3年/W3近1.5年/W4近1年)×5策略"
    status: verified
    confidence: 0.95
    evidence:
      - kind: system
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/validate_strategies.py"
    updatedAt: "2026-08-01"
  - text: "稳定性得分 = 夏普×60 + 收益×0.6 - 样本量罚分 - 回撤罚分"
    status: verified
    confidence: 0.9
    evidence:
      - kind: system
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/validate_strategies.py"
    updatedAt: "2026-08-01"
  - text: "样本量分档权重：≥10笔1.0 / 6-9笔0.75 / 3-5笔0.4 / <3笔0.15 (笔数<6需夏普≥0.4)"
    status: verified
    confidence: 0.9
    evidence:
      - kind: system
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/validate_strategies.py"
    updatedAt: "2026-08-01"
  - text: "置信度护栏：仅当 得分≥25 且 夏普≥0.25 (笔数<6时夏普≥0.4) 才覆盖映射"
    status: verified
    confidence: 0.9
    evidence:
      - kind: system
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "analysis/validate_strategies.py"
    updatedAt: "2026-08-01"
  - text: "自动化接入：周六06:00 weekly-backtest-strategy-refresh 先跑单窗口再跑验证 --write-map"
    status: verified
    confidence: 0.9
    evidence:
      - kind: system
        sourceId: source.bridge.workspace-3bf1d827.memory-2d62c497
        path: "cron jobs"
    updatedAt: "2026-08-01"
bestUsedFor:
  - "策略参数防过拟合验证"
  - "自适应策略分配器自动更新"
  - "低频交易策略保护 (样本量权重机制)"
notEnoughFor:
  - "实时信号生成 (离线批量验证)"
  - "基本面/新闻驱动型策略验证"
confidence: 0.9
status: active
updatedAt: "2026-08-15"
---

# 跨周期多窗口验证体系 (防过拟合)

## 核心问题
单窗口回测极易过拟合：策略在特定时间段表现优异，换窗口即失效。需验证策略在**不同时间尺度**下的稳健性。

## 验证框架设计

### 4 时间窗口
| 窗口 | 覆盖范围 | 用途 |
|------|----------|------|
| **W1 全量** | 2020-01 ~ 今 (6.5年) | 长周期稳健性基准 |
| **W2 近3年** | 2023-01 ~ 今 | 中周期适应性 |
| **W3 近1.5年** | 2024-07 ~ 今 | 近期市场结构适应 |
| **W4 近1年** | 2025-08 ~ 今 | 当前市场微观结构 |

### 5 基准策略 × 4 窗口 = 20 组合/股票

## 稳定性评分模型

```
稳定性得分 = 夏普比率 × 60 + 年化收益 × 0.6 - 样本量罚分 - 最大回撤罚分
```

### 样本量分档权重 (核心创新，2026-08-01 用户建议)
| 交易笔数 | 权重 | 备注 |
|----------|------|------|
| **≥10笔** | **1.0** | 满权，充分统计显著性 |
| **6-9笔** | **0.75** | 轻度惩罚，边际显著 |
| **3-5笔** | **0.4** | 中度惩罚，低显著性 |
| **<3笔** | **0.15** | 重度惩罚，**护栏需夏普≥0.4** |

> **设计初衷**: 保护低频趋势策略 (如布林带仅11笔) 不被误杀，同时惩罚高频噪音策略。

## 置信度护栏 (双重守门)

仅当 **同时满足** 才允许覆盖 `adaptive_strategy_map.json`：
1. **稳定性得分 ≥ 25**
2. **夏普比率 ≥ 0.25** (若样本量 <6笔，则要求 **夏普 ≥ 0.4**)

否则 → **保留原策略**，防止过拟合策略上线。

## 自动化流水线

```
周六 06:00 weekly-backtest-strategy-refresh
    ↓
1. 单窗口回测 (backtest_strategies.py) → analysis/backtest/YYYY-MM-DD.md
    ↓
2. 跨周期验证 (validate_strategies.py --write-map) → analysis/validation_YYYY-MM-DD.json
    ↓
3. 护栏过滤 → 更新 data/adaptive_strategy_map.json
    ↓
4. 收盘扫描 (adaptive_trader.py) 自动读取新映射
```

## 2026-08-01 首轮验证关键变更

| 股票 | 原策略 | 新策略 | 驱动因子 |
|------|--------|--------|----------|
| 久立特材 | ema_cross | **bollinger** | W4窗口趋势策略失效，布林带突破捕捉主升浪 |
| 通富微电 | ema_cross | **ema_obv** | 样本量18笔满权，回撤-20%健康，量价共振更稳 |
| 越秀资本 | bollinger | **macd** | 24笔满权，MACD平滑度优于布林带 |
| 中联重科 | ema_obv | **ema_cross** | 跨周期趋势稳健，弱趋势归类修正 |
| 巨化股份 | ema_obv | **macd** | 权重模型修正 |
| 金力永磁 | ema_cross | **kdj_cci** | 均值回归特征显著 |
| 天齐锂业 | ema_cross | **ema_obv** | 权重模型修正，回撤健康 |

**低置信度保留 (8只)**: 中信建投/中金/恒生/安达维尔/科华/上能/中信特钢/恒立液压

## 代码实现
- **验证脚本**: `analysis/validate_strategies.py`
- **入口**: `python validate_strategies.py --write-map`
- **单股测试**: `python validate_strategies.py --stocks 002180,300847`
- **输出**: `analysis/validation_YYYY-MM-DD.json` (含每只股票完整评分明细)
- **合并机制**: `adaptive_dual.py` 的 `load_latest_validation()` 自动合并所有 validation_*.json (新者优先)

## 关键技术点
1. **新浪日K兜底**: jsonp 接口周末也能拉最近交易日
2. **baostock 延迟**: K线数据滞后1交易日，验证时需对齐
3. **前复权陷阱**: CCI/KDJ 失真已用滚动百分位数修复
4. **pandas_ta 列名**: 动态匹配 `BBU_20_2.0` / `BBU_20_2.0_2.0`