#!/usr/bin/env python3
"""
每日股票量化分析模拟盘
- 从 watchlist 获取关注股票
- 运行技术分析 + 基本面评分
- 生成每日信号（买入/卖出/持有）
- 记录到本地 CSV 用于回测和参数校正
"""

import sys
import os
import json
import csv
from datetime import datetime, date
from pathlib import Path

# 添加 stock-analysis 脚本路径
sys.path.insert(0, '/Users/duguke/.openclaw/workspace/skills/stock-analysis/scripts')

from watchlist import WatchlistManager
from analyze_stock import analyze_stock, print_analysis

# 数据存储路径
DATA_DIR = Path('/Users/duguke/.openclaw/workspace/quant_data')
DATA_DIR.mkdir(exist_ok=True)
SIGNALS_CSV = DATA_DIR / 'daily_signals.csv'
PORTFOLIO_CSV = DATA_DIR / 'paper_portfolio.csv'
PARAMS_JSON = DATA_DIR / 'strategy_params.json'

# 默认策略参数（会根据回测自动调整）
DEFAULT_PARAMS = {
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "ma_short": 5,
    "ma_medium": 20,
    "ma_long": 60,
    "volume_surge_threshold": 1.5,
    "stop_loss_pct": 8.0,
    "take_profit_pct": 15.0,
    "max_position_pct": 10.0,  # 单只股票最大仓位%
    "min_score_buy": 6.0,      # 8维评分买入阈值
    "min_score_sell": 4.0,     # 卖出阈值
}

def load_params():
    if PARAMS_JSON.exists():
        with open(PARAMS_JSON) as f:
            return {**DEFAULT_PARAMS, **json.load(f)}
    return DEFAULT_PARAMS.copy()

def save_params(params):
    with open(PARAMS_JSON, 'w') as f:
        json.dump(params, f, indent=2)

def init_csv():
    """初始化信号记录 CSV"""
    if not SIGNALS_CSV.exists():
        with open(SIGNALS_CSV, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'date', 'symbol', 'name', 'close', 'change_pct',
                'signal', 'score', 'rsi', 'ma5', 'ma20', 'ma60',
                'volume_ratio', 'pe_ttm', 'pb', 'params_snapshot'
            ])

    if not PORTFOLIO_CSV.exists():
        with open(PORTFOLIO_CSV, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'date', 'action', 'symbol', 'name', 'price', 'shares',
                'cash_before', 'cash_after', 'reason'
            ])

def get_watchlist_symbols():
    wm = WatchlistManager()
    items = wm.list()
    symbols = []
    for item in items:
        sym = item['symbol']
        # 标准化格式：SH600030 -> 600030.SS
        if sym.startswith('SH'):
            symbols.append(sym[2:] + '.SS')
        elif sym.startswith('SZ'):
            symbols.append(sym[2:] + '.SZ')
        elif sym.endswith('.SS') or sym.endswith('.SZ'):
            symbols.append(sym)
        else:
            # 尝试推断
            if sym.startswith('6') or sym.startswith('9'):
                symbols.append(sym + '.SS')
            else:
                symbols.append(sym + '.SZ')
    return symbols

def run_analysis(symbol, params):
    """运行单只股票分析，返回结构化结果"""
    try:
        # 使用 analyze_stock 核心逻辑
        result = analyze_stock(symbol, fast_mode=True)
        return result
    except Exception as e:
        print(f"  ❌ {symbol} 分析失败: {e}")
        return None

def extract_signal(result, params):
    """从分析结果提取交易信号"""
    if not result:
        return None
    
    # 基础数据
    quote = result.get('quote', {})
    indicators = result.get('indicators', {})
    score_data = result.get('score', {})
    
    close = quote.get('regularMarketPrice', 0)
    change_pct = quote.get('regularMarketChangePercent', 0)
    
    rsi = indicators.get('rsi', 50)
    ma5 = indicators.get('ma5', close)
    ma20 = indicators.get('ma20', close)
    ma60 = indicators.get('ma60', close)
    volume_ratio = indicators.get('volumeRatio', 1.0)
    
    pe = quote.get('trailingPE', 0)
    pb = quote.get('priceToBook', 0)
    
    # 8维评分
    total_score = score_data.get('total', 0)
    
    # 信号逻辑
    signal = "HOLD"
    reasons = []
    
    # 均线多头排列
    ma_bullish = ma5 > ma20 > ma60
    ma_bearish = ma5 < ma20 < ma60
    
    # RSI
    rsi_oversold = rsi < params["rsi_oversold"]
    rsi_overbought = rsi > params["rsi_overbought"]
    
    # 量能
    vol_surge = volume_ratio > params["volume_surge_threshold"]
    
    # 评分
    score_buy = total_score >= params["min_score_buy"]
    score_sell = total_score <= params["min_score_sell"]
    
    # 综合判断
    buy_signals = sum([ma_bullish, rsi_oversold, vol_surge, score_buy])
    sell_signals = sum([ma_bearish, rsi_overbought, score_sell])
    
    if buy_signals >= 3:
        signal = "BUY"
        reasons.append(f"买入信号({buy_signals}/4)")
    elif sell_signals >= 3:
        signal = "SELL"
        reasons.append(f"卖出信号({sell_signals}/4)")
    else:
        signal = "HOLD"
        reasons.append(f"观望(买:{buy_signals} 卖:{sell_signals})")
    
    if ma_bullish: reasons.append("均线多头")
    if ma_bearish: reasons.append("均线空头")
    if rsi_oversold: reasons.append("RSI超卖")
    if rsi_overbought: reasons.append("RSI超买")
    if vol_surge: reasons.append("放量")
    
    return {
        'signal': signal,
        'score': total_score,
        'reasons': '; '.join(reasons),
        'close': close,
        'change_pct': change_pct,
        'rsi': rsi,
        'ma5': ma5,
        'ma20': ma20,
        'ma60': ma60,
        'volume_ratio': volume_ratio,
        'pe': pe,
        'pb': pb
    }

def paper_trade(signal_data, portfolio_cash=1_000_000):
    """模拟盘交易逻辑（简化版）"""
    # 这里可以扩展为完整的模拟交易系统
    # 暂时只记录信号
    pass

def main():
    print(f"\n{'='*60}")
    print(f"📊 每日量化分析模拟盘 - {date.today()}")
    print(f"{'='*60}")
    
    init_csv()
    params = load_params()
    
    symbols = get_watchlist_symbols()
    print(f"📋 关注列表: {len(symbols)} 只股票")
    print(f"⚙️ 当前策略参数: RSI({params['rsi_oversold']}/{params['rsi_overbought']}) MA({params['ma_short']}/{params['ma_medium']}/{params['ma_long']}) 评分买入>={params['min_score_buy']}")
    
    today = date.today().isoformat()
    signals_today = []
    
    for i, symbol in enumerate(symbols, 1):
        print(f"\n[{i}/{len(symbols)}] 分析 {symbol}...")
        result = run_analysis(symbol, params)
        
        if result:
            signal = extract_signal(result, params)
            if signal:
                quote = result.get('quote', {})
                name = quote.get('longName', symbol)
                
                # 记录到 CSV
                with open(SIGNALS_CSV, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        today, symbol, name, signal['close'], signal['change_pct'],
                        signal['signal'], signal['score'], signal['rsi'],
                        signal['ma5'], signal['ma20'], signal['ma60'],
                        signal['volume_ratio'], signal['pe'], signal['pb'],
                        json.dumps(params)
                    ])
                
                signals_today.append({
                    'symbol': symbol, 'name': name, **signal
                })
                
                # 打印摘要
                emoji = "🟢" if signal['signal'] == "BUY" else "🔴" if signal['signal'] == "SELL" else "🟡"
                print(f"  {emoji} {name}({symbol}): {signal['signal']} | 评分:{signal['score']:.1f} | {signal['reasons']}")
    
    # 汇总
    print(f"\n{'='*60}")
    print(f"📈 今日信号汇总 ({today})")
    print(f"{'='*60}")
    
    buys = [s for s in signals_today if s['signal'] == 'BUY']
    sells = [s for s in signals_today if s['signal'] == 'SELL']
    holds = [s for s in signals_today if s['signal'] == 'HOLD']
    
    print(f"🟢 买入: {len(buys)} 只")
    for s in buys:
        print(f"   {s['name']}({s['symbol']}) - 评分:{s['score']:.1f} - {s['reasons']}")
    
    print(f"🔴 卖出: {len(sells)} 只")
    for s in sells:
        print(f"   {s['name']}({s['symbol']}) - 评分:{s['score']:.1f} - {s['reasons']}")
    
    print(f"🟡 持有: {len(holds)} 只")
    
    # 简单的参数自适应（记录，不自动修改）
    print(f"\n💡 参数自适应建议（需人工确认）:")
    if len(buys) == 0 and len(sells) == 0:
        print("   信号稀少，考虑放宽 RSI 阈值或降低评分要求")
    elif len(buys) > len(symbols) * 0.4:
        print("   买入信号过多，考虑收紧 RSI 超卖阈值或提高评分要求")
    
    print(f"\n✅ 完成！数据已记录到 {SIGNALS_CSV}")

if __name__ == '__main__':
    main()
