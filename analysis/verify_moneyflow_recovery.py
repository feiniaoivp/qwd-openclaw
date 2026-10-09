#!/usr/bin/env python3
"""moneyflow三通道恢复验证 (2026-10-09)

复测资金流取数可用性，并**按数据源**区分口径（关键：能取到数 ≠ 主源已恢复）。

三通道：
  1. push2 fflow/kline?klt=1        实时快照（主源）
  2. push2his fflow/daykline      历史多日（次源）
  3. datacenter-web RPT_DMSK_TS_STOCKNEW 当日兜底（2026-10-09 新增）

判定口径：
  - 对外可用性 = stock_flow 能否拿到 days（消费方只看这个）
  - 主源健康度 = _source 是否为 rt_klt1/daykline（决定"几日口径"与拆分完整性）
  - 仅兜底可用 → 资金维可入排序，但推送必须标「半口径（仅当日、无中/小单）」

输出结论 + 逐通道诊断。退出码：0=至少一路可取数；1=三路全空。
"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import logging
import subprocess
from datetime import datetime

log = logging.getLogger("verify_moneyflow")


def _git_rev() -> str:
    """当前代码版本（禁止硬编码——旧版写死 d230f48 曾误导排查）。"""
    try:
        r = subprocess.run(["git", "-C", WORKSPACE, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "unknown"
    except Exception as e:  # noqa: BLE001
        log.warning("git rev 获取失败: %s", e)
        return "unknown"


def _probe_channels(secid: str = "1.600030") -> dict:
    """逐通道探测（绕过熔断，直接打各源）。"""
    from analysis.moneyflow_em import (
        _fflow_realtime, _get, _FF_RT_HOSTS, _UT, _dc_fund_flow, _FFLOW_FIELDS,
    )
    out = {}

    rt = _fflow_realtime(secid)
    out["rt_klt1"] = bool(rt)

    url = (f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
           f"?lmt=5&klt=101&secid={secid}"
           f"&fields1=f1,f2,f3,f7&fields2={_FFLOW_FIELDS}&ut={_UT}")
    d = _get(url, tries=1, hosts=["push2his.eastmoney.com"])
    out["daykline"] = bool(d and d.get("data"))

    dc = _dc_fund_flow(secid)
    out["datacenter"] = bool(dc and dc.get("days"))
    return out


def main() -> int:
    import time as _time
    from analysis.moneyflow_em import stock_flow, _ff_push2_down, _dc_fund_flow

    n = 5
    usable = 0
    src_hits = {"rt_klt1": 0, "daykline": 0, "datacenter_dc": 0, "unknown": 0}
    for _ in range(n):
        try:
            r = stock_flow("600030")
        except Exception as e:  # noqa: BLE001
            log.warning("stock_flow 抛异常: %s", e)
            r = None
        if isinstance(r, dict) and r.get("days"):
            usable += 1
            src = r["days"][-1].get("_source") or "unknown"
            src_hits[src if src in src_hits else "unknown"] += 1
        _time.sleep(0.5)

    # 熔断状态单独报出：熔断打开期间 rt/daykline 会被**有意跳过**，
    # 此时"主源失败"是熔断结果而非上游状态，不能据此判上游全挂。
    cb_open = _ff_push2_down()
    chans = _probe_channels()

    primary_ok = chans["rt_klt1"] or chans["daykline"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"复测时间: {now} (Asia/Shanghai)")
    print(f"代码版本: {_git_rev()}")
    print(f"{n}次采样 stock_flow: 可取数={usable}/{n} 来源分布={src_hits}")
    print(f"熔断状态: {'OPEN(跳过 push2，直接兜底)' if cb_open else 'CLOSED(正常探测 push2)'}")
    print("逐通道直探: " + " | ".join(
        f"{k}={'OK' if v else 'FAIL'}" for k, v in chans.items()))

    if usable == 0:
        verdict = "❌ 三路全空 → 资金维今日剔除，推送标「资金流不可用」"
        code = 1
    elif primary_ok:
        verdict = "✅ push2 主源已恢复 → 资金维可恢复全口径（多日 + 中/小单拆分）"
        code = 0
    else:
        verdict = ("🟡 仅 datacenter 兜底可取数 → 资金维**可**入排序，"
                   "但推送须标「半口径：仅当日、无中/小单、cum 为单日值」")
        code = 0

    print(f"\n结论: {verdict}")
    return code


if __name__ == "__main__":
    sys.exit(main())
