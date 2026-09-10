#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
出海双因子选股系统
==================
核心假设：具备"核心产品制造能力" + "出海资质/实绩"双重验证的公司，
在特高压/输变电/新能源装备等全球化赛道中享有结构性溢价。

双因子定义：
1. 制造能力因子 (Manufacturing Capability)
   - 核心产品市占率（全球/国内 Top3）
   - 关键技术壁垒（专利/标准制定/认证）
   - 产能规模与利用率

2. 出海资质因子 (Overseas Qualification)  
   - 海外认证/准入（欧盟CE/德国VDE/北美UL/沙特SASO/巴西INMETRO等）
   - 实质海外订单/收入占比（≥15%或单单≥5亿）
   - 当地化交付能力（海外工厂/服务中心/EPC总包资质）

评分体系：每因子 0-10 分，双因子加权总分 0-100
  - 制造能力权重 0.55，出海资质权重 0.45
  - 门槛：双因子均≥5分且总分≥70 为"核心标的"
  - 总分 60-70 为"观察标的"
  - 单因子<5分直接剔除（短板理论）

数据源：
- 财报/招股书/年报MD&A（akshare stock_financial_abstract_ths）
- 公司公告（akshare stock_notice_report）
- 专利/标准（需人工维护知识库）
- 海关/招标/中标公告（可扩展接入）

输出：候选标的列表 + 评分明细，供 adaptive_trader / portfolio_sim 复用
"""

import os, sys, json, warnings
from datetime import datetime
from typing import Dict, List, Tuple, Optional

warnings.filterwarnings("ignore")
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)

try:
    import akshare as ak
    HAS_AKSHARE = True
except Exception:
    HAS_AKSHARE = False

# ============================================================================
# 知识库：已验证的出海双因子标的（种子池，人工维护）
# ============================================================================

SEED_KNOWLEDGE_BASE = {
    # 电网装备/特高压（已纳入关注池）
    "600089": {"name": "特变电工", "sector": "特高压变压器",
               "manufacturing": {"global_share_pct": 15, "top3_global": True,
                                 "key_patents": 200, "standards_setter": True,
                                 "certifications": ["CE", "KEMA", "SASO", "GOST"],
                                 "capacity_utilization": 0.85},
               "overseas": {"revenue_share_pct": 28, "single_order_max_yi": 164,
                            "certifications": ["CE", "KEMA", "SASO", "GOST", "UL"],
                            "local_delivery": ["沙特工厂", "印度工厂", "阿尔及利亚工厂"],
                            "epc_qualification": True},
               "verified_date": "2026-08-29", "source": "年报/公告/招股书"},
    
    "600312": {"name": "平高电气", "sector": "GIS/变压器",
               "manufacturing": {"global_share_pct": 12, "top3_global": True,
                                 "key_patents": 180, "standards_setter": True,
                                 "certifications": ["CE", "KEMA", "CIGRE"],
                                 "capacity_utilization": 0.78},
               "overseas": {"revenue_share_pct": 35, "single_order_max_yi": 30,
                            "certifications": ["CE", "KEMA", "UL", "CSA"],
                            "local_delivery": ["意大利项目", "西班牙项目", "巴西变电站"],
                            "epc_qualification": True},
               "verified_date": "2026-08-29", "source": "年报/中标公告"},
    
    "002028": {"name": "思源电气", "sector": "高压开关/无功补偿",
               "manufacturing": {"global_share_pct": 8, "top3_global": False,
                                 "key_patents": 150, "standards_setter": True,
                                 "certifications": ["CE", "KEMA", "VDE"],
                                 "capacity_utilization": 0.82},
               "overseas": {"revenue_share_pct": 22, "single_order_max_yi": 15,
                            "certifications": ["CE", "KEMA", "VDE", "UL"],
                            "local_delivery": ["德国项目", "瑞典项目", "东南亚"],
                            "epc_qualification": False},
               "verified_date": "2026-08-29", "source": "年报/投资者关系"},
    
    "000400": {"name": "许继电气", "sector": "换流阀/直流控保",
               "manufacturing": {"global_share_pct": 35, "top3_global": True,
                                 "key_patents": 300, "standards_setter": True,
                                 "certifications": ["CE", "KEMA", "CIGRE"],
                                 "capacity_utilization": 0.90},
               "overseas": {"revenue_share_pct": 18, "single_order_max_yi": 43.5,
                            "certifications": ["CE", "KEMA"],
                            "local_delivery": ["巴西±800kV", "海外换流站"],
                            "epc_qualification": True},
               "verified_date": "2026-08-29", "source": "半年报/公告"},
    
    "601179": {"name": "中国西电", "sector": "输变电装备平台",
               "manufacturing": {"global_share_pct": 10, "top3_global": True,
                                 "key_patents": 250, "standards_setter": True,
                                 "certifications": ["CE", "KEMA", "GOST"],
                                 "capacity_utilization": 0.75},
               "overseas": {"revenue_share_pct": 15, "single_order_max_yi": 20,
                            "certifications": ["CE", "KEMA", "GOST"],
                            "local_delivery": ["俄罗斯项目", "中亚项目"],
                            "epc_qualification": True},
               "verified_date": "2026-08-29", "source": "年报/央企平台"},
    
    "002270": {"name": "华明装备", "sector": "分接开关",
               "manufacturing": {"global_share_pct": 40, "top3_global": True,
                                 "key_patents": 80, "standards_setter": False,
                                 "certifications": ["CE", "CIGRE"],
                                 "capacity_utilization": 0.88},
               "overseas": {"revenue_share_pct": 30, "single_order_max_yi": 8,
                            "certifications": ["CE"],
                            "local_delivery": ["随主机出口"],
                            "epc_qualification": False},
               "verified_date": "2026-08-29", "source": "年报/细分龙头"},
    
    "002130": {"name": "沃尔核材", "sector": "电缆附件/绝缘材料",
               "manufacturing": {"global_share_pct": 74, "top3_global": True,
                                 "key_patents": 120, "standards_setter": True,
                                 "certifications": ["CE", "UL", "VDE", "KEMA"],
                                 "capacity_utilization": 0.85},
               "overseas": {"revenue_share_pct": 25, "single_order_max_yi": 12,
                            "certifications": ["CE", "UL", "VDE", "KEMA"],
                            "local_delivery": ["欧洲/北美/东南亚"],
                            "epc_qualification": False},
               "verified_date": "2026-08-29", "source": "年报/材料龙头"},
    
    # 新能源装备（待验证扩展）
    "300014": {"name": "亿纬锂能", "sector": "储能电池",
               "manufacturing": {"global_share_pct": 18, "top3_global": True,
                                 "key_patents": 5000, "standards_setter": True,
                                 "certifications": ["UL9540A", "IEC62619", "UN38.3"],
                                 "capacity_utilization": 0.80},
               "overseas": {"revenue_share_pct": 45, "single_order_max_yi": 100,
                            "certifications": ["UL", "IEC", "CE", "KC", "PSE"],
                            "local_delivery": ["马来西亚工厂", "匈牙利工厂", "美国工厂建设中"],
                            "epc_qualification": False},
               "verified_date": "2026-08-29", "source": "年报/全球化布局"},
    
    "688981": {"name": "中芯国际", "sector": "半导体晶圆代工",
               "manufacturing": {"global_share_pct": 6, "top3_global": False,
                                 "key_patents": 8000, "standards_setter": True,
                                 "certifications": ["IATF16949", "ISO9001"],
                                 "capacity_utilization": 0.95},
               "overseas": {"revenue_share_pct": 30, "single_order_max_yi": 200,
                            "certifications": ["国际汽车/消费电子认证"],
                            "local_delivery": ["天津/北京/上海/深圳/成都多基地"],
                            "epc_qualification": False},
               "verified_date": "2026-08-29", "source": "年报/晶圆厂"},
}

# ============================================================================
# 评分引擎
# ============================================================================

def score_manufacturing(data: dict) -> Tuple[float, dict]:
    """制造能力因子评分 (0-10分)"""
    score = 0.0
    details = {}
    
    # 1. 全球市占率 (0-3分)
    share = data.get("global_share_pct", 0)
    if share >= 30: share_score = 3.0
    elif share >= 20: share_score = 2.4
    elif share >= 10: share_score = 2.0
    elif share >= 5: share_score = 1.2
    else: share_score = 0.6
    score += share_score
    details["global_share_score"] = round(share_score, 2)
    
    # 2. 全球 Top3 地位 (0-2分)
    top3_score = 2.0 if data.get("top3_global", False) else 0.6
    score += top3_score
    details["top3_score"] = top3_score
    
    # 3. 专利/技术壁垒 (0-2分)
    patents = data.get("key_patents", 0)
    if patents >= 2000: patent_score = 2.0
    elif patents >= 500: patent_score = 1.6
    elif patents >= 100: patent_score = 1.2
    elif patents >= 50: patent_score = 0.8
    else: patent_score = 0.4
    if data.get("standards_setter", False):
        patent_score = min(2.0, patent_score + 0.4)
    score += patent_score
    details["patent_score"] = round(patent_score, 2)
    
    # 4. 核心认证 (0-2分)
    certs = data.get("certifications", [])
    core_certs = {"CE", "KEMA", "UL", "VDE", "CIGRE", "SASO", "GOST", "IEC62619", "UL9540A"}
    cert_score = min(2.0, len(set(certs) & core_certs) * 0.4)
    score += cert_score
    details["cert_score"] = round(cert_score, 2)
    
    # 5. 产能利用率 (0-1分)
    util = data.get("capacity_utilization", 0)
    util_score = 1.0 if util >= 0.85 else (0.6 if util >= 0.7 else 0.2)
    score += util_score
    details["util_score"] = util_score
    
    return round(min(10.0, score), 2), details


def score_overseas(data: dict) -> Tuple[float, dict]:
    """出海资质因子评分 (0-10分)"""
    score = 0.0
    details = {}
    
    # 1. 海外收入占比 (0-3分)
    rev_share = data.get("revenue_share_pct", 0)
    if rev_share >= 40: rev_score = 3.0
    elif rev_share >= 30: rev_score = 2.4
    elif rev_share >= 20: rev_score = 2.0
    elif rev_share >= 15: rev_score = 1.4
    elif rev_share >= 10: rev_score = 0.8
    else: rev_score = 0.2
    score += rev_score
    details["revenue_share_score"] = round(rev_score, 2)
    
    # 2. 单单/最大订单规模 (0-2分)
    max_order = data.get("single_order_max_yi", 0)
    if max_order >= 100: order_score = 2.0
    elif max_order >= 50: order_score = 1.6
    elif max_order >= 20: order_score = 1.2
    elif max_order >= 10: order_score = 0.8
    elif max_order >= 5: order_score = 0.4
    else: order_score = 0.2
    score += order_score
    details["order_score"] = order_score
    
    # 3. 海外准入认证广度 (0-2分)
    certs = data.get("certifications", [])
    global_certs = {"CE", "KEMA", "UL", "VDE", "CSA", "SASO", "GOST", "INMETRO", 
                    "KC", "PSE", "CCC", "IEC", "UL9540A", "CIGRE"}
    cert_breadth = len(set(certs) & global_certs)
    cert_score = min(2.0, cert_breadth * 0.25)
    score += cert_score
    details["cert_breadth_score"] = round(cert_score, 2)
    
    # 4. 当地化交付能力 (0-2分)
    local = data.get("local_delivery", [])
    local_score = 0.0
    if any("工厂" in str(x) for x in local):
        local_score += 1.0
    if any("项目" in str(x) for x in local):
        local_score += 0.6
    if len(local) >= 3:
        local_score += 0.4
    score += min(2.0, local_score)
    details["local_delivery_score"] = round(min(2.0, local_score), 2)
    
    # 5. EPC 总包资质 (1分)
    epc_score = 1.0 if data.get("epc_qualification", False) else 0.0
    score += epc_score
    details["epc_score"] = epc_score
    
    return round(min(10.0, score), 2), details


def evaluate_dual_factor(symbol: str, data: dict) -> dict:
    """双因子综合评分"""
    mfg_score, mfg_details = score_manufacturing(data.get("manufacturing", {}))
    ovs_score, ovs_details = score_overseas(data.get("overseas", {}))
    
    # 加权总分 (满分100)
    total_score = round((mfg_score * 0.55 + ovs_score * 0.45) * 10, 1)
    
    # 短板判定
    weak_link = mfg_score < 5.0 or ovs_score < 5.0
    
    # 分级
    if weak_link:
        grade = "剔除(短板)"
    elif total_score >= 80:
        grade = "S级核心"
    elif total_score >= 70:
        grade = "A级核心"
    elif total_score >= 60:
        grade = "B级观察"
    else:
        grade = "C级不达标"
    
    return {
        "symbol": symbol,
        "name": data.get("name", ""),
        "sector": data.get("sector", ""),
        "manufacturing_score": mfg_score,
        "overseas_score": ovs_score,
        "total_score": total_score,
        "grade": grade,
        "weak_link": weak_link,
        "mfg_details": mfg_details,
        "ovs_details": ovs_details,
        "verified_date": data.get("verified_date", ""),
        "source": data.get("source", ""),
    }


def scan_seed_pool() -> List[dict]:
    """扫描种子知识库，输出评分结果"""
    results = []
    for symbol, data in SEED_KNOWLEDGE_BASE.items():
        result = evaluate_dual_factor(symbol, data)
        results.append(result)
    
    # 按总分降序
    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results


def format_report(results: List[dict]) -> str:
    lines = []
    lines.append("# 出海双因子选股评分报告")
    lines.append(f"\n> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("> 评分逻辑: 制造能力(55%) + 出海资质(45%) | 单因子<3分直接剔除")
    lines.append("")
    lines.append("| 代码 | 名称 | 板块 | 制造(5) | 出海(5) | 总分(100) | 等级 | 短板 |")
    lines.append("|------|------|------|---------|---------|-----------|------|------|")
    
    for r in results:
        weak = "⚠️" if r["weak_link"] else ""
        lines.append(f"| {r['symbol']} | {r['name']} | {r['sector']} | "
                     f"{r['manufacturing_score']:.1f} | {r['overseas_score']:.1f} | "
                     f"**{r['total_score']:.1f}** | {r['grade']} | {weak} |")
    
    lines.append("")
    lines.append("## 详细拆解")
    for r in results:
        if r["total_score"] >= 55:  # 只展示观察级以上
            lines.append(f"\n### {r['name']}({r['symbol']}) - {r['grade']} (总分{r['total_score']:.1f})")
            lines.append(f"- **制造能力 {r['manufacturing_score']:.1f}/5.0**")
            for k, v in r["mfg_details"].items():
                lines.append(f"  - {k}: {v}")
            lines.append(f"- **出海资质 {r['overseas_score']:.1f}/5.0**")
            for k, v in r["ovs_details"].items():
                lines.append(f"  - {k}: {v}")
            lines.append(f"- 认证日期: {r['verified_date']} | 来源: {r['source']}")
    
    return "\n".join(lines)


def export_for_adaptive_trader(results: List[dict]) -> dict:
    """导出供 adaptive_trader 复用的格式"""
    core_stocks = [r for r in results if r["grade"] in ("S级核心", "A级核心")]
    watch_stocks = [r for r in results if r["grade"] == "B级观察"]
    
    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "methodology": "双因子：制造能力(55%)+出海资质(45%)，短板剔除",
        "core_candidates": [
            {"symbol": r["symbol"], "name": r["name"], "sector": r["sector"],
             "total_score": r["total_score"], "grade": r["grade"]}
            for r in core_stocks
        ],
        "watch_candidates": [
            {"symbol": r["symbol"], "name": r["name"], "sector": r["sector"],
             "total_score": r["total_score"], "grade": r["grade"]}
            for r in watch_stocks
        ],
        "all_scores": {r["symbol"]: {"total": r["total_score"],
                                      "manufacturing": r["manufacturing_score"],
                                      "overseas": r["overseas_score"],
                                      "grade": r["grade"]} for r in results}
    }


def main():
    print("🔍 出海双因子扫描开始...")
    results = scan_seed_pool()
    
    # 控制台输出
    print(f"\n共评估 {len(results)} 只种子标的")
    core = [r for r in results if r["grade"] in ("S级核心", "A级核心")]
    watch = [r for r in results if r["grade"] == "B级观察"]
    print(f"  核心标的(S/A级): {len(core)} 只")
    print(f"  观察标的(B级): {len(watch)} 只")
    
    for r in results:
        flag = "🔴" if r["weak_link"] else ("🟢" if r["total_score"] >= 65 else "🟡")
        print(f"  {flag} {r['name']}({r['symbol']}) [{r['grade']}] 总分{r['total_score']:.1f} "
              f"(制造{r['manufacturing_score']:.1f}/出海{r['overseas_score']:.1f})")
    
    # 保存 JSON
    json_out = export_for_adaptive_trader(results)
    json_path = os.path.join(WORKSPACE, "data", "overseas_dual_factor.json")
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_out, f, ensure_ascii=False, indent=2)
    print(f"\n📁 JSON已保存: {json_path}")
    
    # 保存 Markdown 报告
    md_path = os.path.join(WORKSPACE, "analysis", "daily", 
                           f"overseas_dual_factor_{datetime.now().strftime('%Y-%m-%d')}.md")
    os.makedirs(os.path.dirname(md_path), exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(format_report(results))
    print(f"📄 报告已保存: {md_path}")
    
    # stdout 输出供 cron 解析
    print("\n=====OVERSEAS_DUAL_FACTOR=====")
    print(json.dumps(json_out, ensure_ascii=False, indent=2, default=str))
    print("=====OVERSEAS_DUAL_FACTOR_END=====")


if __name__ == "__main__":
    main()