# 结构性修复工单看板

| ID | 触发日期 | RC签名 | 次数 | 涉及任务 | 状态 | 门禁脚本 | 流水线步骤 | 目标关闭 |
|----|----------|--------|------|----------|------|----------|------------|----------|
| AUDIT-20261114-ops-silent-orchestrator-misjudged-as-hang | 2026-11-14 | ops.silent-orchestrator-misjudged-as-hang | 3 | RiskGuard.refresh<br>analysis/chip_freshness.py<br>analysis/premarket_pipeline.py<br>...+34更多 | OPEN |  |  | 2026-11-21 |
| AUDIT-20261114-exec-timeout-orphan-child-cron-longrunning | 2026-11-14 | exec.timeout-orphan-child.cron-longrunning | 3 | backtest_strategies<br>backtest_strategies.py<br>baostock<br>...+3更多 | OPEN |  |  | 2026-11-21 |
| AUDIT-20261114-err-dist-io-no-timeout | 2026-11-14 | err.dist-io-no-timeout | 4 | RiskGuard.refresh<br>analysis/portfolio_core.py<br>baostock<br>...+3更多 | OPEN |  |  | 2026-11-21 |
| AUDIT-20261114-data-none-valued-key-attr-crash | 2026-11-14 | data.none-valued-key-attr-crash | 3 | auction_signal_push<br>baostock<br>close_scan<br>...+5更多 | OPEN |  |  | 2026-11-21 |
| AUDIT-20261114-llm-final-response-failure-loses-context-only-work | 2026-11-14 | llm.final-response-failure.loses-context-only-work | 3 | baostock<br>next_day_simulator.py<br>scripts/cluster_flag_watch.py<br>...+2更多 | OPEN |  |  | 2026-11-21 |
| AUDIT-20261114-plan-duplicate-detector-creation | 2026-11-14 | plan.duplicate-detector-creation | 3 | data_freshness_guard<br>gate_runner<br>is_trading_day<br>...+3更多 | OPEN |  |  | 2026-11-21 |