#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下周（11-03 至 11-07）盘中预警监控脚本
标的：招商银行(600036)、长电科技(600584)
用法：python3 analysis/intraday_alerts_600036_600584.py
依赖：akshare、pandas、requests
输出：控制台实时预警 + 可选 Telegram 推送
"""

import os
import sys
import time
import json
import shutil
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import requests

# ============ 配置区 ============
# 关键价位（来源：周线级别深度分析 + 盘中预警配置）
STOCK_CONFIG = {
    "600036": {
        "name": "招商银行",
        "support": 42.50,          # 低吸区下沿
        "support_strong": 42.10,   # 筹码峰强支撑
        "resist": 44.80,           # 筹码峰强阻力/突破确认
        "resist_zone_high": 44.50, # 区间高抛区上沿
        "stop_loss": 42.00,        # 止损铁律
        "fib_1": 43.90,            # 斐波1.0等长目标
        "bb_upper": 47.80,         # 周布林上轨
        "ema20": 44.10,            # 周EMA20
        "ema60": 45.30,            # 周EMA60
        "position_limit": 0.03,    # 3% 仓位上限
        "alert_cooldown_sec": 300, # 同类预警冷却 5 分钟
    },
    "600584": {
        "name": "长电科技",
        "support": 48.50,          # 低吸区下沿/筹码峰支撑
        "support_strong": 47.50,   # 止损位/周低点
        "resist": 52.80,           # 筹码峰2/二级止盈/必清位
        "resist_zone_high": 51.50, # 区间高抛区/一级止盈
        "stop_loss": 47.50,        # 止损铁律
        "fib_1": 53.17,            # 斐波1.0等长目标
        "bb_upper": 58.20,         # 周布林上轨
        "ema20": 51.80,            # 周EMA20
        "ema60": 53.40,            # 周EMA60
        "position_limit": 0.025,   # 2.5% 仓位上限
        "alert_cooldown_sec": 300,
    }
}

# 监控参数
MONITOR_CONFIG = {
    "check_interval_sec": 15,      # 轮询间隔（秒）
    "trading_hours": [
        ("09:30", "11:30"),
        ("13:00", "15:00"),
    ],
    "pre_market_start": "09:15",   # 竞价监控开始
    "post_market_end": "15:05",    # 收盘后延迟监控
    "data_source": "sina",         # 新浪实时行情
    "volume_spike_threshold": 1.5, # 量比阈值
    "price_change_alert_threshold": 0.02,  # 单笔涨跌幅预警 2%
}

# Telegram 推送配置
# 优先读取 config/telegram.json，其次读取环境变量
# 敏感信息建议写在单独配置文件，不要提交到 Git
TELEGRAM_CONFIG = {
    "enabled": True,  # 改为 True 启用
    "bot_token": "",  # 格式: "123456789:ABCdefGhIjkLmnoPQRstuVwxyz"
    "chat_id": "",    # 格式: "123456789" 或 "-1001234567890" (群组)
    "proxy": "http://127.0.0.1:7890",  # 本机代理 (Clash/ClashX/V2Ray)
    "parse_mode": "Markdown",
}

# ============ 日志配置 ============
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

# ============ 状态记忆 ============
alert_state = {symbol: {"last_alert": {}} for symbol in STOCK_CONFIG}

# ============ 数据获取 ============
def fetch_sina_realtime(symbols: List[str]) -> Dict:
    """批量获取新浪实时行情"""
    sina_symbols = []
    for sym in symbols:
        prefix = "sh" if sym.startswith(("6", "9")) else "sz"
        sina_symbols.append(f"{prefix}{sym}")
    
    url = f"https://hq.sinajs.cn/list={','.join(sina_symbols)}"
    headers = {"Referer": "https://finance.sina.com.cn"}
    
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.encoding = "gbk"
        lines = resp.text.strip().split("\n")
        
        result = {}
        for line in lines:
            if not line.startswith("var hq_str_"):
                continue
            # var hq_str_sh600036="招商银行,43.55,43.50,43.60,43.10,43.00,43.55,43.56,123456,12345678,...";
            parts = line.split('="')
            if len(parts) != 2:
                continue
            sym_code = parts[0].split("_")[-1][2:]  # sh600036 -> 600036
            data = parts[1].rstrip('";').split(",")
            if len(data) < 32:
                continue
            result[sym_code] = {
                "name": data[0],
                "open": float(data[1]),
                "pre_close": float(data[2]),
                "price": float(data[3]),
                "high": float(data[4]),
                "low": float(data[5]),
                "bid": float(data[6]),
                "ask": float(data[7]),
                "volume": int(data[8]),
                "amount": float(data[9]),
                "bid1_vol": int(data[10]),
                "bid1_price": float(data[11]),
                "ask1_vol": int(data[12]),
                "ask1_price": float(data[13]),
                "date": data[30],
                "time": data[31],
            }
        return result
    except Exception as e:
        log.error(f"获取行情失败: {e}")
        return {}

# ============ 技术指标计算（简易分时版）============
class IntradayIndicator:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.prices = []
        self.volumes = []
        self.times = []
        self.max_len = 200  # 约 50 分钟 @ 15秒
    
    def update(self, price: float, volume: int, timestamp: str):
        self.prices.append(price)
        self.volumes.append(volume)
        self.times.append(timestamp)
        if len(self.prices) > self.max_len:
            self.prices.pop(0)
            self.volumes.pop(0)
            self.times.pop(0)
    
    def ma(self, period: int) -> Optional[float]:
        if len(self.prices) >= period:
            return sum(self.prices[-period:]) / period
        return None
    
    def volume_ma(self, period: int) -> Optional[float]:
        if len(self.volumes) >= period:
            return sum(self.volumes[-period:]) / period
        return None
    
    def volume_ratio(self) -> Optional[float]:
        """量比：当前分钟成交量 / 过去 5 分钟平均成交量"""
        if len(self.volumes) < 6:
            return None
        cur_vol = self.volumes[-1]
        avg_vol = sum(self.volumes[-6:-1]) / 5
        return cur_vol / avg_vol if avg_vol > 0 else None
    
    def macd_signal(self) -> Dict:
        """简易分时 MACD 信号（EMA12/EMA26/Signal9）"""
        if len(self.prices) < 26:
            return {"cross": None, "hist": 0, "trend": "insufficient"}
        
        prices = pd.Series(self.prices)
        ema12 = prices.ewm(span=12, adjust=False).mean().iloc[-1]
        ema26 = prices.ewm(span=26, adjust=False).mean().iloc[-1]
        dif = ema12 - ema26
        dea = pd.Series([dif]).ewm(span=9, adjust=False).mean().iloc[-1]  # 简化
        hist = 2 * (dif - dea)
        
        # 需要前值判断金叉/死叉，简化返回当前状态
        return {
            "dif": dif,
            "dea": dea,
            "hist": hist,
            "bullish": dif > dea and dif > 0,
            "bearish": dif < dea and dif < 0,
        }
    
    def price_position_in_range(self, low: float, high: float) -> float:
        """价格在区间中的位置 0-1"""
        if high <= low:
            return 0.5
        return max(0, min(1, (self.prices[-1] - low) / (high - low)))

# ============ 预警逻辑 ============
class AlertEngine:
    def __init__(self):
        self.indicators = {sym: IntradayIndicator(sym) for sym in STOCK_CONFIG}
        self.cooldowns = {sym: {} for sym in STOCK_CONFIG}
    
    def check_alerts(self, symbol: str, data: Dict) -> List[Dict]:
        """检查单只股票的所有预警条件"""
        cfg = STOCK_CONFIG[symbol]
        ind = self.indicators[symbol]
        alerts = []
        now = datetime.now()
        price = data["price"]
        volume = data["volume"]
        change_pct = (price / data["pre_close"] - 1) * 100
        
        # 更新指标
        ind.update(price, volume, data["time"])
        
        # --- 1. 止损触发（最高优先级）---
        if price <= cfg["stop_loss"]:
            alerts.append(self._make_alert(symbol, "STOP_LOSS", "🔴 止损触发", 
                f"价格 {price:.2f} ≤ 止损线 {cfg['stop_loss']:.2f}，立即清仓！", "CRITICAL"))
        
        # --- 2. 支撑位低吸机会---
        if cfg["support"] <= price <= cfg["support"] + 0.15:
            vol_ratio = ind.volume_ratio()
            macd = ind.macd_signal()
            if vol_ratio and vol_ratio < 0.8 and macd.get("bullish"):
                alerts.append(self._make_alert(symbol, "BUY_ZONE", "🟢 低吸机会",
                    f"价格 {price:.2f} 回踩支撑区 {cfg['support']:.2f}，缩量(量比{vol_ratio:.2f}) + MACD看多", "HIGH"))
        
        # --- 3. 强支撑位（筹码峰）---
        if abs(price - cfg["support_strong"]) < 0.10:
            alerts.append(self._make_alert(symbol, "STRONG_SUPPORT", "🟡 强支撑测试",
                f"价格 {price:.2f} 接近筹码峰强支撑 {cfg['support_strong']:.2f}，关注分时企稳", "MEDIUM"))
        
        # --- 4. 阻力位高抛---
        if cfg["resist_zone_high"] - 0.10 <= price <= cfg["resist_zone_high"] + 0.10:
            macd = ind.macd_signal()
            alerts.append(self._make_alert(symbol, "RESIST_ZONE", "🟠 区间高抛",
                f"价格 {price:.2f} 触及区间高抛区 {cfg['resist_zone_high']:.2f}，建议分批止盈", "HIGH"))
        
        # --- 5. 关键突破确认---
        if price >= cfg["resist"] and price > data["pre_close"]:
            vol_ratio = ind.volume_ratio()
            if vol_ratio and vol_ratio > MONITOR_CONFIG["volume_spike_threshold"]:
                macd = ind.macd_signal()
                if macd.get("bullish"):
                    alerts.append(self._make_alert(symbol, "BREAKOUT", "🚀 突破确认",
                        f"价格 {price:.2f} 放量(量比{vol_ratio:.2f})突破关键阻力 {cfg['resist']:.2f}，MACD看多，可留仓博趋势", "HIGH"))
        
        # --- 6. 必清仓位（次级筹码峰）---
        if symbol == "600584" and price >= cfg["resist"] - 0.05:
            alerts.append(self._make_alert(symbol, "MUST_SELL", "🛑 必清仓位",
                f"价格 {price:.2f} 临近筹码峰2 {cfg['resist']:.2f}(2.8亿成交量)，严禁贪恋，全清仓！", "CRITICAL"))
        if symbol == "600036" and price >= 46.40:
            alerts.append(self._make_alert(symbol, "MUST_SELL", "🛑 次级峰值预警",
                f"价格 {price:.2f} 临近次级筹码峰 46.50，建议全清仓", "HIGH"))
        
        # --- 7. 单笔异动---
        if abs(change_pct) >= MONITOR_CONFIG["price_change_alert_threshold"] * 100:
            direction = "📈 涨幅异动" if change_pct > 0 else "📉 跌幅异动"
            alerts.append(self._make_alert(symbol, "PRICE_SPIKE", direction,
                f"相对昨收 {change_pct:+.2f}%，当前 {price:.2f}", "LOW"))
        
        # --- 8. 分时均价支撑/阻力---
        ma5 = ind.ma(5)
        ma10 = ind.ma(10)
        if ma5 and ma10:
            if price > ma5 > ma10 and price < ma5 * 1.002:
                alerts.append(self._make_alert(symbol, "MA_SUPPORT", "📊 分时均价支撑",
                    f"价格 {price:.2f} 回踩 5分均价 {ma5:.2f} 获支撑，多头排列", "MEDIUM"))
            if price < ma5 < ma10 and price > ma5 * 0.998:
                alerts.append(self._make_alert(symbol, "MA_RESIST", "📊 分时均价阻力",
                    f"价格 {price:.2f} 反抽 5分均价 {ma5:.2f} 受阻，空头排列", "MEDIUM"))
        
        # 冷却过滤
        filtered = []
        for alert in alerts:
            key = alert["type"]
            last = self.cooldowns[symbol].get(key, 0)
            if now.timestamp() - last >= cfg["alert_cooldown_sec"]:
                self.cooldowns[symbol][key] = now.timestamp()
                filtered.append(alert)
        
        return filtered
    
    def _make_alert(self, symbol: str, alert_type: str, title: str, msg: str, severity: str) -> Dict:
        cfg = STOCK_CONFIG[symbol]
        return {
            "symbol": symbol,
            "name": cfg["name"],
            "type": alert_type,
            "title": title,
            "message": msg,
            "severity": severity,
            "price": self.indicators[symbol].prices[-1] if self.indicators[symbol].prices else 0,
            "time": datetime.now().strftime("%H:%M:%S"),
            "key_levels": {
                "stop_loss": cfg["stop_loss"],
                "support": cfg["support"],
                "resist": cfg["resist"],
                "resist_zone_high": cfg["resist_zone_high"],
            }
        }

# ============ 推送通知 ============
def load_telegram_config() -> Dict:
    """加载 Telegram 配置：优先读取 config/telegram.json，其次环境变量"""
    config_path = "/Users/duguke/.openclaw/workspace/config/telegram.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                file_config = json.load(f)
                TELEGRAM_CONFIG.update(file_config)
                log.info(f"✅ 已加载 Telegram 配置: {config_path}")
                return TELEGRAM_CONFIG
        except Exception as e:
            log.warning(f"读取 Telegram 配置文件失败: {e}")
    # 回退环境变量
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        TELEGRAM_CONFIG["bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN")
        TELEGRAM_CONFIG["chat_id"] = os.getenv("TELEGRAM_CHAT_ID")
        TELEGRAM_CONFIG["enabled"] = True
        log.info("✅ 已从环境变量加载 Telegram 配置")
    return TELEGRAM_CONFIG


def send_telegram(message: str):
    # 确保配置已加载
    load_telegram_config()
    
    if not TELEGRAM_CONFIG["enabled"] or not TELEGRAM_CONFIG["bot_token"] or not TELEGRAM_CONFIG["chat_id"]:
        return
    try:
        proxies = {"http": TELEGRAM_CONFIG["proxy"], "https": TELEGRAM_CONFIG["proxy"]} \
            if TELEGRAM_CONFIG["proxy"] else None
        url = f"https://api.telegram.org/bot{TELEGRAM_CONFIG['bot_token']}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CONFIG["chat_id"],
            "text": message,
            "parse_mode": TELEGRAM_CONFIG.get("parse_mode", "Markdown"),
            "disable_web_page_preview": True
        }
        requests.post(url, json=payload, proxies=proxies, timeout=10)
    except Exception as e:
        log.warning(f"Telegram 推送失败: {e}")

def format_alert(alert: Dict) -> str:
    sev_emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}
    emoji = sev_emoji.get(alert["severity"], "⚪")
    return (f"{emoji} [{alert['time']}] {alert['title']} - {alert['name']}({alert['symbol']})\n"
            f"    价格: {alert['price']:.2f} | {alert['message']}\n"
            f"    关键位: 止损{alert['key_levels']['stop_loss']:.2f} | 支撑{alert['key_levels']['support']:.2f} | "
            f"阻力{alert['key_levels']['resist']:.2f} | 高抛{alert['key_levels']['resist_zone_high']:.2f}")

# ============ 交易软件条件单参数导出 ============
def export_conditional_orders():
    """导出通用条件单参数（可导入同花顺/东财/通达信等）"""
    orders = []
    for symbol, cfg in STOCK_CONFIG.items():
        # 条件单 1：低吸买入
        orders.append({
            "name": f"{cfg['name']}_低吸买入",
            "symbol": symbol,
            "direction": "BUY",
            "condition_type": "PRICE_AND_VOLUME",
            "trigger_price": cfg["support"],
            "volume_ratio_max": 0.8,
            "price_range_pct": 0.35,  # 支撑位 ±0.35%
            "order_price_type": "LIMIT",
            "order_price_offset": 0.02,  # 挂单价 = 触发价 + 0.02
            "position_pct": cfg["position_limit"] * 0.5,  # 首笔半仓
            "valid_days": 5,
            "note": "缩量回踩支撑 + 分时企稳"
        })
        # 条件单 2：区间高抛止盈
        orders.append({
            "name": f"{cfg['name']}_区间高抛",
            "symbol": symbol,
            "direction": "SELL",
            "condition_type": "PRICE",
            "trigger_price": cfg["resist_zone_high"],
            "order_price_type": "LIMIT",
            "order_price_offset": -0.02,
            "sell_pct": 0.66,  # 卖出 2/3
            "valid_days": 5,
            "note": "触及区间阻力分批止盈"
        })
        # 条件单 3：突破留仓
        orders.append({
            "name": f"{cfg['name']}_突破确认留仓",
            "symbol": symbol,
            "direction": "HOLD",
            "condition_type": "PRICE_AND_VOLUME",
            "trigger_price": cfg["resist"],
            "volume_ratio_min": 1.5,
            "order_price_type": "MARKET",
            "action": "ALERT_ONLY",  # 仅提醒，人工决定
            "valid_days": 5,
            "note": "放量突破关键阻力，留 1/3 博趋势"
        })
        # 条件单 4：止损离场
        orders.append({
            "name": f"{cfg['name']}_止损离场",
            "symbol": symbol,
            "direction": "SELL",
            "condition_type": "STOP_LOSS",
            "trigger_price": cfg["stop_loss"],
            "order_price_type": "MARKET",  # 市价止损
            "sell_pct": 1.0,  # 全部清仓
            "valid_days": 30,
            "note": "铁律止损，破位即走"
        })
        # 条件单 5：必清仓（次级筹码峰）
        if symbol == "600584":
            orders.append({
                "name": f"{cfg['name']}_次级峰值必清",
                "symbol": symbol,
                "direction": "SELL",
                "condition_type": "PRICE",
                "trigger_price": cfg["resist"],
                "order_price_type": "LIMIT",
                "order_price_offset": -0.05,
                "sell_pct": 1.0,
                "valid_days": 5,
                "note": "触及筹码峰2(52.80)必清仓，严禁贪恋"
            })
    
    output_path = "/Users/duguke/.openclaw/workspace/analysis/conditional_orders_2026-11-03.json"
    # [2026-11-01] 写前 .bak 备份（backup_guard 铁律：重写型写点必须有备份保护）
    try:
        if os.path.exists(output_path):
            shutil.copy2(output_path, output_path + ".bak")
    except Exception as _e:
        log.warning("写前备份失败(不阻断): %s", _e)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)
    log.info(f"✅ 条件单参数已导出: {output_path}")
    return orders

# ============ 主监控循环 ============
def is_trading_time() -> bool:
    now = datetime.now().time()
    for start_str, end_str in MONITOR_CONFIG["trading_hours"]:
        start = datetime.strptime(start_str, "%H:%M").time()
        end = datetime.strptime(end_str, "%H:%M").time()
        if start <= now <= end:
            return True
    return False

def is_pre_market() -> bool:
    now = datetime.now().time()
    start = datetime.strptime(MONITOR_CONFIG["pre_market_start"], "%H:%M").time()
    end = datetime.strptime("09:30", "%H:%M").time()
    return start <= now < end

def main():
    symbols = list(STOCK_CONFIG.keys())
    engine = AlertEngine()
    
    # 加载 Telegram 配置
    load_telegram_config()
    
    # 导出条件单参数
    export_conditional_orders()
    
    log.info("=" * 60)
    log.info("🚀 盘中预警监控启动")
    targets = ', '.join([f'{s}({STOCK_CONFIG[s]["name"]})' for s in symbols])
    log.info(f"标的: {targets}")
    log.info(f"监控间隔: {MONITOR_CONFIG['check_interval_sec']}秒")
    log.info(f"交易时段: {MONITOR_CONFIG['trading_hours']}")
    log.info("=" * 60)
    
    # 竞价监控
    if is_pre_market():
        log.info("⏰ 竞价时段监控中...")
    
    consecutive_errors = 0
    while True:
        try:
            if not is_trading_time() and not is_pre_market():
                # 非交易时间，检查是否为收盘后延迟监控
                now = datetime.now().time()
                post_end = datetime.strptime(MONITOR_CONFIG["post_market_end"], "%H:%M").time()
                if now > post_end:
                    log.info("📴 非交易时间，监控结束")
                    break
                time.sleep(60)
                continue
            
            # 获取实时行情
            data = fetch_sina_realtime(symbols)
            if not data:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    log.error("连续 3 次获取行情失败，暂停 60 秒")
                    time.sleep(60)
                    consecutive_errors = 0
                continue
            
            consecutive_errors = 0
            
            # 检查预警
            for symbol in symbols:
                if symbol not in data:
                    continue
                alerts = engine.check_alerts(symbol, data[symbol])
                for alert in alerts:
                    msg = format_alert(alert)
                    log.info(msg)
                    if TELEGRAM_CONFIG["enabled"]:
                        send_telegram(msg)
            
            # 竞价特别提示
            if is_pre_market():
                for symbol in symbols:
                    if symbol in data:
                        d = data[symbol]
                        chg = (d["price"] / d["pre_close"] - 1) * 100
                        log.info(f"📊 竞价 {d['name']}({symbol}): {d['price']:.2f} ({chg:+.2f}%) 量: {d['volume']:,}")
            
        except KeyboardInterrupt:
            log.info("👋 用户中断，监控停止")
            break
        except Exception as e:
            log.error(f"监控异常: {e}")
            consecutive_errors += 1
        
        time.sleep(MONITOR_CONFIG["check_interval_sec"])

if __name__ == "__main__":
    main()