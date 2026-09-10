import baostock as bs
import akshare as ak
import signal
import time
from datetime import datetime

WATCHLIST = [
    ('600030', '中信证券'), ('601066', '中信建投'), ('600036', '招商银行'),
    ('601995', '中金公司'), ('000987', '越秀资本'),
    ('600584', '长电科技'), ('688981', '中芯国际'), ('002156', '通富微电'),
    ('002413', '雷科防务'),
    ('300014', '亿纬锂能'), ('002466', '天齐锂业'), ('601865', '福莱特'),
    ('300285', '国瓷材料'), ('603308', '应流股份'), ('300124', '汇川技术'),
    ('601100', '恒立液压'), ('002318', '久立特材'), ('300719', '安达维尔'),
    ('002335', '科华数据'), ('300748', '金力永磁'),
    ('002180', '奔图科技'), ('300847', '中船汉光'),
    ('600160', '巨化股份'), ('600346', '恒力石化'),
    ('000708', '中信特钢'),
    ('600660', '福耀玻璃'), ('600570', '恒生电子'), ('605566', '福莱蒽特'),
    ('000157', '中联重科'), ('601061', '中信金属'),
]

def bs_code(pure):
    if pure.startswith('6') or pure.startswith('9'): return 'sh.' + pure
    elif pure.startswith('0') or pure.startswith('3'): return 'sz.' + pure
    elif pure.startswith('8') or pure.startswith('4'): return 'bj.' + pure
    raise ValueError(pure)

def get_rd(pure, year):
    try:
        def h(s,f): raise TimeoutError()
        signal.signal(signal.SIGALRM, h)
        signal.alarm(10)
        df = ak.stock_financial_benefit_ths(symbol=pure, indicator='按年度')
        signal.alarm(0)
        if df is not None:
            for _, r in df.iterrows():
                if str(year) in str(r.get('报告期','')):
                    for c in ['研发费用','研究开发费用','研究与开发费用']:
                        v = r.get(c)
                        if v is not False and v is not None:
                            s = str(v).strip()
                            if '亿' in s: return float(s.replace('亿',''))
                            if '万' in s: return float(s.replace('万',''))/10000
                            try: return float(s)/1e8
                            except: pass
    except: pass
    return None

print("登录 baostock...")
lg = bs.login()
print(f"Login: {lg.error_code}")

results = []
for i, (code, name) in enumerate(WATCHLIST):
    bsc = bs_code(code)
    
    d24 = {}
    for fn, key in [(bs.query_profit_data, 'profit'), (bs.query_growth_data, 'growth'),
                    (bs.query_cash_flow_data, 'cashflow'), (bs.query_balance_data, 'balance')]:
        rs = fn(code=bsc, year=2024, quarter=4)
        rows = []
        while rs.error_code == '0' and rs.next():
            rows.append(rs.get_row_data())
        if rows: d24[key] = dict(zip(rs.fields, rows[0]))
    
    d23 = {}
    rs = bs.query_profit_data(code=bsc, year=2023, quarter=4)
    rows = []
    while rs.error_code == '0' and rs.next():
        rows.append(rs.get_row_data())
    if rows: d23['profit'] = dict(zip(rs.fields, rows[0]))
    
    div = None
    rs = bs.query_dividend_data(code=bsc, year=2024, yearType='report')
    rows = []
    while rs.error_code == '0' and rs.next():
        rows.append(rs.get_row_data())
    for r in rows:
        if 'dividCashPsBeforeTax' in rs.fields:
            idx = rs.fields.index('dividCashPsBeforeTax')
            val = r[idx]
            if val and val != '':
                f = float(val)
                if f > 0:
                    div = {'per_share': f, 'per_10': f*10}
                    break
    
    def g(d, sec, k):
        try: return float(d.get(sec,{}).get(k, 0))
        except: return None
    
    rev24 = g(d24, 'profit', 'MBRevenue')
    prof24 = g(d24, 'profit', 'netProfit')
    roe24 = g(d24, 'profit', 'roeAvg')
    gm24 = g(d24, 'profit', 'gpMargin')
    eps24 = g(d24, 'profit', 'epsTTM')
    rev_yoy = g(d24, 'growth', 'YOYOR')
    prof_yoy = g(d24, 'growth', 'YOYNI')
    cfo_np = g(d24, 'cashflow', 'CFOToNP')
    cfo_or = g(d24, 'cashflow', 'CFOToOR')
    lev = g(d24, 'balance', 'liabilityToAsset')
    cr = g(d24, 'balance', 'currentRatio')
    
    rev23 = g(d23, 'profit', 'MBRevenue')
    prof23 = g(d23, 'profit', 'netProfit')
    roe23 = g(d23, 'profit', 'roeAvg')
    
    def yi(v): return v/1e8 if v else None
    rev24, prof24, rev23, prof23 = map(yi, [rev24, prof24, rev23, prof23])
    if roe24: roe24 *= 100
    if gm24: gm24 *= 100
    if rev_yoy: rev_yoy *= 100
    if prof_yoy: prof_yoy *= 100
    if lev: lev *= 100
    
    if (rev_yoy is None or rev_yoy == 0) and rev24 and rev23 and rev23 != 0:
        rev_yoy = (rev24 - rev23)/rev23*100
    if (prof_yoy is None or prof_yoy == 0) and prof24 and prof23 and prof23 != 0:
        prof_yoy = (prof24 - prof23)/prof23*100
    
    rd24 = get_rd(code, 2024)
    rd_ratio = rd24/rev24*100 if rd24 and rev24 else None
    
    hl, rk = [], []
    if prof_yoy and prof_yoy > 20: hl.append(f'净利高增{prof_yoy:.1f}%')
    elif prof_yoy and prof_yoy > 10: hl.append(f'净利稳增{prof_yoy:.1f}%')
    if rev_yoy and rev_yoy > 15: hl.append(f'营收高增{rev_yoy:.1f}%')
    if roe24 and roe24 > 15: hl.append(f'ROE优秀{roe24:.1f}%')
    elif roe24 and roe24 > 10: hl.append(f'ROE良好{roe24:.1f}%')
    if gm24 and gm24 > 40: hl.append(f'高毛利{gm24:.1f}%')
    if rd_ratio and rd_ratio > 5: hl.append(f'研发强{rd_ratio:.1f}%')
    if cfo_np and cfo_np > 1.2: hl.append(f'现金流优{cfo_np:.2f}')
    if div: hl.append(f'分红{div["per_share"]:.2f}/股')
    if lev and lev < 40: hl.append(f'低负债{lev:.1f}%')
    
    if prof_yoy and prof_yoy < -10: rk.append(f'净利大降{prof_yoy:.1f}%')
    elif prof_yoy and prof_yoy < 0: rk.append(f'净利下滑{prof_yoy:.1f}%')
    if rev_yoy and rev_yoy < -10: rk.append(f'营收大降{rev_yoy:.1f}%')
    elif rev_yoy and rev_yoy < 0: rk.append(f'营收下滑{rev_yoy:.1f}%')
    if roe24 and roe24 < 5: rk.append(f'ROE低{roe24:.1f}%')
    if gm24 and gm24 < 15: rk.append(f'薄毛利{gm24:.1f}%')
    if cfo_np and cfo_np < 0.5: rk.append(f'现金流差{cfo_np:.2f}')
    if lev and lev > 70: rk.append(f'高负债{lev:.1f}%')
    if cr and cr < 1: rk.append(f'流动性紧{cr:.2f}')
    if rd_ratio and rd_ratio < 1 and name in ['汇川技术','恒立液压','中芯国际','长电科技','通富微电','亿纬锂能','天齐锂业']:
        rk.append(f'研发占比低{rd_ratio:.1f}%')
    if eps24 and eps24 < 0: rk.append('EPS为负')
    if prof24 and prof24 < 0: rk.append('亏损')
    
    results.append({
        'code': code, 'name': name,
        'rev': rev24, 'prof': prof24,
        'rev_yoy': rev_yoy, 'prof_yoy': prof_yoy,
        'roe': roe24, 'gm': gm24, 'eps': eps24,
        'rd': rd24, 'rd_r': rd_ratio,
        'cfo_np': cfo_np, 'cfo_or': cfo_or,
        'lev': lev, 'cr': cr, 'div': div,
        'hl': hl, 'rk': rk,
        'ok': 'profit' in d24
    })
    print(f'[{i+1:2d}/30] {name}({code}) OK')
    time.sleep(0.05)

bs.logout()
print("登出完成")

# 打印核心表格供人工整理
print("\n=== 核心指标表 ===")
print("| 代码 | 名称 | 营收(亿) | 净利(亿) | 营收同比 | 净利同比 | ROE | 毛利率 | 研发占比 | OCF/净利 | OCF/营收 | 负债率 | 分红/股 |")
print("|------|------|----------|----------|----------|----------|-----|--------|----------|----------|----------|--------|---------|")
for r in results:
    if not r['ok']:
        print(f"| {r['code']} | {r['name']} | 数据失败 | - | - | - | - | - | - | - | - | - | - |")
        continue
    def f(v, fmt='.1f'): return format(v, fmt) if v is not None else '无'
    print(f"| {r['code']} | {r['name']} | {f(r['rev'])} | {f(r['prof'])} | {f(r['rev_yoy'])}% | {f(r['prof_yoy'])}% | {f(r['roe'])}% | {f(r['gm'])}% | {f(r['rd_r'])}% | {f(r['cfo_np'], '.2f')} | {f(r['cfo_or'], '.2f')} | {f(r['lev'])}% | {f(r['div']['per_share']) if r['div'] else '无'} |")

print("\n=== 亮点/风险汇总 ===")
for r in results:
    if not r['ok']: continue
    if r['hl'] or r['rk']:
        print(f"\n{r['name']}({r['code']}):")
        for h in r['hl']: print(f"  ✅ {h}")
        for k in r['rk']: print(f"  ⚠️ {k}")

print("\n=== 完成 ===")