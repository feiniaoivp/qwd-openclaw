#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_regime_executor_integration.py — 政权分配器→auto_executor 接入端到端验证（2026-11-08 任务1）

模拟数据全链路（零生产接触：意向目录/配置路径全部指向 tempfile 临时目录）：
  阶段1:   T 日收盘后 build_regime_t1_config 生成并落盘 T+1 政权配置
           （含异常样本过滤 / 幂等重跑 .bak / 样本不足拒写「不缓存空值」）
  阶段1b:  生产默认 method="both" 冒烟（hmmlearn 缺失自动回退阈值法）
  阶段2:   T+1 load_regime_t1_config 读取 + 防未来函数（当日/更早 signal_date 拒绝）
  阶段3:   build_pending_intent 意向政权盖章（有章/无章兼容）
  阶段4:   consume_pending_for_symbol T+1 开盘成交按政权权重调整单票上限
           （intent 章 > day 兜底 > fail-open 原样 三级回退）
  阶段5:   flush_intent_updates 回写 FILLED + .bak 备份
"""
import os
import sys
import json
import shutil
import tempfile

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
# 脚本模式 sys.path 样板（TOOLS.md 强制）：工作区根 + analysis/
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd
from datetime import datetime

from analysis import auto_executor
from analysis.auto_executor import (
    build_regime_t1_config, load_regime_t1_config, apply_regime_to_position_plan,
    build_pending_intent, consume_pending_for_symbol, flush_intent_updates,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" | {detail}" if detail else ""))


def make_mock_dfs(n_syms=20, n_days=500, seed=42):
    """模拟带政权切换的全池 K 线（形态仿真实扫描：date 列 + RangeIndex）。"""
    dates = pd.date_range("2024-01-01", periods=n_days, freq="D")
    np.random.seed(seed)
    data = {}
    for i in range(n_syms):
        sym = str(600000 + i)
        seg = n_days // 4
        rets = np.concatenate([
            np.random.randn(seg) * 0.01 + 0.0008,   # trend_up
            np.random.randn(seg) * 0.015 - 0.0005,  # high_vol
            np.random.randn(seg) * 0.01 - 0.0008,   # trend_down
            np.random.randn(seg) * 0.005,           # sideways
        ])
        close = 100 * np.exp(np.cumsum(rets))
        n = len(rets)
        high = close * (1 + np.abs(np.random.randn(n) * 0.01))
        low = close * (1 - np.abs(np.random.randn(n) * 0.01))
        open_ = close * (1 + np.random.randn(n) * 0.005)
        volume = np.abs(np.random.randn(n) * 1e6 + 5e6)
        data[sym] = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                                  "volume": volume, "date": dates[:n]})
    return data


def main():
    tmp = tempfile.mkdtemp(prefix="regime_exec_test_")
    _orig_dir, _orig_path = auto_executor.INTENTS_DIR, auto_executor.REGIME_T1_PATH
    auto_executor.INTENTS_DIR = os.path.join(tmp, "intents")
    auto_executor.REGIME_T1_PATH = os.path.join(tmp, "regime_t1_config.json")
    os.makedirs(auto_executor.INTENTS_DIR, exist_ok=True)
    SIGNAL_DAY, EXEC_DAY = "2026-01-08", "2026-01-09"
    try:
        dfs = make_mock_dfs()
        print("== 阶段1: T 日收盘后生成 T+1 政权配置 ==")
        dfs["BAD"] = ValueError("模拟取数失败")  # 仿真实扫描的异常混入
        cfg = build_regime_t1_config(dfs, signal_date=SIGNAL_DAY, method="threshold")
        check("配置生成成功(含异常值过滤)", cfg is not None)
        check("异常样本被剔除 n_symbols=20", bool(cfg) and cfg.get("n_symbols") == 20,
              str(cfg and cfg.get("n_symbols")))
        check("regime 合法", bool(cfg) and cfg.get("regime") in {"trend_up", "trend_down", "sideways", "high_vol"},
              str(cfg and cfg.get("regime")))
        check("max_position_pct 合法", bool(cfg) and 0.04 <= cfg.get("max_position_pct", 0) <= 0.13,
              str(cfg and cfg.get("max_position_pct")))
        fw = (cfg or {}).get("factor_weights") or {}
        _sum = sum(abs(v) for v in fw.values())
        check("因子权重已归一(Σ|w|≈1)", abs(_sum - 1.0) < 1e-6, f"sum={_sum:.4f}")
        check("配置落盘", os.path.exists(auto_executor.REGIME_T1_PATH))

        cfg2 = build_regime_t1_config(dfs, signal_date=SIGNAL_DAY, method="threshold")
        baks = [f for f in os.listdir(tmp) if f.startswith("regime_t1_config.json.bak")]
        check("同日重跑幂等+.bak 备份", cfg2 is not None and len(baks) == 1, f"baks={baks}")

        cfg_empty = build_regime_t1_config({"s1": dfs["600000"].head(10)},
                                           signal_date=SIGNAL_DAY, method="threshold")
        check("样本不足拒生成(不缓存空值,不覆盖好文件)", cfg_empty is None)

        print("== 阶段1b: 生产默认 method='both' 冒烟 ==")
        cfg_both = build_regime_t1_config(dfs, signal_date=SIGNAL_DAY, method="both")
        check("both 模式冒烟(HMM 缺失自动回退阈值法)", bool(cfg_both) and bool(cfg_both.get("regime")),
              str(cfg_both and cfg_both.get("regime")))
        # 恢复确定性 threshold 配置（后续阶段依赖一致的 mpp；同日 .bak 已存在不再新增）
        cfg = build_regime_t1_config(dfs, signal_date=SIGNAL_DAY, method="threshold")
        check("恢复 threshold 配置", bool(cfg) and cfg.get("regime") in {"trend_up", "trend_down", "sideways", "high_vol"})

        print("== 阶段2: T+1 读取 T 日配置 ==")
        loaded = load_regime_t1_config(EXEC_DAY)
        check("T+1 读取成功", bool(loaded) and loaded.get("signal_date") == SIGNAL_DAY)
        check("当日配置拒绝(防未来)", load_regime_t1_config(SIGNAL_DAY) is None)
        check("早于 signal_date 拒绝(防未来)", load_regime_t1_config("2026-01-01") is None)

        print("== 阶段3: 意向政权盖章 ==")
        signal = {"action": "买入", "reason": "测试信号", "price": 100.0, "atr": 2.0}
        stamped = build_pending_intent("测试股A", signal, {"bucket": "tier1"}, "ema_cross",
                                       datetime.now().isoformat(), regime_t1=cfg)
        check("盖章意向含 regime", stamped.get("status") == "PENDING"
              and (stamped.get("regime") or {}).get("name") == cfg.get("regime"))
        plain = build_pending_intent("测试股B", signal, {"bucket": "tier1"}, "ema_cross",
                                     datetime.now().isoformat())
        check("无章意向兼容(无 regime 键)", "regime" not in plain)
        with open(os.path.join(auto_executor.INTENTS_DIR, f"{SIGNAL_DAY}_intents.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"600000": stamped, "600001": plain, "600002": plain}, f, ensure_ascii=False)

        print("== 阶段4: T+1 开盘成交（政权权重调整单票上限）==")
        df = pd.DataFrame({"open": [101.0], "high": [102.0], "low": [100.5], "close": [101.5],
                           "volume": [1e6]},
                          index=pd.DatetimeIndex([pd.Timestamp(EXEC_DAY)]))
        captured = {}

        def fake_execute(p, s, d, strategy=None, position_plan=None):
            captured[s["_symbol"]] = dict(position_plan or {})
            return "BUY", "模拟成交"

        base_plan = {"single_stock_max_amount": 100_000, "buy_signal_multiplier": 1.0}
        ctx = {"pending_index": auto_executor.load_pending_intents(EXEC_DAY),
               "execute_trade": fake_execute, "check_dedup": lambda s, p, d: True,
               "position_plan": base_plan, "regime_t1_day": loaded, "max_age_days": 5}
        ratio = max(0.3, min(1.5, cfg["max_position_pct"] / 0.10))
        check("意向索引加载(3 笔)", len(ctx["pending_index"]) == 3, str(sorted(ctx["pending_index"])))

        act, _ = consume_pending_for_symbol(ctx, "600000", "测试股A", {}, df, EXEC_DAY)
        check("盖章意向成交 FILLED", act == "BUY")
        got = captured.get("600000", {})
        check("单票上限按政权缩放(intent 章)",
              abs(got.get("single_stock_max_amount", 0) - 100_000 * ratio) < 1e-6,
              f"mpp={cfg['max_position_pct']} ratio={got.get('regime_sizing_ratio')}")

        act, _ = consume_pending_for_symbol(ctx, "600001", "测试股B", {}, df, EXEC_DAY)
        got = captured.get("600001", {})
        check("无章意向回退 day 配置(同比例)", act == "BUY"
              and abs(got.get("single_stock_max_amount", 0) - 100_000 * ratio) < 1e-6,
              f"ratio={got.get('regime_sizing_ratio')}")

        ctx["regime_t1_day"] = None
        act, _ = consume_pending_for_symbol(ctx, "600002", "测试股B", {}, df, EXEC_DAY)
        got = captured.get("600002", {})
        check("无任何政权配置 fail-open 原样", act == "BUY"
              and abs(got.get("single_stock_max_amount", 0) - 100_000) < 1e-6
              and "regime_name" not in got)

        print("== 阶段5: 意向回写 ==")
        flush_intent_updates(ctx["pending_index"], visited_syms={"600000", "600001", "600002"},
                             today_str=EXEC_DAY)
        with open(os.path.join(auto_executor.INTENTS_DIR, f"{SIGNAL_DAY}_intents.json"), "r",
                  encoding="utf-8") as f:
            data = json.load(f)
        check("3 笔意向 FILLED", all(data[s].get("status") == "FILLED" for s in ("600000", "600001", "600002")),
              str({s: data[s].get("status") for s in data}))
        check("成交价=T+1 开盘价 101.0", data["600000"].get("fill", {}).get("price") == 101.0)
        ibaks = [f for f in os.listdir(auto_executor.INTENTS_DIR) if ".bak" in f]
        check("回写前 .bak 备份", len(ibaks) >= 1, str(ibaks))
    finally:
        auto_executor.INTENTS_DIR, auto_executor.REGIME_T1_PATH = _orig_dir, _orig_path
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== 结果: PASS={len(PASS)} FAIL={len(FAIL)} =====")
    if FAIL:
        print("失败项: " + "; ".join(FAIL))
        sys.exit(1)
    print("✅ 政权分配器→auto_executor 全链路验证通过")


if __name__ == "__main__":
    main()
