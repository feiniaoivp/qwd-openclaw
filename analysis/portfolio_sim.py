#!/usr/bin/env python3
"""
全组合多股模拟盘 (Portfolio Simulator) - 精简版
===================================================
核心逻辑已迁移至 analysis/portfolio_core.py，供模拟盘/实盘/回测复用。
本脚本仅负责：数据获取、调用核心扫描、输出报告。

已统一使用 analysis.data_layer.router.DataRouter (Phase 1 完成)
"""

import os, sys, json, warnings
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import pandas as pd
import pandas_ta as ta
import numpy as np

warnings.filterwarnings("ignore")

# 导入统一数据路由器
from analysis.data_layer.router import get_router

# 导入核心模块
from analysis.portfolio_core import (
    generate_human_report,
    fetch_today_realtime,
    STOCKS as CORE_STOCKS,
    STRATEGY_LABELS,
    COMMISSION, SLIPPAGE, INITIAL_CAPITAL, ATR_STOP_MULT,
)
# 导入统一执行器（单一事实来源：信号/风控/仓位全部复用 portfolio_core）
from analysis.executor_bridge import run_portfolio_scan_via_executor
import time
from datetime import time as dtime

# 交易时段守卫（带开盘缓冲，避免开盘前几分钟抓到 stale price）
TRADING_HOURS = [
    (dtime(9, 30), dtime(11, 30)),   # 上午
    (dtime(13, 0), dtime(15, 0)),    # 下午
]

# 开盘缓冲分钟数：开盘后前 N 分钟不信任实时行情
OPEN_BUFFER_MINUTES = 15

def is_trading_time(dt=None) -> bool:
    """判断是否处于 A 股交易时段（含午休判断 + 开盘缓冲）"""
    if dt is None:
        dt = datetime.now()
    if dt.weekday() >= 5:  # 周末
        return False
    t = dt.time()
    for start, end in TRADING_HOURS:
        # 开盘缓冲：开盘后前 OPEN_BUFFER_MINUTES 分钟不使用实时行情
        buffer_end = dtime(start.hour, start.minute + OPEN_BUFFER_MINUTES)
        if buffer_end <= t <= end:
            return True
    return False

OUTPUT_DIR = os.path.join(WORKSPACE, "analysis", "daily")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 使用核心模块的股票列表（已同步 35 只）
STOCKS = CORE_STOCKS


# ═══════════════════════════════════════════
# 数据获取 — 使用 DataRouter (Phase 1)
# ═══════════════════════════════════════════

_router_instance = None

def get_router_instance():
    global _router_instance
    if _router_instance is None:
        _router_instance = get_router()
    return _router_instance


def fetch_data(symbol, start="20250101", max_retry=3, realtime_fallback=True):
    """
    统一历史日K获取 - 走 DataRouter (内含降级链: 新浪日K -> pytdx -> baostock -> akshare)
    实时行情回补 - 走 DataRouter.get_spot()
    """
    router = get_router_instance()
    
    # 1. 历史日线
    try:
        df = router.get_daily(symbol, "", start_date=start)
        if df is not None and len(df) >= 2:
            return df
    except Exception as e:
        print(f"  ⚠️ {symbol} DataRouter历史获取失败: {e}")
    
    # 2. 兜底：仅在交易时段使用实时行情
    if realtime_fallback and is_trading_time():
        rt = fetch_today_realtime([symbol])
        if symbol in rt:
            r = rt[symbol]
            return pd.DataFrame([{
                "date": pd.Timestamp(r["date"]),
                "open": r["open"], "close": r["close"],
                "high": r["high"], "low": r["low"], "volume": r["volume"],
            }])
    return None


# ═══════════════════════════════════════════
# 成交即通知（2026-09-29 新增）
# 背景：此前卖出成交只落盘/写日报，无任何即时推送 →
#   用户「雷科防务 09-24 卖出」事后才知道。补上卖出/买入成交通知。
# ═══════════════════════════════════════════

_NOTIFY_STATE_FILE = os.path.join(WORKSPACE, "data", "portfolio_trade_notified.json")


def _load_notified() -> dict:
    """读取「已推送成交」幂等状态：{date: {msg_hash: True}}"""
    if os.path.exists(_NOTIFY_STATE_FILE):
        try:
            with open(_NOTIFY_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_notified(data: dict):
    with open(_NOTIFY_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def notify_trades(today_str: str, messages: list, portfolio: dict) -> int:
    """成交即通知：把当日成交（买入/卖出）单独推送到 Telegram。

    幂等：同名/同日/同内容消息不重复推送。
    返回：实际推送的成交条数（0 表示无成交或均已推送过）。
    """
    import hashlib
    if not messages:
        return 0

    try:
        from send_telegram import send_with_retry
    except Exception as e:
        print(f"⚠️ 成交通知：导入 send_telegram 失败: {e}")
        return 0

    notified = _load_notified()
    day_state = notified.setdefault(today_str, {})

    new_msgs = []
    for m in messages:
        h = hashlib.sha1(m.encode("utf-8")).hexdigest()[:16]
        if h in day_state:
            continue
        day_state[h] = True
        new_msgs.append(m)

    if not new_msgs:
        return 0

    t = portfolio or {}
    lines = [
        f"💼 **模拟盘成交通知** 🕒 {today_str}",
        f"组合总资产 ¥{t.get('total_value', 0):,.0f} ({t.get('total_return_pct', 0):+.2f}%)",
        "",
    ]
    lines.extend(f"• {m}" for m in new_msgs)
    text = "\n".join(lines)

    ok = send_with_retry(text, parse_mode="")  # 纯文本，避免 Markdown 实体解析 400
    if ok.get("ok"):
        _save_notified(notified)
        print(f"🔔 成交通知已推送：{len(new_msgs)} 笔")
    else:
        # 推送失败则回滚幂等状态，下次重试
        for m in new_msgs:
            day_state.pop(hashlib.sha1(m.encode("utf-8")).hexdigest()[:16], None)
        if day_state:
            _save_notified(notified)
        print(f"⚠️ 成交通知推送失败: {ok.get('error')}")

    return len(new_msgs)


def main():
    # Cron 时间窗口守卫：防止调度器时区 bug 导致非预期时段执行
    # 配置为 15:40，允许窗口 15:00-16:00
    now = datetime.now()
    import os
    if not (now.weekday() < 5 and dtime(15, 0) <= now.time() <= dtime(16, 0)) and not os.getenv("FORCE_RUN"):
        print(f"⏭️ 非执行窗口 ({now.strftime('%H:%M')})，退出。配置窗口：工作日 15:00-16:00")
        return

    today_str = datetime.now().strftime("%Y-%m-%d")

    # 调用统一执行器（PortfolioExecutor -> portfolio_core.run_portfolio_scan）
    result = run_portfolio_scan_via_executor(
        stocks=STOCKS,
        fetch_data_func=fetch_data,
        today_str=today_str,
    )

    # 输出 JSON（供 agent 解析）
    output = {
        "date": result["date"],
        "stock_count": result["stock_count"],
        "portfolio": result["portfolio"],
        "errors": result["errors"],
        "details": result["details"],
    }
    print("=====PORTFOLIO_SIM_RESULT=====")
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    print("=====PORTFOLIO_SIM_END=====")

    # 生成人类可读日报
    report = generate_human_report(result)
    print("\n" + report)

    # 保存日报
    report_path = os.path.join(OUTPUT_DIR, f"{today_str}_portfolio_sim.md")
    with open(report_path, "w") as f:
        f.write(f"# 全组合模拟盘日报 {today_str}\n\n")
        f.write(report)
    print(f"\n📄 日报已保存: {report_path}")

    # 成交即通知（卖出/买入推送 Telegram）
    try:
        n = notify_trades(today_str, result.get("messages", []), result.get("portfolio", {}))
        if n == 0 and result.get("messages"):
            print("🔕 无新成交（或已推送过），跳过成交通知")
    except Exception as e:
        print(f"⚠️ 成交通知异常: {e}")


if __name__ == "__main__":
    main()