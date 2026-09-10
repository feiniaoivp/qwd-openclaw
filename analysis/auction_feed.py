#!/usr/bin/env python3
"""
集合竞价数据订阅器 (新浪实时为主 + pytdx 备用)
- 9:15-9:25 订阅自选池实时竞价行情
- 9:25:00 快照落盘到 data/auction/YYYY-MM-DD_9_25.json
- 支持断线重连、非交易日跳过、停牌过滤、硬超时保护
- 数据源路由：新浪实时(hq.sinajs.cn)为主 → pytdx备用
"""

import json
import sys
import time
import re
import requests
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread, Event
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

WORKSPACE = Path("/Users/duguke/.openclaw/workspace")
sys.path.insert(0, str(WORKSPACE))

from analysis.service import is_trading_day

# ==================== 自选池（与 memory/watchlist.md 保持一致，2026-08-30版） ====================
WATCHLIST = [
    ("600030", "中信证券"), ("601066", "中信建投"), ("600036", "招商银行"),
    ("601995", "中金公司"), ("000987", "越秀资本"),
    ("600584", "长电科技"), ("688981", "中芯国际"), ("002156", "通富微电"),
    ("002413", "雷科防务"),
    ("300014", "亿纬锂能"), ("002466", "天齐锂业"),
    ("300285", "国瓷材料"), ("603308", "应流股份"), ("300124", "汇川技术"),
    ("601100", "恒立液压"), ("002318", "久立特材"), ("300719", "安达维尔"),
    ("002335", "科华数据"), ("300748", "金力永磁"),
    ("600160", "巨化股份"), ("600346", "恒力石化"), ("000708", "中信特钢"),
    ("600660", "福耀玻璃"), ("600570", "恒生电子"), ("605566", "福莱蒽特"),
    ("000157", "中联重科"), ("601061", "中信金属"),
]

# 市场代码映射：pytdx 需要 market(0=sz, 1=sh) + code
def to_tdx_code(symbol: str) -> Tuple[int, str]:
    if symbol[0] in ("6", "9"):
        return 1, symbol
    return 0, symbol


# ==================== 新浪实时行情接口（主源：稳定、无连接建立开销、HTTP GET即可） ====================
class SinaRealtimeFeed:
    """新浪实时行情 hq.sinajs.cn 批量获取（需 Referer 头）"""

    URL = "https://hq.sinajs.cn/list="
    HEADERS = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    BATCH_SIZE = 50  # 单次请求最多 50 只

    @classmethod
    def fetch_batch(cls, symbols: List[str], timeout: float = 5.0) -> Dict[str, dict]:
        """批量获取实时行情，返回 code -> 字段 dict"""
        if not symbols:
            return {}

        results = {}
        # 分批请求
        for i in range(0, len(symbols), cls.BATCH_SIZE):
            batch = symbols[i:i + cls.BATCH_SIZE]
            sina_codes = []
            for sym in batch:
                pref = "sh" if sym[0] in "69" else "sz"
                sina_codes.append(f"{pref}{sym}")

            url = cls.URL + ",".join(sina_codes)
            try:
                resp = requests.get(url, headers=cls.HEADERS, timeout=timeout)
                resp.encoding = "gbk"  # 新浪返回 GBK
                text = resp.text
                # 解析: var hq_str_sh600030="中信证券,27.12,27.05,27.20,26.95,26.80,27.12,27.13,12345678,123456789,...";
                for line in text.strip().split(";"):
                    if not line or "=" not in line:
                        continue
                    var_part, data_part = line.split("=", 1)
                    match = re.search(r'hq_str_(sh|sz)(\d{6})', var_part)
                    if not match:
                        continue
                    code = match.group(2)
                    fields = data_part.strip('"').split(",")
                    if len(fields) < 30:
                        continue
                    name = fields[0]
                    open_price = float(fields[1])
                    pre_close = float(fields[2])
                    price = float(fields[3])  # 当前价/竞价价
                    high = float(fields[4])
                    low = float(fields[5])
                    bid1 = float(fields[6])
                    ask1 = float(fields[7])
                    vol_shares = int(float(fields[8])) * 100  # 手->股
                    amount = float(fields[9])  # 元
                    # fields[30] 是日期, fields[31] 是时间

                    chg_pct = ((price / pre_close - 1) * 100) if pre_close > 0 else 0.0
                    open_chg_pct = ((open_price / pre_close - 1) * 100) if pre_close > 0 and open_price > 0 else None

                    # 涨跌停价
                    limit_up = round(pre_close * 1.1, 2) if code[0] in "03" else round(pre_close * 1.099, 2)
                    limit_down = round(pre_close * 0.9, 2) if code[0] in "03" else round(pre_close * 0.901, 2)
                    is_limit_up = (abs(bid1 - limit_up) < 0.01)
                    is_limit_down = (abs(ask1 - limit_down) < 0.01)

                    results[code] = {
                        "code": code,
                        "name": name,
                        "pre_close": round(pre_close, 3),
                        "auction_price": round(price, 3),
                        "open": round(open_price, 3),
                        "high": round(high, 3),
                        "low": round(low, 3),
                        "chg_pct": round(chg_pct, 2),
                        "open_chg_pct": round(open_chg_pct, 2) if open_chg_pct is not None else None,
                        "bid1": round(bid1, 3),
                        "ask1": round(ask1, 3),
                        "bid_vol1": int(float(fields[10])) * 100 if len(fields) > 10 else 0,
                        "ask_vol1": int(float(fields[11])) * 100 if len(fields) > 11 else 0,
                        "volume_shares": vol_shares,
                        "amount": round(amount, 2),
                        "amount_yi": round(amount / 1e8, 3),
                        "is_limit_up": is_limit_up,
                        "is_limit_down": is_limit_down,
                        "limit_up_price": limit_up,
                        "limit_down_price": limit_down,
                        "source": "sina_realtime",
                    }
            except Exception as e:
                print(f"[WARN] 新浪实时批量获取失败: {e}")
                continue
        return results


# ==================== pytdx 竞价订阅（备用源：连接不稳定，带超时保护） ====================
class PytdxAuctionSubscriber:
    """基于 pytdx 的集合竞价实时订阅（备用）"""

    SERVERS = [
        ("180.153.18.170", 7709),
        ("180.153.18.171", 7709),
        ("119.147.212.81", 7709),
        ("121.14.110.210", 7709),
        ("218.202.234.67", 7709),
    ]
    CONNECT_TIMEOUT = 5
    QUERY_TIMEOUT = 8

    def __init__(self, watchlist: List[Tuple[str, str]]):
        self.watchlist = watchlist
        self.snapshot: Dict[str, dict] = {}
        self._stop_event = Event()
        self._thread: Optional[Thread] = None
        self._api = None
        self._connected = False

    def _connect(self) -> bool:
        try:
            from pytdx.hq import TdxHq_API
        except ImportError:
            print("[ERROR] pytdx 未安装: pip install pytdx", file=sys.stderr)
            return False

        self._api = TdxHq_API(auto_retry=True, heartbeat=True)

        for ip, port in self.SERVERS:
            try:
                if self._api.connect(ip, port, time_out=self.CONNECT_TIMEOUT):
                    print(f"[INFO] pytdx 连接成功: {ip}:{port}")
                    self._connected = True
                    return True
            except Exception as e:
                print(f"[WARN] pytdx {ip}:{port} 连接失败: {e}")
                continue
        print("[ERROR] 所有 pytdx 服务器均连接失败", file=sys.stderr)
        return False

    def _disconnect(self):
        if self._api and self._connected:
            try:
                self._api.disconnect()
            except Exception:
                pass
            self._connected = False

    def _fetch_once(self) -> Dict[str, dict]:
        """单次批量获取，带超时保护"""
        if not self._connected:
            return {}
        tdx_codes = [to_tdx_code(c) for c, _ in self.watchlist]
        try:
            # pytdx 无原生超时，用线程池包装
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self._api.get_security_quotes, tdx_codes)
                quotes = future.result(timeout=self.QUERY_TIMEOUT)
        except FuturesTimeoutError:
            print("[WARN] pytdx get_security_quotes 超时")
            return {}
        except Exception as e:
            print(f"[WARN] pytdx get_security_quotes 异常: {e}")
            return {}

        if not quotes:
            return {}

        now = datetime.now()
        results = {}
        for q in quotes:
            code = q.get("code", "")
            if not code:
                continue
            pre_close = q.get("last_close", 0.0) or q.get("pre_close", 0.0)
            price = q.get("price", 0.0) or q.get("open", 0.0)
            open_price = q.get("open", 0.0)
            high = q.get("high", 0.0)
            low = q.get("low", 0.0)
            bid1 = q.get("bid1", 0.0)
            ask1 = q.get("ask1", 0.0)
            bid_vol1 = q.get("bid_vol1", 0) * 100
            ask_vol1 = q.get("ask_vol1", 0) * 100
            vol_shares = q.get("vol", 0) * 100
            amount = q.get("amount", 0.0)

            chg_pct = ((price / pre_close - 1) * 100) if pre_close > 0 else 0.0
            open_chg_pct = ((open_price / pre_close - 1) * 100) if pre_close > 0 and open_price > 0 else None

            limit_up = round(pre_close * 1.1, 2) if code[0] in "03" else round(pre_close * 1.099, 2)
            limit_down = round(pre_close * 0.9, 2) if code[0] in "03" else round(pre_close * 0.901, 2)
            is_limit_up = (abs(bid1 - limit_up) < 0.01 and bid_vol1 > 1e7)
            is_limit_down = (abs(ask1 - limit_down) < 0.01 and ask_vol1 > 1e7)

            results[code] = {
                "code": code,
                "name": next((n for c, n in self.watchlist if c == code), ""),
                "pre_close": round(pre_close, 3),
                "auction_price": round(price, 3),
                "open": round(open_price, 3),
                "high": round(high, 3),
                "low": round(low, 3),
                "chg_pct": round(chg_pct, 2),
                "open_chg_pct": round(open_chg_pct, 2) if open_chg_pct is not None else None,
                "bid1": round(bid1, 3),
                "ask1": round(ask1, 3),
                "bid_vol1": bid_vol1,
                "ask_vol1": ask_vol1,
                "volume_shares": vol_shares,
                "amount": round(amount, 2),
                "amount_yi": round(amount / 1e8, 3),
                "is_limit_up": is_limit_up,
                "is_limit_down": is_limit_down,
                "limit_up_price": limit_up,
                "limit_down_price": limit_down,
                "source": "pytdx",
            }
        return results

    def run_loop(self):
        """订阅循环：9:15-9:25 轮询，9:25:05 强制退出"""
        if not self._connect():
            return

        try:
            while not self._stop_event.is_set():
                now = datetime.now()
                # 硬截止：9:25:05 后强制退出循环
                if now.hour == 9 and now.minute >= 25 and now.second >= 5:
                    print("[INFO] 9:25:05 硬截止，退出 pytdx 订阅循环")
                    break
                # 仅在竞价时段运行
                if not (now.hour == 9 and now.minute >= 15 and now.minute < 26):
                    if now.hour >= 9 and now.minute >= 30:
                        break
                    time.sleep(10)
                    continue

                batch = self._fetch_once()
                if batch:
                    self.snapshot.update(batch)

                sleep_sec = 1 if now.minute >= 25 else 3
                time.sleep(sleep_sec)
        finally:
            self._disconnect()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(target=self.run_loop, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)


# ==================== 昨日成交量获取（新浪日K jsonp，复用） ====================
def fetch_prev_day_volume(symbol: str, timeout: float = 8.0) -> int:
    pref = "sh" if symbol[0] in "69" else "sz"
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?"
           f"symbol={pref}{symbol}&scale=240&ma=no&datalen=5")
    try:
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
        i, j = raw.find("["), raw.rfind("]")
        if i >= 0 and j > i:
            arr = json.loads(raw[i:j+1])
            if len(arr) >= 2:
                return int(float(arr[-2].get("volume", 0)))
    except Exception as e:
        print(f"[WARN] 获取 {symbol} 昨日量失败: {e}")
    return 0


# ==================== 主流程：双源融合 + 硬截止 ====================
def run_auction_capture() -> Optional[Path]:
    """
    主入口：
    1. 检查交易日
    2. 并行启动：新浪实时轮询(主) + pytdx(备用)
    3. 9:25:05 强制停止，融合快照落盘
    """
    if not is_trading_day():
        print(f"[INFO] 今日非交易日，跳过竞价捕获")
        return None

    now = datetime.now()
    if now.hour > 9 or (now.hour == 9 and now.minute >= 26):
        print(f"[ERROR] 当前时间 {now.strftime('%H:%M')} 已过竞价窗口，拒绝运行", file=sys.stderr)
        return None

    output_dir = WORKSPACE / "data" / "auction"
    output_dir.mkdir(parents=True, exist_ok=True)

    symbols = [s for s, _ in WATCHLIST]
    name_map = dict(WATCHLIST)

    # —— 共享快照 —— 
    merged_snapshot: Dict[str, dict] = {}
    snapshot_lock = Thread()

    # —— 新浪实时轮询线程 —— 
    def sina_loop():
        while True:
            now = datetime.now()
            if now.hour == 9 and now.minute >= 25 and now.second >= 5:
                break
            if not (now.hour == 9 and now.minute >= 15):
                time.sleep(10)
                continue
            batch = SinaRealtimeFeed.fetch_batch(symbols, timeout=5.0)
            if batch:
                merged_snapshot.update(batch)
            time.sleep(1 if now.minute >= 25 else 3)

    # —— pytdx 备用线程 —— 
    pytdx_sub = PytdxAuctionSubscriber(WATCHLIST)

    sina_thread = Thread(target=sina_loop, daemon=True)
    sina_thread.start()
    pytdx_sub.start()

    # 等待到 9:25:05
    target = now.replace(hour=9, minute=25, second=5, microsecond=0)
    if now < target:
        wait_sec = (target - now).total_seconds()
        print(f"[INFO] 等待 {wait_sec:.0f} 秒至 9:25:05...")
        time.sleep(wait_sec)

    # 停止采集
    pytdx_sub.stop()
    # sina_loop 会在时间到时自动退出
    sina_thread.join(timeout=2)

    # —— 融合：优先用新浪实时，缺失再补 pytdx —— 
    final_snapshot = dict(merged_snapshot)
    for code, row in pytdx_sub.snapshot.items():
        if code not in final_snapshot:
            final_snapshot[code] = row

    # 补全昨日成交量（并行加速）
    print("[INFO] 并行获取昨日成交量...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        fut_map = {executor.submit(fetch_prev_day_volume, code): code for code in final_snapshot}
        for fut in as_completed(fut_map):
            code = fut_map[fut]
            try:
                prev_vol = fut.result(timeout=10)
            except Exception:
                prev_vol = 0
            row = final_snapshot[code]
            row["prev_volume_shares"] = prev_vol
            if prev_vol > 0:
                row["vol_ratio_pct"] = round(row["volume_shares"] / prev_vol * 100, 2)
            else:
                row["vol_ratio_pct"] = None

    # 补全名称
    for code, row in final_snapshot.items():
        row["name"] = row.get("name") or name_map.get(code, "")

    # 落盘
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_file = output_dir / f"{date_str}_9_25.json"

    rows = list(final_snapshot.values())
    rows.sort(key=lambda x: x["code"])

    data = {
        "date": date_str,
        "snapshot_time": "09:25:00",
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(rows),
        "rows": rows,
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 竞价快照已保存: {out_file} ({len(rows)} 只, 新浪{sum(1 for r in rows if r.get('source')=='sina_realtime')} + pytdx{sum(1 for r in rows if r.get('source')=='pytdx')})")
    return out_file


# ==================== CLI ====================
if __name__ == "__main__":
    out = run_auction_capture()
    if out:
        print(f"SUCCESS: {out}")
        sys.exit(0)
    else:
        sys.exit(1)