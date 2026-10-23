> **[2026-10-18 维护 · RC>=3 终态确认]** 本 Pattern-Key 共享计数条目 = ERR-20261006-PM（RC=3 跨 3 任务：weekly 09-26 / premarket 10-06 / 本条；跨 30 天内），本条为早期实证不单独计数。防治规则 TOOLS.md「🔴 长跑 pipeline 卡死判定规则」2026-10-18 复核确认在位，无新复发，终态保持 promoted。

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

> **[2026-10-19 每日维护 · 第2轮]** RC≥3 共享计数条目（跨 3 任务 30 天内）终态复核：TOOLS.md 规则在位（grep 确认「长跑 pipeline 卡死判定规则」章节存在），无新复发，RC 维持 3 不虚增，保持 promoted。
> **[2026-10-20 每日维护]** 第三轮终态确认：TOOLS.md L367 规则仍在位，Last-Seen 维持 2026-10-06，无新复发，promoted 终态不变。
> **[2026-10-22 每日维护]** 第四轮终态确认：TOOLS.md「长跑 pipeline 卡死判定规则」在位，RC 维持 3（跨 3 任务），Last-Seen 维持 2026-10-06，无新复发，promoted 终态不变。
> **[2026-10-23 每日维护]** 第五轮终态确认：RC≥3 且 30 天内（Last-Seen 10-06），grep 复核 TOOLS.md 规则在位；无新复发，RC 不虚增，promoted 终态不变。

> **[2026-10-10 维护去重]** 本条目与 ERR-20261006-PM 同 Pattern-Key `ops.silent-orchestrator-misjudged-as-hang`
> （Recurrence-Count=3 共享计数，First-Seen 2026-09-26）。已合并为一条模式，保留两条作为
> 不同任务的实证记录，不再重复计入。防治规则已落地 TOOLS.md 同名章节。
> 2026-10-18 维护复核：TOOLS.md 规则在位于位（含 pgrep -P / 缓存 mtime / timeout 估算三判据），无新复发，终态保持 promoted。

## [ERR-20260927-87V] openclaw_session_sweep
> **[2026-10-22 维护 · 防误报红线核对]** 本条属历史 `Cron: … failed` 类事件，已按红线跑 `openclaw cron list` 核对：`openclaw_session_sweep` **不在现存 48 个 job 列表中** → 判定为历史误报名，**不转发、不计入失败统计、不新增条目**（2026-10-18 auction-analysis-0926 教训）。

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
- Recurrence-Count: 2（与 ERR-20261005-ZL 共享计数，跨 quant-backtest / zhonglian-monitor 两任务；10-19 维护去重标注，勿逐条把 2 当本条独立复发次数）
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

### Follow-up Closure (2026-10-11 复核)
skill 周期性复核发现遗留待办「zhonglian_monitor.py 接入降级链」一直未落地（3 个活跃 cron
早/午/收盘仍每日跑该脚本，单源无降级）。已闭环：接入多源降级链
（新浪akshare → 东财akshare手→股换算 → baostock，pd.to_numeric coerce 容错），
端到端实测通过（data_date 2026-10-09, price 6.04），commit 500dfda。
**Pattern-Key**: data.datasource-failover
- See Also: ERR-20261002-BSL (data.datasource-failover same pattern, quant-backtest task)
- Recurrence-Count: 2（与 ERR-20261002-BSL 共享计数，同模式跨任务；10-19 维护去重标注）
- First-Seen: 2026-10-02
- Last-Seen: 2026-10-05

> **[2026-10-19 每日维护]** `data.datasource-failover` 两条目为同模式共享计数（RC=2 < 3，跨 2 任务，30 天窗口内），未达 promote 阈值；修复已全部在位（backtest_strategies 降级链 + bs_session 负缓存 + zhonglian_monitor 三源降级 commit 500dfda），维持 resolved + 观察。若第三次复发（任一主源失败且降级链被绕过）则 bump 至 3 并评估提升。
> **[2026-10-21 每日维护]** 第四轮复核：Last-Seen 维持 2026-10-05，无新复发，RC 维持 2；修复三连（降级链/负缓存/三源降级）无绕过证据，终态不变。

---

## [ERR-20261006-PM] premarket-pipeline 被误判"卡死"而误杀两次

**Logged**: 2026-10-06 06:10 (Asia/Shanghai)
**Priority**: medium
**Status**: promoted (2026-10-07 → TOOLS.md「🔴 长跑 pipeline 卡死判定规则」；事故本身 2026-10-06 第三次运行成功已 resolved，2026-10-13 维护统一 Status 为 promoted)
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
**Status**: resolved (2026-10-11 triage: 修复已提交并实测验证；2026-10-13 复核项清零，彻底销账)
**Area**: analysis/auction_feed
**What Happened**: cron 09:25 触发 `analysis/auction_feed.py`，产出 0/27 空快照（新浪0+pytdx0），exit 1。与 10-08 空快照假 ok 同一类事故。
**Root Cause**: 脚本设计为"09:15 前启动、持续轮询、09:25:05 快照"，但 cron 在 09:25:00 才拉起。启动耗时（import + pytdx TCP 连接，含连不通的 180.153.18.170 超时）使实际进入循环时已 ≥09:25:05。`sina_loop` 与 pytdx `run_loop` 的首个循环检查立即命中"硬截止"分支并 break，**从未抓取过一次** → merged_snapshot 为空。新浪源本身完全正常（手动 fetch_batch 27/27）。
**Fix**: 两个循环的硬截止分支增加"晚启动兜底"：若尚未抓取过，则在 break 前强制抓取一次当前行情（sina `late_start_fetch_done` 标志 + pytdx `fetched_once` 标志），保证即使晚启动也至少落一次真实快照。已通过隔离模拟验证新浪路径 27/27。
**Pattern-Key**: analysis.late-start-cutoff-before-first-fetch
**See Also**: cron.silent-noop-fake-ok（空快照=未抓取一次的假 ok）；ERR-20261008-AuctionEmptySnapshot 已并为本条目同一 Pattern-Key（旧ID不单独存在）
**Recurrence-Count**: 2
**First-Seen**: 2026-10-08
**Last-Seen**: 2026-10-09

### Resolution (2026-10-11 triage)
- **Resolved**: 2026-10-11（self-improving 每日维护分诊）
- **Notes**: 兜底修复已提交 commit `344c790`（晚启动强制首抓，sina `late_start_fetch_done` + pytdx `fetched_once`）；
  实测验证：`data/auction/2026-10-09_9_25.json`（17KB 真实快照，非空）+ `decision_2026-10-09.json` 正常产出。
  10-10/10-11 为周末非交易日，无快照属预期，不是复现。Recurrence-Count 修正为 2（10-08、10-09 两次事故已并入本条目）。
  下周一（10-12）盘前可顺手复核一次 09:25 快照非空即可彻底销账。

### Maintenance (2026-10-12 每日维护)
- 复核项移交：10-12（周一）09:25 盘后，后续维护（或任何盘后会话）需确认 `data/auction/2026-10-12_9_25.json` 非空（>1KB 为真实快照）→ 非空则本条彻底销账；空/缺则视为复现，bump Recurrence-Count 至 3 并重查晚启动兜底（cron add 在维护 job 中被 restricted，改为手动跟进项）。
- Pattern-Key `analysis.late-start-cutoff-before-first-fetch` RC=2 < 3，暂不提升，继续观察。

### Maintenance (2026-10-13 每日维护) — 彻底销账 ✅
- 实测复核：`data/auction/2026-10-12_9_25.json` = 17,230 字节，count=27 真实快照（`snapshot_time 09:25:00`，含中联重科等全池行情），`decision_2026-10-12.json` 同步产出 → 晚启动兜底（commit `344c790`）在真实 cron 09:25 拉起场景下验证有效，**无复现**。
- Recurrence-Count 维持 2（10-08/10-09 两次事故），10-12 为验证通过非新事故，不虚增。
- 本条目复核项清零，彻底销账；Pattern-Key RC=2 < 3 继续观察，若再复现则触发 promote 评估。

### Maintenance (2026-10-14 每日维护) — 持续无复现
- 顺延复核：`data/auction/2026-10-13_9_25.json` = 17,249 字节，count=27 真实快照（`snapshot_time 09:25:00`），`decision_2026-10-13.json` 同步产出（10-13 为交易日，is_trading_day=True）→ 连续第 2 个交易日正常，晚启动兜底持续有效。
- Validation 维持 verified；RC 维持 2 不虚增。若后续再复现（RC→3，跨 10-08/10-09 与复现日 ≥2 任务）再触发 promote 评估。

### Maintenance (2026-10-15 每日维护) — 持续无复现
- 顺延复核：`data/auction/2026-10-14_9_25.json` = 17,221 字节，count=27 真实快照（`snapshot_time 09:25:00`），`decision_2026-10-14.json` 09:26 同步产出 → 连续第 3 个交易日正常，晚启动兜底持续有效。
- Validation 维持 verified；RC 维持 2 不虚增。promote 触发条件不变：再复现（RC→3）才评估，否则本条目保持「已销账、持续观察」状态。

### Maintenance (2026-10-16 每日维护) — 持续无复现
- 顺延复核：`data/auction/2026-10-15_9_25.json` = 17,269 字节，count=27 真实快照（`snapshot_time 09:25:00`），`decision_2026-10-15.json` 09:31 同步产出 → 连续第 4 个交易日正常，晚启动兜底持续有效。
- Validation 维持 verified；RC 维持 2 不虚增。RC≥3 三模式（silent-orchestrator / cron.silent-noop / 无新增）复核均保持 promoted；`data.datasource-failover` 两条目为同模式跨任务共享计数（RC=2 <3，已去重标注），继续观察。

### Maintenance (2026-10-17 每日维护) — 持续无复现
- 顺延复核：`data/auction/2026-10-16_9_25.json` = 17,232 字节，count=27 真实快照（`snapshot_time 09:25:00`），`decision_2026-10-16.json` 09:26 同步产出（10-16 为交易日，is_trading_day=True）→ 连续第 5 个交易日正常，晚启动兜底（commit `344c790`）持续有效。
- Validation 维持 verified；RC 维持 2 不虚增。promote 触发条件不变：再复现（RC→3）才评估。
- 全库终态：RC≥3 的 `ops.silent-orchestrator-misjudged-as-hang`（TOOLS.md）与 `cron.silent-noop-fake-ok`（AGENTS.md）均已 promoted，防治规则在各自目标文件中确认在位；本周无新模式达 promote 阈值（RC=2 的 datasource-failover / late-start / gate-path-window-mismatch 均维持观察）。ERRORS.md 无 pending 残留（10-16 sweep 条目已当日分诊 resolved）。

### Maintenance (2026-10-18 每日维护) — 持续无复现（第 6 个交易日）
- 顺延复核：`data/auction/2026-10-16_9_25.json` = 17,232 字节、`snapshot_time 09:25:00`、`count=27`（脚本实测读出），`decision_2026-10-16.json` 09:26 同步产出 → 连续第 5 个交易日正常（10-14/15/16 三份快照均 27/27 全量）。
- 10-17 为周六非交易日，无快照属预期；因此本次可核对的最新交易日仍为 10-16，无新证据。
- Validation 维持 verified；RC 维持 2 不虚增。promote 触发条件不变：再复现（RC→3）才评估。

### Maintenance (2026-10-19 每日维护) — 无新交易日证据
- `data/auction/` 最新仍为 10-16（17,232 B / count=27，ls 实测）；10-17（六）/10-18（日）非交易日，无快照属预期。下一核对点：10-19（周一）09:25 快照非空则连续第 6 交易日。
- Validation 维持 verified；RC 维持 2 不虚增；promote 触发条件不变（再复现 RC→3 才评估）。

### Maintenance (2026-10-21 每日维护) — 持续无复现（连续第 7、8 个交易日）
- 顺延复核：`data/auction/2026-10-19_9_25.json`（17,238 B）与 `2026-10-20_9_25.json`（17,225 B）均 `snapshot_time 09:25:00`、`count=27` 真实快照（python 实测读出）→ 晚启动兜底连续第 7、8 个交易日有效。
- Validation 维持 verified；RC 维持 2 不虚增；promote 触发条件不变（再复现 RC→3 才评估）。每日顺延复核可止于本条目，除非再出现空快照。

## ERR-20261009-QuantPushDetachedOutputLost
**Date**: 2026-10-09
**Priority**: high
**Status**: promoted (2026-10-10 → AGENTS.md「🧠 教训固化原则 · 配套铁律」cron 长任务产出落盘/孤儿进程防护)
**Error**: quant_push.py launched via exec was SIGKILLed at ~300s (exec timeout), and its child backtest_strategies.py was orphaned (reparented to init, kept running). No stdout captured → JSON summary lost; report file still written but Telegram push never fired.
**Root cause**: quant_push.py uses subprocess.run(capture_output=True); killing the parent kills the pipe consumer, but the child survived because the exec kill didn't cascade to the process group. Meanwhile a well-intentioned "restart with logging" created a SECOND parallel run (duplicate compute).
**Resolution**: Let the surviving original run finish (~13min total, 20:30→20:43); report analysis/backtest/2026-10-09.md written; Telegram push lost (stdout unrecoverable, avoided duplicate re-push).
**Prevention**:
1. For this cron, do NOT use short-timeout exec + relaunch. Launch directly with `nohup ... > logs/quant_push_YYYYMMDD.log 2>&1 &` so output survives regardless of exec lifecycle.
2. Before relaunching, ALWAYS `pgrep -f quant_push.py` — SIGKILL on the parent does not necessarily kill the child backtest run.
3. Consider adding stdout tee-to-log inside quant_push.py so the JSON block is always persisted.
**Pattern-Key**: exec.timeout-orphan-child.cron-longrunning
**Recurrence-Count**: 2
**First-Seen**: 2026-10-09
**Last-Seen**: 2026-10-19
**Promoted**: AGENTS.md「🧠 教训固化原则 · 配套铁律」(2026-10-10, Source: .learnings/ERRORS.md#ERR-20261009-QuantPushDetachedOutputLost)

### Follow-up (2026-10-19) — 同模式复发，但落盘防御生效 ✅
- 复发经过：同 cron 仍被以 `exec timeout:300` 短超时启动，wrapper `quant_push.py` 于 300s 被杀（lucky-zephyr failed 5m9s），子进程 `backtest_strategies.py` 再次成孤儿继续跑完（20:30→20:5x，~26min，36 票 x10 策略）。
- 与 10-09 的关键区别：**10-09 落的盘防御奏效** —— quant_push.py 已把子进程 stdout 重定向到 `logs/backtest_run_YYYYMMDD.log`，JSON 摘要完整保留在磁盘；孤儿跑完后从日志提取 JSON 手动 `send_with_retry` 推送成功（message_id 3244），零数据损失。
- 本次未违反规则 #2：cron 二次投递到达时先 `ps aux | grep` 确认无存活回测进程（孤儿已完成）才做恢复处理，**未产生重复实例/重复推送**。
- 仍违反规则 #1：未用 `nohup ... > logs/... 2>&1 &` 直启。下次该 cron 触发必须按 AGENTS.md 铁律 nohup 直启、exec 不带 timeout。
- Recurrence-Count 1 → 2；Last-Seen 2026-10-19。RC=2 < 3，规则已 promoted 维持终态，继续观察；若第三次复发（且落盘防御也失效导致推送丢失）则 bump 3 并复核提升。

### Maintenance
- Claim: 长任务 cron/脚本产出必须落盘日志(`nohup ... > logs/xxx.log 2>&1 &`)，不依赖 exec stdio；重跑前 `pgrep -f` 防孤儿重复实例。
- Scope: 本 workspace 长跑脚本 + cron 任务。
- Authority: quant_push.py `subprocess.run(capture_output=True)` 实测行为。
- Decay: reality / medium
- Revalidate: 新增/改造长跑任务启动方式时。
- Validation: verified（已有防御原则，quant_push.py 已改为 stdout 落盘 logs/）。
- Evidence: 2026-10-09 SIGKILL 父进程后子进程被 init 收养、stdout 丢失、Telegram 推送丢失。
- Disposition: retain/externalize — 已提升为 AGENTS.md 铁律，以后改启动方式即可。
- Guidance: AGENTS.md「🧠 教训固化原则 · 配套铁律」。

## [ERR-20261010-GPWD] weekly_full_pipeline 步骤 0d 固定 today 路径导致门禁误报

**Logged**: 2026-10-10
**Priority**: medium
**Status**: resolved (2026-10-11 triage: 修复已验证并提交 691061b)
**Area**: ops/gate

### What happened
周六 06:00 跑周报流水线时 `verify_market_data.py --report ../analysis/daily/{today}_dual.md` 恒失败（"报告文件不存在"），10-03、10-10 两轮连续误报。

### Root cause
当天 dual 日报尚未生成（06:00 早于盘后扫描），而门禁意图是验证"最新已生成日报"；路径写死 today 属于窗口错配。

### Fix applied
改为 glob 取 `analysis/daily/*_dual.md` 最新一份传入；无 dual 文件时才回退 today 路径（此时报错合理）。10-09 dual 实测通过门禁；修复提交 691061b（此前停留在未提交工作区，triage 时补交）。

### What to do differently
给"最新产物"类门禁传路径前先取实际最新文件（glob 排序取末位），别按运行日期硬拼——运行日 ≠ 产物日。

**Pattern-Key**: ops.gate-path-window-mismatch
- Recurrence-Count: 2
- First-Seen: 2026-10-03
- Last-Seen: 2026-10-10

### Maintenance
- Claim: "验证最新产物"的门禁必须用 glob 取实际最新文件，不得用运行日期硬拼路径。
- Scope: 本 workspace 所有带 --report/--file 参数的门禁脚本（verify_market_data.py 等）。
- Authority: `analysis/weekly_full_pipeline.py` L139-141 修复实现 + 10-09 dual 实测通过。
- Decay: reality / slow
- Revalidate: 新增/改造产物型门禁时检查路径来源。
- Validation: verified (2026-10-11, commit 691061b)。
- Evidence: 10-03/10-10 两轮同因误报 → glob 修复后 10-09 dual 通过门禁。
- Disposition: retain — 已修复提交，模式留存供新增门禁时复用。
- Guidance: 暂无（单模式首次定型，暂不提升）。

### Resolution
- **Resolved**: 2026-10-11T05:40:00+08:00
- **Commit/PR**: 691061b
- **Notes**: glob 取最新 dual + today 兜底；两轮误报根因同一（运行日与产物日错配），Recurrence-Count 记 2。

## [ERR-20261016-C02] openclaw_session_sweep
> **[2026-10-22 维护 · 防误报红线核对]** 同上：`openclaw_session_sweep` job 名不存在于现存 cron 列表 → 历史误报，仅告警不转写。

**Logged**: 2026-10-16T12:33:47.429Z
**Priority**: medium
**Status**: resolved
**Area**: config

### Summary
Session-end sweep detected 1 possible error in the previous OpenClaw session.

### Error
```
Error: Unknown option: --count. Run with --help for usage.
```

### Context
- Detected by the self-improvement hook on `/new` (OpenClaw has no per-tool-call hook, so errors are swept from the session transcript at session end)
- Session key: agent:main:explicit:model-run-79c396a0-c0bb-4353-8d68-eb3269dbf828
- Session transcript: /Users/duguke/.openclaw/agents/main/sessions/86618f62-8707-45be-b68d-7cb501ae8402.jsonl
- Excerpts are truncated and redacted; check the transcript for full context

### Suggested Fix
Triage this entry: if the error was real and non-obvious, keep it and fill in the fix; otherwise mark it resolved or delete it. Before keeping it, grep for its Pattern-Key(s) and fold recurrences into the existing entry (bump Recurrence-Count) instead of duplicating.

### Pattern
跨工具参数混淆：把内置 `web_search`（Brave）的 `--count` 参数当成了 `prismfy-search` CLI 的 flag。
实际命令（transcript 复原）：`~/.prismfy/bin/prismfy-search "A股 10月15日 大跌 原因 上证指数" --engine brave --count 6` → `Unknown option: --count`，该会话 2 次 Prismfy 查询全灭，消息面引用回退本地新闻归档（回退已显式声明，合规）。

### Validation
- 实测 `~/.prismfy/bin/prismfy-search --help`：有效 flag 仅 `--engine/--engines/--time/--domain/--page/--lang/--quota/--raw`，确无 `--count`。

### Disposition
- retain — 真实、非显然、可复用；已固化到 TOOLS.md Prismfy 节 + `~/self-improving/domains/prismfy-search.md`。

### Resolution
- **Resolved**: 2026-10-16T21:19:00+08:00
- **Commit/PR**: N/A（文档/自提升层，无代码变更）
- **Notes**: SOP：条数控制用 `--raw` 出 JSON 自行截取；报 Unknown option 先 `--help` 对照再重试，不要直接放弃搜索（balanced 模式下 Prismfy 是强制首选，直接回退=合规缺口）。

### Metadata
- Source: openclaw-error-sweep
- Reproducible: yes
- Pattern-Key: prismfy.cli-flag-mismatch

---

## ERR-20261017-C01 cron MCP add 忽略 delivery:none 落库成 announce（10-06 教训新变体）

### Summary
- **Date**: 2026-10-17T10:00:00+08:00
- **Component**: cron MCP tool (action=add) — daily-evening-brief-push 新建
- **Error**: 调用 add 时显式传 `delivery={"mode":"none"}`，响应体却返回 `"delivery": {"mode": "announce"}`（无 channel/to）。若不修正，每次运行本体成功后 announce 无目标报错 → job 被误标 error（同 10-06 6/6 全灭事故同根因）。

### Resolution
- **Resolved**: 2026-10-17T10:00:00+08:00
- **Fix**: add 后立即用 update（完整 UUID）重写 `delivery={"mode":"none"}`，response 复核为 none 才算建好。
- **Notes**: 规约升级：**agentTurn/脚本自推型 job 的「add→复核 delivery==none→不符即 update」是三步固定动作**，不能假设 add 参数会原样落库。与 MEMORY.md [2026-10-06]（cron edit --command 隐式改 announce）同族，第二处入口确认。

### Metadata
- Source: manual-session
- Reproducible: yes
- Pattern-Key: cron.mcp-add-delivery-override
- Recurrence-Count: 2（10-06/10-17 两处入口同根因；10-17 当日 add→复核失败为同一次事故的两面，不重复计入）
- First-Seen: 2026-10-06
- Last-Seen: 2026-10-17

### Promotion (2026-10-18 每日维护)
- 触发：同 Key 跨 2 个任务（10-06 edit 入口 / 10-17 add 入口）、30 天窗口内；RC=2 但同一根因连开两处入口，按「最小必要防治」提前提升（与 research.json-int-str-keys 同批），避免第三次入口才补。
- **Status**: promoted → TOOLS.md「🔴 cron job 创建/修改交付口径」（2026-10-18）
- Source: .learnings/ERRORS.md#ERR-20261017-C01

---

## ERR-20261017-C02 json int 键序列化后恒为 str——同日两连坑

### Summary
- **Date**: 2026-10-17T16:0x
- **Component**: rsi30_event_study / dual_signal_event_study 两研究脚本
- **Error**: dict 用 int 键 (HORIZONS=(1,5,10)) 建统计表，json.dump 后键变 "1"/"5"；读取/打印处混用 int 与 str 键 → 统计全 None（rsi 研究）与 KeyError 崩溃（dual 研究，且崩在写盘前的 excess 循环导致 5 分钟数据白跑）。

### Resolution
- **Resolved**: 2026-10-17T16:0x
- **Notes**: 规约：①研究脚本统计表键一律 str(h) 从建表起统一；②大计算主结果先落盘再打印摘要（dual 研究已改：json.dump 前移）。同类坑当日两发，int/str 键不匹配是 pandas/json 研究脚本高频事故。

### Metadata
- Source: manual-session
- Reproducible: yes
- Pattern-Key: research.json-int-str-keys
- Recurrence-Count: 2（同日 rsi30_event_study 统计全 None + dual_signal_event_study KeyError 崩溃，两处独立脚本）
- First-Seen: 2026-10-17
- Last-Seen: 2026-10-17

### Promotion (2026-10-18 每日维护)
- 触发：同 Key 同日两发、30 天窗口内（RC=2，本轮按「同 Key 合并计数」批次纳入；未达 3 但已非一次性，按最小必要规则提升）。
- **Status**: promoted → AGENTS.md「配套铁律 · 研究脚本 JSON 键统一 str + 主结果先落盘」（2026-10-18）
- Source: .learnings/ERRORS.md#ERR-20261017-C02

## ERR-20261018-C01a RiskGuard.refresh 裸网络无超时 → 回测进程永久 sock_read 挂死（第3次同类）
> ⚠️ **ID 消歧（2026-10-22 维护）**：本条目原 ID `ERR-20261018-C01` 与同日 `ERR-20261018-C01（刷新脚本标志位反转）` 撞号。
> 本条目为**网络超时类**，现加后缀 `a` 号；旧引用 `#ERR-20261018-C01` 在 AGENTS.md 指代标志位反转条（C01b），歧义已解除。历史正文不改。
- 现象: cluster_window_ab 两次于逐日循环中 CPU~0%、无产出；/usr/bin/sample 坐实 SSL sock_read。
- 根因: FatalRiskDetector 的 akshare 涨停池/龙虎榜调用无 timeout；net_guard 虽被 import 但进程级默认值未在该链路安装前就已建连/或依赖库自带无限等待。
- 修复: portfolio_core.RiskGuard.refresh() 刷新体包 network_timeout_guard(15)，超时降级旧缓存+可见告警（commit fc625ed）。
-  lesson: 入口脚本装 net_guard ≠ 全链路免疫；**任何被回测/循环调用的风控刷新必须自带超时**。

### Metadata
- Source: cron/manual-session（cluster_window_ab 回测任务）
- Reproducible: yes
- Pattern-Key: err.dist-io-no-timeout（2026-10-19 维护定 key；此前无 key 导致跨任务复发不可计数）
- Recurrence-Count: 4
- First-Seen: 2026-09-17
- Last-Seen: 2026-10-18
- Status: promoted (2026-10-19 → TOOLS.md「🔴 网络调用超时铁律」)
- Promoted: TOOLS.md「🔴 网络调用超时铁律」(2026-10-19, Source: .learnings/ERRORS.md#err.dist-io-no-timeout)
- Maintenance (2026-10-23 每日维护): RC=4 ≥3、Last-Seen 10-18 在 30 天窗口内；grep 复核 TOOLS.md「网络调用超时铁律」在位，无新复发，promoted 终态不变。
- See Also: memory/dreaming/daily-report-2026-09-17.md（sock_recv 挂起 → net_guard 立项）；
  memory/dreaming/daily-report-2026-09-30.md（baostock 常驻会话先于护栏建连，遗留未治）；
  ERR-20261002-BSL（baostock 登录 ~60s 无负缓存，同类无超时放大因素）

### Promotion (2026-10-19 每日维护；2026-10-20 维护复核 TOOLS.md L446 铁律在位，Last-Seen 维持 10-18，终态不变)
- 触发：同根因跨 ≥4 个任务（09-17 net_guard 立项 / 09-29 baostock 常驻会话 / 10-02 回测登录 / 10-18 RiskGuard），30 天窗口内，RC=4 ≥ 3，且此前无防治规则落在 TOOLS.md/AGENTS.md（护栏只有代码，没有行为规则）。
- 验证：`analysis/portfolio_core.py:668-672` 修复在位；`grep -rl net_guard analysis scripts` = 80 文件已接线，但 09-30 遗留项（bs_session 旧连接先于护栏）仍未闭环 → 规则强调「函数体内自带超时」而非仅入口接线。
- 写入：TOOLS.md「🔴 网络调用超时铁律」三条判据（函数体内护栏 / 超时必降级可见 / sample 诊断）。
- 复核（10-19 第 2 轮维护）：TOOLS.md「🔴 网络调用超时铁律」在位（grep 确认章节存在），RC=4 跨 4 任务共享计数为终态，无新复发，保持 promoted。

## ERR-20261018-C02 三臂 A/B 结果逐字节相同 = 门禁不在执行路径的信号
- cluster cap 三臂（prod/roll/off）回测权益/交易完全相同 → run_backtest 主循环不含簇拦截（只在 run_portfolio_scan）。
- lesson: 做门禁 A/B 前先 grep 门禁代码是否真的在被打分的执行路径里（`grep -c cluster portfolio_executor.py` 一条命令即可提前发现，浪费了 ~15 分钟回测）。

### Metadata (2026-10-19 维护补全)
- Source: manual-session（cluster_window_ab 三臂 A/B）
- Reproducible: yes
- Pattern-Key: research.gate-not-in-exec-path
- Recurrence-Count: 2
- First-Seen: 2026-10-18
- Last-Seen: 2026-10-18
- Status: resolved（2026-10-18 当日定位 actuator 路径，非代码缺陷）
- Disposition: retain — 可复用排查启发式，但 RC=2 < 3，暂不提升；若再遇到一次 A/B 空结果则复核提升。

## ERR-20261018-C01b: 刷新脚本标志位反转导致 41 个基准池 CSV 被截断为 1 行（已自愈）

> ⚠️ **ID 冲突（2026-10-19 维护）**：本条目与 ERR-20261018-C01（RiskGuard 挂死）同日同号。历史保留不改名，
> 引用请用 Pattern-Key `data.rewrite-script-flag-inversion`；后续同一事件一律挂此 key。
- **经过**: 写 scripts/refresh_stock_data.py 时，todo 元组第 5 位语义是「是否标准格式(std)」，循环里却当成「是否重建(rebuild)」用 → 标准文件走了全量重写分支，且 start=last 只拉到 1 行 → 41 个 CSV 被截成单行。
- **发现**: 写后自检只跑了 compile/grep '***'，没做语义走查；首跑输出「✅ 41」当时未核对行数，直到第二批 11 个失败才回查。
- **修复**: ①标志位改正；②桩文件自愈规则（<30 行全量重建）；③重写前 .bak_refresh 备份 + 行数防倒退检查（新行数 < 旧行数且旧≥30 → 拒绝写盘）。全部 41 文件已从 baostock 重建（2025-06-30 起，净效果：陈旧 10-08 → 新鲜 10-16）。
- **教训**: ①「重写型」数据脚本必须有写前备份+行数防倒退检查，这是第三个必配防线（前两个: compile 检查、静默 except）；②首跑的「成功」必须抽样核对产物形态（行数/日期范围），不能只看退出码——本轮 41 个 ✅ 全是假成功；③元组多字段传参时用命名元组/dataclass，位置语义最易反转。

### Metadata (2026-10-19 维护补全)
- Source: simplify-and-harden（数据债治理任务）
- Reproducible: yes
- Pattern-Key: data.rewrite-script-flag-inversion
- Recurrence-Count: 1
- First-Seen: 2026-10-18
- Last-Seen: 2026-10-18
- Status: resolved（2026-10-18 自愈：标志位改正 + 桩自愈 + .bak_refresh + 行数防倒退，commit 24e7230）
- Validation: verified — 2026-10-19 实测 `/Users/duguke/stock_data/*.csv` 53 个文件、**0 个 < 30 行**，最新 mtime 10-18 22:12（900925 为已知跳过项）。
- Promoted: AGENTS.md「配套铁律 · 全量/快速双模式 quick 写独立文件 + 重写型脚本备份/行数防倒退」(2026-10-19)
- 附注：教训①③属单次事件、样本 1，未单独提升；提升的是「首跑 ✅ 必须抽样核对产物形态」这一可复用门禁习惯。

## ERR-20261018-C03 三因素门控「缺数据→中性 50」把 fail-open 做成「缺数据必放行」，且 `except: pass` 再兜一层
- **现象**：`three_factor_helper.ResonanceGate.check_buy` 在情绪/基本面取数失败时按 50 计；`W=(0.45,0.30,0.25)`、`BUY_LINE=60` → 技术=90（调用方强买信号）时 `90*.45+50*.30+50*.25=68` 已过线。情绪低到 20 分也仍 60.5 放行。更糟：`portfolio_core.execute_trade` 里 `try: ResonanceGate().check_buy(...) except Exception: pass` —— 门控自身抛错时**直接放行**，形成第二层 fail-open。
- **根因**：把「数据缺失」映射成「中性分」参与加权，等价于给缺失项发及格分；阈值 60 又低于「三分之二中性」的组合分。**缺失 ≠ 中性**，是 `picks` 隐性 5.0 填充同款病（10-08 已立契约修复，门控这一路漏网）。
- **规约**：①多因子门控里缺失维必须以 `None` 传播，由判定层显式选择 fail-open/fail-closed 并在 reason 留痕，禁止在取数层塞默认分；②门控调用处禁止 `except: pass`，异常必须降级为「不确定 → 记录 + 按配置策略」③改门控阈值前先做「缺数据分布」实测，否则永远测不出拦截率。
- **状态**：已量化（矩阵实测见 `<会话日志>`），修复方案待用户拍板（情绪维度可能已退化，见同日观察）。

### Metadata (2026-10-19 维护补全)
- Source: manual-session（三因素门控审计）
- Reproducible: yes
- Pattern-Key: gate.fail-open-missing-as-neutral
- Recurrence-Count: 1
- First-Seen: 2026-10-18
- Last-Seen: 2026-10-18
- Status: promoted (2026-10-19 → AGENTS.md「配套铁律 · 缺失维 None 传播 / 门控禁 except:pass」)
- Validation: verified — `analysis/three_factor_helper.py:50/69-72` 已改为 `score=None` 传播 + `GATE_MISSING_POLICY`（默认 fail_closed）；`analysis/portfolio_core.py:882-885` 已去 `except: pass`，改 `GATE_ERROR_POLICY`（默认 blocked），commit f470472。
- 注：RC=1 但属「安全级 fail-open 缺陷」，按最小必要防治即时提升（与 10-18 安全类条目同口径）；
  同日观察到的「情绪维度可能退化」为独立待验证假设，不并入本规则。

## ERR-20261018-C04 多臂对照各配"自然基准" → 基准口径差冒充实验效应，结论自动反转
**日期**: 2026-10-18
**场景**: gap 成本重放（理想臂 close(T) 买入 vs 现实臂 open(T+1) 买入）。
**错误做法**: 给每条臂配它"最自然"的基准 —— A 臂用 `close(T)→close(T+6)` 全池等权，
B 臂用 `close(T)→open(T+1)` 全池等权。两条基准**持有期完全不同**（6 天 vs 隔夜），
基准均值本身差 **0.918pp**。
**症状（关键）**: 输出「执行成本 −0.22%（实盘吃亏）」但「现实臂超额 +1.27% > 理想臂 +0.58%」——
两个结论方向相反，却各自都带显著 CI，**看起来完全合理**，极易被当结论采信。
**定位**: 单独统计两条基准的均值 → 差 0.918pp 恰好等于两臂超额差，锁定是基准口径而非实验效应。
**修复**:
1. 两臂共用**同一基准**（持有区间与两臂完全一致：`open(T+1)→close(T+1+HOLD)`）；
2. 加**恒等自检** `max|(exB−exA)−cost| ≈ 0`，超 0.005pp 直接 exit 3 拒绝出结论。
**通用规则**: 多臂/多策略对照，**所有臂必须减同一个基准**。臂间差只能来自被控变量，
不能来自基准定义。对照实验必须写一个恒等式自检，用数学关系锁死口径，而不是靠人工核对。

### Metadata (2026-10-19 维护补全)
- Source: manual-session（gap 成本重放）
- Reproducible: yes
- Pattern-Key: research.benchmark-basis-mismatch
- Recurrence-Count: 1
- First-Seen: 2026-10-18
- Last-Seen: 2026-10-18
- Status: promoted (2026-10-19 → AGENTS.md「配套铁律 · 多臂共用同一基准 + 恒等自检」)
- Validation: verified — `scripts/gap_cost_replay.py:156/241` 同基准实现 + 恒等式自检（不过 exit 3）在位；报告 `analysis/research/2026-10-18_gap_cost_replay.md:28/84`。
- 注：RC=1，属「结论方向会自动反转」的高危实验口径缺陷，按最小必要即时提升。

## ERR-20261018-C05 缓存文件"无条件命中 + 无刷新方"= 派生指标会带着新价格用旧状态静默失效

**现象**: chip 第 5 维复活后接进生产，三个读写点全是 `if os.path.exists(): return`
（`fetch_chip_input` / `main()` / `load_chip_metrics` 只取 `iloc[-1]`），且 cron 里
没有任何 chip 刷新任务。当时数据是新的（刚补过），所以实测"一切正常"。

**为什么危险**: `cost_bias = close(T) / avg_cost(T0) - 1` 是一个**跨时间相减**的量。
缓存冻结后分子用今天的价、分母用冻结日的成本：
- 上行行情 → cost_bias 被系统性推高 → `−0.20` 深度折价档**永不触发** → 加分项失效
- 下行行情 → 批量误判"深度折价" → 加分项**错误触发**

两种都是**静默**的：不报错、分数看起来合理、维度覆盖率仍是 36/36。
比"数据缺失"危险得多——缺失会被 `valid_dims` 暴露，静默错误不会。

**修复**:
1. 新增 `analysis/chip_freshness.py` 纯逻辑判定（滞后按**交易日**，基准取价格缓存
   最新日而非 `date.today()`，否则节假日会误判陈旧）
2. 滞后 > CRITICAL(10) 交易日 → 该维返回 `None` + 可见告警（复用「缺失不填充」纪律）
3. `refresh_metrics(--refresh)` 增量刷新接周流水线 0s，契约门禁接 0t

**截断测试揭出的第二层缺陷**: 首版同步判据是「chip 最后日 >= 价格最后日」。
把两者**一起**截到 10-09 时该条件恒真 → 36/36 全 skip，永远不追。
**只有构造"两边同时落后"这种对抗性场景才会暴露**——正常路径下它是绿的。
修正：同步判据必须同时对齐「全市场行情基准日」（跨票最新日期）。

**通用规则**:
1. **凡是"派生指标缓存 + 无条件命中"的组合，必须同时回答两个问题**：谁负责刷新？
   怎么判定过期？缺任一个 = 定时炸弹。接线当天数据是新的不算验证。
2. **陈旧度基准要用"同体系最后成功拉到的行情日"，不能用 `date.today()`**。
3. **同步判据不能只做两两比较**（A>=B），必须引入**绝对参照**（A>=市场基准），
   否则两者一起退化时恒真。
4. 契约测试要构造「两边同时落后」这类**正常路径永远走不到**的场景。

**产物**: `analysis/chip_freshness.py`、`scripts/chip_freshness_test.py`（17 项）、
周流水线 0s/0t、commit f940370

### Maintenance (2026-10-19 每日维护)
- 复核：防治规则 TOOLS.md「🔴 派生指标缓存铁律」四条在位；产物 `analysis/chip_freshness.py`、`scripts/chip_freshness_test.py` 均在位（10-18 更新）。
- Pattern-Key 本文件仅此一处，无跨文件重复；RC 维持 2 不虚增（10-19 无新复发）。
- Validation 维持 verified；终态保持 promoted。

### Metadata (2026-10-20 每日维护补全)
- Source: manual-session（chip 第 5 维接线复核）
- Reproducible: yes
- Pattern-Key: data.derived-cache-unconditional-hit（2026-10-20 维护定 key，消除无 key 盲区）
- Recurrence-Count: 2（10-18 发现 + 同日截断测试揭出第二层同步判据缺陷，两处独立读写点实证；10-19/10-20 无新复发不虚增）
- First-Seen: 2026-10-18
- Last-Seen: 2026-10-18
- Status: promoted (2026-10-19 → TOOLS.md「🔴 派生指标缓存铁律」；2026-10-20 维护 grep 确认规则四条 + chip_freshness 三产物在位)

## ERR-20261018-C06 门控阈值标定用「代理分」= 把变量的维度当常量、常量的维度当变量

**现象**: GATE_BUY_LINE=55 的标定（10-18 f470472）用 `tech×0.45 + 50×0.55` 代理分，
理由是"情绪/基本面历史实值不可回放"。但读生产源码后发现方向性错误：
  * 生产三处 `check_buy(sym)` 都不传 `tech_score` → tech 硬编码 90（**常量**）
  * fund/sent 才是**变量**（0~100），代理分却用 50 顶替了它们
→ 旧研究测的"区分度"来自技术三档（60/75/90），而真实门控的驱动因子（fund+sent）
被压成常量，**测错了维度**。

**修复**: 真实口径重建 `buy_score = 90×0.45 + fund×0.30 + sent×0.25`：
  * 基本面历史 data/research_fund_history.json（36只×504报告期，防前视 pub<=T）
  * 情绪 = 关注池自算上涨占比（非全市场，有局限）
结论: 唯一显著分界在 50（<50 档 −3.43% 显著负超额），55 无统计支撑（只拦 3%，
且 50~55 档 +0.75% 被误杀）。

**通用规则**:
1. **标定任何阈值前，必须先读生产源码确认每个因子的真实身份（变量 vs 常量）**，
   不能凭"接口名/文档印象"假设。代理分/近似分的**方向**错了，结论必然反。
2. 代理分要显式回答：哪些维度被压成常量、哪些被当成变量——**常量维度会掩盖真实区分度**。
3. 阈值标定要做**分年稳健性**（本次发现 2024 主导、2025/2026 反转为正）+
   **随机基准对照**（未超自然命中率）才能判断"有区分度"是否真实。

**产物**: scripts/gate_threshold_study_v2.py / scripts/fund_history_builder.py /
data/research_fund_history.json / analysis/research/2026-10-18_gate_threshold_study_v2.{md,json}

### Metadata (2026-10-19 维护补全)
- Source: manual-session（门控阈值标定 v2）
- Reproducible: yes
- Pattern-Key: research.proxy-score-dimensional-swap
- Recurrence-Count: 1
- First-Seen: 2026-10-18
- Last-Seen: 2026-10-18
- Status: promoted (2026-10-19 → AGENTS.md「配套铁律 · 标定前确认因子是变量还是常量」)
- Validation: verified — 真实口径重建脚本 `scripts/gate_threshold_study_v2.py` 与 `scripts/fund_history_builder.py` 在位；结论「55 线无统计支撑、唯一显著分界 50」已入库（commit 9db0cb8/21b3846）。
- 注：RC=1，与 ERR-20261018-C03 同属「门控因子语义」类，合并为一条贯穿规则写入 AGENTS.md（一条规则覆盖两处根因，避免重复纠偏）。

## ERR-20261018-C07 --quick 冒烟测试覆盖全量数据产物（相同库内数据债）

**现象**: `fund_history_builder.py --quick`（开发验算，只建 5 只）和全量（36 只）写同一个
`data/research_fund_history.json`。冒烟测试跑 --quick 时**覆盖了刚跑完的 36 只全量产物**，
导致提交到 git 的是 5 只的残缺版（+3100 行差异，下一 commit 才发现补回）。

**为什么危险**: 这种"quick 覆盖全量"是**静默**的——文件存在、JSON 合法、键名正确，
只是内容从 36 只缩到 5 只。下游 `gate_threshold_study_v2` 若此刻跑，31 只票 fund=None，
会被「缺失不填充」纪律静默降级成 None 传播，标定结论偏移且无任何报错。

**修复**: quick 模式改写独立文件 `*.quick.json`（`fund_history_builder` / `market_breadth_builder`
两个都改了），全量产物永不被 quick 覆盖。

**通用规则**: 任何「全量/快速」双模式的构建脚本，quick 必须写**不同文件名**，不能靠
"用完就删"或"记得别跑 quick"这种约定——约定必然被违反（本次就是冒烟测试忘了）。

**产物**: scripts/fund_history_builder.py / scripts/market_breadth_builder.py 的 QUICK 分支写独立文件

### Metadata (2026-10-19 维护补全)
- Source: manual-session（fund/tier 数据构建）
- Reproducible: yes
- Pattern-Key: data.quick-mode-overwrites-full
- Recurrence-Count: 1
- First-Seen: 2026-10-18
- Last-Seen: 2026-10-18
- Status: promoted (2026-10-19 → AGENTS.md「配套铁律 · quick 写独立文件名」)
- Validation: verified — `scripts/fund_history_builder.py:50` / `scripts/market_breadth_builder.py:48` 均写 `*.quick.json`。
- 注：RC=1，与同日 CSV 截断事故（`data.rewrite-script-flag-inversion`）同属「重写型数据脚本静默破坏产物」家族，合并为一条铁律提升。

## ERR-20261019-C01 pre_response_guard 的 PE/PB 子串误报（技术报告被拦成市场数据违规）

**现象**: 04:1x 用 `scripts/pre_response_guard.py --stdin` 自检 dreaming 日报，报 **16 条违规**，
逐条看全是 `pe 数值:` 形式的空关键词命中（上下文是 `update` / `spec` / `type_percent` / `patch`），
真实市场数据引用 **0 条**；同一文本里唯一真实的一条（国联先例「量比 0.52」）确实该拦且已补来源。
修完 PE 误报后，该报告才正确 exit 0。

**根因**: `MARKET_KEYWORDS` 里的 `r'PE'` / `r'PB'` 用 `re.IGNORECASE` + 无词边界子串匹配
→ `up**pe**r` / `s**pe**c` / `ty**pe**` 全部命中，且 `NUMBER_PATTERN` 会把 80 字符内的任意数字抓来配对。
2026-10-07 修过「单位组捕获」同类 bug，但没意识到短 ASCII 缩写本身会撞普通英文词。

**修复** (scripts/pre_response_guard.py): PE/PB 从 `MARKET_KEYWORDS` 移出，单独用 `r'\bPE\b'` / `r'\bPB\b'`
+ **区分大小写**（flags=0）匹配；中文关键词保持 IGNORECASE。

**通用规则**: 往「关键词正则列表」里加**短 ASCII 缩写**（PE/PB/AI/ROE 等）必须同时加词边界 + 区分大小写，
否则在中文技术文档里会被普通英文单词大量误命中；**门禁误报比漏报更危险——它会训练人绕过门禁**。

**验证**: 8 例自测全过（有来源放行 / `spec_update` 放行 / 裸成交额拦 / 裸 PE 拦 / 带来源 PE 放行 / 裸量比拦 / 无市场词放行）。

### Metadata (2026-10-19 维护补全)
- Source: manual-session（dreaming 日报自检）
- Reproducible: yes
- Pattern-Key: guard.short-ascii-substring-false-positive
- Recurrence-Count: 1
- First-Seen: 2026-10-19
- Last-Seen: 2026-10-19
- Status: resolved（2026-10-19 当日修复 + 8 例自测）
- Disposition: retain — 可复用提示（门禁误报比漏报更危险），RC=1 < 3，暂不提升；若再出现「门禁刷屏式误报」则复核。

## ERR-20261020-BP brief-push 消息 782 字符超 700 上限（发送前未做长度校验）
- **现象**（2026-10-20 22:40 premarket-news-format 推送，成功 message_id=3301）：全消息 782 字符 > 700 上限，发送前未做字符数校验，靠超限硬发。
- **规约**：推送前必须 `len(text) <= 700` 校验再 send；超限按优先级压缩（先删对垒第 3 只票，再裁背景段），禁止裸发超限消息。

### Metadata (2026-10-21 每日维护登记)
- Source: cron 运行备注（ERRORS.md 末尾游离记录，本次维护登记为正式条目）
- Reproducible: yes
- Pattern-Key: err.push-length-no-precheck
- Recurrence-Count: 1
- First-Seen: 2026-10-20
- Last-Seen: 2026-10-20
- Status: resolved（当日推送实际成功送达，属规范缺口非故障；RC=1 < 3 暂不提升，若再超限则复核）

## ERR-20261021-PremarketAgentTurnNoop
**Date**: 2026-10-21
**Priority**: high
**现象**: `daily-premarket-analysis-cmd`（agentTurn，model=nvidia/nemotron-3-ultra）10-21 06:00 run 记 **ok（23.2s）**，但当日 `2026-10-21_premarket_report.md` 与 `run_20261021_*.json` 双缺。transcript（5cae8518）坐实：模型 read 完 `premarket_pipeline.py` 后回复「需要我做什么？」即结束，**从未执行命令**。**【10-21 21:4x 纠错（证据重核）】** 初稿曾称「10-17（周五交易日）同样双缺」——错误：10-17 为**周六非交易日**（job schedule `0 6 * * 1-5` 周末不跑；weekly-backtest 周六 job 同日 06:00 正常运行可互证），不构成空转证据。确证空转 = **10-21 一次**（RC=1）。随附观察：`zhonglian-monitor-morning-cmd`（同款 nemotron）本地流水 49 次中 4 次「ok 且时长 <35% 中位」（12.6~18.2s vs 中位 55.7s）疑似空转但无法回溯确证；周六 weekly-backtest-pipeline 6.2min 短时长经产物核对（validation 06:04 + adaptive_params 06:01 + backtest 报告 06:01 全在）为健康，销案。当日 06:40 一次性验证 job 虽正确诊断缺失，但自身 model-call 超时（600s、finalStatus=error、assistantTexts 空）且 `deleteAfterRun` 自删，结论未送达——验证网自身也是脆弱 agentTurn，且失败后无痕消失。
**修复**: ① `scripts/cron_health_check.py` 新增产物存在性检查 `missing_products()`（交易日 06:45 后须有当日 premarket_report、19:00 后须有当日 dual；should_alert 冷却去重；selftest 15 例全过）——接入既有 4h 一次的 cron-health-check 自然运行（三层防线第 2/3 层）；② 结构性修法（agentTurn→command 模式）已执行（10-21 21:29 用户批准）：`openclaw cron edit 87c6b7f9... --command "cd /Users/duguke/.openclaw/workspace && python3 analysis/premarket_pipeline.py" --no-deliver --timeout 1800000`，edit response 复核 payload.kind=command / delivery.mode=none（未被隐式改写）/ timeoutSeconds=1800（ms 自动换算，与原 agentTurn 对齐）/ schedule+enabled 零副作用；pipeline 无 argparse 旗标故无 dry-run，明晨 06:00 首个自然 run 为收口验证点（missing_products 06:45 兑底）。
**规约**: **status=ok ≠ 产物存在**。命令型 cron 的唯一可靠健康判据是产物在盘，不是 run 状态、不是时长（23s 说废话与 20min 实干都「合理」）。确定性命令优先 `--command` 直跑（LLM-in-the-loop 仅用于真正需要智能的环节）。一次性验证 job（deleteAfterRun）不得作为生产产物的唯一防线：超时即静默且自删无痕。
### Metadata (2026-10-21 manual-session 登记)
- Source: manual-session（webchat 深究轮；transcript 取证 5cae8518 / a50ba956）
- Reproducible: yes
- Pattern-Key: cron.agentturn-noop-product-missing（同族 cron.silent-noop-fake-ok 第三变体）
- Recurrence-Count: 1（确证 10-21；zhonglian-morning 4 次短时长可疑未计入）
- First-Seen: 2026-10-21
- Last-Seen: 2026-10-21
- Status: resolved（2026-10-22 06:0x 验收通过 ✅）：①`premarket_pipeline.py` 06:00:00 触发、06:01:24 产出 `analysis/daily/2026-10-22_premarket_report.md`（12KB）+ `data/agent_runs/run_20261022_060123.json` + pipeline_cache 四产物（scan/dual/audit/agent），即「产物在盘」判据满足；②首轮实测 `cron get` 的 state 一度残留昨日 agentTurn 的 `ok/23197ms` + agent 口吻 summary（agent 25s 说废话 vs command 1.4min 实干，`lastDurationMs` 一字未变 → 状态字段滞后于真实执行，**再次印证「状态不可信、只有产物可信」**）；③durationMs 在长跑命令模式下存在滞后显示，健康判定一律以产物 mtime 为准。本条终态。

## ERR-20261021-DailyNoteSelfDestruct
**Date**: 2026-10-21
**Priority**: high
**现象**: 19:46:48 一次性验证岗（04f400ef，会话 868398a8）向 memory/2026-10-21.md 补写记忆，写入本身成功；但同一 python 脚本的校验表达式 `print('memory updated ok, *** count:', open(p,'w') and open(p).read().count('***'))` 里 `open(p,'w')` 被求值 → **文件被截断成 0 字节**，`.read()` 读到空串、`count('***')=0`，脚本仍打印 "memory updated ok"。当日全文（dreaming 修复全程 + 傍晚链核对等 6 节）丢失，20:5x 由会话 d0700d43 从 transcript 碎片（18:47 全文快照 + 18:58 diff + 19:46:31 尾部 + 被毁前 append 块）逐字重建。波及：当晚 03:00 light 层当日原料。
**修复**: 日记已重建；promotion_guard 同轮发现「冻结事件假活」语义失真已修（skip 条件扩展 minRecallCount≥999999 硬闸变体）。
**规约**: **验证表达式严禁求值带副作用的写模式调用**。`'w'` 模式 open 只准出现在赋值/with 语句；写后校验必须只读（`len(open(p).read())` / `wc -c` 复核字节数）；「写→验」两步必须用两个独立语句/命令，禁止把校验塞进表达式链。与 10-09 脱敏坑同族：验证手段自身可以成为新的故障注入点（本条 = cron.silent-noop-fake-ok 第四变体：操作报 ok 但效果为毁）。
### Metadata (2026-10-21 manual-session 登记)
- Source: manual-session（webchat 深究轮；transcript 取证 868398a8 / d0700d43）
- Reproducible: yes
- Pattern-Key: ops.verify-expression-destructive-side-effect（同族 cron.silent-noop-fake-ok 第四变体）
- Recurrence-Count: 1
- First-Seen: 2026-10-21
- Last-Seen: 2026-10-21
- Status: resolved（当日重建 + 规约入 MEMORY.md；若再出现「验证/清理动作毁数据」升级三层防线）

## ERR-20261022-HeartbeatFinalTurnIdleTimeout
**Date**: 2026-10-22
**Priority**: high
**现象**: 20:52:05-20:57:38 心跳 run（cdee04a4，isolatedSession+lightContext 首跑）把 3 个任务检查全部执行，且首个工具调用即为 state 回写做准备（`cat memory/heartbeat-state.json; date +%s`），但**最终回复轮 LLM idle timeout (120s)**（amd-radeon/Qwen3.8-Flash-Next，output 2708 tokens 含 1943 reasoning 全部丢弃，`aborted:true idleTimedOut:true`）→ fallback 链 DeepSeek-V4-Flash→MiMo-V2.6-Flash→deepseek-chat 返回裸 `HEARTBEAT_OK`（5 tokens）→ 计划中的 state 回写从未执行。且 taskState 按 `startedAt=20:52:02` 标记完成（源码坐实：marking 在回复路径完成时写 startedAt 值）→ **失败 run 消耗了整个任务周期**。
**修复**: HEARTBEAT.md 新增 `state-bookkeeping` 首位任务（60m，"本 run 第一动作立即执行" + os.replace 原子写），standing rules 记录「回复前补写会被最终轮 LLM 失败连带丢掉」教训；git 159ff62 → fix commit。
**规约**: ①**「回复前补写」类收尾动作必须提前到 run 早期**——最终轮是 LLM 失败高发区（idle timeout/fallback 降级），晚期写入与最终轮共命运；②fallback 链的裸 HEARTBEAT_OK = **假 ok 家族第五变体**（run 半途而废但回复路径「正常完成」，表面全绿）；③一次性验证 cron 的 grep 证据会被**自身命令回显与分析引用污染**（本次 'Run the following periodic tasks' ×5/×9 命中全为自回显/分析引用，非真实注入证据）——验证证据必须先排除自身 transcript 污染再下结论。
### Metadata (2026-10-22 manual-session 登记)
- Source: manual-session（webchat 深究轮；transcript 取证 cdee04a4 / 9e1fb0b1 / e7d1befd）
- Reproducible: yes
- Pattern-Key: llm.final-turn-idle-timeout-loses-deferred-writes + cron.verification-evidence-self-echo（同族 cron.silent-noop-fake-ok 第五变体）
- Recurrence-Count: 1
- First-Seen: 2026-10-22
- Last-Seen: 2026-10-22
- Status: resolved（state-bookkeeping 前置落地；21:5x 首个自然 tick 为验证点）
- Maintenance (2026-10-23 每日维护): 验证点通过 ✅ —— `memory/heartbeat-state.json` mtime 2026-10-23 05:54（修复后多个自然 tick 持续回写），state-bookkeeping 前置任务实测生效，本条终态。

---
## ERR-20261023-AuctionSigNullCrash
**Date**: 2026-10-23
**Priority**: medium
**现象**: 09:27 auction-signal-push 调度 run exit 1：`AttributeError: 'NoneType' object has no attribute 'get'`——竞价池个股（000708 中信特钢）在 close_scan 信号档案中 `signal` 值为 null，旧码 `(sig or {}).get("signal", {}).get("level", "")` 两段兜底都挡不住「键存在但值为 None」；当日命中告警（🔴信号+低开共振，开盘 -5.06%）未送达 TG（09:44 重跑后推送又遇 TG TLS 潮汐，两段式故障）。
**修复**: fe36ddf（`(sig.get("signal") or {})` 兜底 + 09:44 重跑产物落盘）→ 291f1e2（`signals.get(code) or {}` + isinstance 二层，个股无档场景）→ aef3b95 入账。同日 10:30 intraday-alert-watch 同为 curl 35 TG TLS（11:00 自愈）。
**规约**: 外部数据取嵌套字段统一 `(x.get(k) or {})` 写法——`dict.get(k, {})` 的默认值只在**键不存在**时生效，键存在且值为 None 时返回 None，链式 `.get` 必崩（与 2026-10-08「数据源返回 `''` 而非 NaN」同族：外部数据形态不容错）。
### Metadata (2026-10-23 manual-session 登记)
- Source: manual-session（webchat 验收终态轮；cron runs traceback + git log 实证）
- Reproducible: yes
- Pattern-Key: data.none-valued-key-attr-crash（同族 data.source-type-intolerance）
- Recurrence-Count: 1
- First-Seen: 2026-10-23
- Last-Seen: 2026-10-23
- Status: resolved（两层修复已入库；复核点 2026-10-26 周一 09:27）

---
## ERR-20261023-CloseScanPocBinFloat
**Date**: 2026-10-23
**Priority**: high
**现象**: 越秀资本(000987) 在 10-22 18:31 与 10-23 06:00 两次 close_scan 指标计算抛 `TypeError: list indices must be integers or slices, not numpy.float64`（close_scan_v2.py calc_support_resistance:270）→ 股票变 error 行；daily_push slim 变换层 `r.get("signal")` 把 error 行写成 signal=null 并剥掉 error 字段（无痕降级）；下游 auction_signal_push 09:27 对 null signal 链式 .get 崩溃（fe36ddf 误归因 000708，实为 000987）。
**根因**: 窗口内 min(close)==min(low)（收盘恰在 60 日最低价边界）时，pd.cut 默认左开区间 (low,…] 把边界值判 NaN → vol_profile 变 float64 → groupby 键 float64 → idxmax() 返回 np.float64 → python list 以 float 索引崩溃。数据形态触发，非代码变更引入（close_scan_v2 自 10-20 零改动）。
**修复**: 0c86f99——include_lowest=True（边界值归入 bin0，同时修复 poc 一直丢边界样本）+ int(vol_by_bin.idxmax()) 兜底；daily_push slim 增 scan_error 字段保住 error 血统。回归：崩溃态重放 OK + 全池 36 只 0 崩溃。
**规约**: ①pd.cut 用数值边界时一律 include_lowest=True，且 idxmax()/index 取值进 list 前必须 int() 强转（与「键存在但值 None」同族：统计对象/外部数据形态不容错）；②变换层（raw→slim）必须透传 error 字段——剥掉 error = 无痕降级 = 下游拿 null 当数据（假 ok 家族：错误被清洗层吞掉）。
### Metadata (2026-10-23 manual-session 登记)
- Source: manual-session（webchat 深究轮；/tmp 复现脚本真栈 + scan_last.json 实录 + 档案逐日扫描）
- Reproducible: yes（df 终点=10-22 稳定复现；修复后重放通过）
- Pattern-Key: pandas.pd-cut-boundary-nan-float-index（同族 data.none-valued-key-attr-crash；ERR-20261023-AuctionSigNullCrash 为其下游）
- Recurrence-Count: 2（10-22 18:31、10-23 06:00 两次运行）
- First-Seen: 2026-10-22
- Last-Seen: 2026-10-23
- Status: resolved（0c86f99；验证点=今晚 18:30 全池扫描自然运行）
