#!/usr/bin/env python3
"""
因子正交化模块 — PCA + 残差正交
==================================
核心功能：
  1. 识别高共线因子族（基于相关性矩阵聚类）
  2. 每族内做 PCA，保留解释方差 95% 的主成分
  3. 原因子对主成分回归取残差，得到正交因子
  4. 保留可解释性：输出因子族映射、载荷矩阵、解释方差
"""

import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, asdict
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LinearRegression
from pathlib import Path

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)
sys.path.insert(0, os.path.join(WORKSPACE, "analysis"))

ORTHO_DIR = os.path.join(WORKSPACE, "data", "factor_orthogonal")
os.makedirs(ORTHO_DIR, exist_ok=True)

# 预定义因子族（基于领域知识 + 相关性验证）
PREDEFINED_FAMILIES = {
    "EMA": ["EMA_12", "EMA_26", "EMA_60", "EMA_cross_12_26"],
    "MACD": ["MACD_hist", "MACD_signal"],
    "BB": ["BB_width_20", "BB_pos_20"],
    "ICHIMOKU": ["ichimoku_tenkan", "ichimoku_kijun", "ichimoku_span_a", "ichimoku_span_b"],
    "KELTNER": ["keltner_upper", "keltner_lower"],
    "DONCHIAN": ["donchian_upper", "donchian_lower"],
    "ATR": ["ATR_14", "ATR_20"],
    "RSI": ["RSI_14", "RSI_6"],
    "KDJ": ["KDJ_K", "KDJ_D"],
    "HV": ["HV_20", "HV_60"],
}


@dataclass
class FamilyResult:
    """单个因子族正交化结果"""
    family_name: str
    original_factors: List[str]
    kept_pcs: int
    explained_variance_ratio: List[float]
    cumulative_explained: float
    loadings: Dict[str, List[float]]  # factor -> PC loadings
    orthogonal_factors: List[str]  # 新因子名
    pca_model_path: str
    scaler_model_path: str


@dataclass
class OrthogonalizationReport:
    """正交化全局报告"""
    generated_at: str
    input_factors: List[str]
    families_detected: List[str]
    family_results: List[FamilyResult]
    max_abs_corr_before: float
    max_abs_corr_after: float
    mean_abs_corr_before: float
    mean_abs_corr_after: float
    orthogonal_panel_path: str
    report_path: str


def _ensure_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """确保所有列为数值型"""
    return df.apply(pd.to_numeric, errors="coerce")


def detect_families_by_correlation(
    factor_panel: pd.DataFrame,
    threshold: float = 0.7,
    min_family_size: int = 2
) -> Dict[str, List[str]]:
    """
    基于相关性矩阵自动检测高共线因子族
    :param factor_panel: MultiIndex(symbol, date) x factors
    :param threshold: 相关性阈值
    :param min_family_size: 最小族大小
    :return: {family_name: [factor_names]}
    """
    # 计算截面平均相关性
    factors = factor_panel.columns.tolist()
    daily_corrs = []
    
    for date in factor_panel.index.get_level_values("date").unique():
        day_data = factor_panel.xs(date, level="date")
        if len(day_data) < 10:
            continue
        corr = day_data.corr(method="spearman")
        daily_corrs.append(corr)
    
    if not daily_corrs:
        return {}
    
    # 平均相关性矩阵
    avg_corr = pd.concat(daily_corrs).groupby(level=0).mean()
    avg_corr = avg_corr.loc[factors, factors] if hasattr(avg_corr.index, 'levels') else avg_corr
    
    # 贪心聚类：从高相关性配对开始
    families = {}
    used = set()
    
    # 获取所有因子对的平均相关性
    pairs = []
    for i, f1 in enumerate(factors):
        for f2 in factors[i+1:]:
            if f1 in avg_corr.columns and f2 in avg_corr.columns:
                c = avg_corr.loc[f1, f2]
                if not np.isnan(c) and abs(c) >= threshold:
                    pairs.append((abs(c), f1, f2))
    
    pairs.sort(reverse=True)
    
    for _, f1, f2 in pairs:
        if f1 in used and f2 in used:
            continue
        if f1 in used:
            # 找到 f1 所在的族，加入 f2
            for fname, members in families.items():
                if f1 in members:
                    families[fname].append(f2)
                    used.add(f2)
                    break
        elif f2 in used:
            for fname, members in families.items():
                if f2 in members:
                    families[fname].append(f1)
                    used.add(f1)
                    break
        else:
            # 新族
            fname = f"auto_family_{len(families)+1}"
            families[fname] = [f1, f2]
            used.add(f1)
            used.add(f2)
    
    # 过滤太小的族
    families = {k: v for k, v in families.items() if len(v) >= min_family_size}
    
    return families


def orthogonalize_family(
    factor_panel: pd.DataFrame,
    family_name: str,
    factors: List[str],
    variance_threshold: float = 0.95
) -> FamilyResult:
    """
    对单个因子族做正交化
    :param factor_panel: 完整因子面板
    :param family_name: 族名
    :param factors: 该族包含的因子名列表
    :param variance_threshold: 保留解释方差比例
    :return: FamilyResult
    """
    # 提取该族数据
    available = [f for f in factors if f in factor_panel.columns]
    if len(available) < 2:
        raise ValueError(f"族 {family_name} 可用因子不足 2 个: {available}")
    
    # 按日期分组，每日做 PCA（截面）
    # First pass: collect explained variance ratios to determine global n_components
    all_explained = []
    all_loadings = []
    valid_dates = []
    
    for date in factor_panel.index.get_level_values("date").unique():
        day_data = factor_panel.xs(date, level="date")[available].dropna()
        if len(day_data) < 5:
            continue
        
        # 标准化
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(day_data)
        
        # PCA with full components
        pca = PCA()
        pca.fit(X_scaled)
        
        all_explained.append(pca.explained_variance_ratio_)
        all_loadings.append(pca.components_)
        valid_dates.append(date)
    
    if not all_explained:
        raise ValueError(f"族 {family_name} 没有足够的有效数据进行 PCA")
    
    # Determine global n_components based on average explained variance
    max_components = min(len(available), min(len(d) for d in all_explained))
    stacked_explained = np.stack([d[:max_components] for d in all_explained])  # (n_days, max_components)
    mean_explained = np.mean(stacked_explained, axis=0)  # (max_components,)
    cumsum = np.cumsum(mean_explained)
    n_components_global = int(np.searchsorted(cumsum, variance_threshold) + 1)
    n_components_global = min(n_components_global, max_components)
    
    # Second pass: extract loadings with fixed n_components_global
    daily_loadings = {}
    daily_explained = {}
    
    for date, loadings in zip(valid_dates, all_loadings):
        day_data = factor_panel.xs(date, level="date")[available].dropna()
        if len(day_data) < 5:
            continue
        daily_loadings[date] = loadings[:n_components_global]
        daily_explained[date] = all_explained[valid_dates.index(date)][:n_components_global]
    
    # 聚合载荷（取均值）
    if daily_loadings:
        # Stack all daily loadings and take mean
        stacked_loadings = np.stack(list(daily_loadings.values()), axis=0)  # (n_days, n_components, n_factors)
        mean_loadings = np.mean(stacked_loadings, axis=0)  # (n_components, n_factors)
    else:
        mean_loadings = np.array([]).reshape(0, 0)
    
    if daily_explained:
        stacked_explained = np.stack(list(daily_explained.values()), axis=0)  # (n_days, n_components)
        mean_explained = np.mean(stacked_explained, axis=0)  # (n_components,)
    else:
        mean_explained = np.array([])
    
    # 确定全局保留主成分数
    if len(mean_explained) > 0:
        cumsum = np.cumsum(mean_explained)
        n_components_global = int(np.searchsorted(cumsum, variance_threshold) + 1)
    else:
        n_components_global = 1
    
    # 保存 PCA 模型（使用最后一个有效日期的 scaler/pca 作为代表）
    last_date = valid_dates[-1] if valid_dates else None
    pca_model_path = ""
    scaler_model_path = ""
    
    if last_date:
        import joblib
        # 重新拟合最后一天的模型用于保存
        last_data = factor_panel.xs(last_date, level="date")[available].dropna()
        min_samples = min(5, len(available))  # Need at least n_features samples
        if len(last_data) >= min_samples:
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(last_data)
            pca = PCA(n_components=n_components_global)
            pca.fit(X_scaled)
            
            pca_path = os.path.join(ORTHO_DIR, f"pca_{family_name}.joblib")
            scaler_path = os.path.join(ORTHO_DIR, f"scaler_{family_name}.joblib")
            joblib.dump(pca, pca_path)
            joblib.dump(scaler, scaler_path)
            pca_model_path = pca_path
            scaler_model_path = scaler_path
        else:
            # Fallback: fit on all available data across all dates
            all_data_list = []
            for date in valid_dates:
                day_data = factor_panel.xs(date, level="date")[available].dropna()
                if len(day_data) >= min_samples:
                    all_data_list.append(day_data)
            if all_data_list:
                combined = pd.concat(all_data_list).drop_duplicates()
                if len(combined) >= min_samples:
                    scaler = StandardScaler()
                    X_scaled = scaler.fit_transform(combined)
                    pca = PCA(n_components=n_components_global)
                    pca.fit(X_scaled)
                    
                    pca_path = os.path.join(ORTHO_DIR, f"pca_{family_name}.joblib")
                    scaler_path = os.path.join(ORTHO_DIR, f"scaler_{family_name}.joblib")
                    joblib.dump(pca, pca_path)
                    joblib.dump(scaler, scaler_path)
                    pca_model_path = pca_path
                    scaler_model_path = scaler_path
    
    # 生成正交因子名
    ortho_factors = [f"{family_name}_PC{i+1}" for i in range(n_components_global)]
    
    # 计算每个原始因子在每个 PC 上的载荷
    loadings_dict = {}
    if mean_loadings.ndim == 2 and mean_loadings.shape[1] > 0:
        for i, factor in enumerate(available):
            if i < mean_loadings.shape[1]:
                loadings_dict[factor] = mean_loadings[:n_components_global, i].tolist()
            else:
                loadings_dict[factor] = [0.0] * n_components_global
    else:
        for factor in available:
            loadings_dict[factor] = [0.0] * n_components_global
    
    return FamilyResult(
        family_name=family_name,
        original_factors=available,
        kept_pcs=n_components_global,
        explained_variance_ratio=mean_explained[:n_components_global].tolist(),
        cumulative_explained=float(np.sum(mean_explained[:n_components_global])) if len(mean_explained) > 0 else 0.0,
        loadings=loadings_dict,
        orthogonal_factors=ortho_factors,
        pca_model_path=pca_model_path,
        scaler_model_path=scaler_model_path,
    )


def compute_orthogonal_panel(
    factor_panel: pd.DataFrame,
    family_results: List[FamilyResult],
    keep_original: bool = False
) -> pd.DataFrame:
    """
    使用训练好的 PCA 模型生成正交因子面板
    :param factor_panel: 原始因子面板
    :param family_results: 正交化结果
    :param keep_original: 是否保留原始因子
    :return: 正交化后的因子面板
    """
    results = {}
    
    # 加载保存的模型
    import joblib
    models = {}
    for fr in family_results:
        if fr.pca_model_path and os.path.exists(fr.pca_model_path):
            models[fr.family_name] = {
                "pca": joblib.load(fr.pca_model_path),
                "scaler": joblib.load(fr.scaler_model_path),
                "ortho_factors": fr.orthogonal_factors,
                "original_factors": fr.original_factors,
            }
    
    # 逐日变换
    all_dates = factor_panel.index.get_level_values("date").unique()
    ortho_dfs = []
    
    for date in all_dates:
        day_data = factor_panel.xs(date, level="date")
        day_result = day_data.copy() if keep_original else pd.DataFrame(index=day_data.index)
        
        for fam_name, model in models.items():
            orig_factors = model["original_factors"]
            available = [f for f in orig_factors if f in day_data.columns]
            if len(available) < 2:
                continue
            
            X = day_data[available].dropna()
            if len(X) < 5:
                continue
            
            # 标准化 + PCA 变换
            X_scaled = model["scaler"].transform(X)
            pcs = model["pca"].transform(X)[:, :len(model["ortho_factors"])]
            
            # 构建正交因子 DataFrame
            ortho_df = pd.DataFrame(
                pcs,
                index=X.index,
                columns=model["ortho_factors"]
            )
            day_result = day_result.join(ortho_df, how="left")
        
        # Restore MultiIndex for this date
        day_result.index = pd.MultiIndex.from_tuples(
            [(sym, date) for sym in day_result.index], names=["symbol", "date"]
        )
        ortho_dfs.append(day_result)
    
    if not ortho_dfs:
        return factor_panel if keep_original else pd.DataFrame()
    
    ortho_panel = pd.concat(ortho_dfs)
    ortho_panel.index.names = ["symbol", "date"]
    
    return ortho_panel


def compute_correlation_stats(panel: pd.DataFrame) -> Tuple[float, float]:
    """计算相关性统计"""
    factors = panel.columns.tolist()
    daily_corrs = []
    
    for date in panel.index.get_level_values("date").unique():
        day_data = panel.xs(date, level="date")
        if len(day_data) < 10:
            continue
        corr = day_data.corr(method="spearman")
        daily_corrs.append(corr)
    
    if not daily_corrs:
        return 0.0, 0.0
    
    avg_corr = pd.concat(daily_corrs).groupby(level=0).mean()
    
    # 上三角（不含对角线）
    mask = np.triu(np.ones(avg_corr.shape), k=1).astype(bool)
    upper_vals = avg_corr.where(mask).stack().abs().dropna()
    
    return upper_vals.max(), upper_vals.mean()


def run_orthogonalization(
    factor_panel: pd.DataFrame,
    use_predefined: bool = True,
    variance_threshold: float = 0.95,
    corr_threshold: float = 0.7,
    keep_original: bool = False
) -> OrthogonalizationReport:
    """
    主入口：完整正交化流程
    """
    print(f"🔧 开始因子正交化: {len(factor_panel.columns)} 个因子, {factor_panel.index.get_level_values('date').nunique()} 个交易日")
    
    # 1. 计算正交化前相关性
    max_before, mean_before = compute_correlation_stats(factor_panel)
    print(f"  正交化前: max|corr|={max_before:.3f}, mean|corr|={mean_before:.3f}")
    
    # 2. 检测/使用因子族
    if use_predefined:
        families = {k: v for k, v in PREDEFINED_FAMILIES.items() 
                    if any(f in factor_panel.columns for f in v)}
        # 过滤只保留面板中存在的因子
        families = {k: [f for f in v if f in factor_panel.columns] for k, v in families.items()}
        families = {k: v for k, v in families.items() if len(v) >= 2}
    else:
        families = detect_families_by_correlation(factor_panel, threshold=0.7)
    
    print(f"  检测到 {len(families)} 个高共线因子族: {list(families.keys())}")
    for k, v in families.items():
        print(f"    {k}: {v}")
    
    # 3. 逐族正交化
    family_results = []
    for fam_name, factors in families.items():
        print(f"  正交化族: {fam_name} ({len(factors)} 因子)")
        try:
            result = orthogonalize_family(factor_panel, fam_name, factors, variance_threshold=0.95)
            family_results.append(result)
            print(f"    保留 {result.kept_pcs} 个 PC, 累计解释方差: {result.cumulative_explained:.2%}")
            print(f"    新因子: {result.orthogonal_factors}")
        except Exception as e:
            print(f"    ❌ 失败: {e}")
    
    # 4. 生成正交化面板
    print("  生成正交化因子面板...")
    ortho_panel = compute_orthogonal_panel(factor_panel, family_results, keep_original=keep_original)
    
    # 5. 计算正交化后相关性
    max_after, mean_after = compute_correlation_stats(ortho_panel)
    print(f"  正交化后: max|corr|={max_after:.3f}, mean|corr|={mean_after:.3f}")
    
    # 5. 保存结果
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    panel_path = os.path.join(ORTHO_DIR, f"orthogonal_panel_{timestamp}.parquet")
    ortho_panel.to_parquet(panel_path, compression="snappy")
    
    # 保存报告
    report = OrthogonalizationReport(
        generated_at=datetime.now().isoformat(),
        input_factors=factor_panel.columns.tolist(),
        families_detected=list(families.keys()),
        family_results=family_results,
        max_abs_corr_before=max_before,
        max_abs_corr_after=max_after,
        mean_abs_corr_before=mean_before,
        mean_abs_corr_after=mean_after,
        orthogonal_panel_path=panel_path,
        report_path="",
    )
    
    report_path = os.path.join(ORTHO_DIR, f"orthogonalization_report_{timestamp}.json")
    if os.path.exists(report_path):
        import shutil
        shutil.copy2(report_path, report_path + ".bak")  # 写前备份（backup_guard 铁律）
    with open(report_path, "w", encoding="utf-8") as f:
        # 序列化 FamilyResult
        def fr_to_dict(fr):
            d = asdict(fr)
            return d
        
        json.dump({
            "generated_at": report.generated_at,
            "input_factors": report.input_factors,
            "families_detected": report.families_detected,
            "family_results": [fr_to_dict(fr) for fr in family_results],
            "max_abs_corr_before": report.max_abs_corr_before,
            "max_abs_corr_after": report.max_abs_corr_after,
            "mean_abs_corr_before": report.mean_abs_corr_before,
            "mean_abs_corr_after": report.mean_abs_corr_after,
            "orthogonal_panel_path": report.orthogonal_panel_path,
        }, f, ensure_ascii=False, indent=2, default=str)
    
    report.report_path = report_path
    print(f"✅ 正交化完成: 面板 {ortho_panel.shape} -> {panel_path}")
    print(f"📋 报告: {report_path}")
    
    return report


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-panel", help="输入因子面板 parquet 路径")
    ap.add_argument("--variance", type=float, default=0.95, help="PCA 保留方差阈值")
    ap.add_argument("--corr-threshold", type=float, default=0.7, help="相关性阈值（自动检测模式）")
    ap.add_argument("--keep-original", action="store_true", help="保留原始因子")
    ap.add_argument("--no-predefined", action="store_true", help="不使用预定义族，自动检测")
    args = ap.parse_args()
    
    if args.input_panel:
        factor_panel = pd.read_parquet(args.input_panel)
    else:
        # 默认从 factor_engine 生成
        from analysis.factor_engine import get_engine
        from analysis.fib_extension_scan import WATCHLIST
        
        symbols = list(WATCHLIST.keys())[:10]  # 测试用前 10 只
        data = {}
        for sym in symbols:
            df = pd.read_csv(f"{os.getenv('WORKSPACE', '/Users/duguke/.openclaw/workspace')}/data/stock_data/{sym}.csv")
            df.columns = [c.lower() for c in df.columns]
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")[["open","high","low","close","volume"]].sort_index()
            data[sym] = df
        
        engine = get_engine()
        reg = __import__("analysis.factor_registry", fromlist=["get_registry"]).get_registry()
        all_factors = reg.get_names()
        factor_panel = engine.compute_panel(data, all_factors)
    
    report = run_orthogonalization(
        factor_panel,
        use_predefined=not args.no_predefined,
        variance_threshold=args.variance,
        corr_threshold=args.corr_threshold,
        keep_original=args.keep_original,
    )
    
    print("\n" + "="*60)
    print("正交化总结:")
    print(f"  输入因子: {len(report.input_factors)}")
    print(f"  检测族数: {len(report.families_detected)}")
    print(f"  正交化前 max|corr|: {report.max_abs_corr_before:.3f}")
    print(f"  正交化后 max|corr|: {report.max_abs_corr_after:.3f}")
    print(f"  正交化前 mean|corr|: {report.mean_abs_corr_before:.3f}")
    print(f"  正交化后 mean|corr|: {report.mean_abs_corr_after:.3f}")
    print(f"  输出面板: {report.orthogonal_panel_path}")
    print(f"  报告: {report.report_path}")


if __name__ == "__main__":
    main()