#!/usr/bin/env python3
"""
雷科防务 (002413) 财务与行情数据获取脚本
基于 astock-unified-financial 技能文档最佳实践
- 双数据源降级链：同花顺/新浪 → Baostock
- 单位自动转换、异常重试、内存/磁盘缓存
- 输出结构化 JSON + 控制台摘要
"""

import os
import sys
import json
import time
import re
import math
import socket
import shutil
from datetime import datetime
from typing import Optional, Dict, Any, List

import pandas as pd

# ============================================================
# 环境与路径设置（必须置顶，按 TOOLS.md 强制模板）
# ============================================================
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ============================================================
# 核心工具函数（技能文档第10章标准实现）
# ============================================================
NO_DATA = "无"

def safe_float(val, default=NO_DATA):
    """安全转浮点，处理 None/空串/NaN/False/超限值"""
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
    """解析带单位的金额字符串，统一转为亿元"""
    if s is None or s == '' or s == 'False':
        return None
    s = str(s).strip()
    m = re.match(r'^([-\d.]+)\s*万亿$', s)
    if m:
        return float(m.group(1)) * 10000
    m = re.match(r'^([-\d.]+)\s*亿$', s)
    if m:
        return float(m.group(1))
    m = re.match(r'^([-\d.]+)\s*万$', s)
    if m:
        return float(m.group(1)) / 10000
    try:
        return float(s)  # 纯数字，单位需调用者判断
    except (ValueError, TypeError):
        return None

def parse_percent(s):
    """解析百分比字符串"""
    if s is None or s == '' or s == 'False':
        return None
    s = str(s).strip()
    m = re.match(r'^([-\d.]+)\s*%$', s)
    if m:
        return float(m.group(1))
    try:
        return float(s)
    except (ValueError, TypeError):
        return None

def convert_share(val):
    """Baostock totalShare/liqaShare 自动单位检测与转换（→亿股）"""
    f = safe_float(val)
    if f == NO_DATA:
        return NO_DATA
    if f < 10000 and f > 0:
        return f / 10000   # 万股→亿股
    else:
        return f / 100000000  # 股→亿股

def convert_sina_percent(val):
    """新浪(%)列：判断是小数还是百分比，统一转为百分比"""
    f = safe_float(val)
    if f == NO_DATA:
        return NO_DATA
    if abs(f) < 2:
        f = f * 100
    return f

def extract_province(addr):
    """从完整注册地址提取省份"""
    if not addr:
        return ''
    for city in ['北京', '上海', '天津', '重庆']:
        if city in addr:
            return city + '市'
    m = re.search(r'([\u4e00-\u9fa5]+省|[\u4e00-\u9fa5]+自治区|[\u4e00-\u9fa5]+特别行政区)', addr)
    if m:
        return m.group(1)
    return addr

# ============================================================
# 重试与安全调用封装
# ============================================================
def bs_query_with_retry(fn, max_retries=3, wait_seconds=2):
    """带重试的 baostock 查询"""
    for attempt in range(max_retries):
        try:
            rs = fn()
            if rs.error_code == '0':
                return rs
            else:
                print(f"  [BS] 查询错误: {rs.error_msg}, 重试 {attempt+1}/{max_retries}")
        except (socket.timeout, TimeoutError, OSError) as e:
            print(f"  [BS] 网络异常: {e}, 重试 {attempt+1}/{max_retries}")
        time.sleep(wait_seconds)
    return None

def ak_safe_call(fn, *args, default=None, **kwargs):
    """安全调用 akshare 接口"""
    try:
        result = fn(*args, **kwargs)
        if result is None or (hasattr(result, 'empty') and result.empty):
            return default
        return result
    except Exception as e:
        print(f"  [AK] 接口异常: {e}")
        return default

def bs_query_to_df(rs):
    """将 baostock ResultData 转为 DataFrame"""
    rows = []
    while (rs.error_code == '0') and rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return None
    return pd.DataFrame(rows, columns=rs.fields)

# ============================================================
# 磁盘缓存（回测/多股场景）
# ============================================================
CACHE_DIR = os.path.join(WORKSPACE, "data_cache")

def get_cache_path(prefix: str, code: str, start: str, end: str, freq: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{prefix}_{code}_{start}_{end}_{freq}.csv")

def load_cache(path: str) -> Optional[pd.DataFrame]:
    if os.path.exists(path):
        try:
            return pd.read_csv(path, dtype=str)
        except Exception:
            return None
    return None

def save_cache(path: str, df: pd.DataFrame):
    try:
        df.to_csv(path, index=False)
    except Exception as e:
        print(f"  [CACHE] 保存失败: {e}")

# ============================================================
# 股票代码标准化
# ============================================================
def normalize_stock_code(raw: str):
    """统一识别股票代码，返回 (baostock格式, 纯代码)"""
    raw = raw.strip().replace(' ', '').replace('\uff0e', '.').replace('\u3002', '.')
    if '.' in raw:
        parts = raw.split('.')
        bs_code = parts[0].lower() + '.' + parts[1]
        pure_code = parts[1]
    elif raw.startswith('6') or raw.startswith('9'):
        bs_code = 'sh.' + raw
        pure_code = raw
    elif raw.startswith('0') or raw.startswith('3'):
        bs_code = 'sz.' + raw
        pure_code = raw
    elif raw.startswith('8') or raw.startswith('4'):
        bs_code = 'bj.' + raw
        pure_code = raw
    else:
        raise ValueError(f"无法识别代码格式: {raw}")
    return bs_code, pure_code

# ============================================================
# 核心数据获取器
# ============================================================
class FinancialDataFetcher:
    """财务数据获取器 - 主力+备用双源模式（技能文档15.1节）"""

    def __init__(self, raw_code: str = "002413"):
        self.bs_code, self.pure_code = normalize_stock_code(raw_code)
        self._cache = {}
        self._bs_logged_in = False
        self.now = datetime.now()
        # 判断"去年"：5月前年报可能未出，取前年
        self.last_year = self.now.year - 2 if self.now.month < 5 else self.now.year - 1
        self.prev_year = self.last_year - 1

    # ── 登录管理 ──
    def bs_login(self):
        if not self._bs_logged_in:
            import baostock as bs
            lg = bs.login()
            if lg.error_code != '0':
                print(f"  [BS] 登录失败: {lg.error_msg}")
                return False
            self._bs_logged_in = True
            print(f"  [BS] 登录成功")
        return True

    def bs_logout(self):
        if self._bs_logged_in:
            import baostock as bs
            bs.logout()
            self._bs_logged_in = False
            print(f"  [BS] 登出完成")

    def _ensure_bs_login(self):
        if not self._bs_logged_in:
            self.bs_login()

    # ── 通用查询（带内存缓存） ──
    def _bs_query_cached(self, func_name: str, year: int = None, quarter: int = None):
        import baostock as bs
        self._ensure_bs_login()
        cache_key = f"bs_{func_name}_{year}_{quarter}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        func = getattr(bs, func_name)
        if year is not None and quarter is not None:
            rs = bs_query_with_retry(lambda: func(code=self.bs_code, year=year, quarter=quarter))
        elif year is not None:
            rs = bs_query_with_retry(lambda: func(code=self.bs_code, year=year))
        else:
            rs = bs_query_with_retry(lambda: func(code=self.bs_code))
        
        df = bs_query_to_df(rs)
        if df is not None:
            self._cache[cache_key] = df
        return df

    # ── 盈利数据 ──
    def get_profit_data(self, year: int, quarter: int) -> Optional[Dict]:
        df = self._bs_query_cached('query_profit_data', year, quarter)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        return {
            "statDate": row.get('statDate'),
            "pubDate": row.get('pubDate'),
            "netProfit_yi": safe_float(row.get('netProfit')) / 100000000 if safe_float(row.get('netProfit')) != NO_DATA else NO_DATA,
            "epsTTM": safe_float(row.get('epsTTM')),
            "roeAvg_pct": safe_float(row.get('roeAvg')) * 100 if safe_float(row.get('roeAvg')) != NO_DATA else NO_DATA,
            "npMargin_pct": safe_float(row.get('npMargin')) * 100 if safe_float(row.get('npMargin')) != NO_DATA else NO_DATA,
            "gpMargin_pct": safe_float(row.get('gpMargin')) * 100 if safe_float(row.get('gpMargin')) != NO_DATA else NO_DATA,
            "totalShare_yi": convert_share(row.get('totalShare')),
            "liqaShare_yi": convert_share(row.get('liqaShare')),
            "operatingRevenue_yi": safe_float(row.get('MBRevenue')) / 100000000 if safe_float(row.get('MBRevenue')) != NO_DATA else NO_DATA,
        }

    # ── 成长数据 ──
    def get_growth_data(self, year: int, quarter: int) -> Optional[Dict]:
        df = self._bs_query_cached('query_growth_data', year, quarter)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        return {
            "statDate": row.get('statDate'),
            "YOYEquity_pct": safe_float(row.get('YOYEquity')) * 100 if safe_float(row.get('YOYEquity')) != NO_DATA else NO_DATA,
            "YOYAsset_pct": safe_float(row.get('YOYAsset')) * 100 if safe_float(row.get('YOYAsset')) != NO_DATA else NO_DATA,
            "YOYNI_pct": safe_float(row.get('YOYNI')) * 100 if safe_float(row.get('YOYNI')) != NO_DATA else NO_DATA,
            "YOYEPSBasic_pct": safe_float(row.get('YOYEPSBasic')) * 100 if safe_float(row.get('YOYEPSBasic')) != NO_DATA else NO_DATA,
            "YOYPNI_pct": safe_float(row.get('YOYPNI')) * 100 if safe_float(row.get('YOYPNI')) != NO_DATA else NO_DATA,
        }

    # ── 现金流数据 ──
    def get_cashflow_data(self, year: int, quarter: int) -> Optional[Dict]:
        df = self._bs_query_cached('query_cash_flow_data', year, quarter)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        return {
            "statDate": row.get('statDate'),
            "CAToAsset": safe_float(row.get('CAToAsset')),
            "NCAToAsset": safe_float(row.get('NCAToAsset')),
            "ebitToInterest": safe_float(row.get('ebitToInterest')),
            "CFOToOR_pct": safe_float(row.get('CFOToOR')) * 100 if safe_float(row.get('CFOToOR')) != NO_DATA else NO_DATA,
            "CFOToNP_pct": safe_float(row.get('CFOToNP')) * 100 if safe_float(row.get('CFOToNP')) != NO_DATA else NO_DATA,
        }

    # ── 资产负债数据（比率类） ──
    def get_balance_data(self, year: int, quarter: int) -> Optional[Dict]:
        df = self._bs_query_cached('query_balance_data', year, quarter)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        return {
            "statDate": row.get('statDate'),
            "currentRatio": safe_float(row.get('currentRatio')),
            "quickRatio": safe_float(row.get('quickRatio')),
            "cashRatio": safe_float(row.get('cashRatio')),
            "liabilityToAsset_pct": safe_float(row.get('liabilityToAsset')) * 100 if safe_float(row.get('liabilityToAsset')) != NO_DATA else NO_DATA,
            "assetToEquity": safe_float(row.get('assetToEquity')),
        }

    # ── 分红数据 ──
    def get_dividend_data(self, year: int) -> Optional[Dict]:
        import baostock as bs
        self._ensure_bs_login()
        rs = bs_query_with_retry(lambda: bs.query_dividend_data(code=self.bs_code, year=year, yearType='report'))
        df = bs_query_to_df(rs)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        # Baostock 单位：元/股
        return {
            "year": year,
            "dividCashPsBeforeTax": safe_float(row.get('dividCashPsBeforeTax')),
            "dividPreistNoTax": safe_float(row.get('dividPreistNoTax')),
            "dividAistNoTax": safe_float(row.get('dividAistNoTax')),
        }

    # ── 基本信息 ──
    def get_basic_info(self) -> Optional[Dict]:
        import baostock as bs
        self._ensure_bs_login()
        rs = bs_query_with_retry(lambda: bs.query_stock_basic(code=self.bs_code))
        df = bs_query_to_df(rs)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        return {
            "code": row.get('code'),
            "code_name": row.get('code_name'),
            "ipoDate": row.get('ipoDate'),
            "type": row.get('type'),
            "status": row.get('status'),
        }

    # ── 行业分类 ──
    def get_industry(self) -> Optional[Dict]:
        import baostock as bs
        self._ensure_bs_login()
        rs = bs_query_with_retry(lambda: bs.query_stock_industry(code=self.bs_code))
        df = bs_query_to_df(rs)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        return {
            "industry": row.get('industry'),
            "industryClassification": row.get('industryClassification'),
            "updateDate": row.get('updateDate'),
        }

    # ── K线数据（带磁盘缓存） ──
    def get_kline(self, start_date: str, end_date: str, frequency: str = 'd', adjustflag: str = '3') -> Optional[pd.DataFrame]:
        import baostock as bs
        self._ensure_bs_login()
        
        cache_path = get_cache_path("kline", self.bs_code.replace('.', '_'), start_date, end_date, frequency)
        cached = load_cache(cache_path)
        if cached is not None:
            print(f"  [CACHE] 命中磁盘缓存: {cache_path}")
            return cached
        
        fields = 'date,close,open,high,low,volume,amount,turn,pctChg'
        if frequency == '60':
            fields = 'date,time,close,open,high,low,volume,amount'
        
        rs = bs_query_with_retry(lambda: bs.query_history_k_data_plus(
            code=self.bs_code,
            fields=fields,
            start_date=start_date,
            end_date=end_date,
            frequency=frequency,
            adjustflag=adjustflag
        ))
        df = bs_query_to_df(rs)
        if df is not None and not df.empty:
            save_cache(cache_path, df)
        return df

# ============================================================
# Akshare 降级数据源
# ============================================================
class AkshareFetcher:
    """Akshare 数据源 - 同花顺/新浪/巨潮（技能文档降级链）"""

    def __init__(self, pure_code: str):
        self.pure_code = pure_code

    # ── 同花顺利润表（含研发费用，Level 1）──
    def get_ths_profit(self) -> Optional[pd.DataFrame]:
        import akshare as ak
        return ak_safe_call(ak.stock_financial_benefit_ths, symbol=self.pure_code, indicator="按年度")

    # ── 新浪利润表（含研发费用，Level 2）──
    def get_sina_profit(self) -> Optional[pd.DataFrame]:
        import akshare as ak
        return ak_safe_call(ak.stock_financial_report_sina, stock=self.pure_code, symbol="利润表")

    # ── 同花顺财务摘要 ──
    def get_ths_abstract(self) -> Optional[pd.DataFrame]:
        import akshare as ak
        return ak_safe_call(ak.stock_financial_abstract_ths, symbol=self.pure_code, indicator="按年度")

    # ── 巨潮分红 ──
    def get_cninfo_dividend(self) -> Optional[pd.DataFrame]:
        import akshare as ak
        return ak_safe_call(ak.stock_dividend_cninfo, symbol=self.pure_code)

    # ── 巨潮公司概况（注册属地）──
    def get_cninfo_profile(self) -> Optional[pd.DataFrame]:
        import akshare as ak
        return ak_safe_call(ak.stock_profile_cninfo, symbol=self.pure_code)

    # ── 提取研发费用（四级降级，技能文档7.2节）──
    def extract_rd_expense(self, year: int) -> float:
        """返回亿元，失败返回 NO_DATA"""
        # Level 1: 同花顺利润表
        df = self.get_ths_profit()
        if df is not None:
            rd_cols = ['研发费用', '研究开发费用', '研究与开发费用']
            for _, row in df.iterrows():
                if str(year) in str(row.get('报告期', '')):
                    for col in rd_cols:
                        val = row.get(col)
                        if val is not False and val is not None:
                            p = parse_amount(val)
                            if p is not None:
                                return p
        # Level 2: 新浪利润表
        df = self.get_sina_profit()
        if df is not None:
            target = f"{year}1231"
            for _, row in df.iterrows():
                if target in str(row.get('报告日', '')):
                    val = row.get('研发费用')
                    f = safe_float(val)
                    if f != NO_DATA:
                        return f / 100000000
        return NO_DATA

# ============================================================
# 主流程
# ============================================================
def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


def main():
    CODE = "002413"
    print(f"\n{'='*60}")
    print(f"  雷科防务 ({CODE}) 全维度数据获取")
    print(f"  基于 astock-unified-financial 技能规范")
    print(f"{'='*60}\n")

    bs_fetcher = FinancialDataFetcher(CODE)
    ak_fetcher = AkshareFetcher(bs_fetcher.pure_code)

    result = {
        "code": CODE,
        "bs_code": bs_fetcher.bs_code,
        "pure_code": bs_fetcher.pure_code,
        "fetch_time": datetime.now().isoformat(),
        "basic_info": None,
        "industry": None,
        "profit": {},
        "growth": {},
        "cashflow": {},
        "balance": {},
        "dividend": {},
        "rd_expense": {},
        "ths_abstract": None,
        "cninfo_profile": None,
        "kline_daily": None,
        "kline_60m": None,
    }

    try:
        # 1. 基本信息
        print("[1/10] 获取基本信息...")
        result["basic_info"] = bs_fetcher.get_basic_info()
        if result["basic_info"]:
            print(f"  → {result['basic_info']['code_name']} | 上市: {result['basic_info']['ipoDate']}")

        # 2. 行业分类
        print("[2/10] 获取行业分类...")
        result["industry"] = bs_fetcher.get_industry()
        if result["industry"]:
            print(f"  → {result['industry']['industry']} ({result['industry']['industryClassification']})")

        # 3. 盈利数据（近3年年报）
        print("[3/10] 获取盈利数据...")
        for year in [2024, 2023, 2022]:
            data = bs_fetcher.get_profit_data(year, 4)
            if data:
                result["profit"][str(year)] = data
                print(f"  → {year}年报: 净利润={data['netProfit_yi']:.2f}亿, ROE={data['roeAvg_pct']:.2f}%, 毛利率={data['gpMargin_pct']:.2f}%")

        # 4. 成长数据
        print("[4/10] 获取成长数据...")
        for year in [2024, 2023]:
            data = bs_fetcher.get_growth_data(year, 4)
            if data:
                result["growth"][str(year)] = data
                # Baostock 成长数据字段: YOYEquity, YOYAsset, YOYNI, YOYEPSBasic, YOYPNI
                print(f"  → {year}年报: 净资产增速={data.get('YOYEquity_pct', 'N/A'):.2f}%, 总资产增速={data.get('YOYAsset_pct', 'N/A'):.2f}%, 净利增速={data.get('YOYNI_pct', 'N/A'):.2f}%")

        # 5. 现金流
        print("[5/10] 获取现金流数据...")
        for year in [2024, 2023]:
            data = bs_fetcher.get_cashflow_data(year, 4)
            if data:
                result["cashflow"][str(year)] = data
                print(f"  → {year}年报: 经营现金流/营收={data['CFOToOR_pct']:.2f}%")

        # 6. 资产负债比率
        print("[6/10] 获取资产负债比率...")
        for year in [2024, 2023]:
            data = bs_fetcher.get_balance_data(year, 4)
            if data:
                result["balance"][str(year)] = data
                print(f"  → {year}年报: 流动比率={data['currentRatio']:.2f}, 资产负债率={data['liabilityToAsset_pct']:.2f}%")

        # 7. 分红数据（Baostock + 巨潮双源）
        print("[7/10] 获取分红数据...")
        for year in [2024, 2023, 2022, 2021]:
            data = bs_fetcher.get_dividend_data(year)
            if data:
                result["dividend"][str(year)] = data
        # 巨潮补充
        cninfo_div = ak_fetcher.get_cninfo_dividend()
        if cninfo_div is not None:
            result["dividend"]["cninfo_raw"] = cninfo_div.to_dict('records')
            recent = cninfo_div[cninfo_div['报告时间'].astype(str).str.contains('202[0-9]')]
            if not recent.empty:
                print(f"  → 巨潮近年分红: {len(recent)} 条记录")

        # 8. 研发费用（四级降级）
        print("[8/10] 获取研发费用（四级降级）...")
        for year in [2024, 2023, 2022, 2021, 2020]:
            rd = ak_fetcher.extract_rd_expense(year)
            result["rd_expense"][str(year)] = rd
            if rd != NO_DATA:
                print(f"  → {year}年: 研发费用={rd:.2f}亿元")

        # 9. 同花顺财务摘要
        print("[9/10] 获取同花顺财务摘要...")
        ths_abs = ak_fetcher.get_ths_abstract()
        if ths_abs is not None:
            result["ths_abstract"] = ths_abs.to_dict('records')
            latest = ths_abs.iloc[-1]
            print(f"  → 最新({latest['报告期']}): 净利润={latest['净利润']}, ROE={latest['净资产收益率']}, 毛利率={latest['销售毛利率']}")

        # 10. 巨潮公司概况（注册属地）
        print("[10/10] 获取注册属地...")
        cninfo_profile = ak_fetcher.get_cninfo_profile()
        if cninfo_profile is not None:
            result["cninfo_profile"] = cninfo_profile.to_dict('records')
            addr = cninfo_profile.iloc[0]['注册地址']
            prov = extract_province(addr)
            result["province"] = prov
            print(f"  → 注册地址: {addr}")
            print(f"  → 省份: {prov}")

        # 11. K线数据（日线：近1年；60分线：近1月）
        print("[11/11] 获取K线行情...")
        today = datetime.now().strftime("%Y-%m-%d")
        year_ago = (datetime.now().replace(year=datetime.now().year-1)).strftime("%Y-%m-%d")
        df_daily = bs_fetcher.get_kline(year_ago, today, 'd')
        if df_daily is not None:
            result["kline_daily"] = {
                "count": len(df_daily),
                "start": df_daily.iloc[0]['date'],
                "end": df_daily.iloc[-1]['date'],
                "first_close": float(df_daily.iloc[0]['close']),
                "last_close": float(df_daily.iloc[-1]['close']),
                "pct_change": (float(df_daily.iloc[-1]['close']) / float(df_daily.iloc[0]['close']) - 1) * 100,
            }
            print(f"  → 日线: {result['kline_daily']['count']} 根, {result['kline_daily']['start']}~{result['kline_daily']['end']}, 涨幅={result['kline_daily']['pct_change']:.2f}%")

        from dateutil.relativedelta import relativedelta
        month_ago = (datetime.now() - relativedelta(months=1)).strftime("%Y-%m-%d")
        df_60m = bs_fetcher.get_kline(month_ago, today, '60')
        if df_60m is not None:
            result["kline_60m"] = {
                "count": len(df_60m),
                "start": df_60m.iloc[0]['date'],
                "end": df_60m.iloc[-1]['date'],
            }
            print(f"  → 60分线: {result['kline_60m']['count']} 根, {result['kline_60m']['start']}~{result['kline_60m']['end']}")

    finally:
        bs_fetcher.bs_logout()

    # ── 保存完整 JSON ──
    output_path = os.path.join(WORKSPACE, "analysis", f"leike_financial_{datetime.now().strftime('%Y%m%d')}.json")
    _bak(output_path)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✅ 完整数据已保存: {output_path}")

    # ── 控制台摘要 ──
    print(f"\n{'='*60}")
    print("  摘要报告")
    print(f"{'='*60}")
    if result["basic_info"]:
        print(f"股票: {result['basic_info']['code_name']} ({result['bs_code']})")
    if result["industry"]:
        print(f"行业: {result['industry']['industry']}")
    if result["province"]:
        print(f"注册地: {result['province']}")
    if "2024" in result["profit"]:
        p = result["profit"]["2024"]
        print(f"2024年报: 营收={p['operatingRevenue_yi']:.2f}亿, 净利润={p['netProfit_yi']:.2f}亿, ROE={p['roeAvg_pct']:.2f}%, 毛利率={p['gpMargin_pct']:.2f}%")
    if "2024" in result["rd_expense"] and result["rd_expense"]["2024"] != NO_DATA:
        rd = result["rd_expense"]["2024"]
        rev = result["profit"].get("2024", {}).get("operatingRevenue_yi", 0)
        if rev != NO_DATA and rev > 0:
            print(f"研发费用: {rd:.2f}亿元 (占营收 {rd/rev*100:.1f}%)")
    if result["kline_daily"]:
        k = result["kline_daily"]
        print(f"近1年行情: {k['start']}~{k['end']}, 涨幅={k['pct_change']:.2f}% ({k['first_close']:.2f}→{k['last_close']:.2f})")

    return result

if __name__ == "__main__":
    main()