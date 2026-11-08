#!/usr/bin/env python3
"""
生成实战级 α 报告（2020+ 长周期历史数据）
数据源: data/stock_data/{sym}.csv（scripts/backfill_stock_data_2020.py 回填 2020-01-01 至今 qfq）
回测区间从数据动态推导，不再硬编码短窗口
"""

import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

from analysis.fib_extension_scan import WATCHLIST
from analysis.factor_engine import get_engine
from analysis.strategy_composer import StrategyComposer, create_seed_strategies, Strategy, ScoringRule
from analysis.alpha_validator import run_alpha_validation, AlphaValidationReport


def main():
    print("="*60)
    print("📊 生成实战级 α 报告（2020+ 长周期历史数据）")
    print("="*60)
    
    # 1. 加载全量数据
    print("\n📥 加载 36 只关注池股票数据...")
    symbols = list(WATCHLIST.keys())
    data = {}
    data_dir = os.path.join(WORKSPACE, "data", "stock_data")
    
    for sym in symbols:
        df = pd.read_csv(os.path.join(data_dir, f"{sym}.csv"))
        df.columns = [c.lower() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df = df[["open","high","low","close","volume"]].sort_index()
        data[sym] = df
    
    print(f"   加载完成: {len(data)} 只股票")
    _first = min(str(df.index.min().date()) for df in data.values())
    _last = max(str(df.index.max().date()) for df in data.values())
    _ndays = max(len(df) for df in data.values())
    print(f"   数据窗口: {_first} ~ {_last} (单只最长 {_ndays} 个交易日)")
    
    # 2. 计算全因子面板
    print("\n📊 计算因子面板...")
    engine = get_engine()
    factor_names = [
        "EMA_12", "EMA_26", "EMA_60", "EMA_cross_12_26",
        "MACD_hist", "MACD_signal", "ADX_14",
        "RSI_14", "RSI_6", "KDJ_K", "KDJ_D", "CCI_20", "ROC_10", "MOM_20",
        "ATR_14", "ATR_20", "BB_width_20", "BB_pos_20", "HV_20", "HV_60",
        "volume_zscore_20", "volume_ratio_5_20", "turnover_20", "OBV",
        "VWAP_dev", "money_flow_index_14",
        "PE_TTM", "PB_MRQ", "ROE_TTM", "net_profit_margin", "revenue_yoy",
        "amplitude", "gap_pct", "close_to_high", "upper_shadow", "lower_shadow"
    ]
    
    # 过滤可用因子（基本面因子需要额外数据源，暂跳过）
    available_factors = [f for f in factor_names if f not in ("PE_TTM", "PB_MRQ", "ROE_TTM", "net_profit_margin", "revenue_yoy")]
    
    engine = get_engine()
    panel = engine.compute_panel(data, available_factors)
    print(f"   面板形状: {panel.shape} ({len(data)} 股票 × {panel.index.get_level_values('date').nunique()} 交易日 × {len(available_factors)} 因子)")
    
    # 3. 策略评分
    print("\n🎯 策略评分与仓位生成...")
    composer = StrategyComposer()
    strategies = create_seed_strategies()
    
    # 补充：为每个策略只保留面板中可用的因子
    for sname, strat in strategies.items():
        avail = [sr.factor for sr in strat.scoring_rules if sr.factor in panel.columns]
        if avail != [sr.factor for sr in strat.scoring_rules]:
            print(f"   策略 {sname}: 可用因子 {len(avail)}/{len(strat.scoring_rules)}")
            strat.scoring_rules = [sr for sr in strat.scoring_rules if sr.factor in panel.columns]
            # 重新归一化权重
            total_w = sum(sr.weight for sr in strat.scoring_rules)
            if total_w > 0:
                for sr in strat.scoring_rules:
                    sr.weight = sr.weight / total_w
    
    # 测试策略：使用可用因子的组合
    strategies["adaptive_test"] = Strategy(
        name="adaptive_test",
        version="1.0.0",
        description="自适应测试策略：趋势+动量+波动+量价（仅用面板可用因子）",
        scoring_rules=[
            ScoringRule(factor="EMA_cross_12_26", weight=0.25, transform="linear", direction=1),
            ScoringRule(factor="MACD_hist", weight=0.20, transform="linear", direction=1),
            ScoringRule(factor="RSI_14", weight=0.15, transform="linear", direction=-1),
            ScoringRule(factor="BB_pos_20", weight=0.15, transform="linear", direction=1),
            ScoringRule(factor="volume_zscore_20", weight=0.10, transform="zscore", direction=1),
            ScoringRule(factor="ATR_14", weight=0.10, transform="zscore", direction=-1),
            ScoringRule(factor="OBV", weight=0.05, transform="zscore", direction=1),
        ],
        gate_rules=[],
        normalize="zscore",
    )
    
    # 4. 运行 α 验证
    print("\n📈 运行 α 验证 (IC/DSR/正交性/换手调整)...")
    strategy_names = [s for s in strategies.keys() if strategies[s].scoring_rules]
    print(f"   测试策略 ({len(strategy_names)}): {strategy_names}")
    
    report = run_alpha_validation(
        data=data,
        factor_names=list(panel.columns),
        strategy_names=strategy_names,
        period_start=_first,
        period_end=_last,
        universe="watchlist",
        cost_per_turn=0.0015,
    )
    
    # 5. 保存报告
    print("\n📝 保存正式报告...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = os.path.join(WORKSPACE, "analysis", "alpha_reports")
    os.makedirs(report_dir, exist_ok=True)
    
    json_path = os.path.join(report_dir, f"alpha_report_first_{timestamp}.json")
    md_path = os.path.join(report_dir, f"alpha_report_first_{timestamp}.md")
    
    report.save_json(json_path)
    report.save_md(md_path)
    
    # 额外保存面板和评分
    panel_path = os.path.join(report_dir, f"factor_panel_{timestamp}.parquet")
    panel.to_parquet(panel_path, compression="snappy")
    
    print(f"\n✅ 第一份正式 α 报告已生成！")
    print(f"   JSON: {json_path}")
    print(f"   MD:   {md_path}")
    print(f"   面板: {panel_path}")
    
    # 打印核心摘要
    print("\n" + "="*60)
    print("📋 报告核心摘要")
    print("="*60)
    for k, v in report.summary.items():
        print(f"  {k}: {v}")
    
    print(f"\nIC 统计 (Top 10 by ICIR):")
    for ic in sorted(report.ic_stats, key=lambda x: x.icir, reverse=True)[:10]:
        print(f"  {ic.factor:20s} IC={ic.ic_mean:.4f} ICIR={ic.icir:.3f} P={ic.p_value:.4f} (>0={ic.ic_positive_pct:.1%} n={ic.n_periods})")
    
    if report.turnover_adjusted:
        print(f"\n换手调整收益 (按净收益排序):")
        for t in sorted(report.turnover_adjusted, key=lambda x: x.net_annual_return, reverse=True):
            print(f"  {t.strategy:20s} 毛={t.gross_annual_return:.2%} 净={t.net_annual_return:.2%} 换手={t.annual_turnover:.1%} 成本拖累={t.total_cost_drag:.2%} 夏普(净)={t.sharpe_net:.3f}")
    
    if report.dsr_results:
        print(f"\nDSR (Deflated Sharpe Ratio):")
        for d in sorted(report.dsr_results, key=lambda x: x.dsr, reverse=True):
            print(f"  {d.strategy:20s} SR={d.sharpe:.3f} DSR={d.dsr:.3f} P(运气)={d.prob_sharpe_luck:.3f} 试验数={d.n_trials}")
    
    print(f"\n因子正交性:")
    print(f"  最大绝对相关性: {report.orthogonality.max_abs_corr:.3f}")
    print(f"  平均绝对相关性: {report.orthogonality.mean_abs_corr:.3f}")
    print(f"  有效因子数 (95%方差): {report.orthogonality.n_factors_eff:.1f}/{len(available_factors)}")
    print(f"  条件数: {report.orthogonality.condition_number:.1f}")
    
    print(f"\n⚠️ 重要提示:")
    print(f"  - 数据窗口 {_first} ~ {_last}（2020+ 长周期，单只最长约 {_ndays} 个交易日）")
    print(f"  - IC 显著性看 p_value / n_periods；DSR 基于全窗口日收益")
    print(f"  - 历史回测统计，不构成投资建议")
    
    return report


if __name__ == "__main__":
    main()