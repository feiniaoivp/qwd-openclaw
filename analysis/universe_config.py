#!/usr/bin/env python3
"""
Universe Filter 配置 — 单一事实来源（2026-11-09 用户拍板四层漏斗方案）
=====================================================================
所有阈值集中在 data/universe_filter_config.json，周复盘只改这里。
代码只读不写（除首次生成默认配置）。

设计原则（对齐 AGENTS.md 三层防线）:
  1. 文档层: 本文件注释即文档
  2. 门禁层: universe_filter_guard.py 校验配置合法性（待建）
  3. 流水线层: cron universe-l1-daily / universe-l3-daily 消费
"""

import os
import sys
import json
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Tuple

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
CONFIG_PATH = os.path.join(WORKSPACE, "data", "universe_filter_config.json")


@dataclass
class L1HardFilterConfig:
    """L1 硬性准入 — 全市场 → 可交易池，剔除 80% 噪声"""
    min_daily_amount: float = 5e7        # 近20日日均成交额 ≥ 5000万
    min_circ_mcap: float = 3e9           # 流通市值 ≥ 30亿
    max_circ_mcap: float = 2e11          # 流通市值 ≤ 2000亿
    min_listing_days: int = 180          # 上市 ≥ 6个月（剔次新）
    exclude_st: bool = True              # 剔 ST/*ST
    exclude_delisting: bool = True       # 剔退市风险警示
    exclude_bse: bool = True             # 剔北交所 (8/4 开头)
    exclude_star_unprofitable: bool = True  # 剔科创板未盈利 (688 且 eps<0)
    # 数量护栏：超出上限按流动性截断，低于下限告警（不自动放宽，防静默降级）
    target_min: int = 800
    target_max: int = 1800


@dataclass
class L2QualityAnchorConfig:
    """L2 质量锚定 — 可交易池 → 核心池"""
    index_universe: List[str] = field(default_factory=lambda: [
        "hs300", "zz500", "zz1000", "sz50",
    ])
    industry_top_n: int = 5              # 申万一级行业营收前 N 补充
    min_institutional_pct: float = 0.02  # 机构持仓 ≥ 2%（数据缺失 fail-open 计 0 不拦）
    require_profit_ttm: bool = True      # 要求 TTM 盈利（财报缺失 fail-open 保留）
    target_min: int = 250
    target_max: int = 600


@dataclass
class L3SignalResonanceConfig:
    """L3 信号共振 — 核心池 → 候选名单（多维打分）"""
    # 维度权重 Σ=1；政权感知调整由 regime_allocator 提供
    dim_weights: Dict[str, float] = field(default_factory=lambda: {
        "trend": 0.30,
        "volume": 0.25,
        "chip": 0.20,
        "volatility": 0.15,
        "valuation": 0.10,
    })
    # 硬门槛：同时达标才算共振（缺数据 = 该维度 0 分，不默认中性 50 —
    # 对齐 ERR-20261018-C03：缺数据 ≠ 中性分）
    # 2026-11-10: 默认值以 signal_scoring.py 生产实跑值为准修正（0.20/0.90 → 0.15/0.92），
    # 消除「CFG 回落默认 vs 脚本硬编码」fork；signal_scoring 已改读本配置。
    hard_gates: Dict[str, float] = field(default_factory=lambda: {
        "bb_pos_low": 0.15,       # BB_pos_20 ≥ 0.15（不在破位下轨）
        "bb_pos_high": 0.92,      # BB_pos_20 ≤ 0.92（不极端超买）
        "volume_zscore_min": 0.3, # 温和放量以上
    })
    top_k: int = 15                # 候选上限
    min_score: float = 45.0        # 综合评分地板（0-100 归一后）
    regime_boost: Dict[str, float] = field(default_factory=lambda: {
        "trend_up": 1.10,
        "sideways": 1.00,
        "trend_down": 0.85,
        "high_vol": 0.90,
    })


@dataclass
class SeedPoolConfig:
    """种子库动态进化"""
    core_protected: bool = True        # core 层不自动降级（人工管理）
    promotion_days: int = 3            # 连续 N 日进候选前 15% → observe
    promotion_top_pct: float = 0.15    # 候选池前 15%
    confirmation_days: int = 7         # observe 满 N 日且评分中位 > 池中位 → watch
    demotion_days: int = 10            # 连续 N 日评分 < 降级线 → candidate
    demotion_score: float = 35.0
    watch_cap: int = 50                # watch+observe 总上限（防池子无序膨胀）
    history_window: int = 20           # 评分/排名历史保留窗口


@dataclass
class UniverseFilterConfig:
    l1: L1HardFilterConfig = field(default_factory=L1HardFilterConfig)
    l2: L2QualityAnchorConfig = field(default_factory=L2QualityAnchorConfig)
    l3: L3SignalResonanceConfig = field(default_factory=L3SignalResonanceConfig)
    seed: SeedPoolConfig = field(default_factory=SeedPoolConfig)
    version: str = "1.0.0"
    updated_at: str = ""

    def validate(self) -> List[str]:
        """配置合法性校验（门禁层入口）"""
        errors = []
        if abs(sum(self.l3.dim_weights.values()) - 1.0) > 1e-6:
            errors.append(f"L3.dim_weights Σ={sum(self.l3.dim_weights.values()):.4f} ≠ 1.0")
        if self.l1.target_min > self.l1.target_max:
            errors.append("L1.target_min > target_max")
        if self.l3.top_k < 1 or self.l3.top_k > 50:
            errors.append(f"L3.top_k={self.l3.top_k} 越界 [1,50]")
        if not (0 < self.seed.promotion_top_pct <= 0.5):
            errors.append(f"seed.promotion_top_pct={self.seed.promotion_top_pct} 越界 (0,0.5]")
        return errors

    def save(self):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        d = asdict(self)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls) -> "UniverseFilterConfig":
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 嵌套 dataclass 还原
                return cls(
                    l1=L1HardFilterConfig(**data.get("l1", {})),
                    l2=L2QualityAnchorConfig(**data.get("l2", {})),
                    l3=L3SignalResonanceConfig(**data.get("l3", {})),
                    seed=SeedPoolConfig(**data.get("seed", {})),
                    version=data.get("version", "1.0.0"),
                    updated_at=data.get("updated_at", ""),
                )
            except Exception as e:
                print(f"⚠️ universe 配置解析失败，回落默认: {e}")
        cfg = cls()
        return cfg


CFG = UniverseFilterConfig.load()

if __name__ == "__main__":
    errs = CFG.validate()
    if errs:
        print("❌ 配置非法:")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    print("✅ universe_filter_config 合法")
    print(json.dumps(asdict(CFG), ensure_ascii=False, indent=2))
