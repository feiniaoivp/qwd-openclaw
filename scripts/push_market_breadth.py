#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开盘异动三分类扫描推送 — command cron 直跑脚本 (绕开 LLM, 防超时/防乱码)
==========================================================================
流程: 直接 import analysis.market_breadth_scan.run_scan() → 纯文本简报
      → send_telegram 自推 (脚本内置代理兜底) → 落盘 data/market_breadth_output.json
用法: python3 scripts/push_market_breadth.py
输出: 0 = 正常(推送成功); 2 = 扫描失败; 1 = 推送失败
"""
import os
import sys
import json
import shutil
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis"), os.path.join(WORKSPACE, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUTPUT_FILE = os.path.join(WORKSPACE, "data", "market_breadth_output.json")


def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


def main() -> int:
    from analysis.market_breadth_scan import run_scan, OUTPUT_FILE

    try:
        scan = run_scan()
    except Exception as e:
        print(f"扫描失败: {e}", file=sys.stderr)
        return 2

    if not scan or scan.get("counts", {}).get("total", 0) == 0:
        print("扫描结果为空, 不推送", file=sys.stderr)
        return 2

    brief = scan.get("brief", "")
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    _bak(OUTPUT_FILE)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({**scan, "pushed_at": datetime.now().isoformat(timespec="seconds")},
                  f, ensure_ascii=False, indent=1)

    from send_telegram import send_message
    resp = send_message(brief, parse_mode="")
    if isinstance(resp, dict) and resp.get("ok"):
        print(f"已推送开盘异动扫描 ({scan.get('counts')})")
        return 0
    print(f"推送失败: {resp}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
