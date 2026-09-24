# Errors

Command failures and integration errors.

---

## [ERR-20260828-PM1] daily_premarket_pipeline_timeout

**Logged**: 2026-08-28T07:00:00+08:00
**Priority**: high
**Status**: resolved (2026-09-20)
**Area**: cron / analysis

### Summary
每日盘前分析 cron (`daily-premarket-analysis`, `timeoutSeconds:1800`) 整条流水线超时被杀，产出不完整报告。分阶段外部数据/LLM 拉取间歇性挂起（与 `run_agent.py` 代码内注释记录的 TLS 握手挂起同源）。

### Error
- 06:00 首跑：整轮 1800s 超时（`lastDurationMs≈1800232`, `lastErrorReason=timeout`, phase=`tool-execution-started`），进程 SIGKILL，未完成 5 阶段。
- 重跑后 2 阶段仍超时：`close_scan_v2.py` 120s 超时（市场全景缺失）；`run_agent.py` 420s 超时（AI 研判缺失）。
- 06:49 单独重跑：scan 恢复成功（约 5 分钟，产出 up23/down6/443亿），`run_agent.py` 在 25/29 处理进度处停 @~8.5min，未能写出 `data/agent_runs/` JSON，判定挂起。

### Fix applied
1. 排查发现 06:00 与重跑两次 pipeline 并行 → 冲突写文件。先 `pkill` 清掉全部实例，再启动唯一 detached `nohup python3 analysis/premarket_report.py`。
2. 用恢复出的 `close_scan_v2.py` JSON 手工回填「市场全景」（up23/down6/total30/443.44亿/ watch17中性9谨慎4）；AI 段标注不可用并给出基于双策略分歧+信号审计的手工研判。
3. 记录本条目。

### Do differently next time
- `premarket_report.py` 从外部接口拉数前应确认无并发实例（单例锁），避免双写冲突。
- `close_scan_v2.py` 内存 120s 对慢接口过紧，建议提升至 ~300s；`run_agent.py` 建议加 `socket.setdefaulttimeout` 兜底 + 单阶段重试。
- 若 cron 整轮 1800s 内跑不完，考虑把流水线改为 detached 后台 + 单独交付，避免整轮 agent turn 阻塞被杀。

### Metadata
- Source: manual diagnosis of daily cron timeout
- Reproducible: yes (每天 06:00 均跑完整条流水线)
- Pattern-Key: cron.timeout
- Pattern-Key: network.hang

---
