#!/usr/bin/env python3
"""
🧠 挑战质疑审查器 — 规则质疑集
====================================
对 agent 生成的每日盯盘报告做"第二意见式"穷追 why，输出质疑清单推给用户看。
设计原则：
- 只读取已生成文件(agent_runs/*.json)，不重新拉数据
- 基于规则清单的机械追问(免费、快、可复现)
- 输出结构化质疑清单 + 人可读摘要 + JSON 供 cron 解析推送
"""

import os, sys, json, re, glob
from datetime import datetime
from typing import List, Dict, Any, Optional

WORKSPACE = "/Users/duguke/.openclaw/workspace"
AGENT_RUNS_DIR = os.path.join(WORKSPACE, "data", "agent_runs")
DAILY_DIR = os.path.join(WORKSPACE, "analysis", "daily")

# ──────────────────────────────────────────
# 规则质疑集：每条规则对应一个 why 追问
# ──────────────────────────────────────────

class ChallengeRule:
    """单条质疑规则：给定 agent_state，返回 0-N 个质疑项"""
    def __init__(self, name: str, severity: str, check_fn, describe_fn):
        self.name = name
        self.severity = severity  # HIGH / MEDIUM / LOW
        self.check_fn = check_fn
        self.describe_fn = describe_fn

    def apply(self, state: Dict) -> List[Dict]:
        findings = self.check_fn(state)
        for f in findings:
            f["rule"] = self.name
            f["severity"] = self.severity
            f["question"] = self.describe_fn(f)
        return findings


# ── 规则 1: 信号等级与最终建议的方向一致性 ──
def check_signal_vs_advice(state):
    """agent 给出的信号 level 与 final_advice 的 action 是否一致？"""
    findings = []
    signals = {s["symbol"]: s for s in state.get("signals", []) if "error" not in s}
    try:
        advice_list = json.loads(state.get("final_advice", "[]"))
    except Exception:
        advice_list = []
    advice_map = {a["symbol"]: a for a in advice_list}

    # level -> 预期 action 映射(参考 rule_risk_advice 逻辑)
    def expected_action(level: str) -> str:
        lvl = re.sub(r"[\ufffd\ufeff\s]", "", str(level))
        if "强烈卖出" in lvl:
            return "SELL"
        if "强烈买入" in lvl or "关注" in lvl:
            return "BUY"
        return "HOLD"

    for sym, sig in signals.items():
        level = sig.get("signal", {}).get("level", "中性")
        exp = expected_action(level)
        adv = advice_map.get(sym)
        if adv and adv.get("action") != exp:
            findings.append({
                "type": "signal_advice_mismatch",
                "symbol": sym,
                "name": sig.get("name"),
                "signal_level": level,
                "expected": exp,
                "actual": adv.get("action"),
                "advice_reason": adv.get("reason"),
            })
    return findings
def describe_signal_vs_advice(f):
    return (f"为什么 {f['name']}({f['symbol']}) 信号是【{f['signal_level']}】(应 {f['expected']})，"
            f"但最终建议却是 {f['actual']}？理由：{f['advice_reason']}")


# ── 规则 2: 同股票指标内部矛盾 (MACD多头 vs EMA空头，却给买入) ──
def check_indicator_contradiction(state):
    findings = []
    signals = {s["symbol"]: s for s in state.get("signals", []) if "error" not in s}
    try:
        advice_list = json.loads(state.get("final_advice", "[]"))
    except Exception:
        advice_list = []
    advice_map = {a["symbol"]: a for a in advice_list}

    for sym, sig in signals.items():
        ind = sig.get("indicators", {})
        macd_bull = ind.get("MACD_bull")
        ema_bull = ind.get("EMA_bull")
        adv = advice_map.get(sym)
        if adv and adv.get("action") == "BUY":
            # 买入建议，但 MACD 空头 且 EMA 空头
            if macd_bull is False and ema_bull is False:
                findings.append({
                    "type": "indicator_contradiction_buy",
                    "symbol": sym,
                    "name": sig.get("name"),
                    "MACD_bull": macd_bull,
                    "EMA_bull": ema_bull,
                    "action": adv.get("action"),
                    "reason": adv.get("reason"),
                })
            # 买入建议，但 RSI 超买(>70)
            rsi = ind.get("RSI14")
            if rsi is not None and rsi > 70:
                findings.append({
                    "type": "indicator_contradiction_rsi_overbought",
                    "symbol": sym,
                    "name": sig.get("name"),
                    "RSI14": rsi,
                    "action": adv.get("action"),
                    "reason": adv.get("reason"),
                })
    return findings
def describe_indicator_contradiction(f):
    if f["type"] == "indicator_contradiction_buy":
        return (f"为什么 {f['name']}({f['symbol']}) MACD空头({f['MACD_bull']}) 且 EMA空头({f['EMA_bull']})，"
                f"却给出 BUY 建议？理由：{f['reason']}")
    return (f"为什么 {f['name']}({f['symbol']}) RSI={f['RSI14']:.1f} 处于超买区(>70)，"
            f"却仍给出 BUY 建议？理由：{f['reason']}")


# ── 规则 3: 风险标记与建议的矛盾 ──
def check_risk_flag_vs_advice(state):
    findings = []
    risk_flag = state.get("risk_flag", "无")
    try:
        advice_list = json.loads(state.get("final_advice", "[]"))
    except Exception:
        advice_list = []

    sell_count = sum(1 for a in advice_list if a.get("action") == "SELL")
    buy_count = sum(1 for a in advice_list if a.get("action") == "BUY")

    # 风险标记为"无"，但 SELL 建议很多(≥5)
    if risk_flag == "无" and sell_count >= 5:
        findings.append({
            "type": "risk_flag_vs_sell_heavy",
            "risk_flag": risk_flag,
            "sell_count": sell_count,
            "buy_count": buy_count,
            "total": len(advice_list),
        })
    # 风险标记为"致命风险"，但仍有 BUY 建议
    if risk_flag == "致命风险" and buy_count > 0:
        findings.append({
            "type": "risk_flag_vs_buy_under_fatal",
            "risk_flag": risk_flag,
            "buy_count": buy_count,
            "buy_symbols": [a["symbol"] for a in advice_list if a.get("action") == "BUY"],
        })
    return findings
def describe_risk_flag(f):
    if f["type"] == "risk_flag_vs_sell_heavy":
        return (f"为什么风险标记是【{f['risk_flag']}】，但 {f['sell_count']}/{f['total']} 只股票给出 SELL 建议？"
                f"是否低估了系统性风险？")
    return (f"为什么风险标记是【{f['risk_flag']}】，却仍有 {f['buy_count']} 只股票建议 BUY({', '.join(f['buy_symbols'])})？"
            f"致命风险下还买入是否违背 030 风控？")


# ── 规则 4: 市场健康度与整体买入建议的配称性 ──
def check_health_vs_overall_buy(state):
    findings = []
    health = state.get("health_score")
    try:
        advice_list = json.loads(state.get("final_advice", "[]"))
    except Exception:
        advice_list = []
    buy_count = sum(1 for a in advice_list if a.get("action") == "BUY")
    total = len(advice_list)

    if health is not None:
        # 健康度≤4(降仓/空仓区)，但 BUY 占比高(>30%)
        if health <= 4 and total > 0 and buy_count / total > 0.3:
            findings.append({
                "type": "health_low_buy_heavy",
                "health_score": health,
                "buy_count": buy_count,
                "total": total,
                "buy_pct": round(buy_count / total * 100, 1),
            })
        # 健康度≥8(积极区)，但 BUY 很少(<10%)
        if health >= 8 and total > 0 and buy_count / total < 0.1:
            findings.append({
                "type": "health_high_buy_light",
                "health_score": health,
                "buy_count": buy_count,
                "total": total,
                "buy_pct": round(buy_count / total * 100, 1),
            })
    return findings
def describe_health(f):
    if f["type"] == "health_low_buy_heavy":
        return (f"为什么 030 健康度仅 {f['health_score']}/10(降仓/空仓区)，"
                f"却有 {f['buy_count']}/{f['total']}({f['buy_pct']}%) 只股票建议 BUY？"
                f"是否过度乐观？")
    return (f"为什么 030 健康度 {f['health_score']}/10(积极区)，"
            f"却只有 {f['buy_count']}/{f['total']}({f['buy_pct']}%) 只股票建议 BUY？"
            f"是否过度悲观/错失机会？")


# ── 规则 5: 量价背离 —— 放量上涨但 EMA 空头，给 BUY 的可疑性 ──
def check_volume_price_divergence(state):
    findings = []
    signals = {s["symbol"]: s for s in state.get("signals", []) if "error" not in s}
    try:
        advice_list = json.loads(state.get("final_advice", "[]"))
    except Exception:
        advice_list = []
    advice_map = {a["symbol"]: a for a in advice_list}

    for sym, sig in signals.items():
        vol_ratio = sig.get("vol_ratio")
        change_pct = sig.get("change_pct", 0)
        ind = sig.get("indicators", {})
        ema_bull = ind.get("EMA_bull")
        adv = advice_map.get(sym)
        # 放量(vol_ratio>1.5)且上涨(>0)，但 EMA 空头，给 BUY
        if adv and adv.get("action") == "BUY" and vol_ratio and vol_ratio > 1.5 and change_pct > 0 and ema_bull is False:
            findings.append({
                "type": "volume_price_divergence",
                "symbol": sym,
                "name": sig.get("name"),
                "vol_ratio": vol_ratio,
                "change_pct": change_pct,
                "EMA_bull": ema_bull,
                "reason": adv.get("reason"),
            })
    return findings
def describe_volume_price(f):
    return (f"为什么 {f['name']}({f['symbol']}) 放量上涨(量比{f['vol_ratio']:.2f},涨幅{f['change_pct']:.2f}%)，"
            f"但 EMA 空头，仍建议 BUY？理由：{f['reason']} — 这是典型量价背离，需警惕诱多。")


# ── 规则 6: 新股/新增标的无历史验证直接给强烈信号 ──
def check_new_stock_strong_signal(state):
    """检查新加入关注池的股票(奔图 002180、中船汉光 300847)是否直接给强烈买入/卖出"""
    findings = []
    NEW_STOCKS = {"002180", "300847"}  # 8/6 新增
    signals = {s["symbol"]: s for s in state.get("signals", []) if "error" not in s}
    for sym in NEW_STOCKS:
        sig = signals.get(sym)
        if sig:
            level = sig.get("signal", {}).get("level", "")
            if "强烈买入" in level or "强烈卖出" in level:
                findings.append({
                    "type": "new_stock_strong_signal",
                    "symbol": sym,
                    "name": sig.get("name"),
                    "level": level,
                    "data_source": sig.get("data_source"),
                })
    return findings
def describe_new_stock(f):
    return (f"为什么新加入关注池的 {f['name']}({f['symbol']}) 直接给出【{f['level']}】？"
            f"数据源({f['data_source']})是否足够支撑强烈信号？历史验证缺失是否冒险？")


# ── 规则 7: 中联重科双策略信号与组合建议的矛盾 ──
def check_zhonglian_vs_advice(state):
    findings = []
    zhonglian = state.get("zhonglian", {})
    if not zhonglian or "error" in zhonglian:
        return findings
    signals_zh = zhonglian.get("signals", {})
    # 策略2(综合最优)给卖出，但 agent 对中联的整体建议是 BUY/HOLD
    strat2 = signals_zh.get("strategy2", {})
    if strat2.get("sell") is True:
        try:
            advice_list = json.loads(state.get("final_advice", "[]"))
        except Exception:
            advice_list = []
        for a in advice_list:
            if a.get("symbol") == "000157" and a.get("action") in ("BUY", "HOLD"):
                findings.append({
                    "type": "zhonglian_strat2_sell_vs_buy",
                    "strategy": strat2.get("name"),
                    "strat2_sell": True,
                    "advice_action": a.get("action"),
                    "advice_reason": a.get("reason"),
                })
    return findings
def describe_zhonglian(f):
    return (f"为什么中联重科(000157) 综合最优策略({f['strategy']})触发卖出，"
            f"但最终建议却是 {f['advice_action']}？理由：{f['advice_reason']} — "
            f"双策略体系的卖出信号被忽略了吗？")


# ── 规则 8: 数据源质量 —— 实时行情回退(spot)的股票给出强烈信号 ──
def check_spot_fallback_strong_signal(state):
    findings = []
    signals = {s["symbol"]: s for s in state.get("signals", []) if "error" not in s}
    for sym, sig in signals.items():
        if sig.get("data_source") == "spot":
            level = sig.get("signal", {}).get("level", "")
            if "强烈买入" in level or "强烈卖出" in level:
                findings.append({
                    "type": "spot_fallback_strong_signal",
                    "symbol": sym,
                    "name": sig.get("name"),
                    "level": level,
                    "reason": "仅用实时行情回退计算，无完整历史指标支撑",
                })
    return findings
def describe_spot_fallback(f):
    return (f"为什么 {f['name']}({f['symbol']}) 仅用实时行情(spot回退)计算，"
            f"就给出【{f['level']}】？缺少完整历史指标(MACD/EMA/RSI)，强烈信号可靠吗？")


# ── 规则 9: 重复模板化理由 —— 同一理由批量出现，缺乏个股差异化 ──
def check_template_reasons(state):
    findings = []
    try:
        advice_list = json.loads(state.get("final_advice", "[]"))
    except Exception:
        advice_list = []
    reason_counts = {}
    for a in advice_list:
        r = a.get("reason", "")
        if r:
            reason_counts[r] = reason_counts.get(r, 0) + 1
    for reason, cnt in reason_counts.items():
        if cnt >= 5:  # 同一理由出现≥5次
            findings.append({
                "type": "template_reason",
                "reason": reason,
                "count": cnt,
                "symbols": [a["symbol"] for a in advice_list if a.get("reason") == reason],
            })
    return findings
def describe_template(f):
    syms = ", ".join(f["symbols"][:8]) + ("..." if len(f["symbols"]) > 8 else "")
    return (f"为什么 {f['count']} 只股票({syms}) 共用同一理由：「{f['reason']}」？"
            f"缺乏个股差异化分析，是否模板化套用？")


# ── 规则 10: 持仓股(模拟盘)与建议矛盾 ──
def check_portfolio_vs_advice(state):
    findings = []
    # 读取模拟盘持仓状态
    portfolio_path = os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")
    if not os.path.exists(portfolio_path):
        return findings
    try:
        with open(portfolio_path) as f:
            pdata = json.load(f)
    except Exception:
        return findings
    positions = pdata.get("positions", {})
    try:
        advice_list = json.loads(state.get("final_advice", "[]"))
    except Exception:
        advice_list = []
    advice_map = {a["symbol"]: a for a in advice_list}

    for sym, pos in positions.items():
        if pos.get("position"):  # 有持仓
            adv = advice_map.get(sym)
            if adv and adv.get("action") == "SELL":
                findings.append({
                    "type": "portfolio_hold_but_sell",
                    "symbol": sym,
                    "shares": pos.get("shares"),
                    "cost": pos.get("cost"),
                    "current_price": pos.get("current_price"),
                    "pct": pos.get("return_pct"),
                    "advice_reason": adv.get("reason"),
                })
    return findings
def describe_portfolio(f):
    return (f"为什么模拟盘持仓 {f['symbol']}({f['shares']}股,成本¥{f['cost']},盈亏{f['pct']:+.2f}%) "
            f"却给出 SELL 建议？理由：{f['advice_reason']} — 是否应先核对止损位？")


# ── 规则 11: 止损位是否合理 —— BUY 建议是否给出了止损价/止损幅度，且幅度是否在合理区间 ──
def check_stop_loss_reasonableness(state):
    findings = []
    try:
        advice_list = json.loads(state.get("final_advice", "[]"))
    except Exception:
        advice_list = []
    
    # 从 signals 构建 name 映射
    signals = {s["symbol"]: s for s in state.get("signals", []) if "error" not in s}
    
    for a in advice_list:
        if a.get("action") != "BUY":
            continue
        reason = a.get("reason", "")
        # 检查理由中是否提及止损/止损位/止损价/回撤/支撑位
        stop_loss_keywords = ["止损", "止损位", "止损价", "回撤", "支撑位", "支撑", "最大回撤"]
        has_stop_loss = any(kw in reason for kw in stop_loss_keywords)
        if not has_stop_loss:
            sym = a.get("symbol")
            findings.append({
                "type": "missing_stop_loss",
                "symbol": sym,
                "name": signals.get(sym, {}).get("name", ""),
                "reason": reason,
                "msg": "BUY 建议未提及止损位/支撑位/最大回撤，风控缺失",
            })
        else:
            # 简单提取数字看止损幅度是否过大(>15%)或过小(<3%)
            # 这里只做关键词级提示，不深度解析数值
            pass
    return findings
def describe_stop_loss(f):
    return (f"为什么 {f['name']}({f['symbol']}) 给出 BUY 建议，却完全没提止损位/支撑位/最大回撤？"
            f"理由仅：『{f['reason']}』 — 无止损即无交易，这是否符合 030 风控？")


# ── 规则 12: 上次同类判断复盘 —— 对比历史 agent_run，看同类信号/理由下的后续表现 ──
def check_historical_judgment_review(state):
    """读取历史 agent_runs，找出同类信号/理由的股票，检查后续表现是否打脸。"""
    findings = []
    try:
        advice_list = json.loads(state.get("final_advice", "[]"))
    except Exception:
        advice_list = []

    # 收集本次 BUY 股票的信号特征(级别、理由关键词)
    current_buy_signals = {}
    signals = {s["symbol"]: s for s in state.get("signals", []) if "error" not in s}
    for a in advice_list:
        if a.get("action") == "BUY":
            sym = a.get("symbol")
            sig = signals.get(sym, {})
            level = sig.get("signal", {}).get("level", "")
            reason = a.get("reason", "")
            current_buy_signals[sym] = {"level": level, "reason": reason}

    if not current_buy_signals:
        return findings

    # 去重集合：(symbol, hist_date) -> 避免同一天多个 run 产生重复
    seen_keys = set()

    # 读取历史 runs(最近 20 个)
    run_files = sorted(glob.glob(os.path.join(AGENT_RUNS_DIR, "run_*.json")), reverse=True)[:20]

    for sym, cur in current_buy_signals.items():
        cur_level = cur["level"]
        cur_reason = cur["reason"]
        # 简化匹配：同级别 或 理由高度相似(关键词重叠)
        level_key = "强烈买入" if "强烈买入" in cur_level else ("关注" if "关注" in cur_level else "其他")

        for rf in run_files:
            try:
                with open(rf, encoding="utf-8") as f:
                    hist = json.load(f)
            except Exception:
                continue

            # 历史 run 的日期
            hist_date = hist.get("date")
            if not hist_date:
                continue

            # 历史同股票的建议
            try:
                hist_advice = json.loads(hist.get("final_advice", "[]"))
            except Exception:
                continue

            for ha in hist_advice:
                if ha.get("symbol") != sym:
                    continue
                if ha.get("action") == "BUY":
                    # 找到历史同股票 BUY，看后续几天表现(这里只能做标记，无法自动获取后续收益)
                    # 实务上需要对接行情数据，这里只给出复盘提示
                    hist_reason = ha.get("reason", "")
                    # 去重：同一历史日期同股票只记录一次
                    key = (sym, hist_date)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    findings.append({
                        "type": "historical_judgment_review",
                        "symbol": sym,
                        "name": signals.get(sym, {}).get("name", ""),
                        "hist_date": hist_date,
                        "hist_run_id": hist.get("run_id", ""),
                        "hist_reason": hist_reason,
                        "curr_reason": cur_reason,
                        "curr_level": cur_level,
                        "msg": f"历史 {hist_date} 同股票也曾 BUY(理由: {hist_reason[:50]}...)，需复盘后续表现是否打脸",
                    })
                    break  # 只取最近一次历史 BUY
    return findings
def describe_historical(f):
    return (f"为什么 {f['name']}({f['symbol']}) 现在给 BUY(级别:{f['curr_level']}, 理由:{f['curr_reason'][:50]}...)，"
            f"而在 {f['hist_date']} 也曾因类似理由 BUY({f['hist_reason'][:50]}...)？"
            f"历史同类判断后续走势如何？是否重蹈覆辙？需人工复盘。")


# ──────────────────────────────────────────
# 组装所有规则
# ──────────────────────────────────────────

ALL_RULES = [
    ChallengeRule("signal_advice_mismatch", "HIGH", check_signal_vs_advice, describe_signal_vs_advice),
    ChallengeRule("indicator_contradiction", "HIGH", check_indicator_contradiction, describe_indicator_contradiction),
    ChallengeRule("risk_flag_vs_advice", "HIGH", check_risk_flag_vs_advice, describe_risk_flag),
    ChallengeRule("health_vs_overall_buy", "MEDIUM", check_health_vs_overall_buy, describe_health),
    ChallengeRule("volume_price_divergence", "MEDIUM", check_volume_price_divergence, describe_volume_price),
    ChallengeRule("new_stock_strong_signal", "MEDIUM", check_new_stock_strong_signal, describe_new_stock),
    ChallengeRule("zhonglian_vs_advice", "HIGH", check_zhonglian_vs_advice, describe_zhonglian),
    ChallengeRule("spot_fallback_strong_signal", "MEDIUM", check_spot_fallback_strong_signal, describe_spot_fallback),
    ChallengeRule("template_reason", "LOW", check_template_reasons, describe_template),
    ChallengeRule("portfolio_vs_advice", "HIGH", check_portfolio_vs_advice, describe_portfolio),
    ChallengeRule("stop_loss_reasonableness", "HIGH", check_stop_loss_reasonableness, describe_stop_loss),
    ChallengeRule("historical_judgment_review", "MEDIUM", check_historical_judgment_review, describe_historical),
]


# ──────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────

def find_latest_agent_run() -> Optional[str]:
    """找到最新的 agent run json 文件"""
    files = glob.glob(os.path.join(AGENT_RUNS_DIR, "run_*.json"))
    if not files:
        return None
    # 按文件名时间排序(文件名含时间戳 run_YYYYMMDD_HHMMSS_xxx.json)
    files.sort(reverse=True)
    return files[0]


def load_state(path: str) -> Dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_all_challenges(state: Dict) -> List[Dict]:
    all_findings = []
    for rule in ALL_RULES:
        findings = rule.apply(state)
        all_findings.extend(findings)
    # 按严重度排序
    sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    all_findings.sort(key=lambda f: sev_order.get(f.get("severity", "LOW"), 3))
    return all_findings


def format_report(findings: List[Dict], run_id: str, date: str) -> str:
    lines = []
    lines.append(f"🧠 **挑战质疑审查报告** ({date})")
    lines.append(f"Run ID: {run_id}")
    lines.append(f"共 {len(findings)} 项质疑 | 🔴高危{sum(1 for f in findings if f.get('severity')=='HIGH')} 🟡中危{sum(1 for f in findings if f.get('severity')=='MEDIUM')} 🟢低危{sum(1 for f in findings if f.get('severity')=='LOW')}")
    lines.append("")
    if not findings:
        lines.append("✅ 未发现需质疑的明显矛盾，报告内部逻辑自洽。")
    else:
        for i, f in enumerate(findings, 1):
            tag = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(f.get("severity"), "⚪")
            q = f.get("question", f"质疑: {f.get('type')}")
            lines.append(f"{i}. {tag} [{f.get('rule', f.get('type'))}] {q}")
    lines.append("")
    lines.append("⚠️ 说明: 本报告由规则质疑集自动生成，旨在捕获单一智能体自说自话易漏的逻辑盲区，**不构成投资建议**，仅供人工复核参考。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-file", help="指定 agent run json 文件路径，默认取最新")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()

    # 找最新 run
    run_path = args.run_file or find_latest_agent_run()
    if not run_path:
        print("❌ 未找到任何 agent run 文件")
        sys.exit(1)

    state = load_state(run_path)
    run_id = state.get("run_id", os.path.basename(run_path))

    # 跑所有质疑规则
    findings = run_all_challenges(state)

    # 可读报告
    report = format_report(findings, run_id, args.date)
    print(report)

    # JSON 输出供 cron 解析
    output = {
        "date": args.date,
        "run_id": run_id,
        "run_file": run_path,
        "total": len(findings),
        "high": sum(1 for f in findings if f.get("severity") == "HIGH"),
        "medium": sum(1 for f in findings if f.get("severity") == "MEDIUM"),
        "low": sum(1 for f in findings if f.get("severity") == "LOW"),
        "findings": findings,
    }
    print("\n=====CHALLENGE_REVIEW_RESULT=====")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print("=====CHALLENGE_REVIEW_END=====")

    # 落盘
    os.makedirs(DAILY_DIR, exist_ok=True)
    out_path = os.path.join(DAILY_DIR, f"{args.date}_challenge_review.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# 挑战质疑审查报告 {args.date}\n\n")
        f.write(report)
    print(f"\n📄 质疑报告已保存: {out_path}")


if __name__ == "__main__":
    import argparse
    main()