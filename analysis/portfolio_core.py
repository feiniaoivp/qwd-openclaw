#!/usr/bin/env python3
"""
Portfolio Core - 可复用的组合核心逻辑
========================================
供 portfolio_sim.py（模拟盘）、实盘执行器、回测引擎共同调用，
保证「模拟/实盘/回测」行为 100% 一致。

核心能力：
1. 策略切换强平保护
2. 重复 BUY 信号钝化
3. 030 风控信号覆盖（致命/高风险）
4. ATR 止损价计算
5. 成交流水记录（含斐波那契止盈目标）
6. 哑铃策略仓位分类（核心/卫星）
7. 030 仓位管理集成（买入乘数、单股上限、总仓位上限）
"""

import os
import json
import time
import warnings
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

import pandas as pd
import pandas_ta as ta
import numpy as np

# 网络兜底：进程级 socket 默认超时（防数据源挂起导致永久阻塞）
try:
    from analysis.net_guard import install_default_timeout as _install_net_timeout
    _install_net_timeout()
except ImportError:
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from analysis.net_guard import install_default_timeout as _install_net_timeout
    _install_net_timeout()

warnings.filterwarnings("ignore")

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")  # 2026-11-04 模板化：支持沙箱重定向（凌晨 e2e 直写生产事故的机制修复，与全链其它模块对齐）
STATE_FILE = os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")
TRADES_FILE = os.path.join(WORKSPACE, "data", "portfolio_sim_trades.json")
STRATEGY_MAP_FILE = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")
EQUITY_FILE = os.path.join(WORKSPACE, "data", "portfolio_equity.csv")

# 脚本模式导入样板（2026-11-04 补齐，见 TOOLS.md）：工作区根 + analysis/ 双插入
import sys
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# B-管道化执行器（2026-11-04 用户拍板 B）：信号→PENDING 意向→次日开盘价成交；AUTO_EXECUTOR=0 回退 legacy
from analysis import auto_executor

# 回测模式：为 True 时 append_trade / append_equity_snapshot 不落盘，
# 避免回测污染模拟盘生产状态文件。
BACKTEST_MODE = False

# ═══════════════════════════════════════════
# 常量与配置
# ═══════════════════════════════════════════
COMMISSION = 0.0003
SLIPPAGE = float(os.getenv("SLIP_BASE", "0.001"))  # 基础滑点（T2-1 后仅为底座分量）
INITIAL_CAPITAL = 100_000

# ═══════════════════════════════════════════
# T2-1: 动态滑点模型（2026-11-04，取代固定 0.001）
# ═══════════════════════════════════════════
# slip = base + k_vol × (ATR/price) + k_impact × √(订单金额/当日成交额)，上限 cap。
# - 波动率分量：ATR/price 越大 → 短时价格噪声越宽 → 冲击成本越高
# - 流动性分量：订单占当日成交额比例越大（平方根模型，Kyle-type 简化）→ 越难成交
# - 环境变量可调；SLIP_DYNAMIC=0 一键回退固定滑点旧行为
SLIP_DYNAMIC = os.getenv("SLIP_DYNAMIC", "1") == "1"
SLIP_VOL_K = float(os.getenv("SLIP_VOL_K", "0.5"))
SLIP_IMPACT_K = float(os.getenv("SLIP_IMPACT_K", "0.1"))
SLIP_CAP = float(os.getenv("SLIP_CAP", "0.02"))

def calc_slippage(price, atr=None, day_amount=None, order_value=None):
    """计算单笔成交的动态滑点率（0~SLIP_CAP）。

    price: 成交参考价（必填）
    atr:   该票最近 ATR14（缺失时波动率分量=0，fail-open 到基础滑点）
    day_amount: 当日成交额（缺失时流动性分量=0）
    order_value: 本笔订单金额（缺失时流动性分量=0）
    """
    if not SLIP_DYNAMIC:
        return SLIPPAGE
    try:
        price = float(price)
        if price <= 0:
            return SLIPPAGE
        slip = SLIPPAGE
        if atr is not None and float(atr) > 0:
            slip += SLIP_VOL_K * (float(atr) / price)
        if day_amount is not None and order_value is not None \
                and float(day_amount) > 0 and float(order_value) > 0:
            participation = min(float(order_value) / float(day_amount), 1.0)
            slip += SLIP_IMPACT_K * (participation ** 0.5)
        return round(min(slip, SLIP_CAP), 6)
    except (TypeError, ValueError):
        return SLIPPAGE

# 单簇市值占比上限（2026-10-18 用户拍板：同相关簇约束从「只数≤2」升级为组合层
# 市值占比、持仓期持续生效；依据 analysis/research/2026-10-18_cluster_cap_scorecard.md）
CLUSTER_EXPO_CAP = float(os.getenv("CLUSTER_EXPO_CAP", "0.25"))

# 组合总仓位上限执法开关（2026-10-18 用户拍板）：030 的 total_limit_amount 此前
# 无任何消费方（约束层盘点发现），本开关接入后 030 从「展示」变「执法」。
# 紧急回退: 环境变量 TOTAL_LIMIT_ENFORCE=0 即恢复旧行为（不执法）。
TOTAL_LIMIT_ENFORCE = os.getenv("TOTAL_LIMIT_ENFORCE", "1") == "1"
ATR_STOP_MULT = 2.0

# 模拟盘激进模式（2026-11-03 用户批准；配置 data/sim_aggressive_config.json，
# 回滚: enabled=false / 删文件 / 环境变量 SIM_AGGRESSIVE=0；致命风险硬保险不受影响）
try:
    from analysis.sim_aggressive import AGGR as _AGGR_CFG
except Exception as _e:
    print(f"⚠️ sim_aggressive 加载失败, 激进模式禁用: {_e}")
    _AGGR_CFG = {"enabled": False}
if _AGGR_CFG.get("enabled"):
    CLUSTER_EXPO_CAP = float(_AGGR_CFG.get("cluster_expo_cap", CLUSTER_EXPO_CAP))
    ATR_STOP_MULT = float(_AGGR_CFG.get("atr_stop_mult", ATR_STOP_MULT))
    print(f"🔥 激进模式已启用(portfolio_core): 单簇上限{CLUSTER_EXPO_CAP:.0%} ATR止损倍数{ATR_STOP_MULT}")


def _aggr_single_cap_pct(bucket: str) -> float:
    """单股上限（占单票本金10万比例），激进模式感知（2026-11-03）。
    标准: core 15% / satellite 5%；激进: 按配置 core_single_cap/sat_single_cap。
    """
    if _AGGR_CFG.get("enabled"):
        if bucket == "core":
            return float(_AGGR_CFG.get("core_single_cap", 0.15))
        return float(_AGGR_CFG.get("sat_single_cap", 0.05))
    return 0.15 if bucket == "core" else 0.05


def _clamp_legacy_stop_cooldowns(state: Dict) -> int:
    """模式切换自愈：存量止损冷却一次性收缩到当前配置上限（2026-11-03 用户拍板）。

    背景：激进模式 stop_cooldown_days=5 只影响新止损（record 时读配置），
    旧 15 天制遗留值（实证 000987 =13）会静默封锁买入到 ~11-16，违背切换意图。
    规则：仅激进模式启用且配置天数 < 15 时，把 > 配置值的存量冷却收缩到配置值
    （min(现值, 配置)）；标准模式不动作。幂等，随每次扫描自愈。
    返回：本次收缩的票数。
    """
    if not _AGGR_CFG.get("enabled"):
        return 0
    cfg_days = int(_AGGR_CFG.get("stop_cooldown_days", 15))
    if cfg_days >= 15:
        return 0
    n = 0
    for pos in (state.get("positions") or {}).values():
        if not isinstance(pos, dict):
            continue
        cur = int(pos.get("_stop_cooldown_days", 0) or 0)
        if cur > cfg_days:
            pos["_stop_cooldown_days"] = cfg_days
            n += 1
    return n


def _build_atr_map_from_state(state: Dict) -> Dict[str, tuple]:
    """从 state 提取上一扫描日真实 (close, atr14)，供 030 买入乘数微调（2026-11-03）。
    配套修复：position_sizer._estimate_atr_positions 旧实现硬编码 price=10.0/
    ATR≈3%（常量伪装变量，买入乘数永久 ×0.8）；现仅用真实数据，无数据则跳过微调。
    """
    m: Dict[str, tuple] = {}
    for sym, pos in (state.get("positions") or {}).items():
        try:
            c = float(pos.get("_last_close") or 0)
            a = float(pos.get("_last_atr") or 0)
        except (TypeError, ValueError):
            continue
        if c > 0 and a > 0:
            m[str(sym)] = (c, a)
    return m

# 单一事实来源：参考总本金（仓位计算/风控限额/收益率分母统一引用）
# 注：该值为"参考分母"常量，不随关注池只数自动变动；如需调整须走风控变更流程。
REFERENCE_TOTAL_CAPITAL = 2_700_000

STRATEGY_LABELS = {
    "bollinger": "📊 布林带+ATR",
    "kdj_cci": "🎯 KDJ+RSI(均值回归)",
    "ema_obv": "📈 EMA+OBV",
    "ema_cross": "💹 EMA12/26",
    "macd": "📉 纯MACD",
    "bull_trend": "🐂 牛市趋势跟踪",
}

# ── 27只关注股（权威同步 memory/watchlist.md，2026-09-17 撤除电网装备出海8只未入池标的）──
STOCKS = [
    # 证券/金融 (5)
    ("600030","中信证券"), ("601066","中信建投"), ("600036","招商银行"),
    ("601995","中金公司"), ("000987","越秀资本"),
    # 半导体/TMT (4)
    ("600584","长电科技"), ("688981","中芯国际"), ("002156","通富微电"), ("002413","雷科防务"),
    # 新能源/储能 (2)
    ("300014","亿纬锂能"), ("002466","天齐锂业"),
    # 高端制造/材料 (8)
    ("300285","国瓷材料"), ("603308","应流股份"), ("300124","汇川技术"), ("601100","恒立液压"),
    ("002318","久立特材"), ("300719","安达维尔"), ("002335","科华数据"), ("300748","金力永磁"),
    # 化工 (2)
    ("600160","巨化股份"), ("600346","恒力石化"),
    # 钢铁/特钢 (1)
    ("000708","中信特钢"),
    # 消费/其他 (5)
    ("600660","福耀玻璃"), ("600570","恒生电子"), ("605566","福莱蒽特"), ("000157","中联重科"), ("601061","中信金属"),
]
# 注：电网装备/特高压出海8只（600089/600406/000400/601179/002028/002270/002130/600312）
#     已撤出关注池，需要时用 analysis/fib_extension_scan.py 的独立观察清单。

# 参考总本金：固定为当前关注池股票数 × 单票本金，防止调仓/增减股导致收益率分母跳变
REFERENCE_STOCK_COUNT = len(STOCKS)  # 当前 27 只
REFERENCE_TOTAL_CAPITAL = INITIAL_CAPITAL * REFERENCE_STOCK_COUNT

# 030 模块（可选导入）
try:
    from analysis.fatal_risk_detector import FatalRiskDetector
    from analysis.position_sizer import PositionSizer, load_market_context as load_position_context, STOCK_DIRECTION_MAP
    from analysis.signal_arbitrator import SignalArbitrator, RawSignal, SignalAction
    from analysis.moat_factor import calc_moat_score, is_core_moat_stock
    HAS_030_MODULES = True
except ImportError:
    HAS_030_MODULES = False

import urllib.request


def fetch_today_realtime(symbols):
    """新浪实时行情批量获取"""
    prefix_map = {s: ("sh" if s.startswith("6") else "sz") for s in symbols}
    codes = [f"{prefix_map[s]}{s}" for s in symbols]
    url = "https://hq.sinajs.cn/list=" + ",".join(codes)
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    result = {}
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        text = resp.read().decode("gbk")
        for s in symbols:
            prefix = prefix_map[s]
            marker = f'hq_str_{prefix}{s}="'
            start = text.find(marker)
            if start < 0:
                continue
            start += len(marker)
            end = text.find("\"", start)
            if end < 0:
                continue
            fields = text[start:end].split(",")
            if len(fields) < 9:
                continue
            try:
                cur = float(fields[3])
                oh = float(fields[1])
                hi = float(fields[4]) if fields[4] else cur
                lo = float(fields[5]) if fields[5] else cur
                vol = int(fields[8]) if fields[8] else 0
                # 🔴 2026-10-11 同步修复（与 adaptive_dual 一致）：bar 日期取行情快照
                # 自带的交易日(fields[30])，非日历日——防盘前/周末把 T-1 快照当今日 bar。
                qdate = fields[30].strip()[:10].replace("/", "-") if len(fields) > 30 and fields[30] else ""
                result[s] = {"date": qdate,
                             "open": oh, "close": cur, "high": hi, "low": lo, "volume": vol}
            except (ValueError, IndexError):
                pass
        return result
    except Exception:
        return {}
    HAS_030_MODULES = False


# ═══════════════════════════════════════════
# 状态管理
# ═══════════════════════════════════════════
def load_state() -> Dict:
    """
    加载模拟盘状态，带完整性校验。
    - JSON 解析失败：备份损坏文件，返回空 state（不静默重建）
    - 校验 state 与 equity.csv 最后一行 total_initial 一致，不一致报警
    - 回测模式下返回空 state，避免污染生产文件
    """
    if BACKTEST_MODE:
        return {"last_signal_date": None, "positions": {}}
    
    if not os.path.exists(STATE_FILE):
        return {"last_signal_date": None, "positions": {}}
    
    # 读取并解析 JSON
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except json.JSONDecodeError as e:
        # 备份损坏文件，避免静默重建掩盖问题
        import shutil
        from datetime import datetime
        bak = f"{STATE_FILE}.corrupt.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(STATE_FILE, bak)
        print(f"⚠️ STATE_FILE JSON 解析失败，已备份至 {bak}: {e}")
        return {"last_signal_date": None, "positions": {}}
    
    # 基础结构校验
    if not isinstance(state, dict) or "positions" not in state:
        print(f"⚠️ STATE_FILE 结构异常，缺少 'positions' 字段")
        return {"last_signal_date": None, "positions": {}}
    
    # 交叉校验：state 与 equity.csv 最后一行 total_initial 一致性
    # （仅当 equity.csv 存在且有数据时）
    if os.path.exists(EQUITY_FILE):
        try:
            import csv
            with open(EQUITY_FILE) as f:
                reader = list(csv.DictReader(f))
                if reader:
                    last_row = reader[-1]
                    equity_initial = float(last_row.get("total_initial", 0))
                    expected_initial = REFERENCE_TOTAL_CAPITAL
                    if equity_initial > 0 and abs(equity_initial - expected_initial) / expected_initial > 0.05:
                        print(f"⚠️ 口径不一致: equity.csv total_initial={equity_initial:,.0f} vs REFERENCE={expected_initial:,.0f} (偏差 >5%)")
        except Exception as e:
            print(f"⚠️ equity.csv 校验读取失败: {e}")

    # 单票账本一致性自检 (2026-09-28, F-5 根因门禁)
    # 语义: total_pl = 累计已实现盈亏; cash = 现金余额。
    # 空仓时应满足: cash - INITIAL_CAPITAL == total_pl (差值为手续费/滑点尾差, 容忍 50 元)。
    # 违反 => 单票 cash 与 total_pl 双账静默脱钩 (历史 F-5: 8/27 票不一致, 含符号相反)。
    # 失败仅告警不阻断主流程 (防误伤盘中决策)。
    try:
        _bad = []
        for _sym, _p in state.get("positions", {}).items():
            if not isinstance(_p, dict):
                continue
            _cash = float(_p.get("cash", 0) or 0)
            _tpl = float(_p.get("total_pl", 0) or 0)
            _realized = _cash - INITIAL_CAPITAL
            if not _p.get("position"):  # 空仓时可直接比对
                if abs(_realized - _tpl) > 50.0:
                    _bad.append((_sym, round(_realized, 2), round(_tpl, 2)))
        if _bad:
            print(f"⚠️ 单票账本不一致 (F-5口径): {len(_bad)}/{len(state.get('positions', {}))} 票 "
                  f"cash-本金 != total_pl (容忍50元):")
            for _s, _r, _t in _bad[:10]:
                print(f"     {_s}: cash-本金={_r:,.2f} vs total_pl={_t:,.2f}")
            if len(_bad) > 10:
                print(f"     ... 及其他 {len(_bad)-10} 票")
    except Exception as e:
        print(f"⚠️ 单票账本自检失败: {e}")

    return state


def save_state(state: Dict):
    # 回测模式：不得写入生产 state 文件（避免回测污染模拟盘账本，历史 F-5 问题）
    if BACKTEST_MODE:
        return
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)


def load_strategy_map() -> Dict:
    if os.path.exists(STRATEGY_MAP_FILE):
        with open(STRATEGY_MAP_FILE) as f:
            return json.load(f)
    return {}


def get_bucket(symbol: str, strategy_map: Dict) -> str:
    """从策略映射获取分层 bucket，兼容旧格式（仅字符串）和新格式（对象）"""
    info = strategy_map.get(symbol)
    if isinstance(info, dict):
        return info.get("bucket", "tier1")
    # 旧格式兼容：推断 bucket
    # Tier3 股票
    if symbol in ["600030", "601995"]:
        return "tier3"
    # Tier2 股票
    if symbol in ["600570", "000400", "300124", "002318", "300719",
                   "605566", "000157", "601066", "600312", "002130", "600406"]:
        return "tier2"
    return "tier1"


def append_trade(trade: Dict):
    """追加成交流水（回测模式下跳过，避免污染生产文件）"""
    if BACKTEST_MODE:
        return
    log = []
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE) as f:
                log = json.load(f)
        except Exception:
            log = []
    log.append(trade)
    os.makedirs(os.path.dirname(TRADES_FILE), exist_ok=True)
    with open(TRADES_FILE, "w") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def append_equity_snapshot(date: str, portfolio_summary: Dict, core_value: float, sat_value: float, cash_total: float):
    """记录每日组合权益快照（同日去重：若末行日期相同则覆盖，否则追加）
    CSV 列: date, total_value, total_return_pct, core_value, satellite_value, cash_total, positions_held, total_initial

    2026-10-21 C 修复（权益文件冻结 22 个交易日）：
      根因 = 旧实现「读失败 → existing_rows=[] → 整文件重写」把历史行清零，
      且写盘非原子。违反 MEMORY 铁律「重写型脚本必须有写前 .bak + 行数防倒退」。
      新契约：①读取失败一律 raise（不再退化重写）②写前 .bak
      ③行数防倒退（新行数 < 旧行数 直接拒绝）④临时文件 + os.replace 原子替换。
    """
    import csv
    import shutil
    if BACKTEST_MODE:
        return
    file_exists = os.path.exists(EQUITY_FILE)

    fieldnames = ["date", "total_value", "total_return_pct", "core_value",
                  "satellite_value", "cash_total", "positions_held", "total_initial"]
    row = {
        "date": date,
        "total_value": portfolio_summary.get("total_value", 0),
        "total_return_pct": portfolio_summary.get("total_return_pct", 0),
        "core_value": core_value,
        "satellite_value": sat_value,
        "cash_total": cash_total,
        "positions_held": portfolio_summary.get("positions_held", 0),
        "total_initial": portfolio_summary.get("total_initial", 0),
    }

    # 读现有所有行（含表头），用于同日去重。读取失败必须可见地失败，绝不退化为「空历史」。
    existing_rows = []
    _prev_row_count = 0
    if file_exists:
        try:
            with open(EQUITY_FILE, newline='') as f:
                existing_rows = list(csv.DictReader(f))
        except Exception as e:
            raise RuntimeError(
                f"权益快照读取失败，拒绝重写以免清零历史（{EQUITY_FILE}）: {e}"
            ) from e
        _prev_row_count = len(existing_rows)
        if not existing_rows:
            raise RuntimeError(
                f"权益快照读取到 0 行，疑似文件损坏，拒绝重写（{EQUITY_FILE}）"
            )

    # 同日去重：若末行日期 == 本次 date，则覆盖（替换末行 dict），否则新增
    if existing_rows and existing_rows[-1].get("date") == date:
        existing_rows[-1] = row
    else:
        existing_rows.append(row)

    # 行数防倒退：重写后行数不得少于重写前（同日覆盖是替换，不是删除，行数不变）
    if file_exists and len(existing_rows) < _prev_row_count:
        raise RuntimeError(f"权益快照行数倒退({len(existing_rows)} < {_prev_row_count})，拒绝写入")

    os.makedirs(os.path.dirname(EQUITY_FILE), exist_ok=True)
    # 写前备份（保留最近 5 份）
    if file_exists:
        try:
            shutil.copy2(EQUITY_FILE, EQUITY_FILE + f".bak-{date}")
        except Exception as e:
            print(f"⚠️ 权益快照备份失败（继续写入）: {e}")

    # 临时文件 + 原子替换
    tmp_path = EQUITY_FILE + ".tmp"
    with open(tmp_path, "w", newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in existing_rows:
            # 补齐缺失字段（历史行可能缺列），缺失填空
            writer.writerow({k: r.get(k, "") for k in fieldnames})
    os.replace(tmp_path, EQUITY_FILE)


def _resolve_tier(symbol: str) -> str:
    """从策略映射解析真实信号分层（tier1/2/3）。

    E 修复（2026-10-21）：调用点常按旧默认值传 bucket="tier1"，若原样写进
    pos["tier"] 会把真实分层覆盖成 tier1（信号过滤失效）。此处以策略映射为唯一来源；
    映射缺失/读取失败时退回 tier1 并告警（fail-safe，不静默）。
    """
    try:
        # 走 get_bucket 而非直读映射：映射当前是 str 格式（如 "macd"），
        # tier 由 get_bucket 的旧格式兼容表推断；dict 格式则由 get_bucket 读其 bucket 键。
        return get_bucket(symbol, load_strategy_map())
    except Exception as e:
        print(f"⚠️ tier 解析失败 {symbol}，退回 tier1: {e}")
        return "tier1"


def init_position(symbol: str, name: str, strategy: str, bucket: str = "tier1") -> Dict:
    """初始化单只股票持仓结构

    2026-10-20 A 方案字段分拆（修复 bucket=="core" 永假）：
      - pos["tier"]   信号分层（tier1/2/3，喂 _apply_tier_filter），来源=策略映射/get_bucket
      - pos["bucket"] 仓位分类（core/satellite，喂单股 15%/5% 与哑铃 80/20），
        唯一来源 = classify_bucket(symbol)。旧版把 tier 值写进 bucket，
        导致 15% 核心单股限额自 09-10 重构起从未生效（全池按卫星 5% 跑）。
    参数 bucket 语义 = tier（兼容旧调用点：core L1219 / executor L304/L366）。
    """
    return {
        "name": name,
        "strategy": strategy,
        "position": False,
        "entry_price": 0.0,
        "entry_date": None,
        "shares": 0,
        "cash": INITIAL_CAPITAL,
        "total_pl": 0.0,
        "trade_count": 0,
        "_symbol": symbol,
        "_buy_streak": 0,
        "_stop_cooldown_days": 0,
        "_last_action": None,
        "_last_action_date": None,
        "_strategy_switch_pending": False,
        "_old_strategy": None,
        "_new_strategy": None,
        "fib_targets": {},
        # E 修复（2026-10-21）：调用点常不传 tier（默认 "tier1"），若原样写入会把
        # 真实分层覆盖成 tier1（bucket 三层语义共用的第 4 次显形）。此处兜底从策略映射取真实 tier。
        "tier": (bucket if bucket in ("tier2", "tier3") else _resolve_tier(symbol)),
        "bucket": classify_bucket(symbol)[0],
        "direction": STOCK_DIRECTION_MAP.get(symbol, "其他") if HAS_030_MODULES else "其他",
        "atr_params": {"stop_mult": ATR_STOP_MULT,
                       # 激进模式(2026-11-03): 单笔风险/仓位上限按配置放宽（仅新开仓生效）
                       "risk_per_trade": float(_AGGR_CFG.get("risk_per_trade", 0.01)),
                       "max_position_pct": float(_AGGR_CFG.get("max_position_pct", 0.15))},
    }


def sync_strategy_map(state: Dict, strategy_map: Dict, default_strategy: str = "ema_cross"):
    """同步策略映射到持仓，检测切换 → 标记强平；同时同步 bucket"""
    for sym, pos in state["positions"].items():
        info = strategy_map.get(sym, default_strategy)
        if isinstance(info, dict):
            new_strat = info.get("strategy", default_strategy)
            new_tier = info.get("bucket", "tier1")
        else:
            new_strat = info
            new_tier = get_bucket(sym, strategy_map)
        
        old_strat = pos.get("strategy", new_strat)
        old_tier = pos.get("tier", pos.get("bucket", "tier1"))
        pos["strategy"] = new_strat
        # 2026-10-20 A 方案：tier（信号分层）与 bucket（仓位分类）分字段；
        # bucket 全量改由 classify_bucket 驱动，旧 state 的 tier 值在此自动迁移。
        pos["tier"] = new_tier
        pos["bucket"] = classify_bucket(sym)[0]
        
        if pos.get("position") and old_strat != new_strat:
            pos["_strategy_switch_pending"] = True
            pos["_old_strategy"] = old_strat
            pos["_new_strategy"] = new_strat
            print(f"⚠️ 策略切换检测: {sym} {old_strat} -> {new_strat}，持仓中，标记强制平仓")
        
        # 如果 bucket 变更，记录日志
        if pos.get("position") and old_tier != new_tier:
            print(f"ℹ️ 分层变更: {sym} {old_tier} -> {new_tier}")


def _force_liquidate_tier3(state: Dict, strategy_map: Dict):
    """Tier3 股票：启动时强制清仓（仅执行一次）"""
    tier3_symbols = [sym for sym, info in strategy_map.items() 
                     if isinstance(info, dict) and info.get("bucket") == "tier3"]
    for sym in tier3_symbols:
        pos = state["positions"].get(sym)
        if pos and pos.get("position") and not pos.get("_tier3_liquidated", False):
            print(f"🔴 Tier3 强制清仓: {pos['name']}({sym})")
            pos["_tier3_liquidated"] = True
            pos["_force_sell_tier3"] = True


def _log_tier_suppression(signal: Dict, tier: str):
    """把被 Tier 屏蔽的买入追加到 data/tier_suppressed_signals.jsonl（研究可读）。

    设计: 只追加、不覆盖、单行 JSON；写失败仅告警不影响交易主流程。
    """
    try:
        path = os.path.join(WORKSPACE, "data", "tier_suppressed_signals.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rec = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "symbol": str(signal.get("_symbol") or signal.get("symbol") or ""),
            "name": signal.get("name", ""),
            "tier": tier,
            "from_action": signal.get("suppressed_from"),
            "orig_reason": (signal.get("suppressed_reason") or "")[:200],
            "price": signal.get("price"),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"⚠️ Tier 屏蔽信号落盘失败({tier}): {e}")


def _apply_tier_filter(signal: Dict, pos: Dict, tier: str) -> Dict:
    """三层分层过滤：
    - Tier1 (tier1): 禁用择时卖出，仅基本面/止盈/风控卖出
    - Tier2 (tier2): 禁用买入信号，仅保留卖出/止损
    - Tier3 (tier3): 强制卖出，禁用买入
    """
    action = signal.get("action", "持有")
    
    # 2026-10-18: 被 Tier 吞掉的买入必须留可审计标记（用户拍板「配置 + 显式标注」）
    # 原实现把买入静默改成持有，信号评估/事件研究看不到「被吞掉的 20.6% 究竟赚不赚钱」。
    # 标记字段（只增不改信号语义，可被研究脚本直接统计）：
    #   signal["suppressed_by"]   = "tier2" | "tier3"
    #   signal["suppressed_from"] = 原 action（"买入"）
    #   signal["suppressed_reason"] = 原 reason（保真留痕，便于归因）
    def _mark_suppressed(sig: Dict, tier: str):
        sig["suppressed_by"] = tier
        sig["suppressed_from"] = sig.get("action")
        sig["suppressed_reason"] = sig.get("reason", "")
        _log_tier_suppression(sig, tier)

    if tier == "tier3":
        # 2026-10-18: 原条件 `pos.get("position")` 使**空仓 Tier3 标的的买入不被拦**
        # （实测中信/中金空仓时 action 仍为「买入」）→ 与 docstring「Tier3 禁用买入」
        # 自相矛盾。改为：只要存在 tier3 标记（_force_sell_tier3 或 _tier3_liquidated），
        # 或当前有持仓，即进入拦截分支。空仓 tier3 票亦一律禁买（原 `or True` 式
        # 恒真为刻意为之：docstring 口径优先于历史上的意外放行）。
        if True:  # 分支保留以维持原有缩进结构
            if action == "买入":
                _mark_suppressed(signal, "tier3")
                signal["action"] = "持有"
                signal["reason"] = f"🔴 Tier3禁止买入 | 原理由: {signal.get('suppressed_reason', '')}"
            elif action in ["持有", "卖出"]:
                signal["action"] = "卖出"
                signal["reason"] = f"🔴 Tier3强制清仓 | 原理由: {signal.get('reason', '')}"
            pos["_force_sell_tier3"] = False
    elif tier == "tier2":
        if action == "买入":
            # 激进模式(2026-11-03 用户批准): tier2 买入放行，写留痕字段供事后归因
            if _AGGR_CFG.get("enabled") and _AGGR_CFG.get("allow_tier2_buy", False):
                signal["aggressive_allowed"] = "tier2_buy"
            else:
                _mark_suppressed(signal, "tier2")
                signal["action"] = "持有"
                signal["reason"] = f"🟡 Tier2屏蔽买入 | 原理由: {signal.get('suppressed_reason', '')}"
    elif tier == "tier1":
        if action == "卖出":
            reason = signal.get("reason", "")
            allowed_sell_keywords = ["ATR止损", "止损", "☠️", "🟡", "🔄", "致命风险", "高风险", "策略切换", "基本面", "估值"]
            is_allowed = any(kw in reason for kw in allowed_sell_keywords)
            if not is_allowed:
                # [2026-10-30] 补留痕：tier1 择时卖出拦截与 tier2/tier3 买入拦截同协议。
                # 实证：10-27 越秀(000987) MACD死叉卖出被无痕改写为持有，信号审计报
                # 「0 冲突各源一致」——被吞信号消失导致审计盲区（10-18 协议只覆盖了买入）。
                _mark_suppressed(signal, "tier1")
                signal["action"] = "持有"
                signal["reason"] = f"🟢 Tier1禁用择时卖出 | 原理由: {reason}"
    
    return signal


# ══════════════════════════════════════════
# 斐波那契扩展止盈计算
# ═══════════════════════════════════════════
# 斐波那契扩展位止盈目标：收敛为单一实现（fib_extension_scan.compute_fib_targets）
# 历史教训：此处与 adaptive_dual.py 曾各存一份重复实现，改一处漏一处。
try:
    from analysis.fib_extension_scan import compute_fib_targets  # noqa: F401
except ImportError:  # 兜底：单文件运行/路径异常时不致命
    def compute_fib_targets(df, lookback: int = 150) -> Dict:  # type: ignore
        return {}


def _compute_atr_stop(price: float, atr_val: float) -> float:
    return price - ATR_STOP_MULT * atr_val if atr_val > 0 else 0.0


def signal_bollinger_atr(df: pd.DataFrame) -> Dict:
    bb = ta.bbands(df["close"], length=20, std=2)
    bbu = bb[[c for c in bb.columns if "BBU" in c.upper()][0]]
    bbm = bb[[c for c in bb.columns if "BBM" in c.upper()][0]]
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    atr_val = float(atr.iloc[-1])
    action, reason = "持有", "无信号"
    if float(prev["close"]) <= float(bbu.iloc[-2]) and float(row["close"]) > float(bbu.iloc[-1]):
        action, reason = "买入", f"布林上轨突破(¥{float(bbu.iloc[-1]):.2f}) ATR={atr_val:.2f} 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif float(prev["close"]) >= float(bbm.iloc[-2]) and float(row["close"]) < float(bbm.iloc[-1]):
        action, reason = "卖出", f"跌破中轨(¥{float(bbm.iloc[-1]):.2f})"
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"上轨{float(bbu.iloc[-1]):.2f} 中轨{float(bbm.iloc[-1]):.2f} ATR{atr_val:.2f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def signal_kdj_cci(df: pd.DataFrame) -> Dict:
    cci = ta.cci(df["high"], df["low"], df["close"], length=14)
    cci_pct = cci.rolling(60).apply(lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-10) * 100)
    kdj = ta.kdj(df["high"], df["low"], df["close"], length=9, signal=3)
    k, d, j = kdj["K_9_3"], kdj["D_9_3"], kdj["J_9_3"]
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    cpi = float(cci_pct.iloc[-1]) if pd.notna(cci_pct.iloc[-1]) else 50
    j_cross_up = float(j.iloc[-2]) <= float(k.iloc[-2]) and float(j.iloc[-1]) > float(k.iloc[-1])
    j_cross_down = float(j.iloc[-2]) >= float(k.iloc[-2]) and float(j.iloc[-1]) < float(k.iloc[-1])
    action, reason = "持有", "无信号"
    if cpi < 20 and j_cross_up:
        action, reason = "买入", f"CCI低位({cpi:.0f}%)+KDJ金叉 K={float(k.iloc[-1]):.1f} 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif cpi > 80 or j_cross_down:
        reasons = []
        if cpi > 80: reasons.append(f"CCI高位({cpi:.0f}%)")
        if j_cross_down: reasons.append("KDJ死叉")
        action, reason = "卖出", " | ".join(reasons)
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"CCI_pct={cpi:.0f}% K={float(k.iloc[-1]):.1f} J={float(j.iloc[-1]):.1f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def signal_ema_obv(df: pd.DataFrame) -> Dict:
    ema20 = ta.ema(df["close"], length=20)
    obv = ta.obv(df["close"], df["volume"])
    obv_ema20 = ta.ema(obv, length=20)
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    e20 = float(ema20.iloc[-1])
    obv_now, obv_ema = float(obv.iloc[-1]), float(obv_ema20.iloc[-1])
    obv_cross_up = float(obv.iloc[-2]) <= float(obv_ema20.iloc[-2]) and obv_now > obv_ema
    obv_cross_down = float(obv.iloc[-2]) >= float(obv_ema20.iloc[-2]) and obv_now < obv_ema
    action, reason = "持有", "无信号"
    if price > e20 and obv_cross_up:
        action, reason = "买入", f"EMA20上方(¥{e20:.2f}) + OBV上穿均线(量能放大) 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif price < e20 or obv_cross_down:
        reasons = []
        if price < e20: reasons.append(f"跌破EMA20(¥{e20:.2f})")
        if obv_cross_down: reasons.append("OBV下穿均线(资金撤离)")
        action, reason = "卖出", " | ".join(reasons)
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"EMA20={e20:.2f} OBV/EMA={obv_now/obv_ema:.2f}x" if obv_ema > 0 else f"EMA20={e20:.2f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def signal_ema_cross(df: pd.DataFrame) -> Dict:
    ema12 = ta.ema(df["close"], length=12)
    ema26 = ta.ema(df["close"], length=26)
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    e12, e26 = float(ema12.iloc[-1]), float(ema26.iloc[-1])
    pe12, pe26 = float(ema12.iloc[-2]), float(ema26.iloc[-2])
    action, reason = "持有", "无信号"
    if pe12 <= pe26 and e12 > e26:
        action, reason = "买入", f"EMA12({e12:.2f})金叉EMA26({e26:.2f}) 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif pe12 >= pe26 and e12 < e26:
        action, reason = "卖出", f"EMA12({e12:.2f})死叉EMA26({e26:.2f})"
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"EMA12={e12:.2f} EMA26={e26:.2f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def signal_macd(df: pd.DataFrame) -> Dict:
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    macd_line = macd_df["MACD_12_26_9"]
    signal_line = macd_df["MACDs_12_26_9"]
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    m, s = float(macd_line.iloc[-1]), float(signal_line.iloc[-1])
    pm, ps = float(macd_line.iloc[-2]), float(signal_line.iloc[-2])
    action, reason = "持有", "无信号"
    if pm <= ps and m > s:
        action, reason = "买入", f"MACD金叉({m:.4f}/{s:.4f}) 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif pm >= ps and m < s:
        action, reason = "卖出", f"MACD死叉({m:.4f}/{s:.4f})"
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"MACD={m-s:.4f} Signal={s:.4f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


def signal_bull_trend(df: pd.DataFrame) -> Dict:
    ema10 = ta.ema(df["close"], length=10)
    ema20 = ta.ema(df["close"], length=20)
    ema60 = ta.ema(df["close"], length=60)
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr_val = float(atr.iloc[-1])
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])
    e10, e20, e60 = float(ema10.iloc[-1]), float(ema20.iloc[-1]), float(ema60.iloc[-1])
    pe10, pe20, pe60 = float(ema10.iloc[-2]), float(ema20.iloc[-2]), float(ema60.iloc[-2])
    action, reason = "持有", "无信号"
    # 牛市趋势跟踪：EMA20 > EMA60 且价格 > EMA20 为多头排列
    if pe20 <= pe60 and e20 > e60 and price > e20:
        action, reason = "买入", f"牛市趋势确立: EMA20({e20:.2f})金叉EMA60({e60:.2f}) 价格在EMA20上方 止损¥{_compute_atr_stop(price, atr_val):.2f}"
    elif pe20 >= pe60 and e20 < e60:
        action, reason = "卖出", f"趋势破坏: EMA20({e20:.2f})死叉EMA60({e60:.2f})"
    elif price < e60:
        action, reason = "卖出", f"价格跌破EMA60({e60:.2f}) 长期趋势支撑失效"
    return {"action": action, "reason": reason, "price": price,
            "key_indicators": f"EMA10={e10:.2f} EMA20={e20:.2f} EMA60={e60:.2f}",
            "atr": atr_val, "atr_stop": _compute_atr_stop(price, atr_val),
            "fib_tp": {"targets": compute_fib_targets(df)}}


SIGNAL_FUNCS = {
    "bollinger": signal_bollinger_atr,
    "kdj_cci": signal_kdj_cci,
    "ema_obv": signal_ema_obv,
    "ema_cross": signal_ema_cross,
    "macd": signal_macd,
    "bull_trend": signal_bull_trend,
}


# ═══════════════════════════════════════════
# 030 风控预检（单例模式，避免重复登录）
# ═══════════════════════════════════════════
class RiskGuard:
    _instance = None
    _fatal_risk = {"fatal_triggered": False, "high_risk_triggered": False}
    _position_plan = {"buy_signal_multiplier": 1.0, "single_stock_max_pct": 0.05,
                      "total_limit_pct": 0.5, "total_limit_amount": 1_500_000,
                      "direction_allocation": {}}
    _market_context = {"health_score": 5, "market_stage": "震荡筑底", "emotion_cycle": "修复"}
    _last_refresh_key = None

    @classmethod
    def refresh(cls, cache_key: str = None, atr_map: Dict = None):
        """刷新 030 风控。

        cache_key 非空时，同一 key 只真正刷新一次（回测逐日循环里避免
        对每只股票/每一天重复调用 Detector 造成的 O(天×股) 网络开销）。
        atr_map: {symbol: (close, atr14)} 上一扫描日真实数据缓存（2026-11-03
        接真实 ATR：无数据的标的跳过乘数微调，不再用硬编码伪造值参与计算）。
        """
        if cache_key is not None and cache_key == cls._last_refresh_key:
            return
        cls._last_refresh_key = cache_key
        if not HAS_030_MODULES:
            return
        # 2026-10-18 修复: 030 刷新走网络（FatalRiskDetector/PositionSizer），
        # 底层 socket 无超时 → 实测在回测中永久阻塞（sample 抓到 sock_read）。
        # 用 net_guard 临时收紧到 15s，超时按「风控不可用」降级（fail-safe 保持
        # 上次缓存值），绝不静默永久挂起调用方。
        from analysis.net_guard import network_timeout_guard
        try:
            with network_timeout_guard(15):
                fatal_detector = FatalRiskDetector()
                cls._fatal_risk = fatal_detector.check()
                pos_context = load_position_context()
                sizer = PositionSizer(total_capital=REFERENCE_TOTAL_CAPITAL)
                # 方向分配必须在计算时拿到关注池，否则 _calc_direction_allocation
                # 直接 return {} → _check_barbell_constraints 第 3 条方向上限永远失效
                # （2026-10-18 约束层盘点发现：direction_allocation 恒空=死代码）。
                # load_market_context() 不含 watchlist 字段，须回落到 WATCHLIST SSOT。
                _watch = list(pos_context.get("watchlist") or [])
                if not _watch:
                    try:
                        from analysis.fib_extension_scan import WATCHLIST as _WL
                        _watch = [str(_k) for _k in _WL.keys()]
                    except Exception as _e:
                        print(f"⚠️ 方向分配: 关注池不可用({_e})，方向上限本轮不生效")
                pos_plan_obj = sizer.calculate(
                    market_stage=pos_context["market_stage"],
                    emotion_cycle=pos_context["emotion_cycle"],
                    health_score=pos_context["health_score"],
                    fatal_risk=cls._fatal_risk,
                    held_positions=pos_context.get("held_positions"),
                    watchlist=_watch,
                    atr_map=atr_map
                )
                cls._position_plan = {
                    "buy_signal_multiplier": pos_plan_obj.buy_signal_multiplier,
                    "single_stock_max_pct": pos_plan_obj.single_stock_max_pct,
                    "single_stock_max_amount": pos_plan_obj.single_stock_max_amount,
                    "total_limit_pct": pos_plan_obj.total_limit_pct,
                    "total_limit_amount": pos_plan_obj.total_limit_amount,
                    "direction_allocation": pos_plan_obj.direction_allocation,
                    "risk_warnings": pos_plan_obj.risk_warnings,
                }
                cls._market_context = pos_context
        except Exception as e:
            print(f"⚠️ 030风控刷新失败: {e}")

    @classmethod
    def get_fatal_risk(cls) -> Dict:
        return cls._fatal_risk

    @classmethod
    def get_position_plan(cls) -> Dict:
        return cls._position_plan

    @classmethod
    def get_market_context(cls) -> Dict:
        return cls._market_context

    @classmethod
    def override_signal(cls, signal: Dict, pos: Dict) -> Dict:
        """根据风控状态强制覆盖信号"""
        fatal = cls._fatal_risk
        if fatal.get("fatal_triggered", False):
            if pos.get("position"):
                signal["action"] = "卖出"
                signal["reason"] = "☠️ 030致命风险触发：强制清仓"
            else:
                signal["action"] = "持有"
                signal["reason"] = "☠️ 030致命风险触发：禁止买入"
        elif fatal.get("high_risk_triggered", False):
            if pos.get("position"):
                signal["action"] = "卖出"
                signal["reason"] = "🟡 030高风险防御：核心中军≤-3%，减仓止损"
            else:
                signal["action"] = "持有"
                signal["reason"] = "🟡 030高风险防御：不开新仓"
        return signal

    @classmethod
    def check_strategy_switch(cls, signal: Dict, pos: Dict) -> Dict:
        """策略切换强平"""
        if pos.get("_strategy_switch_pending") and pos.get("position"):
            signal["action"] = "卖出"
            old_s = pos.get("_old_strategy", "unknown")
            new_s = pos.get("_new_strategy", "unknown")
            signal["reason"] = f"🔄 策略切换强制平仓: {old_s} -> {new_s}"
            pos.pop("_strategy_switch_pending", None)
            pos.pop("_old_strategy", None)
            pos.pop("_new_strategy", None)
        return signal

    @classmethod
    def apply_buy_streak_dampening(cls, signal: Dict, pos: Dict) -> Tuple[Dict, bool]:
        """
        重复 BUY 钝化（增强版）：
        1. 连续买入信号天数限制（默认最多连续2天执行，第3天起钝化）
        2. ATR波动率动态调整：高波动股票钝化更强
        3. 连续买入后若价格下跌，自动重置钝化计数（防止追高）
        4. 止损后禁回补：止损卖出后 N 个交易日内禁止再次买入（防反向回补追高）
        """
        if signal["action"] == "买入":
            # ── 止损后禁回补检查（P1：国瓷材料 08-19 止损 → 08-28 回补 违纪）──
            stop_cooldown_days = pos.get("_stop_cooldown_days", 0)
            if stop_cooldown_days > 0:
                signal["reason"] = f"{signal.get('reason', '')} | 止损冷却期剩余{stop_cooldown_days}天，禁止回补"
                return signal, False
            
            streak = pos.get("_buy_streak", 0) + 1
            pos["_buy_streak"] = streak
            
            # 获取ATR波动率用于动态钝化
            atr_val = signal.get("atr", 0)
            price = signal.get("price", 0)
            atr_pct = (atr_val / price * 100) if price > 0 else 0
            
            # 波动率调整钝化阈值
            # 高波动(ATR%>3): 仅允许连续1天买入
            # 中波动(ATR% 1.5-3): 允许连续2天
            # 低波动(ATR%<1.5): 允许连续3天
            if atr_pct > 3.0:
                max_streak = 1
            elif atr_pct > 1.5:
                max_streak = 2
            else:
                max_streak = 3
            # 激进模式(2026-11-03): 连续买入天数上限放宽（默认+0不改变行为）
            max_streak += int(_AGGR_CFG.get("streak_max_bonus", 0))
            
            # 检查是否需要钝化
            if streak > max_streak:
                # 记录钝化原因到信号
                signal["reason"] = f"{signal.get('reason', '')} | 钝化: 连续{streak}天买入信号(波动率ATR%={atr_pct:.1f}%, 限制{max_streak}天)"
                return signal, False  # 钝化：不执行
            
            # 连续买入后若价格跌破入场价，重置钝化计数（防追高保护）
            entry_price = pos.get("entry_price", 0)
            if entry_price > 0 and price < entry_price * 0.98:  # 跌破入场价2%
                pos["_buy_streak"] = 0  # 重置，允许新一轮买入
        else:
            pos["_buy_streak"] = 0
        return signal, True

    @classmethod
    def record_stop_loss_cooldown(cls, pos: Dict, cooldown_days: int = 15):
        """
        记录止损冷却期：止损卖出后设置禁回补天数
        每日扫描时自动递减（在 run_portfolio_scan 中调用）
        """
        pos["_stop_cooldown_days"] = cooldown_days
        print(f"⚠️ {pos.get('name', pos.get('_symbol', ''))} 触发止损冷却期：{cooldown_days} 天内禁止买入")

    @classmethod
    def decay_stop_cooldown(cls, pos: Dict):
        """每日递减止损冷却天数"""
        if pos.get("_stop_cooldown_days", 0) > 0:
            pos["_stop_cooldown_days"] -= 1

    @classmethod
    def check_dedup(cls, signal: Dict, pos: Dict, dt: str) -> bool:
        """去重：同一日同方向不重复执行 (D3: 硬限单日成交上限=1)"""
        last_action = pos.get("_last_action")
        last_date = pos.get("_last_action_date")
        if last_date == dt:
            # D3: 单日成交上限=1，任何方向（买/卖）当日只能成交一次
            return False
        if last_action == signal["action"] and last_date == dt:
            return False
        return True


# ═══════════════════════════════════════════
# 仓位分类（哑铃策略）
# ═══════════════════════════════════════════
def classify_bucket(symbol: str) -> Tuple[str, float, float]:
    """返回 (bucket, max_single_pct, max_total_pct)"""
    # 电力设备/特高压出海板块 - 独立方向，核心仓优先
    POWER_OVERSEAS = {
        "600089", "600406", "000400", "601179",
        "600312", "002028", "002270", "002130"
    }
    if symbol in POWER_OVERSEAS:
        return "core", _aggr_single_cap_pct("core"), 0.80
    if HAS_030_MODULES:
        try:
            moat_score, _ = calc_moat_score(symbol)
            is_core = is_core_moat_stock(symbol)
            if is_core:
                return "core", _aggr_single_cap_pct("core"), 0.80
        except Exception:
            pass
    return "satellite", _aggr_single_cap_pct("satellite"), 0.20


# ═══════════════════════════════════════════
# 交易执行器
# ═══════════════════════════════════════════
def execute_trade(pos: Dict, signal: Dict, dt: str, strategy: str,
                  position_plan: Dict) -> Tuple[Optional[str], Optional[str]]:
    """
    执行买卖，返回 (action_type, message)
    action_type: "BUY" | "SELL" | None
    """
    price = signal["price"]
    action = signal["action"]
    # 2026-10-20 A 方案：bucket=core/satellite（classify_bucket 驱动），
    # 核心 15% / 卫星 5% 与哑铃 80/20 首次真正生效（修复前恒为卫星 5%）
    # 激进模式(2026-11-03): 单股上限按 _aggr_single_cap_pct 放宽
    bucket = pos.get("bucket", "satellite")
    max_single_pct = _aggr_single_cap_pct(bucket)
    single_cap = INITIAL_CAPITAL * max_single_pct

    # 030 仓位限制：动态调整可用资金
    if action == "买入" and not pos["position"]:
        max_amount = position_plan.get("single_stock_max_amount", 100_000) * position_plan.get("buy_signal_multiplier", 1.0)
        available_cash = min(pos["cash"], max_amount, single_cap)
    else:
        available_cash = pos["cash"]

    if action == "买入" and not pos["position"]:
        # 三因素共振门控（可选）
        #  2026-10-18: 原 `except Exception: pass` 是第二层 fail-open（门控崩=直接放行），
        #  改为按 GATE_ERROR_POLICY 处置（默认 blocked：门控不可用时不买 + 显式留痕）。
        try:
            from analysis.three_factor_helper import ResonanceGate
            ok, info = ResonanceGate().check_buy(str(pos["_symbol"]))
            if not ok:
                return "HOLD", f"⛔ **{pos['name']}({dt}) 买入被三因素门控拦截**\n{info.get('reason','')}\n技术信号: {signal['reason']}"
        except Exception as e:
            if os.getenv("GATE_ERROR_POLICY", "blocked").lower() == "pass":
                pass  # 紧急回退旧行为（放行）
            else:
                return "HOLD", (f"⛔ **{pos['name']}({dt}) 买入被门控异常拦截**"
                                f"（GATE_ERROR_POLICY=blocked）：{str(e)[:120]}\n技术信号: {signal['reason']}")

        fee = available_cash * COMMISSION
        cash_after_fee = available_cash - fee
        # T2-1: 动态滑点（买入）——波动率分量用缓存 ATR，流动性分量用 订单金额/当日成交额
        slip_buy = calc_slippage(price, atr=pos.get("_last_atr"),
                                 day_amount=pos.get("_last_amount"),
                                 order_value=min(cash_after_fee, single_cap))
        max_shares = int(single_cap / (price * (1 + slip_buy)) / 100) * 100
        shares = int(min(cash_after_fee, single_cap) / (price * (1 + slip_buy)) / 100) * 100

        if shares >= 100:
            cost = shares * price * (1 + slip_buy)
            fee2 = cost * COMMISSION
            pos["cash"] -= (cost + fee2)
            pos["shares"] = shares
            pos["position"] = True
            pos["entry_price"] = price
            pos["entry_date"] = dt
            pos["entry_slippage"] = slip_buy  # T2-1: 留痕买入时实际滑点，供卖出成本基准/未实现盈亏复用
            pos["trade_count"] += 1
            pos["bucket"] = bucket
            pos["_last_action"] = action
            pos["_last_action_date"] = dt
            pos["_buy_streak"] = 1
            pos["fib_targets"] = signal.get("fib_tp", {}).get("targets", {})
            append_trade({"date": dt, "symbol": pos["_symbol"], "name": pos["name"],
                          "action": "BUY", "price": price, "shares": shares,
                          "pnl": 0, "pnl_pct": 0.0, "strategy": strategy, "bucket": bucket})
            bucket_emoji = "🏰" if bucket == "core" else "🛰️"
            fib_str = ""
            if pos["fib_targets"]:
                fib_str = f" | 止盈目标: " + " / ".join([f"{k}¥{v:.2f}" for k,v in pos["fib_targets"].items()])
            return "BUY", f"{bucket_emoji} **{pos['name']}({dt}) 买入**\n价格 ¥{price:.2f} | {shares}股({bucket}) | 理由: {signal['reason']}{fib_str}"

    elif action == "卖出" and pos["position"]:
        # ── 斐波那契止盈触发检查（D1: 分批止盈模式 B）──
        fib_targets = pos.get("fib_targets", {})
        tp_hit = None
        if fib_targets:
            # 分批比例：1.0=30%, 1.272=50%, 1.618=50%, 2.618=20%（累计150%，按比例卖出）
            # 实际实现：按顺序检查，触及即按对应比例卖出
            tp_ratios = [("1.0", 0.30), ("1.272", 0.50), ("1.618", 0.50), ("2.618", 0.20)]
            for ratio_str, sell_pct in tp_ratios:
                target_price = fib_targets.get(ratio_str)
                if target_price and price >= target_price:
                    # 检查该止盈位是否已触发过
                    if pos.get(f"_tp_hit_{ratio_str}", False):
                        continue
                    tp_hit = (ratio_str, target_price, sell_pct)
                    break
            if tp_hit:
                ratio_str, target_price, sell_pct = tp_hit
                # 计算分批卖出股数
                shares_to_sell = int(pos["shares"] * sell_pct / 100) * 100
                if shares_to_sell < 100:
                    shares_to_sell = pos["shares"]  # 不足100股则全卖
                
                slip_tp = calc_slippage(price, atr=pos.get("_last_atr"),
                                        day_amount=pos.get("_last_amount"),
                                        order_value=shares_to_sell * price)
                sell_value = shares_to_sell * price * (1 - slip_tp)
                fee = sell_value * COMMISSION
                net = sell_value - fee
                cost_basis = shares_to_sell * pos["entry_price"] * (1 + pos.get("entry_slippage", SLIPPAGE))
                pnl = net - cost_basis
                
                # 记录本次平仓明细
                pos["_last_sell_shares"] = shares_to_sell
                pos["_last_sell_entry"] = pos["entry_price"]
                pos["cash"] += net
                pos["total_pl"] += pnl
                pos["shares"] -= shares_to_sell
                
                # 标记已触及的止盈位，避免重复触发
                tp_hit_key = f"_tp_hit_{ratio_str}"
                pos[tp_hit_key] = True
                
                # 全部卖完则清空仓位状态
                if pos["shares"] == 0:
                    pos["position"] = False
                    pos["entry_price"] = 0.0
                    pos["entry_date"] = None
                
                pos["trade_count"] += 1
                pos["_buy_streak"] = 0
                pos["_last_action"] = "卖出"
                pos["_last_action_date"] = dt
                
                # 记录止损冷却（止盈不触发冷却）
                append_trade({"date": dt, "symbol": pos["_symbol"], "name": pos["name"],
                              "action": "SELL", "price": price,
                              "shares": shares_to_sell,
                              "pnl": pnl, "pnl_pct": round((pnl / cost_basis) * 100, 2) if cost_basis else 0.0,
                              "strategy": strategy, "reason": f"斐波止盈 {ratio_str} ({sell_pct:.0%})"})
                pnl_pct = round((pnl / cost_basis) * 100, 2) if cost_basis else 0
                emoji = "🟢" if pnl > 0 else "🔴"
                return "SELL", f"{emoji} **{pos['name']}({dt}) 斐波止盈 {ratio_str} ({sell_pct:.0%})**\n价格 ¥{price:.2f} | 目标 ¥{target_price:.2f} | 卖出 {shares_to_sell}股 | 盈亏 {pnl_pct:+.2f}%"

        slip_sell = calc_slippage(price, atr=pos.get("_last_atr"),
                                  day_amount=pos.get("_last_amount"),
                                  order_value=pos["shares"] * price)
        sell_value = pos["shares"] * price * (1 - slip_sell)
        fee = sell_value * COMMISSION
        net = sell_value - fee
        cost_basis = pos["shares"] * pos["entry_price"] * (1 + pos.get("entry_slippage", SLIPPAGE))
        pnl = net - cost_basis
        # 记录本次平仓明细（供回测单笔盈亏统计使用）
        pos["_last_sell_shares"] = pos["shares"]
        pos["_last_sell_entry"] = pos["entry_price"]
        pos["cash"] += net
        pos["total_pl"] += pnl
        pos["shares"] = 0
        pos["position"] = False
        # 平仓即清空软状态（2026-10-22 第五轮，缘起 10-21 深究发现 E）：
        # entry_* 只描述「当前持仓」，空仓后残留旧值属软状态污染；
        # 平仓明细已存入 _last_sell_entry/_last_sell_shares（上行），
        # 单笔盈亏统计不受影响；买侧路径会重新赋值，新建仓无影响。
        pos["entry_price"] = 0.0
        pos["entry_date"] = None
        pos["trade_count"] += 1
        pos["_buy_streak"] = 0
        pos["_last_action"] = action
        pos["_last_action_date"] = dt
        # 判断是否为止损卖出（ATR止损或亏损超过阈值）
        atr_stop = signal.get("atr_stop", 0)
        is_stop_loss = False
        if atr_stop and atr_stop > 0 and price <= atr_stop * 1.02:  # 价格接近/触及ATR止损
            is_stop_loss = True
        elif pnl < 0 and (pnl / cost_basis) < -0.03:  # 亏损超过3%视为止损性卖出
            is_stop_loss = True
        
        if is_stop_loss:
            # 激进模式(2026-11-03): 止损冷却期可配置（默认仍15天）
            RiskGuard.record_stop_loss_cooldown(pos, cooldown_days=int(_AGGR_CFG.get("stop_cooldown_days", 15)))
        
        append_trade({"date": dt, "symbol": pos["_symbol"], "name": pos["name"],
                      "action": "SELL", "price": price,
                      # 2026-11-03 修复：卖出 shares 恒 0 → 台账无法重建持仓史；
                      # 实际股数已存于 _last_sell_shares（本函数上文）
                      "shares": pos.get("_last_sell_shares", 0),
                      "pnl": pnl, "pnl_pct": round((pnl / cost_basis) * 100, 2) if cost_basis else 0.0,
                      "strategy": strategy})
        pnl_pct = round((pnl / cost_basis) * 100, 2) if cost_basis else 0
        emoji = "🟢" if pnl > 0 else "🔴"
        return "SELL", f"{emoji} **{pos['name']}({dt}) 卖出**\n价格 ¥{price:.2f} | 盈亏 {pnl_pct:+.2f}% | 理由: {signal['reason']}"

    return None, None


def _check_barbell_constraints(pos: Dict, signal: Dict, position_plan: Dict,
                                state: Dict) -> Tuple[bool, Optional[str]]:
    """
    哑铃策略硬约束检查（买入前强制预检）
    返回: (是否通过, 拦截原因)
    """
    if signal["action"] != "买入" or pos["position"]:
        return True, None  # 卖出或已持仓不检查
    
    bucket = pos.get("bucket", "satellite")
    max_single_pct = _aggr_single_cap_pct(bucket)
    single_cap = INITIAL_CAPITAL * max_single_pct
    
    # 1. 030 仓位限制：单股上限
    max_amount = position_plan.get("single_stock_max_amount", 100_000) * position_plan.get("buy_signal_multiplier", 1.0)
    if max_amount > single_cap:
        max_amount = single_cap
    
    # 2. 核心/卫星仓位总上限检查（激进模式: 哑铃占比可配置，2026-11-03）
    total_initial = REFERENCE_TOTAL_CAPITAL
    core_limit = total_initial * float(_AGGR_CFG.get("dumbbell_core_max", 0.80))  # 核心仓最多80%
    sat_limit = total_initial * float(_AGGR_CFG.get("dumbbell_sat_max", 0.20))   # 卫星仓最多20%
    
    # 计算当前核心/卫星仓位价值
    positions = state.get("positions", {})
    core_value = 0
    sat_value = 0
    for p in positions.values():
        if p.get("position"):
            b = p.get("bucket", "satellite")
            val = p.get("cash", 0) + p.get("shares", 0) * p.get("entry_price", 0) * (1 + 0.001)  # 近似市值
            if b == "core":
                core_value += val
            else:
                sat_value += val
    
    # 估算买入后价值
    est_buy_value = min(pos["cash"], max_amount)
    
    if bucket == "core":
        if core_value + est_buy_value > core_limit:
            return False, f"🏰 核心仓位上限拦截: 当前¥{core_value:,.0f} + 估算买入¥{est_buy_value:,.0f} > 上限¥{core_limit:,.0f}"
    else:
        if sat_value + est_buy_value > sat_limit:
            return False, f"🛰️ 卫星仓位上限拦截: 当前¥{sat_value:,.0f} + 估算买入¥{est_buy_value:,.0f} > 上限¥{sat_limit:,.0f}"
    
    # 3. 方向分配上限（单日单方向不超过总仓位上限60%）
    direction = pos.get("direction", "其他")
    direction_alloc = position_plan.get("direction_allocation", {})
    dir_limit = direction_alloc.get(direction, 0)
    if dir_limit > 0:
        # 估算该方向已占用
        used_dir = 0
        for p in positions.values():
            if p.get("position") and p.get("direction", "其他") == direction:
                used_dir += p.get("cash", 0) + p.get("shares", 0) * p.get("entry_price", 0) * (1 + 0.001)
        if used_dir + est_buy_value > dir_limit:
            return False, f"🎯 方向{dir_limit:,.0f}上限拦截: {direction}已用¥{used_dir:,.0f} + 估算买入¥{est_buy_value:,.0f} > 上限"
    
    return True, None

def calc_position_value(pos: Dict, price: float) -> float:
    if pos.get("position"):
        return pos["cash"] + pos["shares"] * price
    return pos["cash"]


# ══════════════════════════════════════════
# 主扫描流程（供模拟盘/实盘调用）
# ═══════════════════════════════════════════
def _load_corr_clusters():
    """读取相关簇映射（data/corr_clusters.json，correlation_study.py 月度刷新）。
    fail-open: 文件缺失/损坏返回空 dict（上限不生效，仅告警）。"""
    path = os.path.join(WORKSPACE, "data", "corr_clusters.json")
    try:
        with open(path) as f:
            d = json.load(f)
        return d.get("symbol_to_cluster", {})
    except Exception as e:
        print(f"⚠️ 同簇上限: 簇映射不可用({e})，fail-open 跳过")
        return {}


def _apply_cluster_cap(signal, pos, state, should_exec, sym, sym_to_cluster, name,
                       mv_map=None, position_plan=None):
    """同相关簇**组合层市值占比**上限（2026-10-18 由「只数≤2」升级，用户拍板）。

    根因（评分卡 ef81ed2/2026-10-18_cluster_cap_scorecard.md）：只数口径只在建仓
    瞬间判定，持仓期同簇市值占比随价格漂移持续失控 → 三臂重放显示对收益/回撤
    均无保护。改为市值占比后，簇内齐涨会把占比顶高、后续加仓被自动拦，
    约束在整个持仓期每个扫描日都生效。

    规则: (同簇持仓市值 + 本笔预估买入额) / 组合总资产(持仓市值+现金)
          > CLUSTER_EXPO_CAP → 降级持有并阻断。
    市值口径: mv_map 优先（本轮已处理票的当日价市值），未处理票退化为
          pos["_last_mv"]（上次扫描落盘市值），再退化为 shares×entry_price。
    fail-open: 票未入簇/映射缺失/总市值过小/任何异常 → 放行仅告警。
    只限买入，不动卖出/止损（与旧口径一致）。
    """
    try:
        if "买入" not in str(signal.get("action", "")):
            return signal, should_exec
        cluster = sym_to_cluster.get(sym)
        if not cluster:
            return signal, should_exec

        def _mv_of(other_sym, other_pos):
            # 本轮已处理票优先用当日价市值
            if mv_map and other_sym in mv_map:
                return mv_map[other_sym]
            last = other_pos.get("_last_mv")
            if last:
                return float(last)
            if other_pos.get("position"):
                return float(other_pos.get("shares", 0)) * float(other_pos.get("entry_price", 0))
            return 0.0

        cluster_mv = 0.0
        total_mv = 0.0
        cash_total = 0.0
        for other_sym, other in (state.get("positions") or {}).items():
            cash_total += float(other.get("cash", 0) or 0)
            if other_sym == sym or not other.get("position"):
                continue
            mv = _mv_of(other_sym, other)
            total_mv += mv
            if sym_to_cluster.get(other_sym) == cluster:
                cluster_mv += mv

        # 预估本笔买入额（与 execute_trade 限额口径一致）
        plan = position_plan or {}
        bucket = pos.get("bucket", "satellite")
        single_cap = INITIAL_CAPITAL * _aggr_single_cap_pct(bucket)
        max_amount = plan.get("single_stock_max_amount", 100_000) * plan.get("buy_signal_multiplier", 1.0)
        est_buy = max(0.0, min(float(pos.get("cash", 0) or 0), max_amount, single_cap))

        # 分母 = 组合总资产(持仓市值+现金)，与 portfolio.total_value 同口径；
        # 用「市值/市值」口径会让空组合首笔必拦(100%)，语义错误（契约场景3）。
        denom = total_mv + cash_total
        if denom < 100_000:  # 组合过小/数据未就绪 → fail-open
            return signal, should_exec
        expo = (cluster_mv + est_buy) / denom
        if expo > CLUSTER_EXPO_CAP:
            signal["action"] = "持有"
            signal["reason"] = (signal.get("reason", "") +
                f" | [单簇暴露{expo:.0%}>{CLUSTER_EXPO_CAP:.0%}]{cluster}市值占比超限(同相关簇: 有效N 3.2/36)，禁止加仓").strip()
            print(f"  ⚠️ {name}({sym}) 单簇暴露拦截: {cluster} 当前簇市值¥{cluster_mv:,.0f} + 本笔¥{est_buy:,.0f} → {expo:.1%} > {CLUSTER_EXPO_CAP:.0%}")
            return signal, False
    except Exception as e:
        print(f"⚠️ 单簇暴露检查异常({name}): {e}，fail-open 放行")
    return signal, should_exec


def _apply_total_expo_cap(signal, pos, state, should_exec, sym, name,
                          mv_map=None, position_plan=None):
    """组合总仓位上限执法（2026-10-18 用户拍板接线）。

    背景（约束层盘点）：030 PositionSizer 每日计算 total_limit_amount
    （总资金×市场阶段/情绪/健康度乘数），但此前**无任何消费方**——哑铃
    core80%+sat20%=100% 允许满仓，动态总仓上限形同虚设。

    规则: 当前组合总持仓市值 + 本笔预估买入额 > total_limit_amount
          → 降级持有并阻断买入。
    市值口径与 _apply_cluster_cap 相同（mv_map 当日价优先 → _last_mv → 成本价）。
    fail-open: plan 缺失/total_limit_amount<=0/任何异常 → 放行仅告警。
    只限买入，不动卖出/止损；TOTAL_LIMIT_ENFORCE=0 全局回退。
    """
    try:
        if not TOTAL_LIMIT_ENFORCE:
            return signal, should_exec
        if "买入" not in str(signal.get("action", "")):
            return signal, should_exec
        plan = position_plan or {}
        limit_amount = float(plan.get("total_limit_amount", 0) or 0)
        if limit_amount <= 0:
            return signal, should_exec  # fail-open: 无有效上限不执法

        def _mv_of(other_sym, other_pos):
            if mv_map and other_sym in mv_map:
                return mv_map[other_sym]
            last = other_pos.get("_last_mv")
            if last:
                return float(last)
            if other_pos.get("position"):
                return float(other_pos.get("shares", 0)) * float(other_pos.get("entry_price", 0))
            return 0.0

        total_mv = 0.0
        for other_sym, other in (state.get("positions") or {}).items():
            if other_sym == sym or not other.get("position"):
                continue
            total_mv += _mv_of(other_sym, other)

        # 预估买入额（与 execute_trade 同口径钳制）
        bucket = pos.get("bucket", "satellite")
        single_cap = INITIAL_CAPITAL * _aggr_single_cap_pct(bucket)
        max_amount = plan.get("single_stock_max_amount", 100_000) * plan.get("buy_signal_multiplier", 1.0)
        est_buy = max(0.0, min(float(pos.get("cash", 0) or 0), max_amount, single_cap))

        if total_mv + est_buy > limit_amount:
            signal["action"] = "持有"
            signal["reason"] = (signal.get("reason", "") +
                f" | [总仓上限]持仓¥{total_mv:,.0f}+本笔¥{est_buy:,.0f} > 030上限¥{limit_amount:,.0f}"
                f"({plan.get('total_limit_pct', 0):.0%})，禁止开新仓").strip()
            print(f"  ⚠️ {name}({sym}) 总仓上限拦截: 持仓¥{total_mv:,.0f} + 本笔¥{est_buy:,.0f} > ¥{limit_amount:,.0f}")
            return signal, False
    except Exception as e:
        print(f"⚠️ 总仓上限检查异常({name}): {e}，fail-open 放行")
    return signal, should_exec


def run_portfolio_scan(
    stocks: List[Tuple[str, str]],
    fetch_data_func,  # 签名: fetch_data(symbol) -> DataFrame
    today_str: str = None,
    state: Dict = None,
    strategy_map: Dict = None,
    default_strategy: str = "ema_cross",
) -> Dict:
    """
    统一的组合扫描入口（含三层分层逻辑）。
    返回: {portfolio_summary, details, messages, state}
    """
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")

    if state is None:
        state = load_state()
    if strategy_map is None:
        strategy_map = load_strategy_map()

    # 刷新 030 风控（附带上一扫描日真实 (close, atr) 缓存；2026-11-03 接真实 ATR）
    RiskGuard.refresh(atr_map=_build_atr_map_from_state(state))
    fatal_risk = RiskGuard.get_fatal_risk()
    position_plan = RiskGuard.get_position_plan()
    # 同相关簇映射（2026-10-17 同簇约束数据源；fail-open）
    cluster_map = _load_corr_clusters()
    # 本轮已处理票的当日市值缓存（供单簇暴露上限跨票累计；2026-10-18）
    mv_map: Dict[str, float] = {}

    # 同步策略映射（含 bucket）
    sync_strategy_map(state, strategy_map, default_strategy)

    # 模式切换自愈：存量止损冷却收缩到当前配置（2026-11-03 用户拍板）
    _n_clamped = _clamp_legacy_stop_cooldowns(state)
    if _n_clamped:
        print(f"  ♻️ 存量止损冷却已收缩到激进配置({_AGGR_CFG.get('stop_cooldown_days')}天): {_n_clamped} 只")

    # Tier3：启动时强制清仓（仅首次）
    _force_liquidate_tier3(state, strategy_map)

    print(f"📡 组合扫描开始 ({today_str})...")
    all_msgs = []

    # ── B-管道化（2026-11-04 用户拍板）：信号日排队 / T+1 开盘价成交 ──
    _EXEC_ENABLED = auto_executor.auto_executor_enabled()
    _PENDING_INDEX = auto_executor.load_pending_intents(today_str) if _EXEC_ENABLED else {}
    # 政权分配器接入（2026-11-08 任务1）：读取最近一份 signal_date < today 的政权配置，
    # 作为 T+1 成交时「无章旧意向」的 day 级兜底（有章新意向优先用自身携带的信号日政权章）
    _T1_REGIME_DAY = auto_executor.load_regime_t1_config(today_str) if _EXEC_ENABLED else None
    if _T1_REGIME_DAY:
        print(f"  🌦️ [regime] day 兜底配置: {_T1_REGIME_DAY.get('regime')} "
              f"(signal_date={_T1_REGIME_DAY.get('signal_date')})")
    _EXEC_CTX = {
        "pending_index": _PENDING_INDEX,
        "execute_trade": execute_trade,
        "check_dedup": RiskGuard.check_dedup,
        "position_plan": position_plan,
        "max_age_days": int(os.getenv("AUTO_EXEC_MAX_AGE") or 5),
        "regime_t1_day": _T1_REGIME_DAY,
    }
    trade_count = 0
    errors = []
    results = []

    # ── T2-3: 批量 I/O 预载（2026-11-04）──
    # 信号存档/交易意向/审计日志原实现每票读+写整个文件（36 票≈150 次文件 IO）→
    # 全部改为内存缓冲 + 扫描结束一次性落盘；审计日志用单句柄逐条 append（崩溃不丢已写条目）
    signal_archive_path = os.path.join(WORKSPACE, "data", "signal_archive", f"{today_str}_signals.json")
    signal_archive_buf: Dict[str, Any] = {}
    if os.path.exists(signal_archive_path):
        try:
            with open(signal_archive_path, "r") as f:
                signal_archive_buf = json.load(f)
        except Exception as e:
            print(f"⚠️ 信号存档预载失败（将重建）: {e}")
            signal_archive_buf = {}
    intents_path = os.path.join(WORKSPACE, "data", "trade_intents", f"{today_str}_intents.json")
    intents_buf: Dict[str, Any] = {}
    if os.path.exists(intents_path):
        try:
            with open(intents_path, "r") as f:
                intents_buf = json.load(f)
        except Exception as e:
            print(f"⚠️ 交易意向预载失败（将重建）: {e}")
            intents_buf = {}
    audit_path = os.path.join(WORKSPACE, "audit", "trades_audit.jsonl")
    os.makedirs(os.path.dirname(audit_path), exist_ok=True)
    try:
        audit_fh = open(audit_path, "a", encoding="utf-8")
    except Exception as e:
        print(f"⚠️ 审计日志打开失败（本轮不落盘审计）: {e}")
        audit_fh = None

    # ── T2-3: 并行预取阶段（2026-11-04 执行流水线优化）──
    # 数据获取是纯 IO，与策略计算/风控完全解耦；预取完成后主循环变纯 CPU。
    # 线程安全：新浪 HTTP 主源天然并发；baostock 降级链已在 bs_session 用 RLock 串行化。
    # 回退：SCAN_PARALLEL_WORKERS=0 恢复串行。
    t_scan_start = time.time()
    workers = int(os.getenv("SCAN_PARALLEL_WORKERS", "6"))
    dfs: Dict[str, Any] = {}
    if workers > 1 and len(stocks) > 3:
        from concurrent.futures import ThreadPoolExecutor

        def _fetch_one(s):
            try:
                return s, fetch_data_func(s)
            except Exception as e:
                return s, e

        with ThreadPoolExecutor(max_workers=workers) as _pool:
            for _s, _df in _pool.map(_fetch_one, [sym for sym, _ in stocks]):
                dfs[_s] = _df
        print(f"  ⚡ 并行预取 {len(dfs)}/{len(stocks)} 只完成 (workers={workers}, {time.time()-t_scan_start:.1f}s)")
    else:
        for sym, _ in stocks:
            try:
                dfs[sym] = fetch_data_func(sym)
            except Exception as e:
                dfs[sym] = e

    # ── 政权分配器接入（2026-11-08 任务1）：T 日收盘扫描，预取完成后、意向创建前 ──
    # 调 regime_allocator.generate_next_day_config() 生成 T+1 政权权重配置：
    #   a) 落盘 data/runtime/regime_t1_config.json（T+1 日作 day 级兜底）
    #   b) 返回精简章，盖章到本日新建 PENDING 意向（T+1 开盘成交按其调整单票上限）
    # fail-open：样本不足/异常 → None（意向无章，成交时回退 day 兜底或原样），不阻断扫描
    _REGIME_T1_STAMP = None
    if _EXEC_ENABLED:
        try:
            _REGIME_T1_STAMP = auto_executor.prepare_regime_t1(dfs, signal_date=today_str)
            if _REGIME_T1_STAMP:
                print(f"  🌦️ [regime] T+1 政权配置: {_REGIME_T1_STAMP.get('regime')} "
                      f"(max_pos={_REGIME_T1_STAMP.get('max_position_pct')}, "
                      f"conf={_REGIME_T1_STAMP.get('regime_probability')})")
        except Exception as _e:
            print(f"⚠️ [regime] T+1 政权配置生成失败（fail-open，意向将无政权章）: {_e}")

    for sym, name in stocks:
        pos = state["positions"].get(sym)
        if pos is None:
            # 初始化时读取 bucket
            info = strategy_map.get(sym, default_strategy)
            if isinstance(info, dict):
                strat = info.get("strategy", default_strategy)
                bucket = info.get("bucket", "tier1")
            else:
                strat = info
                bucket = get_bucket(sym, strategy_map)
            pos = init_position(sym, name, strat, bucket)
            state["positions"][sym] = pos
        pos["_symbol"] = sym
        # 每日递减止损冷却期
        RiskGuard.decay_stop_cooldown(pos)
        strat = pos["strategy"]
        tier = pos.get("tier") or get_bucket(sym, strategy_map)
        slabel = STRATEGY_LABELS.get(strat, strat)

        t_sym_start = time.time()
        df = dfs.get(sym)
        if isinstance(df, Exception):
            errors.append(f"{name}({sym}) 数据获取失败: {df}")
            results.append({"symbol": sym, "name": name, "error": f"数据获取失败: {df}",
                            "position": pos["position"], "strategy": slabel})
            continue
        if df is None or len(df) < 60:
            errors.append(f"{name}({sym}) 数据获取失败")
            results.append({"symbol": sym, "name": name, "error": "数据获取失败",
                            "position": pos["position"], "strategy": slabel})
            continue

        sig_func = SIGNAL_FUNCS.get(strat, signal_ema_cross)
        try:
            signal = sig_func(df)
        except Exception as e:
            errors.append(f"{name}({sym}) 信号计算失败: {e}")
            results.append({"symbol": sym, "name": name, "error": str(e),
                            "position": pos["position"], "strategy": slabel})
            continue
        t_signal_ms = round((time.time() - t_sym_start) * 1000, 1)

        # ── T1-1: 信号存档 (Signal Archiving，T2-3 改批量 I/O) ──
        # 在进入任何风险门控/修改前，保存策略原生信号；
        # 全部写入内存字典，扫描结束时一次性落盘（原实现每票读+写整个文件 2 次 IO）
        raw_signal = signal.copy()
        signal_archive_buf[sym] = {
            "name": name,
            "strategy": strat,
            "raw_signal": raw_signal,
            "timestamp": datetime.now().isoformat()
        }

        # ── T1-2: 决策链路审计 (Full-Chain Audit Log，T2-3 改单句柄批量写) ──
        # 记录从【原生信号】到【最终意向】的每一个拦截点
        audit_entry = {
            "timestamp": datetime.now().isoformat(),
            "symbol": sym,
            "name": name,
            "strategy": strat,
            "raw_action": raw_signal["action"],
            "guards": {},
            "timing_ms": {"signal": t_signal_ms},
            "final_action": "UNKNOWN",
            "status": "SKIPPED",
            # T3-归因：决策时市场环境快照（failure_attribution 消费）
            "regime": {
                "fatal": fatal_risk.get("fatal_triggered", False),
                "high_risk": fatal_risk.get("high_risk_triggered", False),
                "buy_mult": position_plan.get("buy_signal_multiplier", 1.0),
                "total_limit_pct": position_plan.get("total_limit_pct", 0),
                "market_regime": (_REGIME_T1_STAMP or {}).get("regime"),
            },
        }

        # ── B-管道化：先消费往日 PENDING 意向（以今日开盘价成交；滑点用昨缓存 ATR/成交额——开盘时点可知的诚实输入）──
        if _EXEC_ENABLED:
            _act, _msg = auto_executor.consume_pending_for_symbol(
                _EXEC_CTX, sym, name, pos, df, today_str, audit_entry=audit_entry, strat=strat)
            if _act:
                trade_count += 1
                all_msgs.append(_msg)

        # 缓存真实 (close, atr14) 供次日 030 买入乘数微调（2026-11-03 接真实 ATR）
        # T2-1: 同步缓存当日成交额 _last_amount（动态滑点流动性分量输入）
        _p = signal.get("price") or 0
        _a = signal.get("atr") or 0
        if _p and _a:
            try:
                pos["_last_close"] = round(float(_p), 4)
                pos["_last_atr"] = round(float(_a), 6)
            except (TypeError, ValueError) as _e:
                print(f"⚠️ ATR缓存失败 {name}({sym}): {_e}")
        try:
            _last_row = df.iloc[-1]
            _vol = float(pd.to_numeric(_last_row.get("volume"), errors="coerce") or 0)
            _amt = pd.to_numeric(_last_row.get("amount"), errors="coerce")
            if pd.isna(_amt) or _amt <= 0:
                _amt = _vol * float(_p or 0)  # 无成交额列时用 成交量×收盘价 估算
            if _amt and _amt > 0:
                pos["_last_amount"] = round(float(_amt), 2)
        except (TypeError, ValueError, KeyError) as _e:
            # 成交额缺失时流动性分量自然退化为0，不阻断扫描，但必须可见（silent_except 棘轮）
            print(f"⚠️ 成交额缓存失败 {name}({sym}): {_e}")

        # 三层分层过滤（核心逻辑）
        # 注入标识供 Tier 吞信号留痕（_apply_tier_filter 需要 symbol/name）
        signal.setdefault("_symbol", sym)
        signal.setdefault("name", name)
        
        # 1. Tier Filter
        sig_before = signal.copy()
        signal = _apply_tier_filter(signal, pos, tier)
        audit_entry["guards"]["tier"] = "OK" if signal["action"] == sig_before["action"] else f"BLOCK({sig_before['action']}->{signal['action']})"
        
        # 2. RiskGuard Overrides
        sig_before = signal.copy()
        signal = RiskGuard.override_signal(signal, pos)
        audit_entry["guards"]["risk_override"] = "OK" if signal["action"] == sig_before["action"] else f"BLOCK({sig_before['action']}->{signal['action']})"
        
        # 3. Strategy Switch
        sig_before = signal.copy()
        signal = RiskGuard.check_strategy_switch(signal, pos)
        audit_entry["guards"]["strat_switch"] = "OK" if signal["action"] == sig_before["action"] else f"BLOCK({sig_before['action']}->{signal['action']})"
        
        # 4. Buy Streak Dampening
        sig_before = signal.copy()
        signal, should_exec = RiskGuard.apply_buy_streak_dampening(signal, pos)
        audit_entry["guards"]["streak_damp"] = "OK" if signal["action"] == sig_before["action"] else f"BLOCK({sig_before['action']}->{signal['action']})"
        
        # 5. Resonance Gate (T0-4)
        if signal["action"] == "买入" and not pos["position"]:
            try:
                from analysis.three_factor_helper import ResonanceGate
                ok, info = ResonanceGate().check_buy(str(pos["_symbol"]))
                if not ok:
                    sig_before = signal.copy()
                    signal["action"] = "持有"
                    signal["reason"] = f"⛔ 三因素门控拦截: {info.get('reason','')}"
                    should_exec = False
                    audit_entry["guards"]["resonance"] = f"BLOCK({sig_before['action']}->持有)"
                else:
                    audit_entry["guards"]["resonance"] = "OK"
            except Exception as e:
                if os.getenv("GATE_ERROR_POLICY", "blocked").lower() == "pass":
                    audit_entry["guards"]["resonance"] = "ERROR(PASS)"
                else:
                    sig_before = signal.copy()
                    signal["action"] = "持有"
                    signal["reason"] = f"⛔ 三因素门控异常拦截 (GATE_ERROR_POLICY=blocked): {str(e)[:120]}"
                    should_exec = False
                    audit_entry["guards"]["resonance"] = f"BLOCK({sig_before['action']}->持有)"
        else:
            audit_entry["guards"]["resonance"] = "N/A"
            
        # 6. Cluster Cap
        sig_before = signal.copy()
        signal, should_exec = _apply_cluster_cap(
            signal, pos, state, should_exec, sym, cluster_map, name,
            mv_map=mv_map, position_plan=position_plan)
        audit_entry["guards"]["cluster"] = "OK" if signal["action"] == sig_before["action"] else f"BLOCK({sig_before['action']}->{signal['action']})"
        
        # 7. Total Cap
        sig_before = signal.copy()
        signal, should_exec = _apply_total_expo_cap(
            signal, pos, state, should_exec, sym, name,
            mv_map=mv_map, position_plan=position_plan)
        audit_entry["guards"]["total_cap"] = "OK" if signal["action"] == sig_before["action"] else f"BLOCK({sig_before['action']}->{signal['action']})"
        
        # 8. Dedup
        # 去重（统一用真实交易日 today_str；data_date 仅用于诊断/滞后检测）
        # 🔴 2026-11-04 T2-3 改造中曾误删此行，零污染端到端测试拦下（NameError）——已恢复
        data_date = df["date"].iloc[-1].strftime("%Y-%m-%d")
        sig_before = signal.copy()
        if not RiskGuard.check_dedup(signal, pos, today_str):
            should_exec = False
            # Dedup is a should_exec block, doesn't necessarily change action
            audit_entry["guards"]["dedup"] = "BLOCK(exec)"
        else:
            audit_entry["guards"]["dedup"] = "OK"


        # 🔴 数据滞后守卫（2026-09-29 新增）：
        # 成交日期必须 == 真实交易日 today_str。若拉到的数据最新日期滞后于今天
        # （跨交易日），说明日线源没更新到今天，此时基于过期数据执行会「无中生有」
        # 回填历史成交（实证：09-27 回补出 09-24 雷科防务涨停价卖出，详见记忆）。
        data_stale = data_date < today_str
        if data_stale:
            stale_reason = f"数据滞后 {data_date} < {today_str}，跳过执行"
            if should_exec:
                errors.append(f"{name}({sym}) {stale_reason}（原信号:{signal['action']}）")
            should_exec = False
        audit_entry["guards"]["stale_data"] = "BLOCK" if data_stale else "OK"

        # 仅新交易日执行（成交日期统一用 today_str，绝不回填历史日期）
        if _EXEC_ENABLED:
            # ── B-管道化（2026-11-04 用户拍板）：信号日只排队，T+1 开盘价成交；同 bar 收盘前视退役。──
            # 注：同日重跑时排队幂等覆盖（不依赖 last_signal_date，防晨间误扫劫持当日排队窗口）；
            #     仅 买入/卖出 入队——持有等非交易动作不入队（否则产出永不成交的 PENDING 噪声）
            if should_exec and signal["action"] not in ("买入", "卖出"):
                audit_entry["final_action"] = signal["action"]
                audit_entry["status"] = "HOLD"
            elif should_exec and signal["action"] == "卖出" and not pos.get("position"):
                audit_entry["final_action"] = signal["action"]
                audit_entry["status"] = "SKIP(空仓卖出不入队)"
            elif should_exec:
                # 政权分配器接入（2026-11-08 任务1）：意向盖章信号日 T 的 T+1 政权配置
                intents_buf[sym] = auto_executor.build_pending_intent(
                    name, signal, pos, strat, datetime.now().isoformat(),
                    regime_t1=_REGIME_T1_STAMP)
                audit_entry["final_action"] = signal["action"]
                audit_entry["status"] = "QUEUED"
                all_msgs.append(f"📨 {name}({sym}) {signal['action']}已排队 → 下一交易日开盘价执行")
            else:
                audit_entry["final_action"] = signal["action"]
                audit_entry["status"] = "HOLD"
        elif should_exec and state.get("last_signal_date") is not None and today_str != state.get("last_signal_date"):
            # ── legacy 回退路径（AUTO_EXECUTOR=0）：原同 bar 收盘成交 ──
            intents_buf[sym] = {
                "name": name,
                "action": signal["action"],
                "reason": signal["reason"],
                "price": signal["price"],
                "bucket": pos.get("bucket", "satellite"),
                "execution_timestamp": datetime.now().isoformat(),
                "status": "PENDING"
            }

            action, msg = execute_trade(pos, signal, today_str, strategy=strat, position_plan=position_plan)
            if action:
                trade_count += 1
                all_msgs.append(msg)
                intents_buf[sym]["status"] = "SUCCESS"
            audit_entry["final_action"] = action or signal["action"]
            audit_entry["status"] = "SUCCESS" if action else "HOLD"
        else:
            audit_entry["final_action"] = signal["action"]
            audit_entry["status"] = "HOLD"

        # ── T1-2 审计落盘（T2-3 改单句柄逐条写；此处是每票唯一写入点）──
        try:
            audit_entry["timing_ms"]["guards"] = round((time.time() - t_sym_start) * 1000 - t_signal_ms, 1)
            if audit_fh:
                audit_fh.write(json.dumps(audit_entry, ensure_ascii=False) + "\n")
                audit_fh.flush()
        except Exception as e:
            print(f"⚠️ 审计日志写入失败 {name}({sym}): {e}")

        # 计算持仓价值
        pos_val = calc_position_value(pos, signal["price"])
        pos_pl = pos["total_pl"]
        if pos.get("position"):
            pos_pl += (pos["shares"] * signal["price"]) - (pos["shares"] * pos["entry_price"] * (1 + pos.get("entry_slippage", SLIPPAGE)))
        pos_return = round((pos_val / INITIAL_CAPITAL - 1) * 100, 2)
        pos_pl_round = round(pos_pl, 2)

        bucket = pos.get("bucket", "unknown")
        if bucket == "unknown":
            bucket, _, _ = classify_bucket(sym)
            pos["bucket"] = bucket

        # 单簇暴露上限跨票缓存：仅持仓票记市值（现金不计入分母）
        mv_now = (pos["shares"] * signal["price"]) if pos.get("position") else 0.0
        mv_map[sym] = mv_now
        pos["_last_mv"] = mv_now

        results.append({
            "symbol": sym, "name": name,
            "strategy": slabel,
            "price": signal["price"],
            "position": pos["position"],
            "entry_price": pos["entry_price"] if pos["position"] else 0,
            "entry_date": pos["entry_date"],
            "shares": pos["shares"],
            "cash": round(pos["cash"], 2),
            "value": round(pos_val, 2),
            "unrealized_pl": round(pos_pl_round, 2),
            "return_pct": pos_return,
            "trade_count": pos["trade_count"],
            "signal": signal["action"],
            "reason": signal["reason"],
            "fib_targets": pos.get("fib_targets", {}),
            "atr_stop": signal.get("atr_stop"),
            "fatal_risk_override": fatal_risk.get("fatal_triggered", False) or fatal_risk.get("high_risk_triggered", False),
            "position_mult": position_plan.get("buy_signal_multiplier", 1.0),
            "max_buy_amount": round(position_plan.get("single_stock_max_amount", 100_000) * position_plan.get("buy_signal_multiplier", 1.0), 2),
            "bucket": bucket,
        })

    # 仅在 legacy 模式（AUTO_EXECUTOR=0）下更新 last_signal_date
    # 管道化模式（_EXEC_ENABLED=True）下，执行日由 auto_executor.consume_pending_for_symbol
    # 在消费成功后标记成交，不在此处预占当日（否则次日执行守卫会误判吞单）
    if not _EXEC_ENABLED:
        state["last_signal_date"] = today_str
        save_state(state)
    else:
        # 管道化模式：仅保存 state（仓位/资金等），不写 last_signal_date
        save_state(state)

    # ── T2-3: 扫描结束批量落盘（信号存档 + 交易意向）──
    try:
        os.makedirs(os.path.dirname(signal_archive_path), exist_ok=True)
        with open(signal_archive_path, "w") as f:
            json.dump(signal_archive_buf, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 信号存档落盘失败: {e}")
    try:
        os.makedirs(os.path.dirname(intents_path), exist_ok=True)
        with open(intents_path, "w") as f:
            json.dump(intents_buf, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 交易意向落盘失败: {e}")
    if _EXEC_ENABLED:
        # 历史意向状态回写（FILLED/BLOCKED/CANCELLED/EXPIRED/SUPERSEDED；文件级当日 .bak）
        try:
            auto_executor.flush_intent_updates(
                _PENDING_INDEX, visited_syms={s for s, _ in stocks}, today_str=today_str)
        except Exception as _e:
            print(f"⚠️ 意向状态回写失败: {_e}")
    try:
        if audit_fh:
            audit_fh.close()
    except Exception:
        pass
    print(f"⏱️ 扫描总耗时 {time.time()-t_scan_start:.1f}s (预取→信号→门控→成交)")

    # 汇总
    # 治本（2026-09-25）：total_value / cash_total 改从持久化 state 计算，
    # 而非当次 results（results 在「某票数据拉取失败」时会缺失该票 cash，
    # 导致 total_value 断崖暴跌——09-25 曾出现 271 万→31 万假崩）。
    # 兜底逻辑：成功票用当次 value（含持仓市值），失败票退化为 pos["cash"]（现金口径）。
    success_syms = {r["symbol"] for r in results if "value" in r and "symbol" in r}
    _total_value_acc = 0.0
    _cash_total_acc = 0.0
    for sym, pos in state["positions"].items():
        cash = float(pos.get("cash", 0) or 0)
        if sym in success_syms:
            r = next(x for x in results if x.get("symbol") == sym)
            _total_value_acc += float(r.get("value", cash) or 0)
        else:
            # 数据失败/未处理票：保守用现金口径（不含未平仓浮盈浮亏）
            _total_value_acc += cash
        _cash_total_acc += cash
    total_value = _total_value_acc
    cash_total = _cash_total_acc

    total_initial = REFERENCE_TOTAL_CAPITAL
    total_return = round((total_value - total_initial) / total_initial * 100, 2)
    pos_count = sum(1 for r in results if r.get("position"))
    buy_qty = sum(1 for r in results if r.get("signal") == "买入")
    sell_qty = sum(1 for r in results if r.get("signal") == "卖出")

    core_positions = [r for r in results if r.get("position") and r.get("bucket") == "core"]
    sat_positions = [r for r in results if r.get("position") and r.get("bucket") == "satellite"]
    core_value = sum(r["value"] for r in core_positions)
    sat_value = sum(r["value"] for r in sat_positions)
    core_limit = REFERENCE_TOTAL_CAPITAL * 0.8
    sat_limit = REFERENCE_TOTAL_CAPITAL * 0.2

    portfolio = {
        "total_value": round(total_value, 2),
        "total_initial": REFERENCE_TOTAL_CAPITAL,
        "total_return_pct": total_return,
        "positions_held": pos_count,
        "buy_signals_today": buy_qty,
        "sell_signals_today": sell_qty,
        "trades_executed": trade_count,
        "core_positions": len(core_positions),
        "satellite_positions": len(sat_positions),
        "core_value": round(core_value, 2),
        "satellite_value": round(sat_value, 2),
        "core_limit_pct": round(core_value / total_initial * 100, 2) if total_initial > 0 else 0,
        "sat_limit_pct": round(sat_value / total_initial * 100, 2) if total_initial > 0 else 0,
        "fatal_risk_triggered": fatal_risk.get("fatal_triggered", False),
        "high_risk_triggered": fatal_risk.get("high_risk_triggered", False),
        "position_plan": position_plan,
    }

    # 记录每日权益快照（移到循环外，仅记录一次）
    # cash_total 已在汇总段从 state 完整计算（治本），此处直接用
    try:
        append_equity_snapshot(today_str, portfolio, core_value, sat_value, cash_total)
    except Exception as e:
        print(f"⚠️ 权益快照记录失败: {e}")

    return {
        "date": today_str,
        "stock_count": len(stocks),
        "portfolio": portfolio,
        "errors": errors,
        "details": results,
        "messages": all_msgs,
        "state": state,
    }


# ═══════════════════════════════════════════
# 人类可读报告生成
# ═══════════════════════════════════════════
def generate_human_report(scan_result: Dict) -> str:
    today_str = scan_result["date"]
    portfolio = scan_result["portfolio"]
    results = scan_result["details"]
    all_msgs = scan_result["messages"]
    errors = scan_result["errors"]
    fatal_risk = {"fatal_triggered": portfolio["fatal_risk_triggered"],
                  "high_risk_triggered": portfolio["high_risk_triggered"]}
    position_plan = portfolio["position_plan"]

    lines = []
    lines.append(f"💼 **全组合日报** ({today_str})")
    lines.append(f"组合总资产: ¥{portfolio['total_value']:,.2f} ({portfolio['total_return_pct']:+.2f}%) | 持仓 {portfolio['positions_held']}/{scan_result['stock_count']} 只")
    lines.append(f"今日信号: 🟢买入 {portfolio['buy_signals_today']} | 🔴卖出 {portfolio['sell_signals_today']} | 实际成交 {portfolio['trades_executed']} 笔")

    if fatal_risk.get("fatal_triggered", False):
        lines.append("☠️ **030致命风险触发**: 强制清仓，禁止买入")
    elif fatal_risk.get("high_risk_triggered", False):
        lines.append("🟡 **030高风险防御**: 核心中军≤-3%，减仓止损，不开新仓")
    else:
        lines.append(f"📐 **030仓位计划**: 总上限{position_plan.get('total_limit_pct', 0):.1%}(¥{position_plan.get('total_limit_amount', 0):,.0f}) | 单股上限{position_plan.get('single_stock_max_pct', 0):.1%}(¥{position_plan.get('single_stock_max_amount', 0):,.0f}) | 买入乘数{position_plan.get('buy_signal_multiplier', 1.0):.1f}x")
        lines.append(f"🏰 **哑铃仓位**: 核心仓{portfolio['core_positions']}只(¥{portfolio['core_value']:,.0f}/{portfolio['core_positions']*100000*0.8:,.0f}) | 卫星仓{portfolio['satellite_positions']}只(¥{portfolio['satellite_value']:,.0f}/{portfolio['satellite_positions']*100000*0.2:,.0f})")
    lines.append("")

    if all_msgs:
        lines.append("📝 **今日成交**")
        lines.extend(all_msgs)
        lines.append("")

    lines.append("📈 **持仓明细**(按收益率排序)")
    held = sorted([r for r in results if r.get("position")], key=lambda x: -x.get("return_pct", 0))
    if held:
        lines.append("| 股票 | 策略 | 现价 | 成本 | 持仓股 | 收益率 | 止损价 | 仓位类型 |")
        lines.append("|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|")
        for r in held:
            bucket = r.get("bucket", "未知")
            bucket_emoji = "🏰" if bucket == "core" else "🛰️" if bucket == "satellite" else "❓"
            atr_stop = r.get("atr_stop", 0)
            stop_str = f"¥{atr_stop:.2f}" if atr_stop and atr_stop > 0 else "—"
            lines.append(f"| {r['name']}({r['symbol']}) | {r['strategy']} | ¥{r['price']:.2f} | ¥{r['entry_price']:.2f} | {r['shares']} | {r['return_pct']:+.2f}% | {stop_str} | {bucket_emoji} |")
        lines.append("")

    lines.append("🎯 **今日信号**")
    buys = [r for r in results if r.get("signal") == "买入"]
    sells = [r for r in results if r.get("signal") == "卖出"]
    if buys:
        lines.append("🟢 买入信号:")
        for r in buys:
            max_amt = r.get("max_buy_amount", 0)
            lines.append(f"  • {r['name']}({r['symbol']}) [{r['strategy']}] {r['reason']} | 限额¥{max_amt:,.0f}")
    if sells:
        lines.append("🔴 卖出信号:")
        for r in sells:
            fatal_override = " ☠️" if r.get("fatal_risk_override", False) else ""
            lines.append(f"  • {r['name']}({r['symbol']}) [{r['strategy']}] {r['reason']}{fatal_override}")
    if not buys and not sells:
        lines.append("  今日无买卖信号，全部持有/观望")

    if errors:
        lines.append("")
        lines.append("⚠️ 数据异常:")
        for e in errors:
            lines.append(f"  • {e}")

    return "\n".join(lines)


if __name__ == "__main__":
    # 简单自测
    from analysis.portfolio_sim import fetch_data, STOCKS
    result = run_portfolio_scan(STOCKS[:3], fetch_data)
    print(generate_human_report(result))
