#!/usr/bin/env python3
"""全市场涨跌家数历史构建（门控情绪维更准替代源，2026-10-18；2026-11-07 修复断更）。

背景
----
门控情绪维生产用 akshare stock_market_activity_legu()（乐咕），但该接口**无历史**，
只能取当日。上一版标定（gate_threshold_study_v2.py）只好用「关注池 36 只自算上涨占比」
当代理，有板块集中偏差（半导体/电网/券商扎堆，上涨占比偏离全市场真值）。

本脚本用 baostock 拉**全市场沪深 A 股**日线，逐日统计上涨/下跌/平盘家数，
重建乐咕同口径的「全市场涨跌家数」历史，消除关注池代理偏差。

断更复盘（2026-11-07）
--------------------
history 停在 2026-10-16 三周未更新，两个根因：① END 写死 "2026-10-17"，跑多少次也
过不了 10-16；② 脚本从未接入任何 cron（10-18 研究期手动跑过一次后无人再跑）。
本次修复：END 动态化（交易日盘中跑取昨日，防盘中 close 污染）、新增 --incremental
增量模式、写盘 .bak + 条数防倒退、周六 08:00 独立 cron 接入（breadth-history-weekly）。

口径
----
* 证券范围: 沪深 A 股（sh.6 主板 / sz.0 主板 / sz.3 创业板），tradeStatus=1
* 涨跌判定: 当日 close vs 该股前一根日 bar 的 close（adjustflag=3 不复权，真实涨跌）
            增量窗口每只多拉 7 天回看，复牌股的跳变也能按其自身前收正确计入
* 活跃度: 「|涨跌幅|中位数」代理（乐咕活跃度无公开历史），与 2026-10-18 标定口径一致
* 失败显式: 登录失败 / 连续 12 天取不到宇宙 / 单股 3 次拉取全败均打印可见告警

用法
----
  python3 scripts/market_breadth_builder.py                  # 全量重建（约 20 分钟）
  python3 scripts/market_breadth_builder.py --incremental    # 增量补 OUT 最后日期之后
  python3 scripts/market_breadth_builder.py --quick          # 只拉 200 只开发验算（写 .quick.json）

输出
----
  data/market_breadth_history.json
    { "date": { "up":.., "down":.., "flat":.., "total":..,
                "up_ratio":.., "active_median_abs_chg":.., "n":.. } }
"""
import os
import sys
import json
import time
import shutil
from datetime import date, datetime, timedelta

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.net_guard import install_default_timeout
install_default_timeout(20)

QUICK = "--quick" in sys.argv
INCREMENTAL = "--incremental" in sys.argv
OUT = os.path.join(WORKSPACE, "data", "market_breadth_history.json")
if QUICK:
    OUT = os.path.join(WORKSPACE, "data", "market_breadth_history.quick.json")
START = "2024-04-01"


def resolve_end():
    """END 动态化。交易日且本地时间 <17:00 → 取昨日（防盘中 close 混入历史）。"""
    today = date.today()
    try:
        from analysis.service import is_trading_day  # 交易日判定 SSOT
        if is_trading_day(today.isoformat()) and datetime.now().hour < 17:
            return (today - timedelta(days=1)).isoformat()
    except Exception as e:  # 判定失败必须可见，禁止静默回退
        print(f"[warn] is_trading_day 失败({e!r})，END 直接取今天", file=sys.stderr)
    return today.isoformat()


def get_a_stock_universe(bs, day):
    """沪深 A 股代码列表；day 非交易日/无数据时逐日回退（最多 12 天）。"""
    d = date.fromisoformat(day)
    for _ in range(12):
        ds = d.isoformat()
        rs = bs.query_all_stock(day=ds)
        codes = []
        while rs.error_code == "0" and rs.next():
            r = rs.get_row_data()
            if r[0].startswith(("sh.6", "sz.0", "sz.3")) and r[1] == "1":
                codes.append(r[0])
        if codes:
            return codes, ds
        d -= timedelta(days=1)
    raise RuntimeError(f"query_all_stock 自 {day} 起连续 12 天无数据")


def fetch_close(bs, code, start, end):
    """单只日线 close（不复权），返回 {date: close}。重试 3 次，失败可见。"""
    for _try in range(3):
        try:
            rs = bs.query_history_k_data_plus(
                code, "date,close,volume", start_date=start, end_date=end,
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
        except Exception as e:
            print(f"[warn] {code} 第 {_try + 1}/3 次拉取失败: {e!r}", file=sys.stderr)
            time.sleep(0.5 * (_try + 1))
    print(f"[warn] {code} 三次全失败，跳过（该股缺失轻微影响当日家数）", file=sys.stderr)
    return {}


def build(bs, codes, start, end, only_after=None):
    """逐只拉 close 拼 panel，按「自身前一根 bar」算涨跌；only_after 之后的日期才进产出。"""
    panel = {}
    for i, c in enumerate(codes, 1):
        panel[c] = fetch_close(bs, c, start, end)
        if i % 200 == 0:
            print(f"  拉取 {i}/{len(codes)}", flush=True)
    from collections import defaultdict
    day_up = defaultdict(int)
    day_down = defaultdict(int)
    day_flat = defaultdict(int)
    day_abs_chg = defaultdict(list)
    for code, d in panel.items():
        dates = sorted(d)
        for j, dt in enumerate(dates):
            if j == 0:
                continue  # 窗口首根 bar 无前收（增量窗口每只多拉 7 天回看，正常覆盖）
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
    out = {}
    for dt in sorted(set(day_up) | set(day_down) | set(day_flat)):
        if only_after and dt <= only_after:
            continue
        n_up, n_down, n_flat = day_up[dt], day_down[dt], day_flat[dt]
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


def save_merged(out):
    """写盘前 .bak + 合并 + 条数防倒退；返回 (旧条数, 合并后总条数)。"""
    old = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f)
        shutil.copy2(OUT, OUT + ".bak")
    merged = dict(old)
    merged.update(out)
    if len(merged) < len(old):
        raise RuntimeError(f"防倒退触发: 合并后 {len(merged)} 天 < 原有 {len(old)} 天，拒绝写盘")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False)
    return len(old), len(merged)


def main():
    import baostock
    lg = baostock.login()
    if lg.error_code != "0":
        print(f"登录失败 {lg.error_msg}")
        return 1
    end = resolve_end()
    codes, universe_day = get_a_stock_universe(baostock, end)
    if QUICK:
        codes = codes[:200]
    only_after = None
    start = START
    if INCREMENTAL and not QUICK:
        old = {}
        if os.path.exists(OUT):
            with open(OUT, encoding="utf-8") as f:
                old = json.load(f)
        if old:
            last = max(old)
            only_after = last
            start = (date.fromisoformat(last) - timedelta(days=7)).isoformat()
            print(f"[incremental] 存量 {len(old)} 天至 {last}，增量窗口 {start}~{end}")
        else:
            print("[incremental] 无存量，退化为全量重建")
    print(f"待拉股票 {len(codes)} 只（universe_day={universe_day}，窗口 {start}~{end}）", flush=True)
    t0 = time.time()
    breadth = build(baostock, codes, start, end, only_after=only_after)
    baostock.logout()
    print(f"耗时 {time.time() - t0:.0f}s，本次产出 {len(breadth)} 个交易日", flush=True)
    if INCREMENTAL and only_after and not breadth:
        print("[incremental] 无新增交易日（已最新），不写盘")
        return 0
    n_old, n_new = save_merged(breadth)
    with open(OUT, encoding="utf-8") as f:
        merged = json.load(f)
    ds = sorted(merged)
    print(f"落盘 {OUT}: 共 {len(merged)} 天（{ds[0]}~{ds[-1]}），较写前新增 {n_new - n_old} 天")
    for d in ds[:2] + ds[-2:]:
        print(f"  {d}: {merged[d]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
