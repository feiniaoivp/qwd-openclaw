#!/usr/bin/env python3
"""
模拟盘激进模式配置加载器（2026-11-03 用户批准）
================================================
单一事实来源: data/sim_aggressive_config.json

作用:
  将模拟盘从「保守防御」切到「激进实测」，提高信号→成交的穿透率，
  用于压力测试与能力验证（纯模拟盘，无真实资金）。

设计原则:
  1. 配置驱动: 所有放宽参数集中在 json，代码只读不写
  2. 一键回滚: enabled=false / 删除配置文件 / 环境变量 SIM_AGGRESSIVE=0 三重开关
  3. 硬保险保留: 030 致命风险(仓位归零/禁买)不因激进模式失效
  4. 放行必留痕: tier2 买入放行等行为写入信号字段供事后归因

被消费方:
  - analysis/position_sizer.py  (030 仓位矩阵/乘数)
  - analysis/portfolio_core.py  (单股上限/簇上限/总仓上限/哑铃/tier过滤/ATR止损)
"""
import os
import sys
import json

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CONFIG_FILE = os.path.join(WORKSPACE, "data", "sim_aggressive_config.json")

# 全部参数默认值（= 标准保守口径），配置文件缺项时回落到这里
DEFAULTS = {
    "enabled": False,
    "mode": "standard",
    # 030 仓位矩阵
    "total_limit_multiplier": 1.0,      # 总仓位上限乘数（夹逼 ≤1.0）
    "single_stock_max_multiplier": 1.0, # 030 矩阵单股上限乘数
    "single_stock_cap": 0.20,           # 乘数放大后的绝对天花板
    "core_single_cap": 0.15,            # 哑铃核心仓单股上限（占单票本金100k）
    "sat_single_cap": 0.05,             # 哑铃卫星仓单股上限
    "health_multiplier_floor": 0.2,     # 健康度乘数下限（越低越保守）
    "high_risk_buy_mult": 0.3,          # 高风险防御买入乘数
    "high_risk_position_mult": 0.5,     # 高风险防御仓位乘数
    "direction_max_ratio": 0.60,        # 单日单方向占比
    # 组合层
    "cluster_expo_cap": 0.25,           # 单簇市值占比上限
    "dumbbell_core_max": 0.80,          # 哑铃核心仓总额占比
    "dumbbell_sat_max": 0.20,           # 哑铃卫星仓总额占比
    # 信号层
    "allow_tier2_buy": False,           # 放行 tier2 买入（留痕）
    "allow_tier3_buy": False,           # 放行 tier3 买入（默认绝不）
    # ATR 止损
    "atr_stop_mult": 2.0,               # ATR 止损倍数（越大越不敏感）
    "risk_per_trade": 0.01,             # ATR 仓位单笔风险
    "max_position_pct": 0.15,           # ATR 仓位单股上限
    # 三因素门控（经 env 传递给 three_factor_helper，其模块导入时读取）
    "gate_buy_line": None,              # None=不覆盖（默认55）；激进建议 45
    "gate_missing_policy": None,        # None=不覆盖（默认fail_closed）；激进建议 ignore
    # 信号钝化
    "streak_max_bonus": 0,              # 连续买入天数上限加成（默认0）
    "stop_cooldown_days": 15,           # 止损后禁回补天数（默认15）
}


def load_aggressive_config() -> dict:
    """读取激进模式配置，合并默认值。

    三重关闭开关（任一生效即禁用）:
      1. 环境变量 SIM_AGGRESSIVE=0
      2. 配置文件 enabled=false / mode != "aggressive"
      3. 配置文件不存在（回落 DEFAULTS，enabled=False）
    配置解析失败: 打印可见告警并禁用（绝不静默吞掉——启用态解析失败
    若被静默处理会让交易者误以为激进生效）。
    """
    cfg = dict(DEFAULTS)
    if os.getenv("SIM_AGGRESSIVE", "1") == "0":
        cfg["enabled"] = False
        cfg["mode"] = "standard"
        cfg["_disabled_reason"] = "env SIM_AGGRESSIVE=0"
        return cfg
    if not os.path.exists(CONFIG_FILE):
        return cfg
    try:
        with open(CONFIG_FILE) as f:
            user_cfg = json.load(f)
        if not isinstance(user_cfg, dict):
            raise ValueError(f"配置须为 dict, 实际 {type(user_cfg).__name__}")
        cfg.update(user_cfg)
    except Exception as e:
        print(f"⚠️ sim_aggressive 配置解析失败, 已回退标准模式: {CONFIG_FILE}: {e}")
        cfg = dict(DEFAULTS)
        cfg["_disabled_reason"] = f"config parse error: {e}"
        return cfg
    if cfg.get("mode") != "aggressive" or not cfg.get("enabled"):
        cfg["enabled"] = False
    return cfg


AGGR = load_aggressive_config()

# 三因素门控 env 传递（必须在 three_factor_helper 首次导入前生效——
# sim 链路中 sim_aggressive 随 portfolio_core 顶层导入，早于 execute_trade
# 内部的惰性 import，时序安全）
if AGGR.get("enabled"):
    if AGGR.get("gate_buy_line") is not None:
        os.environ["GATE_BUY_LINE"] = str(AGGR["gate_buy_line"])
    if AGGR.get("gate_missing_policy"):
        os.environ["GATE_MISSING_POLICY"] = str(AGGR["gate_missing_policy"])


def is_enabled() -> bool:
    return bool(AGGR.get("enabled"))


if __name__ == "__main__":
    # 自检: 打印当前生效配置
    print(json.dumps(AGGR, ensure_ascii=False, indent=2))
    print(f"enabled = {is_enabled()}")
