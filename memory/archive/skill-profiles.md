# 技能档案与工具文档

> 从 MEMORY.md 归档，归档时间：2026-11-11 23:52:38
> 共 3 个小节。按需检索，不常驻上下文。

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

### 2026-08-10 竞价/盘前 cron 反复超时根治（LLM 无关）⭐教训
*   **事件:** auction-real-scan-0920 + preopen-bidding-scan-0900 单日失败8次, 全部 `model-call-started` 超时(300s)。
*   **根因:** 两 cron 设成 `agentTurn`(isolated agent 调 DeepSeek), 但竞价/盘前扫描是**结构化数据快照, 本不需要 LLM**。LLM 在 cron 沙箱不稳定 → 反复超时。脚本本身 0-8s 秒级跑通。
*   **修复:** 1) auction_scan.py + preopen_scan_*.py 各加 `build_brief()` 规则化简报; 2) 两 cron 从 agentTurn 改 `command` 脚本直推, 完全绕过 LLM。手动触发验证: auction 5.5s / preopen 7.8s 成功, consecutiveErrors 4→0。
*   **教训(通用):** 结构化数据快照类 cron(竞价/盘前/收盘扫描) 一律用 `command` 脚本直推 + 脚本内 build_brief() 规则化组装简报, **不要**设成 agentTurn 走 LLM —— 又慢又不稳定, 还会反复超时。

### 2026-08-19 中联信号HOLD vs SELL矛盾 + 破位回落拦截（2处修复）
*   **中联矛盾根因:** nodes.py `rule_risk_advice` 的中联强制SELL `if sym=="000157" and zl_sell` 只看信号层 sell=True，**不看是否实际持仓**。sig2_sell = macd_bear or close<EMA26 是纯技术条件 → 空仓时 close<EMA26 就误判 SELL，与 030 仲裁"持有"矛盾。08-11 修过"综合最优触发卖出强制SELL"但没加持仓判断，空仓也触发 = 变体回归。**修复:** zl_sell 判定结合 zhonglian_state.json 实际持仓，仅对应策略 position=True 才视为真卖出；空仓→HOLD。备份 backups/nodes_20260819_before_zhonglian_fix.py。
*   **新增修复#4 破位回落拦截:** 08-19 国瓷材料盘中破止损(-13.7%近跌停)仍被给"强烈买入"——BUY评级失当。nodes.py "强烈买入/关注"分支最前加：**收盘价 < EMA26 时 BUY 强制降级 HOLD(禁追)**。依据 08-02"天量高开低走=出货"教训。
*   **单测全过(真实K线端到端):** 空仓中联=HOLD / 持仓中联=SELL(风控保留) / 国瓷破位=HOLD / 招行健康=BUY。
*   **11只连买回落回看(报告 analysis/backtest/2026-08-19_连买回落回看.md):** 🔴紧急3(国瓷300285 5日-13.4%盘中破止损清仓、福莱特601865 5日-11%破EMA20/26禁买、奔图002180盘中破止损17.91)；🟠观望2(恒生电子600570守20.61、中船汉光300847 08-18已减仓)；🟡警惕2(安达维尔300719守11.15、中信金属601061守11.45)；🟢正常4(恒力石化+6.3%、福耀玻璃+4.5%、招商银行+1.2%、中信特钢+1.9%)。
*   **教训:** 卖出建议/强制SELL等风控规则必须绑定"实际持仓"状态再执行，只看技术信号标志会在空仓时产生"无仓可卖却喊卖出"的矛盾；BUY建议必须有"破位回落(价<EMA26)拦截"防接飞刀。

### 2026-08-19 三因素共振买入门控体系（ResonanceGate）搭建+接入+演进 ⭐
*   **需求:** 个股基本面+市场情绪+技术方向三维独立维度共同指向同一方向才发买入信号，避免单一技术信号接飞刀（08-19 恐慌市国瓷近跌停还喊"强烈买入"教训）。
*   **共享门控模块:** `analysis/three_factor_helper.py` 的 `ResonanceGate` 类（情绪缓存6h+基本面按个股缓存+check_buy判定）。数据源：技术=新浪/baostock日K(复用calc_full_signal)；基本面=baostock(query_profit_data年化ROE + query_growth_data净利增速 + 净利率)；情绪=ak.stock_market_activity_legu(涨跌家数/活跃度)。
*   **接入三个链路:** ① nodes.py rule_risk_advice（买入/关注信号在破位拦截后加门控）；② adaptive_dual.py 仲裁（final 买入类经 check_buy，未过→HOLD+position_mult=0，结果加 resonance_gate 字段记录拦截原因）；③ portfolio_sim.py execute_trade（买入分支加门控→HOLD）。
*   **门控演进（用户拍板逐步简化）:** 一票否决(情绪+基本面+技术任一不达标则否决)→通道B豁免(技术≥85 且 基本面≥70 不受情绪限制，防恐慌市完全冻结)→纯加权评分制 buy_score=技术×0.45+基本面×0.30+情绪×0.25, 买入线60，**去掉所有一票否决**（极端恐慌情绪分只贡献~2分，技术90+基本面好仍可破60；基本面差的被拒）。
*   **30只真实基本面分分布:** 7只≥70(中信建投94.9 ROE24.96%+增速69%、天齐89.2、越秀86.7、巨化78.1、中金77.5、中信证券75.8、恒力石化70.9)，其余偏低；豁免线70合理卡位（福耀67.2/招行66.1不过）。
*   **端到端验证(真实恐慌市情绪分8.1/上涨仅8%):** nodes招行→HOLD(拦截)✓/portfolio_sim招行买入→HOLD✓/中信建投(基本面94.9技术88)→三链路全BUY成交✓/福莱特(基本面8.1)→拦截✓。恐慌市完全不开仓，符合用户严格防守要求。
*   **增强策略试跑→回滚:** 新增 signal_enhanced(ATR\(2×/3×\)+布林带宽挤压突破+20日高低+斐波那契0.382/0.5/0.618) 回测(30只×2.5年) 平均17.6%<纯MACD 49.7%<EMA金叉43.9%、夏普0.068偏低、回撤-35.8%全池最大 → **判定不佳回滚**，恢复原5策略，保留纯加权门控。**启示:** 单一技术增强难在收益/夏普上超越纯MACD；趋势跟踪(纯MACD/EMA)夏普仍最忧；增强策略只能当"稳健辅助"非"收益引擎"。
*   **baostock 会话加固:** 长循环丢包(接收数据异常/Broken pipe)导致基本面全空 → 加 _bs_ensure_login/_bs_mark_broken + 失败强制 logout/login 重登，修复后30只基本面100%取到。
*   **备份:** backups/resonance_gate/*_20260819_213213.py

### 2026-08-20 斐波那契扩展位前瞻跟踪系统（预先声明→事后检验）⭐
*   **用户原则（关键方法论）:** 「必须尽量多做预见性想法并在实践中验证，避免射完箭再画靶」→ 拒绝拿当前数据回看当下建结论(事后诸葛亮)，改为**预先声明+前瞻跟踪+事后检验**。
*   **新建:** `analysis/fib_extension_scan.py`(扫描止盈位) + `analysis/fib_track.py`(前瞻跟踪)；状态机 `data/fib_tracking_state.json`：无基线→锁定当日各股斐波那契扩展位(1.0/1.272/1.618/2.618)为[预先声明的预测目标]，锁定后不回改；有基线→每日对照验证(是否触及锁定止盈位，触及后5日兑现[回落3%]/突破[续涨2%]/观察)。命中率低→归为无效弃用而非自我印证。
*   **今日基线已锁 2026-08-20:** 27只有效止盈位(3只券商 no_uptrend 跳过)。例：招商银行1.618=44.86、长电科技1.618=120.09、中芯国际1.618=162.04、亿纬锂能1.618=62.93。
*   **数据源坑（新增）:** 新浪日K jsonp 接口清晨段(~05:00) HTTP456 限流, 需冷却数分钟恢复；实时 hq.sinajs.cn 不受限。正则兼容 `var _x=([...]);` 带括号/分号。并行并发 10×30 触发限流 → 改串行退避重试。
*   **cron:** `fib-tracking-daily`(id 7d1b9562) 交易日15:45跑 fib_track.py，锁 deepseek-chat，推 telegram 626141741。注意 cron add 接口 payload.kind 只收 systemEvent/agentTurn（command 是旧格式 legacy）。
*   **vs 恒力石化案例:** 旧扫描(画靶做法)把恒力石化标成 1.272-1.618 止盈区；改用预声明体系后不预下结论，等未来价格触达锁定位才验证。
*   **用户决定（08-20 04:14）:** 放弃「设置过滤器」想法，不再推进不讨论（未追问细节，按用户意愿作行动结论；若重启需用户主动提起）。

### 2026-08-11 盘后监督报告 4 问题修复 ✅
*   背景: 08-11 17:07 用户转发的 15:39 日常监督审查指出 4 问题, 全部根因定位修复, 端到端验证 0 误报。
*   **①持仓股3只信号缺失(恒生/国瓷/巨化) [链路问题]:** signal_audit 读 daily/*_dual.md, 但收盘链路 run_agent 不产 dual 文件 → 审计误报"缺失"; 市场健康分 null = market_health_*.json 未生成。**修复**: daily-supervision-review cron(2ca9e493) 执行串改为 "adaptive_dual → market_health_score → signal_audit → challenge_review", 超时 180→300s。修复后审计 0 findings、全绿、健康分 6.0。
*   **②10只BUY无止损(违反030) [nodes.py]:** rule_risk_advice 的 BUY 只写"信号级X分Y"无止损位。**修复**: 所有 BUY 自动附参考止损(现价×0.95)+支撑(×0.92)+"跌破止损无条件离场"。单测✅。
*   **③判断模板化(连3日同理由) [nodes.py]:** 死模板"MACD多头且EMA多头排列可逢低买入"。**修复**: reason 携带现价+止损位+支撑位差异化文案。单测✅。
*   **④中联重科 SELL vs HOLD 矛盾 [nodes.py]:** 综合最优策略 sell=True 但 level"中性"被映射 HOLD, 卖出被吞。**修复**: 新增中联综合最优触发卖出→final_advice 强制 SELL(030风险第一)。单测✅。
*   改动: analysis/agent/nodes.py(核心规则化建议), daily-supervision-review cron。备份 backups/nodes_20260811_before_fix.py。4 项单测全过, 端到端审计 0 误报。

### 2026-08-09 技能库真实变更（skill-version-watcher 捕获）
*   **self-improving v1.2.16 → self-improving-agent v4.0.2**（升级+改名, 08-08 21:27 更新）; **hf-mem v1.0.10** 新增(08-08 21:20 安装)。baseline memory/skill-versions.json 已更新。
