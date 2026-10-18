#!/usr/bin/env python3
"""对撞信号 U 型假设——样本监测器（2026-10-19 立项，三遗留 TODO 之①）。

背景
----
2026-10-18 对撞降档反事实（analysis/research/2026-10-18_gate_clash_downgrade_counterfactual.md）
撤回了 tech 降档建议，并把「对撞子集 fund+sent 结构为 U 型」**记为假设**：
  * 现状仅 41 条可评估样本，不足以支撑任何精细规则；
  * 待样本积累 >100 条后重测；若 U 型坐实，正确方向是「中 fs 档单独拦截」。

本脚本把该假设变成**可监测、可自动触发重测**的东西：
  1. 用与反事实报告**完全相同**的样本口径（共用 build_clash_samples，SSOT）计数；
  2. 按日追加样本量追踪（data/clash_sample_monitor.jsonl，幂等）；
  3. 样本 >100 时自动跑 U 型重测（三分位 + bootstrap CI），落盘重测报告；
  4. 评估日明细（dual_signal_eval_days.json）陈旧度可见（落后行情多少交易日）。

口径（已对 10-18 报告逐数校准，禁止改动）
----
* fs 合成 = 0.30·fund + 0.25·sent（门控贡献权重，去常数 tech 项）；
* 三分位切法 = fs 升序后 [0:n//3] / [n//3:2n//3] / 其余（41 样本下 = 13/13/15，
  与报告逐数一致：+2.18 / −3.40 / +1.82）；
* U 型坐实判据 = bootstrap 95% CI 满足 CI(低fs−中fs) > 0 且 CI(高fs−中fs) > 0
  （中档显著低于两端 → 「中档陷阱」确认）。

用法
----
  python3 scripts/clash_ushape_monitor.py             # 离线快跑（默认）
  python3 scripts/clash_ushape_monitor.py --replay    # 先重放刷新评估日明细（慢，需网络）

退出码: 0=正常（含重放失败降级但旧数据可用）；1=评估明细缺失/损坏（显式失败）。
"""
import os
import sys
import json
import subprocess
import statistics as st
from datetime import date

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.net_guard import install_default_timeout
install_default_timeout(15)

EVAL_JSON = os.path.join(WORKSPACE, "analysis/research/dual_signal_eval_days.json")
HISTORY_JSONL = os.path.join(WORKSPACE, "data/clash_sample_monitor.jsonl")
RETEST_DIR = os.path.join(WORKSPACE, "analysis/research")
REPLAY_SCRIPT = os.path.join(WORKSPACE, "scripts/dual_signal_event_study.py")
THRESHOLD = 100          # 10-18 报告定的重测触发线
BOOT_N = 10000
W_FUND, W_SENT = 0.30, 0.25   # 门控 fund/sent 贡献权重（与 f470472 门控标定一致）


def load_counterfactual_lib():
    """加载反事实脚本模块（共用样本构建逻辑，SSOT）。"""
    import importlib.util
    p = os.path.join(WORKSPACE, "scripts", "gate_clash_downgrade_counterfactual.py")
    spec = importlib.util.spec_from_file_location("gate_clash_counterfactual", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def market_latest_date(price_dir):
    """价格缓存跨票最新日（本体系最后一次成功拉到的行情日）。"""
    import pandas as pd
    latest = None
    if not os.path.isdir(price_dir):
        return None
    for fn in os.listdir(price_dir):
        if not fn.endswith(".pkl") or fn.startswith(("chip", "chipmetrics")):
            continue
        try:
            df = pd.read_pickle(os.path.join(price_dir, fn))
            d = str(df["date"].max())[:10] if "date" in df.columns and len(df) else None
            if d and (latest is None or d > latest):
                latest = d
        except Exception:
            continue
    return latest


def bootstrap_ci_mean_diff(a, b, n_boot=BOOT_N, seed=42):
    """mean(a)-mean(b) 的 bootstrap 95% CI。"""
    import random
    if len(a) < 5 or len(b) < 5:
        return None
    rng = random.Random(seed)
    diffs = []
    for _ in range(n_boot):
        ma = st.mean(rng.choice(a) for _ in a)
        mb = st.mean(rng.choice(b) for _ in b)
        diffs.append(ma - mb)
    diffs.sort()
    return [diffs[int(0.025 * n_boot)], diffs[int(0.975 * n_boot) - 1]]


def terciles_by_fs(clash):
    """fs 升序 → [0:n//3]/[n//3:2n//3]/其余 三组（与 10-18 报告校准一致）。"""
    xs = sorted(clash, key=lambda r: W_FUND * r["fund"] + W_SENT * r["sent"])
    t = len(xs) // 3
    return [xs[0:t], xs[t:2 * t], xs[2 * t:]]


def main():
    # ── 可选：先重放刷新评估日明细（数据供应，失败降级用旧数据 + 可见告警）──
    if "--replay" in sys.argv:
        print("[monitor] 重放刷新 dual_signal_eval_days.json（可能需要数分钟）...")
        try:
            r = subprocess.run(
                [sys.executable, REPLAY_SCRIPT],
                capture_output=True, text=True, timeout=900, cwd=WORKSPACE)
            tail = (r.stdout or "").strip().splitlines()[-3:]
            for line in tail:
                print(f"  [replay] {line}")
            if r.returncode != 0:
                print(f"⚠️ [monitor] 重放失败 rc={r.returncode}，降级用现有评估明细"
                      f"（err: {(r.stderr or '')[-200:]}）", flush=True)
        except subprocess.TimeoutExpired:
            print("⚠️ [monitor] 重放超时 900s，降级用现有评估明细", flush=True)
        except Exception as e:
            print(f"⚠️ [monitor] 重放异常: {e}，降级用现有评估明细", flush=True)

    if not os.path.exists(EVAL_JSON):
        print(f"❌ 评估日明细缺失: {EVAL_JSON}", flush=True)
        return 1
    try:
        rows = json.load(open(EVAL_JSON))
    except Exception as e:
        print(f"❌ 评估日明细损坏: {e}", flush=True)
        return 1

    # ── 样本构建（与反事实报告同口径，SSOT）──
    cfd = load_counterfactual_lib()
    fund_hist = {s: sorted(v, key=lambda x: x["pub"])
                 for s, v in json.load(open(cfd.FUND)).items()}
    breadth = json.load(open(cfd.BREADTH))
    prices = cfd.load_prices()
    IDX = {sym: {str(d)[:10]: k for k, d in enumerate(ser.index)}
           for sym, ser in prices.items()}
    bI, bR = cfd.build_baselines(prices)
    clash = cfd.build_clash_samples(fund_hist, breadth, prices, IDX, bI, bR)

    n = len(clash)
    by_year = {}
    for r in clash:
        by_year[r["year"]] = by_year.get(r["year"], 0) + 1
    eval_max = max((str(r.get("date") or "")[:10] for r in rows), default=None)
    mkt_max = market_latest_date(cfd.CACHE)
    stale = None
    if eval_max and mkt_max:
        try:
            from analysis.chip_freshness import lag_trading_days
            stale = lag_trading_days(eval_max, mkt_max)
        except Exception:
            stale = None

    # ── 现状三分位（报告性输出，无论是否达到阈值）──
    tstat = None
    if n >= 9:
        groups = terciles_by_fs(clash)
        sizes = [len(g) for g in groups]
        means = [round(st.mean([r["exR"] for r in g]), 2) if g else None for g in groups]
        tstat = {"sizes": sizes, "means_exR": means,
                 "caliber": "fs=0.30*fund+0.25*sent 升序 [0:n//3]/[n//3:2n//3]/其余"}

    # ── U 型重测（>100 条才触发；重测结论独立落盘）──
    retest = None
    if n > THRESHOLD:
        groups = terciles_by_fs(clash)
        exR = [[r["exR"] for r in g] for g in groups]
        ci_lm = bootstrap_ci_mean_diff(exR[0], exR[1])
        ci_hm = bootstrap_ci_mean_diff(exR[2], exR[1])
        u_hold = bool(ci_lm and ci_hm and ci_lm[0] > 0 and ci_hm[0] > 0)
        retest = {
            "date": date.today().isoformat(),
            "n": n,
            "tercile_sizes": [len(g) for g in groups],
            "tercile_means_exR": [round(st.mean(x), 3) if x else None for x in exR],
            "ci_low_minus_mid": [round(v, 3) for v in ci_lm] if ci_lm else None,
            "ci_high_minus_mid": [round(v, 3) for v in ci_hm] if ci_hm else None,
            "u_shape_confirmed": u_hold,
            "verdict": ("U型坐实（中fs档显著低于两端）→ 正确方向是「中fs档单独拦截」"
                        if u_hold else "U型未坐实（中档未显著低于两端）→ 维持 tech=90 现状，"
                        "任何单调降档仍不可用"),
            "criterion": "CI95(低fs-中fs)>0 且 CI95(高fs-中fs)>0，bootstrap 10000 次",
        }
        rpath = os.path.join(RETEST_DIR, f"clash_ushape_retest_{retest['date']}.json")
        with open(rpath, "w", encoding="utf-8") as f:
            json.dump({"hypothesis_source":
                       "analysis/research/2026-10-18_gate_clash_downgrade_counterfactual.md",
                       **retest}, f, ensure_ascii=False, indent=2)
        print(f"📄 重测报告落盘: {os.path.relpath(rpath, WORKSPACE)}")

    # ── 历史追踪（按日替换，幂等）──
    today = date.today().isoformat()
    rec = {"date": today, "n_clash": n, "threshold": THRESHOLD,
           "by_year": dict(sorted(by_year.items())),
           "eval_signal_max_date": eval_max, "market_latest_date": mkt_max,
           "eval_stale_trading_days": stale,
           "status": ("threshold_reached" if n > THRESHOLD else "accumulating"),
           "tercile_snapshot": tstat,
           "retest": ({k: retest[k] for k in ("verdict", "u_shape_confirmed")}
                      if retest else None)}
    try:
        prev = []
        if os.path.exists(HISTORY_JSONL):
            with open(HISTORY_JSONL) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        p = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(p, dict) and p.get("date"):
                        prev.append(p)
        prev = [p for p in prev if p.get("date") != today]
        prev.append(rec)
        with open(HISTORY_JSONL, "w", encoding="utf-8") as f:
            for p in prev:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"⚠️ 历史追踪写入失败（不阻断）: {e}")

    # ── 标记块输出 ──
    print("=====CLASH_MONITOR=====")
    print(f"n_clash={n}/{THRESHOLD} status={rec['status']}")
    print(f"by_year={json.dumps(dict(sorted(by_year.items())), ensure_ascii=False)}")
    print(f"eval_max={eval_max} market_max={mkt_max} "
          f"eval_stale_trading_days={stale}")
    if tstat:
        print(f"tercile sizes={tstat['sizes']} means_exR={tstat['means_exR']}")
    if retest:
        print(f"RETEST verdict={retest['verdict']}")
        print(f"RETEST ci(low-mid)={retest['ci_low_minus_mid']} "
              f"ci(high-mid)={retest['ci_high_minus_mid']}")
    else:
        need = THRESHOLD - n
        print(f"距重测触发还差 {need} 条（按当前积累速度无法预估，看 by_year 趋势）")
    print("=====CLASH_MONITOR_END=====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
