#!/usr/bin/env python3
"""floor-90 判决计算器（DECISION_CALENDAR 轨道 1，协议 v1.1 双判据）。

机械执行预注册判决，杜绝「到时现场编标准」：
  就绪条件：自 2026-11-12 起 ≥20 个交易日 且 已结算触发批次 ≥3
  判据 A（相对质量）：median(ex5) > 0 且 按触发日聚类 bootstrap 95%CI 下界 > 0
  判据 B（现金基准）：median(ret5) > 0 且 按触发日聚类 bootstrap 95%CI 下界 > 0
  判决：双判据全过 → 立项 90→85 影子对照实验；任一不过 → 封案至 2027-Q1

用法:
  python3 scripts/floor_judgment.py             # 真实台账
  python3 scripts/floor_judgment.py --selftest  # 合成数据对抗自测
  python3 scripts/floor_judgment.py --json      # 输出 JSON（供周一审计消费）
"""
import os
import sys
import json
import argparse
import numpy as np

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LEDGER_PATH = os.path.join(WORKSPACE, "data", "universe", "floor_shadow_ledger.json")
START_DATE = "2026-11-12"
MIN_DAYS = 20
MIN_BATCHES = 3
BOOTSTRAP_B = 10000
SEED = 20261115


def day_cluster_ci(values_by_day: dict, stat="median"):
    """按触发日重采样的 cluster bootstrap CI（协议冻结口径）。

    values_by_day: {date: [ex5/ret5, ...]}
    返回 (point, lo, hi)：point = 全样本中位数。
    """
    days = sorted(values_by_day.keys())
    pools = [np.asarray(values_by_day[d], dtype=float) for d in days if len(values_by_day[d])]
    if not pools:
        return None, None, None
    allv = np.concatenate(pools)
    point = float(np.median(allv))
    rng = np.random.default_rng(SEED)
    n = len(pools)
    stats = np.empty(BOOTSTRAP_B)
    for b in range(BOOTSTRAP_B):
        pick = rng.integers(0, n, n)
        sample = np.concatenate([pools[i] for i in pick])
        stats[b] = np.median(sample)
    lo, hi = float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))
    return point, lo, hi


def collect(ledger):
    """从台账抽取已结算候选：{day: {"ex5": [...], "ret5": [...]}}，每候选 ex5 = ret5 − 当日池中位。"""
    by_day = {}
    for e in (ledger.get("entries") or []):
        if not e.get("settled"):
            continue
        d = e.get("date")
        pool_med = e.get("pool_median_ret5")
        ex5s, ret5s = by_day.setdefault(d, {"ex5": [], "ret5": []})["ex5"], by_day[d]["ret5"]
        for c in e.get("candidates") or []:
            r = c.get("ret5")
            if r is None:
                continue
            ret5s.append(float(r))
            if pool_med is not None:
                ex5s.append(float(r) - float(pool_med))
    return by_day


def judge(ledger, today=None, tdays=None):
    by_day = collect(ledger)
    batches = len(by_day)
    if tdays is None:
        from analysis.service import get_trading_days
        tdays = get_trading_days(START_DATE) or []
    days_elapsed = 0
    if today and tdays:
        days_elapsed = len([d for d in tdays if d <= today])
    ready = (days_elapsed >= MIN_DAYS) and (batches >= MIN_BATCHES)
    out = {"ready": ready, "days_elapsed": days_elapsed, "batches": batches,
           "n_samples": sum(len(v["ret5"]) for v in by_day.values())}
    if not by_day:
        out["verdict"] = "继续观察（无已结算批次）"
        return out
    ex5_pt, ex5_lo, ex5_hi = day_cluster_ci({d: v["ex5"] for d, v in by_day.items() if v["ex5"]})
    ret_pt, ret_lo, ret_hi = day_cluster_ci({d: v["ret5"] for d, v in by_day.items() if v["ret5"]})
    out.update({"ex5_median": ex5_pt, "ex5_ci": [ex5_lo, ex5_hi],
                "ret5_median": ret_pt, "ret5_ci": [ret_lo, ret_hi]})
    if not ready:
        out["verdict"] = f"继续观察（{days_elapsed}/{MIN_DAYS} 交易日, {batches}/{MIN_BATCHES} 批）"
        return out
    crit_a = (ex5_pt is not None and ex5_pt > 0 and ex5_lo is not None and ex5_lo > 0)
    crit_b = (ret_pt is not None and ret_pt > 0 and ret_lo is not None and ret_lo > 0)
    out["criteria"] = {"A_相对质量": crit_a, "B_现金基准": crit_b}
    if crit_a and crit_b:
        out["verdict"] = "✅ 双判据通过 → 立项 90→85 影子对照实验（先影子后实盘，实验期另行预注册）"
    else:
        out["verdict"] = "⛔ 封案至 2027-Q1（地板 90 维持，期间不再复议）"
    return out


def _synthetic_ledger(kind):
    """对抗自测数据：3 批 × 10 只。kind=pass → 双判据过；kind=fail → 判据 B 不过。"""
    if kind == "pass":
        rets = [[0.03, 0.05, 0.02, 0.04, 0.06, 0.01, 0.03, 0.05, 0.02, 0.04] for _ in range(3)]
        pool = [0.01] * 3
    else:  # 相对为正（跌得少）但绝对为负（大跌池）
        rets = [[-0.02, -0.03, -0.01, -0.04, -0.02, -0.05, -0.01, -0.03, -0.02, -0.04] for _ in range(3)]
        pool = [-0.12] * 3
    entries = []
    for i in range(3):
        cands = [{"symbol": f"S{i}{j:02d}", "ret5": rets[i][j], "maxdd5": None} for j in range(10)]
        entries.append({"date": f"2026-11-{12 + i:02d}", "settled": True,
                        "pool_median_ret5": pool[i], "candidates": cands})
    return {"entries": entries}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        # 8 项断言：装载/CI 方向/就绪门/双判据方向
        from analysis.service import get_trading_days
        tdays = get_trading_days(START_DATE) or []
        today = tdays[min(len(tdays) - 1, 25)] if tdays else None  # 26 交易日后 → 就绪
        ok_pass = judge(_synthetic_ledger("pass"), today=today, tdays=tdays)
        ok_fail = judge(_synthetic_ledger("fail"), today=today, tdays=tdays)
        assert ok_pass["ready"], "selftest: 就绪门失败"
        assert "立项" in ok_pass["verdict"], f"selftest: pass 场景应立项 → {ok_pass['verdict']}"
        assert ok_pass["ex5_median"] > 0 and ok_pass["ret5_median"] > 0
        assert "封案" in ok_fail["verdict"], f"selftest: fail 场景应封案 → {ok_fail['verdict']}"
        assert ok_fail["ex5_median"] > 0, "selftest: fail 场景 ex5 应为正（跌得少）"
        assert ok_fail["ret5_median"] < 0, "selftest: fail 场景 ret5 应为负（绝对亏）"
        not_ready = judge(_synthetic_ledger("pass"), today="2026-11-14", tdays=tdays)
        assert not not_ready["ready"] and "继续观察" in not_ready["verdict"]
        print("✅ selftest 7 断言全过（pass→立项 / fail(B不过)→封案 / 未就绪→观察）")
        return 0

    if not os.path.exists(LEDGER_PATH):
        print("⚠️ 影子台账不存在:", LEDGER_PATH)
        return 1
    ledger = json.load(open(LEDGER_PATH, encoding="utf-8"))
    res = judge(ledger)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("=== floor-90 判决计算器（协议 v1.1 双判据）===")
        print(f"就绪: {res['ready']} | 交易日 {res['days_elapsed']}/{MIN_DAYS} | 批次 {res['batches']}/{MIN_BATCHES} | 样本 {res['n_samples']}")
        if res.get("ex5_median") is not None:
            print(f"判据A ex5: median {res['ex5_median']:+.2%} CI [{res['ex5_ci'][0]:+.2%}, {res['ex5_ci'][1]:+.2%}]")
            print(f"判据B ret5: median {res['ret5_median']:+.2%} CI [{res['ret5_ci'][0]:+.2%}, {res['ret5_ci'][1]:+.2%}]")
        print("判决:", res["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
