
import sys, os
sys.path.insert(0, "/Users/duguke/.openclaw/workspace/skills/gh-data")
from ghdata import data_fetcher as fetcher
from ghdata import db_manager as db
from ghdata import reporter
from ghdata import chart
from ghdata import config

def collect_all(code: str) -> dict:
    data = {}
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
    return data

stock_codes = [
    "600160", "300827", "600346", "000708", "300748", "002413", "601061", "900925",
    "000157", "603308", "601865", "600660", "002318", "002335", "605566", "601995",
    "600030", "601066", "000987", "601100", "600570", "300124", "300719", "002466",
    "300014", "600036", "300285", "688981", "600584", "002156"
]

for stock_code in stock_codes:
    print(f"--- Processing {stock_code} ---")
    print(f"Collecting 16-dimensional data for {stock_code}...")
    try:
        all_data = collect_all(stock_code)
        print(f"Data collection for {stock_code} completed. First few keys: {list(all_data.keys())[:5]}")

        print(f"Performing quantitative analysis for {stock_code}...")
        analysis_result = db.kline_analyze(stock_code)
        print(f"Quantitative analysis for {stock_code} completed. Result keys: {list(analysis_result.keys())[:5]}")

        print(f"Generating K-line chart for {stock_code}...")
        chart_file = chart.generate_kline_chart(stock_code, days=60)
        print(f"K-line chart saved to: {chart_file}")

        print(f"Generating DOCX report for {stock_code}...")
        report_file = reporter.generate(stock_code)
        print(f"DOCX report saved to: {report_file}")

    except Exception as e:
        print(f"Error processing {stock_code}: {e}")

print("--- All stock processing completed ---")
