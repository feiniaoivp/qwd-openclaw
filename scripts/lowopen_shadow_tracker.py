#!/usr/bin/env python3
"""低开甜点影子跟踪器（2026-10-20 立项，commit 5fe3228 的正式建议）。

背景
----
2026-10-20_low_open_sweetspot 发现：分歧买信号次日温和低开 [-2%,-1%) 开盘执行
5 日超额 +2.81%（CI 显著，三年全正），且桶内差分证明是「信号×低开」交互效应。
但该结论来自多重比较下的历史切片 → 拍板进规则前需前瞻样本（影子跟踪 ≥4 周）。

设计
----
* 信号源 = 生产同链：`dual_signal_event_study` 的 classify/regime + `adaptive_dual`
  的 load_dual_strategy_map / fetch_data / SIGNAL_FUNCS（与 eval 面板同源防分叉）
* 只跟踪买侧类：分歧买 / 双买（对撞 n=41 无信息，不跟踪）
* 台账 data/lowopen_shadow_ledger.json（dict，key="信号日|代码"，幂等 upsert，
  原子写 tmp+rename）
* 三段成熟：signal（信号日 T 捕捉）→ gap（T+1 开盘后回填 gap/tier/oc1）
  → fwd5_oc（T+6 收盘后成熟）
* **捕捉时点纪律**：建议交易日 18:45+（baostock 日线就绪后）固定运行——
  信号分类对边界数据敏感（实测 10-19 翰宇：06:00 回填态 vs 完整 K 线态分类
  可翻转），运行时点漂移会造成假信号变更；台账记录 created_at 供审计
* **只向前积累，不回填历史**——历史口径归 research 面板（单一事实来源纪律）
* 台账是数据，不是决策：本脚本不做任何交易动作，不推送

用法
----
  python3 lowopen_shadow_tracker.py            # capture + report（默认）
  python3 lowopen_shadow_tracker.py --report   # 只出报告（不抓取）
"""
import os
import sys
import json
import time
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.net_guard import install_default_timeout
install_default_timeout(15)

import gap_tier_execution_test as gt          # tier_of（gap 分桶单一来源）
import dual_signal_event_study as des        # 信号链单一来源（classify/regime/策略函数）

LEDGER = os.path.join(WORKSPACE, "data", "lowopen_shadow_ledger.json")
REPORT = os.path.join(WORKSPACE, "analysis", "research", "lowopen_shadow_report.md")
BUY_CLASSES = ("分歧买", "双买")


def pd_notna(v):
    return v == v  # NaN != NaN


def load_ledger():
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            return json.load(f)
    return {}


def save_ledger(d):
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LEDGER)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def capture(ledger):
    t0 = time.time()
    dual = des.load_dual_strategy_map()
    new_signals, gap_filled, matured, skipped = 0, 0, 0, 0
    eval_fail, eval_ok = 0, 0
    cls_dist = {}
    no_open_col = set()
    last_dates = {}
    for sym, name in des.STOCKS:
        entry = dual.get(sym)
        if not entry or len(entry) < 2:
            skipped += 1
            continue
        try:
            df = des.fetch_data(sym, start=des.START)
        except Exception as e:
            print(f"⚠️ fetch 失败 {name}: {e}")
            skipped += 1
            continue
        if df is None or len(df) < des.WARMUP + 6:
            skipped += 1
            continue
        close = df["close"].astype(float).reset_index(drop=True)
        dates = df["date"].astype(str).reset_index(drop=True)
        has_open = "open" in df.columns
        if not has_open:
            no_open_col.add(sym)
        opens = df["open"].astype(float).reset_index(drop=True) if has_open else None
        n = len(close)
        pos = {d: k for k, d in enumerate(dates)}

        # ── 1) 信号捕捉：最新完整 bar（盘后/盘前运行=最近已收盘交易日）──
        i = n - 1
        d_t = str(dates.iloc[i])
        last_dates[sym] = d_t
        key = f"{d_t}|{sym}"
        if key not in ledger:
            slice_df = df.iloc[: i + 1]
            actions, ok = [], True
            for st in entry[:2]:
                fn = des.SIGNAL_FUNCS.get(st["strategy"])
                if fn is None:
                    ok = False
                    eval_fail += 1
                    print(f"⚠️ 未知策略 {name}/{st['strategy']}（SIGNAL_FUNCS 无映射）")
                    break
                try:
                    actions.append(fn(slice_df, params=st.get("params"))["action"])
                except Exception as e:
                    ok = False
                    eval_fail += 1
                    print(f"⚠️ 信号函数异常 {name}/{st['strategy']}: {type(e).__name__}: {e}")
                    break
            if ok and len(actions) == 2:
                eval_ok += 1
                cls = des.classify(actions[0], actions[1])
                cls_dist[cls] = cls_dist.get(cls, 0) + 1
                if cls in BUY_CLASSES:
                    er, adx_s, chop_s, gap_s = des.regime_series(df)
                    ledger[key] = {
                        "symbol": sym, "name": name, "signal_date": d_t, "cls": cls,
                        "actions": actions,
                        "strategies": [st["strategy"] for st in entry[:2]],
                        "close_t": round(float(close.iloc[i]), 4),
                        "er20": round(float(er.iloc[i]), 4) if er is not None and pd_notna(er.iloc[i]) else None,
                        "regime_er": ("trend" if (er is not None and pd_notna(er.iloc[i]) and float(er.iloc[i]) >= des.ER_TH) else "chop") if er is not None and pd_notna(er.iloc[i]) else None,
                        "chop_idx": round(float(chop_s.iloc[i]), 1),
                        "ema_gap_atr": round(float(gap_s.iloc[i]), 2),
                        "p1_chop": bool(chop_s.iloc[i] > des.CHOP_TH),
                        "p1_zone": bool(gap_s.iloc[i] < des.ZONE_TH),
                        "created_at": _now(),
                        "gap": None, "tier": None, "oc1": None, "fwd5_oc": None,
                    }
                    new_signals += 1

        # ── 2) 回填该票所有未成熟行（同一份 df，零额外取数）──
        for k2, row in ledger.items():
            if row.get("symbol") != sym:
                continue
            sd = row.get("signal_date")
            if sd not in pos:
                continue
            k = pos[sd]
            if row.get("gap") is None and opens is not None and k + 1 < n:
                row["gap"] = round(float(opens[k + 1] / close[k] - 1), 5)
                row["tier"] = gt.tier_of(row["gap"])
                row["oc1"] = round(float(close[k + 1] / opens[k + 1] - 1), 5)
                row["gap_filled_at"] = _now()
                gap_filled += 1
            if row.get("fwd5_oc") is None and opens is not None and k + 7 <= n:
                row["fwd5_oc"] = round(float(close[k + 6] / opens[k + 1] - 1), 5)
                row["matured_at"] = _now()
                matured += 1

    save_ledger(ledger)
    print(f"[capture] 台账 {len(ledger)} 行 | 新信号 {new_signals} / 补gap {gap_filled} / 成熟 {matured} | "
          f"跳过 {skipped} | 信号评估 ok {eval_ok} / fail {eval_fail} | {time.time()-t0:.0f}s")
    if cls_dist:
        print(f"[capture] 今日信号分布: {cls_dist}")
    if eval_fail:
        print(f"⚠️ 信号评估失败 {eval_fail} 只（上方有逐只原因）")
    if no_open_col:
        print(f"⚠️ 缺 open 列: {sorted(no_open_col)}")
    sig_dates = sorted({r.get("signal_date") for r in ledger.values()})
    if sig_dates:
        print(f"[capture] 信号日范围 {sig_dates[0]} ~ {sig_dates[-1]}")


def report(ledger):
    rows = list(ledger.values())
    gapped = [r for r in rows if r.get("gap") is not None]
    mature = [r for r in gapped if r.get("fwd5_oc") is not None]
    lines = [
        "# 低开甜点影子跟踪（前瞻样本台账）",
        "",
        f"- 更新: {_now()} | 台账 {len(rows)} 行（信号 {len(rows)} / 已补gap {len(gapped)} / 成熟 {len(mature)}）",
        "- 口径: 信号=生产同链（adaptive_dual 前2策略 as-of T）；gap=open(T+1)/close(T)−1；",
        "  ex5 口径不在此处计算（重归因仍用研究脚本），本报告只看前瞻绝对值与分桶。",
        "- **只向前积累，不回填历史**；历史结论见 2026-10-20_low_open_sweetspot.md",
        "",
    ]
    if not gapped:
        lines.append("（尚无已补 gap 的行——信号出现后次日开盘自动回填）")
    else:
        by_tier = {}
        for r in gapped:
            by_tier.setdefault(r.get("tier", "?"), []).append(r)
        lines += ["| tier | n(gap) | n(成熟) | oc1 均值 | fwd5_oc 均值(成熟) |", "|---|---|---|---|---|"]
        for t in ("gap<-1%", "-1%~0%", "0~+1%", "+1%~+3%", "gap>+3%"):
            sub = by_tier.get(t, [])
            if not sub:
                continue
            m = [r["fwd5_oc"] for r in sub if r.get("fwd5_oc") is not None]
            oc1s = [r["oc1"] for r in sub]
            f5 = f"{sum(m)/len(m)*100:+.2f}%" if m else "待成熟"
            lines.append(
                f"| {t} | {len(sub)} | {len(m)} | {sum(oc1s)/len(oc1s)*100:+.2f}% "
                f"| {f5} |")
        # 甜点档细分提示
        sweet = by_tier.get("gap<-1%", [])
        if sweet:
            fine = [r for r in sweet if -0.02 <= r["gap"] < -0.01]
            lines += ["", f"甜点档细分 [-2,-1): n={len(fine)}（成熟 {sum(1 for r in fine if r.get('fwd5_oc') is not None)}）"]
    with open(REPORT, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[report] 📄 {os.path.relpath(REPORT, WORKSPACE)}")
    for ln in lines[6:16]:
        print("  " + ln)


def main():
    ledger = load_ledger()
    if "--report" not in sys.argv:
        capture(ledger)
    report(ledger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
