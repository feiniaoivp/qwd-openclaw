# bucket==core 永假修复 C 阶段对照（2026-10-20）

- 信号流：dual_signal_eval_days.json，买入 1068 笔，持有 10 日，成本 round-trip 0.0026（plan=position_plan_2026-09-08.json）
- 分桶：classify_bucket → core 24 / satellite 11

| 臂 | 笔数 | 部署 core/sat | pnl5 合计 | winsor 后 | 分年 |
|---|---|---|---|---|---|
| current | 678 | 0/60,000 | 44,839 | 25,102 | 2024:22,218(197) / 2025:21,612(304) / 2026:1,008(177) |
| planA | 678 | 135,000/15,000 | 125,063 | 71,229 | 2024:56,788(197) / 2025:63,462(304) / 2026:4,813(177) |
| planA_poweronly | 678 | 60,000/40,000 | 55,632 | 32,204 | 2024:21,723(197) / 2025:29,820(304) / 2026:4,090(177) |
| all15k | 678 | 0/180,000 | 134,516 | 75,306 | 2024:66,655(197) / 2025:64,835(304) / 2026:3,026(177) |

| 臂 | 成交额 | pnl5/元 | winsor/元 |
|---|---|---|---|
| current | 3,390,000 | 0.0132 | 0.0074 |
| planA | 8,010,000 | 0.0156 | 0.0089 |
| planA_poweronly | 5,020,000 | 0.0111 | 0.0064 |
| all15k | 10,170,000 | 0.0132 | 0.0074 |

- **planA vs current**（配对 bootstrap，Δpnl5/笔）：+118 元 CI[64, 174]，P(Δ>0)=1.0，n=678
- **planA_poweronly vs current**（配对 bootstrap，Δpnl5/笔）：+16 元 CI[-7, 40]，P(Δ>0)=0.912，n=678
- **planA_poweronly vs planA**：-102 元 CI[-153, -54]，P(Δ>0)=0.0
- **all15k vs current**（配对 bootstrap，Δpnl5/笔）：+132 元 CI[73, 199]，P(Δ>0)=1.0，n=678
- **all15k vs planA**：+14 元 CI[-12, 43]，P(Δ>0)=0.835
- **分类价值直接检验**：core 买入 fwd5 1.996%（n=462） vs satellite 0.698%（n=216），差 1.299% CI[0.162, 2.426]
- planA 增量中来自 core 3× 放大部分：80,224 元（462 笔 core 买入）
- 总仓上限 80/20：三臂触界次数 current=0, planA=0, planA_poweronly=0, all15k=0

## A 方案落地判定（C→A，2026-10-20 17:5x）

**绿灯依据**：
- planA 每元效率 0.0156 vs current/all15k 0.0132（+18%）；all15k 绝对 pnl 虽高但与 planA 差异不显著（+14/笔 CI[-12,43] P=0.835），且多用 21% 资金
- 分类价值直接检验：core 买入 fwd5 1.996% vs satellite 0.698%，差 +1.30% CI[0.16, 2.43] 显著
- planA_poweronly（仅电力出海）弱（+16 CI 含 0）→ 价值在 moat 全集分类，非电力子集

**已实施**（A 方案）：
- portfolio_core：pos["tier"]（信号分层）/ pos["bucket"]（仓位分类 core/satellite）分字段；init_position/sync_strategy_map 分字段写入；_apply_tier_filter 改读 tier；8 处 `== "core"` 判据首次真实生效
- state 已迁移（备份 .pre_bucket_split.1792490726，27 桶全空仓，tier 18/7/2 原样，bucket 16 core/11 sat）
- 功能验证：core 买入 ≈14k（15k cap）vs sat ≈4k（5k cap）；tier2 屏蔽照常；三测试 PASS

**局限（如实记录）**：
- 母线=固定 10 日持有投影，非全组合复利模拟；未含止损/卖出时点交互
- classify_bucket 用 2026-09-03 静态 factor_scores 快照套历史信号（look-ahead，不可量化）
- plan 口径：live RiskGuard 返回无 single_stock_max_amount 键 → 默认 100k × mult 1.0；若 030 在某些市态把 max_amount 压到 <15k，两臂收敛
- 今晚 15:40 portfolio-sim-daily 为首个生产验证点
