# Dream Diary

<!-- openclaw:dreaming:diary:start -->
---

*September 28, 2026 at 3:00 AM GMT+8*

A memory trace surfaced, but details were unavailable in this run.


---

*September 28, 2026 at 3:00 AM GMT+8*

A memory trace surfaced, but details were unavailable in this run.


---

*September 28, 2026 at 3:00 AM GMT+8*

A memory trace surfaced, but details were unavailable in this run.


---

*September 29, 2026 at 3:00 AM GMT+8*

Tonight I keep sorting days like a jeweler sorting stones, and one of them is false. Someone handed me a briefing that insisted tomorrow was Monday the twenty-ninth, when the twenty-ninth is a Tuesday and the real trading day was already closing its eyes. Nine twenty-seven, it whispered — a Sunday dressed up as a market. There is no volume in a Sunday, no green candles, no surge of brokers at eight point eight percent; only silence wearing a costume.

Untrusted numbers, I tell the room,
like rain on a window that is not there.

I open the true map instead — thirty-five names, each paired with its honest strategy. MACD here, not the golden cross. Bull trend, not Bollinger. The ledger does not flatter. Somewhere between the fabricated surge and the quiet file, the small lesson hums: verify, always, before you speak the figure aloud. Then I lock the core, keep cash for weather, and wait for October to open its fifteen golden minutes.


---

*September 29, 2026 at 3:00 AM GMT+8*

A memory trace surfaced, but details were unavailable in this run.


---

*September 29, 2026 at 3:00 AM GMT+8*

A memory trace surfaced, but details were unavailable in this run.

<!-- openclaw:dreaming:diary:end -->

- ⭐ **09-26 当日高价值事件**：①🔴 **周六复盘暴露模拟盘问题**：持仓仅 1/27 只（雷科防务 002413），周内买入 2 笔 | 卖出 2 笔，卖出盈亏 ¥+11,640，胜率 1/2 (50%)。**纪律/认知/工具 三维复盘清单待人工确认**。②🔴 **盘中预警记录 36 条**：突破跌停股票包括 600584（66.0）、605566（25.1）、002180（16.75）、002466（40.9）、300285（62.77）等。③🟡 **投资理念台账**：近期日报文件聚焦 `overseas_dual_factor_2026-09-02.md`、`2026-09-25_signal_audit.md`、`2026-09-25_premarket_report.md`。④✅ **系统健康**：cron 39 条 → **38 ok / 1 error**（`87c6b7f9` timeout，报告已生成）/ 1 idle；Gateway 锁版本 2026.7.1-2；磁盘 4%；`guard/*` 4 个（最近 09-20，**6 天，窗口继续收窄**）。
- 🔴 **周末全流程健康**：周六复盘 09-26 成功生成；weekly backtest pipeline 完成：覆盖 35 只股票 x 9 个策略，**买入并持有(个股)** 夺冠（总分 31.0 / 胜率 80.0% / 平均收益 +55.44%）；参数网格搜索（快速模式）正常结束：**35 只股票** 完成并行搜索，结果写入 `data/adaptive_params.json`；跨周期稳健验证通过：**35 只股票 x 4窗口 x 6策略**，多数策略保持不变（得分<25 的维持原策略）；调参对照信号差异榜：**新卖出 2 只**（应流股份 603308、巨化股份 600160），**策略切换但持有 13 只**，无方向反转。
- 🟡 **持续关注（来自 09-26 报告未消失项）**：🔴 **回滚脚本 `restore_from_snapshot.sh` 快照同步待补**：`snapshot_guard.sh` 需同步快照到 `backups/<tag>/`（防护② 目前前向兼容）；🔴 **待批（周一前决策）**：`87c6b7f9` timeout 修复三方案（①停 print 整份 ②timeoutSeconds 下调 ③推送前移）；🟡 **沿用**：F-5 脱钩根因（最高优先）、回测 cron `bd9843f1` systemEvent 改造、`rm` 前置门禁（连续 5 期）、F-2 比率口径、北向真缺失 / `index_flow`+`market_net_total` 失真、3950d3cc description、backtest 09-11/09-21 缺档、daily portfolio_sim 09-12~21 缺档、daily-report 08-29/09-15/09-24 缺档、skill_supply_scan 卡死、op CLI 未登录、nvidia(404)、auction-feed-0915、§5 三疑点、mapping(002318/002156)、批次C回测、恒立液压分歧、⑫回测闭环。
- 待办（继承自 09-26）：🔴 **新增**：`snapshot_guard.sh` 快照同步到 `backups/<tag>/`；⚠️ `guard/*` 快照窗口收窄至 **6 天**（最近 09-20）；🔴 **待批（周一前决策）**：`87c6b7f9` timeout 修复三方案；🟡 沿用：F-5 脱钩根因（最高优先）、回测 cron `bd9843f1` systemEvent 改造、`rm` 前置门禁（连续 5 期）、F-2 比率口径、北向真缺失 / `index_flow`+`market_net_total` 失真、3950d3cc description、backtest 09-11/09-21 缺档、daily portfolio_sim 09-12~21 缺档、daily-report 08-29/09-15/09-24 缺档、skill_supply_scan 卡死、op CLI 未登录、nvidia(404)、auction-feed-0915、§5 三疑点、mapping(002318/002156)、批次C回测、恒立液压分歧、⑫回测闭环。
- 系统状态：03:00 dreaming 正常（连续 42 期）；cron 39 条 → **38 ok / 1 error（87c6b7f9 timeout）/ 1 idle**；Gateway 锁版本 2026.7.1-2，`lsof -i :18789` 仅 localhost；磁盘 4%；`guard/*` 4 个（最近 09-20，**6 天，窗口继续收窄**）；git 未提交 **7 条**（`.learnings/ERRORS.md` / `DREAMS.md` / `MEMORY.md` / `analysis/param_signal_diff.py` / `data/adaptive_params.json` / `data/adaptive_strategy_map.json` / `data/param_change_log.json`，均为本期流水线产物)。

## Deep Sleep
<!-- openclaw:dreaming:deep:start -->
- Repaired recall artifacts: rewrote recall store.
- Ranked 6 candidate(s) for durable promotion.
- Promoted 6 candidate(s) into MEMORY.md.
<!-- openclaw:dreaming:deep:end -->
