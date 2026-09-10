#!/usr/bin/env python3
"""030复盘 — 一次性数据快照（复用 close_scan_v2 的函数）
运行: python3 analysis/review_030_snapshot.py   (非交易日手动跑, 取上一交易日收盘)
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("WORKSPACE", "/Users/duguke/.openclaw/workspace")
import pandas as pd
import akshare as ak
from datetime import datetime
import logging
logging.basicConfig(level=logging.CRITICAL)

from close_scan_v2 import calc_full_signal_whitelist as calc_full_signal, WATCHLIST

OUT = {}

def market_breadth(spot):
    """从新浪全量 spot 计算市场宽度 / 涨停跌停 / 涨跌家数(仅沪深, 排除北交所bj)"""
    df = spot.copy()
    # 代码形如 sh600000/sz000001/bj920000, 排除北交所
    df = df[df["代码"].str.match(r"^(sh|sz)")]
    df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce").fillna(0)
    df["最新价"] = pd.to_numeric(df["最新价"], errors="coerce")
    df["成交额"] = pd.to_numeric(df["成交额"], errors="coerce").fillna(0)

    # 涨停阈值: 主板/沪深300成分 10%, 创业板(30)/科创(68) 20%
    def is_limit_up(row):
        code = row["代码"][2:]
        if code.startswith(("30","68")):
            return row["涨跌幅"] >= 19.8
        return row["涨跌幅"] >= 9.9
    def is_limit_down(row):
        code = row["代码"][2:]
        if code.startswith(("30","68")):
            return row["涨跌幅"] <= -19.8
        return row["涨跌幅"] <= -9.9
    limit_up = int(df.apply(is_limit_up, axis=1).sum())
    limit_down = int(df.apply(is_limit_down, axis=1).sum())
    big_up = int((df["涨跌幅"] >= 19.8).sum())      # 20cm涨停(创业板/科创)

    up = int((df["涨跌幅"] > 0).sum())
    down = int((df["涨跌幅"] < 0).sum())
    flat = int((df["涨跌幅"] == 0).sum())
    total = len(df)
    avg_chg = round(float(df["涨跌幅"].mean()), 2)
    med_chg = round(float(df["涨跌幅"].median()), 2)
    total_amt = round(float(df["成交额"].sum()) / 1e8, 0)  # 亿元

    # 市场宽度分(030: 上涨>70%=10, 50-70=7, 30-50=4, <30=1)
    up_ratio = up / total if total else 0
    if up_ratio > 0.70: width_score = 10
    elif up_ratio > 0.50: width_score = 7
    elif up_ratio > 0.30: width_score = 4
    else: width_score = 1

    # 量能分(无5日均值, 用成交额数量级估算, 标注为主观)
    # 负反馈分(跌停数)
    if limit_down == 0: neg_score = 10
    elif limit_down <= 3: neg_score = 7
    elif limit_down <= 10: neg_score = 4
    else: neg_score = 1

    return {
        "up": up, "down": down, "flat": flat, "total": total,
        "limit_up": limit_up, "limit_down": limit_down, "big_up": big_up,
        "avg_chg": avg_chg, "med_chg": med_chg,
        "total_amount_yi": total_amt,
        "up_ratio": round(up_ratio, 4),
        "width_score": width_score,
        "neg_score": neg_score,
        "data_time": None,
    }

def main():
    print("📡 拉取新浪全量行情...", file=sys.stderr)
    spot = ak.stock_zh_a_spot()
    ts_col = None
    for c in ["时间", "数据时间", "更新时间"]:
        if c in spot.columns:
            ts_col = c; break
    if ts_col:
        OUT["data_time"] = str(spot[ts_col].iloc[0])
    print(f"  全市场 {len(spot)} 行", file=sys.stderr)

    OUT["breadth"] = market_breadth(spot)

    # 指数表现 (上证000001/创业板399006/科创50 000688) 用 hq.sinajs.cn (s_前缀), 最稳
    print("📡 计算指数与自选股信号...", file=sys.stderr)
    import urllib.request
    OUT["index"] = {}
    try:
        url = "http://hq.sinajs.cn/list=s_sh000001,s_sz399006,s_sh000688"
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        iraw = urllib.request.urlopen(req, timeout=10).read().decode("gbk")
        idx_name = {"上证指数": "上证指数", "创业板指": "创业板指", "科创50": "科创50"}
        for line in iraw.splitlines():
            val = line.split('"')[1].split(',')
            if len(val) < 6:
                continue
            nm = val[0]
            if nm in idx_name:
                OUT["index"][idx_name[nm]] = {
                    "price": float(val[1]),
                    "change_pct": float(val[3]),
                    "amount_yi": round(float(val[5])/10000, 0),  # 指数量: 手→(万元), /10000=亿
                }
    except Exception as e:
        print(f"⚠️ 指数拉取失败: {e}", file=sys.stderr)

    OUT["stocks"] = {}  # 初始化
    try:
        # ── 自选股: 技术指标优先用历史日线(baostock前复权) ──
        print("📡 下载31只历史日线(baostock前复权)...", file=sys.stderr)
        import baostock as bs
        bs.login()
        for code, name in WATCHLIST:
            symbol = f"sh.{code}" if code[0] in "69" else f"sz.{code}"
            try:
                rs = bs.query_history_k_data_plus(symbol, "date,close,volume",
                    start_date="20250601", end_date=datetime.now().strftime("%Y-%m-%d"),
                    frequency="d", adjustflag="2")
                rows = []
                while (rs.error_code == '0') and rs.next():
                    rows.append(rs.get_row_data())
                bs.logout()
                bs.login()
                if not rows:
                    continue
                df = pd.DataFrame(rows, columns=["date","close","volume"])
                df["close"] = pd.to_numeric(df["close"]); df["volume"]=pd.to_numeric(df["volume"])
                df["date"] = pd.to_datetime(df["date"])
                sig = calc_full_signal(df)
                sig["name"] = name
                OUT["stocks"][code] = sig
            except Exception as e:
                OUT["stocks"][code] = {"name": name, "error": str(e), "source": "baostock"}
        bs.logout()
    except Exception as e:
        print(f"⚠️ baostock 整体失败({e}), 用hq兜底", file=sys.stderr)

    # ── hq兜底: 未给出信号的股票补涨跌幅(hq实时收盘, 周末取最近交易日) ──
    import urllib.request
    pending = [(c, n) for c, n in WATCHLIST if c not in OUT["stocks"] or "error" in OUT["stocks"].get(c, {}).get("error", "x")]
    if pending:
        print(f"📡 hq兜底拉{len(pending)}只...", file=sys.stderr)
        codes = [("sh" if c[0] in "69" else "sz") + c for c, _n in pending]
        url = "http://hq.sinajs.cn/list=" + ",".join(codes)
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        raw = urllib.request.urlopen(req, timeout=12).read().decode("gbk")
        for line in raw.splitlines():
            val = line.split('"')[1].split(',')
            if len(val) < 10: continue
            key = line.split("hq_str_")[1].split("=")[0]
            code = key[2:]
            name = val[0]
            prev = float(val[2]) if val[2] else 0; last = float(val[3]) if val[3] else 0
            chg = round((last/prev-1)*100, 2) if prev else 0
            OUT["stocks"][code] = {
                "name": name, "price": last, "change_pct": chg,
                "pre_close": prev, "amount": float(val[9]) if val[9] else 0,
                "source": "hq", "data_date": val[30] if len(val)>30 else "",
                "signal": {"level": "⚪ hq快速", "note": "baostock不可用, 仅涨跌幅"},
            }
    OUT["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    with open(os.path.join(os.environ["WORKSPACE"], "data", "review_030_snapshot.json"), "w") as f:
        json.dump(OUT, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已保存 data/review_030_snapshot.json", file=sys.stderr)

    # 简洁打印
    b = OUT["breadth"]
    print(f"\n===== 030复盘快照 {OUT.get('data_time')} =====")
    print(f"市场宽度: 涨{b['up']} 跌{b['down']} 平{b['flat']} / 共{b['total']}")
    print(f"涨停≈{b['limit_up']}(含20cm {b['big_up']}) 跌停={b['limit_down']}")
    print(f"涨跌中位数 {b['med_chg']}% 平均 {b['avg_chg']}% 两市成交≈{b['total_amount_yi']}亿")
    print(f"\n指数:")
    for k, v in OUT["index"].items():
        print(f"  {k}: {v['price']} ({v['change_pct']:+.2f}%) 成交{v['amount_yi']}亿")
    print(f"\n自选股(涨跌幅/信号/RSI):")
    for code, s in OUT["stocks"].items():
        if "error" in s:
            print(f"  {s.get('name', code)}({code}): ERROR {s['error']} src={s.get('source','?')}")
            continue
        ind = s.get("indicators", {})
        if s.get("source") == "hq":
            print(f"  {s.get('name', code)}({code}): {s['change_pct']:+.2f}% [hq兜底] 日期{s.get('data_date','')} 额{s.get('amount',0)/1e8:.1f}亿")
            continue
        print(f"  {s['name']}({code}): {s['change_pct']:+.2f}% 量比{ind.get('vol_ratio',0):.2f} "
              f"RSI{ind.get('RSI14',0):.1f} {'MACD金叉' if ind.get('MACD_cross_up') else ('多头' if ind.get('MACD_bull') else '空头')} "
              f"{'EMA多' if ind.get('EMA_bull') else 'EMA空'} {s['signal']['level']}")

if __name__ == "__main__":
    main()
