import sys, os
sys.path.insert(0, "/Users/duguke/.openclaw/workspace/skills/gh-data")
from ghdata import data_fetcher as fetcher
from ghdata import db_manager as db
from ghdata import reporter
from ghdata import chart
from ghdata import config
import pandas as pd
import numpy as np

# 确保输出目录存在
config.DOC_DIR = "./doc"
os.makedirs(config.DOC_DIR, exist_ok=True)
print(f"[Config] Report directory set to: {os.path.abspath(config.DOC_DIR)}")

class MultiDimensionStrategy:
    def __init__(
        self,
        fast_ma=10,
        slow_ma=30,
        rsi_period=14,
        rsi_buy_thresh=40,
        rsi_sell_thresh=70,
        stop_loss_pct=0.05,
    ):
        """初始化策略参数（融合趋势、超买超卖、风控维度）"""
        self.fast_ma = fast_ma
        self.slow_ma = slow_ma
        self.rsi_period = rsi_period
        self.rsi_buy_thresh = rsi_buy_thresh  # RSI低位（超卖区域具备反弹动能）
        self.rsi_sell_thresh = rsi_sell_thresh  # RSI高位（超买区域注意风险）
        self.stop_loss_pct = stop_loss_pct  # 硬性止损线：5%

    def calculate_indicators(self, df):
        """维度一：计算技术与趋势指标"""
        # 1. 均线趋势因子
        df["MA_Fast"] = df["close"].rolling(window=self.fast_ma).mean()
        df["MA_Slow"] = df["close"].rolling(window=self.slow_ma).mean()

        # 2. MACD 趋势动量因子
        exp1 = df["close"].ewm(span=12, adjust=False).mean()
        exp2 = df["close"].ewm(span=26, adjust=False).mean()
        df["MACD"] = exp1 - exp2
        df["Signal_Line"] = df["MACD"].ewm(span=9, adjust=False).mean()

        # 3. RSI 超买超卖因子
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs = gain / (loss + 1e-9)
        df["RSI"] = 100 - (100 / (1 + rs))

        return df

    def run_backtest(self, df):
        """传入标准的包含 date, close 的 DataFrame，返回带有信号和统计的结果"""
        df = self.calculate_indicators(df)
        df["Signal"] = 0  # 1: 买入, -1: 卖出, 0: 持仓/观望

        # 引入跟踪变量用于风控
        in_position = False
        buy_price = 0.0

        for i in range(1, len(df)):
            current_close = df.loc[i, "close"]

            # --- 维度四：风险控制（硬性止损线检查） ---
            if in_position:
                if current_close <= buy_price * (1 - self.stop_loss_pct):
                    df.loc[i, "Signal"] = -1  # 触发止损卖出
                    in_position = False
                    continue

            # --- 买入逻辑（趋势金叉 + RSI未超买） ---
            # 1. 均线金叉 2. MACD为正 3. RSI处于安全买入区
            if (
                not in_position
                and df.loc[i, "MA_Fast"] > df.loc[i, "MA_Slow"]
                and df.loc[i - 1, "MA_Fast"] <= df.loc[i - 1, "MA_Slow"]
            ):
                if (
                    df.loc[i, "MACD"] > df.loc[i, "Signal_Line"]
                    and df.loc[i, "RSI"] < self.rsi_sell_thresh
                ):
                    df.loc[i, "Signal"] = 1
                    buy_price = current_close
                    in_position = True

            # --- 卖出逻辑（趋势死叉 或 RSI极度超买） ---
            elif in_position:
                # 均线死叉 或 RSI冲高回落触及超买减仓线
                if (
                    df.loc[i, "MA_Fast"] < df.loc[i, "MA_Slow"]
                    or df.loc[i, "RSI"] > self.rsi_sell_thresh
                ):
                    df.loc[i, "Signal"] = -1
                    in_position = False

        return df

def collect_all(code: str) -> dict:
    data = {}
    try:
        data["realtime"] = fetcher.fetch_realtime(code)
        data["kline"] = fetcher.fetch_kline(code, 120)
        data["tick"] = fetcher.fetch_today_tick(code)
        data["moneyFlow"] = fetcher.fetch_money_flow(code, 10)
        data["margin"] = fetcher.fetch_margin_trading(code, 10)
        data["holdings"] = fetcher.fetch_main_holdings(code)
        data["shareholder"] = fetcher.fetch_shareholder_trade(code)
        data["executive"] = fetcher.fetch_executive_change(code)
        data["financial"] = fetcher.fetch_financial(code)
        data["balance"] = fetcher.fetch_balance_sheet(code)
        data["income"] = fetcher.fetch_income_statement(code)
        data["cashflow"] = fetcher.fetch_cashflow(code)
        data["dividend"] = fetcher.fetch_dividend(code)
        data["research"] = fetcher.fetch_research_report(code)
        data["survey"] = fetcher.fetch_institutional_survey(code)
        data["unlock"] = fetcher.fetch_unlock_data(code)
        data["industry"] = fetcher.fetch_industry_info(code)
    except Exception as e:
        print(f"[ERROR] Data collection failed for {code}: {e}")
        # Return partial data if some succeeded
        return data
    return data

# 待处理的股票列表（可以修改为您需要的任何列表）
stock_codes = [
    "600160", "300827", "600346", "000708", "300748", "002413", "601061", "900925",
    "000157", "603308", "601865", "600660", "002318", "002335", "605566", "601995",
    "600030", "601066", "000987", "601100", "600570", "300124", "300719", "002466",
    "300014", "600036", "300285", "688981", "600584", "002156"
]

print(f"Processing {len(stock_codes)} stocks with Multi-Dimensional Strategy...")
print("=" * 60)

successful = 0
failed = 0

for i, stock_code in enumerate(stock_codes, 1):
    print(f"\n[{i}/{len(stock_codes)}] Processing {stock_code}...")
    try:
        # 1. 数据采集
        all_data = collect_all(stock_code)
        if not all_data.get("kline"):
            print(f"[WARNING] {stock_code}: No K-line data, skipping")
            failed += 1
            continue

        # 2. 转换K线数据为DataFrame
        klines = all_data["kline"]
        df = pd.DataFrame(klines)
        # 确保必要的列存在并重命名以匹配策略期望的格式
        # gh-data的K线数据格式：date, open, close, high, low, volume
        # 策略期望：close列（我们将使用gh-data的close列）
        if 'close' not in df.columns:
            print(f"[ERROR] {stock_code}: K-line data missing 'close' column")
            failed += 1
            continue
            
        # 确保日期列存在并排序（策略假设数据是按时间排序的）
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
        
        print(f"  Data points: {len(df)}")

        # 3. 应用多维度策略
        strategy = MultiDimensionStrategy()
        processed_df = strategy.run_backtest(df.copy())  # 调用新的run_backtest方法
        
        # 4. 分析交易信号
        buy_signals = processed_df[processed_df["Signal"] == 1]
        sell_signals = processed_df[processed_df["Signal"] == -1]
        
        print(f"  Buy signals: {len(buy_signals)}, Sell signals: {len(sell_signals)}")

        # 5. 生成K线图（使用原始数据，但我们可以考虑在未来添加信号标记）
        chart_file = chart.generate(code=stock_code, days=60)
        
        # 6. 生成DOCX报告
        report_file = reporter.generate(stock_code)
        
        if chart_file and report_file:
            print(f"[SUCCESS] {stock_code}: Chart and report generated")
            successful += 1
        else:
            print(f"[WARNING] {stock_code}: Chart or report generation failed")
            failed += 1
        
    except Exception as e:
        print(f"[ERROR] Failed to process {stock_code}: {e}")
        import traceback
        traceback.print_exc()
        failed += 1

print("\n" + "=" * 60)
print(f"Strategy-integrated processing completed!")
print(f"Successful: {successful}")
print(f"Failed: {failed}")
print(f"Reports saved in: {os.path.abspath(config.DOC_DIR)}")