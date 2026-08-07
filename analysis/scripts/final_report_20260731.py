#!/usr/bin/env python3
"""
A股每日复盘 2026-07-31（周五）
- 实时行情: akshare stock_zh_a_spot (新浪)
- 历史K线: akshare stock_zh_a_hist 失败 → baostock 兜底 (至07-30)，追加今日实时bar重算指标
- 新闻: akshare stock_info_global_em
- 生成完整日报
"""
import akshare as ak
import baostock as bs
import pandas as pd
import numpy as np
import json, os, time, warnings
warnings.filterwarnings('ignore')

REPORT_DATE = "2026-07-31"
WEEKDAY = "周五"
ANALYSIS_DIR = "/Users/duguke/.openclaw/workspace/analysis"
OUT = f"{ANALYSIS_DIR}/daily/{REPORT_DATE}.md"

STOCKS = {
    "002318": "久立特材", "300014": "亿纬锂能", "601066": "中信建投",
    "600030": "中信证券", "300124": "汇川技术", "601995": "中金公司",
    "600584": "长电科技", "002156": "通富微电", "002466": "天齐锂业",
    "600036": "招商银行", "600570": "恒生电子", "605566": "福莱蒽特",
    "000987": "越秀资本", "603308": "应流股份", "300285": "国瓷材料",
    "002413": "雷科防务", "688981": "中芯国际", "601865": "福莱特",
    "000157": "中联重科", "300719": "安达维尔", "601061": "中信金属",
    "600660": "福耀玻璃", "900925": "机电B股", "002335": "科华数据",
    "601100": "恒立液压"
}
B_STOCKS = {"900925"}

# ---------- 行情 ----------
def fetch_spot():
    df = ak.stock_zh_a_spot()
    df.set_index("代码", inplace=True)
    res = {}
    for code, name in STOCKS.items():
        if code in B_STOCKS:
            res[code] = {"name": name, "status": "B股"}
            continue
        pref = (["sh"+code, code] if code[0] in "69" else ["sz"+code, code])
        for idx in pref:
            if idx in df.index:
                r = df.loc[idx]
                res[code] = {
                    "name": name, "status": "ok",
                    "price": float(r.get("最新价",0)), "change_pct": float(r.get("涨跌幅",0)),
                    "change_amt": float(r.get("涨跌额",0)), "volume": float(r.get("成交量",0)),
                    "amount": float(r.get("成交额",0)), "high": float(r.get("最高",0)),
                    "low": float(r.get("最低",0)), "open": float(r.get("今开",0)),
                    "pre_close": float(r.get("昨收",0)), "turnover": float(r.get("换手率",0)),
                    "pe": float(r.get("市盈率-动态",0))
                }
                break
        else:
            res[code] = {"name": name, "status": "未找到"}
    return res

# ---------- 历史K线 (baostock) ----------
_bs_logged = {"v": False}
def bs_login_once():
    if not _bs_logged["v"]:
        bs.login()
        _bs_logged["v"] = True

def fetch_hist_baostock(code):
    try:
        bs_login_once()
        cmap = "sh" if code[0] in "69" else "sz"
        rs = bs.query_history_k_data_plus(f"{cmap}.{code}",
            "date,open,high,low,close,volume,amount",
            start_date='2025-01-01', end_date='2026-07-31', frequency="d", adjustflag="2")
        rows = []
        while (rs.error_code == '0') & rs.next():
            rows.append(rs.get_row_data())
        if not rows: return None
        df = pd.DataFrame(rows, columns=["日期","开盘","最高","最低","收盘","成交量","成交额"])
        for c in ["开盘","最高","最低","收盘","成交量","成交额"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["日期"] = pd.to_datetime(df["日期"])
        df = df.dropna(subset=["收盘"]).fillna({"成交量":0,"成交额":0})
        df.sort_values("日期", inplace=True)
        return df
    except Exception as e:
        return None

def append_today_bar(df, spot):
    """把今日实时bar追加到日线末尾(若尚未包含今日)"""
    today = pd.Timestamp(REPORT_DATE)
    if len(df) and df["日期"].iloc[-1] == today:
        return df
    bar = pd.DataFrame([{
        "日期": today, "开盘": spot.get("open",0), "最高": spot.get("high",0),
        "最低": spot.get("low",0), "收盘": spot.get("price",0),
        "成交量": spot.get("volume",0), "成交额": spot.get("amount",0)
    }])
    return pd.concat([df, bar], ignore_index=True)

# ---------- 指标 ----------
def calc(hist):
    close = hist["收盘"].values.astype(float); vol = hist["成交量"].values.astype(float)
    def ma(n): return round(float(np.mean(close[-n:])),2) if len(close)>=n else None
    ma5,ma10,ma20,ma60 = ma(5),ma(10),ma(20),ma(60)
    ema12=close.copy(); ema26=close.copy()
    for i in range(1,len(close)):
        ema12[i]=ema12[i-1]*11/13+close[i]*2/13; ema26[i]=ema26[i-1]*25/27+close[i]*2/27
    dif=ema12-ema26; dea=dif.copy()
    for i in range(1,len(dif)): dea[i]=dea[i-1]*8/10+dif[i]*2/10
    macd=2*(dif-dea)
    rsi=None
    if len(close)>=15:
        d=np.diff(close); g=np.where(d>0,d,0); l=np.where(d<0,-d,0)
        ag=np.mean(g[-14:]); al=np.mean(l[-14:]); rsi=round(float(100 if al==0 else 100-(100/(1+ag/al))),1)
    bb_mid=bb_up=bb_lo=None
    if len(close)>=20:
        bb_mid=ma20; s=float(np.std(close[-20:])); bb_up=round(bb_mid+2*s,2); bb_lo=round(bb_mid-2*s,2)
    vol_r=None
    if len(vol)>=10:
        v5=float(np.mean(vol[-5:])); v10=float(np.mean(vol[-10:])); vol_r=round(v5/v10 if v10>0 else 1,2)
    cur=round(float(close[-1]),2)
    sig=[]
    if ma5 and ma10 and ma20:
        if cur>ma5>ma10>ma20: sig.append("多头排列")
        elif cur<ma5<ma10<ma20: sig.append("空头排列")
        elif cur>ma5 and ma5>ma20: sig.append("短期多头")
        elif cur<ma5 and ma5<ma20: sig.append("短期空头")
        else: sig.append("均线交织")
    if rsi is not None: sig.append(f"RSI{rsi}")
    if bb_up and cur>bb_up: sig.append("突破上轨")
    elif bb_lo and cur<bb_lo: sig.append("跌破下轨")
    elif bb_mid and cur>bb_mid: sig.append("中轨上方")
    elif bb_mid: sig.append("中轨下方")
    if vol_r:
        if vol_r>2: sig.append("显著放量")
        elif vol_r>1.5: sig.append("放量")
        elif vol_r<0.6: sig.append("显著缩量")
        elif vol_r<0.8: sig.append("缩量")
        else: sig.append("量能正常")
    support = bb_lo if bb_lo else (ma20 if ma20 else None)
    resistance = bb_up if bb_up else (ma5 if ma5 else None)
    macd_bull = bool(dif[-1]>dea[-1]) if len(dif)>0 else None
    return {
        "ma5":ma5,"ma10":ma10,"ma20":ma20,"ma60":ma60,
        "macd_dif":round(float(dif[-1]),4) if len(dif)>0 else None,
        "macd_dea":round(float(dea[-1]),4) if len(dea)>0 else None,
        "macd_hist":round(float(macd[-1]),4) if len(macd)>0 else None,
        "macd_bullish":macd_bull,
        "macd_golden_cross":bool(len(dif)>=2 and dif[-2]<dea[-2] and dif[-1]>dea[-1]),
        "macd_dead_cross":bool(len(dif)>=2 and dif[-2]>dea[-2] and dif[-1]<dea[-1]),
        "rsi":rsi,"bb_upper":bb_up,"bb_mid":bb_mid,"bb_lower":bb_lo,
        "vol_ratio":vol_r,"support":round(support,2) if support else None,
        "resistance":round(resistance,2) if resistance else None,
        "trend_signals":sig,"current_price":cur,
        "last_date":str(hist["日期"].iloc[-1].date()), "n_days":len(close)
    }

# ---------- 大盘 ----------
def fetch_index():
    try:
        df = ak.stock_zh_index_spot_sina()
        df.set_index("代码", inplace=True)
        r = {}
        for idx,name in {"sh000001":"上证指数","sz399001":"深证成指","sz399006":"创业板指","sh000688":"科创50","sh000300":"沪深300"}.items():
            if idx in df.index:
                row=df.loc[idx]
                r[name]={"price":float(row.get("最新价",0)),"change_pct":float(row.get("涨跌幅",0)),
                         "amount":row.get("成交额",None)}
        return r
    except Exception:
        return None

# ---------- 新闻 ----------
def fetch_news():
    try:
        n = ak.stock_info_global_em()
        if n is not None and not n.empty:
            out=[]
            for _,row in n.head(15).iterrows():
                out.append({"title":str(row.get("标题",""))[:90],"time":str(row.get("发布时间",""))[:16]})
            if out: return out
    except Exception: pass
    try:
        n = ak.stock_news_em()
        if n is not None and not n.empty:
            return [{"title":str(r.get("标题",""))[:90],"time":str(r.get("发布时间",""))[:16]} for _,r in n.head(15).iterrows()]
    except Exception: pass
    return None

# ---------- 决策 ----------
def decide(name, spot, ind):
    pct=spot.get("change_pct",0) or 0
    price=spot.get("price",0) or 0
    sig=ind.get("trend_signals",[]) if ind else []
    macd_bull=ind.get("macd_bullish"); macd_gold=ind.get("macd_golden_cross"); macd_dead=ind.get("macd_dead_cross")
    rsi=ind.get("rsi")
    a=["多头排列" in s for s in sig]
    def has(x): return any(x in s for s in sig)
    trend = ("多头排列" if has("多头排列") else "短期多头" if has("短期多头") else "空头排列" if has("空头排列") else "短期空头" if has("短期空头") else "均线交织")
    macd_s = "MACD金叉" if macd_gold else "MACD死叉" if macd_dead else "MACD多头" if macd_bull else "MACD空头"
    rsi_s = f"RSI超买{rsi}" if rsi and rsi>70 else f"RSI超卖{rsi}" if rsi and rsi<30 else (f"RSI{rsi}" if rsi else "")
    vol_s = "显著放量" if ind.get("vol_ratio") and ind["vol_ratio"]>2 else "放量" if ind.get("vol_ratio") and ind["vol_ratio"]>1.5 else "缩量" if ind.get("vol_ratio") and ind["vol_ratio"]<0.8 else "量能正常"
    support=ind.get("support"); resistance=ind.get("resistance")
    if pct<=-5:
        act="⚠️ 减仓/止损"; reason=f"大跌{pct:+.2f}%，{trend}，{macd_s}"
        if support and price<=support*1.02: act="👀 触及支撑"; reason+=f"，触及支撑{support}关注有效跌破"
    elif pct<=-3:
        act="🔴 关注"; reason=f"回调{pct:+.2f}%，{trend}，{macd_s}"
        if support and price<=support*1.03: act="👀 接近支撑"; reason+=f"，接近支撑{support}"
    elif pct<=0:
        act="👀 观察"; reason=f"回调{pct:+.2f}%，{trend}，{rsi_s}，{vol_s}"
    elif pct>5:
        act="⚠️ 逢高减仓" if (rsi and rsi>70) else "✅ 持有"
        reason=f"大涨{pct:+.2f}%，{trend}，{rsi_s}"
    elif pct>0:
        act="✅ 持有"
        reason=f"上涨{pct:+.2f}%，{trend}，{macd_s}，{vol_s}"
    return act, reason, trend, support, resistance

# ================= MAIN =================
print("="*60); print(f"📊 A股复盘 {REPORT_DATE}（{WEEKDAY}）"); print("="*60)

spot = fetch_spot()
idx = fetch_index()
valid = {k:v for k,v in spot.items() if v.get("status")=="ok"}
print(f"✅ 行情 {len(valid)} 只")

# 历史K线+指标
print("📈 计算技术指标（baostock + 今日实时bar）...")
ind_data={}
for code,name in STOCKS.items():
    if code in B_STOCKS: continue
    df=fetch_hist_baostock(code)
    if df is not None and len(df)>=20:
        df=append_today_bar(df, spot.get(code,{}))
        ind=calc(df)
        ind_data[code]=ind
        print(f"  {name}: {ind['current_price']} MA5={ind['ma5']} MA20={ind['ma20']} RSI={ind['rsi']} src={ind['n_days']}bars")
    else:
        ind_data[code]={}
        print(f"  {name}: ❌")
    time.sleep(0.2)
if _bs_logged["v"]: bs.logout()

news = fetch_news()
print(f"✅ 新闻 {len(news) if news else 0} 条")

# ---- 盘面统计 ----
ups=sum(1 for v in valid.values() if v["change_pct"]>0)
downs=sum(1 for v in valid.values() if v["change_pct"]<0)
flats=sum(1 for v in valid.values() if v["change_pct"]==0)
avg=round(float(np.mean([v["change_pct"] for v in valid.values()])),2)
big_up=sum(1 for v in valid.values() if v["change_pct"]>=5)
mid_up=sum(1 for v in valid.values() if 3<=v["change_pct"]<5)
big_dn=sum(1 for v in valid.values() if v["change_pct"]<=-5)
mid_dn=sum(1 for v in valid.values() if -5<v["change_pct"]<=-3)
up_ratio=round(ups/len(valid)*100,1) if valid else 0

classified={"green":[],"yellow":[],"red":[],"alert":[]}
for code,name in STOCKS.items():
    if code in B_STOCKS: continue
    sp=spot[code]; ind=ind_data.get(code,{})
    act,reason,trend,support,resistance=decide(name,sp,ind)
    e={"code":code,"name":name,"price":sp.get("price"),"pct":sp.get("change_pct",0),
       "turnover":sp.get("turnover"),"action":act,"reason":reason,"trend":trend,
       "support":support,"resistance":resistance,
       "rsi":ind.get("rsi"),"ma5":ind.get("ma5"),"ma20":ind.get("ma20"),
       "vol":ind.get("vol_ratio"),"sig":", ".join(ind.get("trend_signals",[]))[:50]}
    if e["pct"]>0: classified["green"].append(e)
    elif e["pct"]<=-5: classified["alert"].append(e)
    elif e["pct"]<=-3: classified["red"].append(e)
    else: classified["yellow"].append(e)
for k in classified: classified[k].sort(key=lambda x:x["pct"], reverse=(k!="alert"))

# 指数行
idx_rows=""
def _fmt_amt(v):
    try:
        v=float(v)
        if v>=1e12: return f"{v/1e12:.2f}万亿"
        if v>=1e8: return f"{v/1e8:.0f}亿"
        return f"{v:.0f}"
    except Exception: return "-"
if idx:
    for name,row in idx.items():
        ic="🟢" if row["change_pct"]>0 else "🔴" if row["change_pct"]<0 else "⏸️"
        idx_rows+=f"| {name} | {row['price']} | {row['change_pct']:+.2f}% | {_fmt_amt(row.get('amount'))} | {ic} |\n"

idx_txt=" | ".join(f"{n}{r['price']}({r['change_pct']:+.2f}%)" for n,r in (idx or {}).items())

mood = ("🔥 强势普涨" if avg>2 else "📈 整体偏强" if avg>1 else "📊 小幅上行" if avg>0.3 else "➖ 震荡整理" if avg>-0.3 else "📉 整体偏弱" if avg>-1 else "🔻 弱势调整")

# ---- 板块 ----
sectors={
 "新材料":["国瓷材料","久立特材"],"高端制造":["应流股份","恒立液压","汇川技术"],
 "锂电池":["亿纬锂能","天齐锂业"],"券商金融":["中信证券","中信建投","中金公司","越秀资本"],
 "军工":["雷科防务","安达维尔"],"半导体":["中芯国际","长电科技","通富微电"],
 "银行":["招商银行"],"光伏":["福莱特"],"汽配":["福耀玻璃"],"软件":["恒生电子"],
 "数据中心":["科华数据"],"金属":["中信金属"]}
sector_lines=[]
allstocks=classified["green"]+classified["yellow"]+classified["red"]+classified["alert"]
for sec,names in sectors.items():
    ss=[s for s in allstocks if s["name"] in names]
    if ss:
        ap=round(float(np.mean([s["pct"] for s in ss])),1)
        ic="🔥" if ap>2 else "✅" if ap>0.5 else "➖" if ap>-0.5 else "🔻" if ap>-2 else "🔴"
        sector_lines.append(f"- {ic} **{sec}** 均{ap:+.1f}%")

# ---- 报告组装 ----
tbl=lambda lst: "\n".join(
    f"| {e['code']} | {e['name']} | {e['price']} | {e['pct']:+.2f}% | {e.get('turnover','-')}% | {e['sig'][:42]} | 支撑{e['support']} | 压力{e['resistance']} | {e['action']} |"
    for e in lst)

news_section=""
if news:
    news_section="| 时间 | 标题 |\n|------|------|\n"+"\n".join(f"| {n['time']} | {n['title']} |" for n in news[:12])

report=f"""# 📊 A股收盘复盘 - {REPORT_DATE}（{WEEKDAY}）
> 数据日期：2026年7月31日（周五）收盘快照 | 来源：akshare(新浪行情) + baostock(历史K线) | 25只关注股

---

## 📈 盘面总览

| 指标 | 数据 |
|------|------|
| **上涨** | ✅ {ups}/{len(valid)} ({up_ratio}%) |
| **下跌** | 🔴 {downs} |
| **平盘** | ⏸️ {flats} |
| **平均涨跌幅** | {avg:+.2f}% |
| **涨幅≥5%** | 🟢 {big_up}只 |
| **3%-5% ↑** | {mid_up}只 |
| **3%-5% ↓** | 🔶 {mid_dn}只 |
| **跌幅≥5%** | 🔴 {big_dn}只 |

> **{mood}** | 关注股{ups}涨{downs}跌{flats}平，均幅{avg:+.2f}% | 大盘：{idx_txt}

---

## 🏛️ 大盘指数实况（{REPORT_DATE}收盘）

| 指数 | 最新价 | 涨跌幅 | 成交额 | 状态 |
|------|--------|--------|--------|------|
{idx_rows}
> 今日为**周五**，市场呈**超跌反弹**走势，关注创业板/科创50弹性及金融红利风格延续。

---

## 🔥 板块轮动热点

{chr(10).join(sector_lines) if sector_lines else "- 今日板块信号不明显"}

---

## 🟢 逆势上涨 / 强势 ({len(classified['green'])}只)

| 代码 | 名称 | 最新价 | 涨跌 | 换手 | 技术信号 | 支撑 | 压力 | 建议 |
|------|------|--------|------|------|----------|------|------|------|
{tbl(classified['green'])}

## 🟡 小幅回调 / 观望 ({len(classified['yellow'])}只)

| 代码 | 名称 | 最新价 | 涨跌 | 换手 | 技术信号 | 支撑 | 压力 | 建议 |
|------|------|--------|------|------|----------|------|------|------|
{tbl(classified['yellow'])}
"""
if classified["red"]:
    report+=f"""
## 🔴 深度回调 (-5%~-3%) ({len(classified['red'])}只)

| 代码 | 名称 | 最新价 | 涨跌 | 技术信号 | 建议 |
|------|------|--------|------|----------|------|
"""
    for e in classified["red"]:
        report+=f"| {e['code']} | {e['name']} | {e['price']} | {e['pct']:+.2f}% | {e['sig'][:45]} | {e['action']} |\n"
if classified["alert"]:
    report+=f"""
## 🚨 严重回调 (≤-5%) ({len(classified['alert'])}只)

| 代码 | 名称 | 最新价 | 涨跌 | 技术信号 | 建议 |
|------|------|--------|------|----------|------|
"""
    for e in classified["alert"]:
        report+=f"| {e['code']} | {e['name']} | {e['price']} | {e['pct']:+.2f}% | {e['sig'][:45]} | {e['action']} |\n"

report+=f"""
---

## 📰 关键市场新闻

{news_section}

---

## 🔮 明日关注清单（下周一 2026-08-03）

### ✅ 持有 / 强势延续
"""
hold=[e for e in classified["green"] if "持有" in e["action"]][:6]
report+= "\n".join(f"- **{e['name']}({e['code']})** {e['pct']:+.2f}% | 支撑{e['support']} 压力{e['resistance']}" for e in hold) or "- 暂无"
report+=f"""
### 👀 等待企稳 / 关注支撑
"""
watch=[e for e in classified["yellow"]+classified["red"] if "支撑" in e["action"] and e["pct"]<0][:6]
if not watch: watch=[e for e in classified["yellow"] if e["pct"]<0][:6]
report+= "\n".join(f"- **{e['name']}({e['code']})** {e['pct']:+.2f}% | 支撑{e['support']} | {e['action']}" for e in watch) or "- 暂无"
report+=f"""
### ⚠️ 风险关注
"""
risk=[e for e in classified["alert"]+classified["red"] if "止损" in e["action"] or "减仓" in e["action"]]
report+= "\n".join(f"- **{e['name']}({e['code']})** {e['pct']:+.2f}% | {e['action']} | 支撑{e['support']}" for e in risk[:5]) or "- 暂无"
report+=f"""
### ⚡ 超买预警 (RSI>70)
"""
ob=[e for e in allstocks if isinstance(e.get("rsi"),(int,float)) and e["rsi"]>70]
report+= "\n".join(f"- **{e['name']}({e['code']})** RSI={e['rsi']}" for e in ob) or "- 暂无"
report+=f"""
### 💡 超卖机会 (RSI<30)
"""
os_=[e for e in allstocks if isinstance(e.get("rsi"),(int,float)) and e["rsi"]<30]
report+= "\n".join(f"- **{e['name']}({e['code']})** RSI={e['rsi']} 支撑{e['support']}" for e in os_) or "- 暂无"

pos=f"4-6成" if avg>1 else f"3-5成" if avg>0.3 else f"3-4成" if avg>-0.5 else f"2-4成"
report+=f"""
### 💡 总体策略
- **市场氛围**：{mood}
- **仓位建议**：{pos}
- **核心思路**：{"持股待涨、跟踪强势龙头" if avg>0 else "控制仓位、低吸超卖优质标的"}（半导体/锂电超卖，金融红利仍强）
- **明日观察**：① 反弹能否放量延续 ② 科创50/创业板能否收复关键均线 ③ 高位材料股(国瓷/福耀)追高风险

*📅 {REPORT_DATE}（{WEEKDAY}）15:00收盘*
*⚠️ 分析仅供参考，不构成投资建议。投资有风险，入市需谨慎。*
"""

os.makedirs(f"{ANALYSIS_DIR}/daily",exist_ok=True)
with open(OUT,"w",encoding="utf-8") as f: f.write(report)
print(f"\n✅ 报告已保存: {OUT}")

# ---- 摘要 ----
sc=sorted(valid.items(),key=lambda x:x[1]["change_pct"],reverse=True)
top=sc[:3]; bot=sc[-3:]
summary=f"📊 **A股复盘 | {REPORT_DATE}（{WEEKDAY}）**\n"
summary+=f"\n📈 关注股 {ups}涨 {downs}跌 {flats}平 | 均幅 {avg:+.2f}%\n  {idx_txt}\n\n"
summary+="🏆 **涨幅前三：**\n"+"".join(f" 🟢 {d['name']}({c}) {d['change_pct']:+.2f}%\n" for c,d in top)
summary+="\n🔻 **跌幅前三：**\n"+"".join(f" 🔴 {d['name']}({c}) {d['change_pct']:+.2f}%\n" for c,d in reversed(bot))
drops=[(c,d) for c,d in sc if d["change_pct"]<=-3]
if drops: summary+="\n⚠️ **回调关注：**\n"+"".join(f" {d['name']}({c}) {d['change_pct']:+.2f}%\n" for c,d in drops[:5])
if ob: summary+="\n⚡ **超买(RSI>70)：**\n"+"".join(f" {e['name']}({e['code']}) RSI={e['rsi']}\n" for e in ob[:5])
if os_: summary+="\n💡 **超卖(RSI<30)：**\n"+"".join(f" {e['name']}({e['code']}) RSI={e['rsi']}\n" for e in os_[:5])
summary+=f"\n📎 完整版 → daily/{REPORT_DATE}.md"
with open(f"{ANALYSIS_DIR}/daily/{REPORT_DATE}_summary.txt","w",encoding="utf-8") as f: f.write(summary)
print("\n📋 摘要：\n"+summary)
