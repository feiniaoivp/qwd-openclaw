#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PPO+CCI 产业链每日收盘跟踪（12只核心标的）
v2 — 加入 PPO(价格百分比振荡器) + CCI(大宗商品通道指数) 量化指标
数据源：akshare（新浪接口优先 → 东方财富备选 → yfinance兜底）
"""

import sys
import pandas as pd
import numpy as np
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# 自选股列表
# ============================================================
symbols = [
    ('600143', '金发科技'), ('688458', '科思股份'), ('002838', '道恩股份'), ('603010', '万盛股份'),
    ('600309', '万华化学'), ('002648', '卫星化学'), ('002493', '荣盛石化'),
    ('600176', '中国巨石'), ('002563', '森马服饰'),
    ('600183', '生益科技'), ('002916', '深南电路'), ('002463', '沪电股份'),
]

sectors = {
    '600143': '树脂/改性', '688458': '树脂/改性', '002838': '树脂/改性', '603010': '树脂/改性',
    '600309': '上游原料', '002648': '上游原料', '002493': '上游原料',
    '600176': '玻璃布/铜箔', '002563': '玻璃布/铜箔',
    '600183': 'CCL/PCB', '002916': 'CCL/PCB', '002463': 'CCL/PCB',
}

# YFinance 后缀映射
YF_SUFFIX = {'600143': '.SS', '688458': '.SS', '603010': '.SS',
              '600309': '.SS', '600176': '.SS', '600183': '.SS',
              '002838': '.SZ', '002648': '.SZ', '002493': '.SZ',
              '002563': '.SZ', '002916': '.SZ', '002463': '.SZ'}


# ============================================================
# PPO (Price Percentage Oscillator)
# ============================================================
def calc_ppo(close: pd.Series, fast=12, slow=26, signal=9):
    """PPO = ((EMA_fast - EMA_slow) / EMA_slow) * 100"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    ppo = ((ema_fast - ema_slow) / ema_slow) * 100
    signal_line = ppo.ewm(span=signal, adjust=False).mean()
    histogram = ppo - signal_line
    return ppo, signal_line, histogram


# ============================================================
# CCI (Commodity Channel Index)
# ============================================================
def calc_cci(high: pd.Series, low: pd.Series, close: pd.Series, period=20):
    """CCI = (TP - SMA(TP)) / (0.015 * MD)"""
    tp = (high + low + close) / 3
    sma_tp = tp.rolling(window=period).mean()
    md = tp.rolling(window=period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    cci = (tp - sma_tp) / (0.015 * md.replace(0, np.nan))
    return cci


# ============================================================
# 数据获取（多数据源兜底）
# ============================================================
def fetch_data_akshare(code: str) -> pd.DataFrame | None:
    """通过akshare获取近2个月K线"""
    try:
        import akshare as ak
        end_date = datetime.now().strftime('%Y%m%d')
        df = ak.stock_zh_a_hist(symbol=code, period='daily',
                                 start_date='20260501', end_date=end_date, adjust='qfq')
        if df is None or df.empty:
            return None
        df.rename(columns={'日期': 'date', '开盘': 'open', '最高': 'high',
                            '最低': 'low', '收盘': 'close', '成交量': 'volume',
                            '成交额': 'amount'}, inplace=True)
        df['date'] = pd.to_datetime(df['date'])
        return df
    except Exception as e:
        return None


def fetch_data_yfinance(code: str) -> pd.DataFrame | None:
    """yfinance兜底"""
    try:
        import yfinance as yf
        sym = code + YF_SUFFIX.get(code, '.SS')
        ticker = yf.Ticker(sym)
        hist = ticker.history(period='2mo')
        if hist.empty:
            return None
        hist = hist.reset_index()
        hist.rename(columns={'Date': 'date', 'Open': 'open', 'High': 'high',
                             'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
        return hist
    except Exception:
        return None


def fetch_data(code: str) -> pd.DataFrame | None:
    """多数据源优先：新浪 → 东方财富 → yfinance"""
    # 优先akshare（新浪接口）
    df = fetch_data_akshare(code)
    if df is not None and not df.empty:
        return df
    # yfinance兜底
    df = fetch_data_yfinance(code)
    return df


# ============================================================
# 主分析逻辑
# ============================================================
def analyze_one(code: str, name: str):
    """分析单只股票"""
    df = fetch_data(code)
    if df is None or df.empty or len(df) < 30:
        return {
            'sector': sectors.get(code, '—'),
            'name': name, 'code': code,
            'close': 'N/A', 'day_chg': 'N/A', 'chg_5d': 'N/A',
            'turnover': 'N/A', 'vol_ratio': 'N/A', 'amplitude': 'N/A',
            'ppo': 'N/A', 'cci': 'N/A', 'signal': '❌数据不足'
        }

    close = df['close'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)
    volume = df['volume'].astype(float)

    cur_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2]) if len(close) > 1 else cur_close
    if prev_close == 0:
        prev_close = 0.01
    day_chg = (cur_close - prev_close) / prev_close * 100

    chg_5d = day_chg
    if len(close) >= 5:
        c5 = float(close.iloc[-5])
        if c5 != 0:
            chg_5d = (cur_close - c5) / c5 * 100

    last_vol = float(volume.iloc[-1]) if not pd.isna(volume.iloc[-1]) else 0
    turnover_val = cur_close * last_vol / 1e8

    vol_window = volume.tail(min(5, len(volume)))
    avg_vol = float(vol_window[vol_window > 0].mean()) if (vol_window > 0).any() else 1
    vol_ratio = last_vol / avg_vol if avg_vol > 0 else 0

    amplitude = (float(high.iloc[-1]) - float(low.iloc[-1])) / prev_close * 100

    # ---- PPO 计算 ----
    ppo, ppo_signal, ppo_hist = calc_ppo(close)
    last_ppo = float(ppo.iloc[-1]) if not ppo.isna().all() else 0
    last_ppo_signal = float(ppo_signal.iloc[-1]) if not ppo_signal.isna().all() else 0
    last_ppo_hist = float(ppo_hist.iloc[-1]) if not ppo_hist.isna().all() else 0

    # PPO 信号：金叉/死叉
    if len(ppo) > 1:
        prev_ppo = float(ppo.iloc[-2])
        prev_signal = float(ppo_signal.iloc[-2])
        ppo_sig = '🟢PPO金叉' if (last_ppo > last_ppo_signal and prev_ppo <= prev_signal) else \
                  '🔴PPO死叉' if (last_ppo < last_ppo_signal and prev_ppo >= prev_signal) else \
                  '⚪PPO正常'
    else:
        ppo_sig = '—'

    # ---- CCI 计算 ----
    cci = calc_cci(high, low, close)
    last_cci = float(cci.iloc[-1]) if not cci.isna().all() else 0

    if last_cci > 200:
        cci_sig = '🚨极度超买'
    elif last_cci > 100:
        cci_sig = '⚠️超买区'
    elif last_cci < -200:
        cci_sig = '💥极度超卖'
    elif last_cci < -100:
        cci_sig = '💡超卖区'
    else:
        cci_sig = '⚪正常'

    ppo_str = f'{last_ppo:.2f}'
    cci_str = f'{last_cci:.1f}'

    # ---- 综合信号 ----
    signals = []
    if ppo_sig != '⚪PPO正常':
        signals.append(ppo_sig)
    if '超卖' in cci_sig:
        signals.append(cci_sig)
    if vol_ratio > 2:
        signals.append(f'量比>{vol_ratio:.1f}')
    if abs(day_chg) > 3:
        signals.append(f'涨跌{day_chg:+.1f}%')
    alert = '、'.join(signals) if signals else '—'

    return {
        'sector': sectors.get(code, '—'),
        'name': name, 'code': code,
        'close': f'{cur_close:.2f}',
        'day_chg': f'{day_chg:+.2f}%',
        'chg_5d': f'{chg_5d:+.2f}%',
        'turnover': f'{turnover_val:.2f}',
        'vol_ratio': f'{vol_ratio:.2f}',
        'amplitude': f'{amplitude:.2f}%',
        'ppo': ppo_str,
        'cci': cci_str,
        'signal': alert
    }


# ============================================================
# 格式化输出
# ============================================================
def format_report(rows):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = []
    lines.append(f'📊 **PPO+CCI 产业链收盘跟踪** ({now})')
    lines.append(f'*数据源：akshare → yfinance 兜底*')
    lines.append('')
    lines.append('| 板块 | 名称 | 代码 | 收盘价 | 日涨跌 | 5日涨跌 | 成交额(亿) | 量比 | 振幅% | PPO | CCI | 异动信号 |')
    lines.append('|------|------|------|--------|--------|----------|-----------|------|--------|-----|-----|----------|')

    for r in rows:
        has_alert = '❌' not in r['signal'] and r['signal'] != '—'
        highlight = ' ⚠️' if has_alert else ''
        lines.append(
            f"| {r['sector']} | {r['name']} | {r['code']} | {r['close']} | {r['day_chg']} | "
            f"{r['chg_5d']} | {r['turnover']} | {r['vol_ratio']} | {r['amplitude']} | "
            f"{r['ppo']} | {r['cci']} | {r['signal']}{highlight} |"
        )

    lines.append('')
    lines.append('**🔍 异动研判**')
    alert_stocks = [r for r in rows if '❌' not in r['signal'] and r['signal'] != '—']
    if alert_stocks:
        for r in alert_stocks:
            lines.append(f'- **{r["name"]}({r["code"]})** — {r["signal"]}')
    else:
        lines.append('- 今日无显著异动标的')

    lines.append('')
    lines.append('**📌 量化策略要点**')
    lines.append('- PPO金叉=短期均线上穿长期，看多信号；死叉反之')
    lines.append('- CCI>+100超买区注意回调风险；CCI<-100超卖区关注反弹机会')
    lines.append('- 量比>2+振幅>5% = 资金异动，优先跟踪')
    lines.append('- 关注生益科技/中国巨石涨价传导落地情况')

    return '\n'.join(lines)


if __name__ == '__main__':
    results = []
    for code, name in symbols:
        r = analyze_one(code, name)
        results.append(r)

    output = format_report(results)
    print(output)
