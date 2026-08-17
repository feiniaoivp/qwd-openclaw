#!/usr/bin/env python3
"""
周回测全流水线运行器（含 030 自动微调）
===========================================
整合：单窗口回测 → 跨周期验证 → 全量参数调优 → 调参差异榜 → 赛后验证 → 030自动微调 → LLM汇总推送

运行方式：python3 analysis/weekly_full_pipeline.py
建议：将此脚本作为 command cron 运行，避免 agentTurn LLM 超时问题
"""

import os
import sys
import json
import subprocess
import time
from datetime import datetime
from typing import List, Dict, Any

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
ANALYSIS_DIR = os.path.join(WORKSPACE, "analysis")
DATA_DIR = os.path.join(WORKSPACE, "data")

# 确保目录存在
os.makedirs(ANALYSIS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


def run_script(script: str, args: List[str] = None, timeout: int = 600) -> Dict[str, Any]:
    """运行脚本并返回结果"""
    cmd = [sys.executable, os.path.join(ANALYSIS_DIR, script)]
    if args:
        cmd.extend(args)
    
    print(f"\n{'='*60}")
    print(f"🚀 运行: {' '.join(cmd)}")
    print(f"{'='*60}")
    
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        elapsed = time.time() - start
        
        if result.returncode == 0:
            print(f"✅ {script} 完成 ({elapsed:.1f}s)")
            return {"success": True, "stdout": result.stdout, "stderr": result.stderr, "elapsed": elapsed}
        else:
            print(f"❌ {script} 失败 ({elapsed:.1f}s): {result.stderr[:200]}")
            return {"success": False, "stdout": result.stdout, "stderr": result.stderr, "elapsed": elapsed}
    except subprocess.TimeoutExpired:
        print(f"⏱️ {script} 超时 ({timeout}s)")
        return {"success": False, "stdout": "", "stderr": f"Timeout after {timeout}s", "elapsed": timeout}
    except Exception as e:
        print(f"💥 {script} 异常: {e}")
        return {"success": False, "stdout": "", "stderr": str(e), "elapsed": 0}


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n📅 周回测全流水线启动 ({today})")
    
    results = {}
    
    # 步骤 1: 单窗口回测
    print("\n📊 步骤 1/7: 单窗口回测 (backtest_strategies.py)")
    r = run_script("backtest_strategies.py", timeout=300)
    results["backtest"] = r
    if not r["success"]:
        print("⚠️ 回测失败，继续后续步骤...")
    
    # 步骤 2: 跨周期验证
    print("\n🔍 步骤 2/7: 跨周期验证 (validate_strategies.py --write-map)")
    r = run_script("validate_strategies.py", ["--write-map"], timeout=600)
    results["validate"] = r
    if not r["success"]:
        print("⚠️ 验证失败，继续后续步骤...")
    
    # 步骤 3: 全量参数调优
    print("\n⚙️ 步骤 3/7: 全量参数调优 (param_tune.py --write)")
    r = run_script("param_tune.py", ["--write"], timeout=900)
    results["param_tune"] = r
    if not r["success"]:
        print("⚠️ 参数调优失败，继续后续步骤...")
    
    # 步骤 4: 调参差异榜
    print("\n⚖️ 步骤 4/7: 调参差异榜 (param_signal_diff.py)")
    r = run_script("param_signal_diff.py", timeout=120)
    results["param_signal_diff"] = r
    if not r["success"]:
        print("⚠️ 差异榜失败，继续后续步骤...")
    
    # 步骤 5: 赛后验证（备份当前参数）
    print("\n🔄 步骤 5/7: 赛后验证 - 备份参数 (param_evaluate.py --backup)")
    r = run_script("param_evaluate.py", ["--backup"], timeout=120)
    results["param_evaluate_backup"] = r
    if not r["success"]:
        print("⚠️ 参数备份失败，继续后续步骤...")
    
    # 步骤 6: 赛后验证（评估前 14 天）
    print("\n📈 步骤 6/7: 赛后验证 - 评估前 14 天 (param_evaluate.py)")
    r = run_script("param_evaluate.py", timeout=120)
    results["param_evaluate"] = r
    if not r["success"]:
        print("⚠️ 赛后验证失败，继续后续步骤...")
    
    # 步骤 7: 030 自动仓位微调 ⭐ 新增
    print("\n🔧 步骤 7/7: 030 自动仓位微调 (auto_adjust_position.py)")
    r = run_script("auto_adjust_position.py", timeout=60)
    results["auto_adjust"] = r
    if not r["success"]:
        print("⚠️ 自动微调失败（可能成交不足），继续...")
    
    # 汇总
    print(f"\n{'='*60}")
    print("📋 周回测全流水线完成汇总")
    print(f"{'='*60}")
    
    all_success = True
    for step, result in results.items():
        status = "✅" if result["success"] else "❌"
        elapsed = result.get("elapsed", 0)
        print(f"  {status} {step}: {elapsed:.1f}s")
        if not result["success"]:
            all_success = False
    
    # 保存运行记录
    run_record = {
        "date": today,
        "steps": results,
        "all_success": all_success,
        "total_elapsed": sum(r.get("elapsed", 0) for r in results.values()),
    }
    
    record_path = os.path.join(DATA_DIR, f"weekly_pipeline_{today}.json")
    with open(record_path, "w") as f:
        json.dump(run_record, f, ensure_ascii=False, indent=2, default=str)
    
    print(f"\n📄 运行记录已保存: {record_path}")
    
    if all_success:
        print("\n🎉 所有步骤成功！")
        return 0
    else:
        print("\n⚠️ 部分步骤失败，请检查日志")
        return 1


if __name__ == "__main__":
    sys.exit(main())