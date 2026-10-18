#!/usr/bin/env python3
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
import random
import statistics as st
from collections import defaultdict
import pandas as pd, numpy as np
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
    recs.append({'sym':sym,'name':s.get('name'),'date':d,'score':score,'ex5':ex5})
print('总样本', len(recs))

# 个股聚类：每只票的所有信号取平均 ex5，按"该票平均 score"分组
from collections import defaultdict
per_sym=defaultdict(list)
for r in recs:
    per_sym[r['sym']].append(r)
sym_rows=[]
for sym,rs in per_sym.items():
    avg_score=st.mean([r['score'] for r in rs])
    avg_ex5=st.mean([r['ex5'] for r in rs])
    n=len(rs)
    sym_rows.append({'sym':sym,'name':rs[0]['name'],'avg_score':avg_score,'avg_ex5':avg_ex5,'n':n})
print('\n=== 个股聚类后：低分(<55)票 vs 其他票 ===')
blk=[r for r in sym_rows if r['avg_score']<55]
pas=[r for r in sym_rows if r['avg_score']>=55]
print('  低分票 %d 只: %s' % (len(blk), [(r['name'],round(r['avg_ex5'],2),r['n']) for r in sorted(blk,key=lambda z:z['avg_score'])]))
print('  低分票 avg_ex5 均值 %.2f%% | 其他票 %d 只 均值 %.2f%%' % (
    st.mean([r['avg_ex5'] for r in blk]) if blk else 0, len(pas), st.mean([r['avg_ex5'] for r in pas]) if pas else 0))

# bootstrap：按"个股"重采样（而非交易日），看差异是否显著
rng=random.Random(42)
blk_ex=[r['avg_ex5'] for r in blk]; pas_ex=[r['avg_ex5'] for r in pas]
real_diff=st.mean(blk_ex)-st.mean(pas_ex)
diffs=[]
for _ in range(5000):
    b=[rng.choice(blk_ex) for _ in blk_ex]
    p=[rng.choice(pas_ex) for _ in pas_ex]
    if b and p: diffs.append(st.mean(b)-st.mean(p))
diffs.sort()
lo,hi=diffs[int(5000*0.025)],diffs[int(5000*0.975)]
print('  个股聚类 bootstrap: 差=%.2f%% CI95[%.2f,%.2f]' % (real_diff,lo,hi))
print('  → %s' % ('显著' if (lo>0 or hi<0) else '不显著'))
