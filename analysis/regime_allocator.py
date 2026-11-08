#!/usr/bin/env python3
"""
政权检测 + 动态权重分配 — HMM/阈值法识别趋势/震荡/高波/低波 → 因子权重随政权切换
================================================================================
核心设计：
  - 政权定义：4 态 {趋势向上, 震荡, 趋势向下, 高波动}
  - 检测方法：HMM (高斯) + 阈值法双模式，取交集提高鲁棒性
  - 权重映射：每个政权预设因子权重向量（StrategyComposer 兼容）
  - 平滑切换：政权变更时权重线性插值 5 日，避免频繁换仓
  - 实时性：仅用历史数据，T 日收盘后计算 T+1 权重（无前视）
"""

import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
REGIME_DIR = os.path.join(WORKSPACE, "data", "regime")
os.makedirs(REGIME_DIR, exist_ok=True)

sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

from analysis.factor_registry import get_registry
from analysis.factor_engine import get_engine


# ══════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════

@dataclass
class RegimeConfig:
    """政权配置"""
    name: str
    description: str
    # 因子权重向量（对应 factor_registry 中的因子名）
    factor_weights: Dict[str, float] = field(default_factory=dict)
    # 仓位控制
    max_position_pct: float = 0.1
    position_sizing: str = "equal"
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.15
    # 交易频率
    rebalance_freq: str = "D"
    # 门控调整
    gate_adjustments: Dict[str, Dict] = field(default_factory=dict)


@dataclass
class RegimeState:
    """当前政权状态"""
    regime: str                     # 当前政权名
    probability: float              # 置信度
    duration: int                   # 持续天数
    last_change: str                # 最后切换日期
    smoothed_weights: Dict[str, float]  # 平滑后的因子权重
    metadata: Dict[str, Any] = field(default_factory=dict)


# ═════════════════════════════════════════════════════════════
# 预设政权配置（可外部化 JSON）
# ═════════════════════════════════════════════════════════════

DEFAULT_REGIMES = {
    "trend_up": RegimeConfig(
        name="trend_up",
        description="趋势向上：均线多头排列、MACD 金叉、成交量温和放大",
        factor_weights={
            "EMA_cross_12_26": 0.30,
            "MACD_hist": 0.25,
            "BB_pos_20": 0.15,
            "volume_zscore_20": 0.10,
            "OBV": 0.10,
            "RSI_14": -0.05,      # 超买不追
            "ATR_14": -0.05,      # 波动率收敛
        },
        max_position_pct=0.12,
        position_sizing="score_weighted",
        stop_loss_pct=0.06,
        take_profit_pct=0.18,
        gate_adjustments={
            "trend_ok": {"threshold": 0.5, "action": "pass"},
            "not_overbought": {"threshold": 75, "action": "pass"},
        },
    ),
    "trend_down": RegimeConfig(
        name="trend_down",
        description="趋势向下：均线空头排列、MACD 死叉、成交量萎缩",
        factor_weights={
            "EMA_cross_12_26": -0.30,   # 反向
            "MACD_hist": -0.25,
            "RSI_14": -0.20,           # 超卖反弹
            "BB_pos_20": -0.10,
            "ATR_14": 0.10,            # 高波动=机会
            "KDJ_K": 0.15,
            "CCI_20": 0.10,
        },
        max_position_pct=0.05,         # 降低仓位
        position_sizing="equal",
        stop_loss_pct=0.04,
        take_profit_pct=0.10,
        gate_adjustments={
            "oversold_bounce": {"threshold": 30, "action": "pass"},
        },
    ),
    "sideways": RegimeConfig(
        name="sideways",
        description="震荡/横盘：均线粘合、布林带收窄、RSI 来回穿越 50",
        factor_weights={
            "BB_pos_20": 0.30,         # 区间交易核心
            "RSI_14": -0.25,           # 高抛低吸
            "KDJ_K": 0.20,
            "CCI_20": 0.15,
            "volume_ratio_5_20": 0.10,
            "MACD_hist": 0.0,          # 趋势因子失效
        },
        max_position_pct=0.08,
        position_sizing="vol_target",
        stop_loss_pct=0.03,
        take_profit_pct=0.08,
        gate_adjustments={
            "bb_squeeze": {"threshold": 0.1, "action": "warn"},
            "rsi_range": {"low": 30, "high": 70, "action": "pass"},
        },
    ),
    "high_vol": RegimeConfig(
        name="high_vol",
        description="高波动/恐慌：ATR 激增、成交量暴增、大阴线连续、VIX 飙升",
        factor_weights={
            "ATR_14": 0.30,            # 波动率本身是信号
            "BB_width_20": 0.25,
            "RSI_14": -0.20,           # 极端超卖反弹
            "gap_pct": -0.15,          # 跳空缺口回补
            "amplitude": 0.10,
        },
        max_position_pct=0.06,
        position_sizing="vol_target",
        stop_loss_pct=0.08,            # 宽止损
        take_profit_pct=0.20,
        gate_adjustments={
            "panic_mode": {"threshold": 2.5, "action": "block"},
            "oversold_extreme": {"threshold": 20, "action": "pass"},
        },
    ),
}


# ═════════════════════════════════════════════════════════════
# 政权检测器
# ═════════════════════════════════════════════════════════════

class RegimeDetector:
    """政权检测：HMM + 阈值法双模式"""

    def __init__(self, regimes: Dict[str, RegimeConfig] = None):
        self.regimes = regimes or DEFAULT_REGIMES
        self.engine = get_engine()
        self._hmm_model = None

    def _prepare_features(self, data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """提取市场宽度特征（等权指数代理）"""
        # 计算等权指数
        all_closes = {}
        for sym, df in data.items():
            all_closes[sym] = df["close"]
        closes_df = pd.DataFrame(all_closes)
        ew_index = closes_df.pct_change().mean(axis=1).add(1).cumprod()
        
        # 特征工程
        ret = ew_index.pct_change().dropna()
        features = pd.DataFrame(index=ret.index)
        features["ret_1d"] = ret
        features["ret_5d"] = ew_index.pct_change(5)
        features["ret_20d"] = ew_index.pct_change(20)
        features["vol_20d"] = ret.rolling(20).std() * np.sqrt(252)
        features["vol_60d"] = ret.rolling(60).std() * np.sqrt(252)
        
        # 趋势特征
        features["ma_5"] = ew_index.rolling(5).mean()
        features["ma_20"] = ew_index.rolling(20).mean()
        features["ma_60"] = ew_index.rolling(60).mean()
        features["ma_spread_5_20"] = (features["ma_5"] - features["ma_20"]) / features["ma_20"]
        features["ma_spread_20_60"] = (features["ma_20"] - features["ma_60"]) / features["ma_60"]
        
        # 动量特征
        features["rsi_14"] = self._rsi(ew_index, 14)
        features["macd"] = self._macd(ew_index)
        
        # 成交量特征（等权平均）
        all_vols = {}
        for sym, df in data.items():
            all_vols[sym] = df["volume"]
        vols_df = pd.DataFrame(all_vols)
        ew_vol = vols_df.mean(axis=1)
        features["vol_ratio"] = ew_vol / ew_vol.rolling(20).mean()
        
        return features.dropna()

    def _rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - 100 / (1 + rs)

    def _macd(self, series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
        ema_f = series.ewm(span=fast, adjust=False).mean()
        ema_s = series.ewm(span=slow, adjust=False).mean()
        macd = ema_f - ema_s
        sig = macd.ewm(span=signal, adjust=False).mean()
        return macd - sig

    def detect_threshold(self, features: pd.DataFrame) -> pd.Series:
        """阈值法政权判断（单日）"""
        latest = features.iloc[-1]
        regimes = pd.Series(index=features.index, dtype=object)
        
        for i in range(len(features)):
            row = features.iloc[i]
            regime = "sideways"  # 默认
            
            # 高波动优先判断
            if row["vol_20d"] > row["vol_60d"] * 1.5:
                regime = "high_vol"
            # 趋势判断
            elif row["ma_spread_5_20"] > 0.01 and row["ma_spread_20_60"] > 0:
                regime = "trend_up"
            elif row["ma_spread_5_20"] < -0.01 and row["ma_spread_20_60"] < 0:
                regime = "trend_down"
            # 震荡
            elif abs(row["ma_spread_5_20"]) < 0.005 and abs(row["ma_spread_20_60"]) < 0.005:
                regime = "sideways"
            
            regimes.iloc[i] = regime
        
        return regimes

    def detect_hmm(self, features: pd.DataFrame, n_states: int = 4) -> pd.Series:
        """HMM 政权判断（需 hmmlearn）"""
        try:
            from hmmlearn import hmm
        except ImportError:
            print("⚠️ hmmlearn 未安装，回退阈值法")
            return self.detect_threshold(features)
        
        # 观测向量：收益率 + 波动率 + 趋势斜率
        obs = np.column_stack([
            features["ret_1d"].values,
            features["vol_20d"].values,
            features["ma_spread_5_20"].values,
        ])
        obs = np.nan_to_num(obs, nan=0, posinf=0, neginf=0)
        
        # 标准化
        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler()
        obs_scaled = scaler.fit_transform(obs)
        
        # 训练 HMM
        model = hmm.GaussianHMM(n_components=n_states, covariance_type="full", n_iter=100, random_state=42)
        model.fit(obs_scaled)
        
        # 预测隐状态
        hidden_states = model.predict(obs_scaled)
        
        # 映射状态到政权名（按年化收益排序）
        state_returns = {}
        for s in range(n_states):
            mask = hidden_states == s
            if mask.sum() > 0:
                state_returns[s] = features["ret_1d"].iloc[mask].mean() * 252
            else:
                state_returns[s] = -999
        
        # 排序：最高收益 -> trend_up，最低 -> trend_down，中间 -> sideways/high_vol
        sorted_states = sorted(state_returns.keys(), key=lambda x: state_returns[x])
        state_to_regime = {}
        if len(sorted_states) >= 4:
            state_to_regime[sorted_states[0]] = "trend_down"
            state_to_regime[sorted_states[-1]] = "trend_up"
            state_to_regime[sorted_states[1]] = "sideways"
            state_to_regime[sorted_states[2]] = "high_vol"
        else:
            # 兜底映射
            for i, s in enumerate(sorted_states):
                if i == 0:
                    state_to_regime[s] = "trend_down"
                elif i == len(sorted_states) - 1:
                    state_to_regime[s] = "trend_up"
                else:
                    state_to_regime[s] = "sideways"
        
        regimes = pd.Series([state_to_regime.get(s, "sideways") for s in hidden_states], index=features.index)
        return regimes

    def detect(self, data: Dict[str, pd.DataFrame], method: str = "both") -> pd.Series:
        """主检测入口
        :param method: "threshold" | "hmm" | "both" (取交集，冲突时阈值法为准)
        """
        features = self._prepare_features(data)
        
        if method == "threshold":
            return self.detect_threshold(features)
        elif method == "hmm":
            return self.detect_hmm(features)
        else:  # both
            thresh = self.detect_threshold(features)
            hmm_reg = self.detect_hmm(features)
            # 取交集：两者一致用该值，不一致用阈值法
            combined = thresh.copy()
            combined[thresh != hmm_reg] = thresh[thresh != hmm_reg]
            return combined


# ═════════════════════════════════════════════════════════════
# 权重分配器
# ═════════════════════════════════════════════════════════════

class RegimeAllocator:
    """政权自适应权重分配"""

    def __init__(self, regimes: Dict[str, RegimeConfig] = None, transition_days: int = 5):
        self.regimes = regimes or DEFAULT_REGIMES
        self.transition_days = transition_days
        self.current_state: Optional[RegimeState] = None

    def get_regime_weights(self, regime: str) -> Dict[str, float]:
        """获取政权对应的因子权重"""
        if regime in self.regimes:
            return self.regimes[regime].factor_weights.copy()
        return self.regimes["sideways"].factor_weights.copy()

    def get_regime_config(self, regime: str) -> RegimeConfig:
        return self.regimes.get(regime, self.regimes["sideways"])

    def smooth_transition(
        self,
        prev_weights: Dict[str, float],
        new_weights: Dict[str, float],
        day: int,
    ) -> Dict[str, float]:
        """权重线性插值过渡"""
        if day >= self.transition_days:
            return new_weights
        alpha = day / self.transition_days
        all_keys = set(prev_weights) | set(new_weights)
        smoothed = {}
        for k in all_keys:
            prev = prev_weights.get(k, 0)
            new = new_weights.get(k, 0)
            smoothed[k] = prev * (1 - alpha) + new * alpha
        return smoothed

    def update(
        self,
        regime: str,
        probability: float = 1.0,
        date: str = None,
    ) -> RegimeState:
        """更新当前政权状态"""
        new_weights = self.get_regime_weights(regime)
        
        if self.current_state is None:
            # 首次初始化
            self.current_state = RegimeState(
                regime=regime,
                probability=probability,
                duration=1,
                last_change=date or datetime.now().strftime("%Y-%m-%d"),
                smoothed_weights=new_weights,
            )
        elif self.current_state.regime != regime:
            # 政权切换：开始过渡
            self.current_state = RegimeState(
                regime=regime,
                probability=probability,
                duration=1,
                last_change=date or datetime.now().strftime("%Y-%m-%d"),
                smoothed_weights=self.smooth_transition(
                    self.current_state.smoothed_weights,
                    new_weights,
                    day=1,
                ),
            )
        else:
            # 同政权延续：平滑权重
            self.current_state.duration += 1
            self.current_state.probability = probability
            if self.current_state.duration <= self.transition_days:
                self.current_state.smoothed_weights = self.smooth_transition(
                    self.current_state.smoothed_weights,
                    new_weights,
                    day=self.current_state.duration,
                )
            else:
                self.current_state.smoothed_weights = new_weights
        
        return self.current_state

    def get_current_weights(self) -> Dict[str, float]:
        """获取当前平滑权重"""
        if self.current_state:
            return self.current_state.smoothed_weights
        return self.regimes["sideways"].factor_weights.copy()

    def get_current_config(self) -> RegimeConfig:
        """获取当前政权配置"""
        if self.current_state:
            return self.get_regime_config(self.current_state.regime)
        return self.regimes["sideways"]


# ═════════════════════════════════════════════════════════════
# 集成接口：生成下一交易日策略配置
# ═════════════════════════════════════════════════════════════

def generate_next_day_config(
    data: Dict[str, pd.DataFrame],
    method: str = "both",
) -> Dict[str, Any]:
    """
    一站式：输入历史数据 → 输出下一交易日完整策略配置
    """
    detector = RegimeDetector()
    allocator = RegimeAllocator()
    
    # 1. 检测政权序列
    regime_series = detector.detect(data, method=method)
    current_regime = regime_series.iloc[-1]
    regime_prob = (regime_series == current_regime).mean()  # 简单置信度
    
    # 2. 更新分配器
    state = allocator.update(current_regime, probability=regime_prob)
    
    # 3. 获取配置
    config = allocator.get_current_config()
    weights = allocator.get_current_weights()
    
    # 4. 归一化权重（保证和为 1）
    total_abs = sum(abs(v) for v in weights.values())
    if total_abs > 0:
        norm_weights = {k: v / total_abs for k, v in weights.items()}
    else:
        norm_weights = weights
    
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "regime": current_regime,
        "regime_probability": float(regime_prob),
        "regime_duration": int(state.duration),
        "factor_weights": {k: float(v) for k, v in norm_weights.items()},
        "max_position_pct": float(config.max_position_pct),
        "position_sizing": config.position_sizing,
        "stop_loss_pct": float(config.stop_loss_pct),
        "take_profit_pct": float(config.take_profit_pct),
        "rebalance_freq": config.rebalance_freq,
        "gate_adjustments": {k: {kk: float(vv) if isinstance(vv, (int, float)) else vv for kk, vv in v.items()} for k, v in config.gate_adjustments.items()},
        "regime_history": {str(k): v for k, v in regime_series.tail(20).to_dict().items()},
    }


# ═════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="模拟数据测试")
    ap.add_argument("--method", default="both", choices=["threshold", "hmm", "both"])
    args = ap.parse_args()

    if args.test:
        print("🧪 政权检测 + 权重分配测试...")
        np.random.seed(42)
        dates = pd.date_range("2023-01-01", periods=500, freq="D")
        symbols = [f"{i:06d}" for i in range(1, 21)]

        # 生成带政权切换的模拟数据
        data = {}
        for sym in symbols:
            # 制造 4 个政权段
            n = len(dates)
            seg = n // 4
            rets = np.concatenate([
                np.random.randn(seg) * 0.01 + 0.0008,   # trend_up
                np.random.randn(seg) * 0.015 - 0.0005,  # high_vol
                np.random.randn(seg) * 0.01 - 0.0008,   # trend_down
                np.random.randn(seg) * 0.005,           # sideways
            ])
            close = 100 * np.exp(np.cumsum(rets))
            high = close * (1 + np.abs(np.random.randn(n) * 0.01))
            low = close * (1 - np.abs(np.random.randn(n) * 0.01))
            open_ = close * (1 + np.random.randn(n) * 0.005)
            volume = np.abs(np.random.randn(n) * 1e6 + 5e6)
            
            df = pd.DataFrame({
                "open": open_, "high": high, "low": low, "close": close, "volume": volume
            }, index=dates)
            data[sym] = df

        # 检测
        detector = RegimeDetector()
        regimes = detector.detect(data, method=args.method)
        print(f"政权序列后 20 天:\n{regimes.tail(20)}")
        print(f"\n政权分布:\n{regimes.value_counts()}")

        # 生成配置
        config = generate_next_day_config(data, method=args.method)
        print(f"\n📋 下一交易日配置:")
        print(json.dumps(config, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()