#!/usr/bin/env python3
"""构建市场宽度(breadth) — 用新浪 hq.sinajs.cn 批量行情(可靠快速), 替代挂起的 akshare spot
输出: stdout JSON {breadth, index}
用法: python3 analysis/build_breadth_hq.py
"""
import os, sys, json, time, urllib.request
import pandas as pd

def progress(msg, *a):
    print(msg % a if a else msg, file=sys.stderr)

def get_code_list():
    import akshare as ak
    return ak.stock_info_a_code_name()  # 缓存于内存

def fetch_batch(symbols):
    """一次性拉取一批新浪行情, 返回 dict: code->dict"""
    out = {}
    for i in range(0, len(symbols), 80):
        batch = symbols[i:i+80]
        url = "http://hq.sinajs.cn/list=" + ",".join(batch)
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn",
                                                   "User-Agent": "Mozilla/5.0"})
        try:
            raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk")
        except Exception as e:
            progress("  批次失败: %s", e)
            continue
        for line in raw.splitlines():
            if '="' not in line: continue
            key = line.split("hq_str_")[1].split("=")[0]  # e.g. sh600000
            val = line.split('"')[1].split(",")
            if len(val) < 10: continue
            code = key[2:]
            def f(x):
                try: return float(x)
                except: return 0.0
            name = val[0]
            if not name: continue
            out[key] = {
                "code": code, "name": name,
                "open": f(val[1]), "pre_close": f(val[2]), "last": f(val[3]),
                "high": f(val[4]), "low": f(val[5]),
                "volume": f(val[8]), "amount": f(val[9]),
                "date": val[30] if len(val) > 30 else "",
            }
        time.sleep(0.05)
    return out

def compute_breadth(rows):
    df = pd.DataFrame(rows)
    # 排除无效
    df = df[df["last"] > 0]
    df["chg"] = (df["last"] / df["pre_close"] - 1) * 100
    df = df[(df["chg"] > -50) & (df["chg"] < 50)]  # 过滤停牌/异常

    def is_limit_up(r):
        if r["code"].startswith(("30", "68")): return r["chg"] >= 19.8
        return r["chg"] >= 9.9
    def is_limit_down(r):
        if r["code"].startswith(("30", "68")): return r["chg"] <= -19.8
        return r["chg"] <= -9.9
    limit_up = int(df.apply(is_limit_up, axis=1).sum())
    limit_down = int(df.apply(is_limit_down, axis=1).sum())
    big_up = int((df["chg"] >= 19.8).sum())

    up = int((df["chg"] > 0).sum()); down = int((df["chg"] < 0).sum())
    flat = int((df["chg"] == 0).sum()); total = len(df)
    avg_chg = round(float(df["chg"].mean()), 2)
    med_chg = round(float(df["chg"].median()), 2)
    total_amt = round(float(df["amount"].sum()) / 1e8, 0)

    up_ratio = up / total if total else 0
    if up_ratio > 0.70: ws = 10
    elif up_ratio > 0.50: ws = 7
    elif up_ratio > 0.30: ws = 4
    else: ws = 1
    if limit_down == 0: ns = 10
    elif limit_down <= 3: ns = 7
    elif limit_down <= 10: ns = 4
    else: ns = 1
    return {
        "up": up, "down": down, "flat": flat, "total": total,
        "limit_up": limit_up, "limit_down": limit_down, "big_up": big_up,
        "avg_chg": avg_chg, "med_chg": med_chg,
        "total_amount_yi": total_amt, "up_ratio": round(up_ratio, 4),
        "width_score": ws, "neg_score": ns, "data_time": None,
    }

def fetch_index():
    import urllib.request
    url = "http://hq.sinajs.cn/list=s_sh000001,s_sz399006,s_sh000688"
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn",
                                               "User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk")
    idx = {}
    nm = {"上证指数": "上证指数", "创业板指": "创业板指", "科创50": "科创50"}
    for line in raw.splitlines():
        val = line.split('"')[1].split(',')
        if len(val) < 6: continue
        n = val[0]
        if n in nm:
            idx[nm[n]] = {"price": float(val[1]), "change_pct": float(val[3]),
                          "amount_yi": round(float(val[5])/10000, 0)}
    return idx

def main():
    t0 = time.time()
    progress("拉取A股代码表...")
    codes = get_code_list()
    progress("代码 %d 条, %.0fs", len(codes), time.time()-t0)
    # 过滤: 仅沪深(排除北交所 8xxxxx / 9xxxxx 开头)
    # 新浪 hq 前缀: 6xxxxx=sh, 其余=sz; 排除以 4(北所)/8(北所)/9(北所)/0开头且非深? 保守: 保留 sh/sz 常规
    def pref(c): return ("sh" if c.startswith("6") else "sz") + c
    symbols = [pref(c) for c in codes["code"] if str(c)[0] in "036"]
    progress("待拉取 %d 只 (沪深A股)", len(symbols))
    rows = list(fetch_batch(symbols).values())
    progress("成功 %d 只, %.0fs", len(rows), time.time()-t0)
    breadth = compute_breadth(rows)
    index = fetch_index()
    print(json.dumps({"breadth": breadth, "index": index}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
