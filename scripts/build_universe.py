#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_universe.py — 选股宇宙数据湖构建器（2026-11-05，用户拍板：全市场优先/沪深300 可接受）

三层漏斗的第 0 层数据基座（设计见 analysis/research/2026-11-05_universe_funnel_design.md）：
  Tier-0 宇宙层: 沪深300 + 中证500（~800 只，起步）→ 架构支持扩展全市场（--universe all）
  Tier-1 候选池: 因子筛选产出（~50-100 只，周频）        [后续: factor screen]
  Tier-2 核心盘: 影子关注池 → 人工审批 → 替换 WATCHLIST  [后续: shadow promotion]

用法:
    python3 scripts/build_universe.py                    # 默认 hs300zz500，增量更新
    python3 scripts/build_universe.py --universe all     # 全市场（预留，v1 未启用）
    python3 scripts/build_universe.py --full-refresh     # 忽略增量，全量重拉
    python3 scripts/build_universe.py --end 2026-11-04   # 指定截止日

产出:
    data/universe/members.csv                     成分清单（code,name,source,status）
    data/universe/daily/{code}.csv                逐票日线（前复权，date..pctChg）
    data/universe/build_report_{date}.json        构建报告（数量/失败清单/耗时）

退出码: 0 = 失败率 <5%；1 = 失败率 ≥5%（供 cron 告警）
"""
import os
import sys
import json
import time
import argparse
import concurrent.futures
from datetime import datetime, timedelta

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
# 必须同时插入：工作区根 + scripts/（脚本模式自检样板，见 TOOLS.md）
for _p in (WORKSPACE, os.path.join(WORKSPACE, "scripts"), os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.service import is_trading_day  # 交易日判定 SSOT

UNIVERSE_DIR = os.path.join(WORKSPACE, "data", "universe")
DAILY_DIR = os.path.join(UNIVERSE_DIR, "daily")
MEMBERS_CSV = os.path.join(UNIVERSE_DIR, "members.csv")
DEFAULT_START = "2021-01-01"
FIELDS = "date,open,high,low,close,volume,amount,turn,pctChg"
MAX_WORKERS = 1          # 🔴 baostock 全局连接非线程安全（2026-11-05 实证：4 线程持续并发拉取
                         # 触发 zlib 流交叉损坏 "invalid distance too far back" + 忙等挂死；
                         # 短窗 6×6 探测能过是小样本侥幸）。宇宙级批量拉取必须串行。
RETRY_PER_STOCK = 2


def bs_connect():
    from analysis.bs_session import ensure_login
    if not ensure_login():
        raise RuntimeError("baostock 登录失败")
    import baostock as bs
    return bs


def _rows_to_csv(rs, out_path):
    """rs → CSV（流式，防大结果集驻留内存）"""
    import csv
    header = rs.fields
    n = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        while rs.next():
            w.writerow(rs.get_row_data())
            n += 1
    return n


def fetch_members(bs, universe: str):
    """成分清单：hs300 ∪ zz500（all 预留）。返回 [(code, name, source)]"""
    members, seen = [], set()

    def _from(rs, source):
        out = []
        # 按字段名定位列（baostock 返回 updateDate,code,code_name，列序不可假设）
        fields = list(rs.fields)
        try:
            i_code = fields.index("code")
            i_name = fields.index("code_name")
        except ValueError:
            return out
        while (rs.error_code == "0") and rs.next():
            row = rs.get_row_data()
            code, name = row[i_code], row[i_name]
            if code not in seen:
                seen.add(code)
                out.append((code, name, source))
        return out

    rs = bs.query_hs300_stocks()
    members += _from(rs, "hs300")
    rs = bs.query_zz500_stocks()
    members += _from(rs, "zz500")

    if universe == "all":
        # v1 预留：全市场 = query_stock_basic(type=1, status=1) 去掉 ST/上市<1y
        # 拉全市场前先跑 hs300zz500 验证链路，再启用（避免首跑 5000 只把故障放大）
        print("⚠️ --universe all 尚未启用（先验证 hs300zz500 链路，下阶段开放）")
    return members


def pull_one(bs, code: str, start: str, end: str, out_path: str, full_refresh: bool):
    """单票增量拉取。返回 (ok, rows, msg)。CSV 已新鲜时跳过（增量）。"""
    # 增量：已有且末行 date >= end（或 >= 最近已收盘交易日）→ 跳过
    if not full_refresh and os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as f:
                last_line = f.readlines()[-1].split(",")[0].strip()
            if last_line >= end or (last_line >= _last_closed(end)):
                return True, -1, "skip-fresh"
        except (IndexError, OSError):
            pass
    for attempt in range(1, RETRY_PER_STOCK + 1):
        try:
            rs = bs.query_history_k_data_plus(code, FIELDS, start_date=start,
                                              end_date=end, frequency="d", adjustflag="2")
            if rs is None or rs.error_code != "0":
                return False, 0, f"query_err: {getattr(rs, 'error_msg', 'None') if rs else 'rs=None'}"
            n = _rows_to_csv(rs, out_path)
            return True, n, "ok" if n > 0 else "empty"
        except Exception as e:
            if attempt >= RETRY_PER_STOCK:
                return False, 0, f"{type(e).__name__}: {str(e)[:80]}"
            # 重试前重新确保登录态（zlib 流损坏/掉线自愈）
            try:
                from analysis.bs_session import ensure_login as _el
                _el()
            except Exception:
                pass
            time.sleep(1.0 * attempt)
    return False, 0, "unreachable"


_LAST_CLOSED_CACHE = {}


def _last_closed(end_date: str) -> str:
    """end（含）往前最近交易日，进程内缓存。日历不可得时退回 end 本身。"""
    if end_date in _LAST_CLOSED_CACHE:
        return _LAST_CLOSED_CACHE[end_date]
    d = datetime.strptime(end_date, "%Y-%m-%d")
    for _ in range(15):
        ds = d.strftime("%Y-%m-%d")
        try:
            if is_trading_day(ds):
                _LAST_CLOSED_CACHE[end_date] = ds
                return ds
        except Exception:
            break
        d -= timedelta(days=1)
    _LAST_CLOSED_CACHE[end_date] = end_date
    return end_date


def main():
    ap = argparse.ArgumentParser(description="选股宇宙数据湖构建器")
    ap.add_argument("--universe", choices=["hs300zz500", "all"], default="hs300zz500")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--full-refresh", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    os.makedirs(DAILY_DIR, exist_ok=True)

    # 交易日口径：非交易日 end 回退最近已收盘交易日（避免拉到半根K线的误判）
    effective_end = args.end if is_trading_day(args.end) else _last_closed(args.end)

    bs = bs_connect()
    members = fetch_members(bs, args.universe)
    print(f"📡 宇宙={args.universe} 成分 {len(members)} 只 | 区间 {args.start}~{effective_end}"
          f" | 模式={'全量' if args.full_refresh else '增量'} | workers={MAX_WORKERS}", flush=True)

    # 成分清单落盘（重写型写点备份保护）
    import csv as _csv
    import shutil
    if os.path.exists(MEMBERS_CSV):
        shutil.copy2(MEMBERS_CSV, MEMBERS_CSV + ".bak")
    with open(MEMBERS_CSV, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["code", "name", "source", "scraped_at"])
        for code, name, src in members:
            w.writerow([code, name, src, datetime.now().isoformat(timespec="seconds")])

    # 并发拉取（带失败收集 + 末尾串行重试一轮）
    # 🔴 连续失败自愈（2026-11-05 实证）：baostock 连接半死时 ensure_login 受 TTL/负缓存
    # 误报已登录 → 自拉首 200 只后 100% 失败。连续 ≥5 败强制重登，连续 ≥60 败熔断退出。
    from analysis import bs_session as _bss
    results, failed = {}, []
    done, consec_fail, forced_relogins = 0, 0, 0

    def _job(item):
        code, name, _src = item
        safe = code.replace(".", "")
        return code, pull_one(bs, code, args.start, effective_end,
                              os.path.join(DAILY_DIR, f"{safe}.csv"), args.full_refresh)

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for code, (ok, n, msg) in ex.map(_job, members):
            results[code] = (ok, n, msg)
            done += 1
            if not ok:
                failed.append((code, msg))
                consec_fail += 1
                if consec_fail == 5:
                    print(f"  ⚠️ 连续 {consec_fail} 败 → 强制重登 baostock 会话...", flush=True)
                    if _bss.force_relogin():
                        forced_relogins += 1
                        print("  ✅ 重登成功，继续", flush=True)
                if consec_fail >= 60:
                    print(f"  🔴 连续 {consec_fail} 败 → 熔断（baostock 服务疑不可用），剩余票留给下轮", flush=True)
                    break
            else:
                consec_fail = 0
            if done % 25 == 0:
                print(f"  进度 {done}/{len(members)} | 失败 {len(failed)}", flush=True)

    # 失败串行重试一轮（换冷静期）
    if failed:
        print(f"🔁 串行重试 {len(failed)} 只失败票...", flush=True)
        still = []
        for code, _msg in failed:
            ok, n, msg = pull_one(bs, code, args.start, effective_end,
                                  os.path.join(DAILY_DIR, code.replace(".", "") + ".csv"),
                                  args.full_refresh)
            results[code] = (ok, n, msg)
            if not ok:
                still.append((code, msg))
            time.sleep(0.3)
        failed = still

    n_ok = sum(1 for ok, _, _ in results.values() if ok)
    n_rows = sum(n for _, n, _ in results.values() if n > 0)
    fail_rate = len(failed) / max(len(members), 1)
    report = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "universe": args.universe, "members": len(members),
        "processed": done,
        "ok": n_ok, "failed": len(failed), "fail_rate": round(fail_rate, 4),
        "forced_relogins": forced_relogins,
        "aborted": consec_fail >= 60,
        "total_rows": n_rows, "range": [args.start, effective_end],
        "elapsed_s": round(time.time() - t0, 1),
        "failed_list": [{"code": c, "msg": m} for c, m in failed[:50]],
    }
    rp = os.path.join(UNIVERSE_DIR, f"build_report_{datetime.now().strftime('%Y%m%d')}.json")
    if os.path.exists(rp):
        shutil.copy2(rp, rp + ".bak")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"✅ 构建完成: {n_ok}/{len(members)} ok | 失败 {len(failed)} ({fail_rate:.1%})"
          f" | 累计 {n_rows} 行 | {report['elapsed_s']}s", flush=True)
    print(f"   报告: {rp}")
    if failed:
        for c, m in failed[:10]:
            print(f"   ❌ {c}: {m}")
    return 0 if fail_rate < 0.05 else 1


if __name__ == "__main__":
    sys.exit(main())
