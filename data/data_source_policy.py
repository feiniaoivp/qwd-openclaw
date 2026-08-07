# A股数据源路由策略 (Data Source Routing Policy)
# 2026-08-02 由用户指定，为本工作区权威数据源优先级。
# 约定: 每类数据按 "首选 → 兜底" 顺序尝试，首选失败则降级下一层。

DATA_SOURCE_ROUTING = {
    "realtime_quote":   ["sina", "pytdx", "akshare"],      # 实时行情
    "kline_history":    ["pytdx", "akshare"],              # K线历史
    "sector_rank":      ["ths", "akshare"],                # 板块排行 (同花顺)
    "sector_cons":      ["eastmoney_datacenter", "akshare"],  # 板块成分股 (东方财富)
    "fundamental":      ["akshare"],                       # 财务数据
    "news":             ["akshare"],                       # 新闻/公告 (stock_news_em / stock_info_global_em)
}

# 已知可用/不可用状态 (2026-08-02 实测):
# ✅ 新浪(sina): hq.sinajs.cn 实时 + quotes.sina.cn 日K jsonp — 当前唯一稳定源
#    (均需 Referer: https://finance.sina.com.cn, gbk解码)
# ❌ pytdx: 已装1.72, 仅 123.125.108.14:7709 握手成功, bars 全 None → 未接入
# ❌ akshare东财历史 / baostock: 网络被限流
USER_MEMO = "按此路由优先选择数据源, 首选失败自动降级; 需人工/脚本先测可用性。"
