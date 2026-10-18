import os,sys,json,statistics as st
from collections import defaultdict
from collections import defaultdict
import pandas as pd
CACHE='data/research_price_cache'; FUND='data/research_fund_history.json'; BREADTH='data/market_breadth_history.json'; EVAL='analysis/research/dual_signal_eval_days.json'
HOLD=5
def load_prices():
    out={}
    for fn in os.listdir(CACHE):
        if not fn.endswith('.pkl') or fn.startswith('chip'): continue
        sym=fn[:-4]; df=pd.read_pickle(os.path.join(CACHE,fn))
        if 'date' not in df.columns or 'close' not in df.columns: continue
        df=df.copy(); df['_d']=pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        df=df[df['_d']!='1970-01-01'].drop_duplicates('_d').sort_values('_d')
        out[sym]=df.set_index('_d')['close'].astype(float)
    return out
def _norm01(v,lo,hi):
    if v is None or hi<=lo: return None
    return max(0.,min(1.,(v-lo)/(hi-lo)))
def fund_score(r):
    s_roe=_norm01(r.get('roe'),0,.25); s_yoy=_norm01(r.get('yoy_pni'),-100,100); s_npm=_norm01(r.get('np_margin'),0,30)
    parts=[p for p in (s_roe,s_yoy,s_npm) if p is not None]
    return sum(parts)/len(parts)*100 if parts else None
fund_hist=json.load(open(FUND)); fund_hist={s:sorted(v,key=lambda x:x['pub']) for s,v in fund_hist.items()}
breadth=json.load(open(BREADTH))
prices=load_prices()
_IDX={sym:{str(d)[:10]:k for k,d in enumerate(ser.index)} for sym,ser in prices.items()}
bench=defaultdict(list)
for sym,ser in prices.items():
    a=ser.values; ds=[str(x)[:10] for x in ser.index]
    for i in range(len(a)-HOLD):
        if a[i]>0: bench[ds[i]].append((a[i+HOLD]/a[i]-1)*100)
bench5={d:sum(v)/len(v) for d,v in bench.items() if v}
rows=json.load(open(EVAL)); sigs=[r for r in rows if any('买入' in str(a) for a in (r.get('actions') or []))]
recs=[]
for s in sigs:
    sym=str(s.get('symbol') or ''); d=str(s.get('date') or '')[:10]
    ser=prices.get(sym)
    if ser is None or sym not in _IDX or d not in _IDX[sym]: continue
    i=_IDX[sym][d]
    if i+5>=len(ser.values) or d not in bench5: continue
    fr=None
    for r in fund_hist.get(sym,[]):
        if r['pub']<=d: fr=r
        else: break
    fund=fund_score(fr) if fr else None
    b=breadth.get(d)
    if not b: continue
    up_ratio=b['up']/(b['up']+b['down']) if (b['up']+b['down'])>0 else None
    active_norm=max(0.,min(1.,b['active_median_abs_chg']/5.0)) if b.get('active_median_abs_chg') else 0.5
    sent=up_ratio*70+active_norm*30 if up_ratio is not None else None
    score=40.5+(0.30*fund if fund is not None else 0)+(0.25*sent if sent is not None else 0)
    ex5=(ser.values[i+5]/ser.values[i]-1)*100-bench5[d]
    recs.append({'sym':sym,'date':d,'score':score,'fund':fund,'sent':sent,'ex5':ex5})

# 分解：低分(<55) 里，fund 低 vs sent 低 谁贡献大
low=[r for r in recs if r['score']<55]
print('低分样本', len(low), '条')
print('  其中 fund 缺失或<20 的:', sum(1 for r in low if r['fund'] is None or r['fund']<20))
print('  其中 sent<40(弱市) 的:', sum(1 for r in low if r['sent'] is not None and r['sent']<40))
print('  fund 中位: %.1f  sent 中位: %.1f' % (
    st.median([r['fund'] for r in low if r['fund'] is not None]),
    st.median([r['sent'] for r in low if r['sent'] is not None])))

# 全样本对照
print('\n全样本 fund 中位: %.1f  sent 中位: %.1f' % (
    st.median([r['fund'] for r in recs if r['fund'] is not None]),
    st.median([r['sent'] for r in recs if r['sent'] is not None])))

# 关键：低分组的负超额，是"弱市日"本身的负超额，还是"基本弱票"的负超额？
# 对比：全市场弱市日(sent<45) 所有信号的 ex5，vs 弱市日里低分(fund<20)的
weak_market=[r for r in recs if r['sent'] is not None and r['sent']<45]
print('\n弱市日(sent<45)全部信号: n=%d mean ex5=%+.2f%%' % (len(weak_market), st.mean([r['ex5'] for r in weak_market])))
weak_market_strongfund=[r for r in weak_market if r['fund'] is not None and r['fund']>=40]
print('  弱市日+强票(fund>=40): n=%d mean ex5=%+.2f%%' % (len(weak_market_strongfund), st.mean([r['ex5'] for r in weak_market_strongfund]) if weak_market_strongfund else 0))
weak_market_weakfund=[r for r in weak_market if r['fund'] is None or r['fund']<20]
print('  弱市日+弱票(fund<20/缺): n=%d mean ex5=%+.2f%%' % (len(weak_market_weakfund), st.mean([r['ex5'] for r in weak_market_weakfund]) if weak_market_weakfund else 0))
