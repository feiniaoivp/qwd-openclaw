#!/usr/bin/env python3
"""
每周复盘推送脚本 (Command 模式)
===============================
执行周六复盘并通过 send_telegram 推送结果 (纯文本)。
"""

import sys
import os
import json
import subprocess
from datetime import datetime, timedelta

WORKSPACE = "/Users/duguke/.openclaw/workspace"
sys.path.insert(0, WORKSPACE)

from send_telegram import send_with_retry
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)



def run_weekly_review():
    """执行周六复盘逻辑"""
    today = datetime.now().strftime("%Y-%m-%d")
    week_start = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
    
    lines = []
    lines.append(f"📅 周六复盘 {today}")
    lines.append(f"周期: {week_start} ~ {today}")
    lines.append("")
    
    # 1. 模拟盘周报（2026-10-31 改造：复用 analysis/portfolio_weekly.py 单一事实来源。
    #    修复旧版 0/27 bug：旧代码用 p.get("position") 读不存在的字段（实际字段是 shares），
    #    永远显示 0 持仓；且旧版只覆盖主盘，现在两账户 + 信号漏斗一并聚合）
    try:
        from analysis.portfolio_weekly import build_report, format_telegram
        rep = build_report()
        lines.append(format_telegram(rep))
        lines.append("")
    except Exception as e:
        lines.append(f"⚠️ 模拟盘周报生成失败: {e}")
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
            snap_date = alert_state.get("date", "未标注")
            if fired:
                # 修复 2026-10-17: state 现为当日去重快照(跨日自动重置)，
                # 不再是累积值 —— 旧文案「本周 N 条」口径虚高(曾把历史残留全算进去)
                lines.append(f"🔔 预警快照({snap_date}): {len(fired)} 条未清触发位（当日去重口径, 非本周累计）")
                for k, v in list(fired.items())[:5]:
                    lines.append(f"  {k}: {v}")
            else:
                lines.append(f"🔔 预警快照({snap_date}): 无未清触发位")
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
    _bak(report_path)
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