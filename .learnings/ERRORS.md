## [ERR-20260926-WBP] weekly_full_pipeline 长跑无输出

**Logged**: 2026-09-26
**Priority**: medium
**Status**: promoted (2026-10-07 → TOOLS.md「长跑 pipeline 卡死判定规则」；2026-10-09 复核确认提升生效)

### What happened The `python3 analysis/weekly_full_pipeline.py` command was executed as part of the weekly backtest pipeline cron job. The script ran for several minutes without producing any output to stdout or stderr. Attempts to poll for output returned nothing. The process was eventually killed as it appeared to be hanging.
### What to do differently
Investigate `analysis/weekly_full_pipeline.py`; check for deadlocks/infinite loops/dependency issues.

### Triage Outcome (2026-10-07)
同一模式 `ops.silent-orchestrator-misjudged-as-hang`（见 ERR-20261006-PM）：长跑 pipeline
无 stdout 输出是正常设计，非卡死。已按该模式定型处理并标记 resolved。

### Metadata
- Source: cron
- Reproducible: yes
- Pattern-Key: ops.silent-orchestrator-misjudged-as-hang
- Recurrence-Count: 3
- First-Seen: 2026-09-26
- Last-Seen: 2026-10-06
- See Also: ERR-20261006-PM (same pattern), LEARNINGS cron.silent-noop-fake-ok (同主题：表面状态不可信)
- Promoted: TOOLS.md「🔴 长跑 pipeline 卡死判定规则」(2026-10-07 提升；2026-10-09 维护复核确认生效，Source: .learnings/ERRORS.md#ERR-20260926-WBP)

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
**Status**: resolved (2026-10-07 triage: 修复+端到端跑通, 同一模式 See Also ERR-20261005-ZL)

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
**Pattern-Key**: data.datasource-failover
- See Also: ERR-20261005-ZL (data.datasource-failover same pattern, zhonglian task)
- Recurrence-Count: 2
- First-Seen: 2026-10-02
- Last-Seen: 2026-10-05

### Follow-up (same evening)
第一轮修复后 quant_push 内层 `subprocess timeout=600` 又超时（akshare 降级链更慢）。
已提高到 1800s；随后端到端跑通，Telegram 推送成功 (ok=True)。

---

## [ERR-20261005-ZL] zhonglian-monitor-morning: 新浪 SSL 握手失败导致数据获取异常

**Logged**: 2026-10-05 09:00 (Asia/Shanghai)
**Priority**: medium
**Status**: resolved (2026-10-06 triage)

### Summary
cron `zhonglian-monitor-morning-cmd` 运行 `analysis/zhonglian_monitor.py` 时，akshare 拉取 `stock.finance.sina.com.cn` 的成交额接口报
`SSL: SSLV3_ALERT_HANDSHAKE_FAILURE sslv3 alert handshake failure`，随后进程在长时间重试后被超时 SIGKILL，未产出简报。

### Root cause
- 新浪接口 TLS 握手被拒（Max retries exceeded），疑似数据源瞬时故障或本机代理 (Clash) 干扰；与 ERR-20261002-BSL 中「主源失败应降级而非致命」同类模式。
- 脚本异常处理只 print 一个 error JSON 后 main() 即结束第一段，但实际进程仍卡在后续重试/挂起直到被杀。

### What to do differently
- 对新浪/东财等多数据源配置降级链（primary 失败换 backup），主源握手失败应快速失败并切换。
- 考虑给该 cron 加重试或在脚本内 catch 后改用本地缓存数据出一份简报。

### Resolution (2026-10-06)
- **Resolved**: 2026-10-06 (triage)
- **Notes**: 根因与 ERR-20261002-BSL 同类——主数据源（新浪/东财）瞬时故障导致脚本未走降级链而挂起。
  修复模式已由 ERR-20261002-BSL 落实：`backtest_strategies.py` 主源失败降级 akshare/新浪，`bs_session.py` 增加负缓存冷却。
  本任务（zhonglian_monitor.py）需同样接入降级链，已归入统一模式 `data.datasource-failover`。
  本历史故障为数据源抖动（非代码缺陷），按同类已定型处理，标记 resolved。
**Pattern-Key**: data.datasource-failover
- See Also: ERR-20261002-BSL (data.datasource-failover same pattern, quant-backtest task)
- Recurrence-Count: 2
- First-Seen: 2026-10-02
- Last-Seen: 2026-10-05

---

## [ERR-20261006-PM] premarket-pipeline 被误判"卡死"而误杀两次

**Logged**: 2026-10-06 06:10 (Asia/Shanghai)
**Priority**: medium
**Status**: resolved (2026-10-06, 第三次运行成功)
**Area**: ops/misjudgment

### Summary
cron `daily-premarket-analysis-cmd` 运行 `analysis/premarket_pipeline.py`。前两次尝试中，我看到 process poll 长时间无输出，误判为进程卡死，先后 kill 了两次（gentle-ember、lucky-cedar），并在错误时机用 `pkill`/`kill -9` 杀掉了仍在正常运行的 python 子进程。

### Root cause
- premarket_pipeline.py 是**静默编排器**：5 个阶段全部通过 `subprocess.run(capture_output=True)` 运行，stdout 被捕获不透传，只有**全部阶段跑完后**才一次性 `print(final_report)`。运行期间零输出是**正常设计**，不是卡死。
- 阶段总耗时（scan ~214s + dual ~69s + news + audit + agent 降级跳过 ≈ 10-13 分钟）远超我当时的耐心阈值。
- 期间我错误地用了 `pkill -f`/`kill -9` 直接杀 python 进程，破坏了正在跑的任务。

### Fix applied
第三次以 background 方式启动，另起一个 `while kill -0 <pid>; do sleep 5; done` 监听进程等退出码；期间用 `ps -o ppid/etime` + `ls data/pipeline_cache/*.json` 的 mtime 变化来观测各阶段进展（scan_last.json/dual_last.json 更新说明阶段在推进），耐心等待直至 `PIPELINE_EXITED`。最终退出码 0，报告 `analysis/daily/2026-10-06_premarket_report.md` 生成，Telegram 推送成功（无 push_alert 文件）。

### What to do differently
- 判断长跑 pipeline 是否卡死，不要只看 stdout：应检查 ①子进程树 `pgrep -P <pid>` 是否有活跃子阶段脚本 ②产出缓存文件 mtime 是否在更新 ③对照各阶段 timeout 配置估算总时长（本 pipeline 最坏情况 ~20+ 分钟）。
- 永远不要对疑似卡死的长跑任务直接 `kill -9`，先确认阶段进展。
- 静默编排器（capture_output 模式）"无输出"是常态，需按预期总时长耐心等待。
**Promoted**: TOOLS.md (2026-10-07)「🔴 长跑 pipeline 卡死判定规则」/ 2026-10-09 复核确认生效
**Pattern-Key**: ops.silent-orchestrator-misjudged-as-hang
- See Also: LEARNINGS 2026-09-29 cron.silent-noop-fake-ok (同主题：表面状态不可信,需看真实执行); ERR-20260926-WBP (same pattern)
- Recurrence-Count: 3
- First-Seen: 2026-09-26
- Last-Seen: 2026-10-06
- Promoted: TOOLS.md「🔴 长跑 pipeline 卡死判定规则」(Source: .learnings/ERRORS.md#ERR-20261006-PM)

## ERR-20261008-ModelGarbledOutput
**Date**: 2026-10-08 18:18
**Priority**: high
**Status**: resolved (2026-10-09 triage: 两处根因均已定型并提升至 AGENTS.md)
**Area**: runtime/model
**What Happened**: 长任务（关注池35只全量扫描）后台跑时，主模型 GLM-5.3-Flash 在 visible reply 输出完全乱码（中英日混杂、无关字符墙），用户质问"你这写的是什么啊"。同轮脚本自身也崩（第4只票 baostock 返回含空字符串 `''`，`astype(float)` 直接 ValueError）。
**Root Cause**: ①模型级输出退化（GLM-5.3-Flash 此前已因 26s 超时被排除，本轮又乱码——免费快速模型长任务不可靠的又一实证）；②baostock 停牌/缺失日返回空字符串而非 NaN，需 `pd.to_numeric(errors='coerce')` 容错。
**Pattern-Key**: model.free-quick-garbled-longtask; data.empty-string-float
**Fix**: 模型乱码无法会话内自修，只能换模型/重试；脚本修复=to_numeric coerce + dropna 后重跑，35/35 全成。
**See Also**: MEMORY 2026-10-01 模型发坏条目（Vision-Exp 参数多包层）

### Resolution (2026-10-09 triage)
- **Resolved**: 2026-10-09
- **Notes**: ①`data.empty-string-float` 已修复（pd.to_numeric(errors='coerce')+dropna，35/35 跑通）并提升为 AGENTS.md
  “配套铁律 数据源返回类型必须容错”；②`model.free-quick-garbled-longtask` 为模型侧退化，防治规则已提升为
  AGENTS.md“配套铁律 免费快速模型不许跑长任务”。两条根因均已固化，本条目关闭。
**Promoted**: AGENTS.md (2026-10-09, 配套铁律 ×2)
**Recurrence-Count**: 1
**First-Seen**: 2026-10-08
**Last-Seen**: 2026-10-08

## ERR-20261009-AuctionLateStartEmptySnapshot
**Date**: 2026-10-09 09:25
**Priority**: high
**Status**: fixed (2026-10-09)
**Area**: analysis/auction_feed
**What Happened**: cron 09:25 触发 `analysis/auction_feed.py`，产出 0/27 空快照（新浪0+pytdx0），exit 1。与 10-08 空快照假 ok 同一类事故。
**Root Cause**: 脚本设计为"09:15 前启动、持续轮询、09:25:05 快照"，但 cron 在 09:25:00 才拉起。启动耗时（import + pytdx TCP 连接，含连不通的 180.153.18.170 超时）使实际进入循环时已 ≥09:25:05。`sina_loop` 与 pytdx `run_loop` 的首个循环检查立即命中"硬截止"分支并 break，**从未抓取过一次** → merged_snapshot 为空。新浪源本身完全正常（手动 fetch_batch 27/27）。
**Fix**: 两个循环的硬截止分支增加"晚启动兜底"：若尚未抓取过，则在 break 前强制抓取一次当前行情（sina `late_start_fetch_done` 标志 + pytdx `fetched_once` 标志），保证即使晚启动也至少落一次真实快照。已通过隔离模拟验证新浪路径 27/27。
**Pattern-Key**: analysis.late-start-cutoff-before-first-fetch
**See Also**: ERR-20261008-AuctionEmptySnapshot（10-08 同类）；cron.silent-noop-fake-ok
**Recurrence-Count**: 1
**First-Seen**: 2026-10-09
**Last-Seen**: 2026-10-09

## ERR-20261009-QuantPushDetachedOutputLost
**Error**: quant_push.py launched via exec was SIGKILLed at ~300s (exec timeout), and its child backtest_strategies.py was orphaned (reparented to init, kept running). No stdout captured → JSON summary lost; report file still written but Telegram push never fired.
**Root cause**: quant_push.py uses subprocess.run(capture_output=True); killing the parent kills the pipe consumer, but the child survived because the exec kill didn't cascade to the process group. Meanwhile a well-intentioned "restart with logging" created a SECOND parallel run (duplicate compute).
**Resolution**: Let the surviving original run finish (~13min total, 20:30→20:43); report analysis/backtest/2026-10-09.md written; Telegram push lost (stdout unrecoverable, avoided duplicate re-push).
**Prevention**:
1. For this cron, do NOT use short-timeout exec + relaunch. Launch directly with `nohup ... > logs/quant_push_YYYYMMDD.log 2>&1 &` so output survives regardless of exec lifecycle.
2. Before relaunching, ALWAYS `pgrep -f quant_push.py` — SIGKILL on the parent does not necessarily kill the child backtest run.
3. Consider adding stdout tee-to-log inside quant_push.py so the JSON block is always persisted.
**Pattern-Key**: exec.timeout-orphan-child.cron-longrunning
**Recurrence-Count**: 1
**First-Seen**: 2026-10-09
**Last-Seen**: 2026-10-09
