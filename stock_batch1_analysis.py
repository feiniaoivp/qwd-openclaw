#!/usr/bin/env python3
"""
A股5只股票综合分析脚本
数据来源：
 - akshare 财务数据、业绩预告、资金流向（部分可能被block）
 - baostock K线数据（workaround）
"""

import akshare as ak
import baostock as bs
import pandas as pd
import numpy as np
import json
import warnings
import time
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

stocks = [
    {"code": "002318", "name": "久立特材", "market": "sz", "bs_code": "sz.002318"},
    {"code": "300014", "name": "亿纬锂能", "market": "sz", "bs_code": "sz.300014"},
    {"code": "601066", "name": "中信建投", "market": "sh", "bs_code": "sh.601066"},
    {"code": "600030", "name": "中信证券", "market": "sh", "bs_code": "sh.600030"},
    {"code": "300124", "name": "汇川技术", "market": "sz", "bs_code": "sz.300124"},
]

today = datetime.now()
today_str = today.strftime("%Y-%m-%d")
date_str = today.strftime("%Y%m%d")
date_start_bs = (today - timedelta(days=365)).strftime("%Y-%m-%d")
date_end_bs = today.strftime("%Y-%m-%d")

results = []


def safe_json(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(round(obj, 4))
    elif isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    elif isinstance(obj, pd.Timestamp):
        return obj.strftime("%Y-%m-%d")
    elif isinstance(obj, datetime):
        return obj.strftime("%Y-%m-%d")
    elif pd.isna(obj) or obj is None:
        return None
    return obj


def safe_float(val):
    try:
        v = float(val)
        if np.isnan(v) or np.isinf(v):
            return None
        return round(v, 4)
    except:
        return None


def safe_int(val):
    try:
        return int(float(val))
    except:
        return None


def compute_ma(close_series, window):
    if len(close_series) < window:
        return None
    return round(float(close_series.tail(window).mean()), 2)


def compute_macd(close):
    if len(close) < 26:
        return None, None, None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_bar = 2 * (dif - dea)
    return float(round(dif.iloc[-1], 4)) if len(dif) > 0 else None, \
           float(round(dea.iloc[-1], 4)) if len(dea) > 0 else None, \
           float(round(macd_bar.iloc[-1], 4)) if len(macd_bar) > 0 else None


def compute_rsi(close, window):
    if len(close) < window + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=window, min_periods=window).mean()
    avg_loss = loss.rolling(window=window, min_periods=window).mean()
    avg_loss = avg_loss.replace(0, np.nan)
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2) if len(rsi) > 0 else None


def find_support_resistance(kline_df, recent_n=60):
    recent = kline_df.tail(recent_n)
    if len(recent) < 10:
        return None, None
    support = round(float(recent['low'].nsmallest(3).mean()), 2) if len(recent) >= 3 else round(float(recent['low'].min()), 2)
    resistance = round(float(recent['high'].nlargest(3).mean()), 2) if len(recent) >= 3 else round(float(recent['high'].max()), 2)
    return support, resistance


# ===========================================================================
# 1. 业绩预告 & 财务摘要
# ===========================================================================
def get_financial_data(code, name):
    result = {"code": code, "name": name, "yjyg": None, "abstract": None, "financial_indicators": None}

    # 1a. 业绩预告 (stock_yjyg_em is by date, not by symbol - filter)
    try:
        df = ak.stock_yjyg_em(date='20250630')
        if df is not None and not df.empty:
            stock_df = df[df['股票代码'] == code]
            if not stock_df.empty:
                records = []
                for _, row in stock_df.iterrows():
                    record = {col: safe_json(row[col]) for col in row.index}
                    records.append(record)
                result["yjyg"] = records
                print(f"  [OK] {name} 业绩预告: {len(records)}条")
            else:
                print(f"  [INFO] {name} 无业绩预告数据")
    except Exception as e:
        print(f"  [WARN] {name} 业绩预告: {e}")

    # 1b. 财务摘要-最新几期 (stock_financial_abstract_new_ths)
    try:
        df = ak.stock_financial_abstract_new_ths(symbol=code)
        if df is not None and not df.empty:
            metrics = {}
            for _, row in df.iterrows():
                rd = row['report_date']
                mn = row['metric_name']
                if rd not in metrics:
                    metrics[rd] = {}
                metrics[rd][mn] = {
                    "value": safe_float(row['value']),
                    "yoy": safe_float(row['yoy']),
                }
            # 取最近3期
            sorted_dates = sorted(metrics.keys(), reverse=True)[:3]
            latest_metrics = {d: metrics[d] for d in sorted_dates}
            result["abstract"] = latest_metrics
            print(f"  [OK] {name} 财务摘要: {len(sorted_dates)}期数据")
    except Exception as e:
        print(f"  [WARN] {name} 财务摘要: {e}")

    return result


# ===========================================================================
# 2. 机构调研 (通过东方财富)
# ===========================================================================
def get_org_research(code, name):
    result = {"code": code, "name": name, "research": None}
    # stock_org_research_em doesn't exist in current akshare version
    # Try stock_research_report_em
    try:
        df = ak.stock_research_report_em(symbol=code)
        if df is not None and not df.empty:
            records = []
            for _, row in df.head(10).iterrows():
                record = {col: safe_json(row[col]) for col in row.index}
                records.append(record)
            result["research"] = records
            print(f"  [OK] {name} 研报/调研: {len(records)}条")
    except Exception as e:
        print(f"  [INFO] {name} 研报: {e}")

    return result


# ===========================================================================
# 3. 资金流向 (通过stock_fund_flow_individual - 可能被block)
# ===========================================================================
def get_fund_flow(code, name, market):
    result = {"code": code, "name": name, "fund_flow": None}
    try:
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is not None and not df.empty:
            df = df.sort_values('日期', ascending=False).reset_index(drop=True) if '日期' in df.columns else df

            # Identify net flow columns
            cols = df.columns.tolist()
            fund_data = {}
            for period, days in [("近5日", 5), ("近10日", 10), ("近20日", 20)]:
                subset = df.head(min(days, len(df)))
                # Find main force net flow column
                main_col = None
                for c in cols:
                    if '主力净流入' in c or '主力净额' in c:
                        main_col = c
                        break
                # Find all fund flow columns to show
                flow_cols = [c for c in cols if '净流入' in c or '净额' in c]
                period_data = {}
                for c in flow_cols:
                    total = safe_float(subset[c].sum())
                    period_data[c] = total
                period_data['数据天数'] = len(subset)
                fund_data[period] = period_data

            # Latest day detail
            latest = df.iloc[0] if len(df) > 0 else None
            if latest is not None:
                fund_data["最新交易日"] = safe_json(latest.get('日期', 'N/A'))
                fund_data["最新日详情"] = {col: safe_json(latest[col]) for col in cols[:15]}

            result["fund_flow"] = fund_data
            print(f"  [OK] {name} 资金流向: {len(df)}天数据")
        else:
            print(f"  [INFO] {name} 资金流向数据为空")
    except Exception as e:
        print(f"  [WARN] {name} 资金流向: {type(e).__name__}")

    return result


# ===========================================================================
# 4. 技术面数据 (通过baostock获取K线)
# ===========================================================================
def get_technical_data_baostock(code, name, bs_code):
    result = {"code": code, "name": name}
    try:
        bs.login()
        rs = bs.query_history_k_data_plus(
            bs_code,
            'date,code,open,high,low,close,preclose,volume,amount,turn,pctChg',
            start_date=date_start_bs,
            end_date=date_end_bs,
            frequency='d', adjustflag='2'
        )
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        bs.logout()

        if not rows:
            print(f"  [WARN] {name} baostock无数据")
            result["technical"] = {"error": "无K线数据"}
            return result

        df = pd.DataFrame(rows, columns=['date','code','open','high','low','close','preclose','volume','amount','turn','pctChg'])
        for col in ['open','high','low','close','volume','amount','turn','pctChg']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        df = df.sort_values('date').reset_index(drop=True)
        close = df['close']

        latest = df.iloc[-1]
        latest_close = safe_float(latest['close'])
        latest_open = safe_float(latest['open'])
        latest_high = safe_float(latest['high'])
        latest_low = safe_float(latest['low'])
        latest_volume = safe_int(latest['volume'])
        latest_amount = safe_float(latest['amount'])
        latest_pct_chg = safe_float(latest['pctChg'])
        latest_turn = safe_float(latest['turn'])

        # 均线
        ma5 = compute_ma(close, 5)
        ma10 = compute_ma(close, 10)
        ma20 = compute_ma(close, 20)
        ma60 = compute_ma(close, 60)

        # MACD
        dif, dea, macd_bar = compute_macd(close)

        # RSI
        rsi6 = compute_rsi(close, 6)
        rsi12 = compute_rsi(close, 12)

        # 成交量比
        vol_series = df['volume']
        vol_ma5 = vol_series.tail(5).mean() if len(vol_series) >= 5 else None
        vol_ratio = round(float(latest_volume / vol_ma5), 2) if vol_ma5 and vol_ma5 > 0 and latest_volume else None

        # 支撑/压力位
        support, resistance = find_support_resistance(df)

        # 涨跌幅
        chg5 = round(float((close.iloc[-1] / close.iloc[-min(6, len(close))] - 1) * 100), 2) if len(close) >= 6 else None
        chg10 = round(float((close.iloc[-1] / close.iloc[-min(11, len(close))] - 1) * 100), 2) if len(close) >= 11 else None
        chg20 = round(float((close.iloc[-1] / close.iloc[-min(21, len(close))] - 1) * 100), 2) if len(close) >= 21 else None

        tech = {
            "最新数据": {
                "日期": latest['date'],
                "开盘": latest_open,
                "收盘": latest_close,
                "最高": latest_high,
                "最低": latest_low,
                "涨跌幅(%)": latest_pct_chg,
                "换手率(%)": latest_turn,
                "成交量(手)": latest_volume,
                "成交额(元)": latest_amount,
            },
            "均线": {
                "MA5": ma5,
                "MA10": ma10,
                "MA20": ma20,
                "MA60": ma60,
            },
            "MACD": {
                "DIF": dif,
                "DEA": dea,
                "MACD柱": macd_bar,
            },
            "RSI": {
                "RSI6": rsi6,
                "RSI12": rsi12,
            },
            "成交量比(量比)": vol_ratio,
            "涨跌幅": {
                "近5日涨跌幅(%)": chg5,
                "近10日涨跌幅(%)": chg10,
                "近20日涨跌幅(%)": chg20,
            },
            "支撑压力位": {
                "近期支撑位(60日)": support,
                "近期压力位(60日)": resistance,
            },
            "数据覆盖": {
                "交易日数": len(df),
                "起始日期": df['date'].iloc[0],
                "截止日期": df['date'].iloc[-1],
            }
        }

        result["technical"] = tech
        print(f"  [OK] {name} 技术面: {len(df)}个交易日")

    except Exception as e:
        print(f"  [WARN] {name} 技术面获取失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        result["technical"] = {"error": str(e)}

    return result


# ===== 主流程 =====
print(f"🔄 开始批量获取 {len(stocks)} 只股票数据...")
print(f"📅 查询日期: {today_str}\n")

for s in stocks:
    code = s["code"]
    name = s["name"]
    market = s["market"]
    bs_code = s["bs_code"]
    print(f"===== {code} {name} =====")

    stock_data = {"code": code, "name": name, "market": market}

    # 1. 财务数据
    print(f"\n[1/4] 财务数据(业绩预告+最新财务摘要)...")
    fin = get_financial_data(code, name)
    stock_data["financial"] = fin

    # 2. 机构调研/研报
    print(f"\n[2/4] 机构调研/研报...")
    research = get_org_research(code, name)
    stock_data["research"] = research

    # 3. 资金流向
    print(f"\n[3/4] 资金流向(近5/10/20日)...")
    fund = get_fund_flow(code, name, market)
    stock_data["fund_flow"] = fund

    # 4. 技术面
    print(f"\n[4/4] 技术面(K线+均线+MACD+RSI+支撑压力)...")
    tech = get_technical_data_baostock(code, name, bs_code)
    stock_data["technical"] = tech

    results.append(stock_data)
    print(f"\n✅ {name} 完成\n")
    print("=" * 70)
    print()

# 输出JSON
output = {
    "query_date": today_str,
    "total_stocks": len(results),
    "stocks": results,
}

json_str = json.dumps(output, ensure_ascii=False, indent=2, default=str)

# Save
with open("/Users/duguke/.openclaw/workspace/stock_batch1_result.json", "w", encoding="utf-8") as f:
    f.write(json_str)

print(f"\n📊 结果已保存到 stock_batch1_result.json")
print(f"📏 JSON 大小: {len(json_str)} 字符")
