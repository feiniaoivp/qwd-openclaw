#!/usr/bin/env python3
"""
财报亮点与坑点批量分析脚本 (v2 - baostock主力版)
使用 baostock 作为主数据源（稳定、快），仅研发费用用 akshare 兜底
"""

import baostock as bs
import akshare as ak
import pandas as pd
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ── 配置 ──────────────────────────────────────────────────
NO_DATA = "无"

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

# ── 工具函数 ──────────────────────────────────────────────

def safe_float(val, default=NO_DATA):
    if val is None or val == '' or val == 'False' or val == 'NaN' or val is False:
        return default
    try:
        f = float(val)
        if f != f or abs(f) > 1e15:
            return default
        return f
    except (ValueError, TypeError):
        return default

def parse_amount(s):
    if s is None or s == '' or s == 'False' or s is False:
        return None
    s = str(s).strip()
    m = re.match(r'^([-\d.]+)\s*万亿$', s)
    if m: return float(m.group(1)) * 10000
    m = re.match(r'^([-\d.]+)\s*亿$', s)
    if m: return float(m.group(1))
    m = re.match(r'^([-\d.]+)\s*万$', s)
    if m: return float(m.group(1)) / 10000
    try: return float(s)
    except: return None

def bs_code(pure_code: str) -> str:
    """纯代码转 baostock 格式"""
    if pure_code.startswith('6') or pure_code.startswith('9'):
        return 'sh.' + pure_code
    elif pure_code.startswith('0') or pure_code.startswith('3'):
        return 'sz.' + pure_code
    elif pure_code.startswith('8') or pure_code.startswith('4'):
        return 'bj.' + pure_code
    raise ValueError(f"无法识别代码: {pure_code}")

def get_rd_expense_ak(pure_code: str, year: int) -> Optional[float]:
    """获取研发费用(亿元) - 仅 akshare 兜底，带超时保护"""
    import signal
    
    def timeout_handler(signum, frame):
        raise TimeoutError("akshare timeout")
    
    # 同花顺利润表
    try:
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(15)
        df = ak.stock_financial_benefit_ths(symbol=pure_code, indicator="按年度")
        signal.alarm(0)
        if df is not None:
            for _, row in df.iterrows():
                rp = str(row.get('报告期', ''))
                if str(year) in rp:
                    for col in ['研发费用', '研究开发费用', '研究与开发费用']:
                        val = row.get(col)
                        if val is not False and val is not None:
                            p = parse_amount(val)
                            if p is not None:
                                return p
    except Exception:
        pass
    
    # 新浪利润表
    try:
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(15)
        df = ak.stock_financial_report_sina(stock=pure_code, symbol="利润表")
        signal.alarm(0)
        if df is not None:
            target = f"{year}1231"
            for _, row in df.iterrows():
                rp = str(row.get('报告日', ''))
                if target in rp:
                    val = row.get('研发费用')
                    f = safe_float(val)
                    if f != NO_DATA:
                        return f / 100000000
    except Exception:
        pass
    
    return None

# ── 单股分析 ──────────────────────────────────────────────

def analyze_stock_bs(pure_code: str, name: str) -> Dict:
    """用 baostock 分析单只股票"""
    bs_code_str = bs_code(pure_code)
    
    # 查询 2024 年报 (Q4) 和 2023 年报 (Q4)
    def query_all(year: int, quarter: int) -> Dict:
        """查询某年某季度的所有财务表"""
        data = {}
        
        # 盈利
        rs = bs.query_profit_data(code=bs_code_str, year=year, quarter=quarter)
        rows = []
        while rs.error_code == '0' and rs.next():
            rows.append(rs.get_row_data())
        if rows:
            data['profit'] = dict(zip(rs.fields, rows[0]))
        
        # 成长
        rs = bs.query_growth_data(code=bs_code_str, year=year, quarter=quarter)
        rows = []
        while rs.error_code == '0' and rs.next():
            rows.append(rs.get_row_data())
        if rows:
            data['growth'] = dict(zip(rs.fields, rows[0]))
        
        # 现金流
        rs = bs.query_cash_flow_data(code=bs_code_str, year=year, quarter=quarter)
        rows = []
        while rs.error_code == '0' and rs.next():
            rows.append(rs.get_row_data())
        if rows:
            data['cashflow'] = dict(zip(rs.fields, rows[0]))
        
        # 运营
        rs = bs.query_operation_data(code=bs_code_str, year=year, quarter=quarter)
        rows = []
        while rs.error_code == '0' and rs.next():
            rows.append(rs.get_row_data())
        if rows:
            data['operation'] = dict(zip(rs.fields, rows[0]))
        
        # 资产负债
        rs = bs.query_balance_data(code=bs_code_str, year=year, quarter=quarter)
        rows = []
        while rs.error_code == '0' and rs.next():
            rows.append(rs.get_row_data())
        if rows:
            data['balance'] = dict(zip(rs.fields, rows[0]))
        
        return data
    
    d24 = query_all(2024, 4)
    d23 = query_all(2023, 4)
    
    # 分红
    div_info = None
    rs = bs.query_dividend_data(code=bs_code_str, year=2024, yearType='report')
    rows = []
    while rs.error_code == '0' and rs.next():
        rows.append(rs.get_row_data())
    for r in rows:
        if '2024' in str(r[rs.fields.index('dividPlanAnnounceDate')]) if 'dividPlanAnnounceDate' in rs.fields else False:
            cash_ps = safe_float(r[rs.fields.index('dividCashPsBeforeTax')]) if 'dividCashPsBeforeTax' in rs.fields else NO_DATA
            if cash_ps != NO_DATA:
                div_info = {'per_share': cash_ps, 'per_10_shares': cash_ps * 10}
                break
    
    # ── 解析关键指标 ────────────────────────────────────
    def g(data, section, key, default=None):
        return safe_float(data.get(section, {}).get(key), default)
    
    # 2024 核心
    revenue_24 = g(d24, 'profit', 'MBRevenue')  # 主营业务收入(元)
    net_profit_24 = g(d24, 'profit', 'netProfit')  # 净利润(元)
    roe_24 = g(d24, 'profit', 'roeAvg')  # 平均ROE(小数)
    gp_margin_24 = g(d24, 'profit', 'gpMargin')  # 毛利率(小数)
    eps_24 = g(d24, 'profit', 'epsTTM')  # 每股收益TTM(元)
    total_share_24 = g(d24, 'profit', 'totalShare')  # 总股本(股)
    
    # 同比增长率 (来自成长表，小数)
    rev_yoy = g(d24, 'growth', 'YOYOR')  # 营业总收入同比增长率
    profit_yoy = g(d24, 'growth', 'YOYNI')  # 净利润同比增长率
    equity_yoy = g(d24, 'growth', 'YOYEquity')  # 净资产同比
    
    # 现金流质量指标 (比率)
    cfo_to_or = g(d24, 'cashflow', 'CFOToOR')  # 经营现金流/营业收入
    cfo_to_np = g(d24, 'cashflow', 'CFOToNP')  # 经营现金流/净利润
    
    # 资产负债
    liability_to_asset = g(d24, 'balance', 'liabilityToAsset')  # 资产负债率
    current_ratio = g(d24, 'balance', 'currentRatio')  # 流动比率
    
    # 2023 对比
    revenue_23 = g(d23, 'profit', 'MBRevenue')
    net_profit_23 = g(d23, 'profit', 'netProfit')
    roe_23 = g(d23, 'profit', 'roeAvg')
    
    # 转换单位：元 → 亿
    def to_yi(val):
        if val == NO_DATA or val is None: return None
        return val / 100000000
    
    revenue_24 = to_yi(revenue_24)
    net_profit_24 = to_yi(net_profit_24)
    revenue_23 = to_yi(revenue_23)
    net_profit_23 = to_yi(net_profit_23)
    
    # 计算同比 (如果成长表没给，自己算)
    if rev_yoy == NO_DATA and revenue_24 and revenue_23 and revenue_23 != 0:
        rev_yoy = (revenue_24 - revenue_23) / revenue_23 * 100
    elif rev_yoy != NO_DATA:
        rev_yoy = rev_yoy * 100
    
    if profit_yoy == NO_DATA and net_profit_24 and net_profit_23 and net_profit_23 != 0:
        profit_yoy = (net_profit_24 - net_profit_23) / net_profit_23 * 100
    elif profit_yoy != NO_DATA:
        profit_yoy = profit_yoy * 100
    
    if roe_24 != NO_DATA:
        roe_24 = roe_24 * 100
    if gp_margin_24 != NO_DATA:
        gp_margin_24 = gp_margin_24 * 100
    if liability_to_asset != NO_DATA:
        liability_to_asset = liability_to_asset * 100
    
    # 研发费用 (akshare 兜底)
    rd_2024 = get_rd_expense_ak(pure_code, 2024)
    rd_2023 = get_rd_expense_ak(pure_code, 2023)
    rd_ratio = None
    if rd_2024 and revenue_24 and revenue_24 != 0:
        rd_ratio = rd_2024 / revenue_24 * 100
    
    # ── 识别亮点与坑点 ──────────────────────────────────
    highlights = []
    risks = []
    
    # 亮点
    if profit_yoy != NO_DATA and profit_yoy > 20:
        highlights.append(f"净利润高增 {profit_yoy:.1f}%")
    elif profit_yoy != NO_DATA and profit_yoy > 10:
        highlights.append(f"净利润稳增 {profit_yoy:.1f}%")
    
    if rev_yoy != NO_DATA and rev_yoy > 15:
        highlights.append(f"营收高增 {rev_yoy:.1f}%")
    
    if roe_24 != NO_DATA and roe_24 > 15:
        highlights.append(f"ROE优秀 {roe_24:.1f}%")
    elif roe_24 != NO_DATA and roe_24 > 10:
        highlights.append(f"ROE良好 {roe_24:.1f}%")
    
    if gp_margin_24 != NO_DATA and gp_margin_24 > 40:
        highlights.append(f"毛利率高 {gp_margin_24:.1f}%")
    elif gp_margin_24 != NO_DATA and gp_margin_24 > 30:
        highlights.append(f"毛利率中等偏上 {gp_margin_24:.1f}%")
    
    if rd_ratio and rd_ratio > 5:
        highlights.append(f"研发投入强 {rd_ratio:.1f}%营收")
    elif rd_ratio and rd_ratio > 3:
        highlights.append(f"研发投入中等 {rd_ratio:.1f}%营收")
    
    if cfo_to_np != NO_DATA and cfo_to_np > 1.2:
        highlights.append(f"现金流优质 OCF/净利={cfo_to_np:.2f}")
    elif cfo_to_np != NO_DATA and cfo_to_np > 1.0:
        highlights.append(f"现金流健康 OCF/净利={cfo_to_np:.2f}")
    
    if cfo_to_or != NO_DATA and cfo_to_or > 0.1:
        highlights.append(f"经营现金流/营收良好 {cfo_to_or:.2f}")
    
    if div_info and div_info['per_share'] > 0:
        highlights.append(f"分红稳健 每股{div_info['per_share']:.2f}元")
    
    if liability_to_asset != NO_DATA and liability_to_asset < 40:
        highlights.append(f"负债率低 {liability_to_asset:.1f}%")
    
    # 坑点
    if profit_yoy != NO_DATA and profit_yoy < -10:
        risks.append(f"净利润大降 {profit_yoy:.1f}%")
    elif profit_yoy != NO_DATA and profit_yoy < 0:
        risks.append(f"净利润下滑 {profit_yoy:.1f}%")
    
    if rev_yoy != NO_DATA and rev_yoy < -10:
        risks.append(f"营收大降 {rev_yoy:.1f}%")
    elif rev_yoy != NO_DATA and rev_yoy < 0:
        risks.append(f"营收下滑 {rev_yoy:.1f}%")
    
    if roe_24 != NO_DATA and roe_24 < 5:
        risks.append(f"ROE偏低 {roe_24:.1f}%")
    
    if gp_margin_24 != NO_DATA and gp_margin_24 < 15:
        risks.append(f"毛利率薄 {gp_margin_24:.1f}%")
    
    if cfo_to_np != NO_DATA and cfo_to_np < 0.5:
        risks.append(f"现金流差 OCF/净利={cfo_to_np:.2f}")
    
    if liability_to_asset != NO_DATA and liability_to_asset > 70:
        risks.append(f"负债率高 {liability_to_asset:.1f}%")
    
    if current_ratio != NO_DATA and current_ratio < 1:
        risks.append(f"流动性紧张 流动比率={current_ratio:.2f}")
    
    if rd_ratio is not None and rd_ratio < 1 and name in ["汇川技术", "恒立液压", "中芯国际", "长电科技", "通富微电", "亿纬锂能", "天齐锂业"]:
        risks.append(f"研发占比偏低 {rd_ratio:.1f}% (硬科技行业)")
    
    if eps_24 != NO_DATA and eps_24 < 0:
        risks.append("EPS为负，盈利能力存疑")
    
    if net_profit_24 is not None and net_profit_24 < 0:
        risks.append("净利润为负，亏损状态")
    
    return {
        'code': pure_code,
        'name': name,
        'revenue': revenue_24,
        'net_profit': net_profit_24,
        'rev_yoy': rev_yoy if rev_yoy != NO_DATA else None,
        'profit_yoy': profit_yoy if profit_yoy != NO_DATA else None,
        'roe': roe_24 if roe_24 != NO_DATA else None,
        'gross_margin': gp_margin_24 if gp_margin_24 != NO_DATA else None,
        'eps': eps_24 if eps_24 != NO_DATA else None,
        'rd_expense': rd_2024,
        'rd_ratio': rd_ratio,
        'cfo_to_np': cfo_to_np if cfo_to_np != NO_DATA else None,
        'cfo_to_or': cfo_to_or if cfo_to_or != NO_DATA else None,
        'dividend': div_info,
        'liability_ratio': liability_to_asset if liability_to_asset != NO_DATA else None,
        'current_ratio': current_ratio if current_ratio != NO_DATA else None,
        'highlights': highlights,
        'risks': risks,
        'data_available': 'profit' in d24,
    }

# ── 主程序 ──────────────────────────────────────────────

def main():
    print("=" * 60)
    print("30只自选股财报亮点与坑点批量分析 (baostock版)")
    print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("数据源: baostock(主力) + akshare(研发费用兜底)")
    print("=" * 60)
    
    # 登录 baostock
    lg = bs.login()
    if lg.error_code != '0':
        print(f"❌ baostock 登录失败: {lg.error_msg}")
        return
    print("✅ baostock 登录成功")
    
    results = []
    for i, (code, name) in enumerate(WATCHLIST, 1):
        try:
            print(f"[{i:2d}/30] 分析 {name}({code})...")
            res = analyze_stock_bs(code, name)
            results.append(res)
            time.sleep(0.1)  # baostock 很快，轻微限流
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            results.append({'code': code, 'name': name, 'data_available': False, 'error': str(e)})
    
    bs.logout()
    print("✅ baostock 登出")
    
    # 生成报告
    generate_report(results)
    print("\n✅ 分析完成！")

def generate_report(results: List[Dict]):
    lines = []
    lines.append("# 30只自选股 2024年报财报亮点与坑点汇总简报")
    lines.append("")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 数据来源: **baostock** (盈利/成长/现金流/运营/资产负债/分红) + **akshare** (研发费用兜底)")
    lines.append(f"> 覆盖范围: 2024年年报(Q4)为主，对比2023年年报同期")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # ── 总览表 ──────────────────────────────────────────
    lines.append("## 📊 核心指标总览表")
    lines.append("")
    lines.append("| 代码 | 名称 | 营收(亿) | 净利润(亿) | 营收同比 | 净利同比 | ROE | 毛利率 | 研发占比 | OCF/净利 | OCF/营收 | 负债率 | 分红/股 |")
    lines.append("|------|------|----------|------------|----------|----------|-----|--------|----------|----------|----------|--------|---------|")
    
    for r in results:
        if not r.get('data_available'):
            lines.append(f"| {r['code']} | {r['name']} | 数据获取失败 | - | - | - | - | - | - | - | - | - | - |")
            continue
        
        rev = f"{r['revenue']:.1f}" if r['revenue'] else "无"
        prof = f"{r['net_profit']:.1f}" if r['net_profit'] else "无"
        rev_yoy = f"{r['rev_yoy']:.1f}%" if r['rev_yoy'] is not None else "无"
        prof_yoy = f"{r['profit_yoy']:.1f}%" if r['profit_yoy'] is not None else "无"
        roe = f"{r['roe']:.1f}%" if r['roe'] is not None else "无"
        gm = f"{r['gross_margin']:.1f}%" if r['gross_margin'] is not None else "无"
        rd_r = f"{r['rd_ratio']:.1f}%" if r['rd_ratio'] is not None else "无"
        cfo_np = f"{r['cfo_to_np']:.2f}" if r['cfo_to_np'] is not None else "无"
        cfo_or = f"{r['cfo_to_or']:.2f}" if r['cfo_to_or'] is not None else "无"
        lev = f"{r['liability_ratio']:.1f}%" if r['liability_ratio'] is not None else "无"
        div = f"{r['dividend']['per_share']:.2f}" if r['dividend'] else "无"
        
        lines.append(f"| {r['code']} | {r['name']} | {rev} | {prof} | {rev_yoy} | {prof_yoy} | {roe} | {gm} | {rd_r} | {cfo_np} | {cfo_or} | {lev} | {div} |")
    
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
        
        all_highlights = []
        all_risks = []
        for r in sector_results:
            if r and r.get('data_available'):
                for h in r.get('highlights', []):
                    all_highlights.append(f"**{r['name']}**: {h}")
                for rk in r.get('risks', []):
                    all_risks.append(f"**{r['name']}**: {rk}")
        
        if all_highlights:
            lines.append("### ✅ 板块亮点")
            for h in all_highlights[:12]:
                lines.append(f"- {h}")
            lines.append("")
        
        if all_risks:
            lines.append("### ⚠️ 板块风险")
            for rk in all_risks[:12]:
                lines.append(f"- {rk}")
            lines.append("")
        
        lines.append("---")
        lines.append("")
    
    # ── 重点关注名单 ────────────────────────────────────
    lines.append("## 🎯 重点关注名单（综合评分Top 10）")
    lines.append("")
    
    scored = []
    for r in results:
        if not r.get('data_available'): continue
        score = 0
        if r['rev_yoy']: score += max(min(r['rev_yoy'], 50), -50) * 0.25
        if r['profit_yoy']: score += max(min(r['profit_yoy'], 50), -50) * 0.30
        if r['roe']: score += max(min(r['roe'], 30), 0) * 0.20
        if r['cfo_to_np']: score += max(min(r['cfo_to_np'] * 10, 30), -20) * 0.15
        if r['rd_ratio']: score += min(r['rd_ratio'] * 2, 20) * 0.10
        scored.append((score, r))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    
    lines.append("| 排名 | 代码 | 名称 | 综合得分 | 核心优势 |")
    lines.append("|------|------|------|----------|----------|")
    for i, (score, r) in enumerate(scored[:10], 1):
        logic = []
        if r['profit_yoy'] and r['profit_yoy'] > 15: logic.append("高增")
        if r['roe'] and r['roe'] > 15: logic.append("高ROE")
        if r['cfo_to_np'] and r['cfo_to_np'] > 1: logic.append("现金流好")
        if r['rd_ratio'] and r['rd_ratio'] > 5: logic.append("研发强")
        if r['gross_margin'] and r['gross_margin'] > 35: logic.append("高毛利")
        if r['liability_ratio'] and r['liability_ratio'] < 30: logic.append("低负债")
        lines.append(f"| {i} | {r['code']} | {r['name']} | {score:.1f} | {'、'.join(logic) or '均衡'} |")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # ── 避雷名单 ────────────────────────────────────────
    lines.append("## 🚫 避雷/重点关注风险名单")
    lines.append("")
    
    risk_stocks = []
    for r in results:
        if not r.get('data_available'): continue
        risk_count = len(r.get('risks', []))
        if risk_count >= 2:
            risk_stocks.append((risk_count, r))
    
    risk_stocks.sort(key=lambda x: x[0], reverse=True)
    
    lines.append("| 代码 | 名称 | 风险项数 | 主要风险 |")
    lines.append("|------|------|----------|----------|")
    for cnt, r in risk_stocks[:10]:
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
        
        lines.append("**核心财务数据 (2024年报)**")
        lines.append("")
        data_lines = []
        if r['revenue']: data_lines.append(f"- 营收: **{r['revenue']:.1f}亿** (同比 {r['rev_yoy']:.1f}%)" if r['rev_yoy'] else f"- 营收: **{r['revenue']:.1f}亿**")
        if r['net_profit']: data_lines.append(f"- 净利润: **{r['net_profit']:.1f}亿** (同比 {r['profit_yoy']:.1f}%)" if r['profit_yoy'] else f"- 净利润: **{r['net_profit']:.1f}亿**")
        if r['roe'] is not None: data_lines.append(f"- ROE: **{r['roe']:.1f}%**")
        if r['gross_margin'] is not None: data_lines.append(f"- 销售毛利率: **{r['gross_margin']:.1f}%**")
        if r['eps'] is not None: data_lines.append(f"- 每股收益TTM: **{r['eps']:.2f}元**")
        if r['rd_expense']: data_lines.append(f"- 研发费用: **{r['rd_expense']:.1f}亿** (占营收 {r['rd_ratio']:.1f}%)" if r['rd_ratio'] else f"- 研发费用: **{r['rd_expense']:.1f}亿**")
        if r['cfo_to_np']: data_lines.append(f"- OCF/净利润: **{r['cfo_to_np']:.2f}**")
        if r['cfo_to_or']: data_lines.append(f"- OCF/营收: **{r['cfo_to_or']:.2f}**")
        if r['liability_ratio'] is not None: data_lines.append(f"- 资产负债率: **{r['liability_ratio']:.1f}%**")
        if r['current_ratio'] is not None: data_lines.append(f"- 流动比率: **{r['current_ratio']:.2f}**")
        if r['dividend']: data_lines.append(f"- 分红: **每股{r['dividend']['per_share']:.2f}元** (每10派{r['dividend']['per_10_shares']:.2f}元)")
        lines.extend(data_lines)
        lines.append("")
        
        if r.get('highlights'):
            lines.append("**✅ 亮点**")
            for h in r['highlights']:
                lines.append(f"- {h}")
            lines.append("")
        
        if r.get('risks'):
            lines.append("**⚠️ 坑点/风险**")
            for rk in r['risks']:
                lines.append(f"- {rk}")
            lines.append("")
        
        lines.append("---")
        lines.append("")
    
    lines.append("> **风险提示**: 以上分析基于历史财务数据（2024年报及2023年对比），过去表现不代表未来结果。")
    lines.append("> 财报数据存在滞后性，投资决策请结合最新业绩预告、行业周期、宏观环境及个人风险承受能力综合判断。")
    lines.append("> 数据来源为公开接口，可能存在延迟或差异，仅供参考，不构成投资建议。")
    
    output_path = "/Users/duguke/.openclaw/workspace/analysis/financial_report_summary_2024.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    print(f"\n📄 报告已保存至: {output_path}")

if __name__ == "__main__":
    main()