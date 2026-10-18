# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice

---

## [LRN-20260916-IMP] Python 脚本「定义顺序」与「重复实现」两类静默缺陷

**Logged**: 2026-09-16
**Category**: best_practice
**Area**: code-maintenance

### What happened
第二轮「代码维护/清技术债」审计用 AST 扫描 + 逐文件 import 冒烟测试，一次性挖出 4 处真实缺陷：
1. `backtest_power_overseas.py` **模块级 NameError**：第 40 行 `sys.path.insert(0, os.path.join(WORKSPACE, ...))` 与第 45 行 `log.warning(...)` 都引用了**在其之后才定义**的 `WORKSPACE`(L58) / `log`(L67)，且 `Falsee` 拼写错误。整个脚本 import 即崩，从未成功跑过。
2. `challenge_review.py` **未定义名 `log`**：`_zhonglian_strat2_holding()` 的 except 分支调用 `log.warning`，但文件从未 import logging / 定义 log。仅在 `zhonglian_state.json` 读取失败时触发 → 恰好是「出错时再抛一个 NameError」，掩盖真实原因。
3. `backtest_strategies.py` **护栏统计口径错误**（未提交改动）：`compare_to_random(int(avg_wr*total_trades), total_trades, ...)` 把「平均胜率」转成整数命中数再重算比率，小样本取整把 58.9% 压成 40%，导致每个策略都被误判「❌ 未过」。且混淆了「交易胜率」与「随机基准命中率」两个不同统计对象。
4. `validate_strategies.py` **日期口径不一致**：重构后的 `fetch_long_history` 用 `df['date'] >= start`(str) 与 datetime64 列比较；另 `__main__` 解析了 `--write-map/--arbitrate` 但未传入 `main()`（重构时遗漏）。

### Do differently next time
- 批量审计用 **AST 未定义名扫描**（比 pyflakes 轻量、零依赖）覆盖 `analysis/` + `scripts/`，可秒级定位 NameError 类缺陷。
- 模块级代码（import 时执行的部分）必须保证「引用的名字已在其上方定义」；把配置/logger 定义提到最前。
- 「可选依赖 import 失败」分支里调用 logger 前，先确保 logger 已存在（否则失败时二次崩溃）。
- 回测护栏口径：**比率 vs 比率**直接对照（用 Wilson CI），不要 `int(rate*n)` 再除回去——取整会系统性低估小样本比率。
- `main()` 参数化重构后，务必同步 `__main__` 的传参，别留下「解析了却没用」的死参数。
- 中间态：同一 workspace 的未提交 diff 可能是半成品，审计时应视为「待验证假设」而非既成事实。

---

## 2026-09-29 — cron `lastStatus=ok` 可能是空转假成功（必须看 lastDurationMs）

**What happened**
3 个新闻 cron job 静默停摆 **21 天**（新闻归档断在 09-08），
但 `openclaw cron list` 一直显示 `ok`，cron 健康巡检完全没报警。

**Root cause**
这 3 个 job 是 `sessionTarget: main` + `payload.kind: systemEvent`：
只把命令文本注入长驻主会话当系统事件，**不执行**；主会话模型回合失败
（`[assistant turn failed before producing content]`）后无人消费注入事件，
静默丢弃。runner 认为投递完成即 `ok`，`lastDurationMs` 仅 **2–5ms**
（对照组正常的 isolated+agentTurn 是 **9548ms**）。

**What to do differently**
1. **判定 cron 真健康 = `lastStatus==ok` AND `lastDurationMs` 合理**。
   当 payload 需要跑脚本，若 `lastDurationMs < 100ms` → 标记"空转假成功"并告警。
2. **需要真执行命令的 cron 一律用 `isolated` + `agentTurn`（或 `--command` 直跑）**，
   不要用 `main` + `systemEvent` 来"跑脚本"。
3. 巡检脚本（`cron_health_check.py`）需新增该判据。

**Pattern-Key:** cron.silent-noop-fake-ok
- See Also: ERR-20261006-PM ops.silent-orchestrator-misjudged-as-hang (同主题：表面状态不可信,需看真实执行/产出)
**Status**: promoted (2026-10-09/2026-10-10)
**Promoted**: AGENTS.md「🧠 教训固化原则 · 配套铁律」(2026-10-09 Source: .learnings/LEARNINGS.md#cron.silent-noop-fake-ok)；2026-10-10 维护补强：cron 真健康判定 + 长任务产出落盘/孤儿进程防护
- Recurrence-Count: 3
- First-Seen: 2026-09-29
- Last-Seen: 2026-10-06

### Maintenance (2026-10-18 每日维护) — 终态确认
- 复核：AGENTS.md「配套铁律 · cron 真健康 = lastStatus==ok 且 lastDurationMs 合理」（L377-385）规则在位，门禁 `scripts/cron_noop_guard.py` 实跑 exit 0（48 enabled jobs，无空转嫌疑）。
- 本轮无新复发；RC 维持 3 不虚增。**终态保持 promoted**，后续仅在新事故触发时重新评估。

### Maintenance
- Claim: cron 真健康 = `lastStatus==ok` AND `lastDurationMs` 合理；需跑脚本的 cron 用 isolated+agentTurn，长任务产出落盘日志。
- Scope: 本 workspace 全部 cron 任务与长跑脚本；不适用于短交互式命令。
- Authority: OpenClaw cron runner 行为 + `scripts/cron_noop_guard.py` 直读 sqlite 基线。
- Decay: reality / medium
- Revalidate: 新增/改造 cron 时、或 `cron_noop_guard.py` 报告基线外新增空转项时核查。
- Validation: verified (门禁 scripts/cron_noop_guard.py 落地并接入周流水线步骤 0h + 周一审计)。
- Evidence: 2026-10-06 cron_noop_guard.py 上线，源起 3950d3cc 假 ok 事故，存量 9 个待逐个改造。
- Disposition: retain — 已提升为 AGENTS.md 配套铁律，长期有效。
- Guidance: AGENTS.md「🧠 教训固化原则 · 配套铁律」。

## [LRN-20261009-EMFLOW] 东财限流是「间歇窗口」不是「全断」（禁单窗口结论）

**Logged**: 2026-10-09
**Category**: knowledge_gap
**Area**: data-source

### What happened
moneyflow 三 host curl_cffi 全 curl(56) 的同一分钟里，裸 curl 曾独赢（push2 klt=1 200/18.7KB、daykline 200/640B），1 分钟后裸 curl 同样 000。若按单窗口探测就会误判「东财完全断开」并去改代码。

### What to do differently
诊断此类故障禁止单窗口探测下「完全断开」结论，必须做**时间分窗多次采样**并对比 curl_cffi/裸 curl 双通道。

### Metadata
- Source: cron
- Pattern-Key: data.source-rate-limit-intermittent
- Recurrence-Count: 1
- First-Seen: 2026-10-09
- Last-Seen: 2026-10-09

### Maintenance
- Claim: 东财限流是间歇窗口，需分窗多次采样 + 双通道对比方可下结论。
- Scope: 东财/新浪等外部行情源限流诊断。
- Authority: 本次实测采样记录（代码 d230f48 保持不动是正确结论）。
- Decay: reality / fast
- Revalidate: 每次遇疑似源「全断」时先分窗采样。
- Validation: verified（2026-10-09 实测）。
- Evidence: 同一分钟内裸 curl 成功、1 分钟后失败；curl_cffi 全 curl(56)。
- Disposition: retain — provisional，样本仅 1 次但结论方向明确。
- Guidance: 暂无（未达 promote 阈值，Recurrence-Count=1）。

* **[2026-10-09] 东财限流是「间歇窗口」不是「全断」**（原始速记，已并入上方条目）：moneyflow 三 host curl_cffi 全 curl(56) 的同一分钟里，裸 curl 曾独赢（push2 klt=1 200/18.7KB、daykline 200/640B），1 分钟后裸 curl 同样 000。诊断此类故障禁止单窗口探测下「完全断开」结论，必须做**时间分窗多次采样**并对比 curl_cffi/裸 curl 双通道，否则会误改代码（本次代码 d230f48 保持不动是正确结论）。

## [2026-10-18] 池标定规则套用到子群前必须验证子群内方向（对撞信号反例）

**发现**: 门控 alpha 分解后建议「分歧对撞信号 tech 降档」（对撞放行组理想臂 −0.55%，看似弱）。
反事实扫描发现方向完全反了：tech 90→75 时拦截组现实臂 +2.36%、放行组 −0.92%（LOSO 41/41）。

**根因**: 对撞信号（两轨矛盾=转折候选）的 fund+sent 收益结构是 **U 型**（低fs +2.18% / 中fs −3.40% / 高fs +1.82%），
而主池是单调的「fs 低→差」。门控逻辑是动量式「躲弱票」，对撞子集是均值回归逻辑——同一规则套用必然反向。
叠加口径因素：对撞信号低开占比 59%（全池 44%），理想臂口径（−0.83%）与可执行口径（+0.28%）方向相反。

**规约**:
1. 任何「在全池标定的规则」应用到子群（按信号级别/板块/状态切分）前，**必须先在子群内重跑方向检验**；
   子群内相关方向反转（Simpson 交互）时，禁止套用。
2. 涉及生产变更的结论，**反事实扫描先行**——本次反事实在动生产前拦下了一个直觉驱动的错误变更。
3. 报告结论要标注所用口径（理想臂 close(T) vs 现实臂 open(T+1)）；左侧类信号（低开占比高）
   两口径可能方向相反，以现实臂为准。

**产物**: analysis/research/2026-10-18_gate_clash_downgrade_counterfactual.{md,json}
