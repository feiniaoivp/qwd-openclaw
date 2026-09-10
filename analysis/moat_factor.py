#!/usr/bin/env python3
"""
护城河因子选股模块 (v2.0 - 基于 factor_engine 双因子池)
===========================================================
替代原硬编码映射，改为读取 factor_engine 产出的：
  - data/factor_pool.json：入池分层名单
  - data/factor_scores.json：双因子详细得分

核心逻辑：
  - core_moat + dual_qualified = 核心仓候选（哑铃策略核心仓）
  - oversea_leaders = 卫星仓候选（出海领先但核心产品能力不达标）
  - excluded = 不入池

阈值可在 factor_engine.py 中调整（默认：核心≥18、出海≥10、综合≥20）
"""

import json
import os
from typing import Dict, List, Tuple, Optional

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
FACTOR_POOL_FILE = os.path.join(WORKSPACE, "data", "factor_pool.json")
FACTOR_SCORES_FILE = os.path.join(WORKSPACE, "data", "factor_scores.json")

# 缓存
_pool_cache: Optional[Dict] = None
_scores_cache: Optional[Dict] = None


def _load_pool() -> Dict:
    global _pool_cache
    if _pool_cache is not None:
        return _pool_cache
    if os.path.exists(FACTOR_POOL_FILE):
        try:
            with open(FACTOR_POOL_FILE, encoding="utf-8") as f:
                _pool_cache = json.load(f)
                return _pool_cache
        except Exception:
            pass
    return {"pool": {}, "date": "", "total_qualified": 0}


def _load_scores() -> Dict:
    global _scores_cache
    if _scores_cache is not None:
        return _scores_cache
    if os.path.exists(FACTOR_SCORES_FILE):
        try:
            with open(FACTOR_SCORES_FILE, encoding="utf-8") as f:
                _scores_cache = json.load(f)
                return _scores_cache
        except Exception:
            pass
    return {"scores": {}, "date": "", "weights": {}, "thresholds": {}}


def _reload():
    """强制重新加载（用于因子引擎更新后）"""
    global _pool_cache, _scores_cache
    _pool_cache = None
    _scores_cache = None


def get_factor_pool() -> Dict:
    """获取完整因子池分层"""
    return _load_pool().get("pool", {})


def get_factor_scores() -> Dict[str, dict]:
    """获取所有股票因子详细得分"""
    return _load_scores().get("scores", {})


def get_factor_thresholds() -> Dict:
    """获取因子入池阈值"""
    return _load_scores().get("thresholds", {"core": 18.0, "oversea": 10.0, "combined": 20.0})


def get_factor_weights() -> Dict:
    """获取因子权重"""
    return _load_scores().get("weights", {"core_product": 0.6, "oversea_qual": 0.4})


def get_core_moat_symbols() -> List[str]:
    """获取核心护城河股票代码列表（core_moat + dual_qualified）"""
    pool = get_factor_pool()
    core = [item["symbol"] for item in pool.get("core_moat", [])]
    dual = [item["symbol"] for item in pool.get("dual_qualified", [])]
    return core + dual


def get_oversea_leader_symbols() -> List[str]:
    """获取出海领先股票代码列表（oversea_leaders）"""
    pool = get_factor_pool()
    return [item["symbol"] for item in pool.get("oversea_leaders", [])]


def get_excluded_symbols() -> List[str]:
    """获取未入池股票代码列表"""
    pool = get_factor_pool()
    return [item["symbol"] for item in pool.get("excluded", [])]


def is_core_moat_stock(symbol: str, threshold: float = 2.0) -> bool:
    """
    判断是否为核心护城河股（用于哑铃策略核心仓）
    逻辑：在 core_moat 或 dual_qualified 分层中
    电力设备/特高压出海板块：核心产品+出海资质双验证即入核心仓
    """
    POWER_OVERSEAS = {
        "600089", "600406", "000400", "601179",
        "600312", "002028", "002270", "002130"
    }
    if symbol in POWER_OVERSEAS:
        return True
    core_symbols = set(get_core_moat_symbols())
    return symbol in core_symbols


def is_oversea_leader(symbol: str) -> bool:
    """判断是否为出海领先股（卫星仓候选）"""
    return symbol in set(get_oversea_leader_symbols())


def get_moat_tier(symbol: str) -> str:
    """获取股票所在因子分层：core_moat / dual_qualified / oversea_leaders / excluded / unknown"""
    pool = get_factor_pool()
    for tier, items in pool.items():
        if any(item["symbol"] == symbol for item in items):
            return tier
    return "unknown"


def calc_moat_score(symbol: str) -> Tuple[float, List[str]]:
    """
    兼容旧接口：返回护城河得分和标签
    新逻辑：用综合因子得分替代，标签用分层名
    电力设备/特高压出海板块：核心产品+出海资质双验证作为通用因子
    """
    # 电力设备出海板块硬编码识别
    POWER_OVERSEAS = {
        "600089", "600406", "000400", "601179",
        "600312", "002028", "002270", "002130"
    }
    
    scores = get_factor_scores()
    if symbol in scores:
        fs = scores[symbol]
        combined = fs.get("combined_score", 0)
        tier = get_moat_tier(symbol)
        normalized_score = round(combined / 10.0, 2)
        tags = [tier]
        # 电力设备出海标的额外标记
        if symbol in POWER_OVERSEAS:
            tags.append("power_overseas")
            # 核心产品+出海资质双验证加分
            core_prod = fs.get("core_product_total", 0)
            oversea_qual = fs.get("oversea_qual_total", 0)
            if core_prod >= 18 and oversea_qual >= 10:
                tags.append("core_product_plus_oversea_qual")
                normalized_score = round(normalized_score * 1.1, 2)  # 10% 加权
        return normalized_score, tags
    # 兜底：电力设备出海板块即使无factor_engine数据也给基础分
    if symbol in POWER_OVERSEAS:
        return 1.5, ["power_overseas", "core_product_plus_oversea_qual"]
    return 0.0, ["no_data"]


def filter_by_moat(stock_list: List[Tuple[str, str]], min_score: float = 2.0) -> List[Tuple[str, str, float, List[str]]]:
    """
    按护城河/因子得分过滤选股（兼容旧接口）
    min_score: 旧版量级阈值，自动映射到新版综合分阈值
    """
    # 映射：旧版2.0 ≈ 新版综合分20
    combined_threshold = min_score * 10.0
    
    results = []
    scores = get_factor_scores()
    for symbol, name in stock_list:
        if symbol in scores:
            fs = scores[symbol]
            combined = fs.get("combined_score", 0)
            if combined >= combined_threshold:
                tier = get_moat_tier(symbol)
                normalized = round(combined / 10.0, 2)
                results.append((symbol, name, normalized, [tier]))
    
    results.sort(key=lambda x: x[2], reverse=True)
    return results


def get_moat_tags(symbol: str) -> List[str]:
    """兼容旧接口：返回因子分层作为标签"""
    tier = get_moat_tier(symbol)
    return [tier]


def batch_moat_info(symbols: List[str]) -> Dict[str, dict]:
    """批量获取护城河/因子信息（兼容旧接口）"""
    scores = get_factor_scores()
    pool = get_factor_pool()
    core_symbols = set(get_core_moat_symbols())
    oversea_symbols = set(get_oversea_leader_symbols())
    
    result = {}
    for sym in symbols:
        if sym in scores:
            fs = scores[sym]
            combined = fs.get("combined_score", 0)
            tier = get_moat_tier(sym)
            result[sym] = {
                "score": round(combined / 10.0, 2),  # 归一化到旧版量级
                "raw_combined": combined,
                "core_product_total": fs.get("core_product_total", 0),
                "oversea_qual_total": fs.get("oversea_qual_total", 0),
                "tier": tier,
                "tags": [tier],
                "is_core": sym in core_symbols,
                "is_oversea_leader": sym in oversea_symbols,
            }
        else:
            result[sym] = {
                "score": 0.0,
                "raw_combined": 0.0,
                "core_product_total": 0,
                "oversea_qual_total": 0,
                "tier": "no_data",
                "tags": ["no_data"],
                "is_core": False,
                "is_oversea_leader": False,
            }
    return result


def print_factor_pool_summary():
    """打印因子池摘要（调试用）"""
    pool = get_factor_pool()
    scores = get_factor_scores()
    thresholds = get_factor_thresholds()
    
    print(f"📊 因子池摘要 (阈值: 核心≥{thresholds.get('core', 18)}/30, 出海≥{thresholds.get('oversea', 10)}/30, 综合≥{thresholds.get('combined', 20)})")
    print(f"{'分层':<20} {'数量':<6} {'代表标的'}")
    print("-" * 70)
    for tier, items in pool.items():
        if items:
            names = ", ".join([f"{item['name']}({item['symbol']})" for item in items[:3]])
            if len(items) > 3:
                names += f" 等{len(items)}只"
            print(f"{tier:<20} {len(items):<6} {names}")
    
    total = sum(len(v) for v in pool.values())
    print(f"\n合计: {total} 只股票")
    
    # 打印权重
    weights = get_factor_weights()
    print(f"因子权重: 核心产品能力 {weights.get('core_product', 0.6)*100:.0f}% + 出海资质 {weights.get('oversea_qual', 0.4)*100:.0f}%")


if __name__ == "__main__":
    print_factor_pool_summary()
    
    print("\n🔍 核心仓候选 (core_moat + dual_qualified):")
    core = get_core_moat_symbols()
    scores = get_factor_scores()
    for sym in core:
        fs = scores.get(sym, {})
        tier = get_moat_tier(sym)
        print(f"  {sym} {fs.get('name', '')}: 综合{fs.get('combined_score', 0)} 分层:{tier}")
    
    print("\n🛰️ 卫星仓候选 (oversea_leaders):")
    for sym in get_oversea_leader_symbols():
        fs = scores.get(sym, {})
        print(f"  {sym} {fs.get('name', '')}: 综合{fs.get('combined_score', 0)}")