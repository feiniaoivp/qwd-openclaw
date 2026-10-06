#!/usr/bin/env python3
"""修补: 重特变电工/许继电气/沃尔核材 THS 失败 + 全池股息率重算(派息直接为10派X元数值,取最新实施)"""
import os, sys, json, time, re, datetime as dt
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import warnings; warnings.filterwarnings("ignore")
import socket; socket.setdefaulttimeout(20)
import akshare as ak
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))
from fundamental_screening import pct, money_yi, classify, GROWTH_YEARS, fmt3

TODAY = dt.date.today().isoformat()
jpath = os.path.join(WORKSPACE, "analysis", "reports", f"fundamental_screening_{TODAY}.json")
results = json.load(open(jpath))

for r in results:
    code = r["code"]
    # 重试缺失的 THS
    if not r.get("rev_g"):
        print(f"retry THS {code} {r['name']}", flush=True)
        for attempt in range(3):
            try:
                df = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期").set_index("报告期")
                annual = {y: df.loc[y] for y in GROWTH_YEARS if y in df.index}
                r["rev_g"] = [pct(annual[y].get("营业总收入同比增长率")) for y in GROWTH_YEARS if y in annual]
                r["np_g"] = [pct(annual[y].get("净利润同比增长率")) for y in GROWTH_YEARS if y in annual]
                r["roe"] = [pct(annual[y].get("净资产收益率")) for y in GROWTH_YEARS if y in annual]
                r["debt"] = [pct(annual[y].get("资产负债率")) for y in GROWTH_YEARS if y in annual]
                r["net_profit_yi"] = [money_yi(annual[y].get("净利润")) for y in GROWTH_YEARS if y in annual]
                latest = df.iloc[-1]
                r["latest_period"] = str(df.index[-1])
                r["latest_np_yi"] = money_yi(latest.get("净利润"))
                r["latest_np_g"] = pct(latest.get("净利润同比增长率"))
                r["errors"] = [e for e in r["errors"] if not e.startswith("THS")]
                break
            except Exception as e:
                r["errors"] = [e2 for e2 in r["errors"] if not e2.startswith("THS")]
                if attempt == 2:
                    r["errors"].append(f"THS-retry: {e!r}"[:120])
                time.sleep(2)
    # 重算股息率：派息=每10股派X元(数值), 数据最新在第一行
    if r.get("dividend_yield") is None and r.get("price"):
        try:
            d = ak.stock_history_dividend_detail(symbol=code, indicator="分红")
            d = d[d["进度"] == "实施"] if "进度" in d.columns else d
            if len(d):
                row = d.iloc[0]  # 最新实施
                dv10 = float(row.get("派息") or 0)
                if dv10 > 0:
                    r["div_per_share"] = dv10 / 10
                    r["dividend_yield"] = round(dv10 / 10 / r["price"] * 100, 2)
                    r["div_year"] = str(row.get("公告日期", ""))[:10]
            r["errors"] = [e for e in r["errors"] if not e.startswith("DIV")]
        except Exception as e:
            if not any(e2.startswith("DIV") for e2 in r["errors"]):
                r["errors"].append(f"DIV-retry: {e!r}"[:100])
        time.sleep(0.4)
    r["tags"] = classify(r)

json.dump(results, open(jpath, "w"), ensure_ascii=False, indent=1)

lines = [f"# 关注池基本面筛查 {TODAY}（修订版）", "",
         f"- 样本: {len(results)} 只（WATCHLIST SSOT）",
         "- 成长窗口: 2023/2024/2025 年报（营收YoY/净利YoY/ROE，205/206 三季报未及，% 为同比）",
         "- 数据源: 同花顺财务摘要(THS) + 东财估值(stock_value_em) + 新浪分红历史",
         "- ⚠️ 口径: 价值股 PE 阈值为绝对代理(<20，行业均值口径暂缺)；大股东减持接口(10jqka)被SSL拦截，减持项N/A",
         "- 股息率 = 最近一次实施方案「每10股派息/10」/最新收盘价",
         "",
         "| 代码 | 名称 | PE(TTM) | 股息率% | 营收YoY 23/24/25 | 净利YoY 23/24/25 | ROE 23/24/25 | 负债率25A | 判定 |",
         "|---|---|---|---|---|---|---|---|---|"]
for r in results:
    debt_last = f"{r['debt'][-1]:.1f}%" if r.get("debt") and r["debt"][-1] is not None else "N/A"
    dy = f"{r['dividend_yield']:.2f}" if r.get("dividend_yield") else "N/A"
    lines.append("| {code} | {name} | {pe} | {dy} | {rev} | {np} | {roe} | {debt} | {tags} |".format(
        code=r["code"], name=r["name"], pe=r.get("pe_ttm", "N/A"), dy=dy,
        rev=fmt3(r.get("rev_g"), "%"), np=fmt3(r.get("np_g"), "%"),
        roe=fmt3(r.get("roe"), "%"), debt=debt_last, tags=" ".join(r["tags"])))
err = [r for r in results if r["errors"]]
if err:
    lines += ["", "## 数据缺口"]
    for r in err:
        lines.append(f"- {r['code']} {r['name']}: {'; '.join(r['errors'])}")
open(os.path.join(WORKSPACE, "analysis", "reports", f"fundamental_screening_{TODAY}.md"), "w").write("\n".join(lines) + "\n")
print("FIXED OK")
