# 上游报告草稿：zsh 5.9 管道非首级段 heredoc stdin 拼接（macOS 15.8.2 自带 zsh 实证）

> 状态：草稿，待用户批准后提交（外部动作需 /approve）。
> 目标：① OpenClaw GitHub issue（exec 工具在 zsh 下继承此行为，建议加告警/文档/规避）；② 可选转投 zsh-workers。
> 取证时间：2026-10-31 18:46~19:3x GMT+8，机器 iMac（macOS 15.8.2 x64，/bin/zsh 5.9 x86_64-apple-darwin24）。

---

## 一、定性结论（一句话）

**在这台机器的 zsh 5.9 上，管道中「非首级」命令段的 heredoc/here-string 不会覆盖隐式管道 stdin，而是与上游管道输出按序拼接后一并送入该命令的 stdin**——违反 POSIX/bash 一贯语义（显式 stdin 重定向应覆盖隐式管道）。bash 与 /bin/sh 同场景行为正确。

## 二、OpenClaw 侧的关联（为什么值得给 OpenClaw 提 issue）

- OpenClaw exec 工具在该环境实际执行形态（ps 取证）：
  `/bin/zsh -f -c "if [ -r <shell-snapshot> ]; then . <shell-snapshot>; fi; export PATH=…; eval '<原命令>'"`
  （shQuote 包裹、内含真换行；命令文本经比对无损，snapshot 前置与 eval 包裹均非成因——已实测排除。）
- 因此 agent 写 `openclaw cron list --json | python3 - <<'EOF' … EOF` 时：
  python 的 stdin = cron JSON 输出 ⊕ heredoc 脚本体 → JSON 被当 Python 源码执行 → 在首个裸 `true` 处
  `NameError: name 'true' is not defined` → exec 报「Exec failed」。
- 实际影响：2026-10-31 当天 6 次心跳监控误报（03:12/05:12/07:00/09:11/11:43/14:41），均为该模式触发的假故障噪声。

## 三、最小复现（不依赖 OpenClaw）

```zsh
# zsh（错误行为）：
% printf 'PIPE-CONTENT' | cat <<'EOF'
HEREDOC-CONTENT
EOF
PIPE-CONTENTHEREDOC-CONTENT      # ← 实际输出（两段拼接）

# bash / sh（正确行为，POSIX 语义）：
% printf 'PIPE-CONTENT' | cat <<'EOF'
HEREDOC-CONTENT
EOF
HEREDOC-CONTENT                  # ← 显式重定向覆盖隐式管道
```

解释器-from-stdin 场景危害示例（本日事故形态）：

```zsh
% echo '{"enabled": true}' | python3 - <<'EOF'
print("hello")
EOF
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
NameError: name 'true' is not defined. Did you mean: 'True'?
# python 把管道 JSON 当源码执行，heredoc 脚本体排在 JSON 之后从未运行
```

## 四、对照矩阵（11 探针，subprocess 精确 argv 控制）

| # | 场景 | zsh 5.9 实测 | bash 实测 | 期望（POSIX） |
|---|------|-------------|-----------|---------------|
| 1 | `printf P \| cat <<EOF`（-c 直接多行） | P⊕body ❌ | body ✅ | body |
| 2 | 前导 `export PATH=…` 行 + 同命令 | P⊕body ❌ | — | body |
| 3 | `eval '…'` 包裹（复刻 exec 形态） | P⊕body ❌ | body ✅ | body |
| 4 | zsh/bash **脚本文件**模式 | P⊕body ❌ | body ✅ | body |
| 5 | 三级管道，heredoc 挂末级 | P⊕body ❌ | — | body |
| 6 | heredoc 挂**中间级** `printf P \| cat <<EOF \| cat` | P⊕body ❌ | — | body |
| 7 | heredoc 挂**首级** `cat <<EOF \| cat` | body ✅ | — | body |
| 8 | 独立 heredoc（无管道） | body ✅ | body ✅ | body |
| 9 | 上游输出为空 `printf '' \| cat <<EOF` | body ✅（证明拼接而非重复） | — | body |
| 10 | here-string `printf P \| cat <<< 'H'` | P⊕H ❌ | — | H |
| 11 | `/bin/sh -c` | — | body ✅ | body |

（⊕ = 按序字节拼接，无分隔符；heredoc body 始终作为**数据**到达，`echo SHOULD-NOT-RUN` 探针证实 body 不被二次解析执行。）

## 五、已排除的成因

- ❌ OpenClaw exec 命令改写：ps 取证 + python subprocess 逐字段重放 exec 的确切 argv（snapshot 前置 + eval 包裹 + 真换行）→ 行为与裸 zsh 完全一致。
- ❌ eval 语义：M4（bash + eval）正确；zsh 无 eval 同样中招。
- ❌ `-c` 模式特有：zsh 脚本文件模式同样中招（V1）。
- ❌ 解释器探测/改写：`cat` 纯探针同样中招（P1）。
- ❌ 脱敏/引号损坏：body 原样到达（V6）。

## 六、检索确认

- Prismfy 2 查（brave/google）+ 内置 web_search（tavily）：未发现同现象的已知报告/修复。
- zsh 版本：`zsh 5.9 (x86_64-apple-darwin24.0)`（macOS 自带；本机无 Homebrew zsh 可对照，Apple 补丁 vs 上游未能排除——报告时注明）。

## 七、建议（OpenClaw issue 正文用）

1. **文档/告警**：exec 工具在 shell=zsh 下，检测到「管道 + 非首级段 heredoc」组合时打 warning（bash-tools 已有 heredoc 审批探测代码路径，可挂同一解析器）。
2. **规避**：对含 heredoc 的命令可选改用 bash 兼容执行（bash -c），或在文档中明示「heredoc 只能挂管道首级或独立使用」。
3. **上游**：建议同时向 zsh 上游（zsh-workers / zsh-users mirror）报告——附上第三节最小复现与第四节矩阵；OpenClaw 侧仅是继承者。


---

## 2026-11-05 终版：上游回音 + exec 形态实测 + issue 评论

**zsh-workers 线程（归档）**：原信 55234 → Stephane Chazelas 55235（"That's as documented"，MULTIOS 默认开启的文档化设计；`set +o multios` 恢复 POSIX；本人 .zshrc 也关掉）→ Daniel Shahaf 55237（附议；zsh 不承诺解释任意 bash 脚本，引 53892）。文档：zsh.sourceforge.io/Doc/Release/Redirection.html#Multios。

**exec 同款封装实测（/tmp/multios_probe.py，subprocess 精确 argv，zsh 5.9 Apple build）**：

| # | 场景 | 结果 |
|---|------|------|
| 1 | 复现：zsh -f -c + 前置行 + eval 包裹 管道+heredoc | 拼接 ❌（与 exec 现场一致） |
| 2 | `setopt nomultios;` 在 eval 外 | 仅 body ✅ |
| 3 | `set +o multios;` 在 eval 外 | 仅 body ✅ |
| 4 | `setopt nomultios;` 在 eval 内（命令首行） | 仅 body ✅ |
| 5 | `printf P \| cat <&- <<< H`（Chazelas 单点修法） | 仅 H ✅ |
| 6/7 | bash -c / /bin/sh -c 对照 | 仅 body ✅ |

**结论**：10-31 初版「违反 POSIX」定性撤回；行为 = multios 文档化设计。OpenClaw issue #189332 已补终版评论（2026-11-05，comment 6660103120，/approve 后发出）：建议 exec 前置加 `setopt nomultios`，或 heredoc 命令归一化到 bash，或文档警示。本仓实操规约（先落盘再读）不变。

## 2026-11-05 22:2x 深究补遗：exec shell 选择机制 + 全仓 multios 审计

**exec shell 选择（docs/tools/exec.md + 源码）**：非 Windows 主机 exec 跟随网关进程 `SHELL`（fish 特例偏好 bash）；**无 per-call shell 参数、无 tools.exec.shell 配置键**（`config get tools` 仅 profile/web.search；551 行的 `terminal.shell` 属 Control UI 终端，无关）。整类消灭的唯一本地途径 = 网关 SHELL→bash，影响面大（zsh-isms 断、快照行为差异），未采。

**封装构造单点（dist/bash-tools.exec-runtime-u4DiNcL4.js）**：`buildSnapshotWrappedCommand(command, snapshotPath, runtimeEnvRestoreScript)` = `[if -r snapshot: source; runtimeEnvRestoreScript(含 export PATH=OPENCLAW_PREPEND_PATH…), eval shQuote(command)].join("\n")`；zsh 快照捕获参数 `["-f","-i","-c"]`。issue #189332 评论建议「preamble 加 setopt nomultios」正落在 runtimeEnvRestoreScript，单点一行可修，评论表述无需修正。

**全仓审计（双重定向两类火药桶）**：①input 侧 `| cmd <<EOF`（*.sh/*.py）零命中（scipy `flags << 8` 位运算误报）；②output 侧 `cmd >f1 | cmd2` 同 fd 双重定向零命中——5 个 `> x |` 命中全为 `2>/dev/null |`（stderr 单 fd，multios 不触发）。仓库在 multios 语义下无静默行为差异点。

**交互态提示**：~/.zshrc 无 multios 设置 → 用户本人交互 zsh 同样 multios ON，终端里 `cmd | python3 - <<EOF` 会同样拼接；如需交互侧免疫，可考虑 .zshrc 加 `setopt no_multios`（改 shell rc 属请示项，待拍板）。
