# 选股宇宙扩展 + 三层漏斗设计（2026-11-05，用户拍板）

> 背景：36 只关注池是建系统时的临时起意。用户目标：**系统在全市场内自动筛选进入核心关注盘**；
> 全市场优先，算力/工程量大则沪深300 起步可接受。本文档为落地蓝图 + 首阶段实施记录。

## 一、三层漏斗总览

```
Tier-0 宇宙层（数据湖）          Tier-1 候选池（因子筛选）         Tier-2 核心关注盘（影子→审批）
─────────────────────          ─────────────────────           ─────────────────────────
沪深300+中证500 ≈800 只         每周因子打分 → 前 50~100 名        影子关注池（对照 ≥4 周）
日线 2021→今，前复权            IC 证据门禁的因子库加权            → 人工 /approve 晋升
(--universe all 预留全市场)      + 流动性/可交易性过滤             → WATCHLIST 版本化替换
```

- **研究宇宙 ≠ 交易宇宙**：因子 IC 研究在 Tier-0 做（横截面 N≥800 才有统计意义），交易应用仍落在
  Tier-2 关注池。36 只票上做横截面回归 = 自由度灾难（本轮讨论已定性的第一前提）。
- **影子运行**：候选池先影子产出 ≥4 周，与现有 36 只对照（命中率/超额/换手），不直接接管——
  与 `apply_strategy_pending` 候审闸门同款哲学：系统建议、人拍板、可回滚。

## 二、Tier-0：宇宙数据湖（本日落地）

- 脚本：`scripts/build_universe.py`
  - 成分：`bs.query_hs300_stocks() + query_zz500_stocks()` 按字段名解析（**列序不可假设**，实测
    返回 `updateDate,code,code_name`），并集 800 只
  - 数据：2021-01-01 → 最近已收盘交易日，日线前复权，字段 `date,open,high,low,close,volume,amount,turn,pctChg`
  - 产出：`data/universe/members.csv` + `data/universe/daily/{code}.csv` + `build_report_*.json`
  - 增量：已新鲜的票跳过（末行 date ≥ 最近已收盘交易日），全量重建用 `--full-refresh`
  - 退出码：失败率 ≥5% → 1（供 cron 告警）
- **🔴 baostock 并发铁律（今日实证升级）**：全局连接**非线程安全**——4 线程持续批量拉取触发
  zlib 流交叉损坏（`invalid distance too far back`）+ 忙等挂死（kill -TERM 无效需 -9）。
  早上 6w×6sym 探测 0.11s 通过是**小样本侥幸**，不能外推到持续批量。宇宙级拉取一律串行
  （实测 ~4s/只，800 只首建 ≈50min，可接受；增量后续分钟级）。
  关联：.learnings ERR-20261105-BaostockProbeOrphanSpin（忙等家族）。
- 全市场扩展（下阶段）：`--universe all` 预留位已留；启用前置条件 =
  ①hs300zz500 链路验证 ②流动性预过滤（剔除 ST/上市<1年/均额<3000万）③存储评估（~5000 只 ≈ 500MB）。

## 三、Tier-1：因子库 + IC harness（下一步，工作量 3~4 天）

- `analysis/factor_lib/`：因子函数统一签名 `(df, asof) → float`，注册表带元数据
  （universe/horizon/last_audit/audit_interval）。
- IC harness：RankIC、IC 衰减（h=1/5/10）、IC_IR、因子相关性聚类去冗余——**研究宇宙 = Tier-0**。
- 入池门禁（三条件全过才进权重）：|RankIC|>0.02 且 IC_IR>0.3 且与存量因子簇相关<0.7。
- 首批 15~20 因子（现有组件提取：MACD/EMA斜率/OBV/ATR/换手/资金流/chip/基本面），每批 ≤5 过门禁。
- 与 030 评分器关系 = 演进：MACD 骨干保留（10-20 审计结论），因子库提供有 IC 证据的新臂。
- Regime 条件化权重（最后做）：`w = 0.5×基准 + 0.5×近60日条件IC`，clamp 上下限；**不做自由回归**。
- 状态基座：SQLite 状态层（第一步 1a）先行——IC 计算需批量读历史，JSON 湖慢且脏。

## 四、Tier-2：影子关注池 → 审批晋升（候选池稳定后）

- 周频产出 `data/universe/shadow_watchlist_{date}.json`（Top-N + 理由 + 与现池差集），
  推 Telegram 供人审；批准后写入 `data/watchlist_versions/` 并切换 `fib_extension_scan.WATCHLIST`
  读取源（SSOT 从硬编码演进为「版本文件 + 常量回退」）。
- 晋升门禁（复用既有模式）：连续 ≥2 周影子池信息比 ≥ 现池 + 用户 /approve。
- 期间 `strategy_map_guard` 需同步感知新 SSOT（消费方 6 处已列为 blast_radius）。

## 五、里程碑

| # | 事项 | 状态 |
|---|---|---|
| M1 | Tier-0 数据湖首建（800 只×5.9y） | 🔄 今夜 nohup 构建中，明早验收 build_report |
| M2 | SQLite 状态层（含路径 SSOT 收口，终结 data/runtime 兼容层） | 待做 1~2 天 |
| M3 | IC harness + 首批因子 | 待做 3~4 天（依赖 M2） |
| M4 | 周频筛选 cron + 候选池产出 | 待做 0.5 天（依赖 M3） |
| M5 | 影子关注池 + 审批晋升流 | 待做 1 天（依赖 M4，运行 ≥4 周后切） |
| M6 | 全市场扩展（流动性预过滤后） | 预留，M5 稳定后评估 |
