#!/usr/bin/env python3
"""FT中文网 RSS EOF 诊断落盘（2026-10-18）。

复现矩阵：DNS / TLS 直连 / 代理 / Node fetch 四层，输出 JSON 供后续引用。
"""
import os
import sys
import json
import sqlite3
import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    db = os.path.expanduser("~/.blogwatcher/blogwatcher.db")
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT b.name, COUNT(a.id) n, MAX(a.published_date) last_pub, "
        "MAX(a.discovered_date) last_disc "
        "FROM blogs b LEFT JOIN articles a ON a.blog_id=b.id GROUP BY b.id"
    ).fetchall()
    con.close()

    print("=== blogwatcher 源健康快照 ===")
    health = []
    for name, n, lp, ld in rows:
        print(f"{name:14} n={n:<4} last_pub={lp} last_disc={ld}")
        health.append({"name": name, "articles": n, "last_published": lp, "last_discovered": ld})

    diag = {
        "date": datetime.datetime.now().strftime("%F %H:%M"),
        "symptom": "FT中文网 RSS scan 报 EOF；其他源正常",
        "url": "https://www.ftchinese.com/rss/feed",
        "layer_matrix": {
            "dns": {
                "result": "污染/随机化（每次解析不同）",
                "evidence": [
                    "dig 首次 118.193.202.219，再次 31.13.96.194 (IE-FACEBOOK-20110418)",
                    "dig @223.5.5.5 -> 198.18.0.4（保留段）",
                    "dns.alidns DoH -> 128.242.240.212；另一轮 -> 66.220.147.11 (Facebook)",
                    "doh.pub -> 172.67.139.110 + 104.21.8.120 (Cloudflare，最像真值)",
                    "AAAA -> 2a03:2880:f127:83:face:b00c:0:25de (Meta IPv6)",
                ],
            },
            "tls_direct": {
                "result": "ClientHello 后被 EOF/RST",
                "evidence": [
                    "curl (35) SSL_ERROR_SYSCALL :443；:80 curl(52) Empty reply",
                    "python ssl.SSLEOFError UNEXPECTED_EOF_WHILE_READING",
                    "curl_cffi chrome/safari/edge 全 (35) invalid library 00000000",
                    "--resolve 到 Cloudflare 真 IP 172.67/104.21 仍 (35)",
                    "--resolve 到 128.242.240.212 / 118.193.202.219 仍 (35)",
                ],
            },
            "proxy_7890": {
                "result": "CONNECT 200 隧道建立后 TLS 仍 EOF",
                "evidence": [
                    "出口 IP 60.249.85.31（台湾）",
                    "CONNECT tunnel established response 200 -> SSL_ERROR_SYSCALL",
                    "--resolve Cloudflare IP 走代理仍 (35)",
                    "baidu/google 走代理正常 -> 非代理整体故障",
                    "rsshub.app/ftchinese 走代理 403（Cloudflare 挡 RSSHub）",
                ],
            },
            "node_web_fetch": "fetch failed（同一阻断）",
        },
        "verdict": (
            "非 blogwatcher bug，是 SNI/域名级网络封锁 + DNS 投毒。"
            "同一阻断同时作用于直连 / 代理 / Node fetch / curl_cffi 四路径。"
            "换出口节点未验证（FlClash 控制口超时，无法程序化切节点）。"
        ),
        "source_health": health,
        "options": [
            "A 保留源、标注降级（每日 scan 会持续刷 EOF 噪声）",
            "B 移除 FT 源（blogwatcher remove FT中文网）",
            "C wrapper 注入代理/DoH（Go 二进制无 proxy 配置；HTTPS_PROXY 对 Go net/http 默认不生效）",
            "D 换 RSSHub 公共实例（rsshub.app 403，需另找）",
        ],
    }

    os.makedirs(os.path.join(WORKSPACE, "analysis/research"), exist_ok=True)
    p = os.path.join(WORKSPACE, "analysis/research/2026-10-18_ft_feed_diagnosis.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(diag, f, ensure_ascii=False, indent=1)
    print("saved", p)
    return diag


if __name__ == "__main__":
    main()
