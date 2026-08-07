#!/usr/bin/env python3
"""
LLM + Tool � 层：封装对外部知识的获取（新闻、030健康度）以及通用的 LLM �� 调用（带 Tool/Function Calling � 能力）。
所有�函数仅返回数据，不做打印或文件写入。
"""

import os, json, requests
from typing import List, Dict, Any, Optional
from analysis.service import get_spot_scan  # 只为复用，实际不需要，但可保留

# -------------------- 自动加载 ~/.openclaw/.env（供 cron/独立进程读取 LLM key）
def _load_dotenv_if_needed():
    """若环境变量未注入 LLM key，则从 ~/.openclaw/.env 加载（供 cron/独立进程读取）。"""
    if os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY"):
        return
    for _p in (os.path.expanduser("~/.openclaw/.env"), os.path.expanduser("~/.openclaw/workspace/.env")):
        if not os.path.exists(_p):
            continue
        try:
            with open(_p, encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line or _line.startswith("#") or "=" not in _line:
                        continue
                    _k, _, _v = _line.partition("=")
                    _k = _k.strip()
                    _v = _v.strip()
                    if len(_v) >= 2 and _v[0] == '"' and _v[-1] == '"':
                        _v = _v[1:-1]
                    if _k and not os.getenv(_k):
                        os.environ[_k] = _v
        except Exception:
            pass

_load_dotenv_if_needed()
# -------------------- 配置 --------------------
# 2026-08-07 修复：本环境用 DeepSeek(openai-compatible) 做 LLM 解读，默认指向 api.deepseek.com。
# 只需在环境（推荐 ~/.openclaw/.env 或 shell）里设 key 即启用真 LLM 解读；未设时上层走规则化兜底。
# 优先级：DEEPSEEK_API_KEY > OPENAI_API_KEY；两者都没有 -> 空 key（调用 401，上层自动降级）。
OPENAI_API_KEY = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY", "")
# 默认 DeepSeek openai-compatible 端点（网关主模型即 deepseek）；可用 OPENAI_BASE_URL 覆盖。
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
# 模型：可用 LLM_MODEL 覆盖，默认 deepseek-chat。
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
HEADERS = {"Authorization": f"Bearer {OPENAI_API_KEY}",
           "Content-Type": "application/json"}

# -------------------- � 工具：获取今日新闻 --------------------
def get_today_news(limit: int = 5) -> List[Dict[str, str]]:
    """
    �� 调用 akshare 的 stock_news_em 或其他新闻接口，返回标题与简要摘要。
    这里直接使用 akshare.stock_news_em（东财新闻）。
    返回列表，每项包含 title、url、snippet（前200字）。
    """
    try:
        import akshare as ak
        df = ak.stock_news_em()  # 最新的财经新闻
        if df is None or df.empty:
            return []
        # 只取前 limit 条
        df = df.head(limit)
        news = []
        for _, row in df.iterrows():
            title = str(row.get("新闻标题", ""))
            url = str(row.get("新闻�链接", ""))
            content = str(row.get("正文", ""))
            # � 摘要取前200字
            snippet = content[:200].replace("\n", " ").strip()
            news.append({"title": title, "url": url, "snippet": snippet})
        return news
    except Exception as e:
        # 出错时返回空列表，上�层会自行处理
        return []

# -------------------- � 工具：获取 030 � 市场健康度 --------------------
def get_market_health() -> Optional[int]:
    """
    返回 0‑10 的整数市场健康度。

    2026-08-07 修复：原先调用 market_health_score.compute_health 会 ImportError，
    且其底层 akshare(东财/全A) 接口在本机被拦截。改为直接读取
    close_scan_v2 每日生成的 data/market_health_<日期>.json 的 summary.total_score（新浪数据，可靠）。
    若读取失败返回 None，上层会走规则兜底。
    """
    import glob
    try:
        fs = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "..", "data", "market_health_*.json")))
        if not fs:
            return None
        with open(fs[-1], encoding="utf-8") as f:
            d = json.load(f)
        score = (d.get("summary") or {}).get("total_score")
        if isinstance(score, (int, float)):
            return int(round(score))
    except Exception:
        pass
    return None

# -------------------- 通用 LLM �� 调用（支持 Tool/Function Calling） --------------------
def call_llm_with_tools(messages: List[Dict[str, str]],
                        tools: Optional[List[Dict]] = None,
                        tool_choice: str = "auto",
                        temperature: float = 0.2,
                        timeout: int = 30) -> Dict[str, Any]:
    """
    向 OpenAI‑compatible 端点发送�聊天请求，支持 functions/tools。
    messages: 标准的�聊天消息列表，每项含 role 和 content。
    tools: 可选的�函数规范列表（参照 OpenAI � 函数调用格式）。
    tool_choice: "auto", "none", 或强制指定某个�函数名。
    返回解�析后的 JSON � 响应（包含 choices 等字段）。
    """
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
    url = f"{OPENAI_BASE_URL}/chat/completions"
    try:
        resp = requests.post(url, headers=HEADERS, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        # 返回一个结构化的错误信息，便于上�层捕获
        return {"error": str(e), "status_code": getattr(e.response, "status_code", None) if hasattr(e, 'response') else None}

# -------------------- 提取 LLM 文本内容 --------------------
def extract_llm_content(resp) -> str:
    """
    从 call_llm_with_tools 的 OpenAI 兼容返回里提取 assistant 文本。
    resp 可能：(1) 标准 {choices:[{message:{content}}]}；(2) {"error":...}；(3) 其它。
    提取不到返回 ""（空串），调用方据此判断是否需要规则兜底。
    """
    if not isinstance(resp, dict):
        return str(resp)
    if "error" in resp:
        return ""
    try:
        c = resp["choices"][0]["message"]["content"]
        return c if isinstance(c, str) else ""
    except Exception:
        return ""


# -------------------- 便�捷封装：仅获取文本回复（不使用 tools） --------------------
def call_llm(messages: List[Dict[str, str]],
             temperature: float = 0.2,
             timeout: int = 30) -> str:
    """
    简单封装：只返回助手的文本内容（第一个 choice 的 message.content）。
    � 若出错则返回错误信息字符�串。
    """
    resp = call_llm_with_tools(messages, tools=None, temperature=temperature, timeout=timeout)
    if "error" in resp:
        return f"LLM �� 调用失败: {resp['error']}"
    try:
        return resp["choices"][0]["message"]["content"]
    except Exception:
        return str(resp)
