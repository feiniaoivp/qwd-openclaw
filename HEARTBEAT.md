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
  prompt: "跑 `openclaw cron list --json`，报告：lastRunStatus=error 或 consecutiveErrors>0 的 job；nextRunAtMs 已过期超过 2 个间隔的 job。干净则无输出继续。禁止 kill 任何进程。"
- name: proactivity-review
  interval: 4h
  prompt: "读 ~/proactivity/heartbeat.md 与 ~/proactivity/session-state.md：复查活跃 blocker、承诺的 follow-up、超过 24h 未动的 stale work、待用户拍板的决策、到期的验证点。有状态变化则更新 session-state.md。仅在 新告警/需决策/承诺到期/结果就绪 时给用户发消息，否则静默。"
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
