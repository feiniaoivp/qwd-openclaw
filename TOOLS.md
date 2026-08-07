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

Add whatever helps you do your job. This is your cheat sheet.

## Related

- [Agent workspace](/concepts/agent-workspace)
