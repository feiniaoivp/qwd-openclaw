#!/usr/bin/env python3
"""
🔍 信号交叉校验器 (A方案 · Harness "校验者"角色)
==================================================
针对每日收盘后已有的多源输出做**独立复核**，扮演"审计者/Evaluator"，
抓出纯总结性日报(单一智能体自说自话)容易漏掉的四类问题：

  [1] 信号冲突    — 同一股票多策略给出相反方向(买vs卖)，不能默默忽略
  [2] 持仓矛盾    — 模拟盘有持仓，但当日信号方向与之相左/缺失，须提示
  [3] 数据异常    — 现价0、覆盖数<清单数、清单漂移(新旧清单不一致)
  [4] 市场护栏    — 市场健康度低分时，买入信号应降级/标注风险

数据来源(全部读本地盘后已生成文件，不重新拉网，快且可靠):
  - analysis/daily/YYYY-MM-DD_dual.md           (双策略扫描)
  - analysis/daily/YYYY-MM-DD_portfolio_sim.md  (模拟盘)
  - data/market_health_YYYY-MM-DD.json          (030健康度)
  - data/portfolio_sim_state.json               (持仓状态, 权威)

用法:
  python3 analysis/signal_audit.py [--date 2026-08-06]
输出:
  - stdout 可读审计报告(🔍段)
  - 落盘 analysis/daily/YYYY-MM-DD_signal_audit.md
  - stdout JSON 供 agent 解析推送
"""

import os, sys, json, re, argparse
from datetime import datetime

WORKSPACE = "/Users/duguke/.openclaw/workspace"
DAILY_DIR = os.path.join(WORKSPACE, "analysis", "daily")
DATA_DIR = os.path.join(WORKSPACE, "data")

# 复用 service 的 is_trading_day
sys.path.insert(0, WORKSPACE)
from analysis.service import is_trading_day

# ═══════════════════════════════════════════
# 030 专用常量
# ═══════════════════════════════════════════

# 030 仓位矩阵（用于合规性检查）
POSITION_MATRIX_LIMITS = {
    "上升趋势": {"冰点": 0.50, "修复": 0.75, "高潮": 0.50},
    "震荡筑底": {"冰点": 0.30, "修复": 0.50, "高潮": 0.30},
    "下跌趋势": {"冰点": 0.15, "修复": 0.25, "高潮": 0.05},
    "致命风险": {"冰点": 0.00, "修复": 0.00, "高潮": 0.00},
}

SINGLE_STOCK_LIMITS = {
    "上升趋势": {"冰点": 0.08, "修复": 0.12, "高潮": 0.08},
    "震荡筑底": {"冰点": 0.05, "修复": 0.08, "高潮": 0.05},
    "下跌趋势": {"冰点": 0.03, "修复": 0.05, "高潮": 0.02},
    "致命风险": {"冰点": 0.00, "修复": 0.00, "高潮": 0.00},
}

DIRECTION_MAX_RATIO = 0.60  # 单日单方向不超过总仓位上限的60%

# 哑铃策略限制
CORE_BUCKET_LIMIT = 0.80   # 核心仓总上限 80%
SAT_BUCKET_LIMIT = 0.20    # 卫星仓总上限 20%
CORE_SINGLE_LIMIT = 0.15   # 核心仓单票 15%
SAT_SINGLE_LIMIT = 0.05    # 卫星仓单票 5%

# ── 权威股票清单(31只; 2026-08-06后版本) ──
CANONICAL_STOCKS = {
    "002318": "久立特材", "300014": "亿纬锂能", "601066": "中信建投",
    "600030": "中信证券", "300124": "汇川技术", "601995": "中金公司",
    "600584": "长电科技", "002156": "通富微电", "002466": "天齐锂业",
    "600036": "招商银行", "600570": "恒生电子", "605566": "福莱蒽特",
    "000987": "越秀资本", "603308": "应流股份", "300285": "国瓷材料",
    "002413": "雷科防务", "688981": "中芯国际", "601865": "福莱特",
    "000157": "中联重科", "300719": "安达维尔", "601061": "中信金属",
    "600660": "福耀玻璃", "002335": "科华数据",
    "601100": "恒立液压", "600160": "巨化股份", "600346": "恒力石化",
    "000708": "中信特钢", "300748": "金力永磁", "002180": "奔图科技",
    "300847": "中船汉光",
}

# close_scan_v2.py 当前内置清单(旧的, 用于检测"清单漂移")
LEGACY_SCAN_STOCKS = {
    "600030", "601066", "600036", "601995", "000987",
    "600584", "688981", "002156", "002413",
    "300014", "002466", "601865",
    "300285", "603308", "300124", "601100", "002318", "300719", "002335",
    "600660", "600570", "605566", "000157", "601061",
    "600160", "300827", "600346", "000708", "300748",  # 上能电气 300827 是旧的(已废弃,保留供引用)
}


def load_scan_watchlist():
    """动态读取 close_scan_v2.py 当前 WATCHLIST, 用于检测清单漂移(不硬编码)。"""
    import ast as _ast
    path = os.path.join(WORKSPACE, "analysis", "close_scan_v2.py")
    try:
        with open(path) as f:
            src = f.read()
        tree = _ast.parse(src)
        for node in tree.body:
            if isinstance(node, _ast.Assign):
                for t in node.targets:
                    if isinstance(t, _ast.Name) and t.id == "WATCHLIST":
                        codes = set()
                        for elt in node.value.elts:
                            if (isinstance(elt, _ast.Tuple) and len(elt.elts) == 2
                                    and isinstance(elt.elts[0], _ast.Constant)):
                                codes.add(str(elt.elts[0].value))
                        return codes
    except Exception:
        pass
    return None


# ──────────────────────────────────────────
# 读取今日各源输出
# ──────────────────────────────────────────

def parse_dual_md(text: str) -> dict:
    """解析双策略日报 md, 提取每只股票的现价与信号方向。"""
    # 结构: "### 股票名(代码) 现价¥X (Y%) [一致/分歧]" 然后 "- **策略** (score): 动作 — 原因"
    stocks = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"### (.+?)\((\d{6})\)\s+现价¥?([\d.]+)\s+\(([+-]?[\d.]+)%\)\s*\[(.+?)\]", line)
        if m:
            cur = m.group(1)
            stocks[cur] = {
                "symbol": m.group(2),
                "price": float(m.group(3)),
                "change_pct": float(m.group(4)),
                "consensus": m.group(5),
                "signals": [],
            }
            continue
        m2 = re.match(r"- \*\*(.+?)\*\*.*?: (🟢买入|🔴卖出|持有|错误|无)", line)
        if m2 and cur:
            stocks[cur]["signals"].append({"strat": m2.group(1), "action": m2.group(2)})
    return stocks


def parse_portfolio_sim_md(text: str) -> dict:
    """解析模拟盘日报 md, 提取持仓与当日信号。"""
    # 持仓行: "| 股票名(代码) | 策略 | 现价 | 成本 | 持仓股 | 收益率 |"
    positions = {}
    cur_block = None
    for line in text.splitlines():
        if "持仓明细" in line:
            cur_block = "positions"
            continue
        if "今日信号" in line:
            cur_block = "signals"
            continue
        if cur_block == "positions":
            m = re.match(r"\|\s*(.+?)\((\d{6})\)\s*\|\s*(.+?)\s*\|\s*¥?([\d.]+)\s*\|\s*¥?([\d.]+)\s*\|\s*(\d+)\s*\|\s*([+-]?[\d.]+)%", line)
            if m:
                positions[m.group(1)] = {
                    "symbol": m.group(2), "strategy": m.group(3),
                    "price": float(m.group(4)), "cost": float(m.group(5)),
                    "shares": int(m.group(6)), "pct": float(m.group(7)),
                }
    return positions


def load_market_health(date: str) -> dict:
    """读取当日市场健康度 json(若存在)。"""
    path = os.path.join(DATA_DIR, f"market_health_{date}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def load_portfolio_state() -> dict:
    """读取模拟盘持仓状态(权威, 含真实持仓), 归一化为 {股票名: {...}}。

    状态文件格式: {股票代码: {name, strategy, position(bool), entry_price,
    entry_date, shares, total_pl, trade_count, _symbol}}。
    这里归一化为与 parse_portfolio_sim_md 一致的 shape(含 symbol/price/cost/pct),
    供四类审计函数复用。
    """
    path = os.path.join(DATA_DIR, "portfolio_sim_state.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            normalized = {}
            for code, p in (data.get("positions") or {}).items():
                if not isinstance(p, dict):
                    continue
                name = p.get("name") or code
                symbol = p.get("_symbol") or code
                shares = int(p.get("shares") or 0)
                price = 0.0
                cost = float(p.get("entry_price") or 0.0)
                pct = 0.0
                if cost and shares:
                    cost_per = cost
                    pct = (price - cost_per) / cost_per * 100 if price else 0.0
                normalized[name] = {
                    "symbol": symbol,
                    "strategy": p.get("strategy", ""),
                    "position": bool(p.get("position")),
                    "price": price,
                    "cost": cost,
                    "entries": shares,
                    "shares": shares,
                    "pct": pct,
                }
            return normalized
        except Exception:
            pass
    return {}


# ──────────────────────────────────────────
# 四类审计
# ──────────────────────────────────────────

def audit_signal_conflict(dual_stocks) -> list:
    """[1] 多策略方向冲突: 同股一买一卖(或一买一持强烈对立)。"""
    findings = []
    for name, info in dual_stocks.items():
        sigs = info["signals"]
        buys = [s for s in sigs if "买入" in s["action"]]
        sells = [s for s in sigs if "卖出" in s["action"]]
        if buys and sells:
            buy_strats = "/".join(s["strat"] for s in buys)
            sell_strats = "/".join(s["strat"] for s in sells)
            findings.append({
                "type": "signal_conflict",
                "severity": "HIGH",
                "symbol": info["symbol"], "name": name,
                "msg": f"{name}({info['symbol']}) 策略冲突: {buy_strats}【买入】 vs {sell_strats}【卖出】",
            })
    return findings


def audit_position_contradiction(positions, dual_stocks) -> list:
    """[2] 持仓矛盾: 模拟盘持有, 但当日双策略给反向信号, 或信号缺失。"""
    findings = []
    for name, pos in positions.items():
        # 找该股票的当日双策略信号
        info = dual_stocks.get(name)
        if info is None:
            findings.append({
                "type": "position_no_signal",
                "severity": "MEDIUM",
                "symbol": pos["symbol"], "name": name,
                "msg": f"{name}({pos['symbol']}) 有持仓({pos['shares']}股)但今日双策略扫描缺失该股信号",
            })
            continue
        buy = any("买入" in s["action"] for s in info["signals"])
        sell = any("卖出" in s["action"] for s in info["signals"])
        if sell:
            findings.append({
                "type": "position_contradiction",
                "severity": "HIGH",
                "symbol": pos["symbol"], "name": name,
                "msg": f"{name}({pos['symbol']}) 持仓中但双策略触发【卖出】信号, 持仓成本¥{pos.get('cost')}现价¥{pos.get('price')}({pos.get('pct',0):+.2f}%)",
            })
        elif not buy and not sell:
            # 持有但无信号 — 中性, 不报警, 仅在持仓收益异常时提示
            pass
    return findings


def audit_data_anomaly(dual_stocks, positions) -> list:
    """[3] 数据异常: 现价0(获取失败)、覆盖数<31、清单漂移。"""
    insights = []
    findings = []

    # 3a. 现价0 = 数据获取失败 (从双策略日报直接判, 覆盖全部31只)
    zero_names = [f"{name}({info['symbol']})" for name, info in dual_stocks.items()
                  if info.get("price", 0) <= 0]
    if zero_names:
        findings.append({
            "type": "data_zero_price", "severity": "HIGH",
            "symbol": "-", "name": "-",
            "msg": f"{len(zero_names)} 只股票现价=0(数据获取失败): {', '.join(zero_names)}",
        })

    # 3b. 覆盖数 < 权威清单 (仅当确实有扫描覆盖时才判, 避免与"双策略未上线"混淆)
    covered = {i["symbol"] for i in dual_stocks.values()}
    if covered and len(covered) < len(CANONICAL_STOCKS):
        missing = [f"{n}({s})" for s, n in CANONICAL_STOCKS.items() if s not in covered]
        findings.append({
            "type": "coverage_gap", "severity": "MEDIUM",
            "symbol": "-", "name": "-",
            "msg": f"双策略扫描仅覆盖 {len(covered)}/{len(CANONICAL_STOCKS)} 只, 缺失: {', '.join(missing)}",
        })

    # 3c. 清单漂移: 动态对比 close_scan_v2.py 实际 WATCHLIST 与权威清单
    scan_codes = load_scan_watchlist()
    if scan_codes is not None:
        drift = []
        scan_extra = scan_codes - set(CANONICAL_STOCKS.keys())
        if scan_extra:
            for s in sorted(scan_extra):
                drift.append(f"{s}({CANONICAL_STOCKS.get(s, '未知')}) 在扫描中但权威清单已移除")
        line_scan = set(CANONICAL_STOCKS.keys()) - scan_codes
        if line_scan:
            for s in sorted(line_scan):
                drift.append(f"{CANONICAL_STOCKS[s]}({s}) 权威清单有但未进扫描")
        if drift:
            findings.append({
                "type": "list_drift", "severity": "LOW",
                "symbol": "-", "name": "-",
                "msg": "清单漂移: " + "; ".join(drift) + " (close_scan_v2.py 需同步更新)",
            })

    return findings


def audit_market_guardrail(market_health, findings) -> list:
    """[4] 市场护栏: 健康度低分时, 对买入信号降级提示。"""
    guardrail = []
    score = market_health.get("summary", {}).get("total_score")
    verdict = market_health.get("summary", {}).get("verdict", "")
    if score is not None:
        if score < 4:
            guardrail.append({
                "type": "market_guardrail", "severity": "HIGH",
                "symbol": "-", "name": "-",
                "msg": f"市场健康度 {score}/10 → {verdict}。任何【买入】信号都应降仓/观望, 禁止追高",
            })
        elif score < 6:
            guardrail.append({
                "type": "market_guardrail", "severity": "MEDIUM",
                "symbol": "-", "name": "-",
                "msg": f"市场健康度 {score}/10 → {verdict}。买入信号建议降仓至50%",
            })
    return guardrail


def audit_distribution_patterns(dual_stocks) -> list:
    """[5] 出货形态质疑: 双策略给买入/持有, 但近期K线出现出货形态(强度>=4)。"""
    findings = []
    for name, info in dual_stocks.items():
        patterns = info.get("distribution_patterns", [])
        if not patterns:
            continue
        # 统计买入/持有信号
        buy_signals = [s for s in info["signals"] if "买入" in s["action"]]
        hold_signals = [s for s in info["signals"] if s["action"] == "持有"]
        if not buy_signals and not hold_signals:
            continue  # 只有卖出信号, 不需质疑
        
        # 强出货形态(强度>=4)
        strong_patterns = [p for p in patterns if p["strength"] >= 4]
        if not strong_patterns:
            continue
        
        pattern_names = ", ".join([p["pattern"] for p in strong_patterns])
        max_strength = max(p["strength"] for p in strong_patterns)
        
        if buy_signals:
            # 有买入信号却出现出货形态 → HIGH
            findings.append({
                "type": "distribution_pattern_conflict",
                "severity": "HIGH",
                "symbol": info["symbol"], "name": name,
                "msg": f"{name}({info['symbol']}) 双策略【买入】但检测到出货形态: {pattern_names} (强度{max_strength})。建议降级为观望/减仓",
            })
        elif hold_signals:
            # 只有持有信号 → MEDIUM 提示
            findings.append({
                "type": "distribution_pattern_warn",
                "severity": "MEDIUM",
                "symbol": info["symbol"], "name": name,
                "msg": f"{name}({info['symbol']}) 双策略【持有】但检测到出货形态: {pattern_names} (强度{max_strength})。建议警惕, 设置止损",
            })
    return findings


# ═══════════════════════════════════════════
# 030 专用审计项（新增）
# ═══════════════════════════════════════════

def load_fatal_risk(date: str) -> dict:
    """加载致命风险检测结果"""
    path = os.path.join(DATA_DIR, f"fatal_risk_{date}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {"fatal_triggered": False, "high_risk_triggered": False}


def load_position_plan(date: str) -> dict:
    """加载仓位计划"""
    path = os.path.join(DATA_DIR, f"position_plan_{date}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def load_arbitration_results(date: str) -> list:
    """加载裁决结果"""
    path = os.path.join(DATA_DIR, f"arbitration_{date}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def load_next_day_sim(date: str) -> dict:
    """加载隔日推演"""
    path = os.path.join(DATA_DIR, f"next_day_sim_{date}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def audit_fatal_risk_execution(fatal_risk: dict, positions: dict, dual_stocks: dict) -> list:
    """[6] 致命风险执行审计: 致命风险触发时，检查是否实际执行了清仓/禁买"""
    findings = []
    if not fatal_risk.get("fatal_triggered", False) and not fatal_risk.get("high_risk_triggered", False):
        return findings
    
    # 检查持仓是否已清空（致命风险）
    if fatal_risk.get("fatal_triggered", False):
        held_positions = [p for p in positions.values() if p.get("shares", 0) > 0]
        if held_positions:
            names = ", ".join([f"{p.get('symbol','')}" for p in held_positions])
            findings.append({
                "type": "fatal_risk_not_executed",
                "severity": "HIGH",
                "symbol": "-", "name": "-",
                "msg": f"☠️ 致命风险触发但仍有 {len(held_positions)} 只持仓未清空: {names}。必须无条件清仓！",
            })
        
        # 检查是否有买入信号未被压制
        buy_signals = []
        for name, info in dual_stocks.items():
            for s in info.get("signals", []):
                if "买入" in s.get("action", ""):
                    buy_signals.append(f"{name}({info['symbol']}) {s.get('strat','')}")
        if buy_signals:
            findings.append({
                "type": "fatal_risk_buy_not_suppressed",
                "severity": "HIGH",
                "symbol": "-", "name": "-",
                "msg": f"☠️ 致命风险触发但仍有买入信号未压制: {', '.join(buy_signals[:5])}",
            })
    
    # 高风险防御：检查持仓是否减仓/止损
    if fatal_risk.get("high_risk_triggered", False):
        for name, pos in positions.items():
            if pos.get("shares", 0) > 0:
                info = dual_stocks.get(name)
                has_sell = False
                if info:
                    for s in info.get("signals", []):
                        if "卖出" in s.get("action", ""):
                            has_sell = True
                            break
                if not has_sell:
                    findings.append({
                        "type": "high_risk_no_reduce",
                        "severity": "MEDIUM",
                        "symbol": pos.get("symbol", ""), "name": name,
                        "msg": f"🟡 高风险防御触发但 {name}({pos.get('symbol','')}) 持仓未出现减仓/止损信号",
                    })
    return findings


def audit_position_compliance(position_plan: dict, positions: dict, market_context: dict) -> list:
    """[7] 仓位合规审计: 检查实际仓位是否超出 030 矩阵限制"""
    findings = []
    if not position_plan:
        return findings
    
    total_capital = position_plan.get("total_capital", 3_000_000)
    total_limit_pct = position_plan.get("total_limit_pct", 0)
    total_limit_amt = position_plan.get("total_limit_amount", 0)
    single_max_pct = position_plan.get("single_stock_max_pct", 0)
    single_max_amt = position_plan.get("single_stock_max_amount", 0)
    direction_alloc = position_plan.get("direction_allocation", {})
    
    held_positions = [p for p in positions.values() if p.get("shares", 0) > 0]
    total_held_value = 0
    direction_values = {}
    
    for pos in held_positions:
        shares = pos.get("shares", 0)
        cost = pos.get("cost", 0)
        if shares > 0 and cost > 0:
            mv = shares * cost
            total_held_value += mv
            symbol = pos.get("symbol", "")
            direction = _get_direction(symbol)
            direction_values[direction] = direction_values.get(direction, 0) + mv
    
    if total_limit_amt > 0 and total_held_value > total_limit_amt * 1.05:
        findings.append({
            "type": "position_limit_exceeded",
            "severity": "HIGH",
            "symbol": "-", "name": "-",
            "msg": f"📐 总仓位超限: 实际¥{total_held_value:,.0f} > 上限¥{total_limit_amt:,.0f} ({total_held_value/total_capital:.1%} > {total_limit_pct:.1%})",
        })
    
    for pos in held_positions:
        shares = pos.get("shares", 0)
        cost = pos.get("cost", 0)
        if shares > 0 and cost > 0:
            mv = shares * cost
            if mv > single_max_amt * 1.05:
                findings.append({
                    "type": "single_position_limit_exceeded",
                    "severity": "MEDIUM",
                    "symbol": pos.get("symbol", ""), "name": pos.get("name", ""),
                    "msg": f"📈 单股超限: {pos.get('name')}({pos.get('symbol')}) ¥{mv:,.0f} > 上限¥{single_max_amt:,.0f} ({mv/total_capital:.1%} > {single_max_pct:.1%})",
                })
    
    for direction, value in direction_values.items():
        limit = direction_alloc.get(direction, 0)
        if limit > 0 and value > limit * 1.1:
            findings.append({
                "type": "direction_limit_exceeded",
                "severity": "MEDIUM",
                "symbol": "-", "name": direction,
                "msg": f"🎯 方向仓位超限: {direction} ¥{value:,.0f} > 额度¥{limit:,.0f}",
            })
    
    core_value = 0
    sat_value = 0
    for pos in held_positions:
        shares = pos.get("shares", 0)
        cost = pos.get("cost", 0)
        if shares > 0 and cost > 0:
            mv = shares * cost
            symbol = pos.get("symbol", "")
            try:
                from analysis.moat_factor import is_core_moat_stock
                if is_core_moat_stock(symbol):
                    core_value += mv
                else:
                    sat_value += mv
            except:
                sat_value += mv
    
    if total_capital > 0:
        core_pct = core_value / total_capital
        sat_pct = sat_value / total_capital
        if core_pct > CORE_BUCKET_LIMIT + 0.05:
            findings.append({
                "type": "core_bucket_exceeded",
                "severity": "HIGH",
                "symbol": "-", "name": "核心仓",
                "msg": f"🏰 核心仓超限: {core_pct:.1%} > {CORE_BUCKET_LIMIT:.0%} (¥{core_value:,.0f})",
            })
        if sat_pct > SAT_BUCKET_LIMIT + 0.05:
            findings.append({
                "type": "sat_bucket_exceeded",
                "severity": "MEDIUM",
                "symbol": "-", "name": "卫星仓",
                "msg": f"🛰️ 卫星仓超限: {sat_pct:.1%} > {SAT_BUCKET_LIMIT:.0%} (¥{sat_value:,.0f})",
            })
    
    return findings


def _get_direction(symbol: str) -> str:
    """获取股票方向（简化版）"""
    DIRECTION_MAP = {
        "600584": "半导体封测", "002156": "半导体封测", "688981": "半导体晶圆",
        "002413": "军工电子", "300124": "工业自动化", "601100": "高端液压",
        "300014": "锂电池", "002466": "锂资源", "601865": "光伏玻璃",
        "300285": "特种陶瓷", "603308": "特材管材", "002318": "特钢管",
        "600160": "氟化工", "600346": "炼化一体化", "000708": "特钢",
        "300748": "稀土永磁", "002335": "数据中心", "300719": "连接器",
        "600030": "券商", "601066": "券商", "600036": "银行", "601995": "券商",
        "000987": "基建金融", "600570": "金融IT", "605566": "消费电子",
        "600660": "汽车玻璃", "000157": "工程机械", "601061": "有色贸易",
        "002180": "打印机国产替代", "300847": "军工光电",
    }
    return DIRECTION_MAP.get(symbol, "其他")


def audit_arbitration_consistency(arbitration_results: list, dual_stocks: dict) -> list:
    """[8] 裁决一致性审计: 检查裁决引擎输出是否与原始信号矛盾"""
    findings = []
    if not arbitration_results:
        return findings
    
    arb_by_symbol = {r["symbol"]: r for r in arbitration_results}
    
    for name, info in dual_stocks.items():
        symbol = info["symbol"]
        arb = arb_by_symbol.get(symbol)
        if not arb:
            continue
        
        final_action = arb.get("final_action", "")
        arb_path = arb.get("arbitration_path", [])
        
        raw_signals = info.get("signals", [])
        raw_buys = [s for s in raw_signals if "买入" in s.get("action", "")]
        raw_sells = [s for s in raw_signals if "卖出" in s.get("action", "")]
        
        if len(raw_buys) >= 2 and final_action in ["卖出", "减仓", "观望", "强制清仓"]:
            valid_reasons = ["FATAL_RISK", "HIGH_RISK", "NEGATIVE_FEEDBACK", "DISTRIBUTION_PATTERN"]
            has_valid = any(r in arb_path for r in valid_reasons)
            if not has_valid:
                findings.append({
                    "type": "arbitration_unexplained_downgrade",
                    "severity": "MEDIUM",
                    "symbol": symbol, "name": name,
                    "msg": f"⚖️ 裁决无故降级: 原始双买入但裁决为{final_action}，路径={arb_path}",
                })
        
        if len(raw_sells) >= 2 and final_action in ["买入", "积极买入", "小仓买入"]:
            findings.append({
                "type": "arbitration_unexplained_upgrade",
                "severity": "HIGH",
                "symbol": symbol, "name": name,
                "msg": f"⚖️ 裁决无故升级: 原始双卖出但裁决为{final_action}，路径={arb_path}",
            })
    
    return findings


def audit_next_day_sim_consistency(next_day_sim: dict, arbitration_results: list) -> list:
    """[9] 隔日推演一致性: 检查推演操作建议与裁决结果是否一致"""
    findings = []
    if not next_day_sim or not arbitration_results:
        return findings
    
    arb_by_symbol = {r["symbol"]: r for r in arbitration_results}
    directions = next_day_sim.get("directions", [])
    
    for df in directions:
        for symbol in df.get("core_stocks", []):
            arb = arb_by_symbol.get(symbol)
            if not arb:
                continue
            
            final_action = arb.get("final_action", "")
            scenarios = df.get("scenarios", {})
            normal_action = scenarios.get("A", {}).get("action", "")
            
            if final_action in ["积极买入", "买入"] and ("观望" in normal_action or "卖出" in normal_action or "减仓" in normal_action):
                findings.append({
                    "type": "sim_arbitration_mismatch",
                    "severity": "MEDIUM",
                    "symbol": symbol, "name": SYMBOL_TO_NAME.get(symbol, symbol),
                    "msg": f"🔮 推演与裁决不一致: 裁决{final_action}但推演正常情景建议{normal_action}",
                })
    
    return findings


def audit_stop_loss_coverage(arbitration_results: list, positions: dict) -> list:
    """[10] 止损覆盖率: 检查所有持仓是否有止损位设置"""
    findings = []
    if not arbitration_results:
        return findings
    
    arb_by_symbol = {r["symbol"]: r for r in arbitration_results}
    held_positions = [p for p in positions.values() if p.get("shares", 0) > 0]
    
    no_stop_loss = []
    for pos in held_positions:
        symbol = pos.get("symbol", "")
        arb = arb_by_symbol.get(symbol)
        if not arb or not arb.get("stop_loss"):
            no_stop_loss.append(f"{pos.get('name','')}({symbol}) - 成本¥{pos.get('cost',0):.2f}")
    
    if no_stop_loss:
        findings.append({
            "type": "stop_loss_missing",
            "severity": "HIGH",
            "symbol": "-", "name": "-",
            "msg": f"🛑 {len(no_stop_loss)} 只持仓缺止损位: {', '.join(no_stop_loss[:5])}",
        })
    
    return findings

# 导入 SYMBOL_TO_NAME（从 next_day_simulator 复用）
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


# ──────────────────────────────────────────
# 汇总 & 格式化
# ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    date = args.date

    # ── 非交易日直接跳过 ──
    if not is_trading_day(date):
        brief = [
            f"🔍 **信号交叉审计** ({date})",
            "指定日期非交易日，跳过校验。",
            "",
            "⚠️ 校验说明: 非交易日无盘后数据源，自动跳过避免误报。"
        ]
        report = "\n".join(brief)
        print(report)
        output = {
            "date": date,
            "skipped": True,
            "reason": "non_trading_day",
            "total_findings": 0,
            "high": 0, "medium": 0, "low": 0,
            "findings": [],
        }
        print("\n=====SIGNAL_AUDIT_RESULT=====")
        print(json.dumps(output, ensure_ascii=False, indent=2))
        print("=====SIGNAL_AUDIT_END=====")
        os.makedirs(DAILY_DIR, exist_ok=True)
        with open(os.path.join(DAILY_DIR, f"{date}_signal_audit.md"), "w") as f:
            f.write(f"# 信号交叉审计 {date}\n\n")
            f.write(report)
        print(f"\n📄 审计报告已保存: {os.path.join(DAILY_DIR, f'{date}_signal_audit.md')}")
        return

    # ── 读取各源 ──
    dual_path = os.path.join(DAILY_DIR, f"{date}_dual.md")
    sim_path = os.path.join(DAILY_DIR, f"{date}_portfolio_sim.md")

    dual_stocks = {}
    if os.path.exists(dual_path):
        with open(dual_path) as f:
            dual_stocks = parse_dual_md(f.read())
    elif os.path.exists(f"/Users/duguke/.openclaw/workspace/analysis/daily/{date}_adaptive.md"):
        ad_path = f"/Users/duguke/.openclaw/workspace/analysis/daily/{date}_adaptive.md"
        with open(ad_path) as f:
            dual_stocks = parse_dual_md(f.read())

    positions_md = {}
    if os.path.exists(sim_path):
        with open(sim_path) as f:
            positions_md = parse_portfolio_sim_md(f.read())
    positions_state = load_portfolio_state()
    # 状态文件含全量31只观察池, 仅把实际持仓(非空仓)交给审计, 避免空仓误报
    held_state = {n: p for n, p in positions_state.items() if p.get("position") or p.get("shares", 0) > 0}

    market_health = load_market_health(date)
    
    # 加载 030 新增数据源
    fatal_risk = load_fatal_risk(date)
    position_plan = load_position_plan(date)
    arbitration_results = load_arbitration_results(date)
    next_day_sim = load_next_day_sim(date)

    # ── 跑十类审计（原5类 + 030新增5类） ──
    findings = []
    findings += audit_signal_conflict(dual_stocks)
    findings += audit_position_contradiction(positions_md or held_state, dual_stocks)
    findings += audit_data_anomaly(dual_stocks, positions_md)
    findings += audit_market_guardrail(market_health, findings)
    findings += audit_distribution_patterns(dual_stocks)
    
    # 030 新增审计项
    findings += audit_fatal_risk_execution(fatal_risk, positions_md or held_state, dual_stocks)
    findings += audit_position_compliance(position_plan, positions_md or held_state, market_health)
    findings += audit_arbitration_consistency(arbitration_results, dual_stocks)
    findings += audit_next_day_sim_consistency(next_day_sim, arbitration_results)
    findings += audit_stop_loss_coverage(arbitration_results, positions_md or held_state)

    # 排序: HIGH > MEDIUM > LOW
    sev = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    findings.sort(key=lambda f: sev.get(f["severity"], 9))

    output = {
        "date": date,
        "sources": {
            "dual": os.path.exists(dual_path) or os.path.exists(f"/Users/duguke/.openclaw/workspace/analysis/daily/{date}_adaptive.md"),
            "portfolio_sim": os.path.exists(sim_path),
            "market_health": bool(market_health),
        },
        "total_findings": len(findings),
        "high": sum(1 for f in findings if f["severity"] == "HIGH"),
        "medium": sum(1 for f in findings if f["severity"] == "MEDIUM"),
        "low": sum(1 for f in findings if f["severity"] == "LOW"),
        "findings": findings,
        "market_health_score": market_health.get("summary", {}).get("total_score"),
        "positions_held": sum(1 for p in positions_state.values() if p.get("position")),
    }

    # ── 可读报告 ──
    brief = []
    brief.append(f"🔍 **信号交叉审计** ({date})")
    brief.append(f"发现 {output['total_findings']} 项 | 🔴高危{output['high']} 🟡中危{output['medium']} 🟢低危{output['low']} | 持仓 {output['positions_held']} 只 | 市场分 {output['market_health_score'] or '-'}")
    brief.append("")
    if findings:
        for f in findings:
            tag = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(f["severity"], "⚪")
            brief.append(f"{tag} [{f['type']}] {f['msg']}")
    else:
        brief.append("✅ 未发现信号冲突/持仓矛盾/数据异常, 各源信号一致。")
    brief.append("")
    brief.append("⚠️ 校验说明: 本段由独立审计脚本生成(非分析师总结), 用于捕获单一智能体易漏的自相矛盾。")

    report = "\n".join(brief)

    # stdout(可读 + JSON)
    print(report)
    print("\n=====SIGNAL_AUDIT_RESULT=====")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print("=====SIGNAL_AUDIT_END=====")

    # 落盘
    os.makedirs(DAILY_DIR, exist_ok=True)
    with open(os.path.join(DAILY_DIR, f"{date}_signal_audit.md"), "w") as f:
        f.write(f"# 信号交叉审计 {date}\n\n")
        f.write(report)
    print(f"\n📄 审计报告已保存: {os.path.join(DAILY_DIR, f'{date}_signal_audit.md')}")


if __name__ == "__main__":
    main()
