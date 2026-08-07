
import sys, os
import json
import traceback

# 动态获取gh-data技能的根目录
# 假设exec命令是在workspace根目录执行
skill_root_dir = os.path.join(os.getcwd(), 'skills', 'gh-data')
sys.path.insert(0, skill_root_dir)

from ghdata import data_fetcher as fetcher
from ghdata import config

def collect_all(code: str) -> dict:
    data = {}
    fetch_functions = {
        "realtime": (fetcher.fetch_realtime, (code,)),
        "kline": (fetcher.fetch_kline, (code, 365)), # 近一年K线数据
        "tick": (fetcher.fetch_today_tick, (code,)),
        "moneyFlow": (fetcher.fetch_money_flow, (code, 10)),
        "margin": (fetcher.fetch_margin_trading, (code, 10)),
        "holdings": (fetcher.fetch_main_holdings, (code,)),
        "shareholder": (fetcher.fetch_shareholder_trade, (code,)),
        "executive": (fetcher.fetch_executive_change, (code,)),
        "financial": (fetcher.fetch_financial, (code,)),
        "balance": (fetcher.fetch_balance_sheet, (code,)),
        "income": (fetcher.fetch_income_statement, (code,)),
        "cashflow": (fetcher.fetch_cashflow, (code,)),
        "dividend": (fetcher.fetch_dividend, (code,)),
        "research": (fetcher.fetch_research_report, (code,)),
        "survey": (fetcher.fetch_institutional_survey, (code,)),
        "unlock": (fetcher.fetch_unlock_data, (code,)),
        "industry": (fetcher.fetch_industry_info, (code,)),
    }

    for key, (func, args) in fetch_functions.items():
        try:
            data[key] = func(*args)
        except Exception as e:
            data[key] = {"error": f"Error fetching {key}: {str(e)}", "traceback": traceback.format_exc()}
    return data

stock_code = "600346"
all_data = collect_all(stock_code)
print(json.dumps(all_data, ensure_ascii=False, indent=2))
