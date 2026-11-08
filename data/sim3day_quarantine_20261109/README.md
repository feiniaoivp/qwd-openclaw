# sim3day 污染隔离 (2026-11-09 05:1x)

simulate_3day.py (11-08 晚间施工会话产物) 将假 PENDING 意向直接写入生产
data/runtime/trade_intents/（真实股票代码+真实日期+max_age_days=5 内）,
若不处理, 周一 18:45 生产扫描会按假单成交 10 笔买入。

隔离内容 = 污染现场原样复制 (sha256 前 12 位见 clean 脚本日志):
- 2026-11-04_intents.json.polluted: 5 只假买入 (600030/600036/000987/600584/002156)
- 2026-11-05_intents.json.polluted: 同上 5 只
- 2026-11-08_intents.json.polluted: 周日假意向 600030 (生产周日不跑扫描, 纯 sim 产物)
- 2026-11-05_intents.json.bak-2026-11-06.polluted: sim flush 写出的假 .bak

生产侧修复:
- 11-04 文件 = 真实 3 笔 (巨化/福耀/恒生 买入) 按 .bak-2026-11-05 + 流水终态重建, FILLED
- 11-05 文件 = {} (生产 11-05 无意向, 11-06 零成交可证)
- 11-08 文件 = 删除 (已隔离)
- 11-06 真实 3 笔卖出意向 = 未触碰 (周一按设计 T+1 消费)

根因修复: simulate_3day.py 重写为 temp INTENTS_DIR, 永不再触生产路径。
