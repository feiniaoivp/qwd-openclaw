#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
买入档资金流明细 × 自适应策略信号叠加（2026-10-16）

对当日 close_scan 买入档（强烈买入 + 关注）逐票：
  1) 拉 datacenter-web 资金流明细（push2 全挂时的兜底通道，仅当日、无中/小单拆分）
  2) 叠加自适应策略信号（17:40 adaptive.md 卖出名单 + signal_macd 收盘数据单算）
  3) 落盘 analysis/daily/{date}_buy{N}_adaptive_moneyflow.md

用法:
    python3 analysis/buy_tier_overlay.py [--date 2026-10-15]

来源标注:
    - 信号: data/pipeline_cache/close_scan_{date}.json（18:31 cron 落盘）
    - 资金流: datacenter-web RPT_DMSK_TS_STOCKNEW（eastmoney_dc）
    - 自适应: analysis/daily/{date}_adaptive.md + adaptive_trader.signal_macd
"""
import os
import re
import sys
import json
import glob
import logging
import argparse
from typing import Optional

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
# 必须同时插入：工作区根 + analysis/（脚本模式 sys.path 样板）
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("buy_tier_overlay")


def latest_close_scan(date: Optional[str] = None) -> Optional[str]:
    """定位 close_scan json：优先指定日期，否则取 pipeline_cache 最新一份。"""
    if date:
        p = os.path.join(WORKSPACE, "data", "pipeline_cache", f"close_scan_{date}.json")
        return p if os.path.exists(p) else None
    files = sorted(glob.glob(os.path.join(WORKSPACE, "data", "pipeline_cache", "close_scan_*.json")))
    return files[-1] if files else None


def load_buy_tier(scan_path: str):
    """从 close_scan json 提取买入档（强烈买入 + 关注）票。"""
    with open(scan_path, encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("watchlist_scan") or []
    buys = []
    for it in rows:
        sig = it.get("signal") or {}
        level = str(sig.get("level", ""))
        if ("强烈买入" in level) or ("关注" in level and "🟢" in level):
            buys.append({
                "symbol": it.get("symbol"),
                "name": it.get("name"),
                "price": it.get("price"),
                "change_pct": it.get("change_pct"),
                "level": level.replace("🟢", "").strip(),
                "score": sig.get("score"),
                "reasons": sig.get("reasons", ""),
                "risks": sig.get("risks", ""),
            })
    return data.get("date"), buys


def parse_adaptive_sell_list(date: str):
    """解析 adaptive.md 卖出表格 → {code: {strategy, price, reason, stop}}"""
    path = os.path.join(WORKSPACE, "analysis", "daily", f"{date}_adaptive.md")
    out = {}
    if not os.path.exists(path):
        log.warning("adaptive.md 不存在: %s", path)
        return out, False
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(
                r"\|\s*(.+?)\((\d{6})\)\s*\|\s*(.+?)\s*\|\s*¥([\d.]+)\s*\|"
                r"\s*[-+][\d.]+%\s*\|(.*?)\|\s*([^|]*?)\s*\|\s*$", line)
            if m:
                name, code, strategy, price, reason, stop = m.groups()
                out[code] = {
                    "name": name.strip(), "strategy": strategy.strip(),
                    "price": float(price),
                    "reason": reason.strip(" |"), "stop": stop.strip(),
                }
    return out, True


def adaptive_signal(code: str, name: str, sell_map: dict, covered27: bool):
    """自适应信号：卖出名单优先（17:40 权威文本），否则收盘数据单算 signal_macd。"""
    if code in sell_map:
        s = sell_map[code]
        return {
            "source": "adaptive.md(17:40)",
            "action": "🔴卖出", "strategy": s["strategy"],
            "reason": s["reason"], "atr_stop": s["stop"],
        }
    if not covered27:
        pool_note = "27池外,按映射策略单算"
    else:
        pool_note = "27池内(17:40扫描持有)"
    # 不在卖出名单 → 单算（分配策略映射从 strategy_map 读）
    try:
        from analysis.adaptive_trader import get_daily_hist, signal_macd, load_strategy_map
        strategy = load_strategy_map().get(code, "macd")
        if strategy != "macd":
            return {"source": pool_note, "action": f"映射策略 {strategy} 待接入单算",
                    "strategy": strategy, "reason": "-", "atr_stop": "-"}
        df = get_daily_hist(code, name)
        if df is None or len(df) < 30:
            log.warning("%s(%s) 日线获取不足，自适应单算跳过", name, code)
            return {"source": pool_note, "action": "数据不足", "strategy": strategy, "reason": "-", "atr_stop": "-"}
        r = signal_macd(df, code)
        return {"source": f"单算·{pool_note}", "action": r.get("action", "?"),
                "strategy": "纯MACD", "reason": r.get("reason", "-"),
                "atr_stop": r.get("atr_stop", "-"), "key_indicators": r.get("key_indicators", "")}
    except Exception as e:  # noqa: BLE001
        log.warning("自适应单算失败 %s(%s): %s", name, code, e)
        return {"source": pool_note, "action": "失败", "strategy": "?", "reason": str(e), "atr_stop": "-"}


def moneyflow_detail(codes):
    """datacenter 原始行 → 资金流明细（降级口径显式标注）。"""
    from analysis.moneyflow_em import dc_refresh_batch, _dc_lookup
    n = dc_refresh_batch(codes)
    log.info("datacenter 预热命中 %d/%d", n, len(codes))
    out = {}
    for c in codes:
        row = _dc_lookup(c)
        if not row:
            out[c] = None
            log.warning("datacenter 无 %s 当日行", c)
            continue
        def f(k):
            v = row.get(k)
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None
        super_in, super_out = f("SUPERDEAL_INFLOW"), f("SUPERDEAL_OUTFLOW")
        big_in, big_out = f("BIGDEAL_INFLOW"), f("BIGDEAL_OUTFLOW")
        prime = f("PRIME_INFLOW")
        out[c] = {
            "close": f("CLOSE_PRICE"), "change_rate": f("CHANGE_RATE"),
            "turnover_rate": f("TURNOVERRATE"),
            "super_net": (super_in - super_out) if (super_in is not None and super_out is not None) else None,
            "super_in": super_in, "super_out": super_out,
            "big_net": (big_in - big_out) if (big_in is not None and big_out is not None) else None,
            "big_in": big_in, "big_out": big_out,
            "prime_inflow": prime,
            "buy_super_ratio": f("BUY_SUPERDEAL_RATIO"),
            "buy_big_ratio": f("BUY_BIGDEAL_RATIO"),
            "prime_cost": f("PRIME_COST"),
            "trade_date": str(row.get("TRADE_DATE", ""))[:10],
        }
    return out


def yi(v):
    """元 → 亿元字符串"""
    if v is None:
        return "—"
    return f"{v / 1e8:+.2f}亿"


def render_md(date, buys, flows, adaptive, scan_path, push2_down: bool):
    L = []
    L.append(f"# 买入档资金流明细 × 自适应信号叠加（{date}）")
    L.append("")
    L.append(f"- 信号源: close_scan json [来源: close_scan_v2, {os.path.relpath(scan_path, WORKSPACE)}]")
    L.append("- 自适应: 17:40 adaptive.md 卖出名单 + signal_macd 收盘数据单算 [来源: adaptive_trader]")
    if push2_down:
        L.append("- 资金流口径: ⚠️ 降级 — push2/push2his 全挂，走 datacenter-web 龙虎榜资金表"
                 " [来源: eastmoney_dc, RPT_DMSK_TS_STOCKNEW]；仅当日快照、仅超大单/大单拆分、无中/小单、无 main_pct")
    else:
        L.append("- 资金流口径: push2 实时通道 [来源: eastmoney_dc]")
    L.append("")
    L.append("| 股票 | close_scan信号 | 自适应信号(策略) | 现价 | 超大单净 | 大单净 | 主力净 | 换手 | 超大买占比 | ATR止损 |")
    L.append("|:---|:---|:---|---:|---:|---:|---:|---:|---:|---:|")
    for b in buys:
        c = b["symbol"]
        fl = flows.get(c) or {}
        ad = adaptive.get(c) or {}
        L.append(
            f"| {b['name']}({c}) | {b['level']} | {ad.get('action','-')}({ad.get('strategy','-')})"
            f" | ¥{b['price']} | {yi(fl.get('super_net'))} | {yi(fl.get('big_net'))}"
            f" | {yi(fl.get('prime_inflow'))} | {fl.get('turnover_rate','—') if fl.get('turnover_rate') is not None else '—'}%"
            f" | {fl.get('buy_super_ratio','—')} | {ad.get('atr_stop','-')} |")
    L.append("")
    L.append("## 逐票明细与分歧解读")
    L.append("")
    for b in buys:
        c = b["symbol"]
        fl = flows.get(c) or {}
        ad = adaptive.get(c) or {}
        L.append(f"### {b['name']}({c}) — close_scan「{b['level']}」 / 自适应「{ad.get('action','-')}」")
        L.append(f"- close_scan 理由: {b['reasons'] or '-'}；风险: {b['risks'] or '-'}")
        L.append(f"- 自适应({ad.get('source','-')}): {ad.get('reason','-')}"
                 + (f"｜关键指标: {ad.get('key_indicators','')}" if ad.get("key_indicators") else ""))
        if fl:
            L.append(f"- 超大单: 流入 {yi(fl.get('super_in'))} / 流出 {yi(fl.get('super_out'))} → 净 {yi(fl.get('super_net'))}")
            L.append(f"- 大单: 流入 {yi(fl.get('big_in'))} / 流出 {yi(fl.get('big_out'))} → 净 {yi(fl.get('big_net'))}")
            L.append(f"- 主力净流入合计: {yi(fl.get('prime_inflow'))}；主力成本参考: {round(fl['prime_cost'], 2) if fl.get('prime_cost') is not None else '—'}"
                     f"（现价 ¥{b['price']}）")
            if fl.get("trade_date") and fl["trade_date"] != date:
                L.append(f"- ⚠️ 资金流数据日期 {fl['trade_date']} ≠ 信号日期 {date}")
        else:
            L.append("- 资金流明细获取失败（datacenter 无当日行）")
        if str(ad.get("action", "")).startswith("🔴") and str(b["level"]).find("买入") >= 0:
            L.append(f"- ⚠️ 分歧: close_scan 给「{b['level']}」但自适应转卖出 — 以自适应纪律为准（参照越秀资本先例）")
        L.append("")
    L.append("---")
    L.append("以上为技术面叠加分析，不构成投资建议。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="数据日期 YYYY-MM-DD，默认最新 close_scan")
    args = ap.parse_args()

    scan_path = latest_close_scan(args.date)
    if not scan_path:
        log.error("找不到 close_scan json（date=%s）", args.date)
        return 1
    date, buys = load_buy_tier(scan_path)
    if not buys:
        log.error("close_scan 中无买入档票")
        return 1
    log.info("买入档 %d 只: %s", len(buys), ", ".join(f"{b['name']}({b['symbol']})" for b in buys))

    codes = [b["symbol"] for b in buys]
    flows = moneyflow_detail(codes)

    sell_map, _ = parse_adaptive_sell_list(date)
    # 27 只核心池判定（adaptive_trader.STOCKS）
    try:
        from analysis.adaptive_trader import STOCKS
        pool27 = {c for c, _n in STOCKS}
    except Exception as e:  # noqa: BLE001
        log.warning("读取 adaptive_trader.STOCKS 失败: %s", e)
        pool27 = set()
    adaptive = {}
    for b in buys:
        adaptive[b["symbol"]] = adaptive_signal(b["symbol"], b["name"], sell_map, b["symbol"] in pool27)

    push2_down = True  # 凌晨探测仍挂；moneyflow_detail 若走 push2 会在 stock_flow 内部先试，此处按兜底口径标注
    md = render_md(date, buys, flows, adaptive, scan_path, push2_down)
    out_dir = os.path.join(WORKSPACE, "analysis", "daily")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{date}_buy{len(buys)}_adaptive_moneyflow.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
    log.info("落盘: %s", out_path)

    # stdout 摘要
    print("=" * 30, "SUMMARY", "=" * 30)
    for b in buys:
        c, fl, ad = b["symbol"], flows.get(b["symbol"]) or {}, adaptive[b["symbol"]]
        print(f"{b['name']}({c}) {b['level']} | 自适应:{ad['action']}({ad['strategy']}) "
              f"| 主力净 {yi(fl.get('prime_inflow'))} 超大 {yi(fl.get('super_net'))} 大单 {yi(fl.get('big_net'))} "
              f"| 换手 {fl.get('turnover_rate', '—')}%")
    print(f"报告: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
