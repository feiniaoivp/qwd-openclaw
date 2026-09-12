#!/usr/bin/env python3
"""
价格行为分析模块 - 基于 baostock 周线数据计算技术指标
用于周复盘报告的「1. 本周核心观察」>「1.1 个股周涨跌幅排名」
"""

import baostock as bs
import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# 核心标的配置 - 从权威配置 data/power_overseas_config.json 读取，避免代码漂移
_CONFIG_FILE = Path(__file__).resolve().parents[2] / "data" / "power_overseas_config.json"

def _load_core_stocks() -> list:
    fallback = [
        {"code": "002130", "name": "沃尔核材", "market": "sz", "oversea_pct": 45},
        {"code": "600312", "name": "平高电气", "market": "sh", "oversea_pct": 42},
        {"code": "002028", "name": "思源电气", "market": "sz", "oversea_pct": 38},
        {"code": "600089", "name": "特变电工", "market": "sh", "oversea_pct": 30},
        {"code": "601179", "name": "中国西电", "market": "sh", "oversea_pct": 25},
        {"code": "600406", "name": "国电南瑞", "market": "sh", "oversea_pct": 22},
        {"code": "000400", "name": "许继电气", "market": "sz", "oversea_pct": 20},
        {"code": "002270", "name": "华明装备", "market": "sz", "oversea_pct": 18},
    ]
    try:
        cfg = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        out = []
        for code, meta in cfg.get("stocks", {}).items():
            pct = meta.get("revenue_overseas_pct")
            out.append({
                "code": code,
                "name": meta.get("name", code),
                "market": "sh" if code.startswith("6") else "sz",
                "oversea_pct": round(pct * 100) if isinstance(pct, (int, float)) else None,
            })
        return out or fallback
    except Exception:
        return fallback

CORE_STOCKS = _load_core_stocks()

def fetch_weekly_data(code: str, market: str, start_date: str, end_date: str) -> pd.DataFrame:
    """获取周线数据 (前复权 adjustflag=2)
    注意: baostock 周线返回的是每周五的收盘数据，需查询更长时间范围以获得前几周对比数据"""
    bs_code = f"{market}.{code}"
    # 查询更早的开始日期，确保至少有 10 周数据用于指标计算
    query_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=70)).strftime("%Y-%m-%d")
    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,code,open,high,low,close,volume,amount",
        start_date=query_start,
        end_date=end_date,
        frequency="w",
        adjustflag="2"
    )
    
    if rs.error_code != "0":
        raise Exception(f"baostock query failed: {rs.error_msg}")
    
    data = []
    while rs.error_code == "0" and rs.next():
        data.append(rs.get_row_data())
    
    if not data:
        return pd.DataFrame()
    
    df = pd.DataFrame(data, columns=["date", "code", "open", "high", "low", "close", "volume", "amount"])
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    
    # 过滤到指定周范围内 (但保留前几周用于指标计算)
    df = df.sort_values("date").reset_index(drop=True)
    
    return df

def calculate_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算技术指标：MA5/20/60, MACD, RSI, 布林带"""
    if df.empty or len(df) < 2:
        return df
    
    df = df.copy()
    close = df["close"]
    
    # 移动平均线
    df["MA5"] = close.rolling(5).mean()
    df["MA20"] = close.rolling(20).mean()
    df["MA60"] = close.rolling(60).mean()
    
    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["DIFF"] = ema12 - ema26
    df["DEA"] = df["DIFF"].ewm(span=9, adjust=False).mean()
    df["MACD"] = 2 * (df["DIFF"] - df["DEA"])
    df["MACD_signal"] = df.apply(
        lambda r: "金叉" if r["DIFF"] > r["DEA"] and (r.shift(1)["DIFF"] <= r.shift(1)["DEA"] if not pd.isna(r.shift(1)["DIFF"]) else False)
        else ("死叉" if r["DIFF"] < r["DEA"] and (r.shift(1)["DIFF"] >= r.shift(1)["DEA"] if not pd.isna(r.shift(1)["DIFF"]) else False)
        else ("多头" if r["DIFF"] > r["DEA"] else "空头")), axis=1
    )
    
    # RSI (14)
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))
    
    # 布林带 (20, 2)
    df["BB_mid"] = close.rolling(20).mean()
    df["BB_std"] = close.rolling(20).std()
    df["BB_upper"] = df["BB_mid"] + 2 * df["BB_std"]
    df["BB_lower"] = df["BB_mid"] - 2 * df["BB_std"]
    df["BB_position"] = (close - df["BB_lower"]) / (df["BB_upper"] - df["BB_lower"])
    
    # 均线结构判断
    df["MA_trend"] = df.apply(
        lambda r: "多头排列" if r["MA5"] > r["MA20"] > r["MA60"]
        else ("空头排列" if r["MA5"] < r["MA20"] < r["MA60"] else "混杂"), axis=1
    )
    
    return df

def analyze_weekly_performance(df: pd.DataFrame) -> dict:
    """分析最新一周表现"""
    if df.empty or len(df) < 2:
        return {}
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    chg_pct = (last["close"] - prev["close"]) / prev["close"] * 100
    volume_ratio = last["volume"] / prev["volume"] if prev["volume"] > 0 else 0
    
    # 成交量 vs 5周均量
    vol_ma5 = df["volume"].rolling(5).mean().iloc[-1]
    volume_vs_ma5 = last["volume"] / vol_ma5 if vol_ma5 > 0 else 0
    
    return {
        "date": last["date"].strftime("%Y-%m-%d"),
        "close": last["close"],
        "prev_close": prev["close"],
        "chg_pct": round(chg_pct, 2),
        "volume": int(last["volume"]),
        "prev_volume": int(prev["volume"]),
        "volume_ratio": round(volume_ratio, 2),
        "volume_vs_ma5": round(volume_vs_ma5, 2),
        "MA_trend": last.get("MA_trend", "-"),
        "MACD_signal": last.get("MACD_signal", "-"),
        "RSI": round(last.get("RSI", 0), 1) if not pd.isna(last.get("RSI", 0)) else None,
        "BB_position": round(last.get("BB_position", 0), 2) if not pd.isna(last.get("BB_position", 0)) else None,
    }

def analyze_all_stocks(monday: str, friday: str) -> list[dict]:
    """批量分析所有核心标的本周表现"""
    results = []
    lg = bs.login()
    if lg.error_code != "0":
        print(f"[ERROR] baostock login failed: {lg.error_msg}")
        return results
    
    try:
        for stock in CORE_STOCKS:
            try:
                df = fetch_weekly_data(stock["code"], stock["market"], monday, friday)
                if df.empty:
                    print(f"[WARN] No data for {stock['code']} {stock['name']}")
                    continue
                
                df = calculate_technical_indicators(df)
                perf = analyze_weekly_performance(df)
                
                if perf:
                    perf.update({
                        "code": stock["code"],
                        "name": stock["name"],
                        "oversea_pct": stock["oversea_pct"],
                    })
                    results.append(perf)
                    print(f"  ✓ {stock['code']} {stock['name']}: {perf['chg_pct']:+.2f}% | {perf['MA_trend']} | MACD:{perf['MACD_signal']} | RSI:{perf['RSI']}")
                
            except Exception as e:
                print(f"[ERROR] {stock['code']} {stock['name']}: {e}")
    finally:
        bs.logout()
    
    return results

def main():
    """测试运行"""
    # 获取上周范围 (已结束的周)
    today = datetime.now()
    # 如果今天是周六/周日，取上周一到上周五
    if today.weekday() >= 5:  # 周六=5, 周日=6
        last_friday = today - timedelta(days=today.weekday() - 4)
        last_monday = last_friday - timedelta(days=4)
    else:
        # 工作日：取本周一到今天
        last_monday = today - timedelta(days=today.weekday())
        last_friday = today
    
    monday = last_monday.strftime("%Y-%m-%d")
    friday = last_friday.strftime("%Y-%m-%d")
    
    print(f"=== 价格行为分析 {monday} ~ {friday} ===")
    results = analyze_all_stocks(monday, friday)
    
    # 输出汇总表
    print(f"\n=== 本周涨跌幅排名 ===")
    print(f"| 代码 | 名称 | 出海份额 | 周涨跌幅 | 成交量比 | vs 5周均量 | 均线结构 | MACD | RSI | 布林带位置 |")
    print(f"|------|------|----------|----------|----------|------------|----------|------|-----|------------|")
    for r in sorted(results, key=lambda x: x["chg_pct"], reverse=True):
        print(f"| {r['code']} | {r['name']} | {r['oversea_pct']}% | {r['chg_pct']:+.2f}% | {r['volume_ratio']:.2f}x | {r['volume_vs_ma5']:.2f}x | {r['MA_trend']} | {r['MACD_signal']} | {r['RSI'] or '-'} | {r['BB_position'] or '-'} |")
    
    return results

if __name__ == "__main__":
    main()