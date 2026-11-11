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
    """门控规则：评分通过/拦截的条件

    action 语义（2026-11-10 修复，require 语法定案）：
      - "pass"   = require 必要条件：规则描述"何时允许开仓"；不满足 → block
                   （旧实现下不满足时样本保持初始 pass，require 门零约束力）
      - "block"  = 满足条件时拦截
      - "warn"   = 满足条件时告警但放行（留痕 gate_details）
      - "reduce" = 满足条件时降仓位（当前实现同 warn 放行，未实现降权）
    门控评估在【原始因子值】上进行，与阈值设计值域一致；
    NaN 在 require 门下视为不满足（fail-closed，缺数据拦截）。
    """
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
            if gr.action not in ("block", "warn", "reduce", "pass"):
                errors.append(f"门控 action 非法: {gr.name} -> {gr.action}")
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

        # 5. 门控检查 —— 在【原始因子值】上评估（标准化只影响评分，不影响门控阈值；
        #    2026-11-10 修复：此前在 zscore/rank 后的 X 上评估，按原始值域设计的阈值全部漂移，
        #    如 RSI<=30 在 zscore 口径下永不触发 → require 门整体失效）
        gate_status = pd.Series("pass", index=X.index)
        gate_details = pd.Series("", index=X.index)

        for gr in strategy.gate_rules:
            if gr.factor not in factor_panel.columns:
                continue
            factor_vals = factor_panel[gr.factor].reindex(X.index)
            triggered = factor_vals.apply(gr.evaluate)

            if gr.action == "pass":
                # require 语义：不满足 → block（NaN 比较得 False → 视为不满足，fail-closed）
                failed = ~triggered.fillna(False)
                gate_status[failed] = "block"
                gate_details[failed] += f"[{gr.name}:require_fail] "
            elif gr.action == "block":
                trig = triggered.fillna(False)
                gate_status[trig] = "block"
                gate_details[trig] += f"[{gr.name}:block] "
            else:
                # warn / reduce：触发时留痕，但不得覆盖已有 block（拦截优先于告警，
                # 2026-11-10 修复：旧顺序覆盖会洗掉 require 门刚设的 block）
                trig = triggered.fillna(False)
                apply_mask = trig & (gate_status != "block")
                gate_status[apply_mask] = gr.action
                gate_details[trig] += f"[{gr.name}:{gr.action}] "

        return pd.DataFrame({
            "score": scores,
            "raw_score": raw_scores,
            "gate_status": gate_status,
            "gate_details": gate_details,
        }, index=X.index)

    def compute_target_weights(self, scores: pd.DataFrame, strategy: Strategy) -> pd.Series:
        """纯权重层 SSOT：门控过滤 + min_score + sizing + 单股上限 + 归一化。

        2026-11-11 抽取：此前 alpha_validator 与 run_full_backtest 各有一份内联等权循环
        （不过滤 min_score、无视 sizing 语义）→ 同门策略收益必然完全同路
        （ema_obv ≡ ema_cross 收益全同事故根因）。三处消费方统一走本函数。
        返回：MultiIndex (symbol, date) → weight 的 Series（日内和=1）；空策略返回空 Series。
        """
        # 只保留未被拦截且分数达标的（warn/reduce 放行留痕，仅 block 拦截；
        # 2026-11-10 修复：旧 == "pass" 条件把 warn 告警样本也当无效信号丢弃）
        valid = (scores["gate_status"] != "block") & (scores["score"] >= strategy.min_score)
        filtered = scores[valid]
        if filtered.empty:
            return pd.Series(dtype=float)

        out = {}
        for date, group in filtered.groupby(level="date"):
            day_scores = group["score"]
            n = len(day_scores)
            if strategy.position_sizing in ("vol_target", "score_weighted"):
                w = day_scores.clip(lower=0)
                # vol_target 旧实现 day_scores/day_scores.sum() 未 clip 负分 → 长多组合出现
                # 负权重（隐性做空，2026-11-11 修复）；全零分回退等权
                weights = w / w.sum() if w.sum() > 0 else pd.Series(1.0 / n, index=day_scores.index)
            else:
                weights = pd.Series(1.0 / n, index=day_scores.index)

            # 单股上限 + 重新归一化
            weights = weights.clip(upper=strategy.max_position_pct)
            weights = weights / weights.sum()
            # 注：weights 的索引是 group 的 (symbol, date) MultiIndex 子集，items() 直接产出完整键
            for idx_key, wv in weights.items():
                out[idx_key] = float(wv)
        s = pd.Series(out, name="weight")
        s.index.names = ["symbol", "date"]
        return s

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
        weights = self.compute_target_weights(scores, strategy)
        if weights.empty:
            return pd.DataFrame()

        results = []
        for (sym, date), weight in weights.items():
            # 宽表约定：index=date, columns=symbol（旧代码 (date, sym) in prices.index
            # 对单层 DatetimeIndex 永远 False → 价格恒 NaN → 恒返回空仓，2026-11-10 修复）
            if date not in prices.index or sym not in prices.columns:
                continue
            price = float(prices.loc[date, sym])
            if np.isnan(price) or price <= 0:
                continue
            target_value = equity * weight
            shares = int(target_value / price / 100) * 100  # 整手
            if shares == 0:
                continue

            results.append({
                "date": date,
                "symbol": sym,
                "score": scores.loc[(sym, date), "score"] if (sym, date) in scores.index else np.nan,
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
            # require：BB 位置 >= 0.8（触及上轨）才允许开仓 —— 原写 action="block" 把突破门写反
            # （变成"突破时拦截"，与原策略"突破上轨买入"相反），2026-11-10 修复
            GateRule(name="bb_breakout", factor="BB_pos_20", operator=">=", threshold=0.8, action="pass", message="价格触及布林带上轨（require）"),
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
            # require：多头动能状态（MACD hist >= 0）—— 原用事件型 EMA_cross_12_26>=1 做 require 门
            # 会退化为"仅金叉当日可开仓"（该因子 96.7% 为 0），2026-11-10 改用状态型因子
            GateRule(name="ema_bullish", factor="MACD_hist", operator=">=", threshold=0, action="pass", message="多头动能（MACD hist >= 0）"),
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
            # require：多头动能状态 —— 原 EMA_cross_12_26>=0 事件型门在 96.7% 零值下仅拦死叉当日，
            # 趋势过滤形同虚设，2026-11-10 改用状态型 MACD_hist
            GateRule(name="trend_ok", factor="MACD_hist", operator=">=", threshold=0, action="pass", message="趋势向上"),
            GateRule(name="not_overbought", factor="RSI_14", operator="<", threshold=75, action="pass", message="未超买"),
            # 反向修正：原 ATR_14<2.5 触发 warn（把"波动正常"当告警条件，绝对阈值跨价格不可比，
            # zscore 口径下曾反向筛入高波动股），2026-11-10 改为年化波动率 HV_20>0.6 告警（量纲无关）
            GateRule(name="vol_normal", factor="HV_20", operator=">", threshold=0.6, action="warn", message="波动率偏高告警"),
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