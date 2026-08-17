# 更新 weekly-backtest-strategy-refresh Cron 步骤指引

## 当前 Cron 信息
- **Job ID**: `cc0ae9f1` (内部 `agent:main:cron:cc0ae9f1-027c-444b-9d7a-19d6d8b7c519`)
- **Label**: `Cron: weekly-backtest-strategy-refresh`
- **Schedule**: 周六 06:00 (Asia/Shanghai)
- **Prompt Hash**: `8c864a6bce7379d96c0d6c54dba893e3526d21ee9b6989cc4928c63759d4c598`
- **Prompt 文件**: `/Users/duguke/.openclaw/agents/main/sessions/skills-prompts/sha256/8c/8c864a6bce7379d96c0d6c54dba893e3526d21ee9b6989cc4928c63759d4c598.txt`

## 需要做的更新
在 prompt 中的步骤链里，**param_evaluate 之后、LLM 汇总之前**，插入新步骤：
```
7. 自动仓位微调 (auto_adjust_position.py)
```

## 更新方式（二选一）

### 方式一：通过 OpenClaw CLI 更新（推荐）
```bash
# 1. 先备份当前 cron 定义（可选）
openclaw cron get cc0ae9f1 > weekly_backtest_backup.json

# 2. 编辑 prompt 文件，添加第 7 步
#    编辑 /Users/duguke/.openclaw/agents/main/sessions/skills-prompts/sha256/8c/8c864a6bce7379d96c0d6c54dba893e3526d21ee9b6989cc4928c63759d4c598.txt
#    在步骤链中 param_evaluate 后插入：
#    "7. 运行 python3 analysis/auto_adjust_position.py — 读取近10日成交流水，按 ADJUSTMENT_GUIDE 微调 position_sizer.py 参数，记录 param_change_log.json"

# 3. 重新计算 prompt hash（sha256）
#    新 hash 会自动生成新文件名

# 4. 更新 cron job（需要删除旧的、创建新的，因为 prompt hash 变了）
openclaw cron remove cc0ae9f1
openclaw cron add \
  --name "weekly-backtest-strategy-refresh" \
  --schedule '{"kind": "cron", "expr": "0 6 * * 6", "tz": "Asia/Shanghai"}' \
  --payload '{"kind": "agentTurn", "message": "<更新后的完整prompt内容>", "model": "deepseek/deepseek-chat"}' \
  --delivery '{"mode": "announce", "channel": "telegram", "to": "626141741"}'
```

### 方式二：使用新建的全流水线脚本（更稳健，推荐）
将 cron 改为 `command` 类型，直接运行 `weekly_full_pipeline.py`，完全绕过 LLM 超时问题：

```bash
# 删除旧 cron
openclaw cron remove cc0ae9f1

# 创建新 command cron（无需 LLM，更稳定）
openclaw cron add \
  --name "weekly-full-pipeline" \
  --schedule '{"kind": "cron", "expr": "0 6 * * 6", "tz": "Asia/Shanghai"}' \
  --payload '{"kind": "agentTurn", "message": "请以 command 模式运行 analysis/weekly_full_pipeline.py，该脚本会顺序执行：backtest_strategies.py → validate_strategies.py --write-map → param_tune.py --write → param_signal_diff.py → param_evaluate.py --backup → param_evaluate.py → auto_adjust_position.py，全部跑完后汇总推送 Telegram。", "model": "deepseek/deepseek-chat"}' \
  --delivery '{"mode": "announce", "channel": "telegram", "to": "626141741"}'
```

> **注意**：方式二更符合 08-10 教训（结构化数据流水线类 cron 一律用 command 不走 LLM），且已创建好 `weekly_full_pipeline.py` 自动处理错误继续、汇总记录。

## 验证
更新后，下周六 06:00 自动触发，或手动触发测试：
```bash
openclaw cron run cc0ae9f1  # 或新 job id
```

## 相关文件
- `analysis/auto_adjust_position.py` — 自动微调主脚本
- `analysis/weekly_full_pipeline.py` — 全流水线编排脚本（含 7 步）
- `analysis/position_sizer.py` — 含微调配置区（ADJUSTABLE_PARAMS 等）
- `data/param_change_log.json` — 微调记录（自动生成）
- `data/weekly_pipeline_YYYY-MM-DD.json` — 每周运行记录（自动生成）
