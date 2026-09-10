"""
Akshare 财务/新闻/公告提供者
============================
仅用于财务数据、新闻、公告类接口（行情类已有更稳定源）。
"""

import akshare as ak
import pandas as pd
import logging
from typing import Optional, List, Dict

log = logging.getLogger(__name__)

class AkshareFinanceProvider:
    """Akshare 财务/新闻/公告"""
    
    def __init__(self):
        self._healthy = True
        self._consecutive_failures = 0
    
    def get_financial_abstract(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取财务摘要 (同花顺)"""
        try:
            df = ak.stock_financial_abstract_ths(symbol=symbol)
            if df is not None and not df.empty:
                self._consecutive_failures = 0
                self._healthy = True
                return df
        except Exception as e:
            log.warning(f"[AkshareFinance] {symbol} 财务摘要失败: {e}")
            self._consecutive_failures += 1
        return None
    
    def get_stock_news(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取个股新闻 (东财)"""
        try:
            df = ak.stock_news_em(symbol=symbol)
            if df is not None and not df.empty:
                self._consecutive_failures = 0
                self._healthy = True
                return df
        except Exception as e:
            log.warning(f"[AkshareFinance] {symbol} 新闻失败: {e}")
            self._consecutive_failures += 1
        return None
    
    def get_global_news(self) -> Optional[pd.DataFrame]:
        """获取宏观/行业政策快讯 (东财)"""
        try:
            df = ak.stock_info_global_em()
            if df is not None and not df.empty:
                self._consecutive_failures = 0
                self._healthy = True
                return df
        except Exception as e:
            log.warning(f"[AkshareFinance] 宏观新闻失败: {e}")
            self._consecutive_failures += 1
        return None
    
    def get_notices(self, date: str = None) -> Optional[pd.DataFrame]:
        """获取全市场公告 (东财)"""
        try:
            if date is None:
                from datetime import datetime
                date = datetime.now().strftime("%Y%m%d")
            df = ak.stock_notice_report(symbol="全部", date=date)
            if df is not None and not df.empty:
                self._consecutive_failures = 0
                self._healthy = True
                return df
        except Exception as e:
            log.warning(f"[AkshareFinance] 公告失败: {e}")
            self._consecutive_failures += 1
        return None
    
    def is_healthy(self) -> bool:
        return self._healthy


_instance: Optional[AkshareFinanceProvider] = None

def get_provider() -> AkshareFinanceProvider:
    global _instance
    if _instance is None:
        _instance = AkshareFinanceProvider()
    return _instance