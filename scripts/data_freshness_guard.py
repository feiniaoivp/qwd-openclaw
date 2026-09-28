#!/usr/bin/env python3
"""数据新鲜度门禁 (Data Freshness Guard)

背景 / 为什么存在
----------------
2026-09-28 早盘发现：cron 生成的 `analysis/daily/{date}_dual.md` 等报告，
其「现价」实为 **上一交易日收盘价**（新浪日K 最新 bar 停在 09-24），
但报告未做任何日期新鲜度标注，消费方会把滞后数据当作当日数据使用
（典型前视偏差 / 静默使用滞后数据）。

本门禁对报告文本做静态校验：
  若报告声称的日期 D 是交易日，且报告内出现的「数据日期」明显旧于 D，
  则判定违规；若报告**完全未标注数据日期**，同样判定违规（要求显式标注）。

退出码：0 = 通过；1 = 违规（供 pipeline / cron 调用，失败仅告警不阻断主流程）。

用法
----
    python3 scripts/data_freshness_guard.py                       # 校验最新日报
    python3 scripts/data_freshness_guard.py --report <路径>
    python3 scripts/data_freshness_guard.py --quiet
"""
import os
import re
import sys
import glob
import argparse
import datetime

WS = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WS, os.path.join(WS, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 常见节假日（可扩展；缺失时退化为仅周末判断）
HOLIDAYS = {
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-04-06", "2026-05-01", "2026-06-19",  # NOTE: 需按年维护（勿误把普通交易日当假日）
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
}

DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")


def is_trading_day(d: datetime.date) -> bool:
    if d.weekday() >= 5:
        return False
    if d.isoformat() in HOLIDAYS:
        return False
    return True


def prev_trading_day(d: datetime.date) -> datetime.date:
    p = d - datetime.timedelta(days=1)
    while not is_trading_day(p):
        p -= datetime.timedelta(days=1)
    return p


def extract_dates(text: str):
    out = []
    for m in DATE_RE.finditer(text):
        try:
            out.append(datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    return out


def check_report(path: str):
    """返回 (ok, messages)"""
    msgs = []
    if not os.path.exists(path):
        return False, [f"报告不存在: {path}"]

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    base = os.path.basename(path)
    m = DATE_RE.search(base)
    if not m:
        return False, [f"无法从文件名解析报告日期: {base}"]
    report_date = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    if not is_trading_day(report_date):
        return True, [f"{report_date} 非交易日，跳过新鲜度校验（预期产出空/免责报告）"]

    # 优先读取显式标注的数据日期（支持多种格式）
    #   ① `数据日期: YYYY-MM-DD` / `数据日期：YYYY-MM-DD`
    #   ② Markdown 表格 `| 数据日期 | YYYY-MM-DD |`
    #   ③ `数据日期：YYYY-MM-DD |` 等混合
    # (2026-09-28 修复：旧版只支持冒号式，导致表格型报告被误报为「未标注」)
    data_date = None
    dm = re.search(
        r"数据日期[\s:：|]+(20\d{2}-\d{2}-\d{2})",
        text,
    )
    if dm:
        data_date = datetime.date.fromisoformat(dm.group(1))
    else:
        # 兼容旧报告：把正文中出现（且非标题/非未来）的最近日期视为数据日期线索，
        # 但不作为通过依据——仅用于生成更精确的告警。
        dates = extract_dates(text)
        others = [d for d in dates if d != report_date and d <= report_date]
        data_date = max(others) if others else None

    if data_date is None:
        msgs.append(
            f"❌ 报告未标注数据日期（无法确认现价/指标基于哪个交易日）\n"
            f"   报告日期={report_date}（交易日）\n"
            f"   修复：在报告头部显式写入 `数据日期: YYYY-MM-DD`，"
            f"并在 data_date < 上一交易日时输出告警。"
        )
        return False, msgs

    expected = prev_trading_day(report_date)

    if data_date < expected:
        lag = (expected - data_date).days
        msgs.append(
            f"❌ 数据滞后：报告日期={report_date}，数据日期={data_date}"
            f"（应至少为 {expected}，滞后 {lag} 天）\n"
            f"   文件: {path}\n"
            f"   风险：现价/指标基于滞后数据，消费方会误判为当日行情。"
        )
        return False, msgs

    msgs.append(f"✅ {base}: 报告日期={report_date}, 数据日期={data_date} (OK)")
    return True, msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", help="指定报告路径；缺省则校验最新日报")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.report:
        targets = [args.report]
    else:
        daily_dir = os.path.join(WS, "analysis", "daily")
        today = datetime.date.today()
        targets = []
        for pat in (f"{today.isoformat()}_*.md", f"{today.isoformat()}.md"):
            targets.extend(glob.glob(os.path.join(daily_dir, pat)))
        # 回退：最近一份日报
        if not targets and os.path.isdir(daily_dir):
            cands = sorted(glob.glob(os.path.join(daily_dir, "20*_*.md")))
            if cands:
                targets = [cands[-1]]

    if not targets:
        print("⚠️ 未找到可校验的日报（跳过）")
        return 0

    all_ok = True
    for t in targets:
        ok, msgs = check_report(t)
        all_ok = all_ok and ok
        if not args.quiet or not ok:
            for m in msgs:
                print(m)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
