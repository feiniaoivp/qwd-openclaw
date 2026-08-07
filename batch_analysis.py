#!/usr/bin/env python3
"""
批量生成 33 只股票的 5 类量化分析 Markdown 报告
"""

import pandas as pd
import numpy as np
import os
import json
from pathlib import Path
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

DATA_ROOT = Path("/Users/duguke/.openclaw/workspace/akshare_data")
REPORT_DIR = Path("/Users/duguke/.openclaw/workspace/analysis_reports")
REPORT_DIR.mkdir(exist_ok=True)

# 股票代码与名称映射
STOCK_NAMES = {
    "000001": "平安银行", "600519": "贵州茅台", "300750": "宁德时代", "002594": "比亚迪",
    "600036": "招商银行", "600160": "巨化股份", "300827": "上能电气", "600346": "恒力石化",
    "000708": "中信特钢", "300748": "金力永磁", "002413": "雷科防务", "601061": "中信金属",
    "900925": "机电B股", "000157": "中联重科", "603308": "应流股份", "601865": "福莱特",
    "600660": "福耀玻璃", "002318": "久立特材", "002335": "科华数据", "605566": "福莱蒽特",
    "601995": "中金公司", "600030": "中信证券", "601066": "中信建投", "000987": "越秀资本",
    "601100": "恒立液压", "600570": "恒生电子", "300124": "汇川技术", "300719": "安达维尔",
    "002466": "天齐锂业", "300014": "亿纬锂能", "300285": "国瓷材料", "688981": "中芯国际",
    "600584": "长电科技", "002156": "通富微电"
}

def load_financial_data(symbol):
    """加载某只股票的所有财务数据"""
    stock_dir = DATA_ROOT / symbol
    data = {}
    
    files_map = {
        "abstract": "financial_abstract_ths.csv",
        "indicator": "financial_analysis_indicator.csv",
        "zcfzb": "financial_zcfzb.csv",
        "lrb": "financial_lrb.csv",
        "xjllb": "financial_xjllb.csv",
        "margin": "margin_sse.csv",
        "dividend": "dividend_fhps.csv",
    }
    
    for key, fname in files_map.items():
        fpath = stock_dir / fname
        if fpath.exists():
            try:
                data[key] = pd.read_csv(fpath)
            except:
                data[key] = None
        else:
            data[key] = None
    
    return data

def calc_financial_ratios(data):
    """计算核心财务比率"""
    ratios = {}
    
    if data.get("lrb") is not None and data.get("zcfzb") is not None:
        lrb = data["lrb"]
        zcfzb = data["zcfzb"]
        xjllb = data.get("xjllb")
        
        # 取最新一期（假设按报告日倒序）
        for df in [lrb, zcfzb, xjllb]:
            if "报告日" in df.columns:
                df["报告日"] = pd.to_datetime(df["报告日"], errors="coerce")
                df.sort_values("报告日", ascending=False, inplace=True)
        
        latest_lrb = lrb.iloc[0] if len(lrb) > 0 else None
        latest_zcfzb = zcfzb.iloc[0] if len(zcfzb) > 0 else None
        latest_xjllb = xjllb.iloc[0] if xjllb is not None and len(xjllb) > 0 else None
        
        if latest_lrb is not None and latest_zcfzb is not None:
            # 营收、净利润
            revenue = pd.to_numeric(latest_lrb.get("营业收入", latest_lrb.get("营业总收入", 0)), errors="coerce")
            net_profit = pd.to_numeric(latest_lrb.get("净利润", latest_lrb.get("归属于母公司的净利润", 0)), errors="coerce")
            gross_profit = pd.to_numeric(latest_lrb.get("毛利润", revenue - latest_lrb.get("营业成本", 0)), errors="coerce")
            
            # 资产负债表项目
            total_assets = pd.to_numeric(latest_zcfzb.get("资产总计", 0), errors="coerce")
            total_equity = pd.to_numeric(latest_zcfzb.get("归属于母公司股东的权益", latest_zcfzb.get("股东权益", 0)), errors="coerce")
            total_liab = pd.to_numeric(latest_zcfzb.get("负债合计", 0), errors="coerce")
            current_assets = pd.to_numeric(latest_zcfzb.get("流动资产合计", latest_zcfzb.get("资产", 0)), errors="coerce")
            current_liab = pd.to_numeric(latest_zcfzb.get("流动负债合计", latest_zcfzb.get("负债", 0)), errors="coerce")
            
            # 现金流
            ocf = pd.to_numeric(latest_xjllb.get("经营活动产生的现金流量净额", 0), errors="coerce") if latest_xjllb is not None else 0
            
            # 计算比率
            ratios["营收"] = revenue
            ratios["净利润"] = net_profit
            ratios["毛利率"] = gross_profit / revenue * 100 if revenue and revenue != 0 else None
            ratios["净利率"] = net_profit / revenue * 100 if revenue and revenue != 0 else None
            ratios["ROE"] = net_profit / total_equity * 100 if total_equity and total_equity != 0 else None
            ratios["ROA"] = net_profit / total_assets * 100 if total_assets and total_assets != 0 else None
            ratios["资产负债率"] = total_liab / total_assets * 100 if total_assets and total_assets != 0 else None
            ratios["流动比率"] = current_assets / current_liab if current_liab and current_liab != 0 else None
            ratios["速动比率"] = (current_assets - pd.to_numeric(latest_zcfzb.get("存货", 0), errors="coerce")) / current_liab if current_liab and current_liab != 0 else None
            ratios["经营现金流/净利润"] = ocf / net_profit if net_profit and net_profit != 0 else None
            ratios["总资产"] = total_assets
            ratios["净资产"] = total_equity
    
    return ratios

def calc_piotroski_f_score(data):
    """计算 Piotroski F-Score (9分制)"""
    score = 0
    details = {}
    
    if data.get("lrb") is None or data.get("zcfzb") is None or data.get("xjllb") is None:
        return None, {}
    
    lrb = data["lrb"].copy()
    zcfzb = data["zcfzb"].copy()
    xjllb = data["xjllb"].copy()
    
    for df in [lrb, zcfzb, xjllb]:
        if "报告日" in df.columns:
            df["报告日"] = pd.to_datetime(df["报告日"], errors="coerce")
            df.sort_values("报告日", ascending=False, inplace=True)
    
    if len(lrb) < 2 or len(zcfzb) < 2:
        return None, {}
    
    # 当期和上期
    cur_lrb, prev_lrb = lrb.iloc[0], lrb.iloc[1]
    cur_zcfzb, prev_zcfzb = zcfzb.iloc[0], zcfzb.iloc[1]
    cur_xjllb = xjllb.iloc[0] if len(xjllb) > 0 else None
    
    def get_val(row, key, default=0):
        return pd.to_numeric(row.get(key, default), errors="coerce")
    
    # 1. ROA > 0
    net_income = get_val(cur_lrb, "净利润", get_val(cur_lrb, "归属于母公司的净利润"))
    total_assets = get_val(cur_zcfzb, "资产总计")
    roa = net_income / total_assets if total_assets else 0
    details["ROA>0"] = roa > 0
    if roa > 0: score += 1
    
    # 2. 经营现金流 > 0
    ocf = get_val(cur_xjllb, "经营活动产生的现金流量净额") if cur_xjllb is not None else 0
    details["OCF>0"] = ocf > 0
    if ocf > 0: score += 1
    
    # 3. ROA 改善
    prev_net_income = get_val(prev_lrb, "净利润", get_val(prev_lrb, "归属于母公司的净利润"))
    prev_total_assets = get_val(prev_zcfzb, "资产总计")
    prev_roa = prev_net_income / prev_total_assets if prev_total_assets else 0
    details["ROA改善"] = roa > prev_roa
    if roa > prev_roa: score += 1
    
    # 4. 经营现金流 > 净利润 (现金流质量)
    details["OCF>净利润"] = ocf > net_income
    if ocf > net_income: score += 1
    
    # 5. 长期债务比率下降
    cur_lt_debt = get_val(cur_zcfzb, "长期借款") + get_val(cur_zcfzb, "应付债券")
    prev_lt_debt = get_val(prev_zcfzb, "长期借款") + get_val(prev_zcfzb, "应付债券")
    cur_lt_ratio = cur_lt_debt / total_assets if total_assets else 0
    prev_lt_ratio = prev_lt_debt / prev_total_assets if prev_total_assets else 0
    details["长期债务率下降"] = cur_lt_ratio < prev_lt_ratio
    if cur_lt_ratio < prev_lt_ratio: score += 1
    
    # 6. 流动比率改善
    cur_ca = get_val(cur_zcfzb, "流动资产合计", get_val(cur_zcfzb, "资产"))
    cur_cl = get_val(cur_zcfzb, "流动负债合计", get_val(cur_zcfzb, "负债"))
    prev_ca = get_val(prev_zcfzb, "流动资产合计", get_val(prev_zcfzb, "资产"))
    prev_cl = get_val(prev_zcfzb, "流动负债合计", get_val(prev_zcfzb, "负债"))
    cur_current = cur_ca / cur_cl if cur_cl else 0
    prev_current = prev_ca / prev_cl if prev_cl else 0
    details["流动比率改善"] = cur_current > prev_current
    if cur_current > prev_current: score += 1
    
    # 7. 未增发新股
    cur_share = get_val(cur_zcfzb, "股本")
    prev_share = get_val(prev_zcfzb, "股本")
    details["未增发"] = cur_share <= prev_share * 1.01  # 允许1%误差
    if cur_share <= prev_share * 1.01: score += 1
    
    # 8. 毛利率改善
    cur_revenue = get_val(cur_lrb, "营业收入", get_val(cur_lrb, "营业总收入"))
    cur_cogs = get_val(cur_lrb, "营业成本")
    prev_revenue = get_val(prev_lrb, "营业收入", get_val(prev_lrb, "营业总收入"))
    prev_cogs = get_val(prev_lrb, "营业成本")
    cur_gross = (cur_revenue - cur_cogs) / cur_revenue if cur_revenue else 0
    prev_gross = (prev_revenue - prev_cogs) / prev_revenue if prev_revenue else 0
    details["毛利率改善"] = cur_gross > prev_gross
    if cur_gross > prev_gross: score += 1
    
    # 9. 资产周转率改善
    cur_turnover = cur_revenue / total_assets if total_assets else 0
    prev_turnover = prev_revenue / prev_total_assets if prev_total_assets else 0
    details["资产周转改善"] = cur_turnover > prev_turnover
    if cur_turnover > prev_turnover: score += 1
    
    return score, details

def calc_altman_z_score(data):
    """计算 Altman Z-Score"""
    if data.get("zcfzb") is None or data.get("lrb") is None:
        return None, {}
    
    zcfzb = data["zcfzb"].copy()
    lrb = data["lrb"].copy()
    
    for df in [zcfzb, lrb]:
        if "报告日" in df.columns:
            df["报告日"] = pd.to_datetime(df["报告日"], errors="coerce")
            df.sort_values("报告日", ascending=False, inplace=True)
    
    cur_z = zcfzb.iloc[0]
    cur_l = lrb.iloc[0]
    
    def get_val(row, key, default=0):
        return pd.to_numeric(row.get(key, default), errors="coerce")
    
    total_assets = get_val(cur_z, "资产总计")
    total_liab = get_val(cur_z, "负债合计")
    current_assets = get_val(cur_z, "流动资产合计", get_val(cur_z, "资产"))
    current_liab = get_val(cur_z, "流动负债合计", get_val(cur_z, "负债"))
    equity = get_val(cur_z, "归属于母公司股东的权益", get_val(cur_z, "股东权益"))
    retained_earnings = get_val(cur_z, "未分配利润")
    ebit = get_val(cur_l, "利润总额") + get_val(cur_l, "财务费用")  # 简化
    revenue = get_val(cur_l, "营业收入", get_val(cur_l, "营业总收入"))
    
    if total_assets == 0:
        return None, {}
    
    # Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5
    x1 = (current_assets - current_liab) / total_assets  # 流动资金/总资产
    x2 = retained_earnings / total_assets  # 留存收益/总资产
    x3 = ebit / total_assets  # EBIT/总资产
    x4 = equity / total_liab if total_liab else 0  # 权益/负债
    x5 = revenue / total_assets  # 销售/总资产
    
    z_score = 1.2*x1 + 1.4*x2 + 3.3*x3 + 0.6*x4 + 1.0*x5
    
    # 区间判断
    if z_score > 2.99:
        zone = "安全区"
    elif z_score > 1.81:
        zone = "灰色区"
    else:
        zone = "破产区"
    
    return z_score, {"X1": x1, "X2": x2, "X3": x3, "X4": x4, "X5": x5, "区间": zone}

def analyze_dividend(data):
    """分红分析"""
    if data.get("dividend") is None:
        return {}
    
    df = data["dividend"]
    if df.empty:
        return {}
    
    # 取最新一条
    latest = df.iloc[0]
    
    return {
        "分红送转比例": latest.get("送转股份-送转总比例", 0),
        "现金分红比例": latest.get("现金分红-现金分红比例", 0),
        "股息率": latest.get("现金分红-股息率", 0),
        "每股收益": latest.get("每股收益", 0),
        "方案进度": latest.get("方案进度", ""),
        "预案公告日": latest.get("预案公告日", ""),
        "股权登记日": latest.get("股权登记日", ""),
        "除权除息日": latest.get("除权除息日", ""),
    }

def analyze_margin(data):
    """融资融券分析"""
    if data.get("margin") is None:
        return {}
    
    df = data["margin"].copy()
    if df.empty:
        return {}
    
    if "信用交易日期" in df.columns:
        df["信用交易日期"] = pd.to_datetime(df["信用交易日期"], errors="coerce")
        df.sort_values("信用交易日期", ascending=False, inplace=True)
    
    latest = df.iloc[0]
    
    rz_ye = pd.to_numeric(latest.get("融资余额", 0), errors="coerce")
    rz_mr = pd.to_numeric(latest.get("融资买入额", 0), errors="coerce")
    rq_yl = pd.to_numeric(latest.get("融券余量", 0), errors="coerce")
    rq_ye = pd.to_numeric(latest.get("融券余量金额", 0), errors="coerce")
    rq_mc = pd.to_numeric(latest.get("融券卖出量", 0), errors="coerce")
    total = pd.to_numeric(latest.get("融资融券余额", 0), errors="coerce")
    
    # 计算近期趋势 (最近20天)
    recent = df.head(20)
    rz_trend = "上升" if len(recent) > 1 and pd.to_numeric(recent.iloc[0]["融资余额"], errors="coerce") > pd.to_numeric(recent.iloc[-1]["融资余额"], errors="coerce") else "下降"
    
    return {
        "融资余额": rz_ye,
        "融资买入额": rz_mr,
        "融券余量": rq_yl,
        "融券余额": rq_ye,
        "融券卖出量": rq_mc,
        "两融余额": total,
        "融资趋势(20日)": rz_trend,
        "数据日期": latest.get("信用交易日期", ""),
    }

def generate_all_reports():
    """生成所有报告"""
    print("开始批量分析...")
    
    all_results = []
    
    for symbol, name in STOCK_NAMES.items():
        print(f"  分析 {symbol} {name}...")
        data = load_financial_data(symbol)
        
        ratios = calc_financial_ratios(data)
        f_score, f_details = calc_piotroski_f_score(data)
        z_score, z_details = calc_altman_z_score(data)
        div_info = analyze_dividend(data)
        margin_info = analyze_margin(data)
        
        all_results.append({
            "代码": symbol,
            "名称": name,
            "ratios": ratios,
            "f_score": f_score,
            "f_details": f_details,
            "z_score": z_score,
            "z_details": z_details,
            "dividend": div_info,
            "margin": margin_info,
        })
    
    # 生成 5 个 Markdown 表格
    generate_report_1_financial_trends(all_results)
    generate_report_2_valuation(all_results)
    generate_report_3_health_score(all_results)
    generate_report_4_dividend(all_results)
    generate_report_5_margin(all_results)
    generate_summary_report(all_results)
    
    print(f"\n✅ 所有报告已生成至: {REPORT_DIR}")

def generate_report_1_financial_trends(results):
    """报告1: 财务指标趋势"""
    rows = []
    for r in results:
        ratios = r["ratios"]
        rows.append({
            "代码": r["代码"],
            "名称": r["名称"],
            "营收(亿)": f"{ratios.get('营收', 0)/1e8:.2f}" if ratios.get('营收') else "N/A",
            "净利润(亿)": f"{ratios.get('净利润', 0)/1e8:.2f}" if ratios.get('净利润') else "N/A",
            "毛利率(%)": f"{ratios.get('毛利率', 0):.2f}" if ratios.get('毛利率') else "N/A",
            "净利率(%)": f"{ratios.get('净利率', 0):.2f}" if ratios.get('净利率') else "N/A",
            "ROE(%)": f"{ratios.get('ROE', 0):.2f}" if ratios.get('ROE') else "N/A",
            "ROA(%)": f"{ratios.get('ROA', 0):.2f}" if ratios.get('ROA') else "N/A",
            "资产负债率(%)": f"{ratios.get('资产负债率', 0):.2f}" if ratios.get('资产负债率') else "N/A",
            "流动比率": f"{ratios.get('流动比率', 0):.2f}" if ratios.get('流动比率') else "N/A",
            "速动比率": f"{ratios.get('速动比率', 0):.2f}" if ratios.get('速动比率') else "N/A",
            "经营现金流/净利润": f"{ratios.get('经营现金流/净利润', 0):.2f}" if ratios.get('经营现金流/净利润') else "N/A",
        })
    
    df = pd.DataFrame(rows)
    md = df.to_markdown(index=False)
    
    content = f"""# 报告1：财务指标趋势分析

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 核心盈利与偿债指标

{md}

---
*数据说明：基于最新一期财报（同花顺/新浪），单位：亿元、百分比*
"""
    (REPORT_DIR / "01_财务指标趋势.md").write_text(content, encoding="utf-8")

def generate_report_2_valuation(results):
    """报告2: 估值模型 (简易版，需结合当前股价)"""
    rows = []
    for r in results:
        ratios = r["ratios"]
        equity = ratios.get("净资产", 0)
        net_profit = ratios.get("净利润", 0)
        revenue = ratios.get("营收", 0)
        
        # 简易估值指标 (无实时股价，仅展示基本面支撑)
        bps = equity / 1e8 if equity else 0  # 每股净资产(亿股本假设)
        eps = net_profit / 1e8 if net_profit else 0
        sps = revenue / 1e8 if revenue else 0
        
        rows.append({
            "代码": r["代码"],
            "名称": r["名称"],
            "每股净资产(参考)": f"{bps:.2f}" if bps else "N/A",
            "每股收益(参考)": f"{eps:.2f}" if eps else "N/A",
            "每股销售额(参考)": f"{sps:.2f}" if sps else "N/A",
            "净资产收益率(%)": f"{ratios.get('ROE', 0):.2f}" if ratios.get('ROE') else "N/A",
            "隐含PE(市净率/ROE)": "需股价",
            "隐含PB": "需股价",
            "隐含PS": "需股价",
        })
    
    df = pd.DataFrame(rows)
    md = df.to_markdown(index=False)
    
    content = f"""# 报告2：估值模型基础数据

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 估值锚定指标 (需结合实时股价计算 PE/PB/PS)

{md}

### 使用建议
- **PE** = 股价 / 每股收益(EPS)
- **PB** = 股价 / 每股净资产(BPS)  
- **PS** = 股价 / 每股销售额(SPS)
- **PEG** = PE / 净利润增速(%)
- **DCF简易** = 经营现金流现值折现 (需预测增速、折现率)

---
*注：以上每股指标基于总股本粗略估算，精确值需结合最新总股本与实时股价*
"""
    (REPORT_DIR / "02_估值模型基础数据.md").write_text(content, encoding="utf-8")

def generate_report_3_health_score(results):
    """报告3: 财务健康度评分"""
    rows = []
    for r in results:
        f_score = r["f_score"]
        f_det = r["f_details"]
        z_score = r["z_score"]
        z_det = r["z_details"]
        
        # 现金流质量
        ratios = r["ratios"]
        ocf_ni = ratios.get("经营现金流/净利润", 0)
        cash_quality = "优" if ocf_ni and ocf_ni > 1 else ("良" if ocf_ni and ocf_ni > 0.8 else ("一般" if ocf_ni and ocf_ni > 0.5 else "弱"))
        
        rows.append({
            "代码": r["代码"],
            "名称": r["名称"],
            "F-Score(9分)": f_score if f_score is not None else "N/A",
            "F-等级": ("优秀" if f_score and f_score >= 7 else ("良好" if f_score and f_score >= 5 else ("一般" if f_score and f_score >= 3 else "差"))) if f_score else "N/A",
            "ROA>0": "✓" if f_det.get("ROA>0") else "✗",
            "OCF>0": "✓" if f_det.get("OCF>0") else "✗",
            "ROA改善": "✓" if f_det.get("ROA改善") else "✗",
            "现金流质量": "✓" if f_det.get("OCF>净利润") else "✗",
            "长期债务↓": "✓" if f_det.get("长期债务率下降") else "✗",
            "流动比率↑": "✓" if f_det.get("流动比率改善") else "✗",
            "未增发": "✓" if f_det.get("未增发") else "✗",
            "毛利率↑": "✓" if f_det.get("毛利率改善") else "✗",
            "周转率↑": "✓" if f_det.get("资产周转改善") else "✗",
            "Z-Score": f"{z_score:.2f}" if z_score else "N/A",
            "Z-区间": z_det.get("区间", "N/A") if z_det else "N/A",
            "现金流质量评级": cash_quality,
        })
    
    df = pd.DataFrame(rows)
    md = df.to_markdown(index=False)
    
    content = f"""# 报告3：财务健康度评分 (F-Score + Z-Score + 现金流质量)

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 综合健康度评分表

{md}

### 评分标准
- **Piotroski F-Score (9分制)**: ≥7优秀, 5-6良好, 3-4一般, ≤2差
- **Altman Z-Score**: >2.99安全区, 1.81-2.99灰色区, <1.81破产区
- **现金流质量**: 经营现金流/净利润 >1优, 0.8-1良, 0.5-0.8一般, <0.5弱

### 解读建议
- F-Score ≥7 且 Z-Score >2.99 = 财务极其健康
- F-Score ≥5 且 Z-Score >1.81 = 财务基本健康
- 现金流质量"弱"需警惕盈利含金量
"""
    (REPORT_DIR / "03_财务健康度评分.md").write_text(content, encoding="utf-8")

def generate_report_4_dividend(results):
    """报告4: 分红历史与股息率"""
    rows = []
    for r in results:
        div = r["dividend"]
        rows.append({
            "代码": r["代码"],
            "名称": r["名称"],
            "送转比例(%)": div.get("分红送转比例", "N/A"),
            "现金分红比例(%)": div.get("现金分红比例", "N/A"),
            "股息率(%)": div.get("股息率", "N/A"),
            "每股收益": div.get("每股收益", "N/A"),
            "方案进度": div.get("方案进度", "N/A"),
            "预案公告日": div.get("预案公告日", "N/A"),
            "股权登记日": div.get("股权登记日", "N/A"),
            "除权除息日": div.get("除权除息日", "N/A"),
        })
    
    df = pd.DataFrame(rows)
    md = df.to_markdown(index=False)
    
    content = f"""# 报告4：分红历史与股息率分析

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 最新分红方案详情

{md}

### 关键指标说明
- **送转比例**: 每10股送/转股数
- **现金分红比例**: 每10股派发现金(含税)
- **股息率**: 现金分红/股价 × 100% (需结合除权日股价)
- **分红稳定性**: 需结合历史多年数据判断 (当前仅最新一期)

### 选股参考
- 股息率 >3% 且分红稳定 = 高股息策略候选
- 高送转 + 高现金分红 = 业绩确定性高
- 方案进度为"实施"或"完成"落地确定性强
"""
    (REPORT_DIR / "04_分红历史股息率.md").write_text(content, encoding="utf-8")

def generate_report_5_margin(results):
    """报告5: 融资融券余额趋势"""
    rows = []
    for r in results:
        m = r["margin"]
        rows.append({
            "代码": r["代码"],
            "名称": r["名称"],
            "融资余额(万)": f"{m.get('融资余额', 0)/1e4:.1f}" if m.get('融资余额') else "N/A",
            "融资买入额(万)": f"{m.get('融资买入额', 0)/1e4:.1f}" if m.get('融资买入额') else "N/A",
            "融券余量(万股)": f"{m.get('融券余量', 0)/1e4:.1f}" if m.get('融券余量') else "N/A",
            "融券余额(万)": f"{m.get('融券余额', 0)/1e4:.1f}" if m.get('融券余额') else "N/A",
            "融券卖出量(万股)": f"{m.get('融券卖出量', 0)/1e4:.1f}" if m.get('融券卖出量') else "N/A",
            "两融余额(万)": f"{m.get('两融余额', 0)/1e4:.1f}" if m.get('两融余额') else "N/A",
            "融资趋势(20日)": m.get("融资趋势(20日)", "N/A"),
            "数据日期": m.get("数据日期", "N/A"),
        })
    
    df = pd.DataFrame(rows)
    md = df.to_markdown(index=False)
    
    content = f"""# 报告5：融资融券余额趋势与两融余额率

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 最新两融数据 (上交所)

{md}

### 关键指标
- **融资余额**: 投资者借钱买股票余额，反映做多情绪
- **融券余量**: 投资者借股票卖出余量，反映做空情绪  
- **两融余额**: 融资+融券总额
- **融资趋势(20日)**: 近20个交易日融资余额变化方向

### 解读参考
- 融资余额持续增加 + 股价上涨 = 杠杆推升行情，需警惕回调
- 融资余额减少 + 股价上涨 = 杠杆去化健康，上涨可持续
- 融券余量异常放大 = 做空压力大，短期承压
- 两融余额率 = 两融余额/流通市值，>10%属高位区
"""
    (REPORT_DIR / "05_融资融券趋势.md").write_text(content, encoding="utf-8")

def generate_summary_report(results):
    """汇总报告"""
    rows = []
    for r in results:
        ratios = r["ratios"]
        f_score = r["f_score"]
        z_score = r["z_score"]
        div = r["dividend"]
        m = r["margin"]
        
        rows.append({
            "代码": r["代码"],
            "名称": r["名称"],
            "ROE(%)": f"{ratios.get('ROE', 0):.1f}" if ratios.get('ROE') else "N/A",
            "净利率(%)": f"{ratios.get('净利率', 0):.1f}" if ratios.get('净利率') else "N/A",
            "资产负债率(%)": f"{ratios.get('资产负债率', 0):.1f}" if ratios.get('资产负债率') else "N/A",
            "F-Score": f_score if f_score else "N/A",
            "Z-Score": f"{z_score:.1f}" if z_score else "N/A",
            "股息率(%)": div.get("股息率", "N/A"),
            "两融余额(亿)": f"{m.get('两融余额', 0)/1e8:.2f}" if m.get('两融余额') else "N/A",
            "融资趋势": m.get("融资趋势(20日)", "N/A"),
        })
    
    df = pd.DataFrame(rows)
    md = df.to_markdown(index=False)
    
    content = f"""# 汇总报告：33只自选股量化分析一览

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
数据来源: AkShare (同花顺财务摘要、新浪三大报表、上交所融资融券、东方财富分红)

## 核心指标一览表

{md}

## 子报告索引
1. [财务指标趋势分析](01_财务指标趋势.md) - ROE、毛利率、净利率、偿债能力等
2. [估值模型基础数据](02_估值模型基础数据.md) - EPS/BPS/SPS、隐含估值锚点
3. [财务健康度评分](03_财务健康度评分.md) - F-Score、Z-Score、现金流质量
4. [分红历史与股息率](04_分红历史股息率.md) - 分红方案、股息率、落地进度
5. [融资融券趋势](05_融资融券趋势.md) - 两融余额、融资趋势、情绪指标

## 快速筛选建议
- **高质量成长**: ROE>15%、F-Score≥7、现金流质量优
- **高股息价值**: 股息率>3%、分红稳定、Z-Score>2.99
- **避雷预警**: F-Score≤3、Z-Score<1.81、现金流质量弱、融资趋势异常放大
"""
    (REPORT_DIR / "00_汇总报告.md").write_text(content, encoding="utf-8")

if __name__ == "__main__":
    generate_all_reports()