#!/usr/bin/env python3
"""moneyflow 双路恢复验证 (10-09)

复测 stock_flow 是否从 10-08 全天双路失败中恢复。
- 实时链: push2 klt=1 (主源)
- 历史链: push2his daykline

输出结论 + 关键诊断信息。
"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def run_retry_multiple(times: int = 3) -> list:
    result = []
    for t in range(times):
        from analysis.moneyflow_em import _fflow_realtime, _fflow
        secid = "1.600030"
        rt = _fflow_realtime(secid)
        hist = _fflow(secid)
        if rt:
            result.append(("rt", rt["day"]["date"],
                           round(rt["day"]["main"] / 1e8, 2)))
        else:
            result.append(("rt", None, None))
        if hist:
            result.append(("hist_days", len(hist["days"]) if hist.get("days") else 0, None))
        else:
            result.append(("hist_days", None, None))
    return result


def main() -> str:
    # stock_flow public API (1.实时+历史双路)
    failed_runs = 0
    recovered_runs = 0
    detail_times = []
    for t in range(5):
        import time as _time
        from analysis.moneyflow_em import stock_flow
        r = None
        try:
            r = stock_flow("600030")
        except Exception as e:  # noqa: BLE001
            r = f"EXC:{e}"
        recovered = isinstance(r, dict) and r.get("days")
        if recovered:
            recovered_runs += 1
        else:
            failed_runs += 1
            detail_times.append(t + 1)
        _time.sleep(0.5)

    # 补充诊断：直接拆开实时历史两路
    diag = ""
    try:
        from analysis.moneyflow_em import _fflow_realtime, _get, _FF_RT_HOSTS, _FFLOW_HOSTS, _UT
        secid = "1.600030"
        rt = _fflow_realtime(secid)
        if rt:
            diag += f"\n[实时链] OK {rt['day']['date']} main={rt['day']['main']/1e8:+.2f}亿"
        else:
            # 深入查原因
            diag += "\n[实时链] FAIL"
            # 手动逐host
            host_detail = []
            for h in _FF_RT_HOSTS:
                url = (f"https://{h}/api/qt/stock/fflow/kline/get"
                       f"?lmt=0&klt=1&secid={secid}"
                       f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57&ut={_UT}")
                d = _get(url, tries=1, hosts=[h])
                if d and d.get("data"):
                    host_detail.append(f"{h}=OK")
                else:
                    host_detail.append(f"{h}=FAIL({(d or {}).get('_error', 'no-data')})")
            diag += " | " + " | ".join(host_detail)

        # 历史链
        from analysis.moneyflow_em import _fflow
        h = _fflow(secid)
        if h and h.get("days"):
            diag += f"\n[历史链] OK days={len(h['days'])} last={h['days'][-1]['date']} main={h['days'][-1]['main']/1e8:+.2f}亿"
        else:
            diag += "\n[历史链] FAIL"
    except Exception as e:  # noqa: BLE001
        diag += f"\n[诊断异常] {e}"

    verdict = (
        "moneyflow 已恢复，资金维可重新计入五维" if recovered_runs >= 3
        else "双路仍挂，今日选股推送继续用 core 三维半口径"
    )
    from datetime import datetime
    print(f"复测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (Asia/Shanghai)")
    print(f"5次采样: 恢复={recovered_runs} 失败={failed_runs} 失败次数位={detail_times}")
    print(f"代码版本: d230f48 (10-08)", end="")
    print(diag)
    print(f"\n结论: {verdict}")
    return verdict


if __name__ == "__main__":
    main()