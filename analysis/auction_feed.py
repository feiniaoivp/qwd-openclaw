#!/usr/bin/env python3
"""
集合竞价数据订阅器 (pytdx 版)
- 9:15-9:25 订阅自选池 30 只实时竞价行情
- 9:25:00 快照落盘到 data/auction/YYYY-MM-DD_9_25.json
- 支持断线重连、非交易日跳过、停牌过滤
- 复用现有 data_source_policy.py 的路由策略（新浪实时为主）
"""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread, Event
from typing import Dict, List, Optional, Tuple

WORKSPACE = Path("/Users/duguke/.openclaw/workspace")
sys.path.insert(0, str(WORKSPACE))

# 导入现有工具
from analysis.service import is_trading_day

# ==================== 自选池（与 memory/watchlist.md 保持一致） ====================
WATCHLIST = [
    ("600030", "中信证券"), ("601066", "中信建投"), ("600036", "招商银行"),
    ("601995", "中金公司"), ("000987", "越秀资本"),
    ("600584", "长电科技"), ("688981", "中芯国际"), ("002156", "通富微电"),
    ("002413", "雷科防务"),
    ("300014", "亿纬锂能"), ("002466", "天齐锂业"), ("601865", "福莱特"),
    ("300285", "国瓷材料"), ("603308", "应流股份"), ("300124", "汇川技术"),
    ("601100", "恒立液压"), ("002318", "久立特材"), ("300719", "安达维尔"),
    ("002335", "科华数据"),
    ("300748", "金力永磁"),
    ("002180", "奔图科技"), ("300847", "中船汉光"),
    ("600160", "巨化股份"), ("600346", "恒力石化"), ("000708", "中信特钢"),
    ("600660", "福耀玻璃"), ("600570", "恒生电子"), ("605566", "福莱蒽特"),
    ("000157", "中联重科"), ("601061", "中信金属"),
]

# 市场代码映射：pytdx 需要 market(0=sz, 1=sh) + code
def to_tdx_code(symbol: str) -> Tuple[int, str]:
    """返回 (market, code)"""
    if symbol[0] in ("6", "9"):  # sh: 60xxxx, 68xxxx, 900xxx
        return 1, symbol
    return 0, symbol  # sz: 00xxxx, 30xxxx, 002xxx, 000xxx


# ==================== pytdx 竞价订阅核心 ====================
class AuctionSubscriber:
    """基于 pytdx 的集合竞价实时订阅"""

    def __init__(self, watchlist: List[Tuple[str, str]], output_dir: Path):
        self.watchlist = watchlist
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.snapshot: Dict[str, dict] = {}  # code -> 竞价字段
        self._stop_event = Event()
        self._thread: Optional[Thread] = None
        self._api = None
        self._connected = False

    def _connect(self) -> bool:
        """连接通达信行情服务器（自动选最快）"""
        try:
            from pytdx.hq import TdxHq_API
            from pytdx.params import TDXParams
        except ImportError:
            print("[ERROR] pytdx 未安装: pip install pytdx", file=sys.stderr)
            return False

        self._api = TdxHq_API(auto_retry=True, heartbeat=True)

        # 常用服务器列表（按延迟排序，实测可用的在前）
        servers = [
            ("180.153.18.170", 7709),   # 广东电信 (实测可用)
            ("180.153.18.171", 7709),   # 广东电信备用
            ("119.147.212.81", 7709),   # 上海双线
            ("121.14.110.210", 7709),   # 北京联通
            ("218.202.234.67", 7709),   # 上海电信
        ]

        for ip, port in servers:
            try:
                if self._api.connect(ip, port, time_out=5):
                    print(f"[INFO] pytdx 连接成功: {ip}:{port}")
                    self._connected = True
                    return True
            except Exception as e:
                print(f"[WARN] {ip}:{port} 连接失败: {e}")
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

    def _subscribe_loop(self):
        """订阅循环：9:15-9:25 每 3 秒刷新一次"""
        if not self._connect():
            return

        tdx_codes = [to_tdx_code(c) for c, _ in self.watchlist]

        try:
            while not self._stop_event.is_set():
                now = datetime.now()
                # 仅在竞价时段运行
                if not (9 <= now.hour < 9 or (now.hour == 9 and now.minute < 30)):
                    # 非竞价时段：休眠到下一个竞价窗口或退出
                    if now.hour >= 9 and now.minute >= 30:
                        break  # 竞价结束，退出循环
                    time.sleep(10)
                    continue

                # 批量获取报价（pytdx get_security_quotes 支持多只）
                try:
                    quotes = self._api.get_security_quotes(tdx_codes)
                except Exception as e:
                    print(f"[WARN] get_security_quotes 异常: {e}")
                    time.sleep(3)
                    continue

                if not quotes:
                    time.sleep(3)
                    continue

                # 解析并更新快照
                for q in quotes:
                    code = q.get("code", "")
                    if not code:
                        continue

                    # pytdx 返回字段（竞价阶段关键字段）：
                    # price=当前价(竞价匹配价), open=开盘价(竞价参考价), high/low=竞价高低
                    # bid1/bid_vol1, ask1/ask_vol1, vol=成交量(手), amount=成交额(元)
                    # pre_close=昨收
                    pre_close = q.get("last_close", 0.0) or q.get("pre_close", 0.0)
                    price = q.get("price", 0.0) or q.get("open", 0.0)
                    open_price = q.get("open", 0.0)
                    high = q.get("high", 0.0)
                    low = q.get("low", 0.0)
                    bid1 = q.get("bid1", 0.0)
                    ask1 = q.get("ask1", 0.0)
                    bid_vol1 = q.get("bid_vol1", 0) * 100  # 转股
                    ask_vol1 = q.get("ask_vol1", 0) * 100
                    vol_shares = q.get("vol", 0) * 100
                    amount = q.get("amount", 0.0)

                    # 竞价涨跌幅
                    chg_pct = ((price / pre_close - 1) * 100) if pre_close > 0 else 0.0
                    open_chg_pct = ((open_price / pre_close - 1) * 100) if pre_close > 0 and open_price > 0 else None

                    # 一字板判断：买一/卖一价 = 涨跌停价 且 封单巨大
                    limit_up = round(pre_close * 1.1, 2) if code[0] in "03" else round(pre_close * 1.099, 2)
                    limit_down = round(pre_close * 0.9, 2) if code[0] in "03" else round(pre_close * 0.901, 2)
                    is_limit_up = (abs(bid1 - limit_up) < 0.01 and bid_vol1 > 1e7)
                    is_limit_down = (abs(ask1 - limit_down) < 0.01 and ask_vol1 > 1e7)

                    self.snapshot[code] = {
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
                        "update_time": now.strftime("%H:%M:%S"),
                    }

                # 9:25:00 前每 3 秒刷新；9:25 后加速到 1 秒
                sleep_sec = 1 if now.minute >= 25 else 3
                time.sleep(sleep_sec)

        finally:
            self._disconnect()

    def start(self):
        """启动订阅线程"""
        if self._thread and self._thread.is_alive():
            print("[WARN] 订阅已在运行")
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._subscribe_loop, daemon=True)
        self._thread.start()
        print("[INFO] 竞价订阅已启动")

    def stop_and_save(self) -> Path:
        """停止订阅并保存 9:25 快照"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

        # 落盘
        date_str = datetime.now().strftime("%Y-%m-%d")
        out_file = self.output_dir / f"{date_str}_9_25.json"

        # 补全昨日成交量（用于量比计算）——从新浪日K接口获取
        enriched = self._enrich_with_prev_volume(self.snapshot)

        data = {
            "date": date_str,
            "snapshot_time": "09:25:00",
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(enriched),
            "rows": enriched,
        }

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"[INFO] 竞价快照已保存: {out_file} ({len(enriched)} 只)")
        return out_file

    def _enrich_with_prev_volume(self, snapshot: Dict) -> List[dict]:
        """补充昨日成交量（股），计算竞价量/昨日全天量比"""
        enriched = []
        for code, row in snapshot.items():
            prev_vol = self._fetch_prev_day_volume(code)
            vol_ratio = (row["volume_shares"] / prev_vol * 100) if prev_vol > 0 else None

            out_row = dict(row)
            out_row["prev_volume_shares"] = prev_vol
            out_row["vol_ratio_pct"] = round(vol_ratio, 2) if vol_ratio is not None else None
            enriched.append(out_row)

        # 按代码排序
        enriched.sort(key=lambda x: x["code"])
        return enriched

    def _fetch_prev_day_volume(self, symbol: str) -> int:
        """获取昨日成交量（股）——新浪日K jsonp 接口（与 auction_scan.py 复用）"""
        import re, urllib.request
        pref = "sh" if symbol[0] in "69" else "sz"
        url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService."
               f"getKLineData?symbol={pref}{symbol}&scale=240&ma=no&datalen=5")
        try:
            req = urllib.request.Request(url, headers={
                "Referer": "https://finance.sina.com.cn",
                "User-Agent": "Mozilla/5.0"
            })
            raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "ignore")
            m = re.search(r'=\s*\((\[.*\])\)\s*;', raw, re.S)
            if m:
                arr = json.loads(m.group(1))
            else:
                i, j = raw.find("["), raw.rfind("]")
                if i < 0 or j <= i:
                    return 0
                arr = json.loads(raw[i:j+1])
            if len(arr) >= 2:
                return int(float(arr[-2].get("volume", 0)))
        except Exception as e:
            print(f"[WARN] 获取 {symbol} 昨日量失败: {e}")
        return 0


# ==================== 便捷函数（供 cron 直接调用） ====================
def run_auction_capture() -> Optional[Path]:
    """
    主入口：运行一次完整的竞价捕获流程
    - 检查交易日
    - 启动订阅
    - 等待到 9:25:05（留缓冲）保存快照
    - 返回输出文件路径
    """
    if not is_trading_day():
        print(f"[INFO] 今日非交易日，跳过竞价捕获")
        return None

    now = datetime.now()
    # 如果当前已过 9:25，直接报错（避免误跑）
    if now.hour > 9 or (now.hour == 9 and now.minute >= 26):
        print(f"[ERROR] 当前时间 {now.strftime('%H:%M')} 已过竞价窗口，拒绝运行", file=sys.stderr)
        return None

    sub = AuctionSubscriber(WATCHLIST, WORKSPACE / "data" / "auction")
    sub.start()

    # 等待到 9:25:05
    target = now.replace(hour=9, minute=25, second=5, microsecond=0)
    if now < target:
        wait_sec = (target - now).total_seconds()
        print(f"[INFO] 等待 {wait_sec:.0f} 秒至 9:25:05...")
        time.sleep(wait_sec)

    return sub.stop_and_save()


# ==================== CLI ====================
if __name__ == "__main__":
    out = run_auction_capture()
    if out:
        print(f"SUCCESS: {out}")
        sys.exit(0)
    else:
        sys.exit(1)