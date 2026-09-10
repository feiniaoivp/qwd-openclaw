#!/usr/bin/env python3
"""
隔日推演生成器 (030体系核心输出模块)
=====================================
替代当前单一信号输出，生成 030 标准的三情景推演
输入：今日收盘全量数据、健康度、仓位矩阵、核心标的表现、裁决结果
输出：标准化推演报告（Markdown + JSON），含三情景+概率+对应操作+止损位图

030 隔日推演标准：
- 每个活跃方向给出三种情景：正常(A)、超预期(B)、不及预期(C)
- 三者概率和 = 100%
- 每情景包含：描述、触发条件、对应操作
- 概率赋值参考：下午加强+20%、新催化+15%、尾盘抢筹+10%、健康度8+ +10%、健康度4- +10%到不及预期
"""

import os
import json
from datetime import datetime, date
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
DATA_DIR = os.path.join(WORKSPACE, "data")
OUTPUT_DIR = os.path.join(WORKSPACE, "analysis", "daily")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# 方向定义与核心标的映射
# ═══════════════════════════════════════════════════════════════

DIRECTION_CORE_STOCKS = {
    "半导体封测": ["600584", "002156"],           # 长电科技、通富微电
    "半导体晶圆": ["688981"],                    # 中芯国际
    "军工电子": ["002413"],                      # 雷科防务
    "工业自动化": ["300124"],                    # 汇川技术
    "高端液压": ["601100"],                      # 恒立液压
    "锂电池": ["300014"],                        # 亿纬锂能
    "锂资源": ["002466"],                        # 天齐锂业
    "光伏玻璃": ["601865"],                      # 福莱特
    "特种陶瓷": ["300285"],                      # 国瓷材料
    "特材管材": ["603308"],                      # 应流股份
    "特钢管": ["002318"],                        # 久立特材
    "氟化工": ["600160"],                        # 巨化股份
    "炼化一体化": ["600346"],                    # 恒力石化
    "特钢": ["000708"],                          # 中信特钢
    "稀土永磁": ["300748"],                      # 金力永磁
    "数据中心": ["002335"],                      # 科华数据
    "连接器": ["300719"],                        # 安达维尔
    "券商": ["600030", "601066", "601995"],     # 中信证券、中信建投、中金公司
    "银行": ["600036"],                          # 招商银行
    "基建金融": ["000987"],                      # 越秀资本
    "金融IT": ["600570"],                        # 恒生电子
    "消费电子": ["605566"],                      # 福莱蒽特
    "汽车玻璃": ["600660"],                      # 福耀玻璃
    "工程机械": ["000157"],                      # 中联重科
    "有色贸易": ["601061"],                      # 中信金属
    "打印机国产替代": ["002180"],                # 奔图科技
    "军工光电": ["300847"],                      # 中船汉光
}

SYMBOL_TO_NAME = {
    "600584": "长电科技", "002156": "通富微电", "688981": "中芯国际",
    "002413": "雷科防务", "300124": "汇川技术", "601100": "恒立液压",
    "300014": "亿纬锂能", "002466": "天齐锂业", "601865": "福莱特",
    "300285": "国瓷材料", "603308": "应流股份", "002318": "久立特材",
    "600160": "巨化股份", "600346": "恒力石化", "000708": "中信特钢",
    "300748": "金力永磁", "002335": "科华数据", "300719": "安达维尔",
    "600030": "中信证券", "601066": "中信建投", "601995": "中金公司",
    "600036": "招商银行", "000987": "越秀资本", "600570": "恒生电子",
    "605566": "福莱蒽特", "600660": "福耀玻璃", "000157": "中联重科",
    "601061": "中信金属", "002180": "奔图科技", "300847": "中船汉光",
}


@dataclass
class Scenario:
    """单一情景"""
    label: str              # A_normal / B_surprise / C_disappoint
    name: str               # 正常预期 / 超预期 / 不及预期
    probability: int        # 概率 0-100
    description: str        # 情景描述
    trigger: str            # 触发条件/验证信号
    action: str             # 对应操作
    key_levels: Dict[str, float] = None  # 关键价位 {breakout, stop_loss, support}

    def __post_init__(self):
        if self.key_levels is None:
            self.key_levels = {}


@dataclass
class DirectionForecast:
    """单方向推演"""
    direction: str
    core_stocks: List[str]
    scenarios: Dict[str, Scenario]  # A/B/C -> Scenario
    weight: float = 1.0             # 方向权重


@dataclass
class NextDaySimulation:
    """隔日推演完整结果"""
    date: str
    prev_date: str
    market_stage: str
    emotion_cycle: str
    health_score: int
    fatal_risk: bool
    high_risk: bool

    position_plan: Dict[str, Any]      # 来自 position_sizer
    directions: List[DirectionForecast]
    risk_warnings: List[str]
    stop_loss_map: Dict[str, float]    # symbol -> stop_loss

    # 元数据
    generated_at: str = ""
    version: str = "030-v1.0"


class NextDaySimulator:
    """030 隔日推演生成器"""

    def __init__(self):
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.prev_date = self._get_prev_trading_day()

    def _get_prev_trading_day(self) -> str:
        """获取上一交易日（简化：向前推1-3天跳过周末）"""
        from datetime import timedelta
        d = datetime.now()
        for i in range(1, 5):
            prev = d - timedelta(days=i)
            if prev.weekday() < 5:
                return prev.strftime("%Y-%m-%d")
        return (d - timedelta(days=1)).strftime("%Y-%m-%d")

    def generate(self,
                 market_context: dict,
                 position_plan: dict,
                 arbitration_results: List[dict],
                 held_positions: dict,
                 scan_results: dict = None) -> NextDaySimulation:
        """
        生成隔日推演

        Args:
            market_context: {health_score, market_stage, emotion_cycle, fatal_risk, high_risk, ...}
            position_plan: position_sizer 输出
            arbitration_results: signal_arbitrator 输出列表
            held_positions: 当前持仓
            scan_results: close_scan_v2/adaptive_dual 原始扫描结果（可选）

        Returns:
            NextDaySimulation 完整推演对象
        """
        health = market_context.get("health_score", 5)
        stage = market_context.get("market_stage", "震荡筑底")
        emotion = market_context.get("emotion_cycle", "修复")
        fatal = market_context.get("fatal_risk", {}).get("fatal_triggered", False)
        high_risk = market_context.get("fatal_risk", {}).get("high_risk_triggered", False)

        # 1. 构建止损位图
        stop_loss_map = self._build_stop_loss_map(arbitration_results, held_positions)

        # 2. 识别活跃方向（有持仓/有信号/有审计关注的方向）
        active_directions = self._identify_active_directions(
            arbitration_results, held_positions, scan_results
        )

        # 3. 为每个方向生成三情景推演
        direction_forecasts = []
        for direction, info in active_directions.items():
            forecast = self._generate_direction_forecast(
                direction, info, market_context, position_plan, arbitration_results
            )
            direction_forecasts.append(forecast)

        # 4. 生成风险预警
        risk_warnings = self._gen_risk_warnings(
            market_context, position_plan, arbitration_results, fatal, high_risk
        )

        simulation = NextDaySimulation(
            date=self.today,
            prev_date=self.prev_date,
            market_stage=stage,
            emotion_cycle=emotion,
            health_score=health,
            fatal_risk=fatal,
            high_risk=high_risk,
            position_plan=position_plan,
            directions=direction_forecasts,
            risk_warnings=risk_warnings,
            stop_loss_map=stop_loss_map,
            generated_at=datetime.now().strftime("%H:%M:%S"),
        )

        return simulation

    def _build_stop_loss_map(self, arbitration_results: List[dict], held_positions: dict) -> Dict[str, float]:
        """构建止损位图：优先用裁决结果，其次用持仓成本*0.95"""
        stop_loss = {}

        # 从裁决结果获取
        for r in arbitration_results:
            if r.get("stop_loss"):
                stop_loss[r["symbol"]] = r["stop_loss"]

        # 从持仓成本计算（兜底）
        for symbol, pos in held_positions.items():
            if symbol not in stop_loss:
                cost = pos.get("cost", 0) or pos.get("entry_price", 0)
                if cost > 0:
                    stop_loss[symbol] = round(cost * 0.95, 2)

        return stop_loss

    def _identify_active_directions(self,
                                    arbitration_results: List[dict],
                                    held_positions: dict,
                                    scan_results: dict) -> Dict[str, dict]:
        """识别需推演的活跃方向"""
        active = {}

        # 从持仓方向
        for symbol, pos in held_positions.items():
            if pos.get("shares", 0) > 0:
                direction = self._get_stock_direction(symbol)
                if direction not in active:
                    active[direction] = {"core_stocks": [], "has_position": True, "signals": []}
                active[direction]["core_stocks"].append(symbol)
                active[direction]["has_position"] = True

        # 从裁决信号方向（买入/卖出/减仓信号）
        for r in arbitration_results:
            action = r.get("final_action", "")
            if action in ["买入", "积极买入", "小仓买入", "卖出", "减仓", "强制清仓"]:
                direction = self._get_stock_direction(r["symbol"])
                if direction not in active:
                    active[direction] = {"core_stocks": [], "has_position": False, "signals": []}
                active[direction]["core_stocks"].append(r["symbol"])
                active[direction]["signals"].append({
                    "symbol": r["symbol"],
                    "action": action,
                    "reason": r.get("reason", ""),
                })

        # 从扫描结果（强信号）
        if scan_results:
            details = scan_results.get("details", [])
            for d in details:
                if d.get("consensus") in ["一致", "分歧"]:
                    for s in d.get("strategies", []):
                        if "买入" in s.get("action", "") or "卖出" in s.get("action", ""):
                            direction = self._get_stock_direction(d["symbol"])
                            if direction not in active:
                                active[direction] = {"core_stocks": [], "has_position": False, "signals": []}
                            if d["symbol"] not in active[direction]["core_stocks"]:
                                active[direction]["core_stocks"].append(d["symbol"])

        # 去重核心标的
        for info in active.values():
            info["core_stocks"] = list(dict.fromkeys(info["core_stocks"]))

        return active

    def _get_stock_direction(self, symbol: str) -> str:
        """获取股票所属方向"""
        for direction, stocks in DIRECTION_CORE_STOCKS.items():
            if symbol in stocks:
                return direction
        return "其他"

    def _generate_direction_forecast(self,
                                     direction: str,
                                     info: dict,
                                     market_context: dict,
                                     position_plan: dict,
                                     arbitration_results: dict) -> DirectionForecast:
        """为单一方向生成三情景推演"""
        health = market_context.get("health_score", 5)
        stage = market_context.get("market_stage", "震荡筑底")
        emotion = market_context.get("emotion_cycle", "修复")

        core_stocks = info.get("core_stocks", [])
        signals = info.get("signals", [])

        # 计算基础概率
        base_probs = self._calc_base_probabilities(health, stage, emotion, signals)

        # 核心标的关键价位
        key_levels = self._get_key_levels(core_stocks, arbitration_results)

        # 生成三情景
        scenarios = {}

        # A: 正常预期
        scenarios["A"] = Scenario(
            label="A_normal",
            name="正常预期",
            probability=base_probs["normal"],
            description=self._gen_normal_desc(direction, core_stocks, stage, emotion),
            trigger=self._gen_normal_trigger(direction, core_stocks),
            action=self._gen_normal_action(direction, core_stocks, position_plan, key_levels),
            key_levels=key_levels,
        )

        # B: 超预期
        scenarios["B"] = Scenario(
            label="B_surprise",
            name="超预期",
            probability=base_probs["surprise"],
            description=self._gen_surprise_desc(direction, core_stocks, stage),
            trigger=self._gen_surprise_trigger(direction, core_stocks),
            action=self._gen_surprise_action(direction, core_stocks, position_plan, key_levels),
            key_levels=key_levels,
        )

        # C: 不及预期
        scenarios["C"] = Scenario(
            label="C_disappoint",
            name="不及预期",
            probability=base_probs["disappoint"],
            description=self._gen_disappoint_desc(direction, core_stocks, stage),
            trigger=self._gen_disappoint_trigger(direction, core_stocks),
            action=self._gen_disappoint_action(direction, core_stocks, position_plan, key_levels),
            key_levels=key_levels,
        )

        # 概率微调归一化（确保和=100）
        total = sum(s.probability for s in scenarios.values())
        if total != 100:
            for s in scenarios.values():
                s.probability = round(s.probability * 100 / total)

        return DirectionForecast(
            direction=direction,
            core_stocks=core_stocks,
            scenarios=scenarios,
            weight=len(core_stocks) / 30.0,  # 简化权重
        )

    def _calc_base_probabilities(self, health: int, stage: str, emotion: str, signals: List[dict]) -> dict:
        """计算基础概率分布"""
        # 基础分布
        if health >= 8 and stage == "上升趋势":
            base = {"normal": 55, "surprise": 30, "disappoint": 15}
        elif health >= 6:
            base = {"normal": 60, "surprise": 20, "disappoint": 20}
        elif health >= 4:
            base = {"normal": 50, "surprise": 15, "disappoint": 35}
        else:
            base = {"normal": 40, "surprise": 10, "disappoint": 50}

        # 情绪周期调整
        if emotion == "高潮":
            base["surprise"] += 10
            base["disappoint"] += 10
            base["normal"] -= 20
        elif emotion == "冰点":
            base["surprise"] += 15
            base["normal"] -= 10
            base["disappoint"] -= 5

        # 信号倾向调整
        buy_signals = sum(1 for s in signals if "买入" in s.get("action", ""))
        sell_signals = sum(1 for s in signals if "卖出" in s.get("action", "") or "减仓" in s.get("action", ""))
        if buy_signals > sell_signals:
            base["surprise"] += min(10, (buy_signals - sell_signals) * 3)
            base["disappoint"] -= min(10, (buy_signals - sell_signals) * 3)
        elif sell_signals > buy_signals:
            base["disappoint"] += min(15, (sell_signals - buy_signals) * 5)
            base["surprise"] -= min(10, (sell_signals - buy_signals) * 3)

        # 确保非负
        for k in base:
            base[k] = max(5, base[k])

        return base

    def _get_key_levels(self, core_stocks: List[str], arbitration_results: List[dict]) -> Dict[str, float]:
        """获取核心标的关键价位"""
        levels = {}
        for r in arbitration_results:
            if r["symbol"] in core_stocks:
                if r.get("stop_loss"):
                    levels[f"{r['symbol']}_stop"] = r["stop_loss"]
                if r.get("support_price"):
                    levels[f"{r['symbol']}_support"] = r["support_price"]
        return levels

    def _gen_normal_desc(self, direction: str, stocks: List[str], stage: str, emotion: str) -> str:
        names = [SYMBOL_TO_NAME.get(s, s) for s in stocks[:3]]
        return f"{direction}方向({', '.join(names)})延续当前{stage}·{emotion}节奏，早盘分歧午后确认，量能温和放大"

    def _gen_normal_trigger(self, direction: str, stocks: List[str]) -> str:
        names = [SYMBOL_TO_NAME.get(s, s) for s in stocks[:2]]
        return f"核心标的{', '.join(names)}竞价-2%~+2%开盘，开盘半小时量能≥前日同期，午后不破早盘低点"

    def _gen_normal_action(self, direction: str, stocks: List[str], plan: dict, levels: dict) -> str:
        mult = plan.get("buy_signal_multiplier", 1.0)
        total_limit = plan.get("total_limit_pct", 0.5)
        if mult >= 0.8:
            return f"核心标的回调至5日线/支撑位低吸，单股不超过{plan.get('single_stock_max_pct', 0.05):.0%}，方向总仓≤{total_limit:.0%}×60%"
        elif mult >= 0.5:
            return f"核心标的小仓试探性低吸（仓位乘数{mult:.1f}x），严设止损，不追高"
        else:
            return "信号乘数较低，仅观望核心标的回调企稳确认后再考虑微仓参与"

    def _gen_surprise_desc(self, direction: str, stocks: List[str], stage: str) -> str:
        names = [SYMBOL_TO_NAME.get(s, s) for s in stocks[:3]]
        return f"{direction}方向({', '.join(names)})出现超预期催化（政策/业绩/资金），早盘直接封板/直线拉升，情绪急剧升温"

    def _gen_surprise_trigger(self, direction: str, stocks: List[str]) -> str:
        names = [SYMBOL_TO_NAME.get(s, s) for s in stocks[:2]]
        return f"核心标的{', '.join(names)}竞价大涨>5%且封单≥5亿，或盘中突发利好直线拉升，板块联动≥3家涨停"

    def _gen_surprise_action(self, direction: str, stocks: List[str], plan: dict, levels: dict) -> str:
        mult = plan.get("buy_signal_multiplier", 1.0)
        if mult >= 0.7:
            return f"竞价确认强则追首板/龙头，单股上限{plan.get('single_stock_max_pct', 0.05):.0%}，止损设前日低点/均线"
        else:
            return "虽超预期但仓位受限，仅允许核心龙头微仓跟随，严防冲高回落"

    def _gen_disappoint_desc(self, direction: str, stocks: List[str], stage: str) -> str:
        names = [SYMBOL_TO_NAME.get(s, s) for s in stocks[:3]]
        return f"{direction}方向({', '.join(names)})量能萎缩，核心标的跳水/破位，板块联动性断裂，亏钱效应扩散"

    def _gen_disappoint_trigger(self, direction: str, stocks: List[str]) -> str:
        names = [SYMBOL_TO_NAME.get(s, s) for s in stocks[:2]]
        return f"核心标的{', '.join(names)}竞价≤-3%或开盘急跌破5日线，板块内跌停≥2家，量能<前日70%"

    def _gen_disappoint_action(self, direction: str, stocks: List[str], plan: dict, levels: dict) -> str:
        # 拆分子表达式，避免在 f-string 表达式内使用反斜杠（部分 Python 版本不受支持）
        parts = []
        for s in stocks[:2]:
            if f"{s}_stop" in levels:
                name = SYMBOL_TO_NAME.get(s, s)
                stop = levels.get(f"{s}_stop", "?")
                parts.append(f"{name}¥{stop}")
        return f"核心标的跌破止损位（{', '.join(parts)}）无条件离场；未持仓者绝不抄底，等待情绪冰点后企稳确认"

    def _gen_risk_warnings(self, market_context: dict, position_plan: dict,
                           arbitration_results: List[dict], fatal: bool, high_risk: bool) -> List[str]:
        """生成风险预警"""
        warnings = []
        health = market_context.get("health_score", 5)

        if fatal:
            warnings.append("☠️ 致命风险触发：明日无条件空仓，禁止一切买入，等待恢复条件满足")
        if high_risk:
            warnings.append("🟡 高风险防御：核心中军竞价≤-3%，明日不开新仓，持仓严设-5%止损")
        if health <= 3:
            warnings.append(f"🔴 市场健康度极差({health}/10)：系统性风险高，建议空仓或极小仓位")
        elif health <= 5:
            warnings.append(f"🟠 市场健康度偏弱({health}/10)：买入信号降级，仓位上限减半执行")

        # 止损位提醒
        stop_losses = [f"{SYMBOL_TO_NAME.get(s,s)}¥{v}" for s, v in position_plan.get("stop_loss_map", {}).items()]
        if stop_losses:
            warnings.append(f"🛑 重点止损位：{', '.join(stop_losses[:5])}")

        # 裁决冲突提醒
        conflicts = [r for r in arbitration_results if "冲突" in r.get("reason", "") or "质疑" in r.get("reason", "")]
        if conflicts:
            warnings.append(f"⚠️ 存在{len(conflicts)}只股票信号冲突/出货形态质疑，操作需额外谨慎")

        return warnings


def load_all_inputs() -> tuple:
    """加载所有上游模块输出"""
    today = datetime.now().strftime("%Y-%m-%d")

    # 1. 市场上下文
    market_context = {}
    health_path = os.path.join(DATA_DIR, f"market_health_{today}.json")
    if os.path.exists(health_path):
        with open(health_path) as f:
            data = json.load(f)
        summary = data.get("summary", {})
        market_context["health_score"] = summary.get("total_score", 5)
        score = market_context["health_score"]
        if score >= 7:
            market_context["market_stage"] = "上升趋势"
            market_context["emotion_cycle"] = "修复" if score < 9 else "高潮"
        elif score >= 5:
            market_context["market_stage"] = "震荡筑底"
        else:
            market_context["market_stage"] = "下跌趋势"
            market_context["emotion_cycle"] = "冰点" if score < 3 else "修复"

    fatal_path = os.path.join(DATA_DIR, f"fatal_risk_{today}.json")
    if os.path.exists(fatal_path):
        with open(fatal_path) as f:
            market_context["fatal_risk"] = json.load(f)
    else:
        market_context["fatal_risk"] = {"fatal_triggered": False, "high_risk_triggered": False}

    # 2. 仓位计划
    position_plan = {}
    plan_path = os.path.join(DATA_DIR, f"position_plan_{today}.json")
    if os.path.exists(plan_path):
        with open(plan_path) as f:
            position_plan = json.load(f)
    else:
        position_plan = {"buy_signal_multiplier": 1.0, "single_stock_max_pct": 0.05,
                         "total_limit_pct": 0.5, "total_limit_amount": 1_500_000}

    # 3. 裁决结果
    arbitration_results = []
    arb_path = os.path.join(DATA_DIR, f"arbitration_{today}.json")
    if os.path.exists(arb_path):
        with open(arb_path) as f:
            arbitration_results = json.load(f)

    # 4. 持仓
    held_positions = {}
    portfolio_path = os.path.join(DATA_DIR, "portfolio_sim_state.json")
    if os.path.exists(portfolio_path):
        with open(portfolio_path) as f:
            data = json.load(f)
        held_positions = data.get("positions", {})

    # 5. 扫描结果
    scan_results = {}
    dual_path = os.path.join(OUTPUT_DIR, f"{today}_dual.json")
    if not os.path.exists(dual_path):
        dual_path = os.path.join(OUTPUT_DIR, f"{today}_adaptive.json")
    if os.path.exists(dual_path):
        with open(dual_path) as f:
            scan_results = json.load(f)

    return market_context, position_plan, arbitration_results, held_positions, scan_results


def save_simulation(simulation: NextDaySimulation):
    """保存推演结果（JSON + Markdown）"""
    today = simulation.date

    # JSON
    json_path = os.path.join(DATA_DIR, f"next_day_sim_{today}.json")
    with open(json_path, "w") as f:
        json.dump(asdict(simulation), f, ensure_ascii=False, indent=2, default=str)

    # Markdown
    md_path = os.path.join(OUTPUT_DIR, f"{today}_next_day_sim.md")
    with open(md_path, "w") as f:
        f.write(format_markdown(simulation))

    return json_path, md_path


def format_markdown(sim: NextDaySimulation) -> str:
    """格式化为 030 标准推演报告"""
    lines = []
    lines.append(f"# 030隔日概率化推演 {sim.date}")
    lines.append(f"> 基于 {sim.prev_date} 收盘数据生成 | 生成时间: {sim.generated_at} | 版本: {sim.version}")
    lines.append("")

    # 市场定位
    lines.append("## 📍 市场定位")
    lines.append(f"- **市场阶段**: {sim.market_stage}")
    lines.append(f"- **情绪周期**: {sim.emotion_cycle}")
    lines.append(f"- **健康度评分**: {sim.health_score}/10")
    lines.append(f"- **致命风险**: {'是 ☠️' if sim.fatal_risk else '否'}")
    lines.append(f"- **高风险防御**: {'是 🟡' if sim.high_risk else '否'}")
    lines.append("")

    # 仓位计划
    pp = sim.position_plan
    lines.append("## 📐 仓位计划")
    lines.append(f"- **总仓位上限**: {pp.get('total_limit_pct', 0):.1%} (¥{pp.get('total_limit_amount', 0):,.0f})")
    lines.append(f"- **单股上限**: {pp.get('single_stock_max_pct', 0):.1%} (¥{pp.get('single_stock_max_amount', 0):,.0f})")
    lines.append(f"- **买入信号乘数**: {pp.get('buy_signal_multiplier', 1.0):.1f}x")
    if pp.get("direction_allocation"):
        lines.append("- **方向级可用额度**:")
        for d, amt in sorted(pp["direction_allocation"].items(), key=lambda x: -x[1]):
            if amt > 0:
                lines.append(f"  - {d}: ¥{amt:,.0f}")
    lines.append("")

    # 方向推演
    lines.append("## 🎯 方向级隔日推演")
    for df in sim.directions:
        lines.append(f"### {df.direction} (核心: {', '.join(SYMBOL_TO_NAME.get(s, s) for s in df.core_stocks)})")
        lines.append("")
        lines.append("| 情景 | 概率 | 描述 | 触发条件 | 对应操作 |")
        lines.append("|:---|:---:|:---|:---|:---|")
        for key in ["A", "B", "C"]:
            s = df.scenarios[key]
            lines.append(f"| **{s.name}** | {s.probability}% | {s.description} | {s.trigger} | {s.action} |")
        lines.append("")

    # 止损位图
    if sim.stop_loss_map:
        lines.append("## 🛑 止损位图")
        lines.append("| 股票 | 止损价 | 支撑价 |")
        lines.append("|:---|:---:|:---:|")
        for symbol, sl in sim.stop_loss_map.items():
            support = None
            # 从方向推演中找支撑
            for df in sim.directions:
                for s in df.scenarios.values():
                    if f"{symbol}_support" in s.key_levels:
                        support = s.key_levels[f"{symbol}_support"]
                        break
            lines.append(f"| {SYMBOL_TO_NAME.get(symbol, symbol)}({symbol}) | ¥{sl} | ¥{support if support else '—'} |")
        lines.append("")

    # 风险预警
    if sim.risk_warnings:
        lines.append("## ⚠️ 风险预警")
        for w in sim.risk_warnings:
            lines.append(f"- {w}")
        lines.append("")

    # 标准输出模板（供复盘日志直接使用）
    lines.append("---")
    lines.append("## 📋 030标准复盘日志模板（可直接复制）")
    lines.append("")
    lines.append(f"【日期】{sim.date}")
    lines.append(f"【市场健康度评分】{sim.health_score}/10（上日待补充，变化待补充）")
    lines.append(f"【致命风险】{'有' if sim.fatal_risk else '无'}{'（触发条件：' + sim.position_plan.get('fatal_conditions', '') + '）' if sim.fatal_risk else ''}")
    lines.append(f"【市场阶段】{sim.market_stage} × {sim.emotion_cycle}")
    lines.append(f"【仓位计划】总仓位上限{sim.position_plan.get('total_limit_pct', 0):.0%}")
    for df in sim.directions:
        scens = []
        for key in ["A", "B", "C"]:
            s = df.scenarios[key]
            scens.append(f"{s.name}({s.probability}%)→{s.action[:20]}")
        lines.append(f"【隔日推演】{df.direction}：{'；'.join(scens)}")
    lines.append(f"【风险预警】{'; '.join(sim.risk_warnings) if sim.risk_warnings else '无'}")
    lines.append(f"【止损设置】{', '.join(f'{SYMBOL_TO_NAME.get(s,s)}¥{v}' for s, v in sim.stop_loss_map.items()) if sim.stop_loss_map else '无'}")

    return "\n".join(lines)


def print_brief(sim: NextDaySimulation):
    """控制台简报"""
    lines = []
    lines.append(f"🔮 **030隔日推演** ({sim.date})")
    lines.append(f"市场: {sim.market_stage}·{sim.emotion_cycle} | 健康度: {sim.health_score}/10 | 致命: {'是' if sim.fatal_risk else '否'} | 高风险: {'是' if sim.high_risk else '否'}")
    lines.append(f"仓位: {sim.position_plan.get('total_limit_pct', 0):.0%} | 乘数: {sim.position_plan.get('buy_signal_multiplier', 1.0):.1f}x")
    lines.append("")
    for df in sim.directions:
        names = [SYMBOL_TO_NAME.get(s, s) for s in df.core_stocks[:3]]
        probs = [f"{df.scenarios[k].name}{df.scenarios[k].probability}%" for k in ["A", "B", "C"]]
        lines.append(f"  {df.direction}({', '.join(names)}): {' | '.join(probs)}")
    if sim.risk_warnings:
        lines.append("")
        lines.append("⚠️ " + "; ".join(sim.risk_warnings[:3]))
    print("\n".join(lines))


def main():
    """独立运行入口"""
    print("🔮 启动030隔日推演生成器...")

    market_context, position_plan, arbitration_results, held_positions, scan_results = load_all_inputs()

    simulator = NextDaySimulator()
    simulation = simulator.generate(
        market_context=market_context,
        position_plan=position_plan,
        arbitration_results=arbitration_results,
        held_positions=held_positions,
        scan_results=scan_results,
    )

    json_path, md_path = save_simulation(simulation)
    print_brief(simulation)
    print(f"\n📄 JSON: {json_path}")
    print(f"📄 Markdown: {md_path}")

    # stdout JSON
    print("\n=====NEXT_DAY_SIM_RESULT=====")
    print(json.dumps(asdict(simulation), ensure_ascii=False, indent=2, default=str))
    print("=====NEXT_DAY_SIM_END=====")


if __name__ == "__main__":
    main()