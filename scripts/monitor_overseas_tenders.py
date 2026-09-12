#!/usr/bin/env python3
"""
海外招标监控脚本 - 抓取沙特SEC、DEWA、巴西ONS招标公告
运行方式: python scripts/monitor_overseas_tenders.py
输出: data/tender_hits_YYYY-MM-DD.json + TG推送(可选)
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import sys

# 关键词配置
KEYWORDS = [
    "HVDC", "GIS", "Substation", "Transmission Line",
    "换流站", "GIS", "变压器", "阀厅", "直流输电",
    "Linha de Transmissão", "Subestação", "Cable", "400kV", "±800kV", "±1100kV"
]

# 目标站点配置
TARGETS = {
    "SEC": {
        "name": "沙特电力公司(SEC)",
        "url": "https://www.sec.gov.sa/en/Tenders/Pages/default.aspx",  # 实际需对接Etimad平台API
        "list_selector": "table.tenders tbody tr",  # 示例选择器
        "title_selector": "td.title a",
        "date_selector": "td.date",
        "link_attr": "href",
    },
    "DEWA": {
        "name": "迪拜水电局(DEWA)",
        "url": "https://www.dewa.gov.ae/en/tenders",
        "list_selector": ".tender-list .item",
        "title_selector": ".title a",
        "date_selector": ".date",
        "link_attr": "href",
    },
    "ONS": {
        "name": "巴西国家电力系统运营商(ONS)",
        "url": "https://www.ons.org.br/Paginas/resultados-da-licitacao.aspx",
        "list_selector": ".licitacoes-table tbody tr",
        "title_selector": "td.descricao a",
        "date_selector": "td.data",
        "link_attr": "href",
    },
}

# 简单的HTML获取(生产环境建议用requests+BeautifulSoup)
def fetch_html(url: str, headers=None) -> str:
    """获取网页HTML，带Referer和User-Agent"""
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if headers:
        default_headers.update(headers)
    req = Request(url, headers=default_headers)
    try:
        with urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except (URLError, HTTPError, TimeoutError) as e:
        print(f"[WARN] Fetch failed for {url}: {e}")
        return ""

def extract_tenders(html: str, config: dict) -> list[dict]:
    """
    从HTML提取招标列表(简易正则版，生产建议用BeautifulSoup/lxml)
    返回: [{"title": "", "date": "", "url": "", "source": ""}, ...]
    """
    # 这里只是占位逻辑，实际需根据各站点HTML结构编写解析器
    # 示例：用正则提取包含关键词的行
    results = []
    # 简单演示：假设HTML中有<table>结构
    # 实际部署时请为每个站点写专用解析器
    return results

def keyword_match(text: str) -> bool:
    """检查文本是否命中关键词(不区分大小写)"""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in KEYWORDS)

def load_seen_cache(cache_file: Path) -> set:
    """加载已见过的招标URL集合(去重用)"""
    if cache_file.exists():
        try:
            return set(json.loads(cache_file.read_text()))
        except Exception:
            return set()
    return set()

def save_seen_cache(cache_file: Path, seen: set):
    """保存已见URL集合"""
    cache_file.write_text(json.dumps(list(seen), ensure_ascii=False, indent=2))

def push_telegram(message: str, token: str, chat_id: str) -> bool:
    """推送到Telegram(占位，需配置token/chat_id)"""
    # 实际调用: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", ...)
    print(f"[TG PUSH] {message[:100]}...")
    return True

def main():
    today = datetime.now().strftime("%Y-%m-%d")
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    
    cache_file = data_dir / "tender_seen_cache.json"
    seen_urls = load_seen_cache(cache_file)
    
    all_hits = []
    new_seen = set()
    
    print(f"=== 海外招标监控 {today} ===")
    
    for key, config in TARGETS.items():
        print(f"\n[FETCH] {config['name']} -> {config['url']}")
        html = fetch_html(config["url"])
        if not html:
            print(f"  [SKIP] Empty response")
            continue
        
        tenders = extract_tenders(html, config)
        print(f"  [PARSE] Got {len(tenders)} items")
        
        for t in tenders:
            url = t.get("url", "")
            if url in seen_urls:
                continue
            
            title = t.get("title", "")
            if keyword_match(title):
                hit = {
                    "source": key,
                    "source_name": config["name"],
                    "title": title,
                    "date": t.get("date", ""),
                    "url": url,
                    "detected_at": datetime.now().isoformat(),
                    "keywords_matched": [kw for kw in KEYWORDS if kw.lower() in title.lower()],
                }
                all_hits.append(hit)
                new_seen.add(url)
                print(f"  [HIT] {title[:80]}...")
    
    # 保存本次命中
    output_file = data_dir / f"tender_hits_{today}.json"
    output_file.write_text(json.dumps(all_hits, ensure_ascii=False, indent=2))
    print(f"\n[SAVE] {len(all_hits)} hits -> {output_file}")
    
    # 更新去重缓存
    seen_urls.update(new_seen)
    save_seen_cache(cache_file, seen_urls)
    
    # 推送TG(如有命中)
    if all_hits:
        msg_lines = [f"🔔 海外招标命中 {len(all_hits)} 条 ({today})"]
        for h in all_hits[:5]:  # 限制前5条防刷屏
            msg_lines.append(f"• [{h['source_name']}] {h['title'][:60]}... ({h['date']})")
        msg_lines.append(f"详见: {output_file}")
        push_telegram("\n".join(msg_lines), "", "")  # 需填入token/chat_id
    
    print("\n=== 完成 ===")
    return 0

if __name__ == "__main__":
    sys.exit(main())