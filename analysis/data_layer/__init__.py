"""
Data Layer - 统一数据访问层
============================

提供统一的数据获取接口，内部自动处理多数据源降级、缓存、熔断。

数据源优先级（按 MEMORY.md 2026-08-02 权威版）：
- 实时行情: 新浪 hq.sinajs.cn (稳定) → pytdx → akshare
- K线历史: 新浪日K jsonp (稳定, 0.1s/只) → pytdx → akshare东财(单次不sleep)
- 财务/新闻: akshare (同花顺/新浪/巨潮)
- 指数基准: baostock (预加载缓存)

使用示例：
    from analysis.data_layer import get_router
    router = get_router()
    spot = router.get_spot(["600030", "601066"])
    daily = router.get_daily("600030", start_date="20240101")
"""

from analysis.data_layer.router import DataRouter, get_router
from analysis.data_layer.cache import DiskCache, get_cache

__all__ = [
    "DataRouter", "get_router",
    "DiskCache", "get_cache",
]