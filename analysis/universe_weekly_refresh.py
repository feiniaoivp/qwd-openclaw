#!/usr/bin/env python3
"""
Universe 周更 — 指数成分+流动性+CSV 全量刷新（每周六 06:00 cron）
=================================================================
步骟: 1) 重拉4大指数成分股  2) 增量刷流动性  3) L1/L2 重建  4) CSV 补拉
产出: data/index_constituents/*.json + universe_l1/l2.json + stock_data/*.csv
"""
#!/usr/bin/env python3
import os
import sys
import json
import subprocess
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

IDX_DIR = os.path.join(WORKSPACE, "data", "index_constituents")
INDICES = [("000300", "hs300"), ("000905", "zz500"), ("000852", "zz1000"), ("000016", "sz50")]
summary = {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "steps": {}}


def run(cmd, timeout=3600):
    r = subprocess.run(cmd, shell=True, cwd=WORKSPACE, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "")[-500:], (r.stderr or "")[-200:]


def main():
    # 1) 指数成分重拉（中证官网，月度变动足够）
    import akshare as ak
    ok_n = 0
    for code, name in INDICES:
        try:
            df = ak.index_stock_cons_csindex(code)
            syms = df["成分券代码"].astype(str).str.zfill(6).tolist()
            names = dict(zip(df["成分券代码"].astype(str).str.zfill(6), df["成分券名称"]))
            out = {"fetched_at": summary["date"], "index_code": code,
                   "count": len(syms), "symbols": syms, "names": names}
            with open(os.path.join(IDX_DIR, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False)
            ok_n += 1
        except Exception as e:
            summary["steps"][f"idx_{name}"] = f"error:{str(e)[:60]}"
    summary["steps"]["indices"] = f"{ok_n}/4"
    print(f"✅ 指数成分刷新: {ok_n}/4")

    # 2) 全市场名单 + 流动性增量（baostock，含登录重试/限流间隔，~20min）
    rc, out, err = run(f"{sys.executable} analysis/universe_filter.py --refresh-basic && "
                       f"{sys.executable} analysis/universe_filter.py --refresh-liq", timeout=3600)
    summary["steps"]["liq_refresh"] = f"rc={rc}"
    print(out[-300:] if out else err)

    # 3) L1/L2 重建
    rc, out, err = run(f"{sys.executable} analysis/universe_filter.py --l1 && "
                       f"{sys.executable} analysis/universe_filter.py --l2", timeout=600)
    summary["steps"]["l1l2"] = f"rc={rc}"
    print(out[-300:] if out else err)

    # 4) CSV 增量补拉（新入池的票）
    rc, out, err = run(f"{sys.executable} scripts/fill_universe_csv.py", timeout=7200)
    summary["steps"]["csv_fill"] = f"rc={rc}"
    print(out[-300:] if out else err)

    with open(os.path.join(WORKSPACE, "data", "universe", "weekly_refresh_log.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("=====WEEKLY_REFRESH_DONE=====")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
