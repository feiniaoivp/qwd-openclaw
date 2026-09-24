"""
Baostock K线/指数/财务提供者
=============================
备用数据源，复用全局单例 bs_session 会话管理。
核心改进（2026-09-20）：批量查询使用 session_scope 显式复用会话，
避免每只股票单独 login/logout 导致的连接池耗尽 ([Errno 9] Bad file descriptor)。
"""

import pandas as pd
import logging
from typing import Optional, List
from datetime import datetime

from analysis.bs_session import (
    ensure_login, query_history_k_data_plus_retry, 
    session_scope, get_session_status, force_relogin
)

log = logging.getLogger(__name__)

class BaostockProvider:
    """Baostock 历史日线、指数、财务"""
    
    def __init__(self):
        self._healthy = True
        self._consecutive_failures = 0
        self._max_rows = 5000
    
    def _to_bs_code(self, symbol: str) -> str:
        if symbol.startswith("6") or symbol.startswith("9"):
            return f"sh.{symbol}"
        return f"sz.{symbol}"
    
    def _query_with_session(self, bs_code: str, fields: str, start_ymd: str, end_ymd: str,
                           frequency: str, adjustflag: str) -> Optional[list]:
        """在会话上下文内执行查询，自动复用登录态"""
        with session_scope(auto_login=True):
            rs = query_history_k_data_plus_retry(
                bs_code, fields,
                start_date=start_ymd,
                end_date=end_ymd,
                frequency=frequency,
                adjustflag=adjustflag,
                max_retries=2,
                wait_seconds=1.5
            )
            if rs is None or rs.error_code != "0":
                return None
            
            rows = []
            while rs.next() and len(rows) < self._max_rows:
                rows.append(rs.get_row_data())
            
            if len(rows) >= self._max_rows:
                log.warning(f"[Baostock] {bs_code} 返回异常行数({len(rows)})，疑似死循环，已截断")
            
            if not rows:
                return None
            
            return rows
    
    def get_daily(self, symbol: str, start_date: str = "20240101", 
                  end_date: str = None, adjustflag: str = "2") -> Optional[pd.DataFrame]:
        """
        获取历史日线
        adjustflag: 1=后复权, 2=前复权(默认), 3=不复权
        start_date 支持 YYYYMMDD 或 YYYY-MM-DD 格式
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        
        bs_code = self._to_bs_code(symbol)
        # 统一日期格式为 YYYY-MM-DD
        if "-" not in start_date:
            start_ymd = f"{start_date[0:4]}-{start_date[4:6]}-{start_date[6:8]}"
        else:
            start_ymd = start_date
        if "-" not in end_date:
            end_ymd = f"{end_date[0:4]}-{end_date[4:6]}-{end_date[6:8]}"
        else:
            end_ymd = end_date
        
        try:
            rows = self._query_with_session(
                bs_code,
                "date,open,high,low,close,volume",
                start_ymd, end_ymd, "d", adjustflag
            )
            if rows is None:
                raise ValueError("查询失败")
            
            df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True).dropna()
            
            if len(df) >= 2:
                self._consecutive_failures = 0
                self._healthy = True
                log.info(f"  ✅ {symbol}: baostock {len(df)}条日线")
                return df
            
        except Exception as e:
            log.warning(f"  ⚠️ {symbol} baostock获取失败: {e}")
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._healthy = False
                # 连续失败时尝试强制重登
                force_relogin()
        
        return None
    
    def get_daily_batch(self, symbols: List[str], start_date: str = "20240101", 
                        end_date: str = None, adjustflag: str = "2") -> dict:
        """
        批量获取多只股票日线（单次会话复用）
        返回: {symbol: DataFrame}
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if "-" not in start_date:
            start_ymd = f"{start_date[0:4]}-{start_date[4:6]}-{start_date[6:8]}"
        else:
            start_ymd = start_date
        if "-" not in end_date:
            end_ymd = f"{end_date[0:4]}-{end_date[4:6]}-{end_date[6:8]}"
        else:
            end_ymd = end_date
        
        results = {}
        with session_scope(auto_login=True):
            for symbol in symbols:
                bs_code = self._to_bs_code(symbol)
                try:
                    rs = query_history_k_data_plus_retry(
                        bs_code,
                        "date,open,high,low,close,volume",
                        start_date=start_ymd,
                        end_date=end_ymd,
                        frequency="d",
                        adjustflag=adjustflag,
                        max_retries=2,
                        wait_seconds=1.0
                    )
                    if rs is None or rs.error_code != "0":
                        log.warning(f"  ⚠️ {symbol} baostock查询失败: {rs.error_msg if rs else 'None'}")
                        continue
                    
                    rows = []
                    while rs.next() and len(rows) < self._max_rows:
                        rows.append(rs.get_row_data())
                    
                    if not rows:
                        continue
                    
                    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
                    for c in ["open", "high", "low", "close", "volume"]:
                        df[c] = pd.to_numeric(df[c], errors="coerce")
                    df["date"] = pd.to_datetime(df["date"])
                    df = df.sort_values("date").reset_index(drop=True).dropna()
                    
                    if len(df) >= 2:
                        results[symbol] = df
                        log.info(f"  ✅ {symbol}: baostock {len(df)}条日线")
                    else:
                        log.warning(f"  ⚠️ {symbol} 数据不足({len(df)}行)")
                        
                except Exception as e:
                    log.warning(f"  ⚠️ {symbol} baostock批量获取异常: {e}")
        
        if results:
            self._consecutive_failures = 0
            self._healthy = True
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._healthy = False
                force_relogin()
        
        return results
    
    def get_index_daily(self, index_code: str, start_date: str, 
                        end_date: str = None, adjustflag: str = "3") -> Optional[pd.DataFrame]:
        """
        获取指数日线（通常不复权）
        index_code: 如 "sh.000300", "sh.000905"
        start_date 支持 YYYYMMDD 或 YYYY-MM-DD 格式
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        
        # 统一日期格式为 YYYY-MM-DD
        if "-" not in start_date:
            start_ymd = f"{start_date[0:4]}-{start_date[4:6]}-{start_date[6:8]}"
        else:
            start_ymd = start_date
        if "-" not in end_date:
            end_ymd = f"{end_date[0:4]}-{end_date[4:6]}-{end_date[6:8]}"
        else:
            end_ymd = end_date
        
        try:
            rows = self._query_with_session(
                index_code,
                "date,open,close,high,low,volume",
                start_ymd, end_ymd, "d", adjustflag
            )
            if rows is None:
                raise ValueError("查询失败")
            
            df = pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "volume"])
            for c in ["open", "close", "high", "low", "volume"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True).dropna()
            
            if len(df) >= 2:
                self._consecutive_failures = 0
                self._healthy = True
                return df
            
        except Exception as e:
            log.warning(f"  ⚠️ 指数 {index_code} baostock获取失败: {e}")
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._healthy = False
        
        return None
    
    def is_healthy(self) -> bool:
        return self._healthy
    
    def get_session_status(self) -> dict:
        """获取底层会话状态（供监控/健康检查）"""
        return get_session_status()


_instance: Optional[BaostockProvider] = None

def get_provider() -> BaostockProvider:
    global _instance
    if _instance is None:
        _instance = BaostockProvider()
        # 确保登录
        ensure_login()
    return _instance