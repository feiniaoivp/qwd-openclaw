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

INTENTS_DIR = os.path.join(WORKSPACE, "data", "runtime", "trade_intents")
REGIME_T1_PATH = os.path.join(WORKSPACE, "data", "runtime", "regime_t1_config.json")


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
        data_date = df.index[-1].strftime("%Y-%m-%d")
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

    # 政权权重仓位调整（2026-11-08 任务1）：desired_positions 单票上限按政权缩放。
    # 优先级：意向上的信号日政权章 > ctx.regime_t1_day（T 日落盘 day 兜底）> 无（fail-open 原样）
    _plan = ctx.get("position_plan") or {}
    _intent_regime = intent.get("regime")
    if isinstance(_intent_regime, dict) and _intent_regime.get("max_position_pct"):
        _regime_cfg, _regime_src = _intent_regime, "intent"
    else:
        _day_regime = ctx.get("regime_t1_day")
        if isinstance(_day_regime, dict) and _day_regime.get("max_position_pct"):
            _regime_cfg, _regime_src = _day_regime, "day"
        else:
            _regime_cfg, _regime_src = None, None
    if _regime_cfg:
        _plan = apply_regime_to_position_plan(_plan, _regime_cfg)
        if audit_entry is not None:
            audit_entry["guards"]["regime_sizing"] = (
                f"{_regime_cfg.get('regime')} mpp={_regime_cfg.get('max_position_pct')} "
                f"ratio={_plan.get('regime_sizing_ratio')} src={_regime_src}")
    action, msg = ctx["execute_trade"](pos, fill_signal, today_str,
                                       strategy=strat or intent.get("strategy"),
                                       position_plan=_plan)
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


def build_pending_intent(name: str, signal: dict, pos: dict, strat, now_iso: str,
                         regime_t1: dict = None) -> dict:
    """信号日排队：由 run_portfolio_scan 的门控后信号构建 PENDING 意向（字段向后兼容扩充）。

    regime_t1（2026-11-08 任务1）：信号日 T 收盘生成的 T+1 政权配置 → 盖章到意向，
    T+1 开盘成交时 consume_pending_for_symbol 按其调整单票仓位上限。
    """
    intent = {
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
    if isinstance(regime_t1, dict) and regime_t1.get("regime"):
        intent["regime"] = {
            "name": regime_t1.get("regime"),
            "signal_date": regime_t1.get("signal_date"),
            "max_position_pct": regime_t1.get("max_position_pct"),
            "stop_loss_pct": regime_t1.get("stop_loss_pct"),
            "take_profit_pct": regime_t1.get("take_profit_pct"),
            "position_sizing": regime_t1.get("position_sizing"),
            "factor_weights": regime_t1.get("factor_weights") or {},
        }
    return intent


# ═════════════════════════════════════════════════════════════
# 政权分配器接入（2026-11-08 任务1）：T 日收盘 → T+1 政权权重配置
# 数据流：run_portfolio_scan（T 日收盘扫描）→ prepare_regime_t1（调
# regime_allocator.generate_next_day_config）→ a) 落盘 REGIME_T1_PATH 供
# T+1 day 级兜底；b) 精简章随 PENDING 意向落库；T+1 开盘成交时
# consume_pending_for_symbol 按章调整单票仓位上限（fail-open 分级回退）。
# ═════════════════════════════════════════════════════════════

_BASELINE_MAX_POS_PCT = 0.10   # RiskGuard 基准单票上限（默认 10 万 / 初始资金 100 万）≈ 10%，政权缩放以此为 1.0
_REGIME_MIN_SYMS = 5           # 有效样本少于该数不生成配置（禁止缓存空值/小样本结论）
_REGIME_MIN_ROWS = 120         # 每只最少行数（覆盖 ma_60/ret_20/vol_60 特征回看窗口）


def _normalize_regime_df(df):
    """把扫描行情 DataFrame 归一成 regime_allocator 可消费形态（日期索引 + close/volume）。

    返回 None = 该样本不可用（调用方跳过）。
    """
    import pandas as pd
    if df is None or not isinstance(df, pd.DataFrame) or len(df) < _REGIME_MIN_ROWS:
        return None
    d = df
    if "date" in d.columns:
        try:
            d = d.set_index("date")
        except Exception as e:
            print(f"⚠️ [regime] date 列索引化失败（跳过该样本）: {e}")
            return None
    cols = {str(c).lower(): c for c in d.columns}
    if "close" not in cols or "volume" not in cols:
        return None
    out = pd.DataFrame({
        "close": pd.to_numeric(d[cols["close"]], errors="coerce"),
        "volume": pd.to_numeric(d[cols["volume"]], errors="coerce"),
    }).dropna()
    return out if len(out) >= _REGIME_MIN_ROWS else None


def build_regime_t1_config(dfs: dict, signal_date: str = None, method: str = "both") -> dict:
    """T 日收盘后：用当日全池 K 线生成 T+1 政权权重配置并落盘。

    - 数据不足（< _REGIME_MIN_SYMS 只有效样本）→ 返回 None 且**不写文件**（禁止缓存空值）
    - 落盘：原子写（tmp + os.replace）；同 signal_date 首次重写前留 .bak-{signal_date} 备份
    - fail-open：任何异常返回 None 并告警，不阻断扫描
    """
    valid = {}
    for sym, df in (dfs or {}).items():
        nd = _normalize_regime_df(df)
        if nd is not None:
            valid[sym] = nd
    if len(valid) < _REGIME_MIN_SYMS:
        print(f"⚠️ [regime] 有效样本不足 ({len(valid)}/{_REGIME_MIN_SYMS})，跳过 T+1 政权配置生成（不落盘）")
        return None
    try:
        from analysis.regime_allocator import generate_next_day_config
        cfg = generate_next_day_config(valid, method=method)
    except Exception as e:
        print(f"⚠️ [regime] generate_next_day_config 失败（fail-open）: {e}")
        return None
    if not isinstance(cfg, dict) or not cfg.get("regime"):
        print(f"⚠️ [regime] 政权配置输出异常（{type(cfg).__name__}），不落盘")
        return None
    cfg["signal_date"] = signal_date or datetime.now().strftime("%Y-%m-%d")
    cfg["generated_at"] = datetime.now().isoformat()
    cfg["n_symbols"] = len(valid)
    try:
        os.makedirs(os.path.dirname(REGIME_T1_PATH), exist_ok=True)
        bak = f"{REGIME_T1_PATH}.bak-{cfg['signal_date']}"
        if os.path.exists(REGIME_T1_PATH) and not os.path.exists(bak):
            shutil.copy2(REGIME_T1_PATH, bak)
        tmp = REGIME_T1_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, REGIME_T1_PATH)
        print(f"🌦️ [regime] T+1 政权配置已落盘: regime={cfg['regime']} "
              f"(signal_date={cfg['signal_date']}, {len(valid)} 只样本)")
    except Exception as e:
        print(f"⚠️ [regime] 政权配置落盘失败（本次内存中仍可用）: {e}")
    return cfg


def load_regime_t1_config(today_str: str = None) -> dict:
    """读取最近一份政权配置作为 T+1 day 级兜底。

    防未来函数：仅允许 signal_date 严格早于 today_str（当日/未来配置拒绝，当日意向
    用 prepare_regime_t1 的内存返回值，不走此文件）。
    """
    if not os.path.exists(REGIME_T1_PATH):
        return None
    try:
        with open(REGIME_T1_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"⚠️ [regime] 政权配置读取失败: {e}")
        return None
    if not isinstance(cfg, dict) or not cfg.get("regime"):
        return None
    sig = _parse_date(cfg.get("signal_date"))
    if sig is None:
        return None
    today = _parse_date(today_str) if today_str else None
    if today is not None and sig >= today:
        print(f"⚠️ [regime] 配置 signal_date={cfg.get('signal_date')} 不早于 today={today_str}，拒绝（防未来函数）")
        return None
    return cfg


def apply_regime_to_position_plan(position_plan: dict, regime_cfg: dict) -> dict:
    """用政权权重调整 T+1 仓位计划（desired_positions 单票上限缩放）。

    - 缩放比 = regime max_position_pct / 基准 0.10，夹在 [0.3, 1.5]（防极端政权打到 0/爆表）
    - regime_cfg 空/缺 max_position_pct → 返回原计划浅拷贝（fail-open，零调整）
    - 仅改已被 execute_trade 消费的键（single_stock_max_amount）+ 透出审计字段
      regime_name / regime_max_position_pct / regime_sizing_ratio
    """
    out = dict(position_plan or {})
    if not isinstance(regime_cfg, dict):
        return out
    try:
        mpp = float(regime_cfg.get("max_position_pct") or 0)
    except (TypeError, ValueError):
        mpp = 0.0
    if mpp <= 0:
        return out
    ratio = max(0.3, min(1.5, mpp / _BASELINE_MAX_POS_PCT))
    try:
        base_amt = float(out.get("single_stock_max_amount") or 100_000)
    except (TypeError, ValueError):
        base_amt = 100_000.0
    out["single_stock_max_amount"] = round(base_amt * ratio, 2)
    out["regime_name"] = regime_cfg.get("regime")
    out["regime_max_position_pct"] = mpp
    out["regime_sizing_ratio"] = round(ratio, 4)
    return out


def prepare_regime_t1(dfs: dict, signal_date: str = None, method: str = "both") -> dict:
    """run_portfolio_scan 意向创建前的单次入口：生成并落盘 T+1 政权配置，返回盖章用精简配置。

    返回 None = 生成失败/样本不足（调用方 fail-open：意向不盖政权章）。
    """
    cfg = build_regime_t1_config(dfs, signal_date=signal_date, method=method)
    if not cfg:
        return None
    return {
        "regime": cfg.get("regime"),
        "regime_probability": cfg.get("regime_probability"),
        "signal_date": cfg.get("signal_date"),
        "max_position_pct": cfg.get("max_position_pct"),
        "stop_loss_pct": cfg.get("stop_loss_pct"),
        "take_profit_pct": cfg.get("take_profit_pct"),
        "position_sizing": cfg.get("position_sizing"),
        "factor_weights": cfg.get("factor_weights") or {},
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
    # 政权仓位调整纯逻辑自检（2026-11-08 任务1，零生产接触）
    _p = apply_regime_to_position_plan({"single_stock_max_amount": 100_000}, None)
    assert _p == {"single_stock_max_amount": 100_000}
    _p = apply_regime_to_position_plan({"single_stock_max_amount": 100_000},
                                       {"regime": "trend_up", "max_position_pct": 0.12})
    assert abs(_p["single_stock_max_amount"] - 120_000) < 1e-6 and _p["regime_name"] == "trend_up"
    _p = apply_regime_to_position_plan({"single_stock_max_amount": 100_000},
                                       {"regime": "trend_down", "max_position_pct": 0.05})
    assert abs(_p["single_stock_max_amount"] - 50_000) < 1e-6
    _i = build_pending_intent("X", {"action": "买入", "reason": "r", "price": 1.0}, {}, "ema_cross",
                              "2026-01-01T00:00:00",
                              regime_t1={"regime": "sideways", "signal_date": "2026-01-01",
                                         "max_position_pct": 0.08, "factor_weights": {"A": 1.0}})
    assert _i["status"] == "PENDING" and _i["regime"]["name"] == "sideways"
    print("✅ auto_executor 自检通过（开关/加载接口/政权仓位调整，零生产接触）")
