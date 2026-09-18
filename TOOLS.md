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

## Related

- [Agent workspace](/concepts/agent-workspace)
