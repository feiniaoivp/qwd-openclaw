#!/usr/bin/env python3
"""
估值因子获取管线 — 从 akshare 获取 PE/PB/PS/PCF 等估值指标
==============================================================
数据源：akshare stock_a_valuation_... 接口
存储：data/valuation/{symbol}.csv (长表格式: date, metric, value)
调度：日度/周度跑批，增量更新
"""

import os
import sys
import time
import json
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

from analysis.fib_extension_scan import WATCHLIST

VALUATION_DIR = os.path.join(WORKSPACE, "data", "valuation")
os.makedirs(VALUATION_DIR, exist_ok=True)

# 目标指标映射
METRIC_MAP = {
    # 估值类
    "pe_ttm": "PE_TTM",
    "pb_mrq": "PB_MRQ",
    "ps_ttm": "PS_TTM",
    "pcf_ttm": "PCF_TTM",
    "ev_ebitda": "EV_EBITDA",
    "market_cap": "MARKET_CAP",
    "circulating_market_cap": "CIRCULATING_MARKET_CAP",
}

REQUIRED_FIELDS = list(METRIC_MAP.keys()) + ["trade_date"]


def _ensure_numeric(series: pd.Series) -> pd.Series:
    """统一数值容错：空串/None → NaN"""
    return pd.to_numeric(series, errors="coerce")


def fetch_single_symbol(symbol: str, max_retries: int = 3) -> Optional[pd.DataFrame]:
    """抓取单只股票的估值数据"""
    import akshare as ak
    
    for attempt in range(max_retries):
        try:
            # akshare 估值接口
            df = ak.stock_a_valuation_indicator(symbol=symbol)
            if df is None or df.empty:
                print(f"  ⚠️ {symbol}: 空返回")
                return None
            
            # 标准化列名
            df.columns = [c.strip().lower() for c in df.columns]
            
            # 检查必要字段
            available = [c for c in REQUIRED_FIELDS if c in df.columns]
            missing = [c for c in REQUIRED_FIELDS if c not in df.columns]
            if missing:
                print(f"  ⚠️ {symbol}: 缺失字段 {missing}")
            
            df = df[available].copy()
            
            # 重命名指标列
            rename_map = {k: v for k, v in METRIC_MAP.items() if k in df.columns}
            df = df.rename(columns=rename_map)
            
            # 日期标准化
            if "trade_date" in df.columns:
                df["report_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
            else:
                df["report_date"] = pd.NaT
            
            df["symbol"] = symbol
            df["fetched_at"] = datetime.now().isoformat()
            
            # 长表格式
            metric_cols = [c for c in df.columns if c in METRIC_MAP.values()]
            id_cols = ["symbol", "report_date"]
            
            if not metric_cols:
                print(f"  ⚠️ {symbol}: 无有效指标列")
                return None
            
            long_df = df.melt(id_vars=id_cols, value_vars=metric_cols,
                             var_name="metric", value_name="value")
            long_df["value"] = _ensure_numeric(long_df["value"])
            long_df = long_df.dropna(subset=["value"])
            
            if long_df.empty:
                print(f"  ⚠️ {symbol}: 所有指标为空")
                return None
            
            out_cols = ["symbol", "report_date", "metric", "value"]
            long_df = long_df[out_cols].sort_values(["report_date", "metric"])
            
            print(f"  ✅ {symbol}: {len(long_df)} 条记录 ({long_df['metric'].nunique()} 个指标)")
            return long_df
            
        except Exception as e:
            print(f"  ❌ {symbol} 尝试 {attempt+1}/{max_retries} 失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                return None
    return None


def save_valuation_data(symbol: str, new_df: pd.DataFrame) -> bool:
    """保存估值数据（增量合并，去重）"""
    csv_path = os.path.join(VALUATION_DIR, f"{symbol}.csv")
    
    if os.path.exists(csv_path):
        try:
            existing = pd.read_csv(csv_path)
            existing["report_date"] = pd.to_datetime(existing["report_date"], errors="coerce")
        except Exception:
            existing = pd.DataFrame()
    else:
        existing = pd.DataFrame()
    
    if existing.empty:
        merged = new_df
    else:
        merged = pd.concat([existing, new_df], ignore_index=True)
        merged["report_date"] = pd.to_datetime(merged["report_date"], errors="coerce")
        merged = merged.sort_values("fetched_at").drop_duplicates(
            subset=["symbol", "report_date", "metric"], keep="last"
        )
    
    # 备份
    if os.path.exists(csv_path):
        import shutil
        shutil.copy2(csv_path, csv_path + ".bak")
    
    merged.to_csv(csv_path, index=False, encoding="utf-8")
    print(f"  💾 {symbol}: 已保存 {len(merged)} 条记录到 {csv_path}")
    return True


def fetch_all_symbols(symbols: List[str] = None, delay: float = 0.5) -> Dict[str, int]:
    """批量抓取所有股票"""
    if symbols is None:
        symbols = list(WATCHLIST.keys())
    
    results = {}
    total = len(symbols)
    
    print(f"🚀 开始批量抓取 {total} 只股票估值数据...")
    
    for i, sym in enumerate(symbols, 1):
        print(f"\n[{i}/{total}] 处理 {sym} ({WATCHLIST.get(sym, '')})")
        df = fetch_single_symbol(sym)
        if df is not None and not df.empty:
            save_valuation_data(sym, df)
            results[sym] = len(df)
        else:
            results[sym] = 0
        
        if i < total:
            time.sleep(delay)
    
    success = sum(1 for v in results.values() if v > 0)
    print(f"\n📊 批量抓取完成: 成功 {success}/{total}, 总记录数 {sum(results.values())}")
    return results


def build_valuation_panel(symbols: List[str] = None) -> pd.DataFrame:
    """构建估值面板数据（供 factor_engine 使用）"""
    if symbols is None:
        symbols = list(WATCHLIST.keys())
    
    all_data = []
    for sym in symbols:
        csv_path = os.path.join(VALUATION_DIR, f"{sym}.csv")
        if not os.path.exists(csv_path):
            continue
        try:
            df = pd.read_csv(csv_path)
            if df.empty:
                continue
            df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
            # 透视为宽表
            pivot = df.pivot_table(
                index=["symbol", "report_date"],
                columns="metric",
                values="value",
                aggfunc="last"
            ).reset_index()
            pivot.columns.name = None
            all_data.append(pivot)
        except Exception as e:
            print(f"⚠️ {sym} 面板构建失败: {e}")
    
    if not all_data:
        return pd.DataFrame()
    
    panel = pd.concat(all_data, ignore_index=True)
    panel = panel.sort_values(["symbol", "report_date"])
    return panel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", help="指定股票代码列表")
    ap.add_argument("--all", action="store_true", help="抓取全部关注池")
    ap.add_argument("--build-panel", action="store_true", help="构建面板数据")
    ap.add_argument("--delay", type=float, default=0.5, help="请求间隔秒数")
    args = ap.parse_args()
    
    if args.build_panel:
        symbols = args.symbols or list(WATCHLIST.keys())
        panel = build_valuation_panel(symbols)
        if not panel.empty:
            out_path = os.path.join(VALUATION_DIR, "valuation_panel.parquet")
            panel.to_parquet(out_path, compression="snappy")
            print(f"✅ 面板已保存: {out_path} ({panel.shape})")
        else:
            print("⚠️ 无数据可构建面板")
        return
    
    if args.all or args.symbols:
        symbols = args.symbols or list(WATCHLIST.keys())
        fetch_all_symbols(symbols, args.delay)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()