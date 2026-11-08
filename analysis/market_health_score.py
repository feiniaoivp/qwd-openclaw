#!/usr/bin/env python3
"""
030市场健康度自动评分 — 供收盘推送 cron 调用
===============================================
基于030复盘框架的七维健康度评分，自动计算可用数据维度的得分，
无法自动计算的维度(晋级率/炸板率/主线持续性)给出中性分并标注，
供 agent 结合新闻等数据人工复核。

数据源:
  - akshare stock_zh_a_spot  (全市场涨跌/涨停/跌停, 带重试)
  - akshare stock_zt_pool_em (涨停板池: 梯队/晋级率)
  - akshare stock_zt_pool_zbgc_em (炸板股池: 真实炸板率, 2026-11-01 A阶段接入)
  - baostock 指数日线 sh.000002+sz.399107 (全市场成交额5日均值, 2026-11-01 A阶段接入;
    量能维从「静态绝对阈值」升级为「相对5日均值」颗粒度)
  - hq.sinajs.cn             (指数兜底)

输出:
  - stdout JSON (供 agent 解析推送到 telegram)
  - 落盘 data/runtime/market_health_YYYY-MM-DD.json

用法:
  python3 analysis/market_health_score.py [可选: 昨日得分history.json路径]
  # 传昨日得分文件可算出评分变化(030框架跟踪情绪拐点)
"""
import os, sys, json, time
import shutil
from datetime import datetime, timedelta
import logging

# 网络兜底：进程级 socket 默认超时（防数据源挂起导致永久阻塞）
try:
    from analysis.net_guard import install_default_timeout as _install_net_timeout
    _install_net_timeout()
except ImportError:
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from analysis.net_guard import install_default_timeout as _install_net_timeout
    _install_net_timeout()
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
    """东方财富涨停板池，可能失败；返回 (df, date_str)（date_str 供炸板池日期对齐校验）"""
    import akshare as ak
    for back in range(0, 4):
        d = (datetime.now() - timedelta(days=back)).strftime("%Y%m%d")
        for attempt in range(retries):
            try:
                df = ak.stock_zt_pool_em(date=d)
                if df is not None and not df.empty:
                    return df, d
                break
            except Exception:
                if attempt == retries - 1:
                    break
                time.sleep(wait)
    return None, None


def fetch_zbgc_pool(retries=2, wait=2):
    """东方财富炸板股池（涨停开板，10-28 复盘验证路径固化）；返回 (df, date_str)"""
    import akshare as ak
    for back in range(0, 4):
        d = (datetime.now() - timedelta(days=back)).strftime("%Y%m%d")
        for attempt in range(retries):
            try:
                df = ak.stock_zt_pool_zbgc_em(date=d)
                if df is not None and not df.empty:
                    return df, d
                break
            except Exception:
                if attempt == retries - 1:
                    break
                time.sleep(wait)
    return None, None


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


def score_bomb(zt_pool, zt_date=None, zbgc_pool=None, zbgc_date=None):
    """炸板率: <20%=10, <30%=7, <40%=4, else 1（全市场口径，10-28: 69/141≈49%→1分）
    仅当涨停池与炸板池日期对齐时真实评分，否则中性分（防两池错日假信号）"""
    zt_n = len(zt_pool) if zt_pool is not None and not zt_pool.empty else 0
    zb_n = len(zbgc_pool) if zbgc_pool is not None and not zbgc_pool.empty else 0
    if not (zt_n or zb_n):
        return 5, "涨停+炸板池均不可得, 中性分"
    if zt_date and zbgc_date and zt_date != zbgc_date:
        return 5, f"两池日期未对齐(涨停{zt_date}/炸板{zbgc_date}), 中性分"
    denom = zt_n + zb_n
    if denom <= 0:
        return 5, "无样本, 中性分"
    rate = zb_n / denom
    if rate < 0.20: s = 10
    elif rate < 0.30: s = 7
    elif rate < 0.40: s = 4
    else: s = 1
    return s, f"炸板率{rate:.0%}({zb_n}/{denom})"


def score_promotion(prev_codes, today_pool, run_date=None):
    """晋级率 = 昨日涨停今日再涨停 / 昨日涨停: <15%=1, <25%=4, <40%=7, else 10
    （10-28 实盘: 8/41≈19.5%→4分；全市场口径）
    非交易日运行时 zt_pool 会回退到最近交易日 → 与 prev 基准同日 → 假 100%，
    显式拦截（2026-11-01 周日实跑坐实的自比陷阱，与 bomb 维日期对齐同族）"""
    if not prev_codes:
        return 5, "昨日涨停池不可得, 中性分"
    if run_date:
        try:
            from analysis.service import is_trading_day
            if not is_trading_day(run_date):
                return 5, f"{run_date} 非交易日(池数据回退自比), 晋级率中性分"
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 交易日判定失败(晋级率降级中性): {e}", file=sys.stderr)
            return 5, "交易日判定不可得, 晋级率中性分"
    today_codes = set()
    if today_pool is not None and not today_pool.empty and "代码" in today_pool.columns:
        today_codes = set(today_pool["代码"].astype(str))
    promoted = len(set(prev_codes) & today_codes)
    rate = promoted / len(prev_codes)
    if rate < 0.15: s = 1
    elif rate < 0.25: s = 4
    elif rate < 0.40: s = 7
    else: s = 10
    return s, f"晋级率{rate:.0%}({promoted}/{len(prev_codes)})"


def _prev_trading_day():
    """SSOT 交易日历取上一交易日（晋级率基准日）；不可得返回 None（显式降级）"""
    try:
        from analysis.service import get_trading_days
        today = datetime.now().strftime("%Y-%m-%d")
        prior = [d for d in get_trading_days(min_date="2026-01-01") if d < today]
        return prior[-1] if prior else None
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 交易日历不可得(晋级率降级中性): {e}", file=sys.stderr)
        return None


def fetch_amount_history(lookback=5):
    """全市场成交额历史（沪A sh.000002 + 深A sz.399107，baostock 指数日线 amount 字段）。
    返回 [(date_str, amount_yi), ...] 升序、不含今日（今日量能由 spot 口径现算）；
    失败返回 []（量能维显式降级静态阈值，不填默认值）。"""
    try:
        from analysis.data_layer import get_router
        r = get_router()
        start = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        total = None
        for code in ("sh.000002", "sz.399107"):
            df = r.baostock.get_index_daily(code, start, adjustflag="3")
            if df is None or df.empty or "amount" not in df.columns:
                raise ValueError(f"{code} amount 序列不可得")
            s = df.set_index("date")["amount"] / 1e8
            total = s if total is None else total.add(s, fill_value=0)
        if total is None or total.empty:
            return []
        today = datetime.now().strftime("%Y-%m-%d")
        out = [(str(d.date()), round(float(v), 0)) for d, v in total.sort_index().items()
               if pd.notna(v) and str(d.date()) < today]
        return out[-lookback:]
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 成交额历史获取失败(量能维降级静态阈值): {e}", file=sys.stderr)
        return []


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



def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


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

    # 2. 涨停池（梯队/炸板率/晋级率 — 2026-11-01 A阶段全市场口径真实化，替代中性占位）
    zt_pool, zt_date = fetch_zt_pool()
    tier_score, tier_note = score_tier(zt_pool)
    result["dimensions"]["tier_completeness"] = {"score": tier_score, "note": tier_note}

    zbgc_pool, zbgc_date = fetch_zbgc_pool()
    bomb_score, bomb_note = score_bomb(zt_pool, zt_date, zbgc_pool, zbgc_date)
    result["dimensions"]["bomb_rate"] = {"score": bomb_score, "note": bomb_note}

    prev_day = _prev_trading_day()
    prev_codes = []
    if prev_day:
        try:
            import akshare as ak
            prev_pool = ak.stock_zt_pool_em(date=prev_day.replace("-", ""))
            if prev_pool is not None and not prev_pool.empty and "代码" in prev_pool.columns:
                prev_codes = list(prev_pool["代码"].astype(str))
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 昨日涨停池获取失败(晋级率降级中性): {e}", file=sys.stderr)
    promo_score, promo_note = score_promotion(prev_codes, zt_pool,
                                               run_date=datetime.now().strftime("%Y-%m-%d"))
    result["dimensions"]["promotion_rate"] = {"score": promo_score, "note": promo_note,
                                              "prev_date": prev_day or "不可得"}

    # 3. 量能（2026-11-01 A阶段颗粒度：相对5日全市场均值，替代静态绝对阈值；
    #    口径：今日=新浪全A沪深成交额合计，历史=baostock 沪A sh.000002+深A sz.399107）
    _run_date = datetime.now().strftime("%Y-%m-%d")
    _is_td = True
    try:
        from analysis.service import is_trading_day
        _is_td = is_trading_day(_run_date)
    except Exception as _e:  # noqa: BLE001
        print(f"⚠️ 交易日判定失败(量能维保守按交易日处理): {_e}", file=sys.stderr)
    hist = fetch_amount_history()
    _vol_self_note = ""
    if hist and not _is_td:
        # 非交易日运行：spot total_amt 实为最近交易日数据 → 历史序列剔除该日，防自比虚高
        hist = hist[:-1]
        _vol_self_note = "; 非交易日运行已剔除自比日"
    if hist:
        last5 = hist[-5:]
        avg5 = round(sum(v for _, v in last5) / len(last5), 0)
        v_score, v_note = score_volume(total_amt, history={"amount_hist": avg5})
        vol_basis = f"5日全市场均值{avg5:.0f}亿(baostock 沪A+深A){_vol_self_note}"
    else:
        v_score, v_note = score_volume(total_amt, history=None)
        vol_basis = "静态阈值(5日均值不可得,显式降级)"
    result["dimensions"]["volume"] = {"score": v_score, "note": v_note,
                                      "basis": vol_basis, "amount_hist_5d": hist[-5:]}

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
    _bak(path)
    with open(path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # stdout
    print("=====MARKET_HEALTH_SCORE=====")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print("=====MARKET_HEALTH_END=====")
    return result


def _selftest() -> int:
    """对抗自测（无网络）：量能比值分档 / 炸板率 / 晋级率 / 日期错配防假信号 / 显式降级"""
    import pandas as pd

    # 1) 量能比值分档（5日均值接线后走 ratio 分支）
    t1 = (score_volume(13500, history={"amount_hist": 10000})[0] == 10
          and score_volume(10500, history={"amount_hist": 10000})[0] == 7
          and score_volume(8000, history={"amount_hist": 10000})[0] == 4
          and score_volume(6500, history={"amount_hist": 10000})[0] == 1)

    # 2) 量能无历史 → 静态阈值 fallback（显式降级注记）
    s, n = score_volume(12500, history=None)
    t2 = s == 6 and "无5日均值" in n

    # 3) 炸板率真实化：10-28 实盘口径 69/141≈49% → 1分；健康日 14/86≈16% → 10分
    s3a, _ = score_bomb(_mk_pool(72), "20261028", _mk_pool(69), "20261028")
    s3b, _ = score_bomb(_mk_pool(72), "20261030", _mk_pool(14), "20261030")
    t3 = s3a == 1 and s3b == 10

    # 4) 两池日期错配 → 中性分（防错日假信号）
    s4, n4 = score_bomb(_mk_pool(72), "20261030", _mk_pool(69), "20261029")
    t4 = s4 == 5 and "未对齐" in n4

    # 5) 双池均空 → 中性分
    s5, _ = score_bomb(None, None, None, None)
    t5 = s5 == 5

    # 6) 晋级率：10-28 实盘 8/41≈19.5% → 4分；3/41≈7% → 1分；昨日池不可得 → 中性；
    #    非交易日运行自比陷阱 → 显式中性（周日实跑坐实）
    today_pool = _mk_pool(8, codes=[f"60058{i}" for i in range(8)])
    prev_codes = [f"60058{i}" for i in range(41)]
    s6a, _ = score_promotion(prev_codes, today_pool, run_date="2026-10-30")
    s6b, _ = score_promotion(prev_codes[:41], _mk_pool(3, codes=prev_codes[:3]), run_date="2026-10-30")
    s6c, _ = score_promotion([], today_pool, run_date="2026-10-30")
    s6d, n6d = score_promotion(prev_codes, today_pool, run_date="2026-11-01")  # 周日
    t6 = s6a == 4 and s6b == 1 and s6c == 5 and s6d == 5 and "非交易日" in n6d

    cases = [("量能比值分档", t1), ("量能静态降级注记", t2), ("炸板率真实化", t3),
             ("两池错配防假信号", t4), ("双池空中性", t5), ("晋级率分档+非交易日拦截", t6)]
    failed = [n for n, ok in cases if not ok]
    for n, ok in cases:
        print(f"  {'✅' if ok else '❌'} {n}")
    if failed:
        print(f"SELFTEST FAILED: {failed}")
        return 1
    print("SELFTEST PASSED (6/6)")
    return 0


def _mk_pool(n, codes=None):
    """构造最小涨停/炸板池 df（列名对齐东财口径）"""
    if codes is None:
        codes = [f"{600000 + i}" for i in range(n)]
    return pd.DataFrame({"代码": codes[:n] if codes else [f"{600000 + i}" for i in range(n)]})


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    compute()
