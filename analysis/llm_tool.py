#!/usr/bin/env python3
"""
LLM + Tool 层：封装对外部知识的获取（新闻、030健康度）以及通用的 LLM 调用（带 Tool/Function Calling 能力）。
所有函数仅返回数据，不做打印或文件写入。
"""


# ── sys.path 样板（TOOLS.md 强制；2026-09-19）──
# 脚本模式下 sys.path[0] 是脚本所在目录而非工作区根，
# 必须显式插入工作区根（供 `from analysis.xxx`）+ analysis/（供子包引用）。
import os as _os, sys as _sys
_WORKSPACE = _os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (_WORKSPACE, _os.path.join(_WORKSPACE, "analysis")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

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

# -------------------- 工具：获取今日新闻 --------------------
def get_today_news(limit: int = 5) -> Dict[str, Any]:
    """
    2026-10-20 重写（Plan A，用户批准）。
    旧版根因：裸调 ak.stock_news_em() 无参 —— 本机 akshare 1.18.64 官方轮子默认
    symbol='603777'（来伊份，池外股；dist-info RECORD 哈希已验为官方原样，非本机篡改），
    东财搜索按相关度排序，前 5 条被 08-27~10-12 旧文长期霸榜，导致盘前报告连日复读
    同一批来伊份旧闻。另有两个次生 bug：读列名 "正文"（实际列 "新闻内容"）→ 摘要恒空；
    丢弃 "发布时间" 列 → 旧闻顶着 "今日" 标签无日期展示。

    新版：改读新闻流水线权威快照 data/news/raw/{date}_{盘前|盘后|回填}.json
    （analysis/news_monitor.py 产出，08:30 盘前 / 19:06 盘后 cron 落盘）。
    两级时效窗：48h 主窗；不足 limit 扩到 96h 兜底（兜底条目标记 stale）；
    两级皆无 → items=[]，由上层渲染显式占位。全部条目强制带发布时间。

    返回: {"items": [{title, snippet, time, kind, sym, source, stale?}],
           "snapshot": "…08:30:03", "window": "48h"|"96h", "raw_date": "YYYY-MM-DD"}
    """
    import glob as _glob
    from datetime import datetime as _dt

    raw_dir = _os.path.join(_WORKSPACE, "data", "news", "raw")
    out: Dict[str, Any] = {"items": [], "snapshot": None, "window": "48h", "raw_date": None}
    files = _glob.glob(_os.path.join(raw_dir, "*.json"))
    if not files:
        return out
    by_date: Dict[str, list] = {}
    for f in files:
        base = _os.path.basename(f)[:-5]
        if "_" not in base:
            continue
        d, _ = base.rsplit("_", 1)
        by_date.setdefault(d, []).append(f)
    if not by_date:
        return out
    latest = max(by_date)
    out["raw_date"] = latest

    items = []
    snap_ts = None
    for f in sorted(by_date[latest]):
        try:
            with open(f, encoding="utf-8") as _f:
                snap = json.load(_f)
        except Exception:
            continue
        ft = str(snap.get("fetch_time") or "")
        if ft and (snap_ts is None or ft > snap_ts):
            snap_ts = ft
        # 1) 关注股公告（time 精确到日）
        for n in snap.get("notices", {}).get("notices", []) or []:
            t = str(n.get("title") or "")
            if t:
                items.append({"title": t, "snippet": "",
                              "time": str(n.get("date") or ""),
                              "kind": "公告", "sym": str(n.get("symbol") or ""),
                              "source": "东财公告"})
        # 2) 关注股个股新闻
        for sn in snap.get("stock_news", []) or []:
            sym = str(sn.get("symbol") or "")
            name = str(sn.get("name") or "")
            for n in sn.get("news", []) or []:
                if not isinstance(n, dict):
                    continue
                t = str(n.get("title") or "")
                if t:
                    items.append({"title": t, "snippet": "",
                                  "time": str(n.get("time") or ""),
                                  "kind": "新闻",
                                  "sym": (name + "(" + sym + ")") if name else sym,
                                  "source": str(n.get("source") or "")})
        # 3) 宏观/行业（带 summary）
        for m in snap.get("macro", {}).get("news", []) or []:
            t = str(m.get("title") or "")
            if t:
                items.append({"title": t, "snippet": str(m.get("summary") or "")[:100],
                              "time": str(m.get("time") or ""),
                              "kind": "宏观", "sym": "", "source": ""})
    out["snapshot"] = snap_ts

    # 标题去重（同日 盘前/盘后/回填 多快照会重复）
    seen, uniq = set(), []
    for it in items:
        if it["title"] in seen:
            continue
        seen.add(it["title"])
        uniq.append(it)

    # 时效解析：兼容 "YYYY-MM-DD HH:MM:SS" / "YYYY-MM-DD HH:MM" / "YYYY-MM-DD"
    def _parse(ts: str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return _dt.strptime(ts[:len(fmt) + 2].strip(), fmt)
            except ValueError:
                continue
        return None

    now = _dt.now()
    for it in uniq:
        t = _parse(it["time"])
        it["age_h"] = ((now - t).total_seconds() / 3600.0) if t else None

    pool = [it for it in uniq if it["age_h"] is not None and 0 <= it["age_h"] <= 48]
    if len(pool) < limit:
        # 96h 兜底窗（周一 06:00 用得上：最新快照是上周五盘后，条目约 59h）
        have = {it["title"] for it in pool}
        for it in uniq:
            if it["title"] not in have and it["age_h"] is not None and 0 <= it["age_h"] <= 96:
                it["stale"] = True
                pool.append(it)
        if any(it.get("stale") for it in pool):
            out["window"] = "96h"

    # 排序：公告 > 高显著新闻 > 宏观；同级时间倒序；兜底条目降权
    kw_hi = ("回购", "增持", "减持", "举牌", "业绩预", "扭亏", "立案", "处罚",
             "诉讼", "重大合同", "中标", "收购", "重组", "定增", "辞职",
             "涨停", "跌停", "违约", "分红", "说明会")
    def _score(it: Dict[str, Any]) -> int:
        s = {"公告": 30, "新闻": 20, "宏观": 10}.get(it["kind"], 0)
        if any(k in it["title"] for k in kw_hi):
            s += 5
        if it.get("stale"):
            s -= 8
        return s

    pool.sort(key=lambda it: (_score(it), it["time"]), reverse=True)
    out["items"] = pool[:limit]
    for it in out["items"]:
        it.pop("age_h", None)
    return out


# -------------------- 工具：获取 030 市场健康度 --------------------
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

# -------------------- 通用 LLM 调用（支持 Tool/Function Calling） --------------------
def call_llm_with_tools(messages: List[Dict[str, str]],
                        tools: Optional[List[Dict]] = None,
                        tool_choice: str = "auto",
                        temperature: float = 0.2,
                        timeout: int = 30) -> Dict[str, Any]:
    """
 向 OpenAI‑compatible 端点发送聊天请求，支持 functions/tools。
    messages: 标准的聊天消息列表，每项含 role 和 content。
    tools: 可选的函数规范列表（参照 OpenAI 函数调用格式）。
    tool_choice: "auto", "none", 或强制指定某个函数名。
 返回解析后的 JSON 响应（包含 choices 等字段）。
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
        # 返回一个结构化的错误信息，便于上层捕获
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


# -------------------- 便捷封装：仅获取文本回复（不使用 tools） --------------------
def call_llm(messages: List[Dict[str, str]],
             temperature: float = 0.2,
             timeout: int = 30) -> str:
    """
 简单封装：只返回助手的文本内容（第一个 choice 的 message.content）。
 若出错则返回错误信息字符串。
    """
    resp = call_llm_with_tools(messages, tools=None, temperature=temperature, timeout=timeout)
    if "error" in resp:
        return f"LLM 调用失败: {resp['error']}"
    try:
        return resp["choices"][0]["message"]["content"]
    except Exception:
        return str(resp)
