# Dreaming Daily Report — 2026-10-11（周日 · 非交易日）

> file-only 产出，无投递（job `a8c18aed` 配置 `delivery.mode=none`）。
> 03:00 dreaming 三阶段全绿，本期报告正常产出。

---

## 0. 一句话结论

**03:00 三阶段全绿（light 38 候选 staged，全部指向 10-09/10-10 近两日素材 / REM "No strong patterns surfaced"（连续 2 夜）/ deep `Repaired recall artifacts` + Ranked 9 / Promoted 9）。本期三件事：①「回捞旧物」在 light 层继续缓解（38/38 近两日）；②但 deep 排序**仍全部选中 10-06 旧物**（score 0.803–0.804），10-09/10-10 当日素材零入选——分层分歧连续第 2 期坐实（瓶颈锁定在 deep 排序层）；③**promote 预算淘汰在 24h 内二次坐实**：写 10-11 区块后 marker 总数仍恰好 = 9，昨日 10-10 区块（10 条）已整块消失 → promote 存活窗口 < 24h，人写区蒸馏仍是唯一可靠沉淀路径（本期无需新增蒸馏：10-10 当日无重大修复）。另：**10-07 / 10-08 两期报告档案缺失根因已查清**（见 §3）。**

---

## 1. 今夜 pipeline 运行（03:00 自动）

| 阶段 | 状态 | 产物 | 备注 |
|---|---|---|---|
| light | ✅ staged | `memory/dreaming/light/2026-10-11.md`（**38 候选**） | evidence 分布：`memory/2026-10-10.md` ×16 + `memory/2026-10-09.md` ×11 + `memory/2026-10-10-weekly-review.md` ×10 + `memory/2026-10-04.md` ×1 → **37/38 为近两日素材**，收当日料能力延续 |
| REM | ✅ | `memory/dreaming/rem/2026-10-11.md` | **No strong patterns surfaced**（安静期延续，连续 2 夜）；3 条 Lasting Truths 均为 10-09/10-10 素材（Obsidian 归纳 no-op、cron 批量修复、跨会话投递路径），conf 0.48 |
| deep | ✅ | `Repaired recall artifacts: rewrote recall store` + **Ranked 9 / Promoted 9** | MEMORY.md 新区块 **line 1224** `## Promoted From Short-Term Memory (2026-10-11)` |

- **promote 9 条来源全部为旧素材**：`memory/2026-10-06.md`（5 条，score 0.803–0.804：cron per-job 模型覆盖清理的完整处置链）+ `memory/2026-10-06-2235.md`（4 条，score 0.803：09-30 主力净流出 -6.77亿派发结构 + 量价三段式出货）。**10-09/10-10 当日素材一条未中**。
- **🔴 预算淘汰 24h 内二次坐实（本期硬证据）**：写 10-11 区块后，全文 `openclaw-memory-promotion` marker 总数 = **9**（恰等于新 promote 条数）；昨日报告核实过的 10-10 区块（10 条，line 1224 实存）**已整块消失**。即：新写入当夜即把「昨天刚写的整块」挤掉 → **promote 存活窗口 < 24h**（10-10 报告推测「可能 <24h」，本期确证）。「promote = 易失缓冲」口径（TOOLS.md 已采纳）无需再证。
- **`promotion_guard` exit 0**：最近一次 promote（10-11 03:00）条数=9、存活率 100%；回溯 7 次整体存活率 **18%**（仅观测）；连续零存活 0。状态落 `data/promotion_guard_state.json`（checked_at 2026-10-10T20:01Z）。
- **分层分歧连续第 2 期**：light 侧 37/38 指向近两日，deep 侧 9/9 指向 10-06 → 「回捞旧物」**瓶颈确在 deep 排序/配额层，不在召回层**（10-10 首次提出，本期复现）。

---

## 2. 系统状态（04:0x 核对，数据均有出处）

| 项 | 状态 | 出处 |
|---|---|---|
| Gateway | **2026.7.1-2** / LaunchAgent loaded / 18789 仅 loopback | `openclaw gateway status` |
| 磁盘 | `/` **4%**（11Gi/466Gi） | `df -h /` |
| 交易日 | **2026-10-11 = 周日，非交易日**；下一交易日 **2026-10-12（周一）** | `analysis/service.is_trading_day`（SSOT，实跑 False/True） |
| git | **17 项未提交**（含 6 项 `??` 未跟踪：dsr_audit×2 / validation_2026-10-10.json / weekly_pipeline_2026-10-10.json / sector_review×2，均为 10-10 周六流水线产物 + dreaming 自身写入） | `git status --short` |
| MEMORY.md | **231,053 字节**（较 10-10 的 232,252 略降，因整块淘汰） | `wc -c MEMORY.md` |
| U+FFFD 污染 | MEMORY.md / DREAMS.md / light 均 **0** | python 计数 |

**本报告未引用任何市场统计数字**（非交易日，无盘后数据）。

---

## 3. 🔍 10-07 / 10-08 报告档案缺失 — 根因查清（本期结转闭环）

10-10 报告标记的疑点「10-07/10-08 两期 daily-report 未产出」，本期经 `cron runs`（a8c18aed，89 条历史）逐条核对**已定位**：

- **10-07 03:00**：连跑 **6 次全部 error**，`FallbackSummaryError: All models failed (8)` —— **全部 8 个模型 Connection error (timeout)**（含 amd-radeon/Qwen3.8、deepseek/deepseek-chat、nvidia nemotron 双档）。属**网关/上游整体网络层超时**，非 job 配置问题 → 报告未产出。
- **10-08 03:00**：连跑 **2 次 error** 后成功 1 次（ok, 139s, DeepSeek-V4-Flash-Vision-Exp）——但该「ok」run 的 summary 显示模型**降级为纯文本回复、未真正落盘报告文件**（Vision-Exp 工具链缺陷复发：`arguments` 双层包裹导致 write/edit 失败）。
- **结论**：两期缺失 = **10-07 全面网络超时 + 10-08 Vision-Exp 工具崩溃** 的叠加，**底层 dreaming（light/rem/deep 文件）当日均正常**。10-09 起 a8c18aed 已摘除失效 per-job model/thinking 覆盖（继承 Qwen3.8-Flash-Next），10-09/10-10/10-11 连续 3 期报告正常落盘 → **该缺陷已由 10-09 cron 批量修复实质闭环**。本疑点可**销账**。

---

## 4. 核心素材与待办

### ✅ 近期闭环（light 候选背书）
- **10-10 周六流水线产物齐全但未提交**（6 项 `??`）：`analysis/backtest/2026-10-10.md`、`analysis/validation_2026-10-10.json`、`analysis/dsr_audit_2026-10-10.md/.json`、`data/weekly_pipeline_2026-10-10.json`、`analysis/sector_review/`。**DSR 精确口径（N=24，全 35 只 precise）：显著 6 / 偏弱 2 / 不显著 27**，显著名单与 10-05 同构（通富 1.0 / 国瓷 1.0 / 亿纬 0.9998 / 长电 0.9949 / 华明 0.9722 / 西电 0.9574）→ 周护栏写回后**无漂移**。
- **仲裁「调优侧样本量恒 999」bug 已修**（commit `ca262cb`）：`param_tune.py` 源头补写 `details[策略].avg_trades` + `validate_strategies.py` 消费端层间回退 → `confidence_guard` 低样本量严格阈值（trades<6 → 夏普需≥0.4）恢复生效。教训已入 MEMORY.md 人写区。
- **主模型切换落地**：`agents.defaults.model.primary` → **amd-radeon/Qwen3.8-Flash-Next**（Vision-Exp 出链）；主会话 GLM pin 已清。→ 继承默认的 cron job（含本 a8c18aed）现跑 Qwen，根治 Vision-Exp 继承生成失败类。

### 🟡 沿用（连续多期未消）
- **promote 预算淘汰治理 ①**（`memoryFileMaxChars` 止血调参）仍**未 `/approve`**；本期新增「存活窗口 <24h」硬证据。
- **git 17 项未提交**：建议下次交互时一并整理（6 项周流水线产物 + dreaming 自身写入），勿长期挂账。
- **周流水线差异榜观察点**（10-10 简报）：差异「跳过 9 只」中 8 只为电网设备票（特变/国电南瑞/许继/西电/平高/思源/华明 + 福莱特），标「一侧无策略」——下次周跑若仍复现需查 `param_eval` 旧参数侧读取路径。
- Telegram 间歇断连根因（网络层，4 job 已 best-effort 兜底）；`data/news/` 09-09~09-29 回填；`scripts/decode_gnews_link.py` 语法违规；silent_except_guard 存量；DREAMS.md 轮换。
- **周一（10-12）观察点**：五维改造 valid_dims 展示 + moneyflow 主源若恢复自动回 4/5 维；`9c483c25` memory-maintenance-thursday timeout→1800s 修复待下周四 06:00 自然调度验证。

---

## 5. 自省（本期方法论）

分层分歧已**连续 2 期**：light 稳定收近两日料（37/38），deep 稳定回捞 10-06（9/9）——两张皮互不干扰。**这不是 bug，是既有 score 偏好的产物**（旧素材经多轮 recall 累积分更高），但对「当日重要事件」的沉淀等于无效。策略维持并强化：**凡当日重大修复，04:00 报告时直接蒸馏进 MEMORY.md 人写区**（本期 10-10 无重大修复，故无需新增蒸馏；延续观察）。promote 通道对当日事件的沉淀**结构性不可依赖**（预算淘汰已两次坐实）。

本期同时完成一件结转闭环：追查 10-07/10-08 报告缺失（用 `cron runs` 定位到网络超时 + Vision-Exp 崩溃叠加），**验证了「报告 job 异常先查 runs 历史」这一方法论有效**。

---

> 本报告为文件归档，无投递。下一期 10-12 04:00（周一，交易日 — 注意盘后数据引用须走 TOOLS.md 红线流程）。
