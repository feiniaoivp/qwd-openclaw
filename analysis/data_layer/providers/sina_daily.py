"""
新浪日K线历史提供者 (JSONP 接口)
===================================
周末也稳定、单次 ~0.1s/只、返回原始不复权日K。
支持 money.finance.sina.com.cn 接口（quotes.sina.cn 已失效）。
"""

import urllib.request
import re
import json
import pandas as pd
import logging
from typing import Optional

log = logging.getLogger(__name__)

class SinaDailyProvider:
    """新浪历史日K (JSONP / JSON 数组)"""
    
    BASE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    
    def __init__(self):
        self._healthy = True
        self._consecutive_failures = 0
    
    def _to_sina_code(self, symbol: str) -> str:
        if symbol.startswith("6") or symbol.startswith("9"):
            return f"sh{symbol}"
        return f"sz{symbol}"
    
    def get_daily(self, symbol: str, n: int = 300) -> Optional[pd.DataFrame]:
        """
        获取历史日线（不复权）
        返回 DataFrame: date, open, high, low, close, volume
        """
        sina_sym = self._to_sina_code(symbol)
        url = f"{self.BASE_URL}?symbol={sina_sym}&scale=240&ma=no&datalen={n}"
        req = urllib.request.Request(
            url,
            headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
        )
        
        try:
            raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        except Exception as e:
            log.warning(f"[SinaDaily] {symbol} 请求失败: {e}")
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._healthy = False
            return None
        
        self._consecutive_failures = 0
        self._healthy = True
        
        # 解析 JSON 数组（非标准 JSONP，直接是数组）
        try:
            arr = json.loads(raw)
        except json.JSONDecodeError:
            # 尝试提取数组部分
            i, j = raw.find("["), raw.rfind("]")
            if i < 0 or j <= i:
                log.warning(f"[SinaDaily] {symbol} 响应格式异常")
                return None
            arr = json.loads(raw[i:j+1])
        
        if not arr:
            return None
        
        df = pd.DataFrame(arr)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["date"] = pd.to_datetime(df["day"])
        df = df[["date", "open", "high", "low", "close", "volume"]].dropna()
        
        if len(df) >= 2:
            return df.sort_values("date").reset_index(drop=True)
        return None
    
    def is_healthy(self) -> bool:
        return self._healthy


_instance: Optional[SinaDailyProvider] = None

def get_provider() -> SinaDailyProvider:
    global _instance
    if _instance is None:
        _instance = SinaDailyProvider()
    return _instance