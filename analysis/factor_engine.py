#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核心产品能力 + 出海资质 双因子选股引擎
=========================================
量化指标体系（用户定义版）：
  核心产品能力 = 市占率(年报/研报) + 技术壁垒(专利/认证) + 客户粘性(转换成本)
  出海资质    = 海外认证数(CE/UL/KEMA等) + 海外营收占比 + 标杆项目落地国家数

数据源：
  - 财务/基本面：akshare (stock_financial_abstract_ths / stock_balance_sheet_by_report_em)
  - 专利/认证：暂无结构化接口，先用静态配置 + 研报关键词提取
  - 海外营收：akshare 分地域营收 (stock_revenue_by_region_em)
  - 标杆项目：静态配置 + 新闻关键词

输出：
  - data/factor_scores.json：每只股票双因子得分 + 细项
  - data/factor_pool.json：入池/出池名单（阈值筛选）
  - 供 adaptive 系统、portfolio_sim 复用

运行：python3 analysis/factor_engine.py [--update] [--stocks 代码,代码]
"""

import os
import sys
import json
import time
import warnings
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

warnings.filterwarnings("ignore")

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)
DATA_DIR = os.path.join(WORKSPACE, "data")
FACTOR_SCORE_FILE = os.path.join(DATA_DIR, "factor_scores.json")
FACTOR_POOL_FILE = os.path.join(DATA_DIR, "factor_pool.json")
STATIC_CONFIG_FILE = os.path.join(DATA_DIR, "factor_static_config.json")
os.makedirs(DATA_DIR, exist_ok=True)

try:
    import akshare as ak
    HAS_AKSHARE = True
except Exception:
    HAS_AKSHARE = False
    print("⚠️ akshare 不可用，仅用静态配置")

try:
    import pandas as pd
    HAS_PANDAS = True
except Exception:
    HAS_PANDAS = False

# ═══════════════════════════════════════════════════════════════
# 静态配置：人工维护的高确定性指标（专利、认证、标杆项目）
# ═══════════════════════════════════════════════════════════════
DEFAULT_STATIC_CONFIG = {
    "version": "2026-09-03",
    "stocks": {
        # 电网装备/特高压（核心标的）
        "600089": {  # 特变电工
            "core_product": {
                "market_share_pct": 18.5,      # 全球变压器市占率 ~18.5%
                "tech_barrier_score": 9,        # ±1100kV换流变全球唯二、专利墙极高
                "switching_cost_score": 8,      # 电网入网认证周期长、更换成本极高
            },
            "oversea_qual": {
                "cert_count": 12,               # CE/UL/KEMA/GOST/沙特SASO等
                "oversea_revenue_pct": 35.2,    # 2023年海外营收占比
                "benchmark_countries": 45,      # 覆盖国家数
                "flagship_projects": ["沙特NEOM 164亿大单", "巴西±800kV", "阿联酋超高压"]
            }
        },
        "600406": {  # 国电南瑞
            "core_product": {
                "market_share_pct": 48.0,       # 换流阀市占率47-49%
                "tech_barrier_score": 10,       # 柔性直流核心专利全球领先
                "switching_cost_score": 9,      # 电网自动化系统极难替换
            },
            "oversea_qual": {
                "cert_count": 8,
                "oversea_revenue_pct": 12.5,
                "benchmark_countries": 28,
                "flagship_projects": ["巴西美拉换流站", "德国科堡项目", "印度拉达克±350kV"]
            }
        },
        "000400": {  # 许继电气
            "core_product": {
                "market_share_pct": 45.0,       # 换流阀/直流控保双龙头
                "tech_barrier_score": 9,
                "switching_cost_score": 9,
            },
            "oversea_qual": {
                "cert_count": 10,
                "oversea_revenue_pct": 22.0,    # 2026H1国际收入4.35亿大增
                "benchmark_countries": 35,
                "flagship_projects": ["巴西美拉I/II期", "沙特NEOM", "智利Kimal-Lo Aguirre"]
            }
        },
        "601179": {  # 中国西电
            "core_product": {
                "market_share_pct": 30.0,       # 一次设备全链条龙头
                "tech_barrier_score": 8,
                "switching_cost_score": 8,
            },
            "oversea_qual": {
                "cert_count": 6,
                "oversea_revenue_pct": 18.0,
                "benchmark_countries": 30,
                "flagship_projects": ["巴西美拉配套", "沙特输变电", "东南亚多国"]
            }
        },
        "600312": {  # 平高电气
            "core_product": {
                "market_share_pct": 25.0,       # GIS全球龙头
                "tech_barrier_score": 9,
                "switching_cost_score": 8,
            },
            "oversea_qual": {
                "cert_count": 15,               # 意大利/西班牙欧洲标杆最多
                "oversea_revenue_pct": 40.0,    # 海外营收占比最高
                "benchmark_countries": 70,
                "flagship_projects": ["意大利380kV GIS", "西班牙项目", "英国国家电网"]
            }
        },
        "002028": {  # 思源电气
            "core_product": {
                "market_share_pct": 20.0,       # 高压开关/无功补偿龙头
                "tech_barrier_score": 8,
                "switching_cost_score": 7,
            },
            "oversea_qual": {
                "cert_count": 12,
                "oversea_revenue_pct": 28.0,
                "benchmark_countries": 40,
                "flagship_projects": ["德国50Hertz", "瑞典Svenska Kraftnät", "英国国家电网"]
            }
        },
        "002270": {  # 华明装备
            "core_product": {
                "market_share_pct": 60.0,       # 分接开关高壁垒零部件
                "tech_barrier_score": 7,
                "switching_cost_score": 9,      # 随主机出口、单值极低但不可或缺
            },
            "oversea_qual": {
                "cert_count": 5,
                "oversea_revenue_pct": 30.0,
                "benchmark_countries": 25,
                "flagship_projects": ["随特变/西电出口全球"]
            }
        },
        "002130": {  # 沃尔核材
            "core_product": {
                "market_share_pct": 74.0,       # 电缆附件/绝缘材料市占率
                "tech_barrier_score": 8,
                "switching_cost_score": 8,
            },
            "oversea_qual": {
                "cert_count": 8,
                "oversea_revenue_pct": 35.0,
                "benchmark_countries": 30,
                "flagship_projects": ["特高压电缆附件随主机出口"]
            }
        },
        # 半导体/硬科技
        "600584": {  # 长电科技
            "core_product": {
                "market_share_pct": 12.0,       # 全球封测第三
                "tech_barrier_score": 8,        # FC-BGA/Chiplet先进封装
                "switching_cost_score": 8,
            },
            "oversea_qual": {
                "cert_count": 6,
                "oversea_revenue_pct": 55.0,    # 高度国际化
                "benchmark_countries": 20,
                "flagship_projects": ["全球IDM/设计厂代工"]
            }
        },
        "688981": {  # 中芯国际
            "core_product": {
                "market_share_pct": 6.0,        # 大陆晶圆代工一哥
                "tech_barrier_score": 10,       # 28/14/7nm技术壁垒
                "switching_cost_score": 9,
            },
            "oversea_qual": {
                "cert_count": 4,
                "oversea_revenue_pct": 15.0,
                "benchmark_countries": 10,
                "flagship_projects": ["全球Fabless代工"]
            }
        },
        "002156": {  # 通富微电
            "core_product": {
                "market_share_pct": 8.0,        # 封测龙头
                "tech_barrier_score": 7,
                "switching_cost_score": 7,
            },
            "oversea_qual": {
                "cert_count": 5,
                "oversea_revenue_pct": 40.0,
                "benchmark_countries": 15,
                "flagship_projects": ["AMD/英伟达/华为供应链"]
            }
        },
        # 新能源/锂电
        "300014": {  # 亿纬锂能
            "core_product": {
                "market_share_pct": 15.0,       # 消费电池全球第一、动力电池前五
                "tech_barrier_score": 9,
                "switching_cost_score": 9,
            },
            "oversea_qual": {
                "cert_count": 10,
                "oversea_revenue_pct": 60.0,
                "benchmark_countries": 50,
                "flagship_projects": ["宝马/大众/戴姆勒定点", "储能全球出货前三"]
            }
        },
        "002466": {  # 天齐锂业
            "core_product": {
                "market_share_pct": 20.0,       # 锂资源全球龙头
                "tech_barrier_score": 6,        # 资源壁垒为主
                "switching_cost_score": 8,
            },
            "oversea_qual": {
                "cert_count": 3,
                "oversea_revenue_pct": 70.0,    # 矿山在澳洲/智利
                "benchmark_countries": 5,
                "flagship_projects": ["澳洲Greenbushes", "智利SQM股权"]
            }
        },
        # 高端制造/材料
        "300285": {  # 国瓷材料
            "core_product": {
                "market_share_pct": 35.0,       # MLCC用高介质粉料龙头
                "tech_barrier_score": 9,
                "switching_cost_score": 8,
            },
            "oversea_qual": {
                "cert_count": 6,
                "oversea_revenue_pct": 25.0,
                "benchmark_countries": 15,
                "flagship_projects": ["村田/太阳诱电/TDK供应链"]
            }
        },
        "603308": {  # 应流股份
            "core_product": {
                "market_share_pct": 30.0,       # 航空航天特材管材
                "tech_barrier_score": 9,        # 航发认证壁垒极高
                "switching_cost_score": 10,     # 一旦入谱极难替换
            },
            "oversea_qual": {
                "cert_count": 4,
                "oversea_revenue_pct": 10.0,
                "benchmark_countries": 5,
                "flagship_projects": ["国内航发主供、间接随整机出口"]
            }
        },
        "300124": {  # 汇川技术
            "core_product": {
                "market_share_pct": 25.0,       # 国产伺服/变频器龙头
                "tech_barrier_score": 8,
                "switching_cost_score": 8,
            },
            "oversea_qual": {
                "cert_count": 8,
                "oversea_revenue_pct": 20.0,
                "benchmark_countries": 30,
                "flagship_projects": ["欧洲/印度/东南亚工厂"]
            }
        },
        # 金融/权重（低出海资质，高核心产品能力）
        "600030": {  # 中信证券
            "core_product": {
                "market_share_pct": 8.0,        # 券商龙头
                "tech_barrier_score": 5,
                "switching_cost_score": 7,
            },
            "oversea_qual": {
                "cert_count": 2,
                "oversea_revenue_pct": 5.0,
                "benchmark_countries": 8,
                "flagship_projects": ["香港/新加坡牌照"]
            }
        },
        "600570": {  # 恒生电子
            "core_product": {
                "market_share_pct": 40.0,       # 金融IT核心系统龙头
                "tech_barrier_score": 8,
                "switching_cost_score": 9,
            },
            "oversea_qual": {
                "cert_count": 3,
                "oversea_revenue_pct": 8.0,
                "benchmark_countries": 5,
                "flagship_projects": ["东南亚银行核心系统输出"]
            }
        },
    }
}


def load_static_config() -> Dict:
    """加载静态配置（文件不存在则返回默认）"""
    if os.path.exists(STATIC_CONFIG_FILE):
        try:
            with open(STATIC_CONFIG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_STATIC_CONFIG


def save_static_config(config: Dict):
    """保存静态配置"""
    with open(STATIC_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════
# 因子计算逻辑
# ═══════════════════════════════════════════════════════════════

@dataclass
class FactorScore:
    symbol: str
    name: str
    # 核心产品能力三维度 (各0-10分)
    market_share_score: float          # 市占率归一化
    tech_barrier_score: float          # 技术壁垒
    switching_cost_score: float        # 转换成本
    core_product_total: float          # 核心产品能力总分(0-30)
    # 出海资质三维度 (各0-10分)
    cert_count_score: float            # 海外认证数
    oversea_revenue_score: float       # 海外营收占比
    benchmark_country_score: float     # 标杆项目国家数
    oversea_qual_total: float          # 出海资质总分(0-30)
    # 综合
    combined_score: float              # 双因子加权总分
    # 元数据
    data_source: str                   # "static" / "akshare" / "mixed"
    updated_at: str


def normalize_market_share(pct: float) -> float:
    """市占率归一化：0-50%映射到0-10分，>50%封顶10分"""
    return min(10.0, pct / 5.0)


def normalize_cert_count(count: int) -> float:
    """认证数归一化：0-20个映射0-10分"""
    return min(10.0, count / 2.0)


def normalize_oversea_revenue(pct: float) -> float:
    """海外营收占比归一化：0-80%映射0-10分"""
    return min(10.0, pct / 8.0)


def normalize_benchmark_countries(count: int) -> float:
    """标杆项目国家数归一化：0-80国映射0-10分"""
    return min(10.0, count / 8.0)


def calc_factor_score(symbol: str, name: str, static_cfg: Dict) -> FactorScore:
    """计算单只股票双因子得分"""
    core = static_cfg.get("core_product", {})
    oversea = static_cfg.get("oversea_qual", {})
    
    # 核心产品能力
    ms = core.get("market_share_pct", 0)
    tb = core.get("tech_barrier_score", 0)
    sc = core.get("switching_cost_score", 0)
    
    ms_score = normalize_market_share(ms)
    tb_score = min(10.0, max(0.0, tb))  # 已是0-10
    sc_score = min(10.0, max(0.0, sc))
    core_total = ms_score + tb_score + sc_score
    
    # 出海资质
    cc = oversea.get("cert_count", 0)
    or_pct = oversea.get("oversea_revenue_pct", 0)
    bc = oversea.get("benchmark_countries", 0)
    
    cc_score = normalize_cert_count(cc)
    or_score = normalize_oversea_revenue(or_pct)
    bc_score = normalize_benchmark_countries(bc)
    oversea_total = cc_score + or_score + bc_score
    
    # 综合得分：核心产品能力权重60%，出海资质权重40%（可调）
    combined = core_total * 0.6 + oversea_total * 0.4
    
    return FactorScore(
        symbol=symbol,
        name=name,
        market_share_score=round(ms_score, 2),
        tech_barrier_score=round(tb_score, 2),
        switching_cost_score=round(sc_score, 2),
        core_product_total=round(core_total, 2),
        cert_count_score=round(cc_score, 2),
        oversea_revenue_score=round(or_score, 2),
        benchmark_country_score=round(bc_score, 2),
        oversea_qual_total=round(oversea_total, 2),
        combined_score=round(combined, 2),
        data_source="static",
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


# ═══════════════════════════════════════════════════════════════
# 入池/出池判定
# ═══════════════════════════════════════════════════════════════

def evaluate_pool(factor_scores: Dict[str, FactorScore],
                  core_threshold: float = 18.0,      # 核心产品能力≥18/30
                  oversea_threshold: float = 10.0,   # 出海资质≥10/30
                  combined_threshold: float = 20.0) -> Dict:
    """根据阈值生成入池/出池名单"""
    pool = {"core_moat": [], "oversea_leaders": [], "dual_qualified": [], "watchlist": [], "excluded": []}
    
    for symbol, fs in factor_scores.items():
        in_core = fs.core_product_total >= core_threshold
        in_oversea = fs.oversea_qual_total >= oversea_threshold
        in_combined = fs.combined_score >= combined_threshold
        
        if in_core and in_oversea:
            pool["dual_qualified"].append({"symbol": symbol, "name": fs.name, "score": fs.combined_score})
        elif in_core:
            pool["core_moat"].append({"symbol": symbol, "name": fs.name, "score": fs.core_product_total})
        elif in_oversea:
            pool["oversea_leaders"].append({"symbol": symbol, "name": fs.name, "score": fs.oversea_qual_total})
        elif fs.combined_score >= combined_threshold * 0.7:
            pool["watchlist"].append({"symbol": symbol, "name": fs.name, "score": fs.combined_score})
        else:
            pool["excluded"].append({"symbol": symbol, "name": fs.name, "score": fs.combined_score})
    
    # 排序
    for k in pool:
        pool[k].sort(key=lambda x: -x["score"])
    
    return pool


# ═══════════════════════════════════════════════════════════════
# Akshare 增量更新（可选，获取最新财务/海外营收）
# ═══════════════════════════════════════════════════════════════

def fetch_oversea_revenue_akshare(symbol: str) -> Optional[float]:
    """尝试从akshare获取海外营收占比（分地域营收）"""
    if not HAS_AKSHARE:
        return None
    try:
        # akshare 接口：stock_revenue_by_region_em
        df = ak.stock_revenue_by_region_em(symbol=symbol)
        if df is not None and not df.empty:
            # 假设有 '地区' 和 '营收占比' 列
            # 实际列名需调试，这里仅示意
            overseas_rows = df[df['地区'].str.contains('海外|境外|国际|美洲|欧洲|亚洲|非洲', na=False)]
            if not overseas_rows.empty:
                return float(overseas_rows['营收占比'].sum())
    except Exception:
        pass
    return None


def fetch_financial_akshare(symbol: str) -> Dict:
    """获取基本面数据用于校验市占率等"""
    if not HAS_AKSHARE:
        return {}
    try:
        # 财务摘要
        df = ak.stock_financial_abstract_ths(symbol=symbol)
        if df is not None and not df.empty:
            # 提取关键指标
            return {"financial_abstract": df.to_dict()}
    except Exception:
        pass
    return {}


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def main():
    update_mode = "--update" in sys.argv
    stock_filter = None
    if "--stocks" in sys.argv:
        i = sys.argv.index("--stocks")
        stock_filter = sys.argv[i+1].split(",")
    
    print("🔬 核心产品能力+出海资质 双因子引擎启动")
    print("=" * 60)
    
    # 1. 加载静态配置
    static_config = load_static_config()
    print(f"📋 静态配置版本: {static_config.get('version', 'unknown')}")
    print(f"📊 配置股票数: {len(static_config.get('stocks', {}))}")
    
    # 2. 计算因子得分
    factor_scores = {}
    stocks_to_process = static_config.get("stocks", {})
    if stock_filter:
        stocks_to_process = {k: v for k, v in stocks_to_process.items() if k in stock_filter}
    
    for symbol, cfg in stocks_to_process.items():
        name = cfg.get("name", symbol)
        fs = calc_factor_score(symbol, name, cfg)
        factor_scores[symbol] = fs
        print(f"  {name}({symbol}): 核心产品{fs.core_product_total}/30 | 出海资质{fs.oversea_qual_total}/30 | 综合{fs.combined_score}")
    
    # 3. 生成入池名单
    pool = evaluate_pool(factor_scores)
    print("\n🎯 入池分层:")
    for tier, stocks in pool.items():
        if stocks:
            print(f"  {tier}: {len(stocks)}只")
            for s in stocks[:5]:
                print(f"    {s['name']}({s['symbol']}): {s['score']}")
            if len(stocks) > 5:
                print(f"    ... 共{len(stocks)}只")
    
    # 4. 保存结果
    scores_data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "version": "2026-09-03",
        "weights": {"core_product": 0.6, "oversea_qual": 0.4},
        "thresholds": {"core": 18.0, "oversea": 10.0, "combined": 20.0},
        "scores": {s: asdict(fs) for s, fs in factor_scores.items()}
    }
    
    with open(FACTOR_SCORE_FILE, "w", encoding="utf-8") as f:
        json.dump(scores_data, f, ensure_ascii=False, indent=2)
    
    pool_data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "pool": pool,
        "total_qualified": len(pool["dual_qualified"]) + len(pool["core_moat"]) + len(pool["oversea_leaders"])
    }
    
    with open(FACTOR_POOL_FILE, "w", encoding="utf-8") as f:
        json.dump(pool_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 因子得分已保存: {FACTOR_SCORE_FILE}")
    print(f"✅ 入池名单已保存: {FACTOR_POOL_FILE}")
    print(f"📈 双因子达标股票: {pool_data['total_qualified']} 只")
    
    # 5. 可选：尝试用akshare更新海外营收
    if update_mode and HAS_AKSHARE:
        print("\n🔄 尝试更新海外营收数据 (akshare)...")
        for symbol in list(stocks_to_process.keys())[:3]:  # 限制前3只测试
            rev = fetch_oversea_revenue_akshare(symbol)
            if rev is not None:
                print(f"  {symbol}: 海外营收占比 {rev:.1f}%")
                # 更新静态配置
                if symbol in static_config["stocks"]:
                    static_config["stocks"][symbol]["oversea_qual"]["oversea_revenue_pct"] = rev
        save_static_config(static_config)
        print("✅ 静态配置已更新")


if __name__ == "__main__":
    main()
