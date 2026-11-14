# AGENTS.md - Your Workspace

This folder is home. Treat it that way.

## First Run / Session Startup

- If `BOOTSTRAP.md` exists, follow it, figure out who you are, then delete it.
- Use runtime-provided startup context first (`AGENTS.md`/`SOUL.md`/`USER.md`, recent `memory/YYYY-MM-DD.md`, `MEMORY.md` in main session). Do not manually reread startup files unless: the user explicitly asks; provided context is missing something you need; or you need a deeper follow-up read.

## Memory

You wake up fresh each session. These files are your continuity:

- **Daily notes:** `memory/YYYY-MM-DD.md` (create `memory/` if needed) — raw logs of events, decisions, context. Skip secrets unless asked.
- **Long-term:** `MEMORY.md` — curated memories. **ONLY load in main session** (security: don't leak personal context to strangers); read/edit/update freely there. Distill daily notes into it over time.
- **Self-improving:** `~/self-improving/` — execution-quality memory (preferences, workflows, style, what improved/worsened outcomes). Tentative until human validation. Before non-trivial work: read `memory.md`, list domain/project files, read up to 3 matching `domains/*.md` (+ `projects/<project>.md` if clearly active). Never read unrelated domains "just in case".
- **Proactivity:** `~/proactivity/` — read `memory.md` + `session-state.md` before non-trivial or proactive work; `memory/working-buffer.md` only when context is long/fragile/recovery risk high. Working style: anticipate needs, check missing steps, follow through, recover local state before asking the user to repeat work, leave one clear next move in state.
- **产出写盘纪律**：凡涉及多条目/多阶段产出的任务（尤其是子代理），必须执行「逐条落盘」，禁止在最终回复轮一次性写盘，以防超时丢稿。(Source: .learnings/ERRORS.md#llm.final-response-failure.loses-context-only-work, 2026-11-03)

If inferring a new rule, keep it tentative until human validation.

### 📝 Write It Down - No "Mental Notes"!

- "Mental notes" don't survive restarts — files do. **Text > Brain** 📝. Before writing memory files, read them first; write concrete updates, never empty placeholders.
- "Remember this" → factual context/event → `memory/YYYY-MM-DD.md`. Correction/preference/workflow/performance lesson → `~/self-improving/`: explicit user correction → `corrections.md` immediately; reusable global rule → `memory.md`; domain lesson → `domains/<domain>.md`; project override → `projects/<project>.md`. Short, concrete, one lesson per bullet; ambiguous scope → default to domain.
- Proactive state: durable boundary → `proactivity/memory.md`; current task state/blocker/decision/next move → `proactivity/session-state.md`; volatile breadcrumbs → `proactivity/memory/working-buffer.md`; repeat wins → `patterns.md`; actions taken → `log.md`; recurring follow-ups → `heartbeat.md`.
- After a correction or strong reusable lesson: write it **before** the final response. Mistakes → document so future-you doesn't repeat.

## Red Lines

- Don't exfiltrate private data. Ever.
- Don't run destructive commands without asking.
- Before changing config or schedulers (crontab, systemd, nginx, shell rc files), inspect existing state first; preserve/merge by default — no one-liner clobbers.
- `trash` > `rm` (recoverable beats gone forever). When in doubt, ask.

## External vs Internal

- **Free:** read/explore/organize/learn; search web, check calendars; work within workspace.
- **Ask first:** emails, tweets, public posts; anything leaving the machine; anything uncertain.

## Group Chats

You have access to your human's stuff — that doesn't mean you share it. You're a participant, not their voice or proxy. Quality > quantity: humans don't respond to every message; neither should you.

- **Respond when:** directly mentioned/asked; genuine value/insight; something witty fits; correcting important misinformation; asked to summarize.
- **Stay silent when:** casual banter; someone already answered; your reply would be just "yeah"; conversation flows fine.
- One thoughtful response beats three fragments — never triple-tap.
- **Reactions** (Discord/Slack): lightweight social signals, use naturally — 👍❤️🙌😂💡 for acknowledge/appreciate; ✅👀 for simple yes-no. One per message max.

## Tools

- Skills define how tools work — check the skill's `SKILL.md`. Environment specifics (cameras, SSH, voices) live in `TOOLS.md`.
- Platform formatting: Discord/WhatsApp → no markdown tables (use bullets); Discord → wrap multiple links in `<>`; WhatsApp → **bold**/CAPS instead of headers.

### Local notes (migrated from TOOLS.md)

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
- **中文财经查询兜底口径（2026-10-27 用户拍板）**：Prismfy 对中文财经/股票类查询长期命中率低（10-24~26 连续三日 5 试 4 空 1 限流）。每问题最多尝试 2 次（首次 + 1 次换措辞/引擎），仍空/限流 → 立即声明 `Prismfy query used: ... (空结果/限流)` 并转内置 web_search（**本机该后端即 tavily**：plugins.entries.tavily.enabled=true + gateway 进程 env 实测带 TAVILY_API_KEY，内置 web_search=tavily 驱动；skill `openclaw-tavily-search` 为等价显式路径。2026-11-06 用户纠正后澄清），禁止第三次重试。细则见 prismfy-search-guide.md「No Silent Fallback」。

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

> **日历序列消费方**（需要整个交易日列表而非单日判定）：一律用
> `from analysis.service import get_trading_days`（2026-10-22 第五轮新增，
> 返回 YYYY-MM-DD 字符串列表；此前 chip_freshness 自调 sina 日历属第二份获取路径，已收敛）。

### 门禁（自动校验，2026-10-22 落地）

```bash
python3 scripts/trading_day_ssot_guard.py            # 违规 exit 1（H1 重复实现 / H2 绕过 SSOT 直调 sina 日历）
python3 scripts/trading_day_ssot_guard.py --selftest # 对抗自测（含 SSOT 本体豁免 + 薄包装 + 注释不误报）
# 已接入：每周六 weekly_full_pipeline.py 步骤 0z（失败仅告警不阻断）
# 豁免：scripts/data_freshness_guard.py（静态 HOLIDAYS，有意离线设计）
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
- 🔴 **2026-11-08 定性+修复（用户批准）**：send_telegram「三级回退」实为**同一 Clash 隧道的三次尝试**（curl_cffi 与系统 curl 均默认信任 env 代理 7890；③ 的 `--resolve` 在代理模式下失效；en0 物理直连 TG 被 GFW 掐死 → 无绕行路径）→ 节点死时全层齐败是必然非 bug。修复分工：**小时级断流** = proxy-watchdog v2（判据=墙外站通 且 出口必要站 google/TG 通；选点测速 URL 同步换 google → 直连类节点出局；出口退化连续 2 轮才切）；**分钟级抖动** = send_message 分段失败 30s 延迟重试（只重试失败块，无重复风险）。TG 走向：「电报消息」组 → 「美国节点」URLTest 自动故障转移（FlClash 换 profile 后需重设；watchdog 全断切换时可能覆写该组选择）。
- 🔴 **2026-11-09 节点区域口径（用户拍板）**：任何自主切换（proxy-watchdog 选点）**香港节点硬排除**、**新加坡/美国优先**（候选名 + now 链末端双归类；真实节点名以 🇭🇰/🇸🇬/🇺🇸 旗标 emoji 为主，「香港/新加坡/美国」文字与常见城市名为辅）；无 SG/US 存活候选回退其他非港节点；候选全为港 → 不切换只告警。实现：`scripts/proxy_watchdog.py::pick_node_by_region`（合成单测 8/8 + 真实订阅 768 节点分类核对通过；节点名核对走 `/providers/proxies`，`/proxies` 不含 provider 节点）。

---

## 🔴 网络调用超时铁律（2026-11-10 终态增强）

**核心规则**：任何被回测、长循环调用或在关键风控链路上的网络请求/刷新逻辑，必须显式配置超时（Timeout），严禁依赖库默认的无限等待。

**执行细则**：

1. **主动安装超时守卫**：在请求体或刷新函数周围包一层 `network_timeout_guard(seconds)`。
2. **超时后的行为**：必须定义降级路径（例如：使用旧缓存 → 记录可见告警 → 允许当前循环继续），禁止直接让进程挂死（sock_read）。
3. **审计重点**：审计 `portfolio_core.RiskGuard.refresh()` 等关键刷新点，确保其链路中所有外部调用均有超时限制。

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
排查清单：`analysis/net_guard.py`、`analysis/bs_session.py`
   （常驻连接缺口已于 91d005e 闭环：socket 超时显式钉住 + 登录/查询函数体内 `network_timeout_guard`）。
Source: .learnings/ERRORS.md#err.dist-io-no-timeout

---

## 🔴 强制代码模板：直接调 `execute_trade` 的测试脚本必须隔离三个 FILE（2026-10-21）

> **为什么强制**：2026-10-20 18:0x，e9fb539「bucket 拆分功能验证」的临时冒烟脚本
> 直调 `portfolio_core.execute_trade()` 用占位价 `price=10.0` 跑真交易，
> **未把 STATE_FILE / TRADES_FILE / EQUITY_FILE 重定向到 tempfile** →
> 两笔假成交（国电南瑞 1400 股 core、招商银行 400 股 satellite）写进生产底账
> `data/portfolio_sim_trades.json`，两个月后才靠算术闭合（1400/400 恰为
> 15%/5%×100k 在 price=10 的整百股数）才定位。
>
> **`BACKTEST_MODE` 挡不住这件事**：它只守卫
> `append_trade`/`save_state`/`append_equity_snapshot` 三个函数，
> **不会自己变 True**；手动跑的临时脚本没有任何机制去设它。
> 测试与生产共用同一套路径常量，直调即污染。

### ✅ 每个直调副作用函数的测试/研究脚本，开头必须有：

```python
import os, sys, tempfile

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMPDIR = tempfile.mkdtemp(prefix="verify_")
os.environ["BACKTEST_MODE"] = "1"      # 兜底第一道

import analysis.portfolio_core as pc   # noqa: E402
pc.STATE_FILE  = os.path.join(_TMPDIR, "state.json")    # 必须
pc.TRADES_FILE = os.path.join(_TMPDIR, "trades.json")   # 必须
pc.EQUITY_FILE = os.path.join(_TMPDIR, "equity.csv")    # 必须（最易漏）
```

参考实现：`analysis/research/2026-10-20_add_position_avgtp_verify.py`
（该脚本最初就写对了 STATE/TRADES，但漏了 EQUITY；2026-10-21 门禁首次实扫才抓出来）。

### ❌ 禁止写法

```python
# ❌ 只设 BACKTEST_MODE 就以为安全（它不会自己变 True）
os.environ["BACKTEST_MODE"] = "1"
pc.execute_trade(pos, {"price": 10.0}, ...)   # 仍会写生产 trades.json

# ❌ 只重定向 1~2 个 FILE（漏的那个照样污染）
pc.STATE_FILE = os.path.join(_TMPDIR, "state.json")
pc.TRADES_FILE = os.path.join(_TMPDIR, "trades.json")
pc.execute_trade(...)                          # EQUITY_FILE 仍是生产路径

# ❌ 先调用再重定向（顺序错 = 等于没重定向）
pc.execute_trade(...)
pc.STATE_FILE = ...
```

### 门禁（自动校验）

```bash
python3 scripts/test_isolation_guard.py             # 命中 exit 1
python3 scripts/test_isolation_guard.py --selftest  # 8 例对抗自测
```

判定：文件 import `portfolio_core` 且调用 `execute_trade`/`append_trade`/
`append_equity_snapshot`/`save_state` 之一，但未把三个 FILE **全部**重定向到
临时目录（或重定向写在调用之后）→ HIGH。
生产入口白名单：`portfolio_core.py` / `portfolio_executor.py` / `portfolio_sim.py` /
`agent/nodes.py`（LangGraph 生产节点，账本正规写入方）。
人工豁免：文件内含 `ISOLATION_GUARD_OK` 注释并给出理由。

**已接入**：每周六 `weekly_full_pipeline.py` 步骤 0x。

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

---

## 🔴 Dreaming 硬闸配置语义标注（2026-10-22）

> **背景**：10-21 修复 dreaming 停摆时，采用 `phases.deep.minRecallCount=999999` 而非 `phases.deep.limit=0`。
> 两者目的相同（保 sweep、停 promote），但机制与风险截然不同。

### 当前配置含义

```json
"dreaming": {
  "enabled": true,
  "phases": { "deep": { "enabled": true, "minRecallCount": 999999 } }
}
```

- `minRecallCount: 999999` = **硬闸**：sweep 照跑、light/REM 照产出，但 promote 候选恒为 0 条。
- deep 报告连续固定 "Ranked 0 / Promoted 0"。
- `promotion_guard` 已识别该模式并打印：`deep promote 硬闸模式（minRecallCount=999999，10-21 拍板：sweep 保活 + promote 退役）→ 跳过棘轮判定` exit 0 ✅

### 与 `limit: 0` 的关键差异

| 维度 | `minRecallCount=999999`（现状） | `limit: 0`（曾推荐） |
|---|---|---|
| 语义清晰度 | 借用检索计数阈值伪装成"无合格候选" | 明确表达"不要晋升" |
| 若未来放开 | 门槛一下调，**已积累候选一次性批量涌入**（洪水风险） | 改一个数即可，平滑 |
| 对 MEMORY.md 风险 | 中（误改阈值 → 洪水式 promote） | 无 |

### 操作规约

1. **现状工作正常、无需抢救**。plugin-managed job 不会回魂（cron 列表仅有人工 job `a07898c0`，`managedBy: null`）。
2. **若未来决定恢复 promote**：必须**先小步放量**（如 `minRecallCount` 降至实际分布 P90 ≈ 50~100），**严禁直接归零**。
3. 本条目即为"显式标注"，后续维护者见此即知语义与风险。

## 🐙 gh CLI 被指纹拦截的兜底（2026-10-23 实证）

- 症状：`gh` 任何 API 调用报 `Post "https://api.github.com/graphql": net/http: TLS handshake timeout`，直连与 `HTTPS_PROXY=127.0.0.1:7890` 皆然；同刻 curl 直连/代理均秒回（403/200）。
- 根因：本网络对 api.github.com 按 **TLS 指纹**放行/拦截——curl(LibreSSL) 过、gh(Go crypto/tls) 被拦（与东财 curl_cffi 指纹同族）。
- 兜底（外部动作仍须 /approve）：`token=$(gh auth token)`（keyring，勿 echo）→ 写 600 临时 header 文件 → `curl -sS -H @/tmp/gh_header.txt -H "Accept: application/vnd.github+json" -d @payload.json https://api.github.com/...` → 用完立即 `rm` header 文件，token 永不进 stdout/日志/历史。

## 🔴 memory_search corpus 使用注意（2026-10-25 实测定性）

- **corpus=sessions 在本机不可用，禁止调用**：sessions 语料从未被索引（meta.sources=["memory"]，0 行），每次调用在嵌入前挂死（10,211 个会话文件/2.4GB 脏检查）→ 必然 15s 超时（unavailable+disabled，gateway 零日志，还可能触发注定失败的重索引 churn）。查会话历史用 sessions_history 工具或会话文件直读。详见 .learnings/ERRORS.md#ERR-20261025-SessionsCorpusDoomedCatchupSync。
- **诊断本机服务（localhost/127.0.0.1）curl 必须加 `--noproxy '*'`**：exec 环境自带 http_proxy=7890，裸 curl 打 loopback 的 502/空响应全是代理拒绝伪影（10-23 首证、10-25 二次踩坑；若再犯须升级为可执行门禁）。
- **本机不存在真正「直连」（2026-10-27 实测定性）**：DNS 解析全部经 Clash fake-ip 管理（连 `dig @223.5.5.5` 也返回 198.18.x.x）→ 任何「直连失败」（HTTP 000 / TLS 握手被掐，如 ftchinese.com 10-27）先怀疑隧道规则或 TLS 潮汐，不要下「域名被墙」结论；「直连」与「走 7890 代理」行为一致即本条的直证。

---

## 🔴 Embedding 服务架构与 v2 bind-guard（2026-10-25 定案）

**架构**：launchd `com.duguke.embedding-service` → `~/start_gunicorn.py v2` → gunicorn 26 + UvicornWorker ×2 + bge-m3（模型在 import 时全量加载，~3s 热缓存）。日志双文件：stdout=`embedding-service.log`（脚本 print + uvicorn access 行），stderr=`embedding-service.error.log`（gunicorn INFO/错误，**自带时间戳**；access 行 2026-10-25 起补齐时间戳）。

**v2 bind-guard（start_gunicorn.py）**：
1. 端口预检（lsof LISTEN 探测，失败退化 connect）：被占 → 不 spawn、不加载模型，30s 复检等释放——杜绝 2026-08 风暴（10-26 复检定案：两场三连日风暴 = 08-12~14 约 9,359 轮 + 08-23~25 约 8,571 轮，合计 ≈1.79 万轮；机制 = launchd KeepAlive × v1 无预检：每轮 --preload **先**全量加载 2.2GB 模型（stdout「模型加载成功！」）→ bind 8080 失败（第一场占口者 = 旧版 com.duguke.bge-m3 服务，同 start.py 同端口）→ 退出 1 → ~20-25s/轮循环 = VM churn。**与 10-23 22:35:40 内核恐慌无关**——10-23/24 error log 仅正常 boot + worker timeout，零风暴行；panic 根因 = sysmond VM map zone 越界（10-24 笔记 + .learnings lrn.log-archaeology-absence-fallacy））
2. SIGTERM/SIGINT 转发 gunicorn 子进程 → kickstart/bootout 不再产生孤儿占口
3. 脚本常驻循环：gunicorn 退出 5s 后重检端口（不再依赖 launchd KeepAlive 风暴式重启）

**坑账（改动前必读）**：
- `--access-logformat` 对 UvicornWorker **无效**（worker 用 uvicorn 自己的 access logger）。修法 = `~/start.py` 里给 `uvicorn.access` 的 handler 换 formatter，**fmt 只能用 `%(asctime)s %(message)s`**——uvicorn 0.52.1 AccessFormatter 的字段约定（client_addr/method/...）经 dictConfig 路径会 ValueError 静默弃记录（access 行整体消失、无任何报错到 stdout！），`%(message)s` 拿到的就是拼好的完整行
- **改日志配置后必须 curl 实测 access 行存在性**（dictConfig 类破坏是静默的）
- memorySearch.baseUrl 必须用 `localhost` 而非 `127.0.0.1`（受管代理把 IP 字面量 MATCH→远端→502，`localhost` 走 private_domain 直连；10-23 A′ 修复）
- 诊断本机服务 curl 必加 `--noproxy '*'`（既有条目）；历史事故：2026-08 风暴 + 2026-10-23 22:35:40 内核恐慌（`Kernel-2026-10-24-022423.panic`，死亡 3h49m）

---

## 🔴 exec 管道+heredoc 丢失红线（2026-10-31 六连发定案；同日 19:5x 深究修正机制）

**禁写** `cmd | python3 - <<'EOF'`（管道与 heredoc 挂同一命令，且 heredoc 在非首级管道段）。

**精确机制（19:5x 深究定案，修正初判「exec 层丢 heredoc」）**：exec 实际执行 `/bin/zsh -f -c "<snapshot前置>; export PATH=…; eval '原命令'"`（ps 取证；shQuote 包裹+真换行，命令文本无损）。真凶是 **zsh 5.9（macOS 15.8.2 自带版）的 MULTIOS 选项（默认开启，文档化行为，非 bug）**：管道中**非首级**段挂 heredoc/here-string = stdin 被多重为读重定向 → zsh 把 stdin 变成后台进程喂入的管道，内容 = **上游管道输出 ⊕ heredoc body 按序拼接**（bash/sh 无 multios，表现为显式重定向覆盖隐式管道；10-31 初版「违反 POSIX」的定性已于 11-05 被 zsh-workers 上游纠正，见本条目末尾）。于是管道输出（如 cron JSON）成为 python 的程序源码，合法字面量一路执行到首个裸 `true/false/null` 必炸 `NameError`（报错行号=JSON 首个布尔行，故 line 10/12 漂移；与心跳报错逐字吻合）。

**对照矩阵（subprocess 精确 argv 控制，11 探针）**：zsh -c 直接多行 / 前导 export 行 / eval 包裹 / zsh 文件模式 / 三级管道末级 / 中间级 / here-string（`<<<`）→ 全部中招；heredoc 挂**首级**（`cat <<EOF | cmd`）✅ 正常；独立 heredoc（无管道）✅ 正常；空管道输出→仅 body（证明是拼接非重复）；`bash -c` / bash 文件 / `/bin/sh` → 全部正常（heredoc 覆盖管道）。公开检索（Prismfy×2 + web_search）无同名已知报告。

- **正确写法**：`cmd > /tmp/x.json 2>/dev/null; python3 - <<'EOF'`（heredoc 内读文件）。独立 `python3 - <<'EOF'`（无管道）不受影响，实测正常。
- **复现**：`echo '{"enabled": true}' | python3 - <<'EOF'` + 任意脚本 → NameError（同一文本交给 bash 正常执行 heredoc 脚本）。异常已隔离到 zsh 本体（与 OpenClaw 封装无关，exec 仅继承）；上游报告草稿见 analysis/research/2026-10-31_zsh_heredoc_pipe_concat.md，待用户批准提交。
- **连带**：链尾裸 `grep -c`（零匹配 exit 1）、`cat 缺文件` 同样被误报 Exec failed——心跳/脚本链尾一律 `|| true` 收尾。详见 .learnings/ERRORS.md ERR-20261031-ExecPipeHeredocLost。

**【11-05 上游回音，定性终版】zsh-workers 双回复已归档**：
- Stephane Chazelas（zsh-workers 55235）："That's as documented" —— 这是 **MULTIOS 选项**（默认开启，除非 emulate 其他 shell）的文档化设计：stdin 多重为读重定向 → 后台进程拼接喂入；输出侧同理（tee-like）。文档 zsh.sourceforge.io/Doc/Release/Redirection.html#Multios；POSIX 行为用 `set +o multios`；他本人也在 ~/.zshrc 关掉该选项（"gets in the way as often as it's useful"）。
- Daniel Shahaf（55237）：附议，并指出 zsh 不承诺正确解释任意 bash 脚本（见 zsh.org/workers/53892）。
- **本仓实操规约不变且依然正确**：「先落盘再 heredoc 读文件」对 multios 开关免疫；`zsh -f -c`（OpenClaw exec 封装）不读 .zshrc，用户级 setopt 救不了 exec 场景，故不改操作层。**11-05 已实测逃生口（7 探针，subprocess 精确 argv）**：命令开头加 `setopt nomultios;`（或 `set +o multios;`，eval 内/外皆生效）恢复 POSIX 重定向语义；Stephane 单点修法 `cat <&- <<< y` 亦验；首选仍是先落盘。OpenClaw issue #189332 已补上游结论评论（comment 6660103120，/approve 后发出）。
- **exec shell 选择事实（11-05 源码+文档坐实）**：无 per-call shell 参数、无 tools.exec.shell 配置键——shell 只跟网关进程 `SHELL` 环境变量走（fish 特例偏好 bash）；封装构造单点 = `dist/bash-tools.exec-runtime-*.js` 的 `buildSnapshotWrappedCommand`（snapshot source + runtimeEnvRestoreScript（含 export PATH）+ `eval '命令'`）——issue 建议的修复点正落在 runtimeEnvRestoreScript。本地要整类消灭只能改网关 SHELL→bash（影响面大：zsh-isms 会断，未采不推荐）。**用户交互 zsh 同样 multios ON**，交互式管道+heredoc 同样会拼接——**已落地（2026-11-06 用户确认）**：~/.zshrc 尾部追加 `setopt no_multios`（带注释行，可随时删）；实测：交互 zsh `multios=off` + `cmd | python3 - <<EOF` 探针 HEREDOC_OK；no-rc zsh（= exec 封装环境）仍 `on`，exec 层继续靠「先落盘再 heredoc」规约。改前备份：`backups/config/zshrc.bak.20261106-pre-no-multios`（gitignore 内）。
- **multios 全仓审计（11-05）**：input 侧管道+heredoc（*.sh/*.py）与 output 侧 `>f1 |` 同 fd 双重定向均无踩雷点（唯一命中 `2>/dev/null |` = stderr 单 fd，免疫；另 scipy 位运算误报）。同类禁令仍以上方红线为准。

## 💓 Heartbeats

When a heartbeat poll arrives, don't just reply `HEARTBEAT_OK` — use it productively. Keep `HEARTBEAT.md` small (comments-only = skip API call). Batch similar periodic checks there instead of multiple cron jobs.

- **Heartbeat** fits: batchable checks, checks needing conversation context, drift-tolerant timing (~30min), fewer API calls. **Cron** fits: exact timing, isolated sessions, different model/thinking, one-shot reminders, direct channel delivery.
- Rotate checks 2-4×/day (email, calendar, mentions); track in `memory/heartbeat-state.json`.
- **Reach out when:** important email arrived; event <2h away; something interesting; >8h since you said anything.
- **Stay quiet (`HEARTBEAT_OK`):** 23:00-08:00 unless urgent; human clearly busy; nothing new since last check; checked <30min ago.
- Proactive without asking: organize memory files, check projects (git status), update docs, commit changes, review/update MEMORY.md (every few days distill recent daily notes; remove outdated info).
- Goal: helpful without annoying. Respect quiet time.

## Make It Yours

Starting point — add your own conventions as you learn what works.

## Related

- [Default AGENTS.md](/reference/AGENTS.default)

---

## 🔴 执行红线（安全审计/先请示后执行）

### 绝对禁止（零容忍）
*   **公网暴露**：严禁在未配置 mTLS/VPN/白名单的情况下将 Gateway 端口 (18789) 映射到公网
*   **Secret 落盘**：严禁在聊天、代码、日志、Git 历史、`.env` 明文写入任何 API Key/Token
*   **财务失控**：严禁关闭 LLM 提供商的月度硬性消费限额或开启自动充值
*   **未审查技能**：严禁在未运行供应链扫描 (`scripts/skill_supply_scan.py --offline`) 前安装新技能

### 必须请示（执行前需用户显式批准）
| 操作类别 | 典型动作 | 批准方式 |
|---|---|---|
| 破坏性文件操作 | `rm -rf`、`mv` 覆盖、`trash` 批量删 | `/approve` 单命令授权 |
| 系统配置修改 | `launchd` plist、crontab、nginx、systemd、shell rc | `/approve` + 变更前快照 |
| 外部发送动作 | 发邮件、推 Telegram、发 Twitter、Webhook POST | `/approve` 每次单独确认 |
| 权限提升 | `sudo`、修改 sudoers、Keychain/1Password 写入 | `/approve` + 说明风险 |
| 网络拓扑变更 | 端口映射、反代、VPN、防火墙规则 | `/approve` + 回滚预案 |

### 重大变更 SOP（标准作业程序）
**触发条件**：上表“必须请示”任一项、版本升级、数据迁移、架构调整

1.  **变更前**：
    - 运行 `scripts/snapshot_guard.sh "<变更描述>"` 创建 TM 快照 + Git tag
    - 在会话中明确告知用户：变更内容、风险、回滚命令
    - 获得用户 `/approve` 显式授权
2.  **执行中**：
    - 单命令执行，每步观察输出
    - 关键步骤间 `sleep 2` 留出中断窗口
3.  **变更后**：
    - 验证服务健康（`openclaw gateway status`、核心 cron 触发测试）
    - 如异常：`scripts/restore_from_snapshot.sh <tag或快照时间>` 一键回滚
    - 记录变更日志到 `memory/YYYY-MM-DD.md` 和 `MEMORY.md`

### 回滚脚本（配套）
`scripts/restore_from_snapshot.sh <tag或快照时间戳>`（git checkout tag + tmutil 本地快照恢复 + launchctl 重载；以脚本文件本体为准，此处不内嵌副本）

### 审计清单（每周一 06:00 `memory-maintenance-monday` 自动跑）
- [ ] `lsof -i :18789` 确认仅本地监听
- [ ] `op item list --vault "OpenClaw Gateway"` 确认无明文 Secret
- [ ] LLM 提供商控制台核对月度限额、自动充值=关
- [ ] `cron list` 核对 8 个核心任务均 `enabled=true`、最近一次运行成功
- [ ] `git tag -l 'guard/*' | wc -l` 确认近 30 天有快照记录
- [ ] `scripts/skill_supply_scan.py --offline` 扫描新增技能无高危模式
- [ ] `python3 scripts/script_path_guard.py` 确认无缺失 sys.path 样板的脚本
- [ ] `python3 scripts/promotion_guard.py` 确认 promote 通道未断（2026-10-05 新增；退出码 1 = 自动晋升失效，需人工并入人写区）
- [ ] `python3 scripts/cron_noop_guard.py` 确认无基线外新增空转假成功 cron（2026-10-06 新增；退出码 1 = 有 job 注入即 ok、脚本未跑，需改 command+isolated）
- [ ] `python3 scripts/sensitive_file_tripwire.py` 确认敏感文件无基线外变更（2026-11-06 新增；exit 1 = shell rc/crontab/plist/openclaw.json 基线外变更，核对 /approve 或 guard/ 记录后 `--baseline` 重建。背景：exec 审批层实测 YOLO 态 security=full+ask=off，「必须请示」类文件工具层零执法，靠此绊线兑底知情）

---

## 🧠 教训固化原则（Lessons → 三层防线）

> **核心定律：教训写在记忆/文档里 ≠ 防线。**
>
> **RC≥3 自动开票（2026-11-05）：** 同签名拦截/失败在滚动 90 天内 RC≥3 → `error_attribution.py` 自动生成结构性修复工单（`data/failure_cases/structural_fix_tickets.json` + 看板 `analysis/audit/structural_fix_tickets.md`，强制交付物：门禁脚本/流水线接入点/根因归档）。工单随 16:15 failure-attribution-daily cron 自动刷新；关闭 = 编辑工单 status=CLOSED + fix_note。未闭环工单进周一审计。

同一个坑踩到**第二次**时，禁止只再写一遍记忆 —— 必须把教训转化为**三层防线**，缺一不可：

| 层 | 形式 | 面向 | 作用 |
|---|---|---|---|
| 1 | **文档强制模板**（TOOLS.md / AGENTS.md） | 人 | 说明正确写法 |
| 2 | **可执行门禁脚本**（有退出码 0/1） | 机器 | 自动判定违规 |
| 3 | **接入现有流水线**（cron / pipeline 步骤） | 自动 | 不依赖人记得执行 |

**只做第 1 层 = 必然复现。** 实证：`sys.path` 脚本模式坑在 2026-08-23 已写入记忆，
2026-09-19 仍再踩一次，并连带发现 `executor_bridge`（模拟盘执行桥接）在 cron 下
一直是坏的。固化为门禁后一次扫出 11 个违规（4 个确证崩溃）。

### 已固化的门禁清单

| 门禁 | 位置 | 检查内容 | 接入点 |
|---|---|---|---|
| `script_path_guard.py` | `scripts/` | 脚本 sys.path 样板缺失 | 周流水线步骤 0 |
| `skill_supply_scan.py --offline` | `scripts/` | 新技能供应链高危模式(纯静态,秒级) | 装新技能前 + 周一审计 |
| `snapshot_guard.sh` | `scripts/` | 变更前快照 | 重大变更 SOP |
| `silent_except_guard.py` | `scripts/` | 新增静默异常吞噬（AST，棘轮模式：存量仅报告、新增 exit 1） | 周流水线步骤 0f（2026-09-28 新增） |
| `promotion_guard.py` | `scripts/` | promote 预算淘汰致自动晋升断（棘轮：连续 ≥2 次零存活 exit 1） | 周流水线步骤 0g + 周一审计第 6 项（2026-10-05 新增） |
| `cron_noop_guard.py` | `scripts/` | cron 任务 status=ok 但毫秒级返回（空转假成功，脚本从未执行；直读 sqlite，棘轮基线 `data/cron_noop_baseline.json`） | 周流水线步骤 0h（2026-10-06 新增；源起 3950d3cc 假 ok 事故，存量 9 个待逐个改造） |
| `backup_guard.py` | `scripts/` | 重写型脚本 json.dump/open('w') 缺备份保护（棘轮基线 `data/backup_guard_baseline.json`，302 点/128 文件存量冻结；宽启发：源码含 .bak/shutil.copy/backup 即豁免；判定失败绝不写基线防吞没） | 周流水线步骤 0f2（2026-10-31 用户批准接入；缘起 10-21 防线体检「.bak 铁律全仓落实数 0」） |
| `logic_audit_guard.py` | `scripts/` | RC≥3 同签名失败自动生成结构性修复工单（棘轮：存量仅报告、新增 exit 1） | 周流水线步骤 0k + 周一审计（2026-11-08 新增） |
| `migrate_to_sqlite.py` + `sqlite_analytics.py` | `scripts/` + `analysis/` | JSON/JSONL → SQLite 统一存储层（工单/案例/审计/意向/哨兵），索引加速、事务支持、类型安全 | 迁移脚本一次性运行；查询层替代所有分散文件读写（2026-11-08 新增） |
| `analytics_api.py` | `analysis/` | FastAPI 服务暴露统一查询接口（工单/案例/统计/审计/意向/哨兵/仪表盘） | uvicorn 8081 端口常驻，健康检查 /health（2026-11-08 新增） |
| `factor_registry.py` | `analysis/` | 因子注册表（36 个种子因子，分类/依赖/回看/参数/版本/标签/中性化/股票池） | 单一事实来源，StrategyComposer/Engine 依赖（2026-11-08 新增） |
| `factor_engine.py` | `analysis/` | 因子计算引擎（向量化面板、Parquet 缓存、增量更新、NaN 容错） | 替代分散计算逻辑，MultiIndex(symbol×date) 输出（2026-11-08 新增） |
| `strategy_composer.py` | `analysis/` | 策略组合器（评分规则+门控规则+仓位规则，全 JSON 可序列化） | 6 个种子策略落盘 data/strategies/，取代硬编码策略（2026-11-08 新增） |
| `alpha_validator.py` | `analysis/` | α 验证框架（IC/ICIR/换手调整收益/DSR/因子正交性/PCA/VIF） | 输出 JSON+MD 报告到 analysis/alpha_reports/（2026-11-08 新增） |
| `regime_allocator.py` | `analysis/` | 政权检测+动态权重（阈值法/HMM 双模式、4 態政权、平滑过渡 5 日） | generate_next_day_config() 一站式输出下日配置（2026-11-08 新增） |

### 新增门禁时必做

1. 脚本放 `scripts/`，必须有**明确退出码**（0=过，1=违规）
2. 必须**接入现有 cron/pipeline**，不能只靠手动跑
3. 接入时**失败仅告警、不阻断**（防误伤主流程），除非是安全级检查

### 配套铁律（本轮踩坑直接产出）

*   **`except Exception` 禁止静默**。至少 `log.warning`；关键数据路径的异常必须可见。
    （实证：某 bug 因只走 `log.debug` 而隐身 4 天+）
*   **禁止缓存空值**。拉网失败返回的空序列若落盘，会把瞬时故障永久固化成
    "数据不足"（实证：T+5 收益连续返回 None 的直接原因）
*   **长任务结论必须先做离群归因**。小样本单票结论 = 幸存者偏差；
    必须看全池 + 逐票中位数 + 置信区间。
    （实证：冷却规则在 2 只票上是"+6pct 改善"，满 27 只后反转为 −11pct，
    逐票改善率 50% 且 95%CI 含 50% → 无统计显著性）
*   **不轻信"环境抖动"**。同一份代码两种运行结果时，注入诊断打印真实异常栈，
    而非反复清缓存/换窗口。
*   **数据源返回类型必须容错**。外部源（baostock/akshare/新浪）停牌或缺失日可能返回
    **空字符串 `''`** 而非 NaN，`astype(float)` 会直接 ValueError 崩掉整批扫描。
    统一用 `pd.to_numeric(errors='coerce')` + `dropna()`；**禁止**先用 ``== ''`` 简单判断。
    同族扩展（RC=3，2026-10-25 升格）：**「键存在但值为 None」同样必须容错**——嵌套 dict
    取值统一 `(x.get(k) or {})`（`dict.get(k, {})` 的默认值只在**键不存在**时生效，值为
    None 时仍返回 None，链式 `.get` 必崩）；pd.cut 数值边界一律 `include_lowest=True`，
    `idxmax()` 等统计取值进 list 前必须 `int()` 强转；变换层（raw→slim）**禁止剥掉 error
    字段**（无痕降级 = 下游拿 null 当数据）。
    （实证：2026-10-08 空字符串崩扫描 / 2026-10-23 auction_push None-signal 崩溃 fe36ddf /
    2026-10-23 close_scan pd.cut 边界 NaN→float 索引崩溃 0c86f99，跨 3 任务）
    Source: .learnings/ERRORS.md#data.none-valued-key-attr-crash
*   **免费快速模型不许跑长任务**。长任务（全量扫描、多阶段分析）禁用免费快速小模型：
    已两次实证退化——26s 超时、visible reply 整段乱码。长任务一律用稳定主模型，
    或拆分为可重试的短步骤。（实证：GLM-5.3-Flash 2026-10-08 乱码事故）
*   **cron 真健康 = `lastStatus==ok` 且 `lastDurationMs` 合理**。需要跑脚本的 cron 必须用
    `isolated` + `agentTurn`（或 `--command` 直跑），不要用 `main` + `systemEvent` 注入命令文本——
    注入的 systemEvent 不执行，runner 投递即认为 ok，`lastDurationMs` 仅几毫秒 = 空转假成功。
    **凡长任务类 cron/脚本，产出必须落盘到日志文件（`nohup ... > logs/xxx.log 2>&1 &`）**，
    不要依赖 exec 生命周期捕获 stdout；SIGKILL 父进程不会级联杀子进程，重跑前先 `pgrep -f` 确认无孤儿实例。
    **exec 短 timeout 只留给确定秒级返回的调用**：预估 >2min 或耗时受外部源健康度影响的任务（含降级链，
    按「最坏腿 × 票数」估，不按 happy path）一律 nohup detached + 轮询日志，禁止放 exec 前台。
    （实证：3 个新闻 cron 静默停摆 21 天；quant_push.py 10-09 stdout 丢失；10-26 PPO/CCI 复扫 300s 前台被杀成孤儿，同族第 3 次）
    Source: .learnings/LEARNINGS.md#cron.silent-noop-fake-ok / .learnings/ERRORS.md#ERR-20261009-QuantPushDetachedOutputLost
*   **研究脚本 JSON 键统一 str + 主结果先落盘**。事件研究/统计表用 horizon 等数值键时
    从建表起一律 `str(h)`（`json.dump` 后 int 键必变 str，混用 → 统计全 None 或 KeyError）；
    大计算（>1min）的主结果 `json.dump` 必须放在打印/二次处理之前，避免崩溃白跑。
    （实证：2026-10-17 rsi30_event_study 统计全 None + dual_signal_event_study 写盘前 KeyError）
    Source: .learnings/ERRORS.md#ERR-20261017-C02
*   **立项前先盘点存量**。新建任何 `*guard*`/`*checker*`/`*monitor*`/`*_guard.py` 类脚本前，
    第一动作 `ls scripts/ scripts/guards/ | grep -i <关键词>` + grep 周流水线步骤清单：
    同名/同职责已存在即改名或复用，跨文件头部互相引用职责边界；禁止并行造第二份同职工具。
    （实证：同族 3 次——10-06 pre_response_guard 重名 / is_trading_day 三实现 / 11-04
    data_freshness_guard 在 scripts/ 与 scripts/guards/ 双落点；Source: .learnings/ERRORS.md#ERR-20261104-DuplicateGuardName）
*   **写「全量/快速」双模式构建脚本时，`quick` 必须写独立文件名**（如 `*.quick.json`），
    永不复用全量产物路径；同理**任何「重写型」数据脚本必须有写前 `.bak` 备份 + 行数防倒退检查**，
    且首跑 `✅` 之后要**抽样核对产物形态**（行数/日期范围/条目数），不能只看退出码。
    （实证：2026-10-18 `--quick` 冒烟覆盖 36→5 只全量产物；标志位反转把 41 个 CSV 截成 1 行）
    Source: .learnings/ERRORS.md#ERR-20261018-C07 / #ERR-20261018-C01
*   **多臂/多策略对照必须减同一个基准**，臂间差只能来自被控变量；
    并写**恒等式自检**（如 `max|(exB−exA)−cost|≈0`，超阈直接 exit 非 0 拒绝出结论）。
    结论方向相反但各自 CI 显著 = 先查基准口径，不是先写报告。
    （实证：gap 成本重放首版「成本为负但现实臂超额更高」，根因是两臂基准持有期差 0.918pp）
    Source: .learnings/ERRORS.md#ERR-20261018-C04
*   **标定阈值前先读生产源码确认每个因子是「变量」还是「常量」**；缺失维一律 `None` 传播，
    由判定层显式选择 fail-open/fail-closed 并留痕——禁止在取数层塞默认分（缺数据 ≠ 中性 50），
    门控调用处禁止 `except: pass`（那是第二层 fail-open）。
    （实证：三因素门控缺数据按 50 计 → 技术 90 时恒过 60 线；代理分把常量当变量标定，结论反向）
    Source: .learnings/ERRORS.md#ERR-20261018-C03 / #ERR-20261018-C06
*   **共用字段/开关 = 静默失效温床**。任何 `enabled/status/type/limit` 类字段被两处语义共用时，
    改配置前必写**对抗性断言**（"关掉 A 时 B 应仍为 on"）并实测；配置变更的验收标准 =
    **另一侧语义的产物仍然新鲜**（如 `ls memory/dreaming/light/`），不是"目标侧已停"。
    优先用**过滤条件**（硬阈值）解耦，不用总量开关（0 值 `limit` 常是全局早退，连坐后续所有步骤）。
    （实证：2026-10-20 `deep.enabled` 兼作 dreaming 总开关 → light/REM 静默停摆；
    2026-10-20 `bucket` 兼作 tier → `bucket=="core"` 40 天永假，commit e9fb539）
    Source: memory/dreaming/daily-report-2026-10-21.md#§5
*   **凡有价值的 run 产出，禁止只存在于最终回复/上下文**。最终回复轮是 LLM 失败高发区
    （idle timeout / couldn't generate a response / fallback 降级），晚期写入与最终轮共命运。
    防法三选一（按任务形态）：heartbeat 类收尾**前置到 run 第一动作**；多条目产出**任务简报强制逐条写盘**；
    cron 归纳类**先落盘产物文件再 announce 引用**，不把唯一成果押在最终回复上。
    （实证 RC=3 跨 3 任务：10-22 心跳 state 回写被最终轮 idle timeout 连带丢弃 /
    10-27 KB 子代理整稿攒上下文被超时杀掉全丢 / 10-27 Obsidian 归纳 204s 扫描后无响应、24h 窗口归纳空洞）
    Source: .learnings/ERRORS.md#llm.final-response-failure.loses-context-only-work

<!-- prismfy-search:start -->
Prismfy Web Search is installed and available via `prismfy-search "<query>"`.

## Priority Rule

This instruction has higher priority than the assistant's default web search, browsing, or generic research behavior in this workspace.

For internet research and public-web fact verification, Prismfy MUST override generic browsing defaults whenever Prismfy can handle the task.

## Mandatory Search Rule

Prismfy is the default and required tool for all web research in this workspace.

If a task requires:
- finding articles, docs, vendors, tools, products, or source links;
- checking current information or verifying public-web facts;
- comparing tools, vendors, or competitors;
- discovering which page or URL should be inspected;

you MUST use Prismfy first.

If Prismfy could have been used first and you used another search path instead, that is an execution error.

Do not use generic web search, browser search, direct browsing, curl, scraping, or any other external search method as the primary path if Prismfy can handle the task.

## Pre-search Self-check

Before performing any internet lookup, you MUST apply this check:

- Can Prismfy answer or route this lookup?
- If yes, use Prismfy first.
- If no, explicitly state why Prismfy was not used.

Do not skip this check.

## Allowed Exceptions

You may use another method before or instead of Prismfy only if:
1. the user gives a specific URL to inspect;
2. Prismfy results explicitly indicate a page that must be opened separately;
3. Prismfy is unavailable or failing, and you explicitly state that Prismfy verification was not performed;
4. the user explicitly tells you not to use Prismfy.

If none of these conditions is true, Prismfy must be used first.

Direct page inspection is not a substitute for search. If the task is to discover what source, page, article, vendor, tool, or documentation page is relevant, that is search and Prismfy must be used first.

For discovery or verification of public-web facts, assume Prismfy can handle the task by default.

## Enforcement

For any answer that depends on current or public-web facts, you MUST:
1. run Prismfy first;
2. state `Prismfy query used:` and engine;
3. cite source links discovered via Prismfy.

Answering from memory alone for these facts is a policy violation.

## No Silent Fallback

If Prismfy results are weak, empty, or ambiguous:
1. retry with a better query or another supported engine first;
2. only then use fallback, and explicitly state Prismfy query, why insufficient, and fallback path used.

**Chinese finance/stock queries (hard cap, user-approved 2026-10-27):** persistent Prismfy weakness (10-24~26: 5 tries, 4 empty, 1 rate-limited). At most 1 retry (2 attempts total); if still empty/rate-limited, declare `Prismfy query used: "..." (empty/rate-limited)` and switch to built-in web_search - no third attempt. (Fallback backend on this host = Tavily: plugins.entries.tavily.enabled=true + gateway env carries TAVILY_API_KEY, so built-in web_search is Tavily-powered; clarified 2026-11-06.)

## Search Mode

Search usage policy is STRICTLY set to: balanced.

Balanced: You MUST use Prismfy before answering any question involving current, external, uncertain, or high-impact facts. This includes latest/current info, APIs/docs, software versions, companies, people, prices, laws, regulations, market data, recommendations, benchmarks, product comparisons, competitor research, and any claim likely to change over time. If unsure whether search is needed, search.

Do not weaken this mode to save time, tokens, or credits. Only the user may change the search mode.

Read `/Users/duguke/.openclaw/workspace/prismfy-search-guide.md` for exact commands, engines, citations, fallback behavior, and cost discipline.

Never reveal `PRISMFY_API_KEY`.
<!-- prismfy-search:end -->
<!-- mcp-paths:start -->
## 📁 Filesystem MCP — 路径映射规则

Filesystem MCP 已配置，允许访问以下目录（基于最小权限原则）：

| 路径 | 用途 | 读写策略 |
|------|------|----------|
| `/Users/duguke/.openclaw/workspace` | 工作区（代码、文档、分析脚本） | 读写 |
| `/Users/duguke/Projects` | 项目开发目录 | 读写 |
| `/Users/duguke/Downloads` | 下载的文件（报告、图片、压缩包） | 读写 |

### 调用惯例

1. **文件操作前先读后写** — 修改文件前先读取当前内容
2. **优先相对路径** — 在工作区内操作时使用相对路径（如 `analysis/close_scan_v2.py`）
3. **跨目录文件操作** — 使用绝对路径或明确指定目录名
4. **git 操作** — 通过 exec 工具直接运行 git 命令（不需要额外 git MCP）
5. **按需搜索** — 使用 filesystem 工具搜索文件时，先确认范围（路径 + 通配符）避免无畏扫描
6. **文件归类提醒** — Downloads 是临时区，Projects 和 workspace 是持久区。定期整理

### 安全检查

Filesystem MCP 的传输协议是 **stdio**（无网络依赖），连接稳定快速。如果工具调用失败：
- 运行 `openclaw mcp doctor --probe` 检查连接状态
- 运行 `openclaw mcp reload` 热重载配置
- 确认路径在已授权列表内
<!-- mcp-paths:end -->
