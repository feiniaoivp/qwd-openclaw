#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
watch10 盯盘推送 — 宇宙筛选 Top10 专项盘中监控（command cron 直跑，不经 LLM）
==========================================================================
背景（2026-11-14）：用户拍板对 2026-11-13 宇宙筛选 Top10（floor_shadow_ledger，
被 90 地板咔嚓但人工要求盯盘）做盘中推送，通道 = 新 bot @qiu88888_bot。
与既有 push_intraday_alerts.py（36 关注池、策略价位触发）职责不同，不互替。

触发规则（v1）：
  1. 每日首轮 → 推「开盘快照」（全部 10 只现价/涨跌幅，必推）
  2. 涨跌幅每穿越一个 ±2% 档（相对昨收）→ 推一条（per-level 去重，同日同档不重复）
  3. 涨跌幅 |x| ≥ 9%（涨停/跌停逼近）→ 推（独立去重）
用法:
  python3 scripts/push_watch10_intraday.py           # 正常（cron 调用，含时段守卫）
  python3 scripts/push_watch10_intraday.py --test    # 忽略时段守卫，立即推快照（验证用）
输出: 0 = 正常（推送或静默）; 非0 = 出错
"""
import os
import sys
import json
import time
import shutil
import urllib.parse

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from datetime import datetime

CONFIG_FILE = os.path.join(WORKSPACE, "data", "watch10", "config.json")
STATE_FILE = os.path.join(WORKSPACE, "data", "watch10", "state.json")
LOG_FILE = os.path.join(WORKSPACE, "logs", "watch10_intraday.log")
TOKEN_FILE = os.path.join(WORKSPACE, "credentials", "telegram_bot_qiu.token")
CHAT_ID = "626141741"

SINA_URL = "https://hq.sinajs.cn/list="
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}
PROXY = "http://127.0.0.1:7890"


def log(msg):
    line = f"{datetime.now().strftime('%F %T')} {msg}"
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, file=sys.stderr)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        log(f"⚠️ 配置/状态读取失败（用默认/空状态继续）: {path}: {e}")
        return default


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        if os.path.exists(STATE_FILE):
            shutil.copy2(STATE_FILE, STATE_FILE + ".bak")
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log(f"⚠️ 状态写盘失败（不阻断）: {e}")


def fetch_quotes(stocks):
    """新浪批量实时行情。返回 {code: dict}；失败返回 {}（可见告警，绝不静默用空当数据）。"""
    import requests
    symbols = ",".join(s["sina"] for s in stocks)
    url = SINA_URL + urllib.parse.quote(symbols, safe=",")
    last_err = None
    # TLS 潮汐：直连失败 → 7890 代理，交替重试 3 轮（网络调用超时铁律：显式 timeout + 降级可见）
    for attempt in range(3):
        proxies = None if attempt % 2 == 0 else {"http": PROXY, "https": PROXY}
        try:
            r = requests.get(url, headers=SINA_HEADERS, proxies=proxies, timeout=8)
            r.encoding = "gbk"
            out = {}
            for line in r.text.splitlines():
                if '="' not in line:
                    continue
                sym = line.split('hq_str_')[1].split('="')[0]
                fields = line.split('="')[1].rsplit('"')[0].split(',')
                if len(fields) < 32 or not fields[0]:
                    continue
                out[sym] = {
                    "name": fields[0], "open": float(fields[1] or 0),
                    "prev_close": float(fields[2] or 0), "price": float(fields[3] or 0),
                    "high": float(fields[4] or 0), "low": float(fields[5] or 0),
                    "time": f"{fields[30]} {fields[31]}" if len(fields) > 31 else "",
                }
            if out:
                return out
            last_err = "响应无可解析行情"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(1)
    log(f"⚠️ 行情获取失败（本轮跳过，不用空数据触发推送）: {last_err}")
    return {}


def tg_send(text):
    """新 bot 推送：token 从 credentials 读，直连失败走 7890 代理重试。"""
    import requests
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            token = f.read().strip()
    except OSError as e:
        log(f"⚠️ 读不到 bot token，跳过推送: {e}")
        return False
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(4):
        proxies = None if attempt % 2 == 0 else {"http": PROXY, "https": PROXY}
        try:
            r = requests.post(api, data={"chat_id": CHAT_ID, "text": text},
                              proxies=proxies, timeout=10)
            if r.status_code == 200:
                return True
            log(f"⚠️ TG 推送 HTTP {r.status_code}: {r.text[:120]}")
            return False  # 4xx 类错误重试无意义
        except Exception as e:
            log(f"TG 发送失败(attempt {attempt+1}, proxy={bool(proxies)}): {type(e).__name__}")
            time.sleep(2)
    return False


def in_trading_window(now=None):
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return (925 <= t <= 1135) or (1255 <= t <= 1505)


def snapshot_lines(quotes, cfg):
    lines = ["【Watch10 盘中快照】" + datetime.now().strftime("%H:%M")]
    for s in cfg["stocks"]:
        q = quotes.get(s["sina"])
        if not q:
            lines.append(f"❓{s['name']} {s['code']} 无行情")
            continue
        pc = q["prev_close"] or s.get("prev_close") or 0
        pct = (q["price"] / pc - 1) * 100 if pc else 0
        lines.append(f"{s['name']} {q['price']:.2f} {pct:+.2f}%  (昨收{pc:.2f})")
    return "\n".join(lines)


def main():
    test_mode = "--test" in sys.argv
    cfg = load_json(CONFIG_FILE, None)
    if not cfg or not cfg.get("stocks"):
        log("❌ 配置缺失/为空，退出")
        return 2
    if not test_mode and not in_trading_window():
        return 0  # 时段外静默
    if not test_mode:
        from analysis.service import is_trading_day
        if not is_trading_day():
            return 0

    state = load_json(STATE_FILE, {})
    today = datetime.now().strftime("%F")
    day_state = state.get(today) or {}
    quotes = fetch_quotes(cfg["stocks"])
    if not quotes:
        return 1

    pushes = []
    # 1) 每日首轮开盘快照
    if test_mode or not day_state.get("snapshot_done"):
        pushes.append(snapshot_lines(quotes, cfg))
        day_state["snapshot_done"] = True

    # 2) ±2% 档穿越 + 3) 涨停/跌停逼近
    step = float(cfg.get("level_step_pct", 2.0))
    limit_warn = float(cfg.get("limit_warn_pct", 9.0))
    levels_hit = day_state.setdefault("levels", {})
    for s in cfg["stocks"]:
        q = quotes.get(s["sina"])
        if not q:
            continue
        pc = q["prev_close"] or s.get("prev_close") or 0
        if not pc:
            continue
        pct = (q["price"] / pc - 1) * 100
        cur_level = int(pct / step)  # 向零取整：-3.5% → -1 档（-2%~-4% 区间）
        prev = levels_hit.get(s["code"])
        if prev is None or cur_level != prev:
            if prev is not None:
                emoji = "🟢" if pct > 0 else "🔴"
                pushes.append(f"{emoji} {s['name']} {s['code']} {q['price']:.2f} "
                              f"{pct:+.2f}%（穿越 {step:g}% 档）")
            levels_hit[s["code"]] = cur_level
        if abs(pct) >= limit_warn and not day_state.get(f"limitwarn_{s['code']}"):
            emoji = "🚀" if pct > 0 else "🩸"
            pushes.append(f"{emoji} {s['name']} {s['code']} {pct:+.2f}% "
                          f"接近{'涨停' if pct > 0 else '跌停'}！现价 {q['price']:.2f}")
            day_state[f"limitwarn_{s['code']}"] = True

    state[today] = day_state
    # 只保留最近 5 天状态
    keys = sorted(state.keys())
    for k in keys[:-5]:
        state.pop(k, None)
    save_state(state)

    if pushes:
        ok = tg_send("\n\n".join(pushes))
        log(f"推送{'成功' if ok else '失败'}（{len(pushes)} 条）")
        return 0 if ok else 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
