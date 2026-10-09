#!/usr/bin/env python3
"""单股深度诊断：PPO / CCI / 均线系统，baostock 前复权日线。"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
import logging
import pandas as pd
import baostock as bs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("deep_diag")

STOCKS = {
    "600346": "恒力石化",
    "002413": "雷科防务",
    "601061": "中信金属",
    "000708": "中信特钢",
    "002130": "沃尔核材",
}


def code_to_bs(code: str) -> str:
    return f"sh.{code}" if code.startswith("6") else f"sz.{code}"


def fetch_daily(code: str, days: int = 260) -> pd.DataFrame:
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    try:
        rs = bs.query_history_k_data_plus(
            code_to_bs(code),
            "date,open,high,low,close,volume,amount,turn",
            start_date="2025-01-01",
            end_date="2026-10-08",
            frequency="d",
            adjustflag="2",  # 前复权
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        df = pd.DataFrame(rows, columns=rs.fields)
        for c in ("open", "high", "low", "close", "volume", "amount", "turn"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["date"] = pd.to_datetime(df["date"])
        return df.dropna(subset=["close"]).tail(days).reset_index(drop=True)
    finally:
        bs.logout()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    h, l = df["high"], df["low"]
    # PPO: (EMA12-EMA26)/EMA26 *100, signal=EMA9(PPO)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["PPO"] = (ema12 - ema26) / ema26 * 100
    df["PPO_sig"] = df["PPO"].ewm(span=9, adjust=False).mean()
    df["PPO_hist"] = df["PPO"] - df["PPO_sig"]
    # CCI(20)
    tp = (h + l + c) / 3
    ma_tp = tp.rolling(20).mean()
    md = tp.rolling(20).apply(lambda x: (abs(x - x.mean())).mean(), raw=True)
    df["CCI"] = (tp - ma_tp) / (0.015 * md)
    # 均线
    for n in (5, 10, 20, 60, 120, 250):
        df[f"MA{n}"] = c.rolling(n).mean()
    return df


def diagnose(code: str, name: str, df: pd.DataFrame) -> dict:
    last = df.iloc[-1]
    prev = df.iloc[-2]
    ma_keys = ["MA5", "MA10", "MA20", "MA60", "MA120", "MA250"]
    mas = {k: (None if pd.isna(last[k]) else round(float(last[k]), 2)) for k in ma_keys}
    close = round(float(last["close"]), 2)
    above = [k for k in mas if mas[k] and close > mas[k]]
    below = [k for k in mas if mas[k] and close < mas[k]]

    # 均线多空排列
    ma_vals = [mas[k] for k in ("MA5", "MA10", "MA20", "MA60") if mas[k]]
    if len(ma_vals) == 4:
        if ma_vals[0] > ma_vals[1] > ma_vals[2] > ma_vals[3]:
            ma_state = "🔥完全多头排列"
        elif ma_vals[0] < ma_vals[1] < ma_vals[2] < ma_vals[3]:
            ma_state = "❄️完全空头排列"
        elif close > mas["MA20"]:
            ma_state = "偏多（站上MA20）"
        else:
            ma_state = "偏空（MA20下方）"
    else:
        ma_state = "数据不足"

    ppo = float(last["PPO"]); ppo_prev = float(prev["PPO"])
    psig = float(last["PPO_sig"]); psig_prev = float(prev["PPO_sig"])
    ppo_cross = ""
    if ppo > psig and ppo_prev <= psig_prev:
        ppo_cross = "🟢今日金叉"
    elif ppo < psig and ppo_prev >= psig_prev:
        ppo_cross = "🔴今日死叉"
    elif ppo > psig:
        ppo_cross = "多头范围"
    else:
        ppo_cross = "空头范围"

    cci = float(last["CCI"]) if not pd.isna(last["CCI"]) else None
    if cci is not None:
        cci_tag = "⚡超买(>100)" if cci > 100 else ("⚡超卖(<-100)" if cci < -100 else "中性区间")

    # 20日位置
    recent20 = df["close"].tail(20)
    pos20 = (close - recent20.min()) / max(recent20.max() - recent20.min(), 1e-9) * 100

    return {
        "code": code,
        "name": name,
        "date": str(last["date"].date()),
        "close": close,
        "chg_1d_pct": round((close / float(prev["close"]) - 1) * 100, 2),
        "MA": mas,
        "MA_state": ma_state,
        "above_MA": above,
        "below_MA": below,
        "PPO": round(ppo, 3),
        "PPO_signal": round(psig, 3),
        "PPO_hist": round(float(last["PPO_hist"]), 3),
        "PPO_status": ppo_cross,
        "CCI": (None if cci is None else round(cci, 1)),
        "CCI_status": cci_tag if cci is not None else "数据不足",
        "pos_in_20d_range_pct": round(float(pos20), 1),
        "turnover_pct": (None if pd.isna(last["turn"]) else round(float(last["turn"]), 2)),
    }


def main():
    out = []
    for code, name in STOCKS.items():
        try:
            df = fetch_daily(code)
            if len(df) < 30:
                log.warning("%s %s 数据仅 %d 行", code, name, len(df))
            df = add_indicators(df)
            out.append(diagnose(code, name, df))
            log.info("done %s %s", code, name)
        except Exception as e:
            log.warning("%s %s 失败: %r", code, name, e)
    path = os.path.join(WORKSPACE, "analysis", "daily", "2026-10-08_deep_diag_5.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    log.info("saved -> %s", path)


if __name__ == "__main__":
    main()
