#!/usr/bin/env python3
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json

# ── 10-09 双策略信号（来自 dual.md，硬编码本日快照） ──
DISAGREE = {
    "300014": ("EMA+OBV 卖", "布林带+ATR 持", "亿纬锂能", 50.48),
    "002156": ("EMA+OBV 卖", "EMA12/26 持", "通富微电", 55.93),
    "002466": ("EMA+OBV 卖", "纯MACD 持", "天齐锂业", 40.69),
    "603308": ("EMA+OBV 卖", "布林带+ATR 持", "应流股份", 36.07),
    "300285": ("纯MACD 持", "EMA+OBV 卖", "国瓷材料", 60.20),
    "601865": ("EMA+OBV 卖", "KDJ+RSI 持", "福莱特", 9.12),
    "601061": ("EMA+OBV 卖", "纯MACD 持", "中信金属", 10.87),
    "002335": ("KDJ+RSI 持", "纯MACD 卖", "科华数据", 28.95),
    "601100": ("EMA12/26 持", "KDJ+RSI 卖", "恒立液压", 87.39),
    "600160": ("EMA+OBV 卖", "纯MACD 持", "巨化股份", 32.28),
    "601179": ("EMA+OBV 卖", "纯MACD 买", "中国西电", 11.79),
    "002270": ("EMA+OBV 卖", "布林带+ATR 持", "华明装备", 17.67),
}
DOUBLE_SELL = {"002130": ("KDJ+RSI 卖", "EMA+OBV 卖", "沃尔核材", 15.57)}

# DSR 显著名单（10-11 周报，DSR>=0.95）
DSR_SIG = {
    "002156": ("EMA+OBV", 1.389, 1.0),
    "300285": ("纯MACD", 3.527, 1.0),
    "300014": ("牛市趋势跟踪", 2.214, 0.9998),
    "600584": ("纯MACD", 1.979, 0.9949),
    "002270": ("EMA+OBV", 1.449, 0.9722),
    "601179": ("布林带+ATR", 1.443, 0.9574),
}

# 护城河 (从 dual.md) >=2.0 核心
MOAT_CORE = {"002318": 1.7, "300014": 2.0, "300124": 1.7, "600584": 1.6,
             "002466": 1.5, "600570": 1.6, "603308": 1.7, "300285": 1.8,
             "002413": 1.4, "688981": 1.4}

# 组合持仓
HOLDINGS = {"002413": {"name": "雷科防务", "entry": 8.74, "shares": 500,
                        "stop": 7.59, "date": "2026-10-09"}}

print("=" * 60)
print("周一盘前交叉研判 2026-10-12")
print("=" * 60)

# ── 分歧票交叉打分 ──
rows = []
for code, (s1, s2, name, px) in DISAGREE.items():
    dsr = DSR_SIG.get(code)
    moat = MOAT_CORE.get(code, 0)
    # 卖方信号策略是否 DSR 显著
    sell_sig = s1 if "卖" in s1 else (s2 if "卖" in s2 else "")
    buy_sig = s1 if "买" in s1 else (s2 if "买" in s2 else "")
    dsr_hit_sell = any(k in sell_sig for k in [dsr[0][:4]] ) if dsr and sell_sig else False
    dsr_hit_buy = any(k in buy_sig for k in [dsr[0][:4]]) if dsr and buy_sig else False
    # 权重
    w = 0
    if dsr: w += 2
    if moat >= 2.0: w += 1
    if buy_sig: w += 2  # 出现买入信号更值得关注
    if sell_sig and dsr_hit_sell: w += 1  # 卖出信号来自显著策略
    rows.append((w, code, name, px, s1, s2,
                 f"DSR={dsr[2]}" if dsr else "", f"护城河{moat}" if moat else ""))

rows.sort(reverse=True)
print("\n## 分歧12只 优先级排序 (权重: DSR显著+2 / 核心护城河+1 / 含买信号+2 / 卖来自显著策略+1)")
for w, code, name, px, s1, s2, dsr, moat in rows:
    tag = "🔴" if "卖" in s1 and "卖" in s2 else ("🟢" if "买" in s1 or "买" in s2 else "⚡")
    print(f"  {tag} w={w} {name}({code}) ¥{px} | {s1} vs {s2} {dsr} {moat}")

print("\n## 双卖 (1只)")
for code, (s1, s2, name, px) in DOUBLE_SELL.items():
    print(f"  🔴 {name}({code}) ¥{px} | {s1} vs {s2}")

print("\n## 持仓风险核验 (雷科防务)")
h = HOLDINGS["002413"]
print(f"  {h['name']} 500股 成本¥{h['entry']} 止损¥{h['stop']} 入场{h['date']}")
print(f"  10-09 dual报告现价 ¥8.53 (若属实浮亏 {(8.53-h['entry'])/h['entry']*100:+.2f}%)")
print(f"  止损距离 {(h['stop']-8.53)/8.53*100:+.1f}% (以8.53计)")
print(f"  ⚠️ portfolio_sim 显示现价¥8.74/+1.38% 与 dual ¥8.53 矛盾 → 需真实行情核验")
