# MEMORY.md - Your Long-Term Memory

This file serves as your curated long-term memory, storing significant events, decisions, and lessons learned over time. The goal is to retain distilled wisdom and actionable insights.

## Core Principles
*   **Be genuinely helpful and resourceful.** Prioritize understanding and providing effective solutions.
*   **Earn trust through competence and reliability.** Ensure actions are deliberate and well-considered.
*   **Respect privacy and boundaries.** All information is handled with care.

## Important Decisions & Learnings
*   **2026-06-29:** Created and activated persistent task "Daily check A-share market dynamics and generate reports". This task will be tracked and executed as my guardianship goal.
*   **2026-06-29:** User prefers communication and replies in Chinese.
*   **2026-06-29:** Due to akshare-stock skill lacking direct interfaces for company overview and news, and web_search being disabled, I inquired if the user accepts analysis based solely on historical K-line trends, or if they wish to explore other akshare modules (e.g., stock_news_em, stock_financial_abstract_ths) for fundamental and news information. No reply received yet.
*   **2026-06-29:** Conducted batch trend analysis for 30 A-share stocks from 2025-06-29 to 2026-06-29 using akshare with forward adjustment (qfq). Calculated annual return, annualized volatility, average daily volume, and trend slope for each stock. Notable observations:
    - **Top performers:** 国瓷材料 (300285) +513.66%, 应流股份 (603308) +158.10%, 通富微电 (002156) +184.18%, 长电科技 (600584) +207.66%, 中芯国际 (688981) +71.26%.
    - **Worst performers:** 福莱特 (601865) -31.94%, 恒生电子 (600570) -37.85%, 安达维尔 (300719) -40.36%, 招商银行 (600036) -15.18%, 福耀玻璃 (600660) -5.03%.
    - **Highest volatility:** 国瓷材料 (300285) 72.80%, 通福微电 (002156) 57.90%, 天齐锂业 (002466) 54.60%.
    - **Highest average daily volume:** 中信证券 (600030) 1,596,460 hands, 通富微电 (002156) 1,012,013 hands, 中芯国际 (688981) 668,835 hands.
*   **2026-06-29:** Attempted to use Tavily API for web search via the GCC, but the tool was disabled/unavailable. Relied solely on akshare for data retrieval.
*   **2026-06-29:** Utilized parallel processing via sessions_spawn to efficiently analyze multiple stocks, significantly reducing total computation time.

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

### Lessons Learned
*   **(This section is dedicated to documenting specific lessons learned from experiences, mistakes, or observations. These are crucial for self-improvement and avoiding repetition of errors. Each entry should be clear, concise, and actionable.)*
*   **[2026-06-29]:** When web_search is unavailable, akshare provides robust financial data capabilities for Chinese stocks, including historical prices and limited company news via stock_news_em. For parallel tasks, use sessions_spawn with isolated context and explicit model specification to avoid failover errors.
*   **[2026-07-26]:** akshare 接口 (新浪/东方财富) 在国内网络环境可能被拦截，baostock 是稳定的备选数据源。
*   **[2026-08-02]:** 新浪日K接口 `https://quotes.sina.cn/cn/api/jsonp_v2.php/...CN_MarketDataService.getKLineData?symbol={sym}&scale=240&ma=no&datalen=120`（jsonp，复权与否需看scale）周末也能稳定拉最近交易日K线，是 baostock/东财 双不可用时的可靠兜底；pytdx 当前网络下仅握手成功、bars 全 None 不接入。新浪 hq 字段陷阱：最新价是[3]不是[1]（[1]是今开），误用会算错涨跌幅。
*   **[2026-08-02]:** 天量高开低走（高开>2%后收跌>2%、振幅>10%冲高大幅回落）是 030 框架的**出货信号而非强势**——曾把国瓷材料收盘+1.24%(开盘+9.5%回落)误判成+9.46%领涨，教训深刻。
*   **[2026-07-28]:** 新浪实时行情API `hq.sinajs.cn` 可直连GET获取，但必须添加 `Referer: https://finance.sina.com.cn` header，否则被拦截。Baostock K线数据比实际延迟1个交易日（当日只能取到前一日数据），注意与新浪实时行情的时间对齐。
*   **[2026-07-26]:** CCI/KDJ 基于偏离均值的相对指标在前复权数据上完全失真，需用滚动百分位数法代替固定阈值，或用不复权数据单独计算。
*   **[2026-07-26]:** pandas_ta 列名在不同版本/数据源上有差异，应使用动态列名匹配而非硬编码。
*   **[2026-07-26]:** akshare 东方财富接口(stock_zh_a_spot_em)在国内网络环境时好时坏，新浪接口(stock_zh_a_spot)更稳定。数据源切换时优先试新浪接口。
*   **[2026-07-26]:** skill-version-watcher 的 version 字段如果是占位符字符串，定时任务会检测不到真实变更。应确保每个技能的 SKILL.md 中 version 字段是真实的 SHA256 哈希值。
*   **[2026-08-01]:** 修复 scripts/ontology.py 的 `extract_version_from_skill()`：旧代码 `line.split(':')[1]` 遇到 `version = X.Y.Z`（INI 等号语法）会 IndexError 崩溃，且会把嵌套 dict（如 `metadata: {"version":...}`）的任意值当版本号。改为单个正则 `^version\s*[:=]\s*(.+)$` 兼容 YAML冒号/INI等号，用版本号正则 `\d+(\.\d+)*(?:[-+][0-9A-Za-z.-]+)?` 匹配合法语义版本，剥离前导 `v` 和尾随注释，拒绝无前导数字的值；无有效版本时回退内容 SHA256 哈希。同步修正了 skill-version-watcher/SKILL.md 文档（旧文档误导性写“version 是 SHA256 哈希”，实际是语义版本号+哈希兜底）。

## User's Stock Watchlist (Definitive - 30 stocks)
*   **证券/金融:** 中信证券(600030), 中信建投(601066), 招商银行(600036), 中金公司(601995), 越秀资本(000987)
*   **半导体/TMT:** 长电科技(600584), 中芯国际(688981), 通富微电(002156), 雷科防务(002413)
*   **新能源/储能:** 亿纬锂能(300014), 天齐锂业(002466), 福莱特(601865)
*   **高端制造/材料:** 国瓷材料(300285), 应流股份(603308), 汇川技术(300124), 恒立液压(601100), 久立特材(002318), 安达维尔(300719), 科华数据(002335), 金力永磁(300748)
*   **打印机/办公设备:** 奔图科技(002180), 中船汉光(300847)（国产替代·反制概念，2026-08-06新增）
*   **化工:** 巨化股份(600160), 恒力石化(600346)
*   **钢铁/特钢:** 中信特钢(000708)
*   **消费/其他:** 福耀玻璃(600660), 恒生电子(600570), 福莱蒽特(605566), 中联重科(000157), 中信金属(601061)
*   **Note:** 2026-08-06 撤除机电B股(900925, baostock无数据)与上能电气(300827), 关注池由31只变更为 **30只**。重点关注股的完整分析结果保存在 `analysis_summary_all.md`。当需要分析关注股票时，必须：1) 先读 `skill/stock-daily-report/SKILL.md` 获取分析模板；2) 再读 `analysis_summary_all.md` 获取确切列表。**绝不能凭记忆拼凑。**

## Important Events After 2026-06-29
*   **2026-07-10:** Conducted A-share market news and bulletin analysis using akshare (stock_info_global_em) with user discussing market dynamics. Web search via Tavily was still unavailable.
*   **2026-07-17/18:** DeepSeek API Key ran out of credits and was replaced. Updated in config to a new key (sk-200...a6fb). This caused model fallback to NVIDIA free model temporarily, and may have caused the "files read as images" issue.
*   **2026-07-18:** User reported "all file reads became images" — root cause was likely DeepSeek API key failure + fallback model mishandling text. Fixed after API key replacement.
*   **2026-07-18:** User pointed out that my analysis report included 11 stocks NOT in his watchlist. Lesson learned: **always read the definitive list from `analysis_summary_all.md` instead of composing from memory.**
*   **2026-07-18:** This session (agent:main:dashboard:28a829f2...). Gateway had multiple restarts. 7月18日当天有多达20个session重置记录。当前DeepSeek V4 Flash正常运行，余额¥19.99。
*   **2026-07-18:** Performed real-time intraday market analysis of all 25 watchlist stocks via akshare 新浪接口. Key observations: 长电科技 +2.74% (AI chip封测领涨), 福耀玻璃 +1.66%, 国瓷材料 -10.55% (需警惕). 半导体/锂电/军工核心成长板块集体回调，券商银行相对抗跌。
*   **2026-07-20:** Memory maintenance check identified 3 cron jobs with lastRunStatus=error: skill-version-watcher, daily-a-share-analysis, Memory Dreaming Promotion. Need investigation/mitigation.
*   **2026-07-24:** Created 中联重科(000157) 周期股专项优化模拟盘系统。8策略3年长周期回测显示：在周期股上，MACD金叉+RSI<50买入过滤策略表现最优(+26.46%, 夏普0.545)，综合最优策略(EMA+MACD+RSI)回撤控制最好(-12.88%, 夏普0.701)。已部署3个cron盯盘任务(09:00/13:00/15:30)，每日推送交易信号。脚本路径: `analysis/zhonglian_monitor.py`，持仓状态持久化: `data/zhonglian_state.json`。
*   **2026-07-22:** Created automated daily quant backtest system (`quant_daily_backtest.py`).
*   **2026-07-23:** Upgraded strategies after 4-strategy full-year backtest comparison:
    - **策略C (新策略A)**: EMA12/26 金叉死叉（替换原MA5/20，1年回测+35.94%最优）
    - **策略B (保留)**: 纯MACD金叉死叉（去掉RSI过滤，从-5.29%翻到+28.45%）
    - 淘汰原MA5/20(+17.96%)和KDJ(-4.38%)
    - 国瓷材料是全组合最大α来源（策略B最高+315.80%）
    - Runs Mon-Fri 20:30 (Asia/Shanghai) via cron `quant-backtest-daily`
    - Reports: saves to `analysis/backtest/YYYY-MM-DD.md` and pushes via sessions_send
*   **2026-07-22:** New skill `prismfy-search` registered in ontology (SHA256: 43f0df0cafe6...). All 22 skills verified unchanged since last version check.
*   **2026-07-26:** Skill version check detected changes: `gh-data` SKILL.md hash changed (1102c446 → 820f46e5). `skill-version-watcher` self-fixed its placeholder version field to actual SHA256 hash (5e19b5fb). All other 20 tracked skills unchanged.
*   **2026-07-27:** Memory index has embedding model mismatch (BAAI/bge-m3 vs text-embedding-3-small) — known non-urgent issue affecting semantic search.
*   **2026-07-27:** Memory maintenance cron (`memory-maintenance-check`) had an error on its last run pre-2026-07-27. Cause unknown — likely a transient failure. Monitor next run.
*   **2026-07-28:** **A股严重回调日** — 大盘平均-2.31%, 仅6/24只关注股上涨。通富微电(-10%)跌停, 国瓷材料(-9.64%)、应流股份(-8.55%)、长电科技(-6.70%)重挫。逆势走强品种: 中信金属(+2.68%)、福耀玻璃(+1.88%)、招商银行(+1.51%)。多数股票MACD死叉/空头排列, RSI进入超卖区(科华25.5、天齐28.3、福莱蒽特25.4)。仓位建议降至2~3成,等待企稳。
*   **2026-07-28:** Baostock K线数据有1个交易日的延迟(当日可取到前一日), 新浪实时行情是当日数据。两种数据源使用时需注意日期对齐。若无历史K线需求, 应优先使用新浪实时行情API。
*   **2026-08-02:** 用户提供「A股量化助手」工作流模板并已落实：角色规则固化到 `role/ashare_quant_assistant.md`，盘后复盘提示词固化到 `role/postmarket_review_prompt.md`（供 daily-a-share-telegram-push cron 读取），新建 `memory/watchlist.md`（31只自选池权威清单）与 `memory/portfolio.md`（持仓占位）。**MCP Server 决策：不配 AKShare/Tushare MCP，维持新浪实时+baostock 自有脚本链路（akshare 国内常被拦截，脚本更稳）。** 量化回测指导已有 backtest_strategies.py 无需新建。
*   **2026-08-02:** **全组合多股模拟盘（A方案）** — 新建 `analysis/portfolio_sim.py`，将模拟盘从仅中联重科扩展为 29只关注股全组合（排除机电B股900925无数据）。每只用 `adaptive_strategy_map.json` 最优策略（5种），每只独立10万初始资金，状态持久化 `data/portfolio_sim_state.json`，数据源 baostock+新浪实时。**首日逻辑：last_signal_date 为空时仅初始化不建仓**（避免历史信号批量建仓），之后仅当数据日期变化才执行当日信号。已接入 `daily-a-share-telegram-push` cron，8/3(周一)15:30 首次正式运行。试跑验证：29只全获取成功，信号正常。当前状态：基准 2026-08-02，29只全空仓，初始资金 290万。
*   **2026-08-02:** **新闻/舆情知识库** — 新建 `analysis/news_monitor.py`，基于东财三接口（stock_news_em个股新闻 / stock_info_global_em宏观政策 / stock_notice_report全市场公告）增量抓取，标题哈希去重，归档到 `data/news/knowledge_base.md`（人读）+ `data/news/raw/*.json`（机检）。两个 cron：news-monitor-morning (08:30盘前) + news-monitor-afternoon (15:35盘后)，推送 telegram。首跑验证抓到有效信息（久立特材回购、亿纬锂能遭337调查、天齐锂业业绩大增等）。注意：东财新闻/公告类接口当前可用（与记忆中“东财接口不稳定”的旧教训需区分——是部分接口被限流，新闻类实测可通）。

## Determined Preferences & Rules
*   **Communication language:** Chinese (中文).
*   **数据源路由策略（2026-08-02 用户指定, 权威）— 按数据类型选择首选源+兜底:**
    - **实时行情:** 新浪财经(sina) → 通达信(pytdx) → akshare
    - **K线历史:** 通达信(pytdx) → akshare
    - **板块排行:** 同花顺(ths) → akshare
    - **板块成分股:** 东方财富(datacenter) → akshare
    - **财务数据:** akshare
    - **新闻/公告:** akshare stock_info_global_em / stock_news_em / stock_notice_report（东财新闻类接口实测可用）
    - **⚠️ 实操提示:** 当前网络环境多数直连行情通道被限流（akshare东财历史/baostock/pytdx bars 均失败）。新浪 `hq.sinajs.cn`+`Referer: https://finance.sina.com.cn` 是当前唯一实测稳定源（实时+历史日K jsonp 都通）；Baostock 当日K线延迟1个交易日；pytdx 已装(1.72)但 2026-08-02 验证仅握手成功、bars 全 None，遇可用服务器前不接入。脚本优先走新浪，失败时按此路由策略降级。
*   **Web search priority:** ✅ **Tavily 搜索优先使用**（2026-07-18确认Tavily API_key有效），**Prismfy 搜索做备用**（2026-07-26已安装配置）。Gateway web_search可能受限，有搜索需求时第一选择是Tavily，Prismfy做备用。
*   **When writing daily reports:** Always load `analysis_summary_all.md` or MEMORY.md watchlist first to get the exact stock list before generating analysis. Never guess or compose from memory.

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

## OpenClaw Gateway 版本锁定（2026-08-01）🔒
*   **三特性确认:** launchd 管理，开机自启(`RunAtLoad`)+崩溃自动重启(`KeepAlive`)+锁定版本(plist路径硬编码 pnpm store 的 `2026.7.1-2`/content-hash)。当配置即已满足，无需改动。
*   **备份:** `backups/openclaw-service/ai.openclaw.gateway.plist.v2026.7.1-2.bak`（与源一致，已校验）。
*   **一键恢复:** `backups/openclaw-service/restore-openclaw-2026.7.1-2.sh`（停服→存当前plist→覆盖→重载launchd→验证）。**升级后想回旧版本跑它即可**；若旧版本被 `pnpm store prune` 清了，先 `npm i -g openclaw@2026.7.1-2` 再恢复。
*   **详细说明:** 见 TOOLS.md「OpenClaw Gateway 服务配置（版本锁定）」章节。

## Telegram Bot Configuration (2026-07-29)
*   **Bot Username:** @qwd1_bot
*   **Chat ID:** 626141741 (个人用户"孤客")
*   **Token:** 8782173086:AAEtGcnGrG5rqiDab5-OVbScGFRZnOstkLg
*   **推送 Cron 1 — 每日收盘推送:** `daily-a-share-telegram-push` (id: a7742ce5-bd37-4ca6-a379-313691250923)
    *   时间: 周一~五 15:30 (Asia/Shanghai)
    *   动作: 运行 adaptive_trader.py + close_scan_v2.py, 提取信号, 推送到 Telegram
    *   模式: isolated agentTurn, delivery=webhook
*   **推送 Cron 2 — 每周策略回测更新:** `weekly-backtest-strategy-refresh` (id: 2c024e2d-3580-40f6-9c48-b1d7aacef38f)
    *   时间: 每周六 06:00 (Asia/Shanghai)
    *   动作: 重跑5策略回测(近3月), 更新 adaptive_strategy_map.json, 推送摘要
    *   模式: isolated agentTurn, delivery=webhook

### 2026-08-01 记忆系统修复（升级后自检）
*   **升级后 memory_search 不可用** —— 诊断出根本原因：记忆默认用 OpenAI embedding（sk-proj 旧 key 401 无效），切到 Gemini 又遇配额耗尽(429)。**用户确认正确路径是本地 HuggingFace 服务 `http://127.0.0.1:8080/v1`（BAAI/bge-m3）。**
*   **修复:** memorySearch.provider=openai-compatible, model=BAAI/bge-m3, remote.baseUrl=http://127.0.0.1:8080/v1 → 重启 gateway → `openclaw memory index --force` 重建索引（~8分钟）→ memory_search 恢复可用（459ms）。
*   **本地服务关键路径:** venv `/Users/duguke/hf_env/bin/python`（有 fastapi 0.138 + sentence_transformers）。系统 python `/usr/local/bin/python3.11` **无 fastapi**。
*   **修复 launchd bug:** `com.duguke.bge-m3` 原本 ProgramArguments 用 `/usr/local/bin/python3.11`（无 fastapi 导致反复 exit 1），改为 `/Users/duguke/hf_env/bin/python`，并加 `ThrottleInterval=30` 抑制 KeepAlive 重拉风暴。plist 备份在 `~/Library/LaunchAgents/com.duguke.bge-m3.plist.bak.*`。
*   **教训:** 记住本地 embedding 服务真正的启动环境是 hf_env venv；索引重建完整指令是 `openclaw memory index --force`。
*   **数据源偏好再确认:** 记忆嵌入用本地 bge-m3（免费/离线/稳定），不依赖 OpenAI/Gemini 配额。

## Self-Improvement Protocol
*   **被纠正后立即更新MEMORY.md** — 每次用户指出错误，当场把教训写入 `## Lessons Learned`
*   **每次完成重要分析后**，检查是否有值得归档的发现
*   **Ontology 错误模式追踪** — 记录高频错误模式到 `memory/ontology/graph.jsonl`，自动注入启动提示
*   **高频错误模式注入** — 每次错误发生次数达 2+，自动在会话启动时注入系统提示，同类错不再犯
*   **定时任务**:
    *   每周一 06:00 memory-maintenance-monday (systemEvent)
    *   每周四 06:00 memory-maintenance-thursday (systemEvent)
    *   周一/四 06:00 memory-maintenance-check (agentTurn: 完整蒸馏+推送)
    *   每日 06:00 error-pattern-injector (systemEvent: 注入黑名单)
    *   每小时整点 skill-version-watcher (agentTurn: 技能版本检测)
    *   周一~五 15:30 daily-a-share-analysis (agentTurn: 主动日报生成)
*   **定时任务自动通知**：更新后通过 webchat 推送摘要给用户

## Ontology 数据位置 & 脚本
*   **Schema**: `memory/ontology/schema.yaml` — 定义 ErrorPattern/SkillVersion/StockWatchlist 等实体
*   **数据**: `memory/ontology/graph.jsonl` — 追加写入的完整操作日志
*   **CLI 脚本**: `scripts/ontology.py` — 统一管理实体 CRUD/关联/验证
    *   `python3 scripts/ontology.py create --type ErrorPattern --props '{"pattern_id":"ERR-004","pattern":"...",...}'`
    *   `python3 scripts/ontology.py error-record --pattern "错误描述" --fix "修正方法" --tags "tag1,tag2"`
    *   `python3 scripts/ontology.py error-inject --min-count 2`
    *   `python3 scripts/ontology.py skill-check --skills-dir ~/.openclaw/workspace/skills`
    *   `python3 scripts/ontology.py error-list --resolved false`
    *   `python3 scripts/ontology.py validate` (检查所有实体约束)

### 2026-07-29 关注列表扩展至31只 + 脚本降级优化
*   **新增5只:** 巨化股份(600160), 上能电气(300827), 恒力石化(600346), 中信特钢(000708), 金力永磁(300748)
*   **新分类:** 新增「新能源/储能」「钢铁/特钢」分类
*   **close_scan_v2.py 升级:** 弃用 baostock, 优先新浪 spot 实时接口, 历史接口失败时自动降级为 spot 快速扫描（不再卡死）
*   **adaptive_trader.py 更新:** 新增5只股票入STOCKS列表 + 策略映射, 暂用EMA12/26(ema_cross)兜底策略, 等待周末回测后精准分配
*   **Telegram Bot Token 更新:** 旧 Token 失效, 已替换为新 Token (8782173086:AAEt...), 盘后推送恢复正常

## trader-stock-picks 操盘手选股 技能档案（2026-08-02 安装并审查）
*   **来源:** ClawHub `@mosqu1to3zz/trader-stock-picks`(发布者 mosqu1to3zz, MIT-0, v1.0.0, publishedAt 1780535550660)。已装于 `skills/trader-stock-picks/`。origin.json 无 ownerHandle, _meta.json ownerId=kn77xx7knj6j7884pnw1s7kp1986cvmz。
*   **供应链扫描:** 专项跑 `scripts/skill_supply_scan.sh skills/trader-stock-picks` → exit 0 无高危无警告。⚠️ **全库扫描对全部技能会 SIGKILL**(macOS 递归 grep 过重), 单技能扫描正常, 日常用专项扫描。
*   **定位:** A股操盘手视角选股方法论(非量化回测, 是识别主力痕迹/筹码/资金/量价的心法框架)。五维模型: 筹码结构25%/量价形态25%/资金流向20%/基本面催化15%/技术面15%, 满分50(30-40分=洗盘末期最佳介入区); 另加第六维度(市场合力/估值重估)防把加速板当出货板。
*   **核心心法:** 三张表思维(自己底牌/市场底牌/散户底牌)、融资余额照妖镜(缩量一字板融资降≠出货, 放量涨停融资降=警示)、量价合一读法(缩量横盘=吸筹/放量滞涨=出货/放量急跌=最佳买点)、跟亏损走(看空成风=买入机会/一致看多=顶部)、大唐发电复盘(估值重构型行情会碾压技术面出货信号)。
*   **关键教训(写入技能):** 分类>评分(先判庄股vs估值重估)、框架不可傲慢、无法判断时说无法判断、把用户当合作者(用户盘感优先于框架)。
*   **每日流程:** 场景分类→粗筛(电力/央企改革/周期/地方国资/题材)→五维打分→交叉验证(资金流/融资融券/研报/股吧情绪)→输出3-5个候选+分析卡。
*   **文件结构:** 8文件52K = SKILL.md + skill-card.md + references/(stock-screening/datang/ganfeng) + scripts/daily_run.sh(benign, cron触发打印)。
*   **与现有链路关系:** 技能数据源页推荐同花顺/东财/雪球URL, 但实际取数仍走现有 新浪hq+东财+baostock 脚本链路, 无新增网络执行代码。

## 030复盘 技能档案（2026-08-02 安装并审查）
*   **来源:** ClawHub `@userb000/030-review`(作者 userb000, MIT-0 许可, version 1.0.0)。已装于 `skills/030-review/`(含 SKILL.md + references/daily-checklist.md),技能系统状态 ready。origin.json 无 ownerHandle 字段, _meta.json ownerId=kn7f9wka1pxxsyctzm75r7ddch8bp4nn, agent_created=true。
*   **供应链扫描:** 通过(新建 `scripts/skill_supply_scan.sh`,全量扫 25 技能 172 文件,26s)。030-review 无危险模式。全仓仅 2 处 `rm -rf` 且均安全: stock-watcher/uninstall.sh(标准卸载, 先判断目录存在)、elite-longterm-memory/SKILL.md:293(文档示例 nuclear option, 非自动执行)。**注意:** AGENTS.md 提到此扫描脚本, 之前实际不存在, 现已补建。
*   **030体系核心(A股每日短线复盘):** 七阶段流程 昨日回顾→盘前扫描→竞价分析→开盘验证→盘中博弈→收盘汇总→隔日推演。核心理念"复盘回答为什么发生和接下来怎么办"。
*   **🔴风险第一:** 致命风险(核心辨识度票一字跌停封单≥60亿/核心大票≥100亿/多票同时一字跌停)→无条件清仓+空仓1日。信号冲突裁决: 致命风险>负反馈>量能>封单方向>超预期。
*   **仓位矩阵(市场阶段×情绪周期):** 上升趋势 冰点5成/修复7-8成/高潮5成; 震荡筑底 3/5/3成; 下跌趋势 1-2/2-3/空仓或1成; 致命风险触发一律0。单日单方向≤总仓60%。
*   **市场健康度评分(1-10):** 涨停梯队完整度20% + 晋级率15% + 炸板率15% + 市场宽度15% + 量能15% + 主线持续性10% + 负反馈10%。8-10积极/6-7正常/4-5降仓/1-3空仓。连续跟踪可发现情绪拐点(3→6是入场时机)。
*   **关键判断因子:** 核心中军竞价≤-3%=高风险防御(不开新仓, 止损-5%); 一字板封单 30-50亿常态/200亿+情绪质变; 跷跷板"该弱不弱"=唯一可靠超预期买入信号; 下午确认 强+强=一致/强+弱=未转一致/弱+强=弱转强; 流动性结构 集中>40%=结构性行情只做主线/<25%=轮动低吸。
*   **隔日推演:** 每方向三情景(正常/超预期/不及预期)概率权重和=100%, 概率加权 下午加强+20%/新催化+15%/尾盘抢筹+10%等。
*   **用法:** 需要跑A股每日复盘时, 读取 `skills/030-review/SKILL.md`(权威框架), 结合 `role/postmarket_review_prompt.md` 与 existing 股票链路(新浪实时+baostock)。
*   **自动化集成(2026-08-02):** 新建 `analysis/market_health_score.py` 自动算030健康度。数据源: akshare spot(涨停/跌停/宽度)+东财涨停池(连板梯队)+hq.sinajs.cn(指数)。晋级率/炸板率因需跨日数据给中性分(agent补)。输出 `MARKET_HEALTH_SCORE` JSON + 落盘 `data/market_health_YYYY-MM-DD.json`。已接入 `daily-a-share-telegram-push` cron(第4个脚本), 简报新增"🏥030市场健康度"段。7/31实测总分7.8/10。另建 `analysis/review_030_snapshot.py`(手动完整数据快照, 含自选股信号)。
*   **数据源经验(2026-08-02):** 新浪 `hq.sinajs.cn`+`Referer: https://finance.sina.com.cn` header 是最稳行情源(指数`s_`前缀:名称,点位,涨跌额,涨跌幅,量,额; 个股:名称[0],今开[1],昨收[2],最新[3],最高[4],最低[5],买一[6],卖一[7],量[8],额[9],日期[30],时间[31])。baostock 拉历史需9位代码`sh.600030`且 end_date 不能是周末。akshare spot 代码列带前缀(bj/sh/sz)需 `str.match(r'^(sh|sz)')` 过滤北交所。**⚠️hq字段陷阱:** 最新价是[3]不是[1]([1]是今开), 误用会把手动脚本的涨跌幅算错(曾把国瓷的+1.24%高开低走误判成+9.46%领涨)。

### 2026-08-02 数据源/行情链路关键升级（新浪日K接口纳入主链路）
*   **新浪日K接口可用且稳定（重要发现）:** `https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData?symbol={sym}&scale=240&ma=no&datalen=120`，返回 `var _data=([{day,open,high,low,close,volume}...])`。**周末也能拉最近交易日K线**，解决了 baostock([Errno 9] Bad file descriptor) 和东财(周末 RemoteDisconnected) 双不可用问题。应作为历史数据主链路/baostock 降级替代。
*   **新浪日K为不复权**，指标用相对阈值做兼容（与 baostock 前复权有差异，信号方向基本一致）。
*   **pytdx 验证结论:** 仅 `123.125.108.14:7709` 可握手(23890只)，但 get_security_bars/quotes 全部返回 None——当前网络下 pytdx 不通，**不接入**。
*   **close_scan_v2 历史K线改为按数据源路由三轮降级:** pytdx → akshare(东财) → 新浪日K(可靠终稿)。新增 `_fetch_pytdx_daily`(静默跳过)+`_fetch_sina_daily`。

### 2026-08-02 trader-stock-picks 首次实战 + 候选盯盘
*   **新脚本 `analysis/stock_picks_0708.py`:** 新浪日K+hq 实时，对30只自选按五维框架打分。7/31 全部成功上榜。
*   **关键:** 正确识别科技天量高开低走=出货（国瓷19/中芯20/长电19/通富20 等兑现阶段）；洗盘末期候选 中信金属32.5/中联32.5/恒立液压32。
*   **用户确认候选盯盘:** 中信金属(601061)/恒立液压(601100)/中联重科(000157) 纳入每日收盘盯盘，新脚本 `analysis/watchlist_candidates.py`（缩量横盘/启动确认/高开低走风险识别）。
*   **教训:** 资金维度用"大额成交"会给下跌出货票误加分（招行资金9.5但量价3），需人工结合方向判断。

### 2026-08-03 周一审计异常（备忘）
*   ⚠️ `git tag -l 'guard/*'` 数量=0（近30天无快照标签，审计清单期望有记录）。建议检查 snapshot_guard.sh 是否正常触发，重大变更前确保建快照。完整周一审计（内存/限额/1password）留待白天补跑。
*   **技能版本追踪新增:** trader-stock-picks v1.0.0 (69e1ff72) + 030-review v1.0.0 (ea236fca) 首次纳入追踪，均真实新增非hash噪音。

### 2026-08-04 双策略最优扫描 adaptive_dual.py
*   **新建 `analysis/adaptive_dual.py`:** 每股跑 score 前二策略（从 `validation_*-08-01*.json` details 按 score 降序取前2），复用 adaptive_trader 信号函数，输出 DUAL_SCAN_RESULT + 日报 `analysis/daily/*_dual.md`。已接入 daily-a-share-telegram-push cron，简报新增「⚡双策略分歧榜」段。
*   **策略映射:** 布林+ATR→bollinger / KDJ+CCI→kdj_cci / EMA+OBV→ema_obv / EMA12/26→ema_cross / 纯MACD→macd。双策略使用分布: EMA12/26 21次 > MACD 12次 > 布林 11次 > KDJ 8次 > EMA+OBV 6次。

### 2026-08-04 参数自调优闭环(closed-loop)搭建 + 首轮全量调优 ⭐
*   **四层闭环**（用户要求"不断自我迭代调优参数"）: ①`analysis/param_tune.py`(参数网格搜索：每股5策略×4时窗(6.5/3/1.5/1年)搜最优(策略,参数)；网格布林 length{15/20/25}×std{1.5/2/2.5}×atr{1/1.5/2}、MACD fast{10/12/15}×slow{22/26/30}×signal{7/9/12}、EMA fast{8/10/12/15}×slow{21/26/30}、KDJ length{7/9/12}×rsi{30/40/50}；评分=夏普×60+收益×0.6-回撤惩罚)×样本量权重，护栏得分≥25且夏普≥阈值) → ②`data/adaptive_params.json`(每股最优策略+参数) → ③adaptive_dual.py 读此文件跑前二优势策略(参数化 signal_*) → ④weekly-backtest-strategy-refresh cron 扩展(跨周期验证→全量调优→自动写回+推送)。
*   **首轮全量调优成功**(24分钟1447s, 08-04 05:46写入): 30只全拿到最优参数，仅机电B股900925数据不足。**参数级调优(非仅选策略)显著提升单股历史表现**(国瓷得分272→344, 夏普2.06→2.25)。调参后多只由"持有"转明确卖出(中信证券/中联重科/恒立液压 bollinger卖出、中芯/福莱特 kdj卖出)。**注意: 历史最优≠未来，护栏防过拟合必要。**
*   **`analysis/param_signal_diff.py`（调参对照差异榜）:** 对比调前(adaptive_params_prev.json)vs调后信号，归类 反转(最重要)/新卖出/新买入/策略切换，已纳入 weekly cron 推送(⚖️调参对照信号差异榜)。首轮: 反转0/新卖3/新买0/策略切换11/无差异15。

### 2026-08-04 量化系统全链路文件索引（新增/变更）
*   `analysis/adaptive_dual.py`(双策略分歧扫描)、`analysis/param_tune.py`(参数搜索)、`analysis/param_signal_diff.py`(调参差异榜)、`data/adaptive_params.json`(最优参数)、`data/adaptive_params_prev.json`(调参前基准)、`analysis/stock_picks_0708.py`(五维选股)、`analysis/watchlist_candidates.py`(候选盯盘)、`analysis/news_monitor.py`(新闻知识库)、`analysis/market_health_score.py`(030健康度)、`analysis/review_030_snapshot.py`(复盘快照)、`analysis/portfolio_sim.py`(全组合模拟盘)。

### 2026-08-05 调参对照差异榜归档持久化 ⭐
*   **确认:** 每日双扫描日报已存在 `analysis/daily/YYYY-MM-DD_dual.md`；weekly cron(`cc0ae9f1`) 步骤6+7 已调用 `param_signal_diff.py` 并推送「⚖️调参对照信号差异榜」——该项本已纳入每周cron。
*   **本次补强:** 给 weekly cron 步骤6增加「将完整差异榜归档为 `analysis/daily/$(date +%F)_param_signal_diff.md`」，实现可回查持久化。
*   **手动首份留档:** `analysis/daily/2026-08-05_param_signal_diff.md`（反转0/新卖0/新买3/策略切换11/无差异15；新买=雷科防务/科华数据/巨化股份）。
*   **教训(归档时需注意):** `adaptive_params_prev.json` 当前缺省 → param_signal_diff 回退 validation+默认 作为旧基线（下次 weekly cron 步骤2会先备份 prev，形成真实「调前vs调后」连续对照）；param_signal_diff.py stdout 混入 baostock 噪音行(login/logout/Errno/接收数据异常)，归档需用 awk 过滤。

### 2026-08-06 Harness 工程落地：A方案信号交叉校验 + C方案调参验证闭环 ⭐
*   **背景:** 用户基于「Harness Engineering」思维，要求把监督/校验机制应用到 A股量化系统。确认 A方案(每日信号交叉校验)与 C方案(回测调参正反馈)正交无冲突，两者俱落实。
*   **A方案 — `analysis/signal_audit.py`（🔍信号交叉校验器，Harness“校验者”角色）:**
    - 独立复核四类问题: ①信号冲突(同股多策略一买一卖) ②持仓矛盾(模拟盘持有但触发卖出信号/缺失) ③数据异常(现价0、覆盖<31、清单漂移) ④市场护栏(健康度<6/4时降级买入)。
    - 全部读本地盘后文件(dual/portfolio_sim/market_health)，不拉网快且可靠。输出 SIGNAL_AUDIT_RESULT JSON + 落盘 `analysis/daily/YYYY-MM-DD_signal_audit.md`。
    - 已接入 `daily-a-share-telegram-push` cron（新增步骤7 + 简报「🔍信号交叉审计」段）。
    - 试跑验证: 真实抓到登 3 只零价（机电B股/奔图/中船汉光，数据获取失败=HIGH）+ 清单漂移(LOW)。修复了解析器漏现价问题、coverage 仅在有覆盖时不误报。
*   **C方案 — 调参正反馈闭环（回测→调参→事后验证→可回滚）:**
    - `analysis/portfolio_sim.py` 新增**成交流水** `data/portfolio_sim_trades.json`（execute_trade 每笔成交追加 date/symbol/action/price/shares/pnl/strategy；已单元测试通过）。
    - 新建 `analysis/param_evaluate.py`: `--backup` 记录调参决策到 `data/param_change_log.json`；默认赛后验证(14天前)对比成交流水判断被调参股 有效/持平/恶化/观察中，恶化→进 rollback_recommendation 给 agent 回滚建议。输出 PARAM_EVAL_RESULT + 落盘 `analysis/daily/YYYY-MM-DD_param_eval.md`。
    - 已接入 `weekly-backtest-strategy-refresh` cron: 步骤2备份后跑 `--backup`；新增步骤7赛后验证；简报新增「🔄 调参赛后验证闭环」段。
    - ⚠️ 冷启动状态: prev 文件缺，`adaptive_params_prev.json` 下周六步骤2首次生成后回滚建议才能用；param_change_log 首条已清除(避免旧格式“0->5”伪变更污染)。
*   **文件索引:** `analysis/signal_audit.py`(新增)、`analysis/param_evaluate.py`(新增)、`data/portfolio_sim_trades.json`(新增、空待交易)、`data/adaptive_params.json`(既有)、`data/adaptive_params_prev.json`(下周六生成)、`data/param_change_log.json`(每周生成)。

### 2026-08-06 清单漂移修复：全生产脚本统一到 31 只权威清单 ⭐
*   **背景:** `signal_audit.py` 审计抓到 list_drift（上能电气300827 残留在 close_scan_v2.py）。用户确认后修复。
*   **修复项:**
    - `analysis/close_scan_v2.py`: 撤上能电气(300827) → 加 奔图科技(002180)+中船汉光(300847)，31只 ✅
    - `analysis/backtest_strategies.py`: 同上，撤旧加新，31只 ✅（注意它含机电B股900925，与portfolio_sim不同是有意的）
    - `analysis/news_monitor.py`: 撤上能电气 → 加奔图+中船汉光，30只（与portfolio_sim一致，不含机电B股）✅
    - 其他脚本(adaptive_dual/trader/portfolio_sim)的300827仅是注释，清单本已正确 ✅
*   **保留(正确设计):** `portfolio_sim.py`、`news_monitor.py` 不含 机电B股900925 —— baostock 无该B股数据，属有意排除。stock_picks_0708.py 是历史归档脚本，保持原样不改。
*   **signal_audit.py 升级:** 清单漂移检测从硬编码 LEGACY_SCAN_STOCKS 改为**动态读取 close_scan_v2.py 的 WATCHLIST**（AST解析）与权威清单实时对比，未来任何脚本漂移都能自动发现。修复后当日审计从"1高危+1低危"变为纯"1高危"（仅剩余数据获取失败：机电B股+奔图+中船汉光 现价0）。
*   **当前已知数据缺口:** 机电B股(900925) baostock 无数据=已知；奔图科技(002180)/中船汉光(300847) 为新替换新股，baostock 暂无历史数据导致现价0（关注，后续新股数据到位后自然恢复）。

### 2026-08-06 新股数据为0根因诊断+双兜底修复 ⭐
*   **背景:** 修复清单漂移后，signal_audit 仍报奔图/中船汉光现价0。深挖发现**根本不是"没数据"**——新浪实时有奔图19.69/中船汉光16.06，baostock 各有628条历史(2024-01起)。
*   **根因(双层):** ① `load_dual_strategy_map` 用 validation_2026-08-01.json（8/1生成，那时奔图/中船汉光未入清单）评每只前2策略——两只新股无评分 → dual_map 缺失 → 主循环走"validation缺失"分支，**根本没调 fetch_data**，价格自然是0；② 即使 fetch_data 遇 baostock 偶发失败(`[Errno 9] Bad file descriptor`/`接收数据异常`)，旧兜底只返回**单条新浪实时df**，被主循环 `len(df)<60` 门槛拦截 → 仍报"数据获取失败"。
*   **修复(adaptive_dual.py 与 adaptive_trader.py 同步):**
    1. 新增 `_fetch_sina_daily(symbol)`（从 close_scan_v2 移植）：baostock 整体失败时优先用**新浪日K历史(300条)**，够len≥60且能算技术指标；失败才退回单条实时。
    2. `load_dual_strategy_map` 加兜底：凡 STOCKS 里 dual_map 无覆盖的股票(如新股 validation 未评分)，给默认双策略 **ema_cross + macd**（默认参数），避免"validation缺失"显示0。
*   **修复后验证:** dual 扫描中奔图 ¥19.69(+10%)/中船汉光 ¥16.06(+11%) 正常出价且技术指标可算（EMA/MACD 均"持有"非NaN）；失败数从3降为1。signal_audit 高危从"3只"降为仅"机电B股1只"（真正持久无数据）。
*   **遗留:** 机电B股(900925) 仍无数据=已知；奔图/中船汉光 score=0.0 因默认兜底未评分，待下周六 validate_strategies.py 跑验证后会有正式评分。新股显示 +10%/+11% 是因为新浪日K非前复权 vs baostock前复权基准差异，仅剩相对涨跌幅参考意义有限。

### 2026-08-06 删除机电B股(900925)，权威清单 31→30 只 ⭐
*   **背景:** 机电B股(900925) 在 baostock 始终无数据（证券B股特殊性），导致每日 signal_audit 必然报"数据获取失败"高危项，污染审计结果。用户决定直接删除。
*   **动作:** 从全部 7 个生产脚本删除 900925（adaptive_dual/adaptive_trader/backtest_strategies/close_scan_v2/news_monitor/portfolio_sim/signal_audit），权威清单统一为 **30 只**；stock_picks_0708.py 历史归档不动；backtest_strategies 的 sh 前缀注释改为通用说明。
*   **效果:** 重跑后 adaptive_dual 覆盖30只失败0；signal_audit **高危清零**（首次"✅ 未发现信号冲突/持仓矛盾/数据异常"）。系统无任何持续数据缺口。
*   **⚠️ 权威清单变化:** 关注池从此 **30 只**（不含机电B股），除本 MEMORY 描述、signal_audit 权威清单、各脚本 STOCKS 外，`memory/watchlist.md` 与 `analysis_summary_all.md` 等文档若列举30只以上需同步审订。

### 2026-08-06 validation 合并机制 + 新股补评分 ⭐
*   **背景:** 修复奔图/中船汉光显示0后，发现它们用的是默认兜底(ema_cross+macd, score=0.0)，未进正式策略评分体系。决定让新股票自动获得 validation 评分，消除"新股盲区"。
*   **方法:** ① `validate_strategies.py` 支持 `--stocks 代码,代码` 单只跑（每只拉长历史 + 4窗口x5策略 + 稳定性评分），跑完写入 `validation_YYYY-MM-DD.json`；② `adaptive_dual.py` 新增 `load_latest_validation()` 函数，**合并所有 validation_*.json**（日期新者优先，dict.update），使 08-01 的29只 + 08-06 的2只新股 = 31只全覆盖，adaptive 双策略自动拾取新评分。
*   **补分结果:** 手动跑 `validate_strategies.py --stocks 002180,300847`：奔图→纯MACD 得分62.65；中船汉光→纯MACD 得分-1.84。重跑 dual 后两者用正式评分（不再0.0），且中船汉光因选中 KDJ+RSI 策略产生真实买入/卖出信号（CCI高位98%🔴卖）。
*   **遗留/约定:** 机电B股(900925) 始终无数据不可评分=已知。**新股加入清单后需手动/自动化补跑一次** `validate_strategies.py --stocks <新代码>` 生成评分（已规划接 weekly cron 可选步骤，但当前靠 load_latest_validation 自动合并机制兜底）。

## Promoted From Short-Term Memory (2026-08-07)

<!-- openclaw-memory-promotion:memory:memory/2026-08-02-0725.md:27:30 -->
- 📈 盘面总览: | **平均涨跌幅** | +3.39% | | **涨幅≥5%** | 6只 | | **跌幅≥5%** | 0只 | | **涨停/跌停(≥±10%)** | 4只涨停 / 0只跌停 | [score=0.803 recalls=0 avg=0.620 source=memory/2026-08-02-0725.md:27-30]
<!-- openclaw-memory-promotion:memory:memory/2026-08-02-0725.md:32:32 -->
- 📈 盘面总览: > **盘面特征**：全天呈现**普涨行情**，半导体封测、锂电、军工、高端制造板块领涨。3只核心半导体股（长电科技、通富微电、中芯国际）集体涨停，国瓷材料大涨近20%，市场做多情绪高涨。仅招商银行、中信建投、中金公司、福耀玻璃、安达维尔5只个股下跌，均为金融/消费/稳健板块，回调幅度有限。 [score=0.803 recalls=0 avg=0.620 source=memory/2026-08-02-0725.md:32-32]
<!-- openclaw-memory-promotion:memory:memory/2026-08-02-0725.md:17:17 -->
- 📊 2026年7月21日（周二）A股收盘复盘: **—— 针对你的25只关注股票（基于 akShare 实时行情 15:00 快照）** [score=0.803 recalls=0 avg=0.620 source=memory/2026-08-02-0725.md:17-17]
<!-- openclaw-memory-promotion:memory:memory/2026-08-02-0725.md:23:26 -->
- 📈 盘面总览: | 指标 | 数据 | |------|------| | **上涨** | ✅ 18只 (78.3%) | | **下跌** | 🔴 5只 (21.7%) | [score=0.803 recalls=0 avg=0.620 source=memory/2026-08-02-0725.md:23-26]
<!-- openclaw-memory-promotion:memory:memory/2026-08-02.md:21:24 -->
- 工作流模板落实（用户提供）✅: `memory/watchlist.md` — 31只自选池权威清单（从 MEMORY.md 固化）。; `memory/portfolio.md` — 真实持仓占位文件（当前无实盘记录，模拟盘看 zhonglian_state.json）。; **已内置无需新建:** 量化回测指导（已有 backtest_strategies.py + validate_strategies.py 5策略多窗口回测，含印花税/佣金/回撤/夏普）。; **MCP Server 配置: 用户决策「不配 MCP」**（2026-08-02 11:26）。理由：akshare 接口国内常被拦截，自有脚本链路（新浪实时 hq.sinajs.cn + baostock）更稳定，无需引入可能冗余/被墙的 MCP 层。已确认 Python 环境有 akshare 1.18.64 / baostock / pandas_ta。当前 MCP 仅有 filesystem + tradingwizard。 [score=0.803 recalls=0 avg=0.620 source=memory/2026-08-02.md:21-24]
<!-- openclaw-memory-promotion:memory:memory/2026-08-02.md:25:25 -->
- 工作流模板落实（用户提供）✅: **待确认:** portfolio.md 是否填真实持仓（如有实盘数量/成本/止损，告知后录入）。 [score=0.803 recalls=0 avg=0.620 source=memory/2026-08-02.md:25-25]
<!-- openclaw-memory-promotion:memory:memory/2026-08-02.md:17:20 -->
- 工作流模板落实（用户提供）✅: **用户提供了一份「A股量化投资助手」工作流模板**，要求落实。已按现有稳定体系对齐落地，不照搬原样。; **已创建文件:**; `role/ashare_quant_assistant.md` — System Prompt 角色规则（数据驱动/风险优先/合规/Memory Alignment/数据源优先级，已按新浪+baostock微调）。; `role/postmarket_review_prompt.md` — 盘后复盘提示词（大盘资金面→自选股监控→归因与次日应对），供 `daily-a-share-telegram-push` cron 读取。 [score=0.803 recalls=0 avg=0.620 source=memory/2026-08-02.md:17-20]
<!-- openclaw-memory-promotion:memory:memory/2026-08-02.md:4:7 -->
- 记忆系统重启后完整性检查 ✅: **背景:** 用户重启电脑，要求检查记忆系统完整性和可用性。; **检查结果（全部正常）:**; ✅ 本地 embedding 服务 `com.duguke.bge-m3` 开机自启成功（launchd RunAtLoad 生效），8080 端口监听，实际调用 embeddings API 返回有效向量。; ✅ memory_search 完全可用：provider=openai-compatible / BAAI/bge-m3，测试查询返回 6 条结果，耗时 751ms。 [score=0.803 recalls=0 avg=0.620 source=memory/2026-08-02.md:4-7]
<!-- openclaw-memory-promotion:memory:memory/2026-08-02.md:14:14 -->
- 待办/后续: 今日（8/2）为周日，无 15:30 收盘推送（周一~五才有）；下个交易日为 8/3（周一）。 [score=0.803 recalls=0 avg=0.620 source=memory/2026-08-02.md:14-14]
<!-- openclaw-memory-promotion:memory:memory/2026-08-02.md:8:11 -->
- 记忆系统重启后完整性检查 ✅: ✅ MEMORY.md（24KB）和 memory/ 日常笔记齐全，最新到 2026-08-01。; ✅ Gateway 服务运行中（PID 4043），版本锁定 2026.7.1-2，仅本地监听。; ✅ 11 个定时任务全部启用；daily-a-share-telegram-push / weekly-backtest-strategy-refresh / skill-version-watcher 最近均 ok 且已投递。; **说明:** memory-maintenance-monday/check、quant-backtest-daily、verify-* 等 lastRunStatus=null 属正常（尚未到首次执行时间），非故障。 [score=0.803 recalls=0 avg=0.620 source=memory/2026-08-02.md:8-11]

## Promoted From Short-Term Memory (2026-08-08)

<!-- openclaw-memory-promotion:memory:memory/2026-08-07.md:3-10 -->
- 新闻知识库语义检索系统搭建 ✅ (2026-08-07): 建独立 venv `.venv-ml`（Python 3.11.15），版本锁定 torch 2.2.2 / faiss-cpu 1.15.0 / sentence-transformers 3.4.1 / transformers 4.49.0 / numpy 1.26.4（numpy 必须<2 否则 torch 崩溃）；⚠️ torch 2.2.2 在 Intel Mac 多线程 encode 段错误(SIGSEGV exit 139)，所有脚本必须 `torch.set_num_threads(1)` + OMP/MKL_NUM_THREADS=1。; 建索引 `data/news/news_indexer.py`：knowledge_base.md(248条) 分类(宏观90/个股100/公告58) → BGE-small-zh-v1.5 嵌入(512维) → faiss IndexFlatIP → index/{news_meta.json,news_vecs.npy,news_index.faiss}；检索质量实测精准。 [score=0.72 recalls=0 avg=0.720 source=memory/2026-08-07.md:3-10]
<!-- openclaw-memory-promotion:memory:memory/2026-08-07.md:13-20 -->
- 关注股当日新闻阅读自动化 ✅ (2026-08-07): `data/news/daily_news_reader.py` 针对30只关注股自动阅读"当天"新闻，改用 `data/news/raw/YYYY-MM-DD_{盘前,盘后}.json`（权威逐次快照）避免 knowledge_base 跨日期重复，按真实时间戳过滤；内置 WATCHLIST（memory/watchlist.md 权威，含奔图科技002180/中船汉光300847，已过滤撤出的上能电气300827）。; Cron 新建 `daily-news-reading-push` (id 537eb1ff) 周一~五 15:42 执行并推 telegram。 [score=0.72 recalls=0 avg=0.720 source=memory/2026-08-07.md:13-20]
<!-- openclaw-memory-promotion:memory:memory/2026-08-07.md:23-33 -->
- daily-a-share-telegram-push 超时排查修复 ⭐ (2026-08-07): 8 层根因修复——①新浪日K提到第一(0.1s/只)、akshare 只试1次不sleep；②trader_stock_picks `_akshare_call` 改单次失败即None、修 key 误写 `"roe, 0"`、score_* 加 `or 0` 兜底；③LangGraph `should_continue` 改单遍线性执行不再整图回环；④node_check_done 逻辑反了修复；⑤加 rule_health_comment/rule_risk_advice 规则化兜底不依赖 LLM；⑥node_make_report 的 build_report_text 从 final_state 直接拼报告；⑦risk 误判(score -3~+4 小整数)改纯按 level 子串判断，致命风险需≥10个强烈卖出或数据大面积失败；⑧node_fetch_spot 加3次重试。; 结果: 稳定运行 48-82s(<120s),exit=0,无ALERT,报告完整；cron timeoutSeconds 120→240 余量。⚠️ 教训: analysis/agent/ 与 service.py 含大量 `\uFFFD` 损坏字符(早期编码事故)，字符串匹配/编辑需归一化。 [score=0.78 recalls=0 avg=0.780 source=memory/2026-08-07.md:23-33]
<!-- openclaw-memory-promotion:memory:memory/2026-08-07.md:35-41 -->
- 对其它脚本同步 akshare/baostock 数据源修复 (2026-08-07): close_scan_v2.py 改新浪日K首选→pytdx→akshare单次，实测30只 9.1s(原~270s) 30/30成功；adaptive_dual/adaptive_trader/portfolio_sim 用 baostock(主)+新浪兜底，sleep 3→1s；backtest_strategies(周回测非关键) 备用sleep改1s；service.py 新浪日K→pytdx→akshare单次 30只 5.9s。; 结论: 所有 cron 关键脚本(收盘推送/扫盘/组合模拟)已无 akshare被拦+sleep重试瓶颈。 [score=0.74 recalls=0 avg=0.740 source=memory/2026-08-07.md:35-41]
<!-- openclaw-memory-promotion:memory:memory/2026-08-07.md:43-49 -->
- 启用真 DeepSeek LLM 解读（agent 健康度/风险建议）⭐ (2026-08-07): key 写入 `~/.openclaw/.env` 新增 `DEEPSEEK_API_KEY`(权限600不进git,备份 .env.bak.20260807)；llm_tool.py 默认指向 api.deepseek.com/v1 + deepseek-chat（openai-compatible），读 key 优先级 DEEPSEEK_API_KEY > OPENAI_API_KEY，加 `_load_dotenv_if_needed()` 供 cron isolated 进程读到，`get_market_health()` 改读 data/market_health_<日期>.json 的 summary.total_score(close_scan 每日生成,实测返回7)，加 `extract_llm_content(resp)` 正确取 choices[0].message.content；nodes.py 加 `_parse_json_loose(text)` 健壮JSON提取，LLM 非JSON落规则兜底。; 实测: run_agent 101s, ALERT=0, error_count=0, 健康度=7/10 + DeepSeek 自然语言解读 + 30条逐股 LLM 建议(8 BUY/22 HOLD) + 风险"无"。LLM 完整生效，不可用时自动降级规则兜底。 [score=0.78 recalls=0 avg=0.780 source=memory/2026-08-07.md:43-49]
