#!/usr/bin/env python3
"""
资金流文本渲染 (moneyflow_report)
==================================
把 close_scan_v2 输出的 moneyflow 字段渲染为推送用的 Markdown 文本。

用法:
    from analysis.moneyflow_report import render_moneyflow
    md = render_moneyflow(scan_output["moneyflow"])
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _yi(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}亿"


def render_moneyflow(mf: Optional[Dict[str, Any]], top: int = 5) -> str:
    """渲染资金流段落。mf 为 None/异常时返回说明性文本。"""
    if not mf:
        return "💰 **资金流**：未获取\n"
    if mf.get("error"):
        return f"💰 **资金流**：获取失败（{mf['error'][:60]}）\n"

    lines: List[str] = ["💰 **主力资金流（东财）**"]

    # ── 市场级：行业聚合 ──
    tot = mf.get("market_net_total")
    if tot is not None:
        lines.append(f"　全市场行业主力净额合计：**{_yi(tot)}**")

    inflow = mf.get("industry_inflow") or []
    if inflow:
        s = "  ".join(f"{r['name']}({_yi(r['main'])})" for r in inflow[:5])
        lines.append(f"　🔥 行业净流入：{s}")

    outflow = mf.get("industry_outflow") or []
    if outflow:
        s = "  ".join(f"{r['name']}({_yi(r['main'])})" for r in outflow[:5])
        lines.append(f"　🧊 行业净流出：{s}")

    # ── 关注池个股 ──
    ti = mf.get("top_inflow") or []
    if ti:
        lines.append("　关注池净流入 TOP：")
        for x in ti[:top]:
            pct = x.get("main_pct")
            pct_s = f"({pct:+.2f}%)" if isinstance(pct, (int, float)) else ""
            lines.append(f"　　{x['code']} {x.get('name','')}  {_yi(x.get('main'))} {pct_s}")

    to = mf.get("top_outflow") or []
    if to:
        lines.append("　关注池净流出 TOP：")
        for x in to[:top]:
            pct = x.get("main_pct")
            pct_s = f"({pct:+.2f}%)" if isinstance(pct, (int, float)) else ""
            lines.append(f"　　{x['code']} {x.get('name','')}  {_yi(x.get('main'))} {pct_s}")

    # ── 北向 ──
    nb = mf.get("northbound") or {}
    if nb.get("net_flow_discontinued"):
        lines.append(f"　北向：{nb.get('note', '净买入已停止披露（2024-08起）')} ")
    elif nb.get("available"):
        days = nb.get("days") or []
        if days:
            latest = days[0]
            amt = latest.get("deal_amt")
            amt_s = f"{amt/100:.0f}亿" if isinstance(amt, (int, float)) else "—"
            tag = "（净买入已停披，仅成交额）" if nb.get("net_flow_discontinued") else ""
            lines.append(f"　北向：{latest.get('date')} 成交额 {amt_s}{tag}")

    # ── 异常提示 ──
    errs = mf.get("errors") or []
    if errs:
        lines.append(f"　⚠️ 未取到 {len(errs)} 只：{'、'.join(e.split(':')[0] for e in errs[:5])}")

    return "\n".join(lines) + "\n"


def moneyflow_highlights(mf: Optional[Dict[str, Any]]) -> List[str]:
    """
    提取值得写进"风险预警/操作建议"的资金面要点（规则化，不做主观推断）。

    规则：
      1. 关注池个股净流出且净占比 <= -10% → 资金大幅出逃
      2. 关注池个股净流入且净占比 >= +5%  → 资金显著流入
    """
    out: List[str] = []
    if not mf or mf.get("error"):
        return out

    stocks = mf.get("stocks") or {}
    for code, v in stocks.items():
        pct = v.get("main_pct")
        main = v.get("main")
        if not isinstance(pct, (int, float)):
            continue
        name = v.get("name") or code
        if pct <= -10:
            out.append(f"🔴 {name}({code}) 主力资金出逃 {_yi(main)}，净占比 {pct:+.2f}%")
        elif pct >= 5:
            out.append(f"✅ {name}({code}) 主力资金流入 {_yi(main)}，净占比 {pct:+.2f}%")
    return out


if __name__ == "__main__":
    import json
    import sys
    data = json.load(sys.stdin)
    mf = data.get("moneyflow") or data
    print(render_moneyflow(mf))
    print("── 要点 ──")
    for h in moneyflow_highlights(mf):
        print(" ", h)
