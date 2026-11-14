#!/usr/bin/env python3
"""tier1 择时卖出抑制台账：结算被拦卖出的持仓后续 T+5 表现（判决日历轨道 4）。

数据源（零新增生产 hook，只读推导）：
  - 拦截记录: data/tier_suppressed_signals.jsonl（portfolio_core._log_tier_suppression 落盘，
    含 date/symbol/from_action/orig_reason/price）
  - 价格: data/stock_data/{code}.csv（watchlist 全历史，date,open,high,low,close,volume）
          兜底 data/universe/daily/{sh,sz,bj}{code}.csv

台账: data/universe/tier_sell_ledger.json —— 每条被 tier1 拦截的卖出记录：
  date, symbol, name, orig_reason, close_at_block, settled, settle_date, ret5, maxdd5(真实 low 口径)
裁决问题：被拦卖出的持仓后续 T+5 显著为正 = tier1 政策省钱；显著为负 = 政策烧钱
（DECISION_CALENDAR 修订 3，commit 439a46c）。

用法: python3 scripts/tier_sell_settle.py            # 结算 + 汇总打印（report-only，链尾安全 exit 0）
"""
import os
import sys
import json
import glob

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.service import get_trading_days  # 交易日 SSOT

SUPPRESS_PATH = os.path.join(WORKSPACE, "data", "tier_suppressed_signals.jsonl")
LEDGER_PATH = os.path.join(WORKSPACE, "data", "universe", "tier_sell_ledger.json")
STOCK_DIR = os.path.join(WORKSPACE, "data", "stock_data")
LAKE_DIR = os.path.join(WORKSPACE, "data", "universe", "daily")
HORIZON = 5


def _bak(path):
    if os.path.exists(path):
        import shutil
        shutil.copy2(path, path + ".bak")


def load_suppressed_sells():
    """读取 tier1 被拦卖出，按 (date, symbol) 去重取当日最后一条。"""
    if not os.path.exists(SUPPRESS_PATH):
        return []
    seen = {}
    with open(SUPPRESS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("tier") != "tier1" or r.get("from_action") != "卖出":
                continue
            key = (r.get("date"), r.get("symbol"))
            if not key[0] or not key[1]:
                continue
            seen[key] = r  # 后写覆盖 = 当日最后一条
    return [dict(date=k[0], symbol=k[1], **{kk: v.get(kk) for kk in ("name", "orig_reason", "price")})
            for k, v in sorted(seen.items())]


def _csv_for(code):
    """优先 stock_data（无前缀），兜底 universe lake（sh/sz/bj 前缀）。返回 (path, kind) 或 None。"""
    p = os.path.join(STOCK_DIR, f"{code}.csv")
    if os.path.exists(p):
        return p, "stock"
    for pre in ("sh", "sz", "bj"):
        p = os.path.join(LAKE_DIR, f"{pre}{code}.csv")
        if os.path.exists(p):
            return p, "lake"
    return None, None


def load_closes(code):
    """返回 {date: close} 与 {date: low}（升序）。"""
    path, kind = _csv_for(code)
    if not path:
        return None, None
    out_c, out_l = {}, {}
    try:
        with open(path, encoding="utf-8") as f:
            header = f.readline().strip().split(",")
            idx = {c: i for i, c in enumerate(header)}
            need = all(k in idx for k in ("date", "close"))
            if not need:
                return None, None
            has_low = "low" in idx
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < len(header):
                    continue
                d = parts[idx["date"]][:10]
                try:
                    out_c[d] = float(parts[idx["close"]])
                    if has_low:
                        out_l[d] = float(parts[idx["low"]])
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        print(f"⚠️ 读价失败 {code}: {e}")
        return None, None
    return out_c, out_l


def settle(tdays):
    """对台账未结算条目做 T+5 结算（价格齐才结，否则保持 pending）。"""
    ledger = {"meta": {"note": "tier1 择时卖出抑制台账（轨道4）；ret5/maxdd5 相对被拦日收盘",
                       "horizon": HORIZON},
              "entries": []}
    if os.path.exists(LEDGER_PATH):
        try:
            old = json.load(open(LEDGER_PATH, encoding="utf-8"))
            ledger["entries"] = old.get("entries") or []
        except Exception:
            pass
    have = {(e.get("date"), e.get("symbol")): e for e in ledger["entries"]}
    sup = load_suppressed_sells()
    added = 0
    for rec in sup:
        key = (rec["date"], rec["symbol"])
        if key in have:
            continue
        closes, lows = load_closes(rec["symbol"])
        cb = closes.get(rec["date"]) if closes else None
        if cb is None:  # 无价格数据（非 watchlist 或窗口外）→ 跳过不入账
            continue
        have[key] = {"date": rec["date"], "symbol": rec["symbol"], "name": rec.get("name") or "",
                     "orig_reason": rec.get("orig_reason") or "", "close_at_block": cb,
                     "settled": False}
        ledger["entries"].append(have[key])
        added += 1

    settled_n = 0
    for e in ledger["entries"]:
        if e.get("settled"):
            continue
        d0 = e["date"]
        if d0 not in tdays:
            continue
        i0 = tdays.index(d0)
        if len(tdays) - i0 - 1 < HORIZON:
            continue
        settle_date = tdays[i0 + HORIZON]
        closes, lows = load_closes(e["symbol"])
        if not closes or closes.get(settle_date) is None or closes.get(d0) is None:
            continue
        cb = closes[d0]
        e["settle_date"] = settle_date
        e["ret5"] = round(closes[settle_date] / cb - 1.0, 6)
        win = [closes[d] for d in tdays[i0:i0 + HORIZON + 1] if d in closes]
        if lows:  # 真实 low 口径（更诚实），缺 low 的日子退回 close
            win_low = [lows.get(d, closes[d]) for d in tdays[i0:i0 + HORIZON + 1] if d in closes]
        else:
            win_low = win
        if cb > 0 and win_low:
            e["maxdd5"] = round(min(win_low) / cb - 1.0, 6)
        e["settled"] = True
        settled_n += 1

    ledger["entries"] = ledger["entries"][-200:]
    _bak(LEDGER_PATH)
    tmp = LEDGER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LEDGER_PATH)
    return {"total": len(ledger["entries"]), "added": added, "settled": settled_n,
            "pending": sum(1 for e in ledger["entries"] if not e.get("settled"))}


def main():
    tdays = get_trading_days("2026-10-01") or []
    if not tdays:
        print("⚠️ [tier-sell] 交易日历不可得，跳过结算")
        return 0
    st = settle(tdays)
    print(f"📊 [tier-sell] 台账 {st['total']} 条 | 新增 {st['added']} | 本轮结算 {st['settled']} | 未结算 {st['pending']}")
    settled = []
    if os.path.exists(LEDGER_PATH):
        settled = [e for e in json.load(open(LEDGER_PATH, encoding="utf-8")).get("entries", []) if e.get("settled")]
    if settled:
        rs = [e["ret5"] for e in settled if e.get("ret5") is not None]
        if rs:
            rs_sorted = sorted(rs)
            med = rs_sorted[len(rs_sorted) // 2]
            print(f"   已结算 {len(rs)} 条 | 持仓后续 T+5 中位 {med:+.2%} | 区间 [{min(rs):+.2%}, {max(rs):+.2%}]")
            print("   （中位为正 = tier1 政策省钱；为负 = 政策在烧钱 → 详见 DECISION_CALENDAR 轨道 4）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # report-only：链尾安全
        print(f"⚠️ [tier-sell] 非致命异常（不影响主链）: {e}")
        sys.exit(0)
