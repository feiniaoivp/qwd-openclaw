#!/usr/bin/env python3
"""
会话级前置响应门禁：扫描即将输出的文本，拦截「裸数字+市场关键词」且无来源标注的违规内容。
============================================================
用途：在助手回复发送前运行（通过 wrapper/心跳/pre-commit 钩子），
      发现违规 → 返回违规列表，阻止发送，强制助手先跑验证工具。

退出码：0=通过，1=发现违规（需修正后重试）
接入点：心跳检查、会话发送前钩子、手动自检
"""

import re
import sys
import json
from typing import List, Dict, Tuple, Optional

# ============================================================================
# 违规检测规则
# ============================================================================

# 市场统计关键词（触发检测）
MARKET_KEYWORDS = [
    r'成交额', r'成交金额', r'成交量',
    r'北向资金', r'北向净买入', r'北向流入', r'北向流出',
    r'上证指数', r'沪指', r'上证',
    r'深证成指', r'深成指', r'深证',
    r'创业板指', r'创指',
    r'板块涨幅', r'板块跌幅', r'涨幅榜', r'跌幅榜',
    r'主力资金', r'主力净流入', r'主力净流出',
    r'融资余额', r'融券余额', r'两融',
    r'换手率', r'量比',
    r'市盈率', r'PE', r'PB', r'市净率',
]

# 数字模式（含单位）
NUMBER_PATTERN = r'[\d,]+\.?\d*\s*(万亿|亿|万|千万|百万|%|点)?'

# 来源标注模式（合规标记）
SOURCE_PATTERNS = [
    r'\[来源\s*[:：]\s*[^\]]+\]',           # [来源: xxx]
    r'\(来源\s*[:：]\s*[^\)]+\)',           # (来源: xxx)
    r'来源\s*[:：]\s*\S+',                  # 来源: xxx
    r'数据源\s*[:：]\s*\S+',                # 数据源: xxx
    r'data\s*[:：]\s*\S+',                  # data: xxx (英文)
    r'source\s*[:：]\s*\S+',                # source: xxx
]

# 授权源 key（用于二次校验）
AUTHORIZED_SOURCE_KEYS = {
    'sse', 'szse', 'eastmoney_dc', 'eastmoney', '东财', '东方财富',
    'sina_hq', 'sina', '新浪', 'hq.sinajs.cn',
    'akshare_hist', 'akshare', 'ak',
    'baostock', 'baostock',
    'close_scan_v2', 'close_scan', 'dual.md', 'adaptive.md', 'next_day_sim.md',
    '官网', '交易所', '上交所', '深交所',
}

# ============================================================================
# 核心检测逻辑
# ============================================================================

def has_source_annotation(text: str, start: int, end: int, window: int = 120) -> bool:
    """检查数字前后 window 字符内是否有来源标注"""
    left = max(0, start - window)
    right = min(len(text), end + window)
    context = text[left:right]
    for pat in SOURCE_PATTERNS:
        if re.search(pat, context, re.IGNORECASE):
            return True
    return False


def is_authorized_source(text: str, start: int, end: int, window: int = 120) -> bool:
    """检查上下文是否提到授权源 key"""
    left = max(0, start - window)
    right = min(len(text), end + window)
    context = text[left:right].lower()
    for key in AUTHORIZED_SOURCE_KEYS:
        if key.lower() in context:
            return True
    return False


def find_market_data_violations(text: str) -> List[Dict]:
    """
    扫描文本，返回违规列表。
    每个违规包含：keyword, number, position, context, suggestion
    """
    violations = []
    
    # 组合正则：关键词 + 任意字符(≤80) + 数字
    for kw in MARKET_KEYWORDS:
        pattern = rf'({kw}).{{0,80}}?({NUMBER_PATTERN})'
        for match in re.finditer(pattern, text, re.IGNORECASE):
            kw_match = match.group(1)
            num_match = match.group(2)
            kw_start, kw_end = match.span(1)
            num_start, num_end = match.span(2)
            
            # 检查是否已有来源标注
            if has_source_annotation(text, kw_start, num_end):
                continue  # 合规，跳过
            
            # 检查是否在代码块/表格中（通常已有结构化来源）
            # 简单启发：前后有 | 或 ``` 或 ``` 认为可能在表格/代码块
            ctx_left = max(0, kw_start - 200)
            ctx_right = min(len(text), num_end + 200)
            context = text[ctx_left:ctx_right]
            
            # 如果在 markdown 表格行内（含 | 分隔），暂不判违规（表格通常有表头说明来源）
            if '|' in context and context.count('|') >= 4:
                # 但仍需检查表头是否有"来源"列
                if '来源' not in context and 'source' not in context.lower():
                    pass  # 表格无来源列，仍算违规
                else:
                    continue
            
            # 如果在代码块内，跳过
            if '```' in text[max(0, kw_start-500):kw_start] and '```' in text[num_end:num_end+500]:
                continue
            
            # 判定为违规
            violations.append({
                'keyword': kw_match,
                'number': num_match,
                'position': (kw_start, num_end),
                'context': context[:300].replace('\n', ' '),
                'suggestion': f'请在 "{kw_match} {num_match}" 后添加来源标注，格式：[来源: <授权源key>, 文件路径:行号]'
            })
    
    # 另外检测：裸数字 + 单位（万亿/亿/万/%）且上下文有市场词但无关键词匹配
    # 例如："1.45 万亿成交额" 反序
    reverse_pattern = rf'({NUMBER_PATTERN}).{{0,30}}?({"|".join(MARKET_KEYWORDS)})'
    for match in re.finditer(reverse_pattern, text, re.IGNORECASE):
        num_match = match.group(1)
        kw_match = match.group(2)
        num_start, num_end = match.span(1)
        kw_start, kw_end = match.span(2)
        
        if has_source_annotation(text, num_start, kw_end):
            continue
        
        violations.append({
            'keyword': kw_match,
            'number': num_match,
            'position': (num_start, kw_end),
            'context': text[max(0, num_start-150):min(len(text), kw_end+150)].replace('\n', ' ')[:300],
            'suggestion': f'请在 "{num_match} {kw_match}" 添加来源标注'
        })
    
    # 去重：同一位置只保留第一个
    unique = []
    seen_pos = set()
    for v in violations:
        pos_key = (v['position'][0], v['position'][1])
        if pos_key not in seen_pos:
            seen_pos.add(pos_key)
            unique.append(v)
    
    return unique


def check_text(text: str) -> Tuple[bool, List[Dict]]:
    """主检查函数，返回 (是否通过, 违规列表)"""
    violations = find_market_data_violations(text)
    return len(violations) == 0, violations


def format_violations(violations: List[Dict]) -> str:
    """格式化违规输出"""
    if not violations:
        return "✅ 通过：未发现裸数字市场数据引用"
    
    lines = ["❌ 发现市场数据引用违规（裸数字无来源标注）：", ""]
    for i, v in enumerate(violations, 1):
        lines.append(f"  {i}. 关键词: {v['keyword']} | 数值: {v['number']}")
        lines.append(f"     上下文: ...{v['context']}...")
        lines.append(f"     建议: {v['suggestion']}")
        lines.append("")
    lines.append("🔧 修正方法：")
    lines.append("  1. 运行 python3 scripts/verify_market_data.py --report <文件> 验证现有报告")
    lines.append("  2. 或运行 python3 analysis/close_scan_v2.py 获取真实数据")
    lines.append("  3. 在数字后添加 [来源: <授权源key>, 文件路径:行号]")
    lines.append("")
    lines.append("📋 授权源 key: sse, szse, eastmoney_dc, sina_hq, akshare_hist, baostock, close_scan_v2")
    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="会话级前置响应门禁：扫描裸数字市场数据引用")
    parser.add_argument("--text", help="直接检查指定文本（命令行参数）")
    parser.add_argument("--file", help="检查指定文件内容")
    parser.add_argument("--stdin", action="store_true", help="从 stdin 读取文本")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    
    args = parser.parse_args()
    
    if args.stdin:
        text = sys.stdin.read()
    elif args.file:
        with open(args.file, 'r', encoding='utf-8') as f:
            text = f.read()
    elif args.text:
        text = args.text
    else:
        print("用法: python3 pre_response_guard.py --text \"...\" | --file <path> | --stdin", file=sys.stderr)
        return 1
    
    ok, violations = check_text(text)
    
    if args.json:
        print(json.dumps({"pass": ok, "violations": violations}, ensure_ascii=False, indent=2))
    else:
        print(format_violations(violations))
    
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())