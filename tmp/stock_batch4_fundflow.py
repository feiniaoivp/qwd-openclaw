#!/usr/bin/env python3
"""
补资金流向: 尝试其他接口获取近5/10/20日资金流向
"""

import akshare as ak
import warnings, time, json
warnings.filterwarnings('ignore')

TMP = "/Users/duguke/.openclaw/workspace/tmp"

# 加载已有数据
with open(f"{TMP}/batch4_final.json", "r") as f:
    data = json.load(f)

stocks = [(r["code"], r["name"]) for r in data["第4批股票分析"]]

print("=== 补资金流向 ===")

for code, name in stocks:
    mkt = "sh" if code.startswith("6") else "sz"
    
    for attempt in range(3):
        try:
            time.sleep(2)
            df = ak.stock_individual_fund_flow(stock=code, market=mkt)
            if df is not None and not df.empty:
                recent = df.head(20)
                nc = [c for c in recent.columns if '净额' in c]
                if nc:
                    col = nc[0]
                    import numpy as np
                    vs = []
                    for v in recent[col].values:
                        try:
                            vs.append(float(v))
                        except:
                            vs.append(0.0)
                    vs = np.array(vs)
                    
                    fund = {
                        "近5日净流入(万元)": round(float(np.sum(vs[:5])), 2) if len(vs) >= 5 else None,
                        "近10日净流入(万元)": round(float(np.sum(vs[:10])), 2) if len(vs) >= 10 else None,
                        "近20日净流入(万元)": round(float(np.sum(vs[:20])), 2) if len(vs) >= 20 else None,
                    }
                    
                    # 最新一日
                    latest = recent.iloc[0]
                    fund["最新日期"] = str(latest[recent.columns[0]].date()) if hasattr(latest[recent.columns[0]], 'date') else str(latest[recent.columns[0]])
                    fund["最新净流入(万元)"] = float(latest[col]) if latest[col] != '-' else 0
                    
                    print(f"  {code} {name}: 5日={fund['近5日净流入(万元)']}万元, 10日={fund['近10日净流入(万元)']}万元, 20日={fund['近20日净流入(万元)']}万元")
                    
                    # 写入结果
                    for r in data["第4批股票分析"]:
                        if r["code"] == code:
                            r["资金流向"] = fund
                    break
                else:
                    print(f"  {code} {name}: 没有净额列, 列={list(recent.columns)}")
                    fund = {"原始数据列": list(recent.columns)[:8], "数据条数": len(recent)}
                    for r in data["第4批股票分析"]:
                        if r["code"] == code:
                            r["资金流向"] = fund
                    break
        except Exception as e:
            err = str(e)
            if 'RemoteDisconnected' in err:
                print(f"  {code} {name}: 连接断开(尝试{attempt+1}/3)")
                time.sleep(5)
            else:
                print(f"  {code} {name}: {err[:80]}")
                break
    else:
        print(f"  {code} {name}: ❌ 全部重试失败")
        for r in data["第4批股票分析"]:
            if r["code"] == code:
                r["资金流向"] = "EastMoney服务器连接失败，无法获取资金流向数据"

# 再尝试 stock_sector_fund_flow_hist 或 stock_market_fund_flow
print("\n=== 尝试行业/市场整体资金流向 ===")
try:
    time.sleep(2)
    df_mkt = ak.stock_market_fund_flow()
    if df_mkt is not None and not df_mkt.empty:
        print(f"  市场资金流向: {len(df_mkt)}行")
        print(f"  列: {list(df_mkt.columns[:10])}")
except Exception as e:
    print(f"  市场资金流向: {str(e)[:80]}")

# 保存
with open(f"{TMP}/batch4_final.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2, default=str)

print(f"\n✅ 已更新: {TMP}/batch4_final.json")
