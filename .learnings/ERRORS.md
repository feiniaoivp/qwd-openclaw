Date: 2026-09-26
Title: Weekly Backtest Pipeline Failed to Produce Output
What happened: The `python3 analysis/weekly_full_pipeline.py` command was executed as part of the weekly backtest pipeline cron job. The script ran for several minutes without producing any output to stdout or stderr. Attempts to poll for output returned nothing. The process was eventually killed as it appeared to be hanging.
What to do differently: Investigate the `analysis/weekly_full_pipeline.py` script to understand why it's not producing output and ensure it runs correctly. Check for potential deadlocks, infinite loops, or issues with its dependencies.

## [ERR-20260927-87V] openclaw_session_sweep

**Logged**: 2026-09-27T12:00:56.623Z
**Priority**: medium
**Status**: resolved (2026-10-01 triage)
**Area**: config

### Summary
Session-end sweep detected 5 possible errors in the previous OpenClaw session.

### Error
```
{"0":"{\"subsystem\":\"bundle-mcp\"}","1":"failed to start server \"filesystem\" (npx -y @modelcontextprotocol/server-filesystem /Users/duguke/.openclaw/workspace /Users/duguke/Projects /Users/duguke/…
{"0":"{\"subsystem\":\"plugins\"}","1":"memory-core: dreaming promotion complete (workspaces=1, candidates=4, applied=4, failed=0).","_meta":{"runtime":"node","runtimeVersion":"26.3.1","hostname":"iMa…
{"0":"{\"subsystem\":\"diagnostic\"}","1":"lane task error: lane=dreaming-narrative:dreaming-narrative-light-3bf1d8270dc7 durationMs=20650 error=\"FailoverError: LLM request timed out.\"","_meta":{"ru…
{"0":"{\"subsystem\":\"diagnostic\"}","1":"lane task error: lane=session:agent:main:dreaming-narrative-light-3bf1d8270dc7 durationMs=20651 error=\"FailoverError: LLM request timed out.\"","_meta":{"ru…
{"0":"{\"subsystem\":\"model-fallback/decision\"}","1":{"event":"model_fallback_decision","tags":["error_handling","model_fallback","candidate_failed"],"runId":"[REDACTED-BLOB]","sessionId":"2375fcd7-…
```

### Context
- Detected by the self-improvement hook on `/new` (OpenClaw has no per-tool-call hook, so errors are swept from the session transcript at session end)
- Session key: agent:main:dashboard:13366f89-5df4-4c3b-89bb-3acce3bd152f
- Session transcript: /Users/duguke/.openclaw/agents/main/sessions/53c98e54-8e8b-48c9-a8f0-ab2de9968a40.jsonl
- Excerpts are truncated and redacted; check the transcript for full context

### Suggested Fix
Triage this entry: if the error was real and non-obvious, keep it and fill in the fix; otherwise mark it resolved or delete it. Before keeping it, grep for its Pattern-Key(s) and fold recurrences into the existing entry (bump Recurrence-Count) instead of duplicating.

### Triage Outcome (2026-10-01)
已分诊关闭：① filesystem MCP 启动失败为**一次性事件**——本会话 filesystem 工具全程正常可用，
`openclaw mcp doctor --probe` 实测 filesystem/tradingwizard 双 ok，无复现；② dreaming-narrative
LLM timeout 为模型侧瞬时故障，非代码问题。无需行动。

### Metadata
- Source: openclaw-error-sweep
- Reproducible: unknown
- Pattern-Key: runtime.error
- Pattern-Key: runtime.failure

---
