#!/usr/bin/env python3
"""
估值因子计算模块 — 从价格+基本面数据计算 PE/PB/PS/PCF 等估值因子
=====================================================================
核心逻辑：
  PE_TTM = close / eps_ttm (用基本面 eps_basic 近似，或拉取专门 TTM 接口)
  PB_MRQ = close / book_value_per_share
  PS_TTM = close / (revenue_per_share) — 需要每股营收，暂用 revenue_yoy 近似
  PCF_TTM = close / operating_cash_flow_per_share
  EV_EBITDA — 需要企业价值，暂不实现
"""

import os
import sys
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Dict
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

from analysis.factor_registry import get_registry, FactorMeta

VALUATION_DIR = os.path.join(WORKSPACE, "data", "valuation")
os.makedirs(VALUATION_DIR, exist_ok=True)


def compute_valuation_factors(
    price_data: Dict[str, pd.DataFrame],
    fundamental_factors: List[str] = None
) -> Dict[str, pd.DataFrame]:
    """
    从价格+基本面数据计算估值因子
    :param price_data: {symbol: DataFrame(index=date, columns=[OHLCV + fundamental cols])}
    :param fundamental_factors: 需要计算的估值因子列表
    :return: {symbol: DataFrame with valuation factors}
    """
    if fundamental_factors is None:
        fundamental_factors = ["PE_TTM", "PB_MRQ", "PS_TTM", "PCF_TTM"]
    
    results = {}
    
    for symbol, df in price_data.items():
        if df.empty:
            continue
        
        # 确保必要列存在
        required = ["close"]
        val_cols = []
        if "PE_TTM" in fundamental_factors:
            required.append("eps_basic")
            val_cols.append("PE_TTM")
        if "PB_MRQ" in fundamental_factors:
            required.append("book_value_per_share")
            val_cols.append("PB_MRQ")
        if "PS_TTM" in fundamental_factors:
            # 暂用 revenue_yoy 近似，实际需要 revenue_per_share
            required.append("revenue_yoy")
            val_cols.append("PS_TTM")
        if "PCF_TTM" in fundamental_factors:
            required.append("operating_cash_flow_per_share")
            val_cols.append("PCF_TTM")
        
        missing = [c for c in required if c not in df.columns]
        if missing:
            print(f"⚠️ {symbol}: 缺少基本面字段 {missing}, 跳过估值因子计算")
            results[symbol] = pd.DataFrame()
            continue
        
        out = df[["close"]].copy()
        
        # PE_TTM = close / eps_basic (eps > 0)
        if "PE_TTM" in fundamental_factors:
            eps = df["eps_basic"]
            out["PE_TTM"] = np.where(eps > 0, df["close"] / eps, np.nan)
        
        # PB_MRQ = close / book_value_per_share (bv > 0)
        if "PB_MRQ" in fundamental_factors:
            bv = df["book_value_per_share"]
            out["PB_MRQ"] = np.where(bv > 0, df["close"] / bv, np.nan)
        
        # PS_TTM = close / revenue_per_share (近似用 revenue_yoy，实际需要 revenue_per_share)
        # 这里暂时留空，标记需要改进
        if "PS_TTM" in fundamental_factors:
            out["PS_TTM"] = np.nan  # TODO: 需要每股营收数据
        
        # PCF_TTM = close / operating_cash_flow_per_share (ocf > 0)
        if "PCF_TTM" in fundamental_factors:
            ocf = df["operating_cash_flow_per_share"]
            out["PCF_TTM"] = np.where(ocf > 0, df["close"] / ocf, np.nan)
        
        # 只保留估值因子列
        out = out[val_cols]
        results[symbol] = out
    
    return results


def save_valuation_panel(results: Dict[str, pd.DataFrame], output_path: str = None):
    """保存估值因子面板"""
    if not results:
        return
    
    all_data = []
    for symbol, df in results.items():
        if df.empty:
            continue
        df = df.copy()
        df["symbol"] = symbol
        df = df.reset_index()
        all_data.append(df)
    
    if not all_data:
        return
    
    panel = pd.concat(all_data, ignore_index=True)
    panel = panel.sort_values(["symbol", "date"])
    
    if output_path is None:
        output_path = os.path.join(os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace"), "data", "valuation", "valuation_panel.parquet")
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    panel.to_parquet(output_path, compression="snappy")
    print(f"✅ 估值因子面板已保存: {output_path} ({panel.shape})")
    return panel


def register_valuation_factors():
    """将估值因子注册到因子注册表"""
    reg = get_registry()
    
    valuation_factors = [
        FactorMeta(
            name="PE_TTM",
            category="valuation",
            data_deps=["close", "eps_basic"],
            lookback=1,
            compute_fn="valuation_pe_ttm",
            params={},
            description="滚动市盈率（TTM）",
            tags=["valuation", "value"],
            universe="all",
        ),
        FactorMeta(
            name="PB_MRQ",
            category="valuation",
            data_deps=["close", "book_value_per_share"],
            lookback=1,
            compute_fn="valuation_pb_mrq",
            params={},
            description="最新市净率",
            tags=["valuation", "value"],
            universe="all",
        ),
        FactorMeta(
            name="PS_TTM",
            category="valuation",
            data_deps=["close", "revenue_per_share"],
            lookback=1,
            compute_fn="valuation_ps_ttm",
            params={},
            description="滚动市销率（TTM）",
            tags=["valuation", "value"],
            universe="all",
        ),
        FactorMeta(
            name="PCF_TTM",
            category="valuation",
            data_deps=["close", "operating_cash_flow_per_share"],
            lookback=1,
            compute_fn="valuation_pcf_ttm",
            params={},
            description="滚动市现率（TTM）",
            tags=["valuation", "value"],
            universe="all",
        ),
    ]
    
    for fm in valuation_factors:
        if reg.get(fm.name) is None:
            reg.register(fm)
            print(f"✅ 注册估值因子: {fm.name}")
        else:
            print(f"⚠️ 估值因子已存在: {fm.name}")


def compute_valuation_panel(
    symbols: List[str] = None,
    start_date: str = "2020-01-01",
    output_path: str = None
) -> pd.DataFrame:
    """计算全量股票的估值因子面板"""
    from analysis.fib_extension_scan import WATCHLIST
    from analysis.factor_engine import get_engine
    from analysis.factor_registry import get_registry
    
    if symbols is None:
        symbols = list(WATCHLIST.keys())
    
    # Load fundamental panel
    fund_panel = pd.read_parquet(
        os.path.join(os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace"), 
                    "data", "fundamental", "fundamental_panel.parquet")
    )
    fund_panel["symbol"] = fund_panel["symbol"].astype(str).str.zfill(6)
    
    # Load price data
    engine = get_engine()
    price_data = {}
    for sym in symbols:
        df = pd.read_csv(f"{os.getenv('WORKSPACE', '/Users/duguke/.openclaw/workspace')}/data/stock_data/{sym}.csv")
        df.columns = [c.lower() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")[["open", "high", "low", "close", "volume"]].sort_index()
        price_data[sym] = df
    
    # Merge fundamental data (forward fill, extended to price data max date)
    fund_cols = ["eps_basic", "book_value_per_share", "operating_cash_flow_per_share", "revenue_yoy"]
    for sym in symbols:
        df = price_data[sym].copy()
        max_date = df.index.max()
        sym_fund = fund_panel[fund_panel["symbol"] == sym].copy()
        if sym_fund.empty:
            continue
        fund_cols = [c for c in ["eps_basic", "book_value_per_share", "operating_cash_flow_per_share", "revenue_yoy"] if c in fund_panel.columns]
        sym_fund = sym_fund.set_index("report_date")[fund_cols]
        sym_fund = sym_fund.resample("D").ffill()
        
        # Extend fundamental data to cover full price date range
        if sym_fund.index.max() < df.index.max():
            extra_dates = pd.date_range(start=sym_fund.index.max() + pd.Timedelta(days=1), end=df.index.max(), freq='D')
            if len(extra_dates) > 0:
                last_values = sym_fund.iloc[[-1]]
                extra_df = pd.DataFrame(index=extra_dates, data={
                    col: last_values[col].values[0] for col in sym_fund.columns
                })
                sym_fund = pd.concat([sym_fund, extra_df])
        
        price_data[sym] = df.join(sym_fund, how="left")
    
    # Compute valuation factors
    results = compute_valuation_factors(price_data, ["PE_TTM", "PB_MRQ", "PS_TTM", "PCF_TTM"])
    
    # Save panel
    panel = save_valuation_panel(results)
    
    # Register factors
    register_valuation_factors()
    
    return panel


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", help="指定股票代码列表")
    ap.add_argument("--all", action="store_true", help="计算全部关注池")
    ap.add_argument("--start", default="2020-01-01", help="开始日期")
    ap.add_argument("--output", help="输出路径")
    args = ap.parse_args()
    
    if args.all or args.symbols:
        symbols = args.symbols or list(__import__("analysis.fib_extension_scan", fromlist=["WATCHLIST"]).WATCHLIST.keys())
        panel = compute_valuation_panel(symbols, args.start, args.output)
        print(f"✅ 估值因子面板计算完成: {panel.shape}")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()