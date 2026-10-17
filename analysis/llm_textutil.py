#!/usr/bin/env python3
"""LLM 输出清洗工具（SSOT，2026-10-17 P1-D 落地，借鉴 TradingAgents-CN-studio core/textutil.py）。

用途：解析 agent/cron 产出的 LLM 文本时，统一处理两类污染：
  1. dict/list 字面量转储（str(dict) 形态）→ ast.literal_eval 还原后取主字段
  2. 字面量转义残留（\\n、\\t、\\'、双重转义）→ 迭代还原
  3. 成对标记块提取（如 =====X=====...=====X=====，2026-10-06 教训的标准实现）

规约：任何需要解析 LLM 结构化输出的新脚本必须 import 本模块，禁止本地复刻
（同 is_trading_day 三实现教训）。存量已实现配对标记解析的脚本
（close_scan_v2/daily_push/premarket_pipeline）暂不回改，仅新代码接入。

自检：python3 analysis/llm_textutil.py   （exit 0=全部通过）
"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ast
import re
from typing import Optional

# dict 转储中的主字段优先级（裁决/正文优先，命中即返回）
PRIMARY_KEYS = ("judge_decision", "current_response", "decision",
                "report", "content", "summary", "message")


def unescape(text: str, rounds: int = 3) -> str:
    """迭代还原字面量转义（\\n、\\t、\\'、\\"），处理双重转义。"""
    t = text
    for _ in range(rounds):
        if "\\n" not in t and "\\t" not in t:
            break
        t = t.replace("\\n", "\n").replace("\\t", "\t")
    return t.replace("\\'", "'").replace('\\"', '"')


def dict_to_md(obj: dict) -> str:
    """agent 结果 dict → markdown：主字段为正文；无主字段则长字符串字段拼小节。"""
    for key in PRIMARY_KEYS:
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return unescape(v).strip()
    parts = []
    for k, v in obj.items():
        if isinstance(v, str) and len(v.strip()) > 20:
            parts.append("## " + str(k) + "\n\n" + unescape(v).strip())
    return "\n\n".join(parts) if parts else unescape(str(obj))


def normalize_llm_text(text: str) -> str:
    """入口：dict 转储 → 结构化还原；普通文本 → 转义还原。"""
    t = (text or "").strip()
    if t.startswith("{") and t.endswith("}"):
        try:
            obj = ast.literal_eval(t)
            if isinstance(obj, dict):
                return dict_to_md(obj)
        except (ValueError, SyntaxError):
            pass
    return unescape(t)


def extract_marked(text: str, begin: str, end: str) -> Optional[str]:
    """成对标记块提取（DOTALL 语义），返回标记之间内容（strip 后）。

    2026-10-06 教训：解析多块输出必须用成对标记正则，禁用 find('{') 到末尾。
    """
    if not begin or not end:
        return None
    m = re.search(re.escape(begin) + r"(.*?)" + re.escape(end), text, re.DOTALL)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------- 自检
def _selftest() -> int:
    cases = []

    # 1. 单重转义 \n
    out = unescape("line1\\nline2")
    cases.append(("unescape 单重", out == "line1\nline2", repr(out)))

    # 2. 双重转义 \\n（先还原一层后仍是字面量，需再还原）
    out = unescape("a\\\\nb")
    cases.append(("unescape 双重", "\\n" not in out and "\\t" not in out, repr(out)))

    # 3. dict 转储取主字段
    dump = "{'judge_decision': '看空\\\\n跌破EMA20', 'history': 'x'}"
    out = normalize_llm_text(dump)
    cases.append(("dict 主字段", out == "看空\n跌破EMA20", repr(out)))

    # 4. dict 无主字段 → 拼小节
    dump2 = "{'alpha': 'short', 'beta': '" + "x" * 30 + "'}"
    out = normalize_llm_text(dump2)
    cases.append(("dict 拼小节", "## beta" in out and "## alpha" not in out, repr(out)))

    # 5. 普通 JSON 字符串（非 python dict 字面量）→ 走 unescape 兜底
    out = normalize_llm_text('{"a": "含\\\\n转义"}')
    cases.append(("json 兜底", "{" in out, repr(out)))

    # 6. 成对标记提取（多块场景取第一个）
    blob = "pre =====A=====\n{\"k\":1}\n=====A===== tail =====A=====\nsecond\n=====A====="
    out = extract_marked(blob, "=====A=====", "=====A=====")
    cases.append(("成对标记", out == '{"k":1}', repr(out)))

    # 7. 标记不成对 → None
    out = extract_marked("only =====B===== no end", "=====B=====", "=====B=====")
    cases.append(("标记不成对", out is None, repr(out)))

    failed = [c for c in cases if not c[1]]
    for name, ok, got in cases:
        print(("  PASS " if ok else "  FAIL ") + name + ("  got=" + got if not ok else ""))
    print("llm_textutil selftest: %d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_selftest())
