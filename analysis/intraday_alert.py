#!/usr/bin/env python3
"""
盘中实时策略预警 + ATR 止损触发
====================================================
用途: 在交易时段定时扫描关注股，对照 data/intraday_alert_config.json 的
      手动策略规则 + 自动检查持仓 ATR 止损，生成预警消息。

数据源: 新浪 hq.sinajs.cn 实时行情 (稳定，无需历史K线，极快，可高频跑)

运行:
  python3 analysis/intraday_alert.py            # 扫描并打印全部预警(JSON stdout + 可读 stderr)
  python3 analysis/intraday_alert.py --push     # 输出到 data/intraday_alert_output.json 供 cron 读取推送

设计要点:
  - 只做快照式预警判定，不做交易执行、不重历史，保证盘中每几分钟可跑一次不超时
  - 预警规则由 config 定义: break_below(跌破止损) / break_above(升破买点) / drop_pct(跌超) / surge_pct(涨超)
  - 新增: 自动检查 portfolio_sim_state.json 中持仓的 ATR 止损价，跌破即触发 HIGH 级预警
  - 每次运行全量输出(触发/未触发都列)，由调用方(cron/agent)判断是否推送，避免重复推送
"""

from __future__ import annotations

import os
import sys
import json
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

CONFIG_FILE = os.path.join(WORKSPACE, "data", "intraday_alert_config.json")
OUTPUT_FILE = os.path.join(WORKSPACE, "data", "intraday_alert_output.json")
STATE_FILE = os.path.join(WORKSPACE, "data", "intraday_alert_state.json")
PORTFOLIO_STATE_FILE = os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict:
    """记录已触发的预警，避免同一价位重复轰炸"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"fired": {}}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_portfolio_state() -> dict:
    """加载模拟盘持仓状态"""
    if os.path.exists(PORTFOLIO_STATE_FILE):
        with open(PORTFOLIO_STATE_FILE) as f:
            return json.load(f)
    return {"positions": {}}


def check_atr_stop_loss(portfolio_state: dict, spot_data: dict) -> list[dict]:
    """检查持仓是否触及 ATR 止损价，返回预警列表"""
    alerts = []
    positions = portfolio_state.get("positions", {})

    # Build spot lookup by symbol (sina hq 返回格式: symbol -> {last, pre_close, ...})
    spot_by_symbol = {}
    for sym, spot in spot_data.items():
        spot_by_symbol[sym] = spot

    for sym, pos in positions.items():
        if not pos.get("position"):
            continue

        atr_stop = pos.get("atr_stop")
        entry_price = pos.get("entry_price", 0)
        if atr_stop is None or atr_stop <= 0 or entry_price <= 0:
            continue

        spot = spot_by_symbol.get(sym)
        if not spot:
            continue

        current_price = spot.get("last", 0)  # sina hq 字段是 last
        if current_price <= 0:
            continue

        # 检查是否跌破 ATR 止损价
        if current_price <= atr_stop:
            loss_pct = (current_price - entry_price) / entry_price * 100
            alerts.append({
                "code": "atr_stop_loss",
                "symbol": sym,
                "name": pos.get("name", sym),
                "price": round(current_price, 2),
                "change_pct": round((current_price / spot.get("pre_close", current_price) - 1) * 100, 2) if spot.get("pre_close") else 0,
                "msg": f"🛑 ATR止损触发: {pos.get('name', sym)}({sym}) 现价¥{current_price:.2f} 跌破止损价¥{atr_stop:.2f} (入场¥{entry_price:.2f}, 浮亏{loss_pct:.2f}%)",
                "action": "卖出",
                "type": "risk_control",
                "level_name": "ATR止损",
                "severity": "HIGH",
                "level": atr_stop,
            })

    return alerts


def evaluate_stock(cfg: dict, spot: dict) -> list[dict]:
    """对单只股票对照规则判定预警"""
    alerts = []
    sym = spot["symbol"]
    price = spot["price"]
    chg = spot["change_pct"]
    levels = cfg.get("levels", {})
    action = cfg.get("action", "")
    type_ = cfg.get("type", "")

    # 跌破关键位
    bl = levels.get("break_below")
    if bl is not None and price > 0 and price <= bl:
        alerts.append({
            "code": "break_below", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": bl, "change_pct": chg,
            "msg": f"跌破止损位 {bl:.2f} (现{price:.2f}) 应{action}", "action": action, "type": type_,
            "level_name": "止损/减仓", "severity": "HIGH",
        })

    # 升破买点
    ba = levels.get("break_above")
    if ba is not None and price > 0 and price >= ba:
        alerts.append({
            "code": "break_above", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": ba, "change_pct": chg,
            "msg": f"升破关键位 {ba:.2f} (现{price:.2f}) 应{action}", "action": action, "type": type_,
            "level_name": "买点/加仓", "severity": "MEDIUM",
        })

    # 进入买入区下限 (仅当未被 break_below 触发时用于低吸参考)
    blz = levels.get("buy_zone_low")
    if blz is not None and price > 0 and (bl is None or price > bl) and price <= blz:
        alerts.append({
            "code": "buy_zone", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": blz, "change_pct": chg,
            "msg": f"进入买入参考区 (现{price:.2f} 接近{blz:.2f}) 可考虑低吸", "action": action, "type": type_,
            "level_name": "回补/低吸", "severity": "LOW",
        })

    # 急跌 / 急涨 (异常波动)
    dd = cfg.get("drop_pct")
    if dd is not None and chg <= dd:
        alerts.append({
            "code": "drop_pct", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": dd, "change_pct": chg,
            "msg": f"日内跌超{abs(dd):.1f}% ({chg:.1f}%) 注意风控", "action": action, "type": type_,
            "level_name": "急跌预警", "severity": "HIGH",
        })

    sg = cfg.get("surge_pct")
    if sg is not None and chg >= sg:
        alerts.append({
            "code": "surge_pct", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": sg, "change_pct": chg,
            "msg": f"日内涨超{sg:.1f}% ({chg:.1f}%) 注意止盈/追高风险", "action": action, "type": type_,
            "level_name": "急涨预警", "severity": "MEDIUM",
        })

    return alerts


def main():
    config = load_config()
    stocks_cfg = config.get("stocks", {})
    meta = config.get("_meta", {})
    state = load_state()
    now = datetime.now()

    # 只扫描 config 里有规则的股票
    from close_scan_v2 import get_fetcher, WATCHLIST
    fetcher = get_fetcher()
    symbols = []
    code_to_meta = {code: cfg for code, cfg in stocks_cfg.items()}
    # 基于 watchlist 顺序，但只取 config 中定义的
    for code, name in WATCHLIST:
        if code in code_to_meta:
            symbols.append(("sh" if code[0] in "69" else "sz") + code)

    hq = fetcher.fetch_sina_spot(symbols)

    # 加载持仓状态并检查 ATR 止损
    portfolio_state = load_portfolio_state()
    atr_alerts = check_atr_stop_loss(portfolio_state, hq)

    fired_state = state.get("fired", {})
    all_alerts = []
    triggered = []

    # 先处理 ATR 止损预警（最高优先级）
    for a in atr_alerts:
        key = f"{a['code']}:{a['symbol']}"
        if key not in fired_state or fired_state[key] != a["price"]:
            all_alerts.append(a)
            triggered.append(a)
            fired_state[key] = a["price"]

    # 再处理手动配置的预警规则
    for code, cfg in stocks_cfg.items():
        r = hq.get(code)
        if r is None or r["last"] == 0 or r["pre_close"] == 0:
            continue
        chg = (r["last"] / r["pre_close"] - 1) * 100
        spot = {"symbol": code, "name": cfg.get("name", code),
                "price": round(r["last"], 2), "change_pct": round(chg, 2)}
        al = evaluate_stock(cfg, spot)
        if al:
            for a in al:
                key = f"{a['code']}:{code}"
                if key not in fired_state or fired_state[key] != a["price"]:
                    all_alerts.append(a)
                    triggered.append(a)
                    fired_state[key] = a["price"]
        else:
            spot["change_pct"] = round(chg, 2)
            spot["action"] = cfg.get("action", "")
            spot["alert"] = None

    state["fired"] = fired_state
    # 清理过期价位标记(每天重置更干净，但保留当日去重)
    save_state(state)

    output = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "trading_day": datetime.now().strftime("%A"),
        "market_context": meta.get("market_context", ""),
        "total_alerts": len(all_alerts),
        "alerts": all_alerts,
        "triggered_this_run": triggered,
    }

    # 落盘供 cron/agent 读取
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    # stdout = JSON (供 agent 解析推送)
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict:
    """记录已触发的预警，避免同一价位重复轰炸"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"fired": {}}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def evaluate_stock(cfg: dict, spot: dict) -> list[dict]:
    """对单只股票对照规则判定预警"""
    alerts = []
    sym = spot["symbol"]
    price = spot["price"]
    chg = spot["change_pct"]
    levels = cfg.get("levels", {})
    action = cfg.get("action", "")
    type_ = cfg.get("type", "")

    # 跌破关键位
    bl = levels.get("break_below")
    if bl is not None and price > 0 and price <= bl:
        alerts.append({
            "code": "break_below", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": bl, "change_pct": chg,
            "msg": f"跌破止损位 {bl:.2f} (现{price:.2f}) 应{action}", "action": action, "type": type_,
            "level_name": "止损/减仓", "severity": "HIGH",
        })

    # 升破买点
    ba = levels.get("break_above")
    if ba is not None and price > 0 and price >= ba:
        alerts.append({
            "code": "break_above", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": ba, "change_pct": chg,
            "msg": f"升破关键位 {ba:.2f} (现{price:.2f}) 应{action}", "action": action, "type": type_,
            "level_name": "买点/加仓", "severity": "MEDIUM",
        })

    # 进入买入区下限 (仅当未被 break_below 触发时用于低吸参考)
    blz = levels.get("buy_zone_low")
    if blz is not None and price > 0 and (bl is None or price > bl) and price <= blz:
        alerts.append({
            "code": "buy_zone", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": blz, "change_pct": chg,
            "msg": f"进入买入参考区 (现{price:.2f} 接近{blz:.2f}) 可考虑低吸", "action": action, "type": type_,
            "level_name": "回补/低吸", "severity": "LOW",
        })

    # 急跌 / 急涨 (异常波动)
    dd = cfg.get("drop_pct")
    if dd is not None and chg <= dd:
        alerts.append({
            "code": "drop_pct", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": dd, "change_pct": chg,
            "msg": f"日内跌超{abs(dd):.1f}% ({chg:.1f}%) 注意风控", "action": action, "type": type_,
            "level_name": "急跌预警", "severity": "HIGH",
        })

    sg = cfg.get("surge_pct")
    if sg is not None and chg >= sg:
        alerts.append({
            "code": "surge_pct", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": sg, "change_pct": chg,
            "msg": f"日内涨超{sg:.1f}% ({chg:.1f}%) 注意止盈/追高风险", "action": action, "type": type_,
            "level_name": "急涨预警", "severity": "MEDIUM",
        })

    return alerts


if __name__ == "__main__":
    main()