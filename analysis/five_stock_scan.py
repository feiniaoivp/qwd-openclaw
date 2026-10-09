#!/usr/bin/env python3
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

"""5只股票批量扫描: 行情趋势(baostock) + 财务摘要(同花顺) + 研发费用(利润表)
股票: 恒力石化600346 / 雷科防务002413 / 中信金属601061 / 沃尔核材002130 / 中信特钢000708
"""
import datetime
import math
import re

import pandas as pd

STOCKS = [
    ("600346", "恒力石化"),
    ("002413", "雷科防务"),
    ("601061", "中信金属"),
    ("002130", "沃尔核材"),
    ("000708", "中信特钢"),
]

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
    if s is None or s == '' or s == 'False':
        return None
    s = str(s).strip()
    for pat, mult in ((r'^([-\d.]+)\s*万亿$', 10000), (r'^([-\d.]+)\s*亿$', 1),
                      (r'^([-\d.]+)\s*万$', 0.0001)):
        m = re.match(pat, s)
        if m:
            return float(m.group(1)) * mult
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def parse_percent(s):
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


def to_bs_code(pure):
    if pure[0] in '69':
        return 'sh.' + pure
    if pure[0] in '03':
        return 'sz.' + pure
    return 'bj.' + pure


def ak_safe_call(fn, *args, default=None, **kwargs):
    try:
        result = fn(*args, **kwargs)
        if result is None or (hasattr(result, 'empty') and result.empty):
            return default
        return result
    except Exception as e:
        print(f"  [AK] 接口异常: {type(e).__name__}: {e}")
        return default


def fetch_kline(bs_code, start, end):
    """近1年日K, 返回 DataFrame(date,close) 字符串转float"""
    import baostock as bs
    lg = bs.login()
    if lg.error_code != '0':
        print(f"  [BS] 登录失败: {lg.error_msg}")
        return None
    try:
        rs = bs.query_history_k_data_plus(
            code=bs_code, fields="date,close,open,high,low,volume,amount",
            start_date=start, end_date=end, frequency="d", adjustflag="3")
        rows = []
        while (rs.error_code == '0') and rs.next():
            rows.append(rs.get_row_data())
        if rs.error_code != '0':
            print(f"  [BS] 查询错误: {rs.error_msg}")
            return None
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=rs.fields)
        for c in ('close', 'open', 'high', 'low', 'volume', 'amount'):
            df[c] = df[c].astype(float)
        return df
    finally:
        bs.logout()


def trend_metrics(df):
    """近1年趋势指标"""
    close = df['close'].values
    first, last = close[0], close[-1]
    ret_1y = (last / first - 1) * 100
    # 60日趋势斜率(线性回归, %/月约)
    window = close[-60:] if len(close) >= 60 else close
    x = list(range(len(window)))
    n = len(x)
    mx, my = sum(x) / n, sum(window) / n
    slope = sum((xi - mx) * (yi - my) for xi, yi in zip(x, window)) / sum((xi - mx) ** 2 for xi in x)
    slope_pct = slope / my * 100  # 每交易日%变化
    # 年化波动率
    rets = pd.Series(close).pct_change().dropna()
    vol = float(rets.std() * math.sqrt(252) * 100)
    # 250日高回撤
    roll_max = pd.Series(close).cummax()
    dd = float(((pd.Series(close) - roll_max) / roll_max).min() * 100)
    avg_amount = float(df['amount'].mean() / 1e8)  # 亿元/日
    ma20 = close[-20:].mean()
    ma60 = close[-60:].mean() if len(close) >= 60 else close.mean()
    pos = '多头' if ma20 > ma60 else '空头'
    return {
        'last': last, 'ret_1y': ret_1y, 'slope_pct': slope_pct, 'vol': vol,
        'max_dd': dd, 'avg_amount': avg_amount, 'ma20': ma20, 'ma60': ma60, 'ma_pos': pos,
        'last_date': df['date'].iloc[-1],
    }


def fetch_ths_abstract(pure):
    import akshare as ak
    return ak_safe_call(ak.stock_financial_abstract_ths, symbol=pure, indicator="按年度")


def parse_abstract(df, year):
    """取指定年报关键行"""
    out = {}
    if df is None:
        return out
    cols = list(df.columns)
    rp_col = cols[0]
    for _, row in df.iterrows():
        rp = str(row[rp_col]).strip()
        # 报告期格式: 纯年份"2025"或"2025-12-31"(按年度指示器), 只需年份精确匹配
        if rp == str(year) or rp.startswith(f"{year}-"):
            for k in ('净利润', '营业总收入', '净资产收益率', '净利润同比增长率',
                      '营业总收入同比增长率', '基本每股收益'):
                for c in cols:
                    if k in str(c):
                        out[k] = row[c]
                        break
            break
    return out


def fetch_rd_expense(pure, year):
    """研发费用: 同花顺利润表 → 新浪利润表"""
    import akshare as ak
    df = ak_safe_call(ak.stock_financial_benefit_ths, symbol=pure, indicator="按年度")
    if df is not None:
        rd_cols = ['研发费用', '研究开发费用', '研究与开发费用']
        for _, row in df.iterrows():
            if str(year) in str(row.get('报告期', '')):
                for col in rd_cols:
                    val = row.get(col) if col in df.columns else None
                    if val is not False and val is not None:
                        p = parse_amount(val)
                        if p is not None:
                            return p, '同花顺利润表'
    df = ak_safe_call(ak.stock_financial_report_sina, stock=pure, symbol="利润表")
    if df is not None:
        target = f"{year}1231"
        for _, row in df.iterrows():
            if target in str(row.get('报告日', '')):
                val = row.get('研发费用')
                f = safe_float(val)
                if f != NO_DATA:
                    return f / 1e8, '新浪利润表'
    return NO_DATA, '无'


def main():
    now = datetime.datetime.now()
    year = now.year - 1 if now.month >= 5 else now.year - 2
    end = now.strftime('%Y-%m-%d')
    start = (now - datetime.timedelta(days=365)).strftime('%Y-%m-%d')

    print(f"=== 5股批量扫描 | K线区间 {start}~{end} | 财报年度 {year} ===\n")
    results = []
    for pure, name in STOCKS:
        print(f"--- {name}({pure}) ---")
        item = {'code': pure, 'name': name}
        df = fetch_kline(to_bs_code(pure), start, end)
        if df is not None and len(df) >= 60:
            try:
                item.update(trend_metrics(df))
                print(f"  [BS] K线 {len(df)}根, 最新 {item['last_date']} 收盘 {item['last']}")
            except Exception as e:
                print(f"  [BS] 指标计算异常: {type(e).__name__}: {e}")
        else:
            print("  [BS] K线数据不足")

        ab = fetch_ths_abstract(pure)
        item['abstract'] = parse_abstract(ab, year)
        if item['abstract']:
            print(f"  [THS] {year}年报: {item['abstract']}")

        rd, src = fetch_rd_expense(pure, year)
        item['rd'] = rd
        item['rd_src'] = src
        print(f"  [RD] {year}研发费用: {rd}亿 ({src})")
        results.append(item)
        print()

    # 汇总表
    print("=== 汇总 ===")
    print(f"{'代码':<8}{'名称':<8}{'最新收盘':>10}{'近1年%':>9}{'波动%':>8}{'回撤%':>8}"
          f"{'日均额(亿)':>11}{'MA20/60':>9}{'ROE%':>7}{'净利增速%':>10}{'研发(亿)':>9}")
    for it in results:
        ab = it.get('abstract', {})
        roe = parse_percent(ab.get('净资产收益率'))
        npg = parse_percent(ab.get('净利润同比增长率'))
        print(f"{it['code']:<8}{it['name']:<8}"
              f"{it.get('last', NO_DATA):>10}"
              f"{round(it['ret_1y'], 1) if 'ret_1y' in it else NO_DATA:>9}"
              f"{round(it['vol'], 1) if 'vol' in it else NO_DATA:>8}"
              f"{round(it['max_dd'], 1) if 'max_dd' in it else NO_DATA:>8}"
              f"{round(it['avg_amount'], 1) if 'avg_amount' in it else NO_DATA:>11}"
              f"{it.get('ma_pos', NO_DATA):>9}"
              f"{round(roe, 1) if roe is not None else NO_DATA:>7}"
              f"{round(npg, 1) if npg is not None else NO_DATA:>10}"
              f"{round(it['rd'], 2) if isinstance(it['rd'], float) else NO_DATA:>9}")


if __name__ == '__main__':
    main()
