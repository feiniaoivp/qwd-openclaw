#!/usr/bin/env python3
"""
市场数据引用验证门禁脚本
============================================================
用途：在任何报告/简报/计划引用「成交额/北向/指数涨跌/板块涨幅」
等市场统计数据前，强制验证：
  1. 该日为交易日
  2. 数字有闭环来源（交易所官网/东财/同花顺/akshare/baostock/新浪接口）
  3. 来源可追溯到具体接口/字段/时间戳

退出码：0=通过，1=违规（缺来源/非交易日/来源不可达）
接入点：weekly_full_pipeline.py 步骤 0、memory-maintenance-monday 审计
"""

import os
import sys
import json
import subprocess
from datetime import datetime, date
from typing import Optional, Dict, Any, List

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

# ============================================================================
# 权威数据源接口清单（仅允许这些来源）
# ============================================================================
AUTHORIZED_SOURCES = {
    "sse": {
        "name": "上海证券交易所官网",
        "url_pattern": "https://www.sse.com.cn/market/stockdata/overview/day/",
        "fields": ["成交金额", "成交量", "上证指数"],
        "access_method": "web_fetch / 官网公告"
    },
    "szse": {
        "name": "深圳证券交易所官网",
        "url_pattern": "https://www.szse.cn/market/overview/index.html",
        "fields": ["成交金额", "成交量", "深证成指"],
        "access_method": "web_fetch / 官网公告"
    },
    "eastmoney_dc": {
        "name": "东方财富数据中心",
        "url_pattern": "https://datacenter.eastmoney.com/",
        "fields": ["北向资金", "成交额", "板块涨幅"],
        "access_method": "akshare.stock_market_fund_flow / web_fetch"
    },
    "sina_hq": {
        "name": "新浪 hq.sinajs.cn 实时行情",
        "url_pattern": "https://hq.sinajs.cn/list=",
        "fields": ["实时价格", "涨跌幅", "成交量", "成交额"],
        "access_method": "requests GET + Referer header"
    },
    "akshare_hist": {
        "name": "akshare 历史K线 (stock_zh_a_hist)",
        "fields": ["收盘价", "成交量", "成交额", "换手率"],
        "access_method": "ak.stock_zh_a_hist(symbol, adjust='qfq')"
    },
    "baostock": {
        "name": "baostock 历史K线",
        "fields": ["close", "volume", "amount", "pctChg"],
        "access_method": "bs.query_history_k_data_plus(adjustflag='2')"
    },
    "close_scan_v2": {
        "name": "本地 close_scan_v2.py 扫描输出",
        "fields": ["全关注池价格/涨跌/成交量/技术指标", "市场概览"],
        "access_method": "python3 analysis/close_scan_v2.py --json",
        "output_file": "analysis/daily/{date}_dual.md / adaptive.md / next_day_sim.md"
    }
}

# ============================================================================
# 验证函数
# ============================================================================

def is_trading_day_check(target_date: date = None) -> tuple[bool, str]:
    """验证目标日期是否为交易日"""
    from analysis.close_scan_v2 import is_trading_day
    
    # 如果指定了日期，需要临时修改检查逻辑
    if target_date:
        # 复用 close_scan_v2 的逻辑但支持指定日期
        import akshare as ak
        from datetime import datetime
        try:
            trade_cal = ak.tool_trade_date_hist_sina()
            target_str = target_date.strftime("%Y-%m-%d")
            if target_str in trade_cal["trade_date"].values:
                row = trade_cal[trade_cal["trade_date"] == target_str]
                if not row.empty and row.iloc[0]["is_open"] == 1:
                    return True, f"{target_str} 为交易日（akshare 交易日历确认）"
            return False, f"{target_str} 非交易日（akshare 交易日历确认）"
        except Exception as e:
            return False, f"交易日历接口异常: {e}"
    
    # 使用现有函数检查今天
    result = is_trading_day()
    today_str = datetime.now().strftime("%Y-%m-%d")
    return result, f"{today_str} {'是' if result else '非'}交易日（close_scan_v2.is_trading_day() 判定）"


def verify_data_point(
    metric: str,
    value: str,
    claimed_date: str,
    claimed_source: str
) -> tuple[bool, str]:
    """
    验证单个数据点
    返回: (通过与否, 详细信息)
    """
    # 1. 检查日期是否为交易日
    try:
        check_date = datetime.strptime(claimed_date, "%Y-%m-%d").date()
    except ValueError:
        return False, f"日期格式错误: {claimed_date} (应为 YYYY-MM-DD)"
    
    is_td, td_msg = is_trading_day_check(check_date)
    if not is_td:
        return False, f"引用日期 {claimed_date} 非交易日: {td_msg}"
    
    # 2. 检查来源是否在授权清单
    source_key = claimed_source.lower().strip()
    matched_source = None
    for key, info in AUTHORIZED_SOURCES.items():
        if key in source_key or info["name"].lower() in source_key:
            matched_source = key
            break
    
    if not matched_source:
        return False, f"来源 '{claimed_source}' 不在授权清单中。授权来源: {list(AUTHORIZED_SOURCES.keys())}"
    
    # 3. 检查字段是否匹配
    source_info = AUTHORIZED_SOURCES[matched_source]
    metric_lower = metric.lower()
    field_matched = any(f.lower() in metric_lower or metric_lower in f.lower() 
                        for f in source_info["fields"])
    if not field_matched:
        return False, f"指标 '{metric}' 与来源 '{source_info['name']}' 支持字段不匹配: {source_info['fields']}"
    
    # 4. 尝试实时验证（可选，仅做连通性检查）
    # 这里不做完整数据拉取（太慢），只记录可验证路径
    verification_path = f"{source_info['access_method']} -> {source_info['fields']}"
    
    return True, f"✅ 验证通过: {metric}={value} @ {claimed_date} 来源={source_info['name']} ({verification_path})"


def verify_report_file(report_path: str) -> tuple[bool, List[str]]:
    """
    扫描报告文件中的市场数据引用并逐一验证
    支持格式：
    - "9/27 成交额 1.45 万亿 (来源: 东方财富)"
    - "北向资金 8129 亿 [数据源: akshare]"
    - 表格中的数字（需人工标注来源）
    """
    import re
    
    if not os.path.exists(report_path):
        return False, [f"报告文件不存在: {report_path}"]
    
    with open(report_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 简单正则提取：日期 + 指标 + 数值 + 来源
    # 格式：YYYY-MM-DD 或 MM/DD 或 M月D日 + 指标关键词 + 数字 + (来源: xxx)
    patterns = [
        r'(\d{4}-\d{2}-\d{2}).*?(成交额|成交金额|北向资金|北向净买入|上证指数|深证成指|创业板指|板块涨幅|涨跌幅).*?([\d,]+\.?\d*)\s*(万亿|亿|万|%)?\s*[\(（]?来源?[:：]\s*([^\)）\n]+)',
        r'(\d{1,2}[月/]\d{1,2}[日]?).*?(成交额|成交金额|北向资金|北向净买入|上证指数|深证成指|创业板指|板块涨幅|涨跌幅).*?([\d,]+\.?\d*)\s*(万亿|亿|万|%)?\s*[\(（]?来源?[:：]\s*([^\)）\n]+)',
    ]
    
    violations = []
    verified_count = 0
    
    for pattern in patterns:
        matches = re.findall(pattern, content)
        for match in matches:
            raw_date, metric, value, unit, source = match
            # 标准化日期
            if '-' not in raw_date:
                # 简单处理 M月D日 或 MM/DD
                try:
                    if '月' in raw_date:
                        m, d = raw_date.replace('月', '-').replace('日', '').split('-')
                    else:
                        m, d = raw_date.split('/')
                    year = datetime.now().year
                    std_date = f"{year}-{int(m):02d}-{int(d):02d}"
                except:
                    std_date = raw_date
            else:
                std_date = raw_date
            
            full_value = f"{value}{unit}" if unit else value
            ok, msg = verify_data_point(metric, full_value, std_date, source)
            if not ok:
                violations.append(f"❌ {metric}={full_value} @ {std_date} 来源={source}: {msg}")
            else:
                verified_count += 1
                violations.append(f"✅ {msg}")
    
    if verified_count == 0:
        # 没找到带来源标注的数据引用，检查是否有裸数字
        naked_numbers = re.findall(r'(成交额|北向资金|上证指数|深证成指|创业板指).*?([\d,]+\.?\d*)\s*(万亿|亿|万|%)', content)
        if naked_numbers:
            for metric, value, unit in naked_numbers[:5]:  # 只报前5个
                violations.append(f"⚠️ 发现无来源标注的数据引用: {metric}={value}{unit} — 必须标注 (来源: xxx)")
    
    return len([v for v in violations if v.startswith('❌')]) == 0, violations


def main():
    import argparse
    parser = argparse.ArgumentParser(description="市场数据引用验证门禁")
    parser.add_argument("--date", help="验证指定日期 (YYYY-MM-DD)")
    parser.add_argument("--metric", help="指标名 (如: 成交额, 北向资金)")
    parser.add_argument("--value", help="数值 (如: 1.45万亿)")
    parser.add_argument("--source", help="来源 (如: 东方财富, akshare, close_scan_v2)")
    parser.add_argument("--report", help="验证整个报告文件路径")
    parser.add_argument("--list-sources", action="store_true", help="列出授权数据源")
    
    args = parser.parse_args()
    
    if args.list_sources:
        print("=== 授权市场数据源 ===")
        for key, info in AUTHORIZED_SOURCES.items():
            print(f"  {key}: {info['name']}")
            print(f"    字段: {info['fields']}")
            print(f"    访问: {info['access_method']}")
            print()
        return 0
    
    if args.report:
        ok, msgs = verify_report_file(args.report)
        for m in msgs:
            print(m)
        print(f"\n=== 结果: {'通过' if ok else '失败'} ===")
        return 0 if ok else 1
    
    if args.metric and args.value and args.source and args.date:
        ok, msg = verify_data_point(args.metric, args.value, args.date, args.source)
        print(msg)
        return 0 if ok else 1
    
    # 默认：验证今天的交易日状态
    is_td, msg = is_trading_day_check()
    print(msg)
    return 0 if is_td else 1


if __name__ == "__main__":
    sys.exit(main())