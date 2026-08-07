#!/usr/bin/env python3
"""集合竞价扫描 9:20-9:25 — 新浪实时接口(带Referer) 拉自选池30只竞价数据。
对比昨日成交量/额估算量能放大倍率(竞价量 vs 昨日同期无法精确, 用竞价量/昨日全天量做参照)。
"""
import urllib.request, json, sys
from datetime import datetime

WORKSPACE = "/Users/duguke/.openclaw/workspace"
sys.path.insert(0, WORKSPACE)

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

def fetch_hq_dict(codes: list[str]) -> dict:
    import urllib.request
    out = {}
    for i in range(0, len(codes), 60):
        batch = codes[i:i+60]
        url = "http://hq.sinajs.cn/list=" + ",".join(batch)
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        raw = urllib.request.urlopen(req, timeout=12).read().decode("gbk")
        for line in raw.splitlines():
            if '="' not in line:
                continue
            key = line.split('hq_str_')[1].split('=')[0]
            val = line.split('"')[1].split(',')
            if len(val) < 10:
                continue
            symbol = key[2:]
            def f(x):
                try: return float(x)
                except: return 0.0
            out[symbol] = {
                "name": val[0],
                "open": f(val[1]), "pre_close": f(val[2]), "last": f(val[3]),
                "high": f(val[4]), "low": f(val[5]),
                "bid1": f(val[6]), "ask1": f(val[7]),
                "volume": int(float(val[8])), "amount": f(val[9]),
                "date": val[30] if len(val) > 30 else "",
                "time": val[31] if len(val) > 31 else "",
            }
    return out

def fetch_daily_vol(symbol: str) -> float:
    """昨日成交量(手/股). 新浪日K接口. 返回昨日 volume(股)."""
    import re, urllib.request
    pref = "sh" if symbol[0] in "69" else "sz"
    u = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService."
         f"getKLineData?symbol={pref}{symbol}&scale=240&ma=no&datalen=5")
    req = urllib.request.Request(u, headers={"Referer": "https://finance.sina.com.cn",
                                             "User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
    m = re.search(r'=\s*\((\[.*\])\)\s*;', raw, re.S)
    if not m:
        i, j = raw.find("["), raw.rfind("]")
        if i < 0 or j <= i:
            return 0.0
        arr = json.loads(raw[i:j+1])
    else:
        arr = json.loads(m.group(1))
    if len(arr) < 2:
        return 0.0
    prev = arr[-2]
    return float(prev.get("volume", 0))

def main():
    codes = []
    for code, _n in WATCHLIST:
        codes.append(("sh" if code[0] in "69" else "sz") + code)
    hq = fetch_hq_dict(codes)
    now = datetime.now()
    rows = []
    for code, name in WATCHLIST:
        r = hq.get(code)
        if r is None or r["last"] == 0:
            rows.append({"code": code, "name": name, "error": "无数据"})
            continue
        # 竞价匹配价 = last (9:20-9:25 此时last为竞价匹配价格)
        # open字段在竞价阶段会显示当日竞价参考/开盘价
        pre_close = r["pre_close"]
        auction = r["last"] if r["last"] else r["open"]
        chg = (auction / pre_close - 1) * 100 if pre_close else 0.0
        open_chg = (r["open"] / pre_close - 1) * 100 if pre_close and r["open"] else None
        prev_vol = 0.0
        try:
            prev_vol = fetch_daily_vol(code)
        except Exception:
            prev_vol = 0.0
        # 竞价量能: volume为手, 转股; 竞价量/昨日全天量 => 放大倍率(竞价通常仅占全天小比例)
        vol_shares = r["volume"] * 100
        vol_ratio = (vol_shares / prev_vol * 100) if prev_vol > 0 else None
        rows.append({
            "code": code, "name": name,
            "auction_price": auction, "pre_close": pre_close,
            "chg_pct": round(chg, 2),
            "open": r["open"], "open_pct": round(open_chg, 2) if open_chg is not None else None,
            "volume_shares": vol_shares, "amount_wan": round(r["amount"] / 1e4, 1),
            "amount_yi": round(r["amount"] / 1e8, 2),
            "bid1": r["bid1"], "ask1": r["ask1"],
            "vol_ratio_pct": round(vol_ratio, 2) if vol_ratio is not None else None,
            "time": r["time"],
        })
    out = {"server_time": now.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows}
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
