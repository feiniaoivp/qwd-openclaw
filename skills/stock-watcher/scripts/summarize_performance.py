#!/usr/bin/env python3
"""
Summarize the performance of all stocks in the watchlist.

数据源: 新浪实时行情 hq.sinajs.cn (授权源, 需 Referer 头)。
2026-10-11 变更: 弃用 10jqka.com.cn 抓取 —— 其页面为 JS 渲染, 静态 HTML 不含
任何百分比数字, 旧正则解析器对全部股票返回「行情数据暂不可用」(实测 35/35)。
新浪接口单次请求支持多代码批量, 每批 BATCH_SIZE 只, 批间暂停限速。
失败一律可见告警(stderr), 不静默吞异常。
"""
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import WATCHLIST_FILE

BATCH_SIZE = 20
BATCH_PAUSE_SEC = 0.5
HEADERS = {'Referer': 'https://finance.sina.com.cn'}
QUOTE_URL = 'https://hq.sinajs.cn/list='


def sina_prefix(code: str) -> str:
    """6位A股代码 -> 新浪市场前缀 (6=沪, 0/3=深, 4/8=北交所占位)."""
    c = code[0]
    if c in '68':
        return 'sh'
    if c in '03':
        return 'sz'
    return 'bj'  # hq.sinajs.cn 不支持北交所, 让失败可见而不是猜错市场


def load_watchlist():
    """读取 watchlist.txt, 返回 [(code, name), ...]."""
    stocks = []
    if not os.path.exists(WATCHLIST_FILE):
        print(f"[WARN] 自选股文件不存在: {WATCHLIST_FILE}", file=sys.stderr)
        return stocks
    with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('|')
            if len(parts) == 2 and re.fullmatch(r'\d{6}', parts[0].strip()):
                stocks.append((parts[0].strip(), parts[1].strip()))
            else:
                print(f"[WARN] 跳过格式异常行: {line!r}", file=sys.stderr)
    return stocks


def fetch_batch(codes):
    """批量抓取一批行情. 返回 {code: row|None}; 失败可见, 不抛出."""
    out = {c: None for c in codes}
    query = ','.join(sina_prefix(c) + c for c in codes)
    try:
        r = requests.get(QUOTE_URL + query, headers=HEADERS, timeout=10)
        r.raise_for_status()
        r.encoding = 'gbk'
    except Exception as e:  # noqa: BLE001 -- 网络层失败必须整批可见
        print(f"[WARN] 新浪批量请求失败 ({query}): {e}", file=sys.stderr)
        return out

    for line in r.text.splitlines():
        m = re.search(r'hq_str_(?:s[hz]|bj)(\d{6})="([^"]*)"', line)
        if not m:
            continue
        code, payload = m.group(1), m.group(2)
        fields = payload.split(',')
        if len(fields) < 32 or not fields[0]:
            print(f"[WARN] {code} 返回数据不完整: {payload[:60]!r}", file=sys.stderr)
            continue
        name, prev_raw, price_raw, date_str = fields[0], fields[2], fields[3], fields[30]
        try:
            price, prev = float(price_raw), float(prev_raw)
        except ValueError:
            print(f"[WARN] {code} 价格字段不可解析: {price_raw!r}/{prev_raw!r}", file=sys.stderr)
            continue
        if price <= 0 or prev <= 0:
            print(f"[WARN] {code} 价格异常(停牌或空值): price={price}, prev={prev}", file=sys.stderr)
            continue
        out[code] = {
            'name': name,
            'price': price,
            'prev_close': prev,
            'chg_pct': round((price / prev - 1) * 100, 2),
            'date': date_str,
        }

    missing = [c for c, v in out.items() if v is None]
    if missing:
        print(f"[WARN] 本批未返回数据: {missing}", file=sys.stderr)
    return out


def summarize_performance():
    stocks = load_watchlist()
    if not stocks:
        print('Watchlist is empty.')
        return

    rows = {}
    for i in range(0, len(stocks), BATCH_SIZE):
        batch = stocks[i:i + BATCH_SIZE]
        result = fetch_batch([c for c, _ in batch])
        rows.update(result)
        if i + BATCH_SIZE < len(stocks):
            time.sleep(BATCH_PAUSE_SEC)

    dates = sorted({r['date'] for r in rows.values() if r})
    as_of = '/'.join(dates) if dates else '未知'
    ok = sum(1 for r in rows.values() if r)
    print(f'自选股行情 ({ok}/{len(stocks)} 只) 数据日期: {as_of} 来源: sina_hq hq.sinajs.cn')
    print('-' * 60)
    for code, name in stocks:
        r = rows.get(code)
        if r:
            print(f"{code} {r['name']:<6} 收盘 {r['price']:>9.2f}  {r['chg_pct']:>+7.2f}%  [{r['date']}]")
        else:
            print(f"{code} {name:<6} 获取数据失败 (见 stderr 告警)")


if __name__ == '__main__':
    summarize_performance()
