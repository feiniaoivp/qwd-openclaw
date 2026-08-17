#!/usr/bin/env python3
"""
统一信号裁决引擎 (030体系核心决策模块)
=========================================
解决：adaptive_dual 双策略分歧、signal_audit 冲突检测、030 裁决规则 三者不一致
单一权威入口，所有子系统信号汇聚此处裁决

裁决层级（030 铁律，不可违背）：
1. 致命风险信号（无条件）> 
2. 负反馈信号 > 
3. 量能信号 > 
4. 封单方向信号 > 
5. 超预期信号

输入：多源信号 + 致命风险 + 仓位计划 + 市场上下文
输出：最终裁决信号 {action, confidence, reason, position_mult, stop_loss}
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
DATA_DIR = os.path.join(WORKSPACE, "data")
os.makedirs(DATA_DIR, exist_ok=True)


class SignalAction(Enum):
    """标准化动作枚举"""
    FORCE_CLEAR = "强制清仓"      # 致命风险触发
    SELL = "卖出"                # 明确卖出
    REDUCE = "减仓"              # 部分卖出
    HOLD = "持有"                # 维持
    BUY_SMALL = "小仓买入"       # 试探性买入
    BUY = "买入"                 # 标准买入
    BUY_AGGRESSIVE = "积极买入"   # 满仓买入
    WATCH = "观望"               # 信号不清，不操作


class SignalSource(Enum):
    """信号来源"""
    ADAPTIVE_DUAL = "adaptive_dual"
    ADAPTIVE_TRADER = "adaptive_trader"
    CLOSE_SCAN = "close_scan_v2"
    SIGNAL_AUDIT = "signal_audit"
    PORTFOLIO_SIM = "portfolio_sim"
    MANUAL = "manual"
    FATAL_RISK = "fatal_risk"
    MARKET_HEALTH = "market_health"


@dataclass
class RawSignal:
    """原始信号"""
    source: str
    symbol: str
    name: str
    action: str           # 原始动作文本：买入/卖出/持有/强烈买入/强烈卖出/错误
    strength: int         # 信号强度 -2~2 (强烈卖出=-2, 卖出=-1, 持有=0, 买入=1, 强烈买入=2)
    confidence: float     # 来源置信度 0-1
    reason: str           # 信号理由
    indicators: str = ""  # 关键指标
    strategy: str = ""    # 策略名
    validation_score: float = 0.0  # 验证评分
    moat_score: float = 0.0        # 护城河分
    distribution_warning: str = "" # 出货形态预警


@dataclass
class ArbitrationResult:
    """裁决结果"""
    symbol: str
    name: str
    final_action: SignalAction
    confidence: float          # 0-1
    position_mult: float       # 仓位乘数 (0-1)，乘以 position_sizer 给出的单股上限
    stop_loss: Optional[float] = None      # 止损价
    support_price: Optional[float] = None  # 支撑价
    reason: str = ""
    raw_signals: List[RawSignal] = None
    arbitration_path: List[str] = None     # 裁决路径，用于审计追溯

    def __post_init__(self):
        if self.raw_signals is None:
            self.raw_signals = []
        if self.arbitration_path is None:
            self.arbitration_path = []


class SignalArbitrator:
    """030 统一信号裁决引擎"""

    # 裁决层级权重（越高优先级越高）
    HIERARCHY_WEIGHTS = {
        "fatal_risk": 100,           # 致命风险
        "negative_feedback": 80,     # 负反馈（核心中军≤-3%、跌停板传导等）
        "distribution_pattern": 70,  # 出货形态（射击之星、巨量长阴、黄昏之星等）
        "volume": 60,                # 量能（缩量滞涨、放量下跌）
        "seal_direction": 50,        # 封单方向（一字板封单量、封单结构）
        "surprise": 40,              # 超预期（该弱不弱、跷跷板验证）
        "technical": 30,             # 技术指标（MACD、EMA、KDJ等）
        "moat_bonus": 10,            # 护城河加分（核心仓标的容错更高）
    }

    def __init__(self):
        self.today = datetime.now().strftime("%Y-%m-%d")

    def arbitrate(self,
                  raw_signals: List[RawSignal],
                  fatal_risk: dict,
                  position_plan: dict,
                  market_context: dict,
                  held_position: dict = None) -> ArbitrationResult:
        """
        主裁决入口

        Args:
            raw_signals: 所有子系统的原始信号列表
            fatal_risk: fatal_risk_detector 结果
            position_plan: position_sizer 结果
            market_context: 市场上下文 {health_score, market_stage, emotion_cycle, ...}
            held_position: 当前持仓信息 {shares, cost, market_value, pnl_pct}

        Returns:
            ArbitrationResult 裁决结果
        """
        if not raw_signals:
            return ArbitrationResult(
                symbol="", name="", final_action=SignalAction.WATCH,
                confidence=0.0, position_mult=0.0,
                reason="无原始信号输入"
            )

        symbol = raw_signals[0].symbol
        name = raw_signals[0].name

        # 归一化动作到强度
        for s in raw_signals:
            s.strength = self._normalize_action(s.action)

        # 1. 致命风险绝对否决（最高优先级）
        if fatal_risk.get("fatal_triggered", False):
            return ArbitrationResult(
                symbol=symbol, name=name,
                final_action=SignalAction.FORCE_CLEAR,
                confidence=1.0, position_mult=0.0,
                reason="☠️ 触发030致命风险：无条件清仓，禁止买入",
                raw_signals=raw_signals,
                arbitration_path=["FATAL_RISK_OVERRIDE"]
            )

        # 2. 高风险防御
        high_risk = fatal_risk.get("high_risk_triggered", False)
        if high_risk:
            # 持仓则减仓/止损，无仓则禁买
            if held_position and held_position.get("shares", 0) > 0:
                return ArbitrationResult(
                    symbol=symbol, name=name,
                    final_action=SignalAction.REDUCE,
                    confidence=0.9, position_mult=0.3,
                    stop_loss=self._calc_stop_loss(held_position),
                    support_price=self._calc_support(held_position),
                    reason="🟡 高风险防御：核心中军竞价≤-3%，持仓减仓至30%，设-5%止损",
                    raw_signals=raw_signals,
                    arbitration_path=["HIGH_RISK_DEFENSE"]
                )
            else:
                return ArbitrationResult(
                    symbol=symbol, name=name,
                    final_action=SignalAction.WATCH,
                    confidence=0.8, position_mult=0.0,
                    reason="🟡 高风险防御：核心中军竞价≤-3%，不开新仓，观望",
                    raw_signals=raw_signals,
                    arbitration_path=["HIGH_RISK_DEFENSE_NO_POS"]
                )

        # 3. 收集各层级证据
        evidence = self._collect_evidence(raw_signals, market_context)

        # 4. 按层级裁决
        final_action, conf, reason, path = self._arbitrate_by_hierarchy(
            evidence, raw_signals, market_context, held_position
        )

        # 5. 计算仓位乘数
        position_mult = self._calc_position_mult(
            final_action, conf, position_plan, market_context, held_position
        )

        # 6. 止损/支撑价
        stop_loss = self._calc_stop_loss(held_position) if held_position else None
        support = self._calc_support(held_position) if held_position else None

        return ArbitrationResult(
            symbol=symbol, name=name,
            final_action=final_action,
            confidence=conf,
            position_mult=position_mult,
            stop_loss=stop_loss,
            support_price=support,
            reason=reason,
            raw_signals=raw_signals,
            arbitration_path=path
        )

    def _normalize_action(self, action: str) -> int:
        """将各种动作文本归一化为 -2~2 强度"""
        action = action.strip()
        if "强烈买入" in action or "🟢买入" in action:
            return 2
        if "买入" in action or "🟢" in action:
            return 1
        if "持有" in action or "⚪" in action or "中性" in action:
            return 0
        if "卖出" in action or "🔴" in action or "强烈卖出" in action:
            return -1 if "强烈" not in action else -2
        if "错误" in action:
            return 0
        return 0

    def _collect_evidence(self, signals: List[RawSignal], ctx: dict) -> Dict[str, Any]:
        """按裁决层级收集证据"""
        evidence = {
            "negative_feedback": [],      # 负反馈
            "distribution_pattern": [],   # 出货形态
            "volume": [],                 # 量能
            "seal_direction": [],         # 封单方向
            "surprise": [],               # 超预期
            "technical": [],              # 技术指标
            "moat_bonus": 0.0,            # 护城河加分
        }

        health = ctx.get("health_score", 5)
        stage = ctx.get("market_stage", "震荡筑底")

        for s in signals:
            reason = s.reason.lower()
            strength = s.strength

            # 负反馈检测
            if any(kw in reason for kw in ["核心中军", "跌停传导", "板块退潮", "分歧未转一致", "负反馈"]):
                evidence["negative_feedback"].append((s.source, strength, s.reason))

            # 出货形态检测
            if any(kw in reason for kw in ["出货形态", "射击之星", "巨量长阴", "黄昏之星", "吊颈线", "乌云盖顶", "断头铡刀", "跳空回补"]):
                evidence["distribution_pattern"].append((s.source, strength, s.reason))

            # 量能信号
            if any(kw in reason for kw in ["缩量", "放量", "量比", "量能", "成交额"]):
                evidence["volume"].append((s.source, strength, s.reason))

            # 封单方向
            if any(kw in reason for kw in ["封单", "一字板", "封单量", "封单结构"]):
                evidence["seal_direction"].append((s.source, strength, s.reason))

            # 超预期
            if any(kw in reason for kw in ["该弱不弱", "跷跷板", "超预期", "超预期", "弱转强", "强转弱"]):
                evidence["surprise"].append((s.source, strength, s.reason))

            # 技术指标（默认归类）
            if any(kw in reason for kw in ["macd", "ema", "kdj", "rsi", "布林", "均线", "金叉", "死叉"]):
                evidence["technical"].append((s.source, strength, s.reason))

            # 护城河加分
            if s.moat_score >= 2.0:
                evidence["moat_bonus"] += 0.1

        # 市场健康度作为量能/封单的背景权重
        if health <= 3:
            evidence["volume"].append(("market_health", -1, f"健康度{health}/10极差，量能不可信"))
        elif health <= 5:
            evidence["volume"].append(("market_health", 0, f"健康度{health}/10偏弱，量能参考价值降低"))

        return evidence

    def _arbitrate_by_hierarchy(self,
                                evidence: dict,
                                signals: List[RawSignal],
                                ctx: dict,
                                held_position: dict) -> tuple:
        """按层级裁决，返回 (action, confidence, reason, path)"""
        path = []
        health = ctx.get("health_score", 5)

        # 层级1：负反馈（仅次于致命风险）
        if evidence["negative_feedback"]:
            path.append("NEGATIVE_FEEDBACK")
            # 负反馈通常意味着卖出/减仓
            avg_strength = sum(s[1] for s in evidence["negative_feedback"]) / len(evidence["negative_feedback"])
            if avg_strength <= -1:
                return SignalAction.SELL, 0.85, f"负反馈确认：{'; '.join(s[2] for s in evidence['negative_feedback'][:2])}", path
            else:
                return SignalAction.REDUCE, 0.75, f"负反馈警示：{'; '.join(s[2] for s in evidence['negative_feedback'][:2])}", path

        # 层级2：出货形态（强出货形态质疑买入信号）
        strong_dist = [e for e in evidence["distribution_pattern"] if "强度" in e[2] and ("4" in e[2] or "5" in e[2])]
        if strong_dist:
            path.append("DISTRIBUTION_PATTERN")
            # 有买入信号但出现强出货形态 -> 降级为观望/减仓
            has_buy = any(s.strength > 0 for s in signals)
            if has_buy:
                return SignalAction.WATCH, 0.8, f"出货形态质疑买入：{'; '.join(s[2] for s in strong_dist[:2])}", path
            else:
                return SignalAction.REDUCE, 0.7, f"出货形态确认卖出：{'; '.join(s[2] for s in strong_dist[:2])}", path

        # 层级3：量能
        if evidence["volume"]:
            path.append("VOLUME")
            avg_strength = sum(s[1] for s in evidence["volume"]) / len(evidence["volume"])
            if avg_strength <= -1 and health <= 5:
                return SignalAction.REDUCE, 0.7, f"量能配合看空：{'; '.join(s[2] for s in evidence['volume'][:2])}", path

        # 层级4：封单方向
        if evidence["seal_direction"]:
            path.append("SEAL_DIRECTION")
            avg_strength = sum(s[1] for s in evidence["seal_direction"]) / len(evidence["seal_direction"])
            if avg_strength >= 1:
                return SignalAction.BUY_SMALL, 0.65, f"封单方向看多：{'; '.join(s[2] for s in evidence['seal_direction'][:2])}", path

        # 层级5：超预期（该弱不弱 = 唯一可靠超预期买入信号）
        if evidence["surprise"]:
            path.append("SURPRISE")
            for src, strg, rsn in evidence["surprise"]:
                if "该弱不弱" in rsn and strg > 0:
                    return SignalAction.BUY, 0.8, f"超预期买入信号：{rsn}", path

        # 层级6：技术指标综合（加权投票）
        if evidence["technical"]:
            path.append("TECHNICAL_VOTE")
            # 按验证评分加权
            weighted_score = 0
            total_weight = 0
            for s in signals:
                if any(kw in s.reason.lower() for kw in ["macd", "ema", "kdj", "rsi", "布林", "均线"]):
                    weight = max(s.validation_score, 1.0)  # 至少1.0
                    weighted_score += s.strength * weight
                    total_weight += weight
            if total_weight > 0:
                avg = weighted_score / total_weight
                if avg >= 1.5:
                    return SignalAction.BUY_AGGRESSIVE, 0.7, f"技术指标强烈共振买入 (加权{avg:.1f})", path
                elif avg >= 0.5:
                    return SignalAction.BUY, 0.6, f"技术指标倾向买入 (加权{avg:.1f})", path
                elif avg <= -1.5:
                    return SignalAction.SELL, 0.7, f"技术指标强烈共振卖出 (加权{avg:.1f})", path
                elif avg <= -0.5:
                    return SignalAction.REDUCE, 0.6, f"技术指标倾向卖出 (加权{avg:.1f})", path

        # 默认：持有/观望
        path.append("DEFAULT_HOLD")
        return SignalAction.HOLD, 0.5, "无明确方向性信号，维持持有/观望", path

    def _calc_position_mult(self,
                            action: SignalAction,
                            confidence: float,
                            position_plan: dict,
                            ctx: dict,
                            held_position: dict) -> float:
        """计算最终仓位乘数（乘以 position_sizer 给出的单股上限）"""
        base_mult = position_plan.get("buy_signal_multiplier", 1.0)
        single_max_pct = position_plan.get("single_stock_max_pct", 0.05)
        total_limit_pct = position_plan.get("total_limit_pct", 0.0)

        # 总仓位已满时压缩
        if held_position:
            # 简化：假设当前持仓市值占总资金比例
            pass

        # 动作映射乘数
        action_mult = {
            SignalAction.FORCE_CLEAR: 0.0,
            SignalAction.SELL: 0.0,
            SignalAction.REDUCE: 0.3,
            SignalAction.HOLD: 0.0,      # 不增仓
            SignalAction.WATCH: 0.0,     # 观望不买
            SignalAction.BUY_SMALL: 0.4,
            SignalAction.BUY: 0.7,
            SignalAction.BUY_AGGRESSIVE: 1.0,
        }.get(action, 0.0)

        # 置信度调节
        conf_mult = max(0.3, confidence)  # 最低30%

        # 健康度/总仓位约束
        health = ctx.get("health_score", 5)
        if health <= 3:
            health_mult = 0.2
        elif health <= 5:
            health_mult = 0.5
        else:
            health_mult = 1.0

        final_mult = round(action_mult * conf_mult * health_mult * base_mult, 2)
        return max(0.0, min(final_mult, 1.0))

    def _calc_stop_loss(self, held_position: dict) -> Optional[float]:
        """计算止损价：成本价 * 0.95 或 近期低点"""
        cost = held_position.get("cost", 0)
        if cost > 0:
            return round(cost * 0.95, 2)
        return None

    def _calc_support(self, held_position: dict) -> Optional[float]:
        """计算支撑价：成本价 * 0.92"""
        cost = held_position.get("cost", 0)
        if cost > 0:
            return round(cost * 0.92, 2)
        return None


def load_signals_from_files() -> Dict[str, List[RawSignal]]:
    """从各模块输出文件加载原始信号，按股票分组"""
    today = datetime.now().strftime("%Y-%m-%d")
    signals_by_symbol = {}

    # 1. adaptive_dual 双策略日报
    dual_path = os.path.join(WORKSPACE, "analysis", "daily", f"{today}_dual.md")
    if os.path.exists(dual_path):
        _parse_dual_md(dual_path, signals_by_symbol)

    # 2. close_scan_v2 全量扫描
    scan_path = os.path.join(WORKSPACE, "analysis", "daily", f"{today}_adaptive.md")
    if os.path.exists(scan_path):
        _parse_dual_md(scan_path, signals_by_symbol)  # 格式相同

    # 3. signal_audit 审计发现（转为信号）
    audit_path = os.path.join(WORKSPACE, "analysis", "daily", f"{today}_signal_audit.md")
    if os.path.exists(audit_path):
        _parse_audit_md(audit_path, signals_by_symbol)

    return signals_by_symbol


def _parse_dual_md(path: str, signals_by_symbol: dict):
    """解析双策略日报格式"""
    import re
    with open(path) as f:
        text = f.read()

    cur_symbol = None
    cur_name = None
    for line in text.splitlines():
        m = re.match(r"### (.+?)\((\d{6})\)\s+现价¥?([\d.]+)\s+\(([+-]?[\d.]+)%\)\s*\[(.+?)\]", line)
        if m:
            cur_name, cur_symbol = m.group(1), m.group(2)
            continue
        m2 = re.match(r"-\s*\*\*(.+?)\*\s*\(score=([\d.]+)\):\s*(🟢买入|🔴卖出|持有|错误|无)\s*—\s*(.+)", line)
        if m2 and cur_symbol:
            strat = m2.group(1).replace("📊 ", "").replace("🎯 ", "").replace("📈 ", "").replace("💹 ", "").replace("📉 ", "")
            score = float(m2.group(2))
            action = m2.group(3)
            reason = m2.group(4)
            signals_by_symbol.setdefault(cur_symbol, []).append(RawSignal(
                source="adaptive_dual",
                symbol=cur_symbol,
                name=cur_name,
                action=action,
                strength=0,  # 后续归一化
                confidence=min(score / 100.0, 1.0) if score > 0 else 0.5,
                reason=f"[{strat}] {reason}",
                strategy=strat,
                validation_score=score,
            ))


def _parse_audit_md(path: str, signals_by_symbol: dict):
    """解析审计报告，转为负面信号"""
    import re
    with open(path) as f:
        text = f.read()

    for line in text.splitlines():
        # 格式: 🔴 [signal_conflict] 长电科技(600584) 策略冲突: MACD+RSI【买入】 vs 纯MACD【卖出】
        m = re.match(r"[🔴🟡🟢]\s*\[(.+?)\]\s*(.+?)\((\d{6})\)\s*(.+)", line)
        if m:
            find_type, name, symbol, msg = m.groups()
            # 只处理高危/中危
            if "🔴" in line or "🟡" in line:
                action = "卖出" if "冲突" in find_type or "矛盾" in find_type or "出货" in find_type else "持有"
                signals_by_symbol.setdefault(symbol, []).append(RawSignal(
                    source="signal_audit",
                    symbol=symbol,
                    name=name,
                    action=action,
                    strength=-1 if action == "卖出" else 0,
                    confidence=0.9 if "🔴" in line else 0.7,
                    reason=f"[审计-{find_type}] {msg}",
                    strategy="audit",
                ))


def load_fatal_risk() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(DATA_DIR, f"fatal_risk_{today}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"fatal_triggered": False, "high_risk_triggered": False}


def load_position_plan() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(DATA_DIR, f"position_plan_{today}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"buy_signal_multiplier": 1.0, "single_stock_max_pct": 0.05, "total_limit_pct": 0.5}


def load_market_context() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(DATA_DIR, f"market_health_{today}.json")
    ctx = {"health_score": 5, "market_stage": "震荡筑底", "emotion_cycle": "修复"}
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        summary = data.get("summary", {})
        ctx["health_score"] = summary.get("total_score", 5)
        score = ctx["health_score"]
        if score >= 7:
            ctx["market_stage"] = "上升趋势"
            ctx["emotion_cycle"] = "修复" if score < 9 else "高潮"
        elif score >= 5:
            ctx["market_stage"] = "震荡筑底"
        else:
            ctx["market_stage"] = "下跌趋势"
            ctx["emotion_cycle"] = "冰点" if score < 3 else "修复"
    return ctx


def load_held_positions() -> Dict[str, dict]:
    path = os.path.join(DATA_DIR, "portfolio_sim_state.json")
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        return data.get("positions", {})
    return {}


def save_results(results: List[ArbitrationResult]):
    """保存裁决结果"""
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(DATA_DIR, f"arbitration_{today}.json")
    with open(path, "w") as f:
        json.dump([asdict(r) for r in results], f, ensure_ascii=False, indent=2, default=str)


def print_brief(results: List[ArbitrationResult]):
    """标准化简报"""
    lines = [f"⚖️ **030统一信号裁决** ({datetime.now().strftime('%Y-%m-%d')})", ""]

    # 按动作分组
    by_action = {}
    for r in results:
        if not r.symbol:
            continue
        key = r.final_action.value
        by_action.setdefault(key, []).append(r)

    order = ["强制清仓", "卖出", "减仓", "积极买入", "买入", "小仓买入", "持有", "观望"]
    for act in order:
        if act not in by_action:
            continue
        items = by_action[act]
        tag = {"强制清仓": "☠️", "卖出": "🔴", "减仓": "🟠", "积极买入": "🟢",
               "买入": "🟢", "小仓买入": "🟢", "持有": "⚪", "观望": "🔵"}.get(act, "⚪")
        lines.append(f"{tag} **{act}** ({len(items)}只)")
        for r in items[:10]:  # 最多显示10只
            pos_info = f" 仓位乘数{r.position_mult:.1f}x"
            sl_info = f" 止损¥{r.stop_loss}" if r.stop_loss else ""
            lines.append(f"  {r.name}({r.symbol}){pos_info}{sl_info} — {r.reason[:60]}")
        if len(items) > 10:
            lines.append(f"  ... 及其他 {len(items)-10} 只")
        lines.append("")

    print("\n".join(lines))


def main():
    """独立运行：汇聚所有信号并裁决"""
    print("⚖️ 启动030统一信号裁决引擎...")

    # 加载输入
    signals_by_symbol = load_signals_from_files()
    fatal_risk = load_fatal_risk()
    position_plan = load_position_plan()
    market_context = load_market_context()
    held_positions = load_held_positions()

    if not signals_by_symbol:
        print("⚠️ 无原始信号输入，跳过裁决")
        return

    # 裁决每只股票
    arbitrator = SignalArbitrator()
    results = []

    for symbol, signals in signals_by_symbol.items():
        held = held_positions.get(symbol, {})
        result = arbitrator.arbitrate(signals, fatal_risk, position_plan, market_context, held)
        results.append(result)

    # 保存 & 输出
    save_results(results)
    print_brief(results)

    # stdout JSON
    print("\n=====ARBITRATION_RESULT=====")
    print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2, default=str))
    print("=====ARBITRATION_END=====")


if __name__ == "__main__":
    main()