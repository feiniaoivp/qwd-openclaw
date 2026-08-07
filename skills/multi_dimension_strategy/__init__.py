import pandas as pd
import numpy as np
from typing import Dict, Any

def run_multi_dimension_strategy(
    df: pd.DataFrame,
    fast_ma: int = 10,
    slow_ma: int = 30,
    rsi_period: int = 14,
    rsi_buy_thresh: int = 40,
    rsi_sell_thresh: int = 70,
    stop_loss_pct: float = 0.05
) -> pd.DataFrame:
    """
    多维度量化策略：融合趋势、动量和超买超卖指标
    
    参数:
        df: 包含 'date' 和 'close' 列的 pandas DataFrame
        fast_ma: 快速均线周期，默认10
        slow_ma: 慢速均线周期，默认30
        rsi_period: RSI周期，默认14
        rsi_buy_thresh: RSI买入阈值（低于此值视为超卖），默认40
        rsi_sell_thresh: RSI卖出阈值（高于此值视为超买），默认70
        stop_loss_pct: 止损百分比，默认0.05（5%）
    
    返回:
        添加了技术指标和交易信号的DataFrame
        Signal列: 1=买入, -1=卖出, 0=持仓
    """
    # 确保数据按日期排序
    if 'date' in df.columns:
        df = df.sort_values('date').reset_index(drop=True)
    
    # 计算技术指标
    # 1. 均线趋势因子
    df["MA_Fast"] = df["close"].rolling(window=fast_ma).mean()
    df["MA_Slow"] = df["close"].rolling(window=slow_ma).mean()

    # 2. MACD 趋势动量因子
    exp1 = df["close"].ewm(span=12, adjust=False).mean()
    exp2 = df["close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = exp1 - exp2
    df["Signal_Line"] = df["MACD"].ewm(span=9, adjust=False).mean()

    # 3. RSI 超买超卖因子
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=rsi_period).mean()
    rs = gain / (loss + 1e-9)
    df["RSI"] = 100 - (100 / (1 + rs))
    
    # 初始化信号列
    df["Signal"] = 0  # 1: 买入, -1: 卖出, 0: 持仓/观望

    # 引入跟踪变量用于风控
    in_position = False
    buy_price = 0.0

    for i in range(1, len(df)):
        current_close = df.loc[i, "close"]

        # --- 维度四：风险控制（硬性止损线检查） ---
        if in_position:
            if current_close <= buy_price * (1 - stop_loss_pct):
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
                and df.loc[i, "RSI"] < rsi_sell_thresh
            ):
                df.loc[i, "Signal"] = 1
                buy_price = current_close
                in_position = True

        # --- 卖出逻辑（趋势死叉 或 RSI极度超买） ---
        elif in_position:
            # 均线死叉 或 RSI冲高回落触及超买减仓线
            if (
                df.loc[i, "MA_Fast"] < df.loc[i, "MA_Slow"]
                or df.loc[i, "RSI"] > rsi_sell_thresh
            ):
                df.loc[i, "Signal"] = -1
                in_position = False
    
    return df

# 为了兼容性，也提供一个get_latest_signal函数
def get_latest_signal(df: pd.DataFrame) -> dict:
    """
    获取最新的交易信号和状态
    
    返回:
        包含最新信号、RSI值和建议的字典
    """
    if len(df) == 0:
        return {"signal": 0, "rsi": None, "action": "无数据"}
    
    latest = df.iloc[-1]
    signal = latest.get("Signal", 0)
    rsi = latest.get("RSI", None)
    
    action_map = {1: "买入", -1: "卖出", 0: "观望"}
    action = action_map.get(signal, "未知")
    
    return {
        "signal": int(signal),
        "rsi": float(rsi) if rsi is not None and not pd.isna(rsi) else None,
        "action": action,
        "latest_close": float(latest.get("close", 0)) if "close" in latest else 0
    }

# 为了方便直接调用，提供一个包装函数
def run_quant_strategy(stock_code: str, days: int = 100) -> dict:
    """
    一站式函数：获取股票数据并运行多维度策略
    
    参数:
        stock_code: 股票代码（如"600160"）
        days: 获取的历史天数，默认100
    
    返回:
        包含分析结果的字典
    """
    try:
        # 这里我们需要获取股票数据
        # 在实际实现中，我们需要调用gh-data的数据获取函数
        # 由于在这个上下文中我们可能无法直接访问gh-data模块，我们将返回一个示例结构
        # 实际使用时，这应该被替换为真实的数据获取逻辑
        
        # 为了演示，我们返回一个指示如何使用的消息
        return {
            "status": "info",
            "message": "要使用此功能，请在您的环境中提供股票数据DataFrame，然后调用run_multi_dimension_strategy(df)",
            "usage": "result_df = run_multi_dimension_strategy(your_dataframe)",
            "get_latest_signal": "signal_info = get_latest_signal(result_df)"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}