import baostock as bs
import json

lg = bs.login()

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

def g(d, sec, k):
    try: return float(d.get(sec,{}).get(k, 0))
    except: return None

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
    hl, rk = [], []
    if prof_yoy and prof_yoy > 20: hl.append('净利高增%.1f%%'%prof_yoy)
    elif prof_yoy and prof_yoy > 10: hl.append('净利稳增%.1f%%'%prof_yoy)
    if rev_yoy and rev_yoy > 15: hl.append('营收高增%.1f%%'%rev_yoy)
    if roe24 and roe24 > 15: hl.append('ROE优秀%.1f%%'%roe24)
    elif roe24 and roe24 > 10: hl.append('ROE良好%.1f%%'%roe24)
    if gm24 and gm24 > 40: hl.append('高毛利%.1f%%'%gm24)
    if cfo_np and cfo_np > 1.2: hl.append('现金流优%.2f'%cfo_np)
    if div: hl.append('分红%.2f/股'%div['per_share'])
    if lev and lev < 40: hl.append('低负债%.1f%%'%lev)
    if prof_yoy and prof_yoy < -10: rk.append('净利大降%.1f%%'%prof_yoy)
    elif prof_yoy and prof_yoy < 0: rk.append('净利下滑%.1f%%'%prof_yoy)
    if rev_yoy and rev_yoy < -10: rk.append('营收大降%.1f%%'%rev_yoy)
    elif rev_yoy and rev_yoy < 0: rk.append('营收下滑%.1f%%'%rev_yoy)
    if roe24 and roe24 < 5: rk.append('ROE低%.1f%%'%roe24)
    if gm24 and gm24 < 15: rk.append('薄毛利%.1f%%'%gm24)
    if cfo_np and cfo_np < 0.5: rk.append('现金流差%.2f'%cfo_np)
    if lev and lev > 70: rk.append('高负债%.1f%%'%lev)
    if cr and cr < 1: rk.append('流动性紧%.2f'%cr)
    if eps24 and eps24 < 0: rk.append('EPS为负')
    if prof24 and prof24 < 0: rk.append('亏损')
    results.append({
        'code': code, 'name': name,
        'rev': rev24, 'prof': prof24,
        'rev_yoy': rev_yoy, 'prof_yoy': prof_yoy,
        'roe': roe24, 'gm': gm24, 'eps': eps24,
        'cfo_np': cfo_np, 'cfo_or': cfo_or,
        'lev': lev, 'cr': cr, 'div': div,
        'hl': hl, 'rk': rk,
        'ok': 'profit' in d24
    })
    print('[%2d/30] %s(%s) OK'%(i+1, name, code))

bs.logout()

with open('/Users/duguke/.openclaw/workspace/analysis/financial_raw.json', 'w') as f:
    json.dump(results, f, ensure_ascii=False)

print('JSON saved!')