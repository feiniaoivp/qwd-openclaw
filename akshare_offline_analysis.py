#!/usr/bin/env python3
"""
AkShare A股离线数据分析工具
获取历史K线、财务报表、基本面指标、资金流向等数据，保存为本地文件供离线分析
"""

import akshare as ak
import pandas as pd
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
import argparse
import time
import json

# 数据保存根目录
DATA_ROOT = Path(__file__).parent / "akshare_data"
DATA_ROOT.mkdir(exist_ok=True)

def get_stock_dir(symbol: str) -> Path:
    """获取股票专属数据目录"""
    stock_dir = DATA_ROOT / symbol
    stock_dir.mkdir(exist_ok=True)
    return stock_dir

def save_df(df: pd.DataFrame, path: Path, description: str):
    """保存DataFrame到CSV并打印状态"""
    if df is not None and not df.empty:
        df.to_csv(path, index=False, encoding='utf-8-sig')
        print(f"  ✓ {description}: {len(df)} 行 -> {path.name}")
        return True
    else:
        print(f"  ✗ {description}: 无数据")
        return False

def fetch_historical_kline(symbol: str, stock_dir: Path, start_date: str, end_date: str):
    """获取历史K线数据（日/周/月，前复权/后复权/不复权）"""
    print(f"\n📈 获取历史K线数据: {symbol}")
    
    periods = {
        "daily": "日K线",
        "weekly": "周K线", 
        "monthly": "月K线"
    }
    
    adjusts = {
        "qfq": "前复权",
        "hfq": "后复权",
        "": "不复权"
    }
    
    for period_code, period_name in periods.items():
        for adjust_code, adjust_name in adjusts.items():
            success = False
            for attempt in range(3):  # 重试3次
                try:
                    df = ak.stock_zh_a_hist(
                        symbol=symbol,
                        period=period_code,
                        start_date=start_date,
                        end_date=end_date,
                        adjust=adjust_code if adjust_code else None
                    )
                    suffix = f"{period_code}_{adjust_name}" if adjust_code else f"{period_code}_不复权"
                    if save_df(df, stock_dir / f"kline_{suffix}.csv", f"{period_name}({adjust_name})"):
                        success = True
                    break
                except Exception as e:
                    if attempt < 2:
                        print(f"  ⚠ {period_name}({adjust_name}) 第{attempt+1}次失败，重试中... {e}")
                        time.sleep(1 * (attempt + 1))
                    else:
                        print(f"  ✗ {period_name}({adjust_name}) 失败: {e}")
            time.sleep(0.3)  # 避免请求过快

def fetch_financial_data(symbol: str, stock_dir: Path):
    """获取财务数据"""
    print(f"\n💰 获取财务数据: {symbol}")
    
    # 1. 财务报表摘要 (同花顺)
    try:
        df = ak.stock_financial_abstract_ths(symbol=symbol, indicator="按报告期")
        save_df(df, stock_dir / "financial_abstract_ths.csv", "财务报表摘要(同花顺)")
        time.sleep(0.3)
    except Exception as e:
        print(f"  ✗ 财务报表摘要(同花顺) 失败: {e}")
    
    # 2. 主要财务指标分析
    try:
        df = ak.stock_financial_analysis_indicator(symbol=symbol)
        save_df(df, stock_dir / "financial_analysis_indicator.csv", "主要财务指标分析")
        time.sleep(0.3)
    except Exception as e:
        print(f"  ✗ 主要财务指标分析 失败: {e}")
    
    # 3. 新浪财务报表 (需要stock前缀: sh/sz + 代码)
    stock_prefix = f"sh{symbol}" if symbol.startswith("6") else f"sz{symbol}"
    reports = [
        ("资产负债表", "financial_zcfzb.csv", "资产负债表(新浪)"),
        ("利润表", "financial_lrb.csv", "利润表(新浪)"),
        ("现金流量表", "financial_xjllb.csv", "现金流量表(新浪)")
    ]
    for report_symbol, filename, desc in reports:
        try:
            df = ak.stock_financial_report_sina(stock=stock_prefix, symbol=report_symbol)
            save_df(df, stock_dir / filename, desc)
            time.sleep(0.3)
        except Exception as e:
            print(f"  ✗ {desc} 失败: {e}")

def fetch_basic_info(symbol: str, stock_dir: Path):
    """获取基本面/公司概况信息"""
    print(f"\n🏢 获取基本面信息: {symbol}")
    
    # 1. 个股基本信息 (东方财富)
    for attempt in range(3):
        try:
            df = ak.stock_individual_info_em(symbol=symbol)
            if save_df(df, stock_dir / "basic_info_em.csv", "个股基本信息(东方财富)"):
                break
        except Exception as e:
            if attempt < 2:
                print(f"  ⚠ 个股基本信息 第{attempt+1}次失败，重试中... {e}")
                time.sleep(1 * (attempt + 1))
            else:
                print(f"  ✗ 个股基本信息 失败: {e}")
    time.sleep(0.3)
    
    # 2. 基本面数据
    try:
        pass
    except Exception as e:
        print(f"  ✗ 基本面指标 失败: {e}")

def fetch_fund_flow(symbol: str, stock_dir: Path):
    """获取资金流向数据"""
    print(f"\n💸 获取资金流向: {symbol}")
    
    # 判断市场类型
    market = "sh" if symbol.startswith("6") else "sz"
    
    for attempt in range(3):
        try:
            df = ak.stock_individual_fund_flow(stock=symbol, market=market)
            if save_df(df, stock_dir / "fund_flow.csv", "个股资金流向"):
                break
        except Exception as e:
            if attempt < 2:
                print(f"  ⚠ 资金流向 第{attempt+1}次失败，重试中... {e}")
                time.sleep(1 * (attempt + 1))
            else:
                print(f"  ✗ 资金流向 失败: {e}")
    time.sleep(0.3)

def fetch_dragon_tiger(symbol: str, stock_dir: Path, days: int = 30):
    """获取龙虎榜数据（最近N天）"""
    print(f"\n🐉 获取龙虎榜数据: {symbol}")
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    try:
        # 这里需要按日期查询，尝试获取最近几天的龙虎榜
        for i in range(min(days, 10)):  # 只查最近10天避免太多请求
            date_str = (end_date - timedelta(days=i)).strftime("%Y%m%d")
            try:
                df = ak.stock_lhb_detail_em(date=date_str)
                if df is not None and not df.empty:
                    # 筛选该股票
                    stock_df = df[df['代码'] == symbol]
                    if not stock_df.empty:
                        save_df(stock_df, stock_dir / f"lhb_{date_str}.csv", f"龙虎榜({date_str})")
                        break  # 找到最近一次即可
                time.sleep(0.2)
            except:
                continue
    except Exception as e:
        print(f"  ✗ 龙虎榜 失败: {e}")

def fetch_margin_data(symbol: str, stock_dir: Path):
    """获取融资融券数据"""
    print(f"\n📊 获取融资融券: {symbol}")
    
    # stock_margin_sse 需要 start_date 和 end_date
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
    
    for attempt in range(3):
        try:
            df = ak.stock_margin_sse(start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                # 筛选该股票
                stock_df = df[df['代码'] == symbol] if '代码' in df.columns else df
                if not stock_df.empty:
                    save_df(stock_df, stock_dir / "margin_sse.csv", "融资融券(上交所)")
                    break
            time.sleep(0.3)
        except Exception as e:
            if attempt < 2:
                print(f"  ⚠ 融资融券 第{attempt+1}次失败，重试中... {e}")
                time.sleep(1 * (attempt + 1))
            else:
                print(f"  ✗ 融资融券 失败: {e}")
    time.sleep(0.3)

def fetch_dividend_data(symbol: str, stock_dir: Path):
    """获取分红配股数据"""
    print(f"\n🎁 获取分红配股: {symbol}")
    
    # stock_fhps_em 需要 date 参数，获取最新年份
    from datetime import datetime
    current_year = datetime.now().year
    for year in range(current_year, current_year - 5, -1):
        date_str = f"{year}1231"
        try:
            df = ak.stock_fhps_em(date=date_str)
            if df is not None and not df.empty:
                # 筛选该股票
                stock_df = df[df['代码'] == symbol] if '代码' in df.columns else df
                if not stock_df.empty:
                    save_df(stock_df, stock_dir / "dividend_fhps.csv", f"分红配股详情({year}年)")
                    break
            time.sleep(0.2)
        except Exception as e:
            print(f"  ✗ 分红配股({year}年) 失败: {e}")
    time.sleep(0.3)

def fetch_news_data(symbol: str, stock_dir: Path):
    """获取新闻资讯"""
    print(f"\n📰 获取新闻资讯: {symbol}")
    
    try:
        df = ak.stock_news_em(symbol=symbol)
        save_df(df, stock_dir / "news_em.csv", "个股新闻(东方财富)")
        time.sleep(0.3)
    except Exception as e:
        print(f"  ✗ 新闻资讯 失败: {e}")

def generate_summary_report(symbol: str, stock_dir: Path):
    """生成汇总分析报告"""
    print(f"\n📋 生成汇总报告: {symbol}")
    
    report = {
        "symbol": symbol,
        "generated_at": datetime.now().isoformat(),
        "data_files": [],
        "basic_stats": {}
    }
    
    # 扫描所有CSV文件
    for csv_file in stock_dir.glob("*.csv"):
        try:
            df = pd.read_csv(csv_file)
            report["data_files"].append({
                "file": csv_file.name,
                "rows": len(df),
                "columns": list(df.columns),
                "size_kb": round(csv_file.stat().st_size / 1024, 2)
            })
            
            # 如果是日K线前复权，计算一些基础统计
            if "kline_daily_前复权" in csv_file.name:
                if '收盘' in df.columns:
                    closes = pd.to_numeric(df['收盘'], errors='coerce').dropna()
                    if len(closes) > 0:
                        report["basic_stats"]["price"] = {
                            "latest": float(closes.iloc[-1]),
                            "high_1y": float(closes.max()),
                            "low_1y": float(closes.min()),
                            "change_1y_pct": round((closes.iloc[-1] / closes.iloc[0] - 1) * 100, 2)
                        }
        except Exception as e:
            print(f"  读取 {csv_file.name} 失败: {e}")
    
    # 保存汇总报告
    summary_path = stock_dir / "SUMMARY.json"
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"  ✓ 汇总报告 -> {summary_path.name}")
    print(f"  共 {len(report['data_files'])} 个数据文件")

def analyze_stock(symbol: str, start_date: str = None, end_date: str = None, 
                  include_financial: bool = True, include_fund_flow: bool = True,
                  include_lhb: bool = True, include_margin: bool = True,
                  include_dividend: bool = True, include_news: bool = True):
    """主分析函数：获取某只股票的全维度数据"""
    
    # 默认日期范围：最近1年
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
    
    print("=" * 60)
    print(f"🚀 开始离线数据采集: {symbol}")
    print(f"📅 日期范围: {start_date} ~ {end_date}")
    print(f"📁 保存目录: {DATA_ROOT / symbol}")
    print("=" * 60)
    
    stock_dir = get_stock_dir(symbol)
    
    # 1. 历史K线数据（核心）
    fetch_historical_kline(symbol, stock_dir, start_date, end_date)
    
    # 2. 基本面信息
    fetch_basic_info(symbol, stock_dir)
    
    # 3. 财务数据
    if include_financial:
        fetch_financial_data(symbol, stock_dir)
    
    # 4. 资金流向
    if include_fund_flow:
        fetch_fund_flow(symbol, stock_dir)
    
    # 5. 龙虎榜
    if include_lhb:
        fetch_dragon_tiger(symbol, stock_dir)
    
    # 6. 融资融券
    if include_margin:
        fetch_margin_data(symbol, stock_dir)
    
    # 7. 分红配股
    if include_dividend:
        fetch_dividend_data(symbol, stock_dir)
    
    # 8. 新闻资讯
    if include_news:
        fetch_news_data(symbol, stock_dir)
    
    # 9. 生成汇总报告
    generate_summary_report(symbol, stock_dir)
    
    print("\n" + "=" * 60)
    print(f"✅ 完成: {symbol}")
    print(f"📂 数据位置: {stock_dir}")
    print("=" * 60)

def batch_analyze(symbols: list, **kwargs):
    """批量分析多只股票"""
    print(f"\n📦 批量处理 {len(symbols)} 只股票...")
    for i, symbol in enumerate(symbols, 1):
        print(f"\n[{i}/{len(symbols)}] ", end="")
        try:
            analyze_stock(symbol, **kwargs)
        except Exception as e:
            print(f"❌ {symbol} 处理失败: {e}")
        time.sleep(1)  # 批量间隔

def main():
    parser = argparse.ArgumentParser(description="AkShare A股离线数据分析工具")
    parser.add_argument("symbols", nargs="+", help="股票代码，如 000001 600519 300750")
    parser.add_argument("--start", default=None, help="开始日期 YYYYMMDD")
    parser.add_argument("--end", default=None, help="结束日期 YYYYMMDD")
    parser.add_argument("--no-financial", action="store_true", help="跳过财务数据")
    parser.add_argument("--no-fund-flow", action="store_true", help="跳过资金流向")
    parser.add_argument("--no-lhb", action="store_true", help="跳过龙虎榜")
    parser.add_argument("--no-margin", action="store_true", help="跳过融资融券")
    parser.add_argument("--no-dividend", action="store_true", help="跳过分红配股")
    parser.add_argument("--no-news", action="store_true", help="跳过新闻资讯")
    
    args = parser.parse_args()
    
    batch_analyze(
        symbols=args.symbols,
        start_date=args.start,
        end_date=args.end,
        include_financial=not args.no_financial,
        include_fund_flow=not args.no_fund_flow,
        include_lhb=not args.no_lhb,
        include_margin=not args.no_margin,
        include_dividend=not args.no_dividend,
        include_news=not args.no_news
    )

if __name__ == "__main__":
    main()