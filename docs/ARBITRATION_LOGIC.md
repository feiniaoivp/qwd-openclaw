# 策略映射仲裁逻辑文档

## 概述

周回测全流水线 (`weekly_full_pipeline.py`) 中，策略映射的最终决定权由 **跨周期验证步骤** (`validate_strategies.py --write-map --arbitrate`) 掌握。该步骤在参数调优 (`param_tune.py`) 之后运行，通过 **置信度仲裁** 逻辑对比两套推荐结果，输出最终生效的 `adaptive_strategy_map.json`。

---

## 两套推荐来源

| 来源 | 脚本 | 核心目标 | 数据窗口 | 输出 |
|------|------|----------|----------|------|
| **验证版** | `validate_strategies.py` | 跨周期稳健性 | W1全量6.5年 / W2近3年 / W3近1.5年 / W4近1年 | `validation_YYYY-MM-DD.json` |
| **调优版** | `param_tune.py --fast` | 近期最优参数组合 | 同 4 窗口，但目标函数为参数级网格搜索 | `adaptive_params.json` (含 `stocks.{symbol}.strategy`) |

---

## 统一置信度护栏

所有候选（验证版、调优版）必须同时满足：

```
score ≥ 25  AND  sharpe ≥ threshold
```

| 交易频次 (avg_trades) | sharpe 阈值 | 说明 |
|------------------------|-------------|------|
| < 6 笔 | 0.40 | 低样本策略需更高夏普，防侥幸 |
| ≥ 6 笔 | 0.25 | 常规门槛 |

> 函数实现：`confidence_guard(score, sharpe, trades)` → `(bool, reason)`

---

## 仲裁决策树

```
输入: 验证推荐 val_s, 调优推荐 tune_s, 当前生效 old_s
      验证指标 (val_score, val_sharpe, val_trades)
      调优指标 (tune_score, tune_sharpe, tune_trades)
      val_ok = confidence_guard(val_*), tune_ok = confidence_guard(tune_*)
```

### 情况 1：双方一致 (val_s == tune_s)
| 条件 | 决策 | 理由 |
|------|------|------|
| val_ok **或** tune_ok | 采纳该策略 | 双重确认，任一过护栏即可 |
| 均未过护栏 | 保留 old_s | 无可信新证据 |

### 情况 2：双方不一致 (val_s != tune_s)

| 优先级 | 条件 | 决策 | 理由 |
|--------|------|------|------|
| 1 | `tune_ok` **且** `tune_score > val_score + 10` **且** `tune_sharpe > val_sharpe + 0.1` **且** `tune_trades ≥ 6` | 采纳 `tune_s` | 调优在**分数、夏普、样本量**三维度**显著**优于验证 |
| 2 | `val_ok` | 采纳 `val_s` | 验证版跨窗口稳健性优先，作为默认信任源 |
| 3 | 其他 | 保留 `old_s` | 无明确优势方，维持现状避免震荡 |

### 情况 3：仅验证有候选 (tune_s 缺失或为空)
| 条件 | 决策 |
|------|------|
| `val_ok` | 采纳 `val_s` |
| 否则 | 保留 `old_s` |

---

## 关键阈值常量

```python
SCORE_THRESHOLD = 25           # 最低稳定性得分
SHARPE_THRESHOLD_NORMAL = 0.25 # 常规夏普门槛
SHARPE_THRESHOLD_LOW_N = 0.40  # 低样本(<6笔)夏普门槛
TUNE_SCORE_MARGIN = 10         # 调优需领先验证 ≥10 分才考虑覆盖
TUNE_SHARPE_MARGIN = 0.1       # 调优夏普需领先 ≥0.1
MIN_TRADES_FOR_TUNE_WIN = 6    # 调优反胜至少需 6 笔样本
```

---

## 变更记录格式

写回 `adaptive_strategy_map.json` 时，控制台输出：

```
📝 股票名(代码): old_s -> final_s (理由详情)
```

仲裁决策单独汇总：
```
⚖️ 仲裁决策 (N 处):
   股票名(代码): 验证=val_s 调优=tune_s -> 最终=final_s (仲裁理由)
```

保留原策略汇总：
```
⏸️ 以下保留原策略:
   股票名(代码): 当前=old_s 验证=val_s(分X 夏普Y) 调优=tune_s -> 保留 (未过护栏原因)
```

---

## 回滚/人工干预指南

| 场景 | 操作 |
|------|------|
| 仲裁结果异常（如大批量策略翻转） | 1. 备份 `adaptive_strategy_map.json` 2. 人工编辑恢复 3. 下次 pipeline 会重新仲裁 |
| 需强制指定某股票策略 | 直接修改 `adaptive_strategy_map.json`，pipeline 会将其视为 `old_s` 参与下轮仲裁 |
| 暂时冻结某股票策略变更 | 在 `validate_strategies.py` 的 `confidence_guard` 中临时提高该股票阈值，或在 `STOCKS` 列表中暂时移除 |

---

## 测试用例设计 (参考 `test_arbitration.py`)

| 测试名 | 输入构造 | 期望输出 |
|--------|----------|----------|
| `test_both_agree_pass` | val_s=tune_s="ema_cross", val_ok=True | final="ema_cross" |
| `test_both_agree_fail` | val_s=tune_s="macd", val_ok=False, tune_ok=False | final=old_s |
| `test_val_ok_tune_fail` | val_s="bollinger"(ok), tune_s="ema_cross"(fail) | final="bollinger" |
| `test_tune_significantly_better` | val_s="ema_cross"(40,0.3), tune_s="macd"(55,0.45,trades=8) | final="macd" |
| `test_tune_marginally_better` | val_s="ema_cross"(44,0.45), tune_s="macd"(48,0.42,trades=10) | final="ema_cross" (验证胜) |
| `test_low_sample_penalty` | val_s="kdj_cci"(30,0.35,trades=4), tune_s="ema_cross"(35,0.38,trades=5) | 均未过护栏 → final=old_s |
| `test_missing_tune` | val_s="bull_trend"(ok), tune_s=None | final="bull_trend" |

---

## 相关文件

| 文件 | 作用 |
|------|------|
| `analysis/validate_strategies.py` | 仲裁核心实现 (`confidence_guard` + 决策树) |
| `analysis/weekly_full_pipeline.py` | 编排：param_tune → validate --arbitrate |
| `analysis/param_tune.py` | 产出调优候选 (`adaptive_params.json`) |
| `data/adaptive_strategy_map.json` | 最终生效映射（每日盯盘读取） |
| `data/adaptive_params.json` | 调优全量结果（含每只股票各策略最优参数） |
| `analysis/validation_YYYY-MM-DD.json` | 验证全量结果（含每只股票各窗口详细指标） |

---

## 版本历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-09-05 | v1.0 | 初版：实现双源仲裁、统一护栏、显著优势判定、决策日志 |
