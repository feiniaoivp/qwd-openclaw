# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

## 📁 工作区目录约束 (System Rules)

> 以下规则在每次启动时自动注入，无需重复告知。

### 默认操作根目录

| 目录 | 路径 | 用途 |
|------|------|------|
| **工作区** | `/Users/duguke/.openclaw/workspace` | 代码、分析脚本、日报、回测报告 |
| **下载目录** | `/Users/duguke/Downloads` | 临时下载的文件（压缩包、PDF、图片等） |
| **项目目录** | `/Users/duguke/Projects` | 开发项目（当前基本为空） |

### 操作原则

1. **先列后动** — 涉及文件分类、重命名、删除或批量移动前，先列出待处理文件清单，让用户确认后再执行。
2. **结果归位** — 整理数据（CSV/JSON/PDF 提炼）时，结果优先保存到工作区的 `analysis/` 或 `data/` 子目录。
3. **优先相对路径** — 工作区内操作使用相对路径（如 `analysis/close_scan_v2.py` 而非全路径）。跨目录（Downloads↔workspace）使用绝对路径。
4. **先读后写** — 修改已有文件前先读取当前内容确认。
5. **Git 操作** — 通过 exec 而非额外 MCP 运行（工作区已是 Git 仓库）。

### 诊断快捷命令

```bash
# 检查 MCP 连接
openclaw mcp doctor --probe

# 热重载 MCP 配置
openclaw mcp reload

# 查看完整 MCP 状态
openclaw mcp status --verbose
```

---

## OpenClaw Gateway 服务配置（版本锁定 @2026.7.1-2）

**服务由 launchd 管理**，三特性：开机自启 / 崩溃自动重启 / 锁定版本。

| 配置项 | 文件 | 说明 |
|--------|------|------|
| LaunchAgent plist | `~/Library/LaunchAgents/ai.openclaw.gateway.plist` | 服务定义 |
| 锁定版本备份 | `backups/openclaw-service/ai.openclaw.gateway.plist.v2026.7.1-2.bak` | 备份（与源一致） |
| 一键恢复脚本 | `backups/openclaw-service/restore-openclaw-2026.7.1-2.sh` | 见下方用法 |

**版本如何被锁定**：plist 的 ProgramArguments 指向 pnpm store 的绝对版本路径：
```
~/Library/pnpm/store/v11/links/@/openclaw/2026.7.1-2/70f3754.../dist/index.js
```
路径硬编码了 `2026.7.1-2` 版本号 + content-hash。pnpm 按版本隔离，升级会新建新版目录、**不会动旧目录**，因此服务始终启动 2026.7.1-2，不会跳版本。

**⚠️ 升级后想回到旧版本（2026.7.1-2）怎么办：**
1. 如果升级/`daemon 重装`覆盖了 plist 指向新版本，运行一键恢复：
   ```bash
   bash ~/.openclaw/workspace/backups/openclaw-service/restore-openclaw-2026.7.1-2.sh
   ```
   它会：停服务 → 备份当前 plist → 用 .bak 覆盖 → 重载 launchd → 验证版本。
2. 若 pnpm store 里旧版本目录已被清理（`pnpm store prune`），先重装该版本：
   ```bash
   npm i -g openclaw@2026.7.1-2
   ```
   再跑上面的恢复脚本。
3. 手动核对当前启动版本：`openclaw gateway status`（看 Command 路径里的版本号）。

**升级前建议**：先 `cp ~/Library/LaunchAgents/ai.openclaw.gateway.plist backups/openclaw-service/` 手动再存一份当前备份。

---

## 🔴 强制代码模板：Python 脚本的 sys.path 样板（必须照抄）

> **为什么强制**：2026-09-19 踩坑（本项目**第二次**踩同类坑，上次 08-23）。
> 脚本模式下 `sys.path[0]` 是脚本所在目录而非工作区根，导致
> `from analysis.xxx import ...` 抛 `ModuleNotFoundError`；若该异常又被
> `except Exception` 吞掉，会表现为"数据全空/功能静默失效"，**极难排查**
> （`python3 -c` 导入方式因为 cwd 在 sys.path 里，表现完全不同）。

### ✅ 每个可直接执行的脚本（`analysis/*.py`、`scripts/*.py`）顶部必须有：

```python
#!/usr/bin/env python3
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
# 必须同时插入：工作区根 + analysis/。
# - 工作区根：让 `from analysis.xxx import ...` 在**脚本模式**下可解析
# - analysis/：让 `from data_layer.xxx import ...` 等子包内引用可解析
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
```

### ❌ 禁止写法

```python
# ❌ 只插 analysis/ -> `from analysis.xxx` 在脚本模式下崩
sys.path.insert(0, "/Users/duguke/.openclaw/workspace/analysis")

# ❌ 依赖 PYTHONPATH -> 只在交互 shell 生效，cron/子进程不继承

# ❌ 依赖 cwd -> `python3 -c` 能跑、`python3 script.py` 崩，行为不一致
```

### 🧩 自检命令（新建/改完脚本后必跑）

```bash
# 1) 脚本模式导入自检（最接近 cron 的真实执行方式）
cd /Users/duguke/.openclaw/workspace/scripts && python3 -c "import importlib,sys; sys.path.insert(0,'.'); importlib.import_module('myscript')"

# 2) 批量扫描缺样板的脚本
cd /Users/duguke/.openclaw/workspace && for f in analysis/*.py scripts/*.py; do \
  grep -q "from analysis\.\|import analysis\." "$f" 2>/dev/null && ! grep -q "sys.path" "$f" && echo "缺样板: $f"; done
```

### ⚠️ 配套铁律

*   **`except Exception` 里不要静默**。至少要 `log.warning`；关键数据获取路径的
    异常必须可见（本次 bug 就是因为只走 `log.debug` 而隐身 4 天+）。
*   **不要缓存空值**。拉网失败返回的空序列若落盘，会把瞬时故障永久固化成
    "数据不足"（本次 T+5 连续返回 None 的直接原因）。

---

## 🔍 Prismfy 搜索（正确用法 2026-09-29 更新）

- **命令入口：`~/.prismfy/bin/prismfy-search`**（会自动加载 `~/.prismfy/.env`）
- ❌ 不要用 `skills/prismfy-search/search.sh`：旧版脚本不加载 .env，会误报 "Invalid API key"
- Key 存放：`~/.prismfy/.env` 的 `PRISMFY_API_KEY`（权限 600）
- 自检：`~/.prismfy/bin/prismfy-search --quota`
- 套餐：FREE 1000 次/月
- **有效 flag 仅**：`--engine / --engines / --time / --domain / --page / --lang / --quota / --raw`；**无 `--count`**（那是内置 web_search/Brave 的参数，混用必报 `Unknown option: --count`，10-16 实测 2 次查询全灭）——控制条数用 `--raw` 出 JSON 后自行截取 [来源: prismfy-search --help, .learnings/ERRORS.md ERR-20261016-C02]

---

Add whatever helps you do your job. This is your cheat sheet.

---

## 🔴 关注池口径规则（单一事实来源）

> **为什么强制**：2026-09-27 发现「代码已是 35 只、文档还写 27 只」的静默不一致，
> 人工按旧清单误判「回测范围有污染」。按 AGENTS.md「教训→三层防线」固化为门禁。

### 单一事实来源（SSOT）

**关注池以 `analysis/fib_extension_scan.py` 的 `WATCHLIST` 为准（当前 36 只）。**
任何其他位置不得自行维护股票列表。

必须与 WATCHLIST 保持一致的 6 处：

| 位置 | 形式 |
|---|---|
| `data/adaptive_strategy_map.json` | key 集合 == WATCHLIST |
| `analysis_summary_all.md` | 股票集合 == WATCHLIST |
| `memory/watchlist.md` | 头部「权威清单 N 只」== len(WATCHLIST) |
| `MEMORY.md` | `Definitive - N stocks` == len(WATCHLIST) |
| `analysis/adaptive_dual.py` STOCKS | 由 WATCHLIST 派生（禁止硬编码 6 位代码清单，2026-10-16 对齐） |
| `analysis/backtest_strategies.py` STOCKS | 由 WATCHLIST 派生（调参池源头，2026-10-17 对齐；曾硬编码 35 只缺翰宇导致 tune/diff 三口径分叉） |
| `analysis/news_monitor.py` STOCKS + `data/news/daily_news_reader.py` WATCHLIST | 由 WATCHLIST 派生（2026-10-20 对齐：曾硬编码 27/30 只，9 只池内股新闻从未抓取；已纳入 strategy_map_guard E/E2 检查） |

> 注：`analysis/adaptive_trader.py` 的 STOCKS（27 只）为**有意的核心子集**（无幽灵票），不在此列；若其语义变更需另行评估。

### 涉及股票列表的脚本必须引用 WATCHLIST

```python
# ✅ 正确：单一来源
from analysis.fib_extension_scan import WATCHLIST
stocks = list(WATCHLIST.keys())

# ❌ 禁止：硬编码/凭记忆拼凑列表（会随关注池变更而静默过时）
stocks = [("600030", "中信证券"), ...]
```

### 门禁（自动校验）

```bash
# 单独跑
python3 scripts/strategy_map_guard.py        # 违规 exit 1
python3 scripts/strategy_map_guard.py --selftest   # E 检查对抗自测（6 样例，exit 0/1）
# 已接入：每周六 weekly_full_pipeline.py 步骤 0c + 周一 memory-maintenance-monday 审计
```

### 变更关注池时的标准流程

1. 改 `fib_extension_scan.py` 的 `WATCHLIST`（唯一改动点）
2. 跑 `python3 scripts/fill_missing_stock_csv.py` 确保 `stock_data` CSV 齐全（若有新增）
3. 跑 `python3 analyze_all_stocks.py` 重生成 `analysis_summary_all.md`
4. 更新 `memory/watchlist.md` 分层 + `MEMORY.md` 清单与只数声明
5. 跑 `python3 scripts/strategy_map_guard.py` 确认退出码 0

---

## 🔴 记忆 promote 区口径（2026-10-05 源码坐实，正式采纳）

**MEMORY.md 的 `## Promoted From Short-Term Memory` 区 = 易失缓冲，不是长期存储。**

根因（已读插件 dist 源码坐实）：memory-core 的 `compactMemoryForBudget()` 默认预算
`DEFAULT_MEMORY_FILE_MAX_CHARS=1e4`（1万字符）；人写区已 ~117KB 远超预算 → 每次 promote 写入前
按日期升序丢弃最老 promote 区块，且 journal（`memory/.dreams/events.jsonl`）**无 dropped 字段**，
66 次全报 success。无法通过配置止血：全部 2 处调用点均不传 `memoryFileMaxChars`，插件 schema 无此键。

**行为规范**：需要长期保留的教训 → 必须人工/会话写入 MEMORY.md 人写区（Lessons Learned 等，
无条件保留）。发现重要 promote 条目时，顺手将其精华并入人写区，不要指望 promote 区隔日还在。

**门禁**：`scripts/promotion_guard.py`（退出码 0/1；棘轮：连续 ≥2 次 promote 零存活才 exit 1，
避免被设计误伤）已接入周流水线步骤 0g（失败仅告警不阻断）与周一审计 job 第 6 项
（2026-10-05 用户 /approve 后完成，tag `guard/20261005-promotion-guard-landed`）。

**上游反馈**（已提交 openclaw/openclaw#165245，2026-10-05）：
`memory.promotion.applied` 事件应记录 `droppedDates`/`budgetChars`，否则预算淘汰永远不可观测。

---

## 🔴 交易日判定口径规则（单一事实来源，2026-10-01 立项）

> **为什么强制**：2026-10-01 一天内 3 个文件踩同一个坑（`trade_date` 列是 date 对象、字符串匹配永假 → 节假日误放行/交易日误判非交易日），根因是 `is_trading_day` 三份实现互不复用。

### 单一事实来源（SSOT）

**交易日判定以 `analysis/service.py` 的 `is_trading_day(check_date=None)` 为准**（支持 str/date 输入与任意日期，异常可见告警）。

当前已收敛 6 处：close_scan_v2 / verify_market_data（薄包装） / auction_feed / premarket_pipeline / signal_audit / trader_stock_picks。

**唯一例外**：`scripts/data_freshness_guard.py` 保留本地静态 HOLIDAYS 表（freshness 门禁不能依赖网络，有意离线设计）——新离线脚本如需同样例外，须注明理由。

### 涉及交易日判定的脚本必须引用 SSOT

```python
# ✅ 正确：单一来源
from analysis.service import is_trading_day
if not is_trading_day(): ...

# ❌ 禁止：自持日历实现（会随时间静默过时，复现同款坑）
def is_trading_day(): ...
```

---

## 🔴 交互态市场数据引用红线（2026-09-28 新增，零容忍）

> **背景**：2026-09-28 早盘，我在未跑任何验证工具的情况下，编造了「9/27 成交额 1.45 万亿、北向 8129 亿」两个关键数字，且无法给出原始出处。这是典型的「先写后验」事故，违反了 [2026-09-16] 教训固化的三层防线。现有流水线门禁（`verify_market_data.py`）仅覆盖 cron 任务，**不覆盖聊天时的即时输出**，故须在行为层加红线。

### 绝对禁止（零容忍）

| 禁止行为 | 违规示例 | 正确做法 |
|---|---|---|
| **未经验证即引用市场统计** | "今天成交额 1.5 万亿" | 先跑工具拿到真实文件，再引用 |
| **凭记忆/印象写数字** | "我记得北向昨天买了 8000 亿" | "数据未获取，需实时查询" |
| **二手引用不溯源** | "据东财数据显示..."（未给出接口/字段/时间戳） | 追溯到授权源（交易所/东财/新浪/akshare/baostock/close_scan_v2）并给出文件路径+行号 |
| **裸数字无来源标注** | "沪指涨 0.5%" | "沪指涨 0.5% [来源: close_scan_v2.py 输出, analysis/daily/2026-09-28_dual.md:12]" |
| **引用非交易日数据** | "9/27（周六）成交额..." | 先 `is_trading_day()` 确认交易日 |

### 强制执行步骤（每次引用市场统计前**必须**按序执行）

```bash
# 0. 【发送前自检，2026-10-06 补全】回复成稿后、发送前，对全文跑守卫（exit 1 = 禁止发送，修正后重跑）
bash scripts/pre_response_check.sh "<即将发送的全文>"
# 或 cat 草稿文件 | python3 scripts/pre_response_guard.py --stdin

# 1. 确认交易日
python3 -c "from analysis.close_scan_v2 import is_trading_day; print(is_trading_day())"

# 2. 跑真实库存（唯一权威数据源）
python3 analysis/close_scan_v2.py 2>&1 | tee /tmp/scan.log

# 3. 读取 cron 已生成的最新日报（而非自己臆造）
cat analysis/daily/$(date +%F)_dual.md
cat analysis/daily/$(date +%F)_adaptive.md

# 4. 或跑验证器（对已有报告/文本）
python3 scripts/verify_market_data.py --report <文件路径>
# 单点验证：
python3 scripts/verify_market_data.py --date 2026-09-27 --metric 成交额 --value 1.45万亿 --source 东方财富
```

### 引用格式强制规范

> **模板**：`指标 数值 [来源: <授权源key>, <文件路径>:<行号/表格行>]`

```markdown
# ✅ 合规
中信证券 26.29 (-1.87%) [来源: close_scan_v2, analysis/daily/2026-09-28_dual.md:45]
北向资金净买入 120 亿 [来源: eastmoney_dc, akshare.stock_market_fund_flow() 2026-09-27]
上证指数 3150.25 [来源: sina_hq, hq.sinajs.cn 实时行情 2026-09-29 09:30]

# ❌ 违规（无来源/来源模糊/非授权源）
中信证券 26.29 (-1.87%)
北向资金净买入 120 亿（据报道）
成交额 1.45 万亿（记忆）
```

### 违规自罚机制

1. **发现即纠错**：在下一条回复中公开标注 ❌、引用原文、说明根因、给出修正版
2. **连续 2 次违规**：请求用户 `/approve` 临时禁用我的「市场分析/简报撰写」权限 24 小时
3. **流水线双重保险**：`weekly_full_pipeline.py` 步骤 0d 已接入 `verify_market_data.py`，定时任务产出的日报若含裸数字将直接报红

### 授权数据源速查表（仅限这些）

| key | 名称 | 典型字段 | 调用方式 |
|---|---|---|---|
| `sse` | 上交所官网 | 成交金额、上证指数 | `web_fetch` 官网公告 |
| `szse` | 深交所官网 | 成交金额、深证成指 | `web_fetch` 官网公告 |
| `eastmoney_dc` | 东财数据中心 | 北向资金、板块涨幅 | `akshare.stock_market_fund_flow` / `web_fetch` |
| `sina_hq` | 新浪实时行情 | 实时价格、涨跌幅、成交额 | `requests GET hq.sinajs.cn + Referer` |
| `akshare_hist` | akshare 历史K线 | 收盘价、成交量、成交额 | `ak.stock_zh_a_hist(adjust='qfq')` |
| `baostock` | baostock 历史K线 | close、volume、amount | `bs.query_history_k_data_plus(adjustflag='2')` |
| `close_scan_v2` | 本地扫描输出 | 全关注池价格/涨跌/技术指标 | `python3 analysis/close_scan_v2.py` → `analysis/daily/{date}_dual.md` |

---

## Related

- [Agent workspace](/concepts/agent-workspace)

---

## Proactive Tool Use

- Prefer safe internal work, drafts, checks, and preparation before escalating
- Use tools to keep work moving when the next step is clear and reversible
- Try multiple approaches and alternative tools before asking for help
- Use tools to test assumptions, verify mechanisms, and uncover blockers early
- For send, spend, delete, reschedule, or contact actions, stop and ask first
- If a tool result changes active work, update `~/proactivity/session-state.md`

---

## 🔴 长跑 pipeline 卡死判定规则（2026-10-07 由 .learnings 提升）

> **为什么强制**：`ops.silent-orchestrator-misjudged-as-hang` 复发 3 次（weekly_full_pipeline 09-26、premarket_pipeline 10-06）——静默编排器（subprocess `capture_output=True`）运行中零输出是**正常设计**，被误判卡死而误杀。

**判定长跑任务是否真卡死，禁止只看 stdout：**
1. `pgrep -P <pid>` 看子阶段进程是否活跃
2. 看产出缓存文件 mtime 是否在更新（如 `data/pipeline_cache/*.json`）
3. 对照各阶段 timeout 估算总时长再耐心等待

**铁律：永远不要对疑似卡死的长跑任务直接 `kill -9`；先用上述三项确认阶段进展。**

---

## 🔴 cron job 创建/修改交付口径（2026-10-18 由 .learnings 提升）

> **为什么强制**：`cron.mcp-add-delivery-override` 跨 2 个入口复发 3 次（10-06 `cron edit --command` 隐式改 announce；10-17 `cron add` 显式传 `delivery:{"mode":"none"}` 仍回写 announce）——只要 add/edit 后不复核，job 每次运行都会 announce 无目标 → 被误标 error。

**`cron add` / `cron update` 后必须复核 `delivery.mode`：**
1. add/edit 完成后立即 `cron get <jobId>`；
2. 脚本自推、不面向聊天投递的 job 必须为 `mode: none`；
3. 不符就用完整 UUID 再 `update` 一次，**response 复核为 none 才算建好**（不能假设传入参数原样落库）。

Source: .learnings/ERRORS.md#ERR-20261017-C01

---

## 🐞 Python 调试落实（python-debugpy 技能，2026-10-11）

**三种调试器按场景选最小够用的：**

| 场景 | 用法 |
|------|------|
| 本地可复现、能改源码 | 源码插 `breakpoint()`（调完必删，见下方门禁） |
| 崩溃有 traceback、不改代码 | `python3 -m pdb -c continue script.py`（直落事故现场） |
| cron/已运行/长跑进程 | `python3 -m debugpy --listen 127.0.0.1:5678 --pid <pid>` |

### 1. cron 脚本禁止交互调试 → 事后验尸（SSOT: `analysis/debug_pm.py`）

```python
from analysis.debug_pm import pm_dump
try:
    run()
except Exception:
    pm_dump("close_scan")   # traceback + 各帧局部变量 → /tmp/openclaw_pm/，stderr 提示
    raise
```

自检：`python3 analysis/debug_pm.py`（注入样例异常并打印落盘内容）。
产出目录可用环境变量 `PM_DUMP_DIR` 覆盖。

### 2. 手动复现 cron 环境（debugpy，仅手动，禁止留在 cron job 里）

```bash
# 用与 cron 完全相同的命令手动起，另一终端 attach：
python3 -m debugpy --listen 127.0.0.1:5678 --wait-for-client analysis/close_scan_v2.py
# 只看已运行进程的栈：
python3 -m debugpy --listen 127.0.0.1:5678 --pid <pid>
```

铁律：先按「长跑 pipeline 卡死判定规则」确认真卡死再 attach，禁止 kill -9。

### 3. 提交前调试残留门禁（SSOT: `scripts/debug_residue_guard.py`，已接入周流水线步骤 0i）

```bash
python3 scripts/debug_residue_guard.py            # 🔴 HIGH（breakpoint/pdb.set_trace/debugpy.listen）→ exit 1
python3 scripts/debug_residue_guard.py --selftest # 对抗性自测（9 例）
rg -n 'breakpoint\(|pdb\.set_trace|debugpy\.' --type py   # 人工快速版
```

注意：注释/docstring 里的提及不算残留（AST 扫描不误报）；`import pdb`/`post_mortem` 仅 WARN 报告不阻断。

## 🌐 Telegram 推送网络故障兜底（2026-10-14 坐实）

- api.telegram.org 直连会被 TLS 拦截（curl 60 证书域名不匹配 / SSL_ERROR_SYSCALL / curl_cffi 报 invalid library）。
- 本机 `127.0.0.1:7890` 有 HTTP 代理可用：`HTTP_PROXY=http://127.0.0.1:7890 HTTPS_PROXY=... python3 ...` 即恢复。
- cron 脚本（send_telegram.py）✅ 2026-10-16 起已内置代理兜底：直连失败 → 自动探测并走 `127.0.0.1:7890`，脚本层自愈（10-15 前的「不带代理静默失败」已修复）。Gateway announce 层仍无代理（fallbackUsed 只是目标解析回退，非传输层），TLS 潮汐窗内 gateway 直接投递仍会 delivered=false（best-effort job 降级 diagnostic，不误标 error）。补推方法：用落盘的 `data/intraday_alert_output.json` 里 `brief` 直接调 `send_message`，不要重跑扫描/不要重复发送。

---

## 🔴 网络调用超时铁律（2026-10-19 由 err.dist-io-no-timeout 提升）

> **为什么强制**：`analysis/net_guard.py` 全量接线（80 个文件）后仍复发——
> 根因不是「没装护栏」，而是**护栏装不到的位置**：①第三方库内部建连早于
> `install_default_timeout()`；②resolver/框架层自带无限等待。已 4 次实证
> 表现为 `sock_recv/sock_read → internal_select` 永久挂死、CPU≈0、无产出。

**规则（三条判据）**：
1. **入口装 net_guard ≠ 全链路免疫**。被循环/回测反复调用的「刷新/取数」函数，
   必须在函数体内自带 `with network_timeout_guard(<sec>)`，不能只靠进程级默认值。
2. **超时必须有降级路径**：超时 → 用旧缓存/上一有效值 + **可见告警**（fail-safe），
   禁止静默重试或无限等待。
3. **诊断挂死用 `/usr/bin/sample <pid> 5`**（看真实栈，如 SSL 读阻塞），
   不用反复 kill 重启试探。

参考实现：`analysis/portfolio_core.py::RiskGuard.refresh()`（`network_timeout_guard(15)`，commit fc625ed）；
排查清单：`analysis/net_guard.py`、`analysis/bs_session.py`（常驻会话旧连接可能先于护栏建立）。
Source: .learnings/ERRORS.md#err.dist-io-no-timeout

---

## 🔴 派生指标缓存铁律（2026-10-18 由 ERR-20261018-C05 提升）

> **为什么强制**：chip 第 5 维复活当天实测"一切正常"（数据刚补过），但三个读写点
> 全是 `if os.path.exists(): return`、cron 里无任何刷新方。`cost_bias = close(T)/avg_cost(T0) - 1`
> 是跨时间相减的量 —— 缓存冻结后上行行情里该值被系统性推高，`−0.20` 档永不触发，
> **加分项静默失效**：不报错、分数合理、覆盖率仍 36/36。比"数据缺失"更难发现
> （缺失会被 `valid_dims` 暴露，静默错误不会）。

### 铁律

1. **"派生缓存 + 无条件命中"必须同时回答两问**：谁刷新？怎么判过期？缺一 = 定时炸弹。
2. **陈旧度基准用「同体系最后成功拉到的行情日」（如价格缓存最新日），禁止用 `date.today()`**
   —— 否则节假日会误判陈旧。
3. **同步判据不能只做两两比较**（A>=B），必须引入**绝对参照**（A>=市场基准）。
   实测反例：chip 与价格一起被截到 10-09 时，`chip>=价格` 恒真 → 36/36 全 skip，永不追。
4. **契约测试要构造正常路径永远走不到的场景**（如"两边同时落后"）。
5. 滞后按**交易日**计，复用 `analysis/service` 交易日历 SSOT。

### 参考实现

`analysis/chip_freshness.py`（判定）+ `scripts/chip_distribution_study.py --refresh`（刷新）
+ `scripts/chip_freshness_test.py`（17 项契约，周流水线 0t）。新增派生缓存照此模式接入。
