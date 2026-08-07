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

stock_codes = [
    "600160", "300827", "600346", "000708", "300748", "002413", "601061", "900925",
    "000157", "603308", "601865", "600660", "002318", "002335", "605566", "601995",
    "600030", "601066", "000987", "601100", "600570", "300124", "300719", "002466",
    "300014", "600036", "300285", "688981", "600584", "002156"
]

print("Starting batch processing of 30 stocks...")
print("=" * 50)

successful = 0
failed = 0

for i, stock_code in enumerate(stock_codes, 1):
    print(f"\n[{i}/30] Processing {stock_code}...")
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
print(f"Batch processing completed!")
print(f"Successful: {successful}")
print(f"Failed: {failed}")
print(f"Reports saved in: {os.path.abspath(config.DOC_DIR)}")