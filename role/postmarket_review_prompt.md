# 盘后自动化复盘 Prompt（模板 Part 2A）

> 由 `daily-a-share-telegram-push` cron 触发。读取本文件作为复盘分析框架。
> 数据源：新浪实时行情 / baostock · 脚本：`analysis/close_scan_v2.py` + `analysis/adaptive_trader.py`

请针对今日（{{DATE}}）A 股市场数据，结合我的关注池（见 `memory/watchlist.md`）与模拟盘持仓（`data/zhonglian_state.json`）进行盘后总结分析：

## 1. 大盘与资金面
- 指数表现（上证、创业板、科创50）及量能变化。
- 主力资金、北向资金流向及主力净流入前三行业（尽量获取，网络受限则标注"未获取"）。
- 龙虎榜核心标的与机构动向（可用 akshare `stock_lhb_detail_em` 尝试，失败则跳过）。

## 2. 持仓与自选股监控
- 对比今日表现，是否有标的触及关键支撑/阻力位，或达到设定的止损/止盈阈值。
- 是否存在重大公告（业绩预告、减持、立案等，用 akshare `stock_info_global_em`，失败则注明）。

## 3. 归因与次日应对策略
- 今日上涨/下跌的主要驱动逻辑（政策、消息面或技术面）。
- 次日开盘关注重点及预案（突破某位增仓 / 跌破某位减仓）。

## 输出格式
- Markdown 结构化：**【数据事实】→【逻辑推演】→【风险预警】→【操作建议】**。
- 风险优先：每个标的务必标注止损位与最大回撤。
- 合规：客观分析，不给盲目买卖建议。
- 结果自动更新至 `memory/{{DATE}}.md`，并推送到 Telegram。

## 技术面信号补充
- 运行 `analysis/adaptive_trader.py`（自适应策略分配扫描）与 `analysis/close_scan_v2.py`（全量通用扫描），提取买卖信号。
- 关注 31 只股票的 MACD/KDJ/RSI 状态、超买超卖区（RSI<30 超卖 / >70 超买）。
