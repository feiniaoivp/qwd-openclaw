#!/usr/bin/env python3
"""
简版收盘扫描 — 跳过 baostock，直接用 akshare 新浪接口
输出标准 JSON 供 agent 解析推送
"""
import sys, json
import pandas as pd
import akshare as ak
from datetime import datetime

WATCHLIST = [
    ("600030", "中信证券"), ("601066", "中信建投"), ("600036", "招商银行"),
    ("601995", "中金公司"), ("000987", "越秀资本"),
    ("600584", "长电科技"), ("688981", "中芯国际"), ("002156", "通富微电"),
    ("002413", "雷科防务"),
    ("300014", "亿纬锂能"), ("002466", "天齐锂业"), ("601865", "福莱特"),
    ("300285", "国瓷材料"), ("603308", "应流股份"), ("300124", "汇川技术"),
    ("601100", "恒立液压"), ("002318", "久立特材"), ("300719", "安达维尔"),
    ("002335", "科华数据"),
    ("600660", "福耀玻璃"), ("600570", "恒生电子"), ("605566", "福莱蒽特"),
    ("000157", "中联重科"), ("601061", "中信金属"),
]

# 1. 批量获取今日实时行情
try:
    spot = ak.stock_zh_a_spot()
    # filter watchlist
    codes = {code for code, _ in WATCHLIST}
    spot_match = spot[spot['代码'].isin(codes)].copy()
except Exception as e:
    print(json.dumps({"error": f"spot失败: {e}"}), file=sys.stderr)
    sys.exit(1)

results = []
for code, name in WATCHLIST:
    row = spot_match[spot_match['代码'] == code]
    if row.empty:
        results.append({"code": code, "name": name, "error": "无行情数据"})
        continue
    r = row.iloc[0]
    try:
        price = float(r['最新价'])
        change_pct = float(r['涨跌幅'])
        volume = float(r['成交量'])
        amount = float(r['成交额'])
        high = float(r['最高'])
        low = float(r['最低'])
        open_p = float(r['今开'])
        pre_close = float(r['昨收'])
        turnover = float(r['换手率'])
        
        # 初步信号
        signal = ""
        if change_pct > 3:
            signal = "买入"
        elif change_pct > 1:
            signal = "关注"
        elif change_pct < -3:
            signal = "卖出"
        elif change_pct < -1:
            signal = "减仓"
        else:
            signal = "持有"
        
        results.append({
            "code": code,
            "name": name,
            "price": price,
            "change_pct": change_pct,
            "high": high,
            "low": low,
            "volume": volume,
            "amount": amount,
            "turnover": turnover,
            "signal": signal
        })
    except Exception as e:
        results.append({"code": code, "name": name, "error": str(e)})

# 2. 提取信号分组
buy = [r for r in results if r.get("signal") == "买入"]
watch = [r for r in results if r.get("signal") == "关注"]
sell = [r for r in results if r.get("signal") == "卖出"]
reduce = [r for r in results if r.get("signal") == "减仓"]
hold = [r for r in results if r.get("signal") == "持有"]

today = datetime.now().strftime("%Y-%m-%d")
output = {
    "date": today,
    "total": len(results),
    "signals": {
        "buy": [r["name"] for r in buy],
        "watch": [r["name"] for r in watch],
        "sell": [r["name"] for r in sell],
        "reduce": [r["name"] for r in reduce],
        "hold": [r["name"] for r in hold]
    },
    "details": results
}

print(json.dumps(output, ensure_ascii=False, indent=2))
