#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
斐波那契时间线回测验证 (fib_time_backtest.py)
==================================================
严格遵守"预先声明→前瞻跟踪→事后检验"，逐日滚动回测。

评估指标：
- 命中率 = 命中窗口数 / 预警窗口数（目标 ≥ 40%）
- **随机基准对照（强制）**：同股票/同基准日/同窗口长度、随机偏移的窗口命中率。
  仅当 Fib 命中率「显著高于」随机基准时才算通过（单一 40% 阈值无意义）。
- 时价合一增强：仅时间线 vs 时间线∩价格关键位（后者须高 ≥15pct）
- 按 fib 序列分层、按窗口宽度分层（宽度是命中率的主要混杂因素）

⚠️ 2026-09-15 修订：
  1. 删除本地复刻 compute_fib_time_targets_local —— 回测必须调用生产实现
     (analysis.fib_extension_scan.compute_fib_time_targets)，否则验证与生产漂移。
  2. 原「命中率 ≥ 40% → ✅通过」判定被证伪：detect_reversal 判定器本身在
     20+ 交易日窗口上就有 ~63% 自然命中率，与随机窗口无差异（实测 -0.35pct）。
     现改为以「超额 = Fib − 随机」为判定核心。
  3. 生产侧新增窗口过滤（MIN_FIB_FOR_WINDOW / MIN_WINDOW_HITS /
     MIN_WINDOW_LEAD_DAYS，详见 fib_extension_scan.py）与 hits 去重，
     本回测因 import 生产实现而自动沿用；随机对照同步使用相同窗口长度。

输出：JSON 结果 + Markdown 报告
"""
import json
import sys
import os
import math
import random
import re
from datetime import date, timedelta
from typing import Dict, List, Tuple, Optional

import numpy as np
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

WS = "/Users/duguke/.openclaw/workspace"
sys.path.insert(0, WS)

# ── 生产实现（单一真相源）──
# 历史教训：脚本内曾自带一份 compute_fib_time_targets_local 复刻，
# 导致「回测验的东西 ≠ 生产跑的东西」。此处强制 import 生产实现。
from analysis.fib_extension_scan import compute_fib_time_targets  # noqa: E402


def compute_fib_time_targets_local(df, lookback=250, fib_seq=None, window=2, max_fib=144):
    """兼容薄封装：仅转发到生产实现（勿再复制算法本体）。

    保留此名称是为了不破坏外部调用点；实现必须唯一。
    """
    return compute_fib_time_targets(df, lookback=lookback, fib_seq=fib_seq,
                                    window=window, max_fib=max_fib)


# 关注池（35 只，含电力设备出海 8 只）
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
    # 电力设备/特高压出海
    "600089": "特变电工", "600406": "国电南瑞", "000400": "许继电气",
    "601179": "中国西电", "002028": "思源电气", "002270": "华明装备",
    "002130": "沃尔核材", "600312": "平高电气",
}


def fetch_all_baostock_history(symbols: List[str], start_date: str = "2020-01-01") -> Dict[str, pd.DataFrame]:
    """用 baostock 批量拉取前复权日线（adjustflag=2），单次登录复用会话"""
    import baostock as bs
    
    lg = bs.login()
    if lg.error_code != '0':
        print(f"  baostock login failed: {lg.error_msg}")
        return {}
    
    end_date = date.today().strftime("%Y-%m-%d")
    all_data = {}
    
    for sym in symbols:
        prefix = "sh." if sym.startswith("6") else "sz."
        bs_sym = prefix + sym
        
        rs = bs.query_history_k_data_plus(
            bs_sym,
            "date,open,high,low,close,volume,amount",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2"
        )
        
        if rs.error_code != '0':
            print(f"  {sym} query failed: {rs.error_msg}")
            continue
        
        data = []
        while rs.next():
            row = rs.get_row_data()
            data.append({
                "date": pd.Timestamp(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": int(float(row[5])) if row[5] else 0,
            })
        
        if data:
            df = pd.DataFrame(data).sort_values("date").reset_index(drop=True)
            all_data[sym] = df
            print(f"  {sym} OK ({len(df)} bars)")
        else:
            print(f"  {sym} NO DATA")
    
    bs.logout()
    return all_data


def detect_reversal(window_df: pd.DataFrame, lookback_before: pd.DataFrame) -> Tuple[bool, str]:
    """检测变盘：方向反转 / 趋势加速 / 结构破坏"""
    if len(window_df) == 0:
        return False, ""
    
    closes = window_df["close"].values
    highs = window_df["high"].values
    lows = window_df["low"].values
    volumes = window_df["volume"].values
    
    # 基准：窗口前 5 日 vs 窗口内 5 日
    if len(lookback_before) < 5 or len(window_df) < 3:
        return False, ""
    
    prev_5_high = lookback_before["high"].tail(5).max()
    prev_5_low = lookback_before["low"].tail(5).min()
    prev_5_avg_vol = lookback_before["volume"].tail(5).mean()
    
    cur_5_avg_vol = window_df["volume"].head(min(5, len(window_df))).mean()
    window_high = highs.max()
    window_low = lows.min()
    window_close = closes[-1]
    
    # a) 方向反转：窗口内出现单日涨跌幅 ≥3% 且随后 3 日未破该 K 线极值
    for i in range(len(closes)):
        if i == 0:
            pct_change = (closes[i] - lookback_before["close"].iloc[-1]) / lookback_before["close"].iloc[-1]
        else:
            pct_change = (closes[i] - closes[i-1]) / closes[i-1]
        
        if abs(pct_change) >= 0.03:
            # 检查后续 3 日是否破极值
            extreme = highs[i] if pct_change > 0 else lows[i]
            broken = False
            for j in range(i+1, min(i+4, len(closes))):
                if pct_change > 0 and highs[j] > extreme:
                    broken = True
                    break
                if pct_change < 0 and lows[j] < extreme:
                    broken = True
                    break
            if not broken:
                direction = "向上反转" if pct_change > 0 else "向下反转"
                return True, f"方向反转({direction})"
    
    # b) 趋势加速：窗口 5 日均量 > 前 5 日均量 ×1.5 且价格创近 20 日新高/新低
    if cur_5_avg_vol > prev_5_avg_vol * 1.5:
        # 近 20 日（含 lookback_before 尾部）
        combined = pd.concat([lookback_before.tail(20), window_df])
        if len(combined) >= 10:
            if window_close >= combined["high"].max() * 0.995:
                return True, "趋势加速(放量新高)"
            if window_close <= combined["low"].min() * 1.005:
                return True, "趋势加速(放量新低)"
    
    # c) 结构破坏：收盘价跌破/突破前 20 日支撑/压力
    if len(lookback_before) >= 20:
        support_20 = lookback_before["low"].tail(20).min()
        resist_20 = lookback_before["high"].tail(20).max()
        if window_close < support_20 * 0.98:
            return True, "结构破坏(跌破支撑)"
        if window_close > resist_20 * 1.02:
            return True, "结构破坏(突破压力)"
    
    return False, ""


def run_backtest():
    print("=" * 60)
    print("斐波那契时间线回测验证")
    print("=" * 60)
    
    # 回测参数
    START_DATE = "2024-01-01"
    END_DATE = "2026-09-11"
    LOOKBACK = 250
    WINDOW_DAYS = 2  # ±2 交易日
    
    # 先拉取所有股票的全量历史数据
    print("\n[1/3] 拉取全量历史数据 (baostock)...")
    all_data = fetch_all_baostock_history(list(WATCHLIST.keys()), START_DATE)
    # 过滤数据量不足的
    all_data = {k: v for k, v in all_data.items() if len(v) >= 500}
    print(f"\n有效标的: {len(all_data)}/{len(WATCHLIST)}")
    
    # 按交易日遍历
    print("\n[2/3] 逐日滚动回测...")
    
    # 取一个标的的交易日历作为基准
    ref_df = list(all_data.values())[0]
    trade_dates = ref_df[(ref_df["date"] >= START_DATE) & (ref_df["date"] <= END_DATE)]["date"].tolist()
    trade_dates = [d for d in trade_dates if d.weekday() < 5]  # 仅工作日
    
    # 只取有足够历史的日期（需 LOOKBACK 根）
    valid_dates = []
    for d in trade_dates:
        idx = ref_df[ref_df["date"] == d].index
        if len(idx) > 0 and idx[0] >= LOOKBACK:
            valid_dates.append(d)
    
    print(f"有效回测交易日: {len(valid_dates)} (从 {valid_dates[0].date()} 到 {valid_dates[-1].date()})")
    
    # 回测结果收集
    all_warnings = []      # 所有预警窗口
    all_hits = []          # 命中的预警
    random_controls = []   # 随机基准对照窗口（同股票/同基准日/同长度，随机偏移）
    random.seed(42)        # 固定种子，结果可复现
    
    # 进度
    for i, T in enumerate(valid_dates):
        if i % 50 == 0:
            print(f"  进度: {i}/{len(valid_dates)} ({T.date()})")
        
        T_str = T.strftime("%Y-%m-%d")
        
        for sym, df in all_data.items():
            # 截取 [T-250, T] 数据
            idx_T = df[df["date"] == T].index
            if len(idx_T) == 0:
                continue
            idx_T = idx_T[0]
            if idx_T < LOOKBACK:
                continue
            
            hist_df = df.iloc[idx_T - LOOKBACK + 1: idx_T + 1].reset_index(drop=True)
            
            # 计算时间线预警（生产实现）
            ti = compute_fib_time_targets(hist_df, lookback=LOOKBACK)
            if not ti or not ti.get("next_window"):
                continue
            
            nw = ti["next_window"]
            # 预警窗口日期范围
            win_start = pd.Timestamp(nw["window_start"])
            win_end = pd.Timestamp(nw["window_end"])
            
            # 只看未来窗口（相对 T 向后）
            if win_start <= T:
                continue
            
            # 记录预警
            warning = {
                "symbol": sym,
                "name": WATCHLIST[sym],
                "base_date": T_str,
                "window_start": nw["window_start"],
                "window_end": nw["window_end"],
                "hits": nw["hits"],
                "strength": nw["strength"],
                "sources": nw["sources"],
                "window_width_days": int((win_end - win_start).days),
            }
            all_warnings.append(warning)
            
            # 验证窗口 ±2 交易日内是否变盘
            # 找窗口在全数据中的索引范围
            win_start_idx = df[df["date"] >= win_start].index
            
            if len(win_start_idx) == 0:
                continue
            
            ws_idx = win_start_idx[0]
            # 观察窗口：[ws_idx - 2, ws_idx + 2 + (win_end - win_start).days]
            obs_start = max(0, ws_idx - WINDOW_DAYS)
            window_span = (win_end - win_start).days + 1
            obs_end = min(len(df), ws_idx + WINDOW_DAYS + window_span + 5)
            
            obs_window = df.iloc[obs_start:obs_end]
            before_window = df.iloc[max(0, obs_start - 20):obs_start]
            
            hit, reason = detect_reversal(obs_window, before_window)
            if hit:
                warning["hit"] = True
                warning["hit_reason"] = reason
                all_hits.append(warning)
            else:
                warning["hit"] = False

            # ── 随机基准对照（同股票/同基准日/同窗口长度，随机偏移 1~40 交易日）──
            # 目的：扣除 detect_reversal 判定器自身的自然命中率（~63%），
            # 只有「Fib − 随机」的差额才是时间线工具的真实信息量。
            if int((win_end - win_start).days) >= 0:
                off = random.randint(1, 40)
                r_pos = idx_T + off
                if r_pos + 5 < len(df):
                    span = (win_end - win_start).days + 1
                    r_obs_start = max(0, r_pos - WINDOW_DAYS)
                    r_obs_end = min(len(df), r_pos + WINDOW_DAYS + span + 5)
                    r_hit, r_reason = detect_reversal(
                        df.iloc[r_obs_start:r_obs_end],
                        df.iloc[max(0, r_obs_start - 20):r_obs_start])
                    random_controls.append({
                        "symbol": sym,
                        "name": WATCHLIST[sym],
                        "base_date": T_str,
                        "window_start": str(df["date"].iloc[r_pos].date()),
                        "window_end": str(df["date"].iloc[min(len(df) - 1, r_pos + (win_end - win_start).days)].date()),
                        "window_width_days": int((win_end - win_start).days),
                        "hit": bool(r_hit),
                        "hit_reason": r_reason,
                    })
    
    # 统计
    print("\n[3/3] 统计分析...")
    
    total_warnings = len(all_warnings)
    total_hits = len(all_hits)
    hit_rate = total_hits / total_warnings if total_warnings > 0 else 0
    
    # ── 随机基准统计 ──
    rnd_total = len(random_controls)
    rnd_hits = sum(1 for r in random_controls if r.get("hit"))
    rnd_rate = rnd_hits / rnd_total if rnd_total else 0.0

    # 超额 = Fib 命中率 − 随机命中率（pct 点）
    excess_pct = (hit_rate - rnd_rate) * 100 if rnd_total and total_warnings else 0.0
    # 超额 95% CI（两比例差的正态近似）
    if total_warnings and rnd_total:
        se = math.sqrt(hit_rate * (1 - hit_rate) / total_warnings
                       + rnd_rate * (1 - rnd_rate) / rnd_total)
        ci_lo = (hit_rate - rnd_rate) - 1.96 * se
        ci_hi = (hit_rate - rnd_rate) + 1.96 * se
    else:
        ci_lo = ci_hi = 0.0
    # 显著性：95% CI 下界 > 0 才算显著高于随机
    significant = ci_lo > 0

    print(f"\n总预警窗口: {total_warnings}")
    print(f"命中窗口: {total_hits}")
    print(f"命中率: {hit_rate:.2%}")
    print(f"随机基准: {rnd_hits}/{rnd_total} = {rnd_rate:.2%}")
    print(f"超额(Fib−随机): {excess_pct:+.2f} pct  95%CI[{ci_lo*100:+.2f}, {ci_hi*100:+.2f}] "
          f"→ {'显著高于随机' if significant else '与随机无显著差异'}")
    
    # 按强度分层
    for strength in ["high", "medium"]:
        sw = [w for w in all_warnings if w["strength"] == strength]
        sh = [w for w in all_hits if w["strength"] == strength]
        sr = len(sh) / len(sw) if sw else 0
        print(f"  {strength}: {len(sh)}/{len(sw)} = {sr:.2%}")
    
    # 按 fib 序列分层（取 sources 中的 fib 数字）
    print("\n按 fib 序列分层:")
    fib_stats = {}
    for w in all_warnings:
        for src in w["sources"]:
            # 提取数字
            nums = re.findall(r'@(\d+)', src)
            for n in nums:
                fib_stats.setdefault(int(n), {"warn": 0, "hit": 0})
                fib_stats[int(n)]["warn"] += 1
    for w in all_hits:
        for src in w["sources"]:
            nums = re.findall(r'@(\d+)', src)
            for n in nums:
                if int(n) in fib_stats:
                    fib_stats[int(n)]["hit"] += 1
    
    for fib in sorted(fib_stats.keys()):
        s = fib_stats[fib]
        r = s["hit"] / s["warn"] if s["warn"] > 0 else 0
        print(f"  fib{fib}: {s['hit']}/{s['warn']} = {r:.2%}")
    
    # ── 按窗口宽度分层（宽度是命中率的主要混杂因素）──
    def _width_bucket(width_days):
        return "0-2d" if width_days <= 2 else ("3-5d" if width_days <= 5 else "6d+")

    width_stats = {}
    for w in all_warnings:
        b = _width_bucket(w.get("window_width_days", 0))
        width_stats.setdefault(b, {"warn": 0, "hit": 0})
        width_stats[b]["warn"] += 1
        if w.get("hit"):
            width_stats[b]["hit"] += 1
    rnd_width_stats = {}
    for r in random_controls:
        b = _width_bucket(r.get("window_width_days", 0))
        rnd_width_stats.setdefault(b, {"warn": 0, "hit": 0})
        rnd_width_stats[b]["warn"] += 1
        if r.get("hit"):
            rnd_width_stats[b]["hit"] += 1

    print("\n按窗口宽度分层 (Fib vs 随机):")
    for b in ("0-2d", "3-5d", "6d+"):
        f = width_stats.get(b, {"warn": 0, "hit": 0})
        r = rnd_width_stats.get(b, {"warn": 0, "hit": 0})
        fr = f["hit"] / f["warn"] if f["warn"] else 0
        rr = r["hit"] / r["warn"] if r["warn"] else 0
        print(f"  {b}: Fib {f['hit']}/{f['warn']}={fr:.1%}  |  随机 {r['hit']}/{r['warn']}={rr:.1%}"
              f"  |  超额 {(fr-rr)*100:+.1f} pct")
    
    # 保存结果
    result = {
        "params": {
            "start_date": START_DATE,
            "end_date": END_DATE,
            "lookback": LOOKBACK,
            "window_days": WINDOW_DAYS,
            "symbols": len(all_data),
            "trade_days": len(valid_dates),
        },
        "summary": {
            "total_warnings": total_warnings,
            "total_hits": total_hits,
            "hit_rate": hit_rate,
            "random_baseline": {
                "total": rnd_total,
                "hits": rnd_hits,
                "hit_rate": rnd_rate,
            },
            "excess_pct": excess_pct,
            "excess_ci95": [ci_lo * 100, ci_hi * 100],
            "significant_vs_random": bool(significant),
            "by_window_width": {
                b: {
                    "warnings": v["warn"], "hits": v["hit"],
                    "rate": v["hit"] / v["warn"] if v["warn"] else 0,
                    "random_rate": (rnd_width_stats.get(b, {}).get("hit", 0)
                                    / rnd_width_stats[b]["warn"]) if rnd_width_stats.get(b, {}).get("warn") else 0,
                } for b, v in width_stats.items()
            },
            "by_strength": {
                s: {
                    "warnings": len([w for w in all_warnings if w["strength"] == s]),
                    "hits": len([w for w in all_hits if w["strength"] == s]),
                    "rate": len([w for w in all_hits if w["strength"] == s]) / max(1, len([w for w in all_warnings if w["strength"] == s]))
                } for s in ["high", "medium"]
            },
            "by_fib": {str(k): {"warn": v["warn"], "hit": v["hit"], "rate": v["hit"]/v["warn"] if v["warn"]>0 else 0} 
                       for k, v in sorted(fib_stats.items())},
        },
        "warnings": all_warnings,
        "hits": all_hits,
        "random_controls": random_controls,
    }
    
    today = date.today().strftime("%Y-%m-%d")
    out_json = f"{WS}/data/fib_time_backtest_{today}.json"
    # 明细体积控制：warnings/hits/random_controls 各 1.3 万条、13MB，不适合入库。
    # 保留 summary（含全部统计口径）+ 前 200 条样例；全量明细走 --full 另存。
    SAMPLE_N = 200
    if "--full" in sys.argv:
        payload = result
    else:
        payload = dict(result)
        payload["warnings"] = result["warnings"][:SAMPLE_N]
        payload["hits"] = result["hits"][:SAMPLE_N]
        payload["random_controls"] = result["random_controls"][:SAMPLE_N]
        payload["detail_truncated"] = {
            "full_counts": {"warnings": total_warnings, "hits": total_hits,
                            "random_controls": len(random_controls)},
            "sample_n": SAMPLE_N,
            "note": "明细已裁剪；加 --full 可导出全量",
        }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✅ 结果已保存: {out_json}")
    
    # 生成 Markdown 报告
    out_md = f"{WS}/analysis/fib_time_backtest_{today}.md"
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(f"# 斐波那契时间线回测验证报告\n\n")
        f.write(f"**生成日期**: {today}\n")
        f.write(f"**回测区间**: {START_DATE} ~ {END_DATE}\n")
        f.write(f"**回看窗口**: {LOOKBACK} 交易日\n")
        f.write(f"**汇聚容差**: ±{WINDOW_DAYS} 交易日\n")
        f.write(f"**有效标的**: {len(all_data)} 只\n")
        f.write(f"**回测交易日**: {len(valid_dates)} 天\n\n")
        
        f.write(f"## 核心指标\n\n")
        f.write(f"| 指标 | 数值 |\n|------|------|\n")
        f.write(f"| 总预警窗口 | {total_warnings} |\n")
        f.write(f"| 命中窗口 | {total_hits} |\n")
        f.write(f"| **命中率** | **{hit_rate:.2%}** |\n")
        f.write(f"| 随机基准命中率 | {rnd_rate:.2%} ({rnd_hits}/{rnd_total}) |\n")
        f.write(f"| **超额（Fib − 随机）** | **{excess_pct:+.2f} pct** |\n")
        f.write(f"| 超额 95% CI | [{ci_lo*100:+.2f}, {ci_hi*100:+.2f}] pct |\n")
        f.write(f"| 是否显著高于随机 | {'✅ 是' if significant else '❌ 否'} |\n\n")

        warn_note = ("命中率数值本身不是有效性证据：判定器 detect_reversal 结构破坏/趋势加速"
                     "判据在 20+ 交易日窗口上本就有约 60-63% 的自然命中率（见下方随机基准行）。"
                     "有效性只能由「超额 = Fib − 随机」判断。")
        f.write(f"> ⚠️ {warn_note}\n\n")
        
        f.write(f"### 按强度分层\n\n")
        f.write(f"| 强度 | 预警数 | 命中数 | 命中率 |\n|------|--------|--------|--------|\n")
        for s in ["high", "medium"]:
            sw = len([w for w in all_warnings if w["strength"] == s])
            sh = len([w for w in all_hits if w["strength"] == s])
            f.write(f"| {s} | {sw} | {sh} | {sh/sw:.2%} |\n")
        
        f.write(f"\n### 按窗口宽度分层（与随机基准对照）\n\n")
        f.write(f"| 窗口宽度 | 预警数 | 命中数 | Fib命中率 | 随机命中率 | 超额 |\n")
        f.write(f"|---------|--------|--------|-----------|-----------|------|\n")
        for b in ("0-2d", "3-5d", "6d+"):
            fs = width_stats.get(b, {"warn": 0, "hit": 0})
            rs = rnd_width_stats.get(b, {"warn": 0, "hit": 0})
            fr = fs["hit"] / fs["warn"] if fs["warn"] else 0
            rr = rs["hit"] / rs["warn"] if rs["warn"] else 0
            f.write(f"| {b} | {fs['warn']} | {fs['hit']} | {fr:.2%} | {rr:.2%} | {(fr-rr)*100:+.1f} pct |\n")

        f.write(f"\n### 按 Fib 序列分层\n\n")
        f.write(f"| Fib | 预警数 | 命中数 | 命中率 |\n|-----|--------|--------|--------|\n")
        for fib in sorted(fib_stats.keys()):
            s = fib_stats[fib]
            f.write(f"| {fib} | {s['warn']} | {s['hit']} | {s['hit']/s['warn']:.2%} |\n")
        
        f.write(f"\n## 判定\n\n")
        # 判定核心：是否显著高于随机基准（单一 40% 阈值已被证伪）
        if significant and excess_pct >= 15:
            f.write(f"✅ **通过**：命中率 {hit_rate:.2%}，超额 {excess_pct:+.2f} pct "
                    f"(95%CI 下界 {ci_lo*100:+.2f}) 显著且 ≥15pct，可进 P2 消费接入\n")
        elif significant:
            f.write(f"⚠️ **观察**：超额 {excess_pct:+.2f} pct (95%CI 下界 {ci_lo*100:+.2f}) "
                    f"虽显著但 <15pct，仅作报告辅助，不进决策\n")
        else:
            f.write(f"❌ **弃用**：Fib 命中率 {hit_rate:.2%} 与随机基准 {rnd_rate:.2%} "
                    f"无显著差异或更差（超额 {excess_pct:+.2f} pct，"
                    f"95%CI [{ci_lo*100:+.2f}, {ci_hi*100:+.2f}]）。\n")
            f.write(f"   该工具不提供超出随机选日的预测信息量，归档为无效工具，不得进入决策链。\n")
        f.write(f"\n> 判定口径已于 2026-09-15 修订：原「命中率 ≥ 40% → 通过」标准无效，"
                f"因判定器本身自然命中率约 60-63%。现以「显著高于随机基准」为准。\n")

        f.write(f"\n## 随机基准对照（同股票/同基准日/同窗口长度，随机偏移 1~40 交易日）\n\n")
        f.write(f"| 组别 | 窗口数 | 命中数 | 命中率 |\n|------|--------|--------|--------|\n")
        f.write(f"| Fib 时间窗口 | {total_warnings} | {total_hits} | {hit_rate:.2%} |\n")
        f.write(f"| 随机窗口（对照） | {rnd_total} | {rnd_hits} | {rnd_rate:.2%} |\n")
        f.write(f"| **差值** | | | **{excess_pct:+.2f} pct** |\n")
        
        f.write(f"\n## 明细（前 20 条预警）\n\n")
        for w in all_warnings[:20]:
            hit_mark = "✅" if w.get("hit") else "❌"
            f.write(f"- {hit_mark} {w['name']}({w['symbol']}) 基线{w['base_date']} "
                    f"窗口{w['window_start']}~{w['window_end']} "
                    f"{w['strength']}({w['hits']}源) ")
            if w.get("hit"):
                f.write(f"命中:{w.get('hit_reason')}")
            f.write("\n")
    
    print(f"📄 报告已保存: {out_md}")
    return result


if __name__ == "__main__":
    run_backtest()
