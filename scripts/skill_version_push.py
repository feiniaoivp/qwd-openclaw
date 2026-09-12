#!/usr/bin/env python3
"""
技能版本监控推送 (Command 模式)
=================================
替代原 LLM agentTurn 版本。跑 ontology skill-check，仅在【确有版本变更】时推送 TG。

原 prompt 要求 LLM 判断"是否有真实变更"，存在误判/编造风险；本脚本用
ontology 退出码/输出关键词做确定性判定。

用法: python3 scripts/skill_version_push.py [--no-push]
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

WORKSPACE = "/Users/duguke/.openclaw/workspace"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    try:
        r = subprocess.run(
            [sys.executable, "scripts/ontology.py", "skill-check",
             "--skills-dir", f"{Path.home()}/.openclaw/workspace/skills"],
            cwd=WORKSPACE, capture_output=True, text=True, timeout=180,
        )
    except Exception as e:
        print(f"❌ skill-check 执行失败: {type(e).__name__}: {e}")
        return 1

    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    print(f"[stdout] {out}")
    if err:
        print(f"[stderr] {err}")

    # 判定: 无变更信号
    no_change = ("No version changes" in out) or ("无变更" in out) or ("无版本变更" in out)
    if no_change or not out:
        print("[SILENT] 无版本变更，不推送")
        return 0

    text = f"🔧 技能版本变更检测\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{out[:1500]}"
    if not args.no_push:
        from send_telegram import send_with_retry
        send_with_retry(text, parse_mode="")
        print("\n[PUSHED]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
