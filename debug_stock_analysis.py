import sys, os
sys.path.insert(0, "/Users/duguke/.openclaw/workspace/skills/gh-data")
from ghdata import data_fetcher as fetcher
from ghdata import db_manager as db
from ghdata import reporter
from ghdata import chart
from ghdata import config

# Set the DOC_DIR to a valid directory to avoid FileNotFoundError
config.DOC_DIR = "./doc"
# Ensure the directory exists
os.makedirs(config.DOC_DIR, exist_ok=True)

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
        print(f"Error during data collection for {code}: {e}")
        raise # Re-raise to stop if critical failure
    return data

stock_code = "600160" # 巨化股份

print(f"--- Debugging processing for {stock_code} ---")
print(f"Collecting 16-dimensional data for {stock_code}...")
try:
    all_data = collect_all(stock_code)
    print(f"Data collection for {stock_code} completed. First few keys: {list(all_data.keys())[:5]}")

    print(f"Performing quantitative analysis for {stock_code}...")
    analysis_result = db.kline_analyze(stock_code)
    print(f"Quantitative analysis for {stock_code} completed. Result keys: {list(analysis_result.keys())[:5]}")

    print(f"Generating K-line chart for {stock_code}...")
    chart_file = chart.generate(code=stock_code, days=60)
    print(f"K-line chart saved to: {chart_file}")

    print(f"Generating DOCX report for {stock_code}...")
    report_file = reporter.generate(stock_code)
    print(f"DOCX report saved to: {report_file}")

except Exception as e:
    print(f"Critical error during processing of {stock_code}: {e}")

print(f"--- Debugging processing for {stock_code} completed ---")