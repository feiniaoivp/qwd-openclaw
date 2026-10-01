#!/usr/bin/env python3
"""临时脚本：拉取 2026-09-28 收盘行情（新浪实时接口，授权源 sina_hq）
输出：上证/深证/创业板 + 35只关注池收盘价、涨跌幅、成交额
注意 sina hq 字段陷阱：最新价是[3]不是[1]（[1]是今开）
"""
import json
import urllib.request

WORKSPACE = "/Users/duguke/.openclaw/workspace"

# 35只关注池（来源：memory/watchlist.md 权威清单）
WATCHLIST = {
    "sh600030": "中信证券", "sh601066": "中信建投", "sh600036": "招商银行",
    "sh601995": "中金公司", "sz000987": "越秀资本",
    "sh600584": "长电科技", "sh688981": "中芯国际", "sz002156": "通富微电",
    "sz002413": "雷科防务",
    "sz300014": "亿纬锂能", "sz002466": "天齐锂业",
    "sz300285": "国瓷材料", "sh603308": "应流股份", "sz300124": "汇川技术",
    "sh601100": "恒立液压", "sz002318": "久立特材", "sz300719": "安达维尔",
    "sz002335": "科华数据", "sz300748": "金力永磁",
    "sh600160": "巨化股份", "sh600346": "恒力石化",
    "sz000708": "中信特钢",
    "sh600660": "福耀玻璃", "sh600570": "恒生电子", "sh605566": "福莱蒽特",
    "sz000157": "中联重科", "sh601061": "中信金属",
    "sh600089": "特变电工", "sh600406": "国电南瑞", "sz000400": "许继电气",
    "sh601179": "中国西电", "sh600312": "平高电气", "sz002028": "思源电气",
    "sz002270": "华明装备", "sz002130": "沃尔核材",
}
INDEXES = {
    "sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
    "sh000688": "科创50", "sz399106": "深证综指",
}

all_codes = list(INDEXES.keys()) + list(WATCHLIST.keys())
url = "https://hq.sinajs.cn/list=" + ",".join(all_codes)
req = urllib.request.Request(url)
req.add_header("Referer", "https://finance.sina.com.cn")

with urllib.request.urlopen(req, timeout=15) as resp:
    raw = resp.read().decode("gbk")

results = {}
sh_amount = None
sz_amount = None
for line in raw.strip().split("\n"):
    if "=" not in line:
        continue
    code = line.split("=")[0].replace("var hq_str_", "").strip()
    fields = line.split('"')[1].split(",")
    if len(fields) < 10 or not fields[0]:
        continue
    name = fields[0]
    # [1]=今开 [2]=昨收 [3]=最新价 [4]=最高 [5]=最低 [8]=成交量(手) [9]=成交额(元)
    prev_close, latest = float(fields[2]), float(fields[3])
    open_, high, low = float(fields[1]), float(fields[4]), float(fields[5])
    volume = float(fields[8])
    amount = float(fields[9])
    pct = round((latest - prev_close) / prev_close * 100, 2) if prev_close else None
    results[code] = {
        "name": name, "close": latest, "open": open_, "high": high, "low": low,
        "prev_close": prev_close, "pct": pct,
        "volume_hand": volume, "amount_yi": round(amount / 1e8, 2),
    }
    if code == "sh000001":
        sh_amount = results[code]["amount_yi"]
    if code == "sz399106":
        sz_amount = results[code]["amount_yi"]

out = {"total_sh_yi": sh_amount, "total_sz_yi": sz_amount,
       "total_both_yi": round((sh_amount or 0) + (sz_amount or 0), 2),
       "indexes": {k: results[k] for k in INDEXES if k in results},
       "stocks": {k: results[k] for k in WATCHLIST if k in results}}

print(json.dumps(out, ensure_ascii=False, indent=1))
