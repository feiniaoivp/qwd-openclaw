#!/usr/bin/env python3
import os, sys, json, time
SKILL = os.path.expanduser("~/.openclaw/workspace/skills/gh-data")
if SKILL not in sys.path:
    sys.path.insert(0, SKILL)
from concurrent.futures import ThreadPoolExecutor, as_completed
from ghdata import data_fetcher as fetcher
from ghdata import db_manager as db

CODES = {
    "600346": "恒力石化",
    "002413": "雷科防务",
    "601061": "中信金属",
    "002130": "沃尔核材",
    "000708": "中信特钢",
}
OUT = os.path.expanduser("~/.openclaw/workspace/data/ghdata/2026-10-09")
os.makedirs(OUT, exist_ok=True)

def collect(code):
    tasks = [
        ("realtime",  fetcher.fetch_realtime,         code),
        ("kline",     fetcher.fetch_kline,            code, 200),
        ("tick",      fetcher.fetch_today_tick,       code),
        ("moneyFlow", fetcher.fetch_money_flow,       code, 10),
        ("margin",    fetcher.fetch_margin_trading,   code, 10),
        ("holdings",  fetcher.fetch_main_holdings,    code),
        ("shareholder", fetcher.fetch_shareholder_trade, code),
        ("executive", fetcher.fetch_executive_change, code),
        ("financial", fetcher.fetch_financial,        code),
        ("balance",   fetcher.fetch_balance_sheet,    code),
        ("income",    fetcher.fetch_income_statement, code),
        ("cashflow",  fetcher.fetch_cashflow,         code),
        ("dividend",  fetcher.fetch_dividend,         code),
        ("research",  fetcher.fetch_research_report,  code),
        ("survey",    fetcher.fetch_institutional_survey, code),
        ("unlock",    fetcher.fetch_unlock_data,      code),
        ("industry",  fetcher.fetch_industry_info,    code),
    ]
    data = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        fut_map = {pool.submit(fn, *args): key for key, fn, *args in tasks}
        for fut in as_completed(fut_map):
            k = fut_map[fut]
            try:
                data[k] = fut.result()
            except Exception as e:
                print(f"  [warn] collect {k} failed: {e}", file=sys.stderr)
                data[k] = None
    return data

def to_jsonable(x):
    try:
        json.dumps(x)
        return x
    except Exception:
        if hasattr(x, "isoformat"):
            return x.isoformat()
        return str(x)

def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return to_jsonable(o)

for code, name in CODES.items():
    print(f"=== {code} {name} ===", flush=True)
    t0 = time.time()
    data = collect(code)
    result = {"code": code, "name": name, "local": clean(data)}
    # WebAPI quantitative analysis (paid status check per call)
    try:
        ka = db.kline_analyze(code)
        result["kline_analyze"] = clean(ka)
        print(f"  kline_analyze ok, preview={ (ka or {}).get('kline_analyze',{}).get('preview') if isinstance(ka, dict) else '?'}", flush=True)
    except Exception as e:
        print(f"  [warn] kline_analyze failed: {e}", file=sys.stderr)
        result["kline_analyze"] = None
    # Enhanced data via WebAPI
    for key, fn, args in [
        ("etf_flow", db.get_etf_flow, (code, 15)),
        ("research_summary", db.get_research_summary, (code, 90)),
        ("eps_consistency", db.get_eps_consistency, (code, 180)),
        ("broker_ranking", db.get_broker_ranking, (code, 90)),
        ("rating_today", db.get_rating_wind_today, (code,)),
    ]:
        try:
            result[key] = clean(fn(*args))
        except Exception as e:
            print(f"  [warn] {key} failed: {e}", file=sys.stderr)
            result[key] = None
    path = os.path.join(OUT, f"{code}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"  saved {path} ({time.time()-t0:.1f}s)", flush=True)
print("ALL DONE")
