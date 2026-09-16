#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
斐波那契扩展位止盈参考扫描 (fib_extension_scan.py)
==================================================
对关注池/持仓股票计算"上升波段的斐波那契扩展位"(1.0/1.272/1.618/2.618)，
标注当前收盘价所处的扩展区间，结合自适应策略信号给出止盈参考提示。

用途：卖出/止盈端的理论参考锚点（不写入模拟盘交易逻辑，纯参考）。
来源理念：Obsidian《9+ 斐波那契最完整教學｜回撤找買點・扩展找出口》(mio来了)
  - 回撤找买点：0.5-0.618 黄金口袋（入场）
  - 扩展找出口：1.0(首目标等长) / 1.272-1.618(最终止盈强阻力) / 2.618(狂热终点)
  - 共振：扩展位若与水平支撑/EMA均线重合，成功率更高
  - 止损：0.786 下方或前波段低点

用法：
  python3 analysis/fib_extension_scan.py            # 全关注池扫描 → stdout
  python3 analysis/fib_extension_scan.py --save     # 扫描并锁定到 data/fib_predictions_YYYY-MM-DD.json
  python3 analysis/fib_extension_scan.py 300285     # 指定单只
输出：stdout JSON（供 agent 解析/推送）
注意：纯新浪日K（快、含当日）；个别拉不到会标 error=LIMIT，不影响其它。
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, date

import pandas as pd

# 网络兜底：进程级 socket 默认超时（防数据源挂起导致永久阻塞）
try:
    from analysis.net_guard import install_default_timeout as _install_net_timeout
    _install_net_timeout()
except ImportError:
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from analysis.net_guard import install_default_timeout as _install_net_timeout
    _install_net_timeout()


def fetch_sina_kline(symbol, datalen=150):
    """新浪日K jsonp：{date,open,high,low,close,volume}，快且含当日。"""
    prefix = "sh" if symbol.startswith("6") else "sz"
    sym = prefix + symbol
    url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_x=/"
           f"CN_MarketDataService.getKLineData?symbol={sym}"
           f"&scale=240&ma=no&datalen={datalen}")
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    try:
        raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
        m = re.search(r"=\s*\(?(\[.*?\])\s*\)?\s*;?\s*$", raw, re.S)
        if not m:
            return None
        arr = json.loads(m.group(1))
        if not arr:
            return None
        rows = []
        for r in arr:
            rows.append({
                "date": pd.Timestamp(r["day"]),
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "volume": int(float(r.get("volume", 0))),
            })
        df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        return df
    except Exception:
        return None

# 35只关注股 (code: 名称) —— 2026-09-14 同步关注池
#   撤除: 601865福莱特 / 002180奔图科技 / 300847中船汉光
#   新增: 8 只电力设备出海
WATCHLIST = {
    "600030": "中信证券", "601066": "中信建投", "600036": "招商银行",
    "601995": "中金公司", "000987": "越秀资本", "600584": "长电科技",
    "688981": "中芯国际", "002156": "通富微电", "002413": "雷科防务",
    "300014": "亿纬锂能", "002466": "天齐锂业",
    "300285": "国瓷材料", "603308": "应流股份", "300124": "汇川技术",
    "601100": "恒立液压", "002318": "久立特材", "300719": "安达维尔",
    "002335": "科华数据", "300748": "金力永磁",
    "600160": "巨化股份", "600346": "恒力石化",
    "000708": "中信特钢", "600660": "福耀玻璃", "600570": "恒生电子",
    "605566": "福莱蒽特", "000157": "中联重科", "601061": "中信金属",
    # 电力设备/特高压出海 (2026-09-07 新增)
    "600089": "特变电工", "600406": "国电南瑞", "000400": "许继电气",
    "601179": "中国西电", "002028": "思源电气", "002270": "华明装备",
    "002130": "沃尔核材", "600312": "平高电气",
}

# 自适应策略映射(code: 策略名)
try:
    with open("/Users/duguke/.openclaw/workspace/data/adaptive_strategy_map.json") as f:
        _raw_strat = json.load(f)
    # 兼容 {strategy, bucket} 对象格式与纯字符串格式
    STRAT_MAP = {k: (v.get("strategy") if isinstance(v, dict) else v) for k, v in _raw_strat.items()}
except Exception:
    STRAT_MAP = {}


def find_latest_uptrend_swing(df, lookback=150):
    """识别最近的上升波段(swing_low -> swing_high)与当前回调低点。
    优化：优先选「当前价格仍高于起点、涨幅合理、最近」的波段，
    而非整个窗口的绝对最高点（避免抓到早已走完的旧大波段）。
    若当前处深度回调/下跌（价已跌破波段起点 1.05 倍），返回 None 并标注场景不适用。"""
    if df is None or len(df) < 30:
        return None
    d = df.tail(lookback).reset_index(drop=True)
    closes = d["close"].values
    highs = d["high"].values
    lows = d["low"].values
    n = len(d)
    cur = float(closes[-1])

    def is_low(idx):
        lo, lg = max(0, idx - 4), min(n, idx + 5)
        return lows[idx] == min(lows[lo:lg]) and lows[idx] <= closes[idx]

    def is_high(idx):
        lo, lg = max(0, idx - 4), min(n, idx + 5)
        return highs[idx] == max(highs[lo:lg])

    # 从最近往回找显著 swing 高点
    candidate_hi = None
    for i in range(n - 2, max(0, n - 90) - 1, -1):
        if is_high(i) and highs[i] > cur:
            candidate_hi = i
            break
    if candidate_hi is None:
        # 当前价已创/接近阶段新高，取最近显著高点
        for i in range(n - 2, max(0, n - 90) - 1, -1):
            if is_high(i):
                candidate_hi = i
                break
    if candidate_hi is None:
        return None
    swing_high = float(highs[candidate_hi])

    # 从该高点往回找波段起点低点（显著低点）
    swing_low_i, swing_low = None, None
    for i in range(candidate_hi - 1, max(0, candidate_hi - 70) - 1, -1):
        if is_low(i):
            swing_low_i, swing_low = i, lows[i]
            break
    if swing_low_i is None:
        swing_low_i, swing_low = 0, float(min(lows[:candidate_hi]))

    run_pct = swing_high / swing_low - 1 if swing_low > 0 else 0
    if run_pct < 0.05:  # 波段涨幅<5% 不算有效
        return None

    # 当前回调低点（swing_high 之后的盘中最低）
    after = lows[candidate_hi:]
    pullback_low = float(min(after))
    pullback_low_i = candidate_hi + int(after.argmin())

    # 有效性护栏：当前价不能深度跌破波段起点（>5%），否则扩展位场景不成立
    if cur < swing_low * 0.95:
        return {"inapplicable": True, "reason": "当前价已跌破波段起点5%以上，处回调/下跌，扩展位不适用",
                "swing_low": float(swing_low), "swing_high": float(swing_high),
                "run_pct": round(run_pct, 3)}

    return {
        "swing_high": swing_high,
        "swing_low": float(swing_low),
        "swing_high_i": int(candidate_hi),
        "swing_low_i": int(swing_low_i),
        "pullback_low": pullback_low,
        "pullback_low_i": int(pullback_low_i),
        "run_pct": round(run_pct, 3),
    }


def compute_fib_targets(df, lookback: int = 150) -> dict:
    """【权威实现·单一真相源】斐波那契扩展位止盈目标 (1.0/1.272/1.618/2.618)。

    基于最近上升波段: swing_low -> swing_high -> 回调低点。
    返回 {ratio_str: price, ...} 或 {}（无有效上升波段时）。

    ⚠️ 本函数为唯一实现；portfolio_core.py / adaptive_dual.py 均从此处导入，
    勿再各自复制（历史上两份重复实现曾导致改一处漏一处）。
    """
    if df is None or len(df) < 30:
        return {}
    d = df.tail(lookback).reset_index(drop=True)
    closes = d["close"].values
    highs = d["high"].values
    lows = d["low"].values
    n = len(d)
    cur = float(closes[-1])

    def is_low(idx):
        lo, lg = max(0, idx - 4), min(n, idx + 5)
        return lows[idx] == min(lows[lo:lg]) and lows[idx] <= closes[idx]

    def is_high(idx):
        lo, lg = max(0, idx - 4), min(n, idx + 5)
        return highs[idx] == max(highs[lo:lg])

    candidate_hi = None
    for i in range(n - 2, max(0, n - 90) - 1, -1):
        if is_high(i) and highs[i] > cur:
            candidate_hi = i
            break
    if candidate_hi is None:
        for i in range(n - 2, max(0, n - 90) - 1, -1):
            if is_high(i):
                candidate_hi = i
                break
    if candidate_hi is None:
        return {}
    swing_high = float(highs[candidate_hi])

    swing_low_i, swing_low = None, None
    for i in range(candidate_hi - 1, max(0, candidate_hi - 70) - 1, -1):
        if is_low(i):
            swing_low_i, swing_low = i, lows[i]
            break
    if swing_low_i is None:
        swing_low_i, swing_low = 0, float(min(lows[:candidate_hi]))

    run_pct = swing_high / swing_low - 1 if swing_low > 0 else 0
    if run_pct < 0.05:  # 波段涨幅<5% 不算有效
        return {}

    after = lows[candidate_hi:]
    pullback_low = float(min(after))

    # 有效性护栏：当前价不能深度跌破波段起点（>5%）
    if cur < swing_low * 0.95:
        return {}

    base = pullback_low
    amp = swing_high - swing_low
    targets = {}
    for ratio in (1.0, 1.272, 1.618, 2.618):
        targets[str(ratio)] = round(base + ratio * amp, 2)
    return targets


def compute_fib_levels(sw):
    """基于 回调低点 + (高-低)*fib 算扩展位。"""
    base = sw["pullback_low"]
    amp = sw["swing_high"] - sw["swing_low"]
    levels = {}
    for ratio in (1.0, 1.272, 1.618, 2.618):
        levels[ratio] = round(base + ratio * amp, 2)
    # 关键回撤参考(入场/止损用)
    levels["0.786"] = round(sw["swing_high"] - 0.786 * amp, 2)
    levels["0.618"] = round(sw["swing_high"] - 0.618 * amp, 2)
    return levels


def locate_price(price, levels):
    """判断当前价位于哪个扩展区间。返回 (区间, 提示)。"""
    e100, e127, e161, e261 = levels[1.0], levels[1.272], levels[1.618], levels[2.618]
    e061, e078 = levels["0.618"], levels["0.786"]
    if price <= e100:
        return "未到等长目标(1.0下方)", f"距1.0等长目标还有 {(e100/price-1)*100:.1f}%"
    if price <= e127:
        return "1.0-1.272 首目标区", f"已过1.0等长目标，距1.272还有 {(e127/price-1)*100:.1f}%"
    if price <= e161:
        return "1.272-1.618 波段止盈区⭐", f"进入波段最终阻力区，距1.618还有 {(e161/price-1)*100:.1f}%"
    if price <= e261:
        return "1.618-2.618 强势延伸区", f"已过主止盈区，距2.618狂热终点还有 {(e261/price-1)*100:.1f}%"
    return "超2.618 狂热终点上方", "已突破2.618极端位，警惕见顶回落"


# 斐波那契时间序列（交易日）
FIB_SEQ = (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144)

# ── 2026-09-15 修复参数 ──
# 背景：原实现下 `next_window` 几乎永远 = 基准日+1 ——
#   fib=1/2/3 这类最短时间线语义上近乎无信息，却因"最近"总排第一，
#   成为 17/27 只股票的"第一汇聚窗口"，污染了整个展示排序。
MIN_FIB_FOR_WINDOW = 5      # 汇聚窗口至少含一条 fib>=5 的来源，剔除纯 fib1/2/3 伪窗口
MIN_WINDOW_HITS = 2         # 至少 2 条来源才算汇聚
MIN_WINDOW_LEAD_DAYS = 3    # 窗口起点须距基准日 >=3 个交易日（避免"明天就变盘"）


def _next_trade_dates_after(last_date, k):
    """返回 last_date 之后的第 1..k 个推算交易日（单次生成，供外推用）。
    仅工作日，不避节假日（A股节假日需外部日历；此处为近似，已在输出标注 approximate）。
    """
    out, cur = [], pd.Timestamp(last_date)
    while len(out) < k:
        cur = cur + pd.Timedelta(days=1)
        if cur.weekday() < 5:
            out.append(cur)
    return out


# 简易 A股节假日表（元旦/春节/清明/劳动/端午/中秋/国庆长假；尽量覆盖回测与近期）
# 用途：避免把 10-01 之类休市日当作交易日。不追求全量精确，宁保守（多休少开）。
_CN_HOLIDAYS = {
    # 2026
    "2026-01-01", "2026-01-02",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-04-06", "2026-04-07",
    "2026-05-01", "2026-05-04", "2026-05-05",
    "2026-06-19",
    "2026-09-25",
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08",
    # 2025
    "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
    "2025-02-03", "2025-02-04", "2025-04-04", "2025-05-01", "2025-05-02", "2025-05-05",
    "2025-06-02", "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06", "2025-10-07", "2025-10-08",
    # 2024
    "2024-01-01", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16",
    "2024-04-04", "2024-04-05", "2024-05-01", "2024-05-02", "2024-05-03", "2024-06-10",
    "2024-09-16", "2024-09-17", "2024-10-01", "2024-10-02", "2024-10-03", "2024-10-04", "2024-10-07",
}


def _is_trade_day(ts):
    ts = pd.Timestamp(ts)
    if ts.weekday() >= 5:
        return False
    return str(ts.date()) not in _CN_HOLIDAYS


def _find_swing_points(d, lookback_span=250, max_points=5):
    """识别显著高低点（±4 邻域极值），返回按索引升序的 [{'kind','idx','price'}]。
    仅用过去数据，不含未来函数。"""
    n = len(d)
    highs = d["high"].values
    lows = d["low"].values
    closes = d["close"].values
    lo_start = max(5, n - lookback_span)  # 留出 ±4 邻域

    def is_low(idx):
        lo, lg = max(0, idx - 4), min(n, idx + 5)
        return lows[idx] == min(lows[lo:lg]) and lows[idx] <= closes[idx]

    def is_high(idx):
        lo, lg = max(0, idx - 4), min(n, idx + 5)
        return highs[idx] == max(highs[lo:lg])

    pts = []
    for i in range(lo_start, n - 4):
        if is_low(i):
            pts.append({"kind": "swing_low", "idx": int(i), "price": float(lows[i])})
        elif is_high(i):
            pts.append({"kind": "swing_high", "idx": int(i), "price": float(highs[i])})
    # 去重相邻同类点、保留最近 max_points 个显著点
    dedup = []
    for p in pts:
        if dedup and dedup[-1]["kind"] == p["kind"]:
            # 同类相邻：低点取更低、高点取更高
            better = (p["price"] < dedup[-1]["price"]) if p["kind"] == "swing_low" \
                else (p["price"] > dedup[-1]["price"])
            if better:
                dedup[-1] = p
        else:
            dedup.append(p)
    return dedup[-max_points:] if len(dedup) > max_points else dedup


def compute_fib_time_targets(df, lookback=250, fib_seq=None, window=2, max_fib=144):
    """斐波那契时间线：从显著高低点向后投射交易日序列，找未来变盘时点/汇聚窗口。

    ⚠️ 仅作【时点预警辅助】，不可作方向信号；须配合价格维度"时价合一"再作决策。

    参数:
        df: 日线 DataFrame（含 date/open/high/low/close），按日期升序
        lookback: 回看窗口行数（默认 250 ≈ 一年交易日）
        fib_seq: 时间序列，默认 FIB_SEQ
        window: 汇聚聚类窗口宽度（交易日，默认 ±2）
        max_fib: 最大投射步长（截断远未来噪音，默认 144）

    返回:
        {
          "as_of": 基准日, "as_of_idx": 基准行索引,
          "anchors": [{anchor, anchor_date, anchor_idx, targets:[{fib,date,idx,passed}]}],
          "confluence": [{window_start, window_end, hits, raw_hits, sources, strength}],
          "next_window": hits 最大的未来汇聚窗口 or None,
          "calendar_approximate": True,  # 未来日期按粗粒度节假日表外推，非交易所日历
          "filters": {min_fib_for_window, min_window_hits, min_window_lead_days},
          "note": 免责说明
        }
    无有效数据时返回 {}。

    过滤规则（2026-09-15 新增，修复「next_window 永远=明天」伪窗口）:
      - 汇聚至少 MIN_WINDOW_HITS 条【去重后】来源（同锚点多条 fib 只算 1 个来源）
      - 窗口须含至少一条 fib >= MIN_FIB_FOR_WINDOW（排除纯 fib1/2/3 伪汇聚）
      - 窗口起点距基准日 >= MIN_WINDOW_LEAD_DAYS 个交易日
      - confluence 按 hits 降序（信息量优先），不再按日期/宽度
    """
    if df is None or len(df) < 60:
        return {}
    d = df.tail(lookback).reset_index(drop=True)
    n = len(d)
    as_of_idx = n - 1
    as_of = str(pd.Timestamp(d["date"].iloc[-1]).date())
    seq = tuple(fib_seq) if fib_seq else FIB_SEQ
    seq = tuple(k for k in seq if k <= max_fib)

    def idx_to_date(t):
        """索引 → 日期；超出序列尾部时按交易日外推（跳周末 + 粗粒度节假日表）。"""
        if t <= n - 1:
            return str(pd.Timestamp(d["date"].iloc[t]).date())
        last = pd.Timestamp(d["date"].iloc[-1])
        extra = t - (n - 1)
        cur, added = last, 0
        while added < extra:
            cur = cur + pd.Timedelta(days=1)
            if _is_trade_day(cur):
                added += 1
        return str(cur.date())

    anchors_pts = _find_swing_points(d, lookback_span=lookback)
    if not anchors_pts:
        return {"as_of": as_of, "as_of_idx": as_of_idx, "anchors": [],
                "confluence": [], "next_window": None,
                "note": "未识别到显著高低点；仅时点预警，方向需配合价格指标（时价合一）"}

    # ── 用法① 时间区间：每个锚点向后投射 FIB_SEQ ──
    source_lines = []  # (idx, label)
    anchors_out = []
    for a in anchors_pts:
        targets = []
        for k in seq:
            t = a["idx"] + k
            targets.append({"fib": k, "idx": int(t), "date": idx_to_date(t),
                            "passed": t <= as_of_idx})
        anchors_out.append({
            "anchor": a["kind"], "anchor_date": str(pd.Timestamp(d["date"].iloc[a["idx"]]).date()),
            "anchor_idx": a["idx"], "targets": targets,
        })
        for k in seq:
            t = a["idx"] + k
            if t > as_of_idx:
                source_lines.append((t, f"{a['kind']}@{k}"))

    # ── 用法② 时间扩展：最近 3 点 A→B→C ──
    if len(anchors_pts) >= 3:
        A, B, C = anchors_pts[-3], anchors_pts[-2], anchors_pts[-1]
        span = B["idx"] - A["idx"]
        if span > 0:
            for r in (1.272, 1.618, 2.618):
                t = C["idx"] + round(span * r)
                if t > as_of_idx:
                    source_lines.append((t, f"ext({r})"))

    # ── 用法③ 汇聚：按索引聚类（±window 交易日合并）──
    # hits 去重：同一锚点（同 kind 同 anchor_idx）的多个邻近 fib 投射算 1 个来源，
    # 否则 swing_high@8 与 swing_high@34 同源却被算作 2 条独立线，系统性高估强度。
    source_lines.sort()
    clusters = []
    for t, label in source_lines:
        if clusters and t - clusters[-1]["max"] <= window:
            clusters[-1]["srcs"].append(label)
            clusters[-1]["max"] = t
        else:
            clusters.append({"start": t, "max": t, "srcs": [label]})

    def _src_key(label):
        """来源去重键：ext(1.272) → 比值本身；swing_high@21 → 锚点种类（不含 fib）"""
        if label.startswith("ext("):
            return label
        return label.split("@")[0]

    def _fib_of(label):
        if "@" in label:
            try:
                return int(label.split("@")[1])
            except ValueError:
                return 0
        return 0

    confluence = []
    for c in clusters:
        uniq = []
        for lb in c["srcs"]:
            if _src_key(lb) not in [ _src_key(u) for u in uniq ]:
                uniq.append(lb)
        hits = len(uniq)
        if hits < MIN_WINDOW_HITS:
            continue
        # 伪窗口过滤：整个汇聚必须含至少一条 fib >= MIN_FIB_FOR_WINDOW 的来源
        max_fib_in = max((_fib_of(lb) for lb in c["srcs"]), default=0)
        if max_fib_in < MIN_FIB_FOR_WINDOW:
            continue
        # 起跑过滤：窗口起点须距基准日 >=MIN_WINDOW_LEAD_DAYS 个交易日
        if c["start"] - as_of_idx < MIN_WINDOW_LEAD_DAYS:
            continue
        confluence.append({
            "window_start": idx_to_date(c["start"]),
            "window_end": idx_to_date(c["max"]),
            "idx": c["start"],
            "hits": hits,
            "raw_hits": len(c["srcs"]),
            "sources": uniq,
            "strength": "high" if hits >= 3 else "medium",
        })
    # 排序：先按 hits（信息量）降序，再按日期升序 —— 不再让"最宽最无效"的窗口霸榜
    confluence.sort(key=lambda x: (-x["hits"], x["idx"]))
    next_window = confluence[0] if confluence else None

    return {
        "as_of": as_of, "as_of_idx": as_of_idx,
        "anchors": anchors_out,
        "confluence": confluence,
        "next_window": next_window,
        "calendar_approximate": True,   # 未来日期按粗粒度节假日表外推，非交易所日历
        "filters": {
            "min_fib_for_window": MIN_FIB_FOR_WINDOW,
            "min_window_hits": MIN_WINDOW_HITS,
            "min_window_lead_days": MIN_WINDOW_LEAD_DAYS,
        },
        "note": "仅时点预警，不可作方向信号；须配合价格指标（时价合一）再作决策",
    }


def scan(symbol, name=None):
    name = name or WATCHLIST.get(symbol, symbol)
    df = fetch_sina_kline(symbol)  # 新浪日K（快、含当日），无 baostock 回退
    if df is None or len(df) < 30:
        return {"symbol": symbol, "name": name, "error": "LIMIT"}

    price = float(df["close"].iloc[-1])
    dt = str(df["date"].iloc[-1].date())
    sw = find_latest_uptrend_swing(df)
    if sw is None:
        return {"symbol": symbol, "name": name, "date": dt, "price": price,
                "note": "未识别到有效上升波段", "error": "no_uptrend"}
    if sw.get("inapplicable"):
        return {"symbol": symbol, "name": name, "date": dt, "price": price,
                "strategy": STRAT_MAP.get(symbol, "?"),
                "note": sw["reason"],
                "swing_low": sw["swing_low"], "swing_high": sw["swing_high"],
                "run_pct": sw["run_pct"],
                "error": "inapplicable"}

    levels = compute_fib_levels(sw)
    zone, hint = locate_price(price, levels)
    strat = STRAT_MAP.get(symbol, "?")

    time_info = compute_fib_time_targets(df)
    return {
        "symbol": symbol, "name": name, "date": dt, "price": price,
        "strategy": strat,
        "swing_high": sw["swing_high"], "swing_low": sw["swing_low"],
        "pullback_low": sw["pullback_low"],
        "levels": levels,
        "zone": zone, "hint": hint,
        "time_windows": (time_info or {}).get("next_window"),
        "fib_time": time_info,
    }


def main():
    args = sys.argv[1:]
    save = "--save" in args
    targets = [a for a in args if not a.startswith("--")]
    if not targets:
        targets = list(WATCHLIST.keys())
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(scan, sym): sym for sym in targets}
        for f in as_completed(futs):
            sym = futs[f]
            try:
                results.append(f.result())
            except Exception as e:
                results.append({"symbol": sym, "name": WATCHLIST.get(sym, sym),
                                "error": "EXC:" + str(e)})
    results.sort(key=lambda x: x.get("symbol", ""))

    if save:
        today = date.today().strftime("%Y-%m-%d")
        out = f"/Users/duguke/.openclaw/workspace/data/fib_predictions_{today}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"date": today, "baseline": results}, f, ensure_ascii=False, indent=2)
        print(f"✅ 已锁定止盈位基线 → {out}（{len([r for r in results if not r.get('error')])}/{len(results)} 只有效）")
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
