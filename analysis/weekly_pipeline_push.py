#!/usr/bin/env python3
"""
每周回测流水线推送脚本 (Command 模式)
=======================================
直接运行 weekly_full_pipeline.py 并通过 send_telegram 推送结果 (纯文本)。
"""

import sys
import json
import subprocess

WORKSPACE = "/Users/duguke/.openclaw/workspace"
sys.path.insert(0, WORKSPACE)

from send_telegram import send_with_retry


def run_and_push():
    # 运行 weekly_full_pipeline.py 并捕获输出
    # 注意：完整流水线可能需要 20-30 分钟，设置较长超时
    result = subprocess.run(
        [sys.executable, "analysis/weekly_full_pipeline.py"],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        timeout=3600  # 1 小时上限
    )
    
    if result.returncode != 0:
        error_msg = f"❌ weekly_full_pipeline 执行失败 (退出码 {result.returncode}):\n{result.stderr[-3000:]}"
        print(error_msg)
        send_with_retry(error_msg, parse_mode='')
        return False
    
    # 生成简报文本 - 从输出中提取关键信息
    summary = format_summary(result.stdout, result.stderr)
    
    # 发送到 Telegram (纯文本模式)
    send_result = send_with_retry(summary, parse_mode='')
    print(f"Telegram 发送结果: {send_result.get('ok')}")
    
    return send_result.get('ok', False)


def format_summary(stdout: str, stderr: str) -> str:
    """格式化周回测简报 (纯文本)"""
    from datetime import datetime
    
    today = datetime.now().strftime("%Y-%m-%d")
    lines = []
    lines.append(f"📊 周回测全流水线 {today}")
    lines.append("")
    
    # 解析 stdout 中的步骤结果
    if "周回测全流水线完成汇总" in stdout:
        # 提取汇总部分
        start = stdout.find("周回测全流水线完成汇总")
        summary_section = stdout[start:]
        lines.append(summary_section[:2000])
    else:
        # 简单提取成功/失败步骤
        steps = []
        for line in stdout.splitlines():
            if "✅" in line or "❌" in line:
                if any(k in line for k in ["步骤", "完成", "失败", "回测", "调优", "验证", "差异", "备份", "评估", "微调", "简报"]):
                    steps.append(line.strip())
        if steps:
            lines.append("步骤执行情况:")
            lines.extend(steps[-10:])
        else:
            lines.append("(无法解析步骤结果，请查看完整日志)")
    
    lines.append("")
    
    # 检查是否有错误
    if "❌" in stdout or "失败" in stdout or stderr:
        lines.append("⚠️ 存在失败步骤，建议人工检查日志")
    else:
        lines.append("✅ 所有步骤成功完成")
    
    lines.append("")
    lines.append("---")
    lines.append(f"完整日志: data/weekly_pipeline_{today}.json")
    lines.append(f"回测报告: analysis/backtest/{today}.md")
    lines.append(f"参数评估: analysis/daily/{today}_param_eval.md")
    lines.append(f"差异榜: analysis/daily/{today}_param_signal_diff.md")
    
    return "\n".join(lines)


if __name__ == "__main__":
    success = run_and_push()
    sys.exit(0 if success else 1)