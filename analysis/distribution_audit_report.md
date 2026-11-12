# L3 Distribution Audit Report: Tail Decay Analysis
Date: 2026-11-12 12:17

## 1. Trend_Up Regime Tail Analysis (K16-K30)
Total Trading Days analyzed: 349

### Delta S vs Tail Performance (Quantiles)
| delta_s_bin   |   dS_min |   dS_max |   dS_mean |     ex5_mean |   ex5_median |   win_rate |   count |
|:--------------|---------:|---------:|----------:|-------------:|-------------:|-----------:|--------:|
| P20           |     3.21 |     8.06 |   6.67859 | -0.000304737 |  -0.00898205 |   0.421596 |      71 |
| P40           |     8.33 |     9.73 |   9.09286 |  0.00200508  |  -0.00555233 |   0.442857 |      70 |
| P60           |    10    |    11.11 |  10.6941  |  0.00636068  |  -0.00380388 |   0.457971 |      69 |
| P80           |    11.39 |    13.22 |  12.2096  |  0.00597591  |  -0.00113417 |   0.462802 |      69 |
| P100          |    13.22 |    19.28 |  14.8203  |  0.000865117 |  -0.00779198 |   0.44     |      70 |

## 2. Threshold Exploration
- **At P50 (dS > 10.72)**: Tail Median Ex5 = -0.0035 vs. -0.0053 (Below)
- **At P75 (dS > 12.78)**: Tail Median Ex5 = -0.0067 vs. -0.0037 (Below)
- **At P90 (dS > 14.28)**: Tail Median Ex5 = -0.0095 vs. -0.0039 (Below)

## 3. Conclusion & Recommendation
Suggested **Tail_Decay Threshold**: `12.78`
Logic: When Delta S exceeds this value, the distribution becomes 'steep', and the lottery effect of K16-K30 collapses. In these scenarios, contracting Top-K to 15 is recommended.