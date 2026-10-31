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
import pandas as pd
import pandas_ta as ta
import numpy as np

import os
import sys
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 情绪反转覆盖（P0 2026-11-01 落地，源自 10-28 教训）
try:
    from analysis.emotion_reversal import load_reversal_override
except ImportError:  # 兜底：脚本直跑时按 analysis 目录内模块导入
    from emotion_reversal import load_reversal_override

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
DATA_DIR = os.path.join(WORKSPACE, "data")
FACTOR_SCORES_FILE = os.path.join(WORKSPACE, "data", "factor_scores.json")
os.makedirs(DATA_DIR, exist_ok=True)

# 缓存因子得分
_factor_scores_cache = None

def _load_factor_scores() -> Dict:
    global _factor_scores_cache
    if _factor_scores_cache is not None:
        return _factor_scores_cache
    if os.path.exists(FACTOR_SCORES_FILE):
        try:
            with open(FACTOR_SCORES_FILE, encoding="utf-8") as f:
                _factor_scores_cache = json.load(f)
                return _factor_scores_cache
        except Exception as _e:  # noqa: BLE001
            print(f"⚠️ factor_scores 读取失败,退默认权重(可见告警,原先静默): {_e}", file=sys.stderr)
    return {"scores": {}, "weights": {"core_product": 0.6, "oversea_qual": 0.4}}

def get_factor_score(symbol: str) -> dict:
    """获取单只股票的因子详细得分"""
    data = _load_factor_scores()
    return data.get("scores", {}).get(symbol, {})


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
    arbitration_path: List[str] = None     # 裁决路径，用于审计追溯（signal_audit 消费，保持不变）
    decision_trace: List[dict] = None      # 决策轨迹（2026-11-01 B阶段：逐层数字证据，回答「为什么降级/压缩」）
    moat_bonus: float = 0.0    # 护城河加分

    def __post_init__(self):
        if self.raw_signals is None:
            self.raw_signals = []
        if self.arbitration_path is None:
            self.arbitration_path = []
        if self.decision_trace is None:
            self.decision_trace = []


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
        "technical": 55,             # 技术指标（MACD、EMA、KDJ等）— 提升至 55，高于封单/超预期
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
                reason="无原始信号输入",
                decision_trace=[{"stage": "INPUT", "n_signals": 0}]
            )

        symbol = raw_signals[0].symbol
        name = raw_signals[0].name

        # 归一化动作到强度
        for s in raw_signals:
            s.strength = self._normalize_action(s.action)

        # 决策轨迹（B阶段 2026-11-01）：每层裁决记录数字证据，消灭「为什么」盲区
        trace: List[dict] = [{
            "stage": "INPUT",
            "symbol": symbol,
            "n_signals": len(raw_signals),
            "signals": [
                {"source": s.source, "action": s.action, "strength": s.strength,
                 "strategy": s.strategy, "validation_score": round(s.validation_score, 1)}
                for s in raw_signals
            ],
        }]

        # 1. 致命风险绝对否决（最高优先级）
        if fatal_risk.get("fatal_triggered", False):
            trace.append({"stage": "FATAL_RISK_OVERRIDE", "fired": True})
            return ArbitrationResult(
                symbol=symbol, name=name,
                final_action=SignalAction.FORCE_CLEAR,
                confidence=1.0, position_mult=0.0,
                reason="☠️ 触发030致命风险：无条件清仓，禁止买入",
                raw_signals=raw_signals,
                arbitration_path=["FATAL_RISK_OVERRIDE"],
                decision_trace=trace
            )

        # 2. 高风险防御
        high_risk = fatal_risk.get("high_risk_triggered", False)
        if high_risk:
            # 持仓则减仓/止损，无仓则禁买
            trace.append({"stage": "HIGH_RISK_DEFENSE",
                          "has_position": bool(held_position and held_position.get("shares", 0) > 0)})
            if held_position and held_position.get("shares", 0) > 0:
                return ArbitrationResult(
                    symbol=symbol, name=name,
                    final_action=SignalAction.REDUCE,
                    confidence=0.9, position_mult=0.3,
                    stop_loss=self._calc_stop_loss(held_position),
                    support_price=self._calc_support(held_position),
                    reason="🟡 高风险防御：核心中军竞价≤-3%，持仓减仓至30%，设-5%止损",
                    raw_signals=raw_signals,
                    arbitration_path=["HIGH_RISK_DEFENSE"],
                    decision_trace=trace
                )
            else:
                return ArbitrationResult(
                    symbol=symbol, name=name,
                    final_action=SignalAction.WATCH,
                    confidence=0.8, position_mult=0.0,
                    reason="🟡 高风险防御：核心中军竞价≤-3%，不开新仓，观望",
                    raw_signals=raw_signals,
                    arbitration_path=["HIGH_RISK_DEFENSE_NO_POS"],
                    decision_trace=trace
                )

        # 3. 收集各层级证据
        evidence = self._collect_evidence(raw_signals, market_context)
        trace.append({
            "stage": "EVIDENCE",
            "negative_feedback": len(evidence["negative_feedback"]),
            "distribution_pattern": len(evidence["distribution_pattern"]),
            "volume": len(evidence["volume"]),
            "seal_direction": len(evidence["seal_direction"]),
            "surprise": len(evidence["surprise"]),
            "technical": len(evidence["technical"]),
            "moat_bonus": round(evidence.get("moat_bonus", 0.0), 3),
            "health_score": market_context.get("health_score", 5),
        })

        # 4. 按层级裁决
        final_action, conf, reason, path = self._arbitrate_by_hierarchy(
            evidence, raw_signals, market_context, held_position, trace
        )
        trace.append({"stage": "HIERARCHY_RESULT", "action": final_action.value,
                      "path": list(path), "confidence": round(conf, 2)})

        # 4.5 情绪反转覆盖（P0 2026-11-01，10-28 教训）
        # 竞价 fade_risk 票「高开未回落+收涨」+ 跷跷板验证 = 情绪反转首日，
        # 技术性卖出信号在反转首日天然滞后 -> 暂时覆盖，仅保留硬止损。
        # 结构性风险路径（负反馈/出货形态）不覆盖——只覆盖纯技术卖出；
        # 覆盖名单有效期仅限确认日的次一交易日（load_reversal_override 内控）。
        try:
            _rev = load_reversal_override(symbol, self.today)
        except Exception as _e:  # noqa: BLE001
            print(f"  [REVERSAL] 反转覆盖加载失败(fail-closed): {_e}")
            _rev = None
            # B阶段：fail-closed 原先只打 stdout，JSON 无痕 → 补轨迹留痕
            trace.append({"stage": "REVERSAL_LOAD_FAILED", "fail_closed": True, "error": str(_e)})
        if _rev and final_action in (SignalAction.SELL, SignalAction.REDUCE):
            _structural_paths = {"NEGATIVE_FEEDBACK", "DISTRIBUTION_PATTERN"}
            if not (_structural_paths & set(path)):
                _pre_action = final_action.value
                final_action = SignalAction.HOLD
                conf = 0.6
                reason = (f"🔁 情绪反转确认({_rev['confirmed_date']})覆盖技术卖出："
                          f"反转首日技术信号滞后，仅执行硬止损(-{_rev['hard_stop_pct']:.0f}%)")
                path.append("REVERSAL_OVERRIDE")
                trace.append({"stage": "REVERSAL_OVERRIDE", "confirmed_date": _rev["confirmed_date"],
                              "overridden_from": _pre_action, "overridden_to": "持有",
                              "hard_stop_pct": _rev["hard_stop_pct"]})
                print(f"  [REVERSAL] {symbol}: 技术卖出被反转确认覆盖(确认日 {_rev['confirmed_date']})")

        # 4.7 降级/升级留痕（B阶段）：原始信号方向 vs 最终裁决方向矛盾时记录证据
        # （signal_audit 的 arbitration_unexplained_* 消费 path 标签；此处补「为什么」数字链）
        _raw_buys = [s for s in raw_signals if s.strength >= 1]
        _raw_sells = [s for s in raw_signals if s.strength <= -1]
        if _raw_buys and final_action in (SignalAction.HOLD, SignalAction.WATCH):
            trace.append({"stage": "DOWNGRADE_NOTE", "from": "买入", "to": final_action.value,
                          "n_buy_signals": len(_raw_buys), "final_reason": reason})
        if _raw_sells and final_action in (SignalAction.BUY, SignalAction.BUY_SMALL,
                                           SignalAction.BUY_AGGRESSIVE):
            trace.append({"stage": "UPGRADE_NOTE", "from": "卖出", "to": final_action.value,
                          "n_sell_signals": len(_raw_sells), "final_reason": reason})

        # 5. 计算仓位乘数（返回分量明细：健康度压缩透明化）
        position_mult, _mult_parts = self._calc_position_mult(
            final_action, conf, position_plan, market_context, held_position
        )
        trace.append({"stage": "POSITION_MULT", **_mult_parts})

        # 6. 止损/支撑价
        # [2026-10-29] 幽灵持仓过滤：shares=0（已清仓残留条目）的历史成本不得产出止损/支撑位。
        # 实证：国瓷材料幽灵 cost 67.27 → 假止损 63.91 vs 现价 41.90（+52%），
        # next_day_sim stop_loss_map 因此出现 7 只“止损高于现价”的语义错误。
        _has_shares = bool(held_position) and (held_position.get("shares", 0) or 0) > 0
        stop_loss = self._calc_stop_loss(held_position) if _has_shares else None
        support = self._calc_support(held_position) if _has_shares else None
        trace.append({
            "stage": "STOP_LOSS",
            "has_shares": _has_shares,
            "stop_loss": stop_loss,
            "source": (self._stop_loss_source(held_position) if _has_shares
                       else "无持仓(幽灵过滤不产出)"),
        })
        return ArbitrationResult(
            symbol=symbol, name=name,
            final_action=final_action,
            confidence=conf,
            position_mult=position_mult,
            stop_loss=stop_loss,
            support_price=support,
            reason=reason,
            raw_signals=raw_signals,
            arbitration_path=path,
            decision_trace=trace,
            moat_bonus=evidence.get("moat_bonus", 0.0)
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

            # 护城河加分：量化 core_product_total / oversea_qual_total
            factor = get_factor_score(s.symbol)
            core_prod = factor.get("core_product_total", 0)
            oversea_qual = factor.get("oversea_qual_total", 0)
            combined = factor.get("combined_score", 0)
            
            if core_prod > 0 or oversea_qual > 0:
                # 权重：核心产品 0.6，出海资质 0.4（与 factor_engine 一致）
                # 归一化：core_product_total 满分 30，oversea_qual_total 满分 25
                core_norm = min(core_prod / 30.0, 1.0)
                oversea_norm = min(oversea_qual / 25.0, 1.0)
                weighted_moat = core_norm * 0.6 + oversea_norm * 0.4
                bonus = round(weighted_moat * 0.2, 3)  # 最大加 0.2
                evidence["moat_bonus"] += bonus
            elif s.moat_score >= 2.0:
                # 兜底：兼容旧版 moat_score
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
                                held_position: dict,
                                trace: List[dict] = None) -> tuple:
        """按层级裁决，返回 (action, confidence, reason, path)；trace 非空时逐层记录数字证据"""
        path = []
        trace = trace if trace is not None else []
        health = ctx.get("health_score", 5)

        # 层级1：负反馈（仅次于致命风险）
        if evidence["negative_feedback"]:
            path.append("NEGATIVE_FEEDBACK")
            # 负反馈通常意味着卖出/减仓
            avg_strength = sum(s[1] for s in evidence["negative_feedback"]) / len(evidence["negative_feedback"])
            trace.append({"stage": "NEGATIVE_FEEDBACK", "n": len(evidence["negative_feedback"]),
                          "avg_strength": round(avg_strength, 2), "fired": True})
            if avg_strength <= -1:
                return SignalAction.SELL, 0.85, f"负反馈确认：{'; '.join(s[2] for s in evidence['negative_feedback'][:2])}", path
            else:
                return SignalAction.REDUCE, 0.75, f"负反馈警示：{'; '.join(s[2] for s in evidence['negative_feedback'][:2])}", path

        # 层级2：出货形态（强出货形态质疑买入信号）
        strong_dist = [e for e in evidence["distribution_pattern"] if "强度" in e[2] and ("4" in e[2] or "5" in e[2])]
        trace.append({"stage": "DISTRIBUTION_PATTERN", "n_strong": len(strong_dist),
                      "fired": bool(strong_dist)})
        if strong_dist:
            path.append("DISTRIBUTION_PATTERN")
            # 有买入信号但出现强出货形态 -> 降级为观望/减仓
            has_buy = any(s.strength > 0 for s in signals)
            if has_buy:
                return SignalAction.WATCH, 0.8, f"出货形态质疑买入：{'; '.join(s[2] for s in strong_dist[:2])}", path
            else:
                return SignalAction.REDUCE, 0.7, f"出货形态确认卖出：{'; '.join(s[2] for s in strong_dist[:2])}", path

        # 层级2.5：连续 N 天策略卖出硬规则（新增 P2）
        # 统计来自 adaptive_dual/adaptive_trader/close_scan 的连续卖出天数
        tech_sell_streak = 0
        for s in signals:
            if s.source in ("adaptive_dual", "adaptive_trader", "close_scan_v2") and s.strength <= -1:
                # 这里简化处理：实际应读取历史文件，暂用 reason 里的 "连续" 关键词判断
                if "连续" in s.reason and "卖" in s.reason:
                    import re
                    m = re.search(r"连续(\d+)天", s.reason)
                    if m:
                        tech_sell_streak = max(tech_sell_streak, int(m.group(1)))
        # 兜底：若无明确天数，统计当前信号中卖出源的数量
        if tech_sell_streak == 0:
            tech_sell_streak = sum(1 for s in signals if s.source in ("adaptive_dual", "adaptive_trader", "close_scan_v2") and s.strength <= -1)
        trace.append({"stage": "CONSECUTIVE_SELL_CHECK", "streak": tech_sell_streak,
                      "fired": tech_sell_streak >= 3})
        
        if tech_sell_streak >= 3:
            path.append("CONSECUTIVE_SELL_HARD_RULE")
            if held_position and held_position.get("shares", 0) > 0:
                return SignalAction.SELL, 0.9, f"连续 {tech_sell_streak} 天技术面卖出信号，硬规则触发强制卖出", path
            else:
                return SignalAction.WATCH, 0.85, f"连续 {tech_sell_streak} 天技术面卖出信号，空仓禁买观望", path

        # 层级3：量能
        if evidence["volume"]:
            path.append("VOLUME")
            avg_strength = sum(s[1] for s in evidence["volume"]) / len(evidence["volume"])
            trace.append({"stage": "VOLUME", "n": len(evidence["volume"]),
                          "avg_strength": round(avg_strength, 2), "health": health,
                          "fired": bool(avg_strength <= -1 and health <= 5)})
            if avg_strength <= -1 and health <= 5:
                return SignalAction.REDUCE, 0.7, f"量能配合看空：{'; '.join(s[2] for s in evidence['volume'][:2])}", path

        # 层级4：封单方向
        if evidence["seal_direction"]:
            path.append("SEAL_DIRECTION")
            avg_strength = sum(s[1] for s in evidence["seal_direction"]) / len(evidence["seal_direction"])
            trace.append({"stage": "SEAL_DIRECTION", "n": len(evidence["seal_direction"]),
                          "avg_strength": round(avg_strength, 2),
                          "fired": bool(avg_strength >= 1)})
            if avg_strength >= 1:
                return SignalAction.BUY_SMALL, 0.65, f"封单方向看多：{'; '.join(s[2] for s in evidence['seal_direction'][:2])}", path

        # 层级5：超预期（该弱不弱 = 唯一可靠超预期买入信号）
        if evidence["surprise"]:
            path.append("SURPRISE")
            trace.append({"stage": "SURPRISE", "n": len(evidence["surprise"]),
                          "hits": [r for _, st, r in evidence["surprise"] if "该弱不弱" in r and st > 0]})
            for src, strg, rsn in evidence["surprise"]:
                if "该弱不弱" in rsn and strg > 0:
                    return SignalAction.BUY, 0.8, f"超预期买入信号：{rsn}", path

        # 层级5.5：RSI 趋势强度过滤器 (P0) —— 在技术投票前插入
        rsi_trend = self._eval_rsi_trend_strength(signals, ctx)
        trace.append({"stage": "RSI_TREND_FILTER", "value": rsi_trend,
                      "fired": rsi_trend != 0})
        if rsi_trend != 0:
            path.append("RSI_TREND_FILTER")
            if rsi_trend > 0:
                # 趋势多头：提升买入置信度，但不直接触发买入（需技术投票配合）
                # 记录到 evidence 供技术投票加权使用
                evidence["technical"].append(("rsi_trend_filter", 1, f"RSI趋势多头强度(health={ctx.get('health_score',5)})"))
            else:
                evidence["technical"].append(("rsi_trend_filter", -1, f"RSI趋势空头强度(health={ctx.get('health_score',5)})"))

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
            avg = (weighted_score / total_weight) if total_weight > 0 else None
            trace.append({"stage": "TECHNICAL_VOTE", "weighted_avg": (round(avg, 3) if avg is not None else None),
                          "n_weighted": total_weight and len([s for s in signals if any(kw in s.reason.lower() for kw in ["macd", "ema", "kdj", "rsi", "布林", "均线"])]),
                          "thresholds": {"agg_buy": 1.5, "buy": 0.5, "sell": -0.5, "agg_sell": -1.5},
                          "fired": bool(avg is not None and abs(avg) >= 0.5)})
            if total_weight > 0:
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
        trace.append({"stage": "DEFAULT_HOLD",
                      "strongest_raw": max((s.strength for s in signals), default=0),
                      "note": "无层级命中：负反馈/出货/连续卖/量能/封单/超预期/技术投票均未触发"})
        return SignalAction.HOLD, 0.5, "无明确方向性信号，维持持有/观望", path

    def _eval_rsi_trend_strength(self, signals: List[RawSignal], ctx: dict) -> int:
        """
        RSI 趋势强度过滤器 (P0)
        返回: +1(趋势多头), -1(趋势空头), 0(中性/无信号)
        
        逻辑：
        - 需要从信号中提取 RSI 值，或从最近数据计算
        - ADX > 25 判定趋势态，ADX < 20 震荡态
        - 牛市区间 RSI 40-80，熊市区间 20-60
        - RSI > 60 且 ADX > 25 → 趋势多头 +1
        - RSI < 40 且 ADX > 25 → 趋势空头 -1
        - 隐藏背离识别：价格创新高/低但 RSI 未创新高/低 → 趋势延续信号
        """
        # 尝试从信号 reasons 中提取 RSI
        rsi_values = []
        for s in signals:
            reason = s.reason
            # 查找 RSI=xx 或 RSI xx 格式
            import re
            m = re.search(r'RSI[=: ]?([\d.]+)', reason, re.IGNORECASE)
            if m:
                try:
                    rsi_values.append(float(m.group(1)))
                except:
                    pass
            # 查找 CCI_pct=xx% 格式 (KDJ+CCI策略用)
            m = re.search(r'CCI_pct[=: ]?([\d.]+)%', reason, re.IGNORECASE)
            if m:
                try:
                    cci_pct = float(m.group(1))
                    # CCI_pct 可近似映射 RSI 区间: <20->超卖, >80->超买
                    # 这里仅作参考，不直接转 RSI
                except:
                    pass
        
        # 如果信号里没有 RSI，返回 0（后续可接入实时数据计算）
        if not rsi_values:
            return 0
        
        avg_rsi = sum(rsi_values) / len(rsi_values)
        
        # 简易市场牛熊判定：health_score >= 6 视为牛市偏多，<=4 视为熊市偏空
        health = ctx.get("health_score", 5)
        is_bull_market = health >= 6
        is_bear_market = health <= 4
        
        # 牛市区间 40-80，熊市区间 20-60
        if is_bull_market:
            if avg_rsi > 60:
                return 1  # 牛市中 RSI>60 趋势强
            elif avg_rsi < 40:
                return -1  # 牛市中 RSI<40 回调
        elif is_bear_market:
            if avg_rsi > 60:
                return -1  # 熊市中 RSI>60 反弹乏力
            elif avg_rsi < 40:
                return -1  # 熊市中 RSI<40 趋势空
        else:  # 震荡市
            if avg_rsi > 70:
                return -1  # 震荡超买
            elif avg_rsi < 30:
                return 1   # 震荡超卖反弹
        
        return 0

    def _calc_position_mult(self,
                            action: SignalAction,
                            confidence: float,
                            position_plan: dict,
                            ctx: dict,
                            held_position: dict) -> tuple:
        """计算最终仓位乘数；返回 (final_mult, 分量明细 dict) 供决策轨迹留痕（B阶段）"""
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

        final_mult = round(max(0.0, min(action_mult * conf_mult * health_mult * base_mult, 1.0)), 2)
        parts = {
            "action": action.value,
            "action_mult": action_mult,
            "conf": round(confidence, 2),
            "conf_mult": round(conf_mult, 2),
            "health": health,
            "health_mult": health_mult,
            "base_mult": base_mult,
            "final": final_mult,
        }
        return final_mult, parts

    def _calc_stop_loss(self, held_position: dict) -> Optional[float]:
        """计算止损价：优先用 portfolio_sim 计算好的 atr_stop，其次成本价 * 0.95"""
        # 1. 优先使用 portfolio_sim 已计算的 ATR 止损
        atr_stop = held_position.get("atr_stop")
        if atr_stop is not None and atr_stop > 0:
            return float(atr_stop)
        # 2. 兼容字段名：cost / entry_price
        cost = held_position.get("cost") or held_position.get("entry_price") or 0
        if cost > 0:
            return round(cost * 0.95, 2)
        return None

    @staticmethod
    def _stop_loss_source(held_position: dict) -> str:
        """止损价来源标注（B阶段决策轨迹用）：atr_stop / cost×0.95 / 无成本不可算"""
        atr_stop = held_position.get("atr_stop")
        if atr_stop is not None and atr_stop > 0:
            return "atr_stop"
        cost = held_position.get("cost") or held_position.get("entry_price") or 0
        return "cost×0.95" if cost > 0 else "无成本(不可计算)"

    def _calc_support(self, held_position: dict) -> Optional[float]:
        """计算支撑价：成本价 * 0.92"""
        cost = held_position.get("cost") or held_position.get("entry_price") or 0
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
    # [2026-10-17] 文件缺失时改现算(SSOT=position_sizer.compute_plan_now),静态50%兜底仅作最后防线
    try:
        from analysis.position_sizer import compute_plan_now
        return compute_plan_now(verbose=False)
    except Exception as _e:
        print(f"⚠️ position_plan 现算失败,退静态兜底(50%): {_e}")
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
    _bak(path)
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


def _selftest() -> int:
    """对抗自测（无网络；factor_scores 本地缓存读取容许缺失）：决策轨迹结构 + 降级留痕 + 反转覆盖留痕"""
    import json as _json
    from dataclasses import asdict as _asdict

    def _sig(action, reason, strategy="纯MACD", score=80.0):
        return RawSignal(source="adaptive_dual", symbol="600584", name="长电科技",
                         action=action, strength=0, confidence=0.8, reason=reason,
                         strategy=strategy, validation_score=score)

    arb = SignalArbitrator()

    # 1) 单买入 → BUY：轨迹含 INPUT/TECHNICAL_VOTE/POSITION_MULT，无降级注记
    r = arb.arbitrate([_sig("买入", "[纯MACD] MACD金叉放量", score=80.0)],
                      {}, {"buy_signal_multiplier": 1.0}, {"health_score": 6}, None)
    stages = [t["stage"] for t in r.decision_trace]
    t1 = (r.final_action == SignalAction.BUY and "INPUT" in stages
          and "TECHNICAL_VOTE" in stages and "POSITION_MULT" in stages
          and "DOWNGRADE_NOTE" not in stages)

    # 2) 弱买入被加权票压平 → DEFAULT_HOLD 必须留痕（数字证据 + 降级注记）
    sigs = [_sig("买入", "[纯MACD] 金叉", score=1.0)]
    sigs += [_sig("持有", "[均线] 均线走平", strategy="均线", score=1.0) for _ in range(3)]
    r2 = arb.arbitrate(sigs, {}, {"buy_signal_multiplier": 1.0}, {"health_score": 6}, None)
    stages2 = [t["stage"] for t in r2.decision_trace]
    tv = next((t for t in r2.decision_trace if t["stage"] == "TECHNICAL_VOTE"), {})
    t2 = (r2.final_action == SignalAction.HOLD and "DEFAULT_HOLD" in stages2
          and "DOWNGRADE_NOTE" in stages2 and tv.get("weighted_avg") is not None)

    # 3) 健康度压缩透明化：health=2 → health_mult=0.2 进轨迹
    r3 = arb.arbitrate([_sig("买入", "[纯MACD] 金叉", score=80.0)],
                       {}, {"buy_signal_multiplier": 1.0}, {"health_score": 2}, None)
    pm = next((t for t in r3.decision_trace if t["stage"] == "POSITION_MULT"), {})
    t3 = pm.get("health_mult") == 0.2

    # 4) 反转覆盖留痕（monkeypatch，无文件 IO）
    _orig = load_reversal_override
    try:
        globals()["load_reversal_override"] = (
            lambda sym, date: {"confirmed_date": "2026-10-28", "hard_stop_pct": 5.0})
        r4 = arb.arbitrate([_sig("卖出", "[纯MACD] 死叉", score=60.0)],
                           {}, {}, {"health_score": 6},
                           {"symbol": "600584", "shares": 100, "cost": 50.0})
        t4 = ("REVERSAL_OVERRIDE" in [t["stage"] for t in r4.decision_trace]
              and r4.final_action == SignalAction.HOLD
              and any(t.get("overridden_from") for t in r4.decision_trace))
    finally:
        globals()["load_reversal_override"] = _orig

    # 5) 反转加载失败 fail-closed 留痕（技术卖出照常，但轨迹可迮）
    try:
        def _boom(sym, date):
            raise RuntimeError("file corrupt")
        globals()["load_reversal_override"] = _boom
        r5 = arb.arbitrate([_sig("卖出", "[纯MACD] 死叉", score=60.0)],
                           {}, {}, {"health_score": 6},
                           {"symbol": "600584", "shares": 100, "cost": 50.0})
        t5 = ("REVERSAL_LOAD_FAILED" in [t["stage"] for t in r5.decision_trace]
              and r5.final_action in (SignalAction.SELL, SignalAction.REDUCE))
    finally:
        globals()["load_reversal_override"] = _orig

    # 6) 轨迹可 JSON 序列化（落盘契约：asdict → json.dump）
    try:
        _json.dumps(_asdict(r4), ensure_ascii=False, default=str)
        t6 = True
    except Exception:
        t6 = False

    cases = [("买入轨迹完整", t1), ("DEFAULT_HOLD降级留痕", t2), ("健康度压缩透明", t3),
             ("反转覆盖留痕", t4), ("反转fail-closed留痕", t5), ("轨迹可序列化", t6)]
    failed = [n for n, ok in cases if not ok]
    for n, ok in cases:
        print(f"  {'✅' if ok else '❌'} {n}")
    if failed:
        print(f"SELFTEST FAILED: {failed}")
        return 1
    print("SELFTEST PASSED (6/6)")
    return 0


def main():
    """独立运行：汇聚所有信号并裁决"""
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
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