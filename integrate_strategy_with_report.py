import sys, os
sys.path.insert(0, "/Users/duguke/.openclaw/workspace/skills/gh-data")
from ghdata import data_fetcher as fetcher
from ghdata import db_manager as db
from ghdata import reporter
from ghdata import chart
from ghdata import config
import pandas as pd
import numpy as np

# 添加多维度策略路径
skills_path = "/Users/duguke/.openclaw/workspace/skills"
if skills_path not in sys.path:
    sys.path.insert(0, skills_path)
from multi_dimension_strategy import run_multi_dimension_strategy, get_latest_signal

# 确保输出目录存在
config.DOC_DIR = "./doc"
os.makedirs(config.DOC_DIR, exist_ok=True)
print(f"[Config] Report directory set to: {os.path.abspath(config.DOC_DIR)}")

def collect_all(code: str) -> dict:
    data = {}
    try:
        data["realtime"] = fetcher.fetch_realtime(code)
        data["kline"] = fetcher.fetch_kline(code, 120)
        data["tick"] = fetcher.fetch_today_tick(code)
        data["moneyFlow"] = fetcher.fetch_money_flow(code, 10)
        data["margin"] = fetcher.fetch_margin_trading(code, 10)
        data["holdings"] = fetcher.fetch_main_holdings(code)
        data["shareholder"] = fetcher.fetch_shareholder_trade(code)
        data["executive"] = fetcher.fetch_executive_change(code)
        data["financial"] = fetcher.fetch_financial(code)
        data["balance"] = fetcher.fetch_balance_sheet(code)
        data["income"] = fetcher.fetch_income_statement(code)
        data["cashflow"] = fetcher.fetch_cashflow(code)
        data["dividend"] = fetcher.fetch_dividend(code)
        data["research"] = fetcher.fetch_research_report(code)
        data["survey"] = fetcher.fetch_institutional_survey(code)
        data["unlock"] = fetcher.fetch_unlock_data(code)
        data["industry"] = fetcher.fetch_industry_info(code)
    except Exception as e:
        print(f"[ERROR] Data collection failed for {code}: {e}")
        # Return partial data if some succeeded
        return data
    return data

# 待处理的股票列表
stock_codes = [
    "600160", "300827", "600346", "000708", "300748", "002413", "601061", "900925",
    "000157", "603308", "601865", "600660", "002318", "002335", "605566", "601995",
    "600030", "601066", "000987", "601100", "600570", "300170", "300124", "300719", "002466",
    "300014", "600036", "300285", "688981", "600584", "002156"
]

print(f"Processing {len(stock_codes)} stocks with Multi-Dimensional Strategy integration...")
print("=" * 70)

successful = 0
failed = 0

for i, stock_code in enumerate(stock_codes, 1):
    print(f"\n[{i}/{len(stock_codes)}] Processing {stock_code}...")
    try:
        # 1. 数据采集
        all_data = collect_all(stock_code)
        if not all_data.get("kline"):
            print(f"[WARNING] {stock_code}: No K-line data, skipping")
            failed += 1
            continue

        # 2. 转换K线数据为DataFrame
        klines = all_data["kline"]
        df = pd.DataFrame(klines)
        # 确保必要的列存在
        if 'close' not in df.columns:
            print(f"[ERROR] {stock_code}: K-line data missing 'close' column")
            failed += 1
            continue
            
        # 确保日期列存在并排序
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
        
        print(f"  Data points: {len(df)}")

        # 3. 应用多维度策略
        processed_df = run_multi_dimension_strategy(df.copy())
        
        # 4. 分析交易信号
        buy_signals = (processed_df['Signal'] == 1).sum()
        sell_signals = (processed_df['Signal'] == -1).sum()
        latest_signal_info = get_latest_signal(processed_df)
        
        print(f"  Buy signals: {buy_signals}, Sell signals: {sell_signals}")
        print(f"  Latest signal: {latest_signal_info['action']} (RSI: {latest_signal_info['rsi']:.2f})")

        # 5. 准备额外数据用于报告增强
        # 策略结果作为第14章的“多维度量化共振模型评级”
        extra_data_for_report = {
            "multidim_strategy": {
                "points": [
                    f"策略信号: {latest_signal_info['action']}",
                    f"最新RSI: {latest_signal_info['rsi']:.2f}",
                    f"MACD: {latest_signal_info.get('macd', 0):.4f}",
                    f"信号线: {latest_signal_info.get('signal_line', 0):.4f}",
                    f"快均线(MA10): {latest_signal_info.get('ma_fast', 0):.2f}",
                    f"慢均线(MA30): {latest_signal_info.get('ma_slow', 0):.2f}",
                    f"历史买入信号: {buy_signals}次",
                    f"历史卖出信号: {sell_signals}次",
                    f"信号覆盖率: {((buy_signals + sell_signals) / len(processed_df) * 100) if len(processed_df) > 0 else 0:.1f}%"
                ],
                "conclusion": f"基于多维度量化模型（均线+MACD+RSI+止损），{stock_code}当前建议{latest_signal_info['action']}，RSI处于{latest_signal_info['rsi']:.1f}水平。"
            }
        }

        # 6. 生成K线图（使用原始数据）
        chart_file = chart.generate(code=stock_code, days=60)
        
        # 7. 生成DOCX报告，包含策略数据作为额外数据
        report_file = reporter.generate(stock_code, extra_data=extra_data_for_report)
        
        if chart_file and report_file:
            print(f"[SUCCESS] {stock_code}: Chart and enhanced report generated")
            successful += 1
        else:
            print(f"[WARNING] {stock_code}: Chart or report generation failed")
            failed += 1
        
    except Exception as e:
        print(f"[ERROR] Failed to process {stock_code}: {e}")
        import traceback
        traceback.print_exc()
        failed += 1

print("\n" + "=" * 70)
print(f"Strategy-integrated batch processing completed!")
print(f"Successful: {successful}")
print(f"Failed: {failed}")
print(f"Reports saved in: {os.path.abspath(config.DOC_DIR)}")
print("\n每份报告现在包含第14章：多维度量化共振模型评级")