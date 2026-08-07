import os
import pandas as pd
import akshare as ak
import time
import random
from datetime import datetime, timedelta

# 30只关注的股票池
STOCKS = {
 "600160": "巨化股份", "300827": "上能电气", "600346": "恒力石化",
 "000708": "中信特钢", "300748": "金力永磁", "002413": "雷科防务",
 "601061": "中信金属", "900925": "机电B股", "000157": "中联重科",
 "603308": "应流股份", "601865": "福莱特", "600660": "福耀玻璃",
 "002318": "久立特材", "002335": "科华数据", "605566": "福莱蒽特",
 "601995": "中金公司", "600030": "中信证券", "601066": "中信建投",
 "000987": "越秀资本", "601100": "恒立液压", "600570": "恒生电子",
 "300124": "汇川技术", "300719": "安达维尔", "002466": "天齐锂业",
 "300014": "亿纬锂能", "600036": "招商银行", "300285": "国瓷材料",
 "688981": "中芯国际", "600584": "长电科技", "002156": "通富微电"
}

end_date = datetime.today().strftime('%Y%m%d')
start_date = (datetime.today() - timedelta(days=365)).strftime('%Y%m%d')

print(f"🚀 [反反爬升级版] 开始下载 Mac 本地股票数据...")
print(f"📅 时间范围: {start_date} -> {end_date}")
print("-" * 60)

background_list = []

for code, name in STOCKS.items():
 print(f"📦 正在处理: {name}({code})...")

 # --- 1. K线数据下载（带3次失败重试机制） ---
 success = False
 for attempt in range(3):
 try:
 if code == "900925":
 df_kline = ak.stock_zh_b_daily(symbol="sh900925", start_date=start_date, end_date=end_date)
 else:
 df_kline = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")

 if not df_kline.empty:
 df_kline.rename(columns={'开盘': '开盘价', '收盘': '收盘价', '最高': '最高价', '最低': '最低价', '成交量': '成交量'}, inplace=True)
 kline_filename = f"{code}_{name}_kline.csv"
 df_kline.to_csv(kline_filename, index=False, encoding="utf-8-sig")
 print(f" └─ ✅ K线数据保存成功")
 success = True
 break
 except Exception as e:
 wait_time = (attempt + 1) * 2
 time.sleep(wait_time)

 if not success:
 print(f" ❌ [严重错误] 连续3次请求被拒绝，请检查网络")

 # --- 2. 背景资料下载 ---
 industry, business, total_shares = "未知", "未知", "未知"
 try:
 df_profile = ak.stock_individual_info_cls(symbol=code)
 if not df_profile.empty:
 industry = df_profile[df_profile['item'] == '所属行业']['value'].values[0] if '所属行业' in df_profile['item'].values else "未知"
 business = df_profile[df_profile['item'] == '主营业务']['value'].values[0] if '主营业务' in df_profile['item'].values else "未知"
 total_shares = df_profile[df_profile['item'] == '总股本']['value'].values[0] if '总股本' in df_profile['item'].values else "未知"
 except:
 pass

 background_list.append({
 "股票代码": code, "股票名称": name, "所属行业": industry, "总股本": total_shares, "主营业务背景": business
 })

 # --- 3. 每次请求完强制休眠 0.5 到 1.5 秒，防止被封 ---
 time.sleep(random.uniform(0.5, 1.5))

# 导出背景资料
df_bg = pd.DataFrame(background_list)
bg_filename = "股票背景资料汇总.csv"
df_bg.to_csv(bg_filename, index=False, encoding="utf-8-sig")

print("-" * 60)
print(f"🎉 全部跑完！背景资料已汇总至: {bg_filename}")
