# 逻辑审计模板（RC≥3 自动触发）

> **触发条件**：同一 RC 签名（失败模式 + 涉及因子/模块）在滚动 90 天内出现 ≥3 次，且跨 ≥2 个独立任务/脚本。
> **产出**：结构性修复工单 → `data/failure_cases/structural_fix_tickets.json` + 看板 `analysis/audit/structural_fix_tickets.md`
> **关闭标准**：工单 `status=CLOSED` + `fix_note` 填写 + 门禁脚本接入流水线（Layer 2+3 完备）

---

## 📋 审计工单字段（必填）

| 字段 | 说明 | 示例 |
|------|------|------|
| `ticket_id` | `AUDIT-YYYYMMDD-<签名slug>-<序号>` | `AUDIT-20261108-silent-except-001` |
| `trigger_date` | 触发审计的日期（YYYY-MM-DD） | `2026-11-08` |
| `rc_signature` | RC 签名：`模式名::因子/模块` | `silent_except_guard::cron_health_check` |
| `rc_count` | 滚动 90 天内出现次数 | `4` |
| `affected_tasks` | 涉及的独立任务/脚本列表（≥2） | `["cron_health_check.py", "premarket_pipeline.py", "quant_push.py"]` |
| `failure_pattern` | 失败模式一句话描述 | `except Exception 静默吞噬导致监控失效` |
| `root_cause` | 根因归属（必选其一） | `CODE_LOGIC` / `DATA_PIPELINE` / `CONFIG_DRIFT` / `EXTERNAL_DEP` / `ARCHITECTURE` |
| `root_cause_detail` | 根因详细说明（含代码定位） | `_gateway_request() 调 POST /api/cron/{action} 404，异常被 log.debug 吞掉` |
| `fix_action` | 修复动作（可多选，用 `|` 分隔） | `ADD_GUARD_SCRIPT` \| `REFACTOR_MODULE` \| `UPDATE_CONFIG` \| `ADD_TEST` \| `UPDATE_DOC` |
| `fix_detail` | 修复详细步骤 | `1) 新建 silent_except_guard.py AST 检测 2) 接入周流水线步骤 0f 3) 棘轮模式：存量仅报告、新增 exit 1` |
| `regression_test` | 回归验证方法 | `python3 scripts/silent_except_guard.py --selftest`（9 例对抗自测） |
| `guard_script` | 新增/修改的门禁脚本路径 | `scripts/silent_except_guard.py` |
| `pipeline_step` | 接入的流水线步骤 | `weekly_full_pipeline.py 步骤 0f` |
| `status` | `OPEN` / `IN_PROGRESS` / `CLOSED` | `OPEN` |
| `created_at` | ISO8601 时间戳 | `2026-11-08T14:10:00+08:00` |
| `closed_at` | 关闭时间（CLOSED 时必填） | `2026-11-10T09:30:00+08:00` |
| `fix_note` | 关闭备注（CLOSED 时必填） | `门禁已接入周流水线 0f，棘轮模式生效，自测通过` |

---

## 🔄 审计流程（自动化 + 人工复核）

1. **自动扫描**（`logic_audit_guard.py` 每周六跑）：
   - 读 `.learnings/ERRORS.md` 解析 RC 签名与计数
   - 读 `memory/*.md` 补充任务归属
   - RC≥3 且跨≥2任务 → 生成工单 JSON + 看板 MD
   - 退出码 1 = 有 OPEN/IN_PROGRESS 工单（周一审计会查）

2. **人工复核**（周一 `memory-maintenance-monday`）：
   - 打开 `analysis/audit/structural_fix_tickets.md` 逐条确认
   - 分配修复责任、设定目标日期
   - 修复完成后更新工单 `status=CLOSED` + `fix_note`

3. **门禁回环**：
   - 每个工单必须产出 **可执行门禁脚本**（Layer 2）并 **接入流水线**（Layer 3）
   - 无门禁脚本 = 不准 CLOSED（防「只写文档不落地」）

---

## 📊 看板字段（`analysis/audit/structural_fix_tickets.md`）

```markdown
# 结构性修复工单看板

| ID | 触发日期 | RC签名 | 次数 | 涉及任务 | 状态 | 门禁脚本 | 流水线步骤 | 目标关闭 |
|----|----------|--------|------|----------|------|----------|------------|----------|
| AUDIT-20261108-silent-except-001 | 2026-11-08 | silent_except_guard::cron_health_check | 4 | cron_health_check, premarket_pipeline, quant_push | OPEN | scripts/silent_except_guard.py | weekly_full_pipeline 步骤 0f | 2026-11-15 |
```

---

## 🎯 现有 RC≥3 模式（2026-11-08 基线，来自 self-improving 维护）

| RC签名 | 次数 | 跨任务数 | 状态 | 备注 |
|--------|------|----------|------|------|
| `ops.silent-orchestrator-misjudged-as-hang` | 3 | 2 | 已升级门禁 | TOOLS.md「长跑 pipeline 卡死判定规则」 |
| `cron.silent-noop-fake-ok` | 3 | 2 | 已升级门禁 | AGENTS.md「教训固化原则·配套铁律」+ `cron_noop_guard.py` |
| `llm.final-response-failure.loses-context-only-work` | 3 | 3 | 待升级 | TOOLS.md「凡有价值的 run 产出，禁止只存在于最终回复」 |
| `data.none-valued-key-attr-crash` | 3 | 3 | 待升级 | TOOLS.md「数据源返回类型必须容错」 |
| `cron.mcp-add-delivery-override` | 3 | 2 | 待升级 | TOOLS.md「cron job 创建/修改交付口径」 |

> 新模式由 `logic_audit_guard.py` 自动发现并追加。