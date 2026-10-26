#!/usr/bin/env python3
"""
新闻产物质量审计脚本 (news_quality_audit.py)
========================================
检查 news_monitor 的产出质量。

2026-10-26 口径分离（用户拍板方案 a+c，commit 本版）：
- 旧行为：把「新鲜覆盖率 <80%」当 🔴 报警 → 72h 宁缺勿旧窗在财报季/周末
  正常走低时触发假告警，并间接诱发 10-26 上午审计 agent 的假修复报告。
- 新口径：
  1) 抓取成功率 = 链路健康指标（🔴 报警线保留）
  2) 新鲜覆盖率 = 宁缺勿旧 72h 过滤后的内容供给指标（仅观察，不报警，
     财报季/周末正常走低是过滤器的正确工作结果）
  3) 留痕判读 = 依赖 commit c59ecbb 起的 stale_articles 留痕，区分：
     正常拦截旧文 / 时间戳解析疑似误拦（🔴 真故障信号）/ 上游空返回
"""

import os
import sys
import json
import re
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
# TOOLS.md 强制样板：脚本模式 sys.path 必须同时含工作区根 + analysis/
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# env 可覆盖（测试用，生产不设即走默认）
RAW_DIR = os.getenv("NEWS_AUDIT_RAW_DIR", os.path.join(WORKSPACE, "data", "news", "raw"))

# 关注池规模：SSOT 直连优先，失败退回正则解析（两条路径失败都必须可见）
try:
    from analysis.fib_extension_scan import WATCHLIST as _WL
    WATCHLIST_SIZE = len(_WL)
except Exception as _e:  # SSOT 导入失败 → 降级必须可见
    print(f"⚠️ WATCHLIST SSOT 导入失败({_e})，退回正则解析口径")
    _pat = re.compile(r"['\"](\d{6})['\"]\s*:\s*['\"]")
    try:
        with open(os.path.join(WORKSPACE, "analysis", "fib_extension_scan.py"), encoding="utf-8") as f:
            WATCHLIST_SIZE = len(_pat.findall(f.read()))
    except Exception as _e2:
        print(f"⚠️ 正则解析也失败({_e2})，按 36 兜底")
        WATCHLIST_SIZE = 36

# 留痕判读用：复用 news_monitor 的发布时间解析器（同一套口径才能判「误拦」）
try:
    from analysis.news_monitor import _parse_pub_time, FRESH_WINDOW_HOURS
except Exception as _e:
    print(f"⚠️ news_monitor 解析器导入失败({_e})，留痕判读降级为空时间检测")
    _parse_pub_time = None
    FRESH_WINDOW_HOURS = 72


def _parse_dt(ts):
    """快照 fetch_time 解析；失败返回 None"""
    ts = str(ts or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(ts[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    return None


def _classify_zero_fresh(rec):
    """零新鲜股留痕判读 → (类别, 明细)。类别: normal/suspect/empty/legacy"""
    sa = rec.get("stale_articles")
    if sa is None:
        return "legacy", "旧版快照无留痕（c59ecbb 前产物），判读不可用"
    if len(sa) == 0:
        return "empty", "stale_articles 为空且 news 为空 → 上游可能返回 0 行"
    if _parse_pub_time is not None:
        bad = [a for a in sa if not _parse_pub_time(a.get("time"))]
        if bad:
            return "suspect", f"{len(bad)} 条留痕时间无法解析（如 '{bad[0].get('time', '')[:20]}'）→ 疑似时间戳格式漂移误拦"
    return "normal", f"{len(sa)} 条留痕均为可解析旧文（宁缺勿旧正常拦截）"


def audit_today():
    today_str = datetime.now().strftime("%Y-%m-%d")
    modes = ["盘前", "盘后"]
    risks = []
    notices = []

    print(f"🔍 新闻产物质量审计 | 关注池规模: {WATCHLIST_SIZE} (SSOT 口径)")
    print("-" * 60)

    for mode in modes:
        raw_path = os.path.join(RAW_DIR, f"{today_str}_{mode}.json")
        if not os.path.exists(raw_path):
            print(f"⚪ {mode}: 尚未运行或产物缺失 (跳过)")
            continue

        with open(raw_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        stock_news = data.get("stock_news", [])
        errors = [s for s in stock_news if s.get("error")]
        ok_fetch = [s for s in stock_news if not s.get("error")]
        fresh_stocks = [s for s in ok_fetch if s.get("news")]
        zero_fresh = [s for s in ok_fetch if not s.get("news")]

        fetch_ok_rate = len(ok_fetch) / WATCHLIST_SIZE if WATCHLIST_SIZE else 0
        fresh_cov_rate = len(fresh_stocks) / WATCHLIST_SIZE if WATCHLIST_SIZE else 0
        error_rate = len(errors) / WATCHLIST_SIZE if WATCHLIST_SIZE else 0

        # 时效性：72h 内新鲜条目占比（与过滤器同口径，替代旧「今日字符串命中」的周一周末假阴性）
        fetch_dt = _parse_dt(data.get("fetch_time"))
        valid_dates, fresh_count = 0, 0
        for s in stock_news:
            for n in (s.get("news") or []):
                t = _parse_pub_time(n.get("time")) if _parse_pub_time else None
                if t is None:
                    if today_str in str(n.get("time", "")):
                        valid_dates += 1
                        fresh_count += 1
                    continue
                valid_dates += 1
                if fetch_dt is not None:
                    age_h = (fetch_dt - t).total_seconds() / 3600.0
                    if 0 <= age_h <= FRESH_WINDOW_HOURS:
                        fresh_count += 1
                elif today_str in str(n.get("time", "")):
                    fresh_count += 1
        fresh_rate = fresh_count / valid_dates if valid_dates > 0 else None

        print(f"📊 {mode} 审计结果 (fetch_time={data.get('fetch_time')}):")
        print(f"  • 抓取成功率: {len(ok_fetch)}/{WATCHLIST_SIZE} ({fetch_ok_rate:.1%})  [链路健康 · 报警指标]")
        print(f"  • 新鲜覆盖率: {len(fresh_stocks)}/{WATCHLIST_SIZE} ({fresh_cov_rate:.1%})  [宁缺勿旧 72h 过滤后口径 · 仅观察不报警 · 财报季/周末正常走低]")
        if fresh_rate is None:
            print(f"  • 时效性: 0/{valid_dates} [无已存储条目可评]")
        else:
            print(f"  • 时效性: {fresh_count}/{valid_dates} ({fresh_rate:.1%}) [72h 内新鲜条目占比]")

        # 留痕判读（零新鲜股细分）
        cls_count = {"normal": 0, "suspect": 0, "empty": 0, "legacy": 0}
        suspect_detail = []
        for s in zero_fresh:
            cls, detail = _classify_zero_fresh(s)
            cls_count[cls] += 1
            if cls in ("suspect", "empty"):
                suspect_detail.append(f"    {s.get('symbol')} {s.get('name')}: {detail}")
        print(f"  • 零新鲜股判读: {len(zero_fresh)} 只 → 正常拦截留痕 {cls_count['normal']} / 时间戳疑似误拦 {cls_count['suspect']} / 上游空返回 {cls_count['empty']} / 旧版无留痕 {cls_count['legacy']}")

        for e in errors:
            print(f"  ❌ {e.get('symbol')} {e.get('name')}: {str(e.get('error'))[:80]}")
        for d in suspect_detail:
            print(d)

        # ── 报警规则（仅真故障）──
        if error_rate > 0.2:
            risks.append(f"🔴 {mode} 错误率过高 ({error_rate:.1%})，链路可能中断")
        if cls_count["suspect"] > 0:
            risks.append(f"🔴 {mode} {cls_count['suspect']} 只股疑似时间戳格式漂移误拦（新鲜文被当旧文），需人工核对")
        if cls_count["empty"] > 0:
            risks.append(f"🔴 {mode} {cls_count['empty']} 只股上游空返回（无留痕无内容），需人工核对")
        if fresh_rate is not None and fresh_rate < 0.1:
            risks.append(f"🔴 {mode} 时效性极差 (72h 内新鲜条目占比 {fresh_rate:.1%})")
        if cls_count["legacy"] == len(zero_fresh) and zero_fresh:
            notices.append(f"⚪ {mode} 零新鲜股 {len(zero_fresh)} 只均为旧版快照无留痕，判读不可用（c59ecbb 起新产物才可判读）")
        if not risks or all(mode not in r for r in risks):
            if cls_count["normal"] > 0:
                print(f"  ✅ 新鲜覆盖率走低为宁缺勿旧正常工作，非故障（留痕证据齐全）")

    print("-" * 60)
    for n in notices:
        print(n)
    if not risks:
        print("✅ 新闻产物质量合格（口径分离版：抓取成功率 + 留痕判读为报警依据，新鲜覆盖率仅观察）")
        return 0
    print("❌ 发现内容质量风险：")
    for r in risks:
        print(f"  {r}")
    return 1


if __name__ == "__main__":
    sys.exit(audit_today())
