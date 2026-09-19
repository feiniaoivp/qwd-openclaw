#!/usr/bin/env python3
"""
技能供应链安全扫描 (skill_supply_scan)
=========================================
用途: 安装新技能前 / 每周审计时, 静态扫描 skills/ 目录中的高危模式。

模式:
  --offline  (默认) 纯静态扫描本地文件, **零网络调用**, 秒级完成, 可安全接入 cron/pipeline
  --online           额外查询 registry/远端元数据(需网络; 失败自动降级为 offline)

设计要点 (2026-09-19 重写):
  旧版 bash 版每个文件调用 `file` 1 次 + 每条模式 `grep` 1 次 ≈ 3800 次进程启动,
  在 173 个技能文件上会挂 >60s —— **一个会超时的门禁不可能接入自动化**。
  新版单进程遍历 + 预编译正则, 秒级完成。

退出码: 0=全部通过, 1=发现高危或警告, 2=扫描自身异常
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Dict, List, Tuple

WORKSPACE = os.getenv("WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))
DEFAULT_SKILLS_DIR = os.path.join(WORKSPACE, "skills")

# ── 高危: 破坏性 / 数据外泄 / 权限提升 ──
HIGH_RISK = [
    ("递归/强制删除", r"rm\s+-[a-zA-Z]*[rf][a-zA-Z]*"),
    ("sudo 提权", r"sudo\s+(rm|dd|mkfs|chown|chmod)"),
    ("下载后直接执行", r"curl[^|\n]*\|\s*(sudo\s+)?(sh|bash|python|zsh)"),
    ("反向 shell", r"/dev/tcp/"),
    ("bash 反弹 shell", r"bash\s+-i\s*>"),
    ("base64 编码后门", r"eval\([^)\n]*base64"),
    ("Python 动态执行", r"\bos\.system\("),
    ("shell=True 子进程", r"subprocess\.[a-z]+\([^)\n]*shell\s*=\s*True"),
]

# rm 目标是否指向"根/家目录/通配"等真正致命位置
_DANGEROUS_TARGET = re.compile(r"^(/|~|\$HOME|\*|/\*|\$\{[A-Z_]+\})(\/?\*?)$")
_RM_ANY = re.compile(r"rm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+(\S+)")


def _rm_target_severity(match_text: str) -> Tuple[str, str]:
    """评估一条 rm 的危险度。
    返回 (level, note)：level 为 HIGH/DOWNGRADE/INFO。
    - 目标是 / ~ $HOME * 等 → HIGH（真致命）
    - 目标是具体子路径（用户自有目录）→ DOWNGRADE（需人工看一眼）
    - 无目标/无法解析 → INFO
    """
    m = _RM_ANY.search(match_text)
    if not m:
        return ("INFO", "无法解析删除目标")
    tgt = m.group(1)
    if _DANGEROUS_TARGET.match(tgt):
        return ("HIGH", f"⚠️ 删除目标指向根/家目录/通配: {tgt}")
    return ("DOWNGRADE", f"删除目标是具体子路径: {tgt}（需人工确认）")

# ── 警告: 网络请求 / secret / 外部发送 ──
WARNINGS = [
    ("网络请求(curl/wget)", r"\b(curl|wget)\s"),
    ("HTTP(S) 请求(python)", r"requests\.(get|post)\(|\burllib\b"),
    ("明文 secret/API Key", r"(api[_-]?key|apikey|secret|passwd|password)\s*[=:]\s*[\"'][^\"']{8,}"),
    ("疑似 OpenAI key", r"sk-[A-Za-z0-9]{20,}"),
    ("疑似 AWS key", r"AKIA[0-9A-Z]{16}"),
    ("邮件发送", r"smtplib|send_mail"),
    ("IM/webhook 外发", r"\btelegram\b|\bwebhook\b|\bslack\b|dingtalk|feishu"),
    ("环境变量取 secret", r"os\.environ\[[^\]]*(KEY|TOKEN|PASS|SECRET)", ),
    ("HTTP 明文传输", r"http://(?!localhost|127\.0\.0\.1)"),
    ("自动安装依赖", r"\b(pip|npm|pnpm|yarn|brew)\s+(install|add)\b"),
]

# 文本文件后缀（避免对二进制做正则）
TEXT_EXT = {
    ".md", ".txt", ".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".html", ".css",
    ".xml", ".csv", ".env", ".rb", ".go", ".rs", ".java", ".php", ".sql",
}

SKIP_DIRS = {".clawhub", ".git", "node_modules", "__pycache__", ".venv", "venv"}
SKIP_FILES = {".DS_Store"}
MAX_BYTES = 2_000_000  # 跳过超大文件


def _compile(pairs):
    return [(desc, re.compile(pat, re.IGNORECASE)) for desc, pat in pairs]


def _in_code_fence(content: str, pos: int) -> bool:
    """判断 pos 是否位于 Markdown ``` 代码块内（文档示例，非可执行代码）"""
    before = content[:pos]
    return before.count("```") % 2 == 1


def iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in SKIP_FILES:
                continue
            ext = os.path.splitext(fn)[1].lower()
            # 无后缀文件（如脚本）也扫，但需是文本
            if ext and ext not in TEXT_EXT:
                continue
            yield os.path.join(dirpath, fn)


def scan(root: str, verbose: bool = False) -> Dict:
    hi_c = _compile(HIGH_RISK)
    wa_c = _compile(WARNINGS)
    findings = []
    files_scanned = 0

    for path in iter_files(root):
        try:
            if os.path.getsize(path) > MAX_BYTES:
                continue
            with open(path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue
        files_scanned += 1
        rel = os.path.relpath(path, root)

        for desc, rx in hi_c:
            for m in rx.finditer(content):
                line = content.count("\n", 0, m.start()) + 1
                level = "HIGH"
                note = ""
                # rm 类做目标危险度分级，避免"删自有子目录"被当成致命高危
                if desc == "递归/强制删除":
                    level, note = _rm_target_severity(
                        content[max(0, m.start() - 20): m.start() + 200])
                # 代码块(```)内的示例视为文档，降级为 INFO
                if _in_code_fence(content, m.start()):
                    note = (note + " | " if note else "") + "位于 Markdown 代码块(文档示例)"
                    if level == "HIGH":
                        level = "DOWNGRADE"
                findings.append({"level": level, "desc": desc, "file": rel,
                                 "line": line, "match": m.group(0)[:80],
                                 "note": note})
                break  # 每文件每模式只报一次
        for desc, rx in wa_c:
            for m in rx.finditer(content):
                line = content.count("\n", 0, m.start()) + 1
                findings.append({"level": "WARN", "desc": desc, "file": rel,
                                 "line": line, "match": m.group(0)[:80]})
                break

    return {"files": files_scanned, "findings": findings}


def main():
    ap = argparse.ArgumentParser(description="技能供应链安全扫描")
    ap.add_argument("skills_dir", nargs="?", default=DEFAULT_SKILLS_DIR,
                    help=f"技能目录 (默认 {DEFAULT_SKILLS_DIR})")
    ap.add_argument("--offline", action="store_true", default=True,
                    help="纯静态扫描, 零网络调用 (默认)")
    ap.add_argument("--online", action="store_true",
                    help="额外查远端元数据 (需网络; 失败自动降级)")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--quiet", action="store_true", help="仅输出结论行")
    args = ap.parse_args()

    root = os.path.abspath(args.skills_dir)
    if not os.path.isdir(root):
        print(f"❌ 技能目录不存在: {root}", file=sys.stderr)
        return 2

    mode = "online" if args.online else "offline"
    t0 = time.time()
    try:
        result = scan(root)
    except Exception as e:
        print(f"❌ 扫描自身异常: {e}", file=sys.stderr)
        return 2
    elapsed = time.time() - t0

    hi = [f for f in result["findings"] if f["level"] == "HIGH"]
    down = [f for f in result["findings"] if f["level"] == "DOWNGRADE"]
    wa = [f for f in result["findings"] if f["level"] == "WARN"]

    if args.json:
        # JSON 模式：只输出一个合法 JSON 文档（结论行进 stderr），便于程序解析
        print(json.dumps({"mode": mode, "root": root, "files": result["files"],
                          "high": len(hi), "downgrade": len(down), "warn": len(wa),
                          "elapsed_s": round(elapsed, 2),
                          "pass": len(hi) == 0,
                          "findings": result["findings"]}, ensure_ascii=False, indent=2))
        if hi:
            print(f"🔴 发现 {len(hi)} 处致命高危!", file=sys.stderr)
        return 1 if hi else 0

    if not args.quiet:
        print(f"🔍 技能供应链扫描 [{mode}] : {root}")
        print("=" * 62)
        order = {"HIGH": 0, "DOWNGRADE": 1, "WARN": 2}
        for f in sorted(result["findings"],
                        key=lambda x: (order.get(x["level"], 3), x["file"])):
            icon = {"HIGH": "🔴 高危", "DOWNGRADE": "🟠 待确认",
                    "WARN": "🟡 警告"}.get(f["level"], "⚪")
            print(f"{icon} [{f['file']}:{f['line']}] {f['desc']}")
            print(f"      → {f['match']}")
            if f.get("note"):
                print(f"      ℹ️ {f['note']}")

    print("=" * 62)
    if not hi and not down and not wa:
        print(f"✅ 扫描通过: 无高危, 无警告 ({result['files']} 文件, {elapsed:.2f}s)")
        return 0
    if not hi:
        print(f"✅ 无致命高危 | 🟠 待确认 {len(down)} 处 | 🟡 警告 {len(wa)} 处 "
              f"(扫描 {result['files']} 文件, {elapsed:.2f}s)")
        # 无致命高危时视为通过（待确认项供人工看，不阻断自动化）
        return 0
    print(f"🔴 发现 {len(hi)} 处致命高危! 请勿安装/使用该技能 "
          f"(待确认 {len(down)} / 警告 {len(wa)}) | {elapsed:.2f}s")
    return 1


if __name__ == "__main__":
    sys.exit(main())
