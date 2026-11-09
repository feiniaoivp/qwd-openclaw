#!/usr/bin/env python3
"""
L2 扫描池行情数据补拉 — 为 universe_filter L2 产出的 scan_universe 补齐日线 CSV
==============================================================================
数据源: baostock (qfq, 与 stock_data 现有口径一致)
增量: 已有 CSV 且最新日期 ≥ as_of 的跳过
产出: data/stock_data/{sym}.csv (date,open,high,low,close,volume,amount,turn)
运行: nohup python3 scripts/fill_universe_csv.py > logs/fill_universe_csv.log 2>&1 &
"""
#!/usr/bin/env python3
import os
import sys
import json
import time
from datetime import datetime, timedelta

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

STOCK_DIR = os.path.join(WORKSPACE, "data", "stock_data")
L2_PATH = os.path.join(WORKSPACE, "data", "universe", "universe_core.json")
PROGRESS_PATH = os.path.join(WORKSPACE, "data", "universe", "fill_csv_progress.json")
START = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")


def _last_trade_day(days_back=1):
    d = datetime.now() - timedelta(days=days_back)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _bs_login_with_retry(max_attempts: int = 5):
    import time
    import baostock as bs
    last = None
    for i in range(max_attempts):
        try:
            lg = bs.login()
            if lg.error_code == "0":
                return True
            last = lg.error_msg
        except Exception as e:
            last = str(e)[:80]
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"baostock login 失败(重试{max_attempts}次): {last}")


def main():
    import time
    import baostock as bs
    import pandas as pd

    l2 = json.load(open(L2_PATH, encoding="utf-8")) if os.path.exists(L2_PATH) else None
    if not l2:
        print("❌ universe_core.json 缺失，先跑 universe_filter --l1 --l2")
        return
    symbols = l2["scan_universe"]
    names = l2.get("names", {})
    as_of = l2["as_of"]
    progress = json.load(open(PROGRESS_PATH, encoding="utf-8")) if os.path.exists(PROGRESS_PATH) else {"done": {}, "failed": {}}
    done, failed = progress.get("done", {}), progress.get("failed", {})

    todo = []
    for sym in symbols:
        fp = os.path.join(STOCK_DIR, f"{sym}.csv")
        if os.path.exists(fp):
            try:
                df = pd.read_csv(fp)
                last = str(df["date"].iloc[-1])[:10] if "date" in df.columns and len(df) else ""
                if last >= as_of and len(df) > 100:
                    continue
            except Exception:
                pass
        todo.append(sym)
    print(f"待补拉: {len(todo)}/{len(symbols)} 只 (as_of={as_of})")

    _bs_login_with_retry()
    os.makedirs(STOCK_DIR, exist_ok=True)
    t0 = time.time()
    n_since_login = 0

    def _pull(sym, bs):
        bs_code = ("sh." if sym.startswith("6") else "sz.") + sym
        rs = bs.query_history_k_data_plus(
            bs_code, "date,open,high,low,close,volume,amount,turn",
            start_date=START, end_date=as_of, frequency="d", adjustflag="2")
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        return rows

    try:
        for i, sym in enumerate(todo):
            try:
                rows = _pull(sym, bs)
                if len(rows) < 100:  # 疑似限流: 等 2s 重试一次
                    time.sleep(2)
                    rows = _pull(sym, bs)
                if len(rows) < 100:
                    failed[sym] = f"rows={len(rows)}"
                else:
                    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount", "turn"])
                    for c in df.columns[1:]:
                        df[c] = pd.to_numeric(df[c], errors="coerce")
                    df = df.dropna(subset=["close"])
                    if len(df) >= 100:
                        fp = os.path.join(STOCK_DIR, f"{sym}.csv")
                        if os.path.exists(fp):
                            os.replace(fp, fp + ".pre-universe.bak")
                        df.to_csv(fp, index=False)
                        done[sym] = {"rows": len(df), "at": datetime.now().isoformat()}
                        failed.pop(sym, None)
                    else:
                        failed[sym] = f"有效行不足 {len(df)}"
            except Exception as e:
                failed[sym] = str(e)[:80]
            n_since_login += 1
            if n_since_login >= 300:  # 周期重登防限流
                try:
                    bs.logout()
                except Exception:
                    pass
                _bs_login_with_retry()
                n_since_login = 0
                print(f"  🔄 会话重登 @ {i+1}")
            time.sleep(0.15)  # 防限流间隔（baostock 高频查询~200次后静默返空）
            if (i + 1) % 50 == 0:
                el = time.time() - t0
                print(f"进度 {i+1}/{len(todo)} | 成功 {len(done)} 失败 {len(failed)} | {el:.0f}s")
                json.dump({"done": done, "failed": failed}, open(PROGRESS_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    finally:
        bs.logout()
        json.dump({"done": done, "failed": failed}, open(PROGRESS_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"✅ 补拉完成: 成功 {len(done)} 失败 {len(failed)} | 耗时 {time.time()-t0:.0f}s")
    if failed:
        print("失败样例:", dict(list(failed.items())[:10]))


if __name__ == "__main__":
    main()
