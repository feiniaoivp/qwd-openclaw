#!/usr/bin/env python3
"""
电网装备/特高压出海板块 - 真实事件/公告数据源
======================================================
功能：
  1. 从东方财富/巨潮/同花顺抓取实时公告（中标/合同/海外业绩/重大投资）
  2. 关键词过滤与事件分类
  3. 事件去重与持久化存储
  4. 为监控/回测提供标准化事件流

数据源优先级：
  1. 东方财富公告接口 (ak.stock_notice_report) - 最全、实时
  2. 巨潮资讯网 (cninfo) - 权威、需解析
  3. 同花顺/新浪财经公告 - 备选

事件分类：
  - ORDER_WIN: 中标/签署重大合同/框架协议
  - EARNINGS_BEAT: 海外收入/业绩超预期
  - INVESTMENT: 重大投资/建厂/产能扩张(海外)
  - POLICY: 特高压核准/政策利好
  - PARTNERSHIP: 战略合作/合资/收购(海外)
  - FINANCING: 融资/发债(用于海外项目)
  - OTHER: 其他
"""

from __future__ import annotations

import os
import sys
import json
import re
import time
import logging
import hashlib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, asdict, field
from functools import lru_cache

import pandas as pd

try:
    import akshare as ak
    HAS_AKSHARE = True
except Exception:
    HAS_AKSHARE = False

# ============================================================================
# 配置
# ============================================================================

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
CONFIG_FILE = os.path.join(WORKSPACE, "data", "power_overseas_config.json")
EVENTS_DB_FILE = os.path.join(WORKSPACE, "data", "power_overseas_events.json")
EVENTS_CACHE_DIR = os.path.join(WORKSPACE, "data_cache", "events")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ============================================================================
# 事件数据结构
# ============================================================================

@dataclass
class OverseasEvent:
    """标准化海外事件"""
    event_id: str           # 唯一ID: hash(symbol+title+date)
    symbol: str             # 股票代码
    name: str               # 股票名称
    event_type: str         # 事件类型 (ORDER_WIN/EARNINGS_BEAT/INVESTMENT/POLICY/PARTNERSHIP/FINANCING/OTHER)
    title: str              # 公告标题
    content: str            # 公告摘要/内容
    pub_date: str           # 发布日期 YYYY-MM-DD
    source: str             # 数据源 (eastmoney/cninfo/ths/sina)
    url: str                # 原文链接
    keywords_matched: List[str] = field(default_factory=list)  # 命中关键词
    importance: int = 1     # 重要性 1-5
    extracted_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> dict:
        return asdict(self)

# ============================================================================
# 关键词规则库
# ============================================================================

EVENT_KEYWORDS = {
    "ORDER_WIN": [
        "中标", "签署", "合同", "框架协议", "订单", "采购", "招标", "成交",
        "海外订单", "出口订单", "国际招标", "EPC", "总承包", "供货合同",
        "长单", "框架采购", "战略采购", "批量订单"
    ],
    "EARNINGS_BEAT": [
        "海外收入", "出口收入", "国际业务收入", "境外收入",
        "业绩超预期", "业绩预增", "业绩大增", "净利润大增",
        "海外业绩", "国际业务", "出海业绩", "境外利润"
    ],
    "INVESTMENT": [
        "投资建设", "建设工厂", "建厂", "产能扩张", "扩产",
        "海外工厂", "境外工厂", "海外产能", "海外基地",
        "匈牙利", "印度", "越南", "印尼", "墨西哥", "巴西", "沙特", "阿联酋",
        "海外投资", "境外投资", "出海建厂"
    ],
    "POLICY": [
        "特高压", "核准", "开工", "获批", "批复", "立项",
        "国家电网", "南方电网", "特高压工程", "电网建设",
        "一带一路", "中欧", "沙特Vision2030", "中阿", "中巴",
        "电网互联", "跨国电网", "柔性直流", "HVDC"
    ],
    "PARTNERSHIP": [
        "战略合作", "合资", "合作协议", "框架合作", "技术合作",
        "合资公司", "合资企业", "收购", "股权投资", "参股",
        "国际合作", "海外合作", "技术许可", "专利授权"
    ],
    "FINANCING": [
        "发债", "债券", "融资", "贷款", "信贷", "绿色债",
        "海外发债", "美元债", "欧元债", "项目融资",
        "出口信贷", "保理", "保理融资"
    ]
}

# 编译正则
COMPILED_KEYWORDS = {k: [re.compile(w, re.IGNORECASE) for w in v] for k, v in EVENT_KEYWORDS.items()}

# 标的代码映射
POWER_STOCKS = {
    "600089": "特变电工", "600406": "国电南瑞", "000400": "许继电气",
    "601179": "中国西电", "600312": "平高电气", "002028": "思源电气",
    "002270": "华明装备", "002130": "沃尔核材"
}

# ============================================================================
# 事件存储管理
# ============================================================================

class EventStore:
    def __init__(self, db_path: str = EVENTS_DB_FILE):
        self.db_path = db_path
        self.events: List[OverseasEvent] = []
        self.event_ids: Set[str] = set()
        self._load()
    
    def _load(self):
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, encoding="utf-8") as f:
                    data = json.load(f)
                for e in data:
                    evt = OverseasEvent(**e)
                    self.events.append(evt)
                    self.event_ids.add(evt.event_id)
                log.info(f"已加载 {len(self.events)} 条历史事件")
            except Exception as e:
                log.warning(f"事件数据库加载失败: {e}")
    
    def _save(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump([e.to_dict() for e in self.events], f, ensure_ascii=False, indent=2)
    
    def _gen_id(self, symbol: str, title: str, pub_date: str) -> str:
        return hashlib.md5(f"{symbol}|{title}|{pub_date}".encode()).hexdigest()[:16]
    
    def add(self, event: OverseasEvent) -> bool:
        if event.event_id in self.event_ids:
            return False
        self.events.append(event)
        self.event_ids.add(event.event_id)
        self._save()
        return True
    
    def add_batch(self, events: List[OverseasEvent]) -> int:
        added = 0
        for e in events:
            if self.add(e):
                added += 1
        return added
    
    def get_by_symbol(self, symbol: str, days: int = 30) -> List[OverseasEvent]:
        cutoff = datetime.now() - timedelta(days=days)
        return [e for e in self.events 
                if e.symbol == symbol and datetime.fromisoformat(e.pub_date) >= cutoff]
    
    def get_recent(self, days: int = 7) -> List[OverseasEvent]:
        cutoff = datetime.now() - timedelta(days=days)
        return [e for e in self.events if datetime.fromisoformat(e.pub_date) >= cutoff]
    
    def get_by_type(self, event_type: str, days: int = 30) -> List[OverseasEvent]:
        cutoff = datetime.now() - timedelta(days=days)
        return [e for e in self.events 
                if e.event_type == event_type and datetime.fromisoformat(e.pub_date) >= cutoff]

# ============================================================================
# 事件分类器
# ============================================================================

class EventClassifier:
    @staticmethod
    def classify(title: str, content: str) -> tuple[str, List[str], int]:
        """返回 (事件类型, 命中关键词列表, 重要性1-5)"""
        text = f"{title} {content}".lower()
        matched_types = {}
        
        for ev_type, patterns in COMPILED_KEYWORDS.items():
            matches = [p.pattern for p in patterns if p.search(text)]
            if matches:
                matched_types[ev_type] = matches
        
        if not matched_types:
            return "OTHER", [], 1
        
        # 优先级：ORDER_WIN > EARNINGS_BEAT > INVESTMENT > POLICY > PARTNERSHIP > FINANCING
        priority = ["ORDER_WIN", "EARNINGS_BEAT", "INVESTMENT", "POLICY", "PARTNERSHIP", "FINANCING"]
        for p in priority:
            if p in matched_types:
                kw = matched_types[p]
                # 重要性：关键词数量 + 标题命中加权
                importance = min(5, len(kw) + (2 if any(k in title.lower() for k in kw) else 0))
                return p, kw, importance
        
        # 兜底
        first_type = list(matched_types.keys())[0]
        return first_type, matched_types[first_type], 2

# ============================================================================
# 数据源适配器
# ============================================================================

class EastMoneyAdapter:
    """东方财富全市场公告适配器"""
    
    @staticmethod
    def fetch_recent(days: int = 7) -> List[Dict]:
        if not HAS_AKSHARE:
            return []
        
        try:
            # 东方财富全市场公告
            df = ak.stock_notice_report()
            if df is None or df.empty:
                return []
            
            # 字段标准化
            # 列名可能变化，做兼容
            cols = df.columns.tolist()
            log.debug(f"东财公告列名: {cols}")
            
            # 常见列名映射
            title_col = next((c for c in cols if "标题" in c or "title" in c.lower()), cols[0])
            code_col = next((c for c in cols if "代码" in c or "code" in c.lower()), None)
            date_col = next((c for c in cols if "时间" in c or "date" in c.lower() or "发布" in c), None)
            content_col = next((c for c in cols if "内容" in c or "摘要" in c or "content" in c.lower()), None)
            url_col = next((c for c in cols if "链接" in c or "url" in c.lower() or "href" in c.lower()), None)
            type_col = next((c for c in cols if "类型" in c or "type" in c.lower()), None)
            
            if not code_col or not date_col:
                log.warning("东财公告关键列缺失")
                return []
            
            # 过滤目标标的
            df = df[df[code_col].astype(str).str[:6].isin(POWER_STOCKS.keys())]
            if df.empty:
                return []
            
            # 日期过滤
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            cutoff = datetime.now() - timedelta(days=days)
            df = df[df[date_col] >= cutoff]
            
            results = []
            for _, row in df.iterrows():
                code = str(row[code_col])[:6]
                results.append({
                    "symbol": code,
                    "name": POWER_STOCKS.get(code, ""),
                    "title": str(row[title_col]),
                    "content": str(row[content_col]) if content_col and pd.notna(row[content_col]) else "",
                    "pub_date": row[date_col].strftime("%Y-%m-%d"),
                    "source": "eastmoney",
                    "url": str(row[url_col]) if url_col and pd.notna(row[url_col]) else "",
                    "notice_type": str(row[type_col]) if type_col and pd.notna(row[type_col]) else ""
                })
            
            log.info(f"东财公告获取: {len(results)} 条 (近{days}天)")
            return results
            
        except Exception as e:
            log.error(f"东财公告获取失败: {e}")
            return []

class CninfoAdapter:
    """巨潮资讯网适配器 (简易版，需网页解析)"""
    
    @staticmethod
    def fetch_recent(days: int = 7) -> List[Dict]:
        # 巨潮需要解析网页，这里提供接口框架
        # 实际可用 requests + BeautifulSoup 解析 http://www.cninfo.com.cn/new/disclosure
        log.info("巨潮适配器待实现 (需网页解析)")
        return []

class ThsAdapter:
    """同花顺公告适配器 (akshare接口)"""
    
    @staticmethod
    def fetch_recent(days: int = 7) -> List[Dict]:
        if not HAS_AKSHARE:
            return []
        try:
            # 同花顺个股公告
            all_results = []
            for code, name in POWER_STOCKS.items():
                try:
                    df = ak.stock_notice_ths(symbol=code)
                    if df is None or df.empty:
                        continue
                    # 标准化...
                    # 简化处理
                except Exception:
                    continue
            return all_results
        except Exception as e:
            log.error(f"同花顺公告获取失败: {e}")
            return []

# ============================================================================
# 统一事件采集器
# ============================================================================

class EventCollector:
    def __init__(self):
        self.store = EventStore()
        self.classifier = EventClassifier()
        self.adapters = {
            "eastmoney": EastMoneyAdapter(),
            "cninfo": CninfoAdapter(),
            "ths": ThsAdapter()
        }
    
    def collect_all(self, days: int = 7) -> List[OverseasEvent]:
        """从所有数据源采集事件"""
        all_raw = []
        for name, adapter in self.adapters.items():
            try:
                raw = adapter.fetch_recent(days)
                for r in raw:
                    r["_source"] = name
                all_raw.extend(raw)
                log.info(f"[{name}] 采集 {len(raw)} 条原始公告")
            except Exception as e:
                log.error(f"[{name}] 采集失败: {e}")
        
        # 去重 + 分类 + 入库
        events = []
        seen = set()
        for r in all_raw:
            key = (r["symbol"], r["title"], r["pub_date"])
            if key in seen:
                continue
            seen.add(key)
            
            ev_type, keywords, importance = self.classifier.classify(r["title"], r["content"])
            
            event = OverseasEvent(
                event_id=self.store._gen_id(r["symbol"], r["title"], r["pub_date"]),
                symbol=r["symbol"],
                name=r["name"] or POWER_STOCKS.get(r["symbol"], ""),
                event_type=ev_type,
                title=r["title"],
                content=r["content"][:500],  # 截断
                pub_date=r["pub_date"],
                source=r.get("_source", "unknown"),
                url=r.get("url", ""),
                keywords_matched=keywords,
                importance=importance
            )
            events.append(event)
        
        added = self.store.add_batch(events)
        log.info(f"事件采集完成: 新增 {added} 条，总计 {len(self.store.events)} 条")
        return events
    
    def get_events_for_backtest(self, symbol: str, start: str, end: str) -> List[dict]:
        """为回测提供标准化事件列表"""
        start_dt = pd.to_datetime(start)
        end_dt = pd.to_datetime(end)
        
        results = []
        for e in self.store.events:
            if e.symbol != symbol:
                continue
            pub_dt = pd.to_datetime(e.pub_date)
            if start_dt <= pub_dt <= end_dt:
                results.append({
                    "date": e.pub_date,
                    "event_type": e.event_type,
                    "title": e.title,
                    "importance": e.importance,
                    "keywords": e.keywords_matched
                })
        return results

# ============================================================================
# 便捷函数
# ============================================================================

_collector_instance: Optional[EventCollector] = None

def get_collector() -> EventCollector:
    global _collector_instance
    if _collector_instance is None:
        _collector_instance = EventCollector()
    return _collector_instance

def collect_events(days: int = 7) -> List[OverseasEvent]:
    return get_collector().collect_all(days)

def get_events_for_symbol(symbol: str, days: int = 30) -> List[OverseasEvent]:
    return get_collector().store.get_by_symbol(symbol, days)

def get_recent_events(days: int = 7) -> List[OverseasEvent]:
    return get_collector().store.get_recent(days)

def get_events_for_backtest(symbol: str, start: str, end: str) -> List[dict]:
    return get_collector().get_events_for_backtest(symbol, start, end)

# ============================================================================
# CLI 入口
# ============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="电网装备出海板块事件采集")
    parser.add_argument("--days", type=int, default=7, help="采集最近N天")
    parser.add_argument("--symbol", help="指定标的代码")
    parser.add_argument("--type", help="指定事件类型")
    parser.add_argument("--list", action="store_true", help="列出已存储事件")
    args = parser.parse_args()
    
    collector = get_collector()
    
    if args.list:
        events = collector.store.events
        if args.symbol:
            events = [e for e in events if e.symbol == args.symbol]
        if args.type:
            events = [e for e in events if e.event_type == args.type]
        for e in sorted(events, key=lambda x: x.pub_date, reverse=True)[:50]:
            print(f"[{e.pub_date}] {e.symbol} {e.name} | {e.event_type} | {e.title[:60]}")
        return
    
    # 采集模式
    new_events = collector.collect_all(args.days)
    print(f"采集完成，新增 {len(new_events)} 条事件")
    for e in new_events:
        print(f"  [{e.pub_date}] {e.symbol} {e.name} | {e.event_type} (重要性{e.importance}) | {e.title[:80]}")

if __name__ == "__main__":
    main()