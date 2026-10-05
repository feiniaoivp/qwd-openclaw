# frank-quant/ai-trading-videos EP007 脚本审阅记录（2026-10-05）

来源: https://github.com/frank-quant/ai-trading-videos/tree/main/EP007_fourteen-llm-quant-benchmark/scripts
背景: 14 个 LLM 写量化策略的横评（加密币/Freqtrade）。原件保留在 `scripts/` 子目录（只读参考，未接入任何流水线）。

## 安全扫描结论
- 无网络调用、无 subprocess/eval、无凭据访问（grep 命中的 key/token 是目录名 `EP004_env` 和 LLM token 计费解析，误报）
- 多数脚本硬编码 Windows 路径 `D:\freqtrade_demo\...`，本机（macOS）不可直接运行

## 已部署（工作区适配版，analysis/ 下）
| 本地脚本 | 原件 | 用途 | 适配点 |
|---|---|---|---|
| dsr_sharpe.py | deflated_sharpe.py | Deflated Sharpe——「best-of-N 里有多少是运气」| 读 validation_*.json 批量出全池体检；年化 252 |
| mc_bootstrap.py | mc_bootstrap.py | 逐笔 block bootstrap——「盈利概率/P5-P95」| 读 portfolio_sim_trades.json；pnl_pct 百分数自动÷100 |
| subperiod_pnl.py | subperiod.py | 逐月/季盈亏拆分 + 最赚期集中度 | 同上格式；策略维度替代多空维度 |
| alpha_beta_attr.py | alpha_beta.py | alpha/beta/R²/基准拖累归因 | 基准改等权关注池(stock_data)或指数CSV；T<30 拒绝 |

## 只记方法论、不部署（思想已吸收）
- **misreport_check.py**: 「自报 vs 复现」必须同口径，两套夏普差 0.4~0.6，混用会全员误判虚报 → 对应我们的「指标必须带来源+口径」红线
- **exclude_forced.py**: 回测末根 K 线机械强平是回测产物不是能力，Qwen 冠军成绩符号取决于它 → 检查我们 portfolio_sim 月末强制平仓同理
- **factor_causality_check.py**: 全量数据 vs 截断数据同点决策对比查未来函数，比 freqtrade lookahead-analysis 可靠 → 思想可用于我们回测防 look-ahead（脚本本体强绑定 freqtrade docker，不搬）
- **seg_metrics_host.py**: 逐日盯市 vs 已实现盈亏归集两套夏普并行不混用

## 不部署（纯视频制作/Freqtrade/Windows 专用）
make_cross_charts.py, score_radar.py, make_dataset.py, cross_data.py, argmax_table.py,
hyperopt_plot.py, usage_cost.py, codex_cost.py

## EP007 报告本身值得记住的结论（对我方回测体系的镜鉴）
1. 14 个模型样本外全负（剔强平后），「跑赢大盘 ≠ 赚钱」
2. DSR/蒙卡/子区间三件套是「回测结果可信度」的标准审计组合——本次已落地
3. 每个模型只考一次、名次差距小于重复跑方差 → 对应我们「小样本单票结论=幸存者偏差」教训
