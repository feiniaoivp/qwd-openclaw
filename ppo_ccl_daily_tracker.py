#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PPO+CCL 产业链每日收盘跟踪（13只核心标的）
- 数据源：yfinance (Yahoo Finance)
- 运行时间：每个交易日 15:30 (Asia/Shanghai)
- 推送方式：Telegram Bot API
- 技术指标：RSI(14)、MACD(12,26,9)、量价背离检测
"""

import sys
import os
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import traceback
import time
from datetime import datetime

# ============ 配置区 ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHAT_ID   = os.environ.get("CHAT_ID", "626141741")

symbols = [
    ('600143.SS', '金发科技'), ('688458.SS', '科思股份'), ('002838.SZ', '道恩股份'), ('603010.SS', '万盛股份'),
    ('600309.SS', '万华化学'), ('002648.SZ', '卫星石化'), ('600346.SS', '恒力石化'), ('002493.SZ', '荣盛石化'),
    ('600176.SS', '中国巨石'), ('002563.SZ', '德福科技'),
    ('600266.SS', '广东建科'), ('600183.SS', '生益科技'), ('002916.SZ', '深南电路'), ('002463.SZ', '沪士电子')
]

sectors = {
    '600143.SS': '树脂/改性', '688458.SS': '树脂/改性', '002838.SZ': '树脂/改性', '603010.SS': '树脂/改性',
    '600309.SS': '上游原料', '002648.SZ': '上游原料', '600346.SS': '上游原料', '002493.SZ': '上游原料',
    '600176.SS': '玻璃布/铜箔', '002563.SZ': '玻璃布/铜箔',
    '600266.SS': 'CCL/PCB', '600183.SS': 'CCL/PCB', '002916.SZ': 'CCL/PCB', '002463.SZ': 'CCL/PCB'
}

# ============ 技术指标函数 ============

def calc_rsi(close, window=14):
    """RSI(14)"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1/window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calc_macd(close, fast=12, slow=26, signal=9):
    """MACD(12,26,9) → (macd, signal_line, histogram)"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    sig = macd.ewm(span=signal, adjust=False).mean()
    hist = macd - sig
    return macd, sig, hist

def detect_divergence(close, vol, lookback=14):
    """
    量价背离检测
    返回: [顶背离, 底背离, 量价配合]
    - 顶背离: 价格新高但量萎缩 → 回调信号
    - 底背离: 价格新低但量放大 → 反弹信号
    - 量价配合: 涨时放量、跌时缩量 → 健康趋势
    """
    if len(close) < lookback + 5:
        return False, False, False
    
    seg = close.iloc[-lookback:]
    vseg = vol.iloc[-lookback:]
    
    # 简单顶背离: 最近5日创了lookback内新高，但量能低于均值
    peak_idx = seg.idxmax()
    peak_pos = seg.index.get_loc(peak_idx)
    # 创阶段新高
    recent_high = close.iloc[-5:].max()
    period_high = close.iloc[-lookback:].max()
    if recent_high >= period_high * 0.995:  # 接近阶段高点
        avg_vol_lookback = vseg.mean()
        vol_near_peak = vol.loc[close.iloc[-5:].idxmax()] if close.iloc[-5:].idxmax() in vol.index else vol.iloc[-5:].mean()
        if vol_near_peak < avg_vol_lookback * 0.8:
            top_div = True
        else:
            top_div = False
    else:
        top_div = False
    
    # 简单底背离: 最近5日创了lookback内新低，但量能高于均值
    trough_idx = seg.idxmin()
    recent_low = close.iloc[-5:].min()
    period_low = close.iloc[-lookback:].min()
    if recent_low <= period_low * 1.005:  # 接近阶段低点
        avg_vol_lookback = vseg.mean()
        vol_near_trough = vol.loc[close.iloc[-5:].idxmin()] if close.iloc[-5:].idxmin() in vol.index else vol.iloc[-5:].mean()
        if vol_near_trough > avg_vol_lookback * 1.2:
            bot_div = True
        else:
            bot_div = False
    else:
        bot_div = False
    
    # 量价健康配合: 涨放量/跌缩量
    chg5 = close.pct_change(5)
    vol_chg5 = vol.pct_change(5)
    last_chg5 = chg5.iloc[-1] if not chg5.empty and len(chg5.dropna()) > 0 else 0
    last_vol_chg5 = vol_chg5.iloc[-1] if not vol_chg5.empty and len(vol_chg5.dropna()) > 0 else 0
    healthy = (last_chg5 > 0 and last_vol_chg5 > 0.05) or (last_chg5 < 0 and last_vol_chg5 < -0.05)
    
    return top_div, bot_div, healthy


# ============ 核心逻辑 ============

def fetch_and_compute():
    rows = []
    for sym, name in symbols:
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period='3mo')  # 需要更长数据计算 MACD
            if hist.empty or len(hist) < 2:
                rows.append([name, sym, 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', '—', '—', '—'])
                continue

            close = hist['Close']
            vol   = hist['Volume']
            high  = hist['High']
            low   = hist['Low']

            cur_close = close.iloc[-1]
            prev_close = close.iloc[-2] if len(close) > 1 else cur_close
            day_chg_pct = (cur_close - prev_close) / prev_close * 100

            if len(close) >= 5:
                close_5d_ago = close.iloc[-5]
                chg_5d_pct = (cur_close - close_5d_ago) / close_5d_ago * 100
            else:
                chg_5d_pct = day_chg_pct

            turnover = cur_close * vol.iloc[-1] / 1e8
            avg_vol_5d = vol.tail(5).mean()
            vol_ratio = vol.iloc[-1] / avg_vol_5d if avg_vol_5d > 0 else 0
            amplitude = (high.iloc[-1] - low.iloc[-1]) / prev_close * 100

            # ---- 技术指标 ----
            rsi_series = calc_rsi(close)
            rsi_val = rsi_series.iloc[-1] if not rsi_series.empty and not pd.isna(rsi_series.iloc[-1]) else None

            macd, sig, _ = calc_macd(close)
            macd_val = macd.iloc[-1] if not macd.empty and not pd.isna(macd.iloc[-1]) else None
            macd_sig = sig.iloc[-1] if not sig.empty and not pd.isna(sig.iloc[-1]) else None

            # RSI 状态描述
            if rsi_val is not None:
                if rsi_val > 70:      rsi_label = "超买🔴"
                elif rsi_val < 30:    rsi_label = "超卖🟢"
                elif rsi_val > 60:    rsi_label = "偏强🟡"
                elif rsi_val < 40:    rsi_label = "偏弱🟤"
                else:                 rsi_label = "中性⚪"
                rsi_str = f"{rsi_val:.1f} {rsi_label}"
            else:
                rsi_str = "N/A"

            # MACD 状态描述
            if macd_val is not None and macd_sig is not None:
                if macd_val > macd_sig and macd_val > 0:    macd_label = "多头🟢"
                elif macd_val > macd_sig and macd_val < 0:  macd_label = "底金叉⚠️"
                elif macd_val < macd_sig and macd_val > 0:  macd_label = "顶死叉⚠️"
                else:                                        macd_label = "空头🔴"
                macd_str = f"{macd_val:.2f}/{macd_sig:.2f} {macd_label}"
            else:
                macd_str = "N/A"

            # 量价背离检测
            top_div, bot_div, healthy = detect_divergence(close, vol)
            if top_div:
                diver_label = "顶背离⚠️"
            elif bot_div:
                diver_label = "底背离🟢"
            elif healthy:
                diver_label = "量价配合✅"
            else:
                diver_label = "—"

            # ---- 异动组合（包含技术指标信号） ----
            alerts = []
            if day_chg_pct > 3:                  alerts.append('涨>3%')
            if vol_ratio > 2:                    alerts.append('量比>2')
            if rsi_val is not None and rsi_val > 70:  alerts.append('RSI超买')
            if rsi_val is not None and rsi_val < 30:  alerts.append('RSI超卖')
            if top_div:                          alerts.append('顶背离')
            if bot_div:                          alerts.append('底背离')
            if macd_val is not None and macd_sig is not None and macd_val < macd_sig and day_chg_pct < -2:
                alerts.append('死叉+跌')
            if macd_val is not None and macd_sig is not None and macd_val > macd_sig and day_chg_pct > 2:
                alerts.append('金叉+涨')
            alert_str = '、'.join(alerts) if alerts else '—'

            rows.append([
                name, sym,
                f'{cur_close:.2f}', f'{day_chg_pct:+.2f}%', f'{chg_5d_pct:+.2f}%',
                f'{turnover:.2f}', f'{vol_ratio:.2f}', f'{amplitude:.2f}%',
                rsi_str, macd_str, diver_label, alert_str
            ])
        except Exception as e:
            rows.append([name, sym, 'ERR', 'ERR', 'ERR', 'ERR', 'ERR', 'ERR', 'ERR', 'ERR', 'ERR', f'❌{str(e)[:30]}'])
    return rows


def build_markdown(rows):
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = []
    lines.append(f'**PPO+CCL 产业链收盘跟踪 | {now_str} (北京时间)**')
    lines.append('')
    lines.append('| 板块 | 名称 | 价格 | 日涨跌 | 5日% | 成交额(亿) | 量比 | 振幅% | RSI(14) | MACD(值/信号) | 量价背离 | 异动 |')
    lines.append('|------|------|------|--------|------|-----------|------|-------|---------|--------------|----------|------|')
    for r in rows:
        sym = r[1]
        sector = sectors.get(sym, '—')
        alert = r[-1]
        highlight = ' ⚠️' if '❌' not in alert and alert != '—' else ''
        # 将变色符放到异动列末尾（Telegram Markdown 对 emoji 支持良好）
        lines.append(f'| {sector} | {r[0]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} | {r[7]} | {r[8]} | {r[9]} | {r[10]} | {alert}{highlight} |')
    
    lines.append('')
    lines.append('**▎RSI说明：** 超买>70🔴 / 偏强>60🟡 / 中性⚪ / 偏弱<40🟤 / 超卖<30🟢')
    lines.append('**▎MACD说明：** 多头>0线+金叉🟢 / 死叉🔴 / 金叉涨/死叉跌⚠️')
    lines.append('**▎背离说明：** 顶背离=价高量缩(回调)⚠️ / 底背离=价低量放(反弹)🟢 / 量价配合=涨放量跌缩量✅')
    lines.append('')
    lines.append('**研判：**')
    
    # 收集异动/信号
    signal_rows = []
    for r in rows:
        alert = r[-1]
        if '❌' not in alert and alert != '—':
            signal_rows.append(r)
        diver = r[10]
        if diver not in ('—', '量价配合✅') and '❌' not in r[-1]:
            if r not in signal_rows:
                signal_rows.append(r)
    
    if signal_rows:
        # 按信号强度排序：顶背离 > 底背离 > 金叉/死叉 > 普通异动
        for r in signal_rows:
            alerts_here = r[-1]
            diver_here = r[10]
            if '顶背离' in diver_here:
                lines.append(f'- ⚠️ **{r[0]}({r[1]})** — 顶背离，注意回调风险')
            elif '底背离' in diver_here:
                lines.append(f'- 🟢 **{r[0]}({r[1]})** — 底背离，关注反弹机会')
            elif '金叉+涨' in alerts_here:
                lines.append(f'- 🟢 **{r[0]}({r[1]})** — MACD金叉+上涨')
            elif '死叉+跌' in alerts_here:
                lines.append(f'- ⚠️ **{r[0]}({r[1]})** — MACD死叉+下跌')
            elif 'RSI超买' in alerts_here:
                lines.append(f'- 🔴 **{r[0]}({r[1]})** — RSI超买')
            elif 'RSI超卖' in alerts_here:
                lines.append(f'- 🟢 **{r[0]}({r[1]})** — RSI超卖')
            elif alerts_here != '—':
                lines.append(f'- {r[0]}({r[1]}) — {alerts_here}')
    else:
        lines.append('- 今日无明显技术信号或异动')
    lines.append('- 重点关注生益科技/中国巨石/德福科技涨价传导落地情况')
    
    return '\n'.join(lines)


def send_telegram(markdown_text):
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[WARN] BOT_TOKEN 未配置，跳过 Telegram 推送", file=sys.stderr)
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": markdown_text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                return True
            else:
                print(f"[ERROR] Telegram 推送失败 (尝试 {attempt+1}/3): {resp.status_code} {resp.text}", file=sys.stderr)
        except requests.exceptions.Timeout:
            print(f"[ERROR] Telegram 请求超时 (尝试 {attempt+1}/3)", file=sys.stderr)
        except requests.exceptions.ConnectionError:
            print(f"[ERROR] Telegram 连接错误 (尝试 {attempt+1}/3)", file=sys.stderr)
        except Exception as e:
            print(f"[ERROR] Telegram 请求异常 (尝试 {attempt+1}/3): {e}", file=sys.stderr)
        if attempt < 2:
            time.sleep(2)
    return False


def main():
    try:
        rows = fetch_and_compute()
        md = build_markdown(rows)
        print(md)
        ok = send_telegram(md)
        if ok:
            print("[INFO] Telegram 推送成功", file=sys.stderr)
        else:
            print("[WARN] Telegram 推送失败，但数据获取成功", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
