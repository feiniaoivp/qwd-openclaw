#!/usr/bin/env python3
"""
Pipeline 集成测试：验证 weekly_full_pipeline.py 的步骤顺序和产出
"""

import json
import os
import subprocess
import sys

WORKSPACE = "/Users/duguke/.openclaw/workspace"
ANALYSIS_DIR = os.path.join(WORKSPACE, "analysis")
DATA_DIR = os.path.join(WORKSPACE, "data")

def run_cmd(cmd, timeout=60):
    """运行命令并返回结果"""
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=WORKSPACE,
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"

def test_pipeline_step_order():
    """验证 pipeline 步骤顺序正确：param_tune → validate --arbitrate"""
    print("🔍 测试 Pipeline 步骤顺序...")
    
    # 读取 pipeline 脚本
    with open(os.path.join(ANALYSIS_DIR, "weekly_full_pipeline.py"), "r") as f:
        content = f.read()
    
    # 检查步骤顺序
    steps = [
        ("步骤 1", "backtest_strategies.py"),
        ("步骤 2", "param_tune.py.*--fast"),  # 现在是步骤 2，无 --write
        ("步骤 3", "validate_strategies.py.*--write-map.*--arbitrate"),  # 现在是步骤 3，有 --arbitrate
        ("步骤 4", "param_signal_diff.py"),
        ("步骤 5", "param_evaluate.py.*--backup"),
        ("步骤 6", "param_evaluate.py"),
        ("步骤 7", "auto_adjust_position.py"),
    ]
    
    all_ok = True
    for step_name, pattern in steps:
        import re
        if re.search(pattern, content):
            print(f"  ✅ {step_name}: 找到 {pattern}")
        else:
            print(f"  ❌ {step_name}: 未找到 {pattern}")
            all_ok = False
    
    # 验证 param_tune 没有 --write
    if "--write" in content and "param_tune.py" in content:
        # 需要精确检查：param_tune 步骤不应有 --write
        param_tune_section = content[content.find("param_tune.py"):content.find("param_tune.py")+200]
        if "--write" in param_tune_section:
            print(f"  ❌ param_tune 步骤不应包含 --write (应由 validate --arbitrate 最终写回)")
            all_ok = False
        else:
            print(f"  ✅ param_tune 步骤正确无 --write")
    
    return all_ok


def test_arbitration_flag():
    """验证 validate_strategies.py 支持 --arbitrate 标志"""
    print("\n🔍 测试 --arbitrate 标志支持...")
    
    with open(os.path.join(ANALYSIS_DIR, "validate_strategies.py"), "r") as f:
        content = f.read()
    
    checks = [
        ("arbitrate 变量定义", "arbitrate = \"--arbitrate\" in sys.argv"),
        ("写入条件包含 arbitrate", "if write_map or arbitrate:"),
        ("confidence_guard 函数", "def confidence_guard"),
        ("三维显著优势判定", "tune_score > val_score + 10 and tune_sharpe > val_sharpe + 0.1 and tune_trades >= 6"),
    ]
    
    all_ok = True
    for name, pattern in checks:
        if pattern in content:
            print(f"  ✅ {name}")
        else:
            print(f"  ❌ {name}: 未找到 {pattern}")
            all_ok = False
    
    return all_ok


def test_docs_exist():
    """验证文档文件存在"""
    print("\n🔍 测试文档存在性...")
    
    docs = [
        "docs/ARBITRATION_LOGIC.md",
        "analysis/test_arbitration.py",
    ]
    
    all_ok = True
    for doc in docs:
        path = os.path.join(WORKSPACE, doc)
        if os.path.exists(path):
            print(f"  ✅ {doc}")
        else:
            print(f"  ❌ {doc}: 不存在")
            all_ok = False
    
    return all_ok


def test_clean_invalid_stocks():
    """验证无效股票已清理"""
    print("\n🔍 测试无效股票清理...")
    
    invalid = ["002180", "300847", "601865"]
    files = [
        "data/adaptive_strategy_map.json",
        "data/adaptive_params.json",
        "data/adaptive_params_prev.json",
    ]
    
    all_ok = True
    for f in files:
        path = os.path.join(WORKSPACE, f)
        with open(path, "r") as fp:
            data = json.load(fp)
        
        # adaptive_strategy_map.json 是扁平结构
        if f == "data/adaptive_strategy_map.json":
            stocks = data
        else:
            stocks = data.get("stocks", {})
        
        found = [s for s in invalid if s in stocks]
        if found:
            print(f"  ❌ {f}: 仍包含无效股票 {found}")
            all_ok = False
        else:
            print(f"  ✅ {f}: 无无效股票")
    
    return all_ok


def test_final_map_matches_arbitration():
    """验证最终映射符合仲裁结果"""
    print("\n🔍 测试最终映射与仲裁结果一致...")
    
    # 读取最新验证结果
    val_files = [f for f in os.listdir(ANALYSIS_DIR) if f.startswith("validation_") and f.endswith(".json")]
    val_files.sort(reverse=True)
    if not val_files:
        print("  ⚠️ 无验证结果文件")
        return True
    
    with open(os.path.join(ANALYSIS_DIR, val_files[0]), "r") as f:
        validation = json.load(f)
    
    with open(os.path.join(DATA_DIR, "adaptive_strategy_map.json"), "r") as f:
        final_map = json.load(f)
    
    # 检查关键仲裁决策
    key_arbitrations = {
        "002318": "bollinger",   # 验证胜
        "002156": "ema_obv",     # 验证胜
    }
    
    all_ok = True
    for sym, expected in key_arbitrations.items():
        actual = final_map.get(sym)
        if actual == expected:
            print(f"  ✅ {sym}: 最终={actual} 符合仲裁预期")
        else:
            print(f"  ❌ {sym}: 最终={actual} 但仲裁预期={expected}")
            all_ok = False
    
    return all_ok


def build_telegram_summary(passed, failed, total, elapsed, details):
    """生成适合 Telegram 的简洁摘要"""
    lines = []
    lines.append(f"🔧 <b>Pipeline 集成测试报告</b>")
    lines.append(f"📊 {passed}/{total} 通过 | ⏱️ {elapsed:.2f}s")
    lines.append("")
    
    for d in details:
        icon = "✅" if d['ok'] else "❌"
        lines.append(f"{icon} <b>{d['name']}</b>")
        lines.append(f"   {d['detail']}")
        if not d['ok']:
            lines.append(f"   ⚠️ 预期通过")
        lines.append("")
    
    if failed == 0:
        lines.append("🎉 <b>全部通过</b>")
    else:
        lines.append(f"⚠️ <b>{failed} 项失败</b>")
    
    return "\n".join(lines)


def main():
    print("=" * 70)
    print("Pipeline 集成测试")
    print("=" * 70)
    
    tests = [
        ("步骤顺序检查", test_pipeline_step_order),
        ("仲裁标志支持", test_arbitration_flag),
        ("文档存在性", test_docs_exist),
        ("无效股票清理", test_clean_invalid_stocks),
        ("映射一致性", test_final_map_matches_arbitration),
    ]
    
    results = []
    details = []
    import time
    t0 = time.time()
    
    for name, test_func in tests:
        tt0 = time.time()
        try:
            ok = test_func()
        except Exception as e:
            ok = False
            print(f"  ❌ {name}: 异常 {e}")
        elapsed = time.time() - tt0
        results.append(ok)
        details.append({
            'name': name,
            'ok': ok,
            'detail': f"耗时 {elapsed:.2f}s"
        })
    
    total_elapsed = time.time() - t0
    
    print("\n" + "=" * 70)
    passed = sum(results)
    total = len(results)
    print(f"集成测试汇总: {passed}/{total} 通过")
    print("=" * 70)
    
    # 输出 Telegram 摘要到 stdout (供 cron 捕获推送)
    print("\n=====TELEGRAM_SUMMARY=====")
    print(build_telegram_summary(passed, total - passed, total, total_elapsed, details))
    print("=====TELEGRAM_END=====")
    
    return all(results)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
