# 模拟盘激进模式接线变更日志（2026-11-03）

**授权**: 用户 2026-11-03 19:55 提出需求，20:01 `/approve 落实激进模式`
**范围**: 仅模拟盘（paper trading），无真实资金
**变更前快照**: `analysis/portfolio_core.py.bak-20261103-aggr` / `analysis/position_sizer.py.bak-20261103-aggr`（git tag 待提交后补）

## 一键回滚（三重开关任一）
1. `data/sim_aggressive_config.json` 改 `"enabled": false`
2. 删除该配置文件
3. 运行环境变量 `SIM_AGGRESSIVE=0`（单次运行级，验证用）

## 新增文件
| 文件 | 作用 |
|---|---|
| `analysis/sim_aggressive.py` | 配置加载器（SSOT），env 传递门控参数，自检入口 `python3 analysis/sim_aggressive.py` |
| `data/sim_aggressive_config.json` | 全部激进参数（24 键，含回滚说明与硬保险声明） |

## 修改文件与执法点（13 处）

### analysis/position_sizer.py（030 仓位矩阵）
1. 模块级激进块：POSITION_MATRIX ×1.5（致命风险行保留 0）、SINGLE_STOCK_MAX ×2.5（天花板 30%）、DIRECTION_MAX_RATIO 0.60→0.80、HEALTH_MULTIPLIER 地板 0.2→0.5
2. `_calc_buy_multiplier`：高风险买入乘数 0.3→0.5；健康度乘数地板 0.5
3. `calculate()`：高风险仓位乘数 0.5→0.7

### analysis/portfolio_core.py（模拟盘执行链）
4. 常量区：CLUSTER_EXPO_CAP 0.25→0.45、ATR_STOP_MULT 2.0→3.0（止损更宽不敏感）
5. 新增 `_aggr_single_cap_pct()`：core 15%→30%、satellite 5%→12%
6. `classify_bucket` 3 处返回值改走 `_aggr_single_cap_pct`
7. `execute_trade` 单股上限改走 helper
8. `_check_barbell_constraints` 单股上限 + 哑铃总额 80/20→75/40
9. `_apply_cluster_cap` 预估口径改走 helper
10. `_apply_total_expo_cap` 预估口径改走 helper（总仓上限仍执法，上限值随 030 计划 ×1.5）
11. `_apply_tier_filter`：tier2 买入放行 + `aggressive_allowed` 留痕字段（tier3 禁买保留）
12. `apply_buy_streak_dampening`：连续买入上限 +2 天（streak_max_bonus）
13. 止损冷却 15→5 天（stop_cooldown_days）；init_position 的 atr_params：risk_per_trade 0.01→0.02、max_position_pct 0.15→0.30（仅新开仓生效）

### 三因素门控（经 env，无需改 three_factor_helper 本体）
- `GATE_BUY_LINE`: 55→45（合成测试实证：同信号 50.4 分原被拦，现通过）
- `GATE_MISSING_POLICY`: fail_closed→ignore（缺失维剔除重归一，可审计）
- 注：门控有网络调用（乐咕/baostock），数据源抖动时 ignore 策略保证不硬拦

## 硬保险（不放宽）
- 030 致命风险：仓位归零 + 禁买，原样保留
- tier3 强制清仓 + 禁买，原样保留
- 组合总仓位上限仍执法（TOTAL_LIMIT_ENFORCE=1，上限值随计划 ×1.5 放宽）

## 验证记录
1. AST 语法 ×3 文件 ✅；`grep '***'` 脱敏残留 =0 ✅；行数 +31/+37 只增不减 ✅
2. 冒烟双跑（激进 vs SIM_AGGRESSIVE=0）：总仓 37.5% vs 25.0%、单股 10% vs 4%、tier2 买入放行 vs 屏蔽、ATR 3.0 vs 2.0、簇上限 45% vs 25% ✅
3. 合成交易测试（福耀玻璃 ¥26 MACD金叉）：激进=BUY 1100股(core 30%帽)；标准=被三因素门控拦截 ✅
4. 测试污染清理：portfolio_sim_trades.json 移除合成 BUY 笔（.bak 留档），真实 state 未受影响 ✅

## 已知边界
- 存量持仓的 atr_params 沿用开仓时口径（激进 ATR 参数仅新开仓生效）
- `backtest_atr_position.py`（一次性探索脚本）import PositionSizer 会看到激进矩阵；周流水线回测（backtest_strategies/validate_strategies）不消费 position_sizer，不受影响
- 门控 env 传递时序：sim 链路 sim_aggressive 随 portfolio_core 顶层导入，早于 execute_trade 惰性 import three_factor_helper，安全
