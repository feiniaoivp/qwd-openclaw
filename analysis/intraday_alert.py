#!/usr/bin/env python3
"""
盘中实时策略预警 + ATR 止损触发
====================================================
用途: 在交易时段定时扫描关注股，对照 data/runtime/intraday_alert_config.json 的
      手动策略规则 + 自动检查持仓 ATR 止损，生成预警消息。

数据源: 新浪 hq.sinajs.cn 实时行情 (稳定，无需历史K线，极快，可高频跑)

运行:
  python3 analysis/intraday_alert.py            # 扫描并打印全部预警(JSON stdout + 可读 stderr)
  python3 analysis/intraday_alert.py --push     # 输出到 data/runtime/intraday_alert_output.json 供 cron 读取推送

设计要点:
  - 只做快照式预警判定，不做交易执行、不重历史，保证盘中每几分钟可跑一次不超时
  - 预警规则由 config 定义: break_below(跌破止损) / break_above(升破买点) / drop_pct(跌超) / surge_pct(涨超)
  - 新增: 自动检查 portfolio_sim_state.json 中持仓的 ATR 止损价，跌破即触发 HIGH 级预警
  - 每次运行全量输出(触发/未触发都列)，由调用方(cron/agent)判断是否推送，避免重复推送
  - 2026-11-02 新增: 结合 close_scan 指导数据生成方向性建议
"""

from __future__ import annotations

import os
import sys
import shutil
import json
import glob
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

CONFIG_FILE = os.path.join(WORKSPACE, "data", "intraday_alert_config.json")
OUTPUT_FILE = os.path.join(WORKSPACE, "data", "intraday_alert_output.json")
STATE_FILE = os.path.join(WORKSPACE, "data", "intraday_alert_state.json")
PORTFOLIO_STATE_FILE = os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")
SIGNAL_LIFECYCLE_FILE = os.path.join(WORKSPACE, "data", "signal_lifecycle.json")


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict:
    """记录已触发的预警，避免同一价位重复轰炸（当日去重，跨日自动重置）。

    修复 2026-10-17: 旧实现 fired 只增不减、跨日/跨周累积，导致:
    ① 僵尸预警(收盘已收复触发位但状态未清, 48 条中实测 11 条)；
    ② 同价位永久静音(跌破→收复→同价再跌破不告警, 回踩确认漏报)；
    ③ weekly_review 将累积 state 误读为「本周 N 条」(口径虚高)。
    跨日重置一处修复同时解决三题（原注释「每天重置更干净」意图落地）。
    """
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    else:
        state = {"fired": {}}
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("date") != today:
        state = {"date": today, "fired": {}}
    return state


def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    _bak(STATE_FILE)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_portfolio_state() -> dict:
    """加载模拟盘持仓状态"""
    if os.path.exists(PORTFOLIO_STATE_FILE):
        with open(PORTFOLIO_STATE_FILE) as f:
            return json.load(f)
    return {"positions": {}}


def load_signal_lifecycle() -> dict:
    """加载信号生命周期（存活天数、初始评分）"""
    if os.path.exists(SIGNAL_LIFECYCLE_FILE):
        with open(SIGNAL_LIFECYCLE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def find_latest_close_scan_json() -> str | None:
    """查找最新的 close_scan JSON 文件"""
    cands = glob.glob(os.path.join(WORKSPACE, "analysis", "daily", "*_close_scan.json"))
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def load_close_scan_guidance() -> dict:
    """从最新 close_scan JSON 加载方向性指导数据。
    返回: {code: {level, score, reasons, risks, fib_time, stop_long, support, resistance, indicators...}}"""
    path = find_latest_close_scan_json()
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️ close_scan JSON 读取失败: {e}", file=sys.stderr)
        return {}
    guidance = {}
    for s in data.get("stocks", []):
        code = str(s.get("symbol") or "")
        if not code:
            continue
        guidance[code] = {
            "signal_level": s.get("signal", {}).get("level"),
            "signal_score": s.get("signal", {}).get("score"),
            "signal_reasons": s.get("signal", {}).get("reasons"),
            "signal_risks": s.get("signal", {}).get("risks"),
            "fib_time": s.get("signal", {}).get("fib_time"),
            "stop_long": s.get("stop_long"),
            "support": s.get("support"),
            "resistance": s.get("resistance"),
            "data_date": data.get("data_date"),
            "indicators": s.get("indicators", {}),
        }
    return guidance


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


def evaluate_stock(cfg: dict, spot: dict, guidance: dict) -> list[dict]:
    """对单只股票对照规则判定预警，结合 close_scan 指导生成方向性建议"""
    alerts = []
    sym = spot["symbol"]
    price = spot["price"]
    chg = spot["change_pct"]
    levels = cfg.get("levels", {})
    action = cfg.get("action", "")
    type_ = cfg.get("type", "")
    
    g = guidance.get(sym, {})
    signal_level = g.get("signal_level")
    signal_score = g.get("signal_score")
    signal_reasons = g.get("signal_reasons")
    signal_risks = g.get("signal_risks")
    fib_time = g.get("fib_time")
    stop_long = g.get("stop_long")
    support = g.get("support")
    resistance = g.get("resistance")
    indicators = g.get("indicators", {})
    data_date = g.get("data_date")
    
    # 提取 ER 状态和政权信息
    ema_trend = indicators.get("ema_trend", {})
    macd = indicators.get("macd", {})
    vol = indicators.get("volume", {})
    
    # 判断 ER 状态（趋势/震荡）
    er_status = "趋势" if ema_trend.get("ER20", 0) > 0.3 else "震荡"
    # 政权
    adx = ema_trend.get("ADX", 0)
    chop = ema_trend.get("CHOP", 0)
    if adx > 25 and chop < 50:
        regime = "强趋势"
    elif adx > 25:
        regime = "弱趋势"
    else:
        regime = "无趋势"
    
    # 获取斐波那契时间窗口信息
    fib_time_info = ""
    if fib_time and isinstance(fib_time, dict):
        for anchor in fib_time.get("anchors", []):
            for t in anchor.get("targets", []):
                if not t.get("passed") and t.get("fib") in [1, 2, 3, 5, 8, 13, 21, 34]:
                    fib_time_info = f"斐波时间窗口: {t.get('date')} (Fib {t.get('fib')})"
                    break
            if fib_time_info:
                break
    
    # 构建基础方向性指导文本
    base_guidance = []
    if signal_level:
        base_guidance.append(f"收盘信号: {signal_level} (分数: {signal_score})")
    if signal_reasons:
        base_guidance.append(f"利好因子: {signal_reasons}")
    if signal_risks:
        base_guidance.append(f"风险因子: {signal_risks}")
    if er_status:
        base_guidance.append(f"ER状态: {er_status}")
    if regime:
        base_guidance.append(f"市场政权: {regime} (ADX{adx:.1f}/CHOP{chop:.1f})")
    if fib_time_info:
        base_guidance.append(fib_time_info)
    if stop_long and price > 0:
        base_guidance.append(f"ATR止损: ¥{stop_long:.2f} (距当前: {(price-stop_long)/price*100:.1f}%)")
    if support and price > 0:
        base_guidance.append(f"支撑位: ¥{support:.2f} (距当前: {(price-support)/price*100:.1f}%)")
    if resistance and price > 0:
        base_guidance.append(f"阻力位: ¥{resistance:.2f} (距当前: {(resistance-price)/price*100:.1f}%)")
    if data_date:
        base_guidance.append(f"数据基准: {data_date} 收盘")
    guidance_text = " | ".join(base_guidance) if base_guidance else "无额外指导"

    # 跌破关键位
    bl = levels.get("break_below")
    if bl is not None and price > 0 and price <= bl:
        alert = {
            "code": "break_below", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": bl, "change_pct": chg,
            "msg": f"跌破止损位 {bl:.2f} (现{price:.2f}) 应{action}",
            "action": action, "type": type_,
            "level_name": "止损/减仓", "severity": "HIGH",
            "directional_guidance": guidance_text,
            "signal_level": signal_level,
            "signal_score": signal_score,
            "er_status": er_status,
            "regime": regime,
            "stop_long": stop_long,
            "support": support,
            "resistance": resistance,
            "fib_time_info": fib_time_info,
        }
        alerts.append(alert)

    # 升破买点
    ba = levels.get("break_above")
    if ba is not None and price > 0 and price >= ba:
        alert = {
            "code": "break_above", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": ba, "change_pct": chg,
            "msg": f"升破关键位 {ba:.2f} (现{price:.2f}) 应{action}",
            "action": action, "type": type_,
            "level_name": "买点/加仓", "severity": "MEDIUM",
            "directional_guidance": guidance_text,
            "signal_level": signal_level,
            "signal_score": signal_score,
            "er_status": er_status,
            "regime": regime,
            "stop_long": stop_long,
            "support": support,
            "resistance": resistance,
            "fib_time_info": fib_time_info,
        }
        alerts.append(alert)

    # 进入买入区下限 (仅当未被 break_below 触发时用于低吸参考)
    blz = levels.get("buy_zone_low")
    if blz is not None and price > 0 and (bl is None or price > bl) and price <= blz:
        alert = {
            "code": "buy_zone", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": blz, "change_pct": chg,
            "msg": f"进入买入参考区 (现{price:.2f} 接近{blz:.2f}) 可考虑低吸",
            "action": action, "type": type_,
            "level_name": "回补/低吸", "severity": "LOW",
            "directional_guidance": guidance_text,
            "signal_level": signal_level,
            "signal_score": signal_score,
            "er_status": er_status,
            "regime": regime,
            "stop_long": stop_long,
            "support": support,
            "resistance": resistance,
            "fib_time_info": fib_time_info,
        }
        alerts.append(alert)

    # 急跌 / 急涨 (异常波动)
    dd = cfg.get("drop_pct")
    if dd is not None and chg <= dd:
        alert = {
            "code": "drop_pct", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": dd, "change_pct": chg,
            "msg": f"日内跌超{abs(dd):.1f}% ({chg:.1f}%) 注意风控",
            "action": action, "type": type_,
            "level_name": "急跌预警", "severity": "HIGH",
            "directional_guidance": guidance_text,
            "signal_level": signal_level,
            "signal_score": signal_score,
            "er_status": er_status,
            "regime": regime,
            "stop_long": stop_long,
            "support": support,
            "resistance": resistance,
            "fib_time_info": fib_time_info,
        }
        alerts.append(alert)

    sg = cfg.get("surge_pct")
    if sg is not None and chg >= sg:
        alert = {
            "code": "surge_pct", "symbol": sym, "name": cfg.get("name", sym),
            "price": price, "level": sg, "change_pct": chg,
            "msg": f"日内涨超{sg:.1f}% ({chg:.1f}%) 注意止盈/追高风险",
            "action": action, "type": type_,
            "level_name": "急涨预警", "severity": "MEDIUM",
            "directional_guidance": guidance_text,
            "signal_level": signal_level,
            "signal_score": signal_score,
            "er_status": er_status,
            "regime": regime,
            "stop_long": stop_long,
            "support": support,
            "resistance": resistance,
            "fib_time_info": fib_time_info,
        }
        alerts.append(alert)

    return alerts


def main():
    config = load_config()
    stocks_cfg = config.get("stocks", {})
    # ⚠️ import 必须先于调用（2026-10-16 18:2x 修复）：此前 derive_alert_levels()
    #    在下方局部 import 之前被调用 → UnboundLocalError，盘中预警 cron 直接崩。
    #    教训：函数内局部 import 的名字，在函数体内“先使用后绑定”即 UnboundLocalError。
    from close_scan_v2 import get_fetcher, WATCHLIST, derive_alert_levels
    derived, prov = derive_alert_levels()
    meta = config.get("_meta", {})
    state = load_state()
    now = datetime.now()

    # 加载 close_scan 方向性指导
    guidance = load_close_scan_guidance()

    # 只扫描有规则（静态配置 ∪ 派生价位）的股票
    name_by_code = dict(WATCHLIST)
    fetcher = get_fetcher()
    symbols = []
    code_to_meta = {code: cfg for code, cfg in stocks_cfg.items()}
    # 基于 watchlist 顺序，但只取静态配置或派生价位任一存在的
    for code, name in WATCHLIST:
        if code in code_to_meta or code in derived:
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

    # 再处理预警规则（价位已自动派生；静态 levels 仅降级兑底，lock 可豁免）
    eval_codes = [c for c, _ in WATCHLIST if c in stocks_cfg or c in derived]
    for code in eval_codes:
        raw_cfg = stocks_cfg.get(code)
        dlv = derived.get(code) or {}
        if not raw_cfg and not dlv:
            continue
        cfg = dict(raw_cfg) if raw_cfg else {"name": name_by_code.get(code, code),
                                             "action": "策略预警", "type": "scan_derived",
                                             "drop_pct": -5.0, "surge_pct": 5.0}
        _locked = bool((raw_cfg or {}).get("levels", {}).get("lock"))
        if dlv and not _locked:
            cfg["levels"] = dict(dlv)  # 派生价位整组覆盖
        if dlv and not _locked:
            levels_src = f"scan:{prov['data_date']}"
        elif _locked:
            levels_src = "static_config(lock)"
        else:
            levels_src = "static_config(降级)"
        r = hq.get(code)
        if r is None or r["last"] == 0 or r["pre_close"] == 0:
            continue
        chg = (r["last"] / r["pre_close"] - 1) * 100
        spot = {"symbol": code, "name": cfg.get("name", code),
                "price": round(r["last"], 2), "change_pct": round(chg, 2)}
        al = evaluate_stock(cfg, spot, guidance)
        for a in al:
            a["levels_src"] = levels_src
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
        "levels_provenance": prov,
    }

    # 落盘供 cron/agent 读取
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    _bak(OUTPUT_FILE)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    # stdout = JSON (供 agent 解析推送)
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()