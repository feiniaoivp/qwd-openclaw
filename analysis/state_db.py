#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""state_db.py — 模拟盘状态 SQLite 影子层（2026-11-05，进化路径第一步 1a）

设计原则（源于 2026-11-05 迁移分叉教训）：
  - **JSON 仍是唯一活跃写路径**（15:40 扫描链路零改动）；本模块构建 SQLite 影子副本
  - 每日 15:55 cron 同步 + **值级对账**（比较解析后的值，非字节数——size 比对对同长度变更失明）
  - 观察期 ≥2 周零分叉后，才把读端切到 SQLite（写端仍 JSON），最终 JSON 退役为导出格式
  - 路径 SSOT：本模块集中定义核心状态路径，后续脚本逐步迁移引用（终结 data/runtime 符号链接兼容层）

库位置: data/sim_state.db（WAL 模式）
表:
  positions      持仓快照（portfolio_sim_state.positions，raw JSON 兜底字段演进期不丢信息）
  trade_intents  交易意向（data/trade_intents/*_intents.json，含状态机终态）
  trades         成交流水（data/portfolio_sim_trades.json，append-only）
  equity_curve   权益曲线（data/daily_equity_mirror.csv + portfolio_equity.csv）
  kv_meta        杂项状态原文（zhonglian_state.json 等，sha256 校验）

用法:
    python3 analysis/state_db.py sync      # 导入全部 + 值级对账（默认）
    python3 analysis/state_db.py verify    # 只对账不导入
"""
import os
import sys
import json
import csv
import glob
import hashlib
import sqlite3
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── 路径 SSOT（核心状态文件唯一权威定义；后续脚本逐步迁移引用）──
PATHS = {
    "sim_state": os.path.join(WORKSPACE, "data", "portfolio_sim_state.json"),
    "sim_trades": os.path.join(WORKSPACE, "data", "portfolio_sim_trades.json"),
    "intents_dir": os.path.join(WORKSPACE, "data", "trade_intents"),
    "equity_mirror": os.path.join(WORKSPACE, "data", "daily_equity_mirror.csv"),
    "portfolio_equity": os.path.join(WORKSPACE, "data", "portfolio_equity.csv"),
    "zhonglian_state": os.path.join(WORKSPACE, "data", "zhonglian_state.json"),
    "db": os.path.join(WORKSPACE, "data", "sim_state.db"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY, name TEXT, position INTEGER, shares REAL,
    entry_price REAL, entry_date TEXT, entry_slippage REAL,
    strategy TEXT, bucket TEXT,
    last_close REAL, last_atr REAL, last_amount REAL,
    raw TEXT NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS trade_intents (
    intent_date TEXT, symbol TEXT, name TEXT, action TEXT, strategy TEXT,
    price REAL, bucket TEXT, status TEXT,
    fill_price REAL, fill_gap_pct REAL,
    raw TEXT NOT NULL, updated_at TEXT,
    PRIMARY KEY (intent_date, symbol));
CREATE TABLE IF NOT EXISTS trades (
    date TEXT, symbol TEXT, name TEXT, action TEXT, price REAL, shares REAL,
    pnl REAL, pnl_pct REAL, strategy TEXT, reason TEXT, bucket TEXT,
    raw TEXT NOT NULL, updated_at TEXT,
    PRIMARY KEY (date, symbol, action));
CREATE TABLE IF NOT EXISTS equity_curve (
    date TEXT, source TEXT, total_value REAL, cash REAL, market_value REAL,
    return_pct REAL, extra TEXT, updated_at TEXT,
    PRIMARY KEY (date, source));
CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(PATHS["db"], timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ══════════════════════ 导入（JSON → SQLite）══════════════════════

def import_positions(conn) -> int:
    if not os.path.exists(PATHS["sim_state"]):
        return 0
    with open(PATHS["sim_state"], encoding="utf-8") as f:
        state = json.load(f)
    positions = state.get("positions") or {}
    n = 0
    for sym, p in positions.items():
        if not isinstance(p, dict):
            continue
        conn.execute(
            """INSERT INTO positions (symbol,name,position,shares,entry_price,entry_date,
               entry_slippage,strategy,bucket,last_close,last_atr,last_amount,raw,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(symbol) DO UPDATE SET name=excluded.name,position=excluded.position,
               shares=excluded.shares,entry_price=excluded.entry_price,entry_date=excluded.entry_date,
               entry_slippage=excluded.entry_slippage,strategy=excluded.strategy,bucket=excluded.bucket,
               last_close=excluded.last_close,last_atr=excluded.last_atr,last_amount=excluded.last_amount,
               raw=excluded.raw,updated_at=excluded.updated_at""",
            (sym, p.get("name"), int(p.get("position") or 0), _f(p.get("shares")),
             _f(p.get("entry_price")), p.get("entry_date"), _f(p.get("entry_slippage")),
             p.get("strategy"), p.get("bucket"),
             _f(p.get("_last_close")), _f(p.get("_last_atr")), _f(p.get("_last_amount")),
             json.dumps(p, ensure_ascii=False), _now()))
        n += 1
    return n


def import_intents(conn) -> int:
    n = 0
    for fp in sorted(glob.glob(os.path.join(PATHS["intents_dir"], "*_intents.json"))):
        idate = os.path.basename(fp).replace("_intents.json", "")
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for sym, it in data.items():
            if not isinstance(it, dict):
                continue
            fill = it.get("fill") or {}
            conn.execute(
                """INSERT INTO trade_intents (intent_date,symbol,name,action,strategy,price,bucket,
                   status,fill_price,fill_gap_pct,raw,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(intent_date,symbol) DO UPDATE SET name=excluded.name,action=excluded.action,
                   strategy=excluded.strategy,price=excluded.price,bucket=excluded.bucket,
                   status=excluded.status,fill_price=excluded.fill_price,fill_gap_pct=excluded.fill_gap_pct,
                   raw=excluded.raw,updated_at=excluded.updated_at""",
                (idate, sym, it.get("name"), it.get("action"), it.get("strategy"),
                 _f(it.get("price")), it.get("bucket"), it.get("status"),
                 _f(fill.get("price")), _f(fill.get("gap_pct")),
                 json.dumps(it, ensure_ascii=False), _now()))
            n += 1
    return n


def import_trades(conn) -> int:
    if not os.path.exists(PATHS["sim_trades"]):
        return 0
    try:
        with open(PATHS["sim_trades"], encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return 0
    items = raw if isinstance(raw, list) else [t for v in raw.values() for t in (v if isinstance(v, list) else [v])]
    n = 0
    for t in items:
        if not isinstance(t, dict) or not t.get("date") or not t.get("symbol"):
            continue
        try:
            conn.execute(
                """INSERT OR REPLACE INTO trades (date,symbol,name,action,price,shares,pnl,pnl_pct,
                   strategy,reason,bucket,raw,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(t.get("date")), str(t.get("symbol")), t.get("name"), t.get("action"),
                 _f(t.get("price")), _f(t.get("shares")), _f(t.get("pnl")), _f(t.get("pnl_pct")),
                 t.get("strategy"), str(t.get("reason") or "")[:200], t.get("bucket"),
                 json.dumps(t, ensure_ascii=False), _now()))
            n += 1
        except sqlite3.Error:
            continue
    return n


# 两个权益源 schema/口径不同（2026-11-05 实证：mirror 含当日 FILLED 市值 278.6万，
# portfolio 是 030 台账口径 270.1万）——必须按源映射，禁止同键覆盖
EQUITY_SOURCES = {
    "equity_mirror": {
        "source": "mirror",
        "map": {"total_value": "total_equity", "cash": "cash",
                "market_value": "market_value", "return_pct": "return_pct"},
    },
    "portfolio_equity": {
        "source": "portfolio",
        "map": {"total_value": "total_value", "cash": "cash_total",
                "market_value": "__core_plus_satellite__", "return_pct": "total_return_pct"},
        "extra_cols": ["positions_held", "total_initial"],
    },
}


def import_equity(conn) -> int:
    n = 0
    for src_key, cfg in EQUITY_SOURCES.items():
        fp = PATHS[src_key]
        if not os.path.exists(fp):
            continue
        with open(fp, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                date = (row.get("date") or "").strip()
                if not date:
                    continue
                vals = {}
                for db_col, csv_col in cfg["map"].items():
                    if csv_col == "__core_plus_satellite__":
                        vals[db_col] = (_f(row.get("core_value")) or 0) + (_f(row.get("satellite_value")) or 0)
                    else:
                        vals[db_col] = _f(row.get(csv_col))
                extra = json.dumps({c: row.get(c) for c in cfg.get("extra_cols", [])}, ensure_ascii=False)
                conn.execute(
                    """INSERT INTO equity_curve (date,source,total_value,cash,market_value,return_pct,extra,updated_at)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(date,source) DO UPDATE SET total_value=excluded.total_value,
                       cash=excluded.cash,market_value=excluded.market_value,return_pct=excluded.return_pct,
                       extra=excluded.extra,updated_at=excluded.updated_at""",
                    (date, cfg["source"], vals["total_value"], vals["cash"],
                     vals["market_value"], vals["return_pct"], extra, _now()))
                n += 1
    return n


def import_meta(conn) -> int:
    n = 0
    for key, path_key in (("zhonglian_state", "zhonglian_state"),):
        fp = PATHS[path_key]
        if not os.path.exists(fp):
            continue
        with open(fp, "rb") as f:
            blob = f.read()
        conn.execute(
            """INSERT INTO meta (key,value,updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (key, hashlib.sha256(blob).hexdigest(), _now()))
        n += 1
    return n


def sync_all(conn) -> dict:
    stats = {
        "positions": import_positions(conn),
        "trade_intents": import_intents(conn),
        "trades": import_trades(conn),
        "equity_curve": import_equity(conn),
        "meta": import_meta(conn),
    }
    conn.commit()
    return stats


# ══════════════════════ 值级对账（SQLite vs JSON 源）══════════════════════

def verify_all(conn) -> list:
    """值级对账：比较解析后的值（非字节数）。返回问题列表；空列表 = 零分叉。"""
    issues = []

    # 1) equity_curve：按源映射逐行比对（schema/口径不同，禁止跨源比）
    for src_key, cfg in EQUITY_SOURCES.items():
        fp = PATHS[src_key]
        if not os.path.exists(fp):
            continue
        src = cfg["source"]
        with open(fp, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                date = (row.get("date") or "").strip()
                if not date:
                    continue
                r = conn.execute("SELECT * FROM equity_curve WHERE date=? AND source=?",
                                 (date, src)).fetchone()
                if r is None:
                    issues.append(f"equity_curve[{src}] 缺行: {date}")
                    continue
                for db_col, csv_col in cfg["map"].items():
                    if csv_col == "__core_plus_satellite__":
                        a = (_f(row.get("core_value")) or 0) + (_f(row.get("satellite_value")) or 0)
                    else:
                        a = _f(row.get(csv_col))
                    b = r[db_col]
                    if (a is None) != (b is None) or (a is not None and abs(a - (b or 0)) > 1e-6):
                        issues.append(f"equity_curve[{src}][{date}].{db_col}: json={a} db={b}")

    # 2) trade_intents：每个意向文件逐字段比对（状态机关键字段）
    for fp in sorted(glob.glob(os.path.join(PATHS["intents_dir"], "*_intents.json"))):
        idate = os.path.basename(fp).replace("_intents.json", "")
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for sym, it in (data or {}).items():
            if not isinstance(it, dict):
                continue
            r = conn.execute("SELECT * FROM trade_intents WHERE intent_date=? AND symbol=?",
                             (idate, sym)).fetchone()
            if r is None:
                issues.append(f"trade_intents 缺行: {idate}/{sym}")
                continue
            fill = it.get("fill") or {}
            checks = [("status", it.get("status"), r["status"]),
                      ("action", it.get("action"), r["action"]),
                      ("fill.price", _f(fill.get("price")), r["fill_price"]),
                      ("fill.gap_pct", _f(fill.get("gap_pct")), r["fill_gap_pct"])]
            for label, a, b in checks:
                if isinstance(a, float) and isinstance(b, float):
                    bad = abs(a - b) > 1e-9
                else:
                    bad = a != b
                if bad:
                    issues.append(f"trade_intents[{idate}/{sym}].{label}: json={a} db={b}")

    # 3) positions：shares/position/entry_price 比对
    if os.path.exists(PATHS["sim_state"]):
        with open(PATHS["sim_state"], encoding="utf-8") as f:
            state = json.load(f)
        for sym, p in (state.get("positions") or {}).items():
            if not isinstance(p, dict):
                continue
            r = conn.execute("SELECT * FROM positions WHERE symbol=?", (sym,)).fetchone()
            if r is None:
                issues.append(f"positions 缺行: {sym}")
                continue
            for col in ("position", "shares", "entry_price"):
                a, b = p.get(col), r[col]
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    bad = abs(float(a) - float(b)) > 1e-9
                else:
                    bad = a != b
                if bad:
                    issues.append(f"positions[{sym}].{col}: json={a} db={b}")

    # 4) meta：zhonglian_state sha256 比对
    if os.path.exists(PATHS["zhonglian_state"]):
        with open(PATHS["zhonglian_state"], "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
        r = conn.execute("SELECT value FROM meta WHERE key='zhonglian_state'").fetchone()
        if r is None:
            issues.append("meta 缺行: zhonglian_state")
        elif r["value"] != h:
            issues.append("meta[zhonglian_state]: sha256 不一致")

    return issues


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "sync"
    conn = connect()
    try:
        if mode == "sync":
            stats = sync_all(conn)
            print(f"📥 同步: {stats}")
        issues = verify_all(conn)
        if issues:
            print(f"🔴 值级对账分叉 {len(issues)} 处:")
            for i in issues[:20]:
                print(f"  - {i}")
            return 1
        print("✅ 值级对账零分叉（SQLite 影子层 = JSON 源）")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
