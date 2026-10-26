#!/usr/bin/env python3
"""
verify_kb_zero_loss.py — KB 零丢失门禁（news-kb-irrigation 技能配套）

对照 2026-10-26 实战口径：写前备份 vs 写后当前，唯一标题集比对。
- 备份中的每个唯一标题必须仍存在于当前文件（缺失=0 才算零丢失）
- 当前文件允许比备份多（当日新增）；行数缩水可合法（去重），故仅作报告不作为门禁
- 标题行规则镜像 scripts/kb_writer_v2.py 的 _kb_title_set / _norm_title：
  标题行 = 以 •/-/* 开头、且不是 Details|Data|Insight|Related 子行的行；
  归一化 = 全角括号转半角、去空格（含全角空格）、行動/행동 统一为 行动。

用法:
    python3 verify_kb_zero_loss.py BACKUP_FILE CURRENT_FILE

退出码: 0=零丢失通过; 1=发现备份独有标题缺失; 2=文件读取失败
"""
import re
import sys

SUB_KINDS = r"(?:Details|Data|Insight|Related)"
_SUBLINE_RE = re.compile(rf"^\s*-\s*{SUB_KINDS}\s*:")
_TRANS = str.maketrans({
    "\uff08": "(",  # 全角左括号 -> (
    "\uff09": ")",  # 全角右括号 -> )
    " ": "",
    "\u3000": "",   # 全角空格
})


def _norm_title(t):
    t = t.replace("\ud589\ub3d9", "行动").replace("\u884c\u52d5", "行动")
    return t.translate(_TRANS).strip()


def unique_titles(path):
    titles = set()
    with open(path, encoding="utf-8") as f:
        for ln in f.read().splitlines():
            st = ln.strip()
            if st.startswith(("\u2022", "-", "*")) and not _SUBLINE_RE.match(ln):
                titles.add(_norm_title(st.lstrip("\u2022-* ")))
    return titles


def main():
    if len(sys.argv) != 3:
        print("用法: python3 verify_kb_zero_loss.py BACKUP_FILE CURRENT_FILE")
        return 2
    backup, current = sys.argv[1], sys.argv[2]
    try:
        b_titles = unique_titles(backup)
        c_titles = unique_titles(current)
    except OSError as e:
        print(f"❌ 读取失败: {e}")
        return 2

    missing = sorted(b_titles - c_titles)
    added = sorted(c_titles - b_titles)
    print(f"备份唯一标题:   {len(b_titles)}")
    print(f"当前唯一标题:   {len(c_titles)}")
    print(f"缺失（备份有当前无）: {len(missing)}")
    print(f"新增（当前有备份无）: {len(added)}")
    if missing:
        print("\n❌ 零丢失门禁失败——以下标题在备份中存在但当前缺失（前 20 条）:")
        for t in missing[:20]:
            print(f"  - {t}")
        return 1
    print("\n✅ 零丢失通过：备份全部唯一标题仍存在于当前文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
