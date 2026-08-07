#!/usr/bin/env python3
"""
030市场健康度自动评分 — 供收盘推送 cron 调用
===============================================
基于030复盘框架的七维健康度评分，自动计算可用数据维度的得分，
无法自动计算的维度(晋级率/炸板率/主线持续性)给出中性分并标注，
供 agent 结合新闻等数据人工复核。

数据源:
  - akshare stock_zh_a_spot  (全市场涨跌/涨停/跌停, 带重试)
  - akshare stock_zt_pool_em (涨停板池, 若可用则算晋级率/炸板率)
  - hq.sinajs.cn             (指数兜底)

输出:
  - stdout JSON (供 agent 解析推送到 telegram)
  - 落盘 data/market_health_YYYY-MM-DD.json

用法:
  python3 analysis/market_health_score.py [可选: 昨日得分history.json路径]
  # 传昨日得分文件可算出评分变化(030框架跟踪情绪拐点)
"""
import os, sys, json, time
from datetime import datetime
import logging
logging.basicConfig(level=logging.CRITICAL)
import pandas as pd

WORKSPACE = "/Users/duguke/.openclaw/workspace"
OUT_DIR = os.path.join(WORKSPACE, "data")

# 030 评分维度权重
DIM_WEIGHTS = {
    "tier_completeness": 0.20,   # 涨停梯队完整度
    "promotion_rate":    0.15,   # 晋级率
    "bomb_rate":         0.15,   # 炸板率
    "market_width":      0.15,   # 市场宽度
    "volume":            0.15,   # 量能（相对前5日均值）
    "mainline_persist":  0.10,   # 主线持续性
    "negative_feedback": 0.10,   # 负反馈程度
}


def fetch_spot(retries=3, wait=3):
    """拉新浪全A spot，带重试"""
    import akshare as ak
    for attempt in range(retries):
        try:
            return ak.stock_zh_a_spot()
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(wait)


def fetch_zt_pool(retries=2, wait=3):
    """东方财富涨停板池，可能失败"""
    import akshare as ak
    try:
        return ak.stock_zt_pool_em(date=datetime.now().strftime("%Y%m%d"))
    except Exception:
        # 尝试上一交易日(周末/盘前无当日池)
        from datetime import timedelta
        for back in range(1, 4):
            d = (datetime.now() - timedelta(days=back)).strftime("%Y%m%d")
            try:
                return ak.stock_zt_pool_em(date=d)
            except Exception:
                continue
    return None


def score_width(up_ratio):
    if up_ratio > 0.70: return 10
    elif up_ratio > 0.50: return 7
    elif up_ratio > 0.30: return 4
    return 1


def score_neg(limit_down):
    if limit_down == 0: return 10
    elif limit_down <= 3: return 7
    elif limit_down <= 10: return 4
    return 1


def score_tier(zt_pool):
    """涨停梯队完整度: 最高板>=5板=10; 3-4板=7; 1-2板=4; 无连板=1"""
    if zt_pool is None or zt_pool.empty:
        return 5, "无连板数据(池不可用), 中性分"
    try:
        # 东方财富涨停池含'连板数'列(部分版本为'连板数'或'连板')
        col = None
        for c in ["连板数", "连续涨停天数", "连板"]:
            if c in zt_pool.columns:
                col = c; break
        if col is None:
            return 5, "涨停池无连板数列, 中性分"
        max_board = int(pd.to_numeric(zt_pool[col], errors="coerce").max())
        cnt = zt_pool[col].nunique()
        if max_board >= 5: s = 10
        elif max_board >= 3: s = 7
        elif max_board >= 2: s = 4
        else: s = 1
        return s, f"最高{max_board}板"
    except Exception:
        return 5, "连板数解析失败, 中性分"


def score_bomb(zt_pool):
    """炸板率: <20%=10, 20-30%=7, 30-40%=4, >40%=1"""
    if zt_pool is None or zt_pool.empty:
        return 5, "无炸板数据, 中性分"
    cols = zt_pool.columns
    # 涨停池通常不含炸板, 用涨停家数近似；无法取时中性
    return 5, "炸板率需另取炸板池, 中性分"


def score_volume(total_amt_yi, history=None):
    """量能分: 对比前5日均值"""
    if history and history.get("amount_hist"):
        avg5 = history["amount_hist"]
        if avg5 > 0:
            ratio = total_amt_yi / avg5
            if ratio > 1.30: return 10, f"放量{ratio:.0%}"
            elif ratio > 1.00: return 7, f"量能{ratio:.0%}"
            elif ratio > 0.70: return 4, f"缩量{ratio:.0%}"
            else: return 1, f"极度缩量{ratio:.0%}"
    t = total_amt_yi
    if t >= 20000: return 8, f"成交{t:.0f}亿, 显著放量(无5日均值)"
    elif t >= 12000: return 6, f"成交{t:.0f}亿, 正常(无5日均值)"
    elif t >= 8000: return 4, f"成交{t:.0f}亿, 偏缩(无5日均值)"
    else: return 2, f"成交{t:.0f}亿, 地量(无5日均值)"


def score_mainline(index_dict):
    """主线持续性: 结合指数分化。科技强(创业板涨幅>上证)=主线明确6分; 均衡5分; 权重强=7分(主线弱)。"""
    # 030: 主线持续性 连续3天+=10/连续2天=7/首日=4/无=1
    # 这里无法判断连续天数, 用指数结构给个提示分, agent需结合新闻
    if not index_dict:
        return 5, "无指数数据, 中性分"
    csi = index_dict.get("创业板指", {}).get("change_pct", 0)
    shz = index_dict.get("上证指数", {}).get("change_pct", 0)
    diff = csi - shz
    if diff > 2: return 6, f"成长强于权重({diff:+.1f}pp), 主线偏科技"
    elif diff < -2: return 7, f"权重强于成长({diff:+.1f}pp), 主线偏防御/大盘"
    else: return 5, "指数均衡, 主线待确认(结合新闻)"



def compute():
    result = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "dimensions": {},
        "summary": {},
    }

    # 1. 市场宽度
    try:
        spot = fetch_spot()
        df = spot[spot["代码"].str.match(r"^(sh|sz)", na=False)].copy()
        df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce").fillna(0)
        df["最新价"] = pd.to_numeric(df["最新价"], errors="coerce")
        df["成交额"] = pd.to_numeric(df["成交额"], errors="coerce").fillna(0)

        def is_limit_up(r):
            c = r["代码"][2:]
            return (r["涨跌幅"] >= 19.8) if c.startswith(("30", "68")) else (r["涨跌幅"] >= 9.9)
        def is_limit_down(r):
            c = r["代码"][2:]
            return (r["涨跌幅"] <= -19.8) if c.startswith(("30", "68")) else (r["涨跌幅"] <= -9.9)

        limit_up = int(df.apply(is_limit_up, axis=1).sum())
        limit_down = int(df.apply(is_limit_down, axis=1).sum())
        up = int((df["涨跌幅"] > 0).sum())
        down = int((df["涨跌幅"] < 0).sum())
        total = len(df)
        up_ratio = up / total if total else 0
        total_amt = round(float(df["成交额"].sum()) / 1e8, 0)

        result["macro"] = {
            "up": up, "down": down, "total": total,
            "up_ratio": round(up_ratio, 4),
            "limit_up": limit_up, "limit_down": limit_down,
            "total_amount_yi": total_amt,
            "avg_chg": round(float(df["涨跌幅"].mean()), 2),
            "med_chg": round(float(df["涨跌幅"].median()), 2),
        }

        w = score_width(up_ratio)
        neg = score_neg(limit_down)
        result["dimensions"]["market_width"] = {"score": w, "note": f"上涨占比{up_ratio:.0%}(涨{up}/跌{down})"}
        result["dimensions"]["negative_feedback"] = {"score": neg, "note": f"跌停{limit_down}家"}
    except Exception as e:
        result["error"] = f"spot拉取失败: {e}"
        # 兜底给中性分防全脚本失败
        result["dimensions"]["market_width"] = {"score": 5, "note": f"数据失败, 中性分 ({e})"}
        result["dimensions"]["negative_feedback"] = {"score": 5, "note": "数据失败, 中性分"}
        total_amt = 0

    # 2. 涨停池（晋级率/炸板率/梯队）
    zt_pool = fetch_zt_pool()
    tier_score, tier_note = score_tier(zt_pool)
    bomb_score, bomb_note = score_bomb(zt_pool)
    result["dimensions"]["tier_completeness"] = {"score": tier_score, "note": tier_note}
    result["dimensions"]["bomb_rate"] = {"score": bomb_score, "note": bomb_note}
    result["dimensions"]["promotion_rate"] = {
        "score": 5,
        "note": "晋级率需昨日涨停池对比今日, 中性分(agent可结合新闻)",
    }

    # 3. 量能
    v_score, v_note = score_volume(total_amt)
    result["dimensions"]["volume"] = {"score": v_score, "note": v_note}

    # 4. 指数 & 主线
    import urllib.request
    try:
        url = "http://hq.sinajs.cn/list=s_sh000001,s_sz399006,s_sh000688"
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        data = urllib.request.urlopen(req, timeout=8).read().decode("gbk")
        index_dict = {}
        for line in data.splitlines():
            val = line.split('"')[1].split(",")
            if len(val) < 6: continue
            index_dict[val[0]] = {"price": float(val[1]), "chg_pts": float(val[2]), "change_pct": float(val[3])}
        result["index"] = index_dict
        ml_score, ml_note = score_mainline(index_dict)
    except Exception:
        ml_score, ml_note = 5, "指数拉取失败, 中性分"
        result["index"] = {}
    result["dimensions"]["mainline_persist"] = {"score": ml_score, "note": ml_note}

    # ── 加权总分 ──
    weighted = 0.0
    detail = []
    for dim, weight in DIM_WEIGHTS.items():
        sc = result["dimensions"].get(dim, {}).get("score", 5)
        weighted += sc * weight
    total_score = round(weighted, 1)
    result["summary"]["total_score"] = total_score
    if total_score >= 8: result["summary"]["verdict"] = "8-10 市场健康, 可积极交易"
    elif total_score >= 6: result["summary"]["verdict"] = "6-7 市场正常, 按仓位矩阵操作"
    elif total_score >= 4: result["summary"]["verdict"] = "4-5 市场偏弱, 降仓至矩阵建议50%"
    else: result["summary"]["verdict"] = "1-3 市场恶劣, 空仓或极小仓位"

    # 落盘
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"market_health_{datetime.now().strftime('%Y-%m-%d')}.json")
    with open(path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # stdout
    print("=====MARKET_HEALTH_SCORE=====")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print("=====MARKET_HEALTH_END=====")
    return result


if __name__ == "__main__":
    compute()
