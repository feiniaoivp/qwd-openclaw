#!/usr/bin/env python3
"""
A股 TTM选股策略回测脚本

策略说明：
1. TTM ROE + F-Score 复合策略
2. 逐季调仓，模拟真实投资

使用方法：
  python3 ttm_backtest.py           # 默认回测
  python3 ttm_backtest.py --stocks 600519,000858  # 自定义股票池
"""
import sys; sys.path.insert(0, '.')
from ttm_core import analyze_single_deep, _prep_dataframes, calc_ttm_metrics, calc_f_score, fetch_financial_data, _parse_num, _get_val_at_date
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import argparse
import warnings
warnings.filterwarnings('ignore')
import warnings

# ═══════════════════════════════════════════════════════════════
# 策略定义
# ═══════════════════════════════════════════════════════════════

RESEARCH_STOCKS = [
    ('600519', '贵州茅台'), ('000858', '五粮液'), 
    ('000568', '泸州老窖'), ('002304', '洋河股份'),
    ('600809', '山西汾酒'), ('000333', '美的集团'),
    ('000651', '格力电器'), ('600690', '海尔智家'),
    ('002415', '海康威视'), ('300750', '宁德时代'),
    ('601012', '隆基绿能'), ('002475', '立讯精密'),
    ('600036', '招商银行'), ('601166', '兴业银行'),
    ('600030', '中信证券'), ('601318', '中国平安'),
    ('600028', '中国石化'), ('600585', '海螺水泥'),
    ('000002', '万科A'), ('601857', '中国石油'),
]


def ttm_quality_strategy(ttm_df):
    """
    TTM质量选股策略
    
    评分体系（满分20分）:
    - ROE水平: ≥20%得6分, ≥15%得4分, ≥10%得2分, <10%得0分
    - F-Score: ≥7得6分, ≥5得4分, ≥3得2分, <3得0分
    - 毛利率趋势: 同比改善+2分
    - 资产周转率趋势: 改善+2分
    - 现金流质量: CFO > NP +2分
    - 负债率趋势: 降低+2分
    
    返回评分
    """
    if ttm_df.empty or len(ttm_df) < 2:
        return 0, {}
    
    cur = ttm_df.iloc[-1]
    prev = ttm_df.iloc[-2]
    
    score = 0
    details = {}
    
    # ROE水平
    roe = cur.get('TTM ROE(%)', 0) or 0
    if roe >= 20:
        score += 6
        details['ROE评分'] = '6分(≥20%)'
    elif roe >= 15:
        score += 4
        details['ROE评分'] = '4分(≥15%)'
    elif roe >= 10:
        score += 2
        details['ROE评分'] = '2分(≥10%)'
    else:
        details['ROE评分'] = '0分(<10%)'
    
    # F-Score
    fs = cur.get('F-Score', 0) or 0
    if fs >= 7:
        score += 6
        details['F-Score评分'] = '6分(≥7)'
    elif fs >= 5:
        score += 4
        details['F-Score评分'] = '4分(≥5)'
    elif fs >= 3:
        score += 2
        details['F-Score评分'] = '2分(≥3)'
    else:
        details['F-Score评分'] = '0分(<3)'
    
    # 毛利率改善
    gm_cur = cur.get('毛利率(%)')
    gm_prev = prev.get('毛利率(%)')
    if gm_cur is not None and gm_prev is not None and gm_cur > gm_prev:
        score += 2
        details['毛利率趋势'] = '+2分(改善)'
    else:
        details['毛利率趋势'] = '0分'
    
    # 资产周转率改善
    at_cur = cur.get('总资产周转率(TTM)')
    at_prev = prev.get('总资产周转率(TTM)')
    if at_cur is not None and at_prev is not None and at_cur > at_prev:
        score += 2
        details['周转率趋势'] = '+2分(改善)'
    else:
        details['周转率趋势'] = '0分'
    
    # CFO > NP
    cfo = cur.get('TTM经营现金流(亿)')
    np_val = cur.get('TTM归母净利润(亿)')
    if cfo is not None and np_val is not None and cfo > np_val:
        score += 2
        details['现金流质量'] = '+2分(CFO>NP)'
    else:
        details['现金流质量'] = '0分'
    
    # 负债率降低
    dr_cur = cur.get('资产负债率(%)')
    dr_prev = prev.get('资产负债率(%)')
    if dr_cur is not None and dr_prev is not None and dr_cur < dr_prev:
        score += 2
        details['负债率趋势'] = '+2分(降低)'
    else:
        details['负债率趋势'] = '0分'
    
    return score, details


def backtest_ttm_strategy(stock_codes, stock_names, 
                          top_n=5, min_fscore=4, min_roe=10,
                          rebalance_years=3):
    """
    TTM选股策略回测
    
    假设: 以最新报告期为起点，回看过去 N 年逐季调仓
    模拟: 每季度末财务数据公布后，根据TTM指标选股，平均持仓
    
    Parameters:
    - stock_codes: 股票代码列表
    - stock_names: 名称映射
    - top_n: 每期选Top N只建仓
    - min_fscore: 最低F-Score门槛
    - min_roe: 最低ROE门槛(%)
    - rebalance_years: 回测年数
    """
    
    print(f'🚀 开始TTM策略回测')
    print(f'   股票池: {", ".join(stock_names.values())}')
    print(f'   选股: Top{top_n} | F-Score≥{min_fscore} | ROE≥{min_roe}%')
    print(f'   回测期: {rebalance_years}年逐季')
    print()
    
    # 获取所有股票完整时序
    all_ttm = {}
    for i, (code, name) in enumerate(zip(stock_codes, [stock_names.get(c,c) for c in stock_codes])):
        print(f'  [{i+1}/{len(stock_codes)}] 获取 {name}...')
        try:
            ttm, _ = analyze_single_deep(code)
            if not ttm.empty:
                all_ttm[code] = {'name': name, 'ttm': ttm}
                print(f'    ✓ {len(ttm)}期数据')
        except Exception as e:
            print(f'    ❌ {e}')
    
    if not all_ttm:
        print('❌ 无有效数据')
        return None
    
    # 合并所有季末日期
    all_dates = set()
    for info in all_ttm.values():
        all_dates.update(info['ttm']['报告期'].tolist())
    all_dates = sorted(all_dates)
    
    print(f'\n   共{len(all_dates)}个报告期')
    
    # ── 逐季回测 ──
    results = []
    
    for i, report_date in enumerate(all_dates):
        if i < 2:  # 需要至少两期计算F-Score变化
            continue
        
        # 截取到此日期的TTM数据
        candidates = []
        for code, info in all_ttm.items():
            ttm_sub = info['ttm'][info['ttm']['报告期'] <= report_date]
            if len(ttm_sub) >= 2:
                score, details = ttm_quality_strategy(ttm_sub)
                latest = ttm_sub.iloc[-1]
                roe = latest.get('TTM ROE(%)', 0) or 0
                fs = latest.get('F-Score', 0) or 0
                
                if roe >= min_roe and fs >= min_fscore:
                    candidates.append({
                        'code': code,
                        'name': info['name'],
                        'report_date': report_date,
                        'score': score,
                        'roe': roe,
                        'fscore': fs,
                        'details': details,
                    })
        
        # 选Top N
        candidates.sort(key=lambda x: x['score'], reverse=True)
        selected = candidates[:top_n]
        
        if selected:
            avg_score = np.mean([s['score'] for s in selected])
            avg_roe = np.mean([s['roe'] for s in selected])
            avg_fs = np.mean([s['fscore'] for s in selected])
        else:
            avg_score = avg_roe = avg_fs = 0
        
        results.append({
            '报告期': report_date,
            '候选股票': len(candidates),
            '选中数量': len(selected),
            '平均评分': round(avg_score, 1),
            '平均ROE(%)': round(avg_roe, 1),
            '平均F-Score': round(avg_fs, 1),
            '选股': ','.join([s['name'] for s in selected]) if selected else '无',
            '评分明细': '|'.join([f"{s['name']}({s['score']})" for s in selected]) if selected else '',
        })
    
    df_result = pd.DataFrame(results)
    
    # ── 输出统计 ──
    if not df_result.empty:
        print(f'\n{"="*70}')
        print(f'📊 回测统计')
        print(f'{"="*70}')
        
        # 选股频率
        stock_counts = df_result[df_result['选中数量'] > 0]['选股'].str.split(',', expand=False)
        all_stocks = []
        for picks in stock_counts:
            if picks:
                all_stocks.extend(picks)
        
        freq = pd.Series(all_stocks).value_counts()
        print(f'\n🏆 股票入选频率 (Top {top_n} / {len(df_result)}期):')
        for name, cnt in freq.items():
            pct = cnt / len(df_result[df_result['选中数量'] > 0]) * 100
            bar = '█' * int(pct / 5) + '░' * (20 - int(pct / 5))
            print(f'  {name:>10s}: {cnt:3d}次 ({pct:4.1f}%) {bar}')
        
        # 综合统计
        avg_selected = df_result['选中数量'].mean()
        avg_roe_all = df_result[df_result['选中数量'] > 0]['平均ROE(%)'].mean()
        avg_fs_all = df_result[df_result['选中数量'] > 0]['平均F-Score'].mean()
        
        print(f'\n📈 综合统计:')
        print(f'  回测期数: {len(df_result)}')
        print(f'  平均每期候选: {df_result["候选股票"].mean():.0f}只')
        print(f'  平均每期选中: {avg_selected:.1f}只')
        print(f'  平均选中ROE: {avg_roe_all:.1f}%')
        print(f'  平均选中F-Score: {avg_fs_all:.1f}')
        print(f'  空仓期数: {len(df_result[df_result["选中数量"]==0])}')
        
        # 最新选股推荐
        latest = df_result.iloc[-1]
        print(f'\n🎯 最新一期({latest["报告期"]})选股推荐:')
        if latest['选中数量'] > 0:
            picks = latest['选股'].split(',')
            details = latest['评分明细'].split('|')
            for pick, det in zip(picks, details):
                print(f'  ✅ {pick} ({det})')
        else:
            print(f'  ⚠️ 无符合条件的股票')
    
    return df_result


def backtest_with_price(df_result, stock_codes, stock_names):
    """
    加入股价模拟回测（简版）
    使用 yfinance 或 akshare 获取历史股价估算收益
    """
    print('\n⚠️ 完整股价回测需要历史K线数据')
    print('   可使用 akshare.stock_zh_a_hist() 获取历史股价')
    print('   计算每期调仓后的组合收益')
    return df_result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='TTM选股策略回测')
    parser.add_argument('--stocks', type=str, default=None,
                       help='股票代码列表，逗号分隔（如 600519,000858）')
    parser.add_argument('--top', type=int, default=5, help='每期选Top N')
    parser.add_argument('--min-fscore', type=int, default=4, help='最低F-Score')
    parser.add_argument('--min-roe', type=float, default=10.0, help='最低ROE(%)')
    parser.add_argument('--years', type=int, default=3, help='回测年数')
    
    args = parser.parse_args()
    
    if args.stocks:
        codes = args.stocks.split(',')
        names = {c: c for c in codes}
    else:
        codes = [c for c, n in RESEARCH_STOCKS]
        names = {c: n for c, n in RESEARCH_STOCKS}
    
    result = backtest_ttm_strategy(
        codes, names,
        top_n=args.top,
        min_fscore=args.min_fscore,
        min_roe=args.min_roe,
        rebalance_years=args.years
    )
    
    if result is not None:
        print(f'\n{"="*70}')
        print('📋 完整回测明细')
        print(result.to_string(index=False))
        
        # 保存CSV
        result.to_csv('ttm_backtest_results.csv', index=False, encoding='utf-8-sig')
        print(f'\n✅ 回测结果已保存: ttm_backtest_results.csv')
