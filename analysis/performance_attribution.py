#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Performance Attribution - 绩效归因分析
=====================================
基于 portfolio_equity.csv 与持仓明细，计算：
1. Brinson 归因（配置效应 + 选股效应 + 交互效应）
2. 因子暴露分析（Barra 风格因子简化版：规模、价值、动量、波动、Beta）
3. 换手率、持仓集中度、流动性成本
4. 周度/月度报告生成

依赖：portfolio_equity.csv, portfolio_sim_state.json, portfolio_sim_trades.json
"""

import os
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
EQUITY_FILE = os.path.join(WORKSPACE, "data", "portfolio_equity.csv")
STATE_FILE = os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")
TRADES_FILE = os.path.join(WORKSPACE, "data", "portfolio_sim_trades.json")
STRATEGY_MAP_FILE = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")

# 板块映射（用于 Brinson 归因的"行业"维度）
SECTOR_MAP = {
    # 证券/金融
    "600030": "金融", "601066": "金融", "600036": "金融", "601995": "金融", "000987": "金融",
    # 半导体/TMT
    "600584": "科技", "688981": "科技", "002156": "科技", "002413": "科技",
    # 新能源/储能
    "300014": "新能源", "002466": "新能源",
    # 高端制造/材料
    "300285": "制造", "603308": "制造", "300124": "制造", "601100": "制造",
    "002318": "制造", "300719": "制造", "002335": "制造", "300748": "制造",
    # 化工
    "600160": "化工", "600346": "化工",
    # 钢铁/特钢
    "000708": "钢铁",
    # 消费/其他
    "600660": "消费", "600570": "科技", "605566": "制造", "000157": "制造", "601061": "材料",
    # 电网装备/特高压
    "600089": "电力设备", "600406": "电力设备", "000400": "电力设备", "601179": "电力设备",
    "002028": "电力设备", "002270": "电力设备", "002130": "电力设备", "600312": "电力设备",
}


def load_equity_curve() -> pd.DataFrame:
    """加载组合权益曲线"""
    if not os.path.exists(EQUITY_FILE):
        return pd.DataFrame()
    df = pd.read_csv(EQUITY_FILE)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def load_state() -> Dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"positions": {}}


def load_trades() -> pd.DataFrame:
    if not os.path.exists(TRADES_FILE):
        return pd.DataFrame()
    with open(TRADES_FILE) as f:
        trades = json.load(f)
    df = pd.DataFrame(trades)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def load_strategy_map() -> Dict[str, str]:
    if os.path.exists(STRATEGY_MAP_FILE):
        with open(STRATEGY_MAP_FILE) as f:
            return json.load(f)
    return {}


def get_sector(symbol: str) -> str:
    return SECTOR_MAP.get(symbol, "其他")


def resample_weekly(equity_df: pd.DataFrame) -> pd.DataFrame:
    """将日频权益曲线重采样为周频（每周最后一个交易日）"""
    if equity_df.empty:
        return pd.DataFrame()
    equity_df = equity_df.copy()
    equity_df.set_index("date", inplace=True)
    weekly = equity_df.resample("W-FRI").last().dropna()
    weekly.reset_index(inplace=True)
    return weekly


def calculate_brinson_attribution(
    equity_df: pd.DataFrame,
    state: Dict,
    strategy_map: Dict,
    benchmark_return: float = None
) -> Dict:
    """
    Brinson 归因分解（简化版）
    返回: 配置效应、选股效应、交互效应、总超额收益
    
    注：完整 Brinson 需要基准指数的板块权重和收益，这里用组合自身的板块分布近似
    """
    if equity_df.empty or len(equity_df) < 2:
        return {}
    
    positions = state.get("positions", {})
    
    # 获取当前持仓板块分布
    sector_weights = {}
    sector_returns = {}
    total_value = sum(p.get("value", 0) for p in positions.values() if p.get("position"))
    
    if total_value == 0:
        return {}
    
    for sym, pos in positions.items():
        if not pos.get("position"):
            continue
        sector = get_sector(sym)
        weight = pos.get("value", 0) / total_value
        ret = pos.get("return_pct", 0) / 100
        
        sector_weights[sector] = sector_weights.get(sector, 0) + weight
        sector_returns[sector] = ret  # 简化：用个股收益代表板块收益
    
    if not sector_weights:
        return {}
    
    # 基准收益（若未提供，用组合总收益）
    if benchmark_return is None:
        benchmark_return = equity_df["total_return_pct"].iloc[-1] / 100 if "total_return_pct" in equity_df.columns else 0
    
    # Brinson 分解
    allocation_effect = 0  # 配置效应
    selection_effect = 0   # 选股效应
    interaction_effect = 0 # 交互效应
    
    for sector, weight in sector_weights.items():
        sector_ret = sector_returns.get(sector, 0)
        # 简化：假设基准板块权重均匀分布，基准板块收益为基准收益
        bench_weight = 1.0 / len(sector_weights) if sector_weights else 0
        bench_ret = benchmark_return
        
        allocation_effect += (weight - bench_weight) * (bench_ret - benchmark_return)
        selection_effect += bench_weight * (sector_ret - bench_ret)
        interaction_effect += (weight - bench_weight) * (sector_ret - bench_ret)
    
    total_excess = sum(w * r for w, r in zip(sector_weights.values(), sector_returns.values())) - benchmark_return
    
    return {
        "total_excess_return": round(total_excess * 100, 4),
        "allocation_effect": round(allocation_effect * 100, 4),
        "selection_effect": round(selection_effect * 100, 4),
        "interaction_effect": round(interaction_effect * 100, 4),
        "benchmark_return": round(benchmark_return * 100, 4),
        "sector_weights": {k: round(v * 100, 2) for k, v in sector_weights.items()},
        "sector_returns": {k: round(v * 100, 2) for k, v in sector_returns.items()},
    }


def calculate_factor_exposure(state: Dict, lookback_days: int = 60) -> Dict:
    """
    简化版因子暴露分析（Barra 风格因子简化版）
    因子：规模、价值、动量、波动、Beta
    基于持仓股票的基本面/技术特征加权平均
    """
    positions = state.get("positions", {})
    total_value = sum(p.get("value", 0) for p in positions.values() if p.get("position"))
    
    if total_value == 0:
        return {}
    
    # 因子评分映射（简化：根据板块/策略给打分）
    factor_scores = {
        "size": {},      # 规模因子（小市值为正）
        "value": {},     # 价值因子（低估值为正）
        "momentum": {},  # 动量因子（近期涨幅为正）
        "volatility": {},# 波动因子（低波动为正）
        "beta": {},      # Beta（高Beta为正）
    }
    
    # 板块因子刻板印象（简化版）
    sector_factors = {
        "金融": {"size": 0.8, "value": 0.6, "momentum": 0.1, "volatility": 0.4, "beta": 1.1},
        "科技": {"size": 0.3, "value": -0.2, "momentum": 0.7, "volatility": 0.8, "beta": 1.3},
        "新能源": {"size": 0.4, "value": -0.3, "momentum": 0.6, "volatility": 0.9, "beta": 1.4},
        "制造": {"size": 0.5, "value": 0.3, "momentum": 0.2, "volatility": 0.5, "beta": 1.0},
        "化工": {"size": 0.6, "value": 0.4, "momentum": 0.1, "volatility": 0.6, "beta": 1.1},
        "钢铁": {"size": 0.7, "value": 0.5, "momentum": 0.0, "volatility": 0.5, "beta": 1.0},
        "消费": {"size": 0.6, "value": 0.2, "momentum": 0.3, "volatility": 0.4, "beta": 0.9},
        "电力设备": {"size": 0.5, "value": 0.3, "momentum": 0.4, "volatility": 0.5, "beta": 1.0},
        "材料": {"size": 0.5, "value": 0.3, "momentum": 0.2, "volatility": 0.5, "beta": 1.0},
        "其他": {"size": 0.0, "value": 0.0, "momentum": 0.0, "volatility": 0.0, "beta": 1.0},
    }
    
    factor_exposure = {f: 0.0 for f in factor_scores}
    
    for sym, pos in positions.items():
        if not pos.get("position"):
            continue
        weight = pos.get("value", 0) / total_value
        sector = get_sector(sym)
        factors = sector_factors.get(sector, sector_factors["其他"])
        
        for factor, score in factors.items():
            factor_exposure[factor] += weight * score
    
    return {k: round(v, 4) for k, v in factor_exposure.items()}


def calculate_turnover(trades_df: pd.DataFrame, equity_df: pd.DataFrame, period_days: int = 7) -> Dict:
    """计算换手率、持仓集中度、流动性成本"""
    if trades_df.empty or equity_df.empty:
        return {}
    
    # 最近 period_days 的交易
    end_date = trades_df["date"].max()
    start_date = end_date - timedelta(days=period_days)
    recent_trades = trades_df[(trades_df["date"] >= start_date) & (trades_df["date"] <= end_date)]
    
    if recent_trades.empty:
        return {"turnover_rate": 0, "buy_count": 0, "sell_count": 0, "avg_holding_days": 0}
    
    buy_trades = recent_trades[recent_trades["action"] == "BUY"]
    sell_trades = recent_trades[recent_trades["action"] == "SELL"]
    
    # 平均持仓天数（卖出时计算）
    holding_days = []
    for _, sell in sell_trades.iterrows():
        sym = sell["symbol"]
        buy_records = buy_trades[(buy_trades["symbol"] == sym) & (buy_trades["date"] < sell["date"])]
        if not buy_records.empty:
            last_buy = buy_records.iloc[-1]["date"]
            holding_days.append((sell["date"] - last_buy).days)
    
    avg_holding = np.mean(holding_days) if holding_days else 0
    
    # 换手率 = 买入金额 / 平均组合市值
    avg_nav = equity_df.tail(period_days)["total_value"].mean() if "total_value" in equity_df.columns else 0
    buy_amount = (buy_trades["price"] * buy_trades["shares"]).sum() if not buy_trades.empty else 0
    turnover = buy_amount / avg_nav if avg_nav > 0 else 0
    
    return {
        "turnover_rate": round(turnover * 100, 2),
        "buy_count": len(buy_trades),
        "sell_count": len(sell_trades),
        "buy_amount": round(buy_amount, 2),
        "sell_amount": round((sell_trades["price"] * sell_trades["shares"]).sum(), 2) if not sell_trades.empty else 0,
        "avg_holding_days": round(avg_holding, 1),
    }


def calculate_concentration(state: Dict) -> Dict:
    """持仓集中度（Herfindahl 指数、前5大权重）"""
    positions = state.get("positions", {})
    held = [p for p in positions.values() if p.get("position")]
    if not held:
        return {"hhi": 0, "top5_weight": 0, "num_positions": 0, "max_weight": 0}
    
    total = sum(p.get("value", 0) for p in held)
    if total == 0:
        return {"hhi": 0, "top5_weight": 0, "num_positions": len(held), "max_weight": 0}
    
    weights = [p.get("value", 0) / total for p in held]
    weights.sort(reverse=True)
    
    hhi = sum(w * w for w in weights)
    top5 = sum(weights[:5])
    
    return {
        "hhi": round(hhi, 4),
        "top5_weight": round(top5 * 100, 2),
        "num_positions": len(held),
        "max_weight": round(weights[0] * 100, 2) if weights else 0,
    }


def generate_weekly_report(week_end: str = None) -> str:
    """生成周度绩效归因报告"""
    if week_end is None:
        week_end = datetime.now().strftime("%Y-%m-%d")
    
    equity_df = load_equity_curve()
    if equity_df.empty:
        return "❌ 无权益曲线数据"
    
    # 筛选本周数据
    end_date = pd.to_datetime(week_end)
    start_date = end_date - timedelta(days=6)
    weekly_equity = equity_df[(equity_df["date"] >= start_date) & (equity_df["date"] <= end_date)]
    
    if weekly_equity.empty:
        return f"❌ {week_end} 当周无数据"
    
    state = load_state()
    trades_df = load_trades()
    strategy_map = load_strategy_map()
    
    # 本周收益
    week_start_nav = weekly_equity["total_value"].iloc[0]
    week_end_nav = weekly_equity["total_value"].iloc[-1]
    week_return = (week_end_nav - week_start_nav) / week_start_nav * 100
    
    # Brinson 归因
    brinson = calculate_brinson_attribution(weekly_equity, state, strategy_map)
    
    # 因子暴露
    factors = calculate_factor_exposure(state)
    
    # 换手率
    turnover = calculate_turnover(trades_df, equity_df, 7)
    
    # 集中度
    concentration = calculate_concentration(state)
    
    # 生成报告
    lines = []
    lines.append(f"📊 周度绩效归因报告 ({start_date.strftime('%m-%d')} ~ {week_end})")
    lines.append("=" * 50)
    lines.append(f"\n💰 本周收益: {week_return:+.2f}%")
    lines.append(f"   期初净值: ¥{week_start_nav:,.0f}")
    lines.append(f"   期末净值: ¥{week_end_nav:,.0f}")
    
    lines.append(f"\n📈 Brinson 归因分解:")
    lines.append(f"   超额收益: {brinson.get('total_excess_return', 0):+.4f}%")
    lines.append(f"   配置效应: {brinson.get('allocation_effect', 0):+.4f}%")
    lines.append(f"   选股效应: {brinson.get('selection_effect', 0):+.4f}%")
    lines.append(f"   交互效应: {brinson.get('interaction_effect', 0):+.4f}%")
    
    lines.append(f"\n🎯 因子暴露:")
    for f, v in factors.items():
        lines.append(f"   {factor_names.get(f, f)}: {v:+.4f}")
    
    lines.append(f"\n🔄 换手率分析 (周度):")
    lines.append(f"   换手率: {turnover.get('turnover_rate', 0):.2f}%")
    lines.append(f"   买入笔数: {turnover.get('buy_count', 0)} | 卖出笔数: {turnover.get('sell_count', 0)}")
    lines.append(f"   平均持仓天数: {turnover.get('avg_holding_days', 0)}")
    
    lines.append(f"\n📊 持仓集中度:")
    lines.append(f"   HHI指数: {concentration.get('hhi', 0):.4f}")
    lines.append(f"   前5大权重: {concentration.get('top5_weight', 0):.2f}%")
    lines.append(f"   最大单票: {concentration.get('max_weight', 0):.2f}%")
    lines.append(f"   持仓数量: {concentration.get('num_positions', 0)}")
    
    return "\n".join(lines)


factor_names = {
    "size": "规模因子",
    "value": "价值因子", 
    "momentum": "动量因子",
    "volatility": "低波动因子",
    "beta": "Beta因子",
}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="绩效归因分析")
    parser.add_argument("--week-end", help="周结束日期 YYYY-MM-DD (默认本周五)")
    parser.add_argument("--save", action="store_true", help="保存报告到 analysis/daily/")
    args = parser.parse_args()
    
    if args.week_end:
        week_end = args.week_end
    else:
        # 默认本周五
        today = datetime.now()
        week_end = (today + timedelta(days=(4 - today.weekday()) % 7)).strftime("%Y-%m-%d")
    
    report = generate_weekly_report(week_end)
    print(report)
    
    if args.save:
        os.makedirs(os.path.join(WORKSPACE, "analysis", "daily"), exist_ok=True)
        report_file = os.path.join(WORKSPACE, "analysis", "daily", f"{week_end}_attribution.md")
        with open(report_file, "w") as f:
            f.write(report)
        print(f"\n✅ 报告已保存: {report_file}")


if __name__ == "__main__":
    main()