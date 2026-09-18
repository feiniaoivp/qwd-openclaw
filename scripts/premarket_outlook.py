#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘前大盘展望自动更新 — command cron 直跑 (绕开 LLM，防超时/防 fallback 崩)
==========================================================================
每个交易日 08:30 运行:
  1. 拉取隔夜/最新指数数据(上证/深成/创业板/沪深300/中证500) + 技术指标
  2. 拉取最新新闻(宏观+个股) 提取关键资讯
  3. 读取 030 风控状态(健康度/仓位计划/致命风险) + 模拟盘持仓
  4. 结合 `data/market_levels.json`(人工维护的关键位) 生成简报
  5. 纯文本推送 Telegram (parse_mode='' 规避实体 bug)

用法: python3 scripts/premarket_outlook.py
输出: 0 = 正常推送; 非0 = 出错
"""
import os
import sys
import json
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)

LEVELS_FILE = os.path.join(WORKSPACE, "data", "market_levels.json")
OUT_FILE = os.path.join(WORKSPACE, "data", "premarket_outlook_output.json")

INDEX_LIST = [
    ("sh.000001", "上证指数"),
    ("sz.399001", "深证成指"),
    ("sz.399006", "创业板指"),
    ("sh.000300", "沪深300"),
    ("sh.000905", "中证500"),
]


def fmt_vol(v):
    try:
        return f"{float(v)/1e8:.0f}亿"
    except Exception:
        return "-"


def get_index_snapshot():
    """指数快照 + 技术位"""
    out = []
    try:
        from analysis.data_layer import get_router
        import pandas_ta as ta
        r = get_router()
        for code, name in INDEX_LIST:
            try:
                df = r.baostock.get_index_daily(code, "2026-05-01", "2026-12-31", adjustflag="3")
                if df is None or len(df) < 21:
                    continue
                c = df["close"]
                last = float(c.iloc[-1])
                prev = float(c.iloc[-2])
                chg = (last / prev - 1) * 100
                ma5 = float(c.rolling(5).mean().iloc[-1])
                ma20 = float(c.rolling(20).mean().iloc[-1])
                rsi = float(ta.rsi(c, length=14).iloc[-1]) if len(c) > 15 else 50.0
                out.append({
                    "name": name, "close": round(last, 2), "chg": round(chg, 2),
                    "ma5": round(ma5, 2), "ma20": round(ma20, 2), "rsi": round(rsi, 1),
                    "vol": fmt_vol(df["volume"].iloc[-1]),
                    "date": str(df["date"].iloc[-1].date()),
                })
            except Exception as e:
                print(f"  {name} 获取失败: {e}", file=sys.stderr)
    except Exception as e:
        print(f"指数快照失败: {e}", file=sys.stderr)
    return out


def get_risk_state():
    try:
        from analysis.portfolio_core import RiskGuard
        RiskGuard.refresh()
        fatal = RiskGuard.get_fatal_risk() or {}
        pp = RiskGuard.get_position_plan() or {}
        mc = RiskGuard.get_market_context() or {}
        return {
            "health": mc.get("health_score"),
            "stage": mc.get("market_stage"),
            "cycle": mc.get("emotion_cycle"),
            "fatal": bool(fatal.get("fatal_triggered")),
            "high": bool(fatal.get("high_risk_triggered")),
            "total_limit_pct": pp.get("total_limit_pct"),
            "single_pct": pp.get("single_stock_max_pct"),
            "mult": pp.get("buy_signal_multiplier"),
            "warnings": pp.get("risk_warnings", []),
        }
    except Exception as e:
        print(f"风控状态失败: {e}", file=sys.stderr)
        return {}


def get_positions():
    try:
        sf = os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")
        with open(sf, encoding="utf-8") as f:
            st = json.load(f)
        held = []
        for sym, p in st.get("positions", {}).items():
            if p.get("position"):
                held.append({
                    "symbol": sym, "name": p.get("name", sym),
                    "shares": p.get("shares"), "entry": p.get("entry_price"),
                    "atr_stop": p.get("atr_stop"), "strategy": p.get("strategy"),
                })
        return held
    except Exception:
        return []


def get_news(n=14):
    """最新市场相关快讯"""
    try:
        from analysis.data_layer import get_router
        r = get_router()
        df = r.get_global_news()
        if df is None:
            return []
        kw = ["美联储", "加息", "降息", "A股", "大盘", "沪指", "央行", "流动性", "北向",
              "IPO", "关税", "汇率", "人民币", "机构", "策略", "仓位", "指数", "解禁",
              "利好", "利空", "政策", "监管", "证监会"]
        out = []
        for _, row in df.iterrows():
            t = str(row.get("标题") or "")
            if any(k in t for k in kw):
                out.append({"time": str(row.get("发布时间") or "")[11:16] or str(row.get("发布时间") or ""), "title": t})
            if len(out) >= n:
                break
        return out
    except Exception as e:
        print(f"新闻获取失败: {e}", file=sys.stderr)
        return []


def load_levels():
    if os.path.exists(LEVELS_FILE):
        try:
            with open(LEVELS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def build_message():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    idx = get_index_snapshot()
    risk = get_risk_state()
    pos = get_positions()
    news = get_news(12)
    levels = load_levels()

    L = [f"📊 盘前展望 ({now})", "━━━━━━━━━━━━━━━━━━━━"]

    # 指数
    if idx:
        L.append("【指数 (最新收盘)】")
        for d in idx:
            pos_word = "站上" if d["close"] > d["ma20"] else "跌破"
            L.append(f"  {d['name']} {d['close']} ({d['chg']:+.2f}%) 量{d['vol']}")
            L.append(f"    MA5 {d['ma5']} / MA20 {d['ma20']} · {pos_word}MA20 · RSI {d['rsi']}")
        L.append("")

    # 关键位
    if levels:
        L.append("【关键位】")
        if levels.get("support"):
            L.append(f"  支撑: {' / '.join(str(x) for x in levels['support'])}")
        if levels.get("resistance"):
            L.append(f"  压力: {' / '.join(str(x) for x in levels['resistance'])}")
        if levels.get("pivot"):
            L.append(f"  分水岭: {levels['pivot']}")
        L.append("")

    # 风控
    if risk:
        L.append("【030风控】")
        L.append(f"  健康度 {risk.get('health')}/10 · {risk.get('stage')} · {risk.get('cycle')}")
        fatal = "☠️致命风险" if risk.get("fatal") else ("🟡高风险" if risk.get("high") else "✅无风险触发")
        L.append(f"  {fatal}")
        L.append(f"  仓位上限 {risk.get('total_limit_pct', 0):.0%} · 单股 {risk.get('single_pct', 0):.0%} · 乘数 {risk.get('mult')}x")
        for w in risk.get("warnings", [])[:2]:
            L.append(f"  {w}")
        L.append("")

    # 持仓
    L.append("【模拟盘持仓】")
    if pos:
        for p in pos:
            stop = p.get("atr_stop")
            L.append(f"  {p['name']}({p['symbol']}) {p['shares']}股 @{p['entry']}"
                     + (f" · 止损{stop:.2f}" if stop else ""))
    else:
        L.append("  空仓")
    L.append("")

    # 新闻
    if news:
        L.append("【隔夜/最新资讯】")
        for n in news:
            L.append(f"  · {n['title'][:60]}")
        L.append("")

    L.append("⚠️ 数据仅供参考，不构成投资建议")
    return "\n".join(L)


def main():
    msg = build_message()
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now().isoformat(), "brief": msg}, f,
                  ensure_ascii=False, indent=2)
    from send_telegram import send_message
    resp = send_message(msg, parse_mode="")
    if isinstance(resp, dict) and resp.get("ok"):
        print("已推送盘前展望")
        return 0
    print(f"推送失败: {resp}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
