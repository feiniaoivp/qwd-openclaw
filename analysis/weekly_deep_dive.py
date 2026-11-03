#!/usr/bin/env python3
"""周线深度分析数据引擎：MACD(周) / BOLL(周) / 筹码峰。

用法: python3 analysis/weekly_deep_dive.py [股票名称，默认取 WATCHLIST 中匹配 "中信金属" 的票]
产出: analysis/daily/<today>_<sym>_weekly_data.json （主结果先落盘，再打印摘要）

口径说明:
- 周线: baostock frequency=w, adjustflag=2 前复权；MACD(12,26,9)，柱 = 2*(DIF-DEA)（国内口径）
- 布林: 20 周 +/-2 sigma（总体 sigma, ddof=0）
- 筹码: 完全复用 scripts/chip_distribution_study 的数据路径与同参算法
  （400 档、turn%/100、cap 0.5、三角分布峰=(h+l+2c)/4），本脚本只扩展"峰位/支撑/压力"提取。
- 数据质量: 601061 有坏数据史，脚本内做 双源交叉校验（baostock vs sina_hq）+ >25% 拒收标记。
"""
import os
import sys
import json
import shutil
import logging
import datetime as _dt

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis"), os.path.join(WORKSPACE, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

from analysis.fib_extension_scan import WATCHLIST

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("weekly_deep")

TARGET_NAME = sys.argv[1] if len(sys.argv) > 1 else "中信金属"
START_DAILY = "2023-03-01"
START_WEEKLY = "2023-03-01"


def _bak(path):
    """写前 .bak 备份（backup_guard 铁律；本脚本写带日期的新文件，重写时先备份）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        log.warning("写前备份失败(不阻断): %s: %s", path, _e)


def resolve_symbol():
    for k, v in WATCHLIST.items():
        if TARGET_NAME in str(v):
            return k
    raise RuntimeError("WATCHLIST 中未找到: " + TARGET_NAME)


def baostock_kline(sym, freq, start, end):
    import baostock as bs
    full = ("sh." if str(sym).startswith("6") else "sz.") + str(sym)
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError("baostock login 失败: %s" % lg.error_msg)
    fields = "date,open,high,low,close,volume,amount,turn"
    rs = bs.query_history_k_data_plus(full, fields, start_date=start, end_date=end,
                                      frequency=freq, adjustflag="2")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    err = (rs.error_code, rs.error_msg)
    bs.logout()
    if err[0] != "0":
        raise RuntimeError("baostock %s 查询失败: %s" % (freq, str(err)))
    df = pd.DataFrame(rows, columns=fields.split(","))
    if df.empty:
        return df
    df["date"] = df["date"].astype(str)
    for c in ("open", "high", "low", "close", "volume", "amount", "turn"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def sina_spot(sym):
    """sina_hq 实时接口（盘后=最近收盘）。失败可见告警并返回 None。"""
    try:
        import requests
        code = ("sh" + sym) if str(sym).startswith("6") else ("sz" + sym)
        r = requests.get("https://hq.sinajs.cn/list=" + code,
                         headers={"Referer": "https://finance.sina.com.cn"}, timeout=8)
        r.encoding = "gbk"
        parts = r.text.split(",")
        if len(parts) > 3 and '"' in parts[0]:
            name = parts[0].split('"')[1]
            return {"name": name, "last": float(parts[3]), "prev_close": float(parts[2])}
        log.warning("sina spot 返回异常(可见): %r", r.text[:60])
        return None
    except Exception as e:
        log.warning("sina spot 交叉校验失败(可见): %s", e)
        return None


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def weekly_indicators(w):
    w = w.reset_index(drop=True)
    c = w["close"]
    dif = _ema(c, 12) - _ema(c, 26)
    dea = _ema(dif, 9)
    hist = 2.0 * (dif - dea)
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std(ddof=0)
    up = mid + 2.0 * sd
    dn = mid - 2.0 * sd
    pc = c.shift(1)
    tr = pd.concat([w["high"] - w["low"], (w["high"] - pc).abs(), (w["low"] - pc).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    bw = (up - dn) / mid
    return {"dif": dif, "dea": dea, "hist": hist, "up": up, "mid": mid,
            "dn": dn, "atr14": atr14, "bw": bw}


def last_cross(dif, dea):
    diff = (dif - dea).values.astype(float)
    for i in range(len(diff) - 1, 0, -1):
        if np.isnan(diff[i]) or np.isnan(diff[i - 1]):
            continue
        if diff[i - 1] <= 0 < diff[i]:
            return i, "golden"
        if diff[i - 1] >= 0 > diff[i]:
            return i, "dead"
    return None, None


def swing_points(vals, order=3, mode="min"):
    pts = []
    n = len(vals)
    for i in range(order, n - order):
        v = vals[i]
        if np.isnan(v):
            continue
        seg = vals[i - order:i + order + 1]
        seg = seg[~np.isnan(seg)]
        if mode == "min" and v <= seg.min() and (seg < v).any():
            pts.append(i)
        if mode == "max" and v >= seg.max() and (seg > v).any():
            pts.append(i)
    return pts


def divergence(close_v, dif_v):
    """底背离: 更低低点 + DIF 更高; 顶背离: 更高高 + DIF 更低。返回 (type, 描述)。"""
    mins = swing_points(close_v, 3, "min")
    if len(mins) >= 2:
        a, b = mins[-2], mins[-1]
        if close_v[b] < close_v[a] and dif_v[b] > dif_v[a]:
            return "bottom", {"prev_low": float(close_v[a]), "last_low": float(close_v[b]),
                              "prev_dif": float(dif_v[a]), "last_dif": float(dif_v[b])}
    maxs = swing_points(close_v, 3, "max")
    if len(maxs) >= 2:
        a, b = maxs[-2], maxs[-1]
        if close_v[b] > close_v[a] and dif_v[b] < dif_v[a]:
            return "top", {"prev_high": float(close_v[a]), "last_high": float(close_v[b]),
                           "prev_dif": float(dif_v[a]), "last_dif": float(dif_v[b])}
    return None, None


def chip_distribution_full(df, n_bins=400, turn_cap=0.5):
    """与 chip_distribution_study.chip_metrics 完全同参的分布重建（用于峰位提取）。"""
    lo_all = float(df["low"].min())
    hi_all = float(df["high"].max())
    grid = np.linspace(lo_all, hi_all, n_bins)
    centers = (grid[:-1] + grid[1:]) / 2.0
    chips = np.zeros(n_bins - 1)
    for row in df.itertuples(index=False):
        t = row.turn / 100.0 if pd.notna(row.turn) else 0.0
        t = min(max(t, 0.0), turn_cap)
        chips *= (1.0 - t)
        peak = (row.high + row.low + 2.0 * row.close) / 4.0
        vol = row.volume if pd.notna(row.volume) and row.volume > 0 else 0.0
        if vol > 0:
            half = max(row.high - row.low, 1e-9)
            wgt = 1.0 - np.abs(centers - peak) / half
            wgt = np.clip(wgt, 0.0, None)
            s = wgt.sum()
            if s > 0:
                chips += vol * wgt / s
    return centers, chips


def peak_zones(centers, chips, total, max_peaks=4):
    k = np.convolve(chips, np.ones(5) / 5.0, mode="same")
    raw = []
    for i in range(2, len(k) - 2):
        if k[i] >= k[i - 1] and k[i] >= k[i + 1] and k[i] > k[i - 2] and k[i] > k[i + 2]:
            raw.append(i)
    raw.sort(key=lambda i: k[i], reverse=True)
    picked = []
    min_sep = max(1, int(len(centers) * 0.015))
    for i in raw:
        if all(abs(i - j) > min_sep for j, _ in picked):
            picked.append((i, k[i]))
        if len(picked) >= max_peaks:
            break
    zones = []
    for i, h in picked:
        thr = 0.5 * h
        a = i
        while a > 0 and k[a - 1] > thr:
            a -= 1
        b = i
        while b < len(k) - 1 and k[b + 1] > thr:
            b += 1
        share = float(chips[a:b + 1].sum() / total)
        zones.append({"center": float(centers[i]), "low": float(centers[a]),
                      "high": float(centers[b]), "share_pct": round(share * 100, 2)})
    zones.sort(key=lambda z: z["share_pct"], reverse=True)
    return zones


def main():
    sym = resolve_symbol()
    today = _dt.date.today().strftime("%Y-%m-%d")
    log.info("目标: %s (%s), today=%s", TARGET_NAME, sym, today)

    try:
        from analysis.service import is_trading_day
        today_is_td = bool(is_trading_day(today))
    except Exception as e:
        today_is_td = None
        log.warning("is_trading_day 校验失败(可见告警): %s", e)

    daily = baostock_kline(sym, "d", START_DAILY, today)
    if daily.empty or daily["close"].notna().sum() < 60:
        raise RuntimeError("baostock 日线数据不足，无法分析")
    daily = daily.dropna(subset=["close"]).reset_index(drop=True)
    last_date = str(daily["date"].iloc[-1])
    last_close = float(daily["close"].iloc[-1])
    lag_days = (_dt.date.today() - _dt.datetime.strptime(last_date, "%Y-%m-%d").date()).days
    if lag_days > 5:
        log.warning("日线最新 bar 距今 %d 天（数据滞后检查）", lag_days)

    weekly = baostock_kline(sym, "w", START_WEEKLY, today)
    if weekly.empty:
        raise RuntimeError("baostock 周线为空")
    weekly = weekly.dropna(subset=["close"]).reset_index(drop=True)

    # ---- 双源交叉校验（601061 坏数据防线: >25% 拒收）----
    xcheck = {"baostock_last_close": last_close, "baostock_last_date": last_date}
    spot = sina_spot(sym)
    if spot:
        diff_pct = (spot["last"] - last_close) / last_close * 100.0 if last_close else None
        xcheck.update({"sina_last": spot["last"], "sina_name": spot["name"],
                       "diff_pct": round(diff_pct, 2) if diff_pct is not None else None,
                       "reject": bool(abs(diff_pct) > 25.0) if diff_pct is not None else None})
        if xcheck["reject"]:
            log.warning("🔴 双源差异 >25%%，按坏数据防线标记拒收: %s", xcheck)

    # ---- 日线背景指标 ----
    c = daily["close"]
    daily["ema20"] = _ema(c, 20)
    daily["ema50"] = _ema(c, 50)
    daily["ma20"] = c.rolling(20).mean()
    daily["ma60"] = c.rolling(60).mean()
    daily["ma120"] = c.rolling(120).mean()
    daily["ma250"] = c.rolling(250).mean()
    pc = c.shift(1)
    tr = pd.concat([daily["high"] - daily["low"], (daily["high"] - pc).abs(),
                    (daily["low"] - pc).abs()], axis=1).max(axis=1)
    daily["atr14"] = tr.rolling(14).mean()

    drow = daily.iloc[-1]
    daily_ctx = {
        "last_date": last_date,
        "close": last_close,
        "chg_5d_pct": round((last_close / float(c.iloc[-6]) - 1) * 100, 2) if len(c) > 6 else None,
        "chg_20d_pct": round((last_close / float(c.iloc[-21]) - 1) * 100, 2) if len(c) > 21 else None,
        "ema20": round(float(drow["ema20"]), 2),
        "ema50": round(float(drow["ema50"]), 2),
        "ma20": round(float(drow["ma20"]), 2),
        "ma60": round(float(drow["ma60"]), 2),
        "ma120": round(float(drow["ma120"]), 2),
        "ma250": round(float(drow["ma250"]), 2) if pd.notna(drow["ma250"]) else None,
        "atr14": round(float(drow["atr14"]), 3),
        "high_20d": round(float(daily["high"].iloc[-20:].max()), 2),
        "low_20d": round(float(daily["low"].iloc[-20:].min()), 2),
        "high_60d": round(float(daily["high"].iloc[-60:].max()), 2),
        "low_60d": round(float(daily["low"].iloc[-60:].min()), 2),
        "high_250d": round(float(daily["high"].iloc[-250:].max()), 2),
        "low_250d": round(float(daily["low"].iloc[-250:].min()), 2),
        "turn_5d_avg": round(float(daily["turn"].iloc[-5:].mean()), 2),
        "turn_20d_avg": round(float(daily["turn"].iloc[-20:].mean()), 2),
    }

    # ---- 周线指标 ----
    wi = weekly_indicators(weekly)
    wclose = weekly["close"].reset_index(drop=True)
    wc = float(wclose.iloc[-1])
    n_w = len(wclose)
    xidx, xtype = last_cross(wi["dif"], wi["dea"])
    weeks_since_cross = (n_w - 1 - xidx) if xidx is not None else None
    div_type, div_detail = divergence(wclose.values.astype(float), wi["dif"].values.astype(float))
    bw_series = wi["bw"].dropna()
    bw_pct = float((bw_series < bw_series.iloc[-1]).mean() * 100) if len(bw_series) else None
    wk = {
        "last_week_date": str(weekly["date"].iloc[-1]),
        "weeks_total": n_w,
        "close": wc,
        "chg_5w_pct": round((wc / float(wclose.iloc[-6]) - 1) * 100, 2) if n_w > 6 else None,
        "chg_13w_pct": round((wc / float(wclose.iloc[-14]) - 1) * 100, 2) if n_w > 14 else None,
        "dif": round(float(wi["dif"].iloc[-1]), 4),
        "dea": round(float(wi["dea"].iloc[-1]), 4),
        "hist": round(float(wi["hist"].iloc[-1]), 4),
        "hist_prev3": [round(float(v), 4) for v in wi["hist"].iloc[-4:-1]],
        "dif_zero_above": bool(wi["dif"].iloc[-1] > 0),
        "dea_zero_above": bool(wi["dea"].iloc[-1] > 0),
        "last_cross_type": xtype,
        "weeks_since_cross": weeks_since_cross,
        "boll_up": round(float(wi["up"].iloc[-1]), 2),
        "boll_mid": round(float(wi["mid"].iloc[-1]), 2),
        "boll_dn": round(float(wi["dn"].iloc[-1]), 2),
        "pct_b": round(float((wc - float(wi["dn"].iloc[-1])) /
                             (float(wi["up"].iloc[-1]) - float(wi["dn"].iloc[-1]))), 3)
        if float(wi["up"].iloc[-1]) != float(wi["dn"].iloc[-1]) else None,
        "bandwidth": round(float(wi["bw"].iloc[-1]), 4),
        "bandwidth_pctile": round(bw_pct, 1) if bw_pct is not None else None,
        "boll_mid_slope_5w_pct": round((float(wi["mid"].iloc[-1]) / float(wi["mid"].iloc[-6]) - 1) * 100, 2)
        if n_w > 6 and pd.notna(wi["mid"].iloc[-6]) else None,
        "atr14_weekly": round(float(wi["atr14"].iloc[-1]), 3),
        "high_26w": round(float(weekly["high"].iloc[-26:].max()), 2) if n_w >= 26 else None,
        "low_26w": round(float(weekly["low"].iloc[-26:].min()), 2) if n_w >= 26 else None,
        "high_52w": round(float(weekly["high"].iloc[-52:].max()), 2) if n_w >= 52 else None,
        "low_52w": round(float(weekly["low"].iloc[-52:].min()), 2) if n_w >= 52 else None,
        "vol_5w_vs_20w": round(float(weekly["volume"].iloc[-5:].mean() /
                                     max(weekly["volume"].iloc[-25:-5].mean(), 1e-9)), 2) if n_w > 25 else None,
        "divergence": div_type,
        "divergence_detail": div_detail,
    }

    # ---- 筹码（复用 chip_distribution_study 数据路径）----
    chip_out = {}
    try:
        import chip_distribution_study as cds
        chip_df = cds.fetch_chip_input(sym, ref_date=last_date)
        if chip_df is None or len(chip_df) < 120:
            raise RuntimeError("chip df 不足: %s" % (0 if chip_df is None else len(chip_df)))
        chip_df = chip_df.dropna(subset=["close"]).reset_index(drop=True)
        chip_last_date = str(chip_df["date"].iloc[-1])
        if chip_last_date != last_date:
            log.warning("chip 数据末日(%s) 与日线末日(%s)不一致(可见告警)", chip_last_date, last_date)
        metrics = cds.chip_metrics(chip_df)
        mlast = metrics.iloc[-1] if len(metrics) else None
        centers, chips = chip_distribution_full(chip_df)
        total = float(chips.sum())
        zones = peak_zones(centers, chips, total)
        cum = np.cumsum(chips) / total
        def _q(qv):
            return round(float(centers[min(np.searchsorted(cum, qv), len(centers) - 1)]), 2)
        chip_out = {
            "window_start": str(chip_df["date"].iloc[0]),
            "window_end": chip_last_date,
            "rows": int(len(chip_df)),
            "winner_pct": round(float(mlast["winner_pct"]) * 100, 2) if mlast is not None else None,
            "avg_cost": round(float(last_close) / (1 + float(mlast["cost_bias"])), 3)
            if mlast is not None and pd.notna(mlast["cost_bias"]) else None,
            "cost_bias_pct": round(float(mlast["cost_bias"]) * 100, 2)
            if mlast is not None and pd.notna(mlast["cost_bias"]) else None,
            "conc90": round(float(mlast["conc90"]), 4) if mlast is not None and pd.notna(mlast["conc90"]) else None,
            "p10": _q(0.10), "p90": _q(0.90), "p05": _q(0.05), "p95": _q(0.95),
            "peak_zones": zones,
            "winner_trend_10d": [round(float(v) * 100, 1) for v in metrics["winner_pct"].iloc[-10:]],
            "winner_trend_dates": [str(x) for x in metrics["date"].iloc[-10:]],
        }
    except Exception as e:
        log.warning("筹码计算失败(可见告警，不阻断其他分析): %s", e)
        chip_out = {"error": str(e)}

    result = {
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "target": {"name": TARGET_NAME, "symbol": sym},
        "today_is_trading_day": today_is_td,
        "cross_check": xcheck,
        "daily_context": daily_ctx,
        "weekly": wk,
        "chip": chip_out,
        "sources": {
            "kline": "baostock frequency=d/w adjustflag=2 前复权",
            "spot_cross": "sina_hq hq.sinajs.cn (盘后=最近收盘)",
            "chip": "scripts/chip_distribution_study.py 同参经典衰减法 (400档/turn衰减/三角分布)",
        },
    }

    out = os.path.join(WORKSPACE, "analysis", "daily",
                       "%s_%s_weekly_data.json" % (today, sym))
    _bak(out)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("SAVED:", out)
    print(json.dumps(result, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
