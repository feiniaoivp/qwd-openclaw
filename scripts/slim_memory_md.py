#!/usr/bin/env python3
"""
MEMORY.md 瘦身拆分脚本（2026-11-11，OpenClaw 2026.11.3 升级配套）

目标：MEMORY.md 380KB / 1681 行，注入截断 91% → 拆为
  主文件（保留 KEEP 清单内章节）+ memory/archive/ 四个分册

设计原则：
  1. 行数守恒：归档行数 + 主文件行数 == 原始行数（含空行）
  2. 幂等：重复执行结果一致（归档文件按主题整体重写）
  3. 可逆：原始文件已备份到 memory/backup/
  4. 不丢内容：未命中任何规则的行，默认留在主文件
"""
import os
import re
import sys
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
SRC = os.path.join(WORKSPACE, "MEMORY.md")
ARCHIVE_DIR = os.path.join(WORKSPACE, "memory", "archive")

# ---------------------------------------------------------------- 归档规则
# 顺序敏感：先匹配先归。每条 (目标文件, 判定函数)
# 判定函数接收 (章节标题, 章节正文)，返回 True 则归入该分册。


def is_investment_philosophy(title, body):
    return "投资理念归纳" in title


def is_skill_profile(title, body):
    keys = ("技能档案", "Ontology 数据位置", "trader-stock-picks 操盘手选股",
            "030复盘 技能档案")
    return any(k in title for k in keys)


def is_digest_log(title, body):
    # 「蒸馏补充」类流水账 + 「投资理念归纳」下的日期要点小节
    if "蒸馏补充" in title:
        return True
    if re.match(r"^###\s+\d{4}-\d{2}-\d{2}\s*要点", title):
        return True
    return False


def is_incident(title, body):
    keys = ("重大事件", "故障", "整改周复盘", "每周回测", "盘前管道",
            "待办看板", "基建故障", "cron 集群伪造数据")
    return any(k in title for k in keys)


def is_build_record(title, body):
    """2026-06~08 的脚本搭建/回测/数据源切换记录（历史基建，非现行规则）"""
    keys = ("close_scan_v2", "自适应策略分配器", "跨周期多窗口验证",
            "五策略全量回测", "数据源切换", "关注列表扩展",
            "数据源/行情链路关键升级", "双策略最优扫描",
            "参数自调优闭环", "量化系统全链路文件索引", "调参对照差异榜",
            "Harness 工程落地", "清单漂移修复", "新股数据为0根因",
            "删除机电B股", "validation 合并机制", "竞价/盘前 cron",
            "中联信号HOLD", "三因素共振买入门控", "斐波那契扩展位",
            "盘后监督报告", "技能库真实变更", "定时任务全部重建",
            "记忆系统修复", "周一审计异常", "周复盘")
    return any(k in title for k in keys)


ARCHIVE_RULES = [
    ("build-records-2026-06~08.md", "脚本搭建与回测历史（2026-06~08）", is_build_record),
    ("investment-philosophy.md", "投资理念归纳", is_investment_philosophy),
    ("skill-profiles.md", "技能档案与工具文档", is_skill_profile),
    ("digest-logs-2026-09~11.md", "蒸馏补充流水账", is_digest_log),
    ("incidents-2026-07~09.md", "重大事件与故障复盘", is_incident),
]

# ---------------------------------------------------------------- 解析
def split_sections(text):
    """按 '## ' / '### ' 标题切分，但只在「父节本身无子标题」时才用 ###。

    规则：先按 ## 切出父节；若父节内部含 ### 子标题且父节不在 KEEP 白名单，
    则再按 ### 细分为多个可独立归档的小节（每个小节带上父节名做上下文）。
    """
    lines = text.split("\n")

    # 第一轮：按 ##
    parents = []
    cur_title = ""
    cur_lines = []
    for ln in lines:
        if ln.startswith("## "):
            parents.append((cur_title, cur_lines))
            cur_title = ln
            cur_lines = [ln]
        else:
            cur_lines.append(ln)
    parents.append((cur_title, cur_lines))

    # 第二轮：仅在「父节自身不命中任何归档规则」且含 ### 子标题时，按 ### 细分
    out = []
    for ptitle, plines in parents:
        ptext = "\n".join(plines)
        parent_hits = any(pred(ptitle, ptext) for _, _, pred in ARCHIVE_RULES)
        if parent_hits:
            # 父节整体命中归档，不拆
            out.append((ptitle, plines))
            continue
        has_sub = any(l.startswith("### ") for l in plines[1:])
        if not has_sub:
            out.append((ptitle, plines))
            continue
        # 拆分：父节标题行作为引言，随后按 ### 切
        preamble = [plines[0]] if ptitle else []
        sub_title = None
        sub_lines = []
        for ln in plines[1:]:
            if ln.startswith("### "):
                if sub_title is not None:
                    out.append((sub_title, sub_lines))
                elif preamble:
                    out.append((ptitle, preamble))
                    preamble = []
                sub_title = ln
                sub_lines = [ln]
            else:
                if sub_title is None:
                    preamble.append(ln)
                else:
                    sub_lines.append(ln)
        if sub_title is not None:
            out.append((sub_title, sub_lines))
        elif preamble:
            out.append((ptitle, preamble))
    return out


def main():
    if not os.path.exists(SRC):
        print(f"❌ 源文件不存在: {SRC}")
        return 1

    raw = open(SRC, encoding="utf-8").read()
    orig_lines = raw.count("\n") + (0 if raw.endswith("\n") else 1)
    sections = split_sections(raw)

    buckets = {fname: [] for fname, _, _ in ARCHIVE_RULES}
    keep = []

    for title, body in sections:
        line_text = "\n".join(body)
        placed = False
        for fname, _, pred in ARCHIVE_RULES:
            if pred(title, line_text):
                buckets[fname].append((title, body))
                placed = True
                break
        if not placed:
            keep.append((title, body))

    # ------------------------------------------------------------ 写归档
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stats = {}
    for fname, desc, _ in ARCHIVE_RULES:
        secs = buckets[fname]
        if not secs:
            stats[fname] = 0
            continue
        out = [f"# {desc}", "",
               f"> 从 MEMORY.md 归档，归档时间：{stamp}",
               f"> 共 {len(secs)} 个小节。按需检索，不常驻上下文。", ""]
        n = 0
        for title, body in secs:
            out.extend(body)
            if not body or body[-1].strip():
                out.append("")
            n += len(body)
        with open(os.path.join(ARCHIVE_DIR, fname), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        stats[fname] = (len(secs), n)

    # ------------------------------------------------------------ 写主文件
    head = [s for s in keep if s[0] == ""]
    rest = [s for s in keep if s[0] != ""]

    index = ["", "## 📚 记忆归档索引（2026-11-11 拆分）", "",
             "以下内容已归档到 `memory/archive/`，不在上下文常驻。"
             "需要时用 memory_search 或直接读取：", ""]
    for fname, desc, _ in ARCHIVE_RULES:
        if stats.get(fname):
            cnt, _n = stats[fname]
            index.append(f"- `memory/archive/{fname}` — {desc}（{cnt} 个小节）")
    index.append("")

    out_lines = []
    for title, body in head:
        out_lines.extend(body)
    out_lines.extend(index)
    for title, body in rest:
        out_lines.extend(body)

    new_text = "\n".join(out_lines)
    open(SRC, "w", encoding="utf-8").write(new_text)

    # ------------------------------------------------------------ 守恒校验
    arch_total = sum(v[1] for v in stats.values() if v)
    new_lines = new_text.count("\n") + (0 if new_text.endswith("\n") else 1)
    index_lines = len(index)

    print("=== 归档统计 ===")
    for fname, desc, _ in ARCHIVE_RULES:
        v = stats.get(fname)
        if v:
            print(f"  {fname:38s} {v[0]:3d} 节 / {v[1]:5d} 行")
        else:
            print(f"  {fname:38s} (无命中)")
    print()
    print(f"原始行数      : {orig_lines}")
    print(f"主文件新行数  : {new_lines}")
    print(f"归档总行数    : {arch_total}")
    print(f"索引块行数    : {index_lines}")
    print(f"守恒校验      : {new_lines} + {arch_total} - {index_lines} = "
          f"{new_lines + arch_total - index_lines} (原始 {orig_lines})")
    delta = new_lines + arch_total - index_lines - orig_lines
    print(f"偏差          : {delta} 行 {'✅' if abs(delta) <= 2 else '⚠️'}")
    print()
    print(f"主文件体积    : {len(new_text.encode('utf-8')):,} 字节 "
          f"（原 {len(raw.encode('utf-8')):,}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
