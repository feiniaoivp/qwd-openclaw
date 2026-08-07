import sys, os
sys.path.insert(0, "/Users/duguke/.openclaw/workspace/skills/gh-data")
from ghdata import data_fetcher as fetcher
from ghdata import db_manager as db
from ghdata import reporter
from ghdata import chart
from ghdata import config

# Ensure the output directory is set and exists
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

# Missing stocks from previous run
missing_stocks = [
    "000987", "002156", "002466", "300014", "300124", "300285", 
    "300719", "600036", "600570", "601100", "688981", "600584"
]

print(f"Processing {len(missing_stocks)} missing stocks...")
print("=" * 50)

successful = 0
failed = 0

for i, stock_code in enumerate(missing_stocks, 1):
    print(f"\n[{i}/{len(missing_stocks)}] Processing {stock_code}...")
    try:
        # Data collection
        all_data = collect_all(stock_code)
        if not all_data.get("kline"):
            print(f"[WARNING] {stock_code}: No K-line data, skipping report generation")
            failed += 1
            continue

        # Quantitative analysis
        analysis_result = db.kline_analyze(stock_code)
        
        # Generate K-line chart
        chart_file = chart.generate(code=stock_code, days=60)
        
        # Generate DOCX report
        report_file = reporter.generate(stock_code)
        
        if chart_file and report_file:
            print(f"[SUCCESS] {stock_code}: Chart and report generated")
            successful += 1
        else:
            print(f"[WARNING] {stock_code}: Chart or report generation returned empty path")
            failed += 1
        
    except Exception as e:
        print(f"[ERROR] Failed to process {stock_code}: {e}")
        import traceback
        traceback.print_exc()
        failed += 1

print("\n" + "=" * 50)
print(f"Processing completed!")
print(f"Successful: {successful}")
print(f"Failed: {failed}")
print(f"Reports saved in: {os.path.abspath(config.DOC_DIR)}")