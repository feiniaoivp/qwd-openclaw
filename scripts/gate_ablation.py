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
    ex5=(ser.values[i+5]/ser.values[i]-1)*100-bench5[d]
    recs.append({'sym':sym,'date':d,'fund':fund,'sent':sent,'ex5':ex5})

# 三种门控分数，各做"拦弱票"的区分度
def scan(label, score_fn, T_best=55):
    rows2=[{'score':score_fn(r),'ex5':r['ex5'],'date':r['date']} for r in recs]
    # 找最优 T：扫描 40~70
    best=None
    print('\n[%s]' % label)
    for T in (50,55,60):
        blk=[r for r in rows2 if r['score'] is not None and r['score']<T]
        pas=[r for r in rows2 if r['score'] is not None and r['score']>=T]
        if len(blk)<10 or len(pas)<10: 
            print('  T=%d: 样本不足' % T); continue
        bm=st.mean([r['ex5'] for r in blk]); pm=st.mean([r['ex5'] for r in pas])
        # bootstrap by day
        bd=defaultdict(lambda:([],[]))
        for r in rows2:
            if r['score'] is None: continue
            (bd[r['date']][0] if r['score']<T else bd[r['date']][1]).append(r['ex5'])
        days=list(bd); rng=random.Random(42); diffs=[]
        for _ in range(3000):
            pick=[bd[rng.choice(days)] for _ in days]
            b=[x for p in pick for x in p[0]]; q=[x for p in pick for x in p[1]]
            if b and q: diffs.append(sum(b)/len(b)-sum(q)/len(q))
        diffs.sort()
        lo,hi=diffs[len(diffs)//40],diffs[-len(diffs)//40]
        sig='显著' if (lo>0 or hi<0) else '不显著'
        print('  T=%d: 拦截%d 均%+.2f%% 放行%d 均%+.2f%% 差%+.2f%% [%+.2f,%+.2f] %s' % (
            T,len(blk),bm,len(pas),pm,bm-pm,lo,hi,sig))

def score_full(r):   # 40.5+0.30fund+0.25sent
    f=r['fund']; s=r['sent']
    return 40.5+(0.30*f if f is not None else 0)+(0.25*s if s is not None else 0)
def score_fund_only(r):  # 只看基本面，映射到同量纲：fund 归一 0~100 直接用
    return r['fund']  # 但量纲要调整，用 fund(0~100) 直接当 score，阈值另设
def score_sent_only(r):
    return r['sent']

print('=== 三维 vs 单维 区分度对比 ===')
scan('完整门控 40.5+0.30f+0.25s', score_full)
# fund only: 阈值设 20/15/10 (fund<20 = 弱票)
print('\n[仅基本面 fund，阈值=弱票线]')
for T in (15,20,25):
    blk=[r for r in recs if r['fund'] is not None and r['fund']<T]
    pas=[r for r in recs if r['fund'] is not None and r['fund']>=T]
    if len(blk)<10 or len(pas)<10:
        print('  fund<T=%d: 拦截%d 样本不足' % (T,len(blk))); continue
    bm=st.mean([r['ex5'] for r in blk]); pm=st.mean([r['ex5'] for r in pas])
    print('  fund<T=%d: 拦截%d 均%+.2f%% 放行%d 均%+.2f%% 差%+.2f%%' % (T,len(blk),bm,len(pas),pm,bm-pm))
# sent only
print('\n[仅情绪 sent]')
for T in (35,40,45):
    blk=[r for r in recs if r['sent'] is not None and r['sent']<T]
    pas=[r for r in recs if r['sent'] is not None and r['sent']>=T]
    if len(blk)<10 or len(pas)<10:
        print('  sent<T=%d: 拦截%d 样本不足' % (T,len(blk))); continue
    bm=st.mean([r['ex5'] for r in blk]); pm=st.mean([r['ex5'] for r in pas])
    print('  sent<T=%d: 拦截%d 均%+.2f%% 放行%d 均%+.2f%% 差%+.2f%%' % (T,len(blk),bm,len(pas),pm,bm-pm))
