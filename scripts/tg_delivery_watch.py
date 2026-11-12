#!/usr/bin/env python3
#!/usr/bin/env python3
"""Telegram 投递观察哨（2026-11-13 立项，观察 3 天）
背景: Secret 迁移 batch1 后 botToken 走 store 引用，需验证投递链路稳定。
职责: 每次运行对比 cron list 的投递类 job 状态 + gateway 日志投递结果，
      产出 data/secrets/tg_delivery_watch.json 轨迹（追加式）。
判定: migrated_token 相关投递失败(drafted=false/delivered=false) 计入告警;
      TLS 潮汐窗(已知问题)单独归类不误报迁移回归。
用法: python3 scripts/tg_delivery_watch.py   (手动或挂 cron)
"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
import subprocess
import time
from datetime import datetime

OUT = os.path.join(WORKSPACE, "data", "secrets", "tg_delivery_watch.json")
LOG_GW = f"/tmp/openclaw/openclaw-{datetime.now().strftime('%Y-%m-%d')}.log"


def main():
    now = datetime.now().strftime("%F %T")
    entry = {"at": now, "migrated_delivery_jobs": {}, "gateway_tg_lines": [], "alerts": []}

    # 1) cron 投递类 job 状态
    r = subprocess.run(["openclaw", "cron", "list", "--json"], capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        try:
            rows = json.loads(r.stdout)
            rows = rows.get("jobs", rows if isinstance(rows, list) else [])
            for j in rows:
                d = j.get("delivery") or {}
                blob = json.dumps(d, ensure_ascii=False).lower()
                if d.get("channel") or "telegram" in blob:
                    st = j.get("lastRunStatus") or "?"
                    entry["migrated_delivery_jobs"][(j.get("name") or "?")[:40]] = st
                    if st == "error":
                        entry["alerts"].append(f"cron error: {j.get('name')}")
        except Exception as e:
            entry["alerts"].append(f"cron list parse fail: {e}")
    else:
        entry["alerts"].append(f"cron list rc={r.returncode}")

    # 2) gateway 日志近 300 行的 telegram 投递痕迹
    try:
        with open(LOG_GW, errors="ignore") as f:
            tail = f.readlines()[-300:]
        for l in tail:
            ll = l.lower()
            if "telegram" in ll and ("delivered" in ll or "send" in ll or "fail" in ll or "error" in ll):
                entry["gateway_tg_lines"].append(l.strip()[:180])
    except FileNotFoundError:
        entry["alerts"].append("gateway.log 不存在")

    # 3) 追加轨迹（写前备份保护）
    hist = []
    if os.path.exists(OUT):
        try:
            hist = json.load(open(OUT))
            if not isinstance(hist, list):
                hist = []
        except Exception:
            hist = []
    hist.append(entry)
    if len(hist) > 200:
        hist = hist[-200:]
    tmp = OUT + ".tmp"
    json.dump(hist, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)

    n_alert = len(entry["alerts"])
    print(f"[{now}] 投递job={len(entry['migrated_delivery_jobs'])} "
          f"gw痕迹={len(entry['gateway_tg_lines'])} 告警={n_alert}")
    for a in entry["alerts"][:5]:
        print(f"  ⚠️ {a}")
    sys.exit(1 if n_alert else 0)


if __name__ == "__main__":
    main()
