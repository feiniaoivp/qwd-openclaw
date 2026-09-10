"""
Data Router - 统一数据路由入口
================================
对外提供统一接口，内部自动处理：
- 数据源优先级降级
- 缓存命中
- 熔断与健康检查
- 错误重试
"""

import os
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime

import pandas as pd

from analysis.data_layer.providers.sina_realtime import get_provider as get_sina_realtime
from analysis.data_layer.providers.sina_daily import get_provider as get_sina_daily
from analysis.data_layer.providers.baostock import get_provider as get_baostock
from analysis.data_layer.providers.akshare_finance import get_provider as get_akshare_finance
from analysis.data_layer.cache import get_cache

log = logging.getLogger(__name__)

# 缓存 TTL 配置 (秒)
CACHE_TTL = {
    "spot": 30,           # 实时行情 30秒
    "daily": 3600,        # 日线 1小时
    "finance": 86400,     # 财务 1天
    "news": 600,          # 新闻 10分钟
    "index": 86400,       # 指数 1天
}

class DataRouter:
    """统一数据路由器"""
    
    def __init__(self):
        self._sina_realtime = None
        self._sina_daily = None
        self._baostock = None
        self._akshare_finance = None
        self._cache = None
        
        # 健康检查状态
        self._last_health_check = 0
        self._health_check_interval = 600  # 10分钟
    
    @property
    def sina_realtime(self):
        if self._sina_realtime is None:
            self._sina_realtime = get_sina_realtime()
        return self._sina_realtime
    
    @property
    def sina_daily(self):
        if self._sina_daily is None:
            self._sina_daily = get_sina_daily()
        return self._sina_daily
    
    @property
    def baostock(self):
        if self._baostock is None:
            self._baostock = get_baostock()
        return self._baostock
    
    @property
    def akshare_finance(self):
        if self._akshare_finance is None:
            self._akshare_finance = get_akshare_finance()
        return self._akshare_finance
    
    @property
    def cache(self):
        if self._cache is None:
            self._cache = get_cache()
        return self._cache
    
    # ══════════════════════════════════════
    # 实时行情
    # ══════════════════════════════════════
    
    def get_spot(self, symbols: List[str]) -> Dict[str, dict]:
        """
        获取实时行情
        降级链: 新浪 hq.sinajs.cn (主)
        """
        if not symbols:
            return {}
        
        # 实时行情不缓存或极短缓存
        cache_key = f"spot:{','.join(sorted(symbols))}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        
        # 尝试新浪实时
        try:
            result = self.sina_realtime.get_spot(symbols)
            if result:
                self.cache.set(cache_key, result, CACHE_TTL["spot"])
                return result
        except Exception as e:
            log.warning(f"[Router] 新浪实时行情失败: {e}")
        
        # TODO: pytdx 备用
        # TODO: akshare 备用
        
        return {}
    
    # ══════════════════════════════════════
    # 历史日线
    # ══════════════════════════════════════
    
    def get_daily(self, symbol: str, name: str = "", 
                  start_date: str = "20240101", 
                  adjust: str = "qfq") -> Optional[pd.DataFrame]:
        """
        获取历史日线
        降级链: 新浪日K jsonp (主) → pytdx → baostock → akshare
        
        返回: DataFrame (date, open, high, low, close, volume) - 已按日期排序
        """
        cache_key = f"daily:{symbol}:{start_date}:{adjust}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        
        # Level 1: 新浪日K jsonp (主源，稳定、周末可用、返回不复权)
        try:
            df = self.sina_daily.get_daily(symbol, n=5000)
            if df is not None and len(df) >= 2:
                # 按开始日期过滤
                if start_date:
                    cut = pd.to_datetime(start_date)
                    df = df[df["date"] >= cut]
                if len(df) >= 2:
                    log.info(f"  ✅ {name}({symbol}): {len(df)} 条日线 [新浪日K]")
                    self.cache.set(cache_key, df, CACHE_TTL["daily"])
                    return df
        except Exception as e:
            log.warning(f"  ⚠️ {name} 新浪日K失败: {e}")
        
        # Level 2: baostock (前复权)
        try:
            df = self.baostock.get_daily(symbol, start_date=start_date, adjustflag="2")
            if df is not None and len(df) >= 2:
                log.info(f"  ✅ {name}({symbol}): {len(df)} 条日线 [baostock前复权]")
                self.cache.set(cache_key, df, CACHE_TTL["daily"])
                return df
        except Exception as e:
            log.warning(f"  ⚠️ {name} baostock失败: {e}")
        
        # Level 3: akshare (前复权，最后兜底)
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily", 
                                    start_date=start_date, adjust="qfq")
            if df is not None and not df.empty:
                df = df.rename(columns={
                    '日期': 'date', '开盘': 'open', '收盘': 'close',
                    '最高': 'high', '最低': 'low', '成交量': 'volume'
                })
                for c in ['open', 'close', 'high', 'low', 'volume']:
                    df[c] = pd.to_numeric(df[c], errors='coerce')
                df['date'] = pd.to_datetime(df['date'])
                df = df.sort_values('date').reset_index(drop=True).dropna()
                if len(df) >= 2:
                    log.info(f"  ✅ {name}({symbol}): {len(df)} 条日线 [akshare]")
                    self.cache.set(cache_key, df, CACHE_TTL["daily"])
                    return df
        except Exception as e:
            log.warning(f"  ⚠️ {name} akshare失败: {e}")
        
        return None
    
    # ══════════════════════════════════════
    # 指数基准 (预加载缓存)
    # ══════════════════════════════════════
    
    _index_cache: Dict[str, pd.DataFrame] = {}
    
    def preload_index_benchmarks(self, start_date: str, end_date: str = None) -> None:
        """预加载指数基准数据，供回测/验证复用"""
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        
        for code, name in [("sh.000300", "沪深300"), ("sh.000905", "中证500")]:
            if code in self._index_cache:
                continue
            df = self.baostock.get_index_daily(code, start_date, end_date, adjustflag="3")
            if df is not None and len(df) >= 2:
                self._index_cache[code] = df
                log.info(f"  ✅ 指数基准 {name}({code}) 预加载: {len(df)}条")
            else:
                log.warning(f"  ⚠️ 指数基准 {name}({code}) 预加载失败")
    
    def get_index_daily(self, index_code: str, 
                        start_date: str = "20240101", 
                        end_date: str = None) -> Optional[pd.DataFrame]:
        """获取指数日线 (优先读缓存)"""
        if index_code in self._index_cache:
            df = self._index_cache[index_code]
            if start_date:
                cut = pd.to_datetime(start_date)
                df = df[df["date"] >= cut]
            return df
        return self.baostock.get_index_daily(index_code, start_date, end_date, adjustflag="3")
    
    # ══════════════════════════════════════
    # 财务/新闻/公告
    # ══════════════════════════════════════
    
    def get_financial_abstract(self, symbol: str) -> Optional[pd.DataFrame]:
        cache_key = f"finance:{symbol}:abstract"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        df = self.akshare_finance.get_financial_abstract(symbol)
        if df is not None:
            self.cache.set(cache_key, df, CACHE_TTL["finance"])
        return df
    
    def get_stock_news(self, symbol: str) -> Optional[pd.DataFrame]:
        cache_key = f"news:{symbol}:stock"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        df = self.akshare_finance.get_stock_news(symbol)
        if df is not None:
            self.cache.set(cache_key, df, CACHE_TTL["news"])
        return df
    
    def get_global_news(self) -> Optional[pd.DataFrame]:
        cache_key = "news:global"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        df = self.akshare_finance.get_global_news()
        if df is not None:
            self.cache.set(cache_key, df, CACHE_TTL["news"])
        return df
    
    def get_notices(self, date: str = None) -> Optional[pd.DataFrame]:
        cache_key = f"news:notices:{date or datetime.now().strftime('%Y%m%d')}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        df = self.akshare_finance.get_notices(date)
        if df is not None:
            self.cache.set(cache_key, df, CACHE_TTL["news"])
        return df
    
    # ══════════════════════════════════════
    # 健康检查
    # ══════════════════════════════════════
    
    def health_check(self) -> Dict[str, bool]:
        """检查各数据源健康状态"""
        import time
        now = time.time()
        if now - self._last_health_check < self._health_check_interval:
            return getattr(self, "_last_health_result", {})
        
        result = {
            "sina_realtime": self.sina_realtime.is_healthy(),
            "sina_daily": self.sina_daily.is_healthy(),
            "baostock": self.baostock.is_healthy(),
            "akshare_finance": self.akshare_finance.is_healthy(),
        }
        self._last_health_check = now
        self._last_health_result = result
        return result


# 全局单例
_router_instance: Optional[DataRouter] = None

def get_router() -> DataRouter:
    global _router_instance
    if _router_instance is None:
        _router_instance = DataRouter()
    return _router_instance