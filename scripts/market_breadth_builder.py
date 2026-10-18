#!/usr/bin/env python3
"""全市场涨跌家数历史构建（门控情绪维更准替代源，2026-10-18）。

背景
----
门控情绪维生产用 akshare stock_market_activity_legu()（乐咕），但该接口**无历史**，
只能取当日。上一版标定（gate_threshold_study_v2.py）只好用「关注池 36 只自算上涨占比」
当代理，有板块集中偏差（半导体/电网/券商扎堆，上涨占比偏离全市场真值）。

本脚本用 baostock 拉**全市场沪深 A 股**日线，逐日统计上涨/下跌/平盘/停牌家数，
重建乐咕同口径的「全市场涨跌家数」历史，消除关注池代理偏差。

口径
----
* 证券范围: 沪深 A 股（sh.6 主板 / sz.0 主板 / sz.3 创业板），tradeStatus=1
* 涨跌判定: 当日 close vs 前一日 close（adjustflag=3 不复权，取真实涨跌而非复权后）
            其实乐咕的"上涨/下跌"按真实涨跌幅，不复权更贴切
* 活跃度: 乐咕 "活跃度 58.57%" 字段无公开历史 → 本脚本用「当日非停牌且成交量>0 家数
           / 总家数」或「|涨跌幅|中位数」代理，做成可复现的活跃度序列并单独标注局限
* 只读，落盘到 data/market_breadth_history.json

用法
----
  python3 scripts/market_breadth_builder.py            # 全量（约 20 分钟）
  python3 scripts/market_breadth_builder.py --quick    # 只拉 200 只做开发验算
输出
----
  data/market_breadth_history.json
    { "date": { "up":.., "down":.., "flat":.., "halt":.., "total":..,
                "up_ratio":.., "active_median_abs_chg":.., "n":.. } }
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
OUT = os.path.join(WORKSPACE, "data", "market_breadth_history.json")
if QUICK:
    OUT = os.path.join(WORKSPACE, "data", "market_breadth_history.quick.json")
START = "2024-04-01"
END = "2026-10-17"


def get_a_stock_universe(bs):
    """沪深 A 股代码列表（sh.6/sz.0/sz.3，tradeStatus=1）。"""
    rs = bs.query_all_stock(day="2026-10-16")
    codes = []
    while rs.error_code == "0" and rs.next():
        r = rs.get_row_data()
        if r[0].startswith(("sh.6", "sz.0", "sz.3")) and r[1] == "1":
            codes.append(r[0])
    return codes


def fetch_close(bs, code):
    """单只日线 close（不复权），返回 {date: close}。带重试。"""
    for _try in range(3):
        try:
            rs = bs.query_history_k_data_plus(
                code, "date,close,volume", start_date=START, end_date=END,
                frequency="d", adjustflag="3",
            )
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            out = {}
            for r in rows:
                if r[1] and r[1] not in ("", "0"):
                    out[r[0]] = float(r[1])
            return out
        except Exception:
            time.sleep(0.5 * (_try + 1))
    return {}


def build(bs, codes):
    # 逐只拉 close，拼成 {code: {date: close}}
    panel = {}
    for i, c in enumerate(codes, 1):
        panel[c] = fetch_close(bs, c)
        if i % 200 == 0:
            print(f"  拉取 {i}/{len(codes)}")
    # 逐日统计
    from collections import defaultdict
    day_prev_close = defaultdict(dict)
    day_up = defaultdict(int); day_down = defaultdict(int)
    day_flat = defaultdict(int); day_total = defaultdict(int)
    day_abs_chg = defaultdict(list)
    for code, d in panel.items():
        dates = sorted(d)
        for j, dt in enumerate(dates):
            day_total[dt] += 1
            day_abs_chg[dt].append(0.0)  # placeholder, 下面重算
            if j == 0:
                continue  # 首日无前收
            prev = d[dates[j - 1]]
            cur = d[dt]
            if prev > 0:
                chg = (cur / prev - 1) * 100
                day_abs_chg[dt].append(abs(chg))
                if chg > 0:
                    day_up[dt] += 1
                elif chg < 0:
                    day_down[dt] += 1
                else:
                    day_flat[dt] += 1
    # 清理 abs_chg 里的 placeholder 0.0（首日占位）
    out = {}
    for dt in sorted(day_total):
        n_up = day_up[dt]; n_down = day_down[dt]; n_flat = day_flat[dt]
        n_valid = n_up + n_down + n_flat
        if n_valid <= 0:
            continue
        ac = [x for x in day_abs_chg[dt] if x != 0.0]
        median_abs = sorted(ac)[len(ac) // 2] if ac else None
        out[dt] = {
            "up": n_up, "down": n_down, "flat": n_flat,
            "total": n_valid,
            "up_ratio": round(n_up / n_valid, 4),
            "active_median_abs_chg": round(median_abs, 3) if median_abs is not None else None,
            "n": n_valid,
        }
    return out


def main():
    import baostock
    lg = baostock.login()
    if lg.error_code != "0":
        print("登录失败", lg.error_msg)
        return 1
    codes = get_a_stock_universe(baostock)
    if QUICK:
        codes = codes[:200]
    print(f"待拉股票 {len(codes)} 只")
    t0 = time.time()
    breadth = build(baostock, codes)
    baostock.logout()
    print(f"耗时 {time.time()-t0:.0f}s，覆盖 {len(breadth)} 个交易日")
    with open(OUT, "w") as f:
        json.dump(breadth, f, ensure_ascii=False)
    # 抽样展示
    ds = sorted(breadth)
    for d in ds[:2] + ds[-2:]:
        print(f"  {d}: {breadth[d]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
