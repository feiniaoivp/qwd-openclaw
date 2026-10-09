#!/usr/bin/env python3
"""One-off runner: full-WATCHLIST trader picks scoring for today's intraday report."""
import os, sys, json

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.service import WATCHLIST
from analysis.trader_stock_picks import get_trader_picks

if __name__ == "__main__":
    wl = list(WATCHLIST)
    if isinstance(wl, dict):
        wl = list(wl.items())
    picks = get_trader_picks(wl)
    # 口径头部（2026-10-09 五维改造）：消费方读榜前先看维度覆盖
    from collections import Counter
    dist = Counter(p.get("valid_dims", 0) for p in picks)
    header = " | ".join(f"{k}/5维:{v}只" for k, v in sorted(dist.items(), reverse=True))
    print(f"PICKS_DIM_COVERAGE: {header}")
    print("=====PICKS_JSON_BEGIN=====")
    print(json.dumps(picks, ensure_ascii=False, indent=1))
    print("=====PICKS_JSON_END=====")
