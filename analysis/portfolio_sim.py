#!/usr/bin/env python3
"""
全组合多股模拟盘 (Portfolio Simulator) - 精简版
===================================================
核心逻辑已迁移至 analysis/portfolio_core.py，供模拟盘/实盘/回测复用。
本脚本仅负责：数据获取、调用核心扫描、输出报告。
"""

import os, sys, json, warnings
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import pandas as pd
import pandas_ta as ta
import baostock as bs
import numpy as np
import urllib.request

warnings.filterwarnings("ignore")

# 导入核心模块
from analysis.portfolio_core import (
    run_portfolio_scan,
    generate_human_report,
    fetch_today_realtime,
    STOCKS as CORE_STOCKS,
    STRATEGY_LABELS,
    COMMISSION, SLIPPAGE, INITIAL_CAPITAL, ATR_STOP_MULT,
)
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
# 数据获取（保持原有稳定链路）
# ═══════════════════════════════════════════

def fetch_data(symbol, start="20250101", max_retry=3, realtime_fallback=True):
    bs_code = f"sh.{symbol}" if symbol.startswith("6") else f"sz.{symbol}"
    start_ymd = f"{start[0:4]}-{start[4:6]}-{start[6:8]}"
    end_ymd = datetime.now().strftime("%Y-%m-%d")
    today_str = datetime.now().strftime("%Y-%m-%d")

    for attempt in range(max_retry):
        try:
            lg = bs.login()
            if lg.error_code != "0":
                raise ConnectionError(lg.error_msg)
            rs = bs.query_history_k_data_plus(
                bs_code, "date,open,close,high,low,volume",
                start_date=start_ymd, end_date=end_ymd,
                frequency="d", adjustflag="2")
            data = []
            while rs.next():
                data.append(rs.get_row_data())
            bs.logout()
            if not data:
                raise ValueError("空数据")
            df = pd.DataFrame(data, columns=["date","open","close","high","low","volume"])
            for c in ["open","close","high","low","volume"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True).dropna()

            if realtime_fallback:
                latest_bs_date = df["date"].iloc[-1].strftime("%Y-%m-%d")
                if latest_bs_date < today_str:
                    # 仅在交易时段内使用实时行情，避免午休/闭市时抓到 stale price
                    if is_trading_time():
                        rt = fetch_today_realtime([symbol])
                        if symbol in rt:
                            r = rt[symbol]
                            df = pd.concat([df, pd.DataFrame([{
                                "date": pd.Timestamp(r["date"]),
                                "open": r["open"], "close": r["close"],
                                "high": r["high"], "low": r["low"], "volume": r["volume"],
                            }])], ignore_index=True)
            return df
        except Exception:
            time.sleep(1)
        finally:
            try:
                bs.logout()
            except Exception:
                pass

    if realtime_fallback:
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
# 数据获取（复用单次 baostock 登录，避免连接池耗尽）
# ═══════════════════════════════════════════

# 全局 bs 会话管理
_bs_logged_in = False


def _ensure_bs_login():
    global _bs_logged_in
    if not _bs_logged_in:
        lg = bs.login()
        if lg.error_code != "0":
            raise ConnectionError(f"baostock登录失败: {lg.error_msg}")
        _bs_logged_in = True

def _ensure_bs_logout():
    global _bs_logged_in
    if _bs_logged_in:
        try:
            bs.logout()
        except Exception:
            pass
        _bs_logged_in = False


def fetch_data(symbol, start="20250101", max_retry=3, realtime_fallback=True):
    bs_code = f"sh.{symbol}" if symbol.startswith("6") else f"sz.{symbol}"
    start_ymd = f"{start[0:4]}-{start[4:6]}-{start[6:8]}"
    end_ymd = datetime.now().strftime("%Y-%m-%d")
    today_str = datetime.now().strftime("%Y-%m-%d")

    for attempt in range(max_retry):
        try:
            _ensure_bs_login()
            rs = bs.query_history_k_data_plus(
                bs_code, "date,open,close,high,low,volume",
                start_date=start_ymd, end_date=end_ymd,
                frequency="d", adjustflag="2")
            data = []
            while rs.next():
                data.append(rs.get_row_data())
            if not data:
                raise ValueError("空数据")
            df = pd.DataFrame(data, columns=["date","open","close","high","low","volume"])
            for c in ["open","close","high","low","volume"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True).dropna()

            if realtime_fallback:
                latest_bs_date = df["date"].iloc[-1].strftime("%Y-%m-%d")
                if latest_bs_date < today_str:
                    # 仅在交易时段内使用实时行情，避免午休/闭市时抓到 stale price
                    if is_trading_time():
                        rt = fetch_today_realtime([symbol])
                        if symbol in rt:
                            r = rt[symbol]
                            df = pd.concat([df, pd.DataFrame([{
                                "date": pd.Timestamp(r["date"]),
                                "open": r["open"], "close": r["close"],
                                "high": r["high"], "low": r["low"], "volume": r["volume"],
                            }])], ignore_index=True)
            return df
        except Exception as e:
            print(f"  ⚠️ {symbol} 获取失败(尝试{attempt+1}/{max_retry}): {e}")
            time.sleep(1)
    
    # 兜底：仅在交易时段使用实时行情
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


def main():
    # Cron 时间窗口守卫：防止调度器时区 bug 导致非预期时段执行
    # 配置为 15:40，允许窗口 15:00-16:00
    now = datetime.now()
    if not (now.weekday() < 5 and dtime(15, 0) <= now.time() <= dtime(16, 0)):
        print(f"⏭️ 非执行窗口 ({now.strftime('%H:%M')})，退出。配置窗口：工作日 15:00-16:00")
        return

    today_str = datetime.now().strftime("%Y-%m-%d")

    # 确保 baostock 登录
    _ensure_bs_login()
    try:
        # 调用核心扫描流程
        result = run_portfolio_scan(
            stocks=STOCKS,
            fetch_data_func=fetch_data,
            today_str=today_str,
        )
    finally:
        _ensure_bs_logout()

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


if __name__ == "__main__":
    main()