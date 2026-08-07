#!/usr/bin/env python3
"""获取关注股票本周行情总结"""
import baostock as bs
import pandas as pd
import numpy as np

stocks = {
    "600160": "巨化股份",
    "300827": "上能电气",
    "600346": "恒力石化",
    "000708": "中信特钢",
    "300748": "金力永磁",
    "002413": "雷科防务",
    "601061": "中信金属",
    "000157": "中联重科",
    "603308": "应流股份",
    "601865": "福莱特",
    "600660": "福耀玻璃",
    "002318": "久立特材",
    "002335": "科华数据",
    "605566": "福莱蒽特",
    "601995": "中金公司",
    "600030": "中信证券",
    "601066": "中信建投",
    "000987": "越秀资本",
    "601100": "恒立液压",
    "600570": "恒生电子",
    "300124": "汇川技术",
    "300719": "安达维尔",
    "002466": "天齐锂业",
    "600036": "招商银行",
    "300285": "国瓷材料",
    "688981": "中芯国际",
    "600584": "长电科技",
    "002156": "通富微电",
}

def to_bs_code(code):
    if code.startswith(('600','601','603','605','688','900')):
        return f"sh.{code}"
    else:
        return f"sz.{code}"

lg = bs.login()
print(f"登录: {lg.error_msg}")

end_date = "2026-07-17"
start_date = "2026-07-10"

results = []

for code, name in stocks.items():
    bs_code = to_bs_code(code)
    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,code,open,high,low,close,volume,amount,pctChg",
        start_date=start_date,
        end_date=end_date,
        frequency="d",
        adjustflag="2"
    )
    data_list = []
    while rs.next():
        data_list.append(rs.get_row_data())
    if not data_list:
        print(f"⚠ {name}({code}): 无数据")
        continue
    
    df = pd.DataFrame(data_list, columns=["date","code","open","high","low","close","volume","amount","pctChg"])
    for col in ["open","high","low","close","volume","amount","pctChg"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    
    week_open = df.iloc[0]["open"]
    week_close = df.iloc[-1]["close"]
    week_high = df["high"].max()
    week_low = df["low"].min()
    weekly_return = (week_close - week_open) / week_open * 100
    week_amplitude = (week_high - week_low) / week_open * 100
    week_volume = df["volume"].sum()
    week_amount = df["amount"].sum()
    up_days = (df["pctChg"] > 0).sum()
    down_days = (df["pctChg"] < 0).sum()
    
    results.append({
        "代码": code,
        "名称": name,
        "本周开盘": round(week_open, 2),
        "本周最高": round(week_high, 2),
        "本周最低": round(week_low, 2),
        "本周收盘": round(week_close, 2),
        "周涨跌幅%": round(weekly_return, 2),
        "周振幅%": round(week_amplitude, 2),
        "周成交量(万手)": round(week_volume / 10000, 2),
        "周成交额(亿)": round(week_amount / 10000, 2),
        "上涨天数": up_days,
        "下跌天数": down_days,
    })
    print(f"✅ {name}({code}): {weekly_return:+.2f}%")

bs.logout()

df_result = pd.DataFrame(results)
df_result = df_result.sort_values("周涨跌幅%", ascending=False)

print("\n" + "="*70)
print(f"📊 关注股票本周总结 (7/10 - 7/17)")
print(f"覆盖: {len(df_result)} 只")
print("="*70)

print(f"\n🟢 涨幅TOP5:")
for _, r in df_result.head(5).iterrows():
    e = "🟢" if r["周涨跌幅%"] > 0 else "🔴"
    print(f"  {e} {r['名称']}({r['代码']}): {r['周涨跌幅%']:+.2f}%  收盘{r['本周收盘']}  振幅{r['周振幅%']:.2f}%")

print(f"\n🔴 跌幅TOP5:")
for _, r in df_result.tail(5).iterrows():
    print(f"  🔴 {r['名称']}({r['代码']}): {r['周涨跌幅%']:+.2f}%  收盘{r['本周收盘']}  振幅{r['周振幅%']:.2f}%")

pos = sum(1 for r in results if r["周涨跌幅%"] > 0)
neg = sum(1 for r in results if r["周涨跌幅%"] < 0)
avg_all = np.mean([r["周涨跌幅%"] for r in results])
max_up = max(results, key=lambda x: x["周涨跌幅%"])
max_down = min(results, key=lambda x: x["周涨跌幅%"])

print(f"\n📈 总体: 上涨{pos}只 下跌{neg}只 平均{avg_all:+.2f}%")
print(f"  最强: {max_up['名称']} {max_up['周涨跌幅%']:+.2f}%")
print(f"  最弱: {max_down['名称']} {max_down['周涨跌幅%']:+.2f}%")

df_result.to_csv("/Users/duguke/.openclaw/workspace/关注股票周报_0717.csv", index=False, encoding="utf-8-sig")
print(f"\n💾 已保存")
