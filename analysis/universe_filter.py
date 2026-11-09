#!/usr/bin/env python3
"""
Universe Filter — 四层漏斗 L1/L2 生产实现（2026-11-09 用户拍板方案落地）
=========================================================================
分层逻辑（对齐拍板原文）:
  L1 硬性准入: 全市场 → 可交易池
    - 流动性: 近 20 交易日日均成交额 ≥ 5000 万
    - 质量: 剔 ST/*ST/退市/北交所/上市<6个月
    - 产出: data/universe/universe_l1.json

  L2 质量锚定: 可交易池 → 核心池
    - 锚 1: 指数成分 (hs300+sz50 = core 层; zz500+zz1000 = extended 层)
    - 锚 2: 现有关注池 36 只无条件保留（种子保护，无论是否在指数内）
    - 产出: data/universe/universe_core.json

数据源实测（2026-11-09）:
  - 指数成分: akshare index_stock_cons_csindex ✓（中证官网，300/500/1000/50 全通）
  - 全市场名单+ST标记: baostock query_all_stock ✓（5510 只 A 股）
  - 上市日期: baostock query_stock_basic ✓
  - 流动性: baostock query_history_k_data_plus 逐票 20 日（慢，走 bootstrap 后台）
  - 东财 spot_em: ✗ ProxyError（历史拦截延续），不依赖

运行模式:
  python3 analysis/universe_filter.py --bootstrap   # 全量: 名单+成分+流动性(慢,建议nohup)
  python3 analysis/universe_filter.py --l1          # 用缓存构建 L1（秒级）
  python3 analysis/universe_filter.py --l2          # 用 L1 构建 L2 分层（秒级）
  python3 analysis/universe_filter.py --status      # 查看当前漏斗各层状态

铁律:
  - 禁止缓存空值: 流动性拉取失败不落 0，标 unknown 不参与过滤误杀
  - 写前 .bak 备份
  - 全部产出带 fetched_at 时间戳与 coverage 统计
"""
#!/usr/bin/env python3
import os
import sys
import json
import shutil
from datetime import datetime, timedelta

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

UNI_DIR = os.path.join(WORKSPACE, "data", "universe")
IDX_DIR = os.path.join(WORKSPACE, "data", "index_constituents")
L1_PATH = os.path.join(UNI_DIR, "universe_l1.json")
L2_PATH = os.path.join(UNI_DIR, "universe_core.json")
LIQ_PATH = os.path.join(UNI_DIR, "liquidity_cache.json")
BASIC_PATH = os.path.join(UNI_DIR, "stock_basic_cache.json")

MIN_AVG_AMOUNT = 5e7        # L1: 20日日均成交额 5000 万
MIN_LISTING_DAYS = 180      # L1: 上市 6 个月
CORE_INDICES = ("hs300", "sz50")        # L2 core 层
EXT_INDICES = ("zz500", "zz1000")       # L2 extended 层


def _bak(path: str):
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
    except Exception as e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {e}")


def _load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ 读取失败 {path}: {e}")
        return None


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _bak(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _sym6(bs_code: str) -> str:
    """baostock 'sh.600000' -> '600000'"""
    return bs_code.split(".")[-1].zfill(6)


# ══════════════════════════════════════════════════════════════
# 数据拉取（bootstrap，慢）
# ══════════════════════════════════════════════════════════════

def fetch_all_market_basic() -> dict:
    """baostock 全市场名单 + ST/退市标记 + 上市日期 → stock_basic_cache.json"""
    import baostock as bs
    out = {"fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "stocks": {}}
    _bs_login_with_retry()
    try:
        # 1) 当日全市场快照（含 code_name，ST 股名字自带 ST 前缀）
        rs = bs.query_all_stock(day=_last_trade_day_str())
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        for code, status, name in rows:
            if not code.startswith(("sh.6", "sz.0", "sz.3")):
                continue  # 只要 A 股主板/创业板/科创板
            sym = _sym6(code)
            out["stocks"][sym] = {
                "name": name,
                "is_st": ("ST" in name.upper()) or ("退" in name),
                "trade_status": status,
            }
        n_all = len(out["stocks"])
        # 2) 上市日期（query_stock_basic 全表）
        rs2 = bs.query_stock_basic()
        rows2 = []
        while rs2.error_code == "0" and rs2.next():
            rows2.append(rs2.get_row_data())
        fields2 = rs2.fields
        i_code = fields2.index("code")
        i_ipo = fields2.index("ipoDate")
        i_out = fields2.index("outDate") if "outDate" in fields2 else None
        i_type = fields2.index("type") if "type" in fields2 else None
        i_status = fields2.index("status") if "status" in fields2 else None
        have_ipo = 0
        for r in rows2:
            sym = _sym6(r[i_code])
            if sym not in out["stocks"]:
                continue
            if i_type is not None and r[i_type] != "1":
                continue  # type=1 股票（2指数/3其它）
            out["stocks"][sym]["ipo_date"] = r[i_ipo]
            if i_out is not None:
                out["stocks"][sym]["out_date"] = r[i_out] or ""
            if i_status is not None:
                out["stocks"][sym]["bs_status"] = r[i_status]
            have_ipo += 1
        print(f"✅ 全市场名单: {n_all} 只 (含上市日期 {have_ipo})")
    finally:
        bs.logout()
    _save_json(BASIC_PATH, out)
    return out


def _last_trade_day_str(days_back: int = 1) -> str:
    """最近一个已收盘交易日字符串（保守: 回退 1 天，周末再回退）"""
    d = datetime.now() - timedelta(days=days_back)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _bs_login_with_retry(max_attempts: int = 5):
    """baostock 登录重试（瞬时「网络接收错误」实测频发，退避重试可过）"""
    import time
    import baostock as bs
    last_err = None
    for i in range(max_attempts):
        try:
            lg = bs.login()
            if lg.error_code == "0":
                return True
            last_err = lg.error_msg
        except Exception as e:
            last_err = str(e)[:80]
        time.sleep(3 * (i + 1))  # 3/6/9/12/15s 退避
    raise RuntimeError(f"baostock login 失败(重试{max_attempts}次): {last_err}")


def fetch_liquidity(symbols: list) -> dict:
    """逐票拉 20 交易日成交额均值 → liquidity_cache.json

    🔴 2026-11-09 实测坑: baostock 单会话高频查询（~1次/秒）约 200 次后静默限流
    （error_code=0 但 rows=0），修复三件套:
      1. 查询间隔 sleep(0.2) → ~4次/秒以下
      2. 空 rows 重试一次（sleep 2s 后重拉）
      3. 每 pulling 300 次重登会话
    """
    import time
    import baostock as bs
    import pandas as pd
    start = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    end = _last_trade_day_str()
    cache = _load_json(LIQ_PATH) or {"fetched_at": "", "amounts": {}}
    amounts = cache.get("amounts", {})
    _bs_login_with_retry()

    def _pull_one(sym, bs):
        bs_code = ("sh." if sym.startswith("6") else "sz.") + sym
        rs = bs.query_history_k_data_plus(
            bs_code, "date,amount", start_date=start, end_date=end,
            frequency="d", adjustflag="3")
        vals = []
        while rs.error_code == "0" and rs.next():
            vals.append(rs.get_row_data()[1])
        return vals

    try:
        todo = [s for s in symbols if s not in amounts]
        print(f"流动性拉取: {len(todo)}/{len(symbols)} 只待拉 (缓存命中 {len(symbols)-len(todo)})")
        n_since_login = 0
        for i, sym in enumerate(todo):
            try:
                vals = _pull_one(sym, bs)
                if not vals:  # 疑似限流: 等 2s 重试一次
                    time.sleep(2)
                    vals = _pull_one(sym, bs)
                s = pd.to_numeric(pd.Series(vals), errors="coerce").dropna()
                if len(s) >= 5:
                    amounts[sym] = {"avg_amount_20d": float(s.tail(20).mean()),
                                    "n_days": int(len(s)), "as_of": end}
                else:
                    amounts[sym] = {"avg_amount_20d": None, "n_days": int(len(s)), "as_of": end}
            except Exception as e:
                amounts[sym] = {"avg_amount_20d": None, "n_days": -1, "error": str(e)[:80]}
            n_since_login += 1
            if n_since_login >= 300:  # 周期性重登防会话失效
                try:
                    bs.logout()
                except Exception:
                    pass
                _bs_login_with_retry()
                n_since_login = 0
                print(f"  🔄 会话重登 @ {i+1}")
            time.sleep(0.2)  # 防限流间隔
            if (i + 1) % 100 == 0:
                print(f"  进度 {i+1}/{len(todo)}")
                cache.update({"fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                              "amounts": amounts})
                _save_json(LIQ_PATH, cache)  # 增量落盘防中断丢稿
    finally:
        try:
            bs.logout()
        except Exception:
            pass
    cache.update({"fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "amounts": amounts})
    _save_json(LIQ_PATH, cache)
    ok = sum(1 for v in amounts.values() if v.get("avg_amount_20d"))
    print(f"✅ 流动性缓存: {ok}/{len(amounts)} 只有有效均值")
    return cache


# ══════════════════════════════════════════════════════════════
# L1 / L2 构建（秒级，用缓存）
# ══════════════════════════════════════════════════════════════

def _current_watchlist() -> dict:
    """现有关注池（种子保护层）— 单一事实来源 fib_extension_scan.WATCHLIST"""
    try:
        from analysis.fib_extension_scan import WATCHLIST
        return dict(WATCHLIST)
    except Exception as e:
        print(f"⚠️ WATCHLIST 加载失败: {e}")
        return {}


def build_l1() -> dict:
    """L1 硬性准入: 全市场 → 可交易池"""
    basic = _load_json(BASIC_PATH)
    if not basic or not basic.get("stocks"):
        print("❌ stock_basic_cache.json 缺失，先跑 --bootstrap")
        return {}
    liq = _load_json(LIQ_PATH) or {"amounts": {}}
    amounts = liq.get("amounts", {})
    today = datetime.now().strftime("%Y-%m-%d")

    passed, rejected = {}, {"st": 0, "delisted": 0, "illiquid": 0, "new": 0, "no_liq_data": 0}
    for sym, info in basic["stocks"].items():
        if info.get("is_st"):
            rejected["st"] += 1
            continue
        if info.get("out_date"):  # 已退市
            rejected["delisted"] += 1
            continue
        ipo = info.get("ipo_date")
        if ipo:
            try:
                days = (datetime.now() - datetime.strptime(ipo, "%Y-%m-%d")).days
                if days < MIN_LISTING_DAYS:
                    rejected["new"] += 1
                    continue
            except ValueError:
                pass
        li = amounts.get(sym) or {}
        avg = li.get("avg_amount_20d")
        if avg is None:
            # 无流动性数据 = unknown：不误杀也不放行 → 记入 no_liq_data，由 L2 种子保护兜底
            rejected["no_liq_data"] += 1
            continue
        if avg < MIN_AVG_AMOUNT:
            rejected["illiquid"] += 1
            continue
        passed[sym] = {"name": info.get("name", ""), "avg_amount_20d": round(avg, 0)}

    out = {
        "built_at": datetime.now().isoformat(), "as_of": today,
        "count": len(passed), "symbols": sorted(passed),
        "detail": passed,
        "rejected": rejected,
        "coverage": {"basic_cache": basic.get("fetched_at"), "liq_cache": liq.get("fetched_at")},
        "thresholds": {"min_avg_amount_20d": MIN_AVG_AMOUNT, "min_listing_days": MIN_LISTING_DAYS},
    }
    _save_json(L1_PATH, out)
    print(f"✅ L1 可交易池: {len(passed)} 只 | 拒绝: {rejected}")
    return out


def build_l2() -> dict:
    """L2 质量锚定: 可交易池 → 核心池（core/extended/seed 三层）"""
    l1 = _load_json(L1_PATH)
    if not l1:
        print("❌ universe_l1.json 缺失，先跑 --l1")
        return {}
    l1_set = set(l1["symbols"])
    watch = _current_watchlist()

    layers = {"core": [], "extended": [], "seed_only": []}
    names = {}
    for idx in CORE_INDICES:
        d = _load_json(os.path.join(IDX_DIR, f"{idx}.json")) or {}
        for s in d.get("symbols", []):
            if s in l1_set:
                layers["core"].append(s)
                names.update(d.get("names", {}))
    for idx in EXT_INDICES:
        d = _load_json(os.path.join(IDX_DIR, f"{idx}.json")) or {}
        for s in d.get("symbols", []):
            if s in l1_set and s not in layers["core"]:
                layers["extended"].append(s)
                names.update(d.get("names", {}))
    # 种子保护: 现有关注池不满足 L1 也保留（标 seed_only，供审计）
    core_ext = set(layers["core"]) | set(layers["extended"])
    for s in watch:
        if s not in core_ext:
            layers["seed_only"].append(s)
        names.setdefault(s, watch[s])

    for k in layers:
        layers[k] = sorted(set(layers[k]))

    out = {
        "built_at": datetime.now().isoformat(), "as_of": l1["as_of"],
        "layers": layers,
        "count": {k: len(v) for k, v in layers.items()},
        "scan_universe": sorted(core_ext | set(layers["seed_only"])),
        "names": names,
        "note": "core=hs300+sz50∩L1; extended=zz500+zz1000∩L1; seed_only=关注池保护(未过L1也保留)",
    }
    _save_json(L2_PATH, out)
    print(f"✅ L2 核心池: core={out['count']['core']} extended={out['count']['extended']} "
          f"seed_only={out['count']['seed_only']} | 扫描池合计 {len(out['scan_universe'])}")
    return out


def status():
    for name, path in [("stock_basic", BASIC_PATH), ("liquidity", LIQ_PATH),
                       ("L1", L1_PATH), ("L2", L2_PATH)]:
        d = _load_json(path)
        if not d:
            print(f"❌ {name}: 缺失")
            continue
        if name == "L1":
            print(f"✅ {name}: {d['count']} 只 (as_of {d['as_of']}, 拒绝 {d['rejected']})")
        elif name == "L2":
            print(f"✅ {name}: {d['count']} (扫描池 {len(d['scan_universe'])} 只)")
        elif name == "liquidity":
            ok = sum(1 for v in d.get("amounts", {}).values() if v.get("avg_amount_20d"))
            print(f"✅ {name}: {ok}/{len(d.get('amounts', {}))} 有效 ({d.get('fetched_at')})")
        else:
            print(f"✅ {name}: {len(d.get('stocks', {}))} 只 ({d.get('fetched_at')})")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap", action="store_true", help="全量拉取(慢): 名单+流动性")
    ap.add_argument("--refresh-basic", action="store_true", help="仅刷新全市场名单")
    ap.add_argument("--refresh-liq", action="store_true", help="仅刷新流动性(增量)")
    ap.add_argument("--l1", action="store_true")
    ap.add_argument("--l2", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        status()
        return
    if args.bootstrap:
        basic = fetch_all_market_basic()
        # 流动性拉取范围 = 指数并集 + 关注池（非全市场 5510 只，控成本）
        union = set()
        for idx in CORE_INDICES + EXT_INDICES:
            d = _load_json(os.path.join(IDX_DIR, f"{idx}.json")) or {}
            union.update(d.get("symbols", []))
        union.update(_current_watchlist().keys())
        fetch_liquidity(sorted(union))
    if args.refresh_basic:
        fetch_all_market_basic()
    if args.refresh_liq:
        union = set()
        for idx in CORE_INDICES + EXT_INDICES:
            d = _load_json(os.path.join(IDX_DIR, f"{idx}.json")) or {}
            union.update(d.get("symbols", []))
        union.update(_current_watchlist().keys())
        fetch_liquidity(sorted(union))
    if args.l1:
        build_l1()
    if args.l2:
        build_l2()
    if not any([args.bootstrap, args.refresh_basic, args.refresh_liq, args.l1, args.l2, args.status]):
        ap.print_help()


if __name__ == "__main__":
    main()
