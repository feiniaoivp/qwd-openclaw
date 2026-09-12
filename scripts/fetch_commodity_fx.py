#!/usr/bin/env python3
"""
大宗商品 & 汇率抓取脚本 - LME铜、硅钢、SF6、DXY、USDCNH、EURUSD、SAR/USD
运行方式: python scripts/fetch_commodity_fx.py
输出: data/commodity_fx_YYYY-MM-DD.json + TG简报(可选)
"""

import json
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import re
import sys

# 数据源配置
SOURCES = {
    # LME 铜 3M (USD/t) - 新浪全球期货 hf_CAD (伦铜) 直连
    "LME_Copper_3M": {
        "name": "LME铜 3M",
        "unit": "USD/t",
        "sina_global": "hf_CAD",
        "parser": "sina_lme_direct",
    },
    # 硅钢片 - 无公开免费专用源。新浪仅有钢材板块期货(热轧/螺纹)，
    # 作为「钢铁板块趋势」代理，非硅钢专用价，仅看趋势。
    "Silicon_Steel_50W470": {
        "name": "硅钢 50W470 (钢铁板块代理)",
        "unit": "元/吨",
        "parser": "sina_steel_index",
        "sina_code": "HC0",
    },
    "Silicon_Steel_50W600": {
        "name": "硅钢 50W600 (钢铁板块代理)",
        "unit": "元/吨",
        "parser": "sina_steel_index",
        "sina_code": "RB0",
    },
    # SF6 - 暂无免费公开源，需人工录入
    "SF6": {
        "name": "六氟化硫",
        "unit": "元/吨",
        "parser": "manual_entry",
    },
    # 汇率 - 新浪财经实时行情
    "USDCNH": {
        "name": "离岸人民币",
        "unit": "元",
        "sina_code": "fx_susdcnh",
        "parser": "sina_fx",
    },
    "EURUSD": {
        "name": "欧元/美元",
        "unit": "美元",
        "sina_code": "fx_seurusd",
        "parser": "sina_fx",
    },
    "USDSAR": {
        "name": "美元/沙特里亚尔",
        "unit": "里亚尔",
        "sina_code": "fx_susdsar",
        "parser": "sina_fx",
    },
    # 美元指数 - 新浪 DINIW 直连 (ICE DXY 实时)
    "DXY": {
        "name": "美元指数",
        "unit": "点",
        "sina_code": "DINIW",
        "parser": "sina_dxy_direct",
    },
}

# 新浪外汇/期货/指数实时行情接口
SINA_FX_URL = "https://hq.sinajs.cn/list="

def fetch_sina_fx(codes: list[str]) -> dict:
    """批量获取新浪外汇/期货/指数实时行情"""
    if not codes:
        return {}
    url = SINA_FX_URL + ",".join(codes)
    headers = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        print(f"[WARN] Sina fetch failed: {e}")
        return {}
    
    results = {}
    for line in text.strip().split(";"):
        if not line or "=" not in line:
            continue
        var_part, data_part = line.split("=", 1)
        # 提取代码: hq_str_fx_susdcnh 或 hq_str_SC0 或 hq_str_CU2501
        code_match = re.search(r'hq_str_([a-zA-Z0-9_]+)', var_part)
        if not code_match:
            continue
        code = code_match.group(1)
        data_part = data_part.strip('"')
        if not data_part:
            continue
        fields = data_part.split(",")
        try:
            # 统一解析：前10个字段通用
            # forex: 0=时间,1=最新价,2=买入价,3=卖出价,4=量,5=最高,6=?,7=最低,8=今开,9=名称,10=涨跌额,11=涨跌幅%
            # futures: 0=名称,1=时间,2=最新价,3=最高,4=最低,5=开盘,6=买价,7=卖价,8=最新价,9=?,10=昨收,...
            # index: 类似期货
            name = fields[9] if len(fields) > 9 and fields[9] else (fields[0] if len(fields) > 0 else code)
            price = float(fields[1]) if len(fields) > 1 and fields[1] else None
            change = float(fields[10]) if len(fields) > 10 and fields[10] else None
            pct_change = float(fields[11]) if len(fields) > 11 and fields[11] else None
            
            results[code] = {
                "name": name,
                "price": price,
                "change": change,
                "pct_change": pct_change,
                "high": float(fields[5]) if len(fields) > 5 and fields[5] else None,
                "low": float(fields[7]) if len(fields) > 7 and fields[7] else None,
                "open": float(fields[8]) if len(fields) > 8 and fields[8] else None,
            }
        except (ValueError, IndexError) as e:
            print(f"[WARN] Parse failed for {code}: {e}")
            pass
    return results

def fetch_er_api_rates() -> dict:
    """从 er-api.com (免费、无需Key) 获取汇率，用于计算 DXY 近似值"""
    url = "https://open.er-api.com/v6/latest/USD"
    headers = {"User-Agent": "Mozilla/5.0"}
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        data = json.loads(text)
        if data.get("result") == "success":
            return data.get("rates", {})
    except Exception as e:
        print(f"[WARN] er-api fetch failed: {e}")
    return {}

def calculate_dxy_approx(rates: dict) -> dict:
    """基于主要货币权重计算 DXY 近似值
    DXY 权重: EUR 57.6%, JPY 13.6%, GBP 11.9%, CAD 9.1%, SEK 4.2%, CHF 3.6%
    标准 ICE 公式: DXY = 50.14348112 * (EUR/USD)^0.576 * (USD/JPY)^0.136 * (GBP/USD)^0.119 * (USD/CAD)^0.091 * (USD/SEK)^0.042 * (USD/CHF)^0.036
    其中:
    - EUR/USD = USD per EUR (标准报价，如 1.1600)
    - USD/JPY = JPY per USD (如 153.76)
    - GBP/USD = USD per GBP (如 1.3520)
    - USD/CAD = CAD per USD (如 1.386)
    - USD/SEK = SEK per USD (如 9.70)
    - USD/CHF = CHF per USD (如 0.816)
    er-api 返回 1 USD = X foreign currency，需对 EUR/GBP 取倒数
    """
    eur_usd = 1 / rates.get('EUR', 0.862) if rates.get('EUR') else None
    usd_jpy = rates.get('JPY', 153.76)
    gbp_usd = 1 / rates.get('GBP', 0.7395) if rates.get('GBP') else None
    usd_cad = rates.get('CAD', 1.386)
    usd_sek = rates.get('SEK', 9.70)
    usd_chf = rates.get('CHF', 0.816)
    
    if not all([eur_usd, gbp_usd]):
        return {"error": "missing_required_rates"}
    
    dxy = 50.14348112 \
        * (eur_usd ** 0.576) \
        * (usd_jpy ** 0.136) \
        * (gbp_usd ** 0.119) \
        * (usd_cad ** 0.091) \
        * (usd_sek ** 0.042) \
        * (usd_chf ** 0.036)
    
    return {
        "name": "美元指数",
        "unit": "点",
        "price": round(dxy, 2),
        "source": "er_api_calculated",
        "components": {
            "EURUSD": round(eur_usd, 4),
            "USDJPY": round(usd_jpy, 2),
            "GBPUSD": round(gbp_usd, 4),
            "USDCAD": round(usd_cad, 4),
            "USDSEK": round(usd_sek, 4),
            "USDCHF": round(usd_chf, 4),
        }
    }

def fetch_dxy_direct() -> dict:
    """直连新浪 DINIW 获取 ICE 美元指数实时报价。

    为什么不用 er-api 手工计算:
      手工近似版本实测偏差巨大 (算得 126.34，真实值 99.09)，
      权重/报价方向极易出错，不可用于决策，故改为直连。

    字段: 0=时间, 1=最新价, 2=昨收, 3=开盘, 4=?, 5=?, 6=最高, 7=最低, 8=最新价, 9=名称, 10=日期
    """
    url = SINA_FX_URL + "DINIW"
    headers = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0",
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        return {"name": "美元指数", "unit": "点", "error": str(e)}

    for line in text.strip().split(";"):
        if "DINIW" not in line or "=" not in line:
            continue
        data_part = line.split("=", 1)[1].strip().strip('"')
        if not data_part:
            return {"name": "美元指数", "unit": "点", "error": "empty_quote"}
        f = data_part.split(",")

        def _num(idx):
            try:
                return float(f[idx]) if len(f) > idx and f[idx] else None
            except (ValueError, IndexError):
                return None

        price = _num(1)
        prev_close = _num(2)
        change = (price - prev_close) if (price is not None and prev_close) else None
        pct = (change / prev_close * 100) if (change is not None and prev_close) else None

        return {
            "name": "美元指数",
            "unit": "点",
            "price": round(price, 4) if price is not None else None,
            "change": round(change, 4) if change is not None else None,
            "pct_change": round(pct, 4) if pct is not None else None,
            "open": _num(3),
            "high": _num(6),
            "low": _num(7),
            "prev_close": prev_close,
            "quote_time": f[0] if len(f) > 0 else "",
            "date": f[10] if len(f) > 10 else "",
            "source": "sina_dxy_direct",
            "note": "ICE美元指数直连",
        }

    return {"name": "美元指数", "unit": "点", "error": "not_found"}

def fetch_dxy_multi_source() -> dict:
    """多源获取美元指数: 优先 er-api 计算"""
    rates = fetch_er_api_rates()
    if rates:
        result = calculate_dxy_approx(rates)
        if "error" not in result:
            result["extra_rates"] = {
                "USDCNH": rates.get("CNH"),
                "CNY": rates.get("CNY"),
                "SAR": rates.get("SAR"),
            }
            return result
    return {"name": "美元指数", "unit": "点", "error": "all_sources_failed"}

def fetch_shfe_copper() -> dict:
    """从新浪获取沪铜主力合约行情 (SHFE)，作为 LME 铜代理"""
    codes = ["CU2501", "CU2502", "CU2503", "CU2504", "CU2505", "CU2506",
             "CU2507", "CU2508", "CU2509", "CU2510", "CU2511", "CU2512"]
    url = SINA_FX_URL.replace("fx_", "") + ",".join(codes)
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        print(f"[WARN] SHFE copper fetch failed: {e}")
        return {"error": str(e)}
    
    best_contract = None
    best_volume = 0
    
    for line in text.strip().split(";"):
        if not line or "=" not in line:
            continue
        var_part, data_part = line.split("=", 1)
        code_match = re.search(r'hq_str_([A-Z]+\d+)', var_part)
        if not code_match:
            continue
        code = code_match.group(1)
        data_part = data_part.strip('"')
        if not data_part:
            continue
        fields = data_part.split(",")
        try:
            name = fields[0] if len(fields) > 0 else code
            price = float(fields[2]) if len(fields) > 2 and fields[2] else None
            high = float(fields[3]) if len(fields) > 3 and fields[3] else None
            low = float(fields[4]) if len(fields) > 4 and fields[4] else None
            open_price = float(fields[5]) if len(fields) > 5 and fields[5] else None
            bid = float(fields[6]) if len(fields) > 6 and fields[6] else None
            ask = float(fields[7]) if len(fields) > 7 and fields[7] else None
            prev_close = float(fields[10]) if len(fields) > 10 and fields[10] else None
            volume = int(fields[13]) if len(fields) > 13 and fields[13] else 0
            date = fields[18] if len(fields) > 18 else ""
            
            if price is None:
                continue
            
            if volume > best_volume:
                best_volume = volume
                change = price - prev_close if prev_close else 0
                pct_change = change / prev_close * 100 if prev_close else 0
                best_contract = {
                    "contract": code,
                    "name": name,
                    "price_cny": price,
                    "high_cny": high,
                    "low_cny": low,
                    "open_cny": open_price,
                    "prev_close_cny": prev_close,
                    "change_cny": change,
                    "pct_change": pct_change,
                    "volume": volume,
                    "date": date,
                }
        except (ValueError, IndexError):
            pass
    
    if not best_contract:
        return {"error": "no_valid_contract"}
    
    return best_contract

def fetch_lme_copper_proxy() -> dict:
    """[已弃用·仅作直连失败的兜底] LME 铜代理价 = 沪铜主力 / USDCNH。

    ⚠ 口径警告: 沪铜为含13%增值税的国内价, LME 为免税全球基准价,
    两者直接换算误差约 20% (实测 11835 vs 14219 USD/t)。
    正常路径应使用 fetch_lme_copper_direct(), 本函数仅在其失败时兜底并打标。
    """
    shfe = fetch_shfe_copper()
    if "error" in shfe:
        return {"name": "LME铜 3M (SHFE代理)", "unit": "USD/t", "error": shfe["error"]}
    
    fx_codes = ["fx_susdcnh"]
    url = SINA_FX_URL + ",".join(fx_codes)
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    req = Request(url, headers=headers)
    usdcnh = 7.0
    try:
        with urlopen(req, timeout=5) as resp:
            text = resp.read().decode("gbk", errors="ignore")
        for line in text.strip().split(";"):
            if "fx_susdcnh" in line and "=" in line:
                fields = line.split("=")[1].strip('"').split(",")
                if len(fields) > 1 and fields[1]:
                    usdcnh = float(fields[1])
                break
    except Exception:
        pass
    
    price_usd = shfe["price_cny"] / usdcnh if usdcnh > 0 else None
    change_usd = shfe["change_cny"] / usdcnh if usdcnh > 0 and shfe.get("change_cny") else None
    
    return {
        "name": "LME铜 3M (SHFE代理)",
        "unit": "USD/t",
        "price": round(price_usd, 2) if price_usd else None,
        "change": round(change_usd, 2) if change_usd else None,
        "pct_change": shfe.get("pct_change"),
        "high_usd": round(shfe["high_cny"] / usdcnh, 2) if usdcnh > 0 else None,
        "low_usd": round(shfe["low_cny"] / usdcnh, 2) if usdcnh > 0 else None,
        "open_usd": round(shfe["open_cny"] / usdcnh, 2) if usdcnh > 0 else None,
        "prev_close_usd": round(shfe["prev_close_cny"] / usdcnh, 2) if usdcnh > 0 else None,
        "volume": shfe.get("volume"),
        "contract": shfe.get("contract"),
        "date": shfe.get("date"),
        "source": "sina_shfe_proxy",
        "fx_rate_used": usdcnh,
        "shfe_price_cny": shfe.get("price_cny"),
    }

def fetch_lme_copper_direct(sina_global_code: str = "hf_CAD") -> dict:
    """直连新浪全球期货获取 LME 铜(伦铜)真实报价 (USD/t)。

    为什么不再用沪铜代理:
      沪铜报价为含13%增值税的国内价，LME 为不含税的全球基准价，
      两者口径不同，直接除以汇率误差约 20% (实测 11835 vs 14219)，
      故弃用 SHFE 代理，改为直连 LME 盘中报价。

    新浪 hf_ 全球期货字段:
      0=最新价, 1=?, 2=买价, 3=最高, 4=?, 5=最低,
      6=时间, 7=昨收, 8=今开, 9=?, 10=?, 11=?,
      12=日期, 13=名称, 14=成交量
    """
    url = SINA_FX_URL + sina_global_code
    headers = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0",
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        return {"name": "LME铜 3M", "unit": "USD/t", "error": str(e)}

    for line in text.strip().split(";"):
        if "=" not in line:
            continue
        var_part, data_part = line.split("=", 1)
        if sina_global_code not in var_part:
            continue
        data_part = data_part.strip().strip('"')
        if not data_part:
            return {"name": "LME铜 3M", "unit": "USD/t", "error": "empty_quote"}
        f = data_part.split(",")

        def _num(idx, cast=float):
            try:
                return cast(f[idx]) if len(f) > idx and f[idx] not in ("", None) else None
            except (ValueError, IndexError):
                return None

        price = _num(0)
        prev_close = _num(7)
        change = (price - prev_close) if (price is not None and prev_close) else None
        pct = (change / prev_close * 100) if (change is not None and prev_close) else None

        return {
            "name": "LME铜 3M",
            "unit": "USD/t",
            "price": round(price, 2) if price is not None else None,
            "change": round(change, 2) if change is not None else None,
            "pct_change": round(pct, 4) if pct is not None else None,
            "high_usd": _num(3),
            "low_usd": _num(5),
            "open_usd": _num(8),
            "prev_close_usd": prev_close,
            "bid_usd": _num(2),
            "volume": _num(14, int),
            "quote_time": f[6] if len(f) > 6 else "",
            "date": f[12] if len(f) > 12 else "",
            "source": "sina_lme_direct",
            "source_code": sina_global_code,
            "note": "LME伦铜直连 (非沪铜代理)",
        }

    return {"name": "LME铜 3M", "unit": "USD/t", "error": "not_found"}

def fetch_steel_index_proxy(sina_code: str = "HC0") -> dict:
    """获取新浪钢材板块期货(热轧卷板HC0/螺纹钢RB0)作为硅钢趋势代理。

    ⚠ 口径说明: 新浪无硅钢专用免费行情；SC0 实为原油连续，
    曾误用为硅钢代理(显示 606.5 元/吨实为油价)，已纠正。
    本代理仅反映钢铁板块景气方向，绝非硅钢成交价，勿直接当成本数。
    """
    url = SINA_FX_URL.replace("fx_", "") + sina_code
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        print(f"[WARN] Steel index fetch failed: {e}")
        return {"error": str(e)}

    for line in text.strip().split(";"):
        if sina_code in line and "=" in line:
            data_part = line.split("=")[1].strip('"')
            fields = data_part.split(",")
            try:
                # 期货字段: 0=名称,2=最新价,3=最高,4=最低,5=开盘,10=昨收,18=日期
                name = fields[0] if len(fields) > 0 else "钢材"
                price = float(fields[2]) if len(fields) > 2 and fields[2] else None
                prev_close = float(fields[10]) if len(fields) > 10 and fields[10] else None
                if price is None:
                    return {"error": "no_price"}
                change = price - prev_close if prev_close else 0
                pct_change = change / prev_close * 100 if prev_close else 0
                return {
                    "name": f"钢铁板块代理({name})",
                    "unit": "元/吨",
                    "price": price,
                    "change": round(change, 2),
                    "pct_change": round(pct_change, 4),
                    "high": float(fields[3]) if len(fields) > 3 and fields[3] else None,
                    "low": float(fields[4]) if len(fields) > 4 and fields[4] else None,
                    "open": float(fields[5]) if len(fields) > 5 and fields[5] else None,
                    "prev_close": prev_close,
                    "date": fields[18] if len(fields) > 18 else "",
                    "source": f"sina_steel_proxy_{sina_code}",
                    "note": "钢铁板块趋势代理，非硅钢专用价，勿当成本数",
                }
            except (ValueError, IndexError) as e:
                return {"error": f"parse_failed: {e}"}

    return {"error": "no_data"}

def main():
    today = datetime.now().strftime("%Y-%m-%d")
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    
    output = {
        "date": today,
        "timestamp": datetime.now().isoformat(),
        "commodities": {},
        "fx": {},
    }
    
    print(f"=== 大宗商品&汇率抓取 {today} ===\n")
    
    # 1. 汇率 (新浪实时)
    fx_codes = [v["sina_code"] for k, v in SOURCES.items() if v.get("parser") == "sina_fx" and v.get("sina_code")]
    print(f"[FX] Fetching {len(fx_codes)} codes from Sina...")
    fx_data = fetch_sina_fx(fx_codes)
    
    for key, config in SOURCES.items():
        if config.get("parser") != "sina_fx":
            continue
        code = config["sina_code"]
        if code in fx_data:
            d = fx_data[code]
            output["fx"][key] = {
                "name": config["name"],
                "unit": config["unit"],
                "price": d["price"],
                "change": d.get("change"),
                "pct_change": d.get("pct_change"),
                "high": d.get("high"),
                "low": d.get("low"),
                "open": d.get("open"),
                "source": "sina_fx",
            }
            print(f"  ✓ {config['name']}: {d['price']} ({d.get('pct_change',0):+.4f}%)")
        else:
            output["fx"][key] = {"name": config["name"], "unit": config["unit"], "error": "no_data"}
            print(f"  ✗ {config['name']}: no data")
    
    # 2. 美元指数 (新浪 DINIW 直连，失败则回退 er-api 近似)
    print(f"\n[FX] Fetching DXY from Sina DINIW (direct)...")
    dxy_data = fetch_dxy_direct()
    if dxy_data.get("price") is None:
        print(f"  ✗ 直连失败({dxy_data.get('error')})，回退 er-api 近似计算(可能偏差大)")
        fb = fetch_dxy_multi_source()
        if fb.get("price") is not None:
            fb["fallback"] = True
            fb["fallback_warning"] = "主源 sina_dxy_direct 失败，此值为 er-api 近似，审慎使用"
            dxy_data = fb
    if dxy_data.get("price") is not None:
        output["fx"]["DXY"] = dxy_data
        print(f"  ✓ 美元指数: {dxy_data['price']} (源: {dxy_data['source']})")
        if "extra_rates" in dxy_data:
            for k, v in dxy_data["extra_rates"].items():
                if v and k not in output["fx"]:
                    output["fx"][k] = {
                        "name": {"USDCNH": "离岸人民币", "CNY": "在岸人民币", "SAR": "沙特里亚尔"}[k],
                        "unit": "元" if k != "SAR" else "里亚尔",
                        "price": v,
                        "source": "er_api",
                    }
    else:
        output["fx"]["DXY"] = {"name": "美元指数", "unit": "点", "error": dxy_data.get("error", "no_data")}
        print(f"  ✗ 美元指数: {output['fx']['DXY'].get('error')}")
    
    # 3. 大宗商品
    commodity_keys = [k for k, v in SOURCES.items() if v.get("parser") not in ("sina_fx", "dxy_multi_source", "sina_dxy_direct")]
    for key in commodity_keys:
        config = SOURCES[key]
        parser = config.get("parser")
        
        if key == "LME_Copper_3M":
            code = config.get("sina_global", "hf_CAD")
            print(f"[COMMODITY] {config['name']} - parser: {parser} (直连 {code})")
            lme_data = fetch_lme_copper_direct(code)
            output["commodities"][key] = lme_data
            if lme_data.get("price"):
                print(f"  ✓ {config['name']}: {lme_data['price']} USD/t ({lme_data.get('date')} {lme_data.get('quote_time')}, 源: {lme_data['source']})")
            else:
                # 直连失败时回退沪铜代理，但必须显著标注口径差异
                print(f"  ✗ 直连失败({lme_data.get('error')})，回退沪铜代理(误差~20%)")
                proxy = fetch_lme_copper_proxy()
                if proxy.get("price"):
                    proxy["fallback"] = True
                    proxy["fallback_warning"] = "主源 sina_lme_direct 失败，此值为沪铜代理，与真实LME口径差约20%，勿用于决策"
                    output["commodities"][key] = proxy
                    print(f"  ⚠ 回退值: {proxy['price']} USD/t (仅供参考)")
                else:
                    print(f"  ✗ 回退也失败: {proxy.get('error')}")
        
        elif key in ("Silicon_Steel_50W470", "Silicon_Steel_50W600"):
            scode = config.get("sina_code", "HC0")
            print(f"[COMMODITY] {config['name']} - parser: {parser} (代理 {scode})")
            steel_data = fetch_steel_index_proxy(scode)
            if steel_data.get("price"):
                output["commodities"][key] = steel_data
                print(f"  ✓ {config['name']}: {steel_data['price']} 元/吨 ({scode} {steel_data.get('name','')})")
            else:
                output["commodities"][key] = {"name": config["name"], "unit": config["unit"], "error": steel_data.get("error", "no_data")}
                print(f"  ✗ {config['name']}: {steel_data.get('error', 'no_data')}")
        
        elif key == "SF6":
            print(f"[COMMODITY] {config['name']} - parser: manual_entry (需人工录入)")
            output["commodities"][key] = {
                "name": config["name"],
                "unit": config["unit"],
                "price": None,
                "source": "manual_entry",
                "status": "需人工录入 (生意社/百川盈孚需会员)",
            }
            print(f"  ⚠ {config['name']}: 待人工录入")
        
        else:
            print(f"[COMMODITY] {config['name']} - parser: {parser} (TODO: implement)")
            output["commodities"][key] = {
                "name": config["name"],
                "unit": config["unit"],
                "price": None,
                "source": parser,
                "status": "parser_not_implemented",
            }
    
    # 保存
    output_file = data_dir / f"commodity_fx_{today}.json"
    output_file.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\n[SAVE] -> {output_file}")
    
    # 生成TG简报
    lines = [f"📊 大宗商品&汇率快照 {today}"]
    lines.append("")
    lines.append("【汇率】")
    for key in ["DXY", "USDCNH", "EURUSD", "USDSAR"]:
        if key in output["fx"] and output["fx"][key].get("price") is not None:
            d = output["fx"][key]
            pct = d.get('pct_change', 0)
            lines.append(f"  {d['name']}: {d['price']} {d['unit']} ({pct:+.4f}%)")
        else:
            lines.append(f"  {SOURCES[key]['name']}: 数据获取失败")
    
    lines.append("")
    lines.append("【大宗商品】")
    for key in commodity_keys:
        d = output["commodities"][key]
        if d.get("price"):
            lines.append(f"  {d['name']}: {d['price']} {d['unit']}")
        else:
            status = d.get("status", d.get("error", "待接入"))
            lines.append(f"  {d['name']}: {status}")
    
    brief = "\n".join(lines)
    print(f"\n=== TG Brief ===\n{brief}\n")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())