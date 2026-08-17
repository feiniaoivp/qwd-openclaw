# A股金融数据整合技能 — ���查卡片

> 从 SKILL.md 提取的��心模板，供开发时直接复制����

---

## ��� ��心工具��数 (第10章)

```python
import re
import math

NO_DATA = "无"

def safe_float(val, default=NO_DATA):
    if val is None or val == '' or val == 'False' or val == 'NaN':
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f) or abs(f) > 1e15:
            return default
        return f
    except (ValueError, TypeError):
        return default

def parse_amount(s):
    """解��带单位金�� → 亿元"""
    if s is None or s == '' or s == 'False':
        return None
    s = str(s).strip()
    m = re.match(r'^([-\d.]+)\s*万亿$', s)
    if m: return float(m.group(1)) * 10000
    m = re.match(r'^([-\d.]+)\s*亿$', s)
    if m: return float(m.group(1))
    m = re.match(r'^([-\d.]+)\s*万$', s)
    if m: return float(m.group(1)) / 10000
    try: return float(s)  # 纯数字，单位需调用者判断
    except: return None

def parse_percent(s):
    if s is None or s == '' or s == 'False': return None
    s = str(s).strip()
    m = re.match(r'^([-\d.]+)\s*%$', s)
    if m: return float(m.group(1))
    try: return float(s)
    except: return None

def convert_share(val):
    """Baostock ���本 → 亿股"""
    f = safe_float(val)
    if f == NO_DATA: return NO_DATA
    if f < 10000 and f > 0: return f / 10000  # 万股→亿股
    return f / 100000000  # ���→亿股

def convert_sina_percent(val):
    """新��(%)列 → 百分比"""
    f = safe_float(val)
    if f == NO_DATA: return NO_DATA
    if abs(f) < 2: return f * 100
    return f
```

---

## ��� 单位转换速查 (第11章)

| ��� | 原始单位 | 目标单位 | ��换方法 |
|---|---|---|---|
| 同花顺金�� | "1483.91亿" | 亿元 | `parse_amount()` |
| 同花顺利��表 False | ��尔False | — | `if val is not False:` |
| 新��金��(元)列 | 不确定 | 亿元 | `parse_amount()` 失败则 ��1亿 |
| 新��利��表金�� | 元(纯数字) | 亿元 | `val / 100000000` |
| Baostock金�� | 元 | 亿元 | `val / 100000000` |
| Baostock股本 | ���/万股 | 亿股 | `convert_share()` |
| Baostock比率 | 小数 | 百分比 | `val * 100` |
| 新��比率(%)列 | 不确定 | 百分比 | `convert_sina_percent()` |
| 同花顺比率 | "6.22%" | 百分比 | `parse_percent()` |
| �����分红 | ��10股派息 | ��股分红 | `val / 10` |

---

## ��� ��存策略 (第12章)

```python
# ��存��存（财务查��场景）
class FinancialDataFetcher:
    def __init__(self):
        self._cache = {}
    def bs_query(self, func_name, year, quarter):
        cache_key = f"bs_{func_name}_{year}_{quarter}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        # ... ���� ...
        self._cache[cache_key] = result
        return result

# ������存（回��场景）
import os, pandas as pd
CACHE_DIR = "data_cache"
def get_cache_path(code, start_date, end_date, freq):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{code}_{start_date}_{end_date}_{freq}.csv")
def load_cache(path):
    if os.path.exists(path): return pd.read_csv(path, dtype=str)
    return None
def save_cache(path, df): df.to_csv(path, index=False)
```

---

## ��� ���误处理与重试 (第13章)

```python
import time, socket

def bs_query_with_retry(fn, max_retries=3, wait_seconds=2):
    for attempt in range(max_retries):
        try:
            rs = fn()
            if rs.error_code == '0': return rs
            print(f"  [BS] ���误: {rs.error_msg}, 重试 {attempt+1}/{max_retries}")
        except (socket.timeout, TimeoutError, OSError) as e:
            print(f"  [BS] 网����常: {e}, 重试 {attempt+1}/{max_retries}")
        time.sleep(wait_seconds)
    return None

def ak_safe_call(fn, *args, default=None, **kwargs):
    try:
        result = fn(*args, **kwargs)
        if result is None or (hasattr(result, 'empty') and result.empty):
            return default
        return result
    except Exception as e:
        print(f"  [AK] ��口��常: {e}")
        return default

def bs_query_to_df(rs):
    rows = []
    while rs.error_code == '0' and rs.next():
        rows.append(rs.get_row_data())
    if not rows: return None
    import pandas as pd
    return pd.DataFrame(rows, columns=rs.fields)
```

---

## ��� ��据源降级策略 (第14章)

```python
def fetch_with_fallback(primary_fn, fallback_fn1=None, fallback_fn2=None):
    result = primary_fn()
    if result is not None and result != NO_DATA: return result
    if fallback_fn1:
        result = fallback_fn1()
        if result is not None and result != NO_DATA: return result
    if fallback_fn2:
        result = fallback_fn2()
        if result is not None and result != NO_DATA: return result
    return NO_DATA
```

| 目标 | 降级�� |
|---|---|
| K线行情 | **新��日K(��定)** → pytdx → akshare东财 |
| ���报 | Akshare同花顺 → 新�� → Baostock |
| 分红 | Akshare巨�� → Baostock |
| 行业 | Baostock → 东财(不可达) |
| 研发费 | 同花顺利��表 → 新��利��表 → 同花顺备用列名 → 新��备用列名 |

---

## ������ FinancialDataFetcher 完整类模板 (第15.1章)

```python
import datetime
import baostock as bs

class FinancialDataFetcher:
    def __init__(self, bs_code, pure_code):
        self.bs_code = bs_code
        self.pure_code = pure_code
        self._cache = {}
        self._bs_logged_in = False
        self.now = datetime.datetime.now()
        self.last_year = self.now.year - 2 if self.now.month < 5 else self.now.year - 1
        self.prev_year = self.last_year - 1
        self.year_before = self.last_year - 2

    def bs_login(self):
        if not self._bs_logged_in:
            lg = bs.login()
            if lg.error_code != '0': return False
            self._bs_logged_in = True
        return True

    def bs_logout(self):
        if self._bs_logged_in:
            bs.logout()
            self._bs_logged_in = False

    def _ensure_bs_login(self):
        if not self._bs_logged_in: self.bs_login()

    def bs_query(self, func_name, year=None, quarter=None):
        self._ensure_bs_login()
        cache_key = f"bs_{func_name}_{year}_{quarter}"
        if cache_key in self._cache: return self._cache[cache_key]
        # ... 实际查�� ...
        self._cache[cache_key] = result
        return result
```

---

## ��� Numba 加速技术指标 (第15.3章)

```python
from numba import jit
import numpy as np

@jit(nopython=True, cache=True, fastmath=True)
def calc_rsi(close_prices, period=8):
    # RSI周期=8 是关��参数
    length = len(close_prices)
    rsi = np.zeros(length)
    # ... 计算��辑 ...
    return rsi
```

---

## ��� 关��数据源路由策略 (MEMORY.md 2026-08-02 权威版)

```
实时行情: 新��财经(hq.sinajs.cn + Referer) → 通达信(pytdx) → akshare
K线历史: 新��日K(jsonp, scale=240) → 通达信(pytdx) → akshare
财务数据: akshare(同花顺/新��)
新闻/公告: akshare stock_info_global_em / stock_news_em / stock_notice_report
```

> ������ 当前网��环境：**新�� hq.sinajs.cn + 新��日K jsonp 是唯一实����定源**；Baostock 当日K线延��1天；pytdx 仅��手成功 bars全None；akshare东财历史被��截。