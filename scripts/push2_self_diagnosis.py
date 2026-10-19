#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
push2_self_diagnosis.py — B（push2 家族）本体诊断（2026-10-19 立项）

背景（销案争议）：
  - 10-18 05:1x 实测 push2/push2delay/push2his 三 host 全 000（0.04~0.17s 快速失败）
  - 10-19 04:12 实测 push2/push2his "404（链路通）" —— 疑似用裸根路径探测，
    裸根路径在健康时也回 404，属误导性探针
  - 销案标准（10-18 勘误）：连续 ≥2 日多时段稳定达标；"达标"必须用**生产真实路径**判定

诊断矩阵（只读探测，零状态变更，不触碰 moneyflow_em 熔断器记账）：
  轴1 host:   push2 / push2his / push2delay / datacenter-web(对照) / hq.sinajs.cn(无关对照)
  轴2 path:   /(裸根) / fflow klt=1 实时 / fflow daykline klt=101 / stock/get 基础行情 / ulist 列表
  轴3 client: curl_cffi chrome 指纹（生产口径） / 系统裸 curl（无指纹）
  轴4 net:    直连 / 7890 代理
  轴5 DNS:    getaddrinfo 解析记录（对照污染特征）
  轴6 官方库: akshare 官方同接口对照（10-09 教训：区分"本机被针对"与"全网性退化"）

产出: analysis/research/<date>_push2_self_diagnosis.{json,md}
退出码: 0 = 诊断完成（不代表 push2 健康，健康结论看 JSON verdict）

用法: python3 scripts/push2_self_diagnosis.py [--quick]
"""
#!/usr/bin/env python3
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
# 必须同时插入：工作区根 + analysis/（脚本模式下 from analysis.xxx 可解析）
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
import socket
import subprocess
import time
from datetime import date
from typing import Any, Dict, List, Optional

from curl_cffi import requests as cffi_requests

from analysis.net_guard import network_timeout_guard

OUT_DIR = os.path.join(WORKSPACE, "analysis", "research")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
}
_UT = "b2884a393a59ad64002292a3e90d46a5"
_FFLOW_FIELDS = "f51,f52,f53,f54,f55,f56,f57,f58"
PROXY = "http://127.0.0.1:7890"
TIMEOUT = 10

SECID = "1.600030"  # 中信证券，关注池票，固定探针

# 生产真实路径（与 analysis/moneyflow_em.py 逐字符对齐）
PATH_FFLOW_RT = (f"/api/qt/stock/fflow/kline/get?lmt=10&klt=1&secid={SECID}"
                 f"&fields1=f1,f2,f3,f7&fields2={_FFLOW_FIELDS}&ut={_UT}")
PATH_FFLOW_DAY = (f"/api/qt/stock/fflow/daykline/get?lmt=5&klt=101&secid={SECID}"
                  f"&fields1=f1,f2,f3,f7&fields2={_FFLOW_FIELDS}&ut={_UT}")
PATH_QUOTE = f"/api/qt/stock/get?secid={SECID}&fields=f43,f57,f58&ut={_UT}"
PATH_ULIST = (f"/api/qt/ulist.np/get?fltt=2&secids={SECID}&fields=f12,f14,f2,f3&ut={_UT}")

HOSTS = ["push2.eastmoney.com", "push2his.eastmoney.com", "push2delay.eastmoney.com"]
CONTROLS = ["datacenter-web.eastmoney.com", "hq.sinajs.cn"]

# 每个 host 测哪些 path（裸根全测；fflow 双路径按生产 host 分工）


def dns_probe(hosts: List[str]) -> List[Dict[str, Any]]:
    out = []
    for h in hosts:
        rec: Dict[str, Any] = {"host": h}
        try:
            infos = socket.getaddrinfo(h, 443, socket.AF_INET)
            rec["ips"] = sorted({i[4][0] for i in infos})
            rec["ok"] = True
        except Exception as e:  # noqa: BLE001 —— 诊断脚本必须保留错误可见
            rec["ok"] = False
            rec["error"] = f"{type(e).__name__}: {e}"
        out.append(rec)
    return out


def http_probe(host: str, path: str, via_proxy: bool = False,
               use_curl_plain: bool = False) -> Dict[str, Any]:
    url = f"https://{host}{path}" if host != "hq.sinajs.cn" else f"https://{host}/list=sh600030"
    rec: Dict[str, Any] = {"host": host, "path": path.split("?")[0], "proxy": via_proxy,
                           "client": "curl_plain" if use_curl_plain else "curl_cffi_chrome"}
    t0 = time.time()
    try:
        if use_curl_plain:
            cmd = ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
                   "--max-time", str(TIMEOUT), "-A", HEADERS["User-Agent"],
                   "-H", f"Referer: {HEADERS['Referer']}", url]
            if via_proxy:
                cmd[1:1] = ["-x", PROXY]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT + 5)
            rec["http_code"] = p.stdout.strip() or None
            rec["elapsed"] = round(time.time() - t0, 2)
            if p.returncode != 0:
                rec["error"] = (p.stderr or "").strip()[:200]
            rec["ok"] = p.returncode == 0 and rec["http_code"] == "200"
        else:
            proxies = {"http": PROXY, "https": PROXY} if via_proxy else None
            r = cffi_requests.get(url, headers=HEADERS, impersonate="chrome",
                                  timeout=TIMEOUT, proxies=proxies)
            rec["http_code"] = r.status_code
            rec["elapsed"] = round(time.time() - t0, 2)
            body = r.text or ""
            if r.status_code == 200:
                # fflow/quote 类接口健康判据：JSON 且 data 非空（push2delay 对 fflow 是
                # "200+data:null" 诱饵，必须识别为不健康）
                rec["data_nonnull"] = '"data":null' not in body[:2000] and len(body) > 60
                rec["ok"] = bool(rec["data_nonnull"])
                rec["body_head"] = body[:120]
            else:
                rec["ok"] = False
                rec["body_head"] = body[:120]
    except Exception as e:  # noqa: BLE001 —— 网络异常分类正是诊断目标，必须保留
        rec["ok"] = False
        rec["elapsed"] = round(time.time() - t0, 2)
        rec["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return rec


def akshare_official_probe() -> Dict[str, Any]:
    """官方 akshare 同接口对照（stock_individual_fund_flow 走 push2his daykline）。"""
    rec: Dict[str, Any] = {"probe": "ak.stock_individual_fund_flow('600030','sh')",
                           "client": "official akshare 1.18.64"}
    t0 = time.time()
    try:
        import akshare as ak
        with network_timeout_guard(25):
            df = ak.stock_individual_fund_flow(stock="600030", market="sh")
        rec["rows"] = 0 if df is None else int(len(df))
        rec["ok"] = df is not None and len(df) > 0
        if rec["ok"]:
            rec["last_date"] = str(df.iloc[-1, 0])[:10]
    except Exception as e:  # noqa: BLE001 —— 失败模式即诊断结论，必须保留
        rec["ok"] = False
        rec["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    rec["elapsed"] = round(time.time() - t0, 2)
    return rec


def verdict_of(probes: List[Dict[str, Any]], official: Dict[str, Any]) -> Dict[str, Any]:
    """按真实路径归纳：fflow 生产路径任一 host 200+data 非空 = push2 家族可用。"""
    ff_ok = [p for p in probes if p.get("ok") and "fflow" in p.get("path", "")]
    quote_ok = [p for p in probes if p.get("ok") and "stock/get" in p.get("path", "")]
    layer = "unknown"
    errs = [p.get("error", "") for p in probes if not p.get("ok")]
    if any("Connection reset" in e or "SSLError" in e or "ConnectionError" in e for e in errs):
        layer = "TCP/TLS 层（连接被掐断）"
    elif any("404" == str(p.get("http_code")) for p in probes if not p.get("ok")):
        layer = "HTTP 404（可达但路径级拒绝）"
    elif errs and all(("timed out" in e or "Timeout" in e) for e in errs):
        layer = "超时（限流冷却特征）"
    return {
        "push2_family_usable": bool(ff_ok),
        "fflow_ok_probes": [f'{p["host"]}{p["path"]}(proxy={p["proxy"]})' for p in ff_ok],
        "quote_ok_probes": [f'{p["host"]}{p["path"]}(proxy={p["proxy"]})' for p in quote_ok],
        "official_akshare_ok": bool(official.get("ok")),
        "official_rows": official.get("rows"),
        "failing_layer_guess": layer,
        "bare_root_404_is_normal": True,  # 裸根 404 不构成健康证据，须用真实路径
    }


def main() -> int:
    quick = "--quick" in sys.argv
    t0 = time.time()
    result: Dict[str, Any] = {
        "date": str(date.today()),
        "purpose": "push2 家族本体诊断（真实生产路径，替代裸根 404 误导探针）",
        "dns": dns_probe(HOSTS + CONTROLS),
        "probes": [],
        "official_akshare": None,
    }

    # 轴1-3：host × path × client（直连，curl_cffi chrome 为主口径）
    for h in HOSTS:
        result["probes"].append(http_probe(h, "/"))
        result["probes"].append(http_probe(h, PATH_FFLOW_RT))
        if not quick:
            result["probes"].append(http_probe(h, PATH_FFLOW_DAY))
            result["probes"].append(http_probe(h, PATH_QUOTE))
            result["probes"].append(http_probe(h, PATH_ULIST))
            result["probes"].append(http_probe(h, PATH_FFLOW_RT, use_curl_plain=True))
    # 对照组
    result["probes"].append(http_probe("datacenter-web.eastmoney.com",
                                       "/api/data/v1/get?reportName=RPT_DMSK_TS_STOCKNEW"
                                       "&columns=ALL&pageSize=1&source=SECURITIES&client=WEB"))
    result["probes"].append(http_probe("hq.sinajs.cn", "/list=sh600030"))
    # 代理轴（仅对主 host 的实时 fflow 试代理是否改变结局）
    if not quick:
        result["probes"].append(http_probe("push2.eastmoney.com", PATH_FFLOW_RT, via_proxy=True))
        result["probes"].append(http_probe("push2his.eastmoney.com", PATH_FFLOW_DAY, via_proxy=True))

    # 轴6：官方 akshare 对照
    result["official_akshare"] = official = akshare_official_probe()

    result["verdict"] = verdict_of(result["probes"], official)
    result["elapsed_sec"] = round(time.time() - t0, 1)

    # 主结果先落盘（AGENTS.md 铁律）
    os.makedirs(OUT_DIR, exist_ok=True)
    out_json = os.path.join(OUT_DIR, f"{date.today()}_push2_self_diagnosis.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[saved] {out_json}")

    # 摘要
    v = result["verdict"]
    print(json.dumps(v, ensure_ascii=False, indent=2))
    print(f"\nelapsed={result['elapsed_sec']}s quick={quick}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
