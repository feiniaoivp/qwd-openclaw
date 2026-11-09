#!/usr/bin/env python3
"""
α 验证框架 — IC 序列、t-stat、换手调整收益、DSR 运气基线、因子正交性
====================================================================
核心功能：
  1. IC 分析：IC 均值、ICIR、IC > 0 比例、IC 分位数
  2. 因子正交性：相关性矩阵、VIF、主成分解释方差
  3. 换手调整收益：扣除滑点/佣金/冲击成本后的净超额
  4. DSR (Deflated Sharpe Ratio)：Harvey 2016 运气基线修正
  5. 因子有效性报告：HTML/Markdown 双格式
"""

import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
REPORT_DIR = os.path.join(WORKSPACE, "analysis", "alpha_reports")
os.makedirs(REPORT_DIR, exist_ok=True)

sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

from analysis.factor_engine import get_engine
from analysis.factor_registry import get_registry
from analysis.strategy_composer import StrategyComposer, Strategy, create_seed_strategies


# ══════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════

@dataclass
class ICStats:
    """IC 统计指标"""
    factor: str
    ic_mean: float
    ic_std: float
    icir: float                     # IC / std(IC) * sqrt(N)
    ic_positive_pct: float          # IC > 0 的比例
    ic_skew: float
    ic_kurt: float
    ic_quantiles: Dict[str, float]  # 5%, 25%, 50%, 75%, 95%
    n_periods: int
    p_value: float                  # t-test: IC != 0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class TurnoverAdjustedReturn:
    """换手调整收益"""
    strategy: str
    gross_annual_return: float
    net_annual_return: float
    annual_turnover: float
    cost_per_turn: float            # 单边成本（佣金+滑点+冲击）
    total_cost_drag: float          # 年化成本拖累
    sharpe_gross: float
    sharpe_net: float
    max_drawdown: float

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class DSRResult:
    """Deflated Sharpe Ratio (Harvey 2016)"""
    strategy: str
    sharpe: float
    dsr: float
    prob_sharpe_luck: float         # P(SR* >= SR | H0: 真实SR=0)
    n_trials: int                   # 隐性试验次数（策略数 × 参数组合）
    skew: float
    kurt: float
    min_track_record: int           # 最小样本数要求

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class OrthogonalityReport:
    """因子正交性报告"""
    factor_corr: Dict[str, Dict[str, float]]  # 相关性矩阵
    max_abs_corr: float
    mean_abs_corr: float
    vif: Dict[str, float]           # Variance Inflation Factor
    pca_explained_var: List[float]  # 主成分解释方差比
    n_factors_eff: float            # 有效因子数
    condition_number: float         # 条件数
    method: str = "cross_sectional_rank"  # VIF/PCA 口径自述（2026-11-10：旧口径 price_level_raw
                                          # 被价格水平类因子淹没，条件数 9e17/n_eff=0 无意义，已废弃）

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class AlphaValidationReport:
    """完整 α 验证报告"""
    generated_at: str
    period: Dict[str, str]          # {"start": "...", "end": "..."}
    universe: str
    n_stocks: int
    n_days: int
    
    ic_stats: List[ICStats]
    turnover_adjusted: List[TurnoverAdjustedReturn]
    dsr_results: List[DSRResult]
    orthogonality: OrthogonalityReport
    
    summary: Dict[str, Any]         # 关键结论摘要

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["ic_stats"] = [x.to_dict() for x in self.ic_stats]
        d["turnover_adjusted"] = [x.to_dict() for x in self.turnover_adjusted]
        d["dsr_results"] = [x.to_dict() for x in self.dsr_results]
        d["orthogonality"] = self.orthogonality.to_dict()
        return d

    def save_json(self, path: str):
        def _np_default(o):
            # 安全网：numpy 标量统一转原生类型（np.int64 非 int 子类会 TypeError，2026-11-08）
            if hasattr(o, "item"):
                return o.item()
            return str(o)
        import shutil
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")  # 写前备份（backup_guard 铁律）
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2, default=_np_default)

    def save_md(self, path: str):
        """生成人读 Markdown 报告"""
        lines = [
            f"# 📊 α 验证报告",
            f"_生成时间: {self.generated_at}_",
            f"_回测区间: {self.period['start']} ~ {self.period['end']}_",
            f"_股票池: {self.universe} ({self.n_stocks} 只, {self.n_days} 天)_",
            "",
            "## 📈 IC 统计摘要",
            "",
            "| 因子 | IC均值 | IC标准差 | ICIR | IC>0% | P值 | 样本数 |",
            "|------|--------|----------|------|-------|-----|--------|",
        ]
        for ic in self.ic_stats:
            lines.append(f"| {ic.factor} | {ic.ic_mean:.4f} | {ic.ic_std:.4f} | {ic.icir:.3f} | {ic.ic_positive_pct:.1%} | {ic.p_value:.4f} | {ic.n_periods} |")
        
        lines += ["", "## 🔄 换手调整收益", "", "| 策略 | 毛收益(年化) | 净收益(年化) | 换手率 | 单边成本 | 成本拖累 | 夏普(毛) | 夏普(净) | 最大回撤 |", "|------|--------------|--------------|--------|----------|----------|----------|----------|----------|"]
        for t in self.turnover_adjusted:
            lines.append(f"| {t.strategy} | {t.gross_annual_return:.2%} | {t.net_annual_return:.2%} | {t.annual_turnover:.1%} | {t.cost_per_turn:.4f} | {t.total_cost_drag:.2%} | {t.sharpe_gross:.3f} | {t.sharpe_net:.3f} | {t.max_drawdown:.2%} |")

        lines += ["", "## 🎯 DSR (Deflated Sharpe Ratio)", "", "| 策略 | 夏普 | DSR | P(运气) | 隐性试验数 | 偏度 | 峰度 |", "|------|------|-----|---------|------------|------|------|"]
        for d in self.dsr_results:
            lines.append(f"| {d.strategy} | {d.sharpe:.3f} | {d.dsr:.3f} | {d.prob_sharpe_luck:.3f} | {d.n_trials} | {d.skew:.3f} | {d.kurt:.3f} |")

        lines += ["", "## 🔗 因子正交性（VIF/PCA 口径：横截面秩 cross_sectional_rank，2026-11-10）", "", f"- 最大绝对相关性: {self.orthogonality.max_abs_corr:.3f}", f"- 平均绝对相关性: {self.orthogonality.mean_abs_corr:.3f}", f"- 有效因子数: {self.orthogonality.n_factors_eff:.1f}", f"- 条件数: {self.orthogonality.condition_number:.1f}", "", "### VIF (方差膨胀因子)", "| 因子 | VIF |", "|------|-----|"]
        for f, vif in self.orthogonality.vif.items():
            lines.append(f"| {f} | {vif:.2f} |")
        
        lines += ["", "### PCA 解释方差", "| 主成分 | 解释方差% | 累计% |", "|--------|-----------|-------|"]
        cum = 0
        for i, var in enumerate(self.orthogonality.pca_explained_var):
            cum += var
            lines.append(f"| PC{i+1} | {var:.2%} | {cum:.2%} |")

        lines += ["", "## 📋 关键结论", ""]
        for k, v in self.summary.items():
            lines.append(f"- **{k}**: {v}")

        import shutil
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")  # 写前备份（backup_guard 铁律）
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


# ══════════════════════════════════════════════════════════════
# 核心计算函数
# ══════════════════════════════════════════════════════════════

def compute_ic_stats(
    factor_panel: pd.DataFrame,          # symbol × date → factor values
    returns: pd.DataFrame,               # symbol × date → next_period_return
    factor_name: str,
) -> ICStats:
    """计算单因子 IC 序列统计"""
    # 对齐：factor(t) vs return(t+1)
    factor_vals = factor_panel[factor_name].unstack("symbol")  # date × symbol
    ret_vals = returns.unstack("symbol")                       # date × symbol
    
    # 移动因子前移一期（避免前视偏差）
    factor_vals = factor_vals.shift(1)
    
    # 逐期计算 IC (Spearman 秩相关)
    ic_series = []
    common_dates = factor_vals.index.intersection(ret_vals.index)
    for date in common_dates:
        f = factor_vals.loc[date].dropna()
        r = ret_vals.loc[date].dropna()
        common = f.index.intersection(r.index)
        if len(common) < 10:  # 最少样本数
            continue
        ic = f.loc[common].corr(r.loc[common], method="spearman")
        if not np.isnan(ic):
            ic_series.append(ic)
    
    ic_series = pd.Series(ic_series)
    n = len(ic_series)
    
    if n < 5:
        return ICStats(factor=factor_name, ic_mean=0, ic_std=0, icir=0, ic_positive_pct=0,
                       ic_skew=0, ic_kurt=0, ic_quantiles={}, n_periods=n, p_value=1.0)
    
    ic_mean = ic_series.mean()
    ic_std = ic_series.std()
    icir = ic_mean / ic_std * np.sqrt(n) if ic_std > 0 else 0
    ic_pos = (ic_series > 0).mean()
    ic_skew = ic_series.skew()
    ic_kurt = ic_series.kurtosis()
    
    # t-test
    from scipy import stats as scipy_stats
    t_stat, p_val = scipy_stats.ttest_1samp(ic_series.dropna(), 0)
    
    quantiles = ic_series.quantile([0.05, 0.25, 0.5, 0.75, 0.95]).to_dict()
    quantiles = {f"{int(k*100)}%": v for k, v in quantiles.items()}
    
    return ICStats(
        factor=factor_name,
        ic_mean=ic_mean,
        ic_std=ic_std,
        icir=icir,
        ic_positive_pct=ic_pos,
        ic_skew=ic_skew,
        ic_kurt=ic_kurt,
        ic_quantiles=quantiles,
        n_periods=n,
        p_value=p_val,
    )


def compute_turnover_adjusted(
    strategy_name: str,
    positions: pd.DataFrame,      # symbol × date → weight
    returns: pd.DataFrame,        # symbol × date → return
    cost_per_turn: float = 0.0015, # 单边成本：佣金 0.03% + 滑点 0.1% + 冲击 0.02%
) -> TurnoverAdjustedReturn:
    """计算换手调整后收益"""
    # 对齐
    pos = positions.unstack("symbol").fillna(0)  # date × symbol
    ret = returns.unstack("symbol").fillna(0)
    
    # 日收益
    daily_ret = (pos.shift(1) * ret).sum(axis=1)  # T日持仓 × T+1日收益
    daily_ret = daily_ret.dropna()
    
    # 换手率（日级）
    weight_diff = pos.diff().abs().sum(axis=1) / 2  # 双边 → 单边
    daily_turnover = weight_diff.mean()
    annual_turnover = daily_turnover * 252
    
    # 毛收益
    gross_annual = (1 + daily_ret).prod() ** (252 / len(daily_ret)) - 1
    
    # 成本拖累
    cost_drag = annual_turnover * cost_per_turn
    net_annual = gross_annual - cost_drag
    
    # 夏普
    sharpe_gross = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    daily_net = daily_ret - weight_diff * cost_per_turn
    sharpe_net = daily_net.mean() / daily_net.std() * np.sqrt(252) if daily_net.std() > 0 else 0
    
    # 最大回撤
    cum = (1 + daily_ret).cumprod()
    dd = (cum / cum.cummax() - 1).min()
    
    return TurnoverAdjustedReturn(
        strategy=strategy_name,
        gross_annual_return=gross_annual,
        net_annual_return=net_annual,
        annual_turnover=annual_turnover,
        cost_per_turn=cost_per_turn,
        total_cost_drag=cost_drag,
        sharpe_gross=sharpe_gross,
        sharpe_net=sharpe_net,
        max_drawdown=dd,
    )


def compute_dsr(
    strategy_name: str,
    daily_returns: pd.Series,
    n_trials: int = 100,
) -> DSRResult:
    """Deflated Sharpe Ratio (Harvey & Liu 2016)"""
    n = len(daily_returns)
    if n < 30:
        return DSRResult(strategy_name, 0, 0, 1, n_trials, 0, 0, 0)
    
    sr = daily_returns.mean() / daily_returns.std() * np.sqrt(252)
    skew = daily_returns.skew()
    kurt = daily_returns.kurtosis()
    
    # DSR 公式
    # PSR = Φ[(SR* - SR) * sqrt(n-1) / sqrt(1 + 0.5*SR^2 - skew*SR + (kurt-3)/4 * SR^2)]
    # 其中 SR* = 0 (运气假设)
    denom = np.sqrt(1 + 0.5 * sr**2 - skew * sr + (kurt - 3) / 4 * sr**2)
    if denom == 0:
        dsr = 0
        prob = 1
    else:
        z = (-sr) * np.sqrt(n - 1) / denom
        from scipy import stats as scipy_stats
        prob = scipy_stats.norm.cdf(z)  # P(SR* >= SR | H0)
        dsr = sr * (1 - prob)  # 简化版
    
    # 最小样本数要求 (Lopez de Prado)
    min_track = int(np.ceil((1 - skew * sr + (kurt - 1) / 4 * sr**2) * (scipy_stats.norm.ppf(0.95) / sr)**2)) if sr > 0 else 0
    
    return DSRResult(
        strategy=strategy_name,
        sharpe=sr,
        dsr=dsr,
        prob_sharpe_luck=prob,
        n_trials=n_trials,
        skew=skew,
        kurt=kurt,
        min_track_record=min_track,
    )


def compute_orthogonality(factor_panel: pd.DataFrame) -> OrthogonalityReport:
    """因子正交性检查（横截面秩口径，2026-11-10）

    旧口径直接在原始值面板上算 VIF/PCA/条件数，被价格水平类因子淹没
    （OBV 量级 1e8+、ATR 价格单位 vs HV_20 比率型），导致条件数 9e17、n_eff=0 无意义。
    新口径：逐日横截面百分位秩（0~1）后再算 VIF/PCA —— 消除量纲与共同价格水平漂移，
    只保留横截面选股信息的相关结构。corr 矩阵维持逐日 spearman（本就是秩口径）。
    高缺失列（notna 占比 <= 40%，如当时未接通的基本面因子）降级剔除不进 VIF/PCA，
    避免 dropna 全灭导致 n_eff=0；被剔除列 vif 记 inf（无法估计）。
    """
    factors = factor_panel.columns.tolist()
    corr_matrix = pd.DataFrame(index=factors, columns=factors, dtype=float)

    # 性能优化（行为等价，2026-11-08 任务3）：逐日一次性计算整块截面 spearman 相关矩阵。
    corr_sum = pd.DataFrame(0.0, index=factors, columns=factors)
    corr_cnt = pd.DataFrame(0, index=factors, columns=factors)
    for date in factor_panel.index.get_level_values("date").unique():
        day_data = factor_panel.xs(date, level="date")
        if len(day_data) < 2:
            continue
        day_corr = day_data.corr(method="spearman").reindex(index=factors, columns=factors)
        corr_sum = corr_sum + day_corr.fillna(0.0)
        corr_cnt = corr_cnt + day_corr.notna().astype(int)
    cnt = corr_cnt.replace(0, np.nan)
    corr_matrix = (corr_sum / cnt).astype(float)

    # 上三角（不含对角线）统计
    mask = np.triu(np.ones(corr_matrix.shape), k=1).astype(bool)
    upper_vals = corr_matrix.where(mask).stack().abs().dropna()
    max_abs = float(upper_vals.max()) if len(upper_vals) > 0 else 0.0
    mean_abs = float(upper_vals.mean()) if len(upper_vals) > 0 else 0.0

    # ── 横截面秩变换（VIF/PCA 统一输入）──
    def _rank_group(g):
        return g.rank(pct=True, axis=0)
    ranked = factor_panel.groupby(level="date", group_keys=False).apply(_rank_group)

    # 高缺失列剔除（降级而非全灭）
    keep_cols = [c for c in ranked.columns if float(ranked[c].notna().mean()) > 0.4]
    dropped_cols = [c for c in ranked.columns if c not in keep_cols]
    if dropped_cols:
        print(f"  ⚠️ 正交性降级: {len(dropped_cols)} 个高缺失因子不进 VIF/PCA: {dropped_cols}")
    rk = ranked[keep_cols]

    # VIF（横截面秩面板）
    vif = {}
    for f in factors:
        if f not in keep_cols:
            vif[f] = float(np.inf)   # 高缺失列无法估计
            continue
        others = [x for x in keep_cols if x != f]
        if not others:
            vif[f] = 1.0
            continue
        X = rk[others].dropna()
        y = rk[f].reindex(X.index).dropna()
        common = X.index.intersection(y.index)
        if len(common) < 10:
            vif[f] = float(np.inf)
            continue
        Xc = X.loc[common]
        yc = y.loc[common]
        try:
            from sklearn.linear_model import LinearRegression
            reg = LinearRegression().fit(Xc, yc)
            r2 = reg.score(Xc, yc)
            vif[f] = float(1 / (1 - r2)) if r2 < 1 else float(np.inf)
        except Exception as e:
            print(f"  ⚠️ VIF {f} 计算失败（可见告警，非静默）: {e}")
            vif[f] = float(np.inf)

    # PCA（横截面秩面板）
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    data = rk.dropna()
    if len(data) > 10:
        X_scaled = scaler.fit_transform(data)
        pca = PCA().fit(X_scaled)
        explained_var = [float(v) for v in pca.explained_variance_ratio_]
        # 解释 95% 方差所需因子数（int 强转：np.int64 非 JSON 可序列化，2026-11-08 事故）
        n_eff = float(int(np.sum(np.cumsum(explained_var) < 0.95) + 1))
        cond_num = float(np.linalg.cond(X_scaled))
    else:
        explained_var = []
        n_eff = 0.0
        cond_num = float(np.inf)

    return OrthogonalityReport(
        factor_corr=corr_matrix.to_dict(),
        max_abs_corr=max_abs,
        mean_abs_corr=mean_abs,
        vif=vif,
        pca_explained_var=explained_var,
        n_factors_eff=n_eff,
        condition_number=cond_num,
    )


# ══════════════════════════════════════════════════════════════
# 主验证流程
# ══════════════════════════════════════════════════════════════

def run_alpha_validation(
    data: Dict[str, pd.DataFrame],           # 股票 K 线数据
    factor_names: List[str],                 # 待验证因子
    strategy_names: List[str] = None,        # 待验证策略
    period_start: str = None,
    period_end: str = None,
    universe: str = "watchlist",
    cost_per_turn: float = 0.0015,
) -> AlphaValidationReport:
    """完整 α 验证流程"""
    
    engine = get_engine()
    composer = StrategyComposer()
    
    # 1. 计算因子面板
    print("📊 计算因子面板...")
    factor_panel = engine.compute_panel(data, factor_names)
    
    # 2. 计算下一期收益（前视一期）
    returns_dict = {}
    for symbol, df in data.items():
        close = df["close"]
        ret = close.pct_change().shift(-1)  # T+1 收益
        returns_dict[symbol] = ret.rename("next_ret")
    returns_panel = pd.concat(returns_dict, axis=0)
    returns_panel.index.names = ["symbol", "date"]
    
    # 3. IC 统计
    print("📈 计算 IC 统计...")
    ic_stats = []
    for fname in factor_names:
        if fname in factor_panel.columns:
            ic = compute_ic_stats(factor_panel, returns_panel, fname)
            ic_stats.append(ic)
            print(f"  {fname}: IC={ic.ic_mean:.4f}, ICIR={ic.icir:.3f}, P={ic.p_value:.4f}")
    
    # 4. 因子正交性
    print("🔗 检查因子正交性...")
    ortho = compute_orthogonality(factor_panel)
    print(f"  最大相关性: {ortho.max_abs_corr:.3f}, 有效因子数: {ortho.n_factors_eff:.1f}")
    
    # 5. 策略验证
    strategies = create_seed_strategies()
    if strategy_names is None:
        strategy_names = list(strategies.keys())
    
    turnover_results = []
    dsr_results = []
    
    for sname in strategy_names:
        if sname not in strategies:
            print(f"⚠️ 策略不存在: {sname}")
            continue
        
        strategy = strategies[sname]
        print(f"🎯 验证策略: {sname}")
        
        # 计算评分
        scores = composer.compute_scores(factor_panel, strategy)
        
        # 计算目标仓位（简化：每日等权）
        # warn/reduce 放行留痕，仅 block 拦截（与 composer.compute_positions 同口径，2026-11-10）
        valid_scores = scores[scores["gate_status"] != "block"]
        if valid_scores.empty:
            print(f"  无有效信号，跳过")
            continue
        
        # 构建目标权重面板
        positions_dict = {}
        for date in valid_scores.index.get_level_values("date").unique():
            day_scores = valid_scores.xs(date, level="date")
            symbols = day_scores.index.tolist()
            n = len(symbols)
            if n == 0:
                continue
            weight = 1.0 / n
            for sym in symbols:
                positions_dict[(sym, date)] = weight
        
        if not positions_dict:
            continue
        
        positions_df = pd.Series(positions_dict).rename("weight")
        positions_df.index.names = ["symbol", "date"]
        
        # 换手调整收益
        ta = compute_turnover_adjusted(sname, positions_df.to_frame(), returns_panel, cost_per_turn)
        turnover_results.append(ta)
        print(f"  毛收益: {ta.gross_annual_return:.2%}, 净收益: {ta.net_annual_return:.2%}, 换手: {ta.annual_turnover:.1%}")
        
        # DSR
        daily_portfolio_ret = (positions_df.unstack("symbol").shift(1) * returns_panel.unstack("symbol")).sum(axis=1).dropna()
        dsr = compute_dsr(sname, daily_portfolio_ret, n_trials=len(strategy_names))
        dsr_results.append(dsr)
        print(f"  夏普: {dsr.sharpe:.3f}, DSR: {dsr.dsr:.3f}, P(运气): {dsr.prob_sharpe_luck:.3f}")
    
    # 6. 汇总
    summary = {
        "最佳IC因子": max(ic_stats, key=lambda x: x.icir).factor if ic_stats else "N/A",
        "最佳净收益策略": max(turnover_results, key=lambda x: x.net_annual_return).strategy if turnover_results else "N/A",
        "最高DSR策略": max(dsr_results, key=lambda x: x.dsr).strategy if dsr_results else "N/A",
        "因子正交性": "良好" if ortho.max_abs_corr < 0.7 else "需关注",
        "有效因子数": f"{ortho.n_factors_eff:.0f}/{len(factor_names)}",
        "建议": "关注 ICIR>1 且 DSR>0 的因子/策略组合" if any(x.icir > 1 for x in ic_stats) else "因子预测力不足，需增强或替换",
    }
    
    report = AlphaValidationReport(
        generated_at=datetime.now().isoformat(),
        period={"start": period_start or "auto", "end": period_end or "auto"},
        universe=universe,
        n_stocks=len(data),
        n_days=len(next(iter(data.values()))),
        ic_stats=ic_stats,
        turnover_adjusted=turnover_results,
        dsr_results=dsr_results,
        orthogonality=ortho,
        summary=summary,
    )
    
    return report


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="用模拟数据跑通测试")
    ap.add_argument("--factors", nargs="+", help="指定因子列表")
    ap.add_argument("--strategies", nargs="+", help="指定策略列表")
    ap.add_argument("--start", help="开始日期")
    ap.add_argument("--end", help="结束日期")
    ap.add_argument("--cost", type=float, default=0.0015, help="单边交易成本")
    args = ap.parse_args()

    if args.test:
        print("🧪 生成模拟数据测试 α 验证...")
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=300, freq="D")
        symbols = ["000001", "000002", "600000", "600036", "000858", "600519"]

        data = {}
        for sym in symbols:
            base = 10 + np.random.rand() * 50
            close = base + np.cumsum(np.random.randn(300) * 0.5)
            high = close + np.abs(np.random.randn(300) * 0.3)
            low = close - np.abs(np.random.randn(300) * 0.3)
            open_ = close + np.random.randn(300) * 0.2
            volume = np.abs(np.random.randn(300) * 1e6 + 5e6)

            df = pd.DataFrame({
                "open": open_, "high": high, "low": low, "close": close, "volume": volume
            }, index=dates)
            data[sym] = df

        test_factors = args.factors or ["EMA_12", "EMA_26", "RSI_14", "MACD_hist", "ATR_14", "BB_pos_20", "volume_zscore_20", "OBV", "CCI_20"]
        test_strategies = args.strategies or ["ema_cross", "macd", "bollinger_atr", "adaptive_multi"]

        report = run_alpha_validation(
            data=data,
            factor_names=test_factors,
            strategy_names=test_strategies,
            period_start=args.start,
            period_end=args.end,
            cost_per_turn=args.cost,
        )

        # 保存报告
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = os.path.join(REPORT_DIR, f"alpha_validation_{timestamp}.json")
        md_path = os.path.join(REPORT_DIR, f"alpha_validation_{timestamp}.md")
        
        report.save_json(json_path)
        report.save_md(md_path)
        
        print(f"\n✅ 报告已保存:")
        print(f"  JSON: {json_path}")
        print(f"  MD:   {md_path}")
        
        # 打印摘要
        print("\n" + "="*60)
        print(report.summary)


if __name__ == "__main__":
    main()
