#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三因素共振买入门控 —— 只读试跑版
==================================
目标：个股基本面 + 市场情绪 + 技术方向三个独立维度**共同指向同一方向**时才发买入信号，
避免单一技术信号接飞刀（如 2026-08-19 国瓷材料 5日-13.4% 仍被给"强烈买入"的教训）。

数据源（均已实测可用）：
  - 技术维度 : 新浪/baostock 日K -> service.calc_full_signal（复用系统统一口径）
  - 基本面维度: baostock query_profit_data / query_growth_data（ROE/净利增速/净利率）
  - 情绪维度 : akshare stock_market_activity_legu（涨跌家数/活跃度，全市场共享）

门控规则（保守，呼应"共同指向"严格定义）：
  BUY  = 技术分>=60 AND 基本面分>=40 AND 情绪分>=60
  得分 = 三因子加权（技术0.5 + 基本面0.25 + 情绪0.25）合成 0~100 共振分

用法：
  python3 analysis/three_factor_resonance.py            # 全30只试跑
  python3 analysis/three_factor_resonance.py --top 10   # 只打前排

输出：analysis/backtest/2026-08-19_三因素共振.md（只读报告，不改动任何交易/建议）
"""
import os, sys, json, argparse
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from analysis.service import get_daily_hist, calc_full_signal, WATCHLIST

# ---------------- 三因子权重 ----------------
W_TECH, W_FUND, W_SENT = 0.5, 0.25, 0.25
TECH_BUY, FUND_BUY, SENT_BUY = 60, 40, 60   # 各维度买入门槛


# ================= 情绪维度（全市场共享） =================
def sentiment_score() -> dict:
    """基于乐咕乐股涨跌家数/活跃度合成市场情绪分 0~100。全市场每日一个值。"""
    try:
        import akshare as ak
        df = ak.stock_market_activity_legu()
        kv = {str(r["item"]): r["value"] for _, r in df.iterrows()}
        up = _f(kv.get("上涨"))
        down = _f(kv.get("下跌"))
        active = _f(kv.get("活跃度"))
        stat = kv.get("统计日期", "")
        if up is None or down is None or up + down <= 0:
            return {"score": 50, "detail": {"error": "涨跌家数缺失"}, "date": stat}
        total = up + down
        up_ratio = up / total          # 上涨家数占比
        # 活跃度越高, 情绪越真实; 缺省给中值
        active_norm = (active / 100.0) if active is not None else 0.5
        # 情绪分: 上涨占比(70%) + 活跃度(30%) 映射到 0~100
        score = round(up_ratio * 70 + active_norm * 30, 1)
        return {
            "score": score,
            "detail": {"up": up, "down": down, "up_ratio": round(up_ratio, 3),
                       "active_pct": active, "date": stat},
        }
    except Exception as e:
        return {"score": 50, "detail": {"error": str(e)[:60]}, "date": ""}


def _f(v):
    try:
        if v is None or v == "" or v == "--":
            return None
        return float(str(v).replace("%", "").replace(",", ""))
    except Exception:
        return None


# ================= 技术维度（复用系统 calc_full_signal） =================
def tech_score(symbol: str, name: str) -> dict:
    """归一化 system 技术 score(-4~4) 到 0~100, 并提取破位信息供降级参考。"""
    df = get_daily_hist(symbol, name, start_date="20240101")
    if df is None or len(df) < 30:
        return {"score": None, "raw": None}
    sig = calc_full_signal(df)
    sc = sig["signal"]["score"]            # -4 ~ +4
    tech = max(0.0, min(100.0, (sc + 4) / 8.0 * 100.0))
    ind = sig["indicators"]
    broke_ema26 = ind["EMA26"] is not None and sig["price"] < ind["EMA26"]
    return {
        "score": round(tech, 1),
        "raw_score": sc,
        "level": sig["signal"]["level"].replace(" ", ""),
        "price": sig["price"],
        "change_pct": sig["change_pct"],
        "reasons": sig["signal"]["reasons"],
        "risks": sig["signal"]["risks"],
        "broke_ema26": broke_ema26,
        "indicators": {"RSI14": ind["RSI14"], "EMA12": ind["EMA12"], "EMA26": ind["EMA26"],
                       "MACD_bull": ind["MACD_bull"]},
    }


# ================= 基本面维度（baostock） =================
def _bs_bs_code(symbol: str) -> str:
    return ("sh." if symbol.startswith("6") else "sz.") + symbol


def _bs_ensure_login():
    """确保 baostock 会话有效；无效则重登。返回是否可用。"""
    import baostock as bs
    if getattr(bs, "_session_ok", False):
        return True
    try:
        lg = bs.login()
        ok = (lg.error_code == "0")
        bs._session_ok = ok
        return ok
    except Exception:
        getattr(bs, "_session_ok", None)
        try:
            bs._session_ok = False
        except Exception:
            pass
        return False


def _bs_mark_broken():
    """标记会话失效（丢包/异常），下次调用自动重登。"""
    import baostock as bs
    try:
        bs._session_ok = False
    except Exception:
        pass


def fetch_fund(symbol: str, name: str) -> dict:
    """取最新财务: 年化ROE / 净利增速 / 净利率。用最近季度找最近已披露的。"""
    import baostock as bs
    if not _bs_ensure_login():
        return {"roe": None, "yoy_pni": None, "np_margin": None, "stat": None, "pub": None}
    try:
        code = _bs_bs_code(symbol)
        best = {"roe": None, "yoy_pni": None, "np_margin": None, "stat": None, "pub": None}
        found_stat = None
        for q in range(4, 0, -1):
            try:
                rs = bs.query_profit_data(code=code, year=datetime.now().year, quarter=q)
                non_empty = False
                while (rs.error_code == "0") and rs.next():
                    row = rs.get_row_data()
                    if row and row[2]:
                        non_empty = True
                        pq = {rs.fields[i]: row[i] for i in range(len(rs.fields))}
                        roe = _f(pq.get("roeAvg"))
                        np_m = _f(pq.get("npMargin"))
                        found_stat = pq.get("statDate")
                        if roe is not None:
                            # baostock roeAvg 单季ROE, 年化×4近似(口径标注)
                            best["roe"] = round(roe * 4, 4)
                            best["np_margin"] = round(np_m * 100, 2) if np_m is not None else None
                            best["stat"] = found_stat
                            best["pub"] = pq.get("pubDate")
                if non_empty:
                    break
            except Exception:
                _bs_mark_broken()
                continue
        # 成长数据：segment 由 statDate 推断，最多查 statDate口碑 + 年报 两个点
        try:
            yq = _stat_to_yq(found_stat) if found_stat else (datetime.now().year, None)
            # 优先: [statDate年份, statDate季度]；次优先: statDate年份年报(Q4)；再退回前一年年报
            qs = []
            if yq[1] is None:
                qs.append((yq[0], 4))
            else:
                qs.append((yq[0], yq[1]))
                qs.append((yq[0], 4))
            qs.append((yq[0] - 1, 4))
            for yr, qq in qs:
                if yr <= 0:
                    continue
                try:
                    rsg = bs.query_growth_data(code=code, year=yr, quarter=qq)
                    got_one = False
                    while (rsg.error_code == "0") and rsg.next():
                        row = rsg.get_row_data()
                        if row and row[2]:
                            gq = {rsg.fields[i]: row[i] for i in range(len(rsg.fields))}
                            yoy = _f(gq.get("YOYPNI"))
                            if yoy is not None:
                                best["yoy_pni"] = round(yoy * 100, 2)
                                got_one = True
                                break
                    if got_one:
                        break
                except Exception:
                    _bs_mark_broken()
                    continue
        except Exception:
            pass
        return best

    except Exception as e:
        return {"roe": None, "yoy_pni": None, "np_margin": None, "stat": None, "pub": None}


def _stat_to_yq(stat: str):
    """2026-06-30 -> (2026, 2) ; 2026-12-31 -> (2026, 4)"""
    try:
        y = int(stat[:4])
        m = int(stat[5:7])
        return (y, (m + 2) // 3)
    except Exception:
        return (datetime.now().year, None)



def fundamental_score(all_fund: list, symbol: str) -> dict:
    """截面归一化: 对全池 ROE/净利增速/净利率 做 min-max, 加权合成 0~100。"""
    roes = [f["roe"] for f in all_fund if f["roe"] is not None]
    yoys = [f["yoy_pni"] for f in all_fund if f["yoy_pni"] is not None]
    npms = [f["np_margin"] for f in all_fund if f["np_margin"] is not None]

    def _norm(val, col):
        if val is None:
            return None
        if not col:
            return 50.0
        lo, hi = min(col), max(col)
        if hi <= lo:
            return 50.0
        return max(0.0, min(100.0, (val - lo) / (hi - lo) * 100.0))

    f = next((x for x in all_fund if x.get("_symbol") == symbol), {})
    s_roe = _norm(f.get("roe"), roes)
    s_yoy = _norm(f.get("yoy_pni"), yoys)
    s_npm = _norm(f.get("np_margin"), npms)
    # 三因子等权; 缺失项以 50(中性) 计, 不惩罚
    parts = [p for p in (s_roe, s_yoy, s_npm) if p is not None]
    score = round(sum(parts) / len(parts), 1) if parts else None
    return {
        "score": score, "roe": f.get("roe"), "roe_s": round(s_roe, 1) if s_roe else None,
        "yoy_pni": f.get("yoy_pni"), "yoy_s": round(s_yoy, 1) if s_yoy else None,
        "np_margin": f.get("np_margin"), "npm_s": round(s_npm, 1) if s_npm else None,
        "stat": f.get("stat"),
    }


# ================= 主流程 =================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30, help="显示前N只")
    args = ap.parse_args()

    date = datetime.now().strftime("%Y-%m-%d")
    print(f"🌐 三因素共振试跑 {date}  数据源: 技术(新浪/baostock) + 基本面(baostock) + 情绪(乐咕)")

    # baostock 全局登录
    import baostock as bs
    lg = bs.login()
    bs._session_ok = (lg.error_code == "0")
    print(f"🔑 baostock 登录: {'OK' if bs._session_ok else lg.error_msg}")

    # 1) 情绪（全市场共享）
    sent = sentiment_score()
    print(f"📊 市场情绪分: {sent['score']}  {sent['detail']}")

    # 2) 技术 + 基本面抓取
    techs = {}; funds = []
    import time
    total = len(WATCHLIST)
    consecutive_empty = 0
    for idx, (sym, name) in enumerate(WATCHLIST, 1):
        print(f"  [{idx}/{total}] {name}({sym}) ...", flush=True)
        t = tech_score(sym, name)
        techs[sym] = {"symbol": sym, "name": name, **t}
        # 基本面带重试 + 会话加固：连续空结果时强制重登刷新会话
        f = None
        for _attempt in range(3):
            _f = fetch_fund(sym, name)
            if _f.get("roe") is not None or _f.get("np_margin") is not None or _f.get("yoy_pni") is not None:
                f = _f; consecutive_empty = 0; break
            time.sleep(0.6)
            # 若连续多只拿不到基本面, 主动重登 baostock 会话
            if _attempt >= 1:
                import baostock as _bs
                try: _bs.logout()
                except Exception: pass
                try:
                    lg = _bs.login()
                    _bs._session_ok = (lg.error_code == "0")
                except Exception:
                    _bs._session_ok = False
        if f is None:
            f = fetch_fund(sym, name)
            consecutive_empty += 1
        f["_symbol"] = sym
        funds.append(f)
        # 连续失败累计 2 次后强制重登一次
        if consecutive_empty >= 2:
            import baostock as _bs2
            try: _bs2.logout()
            except Exception: pass
            try:
                lg = _bs2.login(); _bs2._session_ok = (lg.error_code == "0")
            except Exception:
                _bs2._session_ok = False
            consecutive_empty = 0

    # 3) 基本面截面归一化
    fund_scores = {f["_symbol"]: fundamental_score(funds, f["_symbol"]) for f in funds}

    # 4) 合成共振得分 + 门控
    rows = []
    for sym, name in WATCHLIST:
        t = techs.get(sym, {})
        f = fund_scores.get(sym, {})
        tech, fund, emo = t.get("score"), f.get("score"), sent.get("score")
        resonance = None
        verdict = "数据不足"
        if tech is not None and fund is not None:
            resonance = round(tech * W_TECH + fund * W_FUND + emo * W_SENT, 1)
            gate = tech >= TECH_BUY and fund >= FUND_BUY and emo >= SENT_BUY
            # 破位拦截: 技术强买但跌破EMA26, 强制降一级
            if gate and t.get("broke_ema26"):
                gate = False
                broke_note = "(破EMA26降级)"
            else:
                broke_note = ""
            if gate:
                verdict = "🟢 BUY 共振"
            elif tech >= 60 and (fund < FUND_BUY or emo < SENT_BUY):
                verdict = f"🟡 观望(技术可但非共振)"
            elif tech < TECH_BUY:
                verdict = "⚪ 弱/空(技术不足)"
            else:
                verdict = "⚪ 观望"
        elif tech is not None and fund is None:
            verdict = "⚪ 技术取到/基本面缺失"
        rows.append({
            "symbol": sym, "name": name, "price": t.get("price"),
            "tech": tech, "fund": fund, "sent": emo, "resonance": resonance,
            "level": t.get("level"), "broke": t.get("broke_ema26"), "verdict": verdict,
            "fund_detail": f,
        })

    # 排序: 共振分降序
    def _rk(r):
        return r["resonance"] if r["resonance"] is not None else -1
    rows.sort(key=_rk, reverse=True)

    # ---- 生成 Markdown 报告 ----
    lines = [f"# 三因素共振试跑报告 {date}", ""]
    lines.append(f"**数据源**: 技术(新浪/baostock) 基本面(baostock) 情绪(乐咕)")
    lines.append(f"**门控**: BUY = 技术≥{TECH_BUY} AND 基本面≥{FUND_BUY} AND 情绪≥{SENT_BUY}（严格共振，破EMA26降级）")
    lines.append(f"**共振分** = 技术×{W_TECH} + 基本面×{W_FUND} + 情绪×{W_SENT}")
    lines.append(f"")
    lines.append(f"📊 **市场情绪分** = **{sent['score']}**  ({sent['detail']})")
    lines.append("")
    lines.append("| # | 代码 | 名称 | 现价 | 技术 | 基本面 | 情绪 | 共振分 | 技术评级 | 判定 |")
    lines.append("|:--:|:--|:--|--:|--:|--:|--:|--:|:--|:--|")
    for i, r in enumerate(rows[:args.top], 1):
        rk = f"{r['resonance']:.0f}" if r["resonance"] is not None else "-"
        t = f"{r['tech']:.0f}" if r["tech"] is not None else "-"
        fu = f"{r['fund']:.0f}" if r["fund"] is not None else "-"
        get = r["verdict"]
        broke = "⬇" if r.get("broke") else ""
        lines.append(f"| {i} | {r['symbol']} | {r['name']} | {r['price'] if r['price'] is not None else '-'} | {t} | {fu} | {r['sent']:.0f} | {rk} | {r['level']}{broke} | {get} |")

    lines.append("")
    lines.append("### 基本面明细（ROE / 净利增速 / 净利率，截面归一）")
    lines.append("")
    for r in rows[:args.top]:
        fd = r.get("fund_detail") or {}
        st = f"({fd.get('stat')})" if fd.get("stat") else ""
        lines.append(f"- **{r['name']}** ({r['symbol']}): ROE={fd.get('roe')} 净利增速={fd.get('yoy_pni')} 净利率={fd.get('np_margin')} {st}")

    out = os.path.join(WORKSPACE, "analysis", "backtest", f"{date}_三因素共振.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"\n📄 报告已写入: {out}")
    try:
        import baostock as bs
        bs.logout()
    except Exception:
        pass
    print("-" * 100)
    print("\n".join(lines[:min(args.top + 6, len(lines))]))


if __name__ == "__main__":
    main()
