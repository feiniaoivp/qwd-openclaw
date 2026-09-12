# Cron 伪造风险审计报告 (2026-09-13)

> 背景：power-overseas 集群被发现 LLM agentTurn + write 权限导致编造数据落盘。
> 本报告扫描全部 35 个 cron，按"能否编造数据"给出风险分级与处置建议。

## 风险原理

**LLM agentTurn + `write`/`apply_patch` 权限 + 要求 LLM 自产内容的 prompt = 伪造数据温床**

三个要素叠加才危险：
1. payload 是 agentTurn（LLM 会"思考"）
2. toolsAllow 含 write/apply_patch/edit（能落盘）
3. prompt 要求 LLM 产出数字/结论/报告（有编造动机）

纯脚本运行者（"cd ... && python3 script.py"，无自产内容）虽也有 write 权限，但 LLM 只需转发脚本输出，风险低。

---

## 分级结果 (35 个 cron)

### 🔴 HIGH — 要求 LLM 自产内容 + 有写权限（需改造）

| 任务 | 自产信号 | 风险点 | 建议 |
|------|----------|--------|------|
| `memory-maintenance-check` | 蒸馏+web_search | 51 个工具含 web_search/web_fetch + write，能从外部拉内容后写进 MEMORY.md | **已加固**：移除 web_search/web_fetch 等非必需工具，限制为记忆维护所需集合 |
| `daily-news-reading-push` | 整理成 | LLM 把新闻"整理成"推送文本，曾可能夸大/编造 | **已加固**：移除 write/edit/apply_patch，prompt 明令不得编造、只能引用脚本报告 |

### 🟡 MED — LLM 转发脚本输出（伪造概率较低，但有空间）

| 任务 | 说明 |
|------|------|
| `obsidian-notes-to-investment-philosophy` | 工具集无 write（靠 exec 落盘），写入的是**真实笔记抽取**结果，风险可控 💡 |
| `skill-version-watcher` | 解析 ontology 输出后判断，判断环节可能出错 |
| `intraday-alert-watch` / `-pm` | 脚本已自含 TG 推送，LLM 多余 |
| `market-context-generate` | 命令 + inline python，实为纯脚本 |
| `fib-tracking-daily` | 要求 LLM"原样宣读 stdout"，明示不得添加 → 相对安全 |
| `power-overseas-backtest-monthly` | 跑回测脚本，输出可能被 LLM 美化 |
| `Memory Dreaming Promotion` / `Dreaming Daily Report` | 系统内置 memory-core 任务，工具集为空 |

### 🟢 LOW — 纯脚本运行（低风险，维持现状）

`test-arbitration-logic`、`daily-premarket-analysis-cmd`、`test-pipeline-integration`、
`zhonglian-monitor-morning-cmd`、`zhonglian-monitor-noon`、`zhonglian-monitor-close`、
`auction-analyze-0926`、`daily-supervision-review`、`weekly-review-trading`、`weekly-backtest-pipeline`

---

## 已处置 (2026-09-13)

| 任务 | 处置 |
|------|------|
| `power-overseas-eod` | ✅ LLM→command 模式 |
| `power-overseas-intraday` | ✅ LLM→command 模式 |
| `sector-overseas-commodity-fx` | ✅ LLM→command 模式 |
| `sector-overseas-weekly-review` | ✅ LLM→command 模式 |
| `power-overseas-weekly` | ✅ LLM→command 模式 |
| `sector-overseas-tender-monitor` | ✅ 停用（伪招标源） |
| `sector-overseas-revenue-tracker` | ✅ 停用（编造营收） |

## 待处置

- `daily-news-reading-push`、`obsidian-notes-to-investment-philosophy`：
  建议改为脚本渲染 + LLM 只转发，或明确要求"不得添加脚本未包含的数字"。
- `memory-maintenance-check`：保留 LLM 能力（记忆维护需判断），但建议限制写入范围。
