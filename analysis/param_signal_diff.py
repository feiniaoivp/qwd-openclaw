#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调参对照信号差异榜 (2026-08-04)
================================
在每周参数调优后，对比"上轮参数"与"本轮新参数"的每股双策略信号，
生成差异榜，供每周 cron 推送给用户 —— 直观看到参数调优改变了哪些信号。

核心对比逻辑:
  - 用同一份最新行情数据, 分别用:
     旧参数文件 adaptive_params_prev.json  (上轮: 每股最优策略+参数)
     新参数文件 adaptive_params.json        (本轮: 每股最优策略+参数)
  - 对每股, 取 前2 策略各跑信号, 对比动作(action)与策略集(前两策略是否更换)
  - 差异归类:
     1. 新卖出 (旧持有 -> 新卖出): 调参后变悲观, 重要
     2. 新买入 (旧持有 -> 新买入): 调参后变乐观, 重要
     3. 反转    (旧买 -> 新卖 或 旧卖 -> 新买): 方向反转, 最重要
     4. 策略切换但信号一致 (前2策略集合变了, 但动作同为持有)
     5. 无差异
  输出: PARAM_SIGNAL_DIFF JSON + 人类可读差异榜

注意: 对比时两套参数都可选; 若某套文件缺失则跳过该方向(只报告单边)。
      旧文件缺省时, 回退用"validation 策略级前二 + 默认参数"作为旧基线。
"""

import os
import sys
import json
from datetime import datetime

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE)                       # 让 `analysis.xxx` 顶层包可导入
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

import adaptive_dual as ad

NEW_FILE = os.path.join(WORKSPACE, "data", "adaptive_params.json")
PREV_FILE = os.path.join(WORKSPACE, "data", "adaptive_params_prev.json")


def action_type(action):
    """归一化动作类型: buy/sell/hold"""
    if "买入" in action:
        return "buy"
    if "卖出" in action:
        return "sell"
    return "hold"


def load_prev_params():
    """读上轮参数文件; 缺失则回退 validation 策略级前二+默认参数(旧基线)"""
    if os.path.exists(PREV_FILE):
        try:
            with open(PREV_FILE) as f:
                prev = json.load(f).get("stocks", {})
            if prev:
                return prev, True
        except Exception:
            pass

    # 回退: validation 策略级前二 + 默认参数
    with open(ad.VALIDATION_FILE) as f:
        val = json.load(f)
    fallback = {}
    for sym, info in val.items():
        details = info.get("details", [])
        ranked = sorted(details, key=lambda x: x.get("score", 0), reverse=True)
        top2 = []
        for d in ranked[:2]:
            key = ad.VALIDATION_STRAT_MAP.get(d.get("strategy"))
            if key:
                top2.append({"strategy": key, "params": None})
        if top2:
            fallback[sym] = {"name": info.get("name", sym), "details": {}}
            for i, t in enumerate(top2):
                # 存成 details 形式, 便于统一处理
                fallback[sym]["details"][ad.STRATEGY_LABELS[t["strategy"]].replace("📊 ","").replace("🎯 ","").replace("📈 ","").replace("💹 ","").replace("📉 ","")]
    # 更简单的方式: 直接返回结构为 {sym: [(strategy, params), ...]} 的简化
    return {sym: [{"strategy": t["strategy"], "params": None} for t in top2]}, False


def params_to_dual_map(params_json):
    """把 adaptive_params.json 的 stocks 结构转成 {sym: [(strategy, params, score)]}"""
    stocks = params_json.get("stocks", {})
    dual = {}
    for sym, info in stocks.items():
        details = info.get("details", {}) if isinstance(info, dict) else {}
        scored = []
        for strat_key, dinfo in details.items():
            if not isinstance(dinfo, dict):
                continue
            bp = dinfo.get("best_params")
            bs = dinfo.get("best_score")
            key = ad.VALIDATION_STRAT_MAP.get(strat_key) or strat_key
            if key in ad.SIGNAL_FUNCS and bp is not None and bs is not None:
                scored.append({"strategy": key, "params": bp, "score": bs})
        if len(scored) >= 1:
            scored.sort(key=lambda x: x["score"], reverse=True)
            dual[sym] = scored[:2]
    return dual


def run_signals(symbol, top2, df):
    """对每股前2策略跑信号, 返回 [(strategy, action_type, action, params)]"""
    out = []
    for t in top2:
        params = t.get("params")
        try:
            sig = ad.SIGNAL_FUNCS[t["strategy"]](df, params)
            out.append({
                "strategy": t["strategy"],
                "params": params,
                "action": sig["action"],
                "type": action_type(sig["action"]),
            })
        except Exception as e:
            out.append({"strategy": t["strategy"], "params": params,
                        "action": "ERR", "type": "err", "error": str(e)})
    return out


def main():
    # 读新参数(本轮)
    with open(NEW_FILE) as f:
        new_raw = json.load(f)
    new_dual = params_to_dual_map(new_raw)

    # 读旧参数(上轮) — 可能是 adaptive_params_prev.json, 或回退 baseline
    if os.path.exists(PREV_FILE):
        with open(PREV_FILE) as f:
            prev_raw = json.load(f)
        prev_dual = params_to_dual_map(prev_raw)
        prev_source = "adaptive_params_prev.json"
    else:
        # 回退: validation 策略级前二 + 默认参数
        with open(ad.VALIDATION_FILE) as f:
            val = json.load(f)
        prev_dual = {}
        for sym, info in val.items():
            details = info.get("details", [])
            ranked = sorted(details, key=lambda x: x.get("score", 0), reverse=True)
            top2 = []
            for d in ranked[:2]:
                key = ad.VALIDATION_STRAT_MAP.get(d.get("strategy"))
                if key:
                    top2.append({"strategy": key, "params": None})
            if top2:
                prev_dual[sym] = top2
        prev_source = "validation+默认参数(回退基线)"

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"📊 调参对照信号差异榜 | {today}")
    print(f"   旧参数源: {prev_source} | 新参数源: adaptive_params.json\n")

    new_sell = []   # 新卖出
    new_buy = []    # 新买入
    reversal = []   # 方向反转
    switch_hold = []  # 策略切换但持有
    no_diff = []    # 无差异
    errors = []

    for sym, name in ad.STOCKS:
        if sym not in new_dual and sym not in prev_dual:
            continue
        old_top2 = prev_dual.get(sym)
        new_top2 = new_dual.get(sym)
        if not old_top2 or not new_top2:
            errors.append(f"{name}({sym}): 一侧无策略")
            continue

        df = ad.fetch_data(sym, realtime_fallback=False)
        if df is None or len(df) < 60:
            errors.append(f"{name}({sym}): 数据不足")
            continue

        old_res = run_signals(sym, old_top2, df)
        new_res = run_signals(sym, new_top2, df)
        if any(r["type"] == "err" for r in old_res + new_res):
            errors.append(f"{name}({sym}): 策略报错")
            continue

        old_types = [r["type"] for r in old_res]
        new_types = [r["type"] for r in new_res]
        old_strats = [r["strategy"] for r in old_res]
        new_strats = [r["strategy"] for r in new_res]

        # 组合动作: 有买入 > 有卖出 > 全持有 (宽松判定)
        def combo(types):
            if "buy" in types:
                return "buy"
            if "sell" in types:
                return "sell"
            return "hold"

        oc, nc = combo(old_types), combo(new_types)

        entry = {
            "symbol": sym, "name": name,
            "old": [{"strategy": r["strategy"], "params": r["params"], "action": r["action"]} for r in old_res],
            "new": [{"strategy": r["strategy"], "params": r["params"], "action": r["action"]} for r in new_res],
        }

        if oc != nc and oc == "sell" and nc == "buy":
            reversal.append({"direction": "卖→买", **entry})
        elif oc != nc and oc == "buy" and nc == "sell":
            reversal.append({"direction": "买→卖", **entry})
        elif nc == "sell" and oc != "sell":
            new_sell.append(entry)
        elif nc == "buy" and oc != "buy":
            new_buy.append(entry)
        elif set(old_strats) != set(new_strats):
            switch_hold.append(entry)
        else:
            no_diff.append(entry)

    # ── 输出差异榜 ──
    print("=" * 60)
    print("🔁 方向反转 (最重要):")
    for e in reversal:
        print(f"  {e['name']}({e['symbol']}) {e['direction']}: "
              f"旧{[(x['strategy'],x['action']) for x in e['old']]} -> "
              f"新{[(x['strategy'],x['action']) for x in e['new']]}")

    print("\n🔴 新出现卖出信号 (旧持有/买入 -> 新卖出):")
    for e in new_sell:
        print(f"  {e['name']}({e['symbol']}): "
              f"旧{[(x['strategy'],x['action']) for x in e['old']]} -> "
              f"新{[(x['strategy'],x['action']) for x in e['new']]}")

    print("\n🟢 新出现买入信号 (旧持有/卖出 -> 新买入):")
    for e in new_buy:
        print(f"  {e['name']}({e['symbol']}): "
              f"旧{[(x['strategy'],x['action']) for x in e['old']]} -> "
              f"新{[(x['strategy'],x['action']) for x in e['new']]}")

    print("\n🔄 策略切换但信号一致 (均持有):")
    for e in switch_hold:
        print(f"  {e['name']}({e['symbol']}): "
              f"旧{[x['strategy'] for x in e['old']]} -> "
              f"新{[x['strategy'] for x in e['new']]} (均持有)")

    if errors:
        print(f"\n⚠️ 跳过/错误 {len(errors)} 只: {'; '.join(errors[:8])}")

    # ── JSON 输出 ──
    output = {
        "date": today,
        "prev_source": prev_source,
        "summary": {
            "reversal": len(reversal),
            "new_sell": len(new_sell),
            "new_buy": len(new_buy),
            "switch_hold": len(switch_hold),
            "no_diff": len(no_diff),
            "errors": len(errors),
        },
        "reversal": reversal,
        "new_sell": new_sell,
        "new_buy": new_buy,
        "switch_hold": switch_hold,
    }
    print("\n=====PARAM_SIGNAL_DIFF=====")
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    print("=====PARAM_SIGNAL_DIFF_END=====")

    # 摘要一行
    s = output["summary"]
    print(f"\n📊 差异榜摘要: 反转{s['reversal']} / 新卖{s['new_sell']} / 新买{s['new_buy']} / 策略切换{s['switch_hold']} / 无差异{s['no_diff']}")


if __name__ == "__main__":
    main()
