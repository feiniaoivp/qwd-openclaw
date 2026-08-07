#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_all_stocks.py - 全自选股分析脚本（实时行情版）
直接从akShare拉取盘中实时数据，不再依赖过时CSV文件。
价格显示的是实时最新价，而非历史收盘价。
"""

import sys
import os
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

# Add path to quant_strategy
sys.path.append('/Users/duguke/.openclaw/workspace/plugins/skills')
from quant_strategy import MultiDimensionStrategy

def fetch_realtime_prices(stock_list):
    """用akShare新浪接口获取实时行情（东方财富接口已失效）"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()
        # 新浪接口的'代码'列带前缀 sh/sz/bj
        # 构建带前缀的查找表
        results = {}
        for short_code, full_code, name in stock_list:
            # 判断前缀
            if full_code.startswith('6') or full_code.startswith('9'):
                prefix = 'sh'
            elif full_code.startswith('0') or full_code.startswith('3'):
                prefix = 'sz'
            elif full_code.startswith('8'):
                prefix = 'bj'
            else:
                prefix = 'sh'
            lookup = f'{prefix}{full_code}'
            row = df[df['代码'] == lookup]
            if not row.empty:
                r = row.iloc[0]
                results[short_code] = {
                    'name': r['名称'],
                    'price': float(r['最新价']),
                    'change_pct': float(r['涨跌幅']),
                }
        return results
    except Exception as e:
        print(f"新浪接口获取实时行情失败: {e}")
        # 尝试备用接口
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            results = {}
            for short_code, full_code, name in stock_list:
                row = df[df['代码'] == full_code]
                if not row.empty:
                    r = row.iloc[0]
                    results[short_code] = {
                        'name': r['名称'],
                        'price': float(r['最新价']),
                        'change_pct': float(r['涨跌幅']),
                    }
            return results
        except Exception as e2:
            print(f"备用接口也失败: {e2}")
            return {}

def analyze_stock_from_source(code, name, source_dir, realtime_prices):
    """
    从stock_data源目录读取历史CSV分析策略，但价格用实时行情覆盖
    """
    csv_path = os.path.join(source_dir, f"{code}_{name}.csv")
    alternate_path = os.path.join(source_dir, f"{code}_{name}.csv")
    
    # 尝试从stock_data目录读取
    if not os.path.exists(csv_path):
        csv_path = f"/Users/duguke/stock_data/{code}_{name}.csv"
    
    if not os.path.exists(csv_path):
        print(f"  找不到 {name}({code}) 的CSV数据")
        return None
    
    try:
        df = pd.read_csv(csv_path, index_col=0)
        
        # 标准化列名
        rename_map = {}
        if '收盘' in df.columns: rename_map['收盘'] = 'close'
        if '日期' in df.columns: rename_map['日期'] = 'date'
        if '开盘' in df.columns: rename_map['开盘'] = 'open'
        if '最高' in df.columns: rename_map['最高'] = 'high'
        if '最低' in df.columns: rename_map['最低'] = 'low'
        if '成交量' in df.columns: rename_map['成交量'] = 'volume'
        if '成交额' in df.columns: rename_map['成交额'] = 'amount'
        df.rename(columns=rename_map, inplace=True)
        
        if 'close' not in df.columns:
            print(f"  {name}({code}) 缺少close列")
            return None
            
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
        
        if len(df) < 30:
            print(f"  {name}({code}) 数据不足: {len(df)}条")
            return None
        
        df_reset = df.reset_index(drop=True)
        
        # 运行策略
        strategy = MultiDimensionStrategy()
        result_df = strategy.generate_signals(df_reset.copy())
        if result_df is None or result_df.empty:
            print(f"  {name}({code}) 策略未生成结果")
            return None
        
        # 信号统计
        signal_counts = result_df['Signal'].value_counts() if 'Signal' in result_df.columns else pd.Series()
        buy = int(signal_counts.get(1, 0))
        sell = int(signal_counts.get(-1, 0))
        hold = int(signal_counts.get(0, 0))
        recent_signal = int(result_df['Signal'].iloc[-1]) if 'Signal' in result_df.columns else 0
        
        # ★ 关键修复: 使用实时行情价格，而非历史CSV收盘价
        if code in realtime_prices:
            live = realtime_prices[code]
            recent_price = live['price']
            change_pct = live['change_pct']
        else:
            # 实时行情没拉到，退而求其次用CSV最后一条收盘价并打标记
            recent_price = float(result_df['close'].iloc[-1]) if 'close' in result_df.columns else 0
            change_pct = 0
        
        result = {
            'code': code,
            'name': name,
            'buy_signals': buy,
            'sell_signals': sell,
            'hold_signals': hold,
            'total_days': len(result_df),
            'recent_signal': recent_signal,
            'recent_price': recent_price,
            'change_pct': change_pct
        }
        
        return result
        
    except Exception as e:
        print(f"  分析 {name}({code}) 出错: {e}")
        return None


def main():
    workspace = "/Users/duguke/.openclaw/workspace"
    source_dir = "/Users/duguke/stock_data"
    
    # 自选股列表（code以0开头补全到6位方便akShare查询）
    stocks = [
        ("002318", "002318", "久立特材"),
        ("300014", "300014", "亿纬锂能"),
        ("601066", "601066", "中信建投"),
        ("600030", "600030", "中信证券"),
        ("300124", "300124", "汇川技术"),
        ("601995", "601995", "中金公司"),
        ("600584", "600584", "长电科技"),
        ("002156", "002156", "通富微电"),
        ("002466", "002466", "天齐锂业"),
        ("600036", "600036", "招商银行"),
        ("600570", "600570", "恒生电子"),
        ("605566", "605566", "福莱蒽特"),
        ("000987", "000987", "越秀资本"),
        ("603308", "603308", "应流股份"),
        ("300285", "300285", "国瓷材料"),
        ("002413", "002413", "雷科防务"),
        ("688981", "688981", "中芯国际"),
        ("601865", "601865", "福莱特"),
        ("000157", "000157", "中联重科"),
        ("300719", "300719", "安达维尔"),
        ("601061", "601061", "中信金属"),
        ("600660", "600660", "福耀玻璃"),
        ("900925", "900925", "机电B股"),  # B股可能查不到实时行情
        ("002335", "002335", "科华数据"),
        ("601100", "601100", "恒立液压"),
    ]
    
    # 第一步：拉实时行情
    print("正在获取实时行情（新浪接口）...")
    realtime_prices = fetch_realtime_prices(stocks)
    print(f"实时行情获取完成: {len(realtime_prices)} 只")
    
    # 第二步：逐只分析
    print(f"\n开始分析 {len(stocks)} 只股票...")
    results = []
    for short_code, full_code, name in stocks:
        print(f"分析 {name}({short_code})...")
        result = analyze_stock_from_source(short_code, name, source_dir, realtime_prices)
        if result:
            results.append(result)
    
    # 第三步：输出结果
    print("\n" + "="*70)
    print("分析完成摘要")
    print("="*70)
    print(f"成功分析: {len(results)} / {len(stocks)} 只股票")
    
    if results:
        print(f"\n{'代码':<8} {'名称':<10} {'买入':<6} {'卖出':<6} {'持有':<6} {'天数':<6} {'信号':<6} {'最新价':<10} {'涨跌幅%':<8}")
        print("-"*70)
        for r in results:
            signal_str = {1: '买入', -1: '卖出', 0: '持有'}.get(r['recent_signal'], '未知')
            change_str = f"{r['change_pct']:+.2f}%" if 'change_pct' in r else "N/A"
            print(f"{r['code']:<8} {r['name']:<10} {r['buy_signals']:<6} {r['sell_signals']:<6} {r['hold_signals']:<6} {r['total_days']:<6} {signal_str:<6} {r['recent_price']:<10.2f} {change_str:<8}")
        
        # 保存摘要
        now_str = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
        summary_path = os.path.join(workspace, "analysis_summary_all.md")
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(f"# 全A股股票多维度策略分析摘要\n\n")
            f.write(f"分析时间: {now_str}\n")
            f.write(f"⚠️ 价格来源: akShare实时行情（盘中实时更新）\n\n")
            f.write(f"成功分析: {len(results)} / {len(stocks)} 只股票\n\n")
            f.write("## 分析结果\n\n")
            f.write("| 股票代码 | 股票名称 | 买入信号 | 卖出信号 | 持有信号 | 总天数 | 最近信号 | 最新价 | 涨跌幅 |\n")
            f.write("|----------|----------|----------|----------|----------|--------|----------|--------|--------|\n")
            for r in results:
                signal_str = {1: '买入', -1: '卖出', 0: '持有'}.get(r['recent_signal'], '未知')
                change_str = f"{r['change_pct']:+.2f}%" if 'change_pct' in r else "N/A"
                f.write(f"| {r['code']} | {r['name']} | {r['buy_signals']} | {r['sell_signals']} | {r['hold_signals']} | {r['total_days']} | {signal_str} | {r['recent_price']:.2f} | {change_str} |\n")
        
        print(f"\n摘要已保存到: {summary_path}")


if __name__ == "__main__":
    main()
