#!/usr/bin/env python3
"""竞价快照预测力研究（2026-10-17，议程第 4 项：闲置数据资产开发）。

问题: 9:25 竞价快照（高开幅度/量比）对当日与次日收益有无预测力？
数据: data/runtime/auction/*_9_25.json（2026-08-13~，55 快照 × 27~36 票），
      当日/次日收盘取 dual_signal_eval_days.json（同窗口含 Aug-Oct 2026）。
指标: gap=开盘涨幅(竞价价/昨收-1)；intraday=收盘/开盘-1；next=次日收盘/当日收盘-1。
口径: 分 gap 桶 × 量比档；统计纪律报 n/胜率。
产出: analysis/research/2026-10-17_auction_gap_study.json
"""
import os
import sys
import json
import glob
from collections import defaultdict
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

eval_rows = json.load(open(os.path.join(WORKSPACE, "analysis/research/dual_signal_eval_days.json")))
close_map = {}          # (sym, date) -> close
dates_by_sym = defaultdict(list)
for r in eval_rows:
    close_map[(r["symbol"], r["date"])] = r["close"]
    dates_by_sym[r["symbol"]].append(r["date"])
for s in dates_by_sym:
    dates_by_sym[s].sort()

def next_close(sym, d):
    ds = dates_by_sym.get(sym, [])
    for i, x in enumerate(ds):
        if x == d:
            return close_map[(sym, ds[i + 1])] if i + 1 < len(ds) else None
    return None

rows_out = []
files = sorted(glob.glob(os.path.join(WORKSPACE, "data/runtime/auction/*_9_25.json")))
used_files = 0
for f in files:
    date = os.path.basename(f).split("_")[0]
    try:
        d = json.load(open(f))
    except Exception:
        continue
    rws = d.get("rows") or []
    if isinstance(rws, str):
        rws = json.loads(rws)
    if not rws:
        continue
    used_files += 1
    for it in rws:
        sym = it.get("code")
        ap = it.get("auction_price")
        pc = it.get("pre_close")
        if not sym or not ap or not pc:
            continue
        dc = close_map.get((sym, date))
        nc = next_close(sym, date)
        gap = it.get("open_chg_pct")
        if gap is None:
            gap = round((ap / pc - 1) * 100, 2)
        rows_out.append({
            "date": date, "symbol": sym, "name": it.get("name"),
            "gap_pct": float(gap),
            "vol_ratio_pct": it.get("vol_ratio_pct"),
            "intraday": round(dc / ap - 1, 5) if dc else None,
            "next_day": round(nc / dc - 1, 5) if (dc and nc) else None,
            "limit_up": it.get("is_limit_up", False),
        })

def bucket(g):
    if g >= 2:
        return "高开≥2%"
    if g >= 0:
        return "高开0~2%"
    if g > -2:
        return "低开0~2%"
    return "低开≥2%"

def stats(v):
    v = [x for x in v if x is not None]
    if not v:
        return None
    n = len(v)
    return {"n": n, "mean": round(sum(v) / n, 5),
            "win": round(sum(x > 0 for x in v) / n, 3)}

out = {"date": "2026-10-17", "files": used_files, "rows": len(rows_out), "buckets": {}}
for b in ("高开≥2%", "高开0~2%", "低开0~2%", "低开≥2%"):
    sub = [r for r in rows_out if bucket(r["gap_pct"]) == b and not r["limit_up"]]
    out["buckets"][b] = {
        "count": len(sub),
        "intraday": stats([r["intraday"] for r in sub]),
        "next_day": stats([r["next_day"] for r in sub]),
    }
# 量比交互（高开0~2% 内: 放量 vs 缩量）
sub = [r for r in rows_out if bucket(r["gap_pct"]) == "高开0~2%" and r.get("vol_ratio_pct") is not None and not r["limit_up"]]
hi = [r for r in sub if r["vol_ratio_pct"] >= 50]
lo = [r for r in sub if r["vol_ratio_pct"] < 50]
out["vol_interaction"] = {
    "高开放量(量比≥50%)": {"n": len(hi), "intraday": stats([r["intraday"] for r in hi])},
    "高开缩量(量比<50%)": {"n": len(lo), "intraday": stats([r["intraday"] for r in lo])},
}
out["limit_up_rows"] = sum(1 for r in rows_out if r["limit_up"])

path = os.path.join(WORKSPACE, "analysis/research/2026-10-17_auction_gap_study.json")
_bak(path)
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)

print(f"[auction] 快照 {used_files} 个 | 评估 stock-days {len(rows_out)} | 涨停开 {out['limit_up_rows']} 行剔除")
for b, info in out["buckets"].items():
    idd, nd = info["intraday"], info["next_day"]
    s1 = f"盘中(开→收) {idd['mean']:+.2%}(胜率{idd['win']:.0%},n={idd['n']})" if idd else "n=0"
    s2 = f"次日 {nd['mean']:+.2%}(n={nd['n']})" if nd else ""
    print(f"[auction] {b}: {s1} | {s2}")
for k, v in out["vol_interaction"].items():
    if v["intraday"]:
        print(f"[auction] {k}: 盘中 {v['intraday']['mean']:+.2%}(n={v['intraday']['n']})")
print(f"[auction] 已写 {path}")
