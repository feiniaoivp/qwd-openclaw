#!/usr/bin/env python3
"""本周A股30只股票行情总结"""
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

# 关注的30只股票
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

# 本周时间范围
end_date = "20260717"
start_date = "20260710"

results = []

for code, name in stocks.items():
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                 start_date=start_date, end_date=end_date, 
                                 adjust="qfq")
        if df.empty:
            print(f"{name}({code}): 无数据")
            continue
        
        # 本周数据
        week_open = df.iloc[0]['开盘']
        week_high = df['最高'].max()
        week_low = df['最低'].min()
        week_close = df.iloc[-1]['收盘']
        week_change = (week_close - week_open) / week_open * 100
        week_amplitude = (week_high - week_low) / week_open * 100
        week_volume = df['成交量'].sum()  # 手
        week_amount = df['成交额'].sum()  # 万元
        avg_volume = df['成交量'].mean()
        
        # 上周收盘价（取第一天的前收盘或开盘）
        prev_close = df.iloc[0]['开盘']  # 近似
        if len(df) > 1:
            # 用第一个交易日较前日涨跌幅反推前收盘
            first_change_pct = (df.iloc[0]['收盘'] - df.iloc[0]['开盘']) / df.iloc[0]['开盘'] * 100
        
        # 与上周收盘比较（用本周一开盘近似）
        weekly_return = week_change
        
        # 周内涨跌日统计
        df['每日涨跌'] = df['收盘'].pct_change() * 100
        up_days = (df['每日涨跌'] > 0).sum()
        down_days = (df['每日涨跌'] < 0).sum()
        if df.iloc[0]['收盘'] > df.iloc[0]['开盘']:
            first_day_up = True
        else:
            first_day_up = False
        
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
    except Exception as e:
        print(f"❌ {name}({code}): {e}")

df_result = pd.DataFrame(results)
df_result = df_result.sort_values("周涨跌幅%", ascending=False)

print("\n\n===== 本周总结 =====")
print(f"统计时间: {start_date} ~ {end_date}")
print(f"覆盖股票: {len(df_result)} 只")
print(f"\n涨幅前5:")
for _, r in df_result.head(5).iterrows():
    print(f"  {r['名称']}({r['代码']}): {r['周涨跌幅%']:+.2f}%  收盘{r['本周收盘']}")

print(f"\n跌幅前5:")
for _, r in df_result.tail(5).iterrows():
    print(f"  {r['名称']}({r['代码']}): {r['周涨跌幅%']:+.2f}%  收盘{r['本周收盘']}")

# 保存CSV
df_result.to_csv("/Users/duguke/.openclaw/workspace/weekly_report_0717.csv", index=False, encoding="utf-8-sig")
print(f"\n💾 已保存到 weekly_report_0717.csv")
