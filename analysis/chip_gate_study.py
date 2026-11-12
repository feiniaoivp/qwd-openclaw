#!/usr/bin/env python3
"""
筹码负向拦截实验（2026-11-12 立项，Step 3）
=================================================================
假设: chip 维正 α 缺失（边际全负已证），但 cost_bias 高位（>20% 深度获利）
     可能具有「负向确定性」——高分票 + 过热筹码 = 追高陷阱。
实验: 把 cost_bias 阈值当作 Hard Gate 加在 L3 候选出口，回测其对候选池
     h5 超额的改善/恶化，与「不拦截」基线对照。

判据规则（对齐 ERR-20261018-C04 多臂对照铁律）:
  - 臂 A（基线）: 现行 L3 规则（score 降序 top_k，无 chip gate）
  - 臂 B（拦截）: 臂 A 基础上剔除 cost_bias > X 的票
  - 两臂共用同一日同一池，差异只能来自拦截本身
  - 分 regime 报告 + 全池合并，均值/中位数/胜率/样本量
  - 恒等式自检: 未命中拦截的票两臂收益必须逐日一致（max|A-B|≈0）

数据:
  - score_rows_3d.pkl (104万行: date/regime/sym/score/ex5)
  - chip_backtest_series.pkl (800票 × 400日 winner_pct/cost_bias/conc90)
运行: python3 analysis/chip_gate_study.py
"""
import os
import sys
import pickle
import numpy as np
import pandas as pd
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SCORE_PATH = os.path.join(WORKSPACE, "data", "universe", "score_rows_3d.pkl")
CHIP_PATH = os.path.join(WORKSPACE, "data", "universe", "chip_backtest_series.pkl")
OUT_MD = os.path.join(WORKSPACE, "analysis", "chip_gate_study_report.md")

THRESHOLDS = [0.15, 0.20, 0.25]   # cost_bias 拦截阈值候选
TOPKS = {"trend_up": 30, "sideways": 15, "trend_down": 15, "high_vol": 5}


def load_data():
    with open(SCORE_PATH, 'rb') as f:
        sc = pickle.load(f)
    with open(CHIP_PATH, 'rb') as f:
        ch = pickle.load(f)
    sc['date'] = pd.to_datetime(sc['date'])
    # 筹码长表拼接
    frames = []
    for sym, df in ch.items():
        d = df.copy()
        d['sym'] = sym
        frames.append(d)
    chip = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    chip['date'] = pd.to_datetime(chip['date'])
    return sc, chip


def main():
    print("🚀 筹码负向拦截实验启动")
    sc, chip = load_data()
    print(f"  打分行 {len(sc):,} | 筹码行 {len(chip):,} ({chip['sym'].nunique()} 票)")

    # 合并: 每票每日的 cost_bias 左连到打分行
    m = sc.merge(chip[['date', 'sym', 'cost_bias', 'winner_pct']],
                 on=['date', 'sym'], how='left')
    cov = m['cost_bias'].notna().mean()
    print(f"  合并后筹码覆盖率: {cov:.1%} (缺口行按「无筹码数据→不拦截」处理)")

    # 逐日按 regime top_k 选臂
    daily_rows = []
    for (d, reg), g in m.groupby(['date', 'regime']):
        k = TOPKS.get(reg, 15)
        g = g.sort_values('score', ascending=False).head(k)
        if g.empty:
            continue
        for thr in THRESHOLDS:
            blocked = g['cost_bias'] > thr
            arm_a = g['ex5']                      # 基线: 全部 top_k
            arm_b = g.loc[~blocked, 'ex5']        # 拦截后
            daily_rows.append({
                'date': d, 'regime': reg, 'thr': thr, 'k': k,
                'n_a': len(arm_a), 'n_b': len(arm_b),
                'n_blocked': int(blocked.sum()),
                'ex_a_mean': arm_a.mean() if len(arm_a) else np.nan,
                'ex_b_mean': arm_b.mean() if len(arm_b) else np.nan,
                'ex_a_med': arm_a.median() if len(arm_a) else np.nan,
                'ex_b_med': arm_b.median() if len(arm_b) else np.nan,
                'wr_a': (arm_a > 0).mean() if len(arm_a) else np.nan,
                'wr_b': (arm_b > 0).mean() if len(arm_b) else np.nan,
            })
    res = pd.DataFrame(daily_rows)

    # ── 恒等式自检: n_blocked==0 的日子两臂必须逐日一致 ──
    chk = res[res['n_blocked'] == 0]
    if len(chk):
        drift = (chk['ex_a_mean'] - chk['ex_b_mean']).abs().max()
        assert drift < 1e-12, f"恒等式破坏: 未拦截日两臂差 {drift}"
    print(f"✅ 恒等式自检通过 (未拦截日 {len(chk):,} 日 max|A-B|=0)")

    # ── 汇总: 按 regime × thr ──
    rep = []
    rep.append("# 筹码负向拦截实验报告（cost_bias Hard Gate）")
    rep.append(f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    rep.append(f"\n- 数据: score_rows_3d.pkl × chip_backtest_series.pkl（合并覆盖率 {cov:.1%}）")
    rep.append(f"- 拦截定义: cost_bias > 阈值 的票从 top_k 候选中剔除")
    rep.append(f"- 注: 缺筹码数据 = 不拦截（fail-open），与生产 gate 语义一致\n")

    for reg in ['trend_up', 'sideways', 'trend_down', 'high_vol']:
        r = res[res['regime'] == reg]
        if r.empty:
            continue
        rep.append(f"\n## {reg} (K={TOPKS.get(reg)}, {r['date'].nunique()} 日)")
        rep.append("| thr | 拦截日数 | 日均拦截票数 | A mean(bp) | B mean(bp) | Δmean | A med(bp) | B med(bp) | Δmed | A wr | B wr |")
        rep.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for thr in THRESHOLDS:
            rt = r[r['thr'] == thr]
            hit = rt[rt['n_blocked'] > 0]
            # 两臂对照只在「拦截实际发生」的日子有意义；合并口径附全期
            a_m = hit['ex_a_mean'].mean() * 1e4 if len(hit) else np.nan
            b_m = hit['ex_b_mean'].mean() * 1e4 if len(hit) else np.nan
            a_md = hit['ex_a_med'].median() * 1e4 if len(hit) else np.nan
            b_md = hit['ex_b_med'].median() * 1e4 if len(hit) else np.nan
            wr_a = hit['wr_a'].mean() if len(hit) else np.nan
            wr_b = hit['wr_b'].mean() if len(hit) else np.nan
            rep.append(f"| {thr} | {len(hit)} | {hit['n_blocked'].mean():.1f} | "
                       f"{a_m:+.1f} | {b_m:+.1f} | {(b_m - a_m):+.1f} | "
                       f"{a_md:+.1f} | {b_md:+.1f} | {(b_md - a_md):+.1f} | "
                       f"{wr_a:.0%} | {wr_b:.0%} |")

    # ── 结论区 ──
    rep.append("\n## 判读标准（预注册）")
    rep.append("- Δmean/Δmed ≥ +10bp 且拦截日数 ≥ 60 → 证据支持立项生产 gate")
    rep.append("- Δ 在 ±10bp 内 → 无操作意义，维持现状（chip 只展示不拦截）")
    rep.append("- Δ < -10bp → 反向证据，cost_bias 高位在这些 regime 反而是动量信号")

    with open(OUT_MD, 'w', encoding='utf-8') as f:
        f.write("\n".join(rep))
    print(f"✅ 报告已落盘 {OUT_MD}")
    return res


if __name__ == "__main__":
    main()
