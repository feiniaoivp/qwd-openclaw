#!/usr/bin/env python3
"""
策略组合器 — 因子权重向量 + 门控逻辑（可序列化 JSON）
=========================================================
核心设计：
  - 策略 = 因子权重字典 + 门控规则列表 + 评分函数
  - 完全可序列化为 JSON（版本控制、回测复现、策略迁移）
  - 支持线性组合、非线性树、条件分支
  - 内置：因子标准化、截面中性化、IC 加权、风险预算
"""

import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Any, Optional, Callable, Union
from dataclasses import dataclass, field, asdict
from pathlib import Path

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
STRATEGY_DIR = os.path.join(WORKSPACE, "data", "strategies")
os.makedirs(STRATEGY_DIR, exist_ok=True)

sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

from analysis.factor_registry import get_registry, FactorMeta
from analysis.factor_engine import get_engine


# ══════════════════════════════════════════════════════════════
# 策略数据结构
# ══════════════════════════════════════════════════════════════

@dataclass
class GateRule:
    """门控规则：评分通过/拦截的条件"""
    name: str                           # 规则名
    factor: str                         # 因子名
    operator: str                       # ">=" | "<=" | ">" | "<" | "==" | "!=" | "between"
    threshold: Union[float, List[float]]  # 阈值（between 用 [low, high]）
    action: str = "block"               # "block" | "warn" | "reduce" | "pass"
    message: str = ""                   # 拦截理由模板
    params: Dict[str, Any] = field(default_factory=dict)

    def evaluate(self, value: float) -> bool:
        """返回 True = 触发动作（拦截/预警）"""
        if self.operator == ">=":
            return value >= self.threshold
        elif self.operator == "<=":
            return value <= self.threshold
        elif self.operator == ">":
            return value > self.threshold
        elif self.operator == "<":
            return value < self.threshold
        elif self.operator == "==":
            return value == self.threshold
        elif self.operator == "!=":
            return value != self.threshold
        elif self.operator == "between":
            low, high = self.threshold
            return low <= value <= high
        return False

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "GateRule":
        return cls(**d)


@dataclass
class ScoringRule:
    """评分规则：因子 → 分数映射"""
    factor: str
    weight: float = 1.0                 # 权重
    transform: str = "linear"           # "linear" | "sigmoid" | "rank" | "zscore" | "clip"
    params: Dict[str, Any] = field(default_factory=dict)  # transform 参数
    direction: int = 1                  # 1=越大越好, -1=越小越好

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "ScoringRule":
        return cls(**d)


@dataclass
class Strategy:
    """完整策略定义（可序列化 JSON）"""
    name: str
    version: str = "1.0.0"
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 评分层
    scoring_rules: List[ScoringRule] = field(default_factory=list)
    normalize: str = "zscore"           # "zscore" | "rank" | "minmax" | "none"
    neutralize: List[str] = field(default_factory=list)  # ["industry", "market_cap"]
    
    # 门控层
    gate_rules: List[GateRule] = field(default_factory=list)
    
    # 仓位/风控层
    position_sizing: str = "equal"      # "equal" | "vol_target" | "kelly" | "risk_parity"
    max_position_pct: float = 0.1       # 单股最大仓位
    stop_loss_pct: float = 0.05         # 止损比例
    take_profit_pct: float = 0.15       # 止盈比例
    
    # 交易规则
    min_score: float = 0.0              # 最低开仓分数
    rebalance_freq: str = "D"           # "D" | "W" | "M"
    allow_short: bool = False
    
    # 元数据
    tags: List[str] = field(default_factory=list)
    universe: str = "watchlist"
    author: str = ""
    parent_strategy: str = ""           # 继承自哪个策略

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["scoring_rules"] = [sr.to_dict() for sr in self.scoring_rules]
        d["gate_rules"] = [gr.to_dict() for gr in self.gate_rules]
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "Strategy":
        scoring = [ScoringRule.from_dict(sr) for sr in d.get("scoring_rules", [])]
        gates = [GateRule.from_dict(gr) for gr in d.get("gate_rules", [])]
        d = d.copy()
        d["scoring_rules"] = scoring
        d["gate_rules"] = gates
        return cls(**d)

    def validate(self) -> List[str]:
        errors = []
        if not self.name:
            errors.append("name 为空")
        if not self.scoring_rules:
            errors.append("至少需要一个评分规则")
        total_weight = sum(sr.weight for sr in self.scoring_rules)
        if abs(total_weight - 1.0) > 1e-6:
            errors.append(f"权重和不为 1.0: {total_weight:.4f}")
        for sr in self.scoring_rules:
            reg = get_registry()
            if reg.get(sr.factor) is None:
                errors.append(f"评分因子不存在于注册表: {sr.factor}")
        for gr in self.gate_rules:
            reg = get_registry()
            if reg.get(gr.factor) is None:
                errors.append(f"门控因子不存在于注册表: {gr.factor}")
        return errors

    def save(self, path: Optional[str] = None) -> str:
        if path is None:
            path = os.path.join(STRATEGY_DIR, f"{self.name}_v{self.version}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            import shutil
            shutil.copy2(path, path + ".bak")  # 写前备份（backup_guard 铁律）
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, path: str) -> "Strategy":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


# ══════════════════════════════════════════════════════════════
# 策略组合器（计算引擎）
# ══════════════════════════════════════════════════════════════

class StrategyComposer:
    """策略评分 + 门控 + 仓位计算"""

    def __init__(self):
        self.registry = get_registry()
        self.engine = get_engine()

    def _normalize_factor(self, series: pd.Series, method: str) -> pd.Series:
        """截面标准化"""
        if method == "zscore":
            mean = float(series.mean())
            std = float(series.std())
            if std == 0 or pd.isna(std):
                return pd.Series(0, index=series.index)
            return (series - mean) / std
        elif method == "rank":
            return series.rank(pct=True) * 2 - 1  # -1 到 1
        elif method == "minmax":
            mn = float(series.min())
            mx = float(series.max())
            if mx == mn or pd.isna(mx) or pd.isna(mn):
                return pd.Series(0, index=series.index)
            return (series - mn) / (mx - mn) * 2 - 1
        return series

    def _apply_transform(self, series: pd.Series, rule: ScoringRule) -> pd.Series:
        """因子变换"""
        s = series.copy()
        if rule.transform == "linear":
            pass
        elif rule.transform == "sigmoid":
            k = rule.params.get("k", 1.0)
            s = 2 / (1 + np.exp(-k * s)) - 1
        elif rule.transform == "rank":
            s = s.rank(pct=True) * 2 - 1
        elif rule.transform == "zscore":
            mean, std = s.mean(), s.std()
            if std > 0:
                s = (s - mean) / std
        elif rule.transform == "clip":
            lo = rule.params.get("lo", -3)
            hi = rule.params.get("hi", 3)
            s = s.clip(lo, hi)
        return s * rule.direction

    def compute_scores(
        self,
        factor_panel: pd.DataFrame,
        strategy: Strategy,
    ) -> pd.DataFrame:
        """
        计算策略评分
        :param factor_panel: MultiIndex(symbol, date) × factor_names
        :param strategy: Strategy 对象
        :return: DataFrame(symbol, date) with columns: score, raw_score, gate_status
        """
        errors = strategy.validate()
        if errors:
            raise ValueError(f"策略校验失败: {errors}")

        # 1. 准备因子数据（只保留策略需要的因子）
        needed_factors = [sr.factor for sr in strategy.scoring_rules]
        available = [f for f in needed_factors if f in factor_panel.columns]
        missing = set(needed_factors) - set(available)
        if missing:
            print(f"⚠️ 缺失因子: {missing}")

        X = factor_panel[available].copy()

        # 2. 截面标准化（每个日期横向标准化）
        if strategy.normalize != "none":
            def _normalize_group(group):
                # group: DataFrame with index=(symbol, date), columns=factors
                # 对每个因子列，在股票截面上标准化
                result = group.copy()
                for col in group.columns:
                    result[col] = self._normalize_factor(group[col], strategy.normalize)
                return result
            X = X.groupby(level="date").apply(_normalize_group).droplevel(0)

        # 3. 因子变换 + 加权求和
        scores = pd.Series(0.0, index=X.index)
        raw_scores = pd.Series(0.0, index=X.index)
        
        for sr in strategy.scoring_rules:
            if sr.factor not in X.columns:
                continue
            transformed = self._apply_transform(X[sr.factor], sr)
            scores += sr.weight * transformed
            raw_scores += sr.weight * X[sr.factor]

        # 4. 中性化（行业/市值）
        for neut in strategy.neutralize:
            if neut == "industry":
                # TODO: 接入行业分类数据
                pass
            elif neut == "market_cap":
                # TODO: 接入市值数据，残差回归
                pass

        # 5. 门控检查
        gate_status = pd.Series("pass", index=X.index)
        gate_details = pd.Series("", index=X.index)
        
        for gr in strategy.gate_rules:
            if gr.factor not in X.columns:
                continue
            factor_vals = X[gr.factor]
            triggered = factor_vals.apply(gr.evaluate)
            gate_status[triggered] = gr.action
            gate_details[triggered] += f"[{gr.name}:{gr.action}] "

        return pd.DataFrame({
            "score": scores,
            "raw_score": raw_scores,
            "gate_status": gate_status,
            "gate_details": gate_details,
        }, index=X.index)

    def compute_positions(
        self,
        scores: pd.DataFrame,
        strategy: Strategy,
        prices: pd.DataFrame,  # symbol × date → close
        equity: float = 1_000_000,
    ) -> pd.DataFrame:
        """
        根据评分计算目标仓位
        :return: DataFrame(symbol, date, target_weight, target_shares, stop_loss, take_profit)
        """
        # 只保留通过门控且分数达标的
        valid = (scores["gate_status"] == "pass") & (scores["score"] >= strategy.min_score)
        filtered = scores[valid].copy()

        if filtered.empty:
            return pd.DataFrame()

        # 按日期分组，每日选股
        results = []
        for date, group in filtered.groupby(level="date"):
            symbols = group.index.get_level_values("symbol")
            day_scores = group["score"]
            
            if strategy.position_sizing == "equal":
                n = len(day_scores)
                weights = pd.Series(1.0 / n, index=day_scores.index)
            elif strategy.position_sizing == "vol_target":
                # 简化：按评分加权
                weights = day_scores / day_scores.sum()
            elif strategy.position_sizing == "score_weighted":
                weights = day_scores.clip(lower=0) / day_scores.clip(lower=0).sum()
            else:
                weights = pd.Series(1.0 / len(day_scores), index=day_scores.index)

            # 单股上限
            weights = weights.clip(upper=strategy.max_position_pct)
            weights = weights / weights.sum()  # 重新归一化

            for sym in symbols:
                price = prices.loc[date, sym] if (date, sym) in prices.index else np.nan
                if np.isnan(price) or price <= 0:
                    continue
                weight = weights.get(sym, 0)
                target_value = equity * weight
                shares = int(target_value / price / 100) * 100  # 整手
                if shares == 0:
                    continue
                
                results.append({
                    "date": date,
                    "symbol": sym,
                    "score": group.loc[(sym, date), "score"] if (sym, date) in scores.index else np.nan,
                    "weight": weight,
                    "target_shares": shares,
                    "target_value": target_value,
                    "stop_loss": price * (1 - strategy.stop_loss_pct),
                    "take_profit": price * (1 + strategy.take_profit_pct),
                    "price": price,
                })

        return pd.DataFrame(results)


# ══════════════════════════════════════════════════════════════
# 内置种子策略（迁移自现有 5 策略 + 自适应策略）
# ══════════════════════════════════════════════════════════════

def create_seed_strategies() -> Dict[str, Strategy]:
    """创建种子策略库"""
    
    strategies = {}

    # 1. 布林带+ATR（趋势跟踪）
    strategies["bollinger_atr"] = Strategy(
        name="bollinger_atr",
        version="1.0.0",
        description="布林带突破 + ATR 动态止损（原策略：趋势跟踪）",
        scoring_rules=[
            ScoringRule(factor="BB_pos_20", weight=0.4, transform="linear", direction=1),
            ScoringRule(factor="BB_width_20", weight=0.2, transform="linear", direction=1),
            ScoringRule(factor="ATR_14", weight=0.2, transform="zscore", direction=-1),
            ScoringRule(factor="EMA_cross_12_26", weight=0.2, transform="linear", direction=1),
        ],
        gate_rules=[
            GateRule(name="bb_breakout", factor="BB_pos_20", operator=">=", threshold=0.8, action="block", message="价格突破布林带上轨"),
            GateRule(name="atr_expansion", factor="ATR_14", operator=">", threshold=2.0, action="warn", message="ATR 异常放大"),
        ],
        normalize="zscore",
        position_sizing="vol_target",
        max_position_pct=0.15,
        stop_loss_pct=0.08,
        tags=["trend_following", "bollinger"],
    )

    # 2. KDJ+RSI（均值回归 v2）
    strategies["kdj_rsi"] = Strategy(
        name="kdj_rsi",
        version="1.0.0",
        description="KDJ 金叉 + RSI 超卖（原策略：均值回归 v2）",
        scoring_rules=[
            ScoringRule(factor="KDJ_K", weight=0.35, transform="linear", direction=1),
            ScoringRule(factor="RSI_14", weight=0.35, transform="linear", direction=-1),  # 越小越好
            ScoringRule(factor="KDJ_D", weight=0.15, transform="linear", direction=1),
            ScoringRule(factor="volume_ratio_5_20", weight=0.15, transform="zscore", direction=1),
        ],
        gate_rules=[
            GateRule(name="rsi_oversold", factor="RSI_14", operator="<=", threshold=30, action="pass", message="RSI 超卖"),
            GateRule(name="kdj_golden", factor="KDJ_K", operator=">=", threshold=80, action="block", message="KDJ 超买"),
        ],
        normalize="rank",
        position_sizing="score_weighted",
        max_position_pct=0.1,
        stop_loss_pct=0.05,
        tags=["mean_reversion", "oversold"],
    )

    # 3. EMA+OBV（量价共振）
    strategies["ema_obv"] = Strategy(
        name="ema_obv",
        version="1.0.0",
        description="EMA20 站上 + OBV 上穿均线（原策略：量价共振）",
        scoring_rules=[
            ScoringRule(factor="EMA_cross_12_26", weight=0.4, transform="linear", direction=1),
            ScoringRule(factor="OBV", weight=0.3, transform="zscore", direction=1),
            ScoringRule(factor="volume_zscore_20", weight=0.2, transform="linear", direction=1),
            ScoringRule(factor="close_to_high", weight=0.1, transform="linear", direction=1),
        ],
        gate_rules=[
            GateRule(name="ema_bullish", factor="EMA_cross_12_26", operator=">=", threshold=1, action="pass", message="EMA 金叉"),
            GateRule(name="volume_surge", factor="volume_zscore_20", operator=">", threshold=2, action="warn", message="异常放量"),
        ],
        normalize="zscore",
        position_sizing="score_weighted",
        max_position_pct=0.12,
        tags=["volume_price", "momentum"],
    )

    # 4. EMA12/26 金叉（基准 C）
    strategies["ema_cross"] = Strategy(
        name="ema_cross",
        version="1.0.0",
        description="EMA12 上穿 EMA26 纯金叉策略（历史最优基准）",
        scoring_rules=[
            ScoringRule(factor="EMA_cross_12_26", weight=0.6, transform="linear", direction=1),
            ScoringRule(factor="MACD_hist", weight=0.4, transform="linear", direction=1),
        ],
        gate_rules=[
            GateRule(name="macd_positive", factor="MACD_hist", operator=">=", threshold=0, action="pass", message="MACD 柱状图转正"),
        ],
        normalize="zscore",
        position_sizing="equal",
        max_position_pct=0.1,
        tags=["trend_following", "baseline"],
    )

    # 5. 纯 MACD（基准 B）
    strategies["macd"] = Strategy(
        name="macd",
        version="1.0.0",
        description="MACD 柱状图由负转正买入，转负卖出（历史次优基准）",
        scoring_rules=[
            ScoringRule(factor="MACD_hist", weight=0.7, transform="linear", direction=1),
            ScoringRule(factor="MACD_signal", weight=0.3, transform="linear", direction=1),
        ],
        gate_rules=[
            GateRule(name="hist_cross_up", factor="MACD_hist", operator=">=", threshold=0, action="pass", message="MACD 柱状图金叉"),
            GateRule(name="hist_cross_down", factor="MACD_hist", operator="<=", threshold=0, action="block", message="MACD 柱状图死叉"),
        ],
        normalize="zscore",
        position_sizing="equal",
        max_position_pct=0.1,
        tags=["trend_following", "macd", "baseline"],
    )

    # 6. 自适应多因子（新：因子库组合）
    strategies["adaptive_multi"] = Strategy(
        name="adaptive_multi",
        version="1.0.0",
        description="多因子自适应：趋势+动量+波动+量价综合评分",
        scoring_rules=[
            ScoringRule(factor="EMA_cross_12_26", weight=0.25, transform="linear", direction=1),
            ScoringRule(factor="MACD_hist", weight=0.2, transform="linear", direction=1),
            ScoringRule(factor="RSI_14", weight=0.15, transform="linear", direction=-1),
            ScoringRule(factor="BB_pos_20", weight=0.15, transform="linear", direction=1),
            ScoringRule(factor="volume_zscore_20", weight=0.1, transform="zscore", direction=1),
            ScoringRule(factor="ATR_14", weight=0.1, transform="zscore", direction=-1),
            ScoringRule(factor="OBV", weight=0.05, transform="zscore", direction=1),
        ],
        gate_rules=[
            GateRule(name="trend_ok", factor="EMA_cross_12_26", operator=">=", threshold=0, action="pass", message="趋势向上"),
            GateRule(name="not_overbought", factor="RSI_14", operator="<", threshold=75, action="pass", message="未超买"),
            GateRule(name="vol_normal", factor="ATR_14", operator="<", threshold=2.5, action="warn", message="波动率正常"),
        ],
        normalize="zscore",
        neutralize=["industry", "market_cap"],
        position_sizing="vol_target",
        max_position_pct=0.08,
        tags=["multi_factor", "adaptive"],
    )

    return strategies


# ══════════════════════════════════════════════════════════════
# CLI / 管理
# ══════════════════════════════════════════════════════════════

def list_strategies():
    for name, strat in create_seed_strategies().items():
        print(f"{name:20s} v{strat.version:6s} | {strat.description}")
        print(f"  评分因子: {[sr.factor for sr in strat.scoring_rules]}")
        print(f"  门控: {[gr.name for gr in strat.gate_rules]}")
        print()


def init_strategy_library():
    """初始化策略库到磁盘"""
    for name, strat in create_seed_strategies().items():
        path = strat.save()
        print(f"✅ 保存策略: {path}")
    print(f"\n策略库初始化完成，共 {len(create_seed_strategies())} 个策略")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="列出种子策略")
    ap.add_argument("--init", action="store_true", help="初始化策略库到磁盘")
    ap.add_argument("--show", help="显示策略详情")
    args = ap.parse_args()

    if args.list:
        list_strategies()

    if args.init:
        init_strategy_library()

    if args.show:
        strategies = create_seed_strategies()
        if args.show in strategies:
            print(json.dumps(strategies[args.show].to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"策略不存在: {args.show}")


if __name__ == "__main__":
    main()