> **[2026-10-18 维护 · RC>=3 终态确认]** 本 Pattern-Key 共享计数条目 = ERR-20261006-PM（RC=3 跨 3 任务：weekly 09-26 / premarket 10-06 / 本条；跨 30 天内），本条为早期实证不单独计数。防治规则 TOOLS.md「🔴 长跑 pipeline 卡死判定规则」2026-10-18 复核确认在位，无新复发，终态保持 promoted。

> **[2026-10-31 每周维护 · RC≥3 家族合并终态确认]** 本轮覆盖 6 条 RC≥3 共享计数家族：
> `ops.silent-orchestrator-misjudged-as-hang`(RC=3) / `err.dist-io-no-timeout`(RC=4) / `cron.silent-noop-fake-ok`(RC=3, LEARNINGS)
> / `exec.timeout-orphan-child.cron-longrunning`(RC=3) / `data.none-valued-key-attr-crash`(RC=3) / `llm.final-response-failure.loses-context-only-work`(RC=3)。
> 逐一复核：防治规则 grep 全在位（TOOLS.md「网络调用超时铁律」L461、「长跑 pipeline 卡死判定规则」L382；AGENTS.md 配套铁律「数据源容错」L168、「cron 真健康」L182、「run 产出禁止只存在于最终回复」L218），
> 近 7 天窗口（10-25~10-31）零新复发（`openclaw cron list` 实跑 0 error job；memory 10-30/10-31 无新事故；amd-radeon 最终轮失败簇仍无确证丢稿，RC 不虚增）。
> **六条全部维持 promoted 终态，本轮零新晋升、零 RC bump。** 依 10-24 收敛规约，各条不再逐条追加重复确认注记，以本合并注记为准。
> 同日销案：`auction-signal-push` 终验观察项关闭（见 ERR-20261023-BacktestCaliberDegradedSilentPush 尾注）。
> 红线核对：`openclaw_session_sweep` 仍不在现存 cron 列表 (2026-11-01 复核) → 历史误报维持，仅 warn，未新增条目。

> **[2026-11-05 每日维护 · 新增 1 条晋升 + 家族复核]**
> ① **本轮唯一晋升**：`plan.duplicate-detector-creation`(RC=3，跨 pre_response_guard/is_trading_day/data_freshness_guard 三案，
> 30 天内) → AGENTS.md 配套铁律「**立项前先盘点存量**」（上轮运行中断致条目标 promoted 但规则本体未落盘，本轮补齐并 grep 验证在位）。
> ② RC≥3 存量家族（10-31 六条 + 本条新晋升）grep 复核防治规则全部在位；近 7 天（10-29~11-05）无同 Key 新复发，零 RC bump。
> ③ 新增条目清点：ERR-20261101-C01(RC=1 resolved)、ERR-20261104-T12AuditNeverWritten(resolved)、
> ERR-20261105-BaostockProbeOrphanSpin(孤儿家族第 3 例变体，RC=1 首证)、ERR-20261105-CommitSweptStagedIndex(RC=1) —— 均未达阈值，维持观察。
> ④ 无 pending 残留；红线核对：`openclaw_session_sweep` 不在现存 cron 列表（11-05 cron list 复核）→ 历史误报维持，仅 warn。

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
- Recurrence-Count: 3 [Promoted] (2026-10-07 → TOOLS.md「🔴 长跑 pipeline 卡死判定规则」)
> **[2026-10-24 每周维护]** 共享计数主条目（本条 + ERR-20260926-WBP，RC=3 跨 3 任务）周度终态确认：TOOLS.md 规则在位，Last-Seen 维持 2026-10-06，无新复发，promoted 终态不变。

> **[2026-10-19 每日维护 · 第2轮]** RC≥3 共享计数条目（跨 3 任务 30 天内）终态复核：TOOLS.md 规则在位（grep 确认「长跑 pipeline 卡死判定规则」章节存在），无新复发，RC 维持 3 不虚增，保持 promoted。
> **[2026-10-20 每日维护]** 第三轮终态确认：TOOLS.md L367 规则仍在位，Last-Seen 维持 2026-10-06，无新复发，promoted 终态不变。
> **[2026-10-22 每日维护]** 第四轮终态确认：TOOLS.md「长跑 pipeline 卡死判定规则」在位，RC 维持 3（跨 3 任务），Last-Seen 维持 2026-10-06，无新复发，promoted 终态不变。
> **[2026-10-23 每日维护]** 第五轮终态确认：RC≥3 且 30 天内（Last-Seen 10-06），grep 复核 TOOLS.md 规则在位；无新复发，RC 不虚增，promoted 终态不变。
> **[2026-10-24 每周维护]** 第六轮终态确认：RC 维持 3，TOOLS.md 规则 grep 在位，无新复发；每日重复终态确认收敛为每周一次。

> **[2026-10-10 维护去重]** 本条目与 ERR-20261006-PM 同 Pattern-Key `ops.silent-orchestrator-misjudged-as-hang`
> （Recurrence-Count=3 共享计数，First-Seen 2026-09-26）。已合并为一条模式，保留两条作为
> 不同任务的实证记录，不再重复计入。防治规则已落地 TOOLS.md 同名章节。
> 2026-10-18 维护复核：TOOLS.md 规则在位于位（含 pgrep -P / 缓存 mtime / timeout 估算三判据），无新复发，终态保持 promoted。

## [ERR-20260927-87V] openclaw_session_sweep
> **[2026-10-29 维护 · 同类归并声明]** 本库共 3 条 `openclaw_session_sweep` sweep 条目（本条 09-27 / ERR-20261016-C02 /
> ERR-20261025-GZY），共享 Pattern-Key `runtime.failure` 家族。它们均属**同一个 sweep-hook 误报源**（钩子在 `/new` 扫会话
> 尾部日志，把 transient/误报名当失败上报），**不计入彼此 Recurrence-Count**（误报不是真复发，虚增会污染 promote 阈值），
> 且均已按红线判定为历史误报名（2026-10-29 再次 `openclaw cron list` 复核：52 jobs 中仍无此名）。后续再入同类 sweep 条目：
> 先看 Pattern-Key 是否已在本三条内，在则仅在本声明追加日期，**不新建条目、不 bump RC**。
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
- Recurrence-Count: 3 [Promoted] (2026-10-07 → TOOLS.md「🔴 长跑 pipeline 卡死判定规则」)

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
**Recurrence-Count**: 3（共享计数：10-09 quant_push / 10-19 同 cron 复发 / 10-26 PPO-CCI 复扫；跨 quant-backtest、ppo-cci-rescan 两任务，30 天窗口内；2026-10-27 维护 bump）
**First-Seen**: 2026-10-09
**Last-Seen**: 2026-10-26
**Promoted**: AGENTS.md「🧠 教训固化原则 · 配套铁律」(2026-10-10, Source: .learnings/ERRORS.md#ERR-20261009-QuantPushDetachedOutputLost)

### Maintenance (2026-10-27 每日维护) — RC 2→3 升格促发条件命中，规则已在前位
- 10-26 PPO/CCI 复扫第三次命中同模式（见 ERR-20261026-PpocciExecTimeoutOrphan，共享计数）：exec timeout=300 前台跑 → 父死子孤儿。与前两次的区别：本次落盘+孤儿处置规约当场执行（detached 重跑 ~1min 完成），零损失。
- 复发的不是「无规则」而是「长任务判定阈值靠拍脑袋」：AGENTS.md 铁律已补一句最小必要规则（exec 短超时只留给确定秒级调用；降级链耗时按最坏腿×票数估）；门禁层面不新增（AGENTS.md 铁律 + cron_noop_guard 已覆盖产物侧）。终态保持 promoted。

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
- Recurrence-Count: 4 [Promoted] (2026-11-10 → TOOLS.md「🔴 网络调用超时铁律」)
- First-Seen: 2026-09-17
- Last-Seen: 2026-10-18
- Status: promoted (2026-10-19 → TOOLS.md「🔴 网络调用超时铁律」)
- Promoted: TOOLS.md「🔴 网络调用超时铁律」(2026-10-19, Source: .learnings/ERRORS.md#err.dist-io-no-timeout)
- Maintenance (2026-10-23 每日维护): RC=4 ≥3、Last-Seen 10-18 在 30 天窗口内；grep 复核 TOOLS.md「网络调用超时铁律」在位，无新复发，promoted 终态不变。
- Maintenance (2026-10-24 每周维护): RC 维持 4，Last-Seen 维持 2026-10-18，TOOLS.md 铁律 grep 在位，无新复发，promoted 终态不变。注意：10-23 quant-backtest 窗口内 baostock/akshare 瞬态失败属已降级故障（ERR-20261023-BacktestCaliberDegradedSilentPush，已盖戳修复），非无超时挂死，不计入本 RC。
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
- Maintenance (2026-10-29 每日维护): 正文「修复方案待用户拍板」为 10-18 会话旧表述——Validation 已坐实修复落地（f470472，None 传播 + GATE_MISSING_POLICY/GATE_ERROR_POLICY），本条修复侧终态；仅「情绪维度退化」假设仍挂观察（独立事项，不阻塞本条）。

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
- Promotion (2026-10-28 每日维护): 同族家族键 `llm.final-response-failure.loses-context-only-work` RC 升至 3（10-22 心跳 / 10-27 KB 子代理 / 10-27 Obsidian cron，跨 3 任务、30 天内）→ 家族规则已提升至 AGENTS.md 配套铁律「凡有价值的 run 产出，禁止只存在于最终回复/上下文」。本条具体修复（state-bookkeeping 前置）继续有效，Status 维持 resolved + promoted 标注。
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
- Pattern-Key: data.none-valued-key-attr-crash（同族 data.source-type-intolerance；共享计数含 ERR-20261023-CloseScanPocBinFloat、
  与 2026-10-08 ERR-20261008-ModelGarbledOutput 的 data.empty-string-float 同属「外部数据形态不容错」家族）
- Recurrence-Count: 3 [Promoted]（共享计数：10-08 空字符串崩扫描 / 10-23 本条 None-signal 崩溃 / 10-23 CloseScanPocBinFloat pd.cut 边界；
  跨 close_scan、auction_signal_push、daily_push 三任务，30 天窗口内；2026-10-25 维护升格 bump）
- First-Seen: 2026-10-08
- Last-Seen: 2026-10-23
- Status: promoted (2026-10-25 → AGENTS.md「配套铁律 · 数据源返回类型必须容错」同族扩展段：(x.get(k) or {}) / include_lowest=True / int() 强转 / 禁剥 error 字段)
- Promoted: AGENTS.md 配套铁律（2026-10-25, Source: .learnings/ERRORS.md#data.none-valued-key-attr-crash）
- Status (原 2026-10-23 登记): resolved（两层修复已入库；复核点 2026-10-26 周一 09:27）
- Maintenance (2026-10-24 每周维护): 旁证 ✅ —— `data/auction/2026-10-23_9_25.json` = 17,222 B 真实快照（10-23 交易日）；cron 列表 `auction-signal-push`(cc1e9a93) 显示的 error 为 10-23 09:27 修复前 run 的残留状态，非新事故。终验等 10-26（周一）09:27 自然 run。
- Maintenance (2026-10-26 每日维护): 终验点=今日 09:27 自然 run（维护时 06:15 尚未发生，末次 run 为 10-25 下午、status ok）。run 后核对 `data/auction/2026-10-26_9_25.json` 产物 + cron runs 无 traceback 即可终态销账；若再次 None 崩溃则 bump 共享计数并复核 (x.get(k) or {}) 兜底覆盖面（291f1e2 二层）。
- Maintenance (2026-10-27 每日维护): **终验通过 ✅ 本条终态销账** —— 10-26 09:27 自然 run 三证齐：①`data/auction/2026-10-26_9_25.json` = 17,236 B 真实快照（09:25 落盘）；②`data/auction/signal_push_2026-10-26.json` 09:27 正常产出（212 B，hits/fade_risk 结构完整，非 traceback）；③`openclaw cron list` 复核 `auction-signal-push`(cc1e9a93) status=ok，10-24 记载的 error 残留态已被自然 run 覆盖清除。共享计数维持 RC=3 不虚增（终验通过非新事故），promoted 终态（AGENTS.md 配套铁律在位，grep 复核 L168）。

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
- Maintenance (2026-10-24 每周维护): 验证点通过 ✅ —— 10-23 18:30 自然运行产物在盘（`analysis/daily/2026-10-23_dual.md` 18:46 生成，000987 行正常「⚪双持有」非 error；`pipeline_cache/close_scan_2026-10-23.json` 全池 0 个 error/None-signal 行），0c86f99 边界修复自然场景生效，本条终态。RC 维持 2 不虚增。

---
## ERR-20261023-GhTlsFingerprintBlocked
**Date**: 2026-10-23
**Priority**: low
**现象**: `gh repo view / issue create` 全部报 `net/http: TLS handshake timeout`（直连与 7890 代理同败），同刻 curl 直连/代理均亚秒响应——gh 的 Go crypto/tls 指纹被网络侧针对 api.github.com 的 TLS 拦截甄别掉（与东财 curl_cffi 指纹拦截同族）。
**修复**: curl + `gh auth token`（keyring 取 token 写 600 临时 header 文件，用后即删）直接 POST API，issue 成功创建（airbing11/memory-lancedb-dreaming#4）。
**规约**: gh 遇 TLS handshake timeout 不要反复重试/换代理——先用 curl 同 URL 诊断（能通=指纹问题），走「token 临时文件 + curl」兜底；token 严禁进 stdout/日志/命令历史。
### Metadata (2026-10-23 manual-session 登记)
- Source: manual-session（webchat；用户 /approve 提 issue 执行时发现）
- Reproducible: yes
- Pattern-Key: net.gh-go-tls-fingerprint-blocked（同族 net.tls-fingerprint-interception）
- Recurrence-Count: 1
- First-Seen: 2026-10-23
- Last-Seen: 2026-10-23
- Status: resolved（curl+token 兜底可用；TOOLS.md 已固化）

---
## ERR-20261023-StaticInferenceFiledAsFact
**Date**: 2026-10-23
**Priority**: medium
**现象**: 对 memory-lancedb-dreaming tag 前缀冲突做了三段式静态推演（收养→深梦境哑火→60s message 振荡）并写进已提交的上游 issue #4；随后用插件真实导出函数 + mock cron 跑 5 tick 实测，两条被推翻：真实机制 = 日报 job id 每 60s 删+建漂移（插件自残，与用户 job 无关），深梦境 job 通过夺取日报 job 正常产生，无 message 振荡。
**修复**: 5-tick mock 实测取证（recon_5tick.mjs）；issue #4 更正评论起草待批；daily-note 已勘误。
**规约**: 对外提交（issue/报告/文档）的行为结论必须先用**真实导出函数 + 最小 mock** 跑过再落笔——静态推演只能产出「待验证假设」标注，不能写成结论（同族：「没有新版接口」需实测、「会话自报闭环」需 grep 核实）。
### Metadata (2026-10-23 manual-session 登记)
- Source: manual-session（webchat 深究轮；node import dist/cron.js 直跑）
- Reproducible: yes（/tmp/recon_5tick.mjs）
- Pattern-Key: analysis.static-inference-not-executed（同族 cron.verification-evidence-self-echo）
- Recurrence-Count: 1
- First-Seen: 2026-10-23
- Last-Seen: 2026-10-23
- Status: resolved（更正评论已发 #4#issuecomment-6364978142，2026-10-23 20:3x；上游作者 2026-10-26 16:55 确认并在 v0.3.19/8965d95 修复，含 5-tick 回归测试 77/77，issue 保持 open 至 ClawHub 发布可见）

## ERR-20261023-BacktestCaliberDegradedSilentPush
**Date**: 2026-10-23
**Priority**: high
**现象**: quant-backtest-daily（20:30）运行窗口内 baostock 登录全败（"网络接收错误"）+ akshare 走 7890 代理被拒（ProxyError RemoteDisconnected），36/36 全部回退新浪【不复权】；逐股 WARNING 只进 stderr，backtest 报告/JSON 摘要/TG 推送对口径降级零标注，"EMA12/26金叉 +46.48%" 照发——与历史前复权报告不可比却无任何提示。18:30 close_scan 时 baostock 尚完全正常（fail_count=0）、21:0x 代理实测存活（200/1.8s），属瞬态上游+代理窗口。
**修复**: backtest_strategies.py 新增 `_data_caliber_summary()`（读 DataRouter provenance 落盘态）→ 报告头口径降级戳 + JSON `data_caliber` 字段；quant_push.format_summary 有降级即追加"勿跨日比较"行。活测：今晚真实 provenance 36/36 fallback 全部命中。当晚报告已会话补注（显式标注非脚本生成）。
**规约**: ①产物必须自带口径标注——降级信息只进 stderr = 推送层失明（同族：daily_push slim 剥 error 字段、except 静默）；②"内部一致但与历史不可比"的降级（全池统一回退）比混合降级更隐蔽，因无跨标的矛盾可察觉；③数据源状态是时变的——"上游正常"结论必须绑定验证时刻。
### Metadata (2026-10-23 manual-session 登记)
- Source: manual-session（webchat 复验轮；logs/backtest_run_20261023.log.err 全量归因 + provenance 活测）
- Reproducible: yes（_data_caliber_summary(syms) → fallback=36）
- Pattern-Key: data.caliber-degradation-invisible-downstream（同族 data.none-valued-key-attr-crash 的"无痕降级"面）
- Recurrence-Count: 1
- First-Seen: 2026-10-23
- Last-Seen: 2026-10-23
- Status: resolved（脚本自动盖戳已落地；下轮 20:30 自然验证）
- Maintenance (2026-10-27 每日维护): **自然验证通过 ✅ 本条终态** —— 10-26 20:30 自然 run 产物 `analysis/backtest/2026-10-26.md` 第 5 行为脚本自动生成的「🔴 口径降级告警：36/36 只为不复权数据(新浪日K兜底)…不得跨日比较」戳（当晚恰为真实降级场景，脚本戳实弹生效，非人工补注）。RC 维持 1 不虚增。

> **[2026-10-25 每日维护]** cron failed 红线核对：本条 Source 为 manual-session 非 cron-failed 事件，不受误报红线约束；
> rc=1 < 3 维持观察，验证点 = 下个工作日 20:30 自然运行报告头部口径戳。另：`auction-signal-push`（cc1e9a93）在现存 52 jobs
> 列表内属实（非误报名），其 status=error 残留经 `cron runs` 坐实为 10-23 09:27 修复前末次 run，非新失败，终验等 10-26 周一自然 run。
> **[2026-10-31 每周维护 · 销案 ✅]** `auction-signal-push` `cron list` 复核 status=ok（末次 run 10-30 09:27 前后，21h ago），
> error 残留已被后续成功 run 覆盖 → 终验观察项关闭。本条（口径戳实弹验证 10-27 已过关）维持终态，RC 维持 1。

## ERR-20261025-EditHeaderSwallowOnAppend
**Date**: 2026-10-25
**Priority**: medium
**现象**: 用 edit 工具向 memory 日志 append 新章节时，两次把「下一章的标题行」当 oldText 且 newText 未保留原文 → 下一章标题被吞、正文悬挂无归属。同日两次（线 C 标题、深究②标题），均靠事后 `grep -n "^## \|^### "` 结构检查发现并修复。
**规约**: ①append 新章节时 oldText 取「前一章末尾的最后一行」而非「下一章的标题行」；若必须以标题为锚点，newText 必须原样保留该标题；②多章节重排优先用 python 脚本按行剪切（本次实测安全），不用 edit 标题替换法；③编辑 memory/*.md 后必跑 `grep -n "^## \|^### "` 验证章节结构完整再收尾。
**防线**: ①本条目（文档层）②编辑后章节结构 grep 自检（流程层，与「写完必验语法」同族）。
### Metadata (2026-10-25 manual-session 登记)
- Source: manual-session（webchat D/E/F 落地轮）
- Pattern-Key: ops.edit-header-swallow-on-append（同族 err.write-tool-mangle 10-09 脱敏坑——验证/写入手段自身是故障注入点家族）
- Recurrence-Count: 2（同日两次，同工具同模式）
- First-Seen: 2026-10-25
- Last-Seen: 2026-10-25
- Status: resolved（两处标题已当日修复；规约固化至本条目）

## ERR-20261025-SessionsCorpusDoomedCatchupSync
**Date**: 2026-10-25
**Priority**: medium
**现象**: memory_search corpus=sessions 本机 4 次调用全部 `unavailable + "timed out after 15s"`（16:47/16:53/17:05/17:23）。实测定性：①`memory_index_meta.sources=["memory"]`、memory_index_sources 零条 sessions 行——sessions 语料从未被索引；②每次调用挂死在嵌入之前（实测窗口零 HTTP POST、零 WAL 增长），疑似 10,211 个会话文件（2.4GB）的脏检查/枚举超出 15s 工具预算；③16:47-17:01 出现 memory-reindex-cc6bc16a 临时库（WAL 41MB）后自行消失且 meta/sources 未变（补索引未持久化即被清理）；④gateway 日志全程零记录。docs 口径：默认 sources=["memory"]，需显式加 "sessions" 才启用。
**修复**: 本地规避（TOOLS.md 标注勿用 corpus=sessions；查会话历史用 sessions_history/文件直读）。上游已立项：[#183499](https://github.com/openclaw/openclaw/issues/183499)（搜索时无界 catch-up 同步 × 15s 工具超时 + 误导性 provider-error 诊断 + 零日志可观测性缺口，附 5 条修复建议）。
**规约**: ①工具描述宣称的 corpus ≠ 可用——首次使用新 corpus 前先小成本验证（maxResults=1）；②「搜索时补索引」类设计在大库上必然超时，调用方无法从错误信息区分"挂死"与"在补索引"；③memory_search 各 corpus 是独立健康面——heartbeat 守卫只测 memory corpus，覆盖不了 sessions/wiki 面。
### Metadata (2026-10-25 manual-session 登记)
- Source: manual-session（webchat 复检+深究④轮）
- Pattern-Key: memory.sessions-corpus-unbounded-catchup-timeout
- Recurrence-Count: 1
- First-Seen: 2026-10-25
- Last-Seen: 2026-10-25
- Status: resolved（本地规避落地 + 上游 #183499 跟踪；本机 corpus=sessions 可用性待上游修复后复评）

## [ERR-20261025-GZY] openclaw_session_sweep
> **[2026-10-27 维护 · 防误报红线核对]** 同上：`openclaw_session_sweep` 不在现存 `cron list`（52 jobs）中 → 历史误报名，仅告警不转发；本条已经 10-25 heartbeat 正常分诊 resolved，维持不变。

**Logged**: 2026-10-25T12:23:57.548Z
**Priority**: medium
**Status**: resolved (2026-10-25 20:4x heartbeat triage)
**Area**: config

### Summary
Session-end sweep detected 4 possible errors in the previous OpenClaw session.

### Error
```
[assistant turn failed before producing content]
"content": "#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n\"\"\"\n斐波那契扩展位止盈参考扫描 (fib_extension_scan.py)\n==================================================\n对关注池/持仓股票计算\"上升波段的斐波那契扩展位\"(1.0/1.272/1.…
SyntaxError: unexpected character after line continuation character
**Method note:** 4 次 web_search 首轮 fetch failed，已全部重试成功；每条结论均附具体来源（USITC新闻稿、巨潮公告PDF、财联社、中指CREIS等）与日期。
```

### Context
- Detected by the self-improvement hook on `/new` (OpenClaw has no per-tool-call hook, so errors are swept from the session transcript at session end)
- Session key: agent:main:dashboard:13366f89-5df4-4c3b-89bb-3acce3bd152f
- Session transcript: /Users/duguke/.openclaw/agents/main/sessions/0d86903c-853e-46da-81d3-e3ba9deaf06f.jsonl
- Excerpts are truncated and redacted; check the transcript for full context

### Suggested Fix
Triage this entry: if the error was real and non-obvious, keep it and fill in the fix; otherwise mark it resolved or delete it. Before keeping it, grep for its Pattern-Key(s) and fold recurrences into the existing entry (bump Recurrence-Count) instead of duplicating.

### Metadata
- Source: openclaw-error-sweep
- Reproducible: unknown
- Pattern-Key: runtime.failure
- Pattern-Key: runtime.exception
- Pattern-Key: runtime.syntax-error

### Triage Outcome (2026-10-25 20:4x heartbeat 维护)
- ①SyntaxError ×4 均为 dashboard 会话（0d86903c）内临时命令迭代（exit 1 即抛）；`analysis/fib_extension_scan.py` 落盘版最后修改 10-12、本次 py_compile 通过 → 写作/迭代过程瞬时失败，非代码缺陷，无数据损失。
- ②"assistant turn failed before producing content" 为模型侧瞬时故障，与 ERR-20260927-87V 分诊同类，无需行动。
- ③method note（web_search 重试全成功）为过程记述，非错误。
- 一次性 transient（RC=1），不立 Pattern，本条关闭。

---

## ERR-20261026-PpocciExecTimeoutOrphan
- **日期**: 2026-10-26 21:06-21:16
- **现象**: 盘后 PPO/CCI 复扫在 exec 内跑（timeout=300s），baostock 不可达时 DataRouter 全链每票白付 2-3s 登录重试 → 36 只拖到 >5min → 被 exec timeout SIGKILL，父 shell 死而 python 子进程幸存成孤儿（stdout 管道断裂，输出不可回收）。
- **根因**: ①低估降级路径耗时（复权源全败 ×36 票的乘数效应）；②已知 3-5 分钟量级任务仍放 exec 前台跑而非 nohup detached——同族第 3 次实证（10-09 quant_push / 本条 / TOOLS.md 既有铁律）。
- **处置**: kill 孤儿 → 改直连新浪腿（跳过死腿）→ nohup > log 2>&1 & detached 重跑，~1min 完成。
- **规约**: 预估 >2min 或耗时受外部源健康度影响的任务，一律 nohup detached + 轮询日志文件；exec timeout 只留给确定秒级返回的调用。给降级链设超时预算时按「最坏腿 × 票数」估算，不按 happy path。
- **状态**: resolved（已提交 sina 直连腿 + detached 运行规约本条）
### Metadata (2026-10-27 维护补全)
- Source: manual-session（盘后复扫任务）
- Reproducible: yes（baostock 不可达 + 36 票全链重试即可复现耗时膨胀）
- Pattern-Key: exec.timeout-orphan-child.cron-longrunning（与 ERR-20261009-QuantPushDetachedOutputLost 共享计数，RC=3 已 bump 至主条目，本条为其第 3 次实证，勿单独重复计数）
- Status: promoted（随主条目终态，规则已补强至 AGENTS.md 配套铁律 2026-10-27）

---

## ERR-20261027-BlogwatcherFeedUrlTruncated
- **日期**: 2026-10-27 06:50-07:00
- **现象**: blogwatcher 重建 FT中文网镜像 源（Google News RSS 带查询参数 URL）后，扫描抓回 38 条英文默认头条（CNBC/Reuters/NYT），冒充 FT 内容入库。
- **根因**: `blogwatcher add <name> <url>` 不带 `--feed-url` 时走自动发现，对带 query string 的 URL 会把 feed_url 截断成 `https://news.google.com/rss`（丢整个 query）→ 扫描实际请求 Google 默认 feed。curl 直测同 URL 完全正常，UA 无关。
- **处置**: remove 后用 `--feed-url` 显式指定完整 URL 重加，scan 后 `articles --blog` 抽样核对内容形态（确为 FT 中文文章）。
- **规约**: ①blogwatcher 对参数化 feed URL 必须显式传 `--feed-url`，add 后必查 `blogwatcher blogs` 的 Feed 字段是否完整；②换源后首次 scan 必须**抽样核对文章内容语言/主题**，不能只看 Found/New 计数（默认 feed 注入 = 计数也合理、内容全错，属「假 ok 家族」变体）；③Google News RSS 的 `+` 空格分隔符建议改 `%20` 规避解析歧义。
- **状态**: resolved（FT 源已修复并验证；676 条积压已备份至 data/blogwatcher/unread_backup_2026-10-27.txt 后 read-all 清零）
### Metadata
- Source: manual-session（blogwatcher 换源任务）
- Reproducible: yes（add 带 query URL 不传 --feed-url 即复现）
- Pattern-Key: blogwatcher.add-feedurl-truncation
- Pattern-Key: feed.default-feed-injection.silent

## ERR-20261027-SpawnDefaultModelUnresolved
- **日期**: 2026-10-27 07:17
- **现象**: sessions_spawn 派出 kb-backfill-batch16 子代理，status=accepted 但立即回失败事件：`Model not found: "amd-radeon/Gemma-4-31B-IT" (disabled)` + `No fallback models available (provider=amd-radeon, model=gemma-4-31b-it)`，消耗一次 spawn 往返。
- **根因**: 未传 `model` 参数时子代理继承会话 default_model（Gemma-4-31B-IT），该模型在 amd-radeon 端点已 disabled，且 amd-radeon 无 fallback 模型列表 → 启动即失败。同会话 session_status 显示实际运行模型为 `amd-radeon/MiMo-V2.6-Flash`（主模型 ≠ default_model），先例 batch_14 曾解析到 z-ai/glm-5.3-flash——子代理模型解析路径不稳定，不能假设默认可跑。
- **处置**: 显式传 `model="amd-radeon/MiMo-V2.6-Flash"` 重派，resolvedModel=modelApplied=true，二次成功。
- **规约**: ①sessions_spawn 长任务必须显式传 `model`，不依赖 default_model 解析（主模型/默认模型/disabled 三态不一致）；②accepted≠running——spawn 后留意首个事件是否为失败，失败即带显式 model 重派，不要空等；③模型不可用时优先换同 provider 已验证可用模型（MiMo-V2.6-Flash 本会话实测可用），而非等 fallback（amd-radeon 无 fallback 列表）。
- **状态**: resolved（batch_16 二次 spawn 成功）
### Metadata
- Source: kb-backfill-batch14→16 续轮派批
- Reproducible: yes（不传 model + default disabled 即复现）
- Pattern-Key: spawn.model-default-unresolved

## ERR-20261027-BaostockFakeLoginRetrySpin
- **日期**: 2026-10-27 05:24 故障 / 17:20 修复
- **现象**: 手动全量扫描 24/36 只失败，连续报 `[BS] 查询错误: 用户未登录, 重试 1/2 → 2/2`；登录本身成功（前 12 只正常），第 13 只起全挂。
- **根因**: baostock 服务端在会话中途踢线（TCP 层全程可达，应用层 reset），但本地 `_bs_logged_in` 标志仍在 30min TTL 内为 True → `ensure_login()` 直接放行 → 重试永远复现「用户未登录」，24 只 × 2 次重试全部空转。**「本地登录态标志」与「服务端会话状态」是两个独立事实，TTL 内的本地标志不能证明服务端会话还活着。**
- **处置**: `analysis/bs_session.py` 新增 `_is_session_dead_error()` 关键词表（用户未登录/登录失效/会话/网络接收错误/接收数据异常），查询错误命中时 `force_relogin()` 后再重试（commit e88d231）。实测 force_relogin 恢复 + 全量重扫 36/36。
- **规约**: ①凡「本地缓存的会话/凭证态」遇服务端拒绝，重试前必须强制重建会话，不能信 TTL 内的本地标志；②诊断这类故障先分三层：TCP 连通性 → 应用层登录握手 → 会话中途错误，登录成功≠会话能撑完整批；③单连接长批量查询要预期服务端踢线，批量越大越要有会话死亡自愈。
- **状态**: resolved（36/36 重扫验证；18:30 cron 前落地）
### Metadata
- Source: manual-session（baostock 诊断任务）
- Reproducible: yes（服务端踢线 + 无自愈逻辑即复现）
- Pattern-Key: baostock.session-death.fake-login-flag.retry-spin

## ERR-20261027-KbBackfillSubagentTimeoutLostWork
- **日期**: 2026-10-27 21:02~22:05
- **现象**: KB 回填 batch_21 双段子代理两轮 dispatch：第一轮两段均 LLM 请求超时（0 token、无任何产物）；重派后 A 段长时间运行后被超时杀掉，已合成条目只存在于上下文、未落盘 → 稿件全部丢失重跑（B 段正常完成）。
- **根因**: 免费端点（glm-5.3-flash）瞬时故障叠加子代理「全部内容攒上下文、最后一次性写盘」的默认产出模式——中断点落在写盘前，已完成工作不可恢复。
- **处置**: batch_22 起任务简报强制「写盘纪律」：每判定完 1 条立即 append 暂存文件（write 首建 + 后续追加），任何时刻中断已写内容必须在盘上。batch_22 实测一次跑通、全部自检通过、零丢稿；batch_21 重派后亦完成并落库。
- **规约**: ①多条目/多阶段产出的子代理任务简报必须写明「逐条落盘、禁止末尾一次性写」；②超时重派前先 ls 暂存件确认有无半成品（0 token 失败=未开始可整段重派；有 token 无产物=可能丢稿，需先找残件）；③同批次两段同型失败优先怀疑端点级故障而非任务内容。
- **状态**: resolved（batch_21/22 均落库，batch_22 按新纪律零丢稿）
### Metadata
- Source: subagent-dispatch（KB 回填 batch_21/22）
- Reproducible: yes（端点抖动 + 末尾一次写模式即复现）
- Pattern-Key: subagent.llm-timeout.context-only-work.write-as-you-go
- Promotion (2026-10-28 每日维护): 同族家族键 `llm.final-response-failure.loses-context-only-work` RC 升至 3（10-22 心跳 / 10-27 KB 子代理 / 10-27 Obsidian cron，跨 3 任务、30 天内）→ 家族规则已提升至 AGENTS.md 配套铁律。本条 write-as-you-go 纪律即该规则的子代理形态实例，Status 维持 resolved + promoted 标注。

## ERR-20261028-KbBackupOverwrittenByStaleRerun
- **日期**: 2026-10-27 06:45（发生）/ 2026-10-28 04:5x（复验发现并恢复）
- **现象**: `knowledge_base.md.bak-batch14`（pre-batch_14 冻结快照，已随 999657e 入库）在 batch_15 合并窗口后被**整份覆盖**为当时的 KB 状态（5110 行/含 batch_13+14+15 三个回填标记）——备份冻结性被破坏，且无任何告警。10-28 复验时靠「备份行数 > 后继备份行数」的异常（473651B > bak-batch15 的 469658B）+ 标记数审计抓出。
- **根因（假说，高置信）**: batch_15 前后存在一次带错误批次号的合并脚本重跑：脚本先 `cp KB bak-batch14`（覆盖历史快照）再进入 assert 防重断言 → 断言命中中止、KB 本体无损，但备份已被覆盖。即「备份先于断言」的顺序 + 批次号来源不可靠（stale counter/queue）共同作用。
- **处置**: `git checkout -- bak-batch14 ×2` 从 999657e 恢复冻结快照原貌（5086 行/仅 batch_13 标记）；七个后继备份标记链审计全部自洽。
- **规约**: ①合并脚本的断言/防重必须放在**任何写操作（含备份）之前**；②备份文件名生成禁止依赖可变计数器——写前应校验目标备份**不存在**才允许创建（存在即中止并报批）；③备份链完整性验证 = 行数单调性 + 标记集单调性双向审计（行数必须随批次号单调递增，标记集必须是前缀关系）——建议并入 kb 灌溉门禁。
- **状态**: resolved（git 恢复 + 全链审计通过）
### Metadata
- Source: 复验会话（KB 备份链审计）
- Reproducible: yes（合并脚本重跑 + 批次号错位即复现）
- Pattern-Key: kb.backup-chain.frozen-snapshot.overwrite-before-assert

## ERR-20261028-ObsidianCronNoResponse
- **日期**: 2026-10-27 19:30 触发 / 19:33 失败（2026-10-28 每日维护发现并登记）
- **现象**: cron `Obsidian笔记→投资理念归纳`（181b0304，agentTurn，model=z-ai/glm-5.3-flash）运行 204s（含 Obsidian Vault 全量扫描工具调用）后 `Agent couldn't generate a response`，零 summary、零产物、announce 未投递 → 10-26 19:30→10-27 19:30 窗口的投资理念归纳完全丢失（后续 24h 窗口不会自动补扫该区间）。
- **根因**: LLM 最终回复轮失败（免费快速模型 + ~3.5min 长 run），唯一成果 = 最终回复文本，失败即全丢。该 job 历史失败同族：10-06 ×4「All models failed (7) deepseek timeout」、10-16 ×4 OutboundDeliveryError（有内容未送达）——**失败高发点始终是「回复/投递」一端，而非扫描本身**。
- **处置**: 登记本条并并入家族键。10-27 缺口如需补齐须手动指定窗口重跑（涉及 telegram 投递，待用户决定，不自动重跑）。
- **规约**: ①cron 归纳类任务的成果先落盘产物文件再 announce 引用，不把唯一成果押在最终回复上；②长 run（>3min）的 agentTurn cron 不用免费快速模型（AGENTS.md 既有铁律的又一实证）；③「couldn't generate a response」= run 内已完成工作全部在上下文中，重跑是唯一恢复路径——凡此失败先查产物侧有无部分落盘。
- **状态**: resolved（登记完成，家族规则已提升；缺口补扫待用户决定）
### Metadata
- Source: cron（每日维护 06:00 巡检发现）
- Reproducible: yes（长 run + 免费模型 + 唯一成果=最终回复即复现丢稿）
- Pattern-Key: llm.final-response-failure.loses-context-only-work（家族键，本条为第 3 次实证）
- Recurrence-Count: 3 [Promoted] (2026-11-03 → AGENTS.md「产出写盘纪律」)
- See Also: ERR-20261022-HeartbeatFinalTurnIdleTimeout（家族第 1 实证）、ERR-20261027-KbBackfillSubagentTimeoutLostWork（家族第 2 实证）、AGENTS.md「免费快速模型不许跑长任务」
- Source: .learnings/ERRORS.md#llm.final-response-failure.loses-context-only-work
- Maintenance (2026-10-29 每日维护): ①**缺口销账 ✅** —— 10-27 归纳缺口无需人工补扫：10-28 19:30 自然 run status=ok（382s，delivered），产物 `wiki/sources/investment-philosophy-2026-10-28.md`（19:36 落盘 2.2KB）按 48h 窗口已覆盖 10-27 区间（Vault 0 篇命中、无积压，会话核实）→「缺口补扫待用户决定」关闭，本条终态。②**观察项（不 bump RC）**：10-28 深夜~10-29 凌晨 dashboard webchat 会话（8eadc40c）出现 ≥6 次 `[assistant turn failed before producing content]`（amd-radeon 端点，用户多次「继续」恢复）——属最终轮失败簇，但已完成工作经会话续跑全部找回、**无确证丢稿**，按「无确证不虚增」纪律 RC 维持 3；若后续出现同簇真丢稿则 bump 至 4 并复核端点健康。

## ERR-20261030-CoverageBoardDisclosureFalseNegative
- **日期**: 2026-10-29 20:03（特变披露）/ 2026-10-30 05:34（兜底核对假阴性）/ 2026-10-30 06:2x（batch_26 补采发现）
- **现象**: 05:34 覆盖板兜底核对报「7 只全部未披露，无漏采」，但特变电工 Q3 已于 10-29 20:03 披露（核对时点前 ~9.5h）——新闻流关键词检索式核对漏检。**同族第 2 次**：10-22 中信特钢漏采 7 天（batch_25 stale_articles 捞回，间隔 24h 内复发）。
- **根因**: 兜底核对用「新闻流检索」而非「公告口径直查」——晚间披露（20:0x）常以「{名称}：2026年第三季度报告」公告快讯形式出现，泛关键词（股票名+业绩/净利）在多源噪声下命中率不稳定；「核对完成」被当成「确认无披露」，实为「本次检索未命中」（假 ok 家族：以动作完成替代事实确认）。
- **处置**: batch_26 特变补采入 KB（db9610a）+ 覆盖板主表 30/0/0/6 + 剩余 6 只落地前预锚预注册（算术自检公式 + 2025 双源基数，10-30 晨落盘）。
- **规约**: ①披露收官周兜底核对必须**逐只用公告口径直查**（「{名称} 三季度报告/三季报」），新闻流检索只作补充；②「未披露」结论 = 公告口径零命中 + 公告页/官方渠道核对，不得由单轮新闻检索推出；③落地前预锚（基数+公式）使漏采在落地日必被算术自检暴露（恒力/特变两轮实证有效）。
- **状态**: resolved（数据补齐；收官窗内控制 = 预锚表 + 19:06 收官抓取 runbook）
### Metadata
- Source: 覆盖板兜底核对（05:34 会话）/ batch_26（并行会话补采）/ 本会话登记
- Reproducible: yes（晚间公告快讯 + 泛关键词检索即复现）
- Pattern-Key: kb.coverage-board.disclosure-false-negative（家族：ops.fake-ok，第 2 次实证）
- Recurrence-Count: 2（特钢 10-22 / 特变 10-29；跨 2 个核对轮次）
- First-Seen: 2026-10-25（特钢披露日）
- Last-Seen: 2026-10-29
- Gate-Status: 文档层（覆盖板规约+本条）已落；可执行门禁**有意缓建**——覆盖板为三季报季季节性产物（10-31 收官后退役 36/36），通用「公告口径直查」规则已入预锚 runbook；若非季报窗口复发（如年报季），届时按三层防线建门禁

## ERR-20261030-Tier1SuppressionNoAuditTrail
- **日期**: 2026-10-27（信号被吞日）/ 2026-10-30 06:1x（发现并修复 e5cbc73）
- **现象**: 10-27 越秀 MACD 死叉卖出信号被 tier1 择时拦截无痕改写为「持有」，当日 signal_audit 却报「0 冲突/各源一致」——被吞信号直接从审计里消失，审计自身成为盲区，且这是 10-18 留痕协议的第三次同族暴露。
- **根因**: `_mark_suppressed` 留痕只挂在 tier2/tier3 **买入**拦截分支，tier1 **卖出**拦截分支漏挂。「留痕协议已落地」被当成「全路径留痕」，实际协议覆盖面从未按分支枚举核对（假 ok 家族：以动作存在替代覆盖完整）。
- **处置**: e5cbc73 为 tier1 卖出拦截分支补挂 `_mark_suppressed(signal, "tier1")`，语法+脱敏检查通过。
- **规约**: ①任何「拦截/抑制/降级」类留痕协议，落地时必须按**决策树全分支枚举**核对挂点（买入×卖出×每个 tier），禁止只覆盖当次触发的那条分支；②审计报「0 冲突」≠「无拦截」——审计侧需能区分「无信号」与「信号被吞」，否则该结论不可用于放行。
- **状态**: resolved（e5cbc73；验证点 = 下次 tier1 卖出拦截自然发生时 signal_audit 出现 suppressed 留痕行）
### Metadata (2026-10-31 每日维护补登记)
- Source: manual-session（10-30 晨审计，memory/2026-10-30.md「发现 B」）
- Reproducible: yes（构造 tier1 卖出拦截信号即无留痕）
- Pattern-Key: ops.silent-suppression-audit-blindspot（假 ok 家族 ops.fake-ok；与 data.caliber-degradation-invisible-downstream、kb.coverage-board.disclosure-false-negative 同族「以动作完成替代事实确认」）
- Recurrence-Count: 1（家族面计：本条为该家族「审计盲区」变体首证；口径无痕/核对假阴性各为其姊妹变体，不共享计数）
- First-Seen: 2026-10-27
- Last-Seen: 2026-10-30
- Status: resolved（修复已落地；RC=1 < 3 暂不 promote，若留痕协议再漏分支则按三层防线建枚举门禁）

## ERR-20261030-KbStubDuplicationReshaped
- **日期**: 2026-10-28~30（发现）/ 10-30 当日治理（db9610a）
- **现象**: KB writer 重复机制未死、**形态已变**——由旧字段级重复变为跨块 stub 重复：10-28 存在两个盘后块（手抓 25 条 vs 官方 19:06 cron 28 条，12 同标题 stub 重叠 ~45%）；特变「Q3 业绩说明会公告」同标题 stub ×3（多晚重抓挂架公告）。危害 = KB 膨胀 + 检索噪声，无事实污染。
- **根因**: writer append 路径无全文查重，防重只在字段级旧机制上生效；跨块/跨日重抓同标题即产 stub 副本（旧机制治理后残留的形态迁移，非新故障源）。
- **处置**: news_monitor.py append 前 • 行全文查重（统计行豁免、fail 告警不静默）+ 存量一次性清理 -84 行（69 重复标题→0，detailed 187 保持，.bak-stubdedup 留底）→ db9610a。量化依据先行（真实重复仅 69 标题/84 行），故选「防重+清理」而非接受现状。
- **规约**: ①旧去重机制修复后必须用**新形态枚举**复查（字段级≠跨块级≠跨日级），"已根治"结论要绑定当时的重复形态；②治理决策前先量化重复规模，规模可控选一次性清理。
- **状态**: resolved（防重钩子 + 存量清零；验证点 = 后续 KB append 后重复标题数持续为 0）
### Metadata (2026-10-31 每日维护补登记)
- Source: manual-session（10-30 webchat 深挖，memory/2026-10-30.md）
- Reproducible: yes（跨日重抓同标题公告即产 stub 副本，修复前）
- Pattern-Key: kb.writer.stub-duplication.reshaped-form
- Recurrence-Count: 1（旧字段级重复为同一机制的前一形态，但旧治理无独立条目可 bump，本条起新计数）
- First-Seen: 2026-10-28
- Last-Seen: 2026-10-30
- Status: resolved（RC=1 < 3 暂不 promote）

## ERR-20261031-ExecPipeHeredocLost
- **日期**: 2026-10-31（03:12/05:12/07:00/09:11/11:43/14:41 六次 Exec failed 推送；当日 15:5x 会话定案）
- **现象**: 心跳 cron-health-scan 反复报 `NameError: name 'true' is not defined. Did you mean: 'True'?`（line 10/12 漂移）；proactivity-review 链偶发 Exec failed。所有 run 模型均同 run 内用「先落盘再读文件」重试成功；cron 舰队实际全绿（4h cron job 当日三报 0 告警）——纯噪声告警，无真实监控盲区。
- **根因**: ①主因=模型习惯写 `openclaw cron list --json | python3 - <<'EOF'`（管道+heredoc 同命令）。【10-31 19:5x 深究修正】机制非「exec 层丢 heredoc」：exec 封装（zsh -f -c + snapshot 前置 + eval 包裹，ps 取证）对命令文本无损，真凶是 **zsh 5.9（macOS 自带版）对「管道非首级段挂 heredoc/here-string」的行为=上游管道输出⊕heredoc body 按序拼接**（bash/sh 均正确覆盖管道；zsh 文件模式/eval 包裹/前导行/三级管道全部复现，与 OpenClaw 无关，exec 仅继承）→ 管道输出（cron JSON）被当 Python 源码执行，JSON 语法合法到首个裸 true → NameError（报错行号=JSON 首个布尔行，故 line 10/12 漂移；会话存档脚本第 12 行与报错不符是突破口）。双复现实证（echo JSON 小样 + cron list 全量均复现；独立 heredoc 无管道正常）。【11-05 终版定性，zsh-workers 双回复纠正】该行为=zsh **MULTIOS 选项（默认开启）的文档化设计，非 bug**：stdin 多重为读重定向→后台进程拼接喂入；POSIX 语义需 `set +o multios`；zsh 也不承诺解释任意 bash 脚本（Daniel Shahaf 引 zsh.org/workers/53892）。归档：原信 55234 / Stephane 55235 / Daniel 55237。②次因=proactivity 链尾裸 grep -c 零匹配 / cat 缺文件（~/proactivity/session-state.md 已丢失）exit 1 被误报。
- **规约**: ①exec 内联脚本一律「先落盘再 heredoc 读文件」，禁 `cmd | python3 - <<EOF`；②心跳链尾必须 exit 0（grep/find 零匹配、缺文件用 `|| true` 收尾）；③防线已落 HEARTBEAT.md（两任务 prompt + standing rules 两条红线）与 TOOLS.md 红线条目。
- **规约**: ①exec 内联脚本一律「先落盘再 heredoc 读文件」，禁 `cmd | python3 - <<EOF`；②心跳链尾必须 exit 0（grep/find 零匹配、缺文件用 `|| true` 收尾）；③防线已落 HEARTBEAT.md（两任务 prompt + standing rules 两条红线）与 TOOLS.md 红线条目。
- **状态**: promoted (2026-11-08 → TOOLS.md「🔴 exec 管道+heredoc 丢失红线」)
- **Promoted**: TOOLS.md (2026-11-08)
### Metadata
- Source: manual-session（用户问询触发，memory/2026-10-31.md）
- Reproducible: yes（echo JSON 管道样例 100% 复现）
- Pattern-Key: exec.pipe-heredoc-lost.json-as-python-source
- Recurrence-Count: 6 [Promoted]（同日六次心跳触发，同一根因；坏写法在会话史最早见于 10-23，此前未归档）
- First-Seen: 2026-10-31
- Last-Seen: 2026-10-31
- Status: resolved（单根因单日集中爆发，当日修复；不另立独立规则）

## [2026-11-01] ERR-20261101-C01: 模拟盘成交档案平仓量不入档 + 指数代码路由误判
- **现象**: ①执行审计按「BUY 累计−SELL」重建持仓得 9 只，与实际（越秀 1 只）严重不符；②反转模块拉沪深300 得 `sz.000300 返回空序列`。
- **根因**: ①portfolio_sim_trades.json 的 SELL 单 **15/15 全部 shares=0**（平仓量从不写入该档案）→ 任何基于成交差值的持仓重建必然虚增；②baostock 代码路由按「0/3 开头=深市」前缀规则把指数 000300 误判为 sz.（指数在沪市 sh.000300），个股路由规则不适用于指数代码。
- **规约**: ①模拟盘「时点持仓」的权威口径 = 仲裁档案 stop_loss 非空（030 幽灵过滤保证 shares>0 才算）或 portfolio_sim_state；禁用 trades 差值重建；②已执行匹配对 SELL 不看 shares、只看 note 无「冲销」；③baostock 指数代码必须显式指定（sh.000300），不走个股前缀路由。
- **状态**: resolved（执行审计 v1.1 + emotion_reversal bs_code 参数当日修复，自测覆盖三场景）
### Metadata
- Source: manual-session（量化系统加固落地，memory/2026-11-01.md）
- Reproducible: yes（10-28 审计复现 / sz.000300 空序列复现）
- Pattern-Key: data.sim-trades-sell-shares-zero + api.bs-index-code-routing
- Recurrence-Count: 1（首证）
- First-Seen: 2026-11-01
- Last-Seen: 2026-11-01
- Status: resolved

## [ERR-20261104-T12AuditNeverWritten] T12 审计报告未写盘
- **日期**: 2026-11-04
- **状态**: resolved (2026-11-05 triage: 补写落盘逻辑 + 验证通过)
- **现象**: 审计脚本运行完毕但未生成报告文件。
- **Metadata**: 
- Pattern-Key: audit.output-lost
- Recurrence-Count: 1
- First-Seen: 2026-11-04
- Last-Seen: 2026-11-04

## [ERR-20261104-DuplicateGuardName] 重名 Guard 脚本导致潜在双跑\n- **日期**: 2026-11-04\n- **Status**: promoted [Promoted] (2026-11-05 → AGENTS.md「立项前先盘点存量」)\n- **现象**: 新建 `scripts/guards/data_freshness_guard.py` 时未发现根目录下已有同名脚本，导致职责重叠。\n- **Metadata**: \n- Pattern-Key: plan.duplicate-detector-creation\n- Recurrence-Count: 3\n- First-Seen: 2026-10-06\n- Last-Seen: 2026-11-04\n- Promoted: AGENTS.md (2026-11-05)
  同名不同职 → 若上线必致消费方混淆/双跑。
- **拦截**: 晚间做治理清单盘点 manifest 时发现，git mv 改名 data_lake_freshness_guard + cron 改指向。
- **规约**: 新建任何 *guard*/*checker*/*monitor* 脚本前，第一动作 `ls scripts/ | grep -i <关键词>`
  + grep 周流水线步骤清单；重名即改名，跨文件头部互相引用说明职责边界。
- **升级**: 同族第 3 次（10-06 pre_response_guard / is_trading_day 三实现 / 本次），已具备升格为
  可执行门禁条件——gate_runner --verify 已校验 manifest 内路径唯一性；补：新建 guard 类脚本的
  命名查重可纳入 script_path_guard 或未来 lint 步骤。

## ERR-20261105-BaostockProbeOrphanSpin
- **日期**: 2026-11-05
- **模式**: ops.network-probe-orphan-busy-spin（孤儿家族新变体：临时诊断脚本孤儿化 + baostock 忙等死循环）
- **经过**: 05:12/05:14 两个 `python -c`/heredoc baostock 连通性测试孤儿化（PPID=1），
  各吃 99% CPU 空转 16-18 分钟——半死 ESTABLISHED 连接上 `rs.next()` 忙等，
  3 行查询永不返回。用户指令巡检时发现并 kill。
- **根因**: ①诊断型网络探测无自身超时（依赖外层 wrapper，wrapper 被杀后 python 孤儿继续转）；
  ②baostock 在连接半死时 `rs.next()` 是 CPU 忙等而非阻塞等待（0% 判据失效，99% 才是它的卡死形态）。
- **修复**: kill -TERM 清理；无 cron 重生风险（62 job 全扫确认）。
- **规约**: ①任何数据源连通性诊断必须自带硬超时（signal.alarm 或 subprocess timeout），
  禁止裸 `python -c` 探测；②baostock 卡死形态是 99% CPU 忙等（区别于编排器 0% 等待），
  巡检判据按进程区分；③网络源诊断会话结束后 `pgrep -fl baostock` 清场；
  ④data_source_sentry.probe_baostock 为进程内探测，存在同款挂死风险（agentTurn 超时兜底较慢），
  建议后续加 per-probe 硬超时（daemon thread + join timeout）。
- **计数**: 孤儿进程家族第 3 例（SIGKILL 不级联 / 10-26 PPO 复扫孤儿 / 本次诊断探测孤儿）。

## ERR-20261105-CommitSweptStagedIndex
- **日期**: 2026-11-05
- **模式**: git.targeted-commit-swept-prestaged（定向提交被索引连坐）
- **经过**: `git add send_telegram.py && git commit` 意图只提交 1 个文件，
  实际提交 13 个——索引里残留先前（他会话）已暂存的 12 个运行时数据文件删除
  （execution_audit/trade_intents/sentry_state/heartbeat_dashboard 等）被一并带入。
- **根因**: `git commit` 提交的是整个索引，不是「刚 add 的那个文件」；
  本仓多会话并行 + 150+ 未提交项，索引状态不可假设干净。
- **修复**: 无需回滚（均为可再生/历史产物，git 历史可恢复，功能无损）；如实披露。
- **规约**: 多会话工作区做定向提交前，先 `git status --porcelain` 看已暂存区（第一列非空行）；
  有残留时用 `git commit -- <paths>`（路径限定）或 `git restore --staged` 清场后再提交。
- **计数**: RC=1。

## ERR-20261105-RuntimeMigrationSplitBrain
- **日期**: 2026-11-05
- **模式**: refactor.path-migration-multiarg-join-puncture（批量路径迁移第 5 变体：字符串 sed 漏掉多参 os.path.join 与 DATA_DIR 派生路径）
- **经过**: 下午执行「执行态数据统一迁移 data/runtime/」：mv 18 类目录 + 字符串替换 23 个脚本。
  验证用「字符串含 data/xxx」扫描自判通过，但实际漏掉两类形态：①`os.path.join(WORKSPACE, "data", "xxx")`
  多参常量（字符串里没有 `data/xxx` 连写）；②`DATA_DIR = os.path.join(WORKSPACE, "data")` 后用
  f-string 拼文件名的脚本（20+ 处，无法字符串级枚举）。结果 14:13 迁移后，15:40/16:15/17:00/18:30
  各 cron 仍写旧路径 → data/ 下 8+ 文件被重建，与 data/runtime/ 形成双写分叉（portfolio_sim_state /
  equity_mirror / zhonglian_state 等实时状态双侧并存）。
- **根因**: ①迁移验证 = 字符串 grep 自判，不是「全量写点枚举」（应按写点审计：所有 open('w')/json.dump
  的最终路径集合）；②在活跃 cron 持续写入的白天做目录迁移，无写入冻结窗口；③10-31 教训
  「文本正则定位=必被击穿」第 5 次实证（本次换皮为「路径替换」而非「代码插入」）。
- **修复**: ①rsync --ignore-existing 把 runtime 独有文件合并回 data/（冲突保留 data/ 较新版本）；
  ②data/runtime 原目录移入 backups/rollback_runtime_20261105/（可恢复，未 rm）；
  ③建符号链接 `data/runtime → .`（兼容层：新路径 data/runtime/xxx 与旧路径 data/xxx 解析到同一文件，
  23 个已改脚本零回改即恢复一致）；④snapshot_guard include 恢复 'data/' 原口径；⑤gitignore 加 data/runtime。
- **规约**: ①目录级路径迁移禁止字符串 sed——正确姿势 = 先枚举全仓写点（AST 扫 open('w')/json.dump/os.path.join
  聚合），产出「迁移清单→逐点确认→冻结窗口内一次切换→写点核对」；②多参 os.path.join 与 DATA_DIR 派生
  路径是字符串扫描的必然盲区，只能 AST/运行时 trace 兜住；③路径统一问题应在 SQLite 状态层落地时以
  单一路径模块（analysis/paths.py SSOT）收口，不再散点迁移；④符号链接兼容层是廉价可靠的过渡手段
  （新旧拼写同 inode），可在冻结窗口不可得时先行止血。
- **计数**: 批量文本 codemod 家族 RC=5（10-31 四例 + 本例）；目录迁移在活跃写入期执行 = 首例。

## ERR-20261106-E2ERegressionAfterRuntimeMigration（e2e 资产被同日生产变更击穿，回归验证缺位）
- **日期**: 2026-11-06
- **模式**: test.e2e-regression-after-prod-path-change（假 ok 家族旁支：验收清单声称「可重复跑」，次日实测双双失败）
- **经过**: 11-05 14:16 固化的 auto-executor 双 e2e（单元版+全流程版）当时全绿；同日下午执行 data/runtime 路径迁移 + 030 市场条件变化后，两版均未重跑。11-06 06:45 用户要求复核时实测：单元版沙箱只建 data/trade_intents、auto_executor.INTENTS_DIR 已改 data/runtime/trade_intents → load_pending_intents 读空 AssertionError；全流程版同款路径分裂 + FatalRiskDetector.check() 走真实市场条件返回 HIGH_RISK → TESTB 买入被 override 成持有 → Day1 排队断言假失败（环境依赖型 flaky）。
- **根因**: ①生产代码路径变更后无「重跑全部既有 e2e 资产」步骤——11-05 迁移验证只看了生产链路，没把测试沙箱视为受影响面；②e2e 沙箱与生产路径结构强耦合（符号链接/目录布局），却无任何机制在布局变更时提醒；③全流程版依赖真实市场风控状态（030），同一测试在不同市场日结果不同，违反「固化资产必须确定性」。
- **修复**: commit da488a6——两版沙箱复刻 data/runtime→data 符号链接；全流程版 stub RiskGuard.refresh 固定中性风控（确定性）；last_signal_date 断言更新为管道化契约。实测双双回绿，生产 sha256 零变化。
- **规约**: ①改生产路径/目录布局/模块导入结构的当天，必须重跑全部既有 e2e/test 资产（列清单执行，不是「想起来再跑」）；②e2e 固化资产禁止依赖真实市场状态（030/行情/日历），环境依赖输入一律 stub 固定值；③沙箱必须显式复刻生产的关键布局约定（符号链接），并在 e2e 文件头注释里声明该耦合，改布局的人才会想起同步。
- **计数**: 假 ok 家族第 6 变体（回归验证缺位型首例）。

## ERR-20261108-DollarQResetByCommandSubstitution
- **Date**: 2026-11-08
- **Symptom**: 批量验证 5 个门禁脚本时写 `python3 $g >/dev/null 2>&1; echo "$(basename $g): exit=$?"`，cron_truth_guard 实际 exit 1（c768b322 历史缺口）但输出显示 `exit=0`，差点误判「全绿」。
- **Root cause**: `$(basename $g)` 命令替换先于 `$?` 展开求值，命令替换的退出码（0）覆盖了 python3 的真实退出码。
- **Fix**: 退出码先落变量再拼输出：`rc=$?; echo "$(basename $g): exit=$rc"`；或每条命令退出码重定向落文件后单独读。
- **规约**: 凡 `echo "...$?..."` 内含命令替换 `$(...)`，`$?` 一定是替换命令的退出码，不是前一条的。退出码验证禁止与命令替换同表达式。
- **计数**: 假 ok 家族第 7 变体（退出码读取被污染型首例；同族：`cmd | tail; $?` 取的是 tail）。

## ERR-20261108-KBDedupeHookPrefixBlindspot
- **Date**: 2026-11-08（同族第 3 次：10-30 粘性重复 / 11-01 存量 182 组 / 11-08 增量 96 组 → 达成 RC≥3 结构性修复）
- **Symptom**: 11-01 清 182 组存根后一周又积 96 组同串重复；周六 0ae 监测到但 warn-only，无人 --apply。
- **Root cause**: ①news_monitor.py KB 写入防重钩子按行前缀 `startswith("•")` 圈覆盖面，简报 4 种行形态（统计行/• 内容行/`**股(码)**:` 分组头/`    - 标题` dash 子行）只覆盖 1 种 → 个股新闻速览 dash 行零防重；②seen_titles 增量状态仅存 2000 条，旧闻滑出 LRU 后再当「新增」（东财按相关度推旧文）；③kb_append_guard 有效但未接入 cron 自动写入路径（工具存在、流程未绑定=三层防线缺第 2 层）。
- **Fix**: commit b28af5b——news_monitor 钩子 v2 复用 kb_append_guard.strip_candidate 全块处理（单一口径，覆盖全部标题形态+空组头清理+块内去重）；variant_merge .bak 改写前无条件刷新（陈旧快照 bug 连带修复）。
- **规约**: ①防重/过滤钩子按行前缀圈覆盖面时，必须先枚举全部写入形态（枚举表进钩子注释）；②「增量状态(LRU)+高频抓取源」组合必须有落盘侧第二道防重，不能只信抓取侧状态；③防线工具（守卫）建好后必须绑定到**全部**写入路径，只绑手工路径 = cron 自动路径裸奔。
- **Status**: resolved (2026-11-08, source+pipeline 双执法；下交易日 cron 自然验证 + 周六 0ae 存量复核)

## ERR-20261112-GatewayThinkingFieldFamilyContract
- **日期**: 2026-11-12 | **RC**: 2（04:00-08:35 跨 6+ runs；同日 openrouter 清理共用根因排查链）| **状态**: resolved
- **签名**: LLM request rejected: "thinking" is not supported on /v1/chat/completions... Use "reasoning_effort"（unsupported_parameter），仅 cron/automation 派发路径触发，交互轮（会话级 reasoning=off 钉扎）不触发。
- **根因**: OpenClaw 2026.11.3 对 DeepSeek-V4/MiMo 族模型在 openai-completions 自定义路由上强制注入 `thinking:{type:"disabled"}`（off 也不例外，Z.AI 系原生合同）；amd-radeon 上游拒收该字段。两层：①thinkingDefault=off 启动期加载（/restart 解决）②字段发射层按模型族合同（compat.thinkingFormat 豁免）。
- **修法**: model defs compat 追加 `"thinkingFormat": "openrouter"`（注入器跳过 + sanitizer 删字段）。**热加载生效，无需重启。**
- **教训**: ①「配置加载了 ≠ 参数不发了」——排查注入类问题必须分清「解析层」（何时决定值）与「发射层」（按 provider/model 合同决定发什么字段），dist 源码 grep 载荷赋值点（`payload.thinking =`）是最短路径；②cron/automation 派发不带会话模型钉扎（session-events 走 config 默认解析），交互轮正常 ≠ 定时任务正常，升级后验收必须含一次 force-run；③auth profile 冷却状态跨重启持久化，链上候选跳过原因要看 `candidate_failed` 而非只看 provider-error。

## [ERROR] 竞价产物路径双源分裂（2026-11-12 发现）
- **现象**: cron_health_check 每日报「竞价快照/竞价裁决 缺失」两连 critical；下游 emotion_reversal 报 `auction_file_missing: true`（11-10/11/12）。
- **根因1**: commit e9e96db（11-08，data/runtime 迁移）只改了 auction_feed/auction_analyze/auction_signal_push 的文档字符串，实际 `output_dir`/`AUCTION_DIR` 仍写 `data/auction` —— 迁移"改了注释没改代码"。
- **根因2**: cron_health_check 的 PRODUCT_ARTIFACT_CHECKS 却按 `data/runtime/auction/` 检查 → 每日必报假告警，且真缺失被噪声掩盖。
- **根因3**: auction-feed-0915 cron 状态 error，诊断 `[ERROR] 当前时间 09:30 已过竞价窗口，拒绝运行` —— 任务实际 09:30 才启动（应为 09:15），feed 的硬截止门直接自杀，快照根本没落盘。
- **修复**: 三脚本路径改回 data/runtime/auction（+ feed 内置旧目录一次性迁移，不覆盖）；health check 检查路径同步；本日存量 101 个文件已迁入。
- **教训**: 路径类批量迁移必须 grep **赋值的常量**（`X = ...`），不能只信文件头 docstring；迁移后跑一次产物存在性校验。cron 启动延迟 > 采集窗口属结构性缺陷，需 `lastRunAtMs - scheduledTime` 监控，而非只看 status。

## ERR-20261113-StopGatewayScriptSelfKill
- 模式: ops.stop-gateway-script-killed-with-gateway
- 2026-11-13 两连发: secrets apply 脚本经 Gateway 后台 exec 运行，bootout Gateway 时脚本被同进程组 SIGKILL（nohup 无效，日志戛然而止于排空循环）；换 launchd 一次性任务后第二轮踩 launchd 无用户 PATH（env: node: No such file or directory, rc=127）
- 规约: ①凡需 stop/restart Gateway 的运维脚本，必须以 launchd 一次性任务（RunAtLoad=true）运行，禁止经 exec/nohup 后台跑；②launchd 任务脚本头部必须显式 export PATH（/usr/local/bin + node bin）；③停服后用 pgrep+lsof 双判据轮询排空 state 库锁（孤儿子进程 ~20s 释放）
- RC=2 (v1 自杀 + v3 PATH)，若复发升 RC=3 固化门禁

## ERR-20261114-ModuleLevelPathOperatorImportCrash
- 模式: llm.py-compile-pass-but-semantics-wrong（第 5 变体：模块级语句 import 才执行）
- 现象: commit b1828fd（11-14 多日轨迹）写 `HIST_PATH = UNI_DIR / "score_dist_history.jsonl"`，但 UNI_DIR 是 str（os.path.join）→ 模块级 TypeError。py_compile 静默通过；若非合成单测里 import 触发，周一 15:35 的 L3 cron 将整模块崩溃（打分+种子库全下线）。
- 根因: py_compile 只编译不执行——模块级语句（路径拼接/常量运算）只有 import 时才跑。10-31 家族（f-string 内插 helper/语法止但语义错）的新成员：这次是「语法完全正确、执行必炸」。
- 修复: os.path.join 改写 + import 冒烟测试（python -c "import analysis.signal_scoring"）纳入变更自检。
- 规约: 凡新增/修改**模块级常量与语句**，自检必须跑一次真实 import（py_compile 不够）；与 09-16「逐文件 import 冒烟测试」教训合并执行。
- RC=1（生产未炸，单测拦截），与 10-31 家族合并计数

## ERR-20261115-AutoExecutorConsumeLegSilentDead
- **日期**: 2026-11-15 | **RC**: 1（首发即修复，但「0/2 纪律未执行」告警被当噪声连看 6 个交易日）| **状态**: resolved
- **签名**: trade_intents 意向永远 PENDING、6 个交易日零成交、卖出止损未执行；trades_audit.jsonl 全部条目 guards.auto_exec=None。
- **根因**: consume_pending_for_symbol 的数据新鲜度闸 `df.index[-1].strftime("%Y-%m-%d")` 对生产 df（整数索引+date 列，baostock 原始格式）必抛 AttributeError → 静默早退（print 只进 cron stdout 无人消费）。e2e 用合成 DatetimeIndex df 掩盖契约错配。
- **修法**: 镜像 portfolio_core:1748「date 列优先、DatetimeIndex 兜底」，fail-closed；commit 8ac008b。
- **教训**: ①executor 类 e2e 必须加「真实 fetch_data + BACKTEST_MODE + 三文件隔离」探针层——合成数据测逻辑，真实数据测契约（09-15「回测复刻≠生产」同族第 2 犯，本次变体=e2e 合成输入掩盖生产数据契约）；②「打印到 stdout 的失败」在 command 型 cron 里等于静默——关键失败路径要么写审计字段（guards.auto_exec）要么落日志文件；③执行审计雏形连日报警 6 天无人处置 = 观察层有洞，周一审计挂钩判决日历后此类告警应有开庭出口。
