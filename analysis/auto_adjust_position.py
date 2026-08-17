#!/usr/bin/env python3
"""
030 仓位参数自动微调器
========================
周六 weekly-backtest-strategy-refresh 流程中运行（param_evaluate 之后，LLM 汇总之前）。
读取近 10 交易日实盘/模拟盘成交流水，按 ADJUSTMENT_GUIDE 规则判断是否需微调，
若触发则修改 position_sizer.py 中的当前值，并记录到 data/param_change_log.json。

运行方式：python3 analysis/auto_adjust_position.py
"""

import os
import json
import re
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
DATA_DIR = os.path.join(WORKSPACE, "data")
POSITION_SIZER_FILE = os.path.join(WORKSPACE, "analysis", "position_sizer.py")
PARAM_CHANGE_LOG = os.path.join(DATA_DIR, "param_change_log.json")
TRADES_FILE = os.path.join(DATA_DIR, "portfolio_sim_trades.json")

# ═══════════════════════════════════════════════════════════════
# 微调规则配置（与 position_sizer.py 保持同步）
# ══════════════════════════════════════════════════════════════

ADJUSTABLE_PARAMS = {
    # 仓位矩阵
    "POSITION_MATRIX.上升趋势.修复": {"current_key": ("POSITION_MATRIX", "上升趋势", "修复"), "min": 0.65, "max": 0.80, "step": 0.05},
    "POSITION_MATRIX.震荡筑底.修复": {"current_key": ("POSITION_MATRIX", "震荡筑底", "修复"), "min": 0.40, "max": 0.60, "step": 0.05},
    "POSITION_MATRIX.下跌趋势.冰点": {"current_key": ("POSITION_MATRIX", "下跌趋势", "冰点"), "min": 0.10, "max": 0.20, "step": 0.05},
    # 健康度乘数
    "HEALTH_MULTIPLIER.8-10": {"current_key": ("HEALTH_MULTIPLIER", (8, 10)), "min": 0.9, "max": 1.0, "step": 0.05},
    "HEALTH_MULTIPLIER.6-7": {"current_key": ("HEALTH_MULTIPLIER", (6, 7)), "min": 0.7, "max": 0.9, "step": 0.05},
    "HEALTH_MULTIPLIER.4-5": {"current_key": ("HEALTH_MULTIPLIER", (4, 5)), "min": 0.4, "max": 0.6, "step": 0.05},
    "HEALTH_MULTIPLIER.1-3": {"current_key": ("HEALTH_MULTIPLIER", (1, 3)), "min": 0.1, "max": 0.3, "step": 0.05},
    # 单日单方向上限
    "DIRECTION_MAX_RATIO": {"current_key": ("DIRECTION_MAX_RATIO",), "min": 0.50, "max": 0.70, "step": 0.05},
    # 核心仓单票上限
    "CORE_SINGLE_MAX.上升趋势.修复": {"current_key": ("CORE_SINGLE_MAX_ADJUSTABLE", "上升趋势", "修复", "current"), "min": 0.12, "max": 0.18, "step": 0.02},
    "CORE_SINGLE_MAX.震荡筑底.修复": {"current_key": ("CORE_SINGLE_MAX_ADJUSTABLE", "震荡筑底", "修复", "current"), "min": 0.06, "max": 0.10, "step": 0.02},
    # 卫星仓单票上限
    "SAT_SINGLE_MAX.上升趋势.修复": {"current_key": ("SAT_SINGLE_MAX_ADJUSTABLE", "上升趋势", "修复", "current"), "min": 0.06, "max": 0.10, "step": 0.02},
    "SAT_SINGLE_MAX.震荡筑底.修复": {"current_key": ("SAT_SINGLE_MAX_ADJUSTABLE", "震荡筑底", "修复", "current"), "min": 0.03, "max": 0.08, "step": 0.02},
    # 验证阈值
    "VALIDATION_SCORE_THRESHOLD": {"current_key": ("VALIDATION_SCORE_THRESHOLD_ADJUSTABLE", "current"), "min": 22, "max": 28, "step": 1},
    "VALIDATION_SHARPE_THRESHOLD": {"current_key": ("VALIDATION_SHARPE_THRESHOLD_ADJUSTABLE", "current"), "min": 0.20, "max": 0.30, "step": 0.05},
}

# 硬护栏（不可跨越）
HARD_GUARDRAILS = {
    "fatal_position_zero": 0.0,
    "health_buy_ban": 4,
    "health_buy_half": 6,
    "direction_max_ratio_ceiling": 0.70,
    "core_single_max_ceiling": 0.20,
    "sat_single_max_ceiling": 0.08,
    "validation_score_floor": 20,
    "validation_sharpe_floor": 0.15,
}


def load_trades() -> List[dict]:
    """加载成交流水"""
    if not os.path.exists(TRADES_FILE):
        return []
    try:
        with open(TRADES_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def filter_recent_trades(trades: List[dict], days: int = 10) -> List[dict]:
    """过滤近 N 交易日的成交（按日期去重计算交易日）"""
    if not trades:
        return []
    # 获取所有交易日期
    dates = sorted(set(t.get("date", "") for t in trades if t.get("date")))
    if not dates:
        return []
    # 取最近的 N 个交易日
    recent_dates = set(dates[-days:])
    return [t for t in trades if t.get("date") in recent_dates]


def calc_metrics(trades: List[dict]) -> dict:
    """计算关键指标"""
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "stop_loss_rate": 0.0,
            "avg_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "position_utilization": 0.0,
        }

    # 仅统计 SELL 成交（已平仓的交易）
    sells = [t for t in trades if t.get("action") == "SELL"]
    if not sells:
        return {
            "total_trades": len(trades),
            "win_rate": 0.0,
            "stop_loss_rate": 0.0,
            "avg_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "position_utilization": 0.0,
        }

    pnls = [t.get("pnl", 0) for t in sells]
    returns = [t.get("pnl_pct", 0) / 100.0 for t in sells if "pnl_pct" in t]
    wins = sum(1 for p in pnls if p > 0)
    stop_losses = sum(1 for p in pnls if p < -3)  # 简化：亏损超 3% 视为止损触发

    # 夏普比率（简化：日度收益率均值 / 标准差）
    import math
    if returns and len(returns) > 1:
        mean_r = sum(returns) / len(returns)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1))
        sharpe = mean_r / std_r * math.sqrt(252) if std_r > 0 else 0
    else:
        sharpe = 0

    # 最大回撤（简化：累计收益序列的最大回撤）
    cum = 0
    peak = 0
    max_dd = 0
    for r in returns:
        cum += r
        if cum > peak:
            peak = cum
        dd = (peak - cum) / (1 + peak) if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        "total_trades": len(sells),
        "win_rate": wins / len(sells) if sells else 0,
        "stop_loss_rate": stop_losses / len(sells) if sells else 0,
        "avg_return": sum(pnls) / len(sells) if sells else 0,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "position_utilization": 0.0,  # 需结合仓位计划计算，暂留 0
    }


def evaluate_adjustment_rules(metrics: dict) -> List[dict]:
    """根据 ADJUSTMENT_GUIDE 评估是否需微调，返回建议调整列表"""
    adjustments = []

    win_rate = metrics.get("win_rate", 0)
    stop_loss_rate = metrics.get("stop_loss_rate", 0)
    max_dd = metrics.get("max_drawdown", 0)
    sharpe = metrics.get("sharpe", 0)
    total_trades = metrics.get("total_trades", 0)

    if total_trades < 5:
        return []  # 样本太少，不调整

    # 规则 a: 实际仓位利用率低且胜率高 → 上调矩阵
    # 这里无法直接获取实际仓位利用率，用胜率代理
    if win_rate > 0.55:
        # 上调上升趋势·修复
        adjustments.append({
            "param": "POSITION_MATRIX.上升趋势.修复",
            "direction": "up",
            "reason": f"胜率 {win_rate:.1%} > 55%，资金利用率可能偏低",
        })

    # 规则 b: 止损触发率高或回撤大 → 下调矩阵
    if stop_loss_rate > 0.40 or max_dd > 0.15:
        adjustments.append({
            "param": "POSITION_MATRIX.上升趋势.修复",
            "direction": "down",
            "reason": f"止损率 {stop_loss_rate:.1%} 或 最大回撤 {max_dd:.1%} 偏高",
        })

    # 规则 c: 健康度评分系统性偏高/低 → 调整健康度乘数
    # 无法直接获取，暂跳过

    # 规则 d: 单日单方向频繁触顶 → 上调 DIRECTION_MAX_RATIO
    # 无法直接获取，暂跳过

    # 规则 e: 核心标确实定性高 → 上调 CORE_SINGLE_MAX
    if win_rate > 0.60 and sharpe > 1.0:
        adjustments.append({
            "param": "CORE_SINGLE_MAX.上升趋势.修复",
            "direction": "up",
            "reason": f"核心标的胜率 {win_rate:.1%} 且夏普 {sharpe:.2f} 优异",
        })

    # 规则 f: 验证通过策略太少/太多 → 调整验证阈值
    # 无法直接获取，暂跳过

    # 去重：同一参数若同时有 up 和 down，取优先级高的（down 优先，风控优先）
    final = {}
    for adj in adjustments:
        key = adj["param"]
        if key not in final:
            final[key] = adj
        elif adj["direction"] == "down" and final[key]["direction"] == "up":
            final[key] = adj  # 下调优先

    return list(final.values())


def get_current_value(param_name: str, namespace: dict) -> Any:
    """从 position_sizer.py 的命名空间获取当前值"""
    config = ADJUSTABLE_PARAMS.get(param_name)
    if not config:
        return None
    keys = config["current_key"]
    obj = namespace
    for k in keys:
        if isinstance(obj, dict):
            obj = obj.get(k)
        elif isinstance(obj, (list, tuple)):
            obj = obj[k] if isinstance(k, int) else None
        else:
            return None
        if obj is None:
            return None
    return obj


def set_value(param_name: str, namespace: dict, new_value: float) -> bool:
    """设置 position_sizer.py 命名空间中的值"""
    config = ADJUSTABLE_PARAMS.get(param_name)
    if not config:
        return False
    keys = config["current_key"]
    obj = namespace
    for k in keys[:-1]:
        if isinstance(obj, dict):
            obj = obj.get(k)
        elif isinstance(obj, (list, tuple)):
            obj = obj[k] if isinstance(k, int) else None
        else:
            return False
        if obj is None:
            return False
    last_key = keys[-1]
    if isinstance(obj, dict):
        obj[last_key] = new_value
        return True
    return False


def update_position_sizer_file(adjustments: List[dict]) -> List[dict]:
    """修改 position_sizer.py 文件中的当前值"""
    # 读取文件
    with open(POSITION_SIZER_FILE, "r") as f:
        content = f.read()

    # 执行命名空间以获取当前对象
    namespace = {}
    exec(content, namespace)

    applied = []
    for adj in adjustments:
        param = adj["param"]
        direction = adj["direction"]
        config = ADJUSTABLE_PARAMS.get(param)
        if not config:
            continue

        current = get_current_value(param, namespace)
        if current is None:
            continue

        step = config["step"]
        min_v = config["min"]
        max_v = config["max"]

        if direction == "up":
            new_val = min(current + step, max_v)
        else:
            new_val = max(current - step, min_v)

        # 硬护栏二次检查
        if param == "DIRECTION_MAX_RATIO" and new_val > HARD_GUARDRAILS["direction_max_ratio_ceiling"]:
            new_val = HARD_GUARDRAILS["direction_max_ratio_ceiling"]
        if "CORE_SINGLE_MAX" in param and new_val > HARD_GUARDRAILS["core_single_max_ceiling"]:
            new_val = HARD_GUARDRAILS["core_single_max_ceiling"]
        if "SAT_SINGLE_MAX" in param and new_val > HARD_GUARDRAILS["sat_single_max_ceiling"]:
            new_val = HARD_GUARDRAILS["sat_single_max_ceiling"]
        if param == "VALIDATION_SCORE_THRESHOLD" and new_val < HARD_GUARDRAILS["validation_score_floor"]:
            new_val = HARD_GUARDRAILS["validation_score_floor"]
        if param == "VALIDATION_SHARPE_THRESHOLD" and new_val < HARD_GUARDRAILS["validation_sharpe_floor"]:
            new_val = HARD_GUARDRAILS["validation_sharpe_floor"]

        if abs(new_val - current) < 1e-10:
            continue  # 无变化

        # 在文件中替换
        # 找到对应行并替换
        pattern = rf'"{re.escape(param.split(".")[-1])}"\s*:\s*\{{\s*"current"\s*:\s*{current}\s*,'
        # 简化：直接用 current 值定位替换（假设文件中 current 值唯一）
        old_str = f'"current": {current}'
        new_str = f'"current": {new_val}'
        if old_str in content:
            content = content.replace(old_str, new_str, 1)
            applied.append({
                "param": param,
                "old": current,
                "new": new_val,
                "reason": adj["reason"],
            })
            # 同步命名空间
            set_value(param, namespace, new_val)

    if applied:
        with open(POSITION_SIZER_FILE, "w") as f:
            f.write(content)

    return applied


def log_changes(changes: List[dict]):
    """记录到 param_change_log.json"""
    log = []
    if os.path.exists(PARAM_CHANGE_LOG):
        try:
            with open(PARAM_CHANGE_LOG) as f:
                log = json.load(f)
        except Exception:
            log = []

    for c in changes:
        log.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "param": c["param"],
            "old": c["old"],
            "new": c["new"],
            "reason": c["reason"],
            "operator": "auto_weekly_review"
        })

    with open(PARAM_CHANGE_LOG, "w") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def main():
    print("🔧 启动 030 仓位参数自动微调...")

    trades = load_trades()
    if not trades:
        print("⚠️ 无成交流水，跳过微调")
        return

    recent = filter_recent_trades(trades, days=10)
    if len(recent) < 5:
        print(f"⚠️ 近 10 交易日成交不足 5 笔 ({len(recent)} 笔)，跳过微调")
        return

    metrics = calc_metrics(recent)
    print(f"📊 近 10 日指标: 交易 {metrics['total_trades']} 笔, 胜率 {metrics['win_rate']:.1%}, "
          f"止损率 {metrics['stop_loss_rate']:.1%}, 最大回撤 {metrics['max_drawdown']:.1%}, "
          f"夏普 {metrics['sharpe']:.2f}")

    adjustments = evaluate_adjustment_rules(metrics)
    if not adjustments:
        print("✅ 无需微调，指标在正常范围内")
        return

    print(f"🔍 检测到 {len(adjustments)} 项微调建议:")
    for a in adjustments:
        print(f"  - {a['param']} {a['direction']}: {a['reason']}")

    applied = update_position_sizer_file(adjustments)
    if applied:
        log_changes(applied)
        print(f"✅ 已应用 {len(applied)} 项微调，记录写入 {PARAM_CHANGE_LOG}")
        for a in applied:
            print(f"  - {a['param']}: {a['old']} → {a['new']} ({a['reason']})")
    else:
        print("⚠️ 微调未生效（可能当前值已在边界）")


if __name__ == "__main__":
    main()