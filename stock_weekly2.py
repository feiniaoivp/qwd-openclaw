#!/usr/bin/env python3
"""本周A股30只股票行情总结 - 使用baostock"""
import baostock as bs
import pandas as pd
import numpy as np

# 关注的30只股票 (baostock代码格式: sh/sz.xxxxxx)
# 深市: 000, 001, 002, 300  sz; 沪市: 600, 601, 603, 688  sh
def to_bs_code(code):
    if code.startswith(('600','601','603','688')):
        return f"sh.{code}"
    else:
        return f"sz.{code}"

stocks = {
    "300285": "国瓷材料",
    "603308": "应流股份",
    "002156": "通富微电",
    "600584": "长电科技",
    "688981": "中芯国际",
    "300274": "阳光电源",
    "002466": "天齐锂业",
    "600036": "招商银行",
    "600660": "福耀玻璃",
    "601865": "福莱特",
    "600570": "恒生电子",
    "300719": "安达维尔",
    "600030": "中信证券",
    "600519": "贵州茅台",
    "300750": "宁德时代",
    "000858": "五粮液",
    "002594": "比亚迪",
    "000333": "美的集团",
    "601318": "中国平安",
    "600900": "长江电力",
    "000568": "泸州老窖",
    "002415": "海康威视",
    "002475": "立讯精密",
    "601012": "隆基绿能",
    "300059": "东方财富",
    "600887": "伊利股份",
    "688008": "澜起科技",
    "688012": "中微公司",
    "002371": "北方华创",
    "603501": "韦尔股份",
}

# 登录
lg = bs.login()
print(f"登录结果: {lg.error_msg}")

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
        adjustflag="2"  # 前复权
    )
    
    data_list = []
    while rs.next():
        data_list.append(rs.get_row_data())
    
    if not data_list:
        print(f"⚠️ {name}({code}): 无本周数据")
        continue
    
    df = pd.DataFrame(data_list, columns=["date","code","open","high","low","close","volume","amount","pctChg"])
    for col in ["open","high","low","close","volume","amount","pctChg"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    
    week_open = df.iloc[0]["open"]
    week_close = df.iloc[-1]["close"]
    week_high = df["high"].max()
    week_low = df["low"].min()
    
    # 周涨跌幅
    weekly_return = (week_close - week_open) / week_open * 100
    
    # 周振幅
    week_amplitude = (week_high - week_low) / week_open * 100
    
    # 成交量(手), 成交额(万元)
    week_volume = df["volume"].sum()
    week_amount = df["amount"].sum()  # 万元
    
    # 涨跌天数
    up_days = (df["pctChg"] > 0).sum()
    down_days = (df["pctChg"] < 0).sum()
    
    # 日均换手率 (简化)
    
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
        "交易日数": len(df),
    })
    print(f"✅ {name}({code}): 周涨跌 {weekly_return:+.2f}%")

bs.logout()

df_result = pd.DataFrame(results)
if df_result.empty:
    print("没有获取到任何数据")
    exit(1)

df_result = df_result.sort_values("周涨跌幅%", ascending=False)

print("\n" + "="*70)
print(f"📊 本周A股持仓总结 (统计周期: {start_date} ~ {end_date})")
print(f"覆盖股票: {len(df_result)} 只")
print("="*70)

print(f"\n🟢 涨幅TOP 5:")
for _, r in df_result.head(5).iterrows():
    emoji_up = "🟢" if r["周涨跌幅%"] > 0 else "🔴"
    print(f"  {emoji_up} {r['名称']}({r['代码']}): {r['周涨跌幅%']:+.2f}%  收盘¥{r['本周收盘']}  振幅{r['周振幅%']:.2f}%")

print(f"\n🔴 跌幅TOP 5:")
for _, r in df_result.tail(5).iterrows():
    emoji = "🔴" if r["周涨跌幅%"] < 0 else "🟢"
    print(f"  {emoji} {r['名称']}({r['代码']}): {r['周涨跌幅%']:+.2f}%  收盘¥{r['本周收盘']}  振幅{r['周振幅%']:.2f}%")

# 板块分类分析
sectors = {
    "半导体/芯片": ["北方华创","中微公司","澜起科技","韦尔股份","长电科技","通富微电","中芯国际"],
    "新能源": ["宁德时代","阳光电源","隆基绿能","天齐锂业","比亚迪"],
    "消费/白酒": ["贵州茅台","五粮液","泸州老窖","伊利股份"],
    "金融": ["招商银行","中国平安","中信证券","东方财富"],
    "制造/其他": ["美的集团","立讯精密","海康威视","福耀玻璃","国瓷材料","应流股份","福莱特","恒生电子","安达维尔","长江电力"],
}

print(f"\n📂 板块表现:")
for sector, names in sectors.items():
    sector_stocks = [r for r in results if r["名称"] in names]
    if sector_stocks:
        avg_return = np.mean([r["周涨跌幅%"] for r in sector_stocks])
        positive = sum(1 for r in sector_stocks if r["周涨跌幅%"] > 0)
        total = len(sector_stocks)
        emoji = "🟢" if avg_return > 0 else "🔴"
        print(f"  {emoji} {sector}: 平均 {avg_return:+.2f}%  ({positive}/{total}上涨)")

# 总结统计
print(f"\n📈 总体统计:")
pos = sum(1 for r in results if r["周涨跌幅%"] > 0)
neg = sum(1 for r in results if r["周涨跌幅%"] < 0)
flat = len(results) - pos - neg
avg_all = np.mean([r["周涨跌幅%"] for r in results])
max_up = max(results, key=lambda x: x["周涨跌幅%"])
max_down = min(results, key=lambda x: x["周涨跌幅%"])
print(f"  上涨: {pos}只  下跌: {neg}只  持平: {flat}只")
print(f"  平均涨跌幅: {avg_all:+.2f}%")
print(f"  最强: {max_up['名称']} {max_up['周涨跌幅%']:+.2f}%")
print(f"  最弱: {max_down['名称']} {max_down['周涨跌幅%']:+.2f}%")

# 保存
df_result.to_csv("/Users/duguke/.openclaw/workspace/weekly_report_0717.csv", index=False, encoding="utf-8-sig")
print(f"\n💾 完整数据已保存到 weekly_report_0717.csv")
