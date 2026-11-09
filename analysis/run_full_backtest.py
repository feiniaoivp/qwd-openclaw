#!/usr/bin/env python3
"""
端到端全周期回测 — 生成第一份正式 α 报告
================================================
流程：
  1. 读取真实股票数据 → 构建 factor_engine 输入
  2. factor_engine 计算全因子面板
  3. strategy_composer 计算各策略评分/仓位
  4. alpha_validator 产出 IC/DSR/正交性/换手调整收益
  5. 生成 JSON + Markdown 正式报告
"""

import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

from analysis.fib_extension_scan import WATCHLIST
from analysis.factor_engine import get_engine
from analysis.strategy_composer import StrategyComposer, create_seed_strategies
from analysis.alpha_validator import run_alpha_validation, AlphaValidationReport
from analysis.regime_allocator import generate_next_day_config


def load_stock_data(symbols: List[str], start_date: str = "2023-01-01") -> Dict[str, pd.DataFrame]:
    """从 CSV 加载股票数据，统一格式（2026-11-09 补缺：join 基本面面板，
    否则估值因子 PE/PB/PCF 拿不到 eps/bvps/ocf 列 → 恒 NaN → IC=0 假象）"""
    data = {}
    data_dir = os.path.join(WORKSPACE, "data", "stock_data")
    fund_path = os.path.join(WORKSPACE, "data", "fundamental", "fundamental_panel.parquet")
    fund_panel = None
    if os.path.exists(fund_path):
        fund_panel = pd.read_parquet(fund_path)
        fund_panel["symbol"] = fund_panel["symbol"].astype(str).str.zfill(6)
    fund_cols = [c for c in ["eps_basic", "book_value_per_share", "operating_cash_flow_per_share",
                             "revenue_yoy", "ROE_TTM", "net_profit_margin"]
                 if fund_panel is not None and c in fund_panel.columns]
    
    for sym in symbols:
        csv_path = os.path.join(data_dir, f"{sym}.csv")
        if not os.path.exists(csv_path):
            print(f"⚠️ 缺失数据: {sym}")
            continue
        
        try:
            df = pd.read_csv(csv_path)
            # 标准化列名
            df.columns = [c.lower().strip() for c in df.columns]
            
            # 确保必要列存在
            required = ["date", "open", "high", "low", "close", "volume"]
            for col in required:
                if col not in df.columns:
                    # 尝试常见别名
                    aliases = {
                        "date": ["trade_date", "datetime", "time"],
                        "open": ["o"],
                        "high": ["h"],
                        "low": ["l"],
                        "close": ["c"],
                        "volume": ["vol", "amount"],
                    }
                    for alias in aliases.get(col, []):
                        if alias in df.columns:
                            df[col] = df[alias]
                            break
            
            # 日期索引
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
            
            # 只保留需要的列
            keep_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
            df = df[keep_cols].sort_index()
            
            # 过滤开始日期
            df = df[df.index >= start_date]
            
            # join 基本面（report_date → 日度 ffill 延展到 K 线末日；列缺失安全跳过）
            if fund_panel is not None and fund_cols and sym in set(fund_panel["symbol"]):
                sym_fund = fund_panel[fund_panel["symbol"] == sym].drop_duplicates("report_date")
                sym_fund = sym_fund.set_index("report_date")[fund_cols].sort_index()
                sym_fund = sym_fund.resample("D").ffill()
                if len(sym_fund) and sym_fund.index.max() < df.index.max():
                    extra = pd.date_range(sym_fund.index.max() + pd.Timedelta(days=1),
                                          df.index.max(), freq="D")
                    if len(extra):
                        pad = pd.DataFrame(index=extra,
                                           data={c: sym_fund.iloc[-1][c] for c in sym_fund.columns})
                        sym_fund = pd.concat([sym_fund, pad])
                df = df.join(sym_fund, how="left")

            if len(df) > 30:  # 最少数据量
                data[sym] = df
            else:
                print(f"⚠️ {sym} 数据不足: {len(df)} 行")
                
        except Exception as e:
            print(f"❌ 读取 {sym} 失败: {e}")
    
    return data


def run_full_backtest(
    start_date: str = "2023-01-01",
    end_date: str = None,
    universe: str = "watchlist",
):
    """执行完整回测"""
    
    print("="*60)
    print("🚀 启动端到端全周期回测")
    print("="*60)
    
    # 1. 加载数据
    print("\n📥 步骤 1/5: 加载股票数据...")
    symbols = list(WATCHLIST.keys())
    data = load_stock_data(symbols, start_date)
    print(f"   成功加载 {len(data)} 只股票")
    
    if not data:
        raise ValueError("无可用数据")
    
    # 确定日期范围
    all_dates = []
    for df in data.values():
        all_dates.extend(df.index.tolist())
    min_date = min(all_dates)
    max_date = max(all_dates)
    
    if end_date:
        max_date = min(pd.Timestamp(end_date), max_date)
    
    print(f"   回测区间: {min_date.date()} ~ {max_date.date()}")
    print(f"   交易日数: {len(pd.date_range(min_date, max_date, freq='B'))}")
    
    # 2. 计算因子面板
    print("\n📊 步骤 2/5: 计算因子面板...")
    engine = get_engine()
    
    # 使用注册表中所有因子
    from analysis.factor_registry import get_registry
    reg = get_registry()
    all_factors = reg.get_names()
    print(f"   待计算因子: {len(all_factors)} 个")
    
    factor_panel = engine.compute_panel(data, all_factors)
    print(f"   面板形状: {factor_panel.shape} (股票×日期 × 因子)")
    print(f"   因子列表: {list(factor_panel.columns)}")
    
    # 3. 策略评分与仓位
    print("\n🎯 步骤 3/5: 策略评分与仓位生成...")
    composer = StrategyComposer()
    strategies = create_seed_strategies()
    
    all_scores = {}
    all_positions = {}
    
    for sname, strategy in strategies.items():
        print(f"   处理策略: {sname}")
        try:
            scores = composer.compute_scores(factor_panel, strategy)
            all_scores[sname] = scores
            
            # 生成目标仓位（简化：每日等权，通过门控+分数阈值）
            valid = (scores["gate_status"] == "pass") & (scores["score"] >= strategy.min_score)
            filtered = scores[valid]
            
            if not filtered.empty:
                positions_dict = {}
                for date in filtered.index.get_level_values("date").unique():
                    day_scores = filtered.xs(date, level="date")
                    symbols = day_scores.index.tolist()
                    n = len(symbols)
                    if n == 0:
                        continue
                    weight = strategy.max_position_pct / n if strategy.position_sizing == "equal" else 1.0 / n
                    for sym in symbols:
                        positions_dict[(sym, date)] = min(weight, strategy.max_position_pct)
                
                if positions_dict:
                    pos_df = pd.Series(positions_dict, name="weight")
                    pos_df.index.names = ["symbol", "date"]
                    all_positions[sname] = pos_df.to_frame()
                    print(f"     生成仓位记录: {len(positions_dict)} 条")
            else:
                print(f"     无有效信号")
                
        except Exception as e:
            print(f"     ❌ 策略 {sname} 失败: {e}")
    
    # 4. α 验证
    print("\n📈 步骤 4/5: α 验证 (IC/DSR/正交性/换手调整)...")
    report = run_alpha_validation(
        data=data,
        factor_names=list(factor_panel.columns),
        strategy_names=list(strategies.keys()),
        period_start=str(min_date.date()),
        period_end=str(max_date.date()),
        universe=universe,
    )
    
    # 5. 生成报告
    print("\n📝 步骤 5/5: 生成正式报告...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = os.path.join(WORKSPACE, "analysis", "alpha_reports")
    os.makedirs(report_dir, exist_ok=True)
    
    json_path = os.path.join(report_dir, f"alpha_report_full_{timestamp}.json")
    md_path = os.path.join(report_dir, f"alpha_report_full_{timestamp}.md")
    
    report.save_json(json_path)
    report.save_md(md_path)
    
    # 额外保存：因子面板快照、策略评分、仓位记录
    panel_path = os.path.join(report_dir, f"factor_panel_{timestamp}.parquet")
    factor_panel.to_parquet(panel_path, compression="snappy")
    
    for sname, scores in all_scores.items():
        score_path = os.path.join(report_dir, f"scores_{sname}_{timestamp}.parquet")
        scores.to_parquet(score_path, compression="snappy")
    
    for sname, pos in all_positions.items():
        pos_path = os.path.join(report_dir, f"positions_{sname}_{timestamp}.parquet")
        pos.to_parquet(pos_path, compression="snappy")
    
    print(f"\n✅ 回测完成！")
    print(f"   报告 JSON: {json_path}")
    print(f"   报告 MD:   {md_path}")
    print(f"   因子面板:  {panel_path}")
    print(f"   评分/仓位: {report_dir}/*_{timestamp}.parquet")
    
    # 打印关键摘要
    print("\n" + "="*60)
    print("📋 关键结论摘要")
    print("="*60)
    for k, v in report.summary.items():
        print(f"  {k}: {v}")
    
    print(f"\nIC 统计 (Top 10):")
    for ic in sorted(report.ic_stats, key=lambda x: x.icir, reverse=True)[:10]:
        print(f"  {ic.factor:20s} IC={ic.ic_mean:.4f} ICIR={ic.icir:.3f} P={ic.p_value:.4f} (>0={ic.ic_positive_pct:.1%})")
    
    if report.turnover_adjusted:
        print(f"\n换手调整收益:")
        for t in sorted(report.turnover_adjusted, key=lambda x: x.net_annual_return, reverse=True):
            print(f"  {t.strategy:20s} 毛={t.gross_annual_return:.2%} 净={t.net_annual_return:.2%} 换手={t.annual_turnover:.1%} 夏普={t.sharpe_net:.3f}")
    
    if report.dsr_results:
        print(f"\nDSR (Deflated Sharpe):")
        for d in sorted(report.dsr_results, key=lambda x: x.dsr, reverse=True):
            print(f"  {d.strategy:20s} SR={d.sharpe:.3f} DSR={d.dsr:.3f} P(运气)={d.prob_sharpe_luck:.3f}")
    
    print(f"\n因子正交性: 最大相关性={report.orthogonality.max_abs_corr:.3f}, 有效因子数={report.orthogonality.n_factors_eff:.1f}")
    
    return report


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01", help="开始日期")
    ap.add_argument("--end", default=None, help="结束日期")
    ap.add_argument("--universe", default="watchlist", help="股票池")
    args = ap.parse_args()
    
    run_full_backtest(args.start, args.end, args.universe)


if __name__ == "__main__":
    main()