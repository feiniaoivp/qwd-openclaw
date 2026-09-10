#!/usr/bin/env python3
"""
市场状态识别模块
==================
判断大盘处于：趋势上涨 / 震荡 / 趋势下跌 / 危机
供自适应策略切换使用
"""

import os, sys, json, time, warnings
from datetime import datetime, timedelta
import pandas as pd
import pandas_ta as ta
import baostock as bs
import numpy as np
import urllib.request

warnings.filterwarnings("ignore")

WORKSPACE = "/Users/duguke/.openclaw/workspace"


# ════════════════════════════════════════
# 数据获取
# ════════════════════════════════════════

def fetch_index_data(index_code, start_date, end_date, max_retry=3):
    """从 baostock 拉取指数日线数据（不复权）"""
    for attempt in range(max_retry):
        try:
            lg = bs.login()
            if lg.error_code != "0":
                raise ConnectionError(f"baostock登录失败: {lg.error_msg}")

            rs = bs.query_history_k_data_plus(
                index_code,
                "date,open,close,high,low,volume,amount",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="3"  # 3=不复权
            )
            data = []
            while rs.next():
                data.append(rs.get_row_data())
            bs.logout()

            if not data:
                raise ValueError("空数据")

            df = pd.DataFrame(data, columns=["date", "open", "close", "high", "low", "volume", "amount"])
            for col in ["open", "close", "high", "low", "volume", "amount"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            df = df.dropna()

            if len(df) < 2:
                raise ValueError(f"数据不足: {len(df)}条")

            return df

        except Exception as e:
            print(f"  ⚠️ 指数 {index_code} 获取失败(尝试{attempt+1}/{max_retry}): {e}")
            time.sleep(1)
        finally:
            try:
                bs.logout()
            except:
                pass

    print(f"  ❌ 指数 {index_code} 所有尝试均失败")
    return None


def fetch_sina_realtime_index(index_code="sh000300"):
    """新浪实时指数行情（用于获取最新价）"""
    try:
        url = f"https://hq.sinajs.cn/list={index_code}"
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        resp = urllib.request.urlopen(req, timeout=10)
        text = resp.read().decode("gbk")
        start = text.find('="')
        if start < 0:
            return None
        start += 2
        end = text.find('"', start)
        fields = text[start:end].split(",")
        if len(fields) < 4:
            return None
        return {
            "name": fields[0],
            "open": float(fields[1]),
            "prev_close": float(fields[2]),
            "cur": float(fields[3]),
            "high": float(fields[4]),
            "low": float(fields[5]),
            "volume": int(fields[8]) if fields[8] else 0,
            "amount": float(fields[9]) if fields[9] else 0,
            "date": datetime.now().strftime("%Y-%m-%d"),
        }
    except Exception as e:
        print(f"  ⚠️ 新浪实时指数获取失败: {e}")
        return None


def fetch_market_breadth():
    """获取市场宽度数据：涨跌停家数、上涨/下跌家数（新浪全市场接口）"""
    try:
        # 新浪全市场涨跌分布接口
        url = "https://hq.sinajs.cn/list=s_up,s_down,s_stop"
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        resp = urllib.request.urlopen(req, timeout=10)
        text = resp.read().decode("gbk")
        
        # 解析涨跌停数据
        up_count = down_count = stop_up_count = stop_down_count = 0
        for line in text.split(";\n"):
            if "s_up" in line:
                up_count = int(line.split('"')[1].split(",")[0]) if '"' in line else 0
            elif "s_down" in line:
                down_count = int(line.split('"')[1].split(",")[0]) if '"' in line else 0
            elif "s_stop" in line:
                # 涨跌停统计
                parts = line.split('"')[1].split(",") if '"' in line else []
                if len(parts) >= 4:
                    stop_up_count = int(parts[0])  # 涨停
                    stop_down_count = int(parts[1])  # 跌停
        
        total = up_count + down_count
        return {
            "up_count": up_count,
            "down_count": down_count,
            "stop_up_count": stop_up_count,
            "stop_down_count": stop_down_count,
            "up_ratio": up_count / total if total > 0 else 0.5,
            "stop_up_ratio": stop_up_count / max(1, up_count),
            "stop_down_ratio": stop_down_count / max(1, down_count),
        }
    except Exception as e:
        print(f"  ⚠️ 市场宽度获取失败: {e}")
        return None


def fetch_north_fund():
    """获取北向资金净流入（新浪接口/东方财富）"""
    try:
        url = "https://hq.sinajs.cn/list=s_nhgg"  # 沪股通
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        resp = urllib.request.urlopen(req, timeout=10)
        text = resp.read().decode("gbk")
        # 解析北向资金数据
        # 这里简化返回，实际需要更复杂的解析
        return {"net_inflow": 0, "available": False}
    except:
        return {"net_inflow": 0, "available": False}


# ════════════════════════════════════════
# 核心判定逻辑
# ════════════════════════════════════════

def calc_regime_indicators(df):
    """计算市场状态指标"""
    if df is None or len(df) < 60:
        return None

    close = df["close"].values
    volume = df["volume"].values
    high = df["high"].values
    low = df["low"].values

    # 1. MA60 斜率（趋势方向）
    ma60 = pd.Series(close).rolling(60).mean()
    ma60_slope = (ma60.iloc[-1] - ma60.iloc[-20]) / ma60.iloc[-20] * 100  # 20日斜率%

    # 2. MA20 vs MA60 相对位置
    ma20 = pd.Series(close).rolling(20).mean()
    ma20_above_ma60 = 1 if ma20.iloc[-1] > ma60.iloc[-1] else 0

    # 3. 价格距离 MA60 偏离度
    price_vs_ma60 = (close[-1] - ma60.iloc[-1]) / ma60.iloc[-1] * 100

    # 4. 成交量变化（近5日 vs 近20日均值）
    vol_5 = np.mean(volume[-5:])
    vol_20 = np.mean(volume[-20:])
    vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0

    # 5. 波动率（ATR%）
    atr = ta.atr(pd.Series(high), pd.Series(low), pd.Series(close), length=14)
    atr_pct = float(atr.iloc[-1] / close[-1] * 100) if not pd.isna(atr.iloc[-1]) else 0

    # 6. MACD 趋势强度
    macd_df = ta.macd(pd.Series(close), fast=12, slow=26, signal=9)
    macd_hist = macd_df["MACDh_12_26_9"] if "MACDh_12_26_9" in macd_df.columns else macd_df.iloc[:, 2]
    macd_trend = 1 if macd_hist.iloc[-1] > 0 else -1

    # 7. RSI 动量
    rsi = ta.rsi(pd.Series(close), length=14)
    rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50

    return {
        "ma60_slope": round(ma60_slope, 3),
        "ma20_above_ma60": ma20_above_ma60,
        "price_vs_ma60": round(price_vs_ma60, 2),
        "vol_ratio": round(vol_ratio, 2),
        "atr_pct": round(atr_pct, 2),
        "macd_trend": macd_trend,
        "rsi": round(rsi_val, 1),
        "close": round(close[-1], 2),
    }


def determine_regime(indicators, breadth=None, north_fund=None):
    """
    综合判定市场状态
    
    返回: regime字符串 + 置信度
    """
    if not indicators:
        return {"regime": "未知", "confidence": 0, "reason": "指标不足"}

    # 评分系统
    score_bull = 0    # 趋势上涨
    score_bear = 0    # 趋势下跌
    score_crisis = 0  # 危机
    reasons = []

    # === 趋势指标 ===
    if indicators["ma60_slope"] > 0.05:      # MA60 明显向上
        score_bull += 2
        reasons.append(f"MA60上斜({indicators['ma60_slope']:.2f}%)")
    elif indicators["ma60_slope"] < -0.05:   # MA60 明显向下
        score_bear += 2
        reasons.append(f"MA60下斜({indicators['ma60_slope']:.2f}%)")
    
    if indicators["ma20_above_ma60"]:
        score_bull += 1
        reasons.append("MA20>MA60")
    else:
        score_bear += 1
        reasons.append("MA20<MA60")

    if indicators["price_vs_ma60"] > 3:
        score_bull += 1
        reasons.append(f"价格显著>MA60({indicators['price_vs_ma60']:.1f}%)")
    elif indicators["price_vs_ma60"] < -5:
        score_bear += 1
        reasons.append(f"价格显著<MA60({indicators['price_vs_ma60']:.1f}%)")

    if indicators["macd_trend"] > 0:
        score_bull += 1
        reasons.append("MACD多头")
    else:
        score_bear += 1
        reasons.append("MACD空头")

    # === 成交量 ===
    if indicators["vol_ratio"] > 1.2:
        score_bull += 1
        reasons.append(f"放量({indicators['vol_ratio']:.1f}x)")
    elif indicators["vol_ratio"] < 0.8:
        score_bear += 1
        reasons.append(f"缩量({indicators['vol_ratio']:.1f}x)")

    # === 市场宽度 ===
    if breadth:
        if breadth["up_ratio"] > 0.6:
            score_bull += 2
            reasons.append(f"普涨({breadth['up_ratio']:.0%})")
        elif breadth["up_ratio"] < 0.35:
            score_bear += 2
            reasons.append(f"普跌({breadth['up_ratio']:.0%})")
        
        if breadth["stop_down_count"] > 50:
            score_crisis += 3
            reasons.append(f"跌停潮({breadth['stop_down_count']}家)")

    # === 波动率/风险 ===
    if indicators["atr_pct"] > 3.0:
        score_crisis += 1
        reasons.append(f"高波动(ATR%={indicators['atr_pct']:.1f}%)")

    # === RSI ===
    if indicators["rsi"] > 70:
        score_bull -= 1  # 过热扣分
        reasons.append(f"RSI过热({indicators['rsi']:.0f})")
    elif indicators["rsi"] < 30:
        score_bear -= 1  # 超卖减分
        reasons.append(f"RSI超卖({indicators['rsi']:.0f})")

    # === 综合判定 ===
    net_score = score_bull - score_bear
    
    if score_crisis >= 3:
        regime = "危机"
        confidence = min(0.9, 0.5 + score_crisis * 0.1)
    elif net_score >= 4:
        regime = "趋势上涨"
        confidence = min(0.9, 0.5 + net_score * 0.05)
    elif net_score <= -4:
        regime = "趋势下跌"
        confidence = min(0.9, 0.5 + abs(net_score) * 0.05)
    else:
        regime = "震荡"
        confidence = 0.6

    return {
        "regime": regime,
        "confidence": round(confidence, 2),
        "score_bull": score_bull,
        "score_bear": score_bear,
        "score_crisis": score_crisis,
        "reasons": reasons,
        "indicators": indicators,
        "breadth": breadth,
    }


def get_market_regime():
    """主入口：获取当前市场状态"""
    today = datetime.now().strftime("%Y-%m-%d")
    start_60d = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")  # 取更多用于MA60

    print(f"📊 正在识别市场状态 ({today})...")

    # 1. 拉取沪深300数据
    df = fetch_index_data("sh.000300", start_60d, today)
    
    # 如果baostock失败，用新浪实时补最新
    if df is not None:
        latest = df["date"].iloc[-1].strftime("%Y-%m-%d")
        if latest < today:
            rt = fetch_sina_realtime_index("sh000300")
            if rt:
                new_row = {
                    "date": pd.Timestamp(rt["date"]),
                    "open": rt["open"],
                    "close": rt["cur"],
                    "high": rt["high"],
                    "low": rt["low"],
                    "volume": rt["volume"],
                    "amount": rt["amount"],
                }
                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                print(f"  [补丁] 沪深300 新浪实时拼接: ¥{rt['cur']:.2f}")

    # 2. 计算指标
    indicators = calc_regime_indicators(df)

    # 3. 获取市场宽度
    breadth = fetch_market_breadth()

    # 4. 综合判定
    result = determine_regime(indicators, breadth)

    print(f"  ✅ 市场状态: {result['regime']} (置信度 {result['confidence']})")
    for r in result.get("reasons", []):
        print(f"     - {r}")

    return result


# ════════════════════════════════════════
# 策略映射表
# ════════════════════════════════════════

REGIME_STRATEGY_MAP = {
    "趋势上涨": {
        "preferred": ["ema_cross", "macd", "bollinger"],
        "avoid": ["kdj_cci"],
        "position_bias": 1.0,      # 满仓
        "desc": "强趋势跟踪为主，动量策略优先"
    },
    "震荡": {
        "preferred": ["kdj_cci", "ema_obv", "bollinger"],
        "avoid": ["ema_cross", "macd"],
        "position_bias": 0.6,      # 半仓
        "desc": "均值回归+量价共振，趋势策略易假突破"
    },
    "趋势下跌": {
        "preferred": ["bollinger", "ema_obv"],
        "avoid": ["ema_cross", "macd", "kdj_cci"],
        "position_bias": 0.2,      # 轻仓/空仓
        "desc": "仅做反弹/超跌反抽，严控回撤"
    },
    "危机": {
        "preferred": [],
        "avoid": ["ema_cross", "macd", "kdj_cci", "ema_obv", "bollinger"],
        "position_bias": 0.0,      # 全空
        "desc": "全空规避，等待企稳信号"
    },
}


def get_strategy_bias(regime):
    """根据市场状态返回策略偏好配置"""
    return REGIME_STRATEGY_MAP.get(regime, REGIME_STRATEGY_MAP["震荡"])


if __name__ == "__main__":
    result = get_market_regime()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))