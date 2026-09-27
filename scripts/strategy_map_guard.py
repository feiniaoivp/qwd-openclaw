#!/usr/bin/env python3
"""策略映射 / 关注池口径一致性门禁 (strategy_map_guard.py)
==============================================================
背景 (2026-09-27):
    关注池口径在 4 处共存，曾出现「代码已是 35 只、文档还写 27 只」的
    静默不一致，导致人工按旧清单判断（误以为回测范围有污染）。
    按 AGENTS.md「教训 → 三层防线」铁律，固化为可执行门禁：
    ①文档强制模板 (TOOLS.md) ②本脚本 (退出码 0/1) ③接入周六流水线 + 周审计

检查项 (任一不一致 => exit 1):
    A. data/adaptive_strategy_map.json  key 集合 == fib_extension_scan.WATCHLIST
    B. analysis_summary_all.md 中的股票集合 ⊆ WATCHLIST 且数量 == len(WATCHLIST)
    C. memory/watchlist.md 头部声明的只数 == len(WATCHLIST)
    D. MEMORY.md 的 "Definitive - N stocks" 声明 == len(WATCHLIST)

用法:
    python3 scripts/strategy_map_guard.py            # 检查，违规 exit 1
    python3 scripts/strategy_map_guard.py --quiet    # 只在失败时输出
"""
import os
import re
import sys
import json

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MAP_FILE = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")
SUMMARY_MD = os.path.join(WORKSPACE, "analysis_summary_all.md")
WATCHLIST_MD = os.path.join(WORKSPACE, "memory", "watchlist.md")
MEMORY_MD = os.path.join(WORKSPACE, "MEMORY.md")


def load_watchlist():
    """从权威源 fib_extension_scan.WATCHLIST 读取关注池。"""
    from analysis.fib_extension_scan import WATCHLIST
    return set(WATCHLIST.keys())


def check_map(watch: set):
    """A. 策略映射 key 集合 == WATCHLIST"""
    if not os.path.exists(MAP_FILE):
        return [f"❌ {MAP_FILE} 不存在"]
    with open(MAP_FILE, encoding="utf-8") as f:
        m = json.load(f)
    mk = set(m.keys())
    errs = []
    if mk - watch:
        errs.append(f"  映射含非关注池股票 (MAP-ONLY): {sorted(mk - watch)}")
    if watch - mk:
        errs.append(f"  关注池股票缺策略映射 (WATCHLIST-ONLY): {sorted(watch - mk)}")
    if errs:
        errs.insert(0, f"❌ [A] adaptive_strategy_map.json ({len(mk)}只) != WATCHLIST ({len(watch)}只)")
        return errs
    return []


def check_summary(watch: set):
    """B. analysis_summary_all.md 股票集合 == WATCHLIST"""
    if not os.path.exists(SUMMARY_MD):
        return [f"❌ {SUMMARY_MD} 不存在"]
    codes = set()
    with open(SUMMARY_MD, encoding="utf-8") as f:
        for line in f:
            mt = re.match(r"^\|\s*(\d{6})\s*\|", line)
            if mt:
                codes.add(mt.group(1))
    errs = []
    if codes - watch:
        errs.append(f"  含非关注池股票: {sorted(codes - watch)}")
    if watch - codes:
        errs.append(f"  缺少股票: {sorted(watch - codes)}")
    if errs:
        errs.insert(0, f"❌ [B] analysis_summary_all.md ({len(codes)}只) != WATCHLIST ({len(watch)}只)")
        return errs
    return []


def check_declared_count(path: str, pattern: str, watch: set, label: str):
    """C/D. 声明只数 == WATCHLIST 大小, 且文档清单段的股票代码集合 == WATCHLIST.

    2026-09-27 加强: 旧版只比数字, 软不一致(数量凑对但名单含幽灵股/漏真股)会漏报.
    """
    if not os.path.exists(path):
        return [f"❌ {path} 不存在"]
    with open(path, encoding="utf-8") as f:
        content = f.read()
    errs = []
    mt = re.search(pattern, content)
    if not mt:
        return [f"⚠️ [{label}] {os.path.basename(path)} 未找到只数声明 (跳过)"]
    declared = int(mt.group(1))
    if declared != len(watch):
        errs.append(f"❌ [{label}] {os.path.basename(path)} 声明 {declared} 只 != WATCHLIST {len(watch)} 只")

    # 集合校验: 只取"清单行"里的代码, 避免历史/说明文字里的旧代码误报
    # - MEMORY.md: 只取 "*   **板块:**" 开头的清单行, 跳过 Note 段
    # - watchlist.md: 取 Tier 表格行 "| 代码 | 名称 |"
    if os.path.basename(path) == "MEMORY.md":
        seg = content[mt.end():]
        nxt = re.search(r"\n##\s", seg)
        if nxt:
            seg = seg[: nxt.start()]
        # 只保留"持仓清单行": 以 "*   **" 开头 + 含(6位代码),
        # 且排除 Note / 含"撤除|移除|清空"的历史说明行(否则历史代码误报)
        skip_words = ("撤除", "移除", "清空", "NOTE", "Note")
        lines = []
        for l in seg.splitlines():
            st = l.strip()
            if not (st.startswith("*   **") and "(" in l):
                continue
            if any(w in l for w in skip_words):
                continue
            lines.append(l)
        seg = "\n".join(lines)
    elif os.path.basename(path) == "watchlist.md":
        lines = [l for l in content.splitlines() if re.match(r"^\|\s*\d{6}\s*\|", l)]
        seg = "\n".join(lines)
    else:
        seg = content
    codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", seg))
    extra = codes - watch
    missing = watch - codes
    if extra:
        errs.append(f"❌ [{label}] {os.path.basename(path)} 含非关注池代码: {sorted(extra)}")
    if missing:
        errs.append(f"❌ [{label}] {os.path.basename(path)} 缺少关注池代码: {sorted(missing)}")
    return errs


def main():
    quiet = "--quiet" in sys.argv
    if not quiet:
        print("🚦 策略映射 / 关注池口径一致性门禁")
        print("=" * 60)

    try:
        watch = load_watchlist()
    except Exception as e:
        print(f"❌ 无法加载 WATCHLIST: {e}")
        return 1

    problems = []
    problems += check_map(watch)
    problems += check_summary(watch)
    problems += check_declared_count(
        WATCHLIST_MD, r"权威清单\s*(\d+)\s*只", watch, "C/memory/watchlist.md"
    )
    problems += check_declared_count(
        MEMORY_MD, r"Definitive\s*-\s*(\d+)\s*stocks", watch, "D/MEMORY.md"
    )

    if problems:
        print(f"\n🚨 口径不一致 (WATCHLIST={len(watch)}只):")
        for p in problems:
            print(p)
        print("\n处置建议: 以 fib_extension_scan.WATCHLIST 为单一事实来源，"
              "用 analyze_all_stocks.py 重生成摘要、核对文档只数。")
        return 1

    if not quiet:
        print(f"✅ 口径一致: adaptive_strategy_map / analysis_summary_all.md / "
              f"watchlist.md / MEMORY.md 均 == WATCHLIST ({len(watch)}只)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
