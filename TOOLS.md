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

Add whatever helps you do your job. This is your cheat sheet.

---

## 🔴 关注池口径规则（单一事实来源）

> **为什么强制**：2026-09-27 发现「代码已是 35 只、文档还写 27 只」的静默不一致，
> 人工按旧清单误判「回测范围有污染」。按 AGENTS.md「教训→三层防线」固化为门禁。

### 单一事实来源（SSOT）

**关注池以 `analysis/fib_extension_scan.py` 的 `WATCHLIST` 为准（当前 35 只）。**
任何其他位置不得自行维护股票列表。

必须与 WATCHLIST 保持一致的 4 处：

| 位置 | 形式 |
|---|---|
| `data/adaptive_strategy_map.json` | key 集合 == WATCHLIST |
| `analysis_summary_all.md` | 股票集合 == WATCHLIST |
| `memory/watchlist.md` | 头部「权威清单 N 只」== len(WATCHLIST) |
| `MEMORY.md` | `Definitive - N stocks` == len(WATCHLIST) |

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
# 已接入：每周六 weekly_full_pipeline.py 步骤 0c + 周一 memory-maintenance-monday 审计
```

### 变更关注池时的标准流程

1. 改 `fib_extension_scan.py` 的 `WATCHLIST`（唯一改动点）
2. 跑 `python3 scripts/fill_missing_stock_csv.py` 确保 `stock_data` CSV 齐全（若有新增）
3. 跑 `python3 analyze_all_stocks.py` 重生成 `analysis_summary_all.md`
4. 更新 `memory/watchlist.md` 分层 + `MEMORY.md` 清单与只数声明
5. 跑 `python3 scripts/strategy_map_guard.py` 确认退出码 0

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
