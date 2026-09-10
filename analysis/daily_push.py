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

WORKSPACE = "/Users/duguke/.openclaw/workspace"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

from send_telegram import send_with_retry


def run_and_push():
    # 运行 close_scan_v2 并捕获 JSON 输出
    result = subprocess.run(
        [sys.executable, "analysis/close_scan_v2.py"],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        timeout=120
    )
    
    if result.returncode != 0:
        error_msg = f"❌ close_scan_v2 执行失败:\n{result.stderr}"
        print(error_msg)
        send_with_retry(error_msg, parse_mode='')
        return False
    
    # 提取 JSON (在 =====PORTFOLIO_SIM_RESULT===== 和 =====PORTFOLIO_SIM_END===== 之间，或直接是整个输出)
    output = result.stdout.strip()
    
    # 尝试解析 JSON
    try:
        # 查找 JSON 开始位置
        start = output.find('{')
        if start >= 0:
            json_str = output[start:]
            data = json.loads(json_str)
        else:
            raise ValueError("输出中未找到 JSON")
    except json.JSONDecodeError as e:
        error_msg = f"❌ JSON 解析失败: {e}\n输出前500字符: {output[:500]}"
        print(error_msg)
        send_with_retry(error_msg, parse_mode='')
        return False
    
    # 生成简报文本
    summary = format_summary(data)
    
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
    
    if not is_td:
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
    
    lines.append("---")
    lines.append("完整 JSON 见控制台输出或日志")
    
    return "\n".join(lines)


if __name__ == "__main__":
    success = run_and_push()
    sys.exit(0 if success else 1)