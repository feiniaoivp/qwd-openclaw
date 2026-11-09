#!/usr/bin/env python3
"""
基本面数据获取管线 — 批量拉取 36 只关注池财务指标
======================================================
数据源：akshare stock_financial_abstract_ths / stock_financial_analysis_ths
存储：data/fundamental/{symbol}.csv (长表格式: date, metric, value, period, report_type)
调度：周末/月度跑批，增量更新
"""

import os
import sys
import time
import json
import argparse
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

import pandas as pd
import numpy as np

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

from analysis.fib_extension_scan import WATCHLIST

FUNDAMENTAL_DIR = os.path.join(WORKSPACE, "data", "fundamental")
os.makedirs(FUNDAMENTAL_DIR, exist_ok=True)

# 目标指标映射（akshare stock_financial_abstract_ths 字段名 -> 标准因子名）
METRIC_MAP = {
    # 质量/盈利类
    "净资产收益率": "ROE_TTM",
    "净资产收益率-摊薄": "ROE_DILUTED",
    "销售净利率": "net_profit_margin",
    "营业总收入同比增长率": "revenue_yoy",
    "净利润同比增长率": "net_profit_yoy",
    # 杠杆/偿债类
    "资产负债率": "debt_to_equity",
    "流动比率": "current_ratio",
    "速动比率": "quick_ratio",
    # 质量/效率类
    "每股净资产": "book_value_per_share",
    "每股经营现金流": "operating_cash_flow_per_share",
    "基本每股收益": "eps_basic",
}

# 需要的原始字段（akshare 返回的列名）
REQUIRED_RAW_FIELDS = [
    "报告期", "净利润", "净利润同比增长率", "扣非净利润", "扣非净利润同比增长率",
    "营业总收入", "营业总收入同比增长率", "基本每股收益", "每股净资产",
    "每股资本公积金", "每股未分配利润", "每股经营现金流",
    "销售净利率", "净资产收益率", "净资产收益率-摊薄",
    "营业周期", "应收账款周转天数", "流动比率", "速动比率",
    "保守速动比率", "产权比率", "资产负债率"
]


def _ensure_numeric(series: pd.Series) -> pd.Series:
    """统一数值容错：空串/None → NaN"""
    return pd.to_numeric(series, errors="coerce")


def fetch_single_symbol(symbol: str, max_retries: int = 3) -> Optional[pd.DataFrame]:
    """抓取单只股票的基本面数据"""
    import akshare as ak
    
    for attempt in range(max_retries):
        try:
            # 同花顺财务摘要（主要源）
            df = ak.stock_financial_abstract_ths(symbol=symbol)
            if df is None or df.empty:
                print(f"  ⚠️ {symbol}: 空返回")
                return None
            
            # 标准化列名
            df.columns = [c.strip() for c in df.columns]
            
            # 只保留需要的字段
            available = [c for c in REQUIRED_RAW_FIELDS if c in df.columns]
            missing = [c for c in REQUIRED_RAW_FIELDS if c not in df.columns]
            if missing:
                print(f"  ⚠️ {symbol}: 缺失字段 {missing}")
            
            df = df[available].copy()
            
            # 重命名指标列
            rename_map = {k: v for k, v in METRIC_MAP.items() if k in df.columns}
            df = df.rename(columns=rename_map)
            
            # 日期标准化
            if "报告期" in df.columns:
                df["report_date"] = pd.to_datetime(df["报告期"], errors="coerce")
            else:
                df["report_date"] = pd.NaT
            
            if "报告类型" in df.columns:
                df["report_type"] = df["报告类型"]
            else:
                df["report_type"] = "annual"
            
            # 长表格式：每行一个指标
            metric_cols = [c for c in df.columns if c in METRIC_MAP.values()]
            id_cols = ["report_date", "report_type"]
            
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
            
            long_df["symbol"] = symbol
            long_df["fetched_at"] = datetime.now().isoformat()
            
            # 只保留标准列
            out_cols = ["symbol", "report_date", "report_type", "metric", "value", "fetched_at"]
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


def _sina_stock_code(symbol: str) -> str:
    """6位代码 → 新浪带交易所前缀代码（6/9 开头=沪，其余=深）"""
    return f"sh{symbol}" if symbol.startswith(("6", "9")) else f"sz{symbol}"


def _fetch_sina_report(symbol: str, report: str, value_cols, metric: str,
                       max_retries: int = 3) -> Optional[pd.DataFrame]:
    """新浪三大报表通用抓取（数值型全历史，与同花顺摘要互补）
    例：report='利润表', value_cols=['营业收入'], metric='total_revenue'
    value_cols 支持候选列表（不同行业报表模板列名不同：一般企业='实收资本净额'，
    银行类='股本'，2026-11-10 实测），取第一个存在的列。
    """
    import akshare as ak
    if isinstance(value_cols, str):
        value_cols = [value_cols]
    code = _sina_stock_code(symbol)
    for attempt in range(max_retries):
        try:
            df = ak.stock_financial_report_sina(stock=code, symbol=report)
            if df is None or df.empty:
                print(f"  ⚠️ {symbol}: {report} 空返回")
                return None
            value_col = next((c for c in value_cols if c in df.columns), None)
            if value_col is None:
                print(f"  ⚠️ {symbol}: {report} 缺候选列 {value_cols}")
                return None
            date_col = df.columns[0]  # '报告日'
            out = pd.DataFrame({
                "symbol": symbol,
                "report_date": pd.to_datetime(df[date_col].astype(str), format="%Y%m%d", errors="coerce"),
                "report_type": f"sina_{report}",
                "metric": metric,
                "value": pd.to_numeric(df[value_col], errors="coerce"),
                "fetched_at": datetime.now().isoformat(),
            }).dropna(subset=["report_date", "value"])
            if out.empty:
                print(f"  ⚠️ {symbol}: {report} 解析后无有效行")
                return None
            print(f"  ✅ {symbol} {report}/{metric}: {len(out)} 期 ({out['report_date'].min().date()} ~ {out['report_date'].max().date()})")
            return out
        except Exception as e:
            print(f"  ❌ {symbol} {report} 尝试 {attempt+1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return None


def _cum_to_quarterly(cum: pd.Series) -> pd.Series:
    """累计营收 → 单季营收：Q1(3月末)=累计值；H1/Q3/FY=同年内差分
    跨年边界自动正确（Q1 恒为新年度首期）；缺 Q1 时下半年差分得 NaN（宁缺勿错）
    """
    q = cum.sort_index()
    prev_same_year = q.groupby(q.index.year).shift(1)
    out = q.where(q.index.month == 3, q - prev_same_year)
    return out


def build_revenue_per_share(combined: pd.DataFrame) -> pd.DataFrame:
    """从 total_revenue(累计) + total_shares 合成 TTM 口径 revenue_per_share

    累计营收 → 单季差分 → TTM(严格 4 期滚动和，缺季 NaN) → / 当期股本
    为什么必须 TTM：直接用累计值会产生季节性锯齿 PS（Q1=3个月 vs FY=12个月），
    因子 IC 会被报告期季节性伪信号污染。
    """
    rev = (combined[combined["metric"] == "total_revenue"]
           .drop_duplicates("report_date").set_index("report_date")["value"])
    sh = (combined[combined["metric"] == "total_shares"]
          .drop_duplicates("report_date").set_index("report_date")["value"])
    if rev.empty or sh.empty:
        return pd.DataFrame()
    quarterly = _cum_to_quarterly(rev)
    ttm = quarterly.rolling(4, min_periods=4).sum()
    m = pd.DataFrame({"ttm_rev": ttm}).join(sh.rename("shares"), how="left")
    m = m[m["shares"] > 0]
    if m.empty or m["ttm_rev"].notna().sum() == 0:
        return pd.DataFrame()
    out = pd.DataFrame({
        "symbol": combined["symbol"].iloc[0],
        "report_date": m.index,
        "report_type": "derived",
        "metric": "revenue_per_share",
        "value": m["ttm_rev"] / m["shares"],
        "fetched_at": datetime.now().isoformat(),
    }).dropna(subset=["value"]).reset_index(drop=True)
    return out


def save_fundamental_data(symbol: str, new_df: pd.DataFrame) -> bool:
    """保存基本面数据（增量合并，去重）"""
    csv_path = os.path.join(FUNDAMENTAL_DIR, f"{symbol}.csv")
    
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
        # 合并去重：同 symbol+report_date+metric 保留最新 fetched_at
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
    
    print(f"🚀 开始批量抓取 {total} 只股票基本面数据...")
    
    for i, sym in enumerate(symbols, 1):
        print(f"\n[{i}/{total}] 处理 {sym} ({WATCHLIST.get(sym, '')})")
        # 三源：同花顺摘要（现有 10 指标）+ 新浪利润表（total_revenue）+ 新浪负债表（total_shares）
        parts = []
        df_ths = fetch_single_symbol(sym)
        if df_ths is not None and not df_ths.empty:
            parts.append(df_ths)
        df_rev = _fetch_sina_report(sym, "利润表", "营业收入", "total_revenue")
        if df_rev is not None:
            parts.append(df_rev)
        df_sh = _fetch_sina_report(sym, "资产负债表", ["实收资本净额", "实收资本(或股本)", "股本"], "total_shares")
        if df_sh is not None:
            parts.append(df_sh)

        if parts:
            combined = pd.concat(parts, ignore_index=True)
            rps = build_revenue_per_share(combined)
            if not rps.empty:
                combined = pd.concat([combined, rps], ignore_index=True)
                print(f"  ✅ {sym} revenue_per_share(TTM): {len(rps)} 期")
            else:
                print(f"  ⚠️ {sym}: revenue_per_share 无法合成（缺营收或股本）")
            save_fundamental_data(sym, combined)
            results[sym] = len(combined)
        else:
            results[sym] = 0
        
        if i < total:
            time.sleep(delay)  # 礼貌延迟
    
    success = sum(1 for v in results.values() if v > 0)
    print(f"\n📊 批量抓取完成: 成功 {success}/{total}, 总记录数 {sum(results.values())}")
    return results


def build_fundamental_panel(symbols: List[str] = None) -> pd.DataFrame:
    """构建基本面面板数据（供 factor_engine 使用）"""
    if symbols is None:
        symbols = list(WATCHLIST.keys())
    
    all_data = []
    for sym in symbols:
        csv_path = os.path.join(FUNDAMENTAL_DIR, f"{sym}.csv")
        if not os.path.exists(csv_path):
            continue
        try:
            df = pd.read_csv(csv_path)
            if df.empty:
                continue
            df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
            # 透视为宽表：每行一只股票一期报告，列为各指标
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
        panel = build_fundamental_panel(symbols)
        if not panel.empty:
            out_path = os.path.join(FUNDAMENTAL_DIR, "fundamental_panel.parquet")
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