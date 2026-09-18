#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测护栏：随机基准对照 + 置信区间检验
==========================================
核心原则：任何回测指标若无显著超越随机基准，默认无效。

用法：
    from analysis.backtest_guard import assert_beats_random, random_baseline_ci
    
    # 1. 先算随机基准（同样本量、同窗口的随机命中率）
    rand_rate, rand_ci = random_baseline_ci(n_trials=1000, n_windows=100, hit_prob=0.63)
    
    # 2. 实测指标必须显著超越
    assert_beats_random("命中率", 0.65, rand_rate, rand_ci)
    
    # 或者一次性算完：实测 vs 随机
    excess, ci, passed = compare_to_random(observed=0.65, n_obs=100, n_rand=1000, hit_prob=0.63)
"""
import numpy as np
import math
from typing import Tuple, Optional
from scipy import stats


def random_baseline_ci(
    n_trials: int = 10000,
    n_windows: int = 1000,
    hit_prob: float = None
) -> Tuple[float, Tuple[float, float]]:
    """
    计算随机基准命中率的均值与 95% 置信区间。
    
    参数：
        n_trials: 蒙特卡洛试验次数（默认 10000）
        n_windows: 每次试验的窗口数（应与实测样本量一致）
        hit_prob: 随机命中概率。若 None，用 0.5（最保守）；
                  若已知市场自然变盘率，可传入（如 0.63）
    
    返回：
        (mean_rate, (ci_low, ci_high))
    """
    if hit_prob is None:
        hit_prob = 0.5
    
    # 二项分布精确 CI（Wilson score interval，比正态近似更准，尤其小样本/边界值）
    # 但蒙特卡洛更直观，且能直接给出分布
    rng = np.random.default_rng(42)  # 固定种子可复现
    samples = rng.binomial(n_windows, hit_prob, size=n_trials) / n_windows
    
    mean_rate = float(samples.mean())
    ci_low, ci_high = np.percentile(samples, [2.5, 97.5])
    
    return mean_rate, (float(ci_low), float(ci_high))


def wilson_ci(k: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
    """
    Wilson score interval for binomial proportion.
    比正态近似更准，推荐用于报告实测指标的 CI。
    """
    if n == 0:
        return (0.0, 0.0)
    z = stats.norm.ppf(1 - (1 - confidence) / 2)
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def compare_to_random(
    observed_hits: int,
    observed_total: int,
    random_hit_prob: float = None,
    n_trials: int = 10000
) -> dict:
    """
    实测 vs 随机基准的完整对照。
    
    参数：
        observed_hits: 实测命中数
        observed_total: 实测总窗口数
        random_hit_prob: 随机命中概率（None=0.5 最保守；若有市场自然率可传入）
        n_trials: 蒙特卡洛次数
    
    返回：
        {
            "observed_rate": float,
            "observed_ci": (low, high),
            "random_rate": float,
            "random_ci": (low, high),
            "excess": float,              # observed - random
            "excess_ci": (low, high),     # 超额的 95% CI (差分比例)
            "p_value": float,             # 单侧：实测 ≤ 随机的概率
            "passed": bool,               # excess > 0 且 excess_ci[0] > 0
        }
    """
    if observed_total == 0:
        return {"error": "observed_total=0"}
    
    observed_rate = observed_hits / observed_total
    obs_ci = wilson_ci(observed_hits, observed_total)
    
    if random_hit_prob is None:
        random_hit_prob = 0.5
    
    rand_rate, rand_ci = random_baseline_ci(
        n_trials=n_trials,
        n_windows=observed_total,
        hit_prob=random_hit_prob
    )
    
    excess = observed_rate - rand_rate
    
    # 超额的 CI：蒙特卡洛差分分布 (更准)
    rng = np.random.default_rng(42)
    rand_samples = rng.binomial(observed_total, random_hit_prob, size=n_trials) / observed_total
    obs_samples = np.full(n_trials, observed_rate)  # 实测视为定值
    excess_samples = obs_samples - rand_samples
    excess_ci = (float(np.percentile(excess_samples, 2.5)), float(np.percentile(excess_samples, 97.5)))
    
    # 单侧 p-value：随机产生 ≥ observed_rate 的概率
    p_value = float((rand_samples >= observed_rate).mean())
    
    # 通过标准：excess > 0 且 excess CI 下界 > 0 (即 95% 置信下超额为正)
    passed = (excess > 0) and (excess_ci[0] > 0)
    
    return {
        "observed_rate": observed_rate,
        "observed_ci": obs_ci,
        "random_rate": rand_rate,
        "random_ci": rand_ci,
        "excess": excess,
        "excess_ci": excess_ci,
        "p_value": p_value,
        "passed": passed,
        "n_trials": n_trials,
    }


def assert_beats_random(
    metric: str,
    observed_hits: int,
    observed_total: int,
    random_hit_prob: float = None,
    n_trials: int = 10000,
    min_excess: float = 0.0,
) -> dict:
    """
    护栏断言：实测指标必须显著超越随机基准。
    
    抛出 AssertionError 若不通过；返回详细结果供日志记录。
    
    参数：
        metric: 指标名（用于报错信息）
        observed_hits: 实测命中数
        observed_total: 实测总数
        random_hit_prob: 随机基准命中率（None=0.5；建议传入市场自然率）
        n_trials: 蒙特卡洛次数
        min_excess: 最小超额阈值（默认 0，可设 0.05 要求至少 5pct 超额）
    
    返回：compare_to_random 的完整结果 dict
    """
    result = compare_to_random(
        observed_hits=observed_hits,
        observed_total=observed_total,
        random_hit_prob=random_hit_prob,
        n_trials=n_trials,
    )
    
    if "error" in result:
        raise AssertionError(f"{metric}: {result['error']}")
    
    excess = result["excess"]
    excess_ci_high = result["excess_ci"][1]
    p_value = result["p_value"]
    
    passed = (excess > min_excess) and (excess_ci_high > min_excess) and (p_value < 0.05)
    
    if not passed:
        msg = (
            f"{metric}={result['observed_rate']:.2%} (CI={result['observed_ci'][0]:.2%}-{result['observed_ci'][1]:.2%}) "
            f"未显著超越随机基准 {result['random_rate']:.2%} (CI={result['random_ci'][0]:.2%}-{result['random_ci'][1]:.2%}) | "
            f"超额={excess:.2%} (CI={result['excess_ci'][0]:.2%}-{result['excess_ci'][1]:.2%}) | "
            f"p={p_value:.4f} | min_excess={min_excess:.2%}"
        )
        raise AssertionError(msg)
    
    return result


# ── 便捷：从实测列表直接算 ──
def assert_list_beats_random(
    metric: str,
    observed_list: list,           # 每元素 True/False 或 1/0
    random_hit_prob: float = None,
    n_trials: int = 10000,
    min_excess: float = 0.0,
) -> dict:
    """observed_list: [True, False, True, ...] 或 [1, 0, 1, ...]"""
    hits = sum(1 for x in observed_list if x)
    total = len(observed_list)
    return assert_beats_random(metric, hits, total, random_hit_prob, n_trials, min_excess)


if __name__ == "__main__":
    # 自测
    print("=== 自测 ===")
    
    # 情况 1：实测 65%，随机 50%，n=100
    r = compare_to_random(65, 100, random_hit_prob=0.5)
    print(f"65/100 vs 50%: excess={r['excess']:.2%}, CI={r['excess_ci']}, passed={r['passed']}, p={r['p_value']:.4f}")
    
    # 情况 2：实测 63%，随机 63%（市场自然率），n=14000
    r = compare_to_random(8856, 14092, random_hit_prob=0.63)
    print(f"8856/14092 vs 63%: excess={r['excess']:.2%}, CI={r['excess_ci']}, passed={r['passed']}, p={r['p_value']:.4f}")
    
    # 情况 3：实测 60%，随机 50%，n=50（小样本）
    r = compare_to_random(30, 50, random_hit_prob=0.5)
    print(f"30/50 vs 50%: excess={r['excess']:.2%}, CI={r['excess_ci']}, passed={r['passed']}, p={r['p_value']:.4f}")
    
    # 测试断言
    print("\n=== 断言测试 ===")
    try:
        assert_beats_random("测试指标", 65, 100, 0.5)
        print("✅ 65/100 vs 50% 通过")
    except AssertionError as e:
        print(f"❌ {e}")
    
    try:
        assert_beats_random("测试指标", 8856, 14092, 0.63)
        print("✅ 8856/14092 vs 63% 通过")
    except AssertionError as e:
        print(f"❌ {e}")