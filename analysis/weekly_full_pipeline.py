#!/usr/bin/env python3
"""
周回测全流水线运行器（含 030 自动微调）
===========================================
整合：单窗口回测 → 参数调优 → 跨周期验证 → 调参差异榜 → 赛后验证 → 030自动微调 → Telegram推送

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
    print("\n📊 步骤 1/8: 单窗口回测 (backtest_strategies.py)")
    r = run_script("backtest_strategies.py", timeout=600)  # 10分钟上限，当前约3.5分钟跑完，留足缓冲
    results["backtest"] = r
    if not r["success"]:
        print("⚠️ 回测失败，继续后续步骤...")
    
    # 步骤 2: 参数调优 (强制快速模式 --fast，约6分钟/35只)
    print("\n⚙️ 步骤 2/8: 参数调优快速模式 (param_tune.py --fast)")
    r = run_script("param_tune.py", ["--fast"], timeout=1800)  # 30分钟上限，足够覆盖快速模式(约6分钟)+缓冲
    results["param_tune"] = r
    if not r["success"]:
        print("⚠️ 参数调优失败，继续后续步骤...")
    
    # 步骤 3: 跨周期验证 — 最后跑，带置信度仲裁写回（拥有最终决定权）
    print("\n🔍 步骤 3/8: 跨周期验证 + 置信度仲裁 (validate_strategies.py --write-map --arbitrate)")
    r = run_script("validate_strategies.py", ["--write-map", "--arbitrate"], timeout=1800)
    results["validate"] = r
    if not r["success"]:
        print("⚠️ 验证失败，继续后续步骤...")
    
    # 步骤 4: 调参差异榜
    print("\n⚖️ 步骤 4/8: 调参差异榜 (param_signal_diff.py)")
    r = run_script("param_signal_diff.py", timeout=300)
    results["param_signal_diff"] = r
    if not r["success"]:
        print("⚠️ 差异榜失败，继续后续步骤...")
    
    # 步骤 5: 赛后验证（备份当前参数）
    print("\n🔄 步骤 5/8: 赛后验证 - 备份参数 (param_evaluate.py --backup)")
    r = run_script("param_evaluate.py", ["--backup"], timeout=120)
    results["param_evaluate_backup"] = r
    if not r["success"]:
        print("⚠️ 参数备份失败，继续后续步骤...")
    
    # 步骤 6: 赛后验证（评估前 14 天）
    print("\n📈 步骤 6/8: 赛后验证 - 评估前 14 天 (param_evaluate.py)")
    r = run_script("param_evaluate.py", timeout=120)
    results["param_evaluate"] = r
    if not r["success"]:
        print("⚠️ 赛后验证失败，继续后续步骤...")
    
    # 步骤 7: 030 自动仓位微调 ⭐ 新增
    print("\n🔧 步骤 7/8: 030 自动仓位微调 (auto_adjust_position.py)")
    r = run_script("auto_adjust_position.py", timeout=60)
    results["auto_adjust"] = r
    if not r["success"]:
        print("⚠️ 自动微调失败（可能成交不足），继续...")

    # 步骤 8: 生成简报并推送 Telegram（纯文本，绕开 LLM 与 Markdown 实体问题）
    print("\n📤 步骤 8/8: 生成周报简报并推送 Telegram")
    brief = build_brief(results)
    results["brief"] = push_brief(brief)

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


def _read_json_file(path):
    """安全读取 JSON；失败返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _latest_validation():
    """取最新的 validation_YYYY-MM-DD.json（用于策略变更数）。"""
    try:
        files = [f for f in os.listdir(ANALYSIS_DIR) if f.startswith("validation_") and f.endswith(".json")]
        if not files:
            return None
        files.sort(reverse=True)
        return _read_json_file(os.path.join(ANALYSIS_DIR, files[0]))
    except Exception:
        return None


def _parse_brief_bool(line):
    """异彩榜提炼：去掉 markdown 表格符号只留数值行。"""
    return line


def build_brief(results=None):
    """读取各步骤产物生成纯文本周报简报。results 可含 param_signal_diff 的 stdout。"""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = []
    lines.append("每周A股策略回测更新简报 " + today)
    lines.append("")

    # 一、策略回测（从 backtest 报告提炼策略榜，去 markdown 符号）
    lines.append("一、五策略全量回测")
    bt_path = os.path.join(ANALYSIS_DIR, "backtest", today + ".md")
    try:
        if os.path.exists(bt_path):
            with open(bt_path, encoding="utf-8") as f:
                content = f.read()
            # 抽整体概况后的策略总分/胜率关键行
            seg = content.split("## 整体概况")[-1].split("## 各策略")[0]
            rows = []
            for ln in seg.splitlines():
                ln = ln.strip()
                if ln.startswith("|") and "平均收益" not in ln and "指标" not in ln and ln.replace("|", "").replace("-", "").strip():
                    rows.append("  " + ln.strip(" |").replace("|", " · "))
            lines.append("\n".join(rows[:9]) if rows else "(无策略榜行)")
        else:
            lines.append("(backtest 报告未生成)")
    except Exception as e:
        lines.append(f"(回测摘要解析失败: {e})")
    lines.append("")

    # 二、策略映射变更数（对比 adaptive_params vs prev）
    lines.append("二、策略/参数变更")
    try:
        new_map = _read_json_file(os.path.join(DATA_DIR, "adaptive_params.json")) or {}
        prev_map = _read_json_file(os.path.join(DATA_DIR, "adaptive_params_prev.json")) or {}
        new_stocks = new_map.get("stocks", new_map) if isinstance(new_map, dict) else {}
        prev_stocks = prev_map.get("stocks", prev_map) if isinstance(prev_map, dict) else {}
        changed = [s for s in new_stocks if s in prev_stocks and new_stocks[s] != prev_stocks[s]]
        if changed:
            lines.append(f"本轮参数变更 {len(changed)} 只: " + ", ".join(changed[:12]))
        else:
            lines.append("本轮无参数变更（或 prev 缺失）")
    except Exception as e:
        lines.append(f"(变更统计失败: {e})")
    lines.append("")

    # 三、调参差异榜（优先用 pipeline 捕获的 stdout；否则读文件）
    lines.append("三、调参对照信号差异榜")
    diff_txt = ""
    if results and results.get("param_signal_diff"):
        diff_txt = results["param_signal_diff"].get("stdout", "") or ""
    if not diff_txt:
        diff_path = os.path.join(ANALYSIS_DIR, "daily", f"{today}_param_signal_diff.md")
        if os.path.exists(diff_path):
            try:
                with open(diff_path, encoding="utf-8") as f:
                    diff_txt = f.read()
            except Exception:
                diff_txt = ""
    if diff_txt:
        # 提炼核心：方向反转/新卖/新买/策略切换 + 摘要
        keep = []
        for ln in diff_txt.splitlines():
            ln = ln.strip()
            if any(k in ln for k in ["方向反转", "新出现卖出", "新出现买入", "策略切换但", "差异榜摘要", "🔁", "🔴 新", "🟢 新", "🔄 策略", "跳过", "无差异", "反转", "新卖", "新买"]):
                keep.append(ln)
        lines.append("\n".join(keep[:15]) if keep else diff_txt[:400].strip())
    else:
        lines.append("(param_signal_diff 无输出/未生成)")
    lines.append("")

    # 四、调参赛后验证
    lines.append("四、调参赛后验证(08-08以来)")
    eval_path = os.path.join(ANALYSIS_DIR, "daily", f"{today}_param_eval.md")
    if os.path.exists(eval_path):
        try:
            with open(eval_path, encoding="utf-8") as f:
                eval_txt = f.read()
            highlights = [l for l in eval_txt.splitlines() if any(k in l for k in ["被调参", "建议回滚", "有成交", "观察中"])]
            if highlights:
                for hline in highlights[:6]:
                    lines.append(hline.strip())
                # 带出恶化明细
                for l in eval_txt.splitlines():
                    if l.strip().startswith("🔴") or l.strip().startswith("⚠️ **建议回滚"):
                        lines.append(l.strip())
            else:
                lines.append(eval_txt.strip()[:500])
        except Exception as e:
            lines.append(f"(赛后验证读取失败: {e})")
    else:
        lines.append("(param_eval 未生成)")
    lines.append("")

    lines.append("—— 由 weekly_full_pipeline.py 自动生成（command cron 直跑，不经 LLM）")
    return "\n".join(lines)


def push_brief(brief_text):
    """用 send_telegram.py 纯文本推送到 Telegram；失败返回错误信息。"""
    try:
        import subprocess
        send_script = os.path.join(WORKSPACE, "send_telegram.py")
        # send_telegram 默认 Markdown；含 () / 冒号易触发实体错误，改内联 requests 纯文本
        import sys
        sys.path.insert(0, WORKSPACE)
        import send_telegram as st
        import requests
        payload = {"chat_id": st.CHAT_ID, "text": brief_text, "parse_mode": ""}
        resp = requests.post(f"{st.API_URL}/sendMessage", json=payload, timeout=30)
        data = resp.json()
        if data.get("ok"):
            return {"success": True, "message_id": data.get("result", {}).get("message_id")}
        return {"success": False, "error": data.get("description", "unknown")}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    sys.exit(main())