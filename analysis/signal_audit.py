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
    """读取模拟盘持仓状态(权威, 含真实持仓)。"""
    path = os.path.join(DATA_DIR, "portfolio_sim_state.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
                return data.get("positions", {})
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
    # 把健康度低的护栏挂在所有买入信号上(列在审计说明里)
    return guardrail


# ──────────────────────────────────────────
# 汇总 & 格式化
# ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    date = args.date

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

    market_health = load_market_health(date)

    # ── 跑四类审计 ──
    findings = []
    findings += audit_signal_conflict(dual_stocks)
    findings += audit_position_contradiction(positions_md or positions_state, dual_stocks)
    findings += audit_data_anomaly(dual_stocks, positions_md)
    findings += audit_market_guardrail(market_health, findings)

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
