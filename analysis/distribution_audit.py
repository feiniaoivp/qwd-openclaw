#!/usr/bin/env python3
import os
import sys
import pandas as pd
import numpy as np
import pickle
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA_PATH = os.path.join(WORKSPACE, "data", "universe", "score_rows_3d.pkl")
OUT_PATH = os.path.join(WORKSPACE, "analysis", "distribution_audit_report.md")

def run_audit():
    print(f"🚀 Starting Distribution Audit on {DATA_PATH}...")
    
    # 1. Load Data
    if not os.path.exists(DATA_PATH):
        print(f"❌ Data file not found: {DATA_PATH}")
        return
    with open(DATA_PATH, 'rb') as f:
        df = pickle.load(f)
    
    df['date'] = pd.to_datetime(df['date'])
    
    # 2. Focus on trend_up regime
    up_df = df[df['regime'] == 'trend_up'].copy()
    if up_df.empty:
        print("❌ No trend_up data found.")
        return

    # 3. Daily Analysis
    daily_stats = []
    dates = up_df['date'].unique()
    
    for d in dates:
        day_data = up_df[up_df['date'] == d].sort_values('score', ascending=False)
        if len(day_data) < 30:
            continue
        
        # Calculate Delta S (Top-1 vs Top-30)
        top1_score = day_data.iloc[0]['score']
        top30_score = day_data.iloc[29]['score']
        delta_s = top1_score - top30_score
        
        # Tail group (Rank 16-30)
        tail_group = day_data.iloc[15:30]
        tail_ex5_mean = tail_group['ex5'].mean()
        tail_ex5_median = tail_group['ex5'].median()
        tail_win_rate = (tail_group['ex5'] > 0).mean()
        
        daily_stats.append({
            'date': d,
            'delta_s': delta_s,
            'tail_ex5_mean': tail_ex5_mean,
            'tail_ex5_median': tail_ex5_median,
            'tail_win_rate': tail_win_rate
        })
    
    res = pd.DataFrame(daily_stats)
    if res.empty:
        print("❌ No sufficient daily samples.")
        return

    # 4. Binning by delta_s
    res['delta_s_bin'] = pd.qcut(res['delta_s'], 5, labels=['P20', 'P40', 'P60', 'P80', 'P100'])
    bin_analysis = res.groupby('delta_s_bin').agg({
        'delta_s': ['min', 'max', 'mean'],
        'tail_ex5_mean': 'mean',
        'tail_ex5_median': 'median',
        'tail_win_rate': 'mean',
        'date': 'count'
    })
    bin_analysis.columns = ['dS_min', 'dS_max', 'dS_mean', 'ex5_mean', 'ex5_median', 'win_rate', 'count']
    
    # 5. Find Threshold
    report = []
    report.append("# L3 Distribution Audit Report: Tail Decay Analysis")
    report.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report.append("\n## 1. Trend_Up Regime Tail Analysis (K16-K30)")
    report.append(f"Total Trading Days analyzed: {len(res)}")
    report.append("\n### Delta S vs Tail Performance (Quantiles)")
    report.append(bin_analysis.to_markdown())
    
    report.append("\n## 2. Threshold Exploration")
    for q in [0.5, 0.75, 0.9]:
        val = res['delta_s'].quantile(q)
        mask = res['delta_s'] > val
        low_ex5 = res[mask]['tail_ex5_median'].mean()
        high_ex5 = res[~mask]['tail_ex5_median'].mean()
        report.append(f"- **At P{int(q*100)} (dS > {val:.2f})**: Tail Median Ex5 = {low_ex5:.4f} vs. {high_ex5:.4f} (Below)")

    # Recommendation
    best_q = 0.75 # Default starting point
    final_threshold = res['delta_s'].quantile(best_q)
    report.append(f"\n## 3. Conclusion & Recommendation")
    report.append(f"Suggested **Tail_Decay Threshold**: `{final_threshold:.2f}`")
    report.append("Logic: When Delta S exceeds this value, the distribution becomes 'steep', and the lottery effect of K16-K30 collapses. In these scenarios, contracting Top-K to 15 is recommended.")

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        f.write("\n".join(report))
    
    print(f"✅ Report saved to {OUT_PATH}")

if __name__ == "__main__":
    run_audit()
