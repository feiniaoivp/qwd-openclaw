#!/usr/bin/env python3
"""
财报亮点与坑点批量分析脚本
分析30只关注股的最新财务数据（2024年报 + 2025前三季度）
输出：Markdown格式简报
"""

import akshare as ak
import pandas as pd
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ── 工具函数 ──────────────────────────────────────────────
NO_DATA = "无"

def safe_float(val, default=NO_DATA):
    if val is None or val == '' or val == 'False' or val == 'NaN' or val is False:
        return default
    try:
        f = float(val)
        if f != f or abs(f) > 1e15:  # NaN or inf
            return default
        return f
    except (ValueError, TypeError):
        return default

def parse_amount(s):
    """解析带单位金额，统一转亿元"""
    if s is None or s == '' or s == 'False' or s is False:
        return None
    s = str(s).strip()
    m = re.match(r'^([-\d.]+)\s*万亿$', s)
    if m:
        return float(m.group(1)) * 10000
    m = re.match(r'^([-\d.]+)\s*亿$', s)
    if m:
        return float(m.group(1))
    m = re.match(r'^([-\d.]+)\s*万$', s)
    if m:
        return float(m.group(1)) / 10000
    try:
        return float(s)  # 纯数字，单位由调用者判断
    except (ValueError, TypeError):
        return None

def parse_percent(s):
    """解析百分比字符串"""
    if s is None or s == '' or s == 'False' or s is False:
        return None
    s = str(s).strip()
    m = re.match(r'^([-\d.]+)\s*%$', s)
    if m:
        return float(m.group(1))
    try:
        return float(s)
    except (ValueError, TypeError):
        return None

def ak_safe_call(fn, *args, default=None, **kwargs):
    """安全调用akshare接口，带重试"""
    for attempt in range(3):
        try:
            result = fn(*args, **kwargs)
            if result is None or (hasattr(result, 'empty') and result.empty):
                return default
            return result
        except Exception as e:
            if attempt == 2:
                print(f"  [AK] 接口异常: {e}")
                return default
            time.sleep(1)
    return default

# ── 30只自选股清单 ────────────────────────────────────────
WATCHLIST = [
    # 证券/金融
    ("600030", "中信证券"), ("601066", "中信建投"), ("600036", "招商银行"),
    ("601995", "中金公司"), ("000987", "越秀资本"),
    # 半导体/TMT
    ("600584", "长电科技"), ("688981", "中芯国际"), ("002156", "通富微电"),
    ("002413", "雷科防务"),
    # 新能源/储能
    ("300014", "亿纬锂能"), ("002466", "天齐锂业"), ("601865", "福莱特"),
    # 高端制造/材料
    ("300285", "国瓷材料"), ("603308", "应流股份"), ("300124", "汇川技术"),
    ("601100", "恒立液压"), ("002318", "久立特材"), ("300719", "安达维尔"),
    ("002335", "科华数据"), ("300748", "金力永磁"),
    # 打印机/办公设备
    ("002180", "奔图科技"), ("300847", "中船汉光"),
    # 化工
    ("600160", "巨化股份"), ("600346", "恒力石化"),
    # 钢铁/特钢
    ("000708", "中信特钢"),
    # 消费/其他
    ("600660", "福耀玻璃"), ("600570", "恒生电子"), ("605566", "福莱蒽特"),
    ("000157", "中联重科"), ("601061", "中信金属"),
]

# ── 财务数据获取 ──────────────────────────────────────────

def get_financial_abstract_ths(code: str) -> Optional[pd.DataFrame]:
    """同花顺财务摘要 - 核心指标：营收、净利、ROE、毛利率、每股收益"""
    return ak_safe_call(ak.stock_financial_abstract_ths, symbol=code, indicator="按年度")

def get_profit_sheet_ths(code: str) -> Optional[pd.DataFrame]:
    """同花顺利润表 - 含研发费用"""
    return ak_safe_call(ak.stock_financial_benefit_ths, symbol=code, indicator="按年度")

def get_profit_sheet_sina(code: str) -> Optional[pd.DataFrame]:
    """新浪利润表 - 备用研发费用"""
    return ak_safe_call(ak.stock_financial_report_sina, stock=code, symbol="利润表")

def get_cash_flow_ths(code: str) -> Optional[pd.DataFrame]:
    """同花顺现金流量表"""
    return ak_safe_call(ak.stock_financial_cash_ths, symbol=code, indicator="按年度")

def get_dividend_cninfo(code: str) -> Optional[pd.DataFrame]:
    """巨潮分红数据"""
    return ak_safe_call(ak.stock_dividend_cninfo, symbol=code)

# ── 数据解析 ──────────────────────────────────────────────

def extract_latest_annual(df: pd.DataFrame, year: int) -> Optional[Dict]:
    """从年度数据中提取指定年份最新行"""
    if df is None:
        return None
    for _, row in df.iterrows():
        rp = str(row.get('报告期', ''))
        if str(year) in rp and ('12-31' in rp or rp == str(year)):
            return row.to_dict()
    return None

def extract_latest_annual_sina(df: pd.DataFrame, year: int) -> Optional[Dict]:
    """从新浪利润表提取年报"""
    if df is None:
        return None
    target = f"{year}1231"
    for _, row in df.iterrows():
        rp = str(row.get('报告日', ''))
        if target in rp:
            return row.to_dict()
    return None

def get_rd_expense(code: str, year: int) -> Optional[float]:
    """获取研发费用(亿元) - 四级降级"""
    # Level 1: 同花顺利润表
    df = get_profit_sheet_ths(code)
    if df is not None:
        row = extract_latest_annual(df, year)
        if row:
            for col in ['研发费用', '研究开发费用', '研究与开发费用']:
                val = row.get(col)
                if val is not False and val is not None:
                    p = parse_amount(val)
                    if p is not None:
                        return p
    # Level 2: 新浪利润表
    df = get_profit_sheet_sina(code)
    if df is not None:
        row = extract_latest_annual_sina(df, year)
        if row:
            val = row.get('研发费用')
            f = safe_float(val)
            if f != NO_DATA:
                return f / 100000000
    return None

def get_dividend_info(code: str, year: int) -> Optional[Dict]:
    """获取分红信息"""
    df = get_dividend_cninfo(code)
    if df is None:
        return None
    target = f'{year}年报'
    for _, row in df.iterrows():
        rt = str(row.get('报告时间', ''))
        if target in rt:
            cash = row.get('派息比例', '')  # 每10股
            p = parse_amount(cash)
            if p is not None:
                return {'per_10_shares': p, 'per_share': p / 10}
    return None

def analyze_stock(code: str, name: str) -> Dict:
    """分析单只股票财报"""
    print(f"  分析 {name}({code})...")
    
    # 获取数据
    abstract_2024 = get_financial_abstract_ths(code)
    profit_2024 = get_profit_sheet_ths(code)
    cash_2024 = get_cash_flow_ths(code)
    
    # 2023年数据对比
    abstract_2023 = get_financial_abstract_ths(code)  # 同一接口含多年
    
    # 提取2024年报
    a24 = extract_latest_annual(abstract_2024, 2024) if abstract_2024 is not None else None
    a23 = extract_latest_annual(abstract_2024, 2023) if abstract_2024 is not None else None
    p24 = extract_latest_annual(profit_2024, 2024) if profit_2024 is not None else None
    c24 = extract_latest_annual(cash_2024, 2024) if cash_2024 is not None else None
    
    # 研发费用
    rd_2024 = get_rd_expense(code, 2024)
    rd_2023 = get_rd_expense(code, 2023)
    
    # 分红
    div_2024 = get_dividend_info(code, 2024)
    
    # ── 解析关键指标 ──────────────────────────────────
    def get_val(row, key, parser=parse_amount):
        if row is None:
            return None
        val = row.get(key)
        if val is None:
            return None
        return parser(val)
    
    def get_pct(row, key):
        return get_val(row, key, parse_percent)
    
    # 2024核心指标
    revenue_24 = get_val(a24, '营业总收入')
    net_profit_24 = get_val(a24, '净利润')
    roe_24 = get_pct(a24, '净资产收益率')
    gross_margin_24 = get_pct(a24, '销售毛利率')
    eps_24 = get_val(a24, '基本每股收益', lambda x: safe_float(x) if x else None)
    ocf_24 = get_val(c24, '经营活动产生的现金流量净额')
    capex_24 = get_val(c24, '购建固定资产、无形资产和其他长期资产支付的现金')
    
    # 2023对比
    revenue_23 = get_val(a23, '营业总收入')
    net_profit_23 = get_val(a23, '净利润')
    roe_23 = get_pct(a23, '净资产收益率')
    
    # 计算同比
    rev_yoy = None
    if revenue_24 and revenue_23 and revenue_23 != 0:
        rev_yoy = (revenue_24 - revenue_23) / revenue_23 * 100
    
    profit_yoy = None
    if net_profit_24 and net_profit_23 and net_profit_23 != 0:
        profit_yoy = (net_profit_24 - net_profit_23) / net_profit_23 * 100
    
    # 研发占比
    rd_ratio = None
    if rd_2024 and revenue_24 and revenue_24 != 0:
        rd_ratio = rd_2024 / revenue_24 * 100
    
    # 现金流质量
    ocf_to_profit = None
    if ocf_24 and net_profit_24 and net_profit_24 != 0:
        ocf_to_profit = ocf_24 / net_profit_24
    
    # 自由现金流
    fcf = None
    if ocf_24 and capex_24:
        fcf = ocf_24 - capex_24
    
    # ── 识别亮点与坑点 ─────────────────────────────────
    highlights = []
    risks = []
    
    # 亮点判断
    if profit_yoy and profit_yoy > 20:
        highlights.append(f"净利润高增 {profit_yoy:.1f}%")
    elif profit_yoy and profit_yoy > 10:
        highlights.append(f"净利润稳增 {profit_yoy:.1f}%")
    
    if rev_yoy and rev_yoy > 15:
        highlights.append(f"营收高增 {rev_yoy:.1f}%")
    
    if roe_24 and roe_24 > 15:
        highlights.append(f"ROE优秀 {roe_24:.1f}%")
    elif roe_24 and roe_24 > 10:
        highlights.append(f"ROE良好 {roe_24:.1f}%")
    
    if gross_margin_24 and gross_margin_24 > 40:
        highlights.append(f"毛利率高 {gross_margin_24:.1f}%")
    
    if rd_ratio and rd_ratio > 5:
        highlights.append(f"研发投入大 {rd_ratio:.1f}%营收")
    elif rd_ratio and rd_ratio > 3:
        highlights.append(f"研发投入中等 {rd_ratio:.1f}%营收")
    
    if ocf_to_profit and ocf_to_profit > 1.2:
        highlights.append(f"现金流优质 OCF/净利={ocf_to_profit:.2f}")
    elif ocf_to_profit and ocf_to_profit > 1.0:
        highlights.append(f"现金流健康 OCF/净利={ocf_to_profit:.2f}")
    
    if fcf and fcf > 0:
        highlights.append(f"自由现金流为正 {fcf:.1f}亿")
    
    if div_2024 and div_2024['per_share'] > 0:
        highlights.append(f"分红稳健 每股{div_2024['per_share']:.2f}元")
    
    # 坑点判断
    if profit_yoy is not None and profit_yoy < -10:
        risks.append(f"净利润大降 {profit_yoy:.1f}%")
    elif profit_yoy is not None and profit_yoy < 0:
        risks.append(f"净利润下滑 {profit_yoy:.1f}%")
    
    if rev_yoy is not None and rev_yoy < -10:
        risks.append(f"营收大降 {rev_yoy:.1f}%")
    elif rev_yoy is not None and rev_yoy < 0:
        risks.append(f"营收下滑 {rev_yoy:.1f}%")
    
    if roe_24 is not None and roe_24 < 5:
        risks.append(f"ROE偏低 {roe_24:.1f}%")
    
    if gross_margin_24 is not None and gross_margin_24 < 15:
        risks.append(f"毛利率薄 {gross_margin_24:.1f}%")
    
    if ocf_to_profit is not None and ocf_to_profit < 0.5:
        risks.append(f"现金流差 OCF/净利={ocf_to_profit:.2f}")
    
    if fcf is not None and fcf < -5:
        risks.append(f"自由现金流大额为负 {fcf:.1f}亿")
    
    if rd_ratio is not None and rd_ratio < 1 and name in ["汇川技术", "恒立液压", "中芯国际", "长电科技", "通富微电"]:
        risks.append(f"研发占比偏低 {rd_ratio:.1f}% (硬科技行业)")
    
    # 估值隐性风险
    if eps_24 and eps_24 < 0:
        risks.append("EPS为负，盈利能力存疑")
    
    return {
        'code': code,
        'name': name,
        'revenue': revenue_24,
        'net_profit': net_profit_24,
        'rev_yoy': rev_yoy,
        'profit_yoy': profit_yoy,
        'roe': roe_24,
        'gross_margin': gross_margin_24,
        'eps': eps_24,
        'rd_expense': rd_2024,
        'rd_ratio': rd_ratio,
        'ocf': ocf_24,
        'fcf': fcf,
        'ocf_to_profit': ocf_to_profit,
        'dividend': div_2024,
        'highlights': highlights,
        'risks': risks,
        'data_available': a24 is not None,
    }

# ── 主程序 ──────────────────────────────────────────────

def main():
    print("=" * 60)
    print("30只自选股财报亮点与坑点批量分析")
    print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("数据源: 同花顺财务摘要/利润表/现金流 + 巨潮分红 + 新浪备用")
    print("=" * 60)
    
    results = []
    for code, name in WATCHLIST:
        try:
            res = analyze_stock(code, name)
            results.append(res)
            time.sleep(0.5)  # 限流
        except Exception as e:
            print(f"  ❌ {name}({code}) 分析失败: {e}")
            results.append({'code': code, 'name': name, 'data_available': False, 'error': str(e)})
    
    # 生成报告
    generate_report(results)
    
    print("\n✅ 分析完成，报告已生成")

def generate_report(results: List[Dict]):
    """生成Markdown报告"""
    lines = []
    lines.append("# 30只自选股 2024年报财报亮点与坑点汇总简报")
    lines.append("")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 数据来源: 同花顺财务摘要/利润表/现金流量表、巨潮分红、新浪财报(备用)")
    lines.append(f"> 覆盖范围: 2024年年报为主，对比2023年同期")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # ── 总览表 ──────────────────────────────────────────
    lines.append("## 📊 核心指标总览表")
    lines.append("")
    lines.append("| 代码 | 名称 | 营收(亿) | 净利润(亿) | 营收同比 | 净利同比 | ROE | 毛利率 | 研发占比 | OCF/净利 | 分红/股 |")
    lines.append("|------|------|----------|------------|----------|----------|-----|--------|----------|----------|---------|")
    
    for r in results:
        if not r.get('data_available'):
            lines.append(f"| {r['code']} | {r['name']} | 数据获取失败 | - | - | - | - | - | - | - | - |")
            continue
        
        rev = f"{r['revenue']:.1f}" if r['revenue'] else "无"
        prof = f"{r['net_profit']:.1f}" if r['net_profit'] else "无"
        rev_yoy = f"{r['rev_yoy']:.1f}%" if r['rev_yoy'] is not None else "无"
        prof_yoy = f"{r['profit_yoy']:.1f}%" if r['profit_yoy'] is not None else "无"
        roe = f"{r['roe']:.1f}%" if r['roe'] is not None else "无"
        gm = f"{r['gross_margin']:.1f}%" if r['gross_margin'] is not None else "无"
        rd_r = f"{r['rd_ratio']:.1f}%" if r['rd_ratio'] is not None else "无"
        ocf_p = f"{r['ocf_to_profit']:.2f}" if r['ocf_to_profit'] is not None else "无"
        div = f"{r['dividend']['per_share']:.2f}" if r['dividend'] else "无"
        
        lines.append(f"| {r['code']} | {r['name']} | {rev} | {prof} | {rev_yoy} | {prof_yoy} | {roe} | {gm} | {rd_r} | {ocf_p} | {div} |")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # ── 板块分组分析 ────────────────────────────────────
    sectors = {
        "证券/金融": ["600030", "601066", "600036", "601995", "000987"],
        "半导体/TMT": ["600584", "688981", "002156", "002413"],
        "新能源/储能": ["300014", "002466", "601865"],
        "高端制造/材料": ["300285", "603308", "300124", "601100", "002318", "300719", "002335", "300748"],
        "打印机/办公设备": ["002180", "300847"],
        "化工": ["600160", "600346"],
        "钢铁/特钢": ["000708"],
        "消费/其他": ["600660", "600570", "605566", "000157", "601061"],
    }
    
    code_to_result = {r['code']: r for r in results}
    
    for sector_name, codes in sectors.items():
        lines.append(f"## 📈 {sector_name}")
        lines.append("")
        
        sector_results = [code_to_result.get(c) for c in codes if code_to_result.get(c)]
        
        # 亮点汇总
        all_highlights = []
        all_risks = []
        for r in sector_results:
            if r and r.get('data_available'):
                for h in r.get('highlights', []):
                    all_highlights.append(f"**{r['name']}**: {h}")
                for rk in r.get('risks', []):
                    all_risks.append(f"**{r['name']}**: {rk}")
        
        if all_highlights:
            lines.append("### ✅ 亮点")
            for h in all_highlights[:10]:  # 限制数量
                lines.append(f"- {h}")
            lines.append("")
        
        if all_risks:
            lines.append("### ⚠️ 坑点/风险")
            for rk in all_risks[:10]:
                lines.append(f"- {rk}")
            lines.append("")
        
        lines.append("---")
        lines.append("")
    
    # ── 重点关注名单 ────────────────────────────────────
    lines.append("## 🎯 重点关注名单（综合评分Top 10）")
    lines.append("")
    
    # 简单评分：营收增速*0.3 + 净利增速*0.3 + ROE*0.2 + 现金流*0.2
    scored = []
    for r in results:
        if not r.get('data_available'):
            continue
        score = 0
        if r['rev_yoy']: score += max(min(r['rev_yoy'], 50), -50) * 0.3
        if r['profit_yoy']: score += max(min(r['profit_yoy'], 50), -50) * 0.3
        if r['roe']: score += max(min(r['roe'], 30), 0) * 0.2
        if r['ocf_to_profit']: score += max(min(r['ocf_to_profit'] * 10, 30), -20) * 0.2
        scored.append((score, r))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    
    lines.append("| 排名 | 代码 | 名称 | 综合得分 | 核心逻辑 |")
    lines.append("|------|------|------|----------|----------|")
    for i, (score, r) in enumerate(scored[:10], 1):
        logic = []
        if r['profit_yoy'] and r['profit_yoy'] > 15: logic.append("高增")
        if r['roe'] and r['roe'] > 15: logic.append("高ROE")
        if r['ocf_to_profit'] and r['ocf_to_profit'] > 1: logic.append("现金流好")
        if r['rd_ratio'] and r['rd_ratio'] > 5: logic.append("研发强")
        lines.append(f"| {i} | {r['code']} | {r['name']} | {score:.1f} | {'、'.join(logic) or '均衡'} |")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # ── 避雷名单 ────────────────────────────────────────
    lines.append("## 🚫 避雷/关注风险名单")
    lines.append("")
    
    risk_stocks = []
    for r in results:
        if not r.get('data_available'):
            continue
        risk_count = len(r.get('risks', []))
        if risk_count >= 2:
            risk_stocks.append((risk_count, r))
    
    risk_stocks.sort(key=lambda x: x[0], reverse=True)
    
    lines.append("| 代码 | 名称 | 风险项数 | 主要风险 |")
    lines.append("|------|------|----------|----------|")
    for cnt, r in risk_stocks[:8]:
        main_risks = "；".join(r.get('risks', [])[:3])
        lines.append(f"| {r['code']} | {r['name']} | {cnt} | {main_risks} |")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # ── 详细个股卡片 ────────────────────────────────────
    lines.append("## 📋 个股详细财报卡片")
    lines.append("")
    
    for r in results:
        if not r.get('data_available'):
            lines.append(f"### {r['name']} ({r['code']})")
            lines.append(f"> ❌ 数据获取失败: {r.get('error', '未知错误')}")
            lines.append("")
            continue
        
        lines.append(f"### {r['name']} ({r['code']})")
        lines.append("")
        
        # 核心数据
        lines.append("**核心财务数据 (2024年报)**")
        lines.append("")
        data_lines = []
        if r['revenue']: data_lines.append(f"- 营收: **{r['revenue']:.1f}亿** (同比 {r['rev_yoy']:.1f}%)" if r['rev_yoy'] else f"- 营收: **{r['revenue']:.1f}亿**")
        if r['net_profit']: data_lines.append(f"- 净利润: **{r['net_profit']:.1f}亿** (同比 {r['profit_yoy']:.1f}%)" if r['profit_yoy'] else f"- 净利润: **{r['net_profit']:.1f}亿**")
        if r['roe'] is not None: data_lines.append(f"- ROE: **{r['roe']:.1f}%**")
        if r['gross_margin'] is not None: data_lines.append(f"- 销售毛利率: **{r['gross_margin']:.1f}%**")
        if r['eps'] is not None: data_lines.append(f"- 基本每股收益: **{r['eps']:.2f}元**")
        if r['rd_expense']: data_lines.append(f"- 研发费用: **{r['rd_expense']:.1f}亿** (占营收 {r['rd_ratio']:.1f}%)" if r['rd_ratio'] else f"- 研发费用: **{r['rd_expense']:.1f}亿**")
        if r['ocf']: data_lines.append(f"- 经营现金流: **{r['ocf']:.1f}亿**")
        if r['fcf'] is not None: data_lines.append(f"- 自由现金流: **{r['fcf']:.1f}亿**")
        if r['ocf_to_profit']: data_lines.append(f"- OCF/净利润: **{r['ocf_to_profit']:.2f}**")
        if r['dividend']: data_lines.append(f"- 分红: **每股{r['dividend']['per_share']:.2f}元** (每10派{r['dividend']['per_10_shares']:.2f}元)")
        lines.extend(data_lines)
        lines.append("")
        
        # 亮点
        if r.get('highlights'):
            lines.append("**✅ 亮点**")
            for h in r['highlights']:
                lines.append(f"- {h}")
            lines.append("")
        
        # 坑点
        if r.get('risks'):
            lines.append("**⚠️ 坑点/风险**")
            for rk in r['risks']:
                lines.append(f"- {rk}")
            lines.append("")
        
        lines.append("---")
        lines.append("")
    
    # 风险提示
    lines.append("> **风险提示**: 以上分析基于历史财务数据（2024年报及2023年对比），过去表现不代表未来结果。")
    lines.append("> 财报数据存在滞后性，投资决策请结合最新业绩预告、行业周期、宏观环境及个人风险承受能力综合判断。")
    lines.append("> 数据来源为公开接口，可能存在延迟或差异，仅供参考，不构成投资建议。")
    
    # 写入文件
    output_path = "/Users/duguke/.openclaw/workspace/analysis/financial_report_summary_2024.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    print(f"\n📄 报告已保存至: {output_path}")

if __name__ == "__main__":
    main()