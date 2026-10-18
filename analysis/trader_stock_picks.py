#!/usr/bin/env python3
"""
Trader stock picks module: simplified implementation of the top trader's stock selection logic.
Provides scoring and recommendation for watchlist stocks based on five dimensions:
chip structure, volume-price, fund flow, fundamentals, technical.
2026-10-09 结构改造（10-08 立项）：数据缺失维度返回 None（禁止隐性 5.0 填充），
总分 = 有效维度均值 ×5 归一到 50 制，输出携带 valid_dims / dims_available，
供消费方展示「口径 x/5 维」。根因教训：10-05「fallback 默认值把贵票伪装成中档票」
的评分版（10-08 官方五维 Top1=福莱蒽特 29.0，RSI70 超买被筹码+资金双 5.0 托底）。
2026-10-17 残余清理（10-08 立项第 4 条收尾）：①维内隐性中性填充清零——
main_net_in / pe/pb/roe / dif/dea/rsi 缺失一律 None（旧 `or 0`/`or 50` 把未观测洗成中性）；
②移除 RSI<30 加 2 分的「超卖是买点」左侧假设，超卖降级为 catalyst 信息；
③stale 注释纠正（下游已不回退 5.0）。
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
from typing import List, Dict, Any, Optional
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

    2026-10-09 改：**缺失字段保留 None，不再 `or 0`**。
    moneyflow_em 新增了 datacenter 兜底通道，该通道**只有当日、且无中/小单拆分
    与 main_pct**（上游就没这些字段）。旧写法 `get(k, 0) or 0` 会把 None 洗成 0，
    等于把「未观测」伪造成「观测到 0 净流入」——同 10-05「隐性 5.0 把贵票伪装成
    中档票」教训的字段版。当前 score_fund_flow 只消费 main_net_in（三路均有值），
    改动不影响现有分数；但不修就是下一次 key/口径契约事故的种子。
    """
    try:
        from analysis.moneyflow_em import stock_flow
        r = stock_flow(symbol, lmt=5)
        if r and r.get("days"):
            latest = r["days"][-1]
            return {
                "main_net_in": latest.get("main"),
                "main_net_in_rate": latest.get("main_pct"),
                "super_net_in": latest.get("xlarge"),
                "big_net_in": latest.get("large"),
                "medium_net_in": latest.get("medium"),
                "small_net_in": latest.get("small"),
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
        # NaN 防御：任一指标 NaN 视为该票技术数据不可用（下游 score_technical 返回 None，
        # 该维退出计分——2026-10-09 结构改造后行为；原「下游回退 5.0」注释 stale，10-17 纠正）
        if any(v != v for v in latest.values()):  # NaN check
            import logging
            logging.getLogger(__name__).warning(f"get_technical_from_hist({symbol}) 指标含 NaN: {latest}")
            return {}
        return latest
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"get_technical_from_hist({symbol}) 失败: {e}")
        return {}

# ── 筹码结构维（第5维复活，2026-10-18）────────────────────────────
# 数据源: data/research_price_cache/chipmetrics_{sym}.pkl（经典筹码衰减法自算，
# 由 scripts/chip_distribution_study.py 生成，全部本地可复现，无外部接口依赖）。
# 标定依据（analysis/research/chip_distribution_study.json + 2026-10-18 绝对阈值复核）:
#   * cost_bias(现价偏离平均成本) < -0.20 → 5日超额 +2.38% CI95[+1.48,+3.26]，n=309
#     < -0.15 → +1.39% CI95[+0.95,+1.81]，n=1157；分年 2024/2025/2026 全部为正
#     （+1.48%/+3.83%/+1.12%）→ 三年度稳健，非单年偶然。
#   * winner_pct(获利盘) 与 conc90(成本集中度) 无单调关系（各档差异 ≤0.5pp 且不单调）
#     → 不参与打分，仅作诊断字段输出。
# 口径限制: 极端档(-0.20)只覆盖 1.5% 样本，故设为**加分项**（缺省 5.0 中性不加不减），
#   不当淘汰条件；< -0.15 给 +2.5，< -0.20 给 +3.5。成本偏离 > +0.15（浮盈过大）
#   依据不足，不扣分（避免引入未证伪的负向假设）。
CHIP_CACHE_DIR = os.path.join(_WORKSPACE, "data", "research_price_cache")


def load_chip_metrics(symbol: str, price_ref: str = None):
    """读 chipmetrics 缓存，取最后一行的三指标。缺失/陈旧返回 None（不填充默认值）。

    2026-10-18 新鲜度把关（契约见 scripts/chip_freshness_test.py）：
    旧实现只取 `df.iloc[-1]`，完全不看这一行有多旧。缓存冻结时
    `cost_bias = close(T)/avg_cost(T0) - 1` 会拿新价减旧成本 → 上行行情里该值被
    系统性推高，−0.20 深度折价档永不触发（加分项静默失效）；下行则批量误报折价。
    现按 analysis/chip_freshness 判定：滞后 > STALE_CRITICAL_DAYS(默认10) 交易日
    → 返回 None（该维退出计分，走「缺失不填充」纪律），并输出可见告警。
    滞后在 (MAX_LAG_DAYS, STALE_CRITICAL_DAYS] 之间仍可用但告警。

    `price_ref` 可外部注入基准日（测试/批量场景可避免逐票重启读价格缓存）。
    """
    try:
        path = os.path.join(CHIP_CACHE_DIR, f"chipmetrics_{symbol}.pkl")
        if not os.path.exists(path):
            return None
        import pandas as pd
        df = pd.read_pickle(path)
        if df is None or len(df) == 0:
            return None
        r = df.iloc[-1]
        chip_date = str(r.get("date", ""))[:10]

        # ── 新鲜度把关 ──
        import logging
        from analysis import chip_freshness as _cf
        fresh = _cf.check(symbol, chip_date,
                          ref_date=price_ref,
                          cache_dir=(None if price_ref else CHIP_CACHE_DIR))
        if not fresh["ok"]:
            if not fresh["trustworthy"]:
                logging.getLogger(__name__).warning(
                    f"load_chip_metrics({symbol}) 筹码数据不可信 → 该维 None: {fresh['reason']}")
                return None
            logging.getLogger(__name__).warning(
                f"load_chip_metrics({symbol}) 筹码数据陈旧但仍在容忍内: {fresh['reason']}")

        return {"date": chip_date,
                "winner_pct": float(r["winner_pct"]),
                "cost_bias": float(r["cost_bias"]),
                "conc90": float(r["conc90"]),
                "lag": fresh["lag"]}
    except Exception as e:
        print(f"⚠️ 筹码指标读取失败 {symbol}: {e}")
        return None


def score_chip_structure(symbol: str) -> float:
    """Chip structure score (0-10)。

    2026-10-09：无数据源 → None（禁止隐性 5.0 填充）。
    2026-10-18：本地自算筹码分布复活该维，按 cost_bias 绝对阈值加分（见上方标定注释）。
    缓存缺失/异常 → None（沿用 10-09「缺失不填充」纪律，不参与总分）。
    """
    m = load_chip_metrics(symbol)
    if not m or not m["date"]:
        return None
    cb = m["cost_bias"]
    if cb < -0.20:
        return 8.5      # 5.0 + 3.5
    if cb < -0.15:
        return 7.5      # 5.0 + 2.5
    return 5.0          # 中性：无证据支持加分或扣分

def score_volume_price(symbol: str, name: str, spot_item: dict) -> float:
    """Volume-price score based on turnover rate and price change.

    2026-10-09 改造：spot_item 无真实 price → 返回 None。
    旧行为：空 spot 也给 base 5.0-1=4.0，把「无行情」伪装成「弱势票」。
    """
    if not spot_item or spot_item.get("price") is None:
        return None
    score = 5.0  # base
    # 2026-10-17 口径修正（依据 scripts/vp_change_event_study.py 事件研究，
    # analysis/research/2026-10-17_vp_change_event_study.json）：
    # 旧 |chg|>5 → +2 方向无关加分；分方向分年基线显示 2026 震荡市
    # 大涨日(≥+5)后 5d 超额 -0.44pp（追高被惩罚），大跌日后 +2.22pp（均值回归）。
    # 2026-10-17 中间版（3ff20d0）：统一 |chg|>2 → +1，仅拆追高奖励。
    # 2026-10-18 用户拍板「涨>5% 削分」：涨幅>5% 改 -1（与平盘同档——追高不再
    # 得正分；事件研究只证伪正分、未支持深罚，故削到平盘档而非更深）。
    # 跌幅>5% 归 +1 档（大跌后 +2.22pp 为证据最强方向，保留均值回归加分）。
    # 中幅 |chg|>2 → +1 保留（强弱识别，未单独证伪）。
    change_pct = spot_item.get("change_pct", 0) or 0
    if change_pct > 5:
        score -= 1  # 追高削分：大涨日后 5d 超额为负，与平盘同档
    elif abs(change_pct) > 2:
        score += 1
    elif abs(change_pct) < 0.5:
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
    main_net_in = data.get("main_net_in")
    if main_net_in is None:
        # 2026-10-17 残余清理：维内隐性填充——资金维取到数据但 main_net_in 缺失时，
        # 旧 `or 0` 把「未观测」洗成 0 → 中性 5.0 伪装。缺字段 = 维不可用 = None。
        return None
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
    pe = data.get("pe")
    pb = data.get("pb")
    roe = data.get("roe")   # 修复：原代码 key 写成 "roe, 0" 导致恒为 None 且比较崩溃
    # 2026-10-17 残余清理：部分缺失时旧 `or 0` 把缺失子字段静默中性化。
    # 与技术维 NaN 防御同口径：三要素任一缺失 = 维不可用 = None（可见告警）。
    if pe is None or pb is None or roe is None:
        import logging
        logging.getLogger(__name__).warning(
            f"score_fundamentals({symbol}) 基本面字段不完整 pe={pe} pb={pb} roe={roe} → 该维 None")
        return None
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

def score_technical(symbol: str, name: str, tech: Optional[dict] = None) -> float:
    """Technical score based on MACD, RSI, etc from historical signal.

    2026-10-17 复核（10-08 立项第 4 条）：移除「RSI<30 加 2 分」的左侧假设——
    强度排序里超卖不是强势，未确认的猜底不应加分；保留 RSI>70 减 2（对已观测
    极值的惩罚）。超卖状态降级为 catalyst 信息。可逆：若回测证明左侧加分有
    超额收益，改回并附证据。tech 可外部传入（get_trader_picks 单趟取数复用）。
    """
    if tech is None:
        tech = get_technical_from_hist(symbol, name)
    if not tech:
        # 2026-10-09：指标不可得 → None（旧 5.0 让 NaN 防御变成中性分伪装）
        return None
    score = 5.0
    # MACD: dif > dea => bullish
    # 注：key 契约 2026-10-08 已修复（get_technical_from_hist 现保证 dif/dea/rsi 存在）
    # 2026-10-17 残余清理：`or 0`/`or 50` 是维内隐性中性填充——键缺失时伪装成
    # dif/dea=0、rsi=50 中性。与「缺即 None」口径统一：任一键缺失 → None（可见告警）。
    dif = tech.get("dif")
    dea = tech.get("dea")
    rsi = tech.get("rsi")
    if dif is None or dea is None or rsi is None:
        import logging
        logging.getLogger(__name__).warning(
            f"score_technical({symbol}) 技术字段不完整 dif={dif} dea={dea} rsi={rsi} → 该维 None")
        return None
    if dif > dea:
        score += 2
    else:
        score -= 2
    # RSI 口径（2026-10-17）：仅保留超买惩罚；超卖不再加分（左侧假设移除）
    if rsi > 70:
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
        # 技术维单趟取数：同一份 tech 供打分与 catalyst 使用（2026-10-17）
        _tech = get_technical_from_hist(symbol, name)
        _rsi = (_tech or {}).get("rsi")
        dims: Dict[str, Any] = {
            "chip": score_chip_structure(symbol),
            "volume_price": score_volume_price(symbol, name, spot_map.get(symbol, {})),
            "fund_flow": score_fund_flow(symbol, name),
            "fundamentals": score_fundamentals(symbol, name),
            "technical": score_technical(symbol, name, tech=_tech),
        }
        valid = {k: float(v) for k, v in dims.items() if v is not None}
        dims_available = list(valid.keys())
        total = round(sum(valid.values()) / len(valid) * 5.0, 2) if valid else None

        stage = _infer_stage(valid)
        # catalyst：仅陈述已观测事实，不做方向加工（2026-10-17 RSI 口径配套）
        catalyst = "待定"
        if _rsi is not None:
            if _rsi < 30:
                catalyst = f"RSI超卖({_rsi:.0f})，左侧信号未确认"
            elif _rsi > 70:
                catalyst = f"RSI超买({_rsi:.0f})"
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
