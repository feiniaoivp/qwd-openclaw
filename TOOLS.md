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
- **中文财经查询兜底口径（2026-10-27 用户拍板）**：Prismfy 对中文财经/股票类查询长期命中率低（10-24~26 连续三日 5 试 4 空 1 限流）。每问题最多尝试 2 次（首次 + 1 次换措辞/引擎），仍空/限流 → 立即声明 `Prismfy query used: ... (空结果/限流)` 并转内置 web_search，禁止第三次重试。细则见 prismfy-search-guide.md「No Silent Fallback」。

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
- **exec shell 选择事实（11-05 源码+文档坐实）**：无 per-call shell 参数、无 tools.exec.shell 配置键——shell 只跟网关进程 `SHELL` 环境变量走（fish 特例偏好 bash）；封装构造单点 = `dist/bash-tools.exec-runtime-*.js` 的 `buildSnapshotWrappedCommand`（snapshot source + runtimeEnvRestoreScript（含 export PATH）+ `eval '命令'`）——issue 建议的修复点正落在 runtimeEnvRestoreScript。本地要整类消灭只能改网关 SHELL→bash（影响面大：zsh-isms 会断，未采不推荐）。**用户交互 zsh 同样 multios ON**（~/.zshrc 未设 no_multios），交互式管道+heredoc 同样会拼接，是否加 `setopt no_multios` 待用户拍板。
- **multios 全仓审计（11-05）**：input 侧管道+heredoc（*.sh/*.py）与 output 侧 `>f1 |` 同 fd 双重定向均无踩雷点（唯一命中 `2>/dev/null |` = stderr 单 fd，免疫；另 scipy 位运算误报）。同类禁令仍以上方红线为准。
