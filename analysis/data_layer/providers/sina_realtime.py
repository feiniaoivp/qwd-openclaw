"""
新浪实时行情提供者 (hq.sinajs.cn)
==================================
主力实时行情源，稳定、支持批量、延迟极低。
"""

import urllib.request
import logging
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

class SinaRealtimeProvider:
    """新浪实时行情 (hq.sinajs.cn)"""
    
    BASE_URL = "http://hq.sinajs.cn/list="
    HEADERS = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0"
    }
    BATCH_SIZE = 60
    
    def __init__(self):
        self._healthy = True
        self._consecutive_failures = 0
    
    def _to_sina_code(self, symbol: str) -> str:
        """6位代码 -> 新浪代码 (sh600030 / sz000001)

        容错：若传入已带 sh/sz 前缀的代码，直接归一化，
        避免拼成 shsh600030 这种非法代码（历史上曾因调用方传各格式
        导致整批行情为空）。
        """
        s = symbol.strip().lower()
        if s.startswith(("sh", "sz", "bj")):
            return s
        if s.startswith("9"):
            return f"sh{s}"          # B股
        if s.startswith(("6", "5")):
            return f"sh{s}"          # 沪市 / 沪市基金
        if s.startswith(("0", "3", "1")):
            return f"sz{s}"          # 深市 / 创业板 / 深市基金
        return f"sz{s}"
    
    def get_spot(self, symbols: List[str]) -> Dict[str, dict]:
        """
        批量获取实时行情
        返回: {symbol: {name, open, pre_close, last, high, low, volume, amount, date, time}}
        """
        if not symbols:
            return {}
        
        # 分批请求
        out: Dict[str, dict] = {}
        for i in range(0, len(symbols), self.BATCH_SIZE):
            batch = symbols[i:i + self.BATCH_SIZE]
            sina_codes = [self._to_sina_code(s) for s in batch]
            url = self.BASE_URL + ",".join(sina_codes)
            
            req = urllib.request.Request(url, headers=self.HEADERS)
            try:
                raw = urllib.request.urlopen(req, timeout=12).read().decode("gbk")
            except Exception as e:
                log.warning(f"[SinaRealtime] 批次请求失败: {e}")
                self._consecutive_failures += 1
                if self._consecutive_failures >= 3:
                    self._healthy = False
                continue
            
            self._consecutive_failures = 0
            self._healthy = True
            
            for line in raw.splitlines():
                if '="' not in line:
                    continue
                key = line.split("hq_str_")[1].split("=")[0]
                val = line.split('"')[1].split(",")
                if len(val) < 10:
                    continue
                symbol = key[2:]  # 去掉 sh/sz 前缀
                def f(x):
                    try: return float(x)
                    except: return 0.0
                out[symbol] = {
                    "name": val[0],
                    "open": f(val[1]),
                    "pre_close": f(val[2]),
                    "last": f(val[3]),
                    # close 为 last 的稳定别名。新浪 hq 协议里最新价是索引 [3]，
                    # 历史教训：多处调用方误用 [1]（今开）。统一暴露 close 别名，
                    # 避免每个下游各自记字段名。
                    "close": f(val[3]),
                    "high": f(val[4]),
                    "low": f(val[5]),
                    "volume": int(float(val[8])),
                    "amount": f(val[9]),
                    "date": val[30] if len(val) > 30 else "",
                    "time": val[31] if len(val) > 31 else "",
                }
        
        return out
    
    def is_healthy(self) -> bool:
        return self._healthy


# 全局单例
_instance: Optional[SinaRealtimeProvider] = None

def get_provider() -> SinaRealtimeProvider:
    global _instance
    if _instance is None:
        _instance = SinaRealtimeProvider()
    return _instance