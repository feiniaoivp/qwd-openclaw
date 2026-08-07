#!/usr/bin/env python3
"""
A股 TTM 指标计算核心模块
- 滚动ROE（Trailing Twelve Months Return on Equity）
- 滚动F-Score（Piotroski F-Score on TTM basis）
- 补齐季报生成滚动指标
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# ─── 解析工具 ───────────────────────────────────────────────

def _parse_num(val):
    """将 '281.54亿' / '-435.44亿' / '14.14%' / 数字 / False 转为 float
    百分比值返回百分比数值（如 14.14% → 14.14）
    金额值统一转为亿
    """
    if val is None or val is False or val == 'False' or val == '':
        return np.nan
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(',', '').replace(' ', '')
    is_pct = False
    if s.endswith('%'):
        is_pct = True
        s = s[:-1].strip()
    neg = 1
    if s.startswith('-'):
        neg = -1
        s = s[1:].strip()
    if '万亿' in s:
        return neg * float(s.replace('万亿', '')) * 10000
    if '亿' in s:
        return neg * float(s.replace('亿', ''))
    if '万' in s:
        return neg * float(s.replace('万', '')) / 10000  # 统一转亿
    try:
        val = float(s) if s else np.nan
        return val * neg
    except:
        return np.nan


def _ymd_to_quarter(report_date):
    """报告期 2026-03-31 -> (2026, 1)"""
    d = str(report_date)
    m = int(d[5:7])
    y = int(d[:4])
    if m <= 3:
        return (y, 1)
    elif m <= 6:
        return (y, 2)
    elif m <= 9:
        return (y, 3)
    else:
        return (y, 4)


def _quarter_type(report_date):
    """判断是什么类型的报告"""
    d = str(report_date)
    m = int(d[5:7])
    if m <= 3:
        return 'Q1'
    elif m <= 6:
        return 'Q2'   # 中报
    elif m <= 9:
        return 'Q3'
    else:
        return 'Q4'   # 年报


# ─── 数据获取 ───────────────────────────────────────────────

def fetch_financial_data(code):
    """
    获取股票完整财务数据
    返回 dict: { 'abstract': df, 'balance': df, 'income': df, 'cashflow': df }
    """
    result = {}
    try:
        result['abstract'] = ak.stock_financial_abstract_ths(symbol=code, indicator='按报告期')
    except Exception as e:
        print(f'  [{code}] abstract 失败: {e}')
        result['abstract'] = pd.DataFrame()
    try:
        result['balance'] = ak.stock_financial_debt_ths(symbol=code)
    except Exception as e:
        print(f'  [{code}] balance 失败: {e}')
        result['balance'] = pd.DataFrame()
    try:
        result['income'] = ak.stock_financial_benefit_ths(symbol=code)
    except Exception as e:
        print(f'  [{code}] income 失败: {e}')
        result['income'] = pd.DataFrame()
    try:
        result['cashflow'] = ak.stock_financial_cash_ths(symbol=code)
    except Exception as e:
        print(f'  [{code}] cashflow 失败: {e}')
        result['cashflow'] = pd.DataFrame()
    return result


# ─── TTM 计算核心 ────────────────────────────────────────────

def calc_ttm_from_abstract(abstract):
    """
    从财务摘要表计算 TTM 指标
    摘要表含：净利润、扣非净利润、营业总收入、基本每股收益等
    """
    if abstract.empty:
        return {}
    
    df = abstract.copy()
    df['date'] = pd.to_datetime(df['报告期'])
    df = df.sort_values('date').reset_index(drop=True)
    
    # 解析数值列
    num_cols = ['净利润', '扣非净利润', '营业总收入', '基本每股收益', 
                '每股净资产', '每股经营现金流', '销售净利率', '净资产收益率',
                '净资产收益率-摊薄']
    for c in num_cols:
        if c in df.columns:
            df[c + '_val'] = df[c].apply(_parse_num)
    
    ttm_results = {}
    
    for idx in range(len(df)):
        row = df.iloc[idx]
        rd = str(row['报告期'])
        qtype = _quarter_type(rd)
        
        # 找匹配的上年同期
        prior_year_same = df[df['date'] == row['date'] - pd.DateOffset(years=1)]
        
        # TTM 计算逻辑
        ttm = {}
        for metric in ['净利润', '扣非净利润', '营业总收入', '每股经营现金流',
                       '销售净利率']:
            val_col = metric + '_val'
            if val_col not in df.columns:
                continue
            cur_val = row.get(val_col, np.nan)
            if pd.isna(cur_val):
                continue
            
            if qtype == 'Q4':  # 年报本身就是全年
                ttm[metric] = cur_val
            else:
                # 找上年年报
                prev_annual = df[df['date'] == row['date'] - pd.DateOffset(years=1) 
                                 if qtype != 'Q1' else 
                                 df[df['date'] == pd.Timestamp(f'{int(rd[:4])-1}-12-31')]]
                # Actually let me fix this logic
                pass
        
        # Simplified TTM logic for latest period
        pass
    
    return ttm_results


def calc_ttm_metrics(abstract, balance, income, cashflow):
    """
    完整 TTM 指标计算
    返回 DataFrame，每行一个报告期，含 TTM 指标
    """
    results = []
    
    # ── 整理数据 ──
    
    # 利润表：提取单季数据（需要从累计值反推）
    inc = income.copy() if not income.empty else pd.DataFrame()
    if not inc.empty:
        inc['date'] = pd.to_datetime(inc['报告期'])
        inc = inc.sort_values('date').reset_index(drop=True)
        for c in ['*净利润', '*营业总收入', '*归属于母公司所有者的净利润', 
                   '*扣除非经常性损益后的净利润']:
            if c in inc.columns:
                inc[c + '_val'] = inc[c].apply(_parse_num)
    
    # 资产负债表
    bs = balance.copy() if not balance.empty else pd.DataFrame()
    if not bs.empty:
        bs['date'] = pd.to_datetime(bs['报告期'])
        bs = bs.sort_values('date').reset_index(drop=True)
        for c in ['*资产合计', '*负债合计', '*归属于母公司所有者权益合计']:
            if c in bs.columns:
                bs[c + '_val'] = bs[c].apply(_parse_num)
    
    # 现金流量表
    cf = cashflow.copy() if not cashflow.empty else pd.DataFrame()
    if not cf.empty:
        cf['date'] = pd.to_datetime(cf['报告期'])
        cf = cf.sort_values('date').reset_index(drop=True)
        for c in ['*经营活动产生的现金流量净额', '*现金及现金等价物净增加额',
                   '*期末现金及现金等价物余额']:
            if c in cf.columns:
                cf[c + '_val'] = cf[c].apply(_parse_num)
    
    # 财务摘要
    ab = abstract.copy() if not abstract.empty else pd.DataFrame()
    if not ab.empty:
        ab['date'] = pd.to_datetime(ab['报告期'])
        ab = ab.sort_values('date').reset_index(drop=True)
        for c in ['净利润', '营业总收入', '净资产收益率', '净资产收益率-摊薄',
                   '基本每股收益', '每股净资产', '每股经营现金流', '销售毛利率',
                   '销售净利率', '流动比率', '速动比率', '资产负债率']:
            if c in ab.columns:
                ab[c + '_val'] = ab[c].apply(_parse_num)
    
    # ── 逐期计算 TTM ──
    # 合并所有报告期
    all_dates = set()
    for df in [inc, bs, cf, ab]:
        if not df.empty:
            all_dates.update(df['date'].tolist())
    all_dates = sorted(all_dates)
    
    for dt in all_dates:
        rd = dt.strftime('%Y-%m-%d')
        qtype = _quarter_type(rd)
        year = int(rd[:4])
        y_prev = year - 1
        
        # ── 1) 计算 TTM 净利润 ──
        ttm_np = np.nan
        cur_np = _get_val_at_date(inc, 'date', dt, '*归属于母公司所有者的净利润_val')
        
        if not pd.isna(cur_np):
            if qtype == 'Q4':
                ttm_np = cur_np  # 年报即全年
            else:
                # TTM = 当前累计值 + (上年年报 - 上年同期累计值)
                # 上年年报始终用 y_prev-12-31
                prev_annual_dt = pd.Timestamp(f'{y_prev}-12-31')
                prev_annual = _get_val_at_date(inc, 'date', 
                    prev_annual_dt,
                    '*归属于母公司所有者的净利润_val')
                prev_same = _get_val_at_date(inc, 'date',
                    dt - pd.DateOffset(years=1),
                    '*归属于母公司所有者的净利润_val')
                if not pd.isna(prev_annual) and not pd.isna(prev_same):
                    ttm_np = cur_np + (prev_annual - prev_same)
        
        # ── 2) 计算 TTM 营业总收入 ──
        ttm_rev = np.nan
        cur_rev = _get_val_at_date(inc, 'date', dt, '*营业总收入_val')
        if not pd.isna(cur_rev):
            if qtype == 'Q4':
                ttm_rev = cur_rev
            else:
                prev_annual_rev = _get_val_at_date(inc, 'date',
                    pd.Timestamp(f'{y_prev}-12-31'),
                    '*营业总收入_val')
                prev_same_rev = _get_val_at_date(inc, 'date',
                    dt - pd.DateOffset(years=1),
                    '*营业总收入_val')
                if not pd.isna(prev_annual_rev) and not pd.isna(prev_same_rev):
                    ttm_rev = cur_rev + (prev_annual_rev - prev_same_rev)
        
        # ── 3) TTM 经营现金流 ──
        ttm_cfo = np.nan
        cur_cfo = _get_val_at_date(cf, 'date', dt, '*经营活动产生的现金流量净额_val')
        if not pd.isna(cur_cfo):
            if qtype == 'Q4':
                ttm_cfo = cur_cfo
            else:
                prev_annual_cfo = _get_val_at_date(cf, 'date',
                    pd.Timestamp(f'{y_prev}-12-31'),
                    '*经营活动产生的现金流量净额_val')
                prev_same_cfo = _get_val_at_date(cf, 'date',
                    dt - pd.DateOffset(years=1),
                    '*经营活动产生的现金流量净额_val')
                if not pd.isna(prev_annual_cfo) and not pd.isna(prev_same_cfo):
                    ttm_cfo = cur_cfo + (prev_annual_cfo - prev_same_cfo)
        
        # ── 4) 净资产（归属母公司） ──
        equity = _get_val_at_date(bs, 'date', dt, '*归属于母公司所有者权益合计_val')
        
        # ── 5) 资产负债率 ──
        liability_ratio = _get_val_at_date(ab, 'date', dt, '资产负债率_val')
        
        # ── 6) 总资产（用于ROA） ──
        total_assets = _get_val_at_date(bs, 'date', dt, '*资产合计_val')
        
        # ── 7) 流动比率 ──
        current_ratio = _get_val_at_date(ab, 'date', dt, '流动比率_val')
        
        # ── 8) 毛利率 ──
        gross_margin = _get_val_at_date(ab, 'date', dt, '销售毛利率_val')
        
        # ── 9) 总资产周转率 (TTM basis) ──
        # 资产周转率 = TTM营收 / 平均总资产
        asset_turnover = np.nan
        if not pd.isna(ttm_rev) and not pd.isna(total_assets) and total_assets > 0:
            # 用上年末总资产做期初
            prev_bs = bs[bs['date'] < dt].sort_values('date')
            if not prev_bs.empty:
                prev_assets = prev_bs.iloc[-1].get('*资产合计_val', np.nan)
                if not pd.isna(prev_assets) and prev_assets > 0:
                    avg_assets = (prev_assets + total_assets) / 2
                    if avg_assets > 0:
                        asset_turnover = ttm_rev / avg_assets
                else:
                    asset_turnover = ttm_rev / total_assets
            else:
                asset_turnover = ttm_rev / total_assets
        
        # ── 10) TTM ROE ──
        ttm_roe = np.nan
        if not pd.isna(ttm_np) and not pd.isna(equity) and equity > 0:
            # 用平均权益
            prev_bs = bs[bs['date'] < dt].sort_values('date')
            if not prev_bs.empty:
                prev_equity = prev_bs.iloc[-1].get('*归属于母公司所有者权益合计_val', np.nan)
                if not pd.isna(prev_equity) and prev_equity > 0:
                    avg_equity = (prev_equity + equity) / 2
                    ttm_roe = ttm_np / avg_equity * 100
                else:
                    ttm_roe = ttm_np / equity * 100
            else:
                ttm_roe = ttm_np / equity * 100
        
        # ── 11) TTM 扣非净利润 ──
        ttm_ded_np = np.nan
        cur_ded = _get_val_at_date(inc, 'date', dt, '*扣除非经常性损益后的净利润_val')
        if not pd.isna(cur_ded):
            if qtype == 'Q4':
                ttm_ded_np = cur_ded
            else:
                prev_annual_ded = _get_val_at_date(inc, 'date',
                    pd.Timestamp(f'{y_prev}-12-31'),
                    '*扣除非经常性损益后的净利润_val')
                prev_same_ded = _get_val_at_date(inc, 'date',
                    dt - pd.DateOffset(years=1),
                    '*扣除非经常性损益后的净利润_val')
                if not pd.isna(prev_annual_ded) and not pd.isna(prev_same_ded):
                    ttm_ded_np = cur_ded + (prev_annual_ded - prev_same_ded)
        
        entry = {
            '报告期': rd,
            '季度类型': qtype,
            'TTM归母净利润(亿)': round(ttm_np, 2) if not pd.isna(ttm_np) else None,
            'TTM营业总收入(亿)': round(ttm_rev, 2) if not pd.isna(ttm_rev) else None,
            'TTM经营现金流(亿)': round(ttm_cfo, 2) if not pd.isna(ttm_cfo) else None,
            'TTM扣非净利润(亿)': round(ttm_ded_np, 2) if not pd.isna(ttm_ded_np) else None,
            '净资产(亿)': round(equity, 2) if not pd.isna(equity) else None,
            '总资产(亿)': round(total_assets, 2) if not pd.isna(total_assets) else None,
            'TTM ROE(%)': round(ttm_roe, 2) if not pd.isna(ttm_roe) else None,
            '资产负债率(%)': round(liability_ratio, 2) if not pd.isna(liability_ratio) else None,
            '流动比率': round(current_ratio, 4) if not pd.isna(current_ratio) else None,
            '毛利率(%)': round(gross_margin, 2) if not pd.isna(gross_margin) else None,
            '总资产周转率(TTM)': round(asset_turnover, 4) if not pd.isna(asset_turnover) else None,
        }
        
        results.append(entry)
    
    return pd.DataFrame(results)


def _get_val_at_date(df, date_col, target_date, val_col):
    """从DataFrame取目标日期行指定列的值，精确匹配"""
    if val_col not in df.columns:
        return np.nan
    mask = df[date_col] == target_date
    if mask.any():
        return df.loc[mask, val_col].iloc[0]
    return np.nan


# ─── F-Score 计算 ───────────────────────────────────────────

def calc_f_score(ttm_df, ab, bs, inc, cf):
    """
    基于 TTM 数据的 Piotroski F-Score（0~9分）
    需要至少两个相邻报告期计算变化
    """
    if len(ttm_df) < 2:
        return ttm_df
    
    df = ttm_df.copy()
    df['F-Score'] = 0
    df['F_明细'] = ''
    
    for i in range(1, len(df)):
        cur = df.iloc[i]
        prev = df.iloc[i-1]
        dt_cur = pd.Timestamp(cur['报告期'])
        dt_prev = pd.Timestamp(prev['报告期'])
        
        score = 0
        details = []
        
        # F1: ROA > 0 (TTM净利润 > 0)
        np_val = cur['TTM归母净利润(亿)']
        if np_val is not None and np_val > 0:
            score += 1
            details.append('F1+')
        else:
            details.append('F1-')
        
        # F2: CFO > 0 (TTM经营现金流 > 0)
        cfo_val = cur['TTM经营现金流(亿)']
        if cfo_val is not None and cfo_val > 0:
            score += 1
            details.append('F2+')
        else:
            details.append('F2-')
        
        # F3: ROA 改善 (当前TTM ROE > 上一期TTM ROE... 
        # 实际上F-Score用ROA变化, 我们用TTM净利润率改善近似)
        roe_cur = cur['TTM ROE(%)']
        roe_prev = prev['TTM ROE(%)']
        if roe_cur is not None and roe_prev is not None and roe_cur > roe_prev:
            score += 1
            details.append('F3+')
        else:
            details.append('F3-')
        
        # F4: CFO > TTM净利润 (应计质量 - Accrual)
        if cfo_val is not None and np_val is not None and cfo_val > np_val:
            score += 1
            details.append('F4+')
        else:
            details.append('F4-')
        
        # F5: 资产负债率降低 (长期负债比率改善)
        # 用资产负债率变化
        dr_cur = _get_val_at_date(ab, 'date', dt_cur, '资产负债率_val')
        dr_prev = _get_val_at_date(ab, 'date', dt_prev, '资产负债率_val')
        if dr_cur is not None and dr_prev is not None and dr_cur < dr_prev:
            score += 1
            details.append('F5+')
        else:
            details.append('F5-')
        
        # F6: 流动比率改善
        cr_cur = _get_val_at_date(ab, 'date', dt_cur, '流动比率_val')
        cr_prev = _get_val_at_date(ab, 'date', dt_prev, '流动比率_val')
        if cr_cur is not None and cr_prev is not None and cr_cur > cr_prev:
            score += 1
            details.append('F6+')
        else:
            details.append('F6-')
        
        # F7: 无增发新股 (检查股本是否增加)
        # 用基本每股收益的变化来反推 - 如果EPS稀释了则扣分
        eps_cur = _get_val_at_date(ab, 'date', dt_cur, '基本每股收益_val')
        eps_prev = _get_val_at_date(ab, 'date', dt_prev, '基本每股收益_val')
        # 更直接：比较净资产增长 vs 留存收益增长
        equity_cur = cur['净资产(亿)']
        equity_prev = prev['净资产(亿)']
        np_ttm_cur = cur['TTM归母净利润(亿)']
        if equity_cur is not None and equity_prev is not None and np_ttm_cur is not None:
            # 如果权益增长 < TTM净利润，说明有分红或回购（好事）；反之可能增发
            equity_growth = equity_cur - equity_prev
            if equity_growth <= np_ttm_cur * 1.1:  # 权益增长基本来自留存收益
                score += 1
                details.append('F7+')
            else:
                details.append('F7-')
        else:
            details.append('F7-')
        
        # F8: 毛利率改善
        gm_cur = _get_val_at_date(ab, 'date', dt_cur, '销售毛利率_val')
        gm_prev = _get_val_at_date(ab, 'date', dt_prev, '销售毛利率_val')
        if gm_cur is not None and gm_prev is not None and gm_cur > gm_prev:
            score += 1
            details.append('F8+')
        else:
            details.append('F8-')
        
        # F9: 总资产周转率改善
        at_cur = cur['总资产周转率(TTM)']
        at_prev = prev['总资产周转率(TTM)']
        if at_cur is not None and at_prev is not None and at_cur > at_prev:
            score += 1
            details.append('F9+')
        else:
            details.append('F9-')
        
        df.at[df.index[i], 'F-Score'] = score
        df.at[df.index[i], 'F_明细'] = '|'.join(details)
    
    return df


# ─── 批量分析 ────────────────────────────────────────────────

def analyze_stocks(stock_codes, stock_names=None):
    """
    批量分析多只股票，计算最新报告期的TTM指标和F-Score
    返回综合DataFrame
    """
    if stock_names is None:
        stock_names = {}
    
    all_results = []
    
    for i, code in enumerate(stock_codes):
        name = stock_names.get(code, code)
        print(f'\n[{i+1}/{len(stock_codes)}] 分析 {name}({code}) ...')
        
        try:
            data = fetch_financial_data(code)
            ab, bs, inc, cf_df = _prep_dataframes(data)
            
            ttm = calc_ttm_metrics(ab, bs, inc, cf_df)
            if ttm.empty:
                print(f'  ❌ {code}: 无数据')
                continue
            
            ttm = calc_f_score(ttm, ab, bs, inc, cf_df)
            
            if not ttm.empty:
                latest = ttm.iloc[-1].to_dict()
                latest['股票代码'] = code
                latest['股票名称'] = name
                all_results.append(latest)
                print(f'  ✅ 最新ROE={latest.get("TTM ROE(%)", "N/A")}%  F-Score={latest.get("F-Score", "N/A")}')
            
        except Exception as e:
            print(f'  ❌ {code} 分析失败: {e}')
            import traceback
            traceback.print_exc()
    
    return pd.DataFrame(all_results)


def _prep_dataframes(data):
    """为财务DataFrame添加date列和_val解析列"""
    ab = data.get('abstract', pd.DataFrame()).copy()
    if not ab.empty:
        ab['date'] = pd.to_datetime(ab['报告期'])
        ab = ab.sort_values('date').reset_index(drop=True)
        for c in ['净利润', '营业总收入', '净资产收益率', '净资产收益率-摊薄',
                   '基本每股收益', '每股净资产', '每股经营现金流', '销售毛利率',
                   '销售净利率', '流动比率', '速动比率', '资产负债率']:
            if c in ab.columns:
                ab[c + '_val'] = ab[c].apply(_parse_num)
    
    bs = data.get('balance', pd.DataFrame()).copy()
    if not bs.empty:
        bs['date'] = pd.to_datetime(bs['报告期'])
        bs = bs.sort_values('date').reset_index(drop=True)
        for c in ['*资产合计', '*负债合计', '*归属于母公司所有者权益合计']:
            if c in bs.columns:
                bs[c + '_val'] = bs[c].apply(_parse_num)
    
    inc = data.get('income', pd.DataFrame()).copy()
    if not inc.empty:
        inc['date'] = pd.to_datetime(inc['报告期'])
        inc = inc.sort_values('date').reset_index(drop=True)
        for c in ['*净利润', '*营业总收入', '*归属于母公司所有者的净利润',
                   '*扣除非经常性损益后的净利润']:
            if c in inc.columns:
                inc[c + '_val'] = inc[c].apply(_parse_num)
    
    cf_df = data.get('cashflow', pd.DataFrame()).copy()
    if not cf_df.empty:
        cf_df['date'] = pd.to_datetime(cf_df['报告期'])
        cf_df = cf_df.sort_values('date').reset_index(drop=True)
        for c in ['*经营活动产生的现金流量净额', '*现金及现金等价物净增加额',
                   '*期末现金及现金等价物余额']:
            if c in cf_df.columns:
                cf_df[c + '_val'] = cf_df[c].apply(_parse_num)
    
    return ab, bs, inc, cf_df


def analyze_single_deep(code):
    """
    对单只股票做深度TTM分析，返回完整的时序DataFrame
    """
    data = fetch_financial_data(code)
    ab, bs, inc, cf_df = _prep_dataframes(data)
    
    ttm = calc_ttm_metrics(ab, bs, inc, cf_df)
    ttm = calc_f_score(ttm, ab, bs, inc, cf_df)
    return ttm, data


if __name__ == '__main__':
    # 测试
    code = '600519'
    ttm, _ = analyze_single_deep(code)
    print(f'\n{"="*60}')
    print(f'{code} TTM分析')
    print(ttm.tail(10).to_string(index=False))
