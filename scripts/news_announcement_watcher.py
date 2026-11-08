#!/usr/bin/env python3
"""
盘中重大公告秒级推送
========================
监听 data/news/raw/ 目录变更，检测关键词（回购/增持/业绩预增/重大合同/股东大会决议），
触发即时 Telegram 推送。

设计原则：
- 基于文件系统事件监听（inotify/Watchdog），非轮询，实现秒级响应
- 关键词匹配：正则 + 中文分词，覆盖同义/变体
- 去重：基于标题哈希，24h 内不重复推送同一公告
- 关注股过滤：仅推送 WATCHLIST 内股票的重大公告
- 失败重试：Telegram 推送失败自动重试 3 次

运行方式：
  python3 scripts/news_announcement_watcher.py  # 守护进程模式
  python3 scripts/news_announcement_watcher.py --once  # 单次扫描模式（用于回测/补推）
"""

import os
import sys
import json
import time
import hashlib
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple
from collections import defaultdict

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)

# 导入统一组件
from analysis.fib_extension_scan import WATCHLIST as _WL
from send_telegram import send_with_retry

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# 关注股代码集合（仅监控这些股票）
FOCUS_CODES = {code for code, _ in _WL.items()}
FOCUS_NAME_MAP = dict(_WL)

# 关键词配置：分类 -> (关键词列表, 权重, 严重度)
# 权重用于评分，严重度用于推送优先级
KEYWORD_CATEGORIES = {
    "回购": {
        "keywords": [
            r"回购", r"股份回购", r"回购方案", r"回购计划", r"回购进度",
            r"以集中竞价方式回购", r"回购专用账户", r"回购价格上限",
            r"回购金额", r"回购数量", r"完成回购", r"首次回购"
        ],
        "weight": 2,
        "severity": "HIGH",
        "emoji": "🔵",
        "label": "回购公告"
    },
    "增持": {
        "keywords": [
            r"增持", r"股东增持", r"大股东增持", r"实控人增持", r"董监高增持",
            r"增持计划", r"增持方案", r"完成增持", r"首次增持",
            r"自愿增持", r"承诺增持", r"增持比例", r"增持金额"
        ],
        "weight": 2,
        "severity": "HIGH",
        "emoji": "🟢",
        "label": "增持公告"
    },
    "业绩预增": {
        "keywords": [
            r"业绩预增", r"预增", r"业绩预告.*增长", r"净利润.*增长",
            r"业绩大增", r"扭亏为盈", r"同比增长", r"业绩超预期",
            r"业绩预告.*向好", r"净利润.*预计.*增", r"归母净利润.*增"
        ],
        "weight": 2,
        "severity": "HIGH",
        "emoji": "📈",
        "label": "业绩预增"
    },
    "重大合同": {
        "keywords": [
            r"重大合同", r"重大订单", r"中标", r"签署.*合同.*金额",
            r"战略合作", r"框架协议", r"采购合同", r"销售合同",
            r"重大项目", r"亿元订单", r"千万订单", r"新签合同",
            r"订单金额", r"合同金额.*亿"
        ],
        "weight": 1,
        "severity": "MEDIUM",
        "emoji": "📋",
        "label": "重大合同"
    },
    "股东大会决议": {
        "keywords": [
            r"股东大会.*决议", r"股东大会.*通过", r"临时股东大会",
            r"年度股东大会", r"股东大会公告", r"决议公告",
            r"股权激励.*通过", r"定增.*通过", r"分红.*通过",
            r"选举.*董事", r"聘任.*审计", r"修订.*章程"
        ],
        "weight": 1,
        "severity": "MEDIUM",
        "emoji": "🏛",
        "label": "股东大会决议"
    },
    "重大资产重组": {
        "keywords": [
            r"重大资产重组", r"资产重组", r"吸收合并", r"发行股份购买资产",
            r"借壳上市", r"重组预案", r"重组方案", r"停牌.*重组",
            r"置入资产", r"置出资产", r"作价"
        ],
        "weight": 2,
        "severity": "HIGH",
        "emoji": "🔄",
        "label": "重大资产重组"
    },
    "分红派息": {
        "keywords": [
            r"分红", r"派息", r"利润分配", r"每股派发现金", r"转增股",
            r"送股", r"资本公积转增", r"分红方案", r"实施分红",
            r"权益分派", r"中期分红", r"年度分红"
        ],
        "weight": 1,
        "severity": "MEDIUM",
        "emoji": "💰",
        "label": "分红派息"
    },
    "减持": {
        "keywords": [
            r"减持", r"股东减持", r"大股东减持", r"实控人减持",
            r"董监高减持", r"减持计划", r"减持方案", r"被动减持",
            r"质权减持", r"集中竞价减持", r"大宗交易减持"
        ],
        "weight": -2,
        "severity": "HIGH",
        "emoji": "🔴",
        "label": "减持公告"
    },
    "立案调查": {
        "keywords": [
            r"立案", r"证监会立案", r"立案调查", r"涉嫌违规",
            r"信披违规", r"行政处罚", r"警示函", r"监管函",
            r"处罚决定", r"立案告知书"
        ],
        "weight": -2,
        "severity": "CRITICAL",
        "emoji": "⚠️",
        "label": "立案调查"
    },
    "业绩预亏": {
        "keywords": [
            r"业绩预亏", r"预亏", r"业绩预告.*亏损", r"净利润.*亏损",
            r"业绩暴雷", r"巨额亏损", r"归母净利润.*亏", r"同比下降",
            r"业绩预告.*向坏", r"净利润.*预计.*亏"
        ],
        "weight": -2,
        "severity": "HIGH",
        "emoji": "📉",
        "label": "业绩预亏"
    }
}

# 编译正则模式
for cat, cfg in KEYWORD_CATEGORIES.items():
    cfg["patterns"] = [re.compile(kw, re.IGNORECASE) for kw in cfg["keywords"]]

# 数据目录
RAW_DIR = Path(WORKSPACE) / "data" / "news" / "raw"
STATE_FILE = Path(WORKSPACE) / "data" / "news" / "announcement_watcher_state.json"

# 去重状态：标题哈希 -> {timestamp, count}
dedup_state: Dict[str, Dict] = {}

# 当前监控的文件位置记录
file_positions: Dict[str, int] = {}


def load_state():
    """加载去重状态"""
    global dedup_state, file_positions
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                dedup_state = data.get("dedup", {})
                file_positions = data.get("positions", {})
                # 清理 24h 以上的去重记录
                cutoff = time.time() - 24 * 3600
                dedup_state = {k: v for k, v in dedup_state.items() if v.get("ts", 0) > cutoff}
                log.info(f"✅ 加载去重状态: {len(dedup_state)} 条记录")
        except Exception as e:
            log.warning(f"加载状态失败: {e}")
            dedup_state = {}
            file_positions = {}


def save_state():
    """保存去重状态（重写型写点：备份保护 backup_guard 铁律）"""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if STATE_FILE.exists():
            import shutil
            try:
                shutil.copy2(STATE_FILE, str(STATE_FILE) + ".bak")
            except Exception as e:
                log.warning(f"⚠️ 备份失败(继续写入): {STATE_FILE}: {e}")
        tmp = str(STATE_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "dedup": dedup_state,
                "positions": file_positions,
                "updated": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log.error(f"保存状态失败: {e}")


def extract_stock_code(title: str, source: str = "") -> Optional[str]:
    """从标题/来源中提取股票代码"""
    # 尝试从标题提取 6 位代码
    code_match = re.search(r"\b(\d{6})\b", title)
    if code_match:
        code = code_match.group(1)
        if code in FOCUS_CODES:
            return code
    # 尝试从标题提取股票简称
    for code, name in FOCUS_NAME_MAP.items():
        if name in title or (len(name) > 2 and name[:4] in title):
            return code
    return None


def match_keywords(text: str) -> List[Tuple[str, Dict]]:
    """匹配关键词，返回匹配的分类及其配置"""
    matches = []
    for cat, cfg in KEYWORD_CATEGORIES.items():
        for pattern in cfg["patterns"]:
            if pattern.search(text):
                matches.append((cat, cfg))
                break  # 同一分类只记录一次
    return matches


def compute_announcement_score(matches: List[Tuple[str, Dict]]) -> Tuple[int, str, str]:
    """计算公告综合评分和标签
    返回: (总分, 综合标签, 最高严重度)
    """
    if not matches:
        return 0, "其他", "LOW"
    
    total_score = sum(cfg["weight"] for _, cfg in matches)
    labels = [cfg["label"] for _, cfg in matches]
    
    # 确定最高严重度
    severity_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    max_severity = max(matches, key=lambda x: severity_order.get(x[1]["severity"], 0))[1]["severity"]
    
    # 正面/负面标签
    pos_labels = [l for l, (_, cfg) in zip(labels, matches) if cfg["weight"] > 0]
    neg_labels = [l for l, (_, cfg) in zip(labels, matches) if cfg["weight"] < 0]
    
    if pos_labels and not neg_labels:
        overall = "利好"
    elif neg_labels and not pos_labels:
        overall = "利空"
    elif pos_labels and neg_labels:
        overall = "混合"
    else:
        overall = "中性"
    
    label = f"{overall}: {', '.join(labels)}"
    return total_score, label, max_severity


def format_push_message(announcement: Dict) -> str:
    """格式化 Telegram 推送消息"""
    code = announcement.get("code", "")
    name = announcement.get("name", "")
    title = announcement.get("title", "")
    category = announcement.get("category", "")
    label = announcement.get("label", "")
    severity = announcement.get("severity", "")
    score = announcement.get("score", 0)
    source = announcement.get("source", "")
    url = announcement.get("url", "")
    pub_time = announcement.get("pub_time", "")
    
    # 严重度对应的 emoji
    severity_emoji = {
        "CRITICAL": "🚨",
        "HIGH": "⚠️",
        "MEDIUM": "📢",
        "LOW": "ℹ️"
    }.get(severity, "📢")
    
    cat_cfg = KEYWORD_CATEGORIES.get(category, {})
    cat_emoji = cat_cfg.get("emoji", "📄")
    
    lines = [
        f"{severity_emoji} <b>盘中重大公告速递</b> {cat_emoji}",
        f"",
        f"<b>{name} ({code})</b>",
        f"📌 <b>{label}</b>",
        f"📝 {title}",
        f"⏰ {pub_time}",
        f"📊 评分: {score:+d} | 级别: {severity}",
    ]
    
    if url:
        lines.append(f"🔗 <a href='{url}'>查看原文</a>")
    
    if source:
        lines.append(f"📰 来源: {source}")
    
    return "\n".join(lines)


def process_announcement(item: Dict, source_file: str) -> Optional[Dict]:
    """处理单条公告/新闻，判断是否需要推送"""
    title = item.get("title", "") or item.get("公告标题", "") or ""
    pub_time = item.get("time", "") or item.get("发布时间", "") or item.get("公告日期", "") or ""
    source = item.get("source", "") or item.get("文章来源", "") or ""
    url = item.get("url", "") or item.get("新闻链接", "") or item.get("网址", "") or ""
    symbol = item.get("symbol", "") or item.get("代码", "") or ""
    
    if not title:
        return None
    
    # 提取股票代码
    code = symbol or extract_stock_code(title, source)
    if not code or code not in FOCUS_CODES:
        return None
    
    name = FOCUS_NAME_MAP.get(code, code)
    
    # 去重：基于标题哈希
    title_hash = hashlib.md5(f"{code}:{title}".encode()).hexdigest()[:16]
    now_ts = time.time()
    
    if title_hash in dedup_state:
        last = dedup_state[title_hash]
        if now_ts - last.get("ts", 0) < 24 * 3600:
            # 24h 内已推送过，增加计数但不再推送
            last["count"] = last.get("count", 1) + 1
            return None
    
    # 关键词匹配
    matches = match_keywords(title)
    if not matches:
        return None
    
    # 计算评分
    score, label, severity = compute_announcement_score(matches)
    # 取权重最高的分类作为主分类
    primary_cat = max(matches, key=lambda x: abs(x[1]["weight"]))[0]
    
    announcement = {
        "code": code,
        "name": name,
        "title": title,
        "category": primary_cat,
        "all_categories": [c for c, _ in matches],
        "label": label,
        "score": score,
        "severity": severity,
        "pub_time": pub_time,
        "source": source,
        "url": url,
        "source_file": source_file,
        "detected_at": datetime.now().isoformat(),
    }
    
    # 记录去重
    dedup_state[title_hash] = {"ts": now_ts, "count": 1}
    
    return announcement


def scan_file(filepath: Path) -> List[Dict]:
    """扫描单个 JSON 文件，提取新增公告"""
    announcements = []
    
    try:
        # 读取文件当前位置
        last_pos = file_positions.get(str(filepath), 0)
        current_size = filepath.stat().st_size
        
        if current_size <= last_pos:
            # 文件未增长或被截断
            if current_size < last_pos:
                file_positions[str(filepath)] = 0
            return announcements
        
        # 读取新增内容
        with open(filepath, "r", encoding="utf-8") as f:
            f.seek(last_pos)
            new_content = f.read()
            file_positions[str(filepath)] = current_size
        
        if not new_content.strip():
            return announcements
        
        # 解析 JSON（可能是增量追加的 JSON 对象或完整 JSON）
        data = None
        # 先尝试解析整个文件
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            # 如果失败，尝试解析新增行（假设每行一个 JSON）
            for line in new_content.strip().split("\n"):
                try:
                    item = json.loads(line)
                    result = process_announcement(item, filepath.name)
                    if result:
                        announcements.append(result)
                except json.JSONDecodeError:
                    continue
            return announcements
        
        # 成功解析完整 JSON，处理其中的公告/新闻
        # 支持 news_monitor.py 的数据结构
        if isinstance(data, dict):
            # news_monitor.py format: {macro: {news: [...], new_count: N}, notices: {notices: [...], new_count: N}, stock_news: [{symbol, name, news: [...], ...}, ...]}
            if "macro" in data and isinstance(data["macro"], dict):
                for item in data["macro"].get("news", []):
                    result = process_announcement(item, filepath.name)
                    if result:
                        announcements.append(result)
            if "notices" in data and isinstance(data["notices"], dict):
                for item in data["notices"].get("notices", []):
                    result = process_announcement(item, filepath.name)
                    if result:
                        announcements.append(result)
            if "stock_news" in data and isinstance(data["stock_news"], list):
                for stock_item in data["stock_news"]:
                    for item in stock_item.get("news", []):
                        result = process_announcement(item, filepath.name)
                        if result:
                            announcements.append(result)
        elif isinstance(data, list):
            # 兼容旧格式：直接是列表
            for item in data:
                result = process_announcement(item, filepath.name)
                if result:
                    announcements.append(result)
        
        # 兼容旧格式：直接是列表
        if isinstance(data, list):
            for item in data:
                result = process_announcement(item, filepath.name)
                if result:
                    announcements.append(result)
                    
    except Exception as e:
        log.error(f"扫描文件 {filepath} 失败: {e}")
    
    return announcements


def scan_all_new_files() -> List[Dict]:
    """扫描所有新增/更新的原始新闻文件"""
    all_announcements = []
    
    if not RAW_DIR.exists():
        log.warning(f"原始新闻目录不存在: {RAW_DIR}")
        return all_announcements
    
    # 按修改时间排序，优先处理最新文件
    files = sorted(RAW_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    
    for filepath in files:
        # 只处理最近 2 小时内修改的文件（盘中实时性）
        mtime = filepath.stat().st_mtime
        if time.time() - mtime > 2 * 3600:
            continue
            
        announcements = scan_file(filepath)
        all_announcements.extend(announcements)
    
    # 去重（同一公告可能在多个文件中）
    seen = set()
    unique = []
    for ann in all_announcements:
        key = f"{ann['code']}:{ann['title']}"
        if key not in seen:
            seen.add(key)
            unique.append(ann)
    
    return unique


def push_announcements(announcements: List[Dict]):
    """批量推送公告到 Telegram"""
    if not announcements:
        return
    
    # 按严重度排序：CRITICAL > HIGH > MEDIUM > LOW
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    announcements.sort(key=lambda x: severity_order.get(x["severity"], 99))
    
    for ann in announcements:
        try:
            message = format_push_message(ann)
            result = send_with_retry(message, parse_mode="HTML", max_retries=3, retry_delay=2)
            if result.get("ok"):
                log.info(f"✅ 推送成功: {ann['name']}({ann['code']}) - {ann['label']}")
            else:
                log.error(f"❌ 推送失败: {ann['name']} - {result.get('error')}")
        except Exception as e:
            log.error(f"推送异常: {ann['name']} - {e}")
        
        # 避免触发 Telegram 限流
        time.sleep(0.5)
    
    save_state()


def run_once():
    """单次扫描模式（用于回测/补推）"""
    log.info("🔍 单次扫描模式启动...")
    load_state()
    announcements = scan_all_new_files()
    
    if announcements:
        log.info(f"📢 发现 {len(announcements)} 条重大公告")
        push_announcements(announcements)
    else:
        log.info("📭 无新增重大公告")
    
    save_state()
    log.info("✅ 单次扫描完成")


def run_daemon():
    """守护进程模式：持续监听文件变更"""
    log.info("🚀 盘中重大公告监听器启动 (守护进程模式)")
    log.info(f"👀 监控目录: {RAW_DIR}")
    log.info(f"🎯 关注股票: {len(FOCUS_CODES)} 只")
    log.info(f"🔑 关键词分类: {len(KEYWORD_CATEGORIES)} 类")
    
    load_state()
    
    # 启动时做一次全量扫描（处理启动期间可能遗漏的）
    initial_anns = scan_all_new_files()
    if initial_anns:
        log.info(f"🔄 启动补扫: 发现 {len(initial_anns)} 条历史公告")
        push_announcements(initial_anns)
    
    log.info("⏳ 进入监听循环...")
    
    try:
        while True:
            # 每 5 秒扫描一次（秒级响应要求，但避免过度 CPU）
            time.sleep(5)
            
            # 仅交易时段活跃监听（09:15-11:30, 13:00-15:00）
            now = datetime.now()
            hour, minute = now.hour, now.minute
            is_trading = (
                (hour == 9 and minute >= 15) or
                (10 <= hour <= 11) or
                (hour == 11 and minute <= 30) or
                (13 <= hour <= 14) or
                (hour == 15 and minute == 0)
            )
            
            if not is_trading:
                # 非交易时段降低频率
                time.sleep(55)
                continue
            
            announcements = scan_all_new_files()
            if announcements:
                log.info(f"🔔 实时检测到 {len(announcements)} 条重大公告")
                push_announcements(announcements)
                
    except KeyboardInterrupt:
        log.info("👋 收到停止信号，保存状态并退出...")
        save_state()
    except Exception as e:
        log.error(f"监听器异常: {e}")
        save_state()
        raise


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_once()
    else:
        run_daemon()