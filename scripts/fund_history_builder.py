#!/usr/bin/env python3
"""基本面历史重建（三因素门控标定前置，2026-10-18）。

目标
----
为「门控阈值真实标定」提供**无前视偏差**的基本面历史序列。

生产口径（analysis/three_factor_helper.py::ResonanceGate._fetch_fund_resonance）：
  * 数据源: baostock query_profit_data（roeAvg 单季 ROE、npMargin 净利率）
           + query_growth_data（YOYPNI 净利同比增速）
  * 归一: 简化版单点 _norm01（硬阈值）:
           ROE   0~25%（roeAvg×4 年化）
           yoy  -100%~100%
           npm   0~30%（npMargin×100）
           score = mean(各分) × 100
  * 时点: 取「最新已披露」报告期（生产用 datetime.now() 倒查季度）

前视偏差防护
------------
生产取"最新已披露"，我们回放历史也必须取「信号日当时已披露」的最新财报，
否则拿 2026 年报去评 2024 的信号 = 前视。实现：
  * 对每只票，拉每个报告期 (year, quarter) 的 profit+growth，记录 pubDate（披露日）
  * 建立「披露日 → 财报值」映射；信号日 T 只取 pubDate <= T 的最新一条

用法
----
  python3 scripts/fund_history_builder.py            # 全量重建，落盘 json
  python3 scripts/fund_history_builder.py --quick    # 只建 5 只用做开发验算
输出
----
  data/research_fund_history.json  { sym: [ {pub, stat, roe, yoy_pni, np_margin}, ... ] }
"""
import os
import sys
import json
import time

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.net_guard import install_default_timeout
install_default_timeout(20)

QUICK = "--quick" in sys.argv
OUT = os.path.join(WORKSPACE, "data", "research_fund_history.json")
if QUICK:
    # quick 开发验算写独立文件，避免覆盖全量产物（2026-10-18 教训：--quick 曾覆盖 36 只全量）
    OUT = os.path.join(WORKSPACE, "data", "research_fund_history.quick.json")
START_YEAR = 2023   # 提前一年，覆盖 2024 跨年信号需要的前一年财报


def bs_code(sym):
    return f"sh.{sym}" if sym.startswith(("6", "9")) else f"sz.{sym}"


def _f(v):
    try:
        if v is None or v == "" or v == "--":
            return None
        return float(str(v).replace("%", "").replace(",", ""))
    except Exception:
        return None


def _sign(bs):
    """单次登录，返回 bs 句柄（脚本级复用），断开时重连。"""
    return bs


def build_one(bs, sym):
    """拉该票 2023~今 所有已披露报告期的 profit+growth，按披露日去重排序。
    带超时重试（baostock 有接收抖动，实测「接收数据异常/timed out」偶发）。"""
    now = __import__("datetime").datetime.now()
    code = bs_code(sym)
    recs = {}
    for year in range(START_YEAR, now.year + 1):
        for q in (1, 2, 3, 4):
            for _try in range(3):
                try:
                    rs = bs.query_profit_data(code=code, year=year, quarter=q)
                    rows = []
                    while rs.error_code == "0" and rs.next():
                        rows.append(rs.get_row_data())
                    for row in rows:
                        if not row or not row[2]:
                            continue
                        pq = {rs.fields[i]: row[i] for i in range(len(rs.fields))}
                        pub = pq.get("pubDate", "") or ""
                        stat = pq.get("statDate", "") or ""
                        roe = _f(pq.get("roeAvg"))
                        npm = _f(pq.get("npMargin"))
                        if pub and roe is not None:
                            key = (pub[:10], stat[:10])
                            recs.setdefault(key, {})["roe"] = round(roe * 4, 4)  # 年化×4（生产口径）
                            recs[key]["np_margin"] = round(npm * 100, 2) if npm is not None else None
                    break
                except Exception:
                    time.sleep(0.6 * (_try + 1))
                    continue
            for _try in range(3):
                try:
                    rs2 = bs.query_growth_data(code=code, year=year, quarter=q)
                    rows2 = []
                    while rs2.error_code == "0" and rs2.next():
                        rows2.append(rs2.get_row_data())
                    for row in rows2:
                        if not row or not row[2]:
                            continue
                        gq = {rs2.fields[i]: row[i] for i in range(len(rs2.fields))}
                        pub = gq.get("pubDate", "") or ""
                        stat = gq.get("statDate", "") or ""
                        yoy = _f(gq.get("YOYPNI"))
                        if pub and yoy is not None:
                            key = (pub[:10], stat[:10])
                            recs.setdefault(key, {})["yoy_pni"] = round(yoy * 100, 2)
                    break
                except Exception:
                    time.sleep(0.6 * (_try + 1))
                    continue
    # 排序成列表
    out = []
    for (pub, stat), v in sorted(recs.items()):
        out.append({"pub": pub, "stat": stat,
                    "roe": v.get("roe"), "yoy_pni": v.get("yoy_pni"),
                    "np_margin": v.get("np_margin")})
    return out


def main():
    from analysis.fib_extension_scan import WATCHLIST
    syms = sorted(WATCHLIST.keys())
    if QUICK:
        syms = syms[:5]

    import baostock
    lg = baostock.login()
    if lg.error_code != "0":
        print("登录失败", lg.error_msg)
        return 1

    out = {}
    for i, sym in enumerate(syms, 1):
        try:
            out[sym] = build_one(baostock, sym)
        except Exception as e:
            print(f"⚠️ {sym} 失败: {e}")
            out[sym] = []
        if i % 8 == 0 or i == len(syms):
            print(f"进度 {i}/{len(syms)}")
    baostock.logout()

    with open(OUT, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    n_total = sum(len(v) for v in out.values())
    print(f"📄 落盘 {OUT}: {len(out)} 只 / {n_total} 条报告期记录")
    # 抽样展示
    for sym in syms[:2]:
        print(f"  {sym}: {out[sym][:2]} ... {out[sym][-1:] if out[sym] else 'EMPTY'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
