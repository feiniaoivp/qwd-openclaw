# 脚本搭建与回测历史（2026-06~08）

> 从 MEMORY.md 归档，归档时间：2026-11-11 23:52:38
> 共 8 个小节。按需检索，不常驻上下文。

### 2026-07-26 升级：盘后全扫描脚本 close_scan_v2.py
*   **做了什么:** 将 `zhonglian_monitor.py` 升级为 `close_scan_v2.py`，整合了四个改进：1) `is_trading_day()` 周末/节假日自动跳过；2) `max_retries=3` 断线重试；3) 弃用实时spot接口，改用 `stock_zh_a_hist(adjust="qfq")` 前复权历史数据；4) 采用 `pandas_ta` 统一计算指标。
*   **覆盖范围:** 25只关注股全扫描 + 中联重科双策略持仓（读取 `zhonglian_state.json`）。
*   **Cron调整:** 停用了早盘09:00的 `zhonglian-monitor-morning`（误差4次），午盘13:00改为调用新脚本仅看中联重科，收盘15:30改为全量扫描推送。
*   **输出方式:** stdout JSON供agent解析推送。
*   **脚本位置:** `analysis/close_scan_v2.py`

### 2026-07-26 自适应策略分配器
*   **做了什么:** 基于五策略回测结果，为25只关注股各自分配历史最优策略，构建自适应扫描系统。
*   **策略映射文件:** `data/adaptive_strategy_map.json`（与 adaptive_trader.py 内 BEST_STRATEGY_MAP 需保持同步；脚本启动时会用内置字典覆盖 json）
*   **分配逻辑:**
    - 布林带+ATR（1只）：亿纬锂能
    - KDJ+RSI（1只）：安达维尔
    - EMA+OBV（4只）：福莱特、中联重科、中信金属、科华数据（弱趋势股最优）
    - EMA12/26金叉（9只）：久立/中信建投/汇川/通富/天齐/恒生/应流/雷科/中芯
    - 纯MACD（9只）：中信/中金/长电/招商/福莱蒽特/越秀/国瓷/福耀/恒立
*   **脚本位置:** `analysis/adaptive_trader.py`
*   **Cron整合:** 收盘15:30任务改为同时跑通用扫描 + 自适应扫描，双重推送。

### 2026-08-01 跨周期多窗口验证（防过拟合升级）
*   **新建:** `analysis/validate_strategies.py` — 解决单窗口回测过拟合。
*   **方法:** baostock 拉 2020 起 6.5 年历史，每只跑 4 窗口(W1全量/W2近3年/W3近1.5年/W4近1年)×5策略；稳定性得分 = 夏普×60 + 收益×0.6 - 样本量罚分 - 回撤罚分。
*   **置信度护栏(写回时内置):** 仅当 得分>=25 且 夏普>=0.25 才覆盖映射，否则保留原策略防过拟合。
*   **样本量分档权重(2026-08-01 升级, 用户建议):** ≥10笔→1.0, 6-9笔→0.75, 3-5笔→0.4, <3笔→0.15; 得分×权重; 笔数<6时护栏需夏普≥0.4。不误杀低频趋势策略。
*   **权重模型修正2处:** 越秀资本→macd(24笔满权), 天齐锂业→ema_obv(回撤-20%健康)。
*   **主要采纳变更6处:** 久立特材→bollinger、通富微电→ema_obv、越秀资本→macd(权重模型后)、中联重科→ema_cross、巨化股份→macd、金力永磁→kdj_cci。
*   **另修2处(权重模型):** 越秀资本 bollinger→macd、天齐锂业 ema_cross→ema_obv。
*   **低置信度保留8处:** 中信建投/中金/恒生/安达维尔/科华/上能/中信特钢/恒立液压。
*   **接入自动化:** 每周六 06:00 `weekly-backtest-strategy-refresh` 改为先跑单窗口回测再跑验证 --write-map，自动用护栏更新 data/adaptive_strategy_map.json。
*   **验证文件:** `analysis/validation_YYYY-MM-DD.json`。机电B股900925 baostock 仍无数据。

### 2026-07-26 五策略全量回测
*   **做了什么:** 新建 `analysis/backtest_strategies.py`，实现了5个策略在25只关注股上1.5年(20240101~20260726)的统一回测对比。
*   **策略清单:**
    1. **布林带+ATR**（趋势跟踪）：收盘价突破布林带上轨买入，跌破中轨卖出，ATR动态止损。平均收益+44.27%，夏普0.34，盈亏比2.79最高，交易次数最少(11次)。
    2. **KDJ+RSI**（均值回归 v2，去掉了CCI）：KDJ金叉+RSI<40买入，KDJ死叉卖出。平均收益+1.72%，最大回撤-18.31%，但在中芯国际(+64.57%)、长电科技(+61.72%)上表现优异，夏普1.355。
    3. **EMA+OBV**（量价共振）：收盘价站上EMA20且OBV上穿均线买入。平均收益+19.59%，胜率最低(24.66%)但盈亏比4.29最高——该赚的抓到狠。
    4. **EMA12/26金叉(基准C)**：历史最优策略，平均收益+58.94%，夏普0.37，总分29.8夺冠。
    5. **纯MACD(基准B)**：平均收益+57.78%，夏普0.40最高，总分29.1紧随其后。
*   **数据源:** baostock（akshare接口被封后改用），`adjustflag=2` 前复权。
*   **回测报告位置:** `analysis/backtest/YYYY-MM-DD.md`
*   **脚本位置:** `analysis/backtest_strategies.py`
*   **重要发现:**
    - 前复权导致CCI/KDJ严重失真（所有值偏负几千），改用滚动百分位数阈值修复。
    - pandas_ta的bbands列名因版本不同有差异（`BBU_20_2.0` vs `BBU_20_2.0_2.0`），已用动态列名匹配修复。
    - EMA12/26在强势股(国瓷、应流、长电)上表现碾压，但在福莱特、中联重科上不如EMA+OBV。

### 2026-07-26 数据源切换：akshare -> baostock
*   **原因:** akshare的东方财富接口(spot_em, push2his)和新浪接口全部被网络拦截(RemoteDisconnected)。
*   **替代方案:** baostock 稳定可用，`query_history_k_data_plus()` 支持前复权(adjustflag=2)/后复权(1)/不复权(3)。
*   **注意:** 机电B股(900925)在baostock上无数据，已知待处理。

### 2026-08-01 定时任务全部重建（升级丢失后恢复）⚠️
*   **事件:** 升级后 cron 存储只剩 1 个系统任务，原先所有定时任务全部丢失（旧 jobs.json 被清理）。已根据 MEMORY.md 记录全部重建。
*   **重建清单 (2026-08-01):**
    1. `daily-a-share-telegram-push` (id 3950d3cc) 周一~五 15:30 收盘推送
    2. `weekly-backtest-strategy-refresh` (id cc0ae9f1) 周六 06:00 周回测
    3. `memory-maintenance-monday` (id 01b13330) 周一 06:00
    4. `memory-maintenance-thursday` (id 9c483c25) 周四 06:00
    5. `memory-maintenance-check` (id 8f676e04) 周一/四 06:00 完整蒸馏
    6. `error-pattern-injector` (id ca08c2b7) 每日 06:00
    7. `skill-version-watcher` (id e8e3d01c) 每小时整点
    8. `quant-backtest-daily` (id bd9843f1) 周一~五 20:30 回测
*   **新 id 与旧 id 不同**（旧 a7742ce5/2c024e2d 已失效）。如需引用，用新 id。
*   **脚本验证通过:** adaptive_trader.py 输出策略映射+扫描信号；close_scan_v2.py 正确识别周末非交易日跳过。数据链路正常。
*   **Telegram 推送链路实测通过 (00:22):** 手动 force 触发 daily-a-share-telegram-push，isolated 会话跑脚本→生成简报→gateway 推送 telegram 成功(messageId 648)。**关键教训:** isolated cron 会话推送必须设 `delivery={mode:"announce", channel:"telegram", to:"626141741"}`（比普通 message 工具更可靠——isolated 会话没有 message 工具）。所有推送类任务(收盘/周回测/记忆蒸馏/技能监控/每日回测)均已按此配置。
*   **持久化确认:** 升级后 cron 数据存 sqlite 表 `cron_jobs`（指向 jobs.json 仅为兼容标识），`~/.openclaw/cron/` 目录已创建。
*   **用户自助守护目标:** 每日A股动态检查为长期守护目标，cron 重建后恢复。

### 2026-08-01 记忆系统修复（升级后自检）
*   **升级后 memory_search 不可用** —— 诊断出根本原因：记忆默认用 OpenAI embedding（sk-proj 旧 key 401 无效），切到 Gemini 又遇配额耗尽(429)。**用户确认正确路径是本地 HuggingFace 服务 `http://127.0.0.1:8080/v1`（BAAI/bge-m3）。**
*   **修复:** memorySearch.provider=openai-compatible, model=BAAI/bge-m3, remote.baseUrl=http://127.0.0.1:8080/v1 → 重启 gateway → `openclaw memory index --force` 重建索引（~8分钟）→ memory_search 恢复可用（459ms）。
*   **本地服务关键路径:** venv `/Users/duguke/hf_env/bin/python`（有 fastapi 0.138 + sentence_transformers）。系统 python `/usr/local/bin/python3.11` **无 fastapi**。
*   **修复 launchd bug:** `com.duguke.bge-m3` 原本 ProgramArguments 用 `/usr/local/bin/python3.11`（无 fastapi 导致反复 exit 1），改为 `/Users/duguke/hf_env/bin/python`，并加 `ThrottleInterval=30` 抑制 KeepAlive 重拉风暴。plist 备份在 `~/Library/LaunchAgents/com.duguke.bge-m3.plist.bak.*`。
*   **教训:** 记住本地 embedding 服务真正的启动环境是 hf_env venv；索引重建完整指令是 `openclaw memory index --force`。
*   **数据源偏好再确认:** 记忆嵌入用本地 bge-m3（免费/离线/稳定），不依赖 OpenAI/Gemini 配额。

## 2026-09-04~07 基建故障周复盘（weekly-review 09-06 蒸馏）＋周一安全审计 ⭐

**周性判断**：本周(09-01~09-05)为「防御窄震 + 基础设施故障」双周：30 标的信号持续偏空(谨慎+中性 80%)，模拟盘全程 0 持仓、实际成交 0 笔(空仓守住现金)。但量化/组合基建三天故障(09-01 组合引擎全崩、09-02/03 回测卡死无报告、09-03 日报缺失)——数据可信度打折，是下周第一优先修复项。

### 🔴 F-1~F-5 组合/数据基建故障清单（下周 09-08 早盘前必修）
*   **F-1 portfolio_sim 09-01 全 27 股信号引擎崩**：`cannot access local variable 'price'`（atr 计算路径），资产字段还夹带 ¥200,000 -92.59% 失真（初始资金口径错乱）。待修 + 补跑 09-01 缺失信号。
*   **F-2 每日回测 09-02/09-03 卡死 52min 无报告**：`strategy_index_benchmark` 在 ALL_STRATEGIES 内被**逐股调用** → ~2 基准 × 28 股 ≈ 56 次独立 baostock 指数查询全失败([Errno9] Bad fd)→ retry 忙等 CPU100%。**修复：指数序列只拉一次缓存复用勿逐股重查 + fetch_index_data 失败快速返回 None/回退到个股价格兜底，禁 retry 忙等死锁**（09-03 已确认设计缺陷）。
*   **F-3 portfolio_sim 缺组合权益日时序**：state 只存 per-symbol 扁平累计(已实现盈亏口径，trade_count=2 即一 BUY 一 SELL)，无组合时序 → **无法算周度回撤/夏普/绩效归因**。补日时序记账为铺路。
*   **F-4 Telegram 投递多次被 nova-sable/nova-cedar 子进程假阴性阻断**(503 回退)：报告落盘 ok 但推送漏，人工可能漏看。09-05 经 3 轮重试才送达周报。
*   **F-5 state 资金口径混乱**：100k 初始现金标在多数 symbol、600036/000400 双列、多桶资金(核心/卫星/哑铃)与总资产 ¥2.7M 系统对不上——多桶账与单桶 sim 混用。
*   **配合 08-29 补档关键数据**：08-29 baostock 大面积失效仅 1/30 股成功(久立特材)，validate 拒绝写回映射(完整度<20% 阈值)；param_tune 全量 900s 超时经 weekly_full_pipeline.py 已强制 `--fast`+timeout 1800s 修复。

### ⚠️ §5 三处硬疑点（需人工复核）
1. **招商银行(600036)**：08-26~09-04 连续“强买/关注”被降 HOLD，且 arbitration 反给“减仓”——技术面与仲裁长期打架。
2. **中联重科(000157)**：策略连 4 天触卖出(RSI 30 弱)但最终建议持续 HOLD → **卖出信号被最终裁决系统性忽略，疑似“持仓不忍割”偏差**。
3. **国瓷材料(300285)**：08-19 已 -4.90% 止损后 08-28 又回补 BUY ¥68.9，state total_pl 仍未记第二腿 → 止损纪律被绕过且 PnL 漏记，需人工确认仓位与账面。

### §6.3 风控参数微调（待落地的既定决策）
1. **仲裁减仓期间 → 禁 BUY（硬规则）**；2. **双策略任一腿🔴卖出且 RSI<35 → 至少减半仓**（修中联卖出被忽略）；3. **030 健康度 ≤6 强制“只减不加”**（覆盖 AI 研判文案“可加仓”措辞）；4. **盘中破 ATR 止损(2.0/risk1%) → 当日必走**，禁隔夜侥幸；5. **科技高估(长电/通富/中芯/亿纬)设 no-new-buy 区**至 re-test 站稳 EMA26。买入乘数本周已 0.5x→0.4x。
*   ⚠️ 认知偏差教训：多份 AI 研判给“逢低加仓”，但量化信号 80% 谨慎/健康度偏弱 →**研判文案与量化信号脱节**，健康度<6 强制只减不加可消除自相矛盾。

### 📅 09-07(一) 安全审计（记忆维护-周一）结论
*   ✅ 端口 18789 loopback-only（Ipv4+IPv6 均 localhost）；Gateway pid 27738 锁版本 2026.7.1-2；技能无高危模式；git 无明文 Key；**heartbeat-state security_audit 时间戳已补记 =1788732716（闭环 08-03 停滞缺口）**。
*   ⚠️ **op CLI 未登录**（No accounts configured）→ `op item list --vault` Secret 核验跳票，待人工补做。
*   ⚠️ **guard/* 快照仅 1 个且 >30 天**（guard/init-20260808-0530）→ 快照节奏断档，snapshot_guard 应高频跑。
*   ⚠️ **3 cron error**：weekly-review-trading(error 4x)、weekly-backtest-pipeline(error 3x)、盘前新闻快讯 40731f98(error)——周六两条连续失败待查根因（疑 baostock/子进程 503）。
*   ✅ **[已解决 2026-09-19] skill_supply_scan 卡死** —— 根因：旧 bash 版逐文件调用 `file`+逐模式 `grep`（≈3800 次进程启动），173 文件规模下挂 >60s。已重写为单进程 Python 版 `scripts/skill_supply_scan.py --offline`（**0.29s，提速 200x+**，纯静态零网络），新增风险分级（仅 `rm` 指向 `/ ~ $HOME *` 等致命位置才阻断），已接入周流水线步骤 0b + 周一审计。详见 `memory/2026-09-19.md`。
    *   教训：**会超时的门禁不可能被接入自动化** —— 这就是它 7 天未能闭环的原因（不是没人想接，是跑不完）。
*   ⚠️ LLM 余额降至 ¥6.16（09-05 沿用口径，注意用量）。
