# Heartbeat checklist

<!-- 结构化任务块：到期任务由 OpenClaw 原生强制注入 prompt（模型无权跳过）；
     无到期任务时整个 tick 直接 skip（no-tasks-due，零 API 成本）。
     last-run 时间戳存 session state（heartbeatTaskState），重启不丢。
     格式要求（2026.7.1-2 parser）：tasks: 顶格；- name: 顶格；interval/prompt 行必须缩进；
     prompt 必须单行（parser 只读该行）；间隔单位支持 m/h/d（默认 m）。 -->

tasks:
- name: state-bookkeeping
  interval: 60m
  prompt: "本 run 第一动作，立即执行（勿留到最后）：`python3 -c` 读 workspace/memory/heartbeat-state.json，把 lastChecks.heartbeat 更新为当前 unix 秒，保留其它键，写回临时文件后 os.replace 原子替换。完成后继续其它任务。"
- name: cron-health-scan
  interval: 60m
  prompt: "跑 `openclaw cron list --json > /tmp/cron_list.json 2>/dev/null` 落盘，再用 `python3 - <<'EOF'` 读 /tmp/cron_list.json 分析（🔴禁止 `openclaw cron list --json | python3 -` 管道接 heredoc：exec 层会丢 heredoc、把 JSON 输出当 Python 源码执行，必报 NameError true，2026-10-31 六次实证），报告：lastRunStatus=error 或 consecutiveErrors>0 的 job；nextRunAtMs 已过期超过 2 个间隔的 job。干净则无输出继续。禁止 kill 任何进程。"
- name: memory-vector-guard
  interval: 2h
  prompt: "调用 memory_search 工具（query 用今天的日期字符串，如 2026-10-25），检查返回中 vectorScore 是否 >0：>0 则无输出继续；=0（且 textScore 正常，即 FTS 在跑）则只返回告警文本：🔴 memory 向量层持续降级（vectorScore=0；已排除服务重启窗口态，2h 间隔仍未自愈属持续异常，2026-10-25 实验定性），恢复建议 /approve 后 openclaw gateway restart，禁止自行重启。禁止改用 corpus=sessions（本机该 corpus 必然 15s 超时，2026-10-25 定性）。"
- name: proactivity-review
  interval: 4h
  prompt: "读 ~/proactivity/heartbeat.md 与 ~/proactivity/session-state.md：复查活跃 blocker、承诺的 follow-up、超过 24h 未动的 stale work、待用户拍板的决策、到期的验证点。有状态变化则更新 session-state.md。仅在 新告警/需决策/承诺到期/结果就绪 时给用户发消息，否则静默。文件缺失按空处理继续；禁止让 shell 链尾非零退出：grep/find 零匹配、cat 缺文件都 exit 1 会被误报 Exec failed，一律 `|| true` 或 `2>/dev/null || true` 收尾。"
- name: self-improving-scan
  interval: 12h
  prompt: "读 ./skills/self-improving/heartbeat-rules.md；对照 ~/self-improving/heartbeat-state.md 检查 ~/self-improving/ 下自上次复核后有无文件变更；检查 .learnings/ERRORS.md 有无 pending 条目。复核后更新 heartbeat-state.md 标记。仅新 RC≥3 模式或未销账错误时报告。"

# Standing rules（追加到每次心跳 prompt 的常驻指令）

- state 回写由 `state-bookkeeping` 任务承担（run 开始即写，不等回复）；若该任务缺席（直接 poll 场景），在首个工具调用前补写。
- 🔴 20:52 run 实证：回复前补写会被最终轮 LLM 失败连带丢掉（idle timeout 120s → fallback 裸 HEARTBEAT_OK），禁止再依赖回复前时机。
- 疑似卡死的长跑 pipeline：先用 pgrep 子进程 + 产出缓存 mtime + 阶段 timeout 估算三招确认，禁止凭 stdout 空就直接 kill。
- 静默时段 23:00-08:00（Asia/Shanghai）：除非 urgent，不产生任何对外消息。
- 全部到期任务干净跑完 → 回复 HEARTBEAT_OK；有任何需要关注的事项 → 只返回告警文本（不要带 HEARTBEAT_OK 字样）。
- 市场数据引用前必须先跑工具拿真实文件（close_scan_v2 / cron 日报），禁止凭记忆写数字。
- 🔴 exec 红线（2026-10-31 六次 Exec failed 根因，双复现实证）：管道与 heredoc 不得挂同一命令——`cmd | python3 - <<'EOF'` 会丢 heredoc，管道输出被当 Python 源码执行，JSON 必炸 NameError true。内联脚本一律先落盘（`cmd > /tmp/x.json`）再 heredoc 读文件。
- 心跳 shell 链尾必须 exit 0：grep -c/find 零匹配、cat/sed 缺文件都会 exit 1 被误报 Exec failed 推送；用 `|| true`、`2>/dev/null || true` 收尾。
