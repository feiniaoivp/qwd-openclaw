#!/usr/bin/env python3
"""chip 缓存新鲜度判定（纯逻辑，零网络；2026-10-18）。

为什么需要
----------
chip 第 5 维（2026-10-18 复活）消费 `data/research_price_cache/chipmetrics_{sym}.pkl`，
而两个读写点都是**无条件命中**：
  * `scripts/chip_distribution_study.py::fetch_chip_input()` — `if exists: return`（不校验日期）
  * `scripts/chip_distribution_study.py::main()`            — `chipmetrics_*.pkl` 存在即读，不重算
  * `analysis/trader_stock_picks.py::load_chip_metrics()`   — 只取 `df.iloc[-1]`，不看它有多旧

且 cron 里**没有任何 chip 刷新任务**。一旦生产跑起来，chipmetrics 会冻结在最后一次
生成的日期，`cost_bias = close / avg_cost - 1` 会拿"T 日的新价"减"T0 日的旧成本"：

  价格上行 → cost_bias 被系统性推高 → −0.20 深度折价档永不触发 → 加分项静默失效
  价格下行 → cost_bias 被系统性压低 → 批量误判为"深度折价"→ 加分项错误触发

这是典型的静默失效：不报错、不告警、分数看起来正常，但维度已经死了。
因此本模块把"新鲜度"变成**可判定、可测试、可门禁**的东西。

口径
----
新鲜度基准 = `data/research_price_cache/{sym}.pkl` 的最新日期（价格缓存本身就是
close_scan / 研究脚本写的那份），而不是 `date.today()`——非交易日/长假日不该判旧。
价格缓存也没有时，退化为与"其他票的最新日期"比（跨票基准），再没有才用今天。

阈值
----
* `MAX_LAG_DAYS = 5` 个**交易日**（约一周）。A 股正常一周 5 个交易日，周流水线每周刷一次，
  超过 5 个交易日说明至少漏了一轮刷新。
  【2026-10-19 证据化】MAX_LAG 是**刷新节奏参数**而非统计量：实证（
  `analysis/research/2026-10-19_chip_freshness_threshold_evidence.md`，36 只全历史
  frozen=cost_bias(T-L) vs fresh=cost_bias(T)）确认 L=5 处失真有界
  （误加分 2.08%/漏加分 2.50%/|bias| 0.11pp），节奏可接受，维持 5。
* 超过 `STALE_CRITICAL_DAYS = 12` 个交易日 → 视为“维度不可信”，生产侧该维返回 None
  （宁缺毋滥：与 2026-10-09「缺失不填充」纪律一致，且比用错数据打分更安全）。
  【2026-10-19 证据化，10→12】误加分主风险向是**上行行情**（冻结值比新值更负 → 虚报
  折价）：实测上行 p_false 在 L=13 首次越过 5%（5.20%），取“最坏 regime p_false≤5%”
  的最大 L=12；同时为春节等长假+漏刷一周场景保留裕度（曲线平缓无悬崖，详见证报告）。

本模块只做判定，不做重算（重算需要 baostock，放在 chip_distribution_study 里）。
"""
import os
import logging

log = logging.getLogger("chip_freshness")

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")

PRICE_CACHE_DIR = os.path.join(WORKSPACE, "data", "research_price_cache")

# 允许滞后的交易日数
MAX_LAG_DAYS = int(os.getenv("CHIP_MAX_LAG_DAYS", "5"))
# 超过此滞后 → 维度判定为不可信（返回 None 而不是旧分）
# 2026-10-19 证据化 10→12：最坏 regime(上行) p_false≤5% 的最大 L（见
# analysis/research/2026-10-19_chip_freshness_threshold_evidence.md）
STALE_CRITICAL_DAYS = int(os.getenv("CHIP_STALE_CRITICAL_DAYS", "12"))


def _norm_date(v):
    """把各种日期表示统一成 'YYYY-MM-DD' 字符串，失败返回 None。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "NaT" or s.startswith("1970"):
        return None
    return s[:10]


def frame_last_date(df):
    """取 DataFrame 的最后一根日期（兼容 date 列存在/为索引两种情况）。"""
    if df is None:
        return None
    try:
        if hasattr(df, "columns") and "date" in df.columns:
            if len(df) == 0:
                return None
            col = df["date"]
            try:
                return _norm_date(max(col.dropna().astype(str)))
            except Exception as e:
                log.warning("frame_last_date 列聚合失败，退回尾行: %s", e)
                return _norm_date(col.iloc[-1])
        if hasattr(df, "index") and len(df) > 0:
            return _norm_date(df.index[-1])
    except Exception as e:
        log.warning("frame_last_date 失败: %s", e)
        return None
    return None


def price_ref_date(symbol, cache_dir=None):
    """该票价格缓存的最新日期；没有则返回 None。"""
    cache_dir = cache_dir or PRICE_CACHE_DIR
    path = os.path.join(cache_dir, f"{symbol}.pkl")
    if not os.path.exists(path):
        return None
    try:
        import pandas as pd
        return frame_last_date(pd.read_pickle(path))
    except Exception as e:
        log.warning("price_ref_date(%s) 读取失败: %s", symbol, e)
        return None


def trading_index():
    """交易日序号映射（2026-10-22 第五轮起改走 service.get_trading_days SSOT 序列接口）。

    此前本函数自调 ak.tool_trade_date_hist_sina()，属 SSOT 之外的第二份数据获取路径
    （被 trading_day_ssot_guard 门禁命中后收敛）。返回 dict{date_str: int} 或 None
    （日历不可得时由调用方退化为自然日近似）。
    """
    try:
        from analysis.service import get_trading_days
        days = get_trading_days(min_date="2024-01-01")
        if not days:
            log.warning("trading_index 交易日历获取失败，退化为自然日近似")
            return None
        return {d: i for i, d in enumerate(days)}
    except Exception as e:
        log.warning("trading_index 交易日历获取失败，退化为自然日近似: %s", e)
        return None


def lag_trading_days(older, newer, index=None):
    """older 相对 newer 落后多少个交易日（都在 index 里时用交易日；否则自然日近似）。"""
    if not older or not newer:
        return None
    idx = index if index is not None else trading_index()
    if idx and older in idx and newer in idx:
        return idx[newer] - idx[older]
    # 退化：自然日 × 5/7（近似交易日）
    try:
        from datetime import date
        d1 = date.fromisoformat(older)
        d2 = date.fromisoformat(newer)
        return int(round(max(0, (d2 - d1).days) * 5 / 7))
    except Exception as e:
        log.warning("lag_trading_days 日期解析失败: %s", e)
        return None


def check(symbol, chip_last_date, ref_date=None, index=None, cache_dir=None):
    """判定一只票的 chip 数据新鲜度。

    返回 dict:
      ok            bool    是否可放心使用（lag <= MAX_LAG_DAYS）
      trustworthy   bool    是否未达"不可信"（lag <= STALE_CRITICAL_DAYS）
      lag           int|None 滞后交易日数
      chip_last     str|None chip 数据最后日期
      ref_last      str|None 基准（价格缓存）最后日期
      reason        str     人读说明
    """
    chip_last = _norm_date(chip_last_date)
    ref = _norm_date(ref_date) or price_ref_date(symbol, cache_dir=cache_dir)
    if not chip_last:
        return {"ok": False, "trustworthy": False, "lag": None, "chip_last": None,
                "ref_last": ref, "reason": "chip 数据无日期"}
    if not ref:
        # 没有基准：只能判定"存在"，保守放行但标注
        return {"ok": True, "trustworthy": True, "lag": None, "chip_last": chip_last,
                "ref_last": None, "reason": "无价格缓存基准，无法判定陈旧度"}
    lag = lag_trading_days(chip_last, ref, index=index)
    if lag is None:
        return {"ok": True, "trustworthy": True, "lag": None, "chip_last": chip_last,
                "ref_last": ref, "reason": "日期解析失败，无法判定陈旧度"}
    if lag < 0:
        # chip 比价格缓存还新：不是错误（研究/刷新可单独跑），但如果差得远，
        # 说明价格缓存本身落后，cost_bias 会拿旧价减新成本 → 同样失真。
        if abs(lag) > MAX_LAG_DAYS:
            return {"ok": False, "trustworthy": False, "lag": lag, "chip_last": chip_last,
                    "ref_last": ref,
                    "reason": (f"chip({chip_last}) 比价格缓存({ref}) 新 {-lag} 交易日"
                               f"→ 价格缓存落后，cost_bias 口径失真")}
        return {"ok": True, "trustworthy": True, "lag": lag, "chip_last": chip_last,
                "ref_last": ref, "reason": f"chip 略新于价格缓存（{-lag} 交易日），可接受"}
    if lag <= MAX_LAG_DAYS:
        return {"ok": True, "trustworthy": True, "lag": lag, "chip_last": chip_last,
                "ref_last": ref, "reason": f"新鲜（滞后 {lag} 交易日）"}
    trustworthy = lag <= STALE_CRITICAL_DAYS
    return {"ok": False, "trustworthy": trustworthy, "lag": lag, "chip_last": chip_last,
            "ref_last": ref,
            "reason": (f"陈旧：chip {chip_last} 落后价格 {ref} {lag} 交易日"
                       f"（阈值 {MAX_LAG_DAYS}）" + ("" if trustworthy else "，超过不可信阈值"))}
