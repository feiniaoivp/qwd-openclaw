#!/usr/bin/env python3
"""
动态仓位计算器 (030体系核心仓位管理模块)
=========================================
替代 portfolio_sim.py 中固定 10万/股 的分配逻辑
输入：市场阶段 + 情绪周期 + 健康度分 + 致命风险状态 + 总资金
输出：动态仓位计划，含总仓位上限、单股上限、方向分配、买入信号乘数

完全对齐 030 仓位矩阵：
| 仓位矩阵 | 冰点 | 修复 | 高潮 |
|---------|------|------|------|
| **上升趋势** | 5成 | 7-8成 | 5成 |
| **震荡筑底** | 3成 | 5成 | 3成 |
| **下跌趋势** | 1-2成 | 2-3成 | 空仓或1成 |
| **致命风险触发** | 0 | 0 | 0 |
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
DATA_DIR = os.path.join(WORKSPACE, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ═══════════════════════════════════════════════════════════════
# 030 仓位矩阵（硬编码核心参数，仅允许微调范围内调整）
# ═══════════════════════════════════════════════════════════════

POSITION_MATRIX = {
    "上升趋势": {"冰点": 0.50, "修复": 0.75, "高潮": 0.50},
    "震荡筑底": {"冰点": 0.30, "修复": 0.50, "高潮": 0.30},
    "下跌趋势": {"冰点": 0.15, "修复": 0.25, "高潮": 0.05},
    "致命风险": {"冰点": 0.00, "修复": 0.00, "高潮": 0.00},
}

# 健康度分 → 仓位乘数（健康度作为矩阵基础上的调节器）
HEALTH_MULTIPLIER = {
    (8, 10): 1.0,   # 健康，全额执行矩阵仓位
    (6, 7): 0.8,    # 正常，打八折
    (4, 5): 0.5,    # 偏弱，减半
    (1, 3): 0.2,    # 恶劣，仅两成
}

# 单股最大仓位限制（占总资金比例）
SINGLE_STOCK_MAX = {
    "上升趋势": {"冰点": 0.08, "修复": 0.12, "高潮": 0.08},
    "震荡筑底": {"冰点": 0.05, "修复": 0.08, "高潮": 0.05},
    "下跌趋势": {"冰点": 0.03, "修复": 0.05, "高潮": 0.02},
    "致命风险": {"冰点": 0.00, "修复": 0.00, "高潮": 0.00},
}

# 单日单方向最大仓位（占总仓位上限比例）
DIRECTION_MAX_RATIO = 0.60  # 单日单方向不超过总仓位上限的60%

# ════════════════════════════════════════════════════════════════
# 030 微调规则配置区（护栏内可调，周复盘时按需微调）
# ═══════════════════════════════════════════════════════════════
# 所有微调必须在下述范围内，超出即为破坏护栏，禁止修改

# 仓位矩阵微调范围（当前值 ± 调整步长，不得超出边界）
POSITION_MATRIX_ADJUSTABLE = {
    "上升趋势": {"修复": {"current": 0.75, "min": 0.65, "max": 0.80, "step": 0.05}},
    "震荡筑底": {"修复": {"current": 0.50, "min": 0.40, "max": 0.60, "step": 0.05}},
    "下跌趋势": {"冰点": {"current": 0.15, "min": 0.10, "max": 0.20, "step": 0.05}},
}

# 健康度乘数微调范围
HEALTH_MULTIPLIER_ADJUSTABLE = {
    (8, 10): {"current": 1.0, "min": 0.9, "max": 1.0, "step": 0.05},
    (6, 7): {"current": 0.8, "min": 0.7, "max": 0.9, "step": 0.05},
    (4, 5): {"current": 0.5, "min": 0.4, "max": 0.6, "step": 0.05},
    (1, 3): {"current": 0.2, "min": 0.1, "max": 0.3, "step": 0.05},
}

# 单日单方向上限微调范围
DIRECTION_MAX_RATIO_ADJUSTABLE = {"current": 0.60, "min": 0.50, "max": 0.70, "step": 0.05}

# 核心仓单票上限微调范围（仅上升/震荡修复阶段）
CORE_SINGLE_MAX_ADJUSTABLE = {
    "上升趋势": {"修复": {"current": 0.15, "min": 0.12, "max": 0.18, "step": 0.02}},
    "震荡筑底": {"修复": {"current": 0.08, "min": 0.06, "max": 0.10, "step": 0.02}},
}

# 卫星仓单票上限微调范围
SAT_SINGLE_MAX_ADJUSTABLE = {
    "上升趋势": {"修复": {"current": 0.08, "min": 0.06, "max": 0.10, "step": 0.02}},
    "震荡筑底": {"修复": {"current": 0.05, "min": 0.03, "max": 0.08, "step": 0.02}},
}

# 验证评分阈值微调范围
VALIDATION_SCORE_THRESHOLD_ADJUSTABLE = {"current": 25, "min": 22, "max": 28, "step": 1}
VALIDATION_SHARPE_THRESHOLD_ADJUSTABLE = {"current": 0.25, "min": 0.20, "max": 0.30, "step": 0.05}

# 硬护栏（绝不可配置，仅作文档记录）
HARD_GUARDRAILS = {
    "fatal_risk_core_recognition_seal": 60_0000_0000,      # 核心辨识度跌停封单 ≥ 60亿
    "fatal_risk_core_large_seal": 100_0000_0000,            # 核心大票跌停封单 ≥ 100亿
    "fatal_risk_core_leader_drop": -3.0,                    # 核心中军竞价 ≤ -3%
    "fatal_position_zero": 0.0,                             # 致命风险触发时仓位 = 0%
    "health_buy_ban": 4,                                    # 健康度 < 4 禁买
    "health_buy_half": 6,                                   # 健康度 < 6 买入乘数 ≤ 0.5
    "direction_max_ratio_ceiling": 0.70,                    # 单日单方向上限天花板 70%
    "core_single_max_ceiling": 0.20,                        # 核心仓单票上限天花板 20%
    "sat_single_max_ceiling": 0.08,                         # 卫星仓单票上限天花板 8%
    "validation_score_floor": 20,                           # 验证评分地板 20
    "validation_sharpe_floor": 0.15,                        # 验证夏普地板 0.15
}

# 微调操作指引（周复盘时参考）
ADJUSTMENT_GUIDE = """
微调操作指引（周六 weekly-backtest-strategy-refresh 时执行）：

1. 统计指标（近 10 交易日）：
   - 实际平均仓位 / 矩阵上限 占比
   - 止损触发率
   - 胜率（盈利笔数 / 总交易笔数）
   - 夏普比率
   - 最大回撤

2. 微调决策规则：
   a) 若实际仓位长期 < 矩阵上限 70% 且胜率 > 55% → 上调对应阶段矩阵值 +step
   b) 若止损触发率 > 40% 或 最大回撤 > 矩阵上限 × 0.5 → 下调对应阶段矩阵值 -step
   c) 若健康度评分系统性偏高/偏低 → 整体上移/下移健康度乘数 ±step
   d) 若单日单方向频繁触顶 → 上调 DIRECTION_MAX_RATIO +step
   e) 若核心标的确定性高且资金利用率低 → 上调 CORE_SINGLE_MAX +step
   f) 若验证通过策略太少/太多 → 调整 VALIDATION_SCORE_THRESHOLD ±step

3. 执行约束：
   - 每次仅调整 1 个参数
   - 步长严格按定义（0.05 或 0.02 或 1）
   - 修改后必须在护栏范围内
   - 记录到 data/param_change_log.json（自动由 weekly cron 完成）
   - 2 周后复盘对比，若指标恶化 → 自动回滚

4. 记录格式：
   {
     "date": "2026-08-17",
     "param": "POSITION_MATRIX.上升趋势.修复",
     "old": 0.75, "new": 0.80,
     "reason": "近10日实际仓位仅62%，胜率58%，资金利用率低",
     "operator": "auto_weekly_review"
   }
"""

# 方向分类映射（监控池股票 -> 方向）
STOCK_DIRECTION_MAP = {
    # 半导体/硬科技
    "600584": "半导体封测", "002156": "半导体封测", "688981": "半导体晶圆",
    "002413": "军工电子", "300124": "工业自动化", "601100": "高端液压",
    # 新能源/锂电
    "300014": "锂电池", "002466": "锂资源", "601865": "光伏玻璃",
    # 高端材料/制造
    "300285": "特种陶瓷", "603308": "特材管材", "002318": "特钢管",
    "600160": "氟化工", "600346": "炼化一体化", "000708": "特钢",
    "300748": "稀土永磁", "002335": "数据中心", "300719": "连接器",
    # 金融/权重
    "600030": "券商", "601066": "券商", "600036": "银行", "601995": "券商",
    "000987": "基建金融", "600570": "金融IT", "605566": "消费电子",
    # 消费/其他
    "600660": "汽车玻璃", "000157": "工程机械", "601061": "有色贸易",
    # 新增：打印机/国产替代
    "002180": "打印机国产替代", "300847": "军工光电",
}


@dataclass
class PositionPlan:
    """仓位计划数据类"""
    date: str
    total_capital: float
    market_stage: str
    emotion_cycle: str
    health_score: int
    fatal_risk: bool
    high_risk: bool

    # 计算结果
    total_limit_pct: float = 0.0        # 总仓位上限 (0-1)
    total_limit_amount: float = 0.0     # 总仓位上限金额
    single_stock_max_pct: float = 0.0   # 单股最大仓位比例
    single_stock_max_amount: float = 0.0
    direction_allocation: Dict[str, float] = None  # 方向级分配金额
    buy_signal_multiplier: float = 1.0  # 买入信号强度乘数
    risk_warnings: List[str] = None

    def __post_init__(self):
        if self.direction_allocation is None:
            self.direction_allocation = {}
        if self.risk_warnings is None:
            self.risk_warnings = []


class PositionSizer:
    """030 动态仓位计算器"""

    def __init__(self, total_capital: float = 3_000_000):
        """
        Args:
            total_capital: 总资金（默认300万，对应30只×10万基准）
        """
        self.total_capital = total_capital

    def calculate(self,
                  market_stage: str,
                  emotion_cycle: str,
                  health_score: int,
                  fatal_risk: dict = None,
                  held_positions: Dict[str, dict] = None,
                  watchlist: List[str] = None) -> PositionPlan:
        """
        计算仓位计划

        Args:
            market_stage: 市场阶段 - "上升趋势" / "震荡筑底" / "下跌趋势" / "致命风险"
            emotion_cycle: 情绪周期 - "冰点" / "修复" / "高潮"
            health_score: 市场健康度评分 (1-10)
            fatal_risk: fatal_risk_detector 返回的结果 dict
            held_positions: 当前持仓 {symbol: {shares, cost, market_value, direction}}
            watchlist: 监控池代码列表

        Returns:
            PositionPlan 对象
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # 1. 致命风险覆盖判断
        fatal_triggered = fatal_risk.get("fatal_triggered", False) if fatal_risk else False
        high_risk_triggered = fatal_risk.get("high_risk_triggered", False) if fatal_risk else False

        if fatal_triggered:
            effective_stage = "致命风险"
        else:
            effective_stage = market_stage

        # 2. 基础矩阵仓位
        base_pct = POSITION_MATRIX.get(effective_stage, {}).get(emotion_cycle, 0.0)

        # 3. 健康度乘数
        health_mult = self._get_health_multiplier(health_score)

        # 4. 高风险防御额外降仓
        high_risk_mult = 0.5 if high_risk_triggered else 1.0

        # 5. 最终总仓位上限
        total_limit_pct = round(base_pct * health_mult * high_risk_mult, 4)
        total_limit_pct = max(0.0, min(total_limit_pct, 1.0))  # 夹逼 [0, 1]

        total_limit_amount = round(self.total_capital * total_limit_pct, 2)

        # 6. 单股最大仓位
        single_max_pct = SINGLE_STOCK_MAX.get(effective_stage, {}).get(emotion_cycle, 0.0)
        single_max_pct = round(single_max_pct * health_mult * high_risk_mult, 4)
        single_max_amount = round(self.total_capital * single_max_pct, 2)

        # 7. 买入信号乘数（致命风险/高风险/健康度综合）
        buy_mult = self._calc_buy_multiplier(fatal_triggered, high_risk_triggered, health_score)

        # 8. 方向级分配建议
        direction_alloc = self._calc_direction_allocation(
            total_limit_amount, held_positions, watchlist, effective_stage, emotion_cycle
        )

        # 9. 风险预警文案
        warnings = self._gen_warnings(fatal_triggered, high_risk_triggered, health_score,
                                      market_stage, emotion_cycle, total_limit_pct)

        plan = PositionPlan(
            date=today,
            total_capital=self.total_capital,
            market_stage=market_stage,
            emotion_cycle=emotion_cycle,
            health_score=health_score,
            fatal_risk=fatal_triggered,
            high_risk=high_risk_triggered,
            total_limit_pct=total_limit_pct,
            total_limit_amount=total_limit_amount,
            single_stock_max_pct=single_max_pct,
            single_stock_max_amount=single_max_amount,
            direction_allocation=direction_alloc,
            buy_signal_multiplier=buy_mult,
            risk_warnings=warnings,
        )

        return plan

    def _get_health_multiplier(self, score: int) -> float:
        """健康度分 → 乘数"""
        for (low, high), mult in HEALTH_MULTIPLIER.items():
            if low <= score <= high:
                return mult
        return 0.2  # 默认最保守

    def _calc_buy_multiplier(self, fatal: bool, high_risk: bool, health: int) -> float:
        """买入信号强度乘数"""
        if fatal:
            return 0.0  # 完全禁买
        if high_risk:
            return 0.3  # 高风险防御：仅允许极小仓试探
        if health >= 8:
            return 1.0
        elif health >= 6:
            return 0.8
        elif health >= 4:
            return 0.5
        else:
            return 0.2

    def _calc_direction_allocation(self,
                                   total_limit: float,
                                   held_positions: Dict[str, dict],
                                   watchlist: List[str],
                                   stage: str,
                                   emotion: str) -> Dict[str, float]:
        """方向级资金分配建议"""
        if not watchlist:
            return {}

        # 统计各方向监控股数量
        dir_counts = {}
        for sym in watchlist:
            direction = STOCK_DIRECTION_MAP.get(sym, "其他")
            dir_counts[direction] = dir_counts.get(direction, 0) + 1

        # 已占用资金（按方向汇总）
        used_by_dir = {}
        if held_positions:
            for sym, pos in held_positions.items():
                direction = STOCK_DIRECTION_MAP.get(sym, "其他")
                mv = pos.get("market_value", 0)
                used_by_dir[direction] = used_by_dir.get(direction, 0) + mv

        # 可用资金 = 总上限 - 已占用
        used_total = sum(used_by_dir.values())
        available = max(0, total_limit - used_total)

        # 简化分配：按监控股数量比例分配可用资金，单方向不超过总上限60%
        total_stocks = sum(dir_counts.values())
        alloc = {}
        for direction, count in dir_counts.items():
            share = count / total_stocks if total_stocks > 0 else 0
            dir_limit = min(total_limit * DIRECTION_MAX_RATIO, available * share)
            alloc[direction] = round(max(0, dir_limit - used_by_dir.get(direction, 0)), 2)

        return alloc

    def _gen_warnings(self, fatal: bool, high_risk: bool, health: int,
                      stage: str, emotion: str, total_pct: float) -> List[str]:
        """生成风险预警文案"""
        warnings = []
        if fatal:
            warnings.append("☠️ 触发致命风险：无条件清仓，禁止一切买入，至少空仓1个交易日")
        if high_risk:
            warnings.append("🟡 高风险防御：核心中军竞价≤-3%，不开新仓，持仓设-5%止损")
        if health <= 3:
            warnings.append(f"🔴 市场健康度极差({health}/10)：建议空仓或极小仓位({total_pct:.0%})")
        elif health <= 5:
            warnings.append(f"🟠 市场健康度偏弱({health}/10)：仓位上限已减半({total_pct:.0%})")
        if stage == "下跌趋势" and emotion == "高潮":
            warnings.append("⚠️ 下跌趋势中情绪高潮：极易回落，严控仓位")
        if total_pct == 0:
            warnings.append("🛑 总仓位上限为0：当前不可建任何新仓")
        return warnings


def load_market_context() -> dict:
    """加载市场上下文（从各模块输出文件读取）"""
    context = {
        "market_stage": "震荡筑底",      # 默认值
        "emotion_cycle": "修复",
        "health_score": 5,
        "fatal_risk": {"fatal_triggered": False, "high_risk_triggered": False},
    }

    # 1. 读取市场健康度（含阶段/情绪判断）
    today = datetime.now().strftime("%Y-%m-%d")
    health_path = os.path.join(DATA_DIR, f"market_health_{today}.json")
    if os.path.exists(health_path):
        try:
            with open(health_path) as f:
                health_data = json.load(f)
            summary = health_data.get("summary", {})
            context["health_score"] = summary.get("total_score", 5)
            # 从健康度推断阶段/情绪（简化版）
            score = context["health_score"]
            if score >= 7:
                context["market_stage"] = "上升趋势"
                context["emotion_cycle"] = "修复" if score < 9 else "高潮"
            elif score >= 5:
                context["market_stage"] = "震荡筑底"
                context["emotion_cycle"] = "修复"
            else:
                context["market_stage"] = "下跌趋势"
                context["emotion_cycle"] = "冰点" if score < 3 else "修复"
        except Exception:
            pass

    # 2. 读取致命风险
    fatal_path = os.path.join(DATA_DIR, f"fatal_risk_{today}.json")
    if os.path.exists(fatal_path):
        try:
            with open(fatal_path) as f:
                context["fatal_risk"] = json.load(f)
        except Exception:
            pass

    # 3. 读取持仓状态
    portfolio_path = os.path.join(DATA_DIR, "portfolio_sim_state.json")
    if os.path.exists(portfolio_path):
        try:
            with open(portfolio_path) as f:
                portfolio = json.load(f)
            context["held_positions"] = portfolio.get("positions", {})
        except Exception:
            context["held_positions"] = {}
    else:
        context["held_positions"] = {}

    return context


def save_plan(plan: PositionPlan):
    """保存仓位计划"""
    path = os.path.join(DATA_DIR, f"position_plan_{plan.date}.json")
    with open(path, "w") as f:
        json.dump(asdict(plan), f, ensure_ascii=False, indent=2, default=str)


def print_brief(plan: PositionPlan):
    """标准化简报"""
    lines = []
    lines.append(f"📐 **030动态仓位计划** ({plan.date})")
    lines.append(f"总资金: ¥{plan.total_capital:,.0f} | 市场阶段: {plan.market_stage} | 情绪周期: {plan.emotion_cycle} | 健康度: {plan.health_score}/10")
    lines.append(f"致命风险: {'是' if plan.fatal_risk else '否'} | 高风险: {'是' if plan.high_risk else '否'}")
    lines.append("")
    lines.append(f"📊 **总仓位上限**: {plan.total_limit_pct:.1%} (¥{plan.total_limit_amount:,.0f})")
    lines.append(f"📈 **单股上限**: {plan.single_stock_max_pct:.1%} (¥{plan.single_stock_max_amount:,.0f})")
    lines.append(f"⚡ **买入信号乘数**: {plan.buy_signal_multiplier:.1f}x")
    lines.append("")
    if plan.direction_allocation:
        lines.append("🎯 **方向级可用额度**:")
        for d, amt in sorted(plan.direction_allocation.items(), key=lambda x: -x[1]):
            if amt > 0:
                lines.append(f"  {d}: ¥{amt:,.0f}")
    lines.append("")
    if plan.risk_warnings:
        lines.append("⚠️ **风险预警**:")
        for w in plan.risk_warnings:
            lines.append(f"  {w}")
    print("\n".join(lines))


def main():
    """独立运行入口：自动加载上下文并计算"""
    context = load_market_context()
    watchlist = list(STOCK_DIRECTION_MAP.keys())

    sizer = PositionSizer(total_capital=3_000_000)
    plan = sizer.calculate(
        market_stage=context["market_stage"],
        emotion_cycle=context["emotion_cycle"],
        health_score=context["health_score"],
        fatal_risk=context["fatal_risk"],
        held_positions=context.get("held_positions"),
        watchlist=watchlist,
    )

    save_plan(plan)
    print_brief(plan)

    # stdout JSON
    print("\n=====POSITION_PLAN_RESULT=====")
    print(json.dumps(asdict(plan), ensure_ascii=False, indent=2, default=str))
    print("=====POSITION_PLAN_END=====")


if __name__ == "__main__":
    main()