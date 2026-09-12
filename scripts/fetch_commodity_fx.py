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
    # LME 铜 3M (USD/t) - 使用沪铜主力合约代理
    "LME_Copper_3M": {
        "name": "LME铜 3M",
        "unit": "USD/t",
        "parser": "sina_shfe_proxy",
    },
    # 硅钢片 - 使用新浪钢材指数 SC0 作为代理
    "Silicon_Steel_50W470": {
        "name": "硅钢 50W470 (SC0代理)",
        "unit": "元/吨",
        "parser": "sina_steel_index",
        "sina_code": "SC0",
    },
    "Silicon_Steel_50W600": {
        "name": "硅钢 50W600 (SC0代理)",
        "unit": "元/吨",
        "parser": "sina_steel_index",
        "sina_code": "SC0",
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
    # 美元指数 - 多源兜底: er-api.com (免费) -> 近似计算
    "DXY": {
        "name": "美元指数",
        "unit": "点",
        "sina_code": None,
        "parser": "dxy_multi_source",
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
    """获取 LME 铜 3M 代理价格 (基于沪铜主力 + 汇率换算)"""
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

def fetch_steel_index_proxy() -> dict:
    """获取新浪钢材指数 SC0 作为硅钢价格代理"""
    codes = ["SC0"]
    url = SINA_FX_URL.replace("fx_", "") + ",".join(codes)
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        print(f"[WARN] Steel index fetch failed: {e}")
        return {"error": str(e)}
    
    for line in text.strip().split(";"):
        if "SC0" in line and "=" in line:
            data_part = line.split("=")[1].strip('"')
            fields = data_part.split(",")
            try:
                # SC0 字段: 0=名称, 1=时间, 2=最新价, 3=最高, 4=最低, 5=开盘, ...
                name = fields[0] if len(fields) > 0 else "钢材指数"
                price = float(fields[2]) if len(fields) > 2 and fields[2] else None
                high = float(fields[3]) if len(fields) > 3 and fields[3] else None
                low = float(fields[4]) if len(fields) > 4 and fields[4] else None
                open_price = float(fields[5]) if len(fields) > 5 and fields[5] else None
                prev_close = float(fields[10]) if len(fields) > 10 and fields[10] else None
                date = fields[18] if len(fields) > 18 else ""
                
                if price is None:
                    return {"error": "no_price"}
                
                change = price - prev_close if prev_close else 0
                pct_change = change / prev_close * 100 if prev_close else 0
                
                return {
                    "name": "硅钢代理(钢材指数SC0)",
                    "unit": "元/吨",
                    "price": price,
                    "change": change,
                    "pct_change": pct_change,
                    "high": high,
                    "low": low,
                    "open": open_price,
                    "prev_close": prev_close,
                    "date": date,
                    "source": "sina_steel_index_proxy",
                    "note": "SC0钢材综合指数，非硅钢专用价格，仅供趋势参考",
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
    
    # 2. 美元指数 (er-api 计算)
    print(f"\n[FX] Fetching DXY from er-api (calculated)...")
    dxy_data = fetch_dxy_multi_source()
    if dxy_data.get("price") is not None:
        output["fx"]["DXY"] = dxy_data
        print(f"  ✓ 美元指数: {dxy_data['price']} (calculated from major pairs)")
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
    commodity_keys = [k for k, v in SOURCES.items() if v.get("parser") not in ("sina_fx", "dxy_multi_source")]
    for key in commodity_keys:
        config = SOURCES[key]
        parser = config.get("parser")
        
        if key == "LME_Copper_3M":
            print(f"[COMMODITY] {config['name']} - parser: sina_shfe_proxy (implemented)")
            lme_data = fetch_lme_copper_proxy()
            output["commodities"][key] = lme_data
            if lme_data.get("price"):
                print(f"  ✓ {config['name']}: {lme_data['price']} USD/t (SHFE {lme_data.get('contract')}: {lme_data.get('shfe_price_cny')} CNY/t @ {lme_data.get('fx_rate_used')} CNY/USD)")
            else:
                print(f"  ✗ {config['name']}: {lme_data.get('error', 'parse_failed')}")
        
        elif key in ("Silicon_Steel_50W470", "Silicon_Steel_50W600"):
            print(f"[COMMODITY] {config['name']} - parser: sina_steel_index_proxy (implemented)")
            steel_data = fetch_steel_index_proxy()
            if steel_data.get("price"):
                output["commodities"][key] = steel_data
                print(f"  ✓ {config['name']}: {steel_data['price']} 元/吨 (SC0指数)")
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