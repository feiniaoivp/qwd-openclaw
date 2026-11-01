#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ML 第7臂：RandomForest 方向分类（P1，2026-11-01，借鉴构建计划 ml_strategy）
============================================================================

按本仓纪律对计划的 ML 策略做的裁剪与收紧：
- 只做 RandomForest（sklearn）：36 只票 × 日线样本量撑不起 LSTM（数据饥渴 +
  非平稳，10-28 情绪反转实证：学到的模式会整体过期）；XGBoost 待 RF 基线
  建立后再对比，不在 v1。
- 特征全部来自 OHLCV：自含、可复现、无额外数据依赖（factor_engine/五维特征
  的接入留待 v2，先验证纯技术特征下限）。
- 严格 walk-forward：每只票独立建模；训练样本的标签必须在预测时点之前完全
  实现（j + H ≤ i-1）；模型每 refit_every 根 bar 重训一次，锚点固定。
- 标签口径与引擎成交口径一致：signal 在 close[i] → 引擎按 close[i] 成交 →
  标签 = close[i+H]/close[i] - 1 的方向，H=5。
- 输出与既有策略臂完全兼容的 actions（date/type/price/reason）。

准入纪律（未过不得写入 strategy_map）：
- 4 窗口 walk-forward（validate_strategies 框架，加入 CANDIDATE 后自动参与）
- DSR 显著性（dsr_sharpe.py）——ML 多重检验风险高于规则策略，必须过
- 随机基准护栏（backtest_guard.compare_to_random）

自测（含截断攻击防前视验证）：
  python3 analysis/ml_strategy.py    （退出码 0=过 / 1=违规）
"""

import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd
import pandas_ta as ta

# ── 可调参数（env 覆盖，默认值进 config 前保持显式）──
HORIZON = int(os.environ.get("ML_HORIZON", "5"))          # 标签前瞻天数
MIN_TRAIN = int(os.environ.get("ML_MIN_TRAIN", "250"))    # 首次训练最少样本 bar 数
REFIT_EVERY = int(os.environ.get("ML_REFIT_EVERY", "60")) # 重训间隔（bar 数）
TH_IN = float(os.environ.get("ML_TH_IN", "0.55"))         # 买入概率阈值
TH_OUT = float(os.environ.get("ML_TH_OUT", "0.45"))       # 卖出概率阈值
RF_TREES = int(os.environ.get("ML_RF_TREES", "200"))
RF_DEPTH = int(os.environ.get("ML_RF_DEPTH", "4"))
RF_JOBS = int(os.environ.get("ML_RF_JOBS", "-1"))  # -1=sklearn并行； macOS 上 loky 孵化开销大，跨股票并行时建议设 1
FEATURE_SET = os.environ.get("ML_FEATURE_SET", "v1")      # v1=13个OHLCV | v2=+5（52周高/量比/筹码代理/横截面rank）
RANDOM_STATE = 42
WARMUP = 70  # 指标预热（最大滚动窗 60 + 余量）；此前不产生任何预测/动作

FEATURE_NAMES_V1 = [
    "f_ret1", "f_ret5", "f_ret10", "f_ret20", "f_vol20",
    "f_rsi14", "f_macd_hist_norm", "f_bb_pos", "f_ema_spread",
    "f_dist_ema20_atr", "f_vol_ratio", "f_atr_pct", "f_obv_chg5",
]
# v2 新增（2026-11-01，负结果后的特征杠杆；全部可从 OHLCV 历史/panel 重建，零新数据源）：
#   f_52w_high_dist   距 250 日高点位置（趋势强度/套牢盘）
#   f_vol_ratio_5_20  短长期量能比（量能扩张/萎缩）
#   f_vwap250_dist    相对 250 日成交量加权成本线位置 —— chip 获利盘代理
#                     （五维研究：chip 绝对分档三年全正，唯一有证据的慢维）
#   f_xs_rank_ret20   横截面 20 日动量分位（panel 注入列，缺失则自动跳过）
#   f_xs_rank_vol20   横截面 20 日波动分位
FEATURE_NAMES_V2 = FEATURE_NAMES_V1 + [
    "f_52w_high_dist", "f_vol_ratio_5_20", "f_vwap250_dist",
    "f_xs_rank_ret20", "f_xs_rank_vol20",
]


def _col(df, prefix):
    """动态列名匹配（pandas_ta 版本差异坑，07-26 固化模式）"""
    hits = [c for c in df.columns if str(c).upper().startswith(prefix.upper())]
    if not hits:
        raise ValueError(f"pandas_ta 未产出前缀 {prefix} 的列: {list(df.columns)}")
    return hits[0]


def build_features(df, feature_set=None):
    """计算特征矩阵（仅用 ≤ 当前 bar 的信息；返回 (feat_df, close_series)）。

    v2 追加特征；横截面 rank 列（f_xs_rank_*）由调用方（panel 研究脚本）预注入
    df，本函数只读取已有列——列缺失时自动跳过（保持单票可独立运行）。
    """
    feature_set = feature_set or FEATURE_SET
    df = df.copy().reset_index(drop=True)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["volume"].astype(float)

    f = pd.DataFrame(index=df.index)
    for n in (1, 5, 10, 20):
        f[f"f_ret{n}"] = close.pct_change(n)
    f["f_vol20"] = close.pct_change().rolling(20).std()
    f["f_rsi14"] = ta.rsi(close, length=14)
    macd = ta.macd(close, fast=12, slow=26, signal=9)
    f["f_macd_hist_norm"] = macd[_col(macd, "MACDh")] / close
    bb = ta.bbands(close, length=20, std=2)
    bbl, bbu = bb[_col(bb, "BBL")], bb[_col(bb, "BBU")]
    f["f_bb_pos"] = ((close - bbl) / (bbu - bbl)).clip(-1, 2)
    f["f_ema_spread"] = (ta.ema(close, length=12) - ta.ema(close, length=26)) / close
    atr = ta.atr(high, low, close, length=14)
    f["f_dist_ema20_atr"] = (close - ta.ema(close, length=20)) / atr
    f["f_vol_ratio"] = vol / vol.rolling(5).mean()
    f["f_atr_pct"] = atr / close
    obv = ta.obv(close, vol)
    obv_d5 = obv.diff(5)
    f["f_obv_chg5"] = obv_d5 / obv_d5.rolling(60).std()

    if feature_set == "v2":
        f["f_52w_high_dist"] = close / close.rolling(250, min_periods=60).max() - 1.0
        f["f_vol_ratio_5_20"] = vol.rolling(5).mean() / vol.rolling(20).mean()
        typical = (high + low + close) / 3.0
        vwap250 = (typical * vol).rolling(250, min_periods=60).sum() \
            / vol.rolling(250, min_periods=60).sum()
        f["f_vwap250_dist"] = close / vwap250 - 1.0
        # 横截面 rank：panel 注入列，缺失则跳过（不报错——单票运行合法）
        for cn in ("f_xs_rank_ret20", "f_xs_rank_vol20"):
            if cn in df.columns:
                f[cn] = pd.to_numeric(df[cn], errors="coerce")
    return f, close


def _fit_predict_probs(feat, close, feature_names=None):
    """逐 bar walk-forward 预测。返回 prob_up 数组（WARMUP 前与样本不足处为 np.nan）。
    泄漏防护：训练样本 j 必须满足 j >= WARMUP 且 j + H <= i - 1（标签在决策时点前实现）。"""
    from sklearn.ensemble import RandomForestClassifier

    names = feature_names or (FEATURE_NAMES_V2 if FEATURE_SET == "v2" else FEATURE_NAMES_V1)
    # 可用列筛选：未注入的横截面列不在 feat 中 → 自动降级；全列全 NaN → 剔除
    names_avail = [nm for nm in names if nm in feat.columns]
    n = len(feat)
    X = feat[names_avail].values.astype(float)
    valid_cols = ~np.isnan(X).all(axis=0)
    X = X[:, valid_cols]
    y = np.full(n, np.nan)
    # 标签：close[i+H]/close[i]-1 方向（最后 H 根无法成标签）；close 为 numpy 数组
    fwd = np.full(n, np.nan)
    fwd[:n - HORIZON] = close[HORIZON:] / close[:n - HORIZON] - 1.0
    y = np.where(np.isnan(fwd), np.nan, (fwd > 0).astype(float))

    prob_up = np.full(n, np.nan)
    model = None
    for i in range(WARMUP, n):
        # 重训锚点：MIN_TRAIN 起步，此后每 REFIT_EVERY 根 bar
        need_refit = (model is None) or ((i - MIN_TRAIN) % REFIT_EVERY == 0)
        if need_refit:
            mask = (~np.isnan(X).any(axis=1)) & (~np.isnan(y))
            train_idx = [j for j in range(WARMUP, i) if mask[j] and (j + HORIZON <= i - 1)]
            if len(train_idx) >= 120:  # 样本量护栏：不足则沿用旧模型
                Xtr, ytr = X[train_idx], y[train_idx]
                model = RandomForestClassifier(
                    n_estimators=RF_TREES, max_depth=RF_DEPTH,
                    min_samples_leaf=20, class_weight="balanced",
                    random_state=RANDOM_STATE, n_jobs=RF_JOBS)
                model.fit(Xtr, ytr)
        if model is not None and not np.isnan(X[i]).any():
            prob_up[i] = float(model.predict_proba(X[i:i + 1])[:, 1][0])
    return prob_up


def strategy_ml_rf(df, feature_set=None):
    """ML 第7臂：RF 方向分类 → 与既有臂兼容的 actions。
    买入: prob_up >= TH_IN 且空仓；卖出: prob_up <= TH_OUT 且持仓。"""
    feature_set = feature_set or FEATURE_SET
    names = FEATURE_NAMES_V2 if feature_set == "v2" else FEATURE_NAMES_V1
    feat, close = build_features(df, feature_set=feature_set)
    prob_up = _fit_predict_probs(feat, close.values.astype(float), feature_names=names)

    actions = []
    position = False
    for i in range(len(df)):
        p = prob_up[i]
        if np.isnan(p):
            continue
        row = df.iloc[i]
        dt = str(row["date"].date())
        price = float(row["close"])
        if not position and p >= TH_IN:
            actions.append({"date": dt, "type": "BUY", "price": price,
                            "reason": f"ML-RF: P(up)={p:.2f}>={TH_IN} (H={HORIZON} 方向分类)"})
            position = True
        elif position and p <= TH_OUT:
            actions.append({"date": dt, "type": "SELL", "price": price,
                            "reason": f"ML-RF: P(up)={p:.2f}<={TH_OUT} (H={HORIZON} 方向分类)"})
            position = False
    return actions


# ════════════════════════════════════════
# 自测：合成数据 + 截断攻击（防前视）+ 确定性
# ════════════════════════════════════════

def _make_synth_df(n=800, seed=7):
    """带趋势段与震荡段的合成日线（几何随机游走 + 动量段），日期为工作日序列。"""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0004, 0.018, n)
    for s in (200, 450, 650):  # 注入三段动量
        rets[s:s + 60] += 0.004
    close = 20 * np.cumprod(1 + rets)
    intraday = np.abs(rng.normal(0, 0.012, n))
    high = close * (1 + intraday)
    low = close * (1 - np.abs(rng.normal(0, 0.012, n)))
    openp = low + (high - low) * rng.random(n)
    dates = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame({"date": dates, "open": openp, "close": close,
                         "high": high, "low": low,
                         "volume": rng.integers(5e6, 5e7, n).astype(float)})


def _selftest() -> int:
    failures = []
    df = _make_synth_df()

    for fs in ("v1", "v2"):
        acts_full = strategy_ml_rf(df, feature_set=fs)

        # 1) 动作形态：预热期内不得有任何动作（防暖期泄漏）
        warmup_end = str(df.iloc[WARMUP - 1]["date"].date())
        early = [a for a in acts_full if a["date"] <= warmup_end]
        if early:
            failures.append(f"[{fs}] 预热期({WARMUP} bar)内出现 {len(early)} 个动作")

        # 2) 状态机合法性：价格与当日收盘一致、BUY/SELL 交替
        date_pos = {str(d.date()): float(c) for d, c in zip(df["date"], df["close"])}
        pos = False
        for a in acts_full:
            if abs(date_pos[a["date"]] - a["price"]) > 1e-9:
                failures.append(f"[{fs}] 价格与当日收盘不符: {a['date']}")
                break
            if a["type"] == "BUY" and pos:
                failures.append(f"[{fs}] 持仓中出现 BUY: {a['date']}")
                break
            if a["type"] == "SELL" and not pos:
                failures.append(f"[{fs}] 空仓中出现 SELL: {a['date']}")
                break
            pos = (a["type"] == "BUY")

        # 3) 截断攻击：只用前 500 根重跑，重叠区间动作必须逐字节一致（无前视硬证明）
        cut = 500
        acts_cut = strategy_ml_rf(df.iloc[:cut].copy(), feature_set=fs)
        cut_end = str(df.iloc[cut - 1]["date"].date())
        overlap_full = [a for a in acts_full if a["date"] <= cut_end]
        if overlap_full != acts_cut:
            diffs = [(a, b) for a, b in zip(overlap_full, acts_cut) if a != b]
            failures.append(f"[{fs}] 截断攻击失败: 前{cut}根重叠动作不一致 {len(diffs)} 处")

        # 4) 确定性：同输入两次完全一致
        if strategy_ml_rf(df, feature_set=fs) != acts_full:
            failures.append(f"[{fs}] 确定性失败: 同一输入两次运行动作不一致")

        n_trades = len(acts_full)
        if n_trades == 0:
            failures.append(f"[{fs}] 合成数据零动作（模型或阈值可能失效）")
        if n_trades > 200:
            failures.append(f"[{fs}] 动作过密({n_trades})，疑似阈值/标签异常")
        print(f"  [{fs}] 动作 {n_trades} 个，四项通过")

    if failures:
        print("❌ ml_strategy 自测失败:")
        for x in failures:
            print("   -", x)
        return 1
    print("✅ ml_strategy 自测通过: v1+v2 双特征集 × 预热隔离/状态机/截断攻击/确定性")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
