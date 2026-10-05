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

## [ERR-20261002-BSL] quant-backtest-daily: baostock 登录失败导致推送中断

**Logged**: 2026-10-02 20:30 (Asia/Shanghai)
**Priority**: high
**Status**: mitigated (2026-10-02)

### Summary
cron `quant-backtest-daily` 运行 `analysis/quant_push.py` 报
`❌ 回测结果 JSON 解析失败: substring not found`。

### Root cause
- baostock 登录在协议握手阶段被重置（TCP 可连通 39.98.161.20:10030，但 `Connection reset by peer`，error_code=10002007），疑似服务端抖动。
- `backtest_strategies.py::main()` 把 `ensure_login()` 失败当作致命错误直接 early-return（exit 0），stdout 里没有 `=====BACKTEST_RESULT=====` 块 → quant_push 解析失败。
- 放大因素：每次失败登录耗时 ~60s 且无负缓存，DataRouter 逐股重试导致整体拖死。
- 当天新浪(hq.sinajs.cn)与 akshare(东财)实测可用，降级链本可工作。

### Fix applied (2026-10-02)
1. `backtest_strategies.py`: baostock 登录失败不再中止，降级 akshare/新浪 继续；`fetch_index_data` 增加 akshare `stock_zh_index_daily` 兜底。
2. `bs_session.py`: 登录失败加 5 分钟负缓存冷却 (`_LOGIN_FAIL_COOLDOWN=300`)，避免每次查询重复 ~60s 失败登录。

### What to do differently
- 回测/数据管道中「主源登录失败」应视为降级信号而非致命错误；只有全链路失败才中止。
- 若明晚仍失败：检查本机 Clash TUN 是否干扰 baostock 长连接（机器上有 ~/.config/clash）。

**Pattern-Key**: data.baostock-login-failure

### Follow-up (same evening)
第一轮修复后 quant_push 内层 `subprocess timeout=600` 又超时（akshare 降级链更慢）。
已提高到 1800s；随后端到端跑通，Telegram 推送成功 (ok=True)。

---

## [ERR-20261005-ZL] zhonglian-monitor-morning: 新浪 SSL 握手失败导致数据获取异常

**Logged**: 2026-10-05 09:00 (Asia/Shanghai)
**Priority**: medium
**Status**: open

### Summary
cron `zhonglian-monitor-morning-cmd` 运行 `analysis/zhonglian_monitor.py` 时，akshare 拉取 `stock.finance.sina.com.cn` 的成交额接口报
`SSL: SSLV3_ALERT_HANDSHAKE_FAILURE sslv3 alert handshake failure`，随后进程在长时间重试后被超时 SIGKILL，未产出简报。

### Root cause
- 新浪接口 TLS 握手被拒（Max retries exceeded），疑似数据源瞬时故障或本机代理 (Clash) 干扰；与 ERR-20261002-BSL 中「主源失败应降级而非致命」同类模式。
- 脚本异常处理只 print 一个 error JSON 后 main() 即结束第一段，但实际进程仍卡在后续重试/挂起直到被杀。

### What to do differently
- 对新浪/东财等多数据源配置降级链（primary 失败换 backup），主源握手失败应快速失败并切换。
- 考虑给该 cron 加重试或在脚本内 catch 后改用本地缓存数据出一份简报。

**Pattern-Key**: data.sina-ssl-handshake-failure
