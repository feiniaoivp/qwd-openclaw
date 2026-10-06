#!/usr/bin/env python3
"""A股关注池基本面筛查（fundamental-screening skill 实现）
数据源：同花顺财务摘要(THS) + 东财估值(stock_value_em) + 新浪分红
输出: analysis/reports/fundamental_screening_YYYY-MM-DD.{md,json}
"""
import os
import sys
import json
import time
import datetime as dt

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import warnings
warnings.filterwarnings("ignore")
import socket
socket.setdefaulttimeout(15)

import akshare as ak
from analysis.fib_extension_scan import WATCHLIST

TODAY = dt.date.today().isoformat()
GROWTH_YEARS = ["2023-12-31", "2024-12-31", "2025-12-31"]  # 连续3年年报


def pct(s):
    """'21.26%' -> 21.26; False/None -> None"""
    try:
        if s is None or s is False:
            return None
        return float(str(s).replace("%", "").replace(",", ""))
    except Exception as e:
        print(f"  [warn] pct parse fail: {s!r} {e!r}", file=sys.stderr)
        return None


def money_yi(s):
    """'63.78亿' -> 63.78; '-3.5亿' -> -3.5"""
    try:
        if s is None or s is False:
            return None
        t = str(s).replace(",", "")
        neg = t.startswith("-")
        t = t.lstrip("-")
        v = None
        if t.endswith("亿"):
            v = float(t[:-1])
        elif t.endswith("万"):
            v = float(t[:-1]) / 1e4
        else:
            v = float(t) / 1e8
        return -v if neg else v
    except Exception as e:
        print(f"  [warn] money parse fail: {s!r} {e!r}", file=sys.stderr)
        return None


def screen_one(code, name):
    r = {"code": code, "name": name, "errors": []}
    # --- 同花顺财务摘要 ---
    try:
        df = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
        df = df.set_index("报告期")
        annual = {y: df.loc[y] for y in GROWTH_YEARS if y in df.index}
        rev_g = [pct(annual[y].get("营业总收入同比增长率")) for y in GROWTH_YEARS if y in annual]
        np_g = [pct(annual[y].get("净利润同比增长率")) for y in GROWTH_YEARS if y in annual]
        roe = [pct(annual[y].get("净资产收益率")) for y in GROWTH_YEARS if y in annual]
        debt = [pct(annual[y].get("资产负债率")) for y in GROWTH_YEARS if y in annual]
        net_profit = [money_yi(annual[y].get("净利润")) for y in GROWTH_YEARS if y in annual]
        r.update(rev_g=rev_g, np_g=np_g, roe=roe, debt=debt,
                 net_profit_yi=net_profit, annual_years=[y[:4] for y in annual])
        # 最新报告期亏损检查
        latest = df.iloc[-1]
        r["latest_period"] = str(df.index[-1])
        r["latest_np_yi"] = money_yi(latest.get("净利润"))
        r["latest_np_g"] = pct(latest.get("净利润同比增长率"))
    except Exception as e:
        r["errors"].append(f"THS: {e!r}"[:120])
    time.sleep(0.4)
    # --- 估值 ---
    try:
        v = ak.stock_value_em(symbol=code).tail(1).iloc[0]
        r["pe_ttm"] = round(float(v["PE(TTM)"]), 2)
        r["pb"] = round(float(v["市净率"]), 2)
        r["price"] = float(v["当日收盘价"])
        r["value_date"] = str(v["数据日期"])
    except Exception as e:
        r["errors"].append(f"VALUE: {e!r}"[:120])
    time.sleep(0.4)
    # --- 分红（最近年度每股派息 -> 股息率） ---
    try:
        d = ak.stock_history_dividend_detail(symbol=code, indicator="分红")
        if len(d):
            last = d.iloc[-1]
            # 派息列格式如 '10派5元(含税)'
            txt = str(last.get("派息", ""))
            import re
            m = re.search(r"10派([\d.]+)", txt)
            if m and r.get("price"):
                r["div_per_share"] = float(m.group(1)) / 10
                r["dividend_yield"] = round(r["div_per_share"] / r["price"] * 100, 2)
                r["div_year"] = str(last.get("分红年度", ""))
    except Exception as e:
        r["errors"].append(f"DIV: {e!r}"[:120])
    return r


def classify(r):
    tags = []
    # 避雷
    if "ST" in r["name"].upper():
        tags.append("🔴ST")
    if r.get("latest_np_yi") is not None and r["latest_np_yi"] < 0:
        tags.append("🔴最新报告期亏损")
    if r.get("latest_np_g") is not None and r["latest_np_g"] <= -50:
        tags.append(f"🔴净利同比{r['latest_np_g']:.0f}%")
    # 成长股: 连续3年 营收>=30 净利>=25 ROE>=15
    if len(r.get("rev_g", [])) == 3 and all(x is not None for x in r["rev_g"] + r["np_g"] + r["roe"]):
        if all(x >= 30 for x in r["rev_g"]) and all(x >= 25 for x in r["np_g"]) and all(x >= 15 for x in r["roe"]):
            tags.append("✅成长股")
    # 价值股: PE<20(行业均值口径暂缺,用绝对值代理) + 股息率>=3% + 资产负债率<=50%
    pe = r.get("pe_ttm")
    dy = r.get("dividend_yield")
    debt = r.get("debt", [])
    if pe and 0 < pe < 20 and dy and dy >= 3 and debt and debt[-1] is not None and debt[-1] <= 50:
        tags.append("💰价值股")
    return tags or ["—"]


def fmt3(xs, unit=""):
    if not xs:
        return "N/A"
    return "/".join("—" if x is None else f"{x:.1f}{unit}" for x in xs)


def main():
    results = []
    n = len(WATCHLIST)
    for i, (code, name) in enumerate(WATCHLIST.items(), 1):
        print(f"[{i}/{n}] {code} {name}", flush=True)
        res = screen_one(code, name)
        res["tags"] = classify(res)
        results.append(res)
    out_dir = os.path.join(WORKSPACE, "analysis", "reports")
    os.makedirs(out_dir, exist_ok=True)
    jpath = os.path.join(out_dir, f"fundamental_screening_{TODAY}.json")
    with open(jpath, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    mpath = os.path.join(out_dir, f"fundamental_screening_{TODAY}.md")
    lines = [f"# 关注池基本面筛查 {TODAY}",
             "",
             f"- 样本: {len(results)} 只（WATCHLIST SSOT）",
             "- 成长窗口: 2023/2024/2025 年报（营收YoY/净利YoY/ROE）",
             "- 数据源: THS财务摘要 + 东财估值 + 新浪分红",
             "- ⚠️ 口径说明: 价值股 PE 阈值为绝对代理(<20，行业均值口径暂缺)；大股东减持接口(10jqka)被SSL拦截，本期减持项N/A",
             "",
             "| 代码 | 名称 | PE(TTM) | PB | 股息率% | 营收YoY 23/24/25 | 净利YoY 23/24/25 | ROE 23/24/25 | 资产负债率(最新) | 判定 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        debt_last = f"{r['debt'][-1]:.1f}%" if r.get("debt") and r["debt"][-1] is not None else "N/A"
        dy = f"{r['dividend_yield']:.2f}" if r.get("dividend_yield") else "N/A"
        lines.append("| {code} | {name} | {pe} | {pb} | {dy} | {rev} | {np} | {roe} | {debt} | {tags} |".format(
            code=r["code"], name=r["name"],
            pe=r.get("pe_ttm", "N/A"), pb=r.get("pb", "N/A"), dy=dy,
            rev=fmt3(r.get("rev_g"), "%"), np=fmt3(r.get("np_g"), "%"),
            roe=fmt3(r.get("roe"), "%"), debt=debt_last, tags=" ".join(r["tags"])))
    err = [r for r in results if r["errors"]]
    if err:
        lines += ["", "## 数据缺口"]
        for r in err:
            lines.append(f"- {r['code']} {r['name']}: {'; '.join(r['errors'])}")
    with open(mpath, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"DONE {mpath}")


if __name__ == "__main__":
    main()
