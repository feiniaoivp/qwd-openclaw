#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
早盘自选股报告生成及发送
1. 运行 analyze_all_stocks.py（实时行情版）
2. 解析结果
3. 发送Telegram消息+附件
"""
import os, sys, subprocess, json, re

WORKSPACE = "/Users/duguke/.openclaw/workspace"
SUMMARY_FILE = os.path.join(WORKSPACE, "analysis_summary_all.md")

def run_analysis():
    result = subprocess.run(
        ["python3", os.path.join(WORKSPACE, "analyze_all_stocks.py")],
        capture_output=True, text=True, cwd=WORKSPACE
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr[:500], file=sys.stderr)
    return result.returncode == 0 and os.path.exists(SUMMARY_FILE)

def parse_summary():
    """解析 summary markdown 文件"""
    if not os.path.exists(SUMMARY_FILE):
        return [], [], []
    
    with open(SUMMARY_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    buys, sells, holds = [], [], []
    for line in lines:
        if line.startswith('|') and '股票代码' not in line and '---' not in line:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 9:
                code, name, price, change, signal = parts[1], parts[2], parts[7], parts[8], parts[6]
                entry = f"`{code}` {name} ¥{price} {change}"
                if signal == '买入':
                    buys.append(entry)
                elif signal == '卖出':
                    sells.append(entry)
                else:
                    holds.append(entry)
    return buys, sells, holds

def build_message(buys, sells, holds):
    from datetime import datetime
    ts = datetime.now().strftime('%Y-%m-%d %H:%M')
    total = len(buys) + len(sells) + len(holds)
    
    lines = [f"📊 **早盘自选股分析** {ts}", ""]
    lines.append(f"✅ 实时行情分析完成: {total} 只股票")
    lines.append("")
    
    if buys:
        lines.append("🔴 **买入信号:**")
        for b in buys:
            lines.append(f"- {b}")
        lines.append("")
    
    if sells:
        lines.append("🔵 **卖出信号:**")
        for s in sells:
            lines.append(f"- {s}")
        lines.append("")
    
    lines.append(f"📈 其余 {len(holds)} 只持有/观望")
    lines.append("")
    lines.append("📎 详见附件报告")
    lines.append("")
    lines.append("⚠️ 价格为实时行情，数据来源 akShare")
    
    return "\n".join(lines)

def send_report(message_text, file_path):
    """调用 send_telegram.py 发送"""
    # 先发送消息
    cmd1 = ["python3", os.path.join(WORKSPACE, "send_telegram.py"), message_text]
    subprocess.run(cmd1, cwd=WORKSPACE)
    
    # 再发附件
    if file_path and os.path.exists(file_path):
        cmd2 = ["python3", os.path.join(WORKSPACE, "send_telegram.py"), "📎 完整报告", file_path]
        subprocess.run(cmd2, cwd=WORKSPACE)

if __name__ == "__main__":
    print("=== 运行实时行情分析 ===")
    if not run_analysis():
        print("分析失败")
        sys.exit(1)
    
    print("\n=== 生成报告 ===")
    buys, sells, holds = parse_summary()
    msg = build_message(buys, sells, holds)
    print(msg)
    
    print("\n=== 发送Telegram ===")
    send_report(msg, SUMMARY_FILE)
    print("完成")
