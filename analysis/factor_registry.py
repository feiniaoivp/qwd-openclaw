#!/usr/bin/env python3
"""
因子注册表 — 统一因子元数据管理
====================================
每个因子必须登记：
  - name: 唯一标识（如 "MACD_hist", "RSI_14", "vol_20d_zscore"）
  - category: 分类（trend/momentum/volatility/volume/fundamental/microstructure）
  - data_deps: 数据依赖（["close", "volume", "high", "low", "turnover", "pe_ttm", ...]）
  - lookback: 回看窗口（最小 K 线数，用于 warm-up 判断）
  - freq: 频率（"D"/"W"/"M"；日频为主）
  - compute_fn: 计算函数引用名（字符串，实际函数在 factor_engine 注册）
  - params: 参数字典（可序列化，如 {"fast":12, "slow":26, "signal":9}）
  - version: 版本号（语义化，变更参数/逻辑必须 bump）
  - description: 人读说明
  - tags: 标签列表（用于筛选，如 ["mean_reversion", "overnight"]）
  - neutralize: 是否需中性化（按行业/市值/风格）
  - universe: 适用股票池（"all"/"watchlist"/"hs300"/"zz500"）

注册表本身作为单一事实来源（SSOT），禁止在策略代码里硬编码因子定义。
"""

import os
import sys
import json
from datetime import datetime
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field, asdict
from pathlib import Path

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
REGISTRY_PATH = os.path.join(WORKSPACE, "data", "factor_registry.json")


# ──────────────────────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────────────────────

@dataclass
class FactorMeta:
    name: str
    category: str                    # trend | momentum | volatility | volume | fundamental | microstructure
    data_deps: List[str]             # ["close", "volume", ...]
    lookback: int                    # 最小 K 线数
    freq: str = "D"                  # D | W | M
    compute_fn: str = ""             # factor_engine 中的函数名
    params: Dict[str, Any] = field(default_factory=dict)
    version: str = "1.0.0"
    description: str = ""
    tags: List[str] = field(default_factory=list)
    neutralize: bool = False         # 是否需行业/市值中性化
    universe: str = "watchlist"      # all | watchlist | hs300 | zz500 | custom
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "FactorMeta":
        return cls(**d)

    def validate(self) -> List[str]:
        """返回错误列表，空=通过"""
        errors = []
        if not self.name:
            errors.append("name 为空")
        if self.category not in ("trend", "momentum", "volatility", "volume", "fundamental", "microstructure", "valuation"):
            errors.append(f"category 非法: {self.category}")
        if self.lookback < 1:
            errors.append("lookback 必须 >= 1")
        if self.freq not in ("D", "W", "M"):
            errors.append(f"freq 非法: {self.freq}")
        if self.universe not in ("all", "watchlist", "hs300", "zz500", "custom"):
            errors.append(f"universe 非法: {self.universe}")
        return errors


# ──────────────────────────────────────────────────────────────
# 内置因子定义（种子数据，迁移自现有策略）
# ──────────────────────────────────────────────────────────────

SEED_FACTORS: List[FactorMeta] = [
    # ── 趋势类 ──────────────────────────────────────────────
    FactorMeta(
        name="EMA_12",
        category="trend",
        data_deps=["close"],
        lookback=12,
        compute_fn="ema",
        params={"span": 12},
        description="12 日指数移动平均",
        tags=["trend_following", "fast"],
    ),
    FactorMeta(
        name="EMA_26",
        category="trend",
        data_deps=["close"],
        lookback=26,
        compute_fn="ema",
        params={"span": 26},
        description="26 日指数移动平均",
        tags=["trend_following", "slow"],
    ),
    FactorMeta(
        name="EMA_60",
        category="trend",
        data_deps=["close"],
        lookback=60,
        compute_fn="ema",
        params={"span": 60},
        description="60 日指数移动平均（长期趋势）",
        tags=["trend_following", "long_term"],
    ),
    FactorMeta(
        name="EMA_cross_12_26",
        category="trend",
        data_deps=["close"],
        lookback=26,
        compute_fn="ema_cross",
        params={"fast": 12, "slow": 26},
        version="1.1.0",
        description="EMA12 上穿 EMA26 金叉信号（+1）/ 死叉（-1）",
        tags=["signal", "trend_following"],
    ),
    FactorMeta(
        name="MACD_hist",
        category="trend",
        data_deps=["close"],
        lookback=35,
        compute_fn="macd",
        params={"fast": 12, "slow": 26, "signal": 9},
        description="MACD 柱状图（MACD - Signal）",
        tags=["momentum", "trend_following"],
    ),
    FactorMeta(
        name="MACD_signal",
        category="trend",
        data_deps=["close"],
        lookback=35,
        compute_fn="macd_signal",
        params={"fast": 12, "slow": 26, "signal": 9},
        description="MACD 信号线",
        tags=["momentum", "trend_following"],
    ),
    FactorMeta(
        name="ADX_14",
        category="trend",
        data_deps=["high", "low", "close"],
        lookback=14,
        compute_fn="adx",
        params={"period": 14},
        description="平均趋向指数（趋势强度）",
        tags=["trend_strength"],
    ),

    # ── 动量/反转类 ──────────────────────────────────────────
    FactorMeta(
        name="RSI_14",
        category="momentum",
        data_deps=["close"],
        lookback=14,
        compute_fn="rsi",
        params={"period": 14},
        description="14 日相对强弱指数",
        tags=["mean_reversion", "oversold_overbought"],
    ),
    FactorMeta(
        name="RSI_6",
        category="momentum",
        data_deps=["close"],
        lookback=6,
        compute_fn="rsi",
        params={"period": 6},
        description="6 日 RSI（短期超买超卖）",
        tags=["mean_reversion", "fast"],
    ),
    FactorMeta(
        name="KDJ_K",
        category="momentum",
        data_deps=["high", "low", "close"],
        lookback=9,
        compute_fn="kdj",
        params={"n": 9, "m1": 3, "m2": 3},
        description="KDJ 随机指标 K 值",
        tags=["mean_reversion", "signal"],
    ),
    FactorMeta(
        name="KDJ_D",
        category="momentum",
        data_deps=["high", "low", "close"],
        lookback=9,
        compute_fn="kdj_d",
        params={"n": 9, "m1": 3, "m2": 3},
        description="KDJ D 值（K 的平滑）",
        tags=["mean_reversion", "signal"],
    ),
    FactorMeta(
        name="CCI_20",
        category="momentum",
        data_deps=["high", "low", "close"],
        lookback=20,
        compute_fn="cci",
        params={"period": 20},
        description="顺势指标（偏离度）",
        tags=["mean_reversion", "cyclical"],
    ),
    FactorMeta(
        name="ROC_10",
        category="momentum",
        data_deps=["close"],
        lookback=10,
        compute_fn="roc",
        params={"period": 10},
        description="10 日变动率（动量）",
        tags=["momentum", "trend_following"],
    ),
    FactorMeta(
        name="MOM_20",
        category="momentum",
        data_deps=["close"],
        lookback=20,
        compute_fn="momentum",
        params={"period": 20},
        description="20 日动量（价格差）",
        tags=["momentum"],
    ),

    # ── 波动率类 ────────────────────────────────────────────
    FactorMeta(
        name="ATR_14",
        category="volatility",
        data_deps=["high", "low", "close"],
        lookback=14,
        compute_fn="atr",
        params={"period": 14},
        description="平均真实波幅（止损/仓位用）",
        tags=["risk", "position_sizing"],
    ),
    FactorMeta(
        name="ATR_20",
        category="volatility",
        data_deps=["high", "low", "close"],
        lookback=20,
        compute_fn="atr",
        params={"period": 20},
        description="20 日 ATR",
        tags=["risk", "position_sizing"],
    ),
    FactorMeta(
        name="BB_width_20",
        category="volatility",
        data_deps=["close"],
        lookback=20,
        compute_fn="bb_width",
        params={"period": 20, "std": 2},
        description="布林带宽度（波动率代理）",
        tags=["volatility", "squeeze"],
    ),
    FactorMeta(
        name="BB_pos_20",
        category="volatility",
        data_deps=["close"],
        lookback=20,
        compute_fn="bb_position",
        params={"period": 20, "std": 2},
        description="价格在布林带中的相对位置（0~1）",
        tags=["mean_reversion", "volatility"],
    ),
    FactorMeta(
        name="HV_20",
        category="volatility",
        data_deps=["close"],
        lookback=20,
        compute_fn="historical_vol",
        params={"period": 20},
        description="20 日历史波动率（年化）",
        tags=["volatility", "risk"],
    ),
    FactorMeta(
        name="HV_60",
        category="volatility",
        data_deps=["close"],
        lookback=60,
        compute_fn="historical_vol",
        params={"period": 60},
        description="60 日历史波动率",
        tags=["volatility", "risk", "long_term"],
    ),

    # ── 量价/资金流类 ────────────────────────────────────────
    FactorMeta(
        name="volume_zscore_20",
        category="volume",
        data_deps=["volume"],
        lookback=20,
        compute_fn="zscore",
        params={"period": 20},
        description="成交量 Z-score（异常放量识别）",
        tags=["anomaly", "volume_surge"],
    ),
    FactorMeta(
        name="volume_ratio_5_20",
        category="volume",
        data_deps=["volume"],
        lookback=20,
        compute_fn="volume_ratio",
        params={"short": 5, "long": 20},
        description="5 日/20 日量比",
        tags=["volume_trend"],
    ),
    FactorMeta(
        name="turnover_20",
        category="volume",
        data_deps=["turnover"],
        lookback=20,
        compute_fn="sma",
        params={"period": 20},
        description="20 日换手率均值",
        tags=["liquidity", "activity"],
    ),
    FactorMeta(
        name="OBV",
        category="volume",
        data_deps=["close", "volume"],
        lookback=1,
        compute_fn="obv",
        params={},
        description="能量潮指标（累积量价）",
        tags=["volume_price", "divergence"],
    ),
    FactorMeta(
        name="VWAP_dev",
        category="volume",
        data_deps=["high", "low", "close", "volume"],
        lookback=1,
        compute_fn="vwap_deviation",
        params={},
        description="收盘价偏离 VWAP 程度",
        tags=["intraday", "fair_value"],
    ),
    FactorMeta(
        name="money_flow_index_14",
        category="volume",
        data_deps=["high", "low", "close", "volume"],
        lookback=14,
        compute_fn="mfi",
        params={"period": 14},
        description="资金流量指数（量价 RSI）",
        tags=["money_flow", "mean_reversion"],
    ),

    # ── 基本面类（需基本面数据接入后启用）─────────────────────
    FactorMeta(
        name="PE_TTM",
        category="fundamental",
        data_deps=["pe_ttm"],
        lookback=1,
        compute_fn="pass_through",
        params={},
        description="滚动市盈率（TTM）",
        tags=["valuation", "value"],
        universe="all",
    ),
    FactorMeta(
        name="PB_MRQ",
        category="fundamental",
        data_deps=["pb_mrq"],
        lookback=1,
        compute_fn="pass_through",
        params={},
        description="最新市净率",
        tags=["valuation", "value"],
        universe="all",
    ),
    FactorMeta(
        name="ROE_TTM",
        category="fundamental",
        data_deps=["roe_ttm"],
        lookback=1,
        compute_fn="pass_through",
        params={},
        description="净资产收益率（TTM）",
        tags=["quality", "profitability"],
        universe="all",
    ),
    FactorMeta(
        name="net_profit_margin",
        category="fundamental",
        data_deps=["net_profit_margin"],
        lookback=1,
        compute_fn="pass_through",
        params={},
        description="销售净利率",
        tags=["quality", "profitability"],
        universe="all",
    ),
    FactorMeta(
        name="revenue_yoy",
        category="fundamental",
        data_deps=["revenue_yoy"],
        lookback=1,
        compute_fn="pass_through",
        params={},
        description="营收同比增速",
        tags=["growth"],
        universe="all",
    ),
    FactorMeta(
        name="debt_to_equity",
        category="fundamental",
        data_deps=["debt_to_equity"],
        lookback=1,
        compute_fn="pass_through",
        params={},
        description="资产负债率",
        tags=["leverage", "risk"],
        universe="all",
    ),
    FactorMeta(
        name="current_ratio",
        category="fundamental",
        data_deps=["current_ratio"],
        lookback=1,
        compute_fn="pass_through",
        params={},
        description="流动比率",
        tags=["liquidity", "risk"],
        universe="all",
    ),
    FactorMeta(
        name="quick_ratio",
        category="fundamental",
        data_deps=["quick_ratio"],
        lookback=1,
        compute_fn="pass_through",
        params={},
        description="速动比率",
        tags=["liquidity", "risk"],
        universe="all",
    ),
    FactorMeta(
        name="net_profit_yoy",
        category="fundamental",
        data_deps=["net_profit_yoy"],
        lookback=1,
        compute_fn="pass_through",
        params={},
        description="净利润同比增速",
        tags=["growth", "profitability"],
        universe="all",
    ),
    FactorMeta(
        name="eps_basic",
        category="fundamental",
        data_deps=["eps_basic"],
        lookback=1,
        compute_fn="pass_through",
        params={},
        description="基本每股收益",
        tags=["valuation", "profitability"],
        universe="all",
    ),
    FactorMeta(
        name="book_value_per_share",
        category="fundamental",
        data_deps=["book_value_per_share"],
        lookback=1,
        compute_fn="pass_through",
        params={},
        description="每股净资产",
        tags=["valuation", "quality"],
        universe="all",
    ),
    FactorMeta(
        name="operating_cash_flow_per_share",
        category="fundamental",
        data_deps=["operating_cash_flow_per_share"],
        lookback=1,
        compute_fn="pass_through",
        params={},
        description="每股经营现金流",
        tags=["cash_flow", "quality"],
        universe="all",
    ),
    FactorMeta(
        name="ROE_DILUTED",
        category="fundamental",
        data_deps=["ROE_DILUTED"],
        lookback=1,
        compute_fn="pass_through",
        params={},
        description="净资产收益率-摊薄",
        tags=["quality", "profitability"],
        universe="all",
    ),

    # ── 微观结构/高频代理 ────────────────────────────────────
    FactorMeta(
        name="amplitude",
        category="microstructure",
        data_deps=["high", "low", "open"],
        lookback=1,
        compute_fn="amplitude",
        params={},
        description="振幅 (High-Low)/Open",
        tags=["intraday", "volatility"],
    ),
    FactorMeta(
        name="gap_pct",
        category="microstructure",
        data_deps=["open", "close"],
        lookback=2,
        compute_fn="gap_pct",
        params={},
        description="跳空缺口 (Open - PrevClose) / PrevClose",
        tags=["overnight", "gap"],
    ),
    FactorMeta(
        name="close_to_high",
        category="microstructure",
        data_deps=["high", "close"],
        lookback=1,
        compute_fn="close_to_high",
        params={},
        description="收盘价接近最高价程度",
        tags=["intraday", "strength"],
    ),
    FactorMeta(
        name="upper_shadow",
        category="microstructure",
        data_deps=["high", "close", "open"],
        lookback=1,
        compute_fn="upper_shadow",
        params={},
        description="上影线长度",
        tags=["intraday", "rejection"],
    ),
    FactorMeta(
        name="lower_shadow",
        category="microstructure",
        data_deps=["low", "close", "open"],
        lookback=1,
        compute_fn="lower_shadow",
        params={},
        description="下影线长度",
        tags=["intraday", "support"],
    ),

    # ── 高级技术因子（仅需 OHLCV）──────────────────────────
    FactorMeta(
        name="williams_r",
        category="momentum",
        data_deps=["high", "low", "close"],
        lookback=14,
        compute_fn="williams_r",
        params={"period": 14},
        description="Williams %R（超买超卖）",
        tags=["mean_reversion", "oversold_overbought"],
    ),
    FactorMeta(
        name="stoch_rsi",
        category="momentum",
        data_deps=["close"],
        lookback=14,
        compute_fn="stoch_rsi",
        params={"period": 14, "smooth_k": 3, "smooth_d": 3},
        description="Stochastic RSI（RSI 的随机指标）",
        tags=["mean_reversion", "momentum"],
    ),
    FactorMeta(
        name="chaikin_money_flow",
        category="volume",
        data_deps=["high", "low", "close", "volume"],
        lookback=20,
        compute_fn="chaikin_money_flow",
        params={"period": 20},
        description="Chaikin 资金流量（量价共振）",
        tags=["money_flow", "volume_price"],
    ),
    FactorMeta(
        name="force_index",
        category="volume",
        data_deps=["close", "volume"],
        lookback=13,
        compute_fn="force_index",
        params={"period": 13},
        description="Force Index（艾尔德力度指标）",
        tags=["volume_price", "trend_strength"],
    ),
    FactorMeta(
        name="keltner_upper",
        category="volatility",
        data_deps=["high", "low", "close"],
        lookback=20,
        compute_fn="keltner_channel",
        params={"period": 20, "mult": 2, "which": "upper"},
        description="肯特纳通道上轨",
        tags=["channel", "breakout"],
    ),
    FactorMeta(
        name="keltner_lower",
        category="volatility",
        data_deps=["high", "low", "close"],
        lookback=20,
        compute_fn="keltner_channel",
        params={"period": 20, "mult": 2, "which": "lower"},
        description="肯特纳通道下轨",
        tags=["channel", "breakout"],
    ),
    FactorMeta(
        name="donchian_upper",
        category="volatility",
        data_deps=["high", "low"],
        lookback=20,
        compute_fn="donchian_channel",
        params={"period": 20, "which": "upper"},
        description="唐奇安通道上轨（20日最高价）",
        tags=["channel", "breakout", "trend_following"],
    ),
    FactorMeta(
        name="donchian_lower",
        category="volatility",
        data_deps=["high", "low"],
        lookback=20,
        compute_fn="donchian_channel",
        params={"period": 20, "which": "lower"},
        description="唐奇安通道下轨（20日最低价）",
        tags=["channel", "breakout", "trend_following"],
    ),
    FactorMeta(
        name="supertrend",
        category="trend",
        data_deps=["high", "low", "close"],
        lookback=10,
        compute_fn="supertrend",
        params={"period": 10, "mult": 3},
        description="Supertrend 趋势跟踪指标",
        tags=["trend_following", "trailing_stop"],
    ),
    FactorMeta(
        name="ichimoku_tenkan",
        category="trend",
        data_deps=["high", "low", "close"],
        lookback=9,
        compute_fn="ichimoku",
        params={"which": "tenkan"},
        description="一目均衡表转换线",
        tags=["ichimoku", "trend_following"],
    ),
    FactorMeta(
        name="ichimoku_kijun",
        category="trend",
        data_deps=["high", "low", "close"],
        lookback=26,
        compute_fn="ichimoku",
        params={"which": "kijun"},
        description="一目均衡表基准线",
        tags=["ichimoku", "trend_following"],
    ),
    FactorMeta(
        name="ichimoku_span_a",
        category="trend",
        data_deps=["high", "low", "close"],
        lookback=52,
        compute_fn="ichimoku",
        params={"which": "span_a"},
        description="一目均衡表先行带 A",
        tags=["ichimoku", "trend_following"],
    ),
    FactorMeta(
        name="ichimoku_span_b",
        category="trend",
        data_deps=["high", "low", "close"],
        lookback=52,
        compute_fn="ichimoku",
        params={"which": "span_b"},
        description="一目均衡表先行带 B",
        tags=["ichimoku", "trend_following"],
    ),
    FactorMeta(
        name="elder_ray_bull",
        category="trend",
        data_deps=["high", "close"],
        lookback=13,
        compute_fn="elder_ray",
        params={"period": 13, "which": "bull"},
        description="艾尔德雷牛力（高价 - EMA）",
        tags=["elder_ray", "trend_strength"],
    ),
    FactorMeta(
        name="elder_ray_bear",
        category="trend",
        data_deps=["low", "close"],
        lookback=13,
        compute_fn="elder_ray",
        params={"period": 13, "which": "bear"},
        description="艾尔德雷熊力（低价 - EMA）",
        tags=["elder_ray", "trend_strength"],
    ),
    FactorMeta(
        name="vwap",
        category="microstructure",
        data_deps=["high", "low", "close", "volume"],
        lookback=1,
        compute_fn="vwap",
        params={},
        description="成交量加权平均价（日内）",
        tags=["intraday", "fair_value"],
    ),
    FactorMeta(
        name="pivot_point",
        category="microstructure",
        data_deps=["high", "low", "close"],
        lookback=2,
        compute_fn="pivot_point",
        params={"which": "pp"},
        description="枢轴点（支撑/阻力参考）",
        tags=["support_resistance", "intraday"],
    ),
]


# ──────────────────────────────────────────────────────────────
# 注册表类
# ──────────────────────────────────────────────────────────────

class FactorRegistry:
    """因子注册表：单例模式，持久化到 JSON"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._factors: Dict[str, FactorMeta] = {}
        self._compute_fns: Dict[str, Callable] = {}
        self.load()
        self._initialized = True

    def load(self):
        """从文件加载，不存在则写入种子数据"""
        if os.path.exists(REGISTRY_PATH):
            with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("factors", []):
                fm = FactorMeta.from_dict(item)
                self._factors[fm.name] = fm
            print(f"✅ 因子注册表加载: {len(self._factors)} 个因子")
        else:
            self.seed()
            self.save()

    def seed(self):
        """写入种子因子"""
        for fm in SEED_FACTORS:
            errors = fm.validate()
            if errors:
                print(f"⚠️ 种子因子 {fm.name} 校验失败: {errors}")
                continue
            self._factors[fm.name] = fm
        print(f"✅ 种子因子注入: {len(self._factors)} 个")

    def save(self):
        """持久化到文件"""
        os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
        data = {
            "version": "1.0",
            "updated_at": datetime.now().isoformat(),
            "factors": [fm.to_dict() for fm in self._factors.values()]
        }
        if os.path.exists(REGISTRY_PATH):
            import shutil
            shutil.copy2(REGISTRY_PATH, REGISTRY_PATH + ".bak")  # 写前备份（backup_guard 铁律）
        with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── 增删改查 ──
    def register(self, fm: FactorMeta) -> bool:
        errors = fm.validate()
        if errors:
            raise ValueError(f"因子 {fm.name} 校验失败: {errors}")
        fm.updated_at = datetime.now().isoformat()
        self._factors[fm.name] = fm
        self.save()
        return True

    def unregister(self, name: str) -> bool:
        if name in self._factors:
            del self._factors[name]
            self.save()
            return True
        return False

    def get(self, name: str) -> Optional[FactorMeta]:
        return self._factors.get(name)

    def list_all(self) -> List[FactorMeta]:
        return list(self._factors.values())

    def list_by_category(self, category: str) -> List[FactorMeta]:
        return [fm for fm in self._factors.values() if fm.category == category]

    def list_by_tags(self, tags: List[str], mode: str = "any") -> List[FactorMeta]:
        """mode: any=含任一标签, all=含所有标签"""
        if mode == "any":
            return [fm for fm in self._factors.values() if any(t in fm.tags for t in tags)]
        else:
            return [fm for fm in self._factors.values() if all(t in fm.tags for t in tags)]

    def list_by_universe(self, universe: str) -> List[FactorMeta]:
        return [fm for fm in self._factors.values() if fm.universe == universe]

    def get_names(self) -> List[str]:
        return list(self._factors.keys())

    def get_compute_fn_name(self, name: str) -> Optional[str]:
        fm = self._factors.get(name)
        return fm.compute_fn if fm else None

    # ── 计算函数注册（供 factor_engine 调用）──
    def register_compute_fn(self, name: str, fn: Callable):
        self._compute_fns[name] = fn

    def get_compute_fn(self, name: str) -> Optional[Callable]:
        return self._compute_fns.get(name)

    # ── 批量导出（供策略组合器用）──
    def to_dataframe(self):
        """导出为 pandas DataFrame（需 pandas）"""
        try:
            import pandas as pd
            rows = [fm.to_dict() for fm in self._factors.values()]
            return pd.DataFrame(rows)
        except ImportError:
            return None


# ──────────────────────────────────────────────────────────────
# 全局单例访问器
# ──────────────────────────────────────────────────────────────

_registry: Optional[FactorRegistry] = None

def get_registry() -> FactorRegistry:
    global _registry
    if _registry is None:
        _registry = FactorRegistry()
    return _registry


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="列出所有因子")
    ap.add_argument("--category", help="按分类筛选")
    ap.add_argument("--tags", help="按标签筛选（逗号分隔）")
    ap.add_argument("--universe", help="按股票池筛选")
    ap.add_argument("--show", help="显示单个因子详情")
    ap.add_argument("--export-csv", help="导出 CSV")
    args = ap.parse_args()

    reg = get_registry()

    if args.list:
        factors = reg.list_all()
        if args.category:
            factors = [f for f in factors if f.category == args.category]
        if args.tags:
            tags = [t.strip() for t in args.tags.split(",")]
            factors = [f for f in factors if any(t in f.tags for t in tags)]
        if args.universe:
            factors = [f for f in factors if f.universe == args.universe]
        for f in sorted(factors, key=lambda x: (x.category, x.name)):
            print(f"{f.name:25s} | {f.category:14s} | lb={f.lookback:3d} | {f.description}")
        print(f"\n总计: {len(factors)}")

    if args.show:
        fm = reg.get(args.show)
        if fm:
            print(json.dumps(fm.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"因子不存在: {args.show}")

    if args.export_csv:
        df = reg.to_dataframe()
        if df is not None:
            df.to_csv(args.export_csv, index=False)
            print(f"✅ 导出 CSV: {args.export_csv} ({len(df)} 行)")
        else:
            print("❌ 需安装 pandas")


if __name__ == "__main__":
    main()