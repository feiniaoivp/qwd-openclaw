import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import sys

# 41 stocks from watchlist
stocks = [
    ("603601", "再升科技"), ("605123", "派克新材"), ("002149", "西部材料"),
    ("003009", "中天火箭"), ("603678", "火炬电子"), ("300337", "银邦股份"),
    ("603596", "伯特利"), ("688433", "华曙高科"), ("300762", "上海瀚讯"),
    ("600343", "航天动力"), ("688066", "ST航图"),
    ("600160", "巨化股份"), ("300827", "上能电气"), ("600346", "恒力石化"),
    ("000708", "中信特钢"), ("300748", "金力永磁"), ("002413", "雷科防务"),
    ("601061", "中信金属"), ("900925", "机电B股"), ("000157", "中联重废联重科"),
    ("603308", "应流股份"), ("601865", "福莱特"), ("600660", "福耀玻璃"),
    ("002318", "久立特材"), ("002335", "科华数据"), ("605566", "福莱蒽特"),
    ("601995", "中金公司"), ("600030", "中信证券"), ("601066", "中信建投"),
    ("000987", "越秀资本"), ("601100", "恒立液压"), ("600570", "恒生电子"),
    ("300124", "汇川技术"), ("300719", "安达维尔"), ("002466", "天齐锂业"),
    ("300014", "亿纬锂能"), ("600036", "招商银行"), ("300285", "国瓷材料"),
    ("688981", "中芯国际"), ("600584", "长电科技"), ("002156", "通富微电"),
]

end_date = datetime.now().strftime("%Y%m%d")
start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

results = []

for i, (code, name) in enumerate(stocks):
    print(f"[{i+1}/41] {code} {name}...", flush=True)
    try:
        # 历史K线
        hist = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                   start_date=start_date, end_date=end_date, adjust="qfq")
        if hist is not None and len(hist) > 10:
            hist = hist.sort_values("日期")
            close = hist["收盘"].values
            vol = hist["成交量"].values
            annual_ret = (close[-1] / close[0] - 1) * 100
            daily_rets = np.diff(close) / close[:-1]
            annual_vol = np.std(daily_rets) * np.sqrt(242) * 100
            avg_vol = np.mean(vol) / 100  # 转为手
        else:
            annual_ret = annual_vol = avg_vol = np.nan
        
        # 财务指标
        pe_ttm = pb = roe = net_profit_yoy = revenue_yoy = debt_asset = np.nan
        try:
            fin = ak.stock_financial_analysis_indicator(symbol=code)
            if fin is not None and len(fin) > 0:
                fin = fin.sort_values("报告日期", ascending=False)
                row = fin.iloc[0]
                pe_ttm = row.get("市盈率TTM", np.nan)
                pb = row.get("市净率", np.nan)
                roe = row.get("净资产收益率", np.nan)
        except:
            pass
        
        try:
            fin_abs = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
            if fin_abs is not None and len(fin_abs) > 0:
                fin_abs = fin_abs.sort_values("报告期", ascending=False)
                row = fin_abs.iloc[0]
                net_profit_yoy = row.get("净利润同比增长", np.nan)
                revenue_yoy = row.get("营业收入同比增长", np.nan)
                debt_asset = row.get("资产负债率", np.nan)
        except:
            pass
        
        results.append({
            "stock_code": code, "stock_name": name,
            "annual_return_pct": round(annual_ret, 2) if not np.isnan(annual_ret) else "",
            "annual_volatility_pct": round(annual_vol, 2) if not np.isnan(annual_vol) else "",
            "avg_daily_volume_hands": round(avg_vol, 0) if not np.isnan(avg_vol) else "",
            "pe_ttm": round(pe_ttm, 2) if not np.isnan(pe_ttm) else "",
            "pb": round(pb, 2) if not np.isnan(pb) else "",
            "roe_latest": round(roe, 2) if not np.isnan(roe) else "",
            "net_profit_yoy_pct": round(net_profit_yoy, 2) if not np.isnan(net_profit_yoy) else "",
            "revenue_yoy_pct": round(revenue_yoy, 2) if not np.isnan(revenue_yoy) else "",
            "debt_to_asset_pct": round(debt_asset, 2) if not np.isnan(debt_asset) else "",
        })
        time.sleep(0.5)  # 限流
    except Exception as e:
        print(f"  Error: {e}", flush=True)
        results.append({"stock_code": code, "stock_name": name, "error": str(e)})

df = pd.DataFrame(results)
df.to_csv("/Users/duguke/.openclaw/workspace/stock_analysis_41.csv", index=False, encoding="utf-8-sig")
print(f"\nDone! Saved {len(results)} rows to stock_analysis_41.csv")
