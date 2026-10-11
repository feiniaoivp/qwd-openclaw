#!/usr/bin/env python3
"""
debug_pm.py — 事后验尸工具（cron/无人值守场景的事故现场还原）
================================================================
背景（2026-10-11 python-debugpy 技能落实）：cron 无人值守脚本没有 stdin，
交互式调试器完全不可用。在关键 except 分支调用 pm_dump()，把完整
traceback + 各帧局部变量落盘到 /tmp/openclaw_pm/（可用 PM_DUMP_DIR 覆盖），
并把落盘路径打印到 stderr，cron 日志可见。

用法（在关键 except 分支，配合 re-raise 或 log.warning）：

    from analysis.debug_pm import pm_dump
    try:
        run()
    except Exception:
        pm_dump("close_scan")   # 落盘 + stderr 提示
        raise

产出文件示例：/tmp/openclaw_pm/pm_20261011-092500_close_scan.txt
每帧局部变量 repr 截断到 300 字符，防止大 DataFrame 刷爆磁盘。

自检：python3 analysis/debug_pm.py    # 注入样例异常，打印落盘内容
"""
import os
import sys
import traceback
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
# 必须同时插入：工作区根 + analysis/（脚本模式/被 import 双兼容）
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DEFAULT_OUT_DIR = os.environ.get("PM_DUMP_DIR", "/tmp/openclaw_pm")
_REPR_MAX = 300


def _safe_repr(value):
    try:
        r = repr(value)
    except Exception as e:
        r = f"<repr failed: {type(e).__name__}: {e}>"
    if len(r) > _REPR_MAX:
        r = r[:_REPR_MAX] + f"...<{len(r)} chars truncated>"
    return r


def pm_dump(tag="exception", out_dir=None, log=None):
    """在 except 块内调用：落盘 traceback + 帧局部变量，返回文件路径。

    - 无活动异常时安全返回空串（并提示）。
    - 验尸工具自身失败不会抛出（不能反过来掩盖原异常），失败原因可见。
    """
    if log is None:
        def log(msg):
            print(msg, file=sys.stderr)
    out_dir = out_dir or DEFAULT_OUT_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_tag = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(tag))[:60]
        path = os.path.join(out_dir, f"pm_{ts}_{safe_tag}.txt")
        lines = []
        exc = sys.exc_info()
        if exc[1] is None:
            lines.append("pm_dump() 在无活动异常时被调用（无现场可落盘）")
        else:
            lines.extend(traceback.format_exception(*exc))
            seen = set()
            tb = exc[2]
            while tb:
                fr = tb.tb_frame
                if id(fr) not in seen:
                    seen.add(id(fr))
                    code = fr.f_code
                    lines.append(
                        f"\n--- Frame {code.co_filename} line {tb.tb_lineno} in {code.co_name} ---"
                    )
                    for k, v in list(fr.f_locals.items()):
                        lines.append(f"  {k} = {_safe_repr(v)}")
                tb = tb.tb_next
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log(f"🔴 post-mortem 已落盘: {path}")
        return path
    except Exception as e:
        log(f"⚠️ pm_dump 自身失败（不影响原异常处理）: {type(e).__name__}: {e}")
        return ""


if __name__ == "__main__":
    def _selftest():
        demo_state = {"frames": 3, "note": "selftest sample"}
        try:
            raise ValueError("pm_dump 自检样例异常")
        except ValueError:
            p = pm_dump("selftest")
        if not p:
            print("❌ 自检失败：未产出落盘文件")
            sys.exit(1)
        print("=== 落盘内容预览 ===")
        with open(p, encoding="utf-8") as f:
            content = f.read()
        print(content[:1500])
        ok = ("ValueError" in content) and ("demo_state" in content)
        print(f"\n{'✅ 自检通过' if ok else '❌ 自检失败（缺少异常或局部变量）'}")
        sys.exit(0 if ok else 1)

    _selftest()
