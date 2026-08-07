import numpy as np
import pandas as pd

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

    def generate_signals(self, df):
        """维度二 & 三：多维度信号共振逻辑"""
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

# 示例运行函数（非必需，可用于测试）
def run_example():
    """运行策略示例使用模拟数据"""
    # 模拟一份K线数据 (包含100天的价格随机波动)
    np.random.seed(42)
    price_changes = np.random.normal(0.001, 0.02, 100)
    initial_price = 100
    prices = initial_price * (1 + np.cumsum(price_changes))

    mock_data = pd.DataFrame({"close": prices})

    # 初始化策略并生成信号
    strategy = MultiDimensionStrategy()
    processed_df = strategy.generate_signals(mock_data)

    # 过滤出有交易信号的日子查看
    trade_signals = processed_df[processed_df["Signal"] != 0][
        ["close", "RSI", "Signal"]
    ]
    return trade_signals.tail(10)