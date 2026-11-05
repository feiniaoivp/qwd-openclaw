#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_executor 全流程 e2e：两天连续扫描（T 日排队 → T+1 开盘成交），沙箱零污染。

与单元版 e2e（test_auto_executor_e2e.py）的差别：本文件驱动完整 run_portfolio_scan
（信号→门控→排队→消费→state/equity/trades/audit/intents 全链落盘），
验证集成时序而非仅执行器函数。强制信号用自定义策略注入，保证确定性。
"""
import os
import sys
import json
import hashlib
import shutil
import tempfile
from datetime import datetime, timedelta

PROD_WS = "/Users/duguke/.openclaw/workspace"
_today_real = datetime.now().strftime("%Y-%m-%d")
PROD_GUARDS = [
    os.path.join(PROD_WS, "data", "portfolio_sim_state.json"),
    os.path.join(PROD_WS, "data", "portfolio_sim_trades.json"),
    os.path.join(PROD_WS, "data", "portfolio_equity.csv"),
    os.path.join(PROD_WS, "data", "trade_intents", _today_real + "_intents.json"),
]


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None


_before = {p: _sha(p) for p in PROD_GUARDS}

SANDBOX = tempfile.mkdtemp(prefix="autoexec_full_")
os.environ["WORKSPACE"] = SANDBOX
os.environ["AUTO_EXECUTOR"] = "1"
os.makedirs(os.path.join(SANDBOX, "data", "trade_intents"), exist_ok=True)
os.makedirs(os.path.join(SANDBOX, "audit"), exist_ok=True)
# 2026-11-06 修复：11-05 data/runtime 迁移后，portfolio_core 写意向用 data/trade_intents、
# auto_executor 读 PENDING 用 data/runtime/trade_intents——生产靠 symlink 同 inode；
# 沙箱不复刻该 symlink 则 Day2 消费永远读空（与单元版同款假失败）。
_rt = os.path.join(SANDBOX, "data", "runtime")
if not os.path.lexists(_rt):
    os.symlink(os.path.join(SANDBOX, "data"), _rt)
for _fn in ("sim_aggressive_config.json", "corr_clusters.json"):
    _src = os.path.join(PROD_WS, "data", _fn)
    if os.path.exists(_src):
        shutil.copy2(_src, os.path.join(SANDBOX, "data", _fn))
# 自定义策略 map：扫描开头的分层同步会用它刷写 pos.strategy/tier（生产 map 无 TEST* 会落默认 ema_cross）
with open(os.path.join(SANDBOX, "data", "adaptive_strategy_map.json"), "w", encoding="utf-8") as f:
    json.dump({
        "TESTB": {"strategy": "e2e_buy", "bucket": "tier2"},
        "TESTS": {"strategy": "e2e_sell", "bucket": "tier2"},
        "TESTH": {"strategy": "e2e_hold", "bucket": "tier2"},
    }, f, ensure_ascii=False)

day1 = "2026-11-04"
day2 = "2026-11-05"


def _pos(name, strategy, position=False, shares=0, entry_price=0.0, cash=0.0, bucket="core"):
    return {"name": name, "strategy": strategy, "position": position, "shares": shares,
            "entry_price": entry_price, "entry_date": day1 if position else None,
            "cash": cash, "total_pl": 0.0, "trade_count": 0, "bucket": bucket,
            "tier": "tier2", "atr_params": {"stop_mult": 3.0, "risk_per_trade": 0.02},
            "atr_stop": 5.0, "_buy_streak": 0, "_stop_cooldown_days": 0,
            "_symbol": name}


_state = {"last_signal_date": None, "positions": {
    "TESTB": _pos("测试买", "e2e_buy", position=False, cash=100000.0, bucket="core"),
    "TESTS": _pos("测试卖", "e2e_sell", position=True, shares=100, entry_price=9.5, cash=100.0, bucket="satellite"),
    "TESTH": _pos("测试持", "e2e_hold", position=True, shares=100, entry_price=10.0, cash=0.0, bucket="core"),
}}
with open(os.path.join(SANDBOX, "data", "portfolio_sim_state.json"), "w", encoding="utf-8") as f:
    json.dump(_state, f, ensure_ascii=False)

sys.path.insert(0, "/Users/duguke/.openclaw/workspace")
import pandas as pd  # noqa: E402
from analysis import portfolio_core as pc  # noqa: E402
import analysis.three_factor_helper as tfh  # noqa: E402


class _StubGate:
    def check_buy(self, sym):
        return True, {"reason": "e2e-stub"}


tfh.ResonanceGate = _StubGate

# 2026-11-06 修复：030 FatalRiskDetector.check() 走真实市场条件——HIGH_RISK 窗口期
# （如 11-05 收盘扫描实测 high_risk=True）会把 TESTB 买入 override 成持有 →
# Day1 排队断言假失败。e2e 固定中性风控保证确定性（可重复跑）。
_pc_default_plan = dict(pc.RiskGuard._position_plan)


def _e2e_neutral_refresh(cls, cache_key=None, atr_map=None):
    cls._fatal_risk = {"fatal_triggered": False, "high_risk_triggered": False}
    cls._position_plan = dict(_pc_default_plan)


pc.RiskGuard.refresh = classmethod(_e2e_neutral_refresh)


def _mk_sig(action):
    def _f(df):
        price = float(df["close"].iloc[-1])
        return {"action": action, "price": price, "reason": "e2e 强制" + action,
                "atr": 0.2, "atr_stop": price * 0.5, "fib_tp": {"targets": {}},
                "key_indicators": "e2e"}
    return _f


pc.SIGNAL_FUNCS["e2e_buy"] = _mk_sig("买入")
pc.SIGNAL_FUNCS["e2e_sell"] = _mk_sig("卖出")
pc.SIGNAL_FUNCS["e2e_hold"] = _mk_sig("持有")


def _mkdf(date_str, open_price, close_price, n=70):
    d0 = datetime.strptime(date_str, "%Y-%m-%d")
    rows = []
    for i in range(n - 1, 0, -1):
        d = d0 - timedelta(days=i)
        rows.append({"date": d, "open": 10.0, "high": 10.2, "low": 9.8,
                     "close": 10.0, "volume": 1e6, "amount": 1e7})
    rows.append({"date": d0, "open": open_price, "high": max(open_price, close_price) + 0.1,
                 "low": min(open_price, close_price) - 0.1, "close": close_price,
                 "volume": 2e6, "amount": 2e7})
    return pd.DataFrame(rows)


STOCKS = [("TESTB", "测试买"), ("TESTS", "测试卖"), ("TESTH", "测试持")]
SB = lambda *p: os.path.join(SANDBOX, *p)

# ── Day1：信号日（应只排队，不成交）──
r1 = pc.run_portfolio_scan(STOCKS, lambda sym: _mkdf(day1, 10.5, 10.6), today_str=day1)

ints1_path = SB("data", "trade_intents", day1 + "_intents.json")
ints1 = json.load(open(ints1_path, encoding="utf-8"))
assert ints1["TESTB"]["status"] == "PENDING" and ints1["TESTB"]["action"] == "买入", ints1.get("TESTB")
assert ints1["TESTS"]["status"] == "PENDING" and ints1["TESTS"]["action"] == "卖出", ints1.get("TESTS")
assert "TESTH" not in ints1, "持有信号不应入队"
assert r1["trade_count"] == 0 if isinstance(r1.get("trade_count"), int) else True
_tr1 = SB("data", "portfolio_sim_trades.json")
assert (not os.path.exists(_tr1)) or json.load(open(_tr1, encoding="utf-8")) in ([], None), "Day1 禁止任何同bar成交"
_st1 = json.load(open(SB("data", "portfolio_sim_state.json"), encoding="utf-8"))
# 2026-11-06 契约更新：管道化模式不再写 last_signal_date（11-05 上午修复 11-04
# 晨间误扫占用 → 同日守卫吞单根因，仅 legacy 路径写）；断言它保持 None。
assert _st1.get("last_signal_date") is None, _st1.get("last_signal_date")
_audit_lines = [json.loads(l) for l in open(SB("audit", "trades_audit.jsonl"), encoding="utf-8") if l.strip()]
assert len(_audit_lines) == 3
_by1 = {e["symbol"]: e for e in _audit_lines[:3]}
assert _by1["TESTB"]["status"] == "QUEUED" and _by1["TESTS"]["status"] == "QUEUED"
assert _by1["TESTH"]["status"] in ("HOLD",)

# ── Day2：执行日（消费 PENDING → 开盘价成交；新信号被 dedup/空仓卖 拦截）──
r2 = pc.run_portfolio_scan(STOCKS, lambda sym: _mkdf(day2, 10.3, 10.4), today_str=day2)

_st2 = json.load(open(SB("data", "portfolio_sim_state.json"), encoding="utf-8"))
_b = _st2["positions"]["TESTB"]
assert _b["position"] is True and _b["entry_price"] == 10.3 and _b["entry_date"] == day2, _b
_s = _st2["positions"]["TESTS"]
assert _s["position"] is False and _s["shares"] == 0, _s
_trades = json.load(open(SB("data", "portfolio_sim_trades.json"), encoding="utf-8"))
assert len(_trades) == 2, _trades
_tb = next(t for t in _trades if t["symbol"] == "TESTB")
_ts = next(t for t in _trades if t["symbol"] == "TESTS")
assert _tb["action"] == "BUY" and _tb["price"] == 10.3 and _tb["date"] == day2
assert _ts["action"] == "SELL" and _ts["price"] == 10.3 and _ts["date"] == day2

ints1_after = json.load(open(ints1_path, encoding="utf-8"))
assert ints1_after["TESTB"]["status"] == "FILLED" and abs(ints1_after["TESTB"]["fill"]["gap_pct"] - (10.3 / 10.6 - 1)) < 1e-9
assert ints1_after["TESTS"]["status"] == "FILLED"
assert os.path.exists(ints1_path + ".bak-" + day2), "回写前当日 .bak 缺失"

ints2_path = SB("data", "trade_intents", day2 + "_intents.json")
ints2 = json.load(open(ints2_path, encoding="utf-8")) if os.path.exists(ints2_path) else {}
assert "TESTB" not in ints2, "执行日已成交，同日新信号应被 dedup 拦截不入队"
assert "TESTS" not in ints2, "空仓卖出不入队"
assert "TESTH" not in ints2

_audit_lines = [json.loads(l) for l in open(SB("audit", "trades_audit.jsonl"), encoding="utf-8") if l.strip()]
assert len(_audit_lines) == 6
_by2 = {e["symbol"]: e for e in _audit_lines[3:]}
assert str(_by2["TESTB"]["guards"].get("auto_exec", "")).startswith("FILLED@open"), _by2["TESTB"]["guards"]
assert str(_by2["TESTS"]["guards"].get("auto_exec", "")).startswith("FILLED@open"), _by2["TESTS"]["guards"]

_equity = open(SB("data", "portfolio_equity.csv"), encoding="utf-8").read().strip().splitlines()
assert any(l.startswith(day1) for l in _equity) and any(l.startswith(day2) for l in _equity), _equity

# ── 生产零污染 ──
_after = {p: _sha(p) for p in PROD_GUARDS}
_touched = [p for p in PROD_GUARDS if _before[p] != _after[p]]
assert not _touched, f"生产文件被触碰: {_touched}"

shutil.rmtree(SANDBOX, ignore_errors=True)
print("✅ 全流程 e2e 全绿：Day1 排队(2 PENDING/持有不入队/零成交) → Day2 开盘成交(2 FILLED/"
      "dedup 拦截再排队/空仓卖跳过) + equity 两日行 + 生产 sha256 零变化")
