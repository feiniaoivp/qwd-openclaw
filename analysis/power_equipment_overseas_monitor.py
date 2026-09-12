#!/usr/bin/env python3
"""
电网装备/特高压出海板块专项监控
======================================================
功能：
  1. 盘中实时异动预警（价格/成交量/资金流）
  2. 盘后深度复盘（技术面+基本面催化剂+汇率影响）
  3. 出海订单/公告事件驱动跟踪
  4. 汇率敏感度动态计算
  5. 板块轮动/资金流向监控

运行模式：
  python3 analysis/power_equipment_overseas_monitor.py --mode intraday   # 盘中30分钟轮询
  python3 analysis/power_equipment_overseas_monitor.py --mode eod        # 收盘后深度复盘
  python3 analysis/power_equipment_overseas_monitor.py --mode weekly     # 周末深度研报
"""

from __future__ import annotations

import os
import sys
import json
import re
import time
import math
import logging
import argparse
from datetime import datetime, timedelta, time as dtime
from typing import Any, Optional, Dict, List
from functools import lru_cache

import pandas as pd
import sys
import os
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

try:
    import pandas_ta as ta
    HAS_PANDAS_TA = True
except Exception:
    HAS_PANDAS_TA = False

try:
    import akshare as ak
    HAS_AKSHARE = True
except Exception:
    HAS_AKSHARE = False

# 导入真实事件数据源
try:
    from power_overseas_events import get_events_for_symbol
    HAS_REAL_EVENTS = True
except Exception as e:
    log.warning(f"真实事件数据源不可用: {e}")
    HAS_REAL_EVENTS = False

# ============================================================================
# 配置加载
# ============================================================================

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
CONFIG_FILE = os.path.join(WORKSPACE, "data", "power_overseas_config.json")
STATE_FILE = os.path.join(WORKSPACE, "data", "power_overseas_state.json")
CACHE_DIR = os.path.join(WORKSPACE, "data_cache")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ============================================================================
# 工具函数
# ============================================================================

def load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"alerts_fired": {}, "last_scan": None, "fx_rates": {}}

def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)

def fetch_sina_spot(symbols: List[str]) -> Dict[str, dict]:
    """批量拉取新浪实时行情"""
    import urllib.request
    out = {}
    for i in range(0, len(symbols), 60):
        batch = symbols[i:i+60]
        url = "http://hq.sinajs.cn/list=" + ",".join(batch)
        req = urllib.request.Request(
            url,
            headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
        )
        try:
            raw = urllib.request.urlopen(req, timeout=12).read().decode("gbk")
        except Exception as e:
            log.warning(f"[SINA-SPOT] 批次请求失败: {e}")
            continue
        for line in raw.splitlines():
            if '="' not in line: continue
            key = line.split("hq_str_")[1].split("=")[0]
            val = line.split('"')[1].split(",")
            if len(val) < 10: continue
            symbol = key[2:]
            def f(x):
                try: return float(x)
                except: return 0.0
            out[symbol] = {
                "name": val[0],
                "open": f(val[1]), "pre_close": f(val[2]), "last": f(val[3]),
                "high": f(val[4]), "low": f(val[5]),
                "volume": int(float(val[8])), "amount": f(val[9]),
                "date": val[30] if len(val) > 30 else "",
                "time": val[31] if len(val) > 31 else "",
            }
    return out

def fetch_sina_daily(symbol: str, n: int = 300) -> Optional[pd.DataFrame]:
    """新浪日K jsonp 接口"""
    import urllib.request
    pref = "sh" if symbol[0] in "69" else "sz"
    url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService."
           f"getKLineData?symbol={pref}{symbol}&scale=240&ma=no&datalen={n}")
    req = urllib.request.Request(
        url,
        headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    )
    try:
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
    except Exception as e:
        log.warning(f"[SINA-DAILY] {symbol} 请求失败: {e}")
        return None
    m = re.search(r"=\s*\((\[.*\])\)\s*;", raw, re.S)
    if not m:
        i, j = raw.find("["), raw.rfind("]")
        if i < 0 or j <= i: return None
        arr = json.loads(raw[i:j+1])
    else:
        arr = json.loads(m.group(1))
    df = pd.DataFrame(arr)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["day"])
    df = df[["date", "open", "high", "low", "close", "volume"]].dropna()
    return df

def calc_technical_signals(df: pd.DataFrame) -> dict:
    """计算核心技术指标信号"""
    if len(df) < 60:
        return {"error": "数据不足"}
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]
    
    # EMA 系统
    ema20 = ta.ema(close, length=20) if HAS_PANDAS_TA else close.ewm(span=20, adjust=False).mean()
    ema50 = ta.ema(close, length=50) if HAS_PANDAS_TA else close.ewm(span=50, adjust=False).mean()
    ema200 = ta.ema(close, length=200) if HAS_PANDAS_TA else close.ewm(span=200, adjust=False).mean()
    
    # MACD
    if HAS_PANDAS_TA:
        macd_df = ta.macd(close, fast=12, slow=26, signal=9)
        macd_line = macd_df.get("MACD_12_26_9", macd_df.get("MACD"))
        signal_line = macd_df.get("MACDs_12_26_9", macd_df.get("MACD_SIGNAL"))
        hist = macd_df.get("MACDh_12_26_9", macd_df.get("MACD_HIST"))
    else:
        ef = close.ewm(span=12, adjust=False).mean()
        es = close.ewm(span=26, adjust=False).mean()
        macd_line = ef - es
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal_line
    
    # ATR
    if HAS_PANDAS_TA:
        atr_series = ta.atr(high, low, close, length=14)
    else:
        tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
        atr_series = tr.rolling(14).mean()
    
    cur = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    chg_pct = (cur / prev - 1) * 100
    
    return {
        "price": round(cur, 2),
        "change_pct": round(chg_pct, 2),
        "volume": int(vol.iloc[-1]),
        "vol_ma20": int(vol.rolling(20).mean().iloc[-1]),
        "vol_ratio": round(float(vol.iloc[-1]) / float(vol.rolling(20).mean().iloc[-1]), 2),
        "ema20": round(float(ema20.iloc[-1]), 2),
        "ema50": round(float(ema50.iloc[-1]), 2),
        "ema200": round(float(ema200.iloc[-1]), 2),
        "above_ema20": cur > float(ema20.iloc[-1]),
        "above_ema50": cur > float(ema50.iloc[-1]),
        "above_ema200": cur > float(ema200.iloc[-1]),
        "bull_arrange": float(ema20.iloc[-1]) > float(ema50.iloc[-1]) > float(ema200.iloc[-1]),
        "macd": round(float(macd_line.iloc[-1]), 4),
        "macd_signal": round(float(signal_line.iloc[-1]), 4),
        "macd_hist": round(float(hist.iloc[-1]), 4),
        "macd_cross_up": float(macd_line.iloc[-2]) <= float(signal_line.iloc[-2]) and float(macd_line.iloc[-1]) > float(signal_line.iloc[-1]),
        "macd_cross_down": float(macd_line.iloc[-2]) >= float(signal_line.iloc[-2]) and float(macd_line.iloc[-1]) < float(signal_line.iloc[-1]),
        "macd_above_zero": float(macd_line.iloc[-1]) > 0 and float(signal_line.iloc[-1]) > 0,
        "atr14": round(float(atr_series.iloc[-1]), 2),
        "stop_long": round(cur - 2 * float(atr_series.iloc[-1]), 2),
        "data_date": str(df["date"].iloc[-1].date()),
    }

def calc_fx_impact(config: dict, fx_rates: dict) -> dict:
    """计算汇率变动对海外收入的影响"""
    fx_exposure = config.get("fx_exposure", {})
    revenue_overseas = config.get("revenue_overseas_pct", 0)
    impact = {}
    total_impact = 0
    for ccy, weight in fx_exposure.items():
        if ccy in fx_rates:
            chg = fx_rates[ccy].get("chg_pct", 0)
            # 简化：海外收入占比 * 汇率权重 * 汇率变化
            ccy_impact = revenue_overseas * weight * chg
            impact[ccy] = round(ccy_impact * 100, 2)  # 基点
            total_impact += ccy_impact
    impact["total_bps"] = round(total_impact * 100, 2)
    return impact

def fetch_fx_rates() -> dict:
    """获取主要汇率 (简易版，实际可接入专业汇率API)"""
    # 这里返回模拟数据，实际应调用汇率API
    return {
        "USD": {"rate": 7.12, "chg_pct": 0.15},
        "EUR": {"rate": 7.85, "chg_pct": -0.08},
        "SAR": {"rate": 1.90, "chg_pct": 0.15},  # 盯住美元
        "BRL": {"rate": 1.28, "chg_pct": -0.45},
        "INR": {"rate": 0.085, "chg_pct": 0.02},
    }

def check_intraday_alerts(spot: dict, config: dict, state: dict) -> List[dict]:
    """盘中异动预警"""
    alerts = []
    symbol = config["symbol"]
    name = config["name"]
    price = spot["last"]
    pre_close = spot["pre_close"]
    chg_pct = (price / pre_close - 1) * 100 if pre_close else 0
    vol_ratio = spot["volume"] / max(1, spot.get("vol_ma20", 1))
    
    threshold = 3  # 3% 异动阈值
    
    # 1. 价格异动
    if abs(chg_pct) >= threshold:
        severity = "HIGH" if abs(chg_pct) >= 5 else "MEDIUM"
        alerts.append({
            "type": "price_surge" if chg_pct > 0 else "price_drop",
            "symbol": symbol, "name": name,
            "price": round(price, 2), "change_pct": round(chg_pct, 2),
            "severity": severity,
            "msg": f"{'异动拉升' if chg_pct > 0 else '异动下跌'} {chg_pct:.2f}%，关注{'出海订单/利好催化' if chg_pct > 0 else '止损/减仓信号'}"
        })
    
    # 2. 放量异动
    if vol_ratio >= 2.0 and chg_pct > 1:
        alerts.append({
            "type": "volume_surge",
            "symbol": symbol, "name": name,
            "price": round(price, 2), "change_pct": round(chg_pct, 2),
            "vol_ratio": round(vol_ratio, 2),
            "severity": "MEDIUM",
            "msg": f"放量上涨 量比{vol_ratio:.1f}x，疑似主力进场或利好兑现"
        })
    
    # 3. 关键支撑/压力位触发 (简易：EMA20/EMA50/EMA200)
    # 需要历史数据，盘中简化处理
    
    # 去重：同一类型同一价格不重复报警
    fired = state.get("alerts_fired", {})
    filtered = []
    for a in alerts:
        key = f"{a['type']}:{symbol}:{a['price']}"
        if fired.get(key) != a.get("change_pct", 0):
            filtered.append(a)
            fired[key] = a.get("change_pct", 0)
    state["alerts_fired"] = fired
    return filtered

def run_intraday_mode():
    """盘中监控模式"""
    config = load_config()
    state = load_state()
    stocks = config["stocks"]
    
    symbols = [("sh" if c[0] in "69" else "sz") + c for c in stocks.keys()]
    hq = fetch_sina_spot(symbols)
    fx_rates = fetch_fx_rates()
    state["fx_rates"] = fx_rates
    
    all_alerts = []
    results = []
    
    for code, cfg in stocks.items():
        spot = hq.get(code)
        if not spot or spot["last"] == 0:
            continue
        
        # 基础行情
        chg_pct = (spot["last"] / spot["pre_close"] - 1) * 100 if spot["pre_close"] else 0
        results.append({
            "symbol": code, "name": cfg["name"],
            "price": round(spot["last"], 2),
            "change_pct": round(chg_pct, 2),
            "volume": spot["volume"],
            "amount": round(spot["amount"], 2),
            "vol_ratio": "N/A"
        })
        
        # 异动预警
        cfg_inner = dict(cfg)  # 不修改原始配置
        cfg_inner["symbol"] = code
        alerts = check_intraday_alerts(spot, cfg_inner, state)
        all_alerts.extend(alerts)
    
    # 汇率影响汇总
    fx_summary = {}
    for code, cfg in stocks.items():
        fx_summary[code] = calc_fx_impact(cfg, fx_rates)
    
    state["last_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)
    
    output = {
        "mode": "intraday",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "board": "电网装备/特高压出海",
        "stocks": results,
        "alerts": all_alerts,
        "fx_impact": fx_summary,
        "market_note": f"监控{len(results)}只标的，触发{len(all_alerts)}条预警"
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))

def run_eod_mode():
    """收盘后深度复盘模式"""
    config = load_config()
    stocks = config["stocks"]
    
    results = []
    sector_summary = {"up": 0, "down": 0, "total_chg": 0, "total_amount": 0}
    
    for code, cfg in stocks.items():
        df = fetch_sina_daily(code, n=250)
        if df is None or len(df) < 60:
            log.warning(f"{cfg['name']}({code}) 历史数据获取失败")
            continue
        
        sig = calc_technical_signals(df)
        if "error" in sig:
            continue
        
        # 基本面催化剂检查 (简易：检查近期公告/新闻)
        catalysts = check_catalysts(code, cfg)
        
        # 汇率影响
        fx_rates = fetch_fx_rates()
        fx_impact = calc_fx_impact(cfg, fx_rates)
        
        # 综合评分
        score = calc_composite_score(sig, cfg, catalysts, fx_impact)
        
        result = {
            "symbol": code, "name": cfg["name"],
            **sig,
            "catalysts": catalysts,
            "fx_impact_bps": fx_impact.get("total_bps", 0),
            "composite_score": score["score"],
            "score_breakdown": score["breakdown"],
            "action": score["action"],
            "stop_loss": cfg["stop_loss_pct"],
            "take_profit": cfg["take_profit_pct"],
            "position_limit": cfg["position_limit_pct"],
            "strategy_bias": cfg["strategy_bias"]
        }
        results.append(result)
        
        # 板块汇总
        sector_summary["total_chg"] += sig["change_pct"]
        sector_summary["total_amount"] += sig.get("amount", 0)
        if sig["change_pct"] > 0: sector_summary["up"] += 1
        else: sector_summary["down"] += 1
    
    sector_summary["avg_chg"] = round(sector_summary["total_chg"] / max(1, len(results)), 2)
    sector_summary["stocks_count"] = len(results)
    
    # 排序输出
    results.sort(key=lambda x: x["composite_score"], reverse=True)
    
    output = {
        "mode": "eod",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "board": "电网装备/特高压出海",
        "sector_summary": sector_summary,
        "stocks": results,
        "macro_drivers": config["sector_macro"]["drivers"],
        "macro_risks": config["sector_macro"]["risks"]
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))

def check_catalysts(code: str, cfg: dict) -> List[dict]:
    """检查近期催化剂事件 (简易版：基于配置的催化剂日历)"""
    catalysts = []
    cal = cfg.get("catalyst_calendar", {})
    today = datetime.now()
    for quarter, events in cal.items():
        for ev in events:
            catalysts.append({"quarter": quarter, "event": ev, "status": "pending"})
    return catalysts

def calc_composite_score(sig: dict, cfg: dict, catalysts: List[dict], fx_impact: dict) -> dict:
    """综合评分：技术面(40%) + 基本面/催化剂(30%) + 汇率/宏观(20%) + 风控(10%)"""
    score = 0
    breakdown = {}
    
    # 技术面 (40分)
    tech_score = 0
    if sig["bull_arrange"]: tech_score += 10; breakdown["多头排列"] = 10
    if sig["above_ema200"]: tech_score += 8; breakdown["站上年线"] = 8
    if sig["above_ema50"]: tech_score += 6; breakdown["站上半年线"] = 6
    if sig["macd_above_zero"]: tech_score += 8; breakdown["MACD零轴上"] = 8
    if sig["macd_cross_up"]: tech_score += 8; breakdown["MACD金叉"] = 8
    if sig["vol_ratio"] > 1.2 and sig["change_pct"] > 0: tech_score += 5; breakdown["量价配合"] = 5
    score += min(tech_score, 40)
    breakdown["技术面小计"] = min(tech_score, 40)
    
    # 基本面/催化剂 (30分)
    fund_score = 15  # 基础分：核心产品+出海资质双验证
    if cfg.get("revenue_overseas_pct", 0) > 0.3: fund_score += 5; breakdown["海外营收占比高"] = 5
    if len(cfg.get("key_orders", [])) > 0: fund_score += 5; breakdown["重大订单在手"] = 5
    if any("2026Q3" in c.get("quarter", "") or "2026Q4" in c.get("quarter", "") for c in catalysts):
        fund_score += 5; breakdown["近期催化剂密集"] = 5
    score += min(fund_score, 30)
    breakdown["基本面小计"] = min(fund_score, 30)
    
    # 汇率/宏观 (20分)
    macro_score = 10
    fx_bps = fx_impact.get("total_bps", 0)
    if fx_bps > 5: macro_score += 5; breakdown["汇率利好"] = 5
    elif fx_bps < -5: macro_score -= 5; breakdown["汇率利空"] = -5
    score += max(0, min(macro_score, 20))
    breakdown["宏观小计"] = max(0, min(macro_score, 20))
    
    # 风控 (10分)
    risk_score = 10
    if sig["price"] < sig.get("stop_long", 0): risk_score -= 5; breakdown["跌破ATR止损"] = -5
    if sig.get("vol_ratio", 0) > 3: risk_score -= 3; breakdown["异常放量风险"] = -3
    score += max(0, risk_score)
    breakdown["风控小计"] = max(0, risk_score)
    
    # 操作建议
    if score >= 70: action = "🟢 重点做多"
    elif score >= 55: action = "🟢 适量买入"
    elif score >= 40: action = "🟡 观望/低吸"
    elif score >= 25: action = "🟠 减仓/谨慎"
    else: action = "🔴 止损/回避"
    
    return {"score": round(score, 1), "breakdown": breakdown, "action": action}

def run_weekly_mode():
    """周末深度研报模式"""
    config = load_config()
    stocks = config["stocks"]
    
    print(f"\n{'='*60}")
    print(f"📊 电网装备/特高压出海板块 周度深度研报")
    print(f"日期: {datetime.now().strftime('%Y-%m-%d')}")
    print(f"{'='*60}\n")
    
    # 1. 板块宏观综述
    print("【宏观驱动因子】")
    for d in config["sector_macro"]["drivers"]:
        print(f"  ✅ {d}")
    print("\n【核心风险因子】")
    for r in config["sector_macro"]["risks"]:
        print(f"  ⚠️ {r}")
    
    # 2. 个股深度
    print("\n【个股深度画像】")
    for code, cfg in stocks.items():
        df = fetch_sina_daily(code, n=250)
        if df is None or len(df) < 60:
            continue
        sig = calc_technical_signals(df)
        if "error" in sig: continue
        
        print(f"\n  📈 {cfg['name']} ({code})")
        print(f"     核心产品: {', '.join(cfg['core_products'][:3])}...")
        print(f"     出海资质: {', '.join(cfg['overseas_qualifications'][:3])}...")
        print(f"     重点订单: {', '.join(cfg['key_orders'][:2])}")
        print(f"     目标市场: {', '.join(cfg['target_markets'][:3])}")
        print(f"     海外营收占比: {cfg['revenue_overseas_pct']*100:.0f}%")
        print(f"     汇率敞口: {cfg['fx_exposure']}")
        print(f"     技术面: 价格{sig['price']} 涨跌{sig['change_pct']}% "
              f"EMA20/50/200:{'多' if sig['bull_arrange'] else '空'} "
              f"MACD:{'金叉' if sig['macd_cross_up'] else ('死叉' if sig['macd_cross_down'] else '平')}")
        print(f"     止损/止盈: {cfg['stop_loss_pct']}% / {cfg['take_profit_pct']}%")
        print(f"     策略偏向: {cfg['strategy_bias']}")
    
    # 3. 关键监测指标
    print("\n【下周关键监测指标】")
    for ind in config["sector_macro"]["key_indicators_to_watch"]:
        print(f"  🔍 {ind}")

def _check_eod_window() -> bool:
    """收盘复盘模式时间窗口守卫：仅工作日 15:00-16:00 允许执行"""
    now = datetime.now()
    if not (now.weekday() < 5 and dtime(15, 0) <= now.time() <= dtime(16, 0)):
        print(f"⏭️ 非 EOD 执行窗口 ({now.strftime('%H:%M')})，退出。配置窗口：工作日 15:00-16:00")
        return False
    return True


def _check_intraday_window() -> bool:
    """盘中预警模式时间窗口守卫：仅交易时段 9:30-11:30, 13:00-15:00 允许执行"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    if not ((dtime(9, 30) <= t <= dtime(11, 30)) or (dtime(13, 0) <= t <= dtime(15, 0))):
        print(f"⏭️ 非盘中预警窗口 ({now.strftime('%H:%M')})，退出。配置窗口：交易时段 9:30-11:30 / 13:00-15:00")
        return False
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="电网装备/特高压出海板块专项监控")
    parser.add_argument("--mode", choices=["intraday", "eod", "weekly"], default="eod",
                        help="运行模式: intraday=盘中预警, eod=收盘复盘, weekly=周末研报")
    args = parser.parse_args()
    
    if args.mode == "intraday":
        if not _check_intraday_window():
            sys.exit(0)
        run_intraday_mode()
    elif args.mode == "eod":
        if not _check_eod_window():
            sys.exit(0)
        run_eod_mode()
    elif args.mode == "weekly":
        # 周末研报无时间窗口限制
        run_weekly_mode()
