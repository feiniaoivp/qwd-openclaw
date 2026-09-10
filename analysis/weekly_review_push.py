#!/usr/bin/env python3
"""
每周复盘推送脚本 (Command 模式)
===============================
执行周六复盘并通过 send_telegram 推送结果 (纯文本)。
"""

import sys
import json
import subprocess
from datetime import datetime, timedelta

WORKSPACE = "/Users/duguke/.openclaw/workspace"
sys.path.insert(0, WORKSPACE)

from send_telegram import send_with_retry


def run_weekly_review():
    """执行周六复盘逻辑"""
    today = datetime.now().strftime("%Y-%m-%d")
    week_start = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
    
    lines = []
    lines.append(f"📅 周六复盘 {today}")
    lines.append(f"周期: {week_start} ~ {today}")
    lines.append("")
    
    # 1. 读取模拟盘状态
    try:
        import os
        state_file = os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")
        trades_file = os.path.join(WORKSPACE, "data", "portfolio_sim_trades.json")
        
        if os.path.exists(state_file):
            with open(state_file) as f:
                state = json.load(f)
            
            positions = state.get("positions", {})
            held = [p for p in positions.values() if p.get("position")]
            
            lines.append(f"💼 模拟盘持仓: {len(held)}/{len(positions)} 只")
            lines.append("")
            
            if held:
                lines.append("📈 持仓明细 (按收益率):")
                held_sorted = sorted(held, key=lambda x: x.get("total_pl", 0), reverse=True)
                for p in held_sorted[:10]:
                    symbol = p.get("_symbol", "")
                    name = p.get("name", "")
                    pl = p.get("total_pl", 0)
                    pl_pct = 0
                    if p.get("entry_price"):
                        pl_pct = (pl / (p.get("shares", 1) * p.get("entry_price", 1))) * 100
                    lines.append(f"  {name}({symbol}): 盈亏 ¥{pl:+,.0f} ({pl_pct:+.2f}%) 策略:{p.get('strategy','')}")
                lines.append("")
        
        # 读取成交流水
        if os.path.exists(trades_file):
            with open(trades_file) as f:
                trades = json.load(f)
            
            week_trades = [t for t in trades if t.get("date", "").startswith(week_start[:7]) or t.get("date", "") >= week_start]
            buys = [t for t in week_trades if t.get("action") == "BUY"]
            sells = [t for t in week_trades if t.get("action") == "SELL"]
            
            lines.append(f"📊 本周成交: 买入 {len(buys)} 笔 | 卖出 {len(sells)} 笔")
            
            if sells:
                wins = [t for t in sells if t.get("pnl", 0) > 0]
                total_pl = sum(t.get("pnl", 0) for t in sells)
                lines.append(f"  卖出盈亏: ¥{total_pl:+,.0f} | 胜率 {len(wins)}/{len(sells)} ({len(wins)/len(sells)*100:.0f}%)")
            
            lines.append("")
            
    except Exception as e:
        lines.append(f"⚠️ 模拟盘数据读取失败: {e}")
        lines.append("")
    
    # 2. 策略信号准确率（简易版：读取最近几天的 close_scan 输出）
    try:
        daily_dir = os.path.join(WORKSPACE, "analysis", "daily")
        if os.path.exists(daily_dir):
            files = sorted([f for f in os.listdir(daily_dir) if f.endswith("_portfolio_sim.md") or f.endswith(".md")], reverse=True)[:5]
            if files:
                lines.append("📋 近期日报文件:")
                for f in files[:3]:
                    lines.append(f"  {f}")
                lines.append("")
    except Exception as e:
        lines.append(f"⚠️ 日报读取失败: {e}")
        lines.append("")
    
    # 3. 盘中预警记录
    try:
        alert_file = os.path.join(WORKSPACE, "data", "intraday_alert_state.json")
        if os.path.exists(alert_file):
            with open(alert_file) as f:
                alert_state = json.load(f)
            fired = alert_state.get("fired", {})
            if fired:
                lines.append(f"🔔 盘中预警记录: {len(fired)} 条")
                for k, v in list(fired.items())[:5]:
                    lines.append(f"  {k}: {v}")
            else:
                lines.append("🔔 盘中预警: 本周无触发")
            lines.append("")
    except Exception as e:
        lines.append(f"⚠️ 预警记录读取失败: {e}")
        lines.append("")
    
    # 4. 纪律执行/认知偏差/工具问题 (模板，供人工补充)
    lines.append("🔍 复盘清单 (需人工确认):")
    lines.append("  □ 纪律: 追涨杀跌 | 止损不果断 | 仓位超限 | 重仓相关性过高")
    lines.append("  □ 认知: 板块/风格固有偏见导致错失/误入")
    lines.append("  □ 工具: 数据源延迟 | 脚本报错 | 推送遗漏")
    lines.append("")
    
    # 5. 下周计划模板
    lines.append("📋 下周交易计划模板:")
    lines.append("  核心持仓梯队调整:")
    lines.append("  逐日执行清单:")
    lines.append("  风控参数微调:")
    lines.append("  重点关注事件:")
    lines.append("")
    
    lines.append("---")
    lines.append(f"归档文件: memory/{today}-weekly-review.md")
    
    return "\n".join(lines)


def main():
    summary = run_weekly_review()
    
    # 保存到 memory 目录
    today = datetime.now().strftime("%Y-%m-%d")
    memory_dir = os.path.join(WORKSPACE, "memory")
    os.makedirs(memory_dir, exist_ok=True)
    report_path = os.path.join(memory_dir, f"{today}-weekly-review.md")
    with open(report_path, "w") as f:
        f.write(f"# 周六复盘 {today}\n\n")
        f.write(summary)
    print(f"📄 复盘报告已保存: {report_path}")
    
    # 发送到 Telegram (纯文本模式，带重试)
    send_result = send_with_retry(summary, parse_mode='')
    print(f"Telegram 发送结果: {send_result.get('ok')}")
    
    return send_result.get('ok', False)


if __name__ == "__main__":
    import os
    success = main()
    sys.exit(0 if success else 1)