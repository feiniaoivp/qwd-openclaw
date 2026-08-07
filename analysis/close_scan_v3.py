#!/usr/bin/env python3
"""
盘后扫描 v3 — 直接用 akshare 新浪 spot 接口（收盘后也稳定）
输出完整 JSON 供 agent 解析推送
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

# 获取全市场实时行情
spot = ak.stock_zh_a_spot()
codes_set = {c for c, _ in WATCHLIST}

results = []
for code, name in WATCHLIST:
    match = spot[spot['代码'].str.endswith(code)]
    if match.empty:
        results.append({"code": code, "name": name, "error": "无行情"})
        continue
    r = match.iloc[0]
    try:
        results.append({
            "code": code,
            "name": name,
            "price": float(r['最新价']),
            "change_pct": float(r['涨跌幅']),
            "high": float(r['最高']),
            "low": float(r['最低']),
            "open": float(r['今开']),
            "pre_close": float(r['昨收']),
            "volume": float(r['成交量']),
            "amount": float(r['成交额']),
        })
    except Exception as e:
        results.append({"code": code, "name": name, "error": str(e)})

# 信号分类
buy = [r for r in results if r.get('change_pct',0) > 3]
watch = [r for r in results if 1 < r.get('change_pct',0) <= 3]
sell = [r for r in results if r.get('change_pct',0) < -5]
reduce = [r for r in results if -5 <= r.get('change_pct',0) < -1.5]
hold = [r for r in results if 'change_pct' in r and -1.5 <= r.get('change_pct',0) <= 1]

# 总概览
prev_close_idx = {r['code']: r['pre_close'] for r in results if 'pre_close' in r}
up = sum(1 for r in results if r.get('change_pct',0) > 0)
down = sum(1 for r in results if r.get('change_pct',0) < 0)
flat = sum(1 for r in results if r.get('change_pct',0) == 0)

# 主流资金信号简化估算
total_amount = sum(r.get('amount',0) for r in results)

today = datetime.now().strftime("%Y-%m-%d")
output = {
    "date": today,
    "total": len(results),
    "up": up, "down": down, "flat": flat,
    "total_amount_billion": round(total_amount / 1e8, 2),
    "signals": {
        "buy": [{"code": r["code"], "name": r["name"], "change_pct": r["change_pct"], "price": r["price"]} for r in buy],
        "watch": [{"code": r["code"], "name": r["name"], "change_pct": r["change_pct"]} for r in watch],
        "sell": [{"code": r["code"], "name": r["name"], "change_pct": r["change_pct"]} for r in sell],
        "reduce": [{"code": r["code"], "name": r["name"], "change_pct": r["change_pct"]} for r in reduce],
        "hold": [{"code": r["code"], "name": r["name"], "change_pct": r["change_pct"]} for r in hold],
    },
    "details": results
}

print(json.dumps(output, ensure_ascii=False, indent=2))
