# Errors

Command failures and integration errors.

---

## [ERR-20260818-GDG] openclaw_session_sweep

**Logged**: 2026-08-18T21:28:50.923Z
**Priority**: medium
**Status**: pending
**Area**: config

### Summary
Session-end sweep detected 5 possible errors in the previous OpenClaw session.

### Error
```
[assistant turn failed before producing content]
{"0":"{\"subsystem\":\"main-session-restart-recovery\"}","1":"marked interrupted main session failed: agent:main:dashboard:895a42ad-09e0-4e29-8094-d328b2fadeb9 (transcript tail is not resumable)","_me…
{"0":"{\"subsystem\":\"main-session-restart-recovery\"}","1":"main-session restart recovery complete: recovered=0 failed=1 skipped=0","_meta":{"runtime":"node","runtimeVersion":"26.3.1","hostname":"iM…
{"0":"{\"subsystem\":\"bundle-mcp\"}","1":"failed to start server \"tradingwizard\" (https://www.tradingwizard.ai/api/mcp): McpError: MCP error -32001: Request timed out","_meta":{"runtime":"node","ru…
GatewayRequestError: unable to resolve opened file path。   ？
```

### Context
- Detected by the self-improvement hook on `/new` (OpenClaw has no per-tool-call hook, so errors are swept from the session transcript at session end)
- Session key: agent:main:dashboard:895a42ad-09e0-4e29-8094-d328b2fadeb9
- Session transcript: /Users/duguke/.openclaw/agents/main/sessions/c9e576ad-f3bf-4a39-b99d-db90b90e4715.jsonl
- Excerpts are truncated and redacted; check the transcript for full context

### Suggested Fix
Triage this entry: if the error was real and non-obvious, keep it and fill in the fix; otherwise mark it resolved or delete it. Before keeping it, grep for its Pattern-Key(s) and fold recurrences into the existing entry (bump Recurrence-Count) instead of duplicating.

### Metadata
- Source: openclaw-error-sweep
- Reproducible: unknown
- Pattern-Key: runtime.failure
- Pattern-Key: runtime.error

---

## [ERR-20260822-X9A] openclaw_session_sweep

**Logged**: 2026-08-22T02:19:58.964Z
**Priority**: medium
**Status**: pending
**Area**: config

### Summary
Session-end sweep detected 4 possible errors in the previous OpenClaw session.

### Error
```
ls: /Users/duguke/Library/Application Support/OpenClaw/: No such file or directory
## Allowed Exceptions
cron: can't open or create /var/run/cron.pid: Permission denied
[bundle-mcp] failed to start server "tradingwizard" (https://www.tradingwizard.ai/api/mcp): McpError: MCP error -32001: Request timed out
```

### Context
- Detected by the self-improvement hook on `/new` (OpenClaw has no per-tool-call hook, so errors are swept from the session transcript at session end)
- Session key: agent:main:dashboard:331d30a0-d326-40c1-b489-8a58127fd997
- Session transcript: /Users/duguke/.openclaw/agents/main/sessions/7c54baa8-4172-474e-be11-c0a0b03ddd2b.jsonl
- Excerpts are truncated and redacted; check the transcript for full context

### Suggested Fix
Triage this entry: if the error was real and non-obvious, keep it and fill in the fix; otherwise mark it resolved or delete it. Before keeping it, grep for its Pattern-Key(s) and fold recurrences into the existing entry (bump Recurrence-Count) instead of duplicating.

### Metadata
- Source: openclaw-error-sweep
- Reproducible: unknown
- Pattern-Key: fs.no-such-file
- Pattern-Key: runtime.exception
- Pattern-Key: fs.permission-denied
- Pattern-Key: runtime.error

---

## [ERR-20260827-V5J] openclaw_session_sweep

**Logged**: 2026-08-27T20:58:03.095Z
**Priority**: medium
**Status**: pending
**Area**: config

### Summary
Session-end sweep detected 3 possible errors in the previous OpenClaw session.

### Error
```
"lastRunError": "⚠️ 🧰 Process: `crisp-nudibranch` failed",
"lastDeliveryError": "⚠️ 🧰 Process: `crisp-nudibranch` failed",
"lastFailureNotificationDeliveryError": "⚠️ 🧰 Process: `crisp-nudibranch` failed"
```

### Context
- Detected by the self-improvement hook on `/new` (OpenClaw has no per-tool-call hook, so errors are swept from the session transcript at session end)
- Session key: agent:main:main
- Session transcript: /Users/duguke/.openclaw/agents/main/sessions/7d542a51-a627-4209-90bf-f5ed0e17cd15.jsonl
- Excerpts are truncated and redacted; check the transcript for full context

### Suggested Fix
Triage this entry: if the error was real and non-obvious, keep it and fill in the fix; otherwise mark it resolved or delete it. Before keeping it, grep for its Pattern-Key(s) and fold recurrences into the existing entry (bump Recurrence-Count) instead of duplicating.

### Metadata
- Source: openclaw-error-sweep
- Reproducible: unknown
- Pattern-Key: runtime.failure

---

## [ERR-20260828-PM1] daily_premarket_pipeline_timeout

**Logged**: 2026-08-28T07:00:00+08:00
**Priority**: high
**Status**: pending
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
