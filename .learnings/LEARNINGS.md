# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice

---

## [LRN-20260916-IMP] Python 脚本「定义顺序」与「重复实现」两类静默缺陷
> **Metadata (2026-10-22 维护补全，此前无 key 属可计数盲区)**
> - Pattern-Key: code.undefined-name-module-level
> - Recurrence-Count: 1
> - First-Seen: 2026-09-16 / Last-Seen: 2026-09-16
> - Status: resolved（当日 4 处全修）
> - Disposition: retain — 已在 TOOLS.md「sys.path 样板强制模板」+ `scripts/script_path_guard.py` 门禁覆盖其子集；RC=1 < 3，再复现则并入 `code.static-audit` 家族一并提升。

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

### Maintenance (2026-10-23 每日维护) — 终态维持
- RC≥3 且 Last-Seen 10-06 在 30 天窗口内；grep 复核 AGENTS.md「cron 真健康」铁律在位。同族第五变体 ERR-20261022-HeartbeatFinalTurnIdleTimeout（裸 HEARTBEAT_OK 假 ok）已单独 resolved，家族计数维持 RC=3 不虚增。无新确证空转事件，保持 promoted。

### Maintenance (2026-10-24 每周维护) — 终态维持
- RC=3，30 天窗口内无新确证空转事件；grep 复核 AGENTS.md「lastDurationMs」铁律在位。同族第五变体（10-22 heartbeat）已验收终态、不计入本家族计数。保持 promoted；每日重复终态确认收敛为每周一次。

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
> - Pattern-Key: research.fix-layer-mismatch（表象复发 = 修错层）
> - Recurrence-Count: 1（10-14 号称“根治”后续发一次，计 1 次独立复发）
> - First-Seen: 2026-10-20（表象）/ 根因层 10-14 修复失败
> - Status: resolved（改 get_today_news 不再裸调 akshare 默认 symbol + 新闻必须带时间戳）
> - Disposition: retain — 与 `research.subgroup-direction-reversal` / `ledger.warning-dismissed-as-noise` 同属「归因过早」家族，RC=1 < 3 暂不提升。
- 发生了什么：用户指出同一批来伊份旧闻反复出现且"以前处理过"。10-14 修复定位为僵尸缓存并宣布"根治"，但真凶是 get_today_news() 裸调 ak.stock_news_em()（akshare 1.18.64 官方默认 symbol='603777'）→ 相关度排序旧文霸榜。修缓存层无效，因为每天会"新鲜地"重拉同一批旧闻。
- 下次怎么做：①表象复发时逐层排查（投递层/缓存层/内容层/源头层），修复后必须验证"内容真的变了"而不只是"容器刷新了"；②裸调任何第三方函数先 inspect.signature 查默认参数；③新闻类输出必须带发布时间戳，无时间戳的新闻段一律视为可疑。

## 2026-10-22 | 模型目录静默漂移：查模型可用性先 GET /models 对照配置
> - Pattern-Key: model.catalog-silent-drift
> - Recurrence-Count: 1
> - First-Seen: 2026-10-22 / Last-Seen: 2026-10-22
> - Status: resolved（已对照端点目录，新增模型保守占位待 agent 级实测）
> - Disposition: retain — RC=1 < 3；若再现“只测配置内模型即宣布可用性结论”则提升。
- 发现：amd-radeon 端点目录已从 7 个扩到 16 个（新增 V4.1/Gemma-4/MiniCPM-V-4.7 等），配置目录 20+ 天未刷新；「检查可用性」若只测配置内模型，会漏掉整批新能力，也会把 GLM-5.3-Flash 这类已恢复模型一直当坏的。
- 下次怎么做：①模型故障排查第一步 `GET {baseUrl}/models` 对照 openclaw.json 配置目录，两侧差异都要处置；②端点 400 报错信息会直接说该模型属于哪个专用端点（systemone/ocr/audio/speech），据此判定"非 chat 模型"而非"模型坏了"；③新模型入库用保守 contextWindow/maxTokens 占位，入链前必须 agent 级实测（裸 API 快 ≠ agent 循环稳，Vision-Exp 先例）。

## [2026-10-22] cron「status=ok + 时长 + agent 口吻 summary」三者都可能滞后于真实执行
- **现象**：10-22 06:00 首轮验收 `daily-premarket-analysis-cmd`，该 job 已于 10-21 21:29 从 agentTurn 改为 `--command` 直跑 pipeline，产物 06:01:24 已落盘；但 `cron get` 的 `state` 仍显示 `lastStatus=ok / lastDurationMs=23197`，`cron runs` 首条 summary 更是昨日 agentTurn 的口吻（"需要我：1. 检查运行结果…"）。`durationMs` 一字未变 → 状态/审计字段滞后于真实执行。
- **规约**：①**命令型 cron 的健康判定只看产物 mtime/内容**（`analysis/daily/<今日>_*.md`、`data/agent_runs/run_<今日>_*.json`），不采信 `lastStatus`/`lastDurationMs`/`summary`；②改造后首轮验收必须**双侧核对**（产物侧 + 调度侧），调度侧滞后时记录为观察项、不据此判定失败；③`summary` 是 LLM 生成的叙述文本，不是执行证据。
- **Pattern-Key**: cron.status-field-lag-vs-artifact（cron.silent-noop-fake-ok 反向变体：这次是字段**滞后**而非**虚报**）
- Recurrence-Count: 1
- First-Seen: 2026-10-22 / Last-Seen: 2026-10-22
- Status: resolved（产物验收通过，本条作为判据补充，不新增门禁——`missing_products()` 已是产物侧门禁）
- Disposition: retain — RC=1；若出现「因状态滞后而误判 cron 故障并重跑/重复推送」则立即提升到 TOOLS.md。

## 2026-10-22 | 情报采集链路鲁棒化：禁止信赖 RSS/中间跳转页 (RC: intel-pipeline-fragile)
- 事故：blogwatcher 抓取的 Google News 链接直接调用 `urllib` 仅获得 HTML 壳（canonical 重定向页），导致情报提取失败。
- 认知：外部情报源 $\rightarrow$ 代理 $\rightarrow$ 正文 的链路中，中间页是常态而非异常。
- SOP (Intelligence Robustness):
    1. **穿透 (Penetrate)**: 发现重定向/中间页 $\rightarrow$ 提取 `canonical` 或 `finalUrl` $\rightarrow$ 使用 `web_fetch` (Readability) 重新抓取。
    2. **固化 (Persist)**: 关键情报严禁仅在对话中汇报 $\rightarrow$ 必须落盘 `data/intel/{topic}.md` $\rightarrow$ 形成单一事实来源 (SSOT)。
    3. **同步 (Sync)**: 关键事实 $\rightarrow$ 同步至 `memory/YYYY-MM-DD.md` $\rightarrow$ 确保跨会话可见。
- 结论：$\text{Scan} \rightarrow \text{Penetrate} \rightarrow \text{Persist} \rightarrow \text{Sync}$ 是情报处理的唯一正确链路。

## [2026-10-28] 030复盘框架实战：三大矛盾 → 三条可执行新规则（RC: review.three-contradictions）

**Logged**: 2026-10-28
**Category**: best_practice
**Area**: trading/review-framework

### What happened
今日 A 股收盘复盘（16:00-17:30）基于 030 框架七阶段完整跑通，发现三大结构性矛盾，每个矛盾都直接转化为可执行规则并记入明日操作手册：

### 矛盾一：信息层级倒挂——竞价引擎 > 盘前报告 > 隔日推演
- **现象**：06:01 盘前报告推荐买入招行/国电南瑞/许继/平高（全天领跌，-2.00%/-0.60%/-1.00%）；09:25 竞价引擎捕捉中芯 +3.4%、通富/长电量额并列第一、fade_risk 4 只高开；实盘科技全天领涨（通富 +5.82%、长电 +3.98%、金力 +3.47%）、银行领跌。
- **根因**：盘前报告基于隔夜数据（滞后 3 小时），竞价引擎实时捕捉资金意图，隔日推演基于昨日收盘（方向延续性假设在反转日失效）。
- **新规则（P0）**：**明日 09:25 以竞价引擎输出为唯一决策源**；盘前报告仅作参考，隔日推演仅作概率分布参考。
- **fade_risk 重新定义**：竞价「卖出信号遇高开」若全天收阳 + 放量 + 跷跷板验证 = **「该弱不弱 = 超预期买入」**，暂时覆盖技术卖出信号，仅设硬止损（竞价开盘价 -1.5% 或 2×ATR）。

### 矛盾二：信号体系在「情绪反转首日」天然滞后
- **三大失效模式实证**：
  1. 防御型强烈买入 → 领跌（招行/国电南瑞/平高，信号方向 180° 反向）
  2. 卖出/谨慎信号 → 大涨（通富/长电/金力 均在 EMA20 下方、MACD 死叉区，但资金强行拉升）
  3. 策略对撞 → 无指导性（天齐 MACD 买入 vs EMA+OBV 卖出 + 乌云盖顶，030 裁决持有，实盘 -1.65%）
- **结构性根因**：数据滞后层 + 指标滞后层 + 框架盲区层（无「情绪反转首日识别模块」）
- **新规则（P0）**：框架增加「情绪反转首日识别模块」——竞价 fade_risk 名单全天收阳 + 放量 + 跷跷板验证 = 反转确认，**暂时覆盖技术卖出信号**，仅设硬止损。

### 矛盾三：执行率 < 100% = 无系统
- **实盘模拟账户证据**：组合总资产 -0.52%，仅持越秀 1 只（双卖信号未执行减仓，浮亏扩大至 -3.33%），错失通富/长电/金力核心机会，全天 0 笔成交。
- **心理机制**：信号滞后 → 不信竞价反转 → 等技术指标确认 → 错过主升浪；持仓亏损 → 锚定成本价 → 不愿止损 → 违背纪律死扛；健康度 5/10 → 买入乘数 0.4x → 合理化不作为。
- **新纪律（P0）**：
  1. 越秀资本：若再触发卖出信号，**市价减仓 ≥50%**，止损 6.40 硬执行
  2. 竞价 fade_risk 再现「卖出信号遇高开」：**按超预期信号买入**，单票 ≤3%，止损竞价开盘价 -1.5%
  3. 健康度修复至 6/10 → 买入乘数恢复 1.0x，**仓位上限释放至 3 成**

### 030 框架参数实盘校准记录（用于迭代优化）
| 参数 | 当前值 | 今日实盘反馈 | 建议调整 | 优先级 |
|------|--------|--------------|----------|--------|
| fade_risk 判定逻辑 | 卖出信号遇高开 = 警惕诱多 | 4 只全高开高走，胜率 100%（小样本） | 增加「fade_risk 全天收阳 + 放量 = 超预期买入」覆盖规则 | 🔴 P0 |
| 健康度评分权重 | 梯队/晋级率/炸板率 50% 权重 | 全市场数据缺口，关注池代理失真 | 引入「竞价量能放大幅度」「跷跷板验证通过率」替代缺口指标 | 🟡 P1 |
| 买入乘数映射 | 健康度 5/10 → 0.5x | 实盘修复至 6/10 但买入乘数未动态更新 | 健康度 ≥6 → 1.0x；5-6 → 0.75x；<5 → 0.5x | 🟡 P1 |
| 止损倍数 | 日内 2×ATR / 隔夜 3×ATR | 越秀 3×ATR = 6.40，竞价开盘价 -1.5% = 6.59 | 情绪反转首日：止损取 min(竞价开盘价 -1.5%, 2×ATR) | 🟢 P2 |
| 仓位矩阵细分 | 修复期×震荡筑底 = 3-5 成 | 实盘建议 ≤3 成（主线未确认） | 增加「主线确认天数」维度：0 天 = 下限，1 天 = 中位，≥2 天 = 上限 | 🟢 P2 |

### 复盘闭环完整性验证
- 昨日推演 vs 今日实际 → 偏差归因 → 规则修正 → 明日执行手册，**形成完整 PDCA 循环**
- 所有数字均来自授权源文件：`close_scan_v2` 实时扫描、`data/auction/` 竞价快照、`data/news/daily_2026-10-28.md` 新闻公告、`portfolio_sim.md` 实盘模拟，**无凭记忆数字**
- 产出已完整落盘：`analysis/daily/2026-10-28_recap.md`（标准版 + 增补版）+ `memory/2026-10-28.md`（本条目）

### Metadata
- Source: trading-session（030框架实战复盘）
- Pattern-Key: review.three-contradictions
- Recurrence-Count: 1
- First-Seen: 2026-10-28 / Last-Seen: 2026-10-28
- Status: resolved（三条新规则已写入明日操作手册、030 参数校准表）
- Disposition: retain — 高价值实战教训，待第二次实证验证规则有效性后评估 promote

---

## [2026-10-25] lrn.log-archaeology-absence-fallacy / lrn.uvicorn-accessformatter-fields

**A. 日志考古「缺席≠不存在」+ 总量守恒应对齐在第一轮**
- 经过：embedding 日志考古中，因「区域内无 POST 行」推断 access log 未开（虚构了不存在的脚本编辑），又未对 17,909(stdout 洪水)≈17,908(archive 风暴 boot) 做同源对齐，虚构了「10-23 幽灵启动器」；用户补充「10-23 晚当机、凌晨重启」+ panic 文件 uptime 反推（16.124d → 22:35:40）才破局。真相：洪水=8 月风暴；cluster1/2 是风暴期内成功服务窗；「机器睡眠」实为内核恐慌死机 3h49m。
- 规约：①多写者、无时间戳日志**不得用于事件定窗**；②两个独立来源计数几乎相等时，第一反应=同源假设并验证；③关键时间锚优先问用户现场记忆（最高价值、零成本）。
- **10-26 复检升级（锚点夹逼定案，零用户输入）**：两场风暴精确切割 = 08-12~14（≈9,359 轮，error log 112,099 行）+ 08-23~25（≈8,571 轮，102,864 行）；机制 = launchd KeepAlive × v1 无预检（--preload **先**加载 2.2GB 模型 → bind 8080 失败重试 5 次 → 退出 1 → ~20-25s/轮）；第一场占口者实锤 = 旧版 com.duguke.bge-m3（同 start.py 同 8080，gunicorn 版上线时未退场）；第二场占口者未定案（旧日志 0 字节）；10-23/24 error log 零风暴行，与 panic 无关实锤。**机械方法**：无时间戳日志定窗 = 同文件带时间戳锚点行号 + 🚀 段区间 + 跨文件行数守恒（每轮 stderr 行数）夹逼，可比问记忆更硬。

**B. uvicorn AccessFormatter 经 dictConfig 换 fmt 的字段陷阱**
- 经过：给 uvicorn.access handler 换带时间戳 formatter，fmt 用 AccessFormatter 文档化字段（client_addr/method/...）→ 每条记录 ValueError 被 logging 静默丢弃，**access 行整体消失且无任何可见报错**（仅在 stderr 侧的 logging error traceback 可见）；两次重启才定位。
- 规约：①fmt 用 `%(asctime)s %(message)s`（uvicorn 传入的 message 已是拼好的完整行，永远可用）；②改任何日志配置后必须 curl 实测目标行的存在性与格式，静默失败是这类改动的默认失败模式。

## [2026-10-31] 棘轮门禁复跑会洗白首跑违规（silent_except_guard 基线吞没）
- 现象：周流水线 0f silent_except_guard exit 1（close_scan_v2 新增 2 处 except-pass），手动复跑却 exit 0。
- 根因：guard 在 exit 判定**之前**无条件把 current 写回 baseline → 任何复跑都把上一次的「新增债务」并入基线，违规证据消失，但代码没修。
- 对策：判定 exit 1 时应**跳过基线写回**（或写回前打印将被并入的 new_debt 并留痕 logs/）；排障类复跑前先 `git stash baseline` 或读 git diff 保住首跑证据。
Source: weekly_pipeline_2026-10-31 step 0f
