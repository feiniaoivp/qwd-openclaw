#!/usr/bin/env python3
"""
每日收盘推送脚本 (Command 模式)
==================================
直接运行 close_scan_v2.py 并通过 send_telegram 推送结果。
用于 command cron 直连，避免 LLM 子进程 503 阻断。
"""

import sys
import json
import subprocess
import os
from datetime import datetime, timedelta

WORKSPACE = "/Users/duguke/.openclaw/workspace"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

from send_telegram import send_with_retry


# ── 2026-10-11 新增：产物落盘 + 数据滞后可见化 ─────────────────────────────
# 背景：收盘扫描此前只推 Telegram 不落盘，T 日收盘信号无档案可查
# （10-11 晨引用 10-09_dual.md 时发现其指标实际只到 10-08，被迫手动重跑全池）。


def _expected_latest_trading_date() -> str:
    """最近一个交易日（含今天）。交易日判定走 analysis.service 单一来源。"""
    from analysis.service import is_trading_day

    d = datetime.now()
    for _ in range(10):
        ds = d.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            return ds
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def check_staleness(data: dict) -> str:
    """数据滞后校验：滞后时返回警告前缀文本，正常返回空串。"""
    warnings = []
    expected = _expected_latest_trading_date()
    data_date = data.get("data_date") or ""
    if data_date and data_date != expected:
        warnings.append(
            f"⚠️ 行情日期滞后: 预期 {expected}，实际 {data_date}（指标口径以 {data_date} 为准）"
        )
    # K线滞后检测：spot 收盘 与 hist 最后一根收盘 不一致的占比
    # （baostock 当日K线未更新时，指标会静默退回 T-1 口径，必须显式告警）
    rows = data.get("watchlist_scan") or []
    total = mism = 0
    for r in rows:
        ind = r.get("indicators") or {}
        hp = (ind.get("ema_trend") or {}).get("price")
        sp = r.get("price")
        if hp and sp:
            total += 1
            if abs(sp - hp) / hp > 0.001:
                mism += 1
    if total and mism / total > 0.3:
        warnings.append(
            f"⚠️ K线滞后: {mism}/{total} 只 K线收盘不等于spot收盘（指标可能仍基于 T-1，信号降权看待）"
        )
    # 2026-10-13: 单票价格 sanity——spot价 与 K线价 偏离>25% 直接告警。
    # 10-12 事故: 601061 坏K线 15.82 vs 真实 10.80(偏离46%), 整体不一致占比仅 1/36,
    # 未达上面 30% 旧阈值而漏网; 单票级坏数据必须逐票拦截。
    bad = []
    for r in rows:
        ind = r.get("indicators") or {}
        hp = (ind.get("ema_trend") or {}).get("price")
        sp = r.get("price")
        if hp and sp and abs(sp - hp) / hp > 0.25:
            bad.append(f"{r.get('symbol', '')} {r.get('name', '')}: 报告价 {sp} vs K线 {hp}")
    if bad:
        warnings.append(
            "🔴 单票价格异常(偏离>25%, 数据源疑似损坏, 信号不可信): " + "; ".join(bad[:5])
        )
    return ("\n".join(warnings) + "\n\n") if warnings else ""


def persist_artifacts(data: dict, summary_text: str) -> list:
    """落盘收盘扫描产物（md 简报 + slim JSON），返回写入路径列表。"""
    data_date = data.get("data_date") or data.get("date") or datetime.now().strftime("%Y-%m-%d")
    daily_dir = os.path.join(WORKSPACE, "analysis", "daily")
    os.makedirs(daily_dir, exist_ok=True)
    slim = {
        "data_date": data.get("data_date"),
        "scan_run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_source": data.get("data_source"),
        "summary": data.get("summary"),
        "market_overview": data.get("market_overview"),
        "moneyflow_degraded": bool((data.get("moneyflow") or {}).get("degraded")),
        "stocks": [
            {
                "symbol": r.get("symbol"),
                "name": r.get("name"),
                "price": r.get("price"),
                "change_pct": r.get("change_pct"),
                "signal": r.get("signal"),
                "scan_error": r.get("error"),
                "trend": (r.get("indicators") or {}).get("ema_trend", {}).get("trend"),
                "stop_long": (r.get("indicators") or {}).get("atr", {}).get("stop_long"),
                "support": (r.get("indicators") or {}).get("support_resistance", {}).get("support_recent"),
                "resistance": (r.get("indicators") or {}).get("support_resistance", {}).get("resistance_recent"),
                "vol_ratio": (r.get("indicators") or {}).get("volume", {}).get("vol_ratio"),
            }
            for r in (data.get("watchlist_scan") or [])
        ],
    }
    paths = []
    md_path = os.path.join(daily_dir, f"{data_date}_close_scan.md")
    header = (
        f"# 收盘全池扫描 {data_date}\n\n"
        f"- 数据日期: {data.get('data_date')} | 数据源: {data.get('data_source')}\n"
        f"- 生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}（daily-a-share-telegram-push 18:30 cron 落盘）\n\n"
    )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(header + summary_text + "\n")
    paths.append(md_path)
    json_path = os.path.join(daily_dir, f"{data_date}_close_scan.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=1)
    paths.append(json_path)
    # 2026-10-11(#1): 全量 JSON 落盘到 pipeline_cache，供次日盘前 scan 阶段直接
    # 消费——06:00 开盘前 spot=昨收、K线=到昨收，与昨夜 18:30 扫描数据完全等价，
    # 复用可省一次全池重扫并消除盘前数据源抖动。保留最近 10 份。
    cache_dir = os.path.join(WORKSPACE, "data", "pipeline_cache")
    os.makedirs(cache_dir, exist_ok=True)
    full_path = os.path.join(cache_dir, f"close_scan_{data_date}.json")
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    paths.append(full_path)
    try:
        import glob as _glob
        old = sorted(_glob.glob(os.path.join(cache_dir, "close_scan_*.json")))
        for p in old[:-10]:
            os.remove(p)
    except Exception:  # noqa: BLE001
        pass
    return paths


def run_and_push():
    # 运行 close_scan_v2 并捕获 JSON 输出
    result = subprocess.run(
        [sys.executable, "analysis/close_scan_v2.py"],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        # 2026-10-06: 实测 close_scan_v2 全池扫描 363.7s（资金流占 2m18s），
        # 原写死 120s 导致 command cron 必超时。720s 留足余量且低于 job 级 900s。
        timeout=720
    )
    
    if result.returncode != 0:
        error_msg = f"❌ close_scan_v2 执行失败:\n{result.stderr}"
        print(error_msg)
        send_with_retry(error_msg, parse_mode='')
        return False
    
    # 提取 JSON：close_scan_v2 在 =====CLOSE_SCAN_RESULT===== 与 =====CLOSE_SCAN_END===== 之间输出主体 JSON，
    # 后续还可能拼接其他结果块，不能用 find('{') 取到末尾（会 Extra data）。
    output = result.stdout
    marker_re = r"=====CLOSE_SCAN_RESULT=====(.*?)=====CLOSE_SCAN_END====="
    m = __import__("re").search(marker_re, output, __import__("re").DOTALL)
    try:
        if m:
            data = json.loads(m.group(1).strip())
        else:
            # 兼容旧格式：单 JSON 块直接截到末尾
            start = output.find('{')
            if start >= 0:
                data = json.loads(output[start:].strip())
            else:
                raise ValueError("输出中未找到 JSON 或 CLOSE_SCAN 标记")
    except json.JSONDecodeError as e:
        error_msg = f"❌ JSON 解析失败: {e}\n输出前500字符: {output[:500]}"
        print(error_msg)
        send_with_retry(error_msg, parse_mode='')
        return False
    
    # 生成简报文本
    summary = format_summary(data)

    # 2026-10-11: 先落盘再推送（推送失败也有档案），随后附加滞后告警。
    try:
        persisted = persist_artifacts(data, summary)
        print(f"已落盘: {persisted}")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 落盘失败(不影响推送): {e}")

    staleness = check_staleness(data)
    if staleness:
        summary = staleness + summary

    # 发送到 Telegram (纯文本模式)
    send_result = send_with_retry(summary, parse_mode='')
    print(f"Telegram 发送结果: {send_result.get('ok')}")
    
    return send_result.get('ok', False)


def format_summary(data: dict) -> str:
    """格式化每日收盘简报 (纯文本)"""
    lines = []
    
    date = data.get('date', '')
    is_td = data.get('is_trading_day', True)
    data_date = data.get('data_date', '')
    source = data.get('data_source', '')
    
    # 2026-10-11: 非交易日但扫描仍有数据（如周末手动 force）时照常渲染，
    # 只有真正空扫描才跳过 —— 避免有档案数据却推“跳过”造成误导。
    if not is_td and not data.get('watchlist_scan'):
        return f"📅 {date} 非交易日，跳过扫描"
    
    lines.append(f"📊 A股收盘扫描 {date}")
    lines.append(f"数据日期: {data_date} | 数据源: {source}")
    lines.append("")
    
    # 市场概览
    mo = data.get('market_overview', {})
    if mo:
        lines.append(f"📈 市场概览: 上涨 {mo.get('up',0)} 只 | 下跌 {mo.get('down',0)} 只 | 成交额 {mo.get('total_amount_billion',0):.1f}亿")
        lines.append("")
    
    # 汇总
    summary = data.get('summary', {})
    lines.append("🎯 信号汇总:")
    lines.append(f"  🟢 强烈买入: {summary.get('strong_buy',0)}")
    lines.append(f"  🟢 关注: {summary.get('watch',0)}")
    lines.append(f"  🟡 中性: {summary.get('neutral',0)}")
    lines.append(f"  🟠 谨慎: {summary.get('caution',0)}")
    lines.append(f"  🔴 强烈卖出: {summary.get('strong_sell',0)}")
    lines.append(f"  ❌ 失败: {summary.get('fail_count',0)}")
    lines.append("")
    
    # 强烈买入/卖出详情
    scan = data.get('watchlist_scan', [])
    strong_buys = [s for s in scan if s.get('signal',{}).get('level','').startswith('🟢')]
    strong_sells = [s for s in scan if s.get('signal',{}).get('level','').startswith('🔴')]
    
    if strong_buys:
        lines.append("🟢 强烈买入:")
        for s in strong_buys[:5]:
            sig = s.get('signal', {})
            lines.append(f"  {s.get('name')}({s.get('symbol')}) ¥{s.get('price')} {sig.get('level')} | {sig.get('reasons','')[:60]}")
        lines.append("")
    
    if strong_sells:
        lines.append("🔴 强烈卖出:")
        for s in strong_sells[:5]:
            sig = s.get('signal', {})
            lines.append(f"  {s.get('name')}({s.get('symbol')}) ¥{s.get('price')} {sig.get('level')} | {sig.get('risks','')[:60]}")
        lines.append("")
    
    # 中联重科
    zl = data.get('zhonglian')
    if zl and 'error' not in zl:
        lines.append("🏭 中联重科双策略:")
        pf = zl.get('portfolio', {})
        s1 = pf.get('strategy1', {})
        s2 = pf.get('strategy2', {})
        lines.append(f"  策略1(MACD+RSI<50): {'持仓' if s1.get('position') else '空仓'} 入场{s1.get('entry_price')} 现价{zl.get('price')} 资金{s1.get('capital')}")
        lines.append(f"  策略2(综合最优): {'持仓' if s2.get('position') else '空仓'} 入场{s2.get('entry_price')} 现价{zl.get('price')} 资金{s2.get('capital')}")
        lines.append(f"  总净值: ¥{pf.get('total_value',0):,.0f} ({pf.get('total_return_pct',0):+.2f}%)")
        if data.get('zhonglian_messages'):
            lines.append("  今日动作: " + " | ".join(data['zhonglian_messages']))
        lines.append("")
    
    # 盘中预警
    triggered = data.get('strategy_alert_triggered', [])
    if triggered:
        lines.append("🔔 盘中预警触发:")
        for a in triggered[:5]:
            lines.append(f"  {a.get('name')}({a.get('symbol')}) {a.get('level_name')}: {a.get('msg')}")
        lines.append("")

    # 资金流（东财）
    mf = data.get('moneyflow')
    if mf and not mf.get('error'):
        try:
            _adir = os.path.join(WORKSPACE, 'analysis')
            if _adir not in sys.path:
                sys.path.insert(0, _adir)
            from moneyflow_report import render_moneyflow, moneyflow_highlights
            lines.append(render_moneyflow(mf).rstrip())
            hl = moneyflow_highlights(mf)
            if hl:
                lines.append("")
                lines.append("💡 资金面要点:")
                for h in hl[:8]:
                    lines.append(f"  {h}")
            lines.append("")
        except Exception as e:  # noqa: BLE001
            lines.append(f"💰 资金流渲染失败: {e}")
            lines.append("")

    lines.append("---")
    lines.append("完整 JSON 见控制台输出或日志")

    return "\n".join(lines)


if __name__ == "__main__":
    success = run_and_push()
    sys.exit(0 if success else 1)
