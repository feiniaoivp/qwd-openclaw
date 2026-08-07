#!/usr/bin/env python3
"""
最终输出: 从batch4_final.json提取数据,组装为规范格式输出
"""

import json

with open("/Users/duguke/.openclaw/workspace/tmp/batch4_final.json") as f:
    data = json.load(f)

stocks = data["第4批股票分析"]

output = []

for s in stocks:
    code = s["code"]
    name = s["name"]
    tc = s.get("技术面", {})
    fc = s.get("资金流向", {})
    fd = s.get("财务数据", {})
    
    # 财务数据 - 中报预告/业绩快报
    yj_records = []
    for key in ["2025年中报业绩预告", "业绩预告_20250630"]:
        if key in fd and isinstance(fd[key], list):
            yj_records = fd[key]
            break
    
    fin_summary = []
    for r in yj_records:
        fin_summary.append({
            "预测指标": r.get("预测指标", ""),
            "业绩变动": r.get("业绩变动", ""),
            "预告类型": r.get("预告类型", ""),
            "业绩变动幅度%": r.get("业绩变动幅度")
        })
    
    # 机构调研
    research = s.get("机构调研", {})
    research_summary = ""
    if research and isinstance(research, dict):
        keys = list(research.keys())
        if keys:
            first_key = keys[0]
            items = research[first_key]
            if isinstance(items, list) and len(items) > 0:
                # 如果龙虎榜机构统计里没有匹配该股票,标注无数据
                found = False
                for item in items[:5]:
                    if name[:2] in str(item.get("名称", "")) or code in str(item.get("代码", "")):
                        found = True
                        break
                research_summary = f"有{len(items)}条龙虎榜机构统计数据, 其中该股票特征不明显" if not found else f"有匹配的龙虎榜机构数据({len(items)}条)"
            else:
                research_summary = "无最近机构调研数据"
        else:
            research_summary = "无最近机构调研数据"
    else:
        research_summary = "无最近机构调研数据"
    
    # 资金流向
    fund_flow = {}
    if isinstance(fc, dict):
        fund_flow = {
            "近5日净流入(万元)": fc.get("近5日净流入(万元)", fc.get("近5日净流入")),
            "近10日净流入(万元)": fc.get("近10日净流入(万元)", fc.get("近10日净流入")),
            "近20日净流入(万元)": fc.get("近20日净流入(万元)", fc.get("近20日净流入"))
        }
    else:
        fund_flow = {"说明": str(fc) if fc else "EastMoney接口连接失败,无法获取"}
    
    # 技术面
    tech = {}
    if isinstance(tc, dict) and tc:
        tech = {
            "最新交易日": tc.get("最新日期"),
            "开盘价": tc.get("开盘价"),
            "收盘价": tc.get("收盘价"),
            "最高价": tc.get("最高价"),
            "最低价": tc.get("最低价"),
            "涨跌幅%": tc.get("涨跌幅%"),
            "成交量(手)": tc.get("成交量(手)"),
            "成交额(万元)": tc.get("成交额(万元)"),
            "MA5": tc.get("MA5"),
            "MA10": tc.get("MA10"),
            "MA20": tc.get("MA20"),
            "MA60": tc.get("MA60"),
            "MACD_DIF": tc.get("MACD_DIF"),
            "MACD_DEA": tc.get("MACD_DEA"),
            "MACD_柱状值": tc.get("MACD_柱状值"),
            "MACD_柱状前日": tc.get("MACD_柱状前日"),
            "MACD_趋势": f"{tc.get('MACD_红绿','')} + {tc.get('MACD_柱状方向','')}",
            "RSI6": tc.get("RSI6"),
            "RSI12": tc.get("RSI12"),
            "RSI6_状态": tc.get("RSI6_状态"),
            "量比(较5日均量)": tc.get("量比(较5日均量)"),
            "近20日支撑位(最低)": tc.get("近20日支撑位"),
            "近20日压力位(最高)": tc.get("近20日压力位"),
            "近60日最高价": tc.get("近60日最高价"),
            "近60日最低价": tc.get("近60日最低价"),
            "均线位置": tc.get("均线位置")
        }
    else:
        tech = {"说明": "技术面数据获取失败"}
    
    # 利润表补充(baostock)
    profit_data = {}
    for k, v in fd.items():
        if k.startswith("利润表"):
            profit_data[k] = v
    
    entry = {
        "code": code,
        "name": name,
        "中报预告_业绩快报": fin_summary if fin_summary else "无数据",
        "利润表(baostock)": profit_data if profit_data else "无数据",
        "机构调研": research_summary,
        "资金流向": fund_flow,
        "技术面": tech
    }
    
    output.append(entry)

# 输出最终JSON
final = {
    "报告日期": "2026-07-26",
    "数据来源": "akshare(stock_yjyg_em) + baostock(日K线+利润表) + stock_lhb_jgstatistic_em(龙虎榜)",
    "说明": "资金流向数据(EastMoney stock_individual_fund_flow)因服务器连接失败(RemoteDisconnected)无法获取",
    "第4批股票清单": output
}

clean_path = "/Users/duguke/.openclaw/workspace/tmp/batch4_clean.json"
with open(clean_path, "w", encoding="utf-8") as f:
    json.dump(final, f, ensure_ascii=False, indent=2, default=str)

print("=" * 80)
print(json.dumps(final, ensure_ascii=False, indent=2, default=str))
print("=" * 80)
print(f"\n✅ 已保存: {clean_path}")
