#!/usr/bin/env python3
"""
Universe 每日运行器 — L1→L3→种子库 一键链（2026-11-09 四层漏斗落地）
====================================================================
用法（cron 15:35 交易日后调用）:
  python3 analysis/universe_daily_runner.py            # 全链: L1重建→L3打分→种子库
  python3 analysis/universe_daily_runner.py --skip-l1  # 跳过L1重建（流动性周更时）
产出:
  data/universe/universe_l1.json / universe_core.json (周更)
  data/universe/candidates_daily.json (日更)
  data/universe/seed_pool.json + seed_pool_audit.log (日更)
铁律:
  - 交易日判定 is_trading_day()，非交易日直接退出
  - 每步失败不阻断后续（L1失败仍可用昨日L2跑L3），全留痕
  - stdout JSON 摘要块供推送解析
"""
#!/usr/bin/env python3
import os
import sys
import json
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

UNI_DIR = os.path.join(WORKSPACE, "data", "universe")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-l1", action="store_true")
    ap.add_argument("--skip-trading-check", action="store_true", help="仅测试用")
    args = ap.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    summary = {"date": today, "steps": {}}

    # 0) 交易日判定（SSOT: analysis.service.is_trading_day）
    if not args.skip_trading_check:
        try:
            from analysis.service import is_trading_day
            if not is_trading_day(today):
                print(f"📅 {today} 非交易日，跳过 universe 漏斗")
                return
            summary["steps"]["trading_day"] = "ok"
        except Exception as e:
            print(f"⚠️ 交易日判定失败(fail-open继续): {e}")
            summary["steps"]["trading_day"] = f"warn:{e}"

    # 1) L1 重建（流动性缓存周更/日更均可，快）
    if not args.skip_l1:
        try:
            from analysis.universe_filter import build_l1, build_l2
            l1 = build_l1()
            l2 = build_l2()
            summary["steps"]["l1"] = {"count": l1.get("count"), "rejected": l1.get("rejected")}
            summary["steps"]["l2"] = l2.get("count")
        except Exception as e:
            print(f"⚠️ L1/L2 构建失败(用昨日缓存继续): {e}")
            summary["steps"]["l1"] = f"error:{e}"

    # 2) L3 打分 + 种子库进化（signal_scoring 内部调用 seed_pool.update_daily）
    try:
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(WORKSPACE, "analysis", "signal_scoring.py")],
                           capture_output=True, text=True, timeout=600)
        out = (r.stdout or "") + (r.stderr or "")
        # 解析候选数
        import re
        m = re.search(r"L3 候选: (\d+) 只 \(门槛后\) / 全池 (\d+)", out)
        summary["steps"]["l3"] = {
            "candidates": int(m.group(1)) if m else None,
            "n_scored": int(m.group(2)) if m else None,
            "rc": r.returncode,
        }
        print(out[-1500:] if len(out) > 1500 else out)
    except Exception as e:
        print(f"⚠️ L3 打分失败: {e}")
        summary["steps"]["l3"] = f"error:{e}"

    # 3) 汇总落盘 + stdout JSON 块
    summary_path = os.path.join(UNI_DIR, f"daily_summary_{today}.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("\n=====UNIVERSE_SUMMARY=====")
    print(json.dumps(summary, ensure_ascii=False))
    print("=====UNIVERSE_END=====")


if __name__ == "__main__":
    main()
