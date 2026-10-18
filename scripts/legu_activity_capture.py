#!/usr/bin/env python3
"""乐咕全市场活跃度——真实口径每日历史累积（2026-10-19 立项，三遗留 TODO 之③）。

背景
----
门控 alpha 分解（analysis/research/2026-10-18_gate_alpha_decomposition.md）遗留 TODO：
生产情绪维用乐咕**真实**活跃度，但该接口无公开历史，回测只能用 |涨幅|中位数代理，
口径不一 → 「活跃度维去留」悬而未决。data/market_breadth_history.json 只重建了
涨跌家数（up/down），活跃度仍是缺口。

本脚本把「待积累」变成自动积累：每个交易日收盘后抓一次乐咕快照，按 stat_date
幂等落盘。积累够历史后（建议 ≥1 年）即可做「活跃度维去留」的增量值检验。

口径
----
* 数据源: akshare stock_market_activity_legu()（与生产 three_factor_helper 同源同解析）
* 键: 源返回的「统计日期」（数据实际描述的交易日）；源缺失时退化为今天（仅交易日）
* 幂等: 同一 stat_date 重跑覆盖（值相同则无害）
* 显式失败: 抓取失败/空表/无上涨下跌字段 → exit 1（空产出显式失败原则，宁缺毋假）

产出
----
  data/market_activity_legu_history.json: {stat_date: {...raw + up_ratio + prod_sentiment}}
"""
import os
import sys
import json
import logging
from datetime import datetime, date

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.net_guard import install_default_timeout, network_timeout_guard
install_default_timeout(15)

OUT = os.path.join(WORKSPACE, "data", "market_activity_legu_history.json")
log = logging.getLogger("legu_capture")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# 源字段 → 存储字段（全保留，raw 优先）
FIELD_MAP = {
    "上涨": "up", "涨停": "limit_up", "真实涨停": "real_limit_up",
    "st st*涨停": "st_limit_up",
    "下跌": "down", "跌停": "limit_down", "真实跌停": "real_limit_down",
    "st st*跌停": "st_limit_down",
    "平盘": "flat", "停牌": "halt", "活跃度": "active_pct",
}


def _num(v):
    """与生产 three_factor_helper._ff 同口径：剥离 % 与逗号后转 float。"""
    try:
        if v is None or str(v).strip() in ("", "--"):
            return None
        return float(str(v).replace("%", "").replace(",", ""))
    except Exception:
        return None


def _norm_stat_date(v):
    """乐咕统计日期 → YYYY-MM-DD；解析失败返回 None。"""
    if v is None:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10] if "-" in s else s[:8], fmt).date().isoformat()
        except Exception:
            continue
    return None


def fetch_legu() -> dict:
    """抓取并解析乐咕快照（函数体内自带超时护栏，不依赖进程级默认值）。"""
    import akshare as ak
    import pandas as pd
    with network_timeout_guard(20):
        df = ak.stock_market_activity_legu()
    if df is None or len(df) == 0:
        raise RuntimeError("乐咕快照为空")
    kv = {str(r["item"]): r["value"] for _, r in df.iterrows()}
    rec = {}
    for src, dst in FIELD_MAP.items():
        if src in kv:
            rec[dst] = _num(kv[src])
    if rec.get("up") is None or rec.get("down") is None or rec["up"] + rec["down"] <= 0:
        raise RuntimeError(f"上涨/下跌字段缺失（keys={sorted(kv)[:8]}...）")
    total = rec["up"] + rec["down"]
    rec["up_ratio"] = round(rec["up"] / total, 4)
    # 生产口径复算（three_factor_helper.sentiment 同式）：缺失 ≠ 中性 → 不复算
    if rec.get("active_pct") is not None:
        rec["prod_sentiment"] = round(rec["up_ratio"] * 70 + (rec["active_pct"] / 100.0) * 30, 1)
    rec["stat_date"] = _norm_stat_date(kv.get("统计日期"))
    rec["source"] = "akshare stock_market_activity_legu"
    return rec


def main():
    rec = None
    try:
        rec = fetch_legu()
    except Exception as e:
        # 降级路径：无旧值可兜（一次性快照，过时不可补采）→ 显式失败
        print(f"❌ 乐咕活跃度抓取失败: {e}", flush=True)
        print("=====LEGU_ACTIVITY=====")
        print("status=failed")
        print("=====LEGU_ACTIVITY_END=====")
        return 1

    # stat_date：源优先；缺失时仅交易日可用今天兜底（非交易日无新数据可记）
    stat_date = rec.get("stat_date")
    if not stat_date:
        from analysis.service import is_trading_day
        if is_trading_day():
            stat_date = date.today().isoformat()
            rec["stat_date"] = stat_date
            rec["stat_date_fallback"] = "today"
        else:
            print("⚠️ 源无统计日期且今日非交易日 → 无可记数据，跳过", flush=True)
            print("=====LEGU_ACTIVITY=====")
            print("status=skipped (no stat_date, non-trading day)")
            print("=====LEGU_ACTIVITY_END=====")
            return 0

    hist = {}
    if os.path.exists(OUT):
        try:
            hist = json.load(open(OUT))
            if not isinstance(hist, dict):
                hist = {}
        except Exception as e:
            log.warning("历史文件损坏，重建（旧内容见 .bak）: %s", e)
            try:
                os.replace(OUT, OUT + ".bak")
            except Exception as e2:
                log.warning(".bak 备份失败（继续重建）: %s", e2)
            hist = {}

    rec["captured_at"] = datetime.now().isoformat(timespec="seconds")
    # 幂等比较剔除 captured_at（每次必变），只比对业务字段
    _strip = lambda d: {k: v for k, v in (d or {}).items() if k != "captured_at"}
    unchanged = _strip(hist.get(stat_date)) == _strip(rec)
    hist[stat_date] = rec
    hist = dict(sorted(hist.items()))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)

    print("=====LEGU_ACTIVITY=====")
    print(f"status={'unchanged' if unchanged else 'captured'}")
    print(f"stat_date={stat_date} up={rec['up']:.0f} down={rec['down']:.0f} "
          f"active_pct={rec.get('active_pct')} up_ratio={rec.get('up_ratio')} "
          f"prod_sentiment={rec.get('prod_sentiment')}")
    print(f"history_days={len(hist)} range={min(hist)}~{max(hist)}")
    print(f"out={os.path.relpath(OUT, WORKSPACE)}")
    print("=====LEGU_ACTIVITY_END=====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
