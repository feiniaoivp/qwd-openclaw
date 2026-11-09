#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_executor 零污染 e2e（2026-11-04 B-管道化落地验证）

场景：①买意向按执行日开盘价成交（滑点+gap 留痕） ②空仓卖 CANCELLED
③超龄 EXPIRED ④数据滞后保持 PENDING ⑤执行日 dedup BLOCKED ⑥回写+当日.bak
＋legacy 开关语义 ＋生产文件 sha256 前后零变化（value-level 防线，
吸取 2026-11-04 凌晨「size 比对对同长度值变更失明」教训）
"""
import os
import sys
import json
import hashlib
import shutil
import tempfile
from datetime import datetime, timedelta

PROD_WS = "/Users/duguke/.openclaw/workspace"
_today = datetime.now().strftime("%Y-%m-%d")
PROD_GUARDS = [
    os.path.join(PROD_WS, "data", "portfolio_sim_state.json"),
    os.path.join(PROD_WS, "data", "portfolio_sim_trades.json"),
    os.path.join(PROD_WS, "data", "portfolio_equity.csv"),
    os.path.join(PROD_WS, "data", "trade_intents", _today + "_intents.json"),
]


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None


_before = {p: _sha(p) for p in PROD_GUARDS}

SANDBOX = tempfile.mkdtemp(prefix="autoexec_e2e_")
os.environ["WORKSPACE"] = SANDBOX
os.environ["AUTO_EXECUTOR"] = "1"
os.makedirs(os.path.join(SANDBOX, "data", "trade_intents"), exist_ok=True)
# 2026-11-06 修复：11-05 data/runtime 迁移后 auto_executor.INTENTS_DIR 指向
# data/runtime/trade_intents（生产经 data/runtime→data 符号链接与 data/trade_intents 同 inode）。
# 沙箱必须复刻该 symlink，否则 load_pending_intents 读空 → e2e 假失败。
_rt = os.path.join(SANDBOX, "data", "runtime")
if not os.path.lexists(_rt):
    os.symlink(os.path.join(SANDBOX, "data"), _rt)
_src_cfg = os.path.join(PROD_WS, "data", "sim_aggressive_config.json")
if os.path.exists(_src_cfg):
    shutil.copy2(_src_cfg, os.path.join(SANDBOX, "data", "sim_aggressive_config.json"))

yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
old_date = (datetime.now() - timedelta(days=9)).strftime("%Y-%m-%d")


def _pending(action, price, reason):
    return {"name": "x", "action": action, "reason": reason, "price": price,
            "bucket": "core", "strategy": "macd", "atr": 0.2, "atr_stop": price * 0.9,
            "fib_tp": {}, "created_at": yesterday + "T15:40:00", "status": "PENDING"}


_fy = os.path.join(SANDBOX, "data", "trade_intents", yesterday + "_intents.json")
with open(_fy, "w", encoding="utf-8") as f:
    json.dump({
        "TESTBUY1": _pending("买入", 10.0, "e2e 买"),
        "TESTSELL1": _pending("卖出", 10.0, "e2e 卖"),
        "TESTSTALE1": _pending("买入", 10.0, "e2e 滞后"),
        "TESTDEDUP1": _pending("买入", 10.0, "e2e dedup"),
    }, f, ensure_ascii=False)
_fo = os.path.join(SANDBOX, "data", "trade_intents", old_date + "_intents.json")
with open(_fo, "w", encoding="utf-8") as f:
    json.dump({"TESTOLD1": _pending("买入", 10.0, "e2e 超龄")}, f, ensure_ascii=False)

sys.path.insert(0, "/Users/duguke/.openclaw/workspace")
import pandas as pd  # noqa: E402
from analysis import auto_executor  # noqa: E402
from analysis import portfolio_core as pc  # noqa: E402
import analysis.three_factor_helper as tfh  # noqa: E402


class _StubGate:
    def check_buy(self, sym):
        return True, {"reason": "e2e-stub"}


tfh.ResonanceGate = _StubGate

_CTX = {
    "pending_index": auto_executor.load_pending_intents(_today),
    "execute_trade": pc.execute_trade,
    "check_dedup": pc.RiskGuard.check_dedup,
    "position_plan": {"buy_signal_multiplier": 1.0, "single_stock_max_amount": 100000.0},
    "max_age_days": 5,
}
assert set(_CTX["pending_index"].keys()) == {"TESTBUY1", "TESTSELL1", "TESTOLD1", "TESTSTALE1", "TESTDEDUP1"}, _CTX["pending_index"].keys()


def _mk_df(open_price, close_price, date_str, n=70):
    rows = []
    for i in range(n - 1, 0, -1):
        d = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=i)
        rows.append({"date": d, "open": 10.0, "high": 10.2, "low": 9.8,
                     "close": 10.0, "volume": 1e6, "amount": 1e7})
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    rows.append({"date": dt, "open": open_price, "high": max(open_price, close_price) + 0.1,
                 "low": min(open_price, close_price) - 0.1, "close": close_price,
                 "volume": 2e6, "amount": 2e7})
    df = pd.DataFrame(rows)
    df = df.set_index("date")
    return df


def _mk_pos(sym, position=False, cash=100000.0):
    return {"name": sym, "_symbol": sym, "strategy": "macd", "bucket": "core",
            "position": position, "shares": 100 if position else 0,
            "cash": cash, "entry_price": 10.0 if position else 0.0,
            "entry_date": yesterday if position else None, "trade_count": 0,
            "_last_atr": 0.2, "_last_amount": 2e7}


df_today = _mk_df(10.5, 10.6, _today)

# ① 买意向 → 开盘价成交
pos_buy = _mk_pos("TESTBUY1")
act, msg = auto_executor.consume_pending_for_symbol(_CTX, "TESTBUY1", "测试买", pos_buy, df_today, _today, strat="macd")
assert act == "BUY", (act, msg)
assert pos_buy["position"] is True and pos_buy["shares"] >= 100
assert pos_buy["entry_price"] == 10.5, pos_buy["entry_price"]
assert pos_buy.get("entry_slippage", 0) > 0 and pos_buy["cash"] < 100000.0
_e1 = _CTX["pending_index"]["TESTBUY1"][-1]["intent"]
assert _e1["status"] == "FILLED" and abs(_e1["fill"]["gap_pct"] - 0.05) < 1e-9, _e1
_trades_path = os.path.join(SANDBOX, "data", "portfolio_sim_trades.json")
_trades = json.load(open(_trades_path, encoding="utf-8"))
assert _trades and _trades[-1]["action"] == "BUY" and _trades[-1]["date"] == _today, _trades[-1]

# ② 空仓卖 → CANCELLED
pos_sell = _mk_pos("TESTSELL1", position=False)
act2, _ = auto_executor.consume_pending_for_symbol(_CTX, "TESTSELL1", "测试卖", pos_sell, df_today, _today, strat="macd")
assert act2 is None
assert _CTX["pending_index"]["TESTSELL1"][-1]["intent"]["status"] == "CANCELLED"

# ③ 超龄 → EXPIRED
pos_old = _mk_pos("TESTOLD1")
act3, _ = auto_executor.consume_pending_for_symbol(_CTX, "TESTOLD1", "测试老", pos_old, df_today, _today, strat="macd")
assert act3 is None
assert _CTX["pending_index"]["TESTOLD1"][-1]["intent"]["status"] == "EXPIRED"

# ④ 数据滞后 → 保持 PENDING（留待正式扫描）
df_stale = _mk_df(10.5, 10.6, yesterday)
pos_stale = _mk_pos("TESTSTALE1")
act4, _ = auto_executor.consume_pending_for_symbol(_CTX, "TESTSTALE1", "测试滞", pos_stale, df_stale, _today, strat="macd")
assert act4 is None
assert _CTX["pending_index"]["TESTSTALE1"][-1]["intent"]["status"] == "PENDING"

# ⑤ 执行日 dedup → BLOCKED
pos_dd = _mk_pos("TESTDEDUP1")
pos_dd["_last_action"] = "买入"
pos_dd["_last_action_date"] = _today
act5, _ = auto_executor.consume_pending_for_symbol(_CTX, "TESTDEDUP1", "测试重", pos_dd, df_today, _today, strat="macd")
assert act5 is None
assert _CTX["pending_index"]["TESTDEDUP1"][-1]["intent"]["status"] == "BLOCKED"

# ⑥ 回写 + 当日 .bak + 持久化断言
auto_executor.flush_intent_updates(_CTX["pending_index"],
                                   visited_syms={"TESTBUY1", "TESTSELL1", "TESTOLD1", "TESTSTALE1", "TESTDEDUP1"},
                                   today_str=_today)
assert os.path.exists(_fy + ".bak-" + _today), "回写前 .bak 缺失"
_re = json.load(open(_fy, encoding="utf-8"))
assert _re["TESTBUY1"]["status"] == "FILLED" and _re["TESTSELL1"]["status"] == "CANCELLED"
assert _re["TESTSTALE1"]["status"] == "PENDING"
assert json.load(open(_fo, encoding="utf-8"))["TESTOLD1"]["status"] == "EXPIRED"

# ⑦ legacy 开关
os.environ["AUTO_EXECUTOR"] = "0"
assert auto_executor.auto_executor_enabled() is False
os.environ["AUTO_EXECUTOR"] = "1"
assert auto_executor.auto_executor_enabled() is True

# 生产零污染：sha256 前后对照（value-level）
_after = {p: _sha(p) for p in PROD_GUARDS}
_touched = [p for p in PROD_GUARDS if _before[p] != _after[p]]
assert not _touched, f"生产文件被触碰: {_touched}"

shutil.rmtree(SANDBOX, ignore_errors=True)
print("✅ e2e 全绿：7 场景通过（成交/取消/过期/滞后/去重/回写/开关），生产文件 sha256 零变化")
