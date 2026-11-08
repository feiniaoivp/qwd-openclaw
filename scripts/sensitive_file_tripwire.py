#!/usr/bin/env python3
"""sensitive_file_tripwire.py — 敏感文件变更绊线（补偿 exec 审批层 YOLO 态的知情门禁）

背景（2026-11-06 exec-policy show 定性）: 本机 exec 生效策略 = security=full + ask=off，
AGENTS.md「必须请示」类目标文件（shell rc / crontab / launchd plist / openclaw.json）
在工具层零执法，红线 100% 依赖模型自觉。本脚本 = 三层防线的第 2+3 层：
对受监视文件做 sha256 基线比对，基线外变更 → exit 1（进周一审计告警）。

用法:
  python3 scripts/sensitive_file_tripwire.py             # 比对（无基线则先建）
  python3 scripts/sensitive_file_tripwire.py --baseline  # 确认变更合法后重建基线

退出码: 0=干净(或首次建基线)  1=检出基线外变更  2=执行出错
设计: 纯 stdlib、哈希比对（非 mtime，免疫 touch）、crontab 导出快照后参与哈希。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
BASELINE = os.path.join(WORKSPACE, "data", "sensitive_files_baseline.json")
HOME = os.path.expanduser("~")

PLIST_KEYWORDS = ("openclaw", "embedding", "duguke")


def build_crontab_snapshot():
    """crontab 无文件路径可盯，导出快照文件参与哈希；无 crontab 返回 None。"""
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    except Exception as e:
        print(f"WARN: crontab read failed: {e}")
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    snap = os.path.join(WORKSPACE, "data", ".crontab_snapshot_for_tripwire")
    with open(snap, "w") as f:
        f.write(r.stdout)
    return snap


def watched_files():
    files = [
        os.path.join(HOME, ".zshrc"),
        os.path.join(HOME, ".zprofile"),
        os.path.join(HOME, ".zshenv"),
        os.path.join(HOME, ".openclaw", "openclaw.json"),
    ]
    snap = build_crontab_snapshot()
    if snap:
        files.append(snap)
    la = os.path.join(HOME, "Library", "LaunchAgents")
    if os.path.isdir(la):
        for n in sorted(os.listdir(la)):
            if n.endswith(".plist") and any(k in n.lower() for k in PLIST_KEYWORDS):
                files.append(os.path.join(la, n))
    return files


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    rebuild = "--baseline" in sys.argv
    files = watched_files()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if rebuild or not os.path.isfile(BASELINE):
        if os.path.isfile(BASELINE):
            shutil.copy2(BASELINE, BASELINE + ".bak")
        base = {p: sha256(p) for p in files if os.path.isfile(p)}
        with open(BASELINE, "w") as f:
            json.dump({"generatedAt": now, "hashes": base}, f, indent=1)
        print(f"BASELINE_WRITTEN files={len(base)} at {now}")
        return 0

    with open(BASELINE) as f:
        old = json.load(f).get("hashes", {})
    cur = {p: sha256(p) for p in files if os.path.isfile(p)}
    changed = [p for p in cur if p in old and cur[p] != old[p]]
    added = [p for p in cur if p not in old]
    removed = [p for p in old if p not in cur]
    if changed or added or removed:
        for tag, lst in (("CHANGED", changed), ("ADDED", added), ("REMOVED", removed)):
            for p in lst:
                print(f"{tag}: {p}")
        print(f"TRIPWIRE_ALERT total={len(changed) + len(added) + len(removed)}")
        print("人工核对: 变更是否有对应 /approve 或 guard/ 快照记录；合法则 --baseline 重建")
        return 1
    print(f"TRIPWIRE_CLEAN files={len(cur)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"TRIPWIRE_ERROR: {e}")
        sys.exit(2)
