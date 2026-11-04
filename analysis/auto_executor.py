#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_executor.py — 信号→次日开盘 管道化执行器（2026-11-04 用户拍板方案 B）

设计（对应拍板原文「信号→次日开盘限价 + 影子双轨，同 bar 收盘前视退役」）：
- 信号日 T：run_portfolio_scan 门控链通过的可执行信号 → 写 PENDING 意向（**不成交**）
- 执行日 T+1（下一交易日）：扫描消费 PENDING → 以当日开盘价（K 线 open）成交，
  动态滑点照常、030 限额/三因素门控在执行日**再校验**（仓位与资金以 T+1 实况为准）
- 同 bar 收盘前视成交退役；回退开关：环境变量 AUTO_EXECUTOR=0 走 legacy 原路径
- 影子双轨不受影响：lowopen-shadow-capture(18:45) 与 19:41 执行审计照常独立运行

意向状态机（data/trade_intents/{信号日}_intents.json）：
  PENDING → FILLED（已按执行日开盘价成交，记录 gap）
           → BLOCKED（执行日门控/dedup 拦截，留 reason）
           → CANCELLED（执行日仓位已不存在，空仓卖噪声）
           → EXPIRED（超龄 AUTO_EXEC_MAX_AGE 天，默认 5 日历日）
           → SUPERSEDED（同标的更新意向已接管）

幂等性：执行日同一标的二次消费被 check_dedup（D3 单日 1 笔硬限）拦截；
意向文件重跑同日仅覆盖同内容；回写前当日 .bak 防丢史。
"""
import os
import sys
import glob
import json
import shutil
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
# 同时插入：工作区根 + analysis/（脚本模式自检样板，见 TOOLS.md）
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

INTENTS_DIR = os.path.join(WORKSPACE, "data", "trade_intents")


def auto_executor_enabled() -> bool:
    """总开关：AUTO_EXECUTOR=0 一键回退 legacy 同 bar 收盘路径。"""
    return os.getenv("AUTO_EXECUTOR", "1").strip() != "0"


def _max_age_days() -> int:
    return int(os.getenv("AUTO_EXEC_MAX_AGE") or 5)


def _parse_date(s):
    try:
        return datetime.strptime(str(s), "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def load_pending_intents(today_str: str) -> dict:
    """读取所有历史意向文件，返回 {sym: [entry, ...]}（按意向日升序）。

    entry = {"date": 意向日, "sym": 代码, "intent": dict, "path": 文件路径, "_mutated": False}
    仅收 status==PENDING 且意向日 < today_str 的条目；今日/未来文件不动（主流程管理）。
    """
    index = {}
    if not os.path.isdir(INTENTS_DIR):
        return index
    today_dt = _parse_date(today_str)
    if today_dt is None:
        print(f"⚠️ [auto-exec] today_str 非法: {today_str}")
        return index
    for fp in sorted(glob.glob(os.path.join(INTENTS_DIR, "*_intents.json"))):
        idate = os.path.basename(fp).replace("_intents.json", "")
        d = _parse_date(idate)
        if d is None or d >= today_dt:
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"⚠️ [auto-exec] 意向文件读取失败 {os.path.basename(fp)}: {e}")
            continue
        if not isinstance(data, dict):
            continue
        for sym, intent in data.items():
            if not isinstance(intent, dict) or intent.get("status") != "PENDING":
                continue
            index.setdefault(sym, []).append(
                {"date": idate, "sym": sym, "intent": intent, "path": fp, "_mutated": False}
            )
    for sym in index:
        index[sym].sort(key=lambda e: e["date"])
    return index


def consume_pending_for_symbol(ctx: dict, sym: str, name: str, pos: dict,
                               df, today_str: str, audit_entry=None, strat=None):
    """消费该标的往日 PENDING 意向 → 以今日开盘价成交。

    返回 (action, msg)：action 为 BUY/SELL 时调用方累计 trade_count 并展示 msg；
    其余情况意向状态已在文件数据内变更（flush 统一回写）。
    """
    entries = ctx.get("pending_index", {}).get(sym) or []
    if not entries:
        return None, None

    # 数据新鲜度：必须拿到「今日」K 线才允许以今日开盘价成交（预跑/滞后数据 → 留待正式扫描）
    try:
        data_date = df["date"].iloc[-1].strftime("%Y-%m-%d")
    except Exception as e:
        print(f"⚠️ [auto-exec] {name}({sym}) data_date 解析失败: {e}")
        return None, None
    if str(data_date) < today_str:
        return None, None

    # 同标的多个待处理意向：最新接管，旧的废弃
    latest = entries[-1]
    for old in entries[:-1]:
        old["intent"]["status"] = "SUPERSEDED"
        old["intent"]["superseded_by"] = latest["date"]
        old["_mutated"] = True

    intent = latest["intent"]
    intent_date = latest["date"]
    d_today, d_intent = _parse_date(today_str), _parse_date(intent_date)
    age_days = (d_today - d_intent).days if (d_today and d_intent) else 9999
    max_age = int(ctx.get("max_age_days") or _max_age_days())
    if age_days > max_age:
        intent["status"] = "EXPIRED"
        intent["expired_reason"] = f"意向 age={age_days}d > 上限 {max_age}d"
        latest["_mutated"] = True
        if audit_entry is not None:
            audit_entry["guards"]["auto_exec"] = f"EXPIRED(age={age_days}d)"
        print(f"📨→⏳ [auto-exec] {name}({sym}) 意向过期: {intent['expired_reason']}")
        return None, None

    # 今日开盘价
    try:
        open_price = float(df["open"].iloc[-1])
    except (TypeError, ValueError, KeyError) as e:
        print(f"⚠️ [auto-exec] {name}({sym}) 开盘价缺失: {e}")
        return None, None
    if not (open_price and open_price > 0):
        print(f"⚠️ [auto-exec] {name}({sym}) 开盘价异常: {open_price!r}")
        return None, None

    sig_close = float(intent.get("price") or 0)
    gap_pct = (open_price / sig_close - 1) if sig_close > 0 else None

    # 执行日仓位存在性（空仓卖噪声 / 仓位已被止盈等方式了结）
    if intent.get("action") == "卖出" and not pos.get("position"):
        intent["status"] = "CANCELLED"
        intent["cancel_reason"] = "执行日仓位已不存在"
        latest["_mutated"] = True
        if audit_entry is not None:
            audit_entry["guards"]["auto_exec"] = "CANCELLED(无持仓)"
        return None, None

    gap_txt = f", gap {gap_pct:+.2%}" if gap_pct is not None else ""
    fill_signal = {
        "action": intent.get("action"),
        "price": open_price,
        "reason": f"{intent.get('reason', '')} | 次日开盘执行(信号日 {intent_date}{gap_txt})",
        "atr": intent.get("atr"),
        "atr_stop": intent.get("atr_stop"),
        "fib_tp": intent.get("fib_tp") or {},
        "key_indicators": intent.get("key_indicators", ""),
        "_symbol": sym,
        "name": name,
    }

    # 执行日再校验：D3 单日 1 笔硬限等（与信号日门控解耦）
    if not ctx["check_dedup"](fill_signal, pos, today_str):
        intent["status"] = "BLOCKED"
        intent["block_reason"] = "执行日 dedup：该标的当日已有成交"
        latest["_mutated"] = True
        if audit_entry is not None:
            audit_entry["guards"]["auto_exec"] = "BLOCKED(dedup)"
        print(f"📨→⛔ [auto-exec] {name}({sym}) 意向未成交: {intent['block_reason']}")
        return None, None

    action, msg = ctx["execute_trade"](pos, fill_signal, today_str,
                                       strategy=strat or intent.get("strategy"),
                                       position_plan=ctx.get("position_plan") or {})
    if action in ("BUY", "SELL"):
        intent["status"] = "FILLED"
        intent["fill"] = {"date": today_str, "price": open_price, "gap_pct": gap_pct,
                          "ts": datetime.now().isoformat()}
        latest["_mutated"] = True
        if audit_entry is not None:
            audit_entry["guards"]["auto_exec"] = (
                f"FILLED@open {open_price} (gap {gap_pct:+.2%})" if gap_pct is not None
                else f"FILLED@open {open_price}"
            )
        return action, msg

    intent["status"] = "BLOCKED"
    intent["block_reason"] = str(msg or "")[:200]
    latest["_mutated"] = True
    if audit_entry is not None:
        audit_entry["guards"]["auto_exec"] = f"BLOCKED({action or 'None'})"
    print(f"📨→⛔ [auto-exec] {name}({sym}) 意向未成交: {intent['block_reason']}")
    return None, None


def flush_intent_updates(pending_index: dict, visited_syms=None, today_str: str = None):
    """把消费/过期/取代后的意向状态回写原文件。

    - 每文件写前创建当日 .bak（同日仅一次），防重写丢史（backup 铁律）
    - 未被本扫描遍历的标的：仅做超龄 EXPIRE，避免幽灵 PENDING 永挂
    """
    visited = visited_syms or set()
    by_path = {}
    for sym, entries in pending_index.items():
        if sym not in visited and today_str:
            d_today = _parse_date(today_str)
            for e in entries:
                if e["intent"].get("status") != "PENDING":
                    continue
                d_i = _parse_date(e["date"])
                age = (d_today - d_i).days if (d_today and d_i) else 9999
                if age > _max_age_days():
                    e["intent"]["status"] = "EXPIRED"
                    e["intent"]["expired_reason"] = f"未被扫描遍历且 age={age}d"
                    e["_mutated"] = True
        for e in entries:
            if e.get("_mutated"):
                by_path.setdefault(e["path"], []).append(e)

    for fp, entries in by_path.items():
        if not entries:
            continue
        bak = f"{fp}.bak-{today_str}" if today_str else f"{fp}.bak"
        if not os.path.exists(bak):
            try:
                shutil.copy2(fp, bak)
            except Exception as e:
                print(f"⚠️ [auto-exec] 意向备份失败 {fp}: {e}")
                continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            for e in entries:
                data[e["sym"]] = e["intent"]
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            n = len(entries)
            print(f"📨 [auto-exec] 意向回写 {os.path.basename(fp)}: {n} 条状态更新")
        except Exception as e:
            print(f"⚠️ [auto-exec] 意向回写失败 {fp}: {e}")


def build_pending_intent(name: str, signal: dict, pos: dict, strat, now_iso: str) -> dict:
    """信号日排队：由 run_portfolio_scan 的门控后信号构建 PENDING 意向（字段向后兼容扩充）。"""
    return {
        "name": name,
        "action": signal["action"],
        "reason": signal["reason"],
        "price": signal["price"],
        "bucket": pos.get("bucket", "satellite"),
        "strategy": strat,
        "atr": signal.get("atr"),
        "atr_stop": signal.get("atr_stop"),
        "fib_tp": signal.get("fib_tp") or {},
        "created_at": now_iso,
        "status": "PENDING",
    }


if __name__ == "__main__":
    # 自检：不触任何生产文件（开关语义 + 状态机纯逻辑）
    os.environ["AUTO_EXECUTOR"] = "1"
    assert auto_executor_enabled() is True
    os.environ["AUTO_EXECUTOR"] = "0"
    assert auto_executor_enabled() is False
    os.environ["AUTO_EXECUTOR"] = "1"
    empty = load_pending_intents("1970-01-01")
    assert isinstance(empty, dict)
    print("✅ auto_executor 自检通过（开关/加载接口正常，零生产接触）")
