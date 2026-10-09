#!/usr/bin/env python3
"""
Trader stock picks module: simplified implementation of the top trader's stock selection logic.
Provides scoring and recommendation for watchlist stocks based on five dimensions:
chip structure, volume-price, fund flow, fundamentals, technical.
2026-10-09 结构改造（10-08 立项）：数据缺失维度返回 None（禁止隐性 5.0 填充），
总分 = 有效维度均值 ×5 归一到 50 制，输出携带 valid_dims / dims_available，
供消费方展示「口径 x/5 维」。根因教训：10-05「fallback 默认值把贵票伪装成中档票」
的评分版（10-08 官方五维 Top1=福莱蒽特 29.0，RSI70 超买被筹码+资金双 5.0 托底）。
"""


# ── sys.path 样板（TOOLS.md 强制；2026-09-19）──
# 脚本模式下 sys.path[0] 是脚本所在目录而非工作区根，
# 必须显式插入工作区根（供 `from analysis.xxx`）+ analysis/（供子包引用）。
import os as _os, sys as _sys
_WORKSPACE = _os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (_WORKSPACE, _os.path.join(_WORKSPACE, "analysis")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os, json, time, math
from typing import List, Dict, Any
import pandas as pd

try:
    import akshare as ak
    _HAS_AKSHARE = True
except Exception:
    _HAS_AKSHARE = False

from analysis.service import get_daily_hist, get_spot_scan, is_trading_day

# Helper to safely fetch akshare data (fail fast)
# 2026-08 实测：本机 akshare 财务/资金流等东财接口被网络拦截，重试+sleep 只会白白拖慢
# (29只×多次重试→超 18s)。改为单次尝试、失败即返回 None，上层已做好优雅降级。
def _akshare_call(func, *args, **kwargs):
    if not _HAS_AKSHARE:
        return None
    try:
        return func(*args, **kwargs)
    except Exception:
        return None

def get_fundamentals(symbol: str) -> Dict[str, float]:
    """Fetch PE/PB/ROE etc.
    2026-10-05 改：akshare 新浪财务接口已退化（返回 0 行）且 stock_a_indicator_lg 已被新版移除；
    改用 baostock：peTTM/pbMRQ 来自日K扩展字段，roe/profit 来自 query_profit_data（最近季度）。
    """
    try:
        import baostock as bs
    except ImportError:
        return {}
    code = ("sh." + symbol) if symbol.startswith(("600", "601", "603", "605", "688")) else ("sz." + symbol)
    out: Dict[str, float] = {}
    lg = bs.login()
    if lg.error_code != "0":
        return {}
    try:
        rs = bs.query_history_k_data_plus(
            code, "date,close,peTTM,pbMRQ,psTTM",
            start_date="2026-06-01", end_date="2026-12-31",
            frequency="d", adjustflag="2")
        last = None
        while rs.error_code == "0" and rs.next():
            last = rs.get_row_data()
        if last and last[2] not in ("", "0.0000"):
            out["pe"] = float(last[2])
            out["pb"] = float(last[3])
        rs2 = bs.query_profit_data(code=code, year=2026, quarter=2)
        prow = rs2.get_row_data() if rs2.error_code == "0" and rs2.next() else None
        if not prow:  # 部分标的 Q2 未披露，回退 Q1
            rs2 = bs.query_profit_data(code=code, year=2026, quarter=1)
            prow = rs2.get_row_data() if rs2.error_code == "0" and rs2.next() else None
        if prow:
            # fields: code,pubDate,statDate,roeAvg,npMargin,gpMargin,netProfit,epsTTM,MBRevenue,totalShare,liqaShare
            if prow[3] not in ("", None):
                out["roe"] = float(prow[3]) * 100
            if prow[4] not in ("", None):
                out["profit"] = float(prow[4]) * 100
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"get_fundamentals({symbol}) baostock 失败: {e}")
    finally:
        bs.logout()
    return out

def get_fund_flow(symbol: str) -> Dict[str, float]:
    """Fetch main force net inflow, etc.
    2026-10-05 改：akshare stock_individual_fund_flow（东财）在本机被 TLS 指纹拦截；
    改用 moneyflow_em（curl_cffi Chrome 指纹 + push2delay 回退链，已实战验证可用）。
    """
    try:
        from analysis.moneyflow_em import stock_flow
        r = stock_flow(symbol, lmt=5)
        if r and r.get("days"):
            latest = r["days"][-1]
            return {
                "main_net_in": latest.get("main", 0) or 0,
                "main_net_in_rate": latest.get("main_pct", 0) or 0,
                "super_net_in": latest.get("xlarge", 0) or 0,
                "big_net_in": latest.get("large", 0) or 0,
                "medium_net_in": latest.get("medium", 0) or 0,
                "small_net_in": latest.get("small", 0) or 0,
            }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"get_fund_flow({symbol}) moneyflow_em 失败: {e}")
    return {}

def get_technical_from_hist(symbol: str, name: str) -> Dict[str, Any]:
    """Compute technical indicators from historical data.

    契约（2026-10-08 修复）：返回 dict，keys 固定为 macd/dif/dea/rsi。
    下游 score_technical 依赖这些 key —— 旧版主路径返回 calc_full_signal 的
    signal dict（keys=level/score/reasons/risks），与下游取值键完全错配，
    导致技术分全池恒 3.0（第二个 key 契约事故，同 roe 引号坑）。
    calc_full_signal 是信号判定接口，不保证指标数值 key，不再作为数据源使用。
    """
    df = get_daily_hist(symbol, name, start_date="20260101")
    if df is None or len(df) < 30:
        return {}
    try:
        close = df["close"]
        # MACD
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9).mean()
        macd = dif - dea
        # RSI
        delta = close.diff()
        up = delta.clip(lower=0)
        down = -delta.clip(upper=0)
        ma_up = up.ewm(com=13, adjust=False).mean()
        ma_down = down.ewm(com=13, adjust=False).mean()
        rs = ma_up / ma_down
        rsi = 100 - (100 / (1 + rs))
        latest = {
            "macd": float(macd.iloc[-1]),
            "dif": float(dif.iloc[-1]),
            "dea": float(dea.iloc[-1]),
            "rsi": float(rsi.iloc[-1]),
        }
        # NaN 防御：任一指标 NaN 视为该票技术数据不可用（下游回退 5.0，而非静默错分）
        if any(v != v for v in latest.values()):  # NaN check
            import logging
            logging.getLogger(__name__).warning(f"get_technical_from_hist({symbol}) 指标含 NaN: {latest}")
            return {}
        return latest
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"get_technical_from_hist({symbol}) 失败: {e}")
        return {}

def score_chip_structure(symbol: str) -> float:
    """Chip structure score (0-10). Default medium."""
    # In reality we would look at institutional holder %, top 10 changes, etc.
    # 2026-10-09 改造（10-08 立项）：无数据源 → 返回 None，禁止隐性 5.0 填充。
    # 当前无筹码数据源 → 该维对所有票一致为 None，不参与总分（对相对排序无信息量）。
    return None

def score_volume_price(symbol: str, name: str, spot_item: dict) -> float:
    """Volume-price score based on turnover rate and price change.

    2026-10-09 改造：spot_item 无真实 price → 返回 None。
    旧行为：空 spot 也给 base 5.0-1=4.0，把「无行情」伪装成「弱势票」。
    """
    if not spot_item or spot_item.get("price") is None:
        return None
    score = 5.0  # base
    # Price change magnitude (absolute)
    change_pct = abs(spot_item.get("change_pct", 0))
    if change_pct > 5:
        score += 2
    elif change_pct > 2:
        score += 1
    if change_pct < 0.5:
        score -= 1
    # Turnover rate (if available)
    turnover = spot_item.get("turnover_rate")
    if turnover is not None:
        if turnover > 10:
            score += 2
        elif turnover > 5:
            score += 1
        elif turnover < 1:
            score -= 1
    # Clamp
    return max(0, min(10, score))

def score_fund_flow(symbol: str, name: str) -> float:
    """Fund flow score based on net main force inflow."""
    data = get_fund_flow(symbol)
    if not data:
        # 2026-10-09：缺失即 None，禁止 5.0 托底（moneyflow 双路失败时资金维退出排序）
        return None
    main_net_in = data.get("main_net_in") or 0
    # Normalize roughly: assume +/- 1e8 is strong
    # Convert to 0-10 scale
    # Simple: if main_net_in > 0 => bullish, else bearish
    if main_net_in > 0:
        score = 5 + min(5, main_net_in / 20000000)  # 每 2000万 加 1 分，上限 10
    else:
        score = 5 - min(5, abs(main_net_in) / 20000000)
    return max(0, min(10, score))

def score_fundamentals(symbol: str, name: str) -> float:
    """Fundamentals score based on PE, PB, ROE."""
    data = get_fundamentals(symbol)
    if not data:
        # 2026-10-09：baostock 完全取不到 PE/PB/ROE → None（旧 5.0 把高估值票伪装成中档票）
        return None
    pe = data.get("pe") or 0
    pb = data.get("pb") or 0
    roe = data.get("roe") or 0   # 修复：原代码 key 写成 "roe, 0" 导致恒为 None 且比较崩溃
    score = 5.0
    # PE lower better (but not too low)
    if 0 < pe < 15:
        score += 2
    elif pe < 0:
        score -= 2  # loss
    elif pe > 50:
        score -= 2
    # PB lower better
    if 0 < pb < 3:
        score += 1.5
    elif pb > 10:
        score -= 1.5
    # ROE higher better
    if roe > 15:
        score += 1.5
    elif roe < 5:
        score -= 1
    return max(0, min(10, score))

def score_technical(symbol: str, name: str) -> float:
    """Technical score based on MACD, RSI, etc from historical signal."""
    tech = get_technical_from_hist(symbol, name)
    if not tech:
        # 2026-10-09：指标不可得 → None（旧 5.0 让 NaN 防御变成中性分伪装）
        return None
    score = 5.0
    # MACD: dif > dea => bullish
    # 注：key 契约 2026-10-08 已修复（get_technical_from_hist 现保证 dif/dea/rsi 存在）
    dif = tech.get("dif") or 0
    dea = tech.get("dea") or 0
    if dif > dea:
        score += 2
    else:
        score -= 2
    # RSI: 30-70 neutral, <30 oversold => bullish, >70 overbought => bearish
    rsi = tech.get("rsi") or 50
    if rsi < 30:
        score += 2
    elif rsi > 70:
        score -= 2
    # MACD histogram rising?
    macd = tech.get("macd", 0)
    # Not stored, compute from dif/dea if available
    # We'll approximate: if dif > 0 and dea > 0 and dif > dea? Already captured.
    return max(0, min(10, score))

def get_trader_picks(watchlist: List[tuple]) -> List[Dict[str, Any]]:
    """
    Return list of stock picks sorted by total score.

    2026-10-09 结构改造（10-08 立项，用户批准）：
    - 缺失维度 = None，**不再回退 5.0/0.0**
    - total_score = 有效维度均值 ×5，归一到 50 制（与旧「五维相加」量纲一致，阈值 35/20 可沿用）
    - 新增 valid_dims（int, 0-5）与 dims_available（可用维度名列表），消费方须展示「口径 x/5 维」
    - 有效维度为 0 的票 total_score=None，参与列表但排在最后，且 advice 标为「无法评估」
    Each item: {
        "symbol": str,
        "name": str,
        "total_score": float | None,   # 有效维度均值 ×5（50 制）
        "valid_dims": int,             # 参与计算的有效维度个数 0-5
        "dims_available": [str],       # 有效维度名
        "chip": float | None,
        "volume_price": float | None,
        "fund_flow": float | None,
        "fundamentals": float | None,
        "technical": float | None,
        "stage": str,  # e.g., "吸筹", "洗盘末期", "拉升初期"
        "catalyst": str,
        "advice": str  # BUY/SELL/HOLD with reason
    }
    """
    # spot 一次性拉取，量价维直接用真实行情（旧实现先空 spot 打分再二次覆盖，
    # 空 spot 阶段会产出伪装的 4.0 分，且要二次重排；现改为单趟）
    spot_data, _ = get_spot_scan()
    spot_map = {item["symbol"]: item for item in (spot_data or []) if "symbol" in item}

    results: List[Dict[str, Any]] = []
    for symbol, name in watchlist:
        dims: Dict[str, Any] = {
            "chip": score_chip_structure(symbol),
            "volume_price": score_volume_price(symbol, name, spot_map.get(symbol, {})),
            "fund_flow": score_fund_flow(symbol, name),
            "fundamentals": score_fundamentals(symbol, name),
            "technical": score_technical(symbol, name),
        }
        valid = {k: float(v) for k, v in dims.items() if v is not None}
        dims_available = list(valid.keys())
        total = round(sum(valid.values()) / len(valid) * 5.0, 2) if valid else None

        stage = _infer_stage(valid)
        catalyst = "待定"
        if total is None:
            action, reason = "N/A", "全部维度数据缺失，无法评估"
        elif total >= 35:
            action, reason = "BUY", f"综合评分较高（{len(valid)}/5 维口径），具备启动条件"
        elif total <= 20:
            action, reason = "SELL", f"综合评分偏低（{len(valid)}/5 维口径），注意风险"
        else:
            action, reason = "HOLD", f"综合评分中性（{len(valid)}/5 维口径），建议观望"

        results.append({
            "symbol": symbol,
            "name": name,
            "total_score": total,
            "valid_dims": len(valid),
            "dims_available": dims_available,
            "chip": None if dims["chip"] is None else round(dims["chip"], 2),
            "volume_price": None if dims["volume_price"] is None else round(dims["volume_price"], 2),
            "fund_flow": None if dims["fund_flow"] is None else round(dims["fund_flow"], 2),
            "fundamentals": None if dims["fundamentals"] is None else round(dims["fundamentals"], 2),
            "technical": None if dims["technical"] is None else round(dims["technical"], 2),
            "stage": stage,
            "catalyst": catalyst,
            "advice": f"{action}: {reason}",
        })
    # 有效分降序；无有效维度的票排最后
    results.sort(key=lambda x: (x["total_score"] is not None, x["total_score"] or 0.0), reverse=True)
    return results


def _infer_stage(valid: Dict[str, float]) -> str:
    """阶段判定（None 安全版）。缺失维度不参与判定，不再被 5.0 假分触发/压制。"""
    tech = valid.get("technical")
    ff = valid.get("fund_flow")
    chip = valid.get("chip")
    vp = valid.get("volume_price")
    if tech is not None and ff is not None:
        if tech > 6 and ff > 6:
            return "拉升初期"
        if tech < 4 and ff < 4:
            return "洗盘末期"
    if chip is not None and vp is not None and chip > 7 and vp > 6:
        return "吸筹"
    return "观察"


def format_picks_for_report(picks: List[Dict[str, Any]], top_n: int = 10) -> str:
    """推送/报告侧可读表（2026-10-09 五维改造配套）。

    强制展示「口径 x/5 维」——这是本次结构改造的红线：
    禁止只报总分不报维度覆盖（防止半口径票被当成全口径票比较）。
    """
    if not picks:
        return "(操盘手选股无数据)"
    from collections import Counter
    dist = Counter(p.get("valid_dims", 0) for p in picks)
    cov = " | ".join(f"{k}/5维 {v}只" for k, v in sorted(dist.items(), reverse=True))
    lines = [f"维度覆盖: {cov}",
             "| 排名 | 股票 | 总分(50制) | 口径 | 量价 | 基本面 | 技术 | 资金 | 筹码 | 阶段 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for i, p in enumerate(picks[:top_n], 1):
        fmt = lambda v: "—" if v is None else f"{v:g}"
        lines.append(
            f"| {i} | {p['name']}({p['symbol']}) | {fmt(p.get('total_score'))} "
            f"| {p.get('valid_dims', 0)}/5 | {fmt(p.get('volume_price'))} "
            f"| {fmt(p.get('fundamentals'))} | {fmt(p.get('technical'))} "
            f"| {fmt(p.get('fund_flow'))} | {fmt(p.get('chip'))} | {p.get('stage', '')} |"
        )
    lines.append("> 口径说明：总分=有效维度均值×5；— 表示该维数据缺失，未参与计分（禁止 5.0 填充）。")
    return "\n".join(lines)

if __name__ == "__main__":
    # Example usage with a small watchlist
    from analysis.service import WATCHLIST
    picks = get_trader_picks(WATCHLIST[:5])  # test with first 5
    for p in picks:
        print(p)
