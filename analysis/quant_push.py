#!/usr/bin/env python3
"""
每日量化回测推送脚本 (Command 模式)
=====================================
直接运行 backtest_strategies.py 并通过 send_telegram 推送结果。
"""

import sys
import os
import json
import subprocess
from datetime import date

WORKSPACE = "/Users/duguke/.openclaw/workspace"
sys.path.insert(0, WORKSPACE)

from send_telegram import send_with_retry


def run_and_push():
    # 运行 backtest_strategies.py；stdout 直接落盘到 logs/，
    # 这样即使父进程/包装 shell 被杀，JSON 摘要也不会丢失（2026-10-09 教训）
    log_dir = os.path.join(WORKSPACE, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"backtest_run_{date.today():%Y%m%d}.log")
    with open(log_path, "w", encoding="utf-8") as logf, \
            open(log_path + ".err", "w", encoding="utf-8") as errf:
        result = subprocess.run(
            [sys.executable, "analysis/backtest_strategies.py"],
            cwd=WORKSPACE,
            stdout=logf,
            stderr=errf,
            text=True,
            timeout=1800  # 回测可能需要较长时间（2026-10-02: 600s 在 akshare 降级链下不够用）
        )

    with open(log_path, encoding="utf-8") as f:
        stdout_text = f.read()
    with open(log_path + ".err", encoding="utf-8") as f:
        stderr_text = f.read()

    if result.returncode != 0:
        error_msg = f"❌ backtest_strategies 执行失败:\n{stderr_text[-2000:]}"
        print(error_msg)
        send_with_retry(error_msg, parse_mode='')
        return False
    
    # 提取 JSON (在 =====BACKTEST_RESULT===== 和 =====BACKTEST_END===== 之间)
    output = stdout_text
    try:
        start = output.index("=====BACKTEST_RESULT=====") + len("=====BACKTEST_RESULT=====")
        end = output.index("=====BACKTEST_END=====")
        json_str = output[start:end].strip()
        data = json.loads(json_str)
    except (ValueError, json.JSONDecodeError) as e:
        error_msg = f"❌ 回测结果 JSON 解析失败: {e}"
        print(error_msg)
        send_with_retry(error_msg, parse_mode='')
        return False
    
    # 生成简报文本
    summary = format_summary(data, stdout_text)
    
    # 发送到 Telegram (纯文本模式)
    send_result = send_with_retry(summary, parse_mode='')
    print(f"Telegram 发送结果: {send_result.get('ok')}")
    
    return send_result.get('ok', False)


def format_summary(data: dict, full_output: str) -> str:
    """格式化回测简报 (纯文本)"""
    lines = []
    
    date = data.get('date', '')
    stock_count = data.get('stock_count', 0)
    winner = data.get('winner', '')
    winner_ret = data.get('winner_avg_return', 0)
    
    lines.append(f"📊 每日量化回测 {date}")
    lines.append(f"覆盖: {stock_count} 只股票 x {data.get('strategy_count', 0)} 个策略")
    cal = data.get('data_caliber') or {}
    if cal.get('fallback_count') or cal.get('unknown_count'):
        lines.append(f"⚠️ 口径降级: {cal.get('fallback_count', 0)} 只不复权 / {cal.get('unknown_count', 0)} 只口径未知 —— 勿与历史前复权报告跨日比较")
    lines.append("")
    
    # 综合评分表
    scores = data.get('composite_scores', {})
    avg_returns = data.get('avg_returns', {})
    avg_sharpes = data.get('avg_sharpes', {})
    avg_maxdd = data.get('avg_maxdd', {})
    
    lines.append("🏆 策略综合评分:")
    lines.append(f"| 策略 | 平均收益 | 夏普 | 最大回撤 | 总分 |")
    lines.append(f"|:---|:----:|:---:|:------:|:---:|")
    
    # 按总分排序
    sorted_strategies = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    for sname, score in sorted_strategies:
        ret = avg_returns.get(sname, 0) or 0
        sh = avg_sharpes.get(sname, 0) or 0
        dd = avg_maxdd.get(sname, 0) or 0
        marker = " 👑" if sname == winner else ""
        lines.append(f"| {sname} | {ret:+.2f}% | {sh:.2f} | {dd:.1f}% | {score:.1f}{marker} |")
    
    lines.append("")
    lines.append(f"🥇 综合最优: **{winner}** (平均收益 {winner_ret:+.2f}%)")
    lines.append("")
    
    # 各策略收益Top3 (从完整输出中提取)
    # 简单起见，只显示前几行完整输出中的关键信息
    if "各策略收益Top5" in full_output:
        # 截取前几个策略的top3
        pass
    
    lines.append("---")
    lines.append(f"完整报告: analysis/backtest/{date}.md")
    
    return "\n".join(lines)


if __name__ == "__main__":
    success = run_and_push()
    sys.exit(0 if success else 1)