#!/usr/bin/env python3
"""
encoding_guard — 文本编码污染护栏
====================================================
背景: 2026-09-11 发现 analysis/service.py 等文件中硬编码了 U+FFFD 替换字符,
      导致所有信号标签(如 「🟡 关注」「强烈买入」)被污染, 并进一步污染
      下游 LLM prompt / 报告 / 规则匹配。

本模块提供三层保护:
  1. sanitize(text)         — 清除字符串中的 U+FFFD 及常见乱码控制字符
  2. is_clean(text)         — 校验, 返回 (ok, 问题列表)
  3. assert_clean(...)      — 写入前断言, 检出即抛异常/记日志

用法:
    from encoding_guard import sanitize, is_clean, guard_write

    level = sanitize(raw_level)            # 生成信号标签时清洗
    ok, issues = is_clean(report_text)     # 写入文件前校验
    guard_write(path, report_text)         # 安全写文件(带校验与告警)

设计原则:
  - 只删「孤立的」U+FFFD (前后为空白/标点/行首行尾), 中文词内残留也删,
    因为 U+FFFD 在任何正常中文文本中都不应出现 —— 一律视为污染。
  - 不静默吞掉问题: 检出时写日志, 便于追溯污染源头。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Iterable, Tuple

log = logging.getLogger(__name__)

# U+FFFD REPLACEMENT CHARACTER 及常见同形污染字符
POLLUTION_RE = re.compile(r"[\ufffd\ufeff]")

# 因删除污染字符可能留下的多余空格 (中文字符前后)
_SPACE_CLEANUP_RE = re.compile(
    r"[ \t]{2,}(?=[\u4e00-\u9fff\uff0c\u3002\uff1a\uff1b\uff08\uff09\u3010\u3011])"
)


def sanitize(text: str) -> str:
    """清除文本中的编码污染字符, 返回干净字符串。

    适用于: 生成信号标签、拼接 prompt、写出报告前。
    """
    if not isinstance(text, str):
        return text
    cleaned = POLLUTION_RE.sub("", text)
    cleaned = _SPACE_CLEANUP_RE.sub(" ", cleaned)
    # 清理行尾因删除产生的空格
    cleaned = "\n".join(line.rstrip() for line in cleaned.split("\n"))
    if cleaned != text:
        n = len(POLLUTION_RE.findall(text))
        log.warning("encoding_guard: 已清除 %d 个污染字符 (U+FFFD/BOM)", n)
    return cleaned


def is_clean(text: str) -> Tuple[bool, list]:
    """校验文本是否无污染, 返回 (ok, 问题描述列表)。"""
    issues = []
    if not isinstance(text, str):
        return True, issues
    matches = POLLUTION_RE.findall(text)
    if matches:
        issues.append(f"发现 {len(matches)} 个 U+FFFD/BOM 污染字符")
    # 抽样展示上下文, 便于定位
    for m in list(POLLUTION_RE.finditer(text))[:5]:
        i = m.start()
        ctx = text[max(0, i - 20): i + 20].replace("\n", "\\n")
        issues.append(f"  ...{ctx}...")
    return (not issues), issues


def assert_clean(text: str, label: str = "text") -> str:
    """校验并在发现污染时记日志告警(不抛异常, 保证管道不中断)。

    Returns: 清洗后的文本
    """
    ok, issues = is_clean(text)
    if not ok:
        log.error("encoding_guard: %s 存在编码污染! %s", label, "; ".join(issues))
    return sanitize(text)


def guard_write(path: str, content: str, label: str | None = None, encoding: str = "utf-8") -> bool:
    """安全写文件: 写前校验+清洗, 写后复查。返回是否原本干净。"""
    label = label or os.path.basename(path)
    was_clean, issues = is_clean(content)
    if not was_clean:
        log.error("encoding_guard: 写入 %s 前检出污染: %s", label, "; ".join(issues))
    content = sanitize(content)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding=encoding) as f:
        f.write(content)
    # 复查落盘内容
    with open(path, encoding=encoding) as f:
        verify = f.read()
    if not is_clean(verify)[0]:
        log.error("encoding_guard: %s 落盘后仍存在污染!", label)
        return False
    return was_clean


def scan_files(paths: Iterable[str]) -> dict:
    """扫描文件列表, 返回 {path: 污染字符数} (仅含有污染的)。"""
    hits = {}
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                n = len(POLLUTION_RE.findall(f.read()))
        except (UnicodeDecodeError, OSError) as e:
            log.warning("encoding_guard: 无法读取 %s: %s", p, e)
            continue
        if n:
            hits[p] = n
    return hits


# 标准信号标签白名单 —— 供上游校验 level 是否合法
VALID_LEVELS = {
    "强烈买入", "关注", "中性", "谨慎", "强烈卖出",
    "🟢 强烈买入", "🟡 关注", "⚪ 中性", "🟠 谨慎", "🔴 强烈卖出",
    "持有", "买入", "卖出", "观望",
}


def validate_level(level: str) -> str:
    """校验信号标签: 清洗后若仍不合法, 回退为纯文本形式并告警。"""
    if not isinstance(level, str):
        return "⚪ 中性"
    cleaned = sanitize(level).strip()
    if cleaned in VALID_LEVELS:
        return cleaned
    # 尝试去掉 emoji 前缀做二次匹配
    core = re.sub(r"^[\W_]+", "", cleaned).strip()
    for valid in VALID_LEVELS:
        if valid.endswith(core) or core == valid:
            return valid
    log.warning("encoding_guard: 无法识别的信号标签 %r, 回退为纯文本", level)
    return "⚪ 中性"


if __name__ == "__main__":
    # 自检
    dirty = "���🟢 � 强烈�买入"
    print("原始:", repr(dirty))
    print("清洗:", repr(sanitize(dirty)))
    print("校验:", is_clean(dirty))
    print("validate_level:", validate_level(dirty))
    print("validate_level(干净):", validate_level("🟢 强烈买入"))
