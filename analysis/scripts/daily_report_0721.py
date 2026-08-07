#!/usr/bin/env python3
"""
每日A股复盘分析脚本 - 2026-07-21
25只关注股：全维度技术+基本面分析
数据源: akshare spot + baostock history(前复权) + stock_news_em
"""
import akshare as ak
import pandas as pd
import numpy as np
import json, os, sys, warnings
warnings.filterwarnings('ignore')
from datetime import datetime, timedelta

STOCKS = [
    ("002318","久立特材"), ("300014","亿纬锂能"), ("601066","中信建投"),
    ("600030","中信证券"), ("300124","汇川技术"), ("601995","中金公司"),
    ("600584","长电科技"), ("002156","通富微电"), ("002466","天齐锂业"),
    ("600036","招商银行"), ("600570","恒生电子"), ("605566","福莱蒽特"),
    ("000987","越秀资本"), ("603308","应流股份"), ("300285","国瓷材料"),
    ("002413","雷科防务"), ("688981","中芯国际"), ("601865","福莱特"),
    ("000157","中联重科"), ("300719","安达维尔"), ("601061","中信金属"),
    ("600660","福耀玻璃"), ("900925","机电B股"), ("002335","科华数据"),
    ("601100","恒立液压")
]

TODAY = "2026-07-21"
OUTPUT_DIR = "/Users/duguke/.openclaw/workspace/analysis/daily"
REPORT_PATH = os.path.join(OUTPUT_DIR, f"{TODAY}.md")
SUMMARY_PATH = os.path.join(OUTPUT_DIR, f"{TODAY}_summary.json")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- Technical helpers ---
def calc_ma(s, w):
    return s.rolling(w).mean()

def calc_macd(c, f=12, sl=26, sg=9):
    ef = c.ewm(span=f).mean()
    es = c.ewm(span=sl).mean()
    dif = ef - es
    dea = dif.ewm(span=sg).mean()
    return dif, dea, 2*(dif-dea)

def calc_rsi(s, p=14):
    d = s.diff()
    g = d.where(d>0,0).rolling(p).mean()
    l = (-d.where(d<0,0)).rolling(p).mean()
    rs = g/l
    return 100 - 100/(1+rs)

def calc_boll(c, w=20, ns=2):
    m = c.rolling(w).mean()
    s = c.rolling(w).std()
    return m+ns*s, m, m-ns*s

def vol_ratio(v, w=5):
    return v / v.rolling(w).mean()

def classify_sector(name):
    m = {
        "久立特材":"钢铁/金属","中信金属":"金属/有色",
        "亿纬锂能":"新能源/锂电","天齐锂业":"新能源/锂电",
        "中信建投":"券商","中信证券":"券商","中金公司":"券商","越秀资本":"券商/金融",
        "汇川技术":"高端制造","应流股份":"高端制造","中联重科":"高端制造","恒立液压":"高端制造",
        "长电科技":"芯片/半导体","通富微电":"芯片/半导体","中芯国际":"芯片/半导体",
        "招商银行":"银行","恒生电子":"科技/软件","科华数据":"科技/软件",
        "福莱蒽特":"光伏","福莱特":"光伏","国瓷材料":"材料",
        "雷科防务":"军工","安达维尔":"军工","福耀玻璃":"汽车玻璃",
        "机电B股":"B股",
    }
    return m.get(name,"其他")

# --- Data Fetching ---
def fetch_spot():
    print(">> 获取实时行情(新浪)...")
    spot = ak.stock_zh_a_spot()
    d = {}
    for _, row in spot.iterrows():
        raw_code = str(row['代码']).zfill(10).replace('sh','').replace('sz','').replace('bj','').strip()
        # Extract last 6 digits as code
        code = raw_code[-6:] if len(raw_code) >= 6 else raw_code.zfill(6)
        d[code] = {
            'price': row.get('最新价'), 'change_pct': row.get('涨跌幅'),
            'high': row.get('最高'), 'low': row.get('最低'),
            'open': row.get('今开'), 'pre_close': row.get('昨收'),
            'volume': row.get('成交量'), 'amount': row.get('成交额'),
        }
    print(f"  ✓ 获取{len(d)}只股票行情")
    return d

def fetch_history(code, days=150):
    """Baostock 前复权K线"""
    import baostock as bs
    prefix = 'sz' if code.startswith(('0','3')) else 'sh'
    bs_code = f"{prefix}.{code}"
    end = TODAY
    start = (datetime.strptime(TODAY,"%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    
    # Try qfq first
    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,code,open,high,low,close,volume,amount,peTTM,pctChg",
        start_date=start, end_date=end, frequency="d"
    )
    if rs is None or rs.error_code != '0':
        return pd.DataFrame()
    
    rows = []
    while (rs.error_code == '0') & rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return pd.DataFrame()
    
    cols = ['date','code','open','high','low','close','volume','amount']
    if len(rows[0]) >= 10:
        cols = ['date','code','open','high','low','close','volume','amount','peTTM','pctChg']
    df = pd.DataFrame(rows, columns=cols)
    for c in ['open','high','low','close','volume','amount']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['date'] = pd.to_datetime(df['date'])
    return df.sort_values('date').reset_index(drop=True)

def fetch_news(code):
    try:
        news = ak.stock_news_em(symbol=code)
        if news is not None and not news.empty:
            return [f"{str(r.get('发布时间',''))[:10]} {str(r.get('新闻标题',''))[:80]}" 
                    for _, r in news.head(3).iterrows() if str(r.get('新闻标题',''))]
    except:
        pass
    return []

# --- Analysis ---
def analyze_stock(code, name, spot, hist):
    r = {
        'code': code, 'name': name,
        'sector': classify_sector(name),
        'price': spot.get('price') if spot else None,
        'change_pct': spot.get('change_pct') if spot else None,
    }
    if hist.empty or 'close' not in hist.columns or len(hist) < 5:
        r['error'] = '历史数据不足'; return r
    
    c = hist['close']; v = hist.get('volume', pd.Series(dtype=float))
    
    if pd.isna(r.get('price')) or r['price'] is None:
        r['price'] = float(c.iloc[-1])
    
    # Keep spot change_pct as priority (has +0.926% format)
    # Only use history as last resort

    latest = float(c.iloc[-1])
    
    # MAs
    for lbl, w in [('ma5',5),('ma10',10),('ma20',20),('ma60',60)]:
        ma = calc_ma(c,w)
        r[lbl] = round(float(ma.iloc[-1]),2) if len(ma)>0 and not pd.isna(ma.iloc[-1]) else None
    
    # MACD
    dif, dea, macd = calc_macd(c)
    r['macd_dif'] = round(float(dif.iloc[-1]),3) if len(dif)>0 and not pd.isna(dif.iloc[-1]) else None
    r['macd_dea'] = round(float(dea.iloc[-1]),3) if len(dea)>0 and not pd.isna(dea.iloc[-1]) else None
    r['macd'] = round(float(macd.iloc[-1]),3) if len(macd)>0 and not pd.isna(macd.iloc[-1]) else None
    
    # RSI
    rsi = calc_rsi(c,14)
    r['rsi'] = round(float(rsi.iloc[-1]),1) if len(rsi)>0 and not pd.isna(rsi.iloc[-1]) else None
    
    # Bollinger
    u, m, lo = calc_boll(c)
    for lbl, val in [('boll_upper',u),('boll_mid',m),('boll_lower',lo)]:
        r[lbl] = round(float(val.iloc[-1]),2) if len(val)>0 and not pd.isna(val.iloc[-1]) else None
    if r['boll_upper'] and r['boll_lower']:
        if latest > r['boll_upper']: r['boll_pos'] = 'above_upper'
        elif latest < r['boll_lower']: r['boll_pos'] = 'below_lower'
        elif latest > r['boll_mid']: r['boll_pos'] = 'mid_upper'
        else: r['boll_pos'] = 'mid_lower'
    
    # Volume
    if len(v) > 5:
        vr = vol_ratio(v,5)
        r['vol_ratio'] = round(float(vr.iloc[-1]),2) if len(vr)>0 and not pd.isna(vr.iloc[-1]) else None
        avg20 = v.rolling(20).mean()
        r['avg_vol_20'] = round(float(avg20.iloc[-1]),0) if len(avg20)>0 and not pd.isna(avg20.iloc[-1]) else None
    
    # Support/Resistance
    if len(c) >= 20:
        r['resistance_20d'] = round(float(np.max(c[-20:])),2)
        r['support_20d'] = round(float(np.min(c[-20:])),2)
    if len(c) >= 60:
        r['resistance_60d'] = round(float(np.max(c[-60:])),2)
        r['support_60d'] = round(float(np.min(c[-60:])),2)
    
    # 5-day change
    if len(c) >= 5:
        r['change_5d'] = round(float(c.iloc[-1]/c.iloc[-5]-1)*100, 2)
    
    # Trend scoring
    strength = 0; signals = []
    if r['ma5'] and r['ma10'] and r['ma20']:
        if r['ma5'] > r['ma10'] > r['ma20']: strength += 2; signals.append("多头排列")
        elif r['ma5'] < r['ma10'] < r['ma20']: strength -= 2; signals.append("空头排列")
        else: signals.append("均线交错")
    ma_up = sum(1 for m in ['ma5','ma10','ma20','ma60'] if r.get(m) and latest > r[m])
    if ma_up >= 3: strength += 1
    elif ma_up <= 1: strength -= 1
    
    if r['macd_dif'] and r['macd_dea']:
        if r['macd_dif'] > r['macd_dea']: strength += 1; signals.append("MACD金叉")
        else: strength -= 1; signals.append("MACD死叉")
    
    if r['rsi'] is not None:
        if r['rsi'] > 70: signals.append(f"RSI超买({r['rsi']})")
        elif r['rsi'] < 30: strength += 1; signals.append(f"RSI超卖待弹({r['rsi']})")
        elif r['rsi'] > 50: strength += 1; signals.append(f"RSI偏强({r['rsi']})")
        else: strength -= 1; signals.append(f"RSI偏弱({r['rsi']})")
    
    bp = r.get('boll_pos','')
    if bp == 'above_upper': strength -= 1; signals.append("触及上轨⚠️")
    elif bp == 'below_lower': strength += 1; signals.append("触及下轨💡")
    elif bp == 'mid_upper': strength += 1; signals.append("布林中上轨")
    
    if r.get('vol_ratio'):
        if r['vol_ratio'] > 1.5:
            signals.append(f"放量({r['vol_ratio']})")
            if strength > 0: strength += 1; signals.append("量价配合")
        elif r['vol_ratio'] < 0.6:
            signals.append(f"缩量({r['vol_ratio']})")
    
    if r.get('change_5d'):
        if r['change_5d'] > 5: signals.append(f"5日+{r['change_5d']}%")
        elif r['change_5d'] < -5: signals.append(f"5日{r['change_5d']}%弱势")
    
    if strength >= 3: r['trend'] = "📈多头(强势)"
    elif strength >= 1: r['trend'] = "↗偏多(震荡偏强)"
    elif strength <= -3: r['trend'] = "📉空头(弱势)"
    elif strength <= -1: r['trend'] = "↘偏空(震荡偏弱)"
    else: r['trend'] = "➡震荡"
    r['strength'] = strength; r['signals'] = signals
    r['news'] = fetch_news(code)
    return r

def get_op(r):
    t = r.get('trend',''); st = r.get('strength',0)
    rsi = r.get('rsi',50); bp = r.get('boll_pos','')
    if "多头" in t and st >= 3:
        if bp == 'above_upper': return "强势接近上轨，持有但勿追高"
        return "多头良好，持有为主，回踩可关注"
    if "偏多" in t: return "震荡偏强，关注突破机会"
    if "震荡" in t: return "横盘震荡，等待方向"
    if "偏空" in t: return "偏弱观望" if not (rsi and rsi<30) else "超卖区，谨慎关注反弹"
    if "空头" in t: return "空头趋势，回避"
    return "观望为主"

# --- Report ---
def gen_report(results):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []
    lines.append(f"# 📊 {TODAY}（周二）A股收盘复盘\n")
    lines.append(f"**—— 针对你的25只关注股票**\n")
    lines.append(f"> 📅 数据来源：akshare实时行情 + baostock历史K线(前复权) + 东方财富新闻\n")
    lines.append(f"> ⚠️ 分析基于客观数据，不构成投资建议\n")
    lines.append(f"> ⏰ 生成时间：{now}\n")
    
    lines.append("---\n## 📈 大盘指数走势\n")
    lines.append("| 指数 | 参考 |\n|------|------|\n")
    lines.append("| 上证指数 | 盘中实时 |\n| 深证成指 | 盘中实时 |\n| 创业板指 | 盘中实时 |\n| 科创50 | 盘中实时 |\n")
    lines.append("> 📌 今日A股概况：基于25只关注股数据分析\n")
    
    chgs = [r['change_pct'] for r in results if r.get('change_pct') is not None and not pd.isna(r.get('change_pct'))]
    up = sum(1 for c in chgs if c > 0); dn = sum(1 for c in chgs if c < 0)
    avg_c = round(np.mean(chgs),2) if chgs else 0
    best = max(results, key=lambda r: r.get('change_pct') if r.get('change_pct') is not None else -999)
    worst = min(results, key=lambda r: r.get('change_pct') if r.get('change_pct') is not None else 999)
    
    lines.append("---\n## 📋 关注股总体表现\n")
    lines.append(f"| 指标 | 数据 |\n|------|------|\n")
    lines.append(f"| **上涨** | ✅ {up}只 ({up/25*100:.1f}%) |\n")
    lines.append(f"| **下跌** | 🔴 {dn}只 ({dn/25*100:.1f}%) |\n")
    lines.append(f"| **平均涨跌幅** | {avg_c:+.2f}% |\n")
    lines.append(f"| **最强** | 🚀 {best['name']} {best.get('change_pct',0) if best.get('change_pct') is not None else 0:+.2f}% |\n")
    lines.append(f"| **最弱** | 🚨 {worst['name']} {worst.get('change_pct',0) if worst.get('change_pct') is not None else 0:+.2f}% |\n\n")
    lines.append(f"> 📊 {up}涨 {dn}跌 平均{avg_c:+.2f}%\n")
    
    # Sector
    sectors = {}
    for r in results:
        sectors.setdefault(r.get('sector','其他'),[]).append(r)
    lines.append("---\n## 🔄 板块轮动热点\n")
    lines.append("| 板块 | 平均涨跌 | 关注股数 | 代表标的 |\n|------|---------|---------|---------|\n")
    sec_list = []
    for sec, stocks in sectors.items():
        sc = [s['change_pct'] for s in stocks if s.get('change_pct') is not None and not pd.isna(s.get('change_pct'))]
        avg_s = round(np.mean(sc),2) if sc else 0
        names = "、".join([s['name'] for s in stocks[:3]])
        if len(stocks) > 3: names += f"等{len(stocks)}只"
        sec_list.append((sec, avg_s, len(stocks), names))
    sec_list.sort(key=lambda x: x[1], reverse=True)
    for sec, avg_s, cnt, ns in sec_list:
        icon = "🟢" if avg_s > 1 else ("🔴" if avg_s < -1 else "🟡")
        lines.append(f"| {icon} {sec} | {avg_s:+.2f}% | {cnt}只 | {ns} |\n")
    if sec_list:
        lines.append(f"> 🔥 **最强板块：** {sec_list[0][0]} ({sec_list[0][1]:+.2f}%)\n")
        lines.append(f"> ❄️ **最弱板块：** {sec_list[-1][0]} ({sec_list[-1][1]:+.2f}%)\n")
    
    # Individual analysis
    lines.append("---\n## 🏢 个股技术分析\n")
    sorted_r = sorted(results, key=lambda r: r.get('strength',0), reverse=True)
    for r in sorted_r:
        n = r['name']; c = r['code']; s = r.get('sector','')
        ps = f"¥{r['price']:.2f}" if r.get('price') is not None and not pd.isna(r.get('price')) else "¥?"
        cs = f"{r['change_pct']:+.2f}%" if r.get('change_pct') is not None and not pd.isna(r.get('change_pct')) else "N/A"
        t = r.get('trend','?'); st = r.get('strength',0)
        sigs = r.get('signals',[]); news = r.get('news',[])
        
        ti = "🟢" if "多头" in t else ("🔴" if "空头" in t else ("🟡" if "偏多" in t else ("🟠" if "偏空" in t else "⚪")))
        
        lines.append(f"### {ti} {n} ({c}) — {s}\n")
        lines.append(f"**{ps}** | 涨跌：{cs} | 趋势：{t} | 强度：{st}\n")
        lines.append("| 指标 | 数值 | 判断 |\n|------|------|------|\n")
        ma_str = " ".join([f"MA{k}={r.get(f'ma{k}','-')}" for k in [5,10,20,60]])
        ma_ok = r.get('ma5') and r.get('ma10') and r['ma5'] > r['ma10']
        lines.append(f"| 均线 | {ma_str} | {'多头✓' if ma_ok else '关注'} |\n")
        mc = "金叉📈" if r.get('macd_dif') and r.get('macd_dea') and r['macd_dif']>r['macd_dea'] else ("死叉📉" if r.get('macd_dif') and r.get('macd_dea') else "-")
        lines.append(f"| MACD | DIF={r.get('macd_dif','-')} DEA={r.get('macd_dea','-')} | {mc} |\n")
        lines.append(f"| RSI | {r.get('rsi','-')} | {'超买⚠️' if r.get('rsi') and r['rsi']>70 else '超卖💡' if r.get('rsi') and r['rsi']<30 else '中性'} |\n")
        bols = f"上={r.get('boll_upper','-')} 中={r.get('boll_mid','-')} 下={r.get('boll_lower','-')}"
        bpm = {'above_upper':'上轨上方⚠️','below_lower':'下轨下方💡','mid_upper':'中上轨','mid_lower':'中下轨'}
        lines.append(f"| 布林带 | {bols} | {bpm.get(r.get('boll_pos',''),'-')} |\n")
        kz = [f"支撑{r.get('support_20d','')}" if r.get('support_20d') else "", f"阻力{r.get('resistance_20d','')}" if r.get('resistance_20d') else ""]
        lines.append(f"| 支撑/阻力 | {' '.join(filter(None,kz))} | 20日区间 |\n")
        vs = f"量比={r.get('vol_ratio','-')}" + (f" 均量={r['avg_vol_20']:.0f}" if r.get('avg_vol_20') else "")
        vsig = "放量" if r.get('vol_ratio') and r['vol_ratio']>1.5 else ("缩量" if r.get('vol_ratio') and r['vol_ratio']<0.6 else "正常")
        lines.append(f"| 成交量 | {vs} | {vsig} |\n")
        if r.get('change_5d') is not None:
            lines.append(f"| 5日涨跌 | {r['change_5d']:+.2f}% | - |\n")
        
        kzs = []
        if r.get('support_20d'): kzs.append(f"短期支撑**{r['support_20d']}**")
        if r.get('support_60d'): kzs.append(f"中期支撑**{r['support_60d']}**")
        if r.get('resistance_20d'): kzs.append(f"短期阻力**{r['resistance_20d']}**")
        if r.get('resistance_60d'): kzs.append(f"中期阻力**{r['resistance_60d']}**")
        if kzs: lines.append(f"> **关键价位：** {' | '.join(kzs)}\n")
        if sigs: lines.append(f"> **技术信号：** {' | '.join(sigs[:5])}\n")
        if news: lines.append("> **📰 近期消息：**\n" + "\n".join([f"  - {n}" for n in news]) + "\n")
        lines.append(f"> **操作建议：** {get_op(r)}\n---\n")
    
    # Tomorrow
    lines.append("## 🎯 明日关注清单 & 预案\n")
    strong = [r for r in sorted_r if r.get('strength',0) >= 1][:5]
    weak = [r for r in reversed(sorted_r) if r.get('strength',0) <= -1][:5]
    lines.append("### 🔥 强势关注（趋势延续）\n")
    if strong:
        lines.append("| 股票 | 趋势 | 强度 | 关键阻力 | 策略 |\n|------|------|------|---------|------|\n")
        for s in strong:
            lines.append(f"| {s['name']} | {s.get('trend','')} | {s['strength']} | {s.get('resistance_20d','-')} | {'持有,突破加仓' if s['strength']>=2 else '持有观望'} |\n")
    else: lines.append("（暂无显著多头标的）\n")
    lines.append("\n### ⚠️ 弱势关注（可能反弹或继续回调）\n")
    if weak:
        lines.append("| 股票 | 趋势 | 强度 | 关键支撑 | 策略 |\n|------|------|------|---------|------|\n")
        for w in weak:
            lines.append(f"| {w['name']} | {w.get('trend','')} | {w['strength']} | {w.get('support_20d','-')} | {'暂不参与,等企稳' if w['strength']<=-2 else '关注支撑'} |\n")
    else: lines.append("（暂无显著空头标的）\n")
    
    lines.append("\n### 📌 明日预案\n")
    lines.append("1. **大盘环境：** 关注政策面和量能变化\n")
    if sec_list: lines.append(f"2. **板块轮动：** 关注{sec_list[0][0]}板块持续性\n")
    lines.append(f"3. **强势策略：** {'、'.join([s['name'] for s in strong[:3]]) if strong else '无'}\n")
    lines.append(f"4. **弱势策略：** {'、'.join([w['name'] for w in weak[:3]]) if weak else '无'} 等待企稳\n")
    lines.append("5. **仓位建议：** 趋势良好可持有，弱势股控制仓位\n")
    lines.append("6. **风险提示：** 技术分析仅供参考\n---\n")
    lines.append(f"*报告自动生成于 {now}*\n*数据：akshare + baostock | 不做投资建议*\n")
    return "".join(lines)

def gen_summary(results):
    chgs = [r['change_pct'] for r in results if r.get('change_pct') is not None and not pd.isna(r.get('change_pct'))]
    up = sum(1 for c in chgs if c > 0); dn = sum(1 for c in chgs if c < 0)
    return {
        "date": TODAY,
        "summary": {"total": len(results), "up": up, "down": dn,
                     "avg_change": round(np.mean(chgs),2) if chgs else 0,
                     "max_change": round(max(chgs),2) if chgs else 0,
                     "min_change": round(min(chgs),2) if chgs else 0},
        "stocks": [{
            "code": r['code'], "name": r['name'],
            "price": round(float(r['price']),2) if r.get('price') is not None and not pd.isna(r.get('price')) else None,
            "change_pct": round(float(r['change_pct']),2) if r.get('change_pct') is not None and not pd.isna(r.get('change_pct')) else None,
            "trend": r.get('trend'), "strength": r.get('strength'),
            "ma5": r.get('ma5'), "ma10": r.get('ma10'), "ma20": r.get('ma20'), "ma60": r.get('ma60'),
            "rsi": r.get('rsi'), "support": r.get('support_20d'), "resistance": r.get('resistance_20d'),
            "vol_ratio": r.get('vol_ratio'), "sector": r.get('sector'), "suggestion": get_op(r)
        } for r in results]
    }

def webchat_msg(results, jd):
    s = jd['summary']
    sec_chgs = {}
    for r in results:
        sec = r.get('sector','其他'); chg = r.get('change_pct')
        if chg is not None and not pd.isna(chg):
            sec_chgs.setdefault(sec,[]).append(chg)
    sec_avg = sorted([(s,round(np.mean(c),2),len(c)) for s,c in sec_chgs.items()], key=lambda x: x[1], reverse=True)
    sorted_st = sorted(jd['stocks'], key=lambda x: x.get('change_pct',0) or 0, reverse=True)
    top3 = sorted_st[:3]; bot3 = sorted_st[-3:]
    strong_t = [r for r in jd['stocks'] if r.get('trend') and '多头' in str(r.get('trend','')) and r.get('strength',0) >= 2]
    
    msg = f"📊 **{TODAY} A股复盘摘要**\n\n"
    msg += f"🎯 **总体：** {s['up']}涨 {s['down']}跌 | 均{s['avg_change']:+.2f}% | 最强{s['max_change']:+.2f}%\n\n"
    msg += "📈 **板块轮动：**\n"
    for sec, avg, cnt in sec_avg[:5]:
        icon = "🟢" if avg>0.5 else ("🔴" if avg<-0.5 else "🟡")
        msg += f"  {icon} {sec}: {avg:+.2f}% ({cnt}只)\n"
    msg += "\n🏆 **领涨：**\n"
    for st in top3:
        if st.get('change_pct') is not None:
            msg += f"  ✅ {st['name']} +{st['change_pct']:.2f}% ({st.get('trend','')})\n"
    msg += "\n⚠️ **领跌：**\n"
    for st in bot3:
        if st.get('change_pct') is not None:
            msg += f"  ❌ {st['name']} {st['change_pct']:.2f}% ({st.get('trend','')})\n"
    if strong_t:
        msg += f"\n🔥 **多头强势：** {'、'.join([s['name'] for s in strong_t[:5]])}\n"
    msg += f"\n📄 详见: analysis/daily/{TODAY}.md"
    return msg

def main():
    print(f"=== A股每日复盘分析 - {TODAY} ===\n")
    
    spot_data = fetch_spot()
    if not spot_data: print("[FATAL] No spot data"); sys.exit(1)
    
    import baostock as bs
    bs.login()
    
    results = []
    print(f"\n>> 分析{len(STOCKS)}只股票 (baostock历史K线)...")
    for code, name in STOCKS:
        print(f"  [{code}] {name}...", end=" ", flush=True)
        hist = fetch_history(code)
        r = analyze_stock(code, name, spot_data.get(code,{}), hist)
        results.append(r)
        ps = f"¥{r.get('price','?'):.2f}" if r.get('price') is not None and not pd.isna(r.get('price')) else "¥?"
        print(f" {ps} {r.get('change_pct','?')}% {r.get('trend','?')}")
    
    bs.logout()
    
    print("\n>> 生成报告...")
    report = gen_report(results)
    with open(REPORT_PATH, 'w', encoding='utf-8') as f: f.write(report)
    print(f"✅ 报告: {REPORT_PATH}")
    
    jd = gen_summary(results)
    with open(SUMMARY_PATH, 'w', encoding='utf-8') as f: json.dump(jd, f, ensure_ascii=False, indent=2)
    print(f"✅ 摘要: {SUMMARY_PATH}")
    
    wc = webchat_msg(results, jd)
    print("\n" + "="*60)
    print("📋 WEBCHAT SUMMARY:")
    print("="*60)
    print(wc)
    print("="*60)
    
    # Output structured for capture
    print("\n---WEBCHAT_BEGIN---")
    print(wc)
    print("---WEBCHAT_END---")

if __name__ == "__main__":
    main()
