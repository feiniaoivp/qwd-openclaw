#!/usr/bin/env python3
"""
East Money 资金流数据获取模块 (moneyflow_em)
=============================================
解决"主力资金/北向数据缺失"问题。

🔑 关键技术点：
  1. 东财接口对 urllib/requests 会 TLS 指纹识别 → RemoteDisconnected
  2. 必须用 curl_cffi + impersonate="chrome" 伪装 Chrome TLS 指纹
  3. 偶发断连需重试（指数退避）

数据可得性（2026-09 实测）：
  ✅ 个股主力资金流（日线/分钟）—— 可用
  ✅ 大盘/指数主力资金流 —— 可用
  ✅ 行业板块主力净流入排名 —— 可用
  ❌ 北向资金净买入 —— **自 2024-08 起交易所停止实时披露**（真实缺失，非网络问题）
  ⚠️ 龙虎榜 —— 接口通，但部分日期无数据

用法：
    from analysis.moneyflow_em import index_flow, stock_flow, industry_flow_rank, northbound_summary
"""
from __future__ import annotations

import time
import json
from typing import Dict, List, Optional, Any

try:
    from curl_cffi import requests as _cr
except ImportError as e:  # pragma: no cover
    raise ImportError("需要 curl_cffi: pip install curl_cffi") from e

_IMPERSONATE = "chrome"
_TIMEOUT = 15
_RETRY = 4

# 🔑 主机回退链（2026-09-17 实测）
#   push2his / push2 在密集请求后会进入限流冷却（curl 56 Connection closed abruptly），
#   而 push2delay.eastmoney.com 返回**完全相同的数据**且不易被限流。
#   故：优先 push2delay，失败再回退 push2his。
_FFLOW_HOSTS = [
    "push2delay.eastmoney.com",
    "push2his.eastmoney.com",
]
_CLIST_HOSTS = [
    "push2delay.eastmoney.com",
    "push2.eastmoney.com",
]

# 统一 UA/Referer（配合 TLS 指纹伪装）
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
}

_FFLOW_FIELDS = "f51,f52,f53,f54,f55,f56,f57,f58"
_UT = "b2884a393a59ad64002292a3e90d46a5"


def _get(url: str, tries: int = _RETRY) -> Optional[dict]:
    """带指数退避重试的 GET（curl_cffi + Chrome TLS 指纹）

    自动在 _FFLOW_HOSTS / _CLIST_HOSTS 之间回退：把 URL 里的主机名替换后重试。
    """
    # 构造主机候选列表（保持相对路径与查询串不变）
    hosts: list[str] = []
    for h in (_FFLOW_HOSTS + _CLIST_HOSTS):
        if h not in hosts:
            hosts.append(h)
    # 用第一个主机作为基准模板
    import re as _re
    m = _re.match(r"https?://([^/]+)(/.*)$", url)
    base_path = m.group(2) if m else url

    last = None
    for attempt in range(tries):
        host = hosts[attempt % len(hosts)]
        cand = f"https://{host}{base_path}"
        try:
            r = _cr.get(cand, headers=_HEADERS, impersonate=_IMPERSONATE, timeout=_TIMEOUT)
            if r.status_code == 200 and r.content:
                try:
                    j = r.json()
                except Exception:  # noqa: BLE001
                    last = "non-JSON response"
                    continue
                # 东财用 {"_error":...} 标记失败；也可能返回 data=null
                if isinstance(j, dict) and "_error" not in j:
                    return j
                last = str(j.get("_error")) if isinstance(j, dict) else "bad payload"
            else:
                last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(0.6 * (attempt + 1))
    return {"_error": str(last)}


def _to_secid(code: str) -> str:
    """A股代码 → 东财 secid (1.=沪, 0.=深)"""
    code = str(code).strip()
    if "." in code:  # 已是 secid
        return code
    if code.startswith(("6", "5", "9", "11", "13")):
        return f"1.{code}"
    return f"0.{code}"


# ────────────────────────────────────────────
# 指数 / 个股 主力资金流
# ────────────────────────────────────────────

def _fflow(secid: str, lmt: int = 5) -> Optional[Dict[str, Any]]:
    url = (f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
           f"?lmt={lmt}&klt=101&secid={secid}"
           f"&fields1=f1,f2,f3,f7&fields2={_FFLOW_FIELDS}&ut={_UT}")
    d = _get(url)
    if not d or d.get("_error") or not d.get("data"):
        return None
    out = {"code": d["data"].get("code"), "name": d["data"].get("name"), "days": []}
    # f51=日期 f52=主力净额 f53=小单 f54=中单 f55=大单 f56=超大单 f57=主力净占比 f58=?
    for line in d["data"].get("klines", []):
        p = line.split(",")
        if len(p) < 7:
            continue
        out["days"].append({
            "date": p[0],
            "main": float(p[1]),          # 主力净额(元)
            "small": float(p[2]),
            "medium": float(p[3]),
            "large": float(p[4]),
            "xlarge": float(p[5]),
            "main_pct": float(p[6]) if p[6] not in ("", "-") else None,
        })
    return out


def index_flow(index_code: str = "000001", lmt: int = 5) -> Optional[Dict[str, Any]]:
    """指数主力资金流。index_code: 000001上证 399001深成 399006创业板 000688科创50 000300沪深300

    ⚠️ 可靠性警告（2026-09 实测）：该接口对**部分指数**（如上证指数、科创50）
    返回的量级明显失真（每日仅 ±0.2 亿，量级不合理），仅深证成指/创业板指量级正常。
    → 需要"全市场主力资金"时，**优先用 market_main_flow()（行业板块聚合）**，
       它量级合理且可交叉验证。本函数仅作参考。
    """
    return _fflow(_to_secid(index_code), lmt)


def market_main_flow(top_out: int = 6) -> Dict[str, Any]:
    """全市场主力资金流（基于行业板块聚合，量级可靠）

    返回 {inflow_total, inflow_top, outflow_top, net_total}
    这是获取"市场级主力资金"的**首选**方法。
    """
    inflow = industry_flow_rank(100)
    out_url = (f"https://push2.eastmoney.com/api/qt/clist/get"
               f"?fid=f62&po=0&pz={max(top_out, 1)}&pn=1&np=1&fltt=2&invt=2&ut={_UT}"
               f"&fs={_FS_INDUSTRY}&fields=f12,f14,f62,f184")
    d = _get(out_url)
    outflow = []
    if d and not d.get("_error") and d.get("data"):
        for x in d["data"].get("diff", []):
            outflow.append({"name": x.get("f14"), "main_net": x.get("f62"), "main_pct": x.get("f184")})
    total = sum(r["main_net"] for r in inflow if r.get("main_net"))
    return {
        "net_total": total,
        "board_count": len(inflow),
        "inflow_top": inflow[:10],
        "outflow_top": outflow,
    }


def stock_flow(stock_code: str, lmt: int = 5) -> Optional[Dict[str, Any]]:
    """个股主力资金流"""
    return _fflow(_to_secid(stock_code), lmt)


def batch_stock_flow(codes: List[str], lmt: int = 3) -> Dict[str, Optional[Dict[str, Any]]]:
    """批量个股主力资金流（串行，带重试；注意东财限流，建议 <=30 只）"""
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for c in codes:
        out[c] = stock_flow(c, lmt=lmt)
        time.sleep(0.25)
    return out


# ────────────────────────────────────────────
# 行业板块主力净流入排名
# ────────────────────────────────────────────

# 行业板块 fs 参数
_FS_INDUSTRY = "m:90+t:2"       # 行业板块(东财二级)
_FS_CONCEPT = "m:90+t:3"        # 概念板块


def industry_flow_rank(top: int = 10, concept: bool = False) -> List[Dict[str, Any]]:
    """
    行业(或概念)板块主力净流入排名。
    fid=f62 按主力净额排序, po=1 降序
    """
    fs = _FS_CONCEPT if concept else _FS_INDUSTRY
    url = (f"https://push2.eastmoney.com/api/qt/clist/get"
           f"?fid=f62&po=1&pz={max(top, 1)}&pn=1&np=1&fltt=2&invt=2&ut={_UT}"
           f"&fs={fs}&fields=f12,f14,f62,f184,f66,f69,f3,f2")
    d = _get(url)
    if not d or d.get("_error") or not d.get("data"):
        return []
    rows = []
    for x in d["data"].get("diff", []):
        rows.append({
            "code": x.get("f12"),
            "name": x.get("f14"),
            "main_net": x.get("f62"),          # 主力净额(元)
            "main_pct": x.get("f184"),         # 主力净占比(%)
            "xlarge_net": x.get("f66"),
            "change_pct": x.get("f3"),
        })
    return rows


# ────────────────────────────────────────────
# 北向资金（注意：净买入已停止披露）
# ────────────────────────────────────────────

def northbound_summary(days: int = 5) -> Dict[str, Any]:
    """
    北向资金。⚠️ 自 2024-08 起交易所停止实时披露净买入，
    故 NET_DEAL_AMT / FUND_INFLOW 恒为 None。可用的仅成交额。
    """
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
           "?reportName=RPT_MUTUAL_DEAL_HISTORY&columns=ALL"
           "&filter=(MUTUAL_TYPE%3D%22001%22)"
           f"&sortColumns=TRADE_DATE&sortTypes=-1&pageSize={days}&pageNumber=1")
    d = _get(url)
    if not d or d.get("_error"):
        return {"available": False, "reason": str(d.get("_error") if d else "no response")}
    rows = ((d.get("result") or {}).get("data")) or []
    out = []
    for r in rows:
        out.append({
            "date": (r.get("TRADE_DATE") or "")[:10],
            "deal_amt": r.get("DEAL_AMT"),          # 成交额(百万)
            "net_deal": r.get("NET_DEAL_AMT"),      # 净买入 —— 恒为 None
            "fund_inflow": r.get("FUND_INFLOW"),    # 恒为 None
            "lead_stock": r.get("LEAD_STOCKS_NAME"),
        })
    return {
        "available": True,
        "net_flow_discontinued": True,
        "note": "交易所自2024-08停止实时披露北向净买入；仅成交额可用",
        "days": out,
    }


# ────────────────────────────────────────────
# 龙虎榜
# ────────────────────────────────────────────

def billboard(date_str: str, top: int = 10) -> List[Dict[str, Any]]:
    """龙虎榜明细（date_str: YYYY-MM-DD）。部分日期可能返回空。"""
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
           "?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL"
           f"&filter=(TRADE_DATE%3D%27{date_str}%27)"
           f"&sortColumns=NET_BUY_AMT&sortTypes=-1&pageSize={top}&pageNumber=1")
    d = _get(url)
    if not d or d.get("_error"):
        return []
    rows = ((d.get("result") or {}).get("data")) or []
    out = []
    for r in rows:
        out.append({
            "code": r.get("SECURITY_CODE"),
            "name": r.get("SECURITY_NAME_ABBR"),
            "net_buy": r.get("NET_BUY_AMT"),
            "change_pct": r.get("CHANGE_RATE"),
            "reason": r.get("EXPLANATION"),
        })
    return out


def _fmt_yi(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v / 1e8:+.2f}亿"


if __name__ == "__main__":
    print("\n【全市场主力资金（行业板块聚合，首选口径）】")
    mk = market_main_flow(6)
    print(f"  板块数 {mk['board_count']}  主力净额合计 {_fmt_yi(mk['net_total'])}")
    print("  净流入 TOP:")
    for r in mk["inflow_top"][:5]:
        print(f"    {r['name']:<14} {_fmt_yi(r['main_net']):>10}  净占比 {r['main_pct']:>+6.2f}%")
    print("  净流出 TOP:")
    for r in mk["outflow_top"]:
        print(f"    {r['name']:<14} {_fmt_yi(r['main_net']):>10}  净占比 {r['main_pct']:>+6.2f}%")

    print("\n【指数主力资金流（⚠️ 部分指数量级失真，仅参考）】")
    for name, code in [("上证指数", "000001"), ("深证成指", "399001"),
                       ("创业板指", "399006"), ("科创50", "000688"),
                       ("沪深300", "000300")]:
        d = index_flow(code, lmt=1)
        if d and d["days"]:
            t = d["days"][-1]
            print(f"  {name:<8} {t['date']}  主力 {_fmt_yi(t['main'])}  "
                  f"超大单 {_fmt_yi(t['xlarge'])}  大单 {_fmt_yi(t['large'])}")

    print("\n【行业主力净流入 TOP10】")
    for r in industry_flow_rank(10):
        print(f"  {r['name']:<14} {_fmt_yi(r['main_net']):>10}  净占比 {r['main_pct']:>+6.2f}%")

    print("\n【北向资金】")
    nb = northbound_summary(3)
    print(f"  可用={nb.get('available')} 净买入已停披={nb.get('net_flow_discontinued')}")
    for r in nb.get("days", []):
        print(f"  {r['date']}  成交额 {r['deal_amt']:.0f}百万  净买入={r['net_deal']}")
