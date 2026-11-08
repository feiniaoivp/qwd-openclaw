#!/usr/bin/env python3
"""
情绪反转首日识别模块 (Regime-Switch P0, 2026-11-01 落地)
=========================================================
源自 2026-10-28 复盘教训（.learnings/ERRORS.md 同源）：
  竞价 fade_risk 名单（高开>=2% 未涨停的高开回落风险票）若「全天收阳 + 放量」，
  且市场出现跷跷板式验证（宽度/指数该弱不弱），则判定为「情绪反转首日」——
  技术性卖出信号在反转首日天然滞后，应被暂时覆盖，仅保留硬止损。

规则口径（v1）：
  - 单票确认: close > open 且 close > prev_close（高开高走）且 vol >= 1.2 x 前5日均量
  - 候选集: data/runtime/auction/signal_push_{date}.json 的 fade_risk 名单 ∪ 「卖出信号遇高开(诱多)」hits
  - 跷跷板（任一成立即算，缺数据按 None 传播，绝不静默填默认）:
      a) 宽度: legu 口径 up_ratio(D) - up_ratio(D-1) >= +0.10 且 up_ratio(D) >= 0.45
      b) 指数: 沪深300 D 日收涨 且 D-1 日收跌（该弱不弱）
  - 日级反转确认: seesaw 为真 且 (>=2 只 fade 名单票单票确认 或 fade 名单为空时 >=1 只诱多票确认)
  - 覆盖效力: 仅对 confirmed_codes 内个股生效，有效期 = 文件日的下一交易日（仅一日），
    由 signal_arbitrator 在技术性卖出路径上消费（结构性风险路径不覆盖）。

数据源: baostock 前复权日线（授权源），legu 宽度史 data/market_activity_legu_history.json
产出:   data/emotion_reversal/reversal_{date}.json + analysis/daily/{date}_reversal.md
"""

import os
import sys
import json
import glob
import shutil
import time
import logging
from datetime import datetime, timedelta

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

log = logging.getLogger("emotion_reversal")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DATA_DIR = os.path.join(WORKSPACE, "data")
OUT_DIR = os.path.join(DATA_DIR, "emotion_reversal")
DAILY_DIR = os.path.join(WORKSPACE, "analysis", "daily")
AUCTION_DIR = os.path.join(DATA_DIR, "auction")
LEGU_HIST = os.path.join(DATA_DIR, "market_activity_legu_history.json")

# ── 可调参数（集中声明，避免魔法数散落）──
# v1.1 口径校准（2026-11-01，10-28 历史实证）：反转首日个股量能并不同步
# （通富 1.36x/长电 1.16x/国瓷 0.89x vs 昨日），量能证据主要在市场宽度
# （up_ratio 0.11→0.742，跳变 +0.63）。故个股量能三选一：
#   a) 对前5日均量放量  b) 对昨日放量  c) 宽度大跳变豁免（市场级参与度即量能证据）
VOL_SURGE_MULT = 1.2        # a) 当日量 >= 1.2 x 前5日均量
VOL_SURGE_PREV_MULT = 1.15  # b) 当日量 >= 1.15 x 昨日量
BREADTH_WAIVE_JUMP = 0.20   # c) up_ratio 环比跳变 >= 0.20 时豁免个股量能要求
BREADTH_JUMP = 0.10         # 跷跷板宽度改善阈值: up_ratio 环比提升
BREADTH_FLOOR = 0.45        # 宽度绝对水位下限（低位改善才算反转而非高位加速）
MIN_FADE_CONFIRM = 2        # fade 名单中最少确认票数
INDEX_CODE_BS = "sh.000300"   # 沪深300（baostock 代码）
HARD_STOP_PCT = 5.0       # 覆盖生效期间保留的硬止损（-5%，对齐 030 高风险防御口径）
VALIDITY_DAYS = 1         # 覆盖仅对文件日的次一交易日生效


def _bak(path: str):
    """写前 .bak 备份（backup_guard 铁律；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
    except Exception as e:  # noqa: BLE001
        log.warning("写前备份失败(不阻断) %s: %s", path, e)


# ══════════════════════════════════════
# 数据获取（baostock 授权源）
# ══════════════════════════════════════

def _to_bs_code(code: str) -> str:
    """6 位代码 -> baostock 代码（6/9 开头沪市, 0/3 开头深市, 4/8 北交所不处理）"""
    code = str(code).strip()
    if code.startswith(("6", "9")):
        return f"sh.{code}"
    if code.startswith(("0", "3")):
        return f"sz.{code}"
    return f"sh.{code}"


def fetch_daily_k(code: str, end_date: str, lookback_days: int = 30, max_retry: int = 3, bs_code: str = None):
    """拉前复权日线（date, open, close, volume），按日期升序；失败返回 None（不缓存空值）
    bs_code: 直接指定 baostock 代码（指数用，绕过个股前缀路由）"""
    import baostock as bs
    import pandas as pd

    start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    bs_code = bs_code or _to_bs_code(code)
    last_err = None
    for attempt in range(1, max_retry + 1):
        try:
            lg = bs.login()
            if lg.error_code != "0":
                raise ConnectionError(f"baostock 登录失败: {lg.error_msg}")
            try:
                rs = bs.query_history_k_data_plus(
                    bs_code, "date,open,close,volume",
                    start_date=start, end_date=end_date,
                    frequency="d", adjustflag="2")
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
            finally:
                bs.logout()
            if not rows:
                raise ValueError(f"{bs_code} 返回空序列")
            df = pd.DataFrame(rows, columns=["date", "open", "close", "volume"])
            for c in ("open", "close", "volume"):
                # 数据源容错: 空字符串/None 一律 coerce（2026-10-08 教训）
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna().sort_values("date").reset_index(drop=True)
            if len(df) < 7:
                raise ValueError(f"{bs_code} 样本不足: {len(df)} 行")
            return df
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("K线获取失败(尝试 %s/%s) %s: %s", attempt, max_retry, bs_code, e)
            time.sleep(1)
    log.error("K线获取最终失败 %s: %s", bs_code, last_err)
    return None


def fetch_index_bars(end_date: str, lookback_days: int = 15):
    """沪深300 最近日线（判定该弱不弱）；失败返回 None
    [2026-11-01 修复] 指数代码必须直用 sh.000300——0 前缀走个股路由会误判为 sz.（10-28 实证空序列）"""
    return fetch_daily_k("000300", end_date, lookback_days=lookback_days, bs_code=INDEX_CODE_BS)


# ══════════════════════════════════════
# 竞价名单加载
# ══════════════════════════════════════

def load_fade_candidates(date: str) -> dict:
    """从 signal_push_{date}.json 取 fade_risk 名单 + 诱多 hits，合并为候选集"""
    path = os.path.join(AUCTION_DIR, f"signal_push_{date}.json")
    if not os.path.exists(path):
        return {"fade": [], "trap_hits": [], "missing": True}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        log.warning("竞价交集档案解析失败 %s: %s", path, e)
        return {"fade": [], "trap_hits": [], "missing": True}

    fade = data.get("fade_risk") or []
    trap_hits = [h for h in (data.get("hits") or []) if "诱多" in (h.get("tag") or "")]
    return {"fade": fade, "trap_hits": trap_hits, "missing": False}


def load_legu_breadth(date: str):
    """legu 宽度史: 返回 (up_ratio_D, up_ratio_prev) 或 (None, None)——缺口显式传播"""
    try:
        with open(LEGU_HIST, encoding="utf-8") as f:
            hist = json.load(f)
    except Exception as e:  # noqa: BLE001
        log.warning("legu 宽度史读取失败: %s", e)
        return None, None
    today_v = hist.get(date)
    if not isinstance(today_v, dict):
        return None, None
    up_today = today_v.get("up_ratio")
    if up_today is None:
        total = (today_v.get("up") or 0) + (today_v.get("down") or 0) + (today_v.get("flat") or 0)
        up_today = (today_v.get("up") or 0) / total if total > 0 else None
    prev_dates = sorted(k for k in hist.keys() if k < date)
    up_prev = None
    if prev_dates:
        pv = hist.get(prev_dates[-1])
        if isinstance(pv, dict):
            up_prev = pv.get("up_ratio")
            if up_prev is None:
                total_p = (pv.get("up") or 0) + (pv.get("down") or 0) + (pv.get("flat") or 0)
                up_prev = (pv.get("up") or 0) / total_p if total_p > 0 else None
    return up_today, up_prev


# ══════════════════════════════════════
# 纯逻辑评估（可对抗自测，无 IO 无网络）
# ══════════════════════════════════════

def evaluate(candidates: list, k_by_code: dict, breadth: dict, index_bars) -> dict:
    """
    candidates: [{"code","name","kind":"fade"|"trap","open_chg_pct"}, ...]
    k_by_code:  code -> {"open","close","prev_close","vol","vol_ma5_prev"} 或 None(数据缺口)
    breadth:    {"up_ratio_today": x|None, "up_ratio_prev": y|None}
    index_bars: DataFrame(date,open,close,...) 或 None
    """
    # 跷跷板维度先算（个股量能豁免需要宽度跳变，缺口显式标注，fail-closed）
    br_t, br_p = breadth.get("up_ratio_today"), breadth.get("up_ratio_prev")
    breadth_ok = None
    breadth_jump = None
    if br_t is not None and br_p is not None:
        breadth_jump = br_t - br_p
        breadth_ok = breadth_jump >= BREADTH_JUMP and br_t >= BREADTH_FLOOR
    breadth_gap = breadth_ok is None

    per_stock = []
    for c in candidates:
        code = c["code"]
        k = k_by_code.get(code)
        if not k:
            per_stock.append({**c, "confirmed": False, "data_gap": True,
                              "note": "K线数据缺口，无法确认"})
            continue
        bullish = (k["close"] > k["open"]) and (k["close"] > k["prev_close"])
        vol_ma5 = k["vol_ma5_prev"]
        vol_prev = k.get("vol_prev")
        surge_ma5 = bool(vol_ma5 and vol_ma5 > 0 and k["vol"] >= VOL_SURGE_MULT * vol_ma5)
        surge_prev = bool(vol_prev and vol_prev > 0 and k["vol"] >= VOL_SURGE_PREV_MULT * vol_prev)
        # v1.1: 宽度大跳变日豁免个股量能（市场级参与度即量能证据，10-28 校准）
        waive = bool(breadth_jump is not None and breadth_jump >= BREADTH_WAIVE_JUMP)
        vol_ok = surge_ma5 or surge_prev or waive
        vol_ratio_prev = round(k["vol"] / vol_prev, 2) if vol_prev else None
        per_stock.append({**c, "confirmed": bool(bullish and vol_ok),
                          "data_gap": False,
                          "bullish_close": bullish,
                          "vol_surge": bool(surge_ma5 or surge_prev),
                          "vol_waived_by_breadth": bool(waive and bullish and not (surge_ma5 or surge_prev)),
                          "vol_multiple_ma5": round(k["vol"] / vol_ma5, 2) if vol_ma5 else None,
                          "vol_ratio_prev": vol_ratio_prev})

    idx_rev = None
    idx_gap = True
    if index_bars is not None and len(index_bars) >= 3:
        idx_gap = False
        closes = list(index_bars["close"])
        chg_d = closes[-1] - closes[-2]
        chg_prev = closes[-2] - closes[-3]
        idx_rev = (chg_d > 0) and (chg_prev < 0)

    seesaw_parts = {}
    if not breadth_gap:
        seesaw_parts["breadth"] = breadth_ok
    if not idx_gap:
        seesaw_parts["index_reversal"] = idx_rev
    seesaw = any(v for v in seesaw_parts.values() if v is not None) if seesaw_parts else False
    seesaw_gap = breadth_gap and idx_gap

    fade_confirmed = [p for p in per_stock if p["kind"] == "fade" and p["confirmed"]]
    trap_confirmed = [p for p in per_stock if p["kind"] == "trap" and p["confirmed"]]
    n_fade = sum(1 for p in per_stock if p["kind"] == "fade")

    if n_fade > 0:
        day_confirmed = seesaw and len(fade_confirmed) >= MIN_FADE_CONFIRM
    else:
        day_confirmed = seesaw and len(trap_confirmed) >= 1

    return {
        "per_stock": per_stock,
        "seesaw": bool(seesaw),
        "seesaw_parts": seesaw_parts,
        "seesaw_data_gap": seesaw_gap,
        "breadth_gap": breadth_gap,
        "index_gap": idx_gap,
        "confirmed_codes": [p["code"] for p in per_stock if p["confirmed"]],
        "reversal_confirmed": bool(day_confirmed),
    }


# ══════════════════════════════════════
# 主流程（IO 编排）
# ══════════════════════════════════════

def detect(date: str = None, push: bool = False) -> dict:
    from analysis.service import is_trading_day
    date = date or datetime.now().strftime("%Y-%m-%d")
    if not is_trading_day(date):
        log.info("%s 非交易日，跳过反转检测", date)
        return {"date": date, "reversal_confirmed": False, "skipped": "非交易日"}

    cand = load_fade_candidates(date)
    candidates = []
    for fd in cand["fade"]:
        candidates.append({"code": fd.get("code", ""), "name": fd.get("name", ""),
                           "kind": "fade", "open_chg_pct": fd.get("open_chg_pct")})
    for h in cand["trap_hits"]:
        if h.get("code") and h["code"] not in {c["code"] for c in candidates}:
            candidates.append({"code": h["code"], "name": h.get("name", ""),
                               "kind": "trap", "open_chg_pct": h.get("open_chg_pct")})

    result = {
        "date": date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rule_version": "v1.1 (2026-11-01, 10-28 量能三tier校准)",
        "candidates": candidates,
        "auction_file_missing": cand["missing"],
    }

    if not candidates:
        result.update({
            "reversal_confirmed": False,
            "note": "当日无 fade_risk 名单且无诱多票（无高开回落候选），无需反转判断",
            "per_stock": [], "confirmed_codes": [],
            "seesaw": None, "seesaw_parts": {},
        })
    else:
        k_by_code = {}
        for c in candidates:
            df = fetch_daily_k(c["code"], date)
            if df is None or len(df) < 7:
                k_by_code[c["code"]] = None
                continue
            tail = df.tail(7)
            k_by_code[c["code"]] = {
                "open": float(tail["open"].iloc[-1]),
                "close": float(tail["close"].iloc[-1]),
                "prev_close": float(tail["close"].iloc[-2]),
                "vol": float(tail["volume"].iloc[-1]),
                "vol_prev": float(tail["volume"].iloc[-2]),
                "vol_ma5_prev": float(tail["volume"].iloc[-6:-1].mean()),
            }
        br_t, br_p = load_legu_breadth(date)
        index_bars = fetch_index_bars(date)
        result.update(evaluate(candidates, k_by_code,
                               {"up_ratio_today": br_t, "up_ratio_prev": br_p},
                               index_bars))
        result["breadth"] = {"up_ratio_today": br_t, "up_ratio_prev": br_p}
        if index_bars is not None and len(index_bars) >= 3:
            result["index_close"] = round(float(index_bars["close"].iloc[-1]), 2)
            result["index_prev_close"] = round(float(index_bars["close"].iloc[-2]), 2)
        result["hard_stop_pct"] = HARD_STOP_PCT

    _write_outputs(date, result)
    if push:
        _push_telegram(result)
    return result


def _write_outputs(date: str, result: dict):
    os.makedirs(OUT_DIR, exist_ok=True)
    jpath = os.path.join(OUT_DIR, f"reversal_{date}.json")
    _bak(jpath)
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, default=str)
    mpath = os.path.join(DAILY_DIR, f"{date}_reversal.md")
    _bak(mpath)
    with open(mpath, "w", encoding="utf-8") as f:
        f.write(render_brief(result))


def render_brief(result: dict) -> str:
    d = result.get("date", "?")
    lines = [f"🔁 情绪反转检测 {d}（{result.get('rule_version', '')}）", ""]
    if result.get("skipped"):
        lines.append(f"⏭️ {result['skipped']}")
        return "\n".join(lines) + "\n"
    cands = result.get("candidates") or []
    if not cands:
        lines.append(f"ℹ️ {result.get('note', '无候选')}")
        return "\n".join(lines) + "\n"
    ss = result.get("seesaw_parts") or {}
    ss_txt = []
    if "breadth" in ss:
        ss_txt.append(f"宽度 {'✅' if ss['breadth'] else '❌'}")
    if "index_reversal" in ss:
        ss_txt.append(f"指数该弱不弱 {'✅' if ss['index_reversal'] else '❌'}")
    if result.get("seesaw_data_gap"):
        ss_txt.append("⚠️ 跷跷板双维度数据缺口(fail-closed)")
    lines.append(f"跷跷板验证: {'✅' if result.get('seesaw') else '❌'} ({', '.join(ss_txt) or '无维度'})")
    lines.append("")
    for p in result.get("per_stock") or []:
        kind = "fade" if p["kind"] == "fade" else "诱多"
        if p.get("data_gap"):
            lines.append(f"  ⚠️ {p['name']}({p['code']})[{kind}] 数据缺口")
        else:
            mark = "✅确认" if p["confirmed"] else "❌未确认"
            vol_txt = f"量比(对昨)={p.get('vol_ratio_prev')}"
            if p.get("vol_waived_by_breadth"):
                vol_txt += " 量能由宽度跳变豁免"
            lines.append(
                f"  {mark} {p['name']}({p['code']})[{kind}] 高开{p.get('open_chg_pct'):+.2f}%"
                f" | 收阳={p.get('bullish_close')} {vol_txt}")
    lines.append("")
    if result.get("reversal_confirmed"):
        lines.append(f"🔴 结论: 情绪反转首日确认 → 次一交易日对 {len(result['confirmed_codes'])} 只确认票"
                     f"覆盖技术性卖出信号，仅保留 -{HARD_STOP_PCT:.0f}% 硬止损")
        lines.append(f"   确认名单: {', '.join(result['confirmed_codes'])}")
    else:
        lines.append("⚪ 结论: 反转未确认，技术卖出信号照常执行")
    return "\n".join(lines) + "\n"


def _push_telegram(result: dict):
    try:
        from send_telegram import send_with_retry
        res = send_with_retry(render_brief(result), parse_mode="")
        log.info("Telegram 推送 ok=%s", res.get("ok"))
    except Exception as e:  # noqa: BLE001
        log.warning("Telegram 推送失败(产物已落盘): %s", e)


# ══════════════════════════════════════
# 覆盖消费口（signal_arbitrator 调用）
# ══════════════════════════════════════

def load_reversal_override(symbol: str, for_date: str = None):
    """
    返回 {confirmed_date, hard_stop_pct} 若 symbol 在「上一交易日」确认的反转名单内；
    否则 None。有效性窗口 = 文件日的次一交易日，仅一日，防陈旧覆盖。
    """
    for_date = for_date or datetime.now().strftime("%Y-%m-%d")
    try:
        from analysis.service import get_trading_days
        days = get_trading_days(min_date="2024-01-01")
        if not days:
            log.warning("交易日历不可得，反转覆盖退化为仅接受「昨日」口径")
            prev = (datetime.strptime(for_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            prior = [d for d in days if d < for_date]
            prev = prior[-1] if prior else None
        if not prev:
            return None
    except Exception as e:  # noqa: BLE001
        log.warning("反转覆盖交易日解析失败(fail-closed): %s", e)
        return None

    path = os.path.join(OUT_DIR, f"reversal_{prev}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        log.warning("反转档案解析失败 %s: %s", path, e)
        return None
    if not data.get("reversal_confirmed"):
        return None
    if symbol not in (data.get("confirmed_codes") or []):
        return None
    return {"confirmed_date": prev, "hard_stop_pct": data.get("hard_stop_pct", HARD_STOP_PCT)}


# ══════════════════════════════════════
# 对抗自测（无网络无真实数据）
# ══════════════════════════════════════

def _selftest() -> int:
    """7 组合成场景断言，任何断言失败 exit 1"""
    cases = []

    def k(open_, close, prev_close, vol, ma5, vol_prev=None):
        return {"open": open_, "close": close, "prev_close": prev_close,
                "vol": vol, "vol_ma5_prev": ma5, "vol_prev": vol_prev}

    cands = [{"code": "AAAAAA", "name": "甲", "kind": "fade", "open_chg_pct": 2.5},
             {"code": "BBBBBB", "name": "乙", "kind": "fade", "open_chg_pct": 3.1}]
    kdata = {c["code"]: k(10.0, 10.6, 10.1, 150000, 100000, 130000) for c in cands}

    # 1. 全条件成立 -> 确认
    r = evaluate(cands, kdata, {"up_ratio_today": 0.70, "up_ratio_prev": 0.30}, None)
    cases.append(("全条件确认", r["reversal_confirmed"] is True and set(r["confirmed_codes"]) == {"AAAAAA", "BBBBBB"}))
    # 2. 无跷跷板（宽度不动）-> 不确认，但个股仍确认
    r = evaluate(cands, kdata, {"up_ratio_today": 0.70, "up_ratio_prev": 0.68}, None)
    cases.append(("宽度无改善不确认", r["reversal_confirmed"] is False and len(r["confirmed_codes"]) == 2))
    # 3. 跷跷板成立但票收阴 -> 不确认
    kdown = {c["code"]: k(10.5, 10.2, 10.1, 150000, 100000, 130000) for c in cands}
    r = evaluate(cands, kdown, {"up_ratio_today": 0.70, "up_ratio_prev": 0.30}, None)
    cases.append(("收阴不确认", r["reversal_confirmed"] is False and r["confirmed_codes"] == []))
    # 4. 跷跷板缺数据(fail-closed) -> 不确认
    r = evaluate(cands, kdata, {"up_ratio_today": None, "up_ratio_prev": None}, None)
    cases.append(("跷跷板缺数据fail-closed", r["reversal_confirmed"] is False and r["seesaw_data_gap"] is True))
    # 5. 仅 1 只 fade 确认 -> 不确认（需 >=2）
    kone = dict(kdata)
    kone["BBBBBB"] = k(10.5, 10.2, 10.1, 150000, 100000, 130000)
    r = evaluate(cands, kone, {"up_ratio_today": 0.70, "up_ratio_prev": 0.30}, None)
    cases.append(("单票fade不足", r["reversal_confirmed"] is False and r["confirmed_codes"] == ["AAAAAA"]))
    # 6. fade 空 + 1 只诱多票确认 + 跷跷板 -> 确认（该弱不弱单票口径）
    trap = [{"code": "CCCCCC", "name": "丙", "kind": "trap", "open_chg_pct": 3.4}]
    r = evaluate(trap, {"CCCCCC": k(20.0, 21.0, 20.2, 90000, 60000, 70000)},
                 {"up_ratio_today": 0.55, "up_ratio_prev": 0.40}, None)
    cases.append(("诱多票单票口径", r["reversal_confirmed"] is True and r["confirmed_codes"] == ["CCCCCC"]))
    # 7. 个股不放量但宽度大跳变(>=0.20)豁免 -> 确认（v1.1 口径，10-28 校准场景）
    knovol = {c["code"]: k(10.0, 10.6, 10.1, 95000, 100000, 100000) for c in cands}
    r = evaluate(cands, knovol, {"up_ratio_today": 0.742, "up_ratio_prev": 0.11}, None)
    cases.append(("宽度跳变豁免个股量能",
                  r["reversal_confirmed"] is True
                  and all(p.get("vol_waived_by_breadth") for p in r["per_stock"])))

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(f"  {'✅' if ok else '❌'} {name}")
    if failed:
        print(f"SELFTEST FAILED: {failed}")
        return 1
    print("SELFTEST PASSED (7/7)")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--selftest" in args:
        return _selftest()
    date = None
    for a in args:
        if a.startswith("--date="):
            date = a.split("=", 1)[1]
    push = "--push" in args
    result = detect(date=date, push=push)
    print(render_brief(result))
    print("\n=====REVERSAL_RESULT=====")
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    print("=====REVERSAL_END=====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
