#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
斐波那契扩展位·前瞻跟踪与事后检验 (fib_track.py)
==================================================
严格遵守"预先声明→前瞻跟踪→事后检验"，避免射箭再画靶。

核心原则：
- 基线择日：当日无活跃基线(active_baseline_date 为空)时，锁定当日各股扩展止盈位
  (1.0/1.272/1.618/2.618)为"预先声明的预测目标"。
- 锁定后不再重算(价格随走势，但预测目标固定不回改) —— 这是避免事后画靶的关键。
- 每日验证：只看"锁定的止盈位"是否被触及，以及触及后是否兑现(回落)或突破(续涨)。
  命中率 = 触及后兑现的股数 / 触及过的股数。命中率低 → 说明该工具无效，应弃用。

用法：
  python3 analysis/fib_track.py               # 无基线→锁当日；有基线→对照验证
状态：data/fib_tracking_state.json
数据：新浪日K(快、含当日)，限流时自动重试，仍失败则跳过并保留现状。
"""
import json
import os
import sys
import time
from datetime import date, datetime

WS = "/Users/duguke/.openclaw/workspace"
sys.path.insert(0, WS)
from analysis.fib_extension_scan import (  # noqa: E402
    fetch_sina_kline, find_latest_uptrend_swing,
    compute_fib_levels, WATCHLIST,
)

STATE = os.path.join(WS, "data/fib_tracking_state.json")

# 验证参数
TOUCH_CHECK_DAYS = 5     # 触及止盈位后 5 个交易日观察是否兑现
CONFIRM_FALL_PCT = 0.03  # 触及后回落 3% 视为"兑现"(预测成立)
BREAK_PCT = 0.02         # 触及后继续上涨并站稳 2% 视为"突破"(预测失效)


def load_state():
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "active_baseline_date": None,
        "baseline": {},      # {symbol: {levels:{1.0..2.618}, swing_high, swing_low, pullback_low}}
        "touched": {},       # {symbol: {level:{'touch_date','touch_price'}}}
        "outcomes": {},      # {symbol: [(level, touch_date, touch_price, outcome, date, price, note)]}
        "daily": [],         # 日志
    }


def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def fetch_retry(symbol, tries=4):
    for i in range(tries):
        df = fetch_sina_kline(symbol)
        if df is not None:
            return df
        time.sleep(6 * (i + 1))  # 退避：6s,12s,18s,24s
    return None


def lock_baseline(s):
    """当日无基线 → 对全池锁定止盈位(预先声明)。"""
    today = date.today().strftime("%Y-%m-%d")
    baseline, errs = {}, []
    for sym in WATCHLIST:
        df = fetch_retry(sym)
        if df is None or len(df) < 30:
            errs.append(sym)
            continue
        sw = find_latest_uptrend_swing(df)
        if sw is None or sw.get("inapplicable"):
            continue
        levels = compute_fib_levels(sw)
        baseline[sym] = {
            "name": WATCHLIST[sym],
            "price": float(df["close"].iloc[-1]),
            "swing_high": sw["swing_high"],
            "swing_low": sw["swing_low"],
            "pullback_low": sw["pullback_low"],
            "levels": {k: v for k, v in levels.items()},
            "locked_at": today,
        }
    s["active_baseline_date"] = today
    s["baseline"] = baseline
    s["daily"].append({"date": today, "kind": "lock",
                       "n": len(baseline), "err": errs})
    save_state(s)
    print(f"📌 已锁定止盈位基线 {today}：{len(baseline)} 只有效"
          f"{('，shutdown:'+str(errs) if errs else '')}")


def verify(s):
    """有基线 → 对照验证：是否触及锁定止盈位，触及后兑现还是突破。"""
    today = date.today().strftime("%Y-%m-%d")
    bdate = s["active_baseline_date"]
    report_events = []
    for sym, rec in s["baseline"].items():
        level_167 = rec["levels"].get("1.618")
        level_127 = rec["levels"].get("1.272")
        level_100 = rec["levels"].get("1.0")
        df = fetch_retry(sym)
        if df is None:
            continue
        idx_last = len(df) - 1
        cur = float(df["close"].iloc[-1])
        # 找到基线日价格
        base_rows = df[df["date"] <= pd_to_dt(rec["locked_at"])]
        base_price = float(base_rows["close"].iloc[-1]) if len(base_rows) else rec["price"]

        for lvl_name, lvl in (("1.0", level_100), ("1.272", level_127), ("1.618", level_167)):
            if lvl is None:
                continue
            key = f"{sym}:{lvl_name}:{round(lvl, 2)}"
            if key in s["touched"]:
                continue  # 已标记触及，等判定
            # 追踪基线日之后的价格，看是否触及该位
            after = df[df["date"] > pd_to_dt(rec["locked_at"])]
            if after.empty:
                continue
            touch = after[after["high"] >= lvl]
            if touch.empty:
                continue
            touch_idx = int(touch.index[0])
            touch_price = float(df["high"].iloc[touch_idx])
            s["touched"][key] = {
                "level_name": lvl_name, "level": lvl,
                "touch_date": str(df["date"].iloc[touch_idx].date()),
                "touch_price": touch_price,
            }
            # 触发后 TOUCH_CHECK_DAYS 内观察走势
            window = df.iloc[touch_idx + 1: touch_idx + 1 + TOUCH_CHECK_DAYS]
            if len(window) == 0:
                note, outcome = "观察中", "pending"
            else:
                high_after = float(window["high"].max())
                low_after = float(window["low"].min())
                last_close = float(window["close"].iloc[-1])
                if low_after <= touch_price * (1 - CONFIRM_FALL_PCT):
                    outcome, note = "兑现", f"触及后回落至{low_after:.2f}，跌破{touch_price - touch_price * CONFIRM_FALL_PCT:.2f}"
                elif last_close >= touch_price * (1 + BREAK_PCT):
                    outcome, note = "突破", f"触及后收于{last_close:.2f}，站稳{touch_price * (1 + BREAK_PCT):.2f}"
                else:
                    outcome, note = "pending", f"观察中(高{high_after:.2f}/低{low_after:.2f}/收{last_close:.2f})"
            s["outcomes"].setdefault(sym, []).append(
                {"level": lvl_name, "touch_date": s["touched"][key]["touch_date"],
                 "touch_price": touch_price, "outcome": outcome, "date": today,
                 "price": cur, "note": note})
            report_events.append(f"  {'✅兑现' if outcome=='兑现' else ('🟢突破' if outcome=='突破' else '⏳观察')} {WATCHLIST[sym]}({sym}) 触及锁定 {lvl_name}={lvl} 于 {s['touched'][key]['touch_date']} 价{touch_price:.2f} → {note}")

    # 汇总命中率
    outcomes = [o for v in s["outcomes"].values() for o in v if o["outcome"] in ("兑现", "突破")]
    hit = sum(1 for o in outcomes if o["outcome"] == "兑现")
    rate = f"{hit}/{len(outcomes)}" if outcomes else "0/0(尚无数)"
    s["daily"].append({"date": today, "kind": "verify",
                       "events": len(report_events), "hit_rate": rate})
    save_state(s)

    print(f"🔍 基线 {bdate} 对照验证（{today}）｜止损兑现 {rate}")
    for e in report_events:
        print(e)
    if not report_events:
        print("  （今日无触及锁定止盈位的事件）")


def pd_to_dt(dstr):
    import pandas as pd
    return pd.Timestamp(dstr)


def build_take_profit_section(current_prices: dict = None) -> str:
    """生成斐波那契波段止盈参考小节(接入收盘盯盘报告)。

    - 读已锁定的 fib_tracking_state.json 基线止盈位(预声明,非现算→不画靶)
    - current_prices: {symbol: 当前价}; 缺省时尝试拉新浪实时 hq(若当日无数据则回退基准价)
    - 输出：触及/接近 锁定1.272-1.618 波段止盈参考区的标的列表
    """
    import os
    state_path = os.path.join(WS, "data/fib_tracking_state.json")
    if not os.path.exists(state_path):
        return "（尚无斐波那契止盈基线，跳过）"
    try:
        with open(state_path, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return "（斐波那契基线读取失败）"

    base = st.get("baseline") or {}
    if not base:
        return "（斐波那契基线为空）"
    bdate = st.get("active_baseline_date", "?")

    # 当前价：优先入参，否则拉新浪 hq
    prices = current_prices or {}
    if not prices:
        try:
            codes = [("sh" if s.startswith("6") else "sz") + s for s in base]
            from analysis.service import fetch_hq_dict
            hq = fetch_hq_dict(codes)
            for s in base:
                r = hq.get(s) or {}
                p = r.get("last") or 0
                prices[s] = p if p > 0 else None
        except Exception:
            pass

    lines = [f"📐 斐波那契波段止盈参考·基线{bdate}", ""]
    hits = []
    for sym, rec in base.items():
        lv = rec.get("levels") or {}
        tp10, tp127, tp161 = lv.get("1.0"), lv.get("1.272"), lv.get("1.618")
        price = prices.get(sym)
        if price is None:
            continue
        name = rec.get("name", sym)
        # 接近/到达 波段止盈区(1.27-1.618)
        if tp127 and tp161 and price >= tp127 * 0.97:
            if price >= tp161:
                zone, note = "已超1.618", f"现价{price:.2f} ≥ 1.618({tp161:.2f})"
            elif price >= tp127:
                zone, note = "1.272-1.618止盈区", f"现价{price:.2f} 距1.618还有{(tp161/price-1)*100:.1f}%"
            else:
                continue
            hits.append((name, sym, price, zone, note))
        elif tp10 and price >= tp10 * 0.97 and tp10:
            hits.append((name, sym, price, "接近1.0等长目标", f"现价{price:.2f} 距1.0({tp10:.2f})还有{(tp10/price-1)*100:.1f}%"))

    if not hits:
        return "\n".join([lines[0], "（今日无触及/接近锁定止盈参考区标的）"])
    for name, sym, price, zone, note in sorted(hits, key=lambda x: -x[2]):
        lines.append(f"• {name}({sym}) 现价{price:.2f}｜{zone}｜{note}")
    lines.append("")
    lines.append("注：仅锁定基线的止盈参考；是否兑现需结合030/MACD/CCI共振，不构成独立买卖信号。")
    return "\n".join(lines)


def main():
    s = load_state()
    today = date.today().strftime("%Y-%m-%d")
    if s.get("active_baseline_date") is None:
        lock_baseline(s)
    else:
        # 每个自然日只验证一次(避免重复计数)
        last = s["daily"][-1] if s["daily"] else {}
        if last.get("date") == today and last.get("kind") == "verify":
            print(f"⏭ {today} 今日已验证，跳过")
        else:
            verify(s)


if __name__ == "__main__":
    main()
