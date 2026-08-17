#!/usr/bin/env python3
"""
集合竞价决策分析器
- 读取 auction_feed.py 产出的 9:25 快照
- 结合昨日情绪/阶段、自适应策略映射、隔夜消息
- 执行竞价分析技能的 4 步决策逻辑
- 产出结构化决策 JSON + 可读 Markdown（供 Telegram 推送）
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

WORKSPACE = Path("/Users/duguke/.openclaw/workspace")
sys.path.insert(0, str(WORKSPACE))

# 导入护城河因子用于哲学过滤
try:
    from analysis.moat_factor import calc_moat_score, is_core_moat_stock
    MOAT_AVAILABLE = True
except ImportError:
    MOAT_AVAILABLE = False

# ==================== 配置常量 ====================
AUCTION_DIR = WORKSPACE / "data" / "auction"
CONTEXT_DIR = WORKSPACE / "data" / "context"
WATCHLIST_FILE = WORKSPACE / "memory" / "watchlist.md"
ADAPTIVE_MAP_FILE = WORKSPACE / "data" / "adaptive_strategy_map.json"

# 致命风险阈值（来自 SKILL.md）
FATAL_RISK = {
    "core_identification_limit_down_seal": 6_000_000_000,    # 核心辨识度票一字跌停封单≥60亿
    "core_capacity_limit_down_seal": 10_000_000_000,         # 核心大票一字跌停封单≥100亿
    "multi_identification_limit_down_count": 2,               # 多个辨识度标的同时一字跌停≥2只
}

HIGH_RISK = {
    "core_capacity_chg_pct": -3.0,       # 核心容量中军竞价≤-3%
    "market_limit_down_count": 5,         # 全市场一字跌停≥5家（需全市场数据，这里用自选池代理）
    "sector_batch_chg_pct": -3.0,         # 核心标的板块批量低开<-3%
}

# 核心标的分类（与 030 体系一致）
CORE_CAPACITY = {"600030", "600584", "688981", "002156", "300014", "600570", "600036", "601066"}  # 容量中军
CORE_IDENTIFICATION = {"300285", "603308", "002466", "300124", "601100", "002318"}  # 辨识度个股
# 板块映射（简化版，用于跷跷板/板块批量判断）
SECTOR_MAP = {
    "券商": {"600030", "601066", "601995", "000987"},
    "半导体": {"600584", "688981", "002156", "002413"},
    "锂电": {"300014", "002466", "601865"},
    "高端制造": {"300285", "603308", "300124", "601100", "002318", "300719", "002335", "300748"},
    "化工": {"600160", "600346"},
    "钢铁": {"000708"},
    "消费": {"600660", "600570", "605566"},
    "工程机械": {"000157", "601061"},
    "办公设备": {"002180", "300847"},
}


def load_latest_auction_snapshot() -> Optional[Dict]:
    """加载最新的竞价快照文件"""
    files = sorted(AUCTION_DIR.glob("*_9_25.json"))
    if not files:
        return None
    latest = files[-1]
    with open(latest, encoding="utf-8") as f:
        return json.load(f)


def load_market_context(date_str: str) -> Dict:
    """加载昨日收盘复盘产出的市场上下文"""
    ctx_file = CONTEXT_DIR / f"market_context_{date_str}.json"
    if ctx_file.exists():
        with open(ctx_file, encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_adaptive_strategy_map() -> Dict:
    """加载自适应策略映射"""
    if ADAPTIVE_MAP_FILE.exists():
        with open(ADAPTIVE_MAP_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_news_sentiment(date_str: str) -> Dict:
    """加载隔夜/盘前新闻情绪（来自 daily_news_reader.py）"""
    news_file = WORKSPACE / "data" / "news" / f"daily_{date_str}.md"
    if news_file.exists():
        # 简单解析：统计关键词
        text = news_file.read_text(encoding="utf-8")
        return {
            "has_buyback": "回购" in text,
            "has_earnings_up": "业绩" in text and ("大增" in text or "预增" in text),
            "has_investigation": "调查" in text or "立案" in text,
            "has_m_a": "收购" in text or "重组" in text,
            "raw_length": len(text),
        }
    return {}


def get_sector(code: str) -> str:
    for sector, codes in SECTOR_MAP.items():
        if code in codes:
            return sector
    return "其他"


def get_weekly_trend(rows: List[Dict]) -> str:
    """
    获取大周期趋势判断（简化版：基于竞价数据推断周线趋势）
    实际应接入周线数据，这里用简化逻辑：
    - UP: 核心容量中军多数竞价>0 且 封单买单占优
    - DOWN: 核心容量中军多数竞价<-2% 或 卖单占优
    - SIDEWAYS: 其他
    """
    capacity_chgs = []
    for r in rows:
        if r["code"] in CORE_CAPACITY:
            capacity_chgs.append(r["chg_pct"])
    
    if not capacity_chgs:
        return "SIDEWAYS"
    
    avg_chg = sum(capacity_chgs) / len(capacity_chgs)
    up_count = sum(1 for c in capacity_chgs if c > 0)
    down_count = sum(1 for c in capacity_chgs if c < 0)
    
    if avg_chg > 1 and up_count > len(capacity_chgs) / 2:
        return "UP"
    elif avg_chg < -2 or down_count > len(capacity_chgs) / 2:
        return "DOWN"
    return "SIDEWAYS"


# ==================== 步骤 1：致命风险扫描 ====================
def scan_fatal_risk(rows: List[Dict]) -> Optional[Dict]:
    """
    返回风险字典（若触发致命风险），否则返回 None
    """
    risks = []

    # 1) 核心辨识度票一字跌停
    id_limit_down = []
    for r in rows:
        if r["code"] in CORE_IDENTIFICATION and r["is_limit_down"]:
            seal_amount = r["ask_vol1"] * r["ask1"]  # 卖一封单金额(元)
            if seal_amount >= FATAL_RISK["core_identification_limit_down_seal"]:
                id_limit_down.append((r["name"], r["code"], seal_amount / 1e8))

    if id_limit_down:
        risks.append({
            "type": "核心辨识度票一字跌停",
            "details": id_limit_down,
            "action": "无条件清仓，停止交易",
        })

    # 2) 核心大票一字跌停
    cap_limit_down = []
    for r in rows:
        if r["code"] in CORE_CAPACITY and r["is_limit_down"]:
            seal_amount = r["ask_vol1"] * r["ask1"]
            if seal_amount >= FATAL_RISK["core_capacity_limit_down_seal"]:
                cap_limit_down.append((r["name"], r["code"], seal_amount / 1e8))

    if cap_limit_down:
        risks.append({
            "type": "核心容量中军一字跌停",
            "details": cap_limit_down,
            "action": "无条件清仓，停止交易",
        })

    # 3) 多个辨识度标的同时一字跌停
    if len(id_limit_down) >= FATAL_RISK["multi_identification_limit_down_count"]:
        risks.append({
            "type": f"多个辨识度标的一字跌停({len(id_limit_down)}只)",
            "details": id_limit_down,
            "action": "无条件清仓，停止交易",
        })

    if risks:
        return {"level": "致命风险", "risks": risks, "decision": "STOP_TRADING"}

    return None


# ==================== 步骤 1.5：高风险防御 ====================
def scan_high_risk(rows: List[Dict]) -> List[Dict]:
    """返回高风险防御列表（不阻止交易，但要求不开新仓/设止损）"""
    alerts = []

    # 核心容量中军竞价≤-3%
    for r in rows:
        if r["code"] in CORE_CAPACITY and r["chg_pct"] <= HIGH_RISK["core_capacity_chg_pct"]:
            alerts.append({
                "type": "核心中军竞价大幅低开",
                "target": f"{r['name']}({r['code']}) {r['chg_pct']:+.2f}%",
                "action": "不开新仓，持仓设-5%止损",
            })

    # 自选池内一字跌停≥5家（代理全市场）
    limit_down_count = sum(1 for r in rows if r["is_limit_down"])
    if limit_down_count >= HIGH_RISK["market_limit_down_count"]:
        alerts.append({
            "type": f"自选池一字跌停{limit_down_count}家",
            "target": f"共{limit_down_count}只",
            "action": "不开新仓",
        })

    # 板块批量低开
    sector_chg: Dict[str, List[float]] = {}
    for r in rows:
        sec = get_sector(r["code"])
        sector_chg.setdefault(sec, []).append(r["chg_pct"])

    for sec, chgs in sector_chg.items():
        if len(chgs) >= 3:
            avg_chg = sum(chgs) / len(chgs)
            if avg_chg <= HIGH_RISK["sector_batch_chg_pct"]:
                alerts.append({
                    "type": f"板块批量低开: {sec}",
                    "target": f"均值{avg_chg:+.2f}% ({len(chgs)}只)",
                    "action": f"{sec}方向不参与",
                })

    return alerts


# ==================== 步骤 2：机会方向识别 ====================
def identify_opportunities(rows: List[Dict], context: Dict, strategy_map: Dict, news: Dict) -> List[Dict]:
    """识别当日机会方向，按优先级排序"""
    # 先获取大周期趋势（周线/月线）用于哲学过滤
    weekly_trend = get_weekly_trend(rows)
    directions = []

    # 统计封单方向集中度
    limit_up_stocks = [r for r in rows if r["is_limit_up"]]
    limit_down_stocks = [r for r in rows if r["is_limit_down"]]

    # 方向 1：一字涨停方向（最强信号）
    if limit_up_stocks:
        # 按板块聚类
        sector_count: Dict[str, List[Dict]] = {}
        for r in limit_up_stocks:
            sec = get_sector(r["code"])
            sector_count.setdefault(sec, []).append(r)

        for sec, stocks in sorted(sector_count.items(), key=lambda x: -len(x[1])):
            if len(stocks) >= 2:  # 同方向多只一字板
                total_seal = sum(s["bid_vol1"] * s["bid1"] for s in stocks) / 1e8
                core_stocks = [s for s in stocks if s["code"] in CORE_CAPACITY | CORE_IDENTIFICATION]
                directions.append({
                    "priority": 1,
                    "direction": f"{sec}方向（一字涨停确认）",
                    "driver": f"封单集中: {len(stocks)}只一字板，总封单{total_seal:.1f}亿",
                    "core_stocks": [f"{s['name']}({s['code']})" for s in core_stocks[:3]],
                    "all_stocks": [f"{s['name']}({s['code']})" for s in stocks],
                    "verify": f"开盘{len(stocks)}只能否维持一字/炸板回封；{sec}指数前30分放量",
                    "abandon": f"开盘炸板且封单快速消失、量能萎缩",
                })

    # 方向 2：竞价异动（大单拉升，非一字板）
    strong_chg = [r for r in rows if r["chg_pct"] > 3 and not r["is_limit_up"]]
    if strong_chg:
        sector_count: Dict[str, List[Dict]] = {}
        for r in strong_chg:
            sec = get_sector(r["code"])
            sector_count.setdefault(sec, []).append(r)

        for sec, stocks in sorted(sector_count.items(), key=lambda x: -len(x[1])):
            if len(stocks) >= 2:
                avg_chg = sum(s["chg_pct"] for s in stocks) / len(stocks)
                total_amt = sum(s["amount_yi"] for s in stocks)
                directions.append({
                    "priority": 2,
                    "direction": f"{sec}方向（竞价异动）",
                    "driver": f"竞价集体拉升: {len(stocks)}只，均涨{avg_chg:.1f}%，额{total_amt:.1f}亿",
                    "core_stocks": [f"{s['name']}({s['code']})" for s in stocks if s["code"] in CORE_CAPACITY | CORE_IDENTIFICATION][:3],
                    "all_stocks": [f"{s['name']}({s['code']})" for s in stocks],
                    "verify": f"开盘延续放量上攻、分时不破竞价均价",
                    "abandon": f"开盘即巅峰、分时破竞价均价回落",
                })

    # 方向 3：跷跷板预判（基于昨日强势方向 + 今日封单对比）
    prev_hot = context.get("yesterday_hot_sectors", [])
    if prev_hot:
        for sec in prev_hot:
            # 昨日强、今日封单弱 -> 可能被抽血（跷跷板对手方）
            sec_stocks = [r for r in rows if get_sector(r["code"]) == sec]
            if sec_stocks:
                avg_chg = sum(s["chg_pct"] for s in sec_stocks) / len(sec_stocks)
                if avg_chg < -1:  # 昨日热门今日弱
                    directions.append({
                        "priority": 3,
                        "direction": f"{sec}方向（跷跷板对手方·警惕抽血）",
                        "driver": f"昨日强势今日竞价走弱(均{avg_chg:+.1f}%)，资金可能流向新主线",
                        "core_stocks": [],
                        "all_stocks": [f"{s['name']}({s['code']})" for s in sec_stocks[:3]],
                        "verify": f"若该方向\"该弱不弱\"(开盘红盘/快速拉回) -> 超预期机会，可反向关注",
                        "abandon": f"持续走弱、无资金护盘",
                    })

    # 方向 4：容量中军确认
    capacity_signals = []
    for r in rows:
        if r["code"] in CORE_CAPACITY:
            if r["chg_pct"] >= 3 and r["bid_vol1"] > r["ask_vol1"] * 2:
                capacity_signals.append(r)
    if capacity_signals:
        directions.append({
            "priority": 4,
            "direction": "容量中军强势确认（跟随主线）",
            "driver": f"{len(capacity_signals)}只中军竞价>3%且买单占优",
            "core_stocks": [f"{s['name']}({s['code']}) {s['chg_pct']:+.1f}%" for s in capacity_signals],
            "all_stocks": [f"{s['name']}({s['code']}) {s['chg_pct']:+.1f}%" for s in capacity_signals],
            "verify": "开盘中军继续放量领涨、带动板块联动",
            "abandon": "中军高开低走、量能不跟",
        })

    # 方向 5：新闻驱动个股（回购/业绩/重组）
    news_driven = []
    for r in rows:
        code = r["code"]
        # 简化：结合新闻情绪关键词（实际应匹配具体个股新闻）
        if news.get("has_buyback") and code in {"002318", "300014", "600584"}:  # 久立/亿纬/长电近期有回购
            news_driven.append(r)
    if news_driven:
        directions.append({
            "priority": 5,
            "direction": "新闻催化个股（事件驱动）",
            "driver": f"隔夜消息面利好: 回购/业绩/重组",
            "core_stocks": [f"{s['name']}({s['code']})" for s in news_driven],
            "all_stocks": [f"{s['name']}({s['code']})" for s in news_driven],
            "verify": "开盘资金认可、放量上攻",
            "abandon": "利好出尽、高开低走",
        })

    # 🧠 哲学过滤：顺大势、逆小势
    # 顺大势：周线向上(UP)时，只保留 priority<=3 的做多方向，过滤做空方向
    # 逆小势：周线向上时，竞价回调(-2%到0)的优质标的可作为买点
    # 逆势操作：周线向下(DOWN)时，大幅降低买入权重，提高卖出权重
    filtered = []
    for d in directions:
        if weekly_trend == "UP":
            if d["direction"].startswith("容量中军") or d["direction"].startswith("一字涨停") or d["direction"].startswith("竞价异动"):
                # 顺大势：强化做多方向
                d["philosophy_boost"] = True
                d["priority"] = max(1, d["priority"] - 1)  # 提升优先级
            elif "跷跷板" in d["direction"] or "卖出" in d.get("driver", ""):
                # 逆小势/风险方向：标记但不完全过滤
                d["philosophy_warn"] = "顺大势背景下，此方向为逆势/风险"
        elif weekly_trend == "DOWN":
            if "买入" in str(d.get("driver", "")) or "涨停" in d["direction"]:
                # 逆大势买入：大幅降权
                d["priority"] = min(5, d["priority"] + 2)
                d["philosophy_warn"] = "逆大势操作，大幅降权"
            elif "卖出" in d.get("driver", "") or "跷跷板" in d["direction"]:
                # 顺势卖出：提升优先级
                d["priority"] = max(1, d["priority"] - 1)
                d["philosophy_boost"] = True
        # SIDEWAYS: 保持原优先级，仅标记
        filtered.append(d)
    
    # 重新按 priority 排序
    filtered.sort(key=lambda x: x["priority"])
    return filtered


# ==================== 步骤 3：量能预估 ====================
def estimate_volume(rows: List[Dict]) -> Dict:
    """基于竞价量能预估全天量能方向"""
    total_auction_amt = sum(r["amount_yi"] for r in rows)  # 竞价成交额(亿)
    total_auction_vol = sum(r["volume_shares"] for r in rows)  # 竞价成交量(股)

    # 与昨日全天对比（取自选池代理）
    total_prev_vol = sum(r["prev_volume_shares"] for r in rows if r.get("prev_volume_shares"))
    vol_ratio = (total_auction_vol / total_prev_vol * 100) if total_prev_vol > 0 else None

    # 竞价通常占全天 1%-3%，若竞价量/昨日全天 > 2% 视为放量预期
    if vol_ratio is not None:
        if vol_ratio >= 200:
            volume_bias = "大概率放量"
        elif vol_ratio >= 100:
            volume_bias = "温和放量"
        else:
            volume_bias = "缩量震荡预期"
    else:
        volume_bias = "无法判断"

    # 封单 vs 成交一致性
    total_bid_amt = sum(r["bid_vol1"] * r["bid1"] for r in rows) / 1e8
    total_ask_amt = sum(r["ask_vol1"] * r["ask1"] for r in rows) / 1e8
    seal_vs_trade = "封单成交一致" if abs(total_bid_amt - total_auction_amt) / max(total_auction_amt, 0.01) < 0.5 else "封单成交背离(警惕虚假封单)"

    return {
        "auction_amount_yi": round(total_auction_amt, 2),
        "auction_volume_shares": total_auction_vol,
        "vol_ratio_pct": round(vol_ratio, 1) if vol_ratio else None,
        "volume_bias": volume_bias,
        "total_bid_seal_yi": round(total_bid_amt, 2),
        "total_ask_seal_yi": round(total_ask_amt, 2),
        "seal_vs_trade": seal_vs_trade,
    }


# ==================== 步骤 4：输出决策 ====================
def build_decision(
    fatal_risk: Optional[Dict],
    high_risks: List[Dict],
    opportunities: List[Dict],
    volume: Dict,
    date_str: str,
) -> Dict:
    """构建最终决策对象"""
    now = datetime.now().strftime("%H:%M:%S")

    if fatal_risk:
        risk_judgment = "致命风险 → 无条件清仓，停止交易"
        risk_level = "🔴 致命"
    elif high_risks:
        risk_judgment = "高风险防御 → 不开新仓，持仓设止损"
        risk_level = "🟠 高风险"
    else:
        risk_judgment = "正常 → 按机会方向操作"
        risk_level = "🟢 正常"

    # 风险警示
    risk_warnings = []
    if fatal_risk:
        for r in fatal_risk["risks"]:
            risk_warnings.append(f"- {r['type']}: {r['details']}")
    for hr in high_risks:
        risk_warnings.append(f"- {hr['type']}: {hr['target']} -> {hr['action']}")

    # 跷跷板抽血风险方向
    teeter_warnings = []
    for opp in opportunities:
        if "跷跷板对手方" in opp["direction"]:
            teeter_warnings.append(f"- {opp['direction']}: {opp['driver']}")

    # 警惕兑现方向
    abandon_warnings = []
    for opp in opportunities:
        if opp.get("abandon"):
            abandon_warnings.append(f"- {opp['direction']}: {opp['abandon']}")

    decision = {
        "meta": {
            "date": date_str,
            "generated_at": now,
            "snapshot_file": f"{date_str}_9_25.json",
        },
        "risk_judgment": {
            "level": risk_level,
            "text": risk_judgment,
            "fatal_triggered": fatal_risk is not None,
            "high_risks": high_risks,
        },
        "opportunities": opportunities,
        "volume_analysis": volume,
        "risk_warnings": {
            "teeter_board": teeter_warnings,
            "abandon_risk": abandon_warnings,
            "general": risk_warnings,
        },
        "execution_plan": {
            "watch_9_30_9_35": [
                "机会方向开盘是否延续竞价走势？",
                "跷跷板对手方是否如预期走弱？还是\"该弱不弱\"？",
                "前30分钟量能是否支持方向持续？",
            ],
            "position_sizing": "致命风险=0仓；高风险=不加仓+止损；正常=按方向优先级分仓，单向≤30%",
            "stop_loss": "日内-5%，隔夜-8%",
        },
    }

    return decision


def format_markdown(decision: Dict) -> str:
    """格式化为可读 Markdown（Telegram 推送用）"""
    d = decision
    lines = [
        f"⚡ **竞价决策 · {d['meta']['date']} {d['meta']['generated_at']}**",
        "",
        f"**🔴 风险判断**: {d['risk_judgment']['level']}  {d['risk_judgment']['text']}",
        "",
    ]

    # 机会方向
    if d["opportunities"]:
        lines.append("**🎯 当日机会方向（按优先级）**:")
        for i, opp in enumerate(d["opportunities"], 1):
            lines.append(f"  **方向{i}: {opp['direction']}**")
            lines.append(f"    - 驱动信号: {opp['driver']}")
            if opp["core_stocks"]:
                lines.append(f"    - 核心标的: {', '.join(opp['core_stocks'])}")
            if opp["all_stocks"]:
                lines.append(f"    - 全部标的: {', '.join(opp['all_stocks'])}")
            lines.append(f"    - ✅ 开盘验证: {opp['verify']}")
            lines.append(f"    - ❌ 放弃条件: {opp['abandon']}")
            lines.append("")
    else:
        lines.append("**🎯 当日机会方向**: 无明确方向，观望为主")
        lines.append("")

    # 量能分析
    vol = d["volume_analysis"]
    lines.append("**📊 量能预估**:")
    lines.append(f"  - 竞价成交额: {vol['auction_amount_yi']:.2f}亿")
    lines.append(f"  - 竞价量/昨日全天: {vol['vol_ratio_pct']:.1f}%" if vol["vol_ratio_pct"] else "  - 量比: 无数据")
    lines.append(f"  - 全天倾向: **{vol['volume_bias']}**")
    lines.append(f"  - 买一封单: {vol['total_bid_seal_yi']:.2f}亿 | 卖一封单: {vol['total_ask_seal_yi']:.2f}亿")
    lines.append(f"  - 封单成交: {vol['seal_vs_trade']}")
    lines.append("")

    # 风险警示
    rw = d["risk_warnings"]
    if rw["teeter_board"]:
        lines.append("**⚠️ 跷跷板抽血风险**:")
        lines.extend(rw["teeter_board"])
        lines.append("")
    if rw["abandon_risk"]:
        lines.append("**⚠️ 警惕兑现方向**:")
        lines.extend(rw["abandon_risk"])
        lines.append("")
    if rw["general"]:
        lines.append("**⚠️ 一般风险提示**:")
        lines.extend(rw["general"])
        lines.append("")

    # 执行计划
    ep = d["execution_plan"]
    lines.append("**📋 开盘重点观察 (9:30-9:35)**:")
    for item in ep["watch_9_30_9_35"]:
        lines.append(f"  1. {item}")
    lines.append("")
    lines.append(f"**📈 仓位管理**: {ep['position_sizing']}")
    lines.append(f"**🛑 止损纪律**: {ep['stop_loss']}")

    return "\n".join(lines)


# ==================== 主流程 ====================
def main() -> int:
    # 1. 加载竞价快照
    snapshot = load_latest_auction_snapshot()
    if not snapshot:
        print("[ERROR] 未找到竞价快照文件", file=sys.stderr)
        return 1

    date_str = snapshot["date"]
    rows = snapshot["rows"]
    print(f"[INFO] 加载快照: {date_str} ({len(rows)} 只)")

    # 2. 加载上下文
    # 昨日日期（简单取前一日，实际应用交易日历）
    from datetime import timedelta
    prev_date = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    context = load_market_context(prev_date)
    strategy_map = load_adaptive_strategy_map()
    news = load_news_sentiment(date_str)

    # 3. 执行 4 步分析
    # 步骤 1
    fatal_risk = scan_fatal_risk(rows)
    high_risks = scan_high_risk(rows)

    if fatal_risk:
        print(f"[WARN] 触发致命风险: {fatal_risk['risks'][0]['type']}")

    # 步骤 2
    opportunities = identify_opportunities(rows, context, strategy_map, news)

    # 步骤 3
    volume = estimate_volume(rows)

    # 步骤 4
    decision = build_decision(fatal_risk, high_risks, opportunities, volume, date_str)

    # 5. 保存决策 JSON
    out_json = AUCTION_DIR / f"decision_{date_str}.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(decision, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 决策已保存: {out_json}")

    # 6. 输出 Markdown（stdout 供 cron 捕获推送）
    md = format_markdown(decision)
    print(md)

    return 0


if __name__ == "__main__":
    sys.exit(main())