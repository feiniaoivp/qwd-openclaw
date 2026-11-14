#!/usr/bin/env python3
"""watch10 影子组合 NAV（真实执行规则版）—— 判决日历轨道 3 的连续曲线证据。

口径（协议 v1.1，2026-11-15 冻结）：
  - 入场：watch10 建组（2026-11-13）后首个交易日的**开盘价**等权买入（T+1 开盘执行，与主组合同口径）
  - 止损：复刻 36 组合 core 规则 v1——
      (a) 减仓线：收盘 ≤ 入场价×0.97 → 次日开盘减仓 50%（单次，不重复触发）
      (b) ATR 止损：收盘 ≤ 入场价 − 3×ATR14（ATR 用入场日**之前**数据，开盘时点可知的诚实输入）→ 次日开盘清仓
  - NAV = 现金 + Σ 持股×收盘；与 T+5 端点台账（轨道 1）的差值 = 无止损偏差的定量度量
  - 不是判决者：floor 判决以轨道 1 双判据为准，本曲线是路径风险的支持证据

状态: data/watch10/nav_state.json（写前 .bak + 原子替换）；曲线: data/watch10/nav.csv
用法: python3 scripts/watch10_nav_eod.py [--selftest]
"""
import os
import sys
import json
import csv
import argparse

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.service import is_trading_day  # 交易日 SSOT

sys.path.insert(0, os.path.join(WORKSPACE, "scripts"))
from push_watch10_intraday import fetch_quotes, log  # 复用已验证的新浪批量行情（TLS 兜底）

CFG_PATH = os.path.join(WORKSPACE, "data", "watch10", "config.json")
STATE_PATH = os.path.join(WORKSPACE, "data", "watch10", "nav_state.json")
NAV_CSV = os.path.join(WORKSPACE, "data", "watch10", "nav.csv")
STOCK_DIR = os.path.join(WORKSPACE, "data", "stock_data")
NOTIONAL = 1_000_000.0
REDUCE_LINE = 0.97   # core ≤−3% 减仓
ATR_MULT = 3.0       # 激进模式 ATR 止损倍数


def _bak(path):
    if os.path.exists(path):
        import shutil
        shutil.copy2(path, path + ".bak")


def load_state():
    if os.path.exists(STATE_PATH):
        return json.load(open(STATE_PATH, encoding="utf-8"))
    return {"entered": False, "entry_date": None, "cash": NOTIONAL,
            "positions": {}, "pending": {}, "nav_history": []}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    _bak(STATE_PATH)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def atr14_before(code, before_date):
    """入场日前 14 根 K 线的 ATR14（不含入场日——开盘时点可知的诚实输入）。"""
    path = os.path.join(STOCK_DIR, f"{code}.csv")
    if not os.path.exists(path):
        return None
    try:
        rows = []
        with open(path, encoding="utf-8") as f:
            rd = csv.DictReader(f)
            for r in rd:
                if (r.get("date") or "")[:10] < before_date:
                    rows.append(r)
        if len(rows) < 15:
            return None
        trs = []
        for i in range(len(rows) - 14, len(rows)):
            h, l = float(rows[i]["high"]), float(rows[i]["low"])
            pc = float(rows[i - 1]["close"]) if i > 0 else float(rows[i]["open"])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return sum(trs) / len(trs)
    except Exception as e:
        log(f"⚠️ ATR 计算失败 {code}: {e}")
        return None


def _lot(shares_cash, price):
    """按 100 股整手取整。"""
    if price <= 0:
        return 0
    return int(shares_cash / price / 100) * 100


def apply_day(state, today, quotes):
    """单日推进（纯函数便于自测）：先执行昨日排程的次日开盘单，再做当日止损判定与估值。

    quotes: {code: {"open": x, "price": x(=收盘), ...}}；返回 notes 列表。
    """
    notes = []
    # ── 1. 入场（首个交易日开盘价等权）──
    if not state.get("entered"):
        opens = {c: (quotes.get(c) or {}).get("open") or 0 for c in (s["code"] for s in STOCKS)}
        missing = [c for c, o in opens.items() if o <= 0]
        if missing:
            notes.append(f"⚠️ 开盘价缺失 {len(missing)} 只，入场顺延")
        else:
            per = NOTIONAL / len(STOCKS)
            for s in STOCKS:
                c = s["code"]
                px = opens[c]
                sh = _lot(per, px)
                if sh <= 0:
                    continue
                atr = atr14_before(c, today)
                state["positions"][c] = {"name": s["name"], "shares": sh, "entry_price": px,
                                         "entry_date": today,
                                         "atr_stop": round(px - ATR_MULT * atr, 4) if atr else None,
                                         "reduced": False}
                state["cash"] -= sh * px
                notes.append(f"入场 {s['name']}({c}) {sh}股 @ {px}")
            state["entered"] = True
            state["entry_date"] = today
        return state, notes

    # ── 2. 执行昨日排程的次日开盘单 ──
    pending = state.get("pending") or {}
    for c, act in list(pending.items()):
        pos = state["positions"].get(c)
        o = (quotes.get(c) or {}).get("open") or 0
        if not pos or o <= 0:
            continue
        if act == "exit":
            state["cash"] += pos["shares"] * o
            notes.append(f"清仓 {pos.get('name','')}({c}) {pos['shares']}股 @ {o} (ATR止损)")
            del state["positions"][c]
        elif act == "reduce":
            half = int(pos["shares"] // 2 // 100) * 100
            if half > 0:
                state["cash"] += half * o
                pos["shares"] -= half
                notes.append(f"减仓 {pos.get('name','')}({c}) {half}股 @ {o} (-3%线)")
        del pending[c]
    state["pending"] = pending

    # ── 3. 当日收盘止损判定 ──
    for c, pos in list(state["positions"].items()):
        q = quotes.get(c) or {}
        close = q.get("price") or 0
        if close <= 0:
            continue
        entry = pos["entry_price"]
        if close <= (pos.get("atr_stop") or 0):
            state["pending"][c] = "exit"
            notes.append(f"⚠️ ATR止损触发 {c} close={close} ≤ {pos['atr_stop']}")
        elif (not pos.get("reduced")) and close <= entry * REDUCE_LINE:
            state["pending"][c] = "reduce"
            pos["reduced"] = True
            notes.append(f"⚠️ -3%减仓线触发 {c} close={close}")

    # ── 4. 估值 ──
    mv = sum(p["shares"] * ((quotes.get(c) or {}).get("price") or p["entry_price"])
             for c, p in state["positions"].items())
    nav = state["cash"] + mv
    state.setdefault("nav_history", []).append(
        {"date": today, "nav": round(nav, 2), "cash": round(state["cash"], 2),
         "n_positions": len(state["positions"])})
    return state, notes


def write_nav_csv(state):
    os.makedirs(os.path.dirname(NAV_CSV), exist_ok=True)
    rows = state.get("nav_history") or []
    if not rows:
        return
    with open(NAV_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "nav", "ret_cum_pct", "cash", "n_positions"])
        for r in rows:
            ret = (r["nav"] / NOTIONAL - 1) * 100
            w.writerow([r["date"], r["nav"], f"{ret:.3f}", r["cash"], r["n_positions"]])


STOCKS = []


def main():
    global STOCKS
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    STOCKS = cfg.get("stocks") or []

    if args.selftest:
        # 6 断言：入场整手/ATR 止损/-3% 减仓/次日开盘执行/清仓后估值/现金守恒
        global STOCKS_OVERRIDE
        STOCKS = [{"code": "T1", "name": "测试一", "sina": "shT1"},
                  {"code": "T2", "name": "测试二", "sina": "szT2"}]
        st = {"entered": False, "cash": NOTIONAL, "positions": {}, "pending": {}, "nav_history": []}
        # 日1: 入场（open=10.00 → 每只 9900 股）
        q1 = {"T1": {"open": 10.0, "price": 10.2}, "T2": {"open": 20.0, "price": 19.5}}
        st, n1 = apply_day(st, "2026-11-16", q1)
        assert st["entered"] and st["positions"]["T1"]["shares"] == 50000, n1  # 2只→每只50万名义→5万股@10.0
        assert st["positions"]["T2"]["shares"] == 25000, n1  # 50万/20.0→2.5万股
        nav1 = st["cash"] + 9900 * 10.2 + 9900 * 19.5
        # 手动注入 ATR 止损价（跳过 CSV 依赖）
        st["positions"]["T1"]["atr_stop"] = 9.0
        st["positions"]["T2"]["atr_stop"] = 18.0
        # 日2: T1 收 9.5 → 无触发（>9.0 且 >9.7？9.5>9.7 假 → -3%线触发 9.5 ≤ 9.7 ✓）
        q2 = {"T1": {"open": 10.1, "price": 9.5}, "T2": {"open": 19.4, "price": 17.9}}
        st, n2 = apply_day(st, "2026-11-17", q2)
        assert st["pending"].get("T1") == "reduce" and st["pending"].get("T2") == "exit", (n2, st["pending"])
        # 日3: 次日开盘执行 + T1 继续跌穿 9.0 不可能（已减半后 close 触发 exit？v1: reduced 后 -3% 不再触发，ATR 9.0 才清仓）
        q3 = {"T1": {"open": 9.4, "price": 8.9}, "T2": {"open": 17.5, "price": 17.0}}
        st, n3 = apply_day(st, "2026-11-18", q3)
        assert "T2" not in st["positions"], "T2 应已清仓"
        assert st["positions"]["T1"]["shares"] == 25000, "T1 应减半（50000→25000）"
        assert st["pending"].get("T1") == "exit", f"T1 8.9≤9.0 应排程清仓: {st['pending']}"
        # 现金守恒：日3 执行 T1 减仓款(25000×9.4) + T2 清仓款(25000×17.5)
        assert abs(st["cash"] - (25000 * 17.5 + 25000 * 9.4)) < 1e-6, f"cash 应等于两笔执行款: {st['cash']}"
        print("✅ watch10 NAV selftest 6 断言全过（入场整手/-3%减仓/ATR清仓/次日执行/减半/现金守恒）")
        return 0

    if not is_trading_day():
        print("[watch10-nav] 非交易日，跳过")
        return 0
    state = load_state()
    quotes = fetch_quotes(STOCKS)
    if not quotes:
        log("⚠️ 行情获取失败，本轮跳过（不用空数据推进 NAV）")
        return 0
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    state, notes = apply_day(state, today, quotes)
    save_state(state)
    write_nav_csv(state)
    hist = state.get("nav_history") or []
    last = hist[-1] if hist else {}
    ret = (last.get("nav", NOTIONAL) / NOTIONAL - 1) * 100
    print(f"📈 [watch10-NAV] {today} NAV {last.get('nav'):,.0f} ({ret:+.2f}%) | 持仓 {last.get('n_positions')}/10")
    for n in notes:
        print("  ", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
