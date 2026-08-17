#!/usr/bin/env python3
"""
致命风险检测器 (030体系核心风控模块)
=====================================
独立运行，每日收盘/盘前检测，输出 FATAL_RISK_JSON
供所有下游系统强制引用：adaptive_dual, portfolio_sim, signal_audit, close_scan_v2

判断逻辑完全对齐 030 体系：
- 核心辨识度票一字跌停封单 ≥ 60亿 → 无条件清仓+停止买入
- 核心大票一字跌停封单 ≥ 100亿 → 无条件清仓+停止买入
- 多个近期辨识度标的同时一字跌停 → 无条件清仓+停止买入
- 核心中军竞价/开盘 ≤ -3%（非连续杀跌） → 高风险防御：不开新仓，持仓设-5%止损

数据源：新浪实时 hq.sinajs.cn (需 Referer) + akshare 涨停池/龙虎榜
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, date
from typing import Dict, List, Optional, Any
import urllib.request
import re

# 尝试导入 akshare
try:
    import akshare as ak
    HAS_AKSHARE = True
except Exception:
    HAS_AKSHARE = False

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
DATA_DIR = os.path.join(WORKSPACE, "data")
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 030 硬编码阈值（不可配置，防止人为放宽）
# ═══════════════════════════════════════════════════════════════
FATAL_THRESHOLDS = {
    "core_recognition_seal": 60_0000_0000,      # 核心辨识度票一字跌停封单 ≥ 60亿
    "core_large_seal": 100_0000_0000,           # 核心大票一字跌停封单 ≥ 100亿
    "core_leader_intraday_drop": -3.0,          # 核心中军竞价/开盘 ≤ -3%
}

# 核心辨识度标的白名单（需人工维护，近期涨停/连板/龙头）
CORE_RECOGNITION_STOCKS = {
    "600584": "长电科技", "002156": "通富微电", "688981": "中芯国际",  # 半导体
    "300014": "亿纬锂能", "002466": "天齐锂业", "601865": "福莱特",    # 新能源
    "300285": "国瓷材料", "603308": "应流股份",                        # 高端材料
    "300124": "汇川技术", "601100": "恒立液压",                        # 高端制造
    "600030": "中信证券", "600036": "招商银行",                        # 权重金融
    "000157": "中联重科", "601061": "中信金属",                        # 周期/资源
}

# 核心大票白名单（流通市值大、指数权重高）
CORE_LARGE_STOCKS = {
    "600030": "中信证券", "600036": "招商银行", "601995": "中金公司",
    "601066": "中信建投", "000987": "越秀资本", "600570": "恒生电子",
    "600660": "福耀玻璃", "601100": "恒立液压",
}


# ═══════════════════════════════════════════════════════════════
# 数据获取
# ═══════════════════════════════════════════════════════════════

def fetch_sina_spot(symbols: List[str]) -> Dict[str, dict]:
    """批量拉取新浪实时行情，返回 {纯代码: {...}}"""
    out = {}
    for i in range(0, len(symbols), 60):
        batch = symbols[i:i+60]
        url = "http://hq.sinajs.cn/list=" + ",".join(batch)
        req = urllib.request.Request(
            url,
            headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
        )
        try:
            raw = urllib.request.urlopen(req, timeout=12).read().decode("gbk")
        except Exception as e:
            log.warning(f"[SINA-SPOT] 批次请求失败: {e}")
            continue

        for line in raw.splitlines():
            if '="' not in line:
                continue
            key = line.split("hq_str_")[1].split("=")[0]
            val = line.split('"')[1].split(",")
            if len(val) < 10:
                continue
            symbol = key[2:]  # 去 sh/sz 前缀
            def f(x):
                try: return float(x)
                except: return 0.0
            out[symbol] = {
                "name": val[0],
                "open": f(val[1]), "pre_close": f(val[2]), "last": f(val[3]),
                "high": f(val[4]), "low": f(val[5]),
                "volume": int(float(val[8])), "amount": f(val[9]),
                "date": val[30] if len(val) > 30 else "",
                "time": val[31] if len(val) > 31 else "",
            }
    return out


def fetch_limit_up_pool() -> List[dict]:
    """获取涨停池数据（akshare stock_zt_pool_em），返回含封单金额"""
    if not HAS_AKSHARE:
        return []
    try:
        df = ak.stock_zt_pool_em(date=datetime.now().strftime("%Y%m%d"))
        if df is None or df.empty:
            return []
        # 关键字段：代码、名称、涨停类型、封单金额、首次封板时间、炸板次数
        cols = ["代码", "名称", "涨停类型", "封单金额", "首次封板时间", "炸板次数"]
        available = [c for c in cols if c in df.columns]
        return df[available].to_dict("records")
    except Exception as e:
        log.warning(f"[AKSHARE] 涨停池获取失败: {e}")
        return []


def fetch_lhb_detail() -> List[dict]:
    """获取龙虎榜详情（akshare stock_lhb_detail_em），辅助判断核心票异动"""
    if not HAS_AKSHARE:
        return []
    try:
        df = ak.stock_lhb_detail_em(date=datetime.now().strftime("%Y%m%d"))
        if df is None or df.empty:
            return []
        return df.to_dict("records")
    except Exception as e:
        log.warning(f"[AKSHARE] 龙虎榜获取失败: {e}")
        return []


# ═══════════════════════════════════════════════════════════════
# 核心判断逻辑
# ═══════════════════════════════════════════════════════════════

class FatalRiskDetector:
    """030 致命风险检测器"""

    def __init__(self):
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.sina_symbols = list({**CORE_RECOGNITION_STOCKS, **CORE_LARGE_STOCKS}.keys())
        self.sina_prefixed = [("sh" if s[0] in "69" else "sz") + s for s in self.sina_symbols]

    def check(self) -> dict:
        """主检测入口，返回标准 FATAL_RISK_JSON"""
        log.info("🔍 开始致命风险检测...")

        # 1. 拉取实时行情
        hq = fetch_sina_spot(self.sina_prefixed)

        # 2. 拉取涨停池（获取封单金额）
        zt_pool = fetch_limit_up_pool()

        # 3. 构建代码→封单金额映射（涨停池中代码带前缀）
        seal_map = {}
        for item in zt_pool:
            code = str(item.get("代码", "")).strip()
            if code:
                seal_map[code[-6:]] = item.get("封单金额", 0)  # 取后6位纯代码

        # 4. 逐条件检测
        conditions = []

        # 条件1：核心辨识度票一字跌停封单 ≥ 60亿
        cond1 = self._check_core_recognition_limit_down(hq, seal_map)
        if cond1:
            conditions.extend(cond1)

        # 条件2：核心大票一字跌停封单 ≥ 100亿
        cond2 = self._check_core_large_limit_down(hq, seal_map)
        if cond2:
            conditions.extend(cond2)

        # 条件3：多个近期辨识度标的同时一字跌停
        cond3 = self._check_multiple_recognition_limit_down(hq, seal_map)
        if cond3:
            conditions.extend(cond3)

        # 条件4：核心中军竞价/开盘 ≤ -3%（高风险防御，非致命）
        cond4 = self._check_core_leader_drop(hq)
        if cond4:
            conditions.extend(cond4)

        # 5. 汇总判断
        fatal_triggered = any(c["level"] == "FATAL" for c in conditions)
        high_risk_triggered = any(c["level"] == "HIGH_RISK" for c in conditions)

        if fatal_triggered:
            action = "FORCE_CLEAR_ALL"
            min_cash_days = 1
        elif high_risk_triggered:
            action = "HIGH_RISK_DEFENSE"
            min_cash_days = 0  # 当日生效，不强制空仓过夜
        else:
            action = "NONE"
            min_cash_days = 0

        result = {
            "date": self.today,
            "check_time": datetime.now().strftime("%H:%M:%S"),
            "triggered": fatal_triggered or high_risk_triggered,
            "fatal_triggered": fatal_triggered,
            "high_risk_triggered": high_risk_triggered,
            "action": action,
            "conditions": conditions,
            "recovery_condition": "次日竞价无新增一字跌停 + 封单回落至30亿以下 + 大盘未低开",
            "min_cash_days": min_cash_days,
            "data_source": "sina_hq + akshare_zt_pool",
            "checked_stocks": len(hq),
        }

        log.info(f"✅ 致命风险检测完成: action={action}, fatal={fatal_triggered}, high_risk={high_risk_triggered}, conditions={len(conditions)}")
        return result

    def _check_core_recognition_limit_down(self, hq: dict, seal_map: dict) -> List[dict]:
        """核心辨识度票一字跌停封单 ≥ 60亿"""
        findings = []
        for code, name in CORE_RECOGNITION_STOCKS.items():
            data = hq.get(code)
            if not data or data["last"] <= 0:
                continue
            # 判断一字跌停：最新价 == 跌停价 ≈ 昨收 * 0.9 (主板) 或 0.8 (创业/科创)
            # 简化：跌幅 ≤ -9.5% 且 最高==最低==最新 (一字板特征)
            change_pct = (data["last"] / data["pre_close"] - 1) * 100 if data["pre_close"] else 0
            is_limit_down = change_pct <= -9.5 and abs(data["high"] - data["low"]) < 0.01
            if is_limit_down:
                seal_amount = seal_map.get(code, 0)
                if seal_amount >= FATAL_THRESHOLDS["core_recognition_seal"]:
                    findings.append({
                        "type": "核心辨识度一字跌停",
                        "level": "FATAL",
                        "symbol": code,
                        "name": name,
                        "seal_amount": seal_amount,
                        "threshold": FATAL_THRESHOLDS["core_recognition_seal"],
                        "change_pct": round(change_pct, 2),
                        "msg": f"{name}({code}) 一字跌停，封单 {seal_amount/1e8:.1f}亿 ≥ 阈值 60亿",
                    })
        return findings

    def _check_core_large_limit_down(self, hq: dict, seal_map: dict) -> List[dict]:
        """核心大票一字跌停封单 ≥ 100亿"""
        findings = []
        for code, name in CORE_LARGE_STOCKS.items():
            data = hq.get(code)
            if not data or data["last"] <= 0:
                continue
            change_pct = (data["last"] / data["pre_close"] - 1) * 100 if data["pre_close"] else 0
            is_limit_down = change_pct <= -9.5 and abs(data["high"] - data["low"]) < 0.01
            if is_limit_down:
                seal_amount = seal_map.get(code, 0)
                if seal_amount >= FATAL_THRESHOLDS["core_large_seal"]:
                    findings.append({
                        "type": "核心大票一字跌停",
                        "level": "FATAL",
                        "symbol": code,
                        "name": name,
                        "seal_amount": seal_amount,
                        "threshold": FATAL_THRESHOLDS["core_large_seal"],
                        "change_pct": round(change_pct, 2),
                        "msg": f"{name}({code}) 一字跌停，封单 {seal_amount/1e8:.1f}亿 ≥ 阈值 100亿",
                    })
        return findings

    def _check_multiple_recognition_limit_down(self, hq: dict, seal_map: dict) -> List[dict]:
        """多个近期辨识度标的同时一字跌停"""
        limit_down_stocks = []
        for code, name in CORE_RECOGNITION_STOCKS.items():
            data = hq.get(code)
            if not data or data["last"] <= 0:
                continue
            change_pct = (data["last"] / data["pre_close"] - 1) * 100 if data["pre_close"] else 0
            is_limit_down = change_pct <= -9.5 and abs(data["high"] - data["low"]) < 0.01
            if is_limit_down:
                limit_down_stocks.append({"symbol": code, "name": name, "change_pct": round(change_pct, 2)})

        if len(limit_down_stocks) >= 2:
            return [{
                "type": "多辨识度标的同时一字跌停",
                "level": "FATAL",
                "stocks": limit_down_stocks,
                "count": len(limit_down_stocks),
                "msg": "共 {} 只核心辨识度标的同时一字跌停: {}".format(len(limit_down_stocks), ', '.join('{}({})'.format(s.get('name',''), s.get('symbol','')) for s in limit_down_stocks)),
            }]
        return []

    def _check_core_leader_drop(self, hq: dict) -> List[dict]:
        """核心中军竞价/开盘 ≤ -3%（高风险防御）"""
        findings = []
        # 以各板块容量核心为准（简化：取监控池中流通市值最大的几只）
        leaders = ["600584", "300014", "600030", "600036", "600584", "000157", "300124", "688981"]
        for code in leaders:
            data = hq.get(code)
            if not data or data["last"] <= 0 or data["open"] <= 0:
                continue
            # 竞价/开盘跌幅 = (今开 - 昨收) / 昨收
            open_change = (data["open"] / data["pre_close"] - 1) * 100 if data["pre_close"] else 0
            if open_change <= FATAL_THRESHOLDS["core_leader_intraday_drop"]:
                # 简化：不判断"连续2日以上杀跌"例外，实战中需人工复核
                findings.append({
                    "type": "核心中军竞价大跌",
                    "level": "HIGH_RISK",
                    "symbol": code,
                    "name": hq[code].get("name", CORE_RECOGNITION_STOCKS.get(code, CORE_LARGE_STOCKS.get(code, ""))),
                    "open_change_pct": round(open_change, 2),
                    "threshold": FATAL_THRESHOLDS["core_leader_intraday_drop"],
                    "msg": f"{data['name']}({code}) 竞价开盘跌幅 {open_change:.2f}% ≤ -3%，触发高风险防御",
                })
        return findings


# ═══════════════════════════════════════════════════════════════
# 持久化 & 标准输出
# ═══════════════════════════════════════════════════════════════

def save_result(result: dict):
    """落盘 data/fatal_risk_YYYY-MM-DD.json"""
    path = os.path.join(DATA_DIR, f"fatal_risk_{result['date']}.json")
    with open(path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    log.info(f"📄 致命风险结果已保存: {path}")


def print_brief(result: dict):
    """标准化简报输出"""
    lines = []
    lines.append(f"☠️ **030致命风险检测** ({result['date']} {result['check_time']})")
    lines.append(f"动作: {result['action']} | 致命风险: {'是' if result['fatal_triggered'] else '否'} | 高风险: {'是' if result['high_risk_triggered'] else '否'}")
    lines.append("")
    if result["conditions"]:
        for c in result["conditions"]:
            tag = "🔴" if c["level"] == "FATAL" else "🟡"
            lines.append(f"{tag} [{c['type']}] {c['msg']}")
    else:
        lines.append("✅ 无致命风险/高风险触发")
    lines.append("")
    lines.append(f"恢复条件: {result['recovery_condition']}")
    if result["min_cash_days"] > 0:
        lines.append(f"最少空仓观察: {result['min_cash_days']} 个交易日")
    print("\n".join(lines))


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main():
    detector = FatalRiskDetector()
    result = detector.check()
    save_result(result)
    print_brief(result)

    # stdout JSON 供 agent 解析
    print("\n=====FATAL_RISK_RESULT=====")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print("=====FATAL_RISK_END=====")


if __name__ == "__main__":
    main()