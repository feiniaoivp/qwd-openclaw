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

### Maintenance (2026-10-19 每日维护) — 终态维持
- 复核：AGENTS.md「cron 真健康 = lastStatus==ok 且 lastDurationMs 合理」（grep L379-381 确认在位）；RC=3 跨任务共享计数为终态，无新复发，保持 promoted。

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

- **Logged**: 2026-10-18
- **Category**: best_practice
- **Area**: research/gate

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

### Metadata (2026-10-19 维护补全)
- Source: manual-session（对撞信号降档反事实）
- Pattern-Key: research.subgroup-direction-reversal
- Recurrence-Count: 1
- First-Seen: 2026-10-18
- Last-Seen: 2026-10-18
- Status: resolved（反事实拦下错误变更，生产未动，commit 93ccbb4）
- Disposition: retain — RC=1 < 3，与 `research.benchmark-basis-mismatch` / `research.proxy-score-dimensional-swap` 同属「实验口径类」家族；
  三条约形成集群后再统一评估是否合并提升，避免逐条写规则造成 AGENTS.md 膨胀。

## [2026-10-20] 每日 ⚠️ 告警被标为"存量噪声"40 天，实为结构性虚增（RC: ledger-warning-dismissed）
- 现象：equity_guard/portfolio_core 每日打印「601066/000987 cash-本金 != total_pl」，10-19 前一直被记为"疑似口径清理前遗留"，未深查。
- 真相：虚增额 == 该票全部买入成本 → 2026-08 期间 execute_trade 买入分支不扣 cash；019b40f(09-10) 修代码未回填存量。权益 +0.50% 实为 -0.52%（虚增 27,566.54）。
- 泛化规则：**重复出现的固定金额告警 = 恒定污染源信号，禁止归为噪声**；"修了代码没回填数据"是迁移类修复的标配缺口——凡是修账本类 bug，必须附带「存量污染扫描 + 回填/冲正脚本」才算闭环。
- 反证技巧：按「最后一次交易日 ≥ 修复时点的票 diff 应≈0」分桶，可精确圈定污染窗口。

### Metadata (2026-10-20 每日维护补全)
- Source: dreaming-session（equity_guard 告警深查）
- Reproducible: yes
- Pattern-Key: ledger.warning-dismissed-as-noise
- Recurrence-Count: 1（告警本身连续重复 40 天属同一事件被忽视，非跨任务复发，不虚增）
- First-Seen: 2026-10-19（深查日）；污染源 2026-08
- Last-Seen: 2026-10-19
- Status: resolved（代码 019b40f + 存量回填完成，权益修正 -0.52%）；规则 RC=1 < 3 暂不提升，若再现「固定金额告警归噪声」则 bump 复核
- Disposition: retain — 「修账本类 bug 必须附带存量污染扫描+回填才算闭环」为高价值通用规则，等待第二次实证

## [2026-10-20] 根因归因必须先做 git 考古，再谈数值指纹（RC: rootcause-without-history）
- 事故：账本虚增 27,566.54，我第一轮凭「现金差额≈买入成本」直接断言「买入分支漏扣 cash」并写进记忆/commit。
- 证伪方式极廉价：`git show <首末版本>:<file> | grep 'pos\["cash"\]'` —— 两个时点**都有**扣款。
- 真相在更隐蔽处：`original_cash = pos["cash"]; pos["cash"]=min(...); execute_trade(); pos["cash"]=original_cash` —— 「临时压缩 + 恢复」把扣款抹掉。
- 规则：①凡涉及历史数据的 bug，先把「涉事函数在事故窗口内的每个版本」捞出来看，别用现行代码推断历史；②数值指纹（反解股数/单价）只能**验证**假设，不能**生成**假设；③连续两次推翻自己的假设时，把推翻过程也写进注释，防止后人重走。

## 2026-10-20 | 盘前报告旧闻复读：修复只治了缓存层，漏了内容层
- 发生了什么：用户指出同一批来伊份旧闻反复出现且"以前处理过"。10-14 修复定位为僵尸缓存并宣布"根治"，但真凶是 get_today_news() 裸调 ak.stock_news_em()（akshare 1.18.64 官方默认 symbol='603777'）→ 相关度排序旧文霸榜。修缓存层无效，因为每天会"新鲜地"重拉同一批旧闻。
- 下次怎么做：①表象复发时逐层排查（投递层/缓存层/内容层/源头层），修复后必须验证"内容真的变了"而不只是"容器刷新了"；②裸调任何第三方函数先 inspect.signature 查默认参数；③新闻类输出必须带发布时间戳，无时间戳的新闻段一律视为可疑。
